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

MAX_RESULT_BYTES = 16 * 1024 * 1024


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
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ValueError("Invalid PDF SHA-256")

    if not re.fullmatch(r"[0-9a-f]{64}", plan_fingerprint):
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
        if not re.fullmatch(r"[0-9a-f]{64}", scope):
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

            conn.execute("""
                CREATE TABLE IF NOT EXISTS ocr_results (
                    scope TEXT NOT NULL,
                    batch_key TEXT NOT NULL,
                    claim_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    sha256 TEXT NOT NULL,

                    PRIMARY KEY (
                        scope,
                        batch_key
                    ),

                    FOREIGN KEY (
                        scope,
                        batch_key
                    )
                    REFERENCES ocr_jobs (
                        scope,
                        batch_key
                    )
                    ON DELETE CASCADE
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

            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=FULL")

            with conn:
                conn.execute("BEGIN IMMEDIATE")

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
            (self.scope, key),
        ).fetchone()

        return JobRecord(*row) if row else None


    def reserve(
        self,
        key: str,
    ) -> Reservation:
        """Reserve before POST; do not retry unknown submissions."""

        with self._transaction() as conn:
            record = self._read(conn, key)

            if record is not None:
                return Reservation(False, record)

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
            current = self._read(conn, key)

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
        """Record a validated terminal job state."""

        target = "failed" if failed else "completed"

        with self._transaction() as conn:
            current = self._read(conn, key)

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
        """Only release a submission proven not to have connected."""

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


    @staticmethod
    def _valid_result(
        pages,
        info,
        page_count,
    ):
        return (
            isinstance(pages, list)
            and len(pages) == page_count
            and all(
                isinstance(page, dict)
                for page in pages
            )
            and isinstance(info, dict)
        )


    def finish_with_result(
        self,
        key,
        claim_id,
        job_id,
        pages,
        info,
        *,
        page_count: int,
    ) -> bool:
        """
        Commit result and completed state atomically.

        Oversized results are not cached; their provider
        Job IDs remain reusable.
        """

        if not self._valid_result(
            pages,
            info,
            page_count,
        ):
            raise ValueError(
                "Invalid OCR batch result"
            )

        payload = json.dumps(
            {
                "version": 1,
                "pages": pages,
                "data_info": info,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")

        cacheable = len(payload) <= MAX_RESULT_BYTES

        with self._transaction() as conn:
            current = self._read(conn, key)

            if (
                current is None
                or current.claim_id != claim_id
                or current.job_id != job_id
                or current.state not in (
                    "accepted",
                    "completed",
                )
            ):
                raise JournalStateError(
                    "Invalid OCR result transition"
                )

            if cacheable:
                conn.execute(
                    """
                    INSERT INTO ocr_results (
                        scope,
                        batch_key,
                        claim_id,
                        job_id,
                        payload,
                        sha256
                    )
                    VALUES (?, ?, ?, ?, ?, ?)

                    ON CONFLICT(scope, batch_key)
                    DO UPDATE SET
                        claim_id = excluded.claim_id,
                        job_id = excluded.job_id,
                        payload = excluded.payload,
                        sha256 = excluded.sha256
                    """,
                    (
                        self.scope,
                        key,
                        claim_id,
                        job_id,
                        payload,
                        hashlib.sha256(
                            payload
                        ).hexdigest(),
                    ),
                )

            else:
                conn.execute(
                    """
                    DELETE FROM ocr_results
                    WHERE scope = ?
                      AND batch_key = ?
                    """,
                    (self.scope, key),
                )

            conn.execute(
                """
                UPDATE ocr_jobs
                SET state = 'completed',
                    updated_at = ?
                WHERE scope = ?
                  AND batch_key = ?
                """,
                (
                    time.time(),
                    self.scope,
                    key,
                ),
            )

        return cacheable


    def load_result(
        self,
        key,
        claim_id,
        job_id,
        *,
        page_count: int,
    ):
        """Return validated cached data or None."""

        with self._transaction() as conn:
            current = self._read(conn, key)

            if (
                current is None
                or current.state != "completed"
                or current.claim_id != claim_id
                or current.job_id != job_id
            ):
                return None

            row = conn.execute(
                """
                SELECT payload, sha256
                FROM ocr_results
                WHERE scope = ?
                  AND batch_key = ?
                  AND claim_id = ?
                  AND job_id = ?
                  AND typeof(payload) = 'blob'
                  AND length(payload) <= ?
                """,
                (
                    self.scope,
                    key,
                    claim_id,
                    job_id,
                    MAX_RESULT_BYTES,
                ),
            ).fetchone()

        if row is None:
            return None

        payload, digest = row

        if hashlib.sha256(payload).hexdigest() != digest:
            return None

        try:
            value = json.loads(payload)

        except (
            ValueError,
            UnicodeError,
            RecursionError,
        ):
            return None

        if (
            not isinstance(value, dict)
            or value.get("version") != 1
        ):
            return None

        pages = value.get("pages")
        info = value.get("data_info")

        if not self._valid_result(
            pages,
            info,
            page_count,
        ):
            return None

        return pages, info