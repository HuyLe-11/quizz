"""Offline inspection utilities for OCR Journal recovery."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from backend.engine.ocr_job_journal import (
    DEFAULT_PATH,
    MAX_RESULT_BYTES,
    OCRJobJournal,
)


PRIMARY_MODEL = "PaddleOCR-VL-1.6"
SUPPLEMENTAL_MODEL = "PP-OCRv6"


class RecoveryInspectionError(RuntimeError):
    """The recovery target cannot be safely identified."""


def _connect_readonly(path: Path):
    path = Path(path).resolve()

    if not path.is_file():
        raise RecoveryInspectionError(
            "OCR Journal database not found."
        )

    conn = sqlite3.connect(
        path.as_uri() + "?mode=ro",
        uri=True,
        timeout=10,
    )

    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    except Exception:
        conn.close()
        raise


def _validate_scope(scope: str | None):
    if scope is None:
        return

    if not re.fullmatch(
        r"[0-9a-f]{64}",
        scope,
    ):
        raise RecoveryInspectionError(
            "Invalid upload scope."
        )


def _validate_job_id(job_id: str):
    if not isinstance(job_id, str):
        raise RecoveryInspectionError(
            "Invalid Job ID."
        )

    if not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}",
        job_id,
    ):
        raise RecoveryInspectionError(
            "Invalid Job ID."
        )


def _read_job(
    conn,
    scope: str,
    batch_key: str,
):
    return conn.execute(
        """
        SELECT
            scope,
            batch_key,
            claim_id,
            state,
            job_id,
            updated_at
        FROM ocr_jobs
        WHERE scope = ?
          AND batch_key = ?
        """,
        (
            scope,
            batch_key,
        ),
    ).fetchone()


def _format_job(row):
    if row is None:
        return None

    return {
        "scope": row["scope"],
        "batch_key": row["batch_key"],
        "claim_id": row["claim_id"],
        "state": row["state"],
        "job_id": row["job_id"],
        "updated_at": datetime.fromtimestamp(
            row["updated_at"],
            tz=timezone.utc,
        ).isoformat(),
    }


def _find_primary(
    conn,
    primary_job_id: str,
    primary_key: str,
    scope: str | None,
):
    query = """
        SELECT
            scope,
            batch_key,
            claim_id,
            state,
            job_id,
            updated_at
        FROM ocr_jobs
        WHERE batch_key = ?
          AND job_id = ?
    """

    params = [
        primary_key,
        primary_job_id,
    ]

    if scope is not None:
        query += " AND scope = ?"
        params.append(scope)

    rows = conn.execute(
        query,
        params,
    ).fetchall()

    if not rows:
        raise RecoveryInspectionError(
            "Matching primary OCR job not found."
        )

    if len(rows) != 1:
        raise RecoveryInspectionError(
            "Multiple matching upload scopes. "
            "Specify the exact scope."
        )

    return rows[0]


def _inspect_cache(
    conn,
    job,
    page_count: int,
):
    result = {
        "present": False,
        "payload_bytes": None,
        "identity_valid": False,
        "checksum_valid": False,
        "structure_valid": False,
        "cached_pages": None,
        "valid": False,
        "reason": None,
    }

    row = conn.execute(
        """
        SELECT
            claim_id,
            job_id,
            sha256,
            typeof(payload) AS payload_type,
            length(payload) AS payload_bytes,
            CASE
                WHEN typeof(payload) = 'blob'
                 AND length(payload) <= ?
                THEN payload
                ELSE NULL
            END AS payload
        FROM ocr_results
        WHERE scope = ?
          AND batch_key = ?
        """,
        (
            MAX_RESULT_BYTES,
            job["scope"],
            job["batch_key"],
        ),
    ).fetchone()

    if row is None:
        result["reason"] = "cache_missing"
        return result

    result["present"] = True
    result["payload_bytes"] = row["payload_bytes"]

    result["identity_valid"] = (
        row["claim_id"] == job["claim_id"]
        and row["job_id"] == job["job_id"]
    )

    if not result["identity_valid"]:
        result["reason"] = "identity_mismatch"
        return result

    if row["payload_type"] != "blob":
        result["reason"] = "invalid_payload_type"
        return result

    payload = row["payload"]

    if payload is None:
        result["reason"] = "payload_too_large"
        return result

    if hashlib.sha256(payload).hexdigest() != row["sha256"]:
        result["reason"] = "checksum_mismatch"
        return result

    result["checksum_valid"] = True

    try:
        value = json.loads(payload)

    except (
        ValueError,
        UnicodeError,
        RecursionError,
    ):
        result["reason"] = "invalid_json"
        return result

    if (
        not isinstance(value, dict)
        or value.get("version") != 1
    ):
        result["reason"] = "invalid_payload_version"
        return result

    pages = value.get("pages")
    info = value.get("data_info")

    if isinstance(pages, list):
        result["cached_pages"] = len(pages)

    if (
        not isinstance(pages, list)
        or len(pages) != page_count
        or not all(
            isinstance(page, dict)
            for page in pages
        )
        or not isinstance(info, dict)
    ):
        result["reason"] = "invalid_result_structure"
        return result

    result["structure_valid"] = True

    if job["state"] != "completed":
        result["reason"] = "job_not_completed"
        return result

    result["valid"] = True

    return result


def _inspect_target(
    conn,
    scope: str,
    batch_key: str,
    page_count: int,
):
    target = _read_job(
        conn,
        scope,
        batch_key,
    )

    if target is None:
        return {
            "batch_key": batch_key,
            "job": None,
            "cache": None,
            "classification": "missing",
            "blocked": False,
        }

    cache = _inspect_cache(
        conn,
        target,
        page_count,
    )

    state = target["state"]

    if state == "submitting":
        classification = (
            "submission_unconfirmed"
        )

    elif state == "accepted":
        classification = (
            "existing_job_available"
        )

    elif state == "completed":
        classification = (
            "completed_with_cache"
            if cache["valid"]
            else "completed_without_valid_cache"
        )

    elif state == "failed":
        classification = "provider_job_failed"

    else:
        classification = "unknown_state"

    return {
        "batch_key": batch_key,
        "job": _format_job(target),
        "cache": cache,
        "classification": classification,
        "blocked": state == "submitting",
    }


def inspect_recovery(
    *,
    path: Path = DEFAULT_PATH,
    primary_job_id: str,
    scope: str | None = None,
    start_page: int = 0,
    page_count: int = 1,
    primary_model: str = PRIMARY_MODEL,
    target_model: str = SUPPLEMENTAL_MODEL,
):
    """
    Inspect one owner-scoped OCR recovery target.

    This operation never submits OCR jobs and never
    modifies the journal database.
    """

    _validate_job_id(primary_job_id)
    _validate_scope(scope)

    if primary_model == target_model:
        raise RecoveryInspectionError(
            "Primary and target models must differ."
        )

    primary_key = OCRJobJournal.batch_key(
        start_page,
        page_count,
        primary_model,
    )

    target_key = OCRJobJournal.batch_key(
        start_page,
        page_count,
        target_model,
    )

    with closing(
        _connect_readonly(path)
    ) as conn:

        conn.execute("BEGIN")

        primary = _find_primary(
            conn,
            primary_job_id,
            primary_key,
            scope,
        )

        selected_scope = primary["scope"]

        primary_cache = _inspect_cache(
            conn,
            primary,
            page_count,
        )

        target = _inspect_target(
            conn,
            selected_scope,
            target_key,
            page_count,
        )

        blockers = []

        if primary["state"] != "completed":
            blockers.append(
                "primary_job_not_completed"
            )

        if not primary_cache["valid"]:
            blockers.append(
                "primary_cache_not_valid"
            )

        if not target["blocked"]:
            blockers.append(
                "target_not_in_submitting_state"
            )

        if (
            target["cache"] is not None
            and target["cache"]["present"]
        ):
            blockers.append(
                "unexpected_target_cache"
            )

        conn.rollback()

        return {
            "database": str(
                Path(path).resolve()
            ),
            "mode": "read_only",
            "scope": selected_scope,
            "start_page": start_page,
            "page_count": page_count,
            "primary": {
                "model": primary_model,
                "job": _format_job(primary),
                "cache": primary_cache,
            },
            "target": {
                "model": target_model,
                **target,
            },
            "structural_checks_passed": (
                not blockers
            ),
            "blockers": blockers,
            "submission_metadata_checked": False,
            "mutation_authorized": False,
            "requires_operator_confirmation": True,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Offline OCR Journal recovery inspection."
        )
    )

    parser.add_argument(
        "action",
        choices=["inspect"],
    )

    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_PATH,
    )

    parser.add_argument(
        "--primary-job-id",
        required=True,
    )

    parser.add_argument(
        "--scope",
        default=None,
    )

    parser.add_argument(
        "--start-page",
        type=int,
        default=0,
    )

    parser.add_argument(
        "--page-count",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--primary-model",
        default=PRIMARY_MODEL,
    )

    parser.add_argument(
        "--target-model",
        default=SUPPLEMENTAL_MODEL,
    )

    args = parser.parse_args(argv)

    try:
        report = inspect_recovery(
            path=args.database,
            primary_job_id=args.primary_job_id,
            scope=args.scope,
            start_page=args.start_page,
            page_count=args.page_count,
            primary_model=args.primary_model,
            target_model=args.target_model,
        )

    except (
        RecoveryInspectionError,
        sqlite3.Error,
        ValueError,
        OSError,
        OverflowError,
    ) as exc:

        print(
            json.dumps(
                {
                    "mode": "read_only",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "mutation_authorized": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        return 1

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())