"""Offline, explicit repair of durable OCR batch reservations."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time

from contextlib import closing, contextmanager
from pathlib import Path


STATES = (
    "submitting",
    "accepted",
    "completed",
    "failed",
)


@contextmanager
def _database(
    path: Path,
    apply: bool = False,
):
    uri = Path(path).resolve().as_uri()

    uri += "?mode=rw" if apply else "?mode=ro"

    with closing(
        sqlite3.connect(
            uri,
            uri=True,
            timeout=10,
        )
    ) as conn:

        conn.row_factory = sqlite3.Row

        conn.execute("PRAGMA foreign_keys=ON")

        if apply:
            conn.execute("PRAGMA synchronous=FULL")

        with conn:
            conn.execute(
                "BEGIN IMMEDIATE"
                if apply
                else "BEGIN"
            )

            yield conn


def list_jobs(
    path: Path,
    limit: int = 100,
) -> list[dict]:

    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError(
            "limit must be between 1 and 1000"
        )

    with _database(path) as conn:

        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    j.scope,
                    j.batch_key,
                    j.claim_id,
                    j.state,
                    j.job_id,
                    j.updated_at,
                    length(r.payload) AS cache_bytes

                FROM ocr_jobs j

                LEFT JOIN ocr_results r
                    ON r.scope = j.scope
                    AND r.batch_key = j.batch_key

                ORDER BY
                    j.updated_at DESC,
                    j.scope,
                    j.batch_key

                LIMIT ?
                """,
                (limit,),
            )
        ]


def recover_batch(
    path: Path,
    *,
    scope: str,
    batch_key: str,
    claim_id: str,
    expected_state: str,
    action: str,
    reason: str,
    new_job_id: str | None = None,
    confirmed: bool = False,
    apply: bool = False,
) -> dict:
    """Validate and optionally apply one operator-authorized transition."""

    for value in (scope, batch_key):

        if not re.fullmatch(
            r"[0-9a-f]{64}",
            value,
        ):
            raise ValueError(
                "Invalid scope or batch key"
            )

    if not re.fullmatch(
        r"[0-9a-f]{32}",
        claim_id,
    ):
        raise ValueError(
            "Invalid claim ID"
        )

    if (
        expected_state not in STATES
        or action not in ("attach", "reset")
    ):
        raise ValueError(
            "Invalid state or action"
        )

    if not reason.strip() or len(reason) > 300:
        raise ValueError(
            "A reason of 1 to 300 characters is required"
        )

    if action == "attach":

        if (
            expected_state != "submitting"
            or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,128}",
                new_job_id or "",
            )
        ):
            raise ValueError(
                "Attach requires a submitting row "
                "and valid Job ID"
            )

    elif new_job_id is not None:

        raise ValueError(
            "Reset does not accept a new Job ID"
        )

    if apply and not confirmed:

        raise ValueError(
            "Explicit confirmation is required "
            "to apply recovery"
        )

    with _database(path, apply) as conn:

        row = conn.execute(
            """
            SELECT
                state,
                claim_id,
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

        if (
            row is None
            or row["claim_id"] != claim_id
            or row["state"] != expected_state
        ):
            raise RuntimeError(
                "Job changed or does not exist; inspect again"
            )

        cache = conn.execute(
            """
            SELECT 1
            FROM ocr_results

            WHERE scope = ?
              AND batch_key = ?
            """,
            (
                scope,
                batch_key,
            ),
        ).fetchone()

        if cache is not None:

            raise RuntimeError(
                "Cache exists; recovery refuses "
                "to modify this batch"
            )

        if action == "attach" and conn.execute(
            """
            SELECT 1
            FROM ocr_jobs

            WHERE job_id = ?

            LIMIT 1
            """,
            (new_job_id,),
        ).fetchone() is not None:

            raise RuntimeError(
                "Provider Job ID is already assigned"
            )

        report = {
            "mode":
                "apply" if apply else "dry-run",

            "action": action,

            "scope": scope,

            "batch_key": batch_key,

            "claim_id": claim_id,

            "old_state": row["state"],

            "old_job_id": row["job_id"],

            "new_state":
                "accepted" if action == "attach" else None,

            "new_job_id": new_job_id,
        }

        if not apply:
            return report

        now = time.time()

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ocr_recovery_audit (
                id INTEGER PRIMARY KEY,

                occurred_at REAL NOT NULL,

                scope TEXT NOT NULL,

                batch_key TEXT NOT NULL,

                claim_id TEXT NOT NULL,

                action TEXT NOT NULL,

                old_state TEXT NOT NULL,

                old_job_id TEXT,

                old_updated_at REAL NOT NULL,

                new_job_id TEXT,

                reason TEXT NOT NULL
            )
        """)

        conn.execute(
            """
            INSERT INTO ocr_recovery_audit (
                occurred_at,
                scope,
                batch_key,
                claim_id,
                action,
                old_state,
                old_job_id,
                old_updated_at,
                new_job_id,
                reason
            )

            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                scope,
                batch_key,
                claim_id,
                action,
                row["state"],
                row["job_id"],
                row["updated_at"],
                new_job_id,
                reason.strip(),
            ),
        )

        identity = (
            scope,
            batch_key,
            claim_id,
            expected_state,
        )

        if action == "attach":

            cursor = conn.execute(
                """
                UPDATE ocr_jobs

                SET state = 'accepted',
                    job_id = ?,
                    updated_at = ?

                WHERE scope = ?
                  AND batch_key = ?
                  AND claim_id = ?
                  AND state = ?
                  AND job_id IS NULL
                """,
                (
                    new_job_id,
                    now,
                    *identity,
                ),
            )

        else:

            cursor = conn.execute(
                """
                DELETE FROM ocr_jobs

                WHERE scope = ?
                  AND batch_key = ?
                  AND claim_id = ?
                  AND state = ?
                """,
                identity,
            )

        if cursor.rowcount != 1:

            raise RuntimeError(
                "Recovery transition failed"
            )

        return report


def main(argv=None) -> int:

    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--db",
        type=Path,
    )

    sub = parser.add_subparsers(
        dest="action",
        required=True,
    )

    inspect = sub.add_parser("list")

    inspect.add_argument(
        "--limit",
        type=int,
        default=100,
    )

    for action in ("attach", "reset"):

        command = sub.add_parser(action)

        command.add_argument(
            "--scope",
            required=True,
        )

        command.add_argument(
            "--batch-key",
            required=True,
        )

        command.add_argument(
            "--claim-id",
            required=True,
        )

        command.add_argument(
            "--state",
            choices=STATES,
            required=True,
        )

        command.add_argument(
            "--reason",
            required=True,
        )

        command.add_argument(
            "--apply",
            action="store_true",
        )

        if action == "attach":

            command.add_argument(
                "--job-id",
                required=True,
            )

            command.add_argument(
                "--verified-job",
                action="store_true",
            )

        else:

            command.add_argument(
                "--allow-new-submission",
                action="store_true",
            )

    args = parser.parse_args(argv)

    if args.db is None:

        from .ocr_job_journal import DEFAULT_PATH

        args.db = DEFAULT_PATH

    try:

        if args.action == "list":

            report = list_jobs(
                args.db,
                args.limit,
            )

        else:

            report = recover_batch(
                args.db,

                scope=args.scope,

                batch_key=args.batch_key,

                claim_id=args.claim_id,

                expected_state=args.state,

                action=args.action,

                reason=args.reason,

                new_job_id=getattr(
                    args,
                    "job_id",
                    None,
                ),

                confirmed=(
                    args.verified_job
                    if args.action == "attach"
                    else args.allow_new_submission
                ),

                apply=args.apply,
            )

    except (
        sqlite3.Error,
        RuntimeError,
        ValueError,
    ) as exc:

        parser.exit(
            1,
            f"OCR recovery failed: {exc}\n",
        )

    print(
        json.dumps(
            report,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())