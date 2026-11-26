"""Guarded offline repair of unconfirmed OCR submissions."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
import time

from contextlib import closing
from pathlib import Path

from backend.engine.ocr_job_journal import (
    DEFAULT_PATH,
    OCRJobJournal,
)

from backend.engine.ocr_journal_recovery import (
    PRIMARY_MODEL,
    SUPPLEMENTAL_MODEL,
    RecoveryInspectionError,
    _connect_readonly,
    _validate_scope,
    _validate_job_id,
    _find_primary,
    _format_job,
    _inspect_cache,
    _inspect_target,
)


RESUME_CONFIRMATION = "VERIFIED-JOB"
RETRY_CONFIRMATION = "ALLOW-ONE-RETRY"


class RecoveryRepairError(RuntimeError):
    """The requested repair cannot be performed safely."""


def _connect_readwrite(path):
    path = Path(path).resolve()

    if not path.is_file():
        raise RecoveryRepairError(
            "OCR Journal database not found."
        )

    conn = sqlite3.connect(
        path.as_uri() + "?mode=rw",
        uri=True,
        timeout=10,
    )

    try:
        conn.row_factory = sqlite3.Row

        conn.execute(
            "PRAGMA foreign_keys=ON"
        )

        conn.execute(
            "PRAGMA synchronous=FULL"
        )

        return conn

    except Exception:
        conn.close()
        raise


def _submission_issue(
    conn,
    scope,
    batch_key,
    claim_id,
):
    exists = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'ocr_submission_issues'
        """
    ).fetchone()

    if exists is None:
        return None

    row = conn.execute(
        """
        SELECT
            claim_id,
            stage,
            error_type,
            http_status,
            observed_job_id,
            recorded_at
        FROM ocr_submission_issues
        WHERE scope = ?
          AND batch_key = ?
        """,
        (
            scope,
            batch_key,
        ),
    ).fetchone()

    if row is None:
        return None

    if row["claim_id"] != claim_id:
        raise RecoveryRepairError(
            "Submission metadata belongs "
            "to a different claim."
        )

    return {
        "claim_id": row["claim_id"],
        "stage": row["stage"],
        "error_type": row["error_type"],
        "http_status": row["http_status"],
        "observed_job_id": row["observed_job_id"],
        "recorded_at": row["recorded_at"],
    }


def _load_target(
    conn,
    *,
    primary_job_id,
    scope,
    start_page,
    page_count,
):
    _validate_job_id(
        primary_job_id
    )

    _validate_scope(
        scope
    )

    primary_key = OCRJobJournal.batch_key(
        start_page,
        page_count,
        PRIMARY_MODEL,
    )

    target_key = OCRJobJournal.batch_key(
        start_page,
        page_count,
        SUPPLEMENTAL_MODEL,
    )

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

    target_job = target["job"]

    issue = None

    if (
        target_job is not None
        and target_job["state"] == "submitting"
    ):
        issue = _submission_issue(
            conn,
            selected_scope,
            target_key,
            target_job["claim_id"],
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

    observed_job_id = (
        issue["observed_job_id"]
        if issue is not None
        else None
    )

    if blockers:
        recommendation = "do_not_repair"

    elif observed_job_id:
        recommendation = "verify_and_resume"

    else:
        recommendation = (
            "locate_existing_job_or_confirm_retry"
        )

    return {
        "scope": selected_scope,
        "start_page": start_page,
        "page_count": page_count,
        "primary": {
            "model": PRIMARY_MODEL,
            "job": _format_job(primary),
            "cache": primary_cache,
        },
        "target": {
            "model": SUPPLEMENTAL_MODEL,
            **target,
        },
        "submission_issue": issue,
        "submission_metadata_checked": True,
        "observed_job_id": observed_job_id,
        "structural_checks_passed": not blockers,
        "blockers": blockers,
        "recommendation": recommendation,
        "mutation_authorized": False,
    }


def inspect_repair(
    *,
    path=DEFAULT_PATH,
    primary_job_id,
    scope=None,
    start_page=0,
    page_count=1,
):
    """Inspect journal and submission metadata without writes."""

    with closing(
        _connect_readonly(path)
    ) as conn:

        conn.execute(
            "BEGIN"
        )

        report = _load_target(
            conn,
            primary_job_id=primary_job_id,
            scope=scope,
            start_page=start_page,
            page_count=page_count,
        )

        conn.rollback()

    return {
        "database": str(
            Path(path).resolve()
        ),
        "mode": "read_only",
        **report,
    }


def _backup_database(path):
    """
    Create a consistent SQLite backup.

    The caller must already hold a BEGIN IMMEDIATE
    transaction without having modified any rows.
    """

    path = Path(path).resolve()

    fd, filename = tempfile.mkstemp(
        prefix="jobs.before-recovery-",
        suffix=".sqlite3",
        dir=path.parent,
    )

    os.close(fd)

    backup = Path(filename)

    try:
        with closing(
            _connect_readonly(path)
        ) as source, closing(
            sqlite3.connect(backup)
        ) as destination:

            source.backup(
                destination
            )

            result = destination.execute(
                "PRAGMA integrity_check"
            ).fetchall()

            if result != [("ok",)]:
                raise RecoveryRepairError(
                    "Backup integrity check failed."
                )

        return backup

    except Exception:
        backup.unlink(
            missing_ok=True
        )
        raise


def _validate_request(
    *,
    action,
    scope,
    expected_claim,
    job_id,
    confirmation,
    workers_stopped,
):
    if action not in (
        "resume",
        "retry",
    ):
        raise RecoveryRepairError(
            "Invalid repair action."
        )

    if not scope:
        raise RecoveryRepairError(
            "An explicit upload scope is required."
        )

    _validate_scope(scope)

    if (
        not isinstance(expected_claim, str)
        or not expected_claim.strip()
    ):
        raise RecoveryRepairError(
            "Expected claim ID is required."
        )

    if not workers_stopped:
        raise RecoveryRepairError(
            "Stop all backend workers before repair."
        )

    if action == "resume":
        if confirmation != RESUME_CONFIRMATION:
            raise RecoveryRepairError(
                "Resume confirmation is required."
            )

        _validate_job_id(
            job_id
        )

    else:
        if confirmation != RETRY_CONFIRMATION:
            raise RecoveryRepairError(
                "Retry confirmation is required."
            )

        if job_id is not None:
            raise RecoveryRepairError(
                "Retry must not specify a Job ID."
            )


def _validate_repair_target(
    conn,
    report,
    *,
    action,
    expected_claim,
    job_id,
):
    if not report["structural_checks_passed"]:
        raise RecoveryRepairError(
            "Recovery blocked: "
            + ", ".join(
                report["blockers"]
            )
        )

    target = report["target"]["job"]

    if target["claim_id"] != expected_claim:
        raise RecoveryRepairError(
            "Target claim changed after inspection."
        )

    if target["job_id"] is not None:
        raise RecoveryRepairError(
            "Target already contains a Job ID."
        )

    observed = report["observed_job_id"]

    if action == "retry":
        if observed:
            raise RecoveryRepairError(
                "A provider Job ID was already observed. "
                "Verify and resume it instead of retrying."
            )

        return

    if job_id == report["primary"]["job"]["job_id"]:
        raise RecoveryRepairError(
            "Supplemental Job ID cannot equal "
            "the primary Job ID."
        )

    if observed and observed != job_id:
        raise RecoveryRepairError(
            "Provided Job ID differs from "
            "the observed Job ID."
        )

    duplicate = conn.execute(
        """
        SELECT 1
        FROM ocr_jobs
        WHERE job_id = ?
        LIMIT 1
        """,
        (job_id,),
    ).fetchone()

    if duplicate is not None:
        raise RecoveryRepairError(
            "Job ID is already assigned "
            "to another journal record."
        )


def _apply_resume(
    conn,
    *,
    scope,
    batch_key,
    claim_id,
    job_id,
):
    cursor = conn.execute(
        """
        UPDATE ocr_jobs
        SET state = 'accepted',
            job_id = ?,
            updated_at = ?
        WHERE scope = ?
          AND batch_key = ?
          AND claim_id = ?
          AND state = 'submitting'
          AND job_id IS NULL
        """,
        (
            job_id,
            time.time(),
            scope,
            batch_key,
            claim_id,
        ),
    )

    if cursor.rowcount != 1:
        raise RecoveryRepairError(
            "Resume did not update exactly one record."
        )


def _apply_retry(
    conn,
    *,
    scope,
    batch_key,
    claim_id,
):
    cursor = conn.execute(
        """
        DELETE FROM ocr_jobs
        WHERE scope = ?
          AND batch_key = ?
          AND claim_id = ?
          AND state = 'submitting'
          AND job_id IS NULL
        """,
        (
            scope,
            batch_key,
            claim_id,
        ),
    )

    if cursor.rowcount != 1:
        raise RecoveryRepairError(
            "Retry did not release exactly one record."
        )


def repair_submission(
    *,
    path=DEFAULT_PATH,
    primary_job_id,
    scope,
    expected_claim,
    action,
    confirmation,
    workers_stopped=False,
    job_id=None,
    start_page=0,
    page_count=1,
):
    """
    Repair one unconfirmed supplemental OCR reservation.

    This function performs no HTTP requests.
    It never submits an OCR job.
    """

    _validate_request(
        action=action,
        scope=scope,
        expected_claim=expected_claim,
        job_id=job_id,
        confirmation=confirmation,
        workers_stopped=workers_stopped,
    )

    path = Path(path).resolve()

    backup = None

    with closing(
        _connect_readwrite(path)
    ) as conn:

        with conn:

            conn.execute(
                "BEGIN IMMEDIATE"
            )

            report = _load_target(
                conn,
                primary_job_id=primary_job_id,
                scope=scope,
                start_page=start_page,
                page_count=page_count,
            )

            _validate_repair_target(
                conn,
                report,
                action=action,
                expected_claim=expected_claim,
                job_id=job_id,
            )

            batch_key = report["target"]["batch_key"]

            backup = _backup_database(
                path
            )

            if action == "resume":
                _apply_resume(
                    conn,
                    scope=scope,
                    batch_key=batch_key,
                    claim_id=expected_claim,
                    job_id=job_id,
                )

                new_state = "accepted"

            else:
                _apply_retry(
                    conn,
                    scope=scope,
                    batch_key=batch_key,
                    claim_id=expected_claim,
                )

                new_state = "reservation_released"

            updated = _load_target(
                conn,
                primary_job_id=primary_job_id,
                scope=scope,
                start_page=start_page,
                page_count=page_count,
            )

            if updated["primary"] != report["primary"]:
                raise RecoveryRepairError(
                    "Primary OCR data changed during repair."
                )

            updated_job = updated["target"]["job"]

            if action == "resume":
                if (
                    updated_job is None
                    or updated_job["state"] != "accepted"
                    or updated_job["job_id"] != job_id
                    or updated_job["claim_id"] != expected_claim
                ):
                    raise RecoveryRepairError(
                        "Resume postcondition failed."
                    )

            elif updated_job is not None:
                raise RecoveryRepairError(
                    "Retry postcondition failed."
                )

    return {
        "database": str(path),
        "mode": "guarded_repair",
        "action": action,
        "scope": scope,
        "batch_key": batch_key,
        "previous_claim_id": expected_claim,
        "previous_state": "submitting",
        "new_state": new_state,
        "job_id": job_id,
        "backup": str(backup),
        "primary_cache_preserved": True,
        "provider_requests": 0,
        "new_job_submitted": False,
        "committed": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Guarded offline OCR Journal repair."
        )
    )

    parser.add_argument(
        "action",
        choices=[
            "inspect",
            "resume",
            "retry",
        ],
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
    )

    parser.add_argument(
        "--expected-claim",
    )

    parser.add_argument(
        "--job-id",
    )

    parser.add_argument(
        "--confirmation",
    )

    parser.add_argument(
        "--workers-stopped",
        action="store_true",
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

    args = parser.parse_args(argv)

    common = {
        "path": args.database,
        "primary_job_id": args.primary_job_id,
        "scope": args.scope,
        "start_page": args.start_page,
        "page_count": args.page_count,
    }

    try:
        if args.action == "inspect":
            result = inspect_repair(
                **common
            )

        else:
            result = repair_submission(
                **common,
                expected_claim=args.expected_claim,
                action=args.action,
                confirmation=args.confirmation,
                workers_stopped=args.workers_stopped,
                job_id=args.job_id,
            )

    except (
        RecoveryInspectionError,
        RecoveryRepairError,
        sqlite3.Error,
        ValueError,
        OSError,
        OverflowError,
    ) as exc:

        print(
            json.dumps(
                {
                    "action": args.action,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "committed": False,
                    "provider_requests": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        return 1

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())