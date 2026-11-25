"""Durable local journal for owner-scoped OCR batch submissions."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time

from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4


DEFAULT_PATH = (
    Path(__file__).resolve().parents[1]
    / "data" / "storage" / "ocr-journal" / "jobs.sqlite3"
)


class JournalStateError(RuntimeError):
    """The requested state transition is not safe."""


def _digest(value: dict) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(payload).hexdigest()


def upload_scope(
    *,
    user_id: str,
    filename: str,
    source_sha256: str,
    model: str,
    plan_fingerprint: str,
) -> str:

    if not re.fullmatch(
        r"[0-9a-f]{64}",
        source_sha256,
    ):
        raise ValueError("Invalid PDF SHA-256")

    if not re.fullmatch(
        r"[0-9a-f]{64}",
        plan_fingerprint,
    ):
        raise ValueError("Invalid OCR plan fingerprint")

    if not filename.strip() or not model.strip():
        raise ValueError("Invalid OCR upload identity")

    return _digest({
        "version": 1,
        "owner": str(UUID(user_id)),
        "filename": filename,
        "source_sha256": source_sha256,
        "model": model,
        "plan": plan_fingerprint,
    })


@dataclass(frozen=True)
class JobRecord:
    state: str
    claim_id: str
    job_id: str | None


@dataclass(frozen=True)
class Reservation:
    created: bool
    record: JobRecord


class OCRJobJournal:

    def __init__(
        self,
        scope: str,
        path: Path = DEFAULT_PATH,
    ):
        if not re.fullmatch(
            r"[0-9a-f]{64}",
            scope,
        ):
            raise ValueError("Invalid upload scope")

        self.scope = scope

        self.path = Path(path)

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )

        if os.name != "nt":
            self.path.parent.chmod(0o700)

        with self._transaction() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ocr_jobs (
                    scope TEXT NOT NULL,
                    batch_key TEXT NOT NULL,
                    claim_id TEXT NOT NULL,

                    state TEXT NOT NULL CHECK (
                        state IN (
                            'submitting',
                            'accepted',
                            'completed',
                            'failed'
                        )
                    ),

                    job_id TEXT,
                    updated_at REAL NOT NULL,

                    PRIMARY KEY (
                        scope,
                        batch_key
                    ),

                    CHECK (
                        (
                            state = 'submitting'
                            AND job_id IS NULL
                        )
                        OR
                        (
                            state != 'submitting'
                            AND job_id IS NOT NULL
                        )
                    )
                )
            """)

        if os.name != "nt":
            self.path.chmod(0o600)


    @contextmanager
    def _transaction(self):
        with closing(
            sqlite3.connect(
                self.path,
                timeout=10,
            )
        ) as conn:

            conn.execute(
                "PRAGMA synchronous=FULL"
            )

            with conn:
                conn.execute(
                    "BEGIN IMMEDIATE"
                )

                yield conn


    @staticmethod
    def batch_key(
        start_page: int,
        page_count: int,
        model: str,
    ) -> str:

        if (
            type(start_page) is not int
            or start_page < 0
            or type(page_count) is not int
            or page_count < 1
            or not model.strip()
        ):
            raise ValueError(
                "Invalid OCR batch identity"
            )

        return _digest({
            "start_page": start_page,
            "page_count": page_count,
            "model": model,
        })


    def _read(
        self,
        conn,
        key: str,
    ) -> JobRecord | None:

        row = conn.execute(
            """
            SELECT state, claim_id, job_id
            FROM ocr_jobs
            WHERE scope = ?
              AND batch_key = ?
            """,
            (
                self.scope,
                key,
            ),
        ).fetchone()

        return JobRecord(*row) if row else None


    def reserve(
        self,
        key: str,
    ) -> Reservation:
        """
        Reserve before POST.

        Existing submitting rows must not be retried.
        """

        with self._transaction() as conn:

            record = self._read(
                conn,
                key,
            )

            if record is not None:
                return Reservation(
                    False,
                    record,
                )

            claim = uuid4().hex

            conn.execute(
                """
                INSERT INTO ocr_jobs
                VALUES (
                    ?, ?, ?,
                    'submitting',
                    NULL,
                    ?
                )
                """,
                (
                    self.scope,
                    key,
                    claim,
                    time.time(),
                ),
            )

            return Reservation(
                True,
                JobRecord(
                    "submitting",
                    claim,
                    None,
                ),
            )


    def accept(
        self,
        key: str,
        claim_id: str,
        job_id: str,
    ) -> None:

        if (
            not isinstance(job_id, str)
            or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,128}",
                job_id,
            )
        ):
            raise ValueError(
                "Invalid provider Job ID"
            )

        with self._transaction() as conn:

            current = self._read(
                conn,
                key,
            )

            if (
                current is None
                or current.claim_id != claim_id
            ):
                raise JournalStateError(
                    "OCR submission claim mismatch"
                )

            if current.state != "submitting":

                if current.job_id == job_id:
                    return

                raise JournalStateError(
                    "OCR Job ID already recorded"
                )

            conn.execute(
                """
                UPDATE ocr_jobs
                SET state = 'accepted',
                    job_id = ?,
                    updated_at = ?
                WHERE scope = ?
                  AND batch_key = ?
                """,
                (
                    job_id,
                    time.time(),
                    self.scope,
                    key,
                ),
            )


    def finish(
        self,
        key: str,
        claim_id: str,
        *,
        failed: bool = False,
    ) -> None:
        """
        Complete only after validation,
        or record explicit provider failure.
        """

        target = (
            "failed"
            if failed
            else "completed"
        )

        with self._transaction() as conn:

            current = self._read(
                conn,
                key,
            )

            if (
                current is None
                or current.claim_id != claim_id
            ):
                raise JournalStateError(
                    "OCR submission claim mismatch"
                )

            if current.state == target:
                return

            if current.state != "accepted":
                raise JournalStateError(
                    "Invalid OCR terminal transition"
                )

            conn.execute(
                """
                UPDATE ocr_jobs
                SET state = ?,
                    updated_at = ?
                WHERE scope = ?
                  AND batch_key = ?
                """,
                (
                    target,
                    time.time(),
                    self.scope,
                    key,
                ),
            )


    def release_unconnected(
        self,
        key: str,
        claim_id: str,
    ) -> None:
        """
        Only call after proving that no submission
        reached the provider.
        """

        with self._transaction() as conn:

            cursor = conn.execute(
                """
                DELETE FROM ocr_jobs
                WHERE scope = ?
                  AND batch_key = ?
                  AND state = 'submitting'
                  AND claim_id = ?
                """,
                (
                    self.scope,
                    key,
                    claim_id,
                ),
            )

            if cursor.rowcount != 1:
                raise JournalStateError(
                    "Cannot release OCR submission claim"
                )