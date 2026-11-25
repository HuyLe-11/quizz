"""Offline maintenance of OCR result payloads, never provider job records."""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time

from contextlib import closing
from pathlib import Path

from .ocr_job_journal import DEFAULT_PATH


MIB = 1024 * 1024

DAY = 86400


def maintain_cache(
    path: Path = DEFAULT_PATH,
    *,
    ttl_days: int = 7,
    max_bytes: int = 256 * MIB,
    grace_hours: int = 24,
    apply: bool = False,
    now: float | None = None,
) -> dict:
    """Plan or apply payload eviction. Run with all upload workers stopped."""

    for value, minimum in (
        (ttl_days, 1),
        (max_bytes, 0),
        (grace_hours, 1),
    ):
        if type(value) is not int or value < minimum:
            raise ValueError(
                "Invalid OCR cache retention configuration"
            )

    now = time.time() if now is None else now

    if not math.isfinite(now):
        raise ValueError(
            "Invalid maintenance timestamp"
        )

    cutoff = now - ttl_days * DAY

    grace = now - grace_hours * 3600

    path = Path(path).resolve()

    # Never silently create a missing journal.
    uri = path.as_uri() + (
        "?mode=rw" if apply else "?mode=ro"
    )

    with closing(
        sqlite3.connect(
            uri,
            uri=True,
            timeout=10,
        )
    ) as conn:

        conn.execute(
            "PRAGMA foreign_keys=ON"
        )

        if apply:
            conn.execute(
                "PRAGMA synchronous=FULL"
            )

        with conn:

            conn.execute(
                "BEGIN IMMEDIATE"
                if apply
                else "BEGIN"
            )

            before = conn.execute(
                """
                SELECT COALESCE(
                    SUM(length(payload)),
                    0
                )
                FROM ocr_results
                """
            ).fetchone()[0]

            counts = dict(
                conn.execute(
                    """
                    SELECT state, COUNT(*)
                    FROM ocr_jobs
                    GROUP BY state
                    """
                )
            )

            stale = dict(
                conn.execute(
                    """
                    SELECT state, COUNT(*)
                    FROM ocr_jobs
                    WHERE state IN (
                        'submitting',
                        'accepted'
                    )
                    AND updated_at <= ?
                    GROUP BY state
                    """,
                    (grace,),
                )
            )

            # Protect the ENTIRE scope if any batch
            # is unresolved.
            # Only evict cache belonging to matching,
            # completed jobs.

            scopes = conn.execute(
                """
                SELECT
                    j.scope,
                    MAX(j.updated_at),
                    COUNT(r.payload),
                    COALESCE(
                        SUM(length(r.payload)),
                        0
                    )

                FROM ocr_jobs j

                LEFT JOIN ocr_results r
                    ON r.scope = j.scope
                    AND r.batch_key = j.batch_key
                    AND r.claim_id = j.claim_id
                    AND r.job_id = j.job_id
                    AND j.state = 'completed'

                GROUP BY j.scope

                HAVING SUM(
                    CASE
                        WHEN j.state IN (
                            'completed',
                            'failed'
                        )
                        THEN 0
                        ELSE 1
                    END
                ) = 0

                AND MAX(j.updated_at) <= ?

                AND COUNT(r.payload) > 0

                ORDER BY
                    MAX(j.updated_at),
                    j.scope
                """,
                (grace,),
            ).fetchall()

            remaining = before

            selected = []

            for scope, updated, rows, size in scopes:

                if (
                    updated <= cutoff
                    or remaining > max_bytes
                ):
                    selected.append(
                        (scope, rows, size)
                    )

                    remaining -= size

            # Dry run never executes DELETE.
            # Apply recomputes the plan under a
            # write transaction; it does not trust
            # old output.

            if apply:

                for scope, rows, _ in selected:

                    deleted = conn.execute(
                        """
                        DELETE FROM ocr_results

                        WHERE scope = ?

                        AND EXISTS (
                            SELECT 1
                            FROM ocr_jobs j

                            WHERE
                                j.scope =
                                    ocr_results.scope

                            AND j.batch_key =
                                ocr_results.batch_key

                            AND j.claim_id =
                                ocr_results.claim_id

                            AND j.job_id =
                                ocr_results.job_id

                            AND j.state =
                                'completed'
                        )
                        """,
                        (scope,),
                    ).rowcount

                    if deleted != rows:
                        raise RuntimeError(
                            "OCR cache eviction count mismatch"
                        )

                actual = conn.execute(
                    """
                    SELECT COALESCE(
                        SUM(length(payload)),
                        0
                    )
                    FROM ocr_results
                    """
                ).fetchone()[0]

                if actual != remaining:
                    raise RuntimeError(
                        "OCR cache eviction size mismatch"
                    )

            return {
                "mode":
                    "apply" if apply else "dry-run",

                "payload_bytes_before":
                    before,

                "payload_bytes_after_or_planned":
                    remaining,

                "payload_bytes_reclaimed_or_planned":
                    before - remaining,

                "scopes_selected":
                    len(selected),

                "results_selected":
                    sum(
                        rows
                        for _, rows, _ in selected
                    ),

                "budget_bytes":
                    max_bytes,

                "budget_met":
                    remaining <= max_bytes,

                "job_counts":
                    counts,

                "stale_submitting":
                    stale.get("submitting", 0),

                "stale_accepted":
                    stale.get("accepted", 0),
            }


def main() -> int:

    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_PATH,
    )

    parser.add_argument(
        "--ttl-days",
        type=int,
        default=7,
    )

    parser.add_argument(
        "--max-mib",
        type=int,
        default=256,
    )

    parser.add_argument(
        "--grace-hours",
        type=int,
        default=24,
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Delete eligible payloads; "
            "stop upload workers first"
        ),
    )

    args = parser.parse_args()

    try:

        report = maintain_cache(
            args.db,

            ttl_days=args.ttl_days,

            max_bytes=args.max_mib * MIB,

            grace_hours=args.grace_hours,

            apply=args.apply,
        )

    except (
        sqlite3.Error,
        ValueError,
        RuntimeError,
    ) as exc:

        parser.exit(
            1,
            f"OCR cache maintenance failed: {exc}\n",
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