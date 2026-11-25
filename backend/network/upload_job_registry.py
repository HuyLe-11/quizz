"""Durable cloud upload status, not a persistent work queue."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

DEFAULT_PATH = (
    Path(__file__).resolve().parents[1]
    / "data" / "storage" / "upload-jobs" / "jobs.sqlite3"
)

PHASES = frozenset({
    "starting", "ocr", "question_extraction",
    "question_crops", "cloud_save",
})

INTERRUPTED_MESSAGE = (
    "Backend đã khởi động lại khi đang xử lý PDF. "
    "Hãy kiểm tra lịch sử đề thi trước khi tải lại."
)


class UploadJobStateError(RuntimeError):
    pass


class UploadJobRegistry:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )

        with self._transaction() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS upload_jobs (
                    job_id TEXT PRIMARY KEY NOT NULL,
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'queued',
                            'processing',
                            'completed',
                            'failed'
                        )
                    ),
                    phase TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    error_status INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)

    @contextmanager
    def _transaction(self):
        with closing(
            sqlite3.connect(
                self.path,
                timeout=10,
            )
        ) as conn:

            conn.row_factory = sqlite3.Row

            conn.execute(
                "PRAGMA synchronous=FULL"
            )

            with conn:
                conn.execute(
                    "BEGIN IMMEDIATE"
                )

                yield conn

    @staticmethod
    def _public(row: sqlite3.Row) -> dict:
        return {
            "job_id": row["job_id"],
            "status": row["status"],
            "phase": row["phase"],
            "result": (
                json.loads(row["result_json"])
                if row["result_json"] is not None
                else None
            ),
            "error": row["error"],
            "error_status": row["error_status"],
        }

    def create(self, owner_id: str) -> dict:
        if (
            not isinstance(owner_id, str)
            or not owner_id.strip()
            or len(owner_id) > 128
        ):
            raise ValueError(
                "Invalid upload owner"
            )

        job_id = str(uuid4())

        now = time.time()

        with self._transaction() as conn:

            conn.execute("""
                INSERT INTO upload_jobs (
                    job_id,
                    owner_id,
                    status,
                    phase,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?,
                    'queued',
                    'queued',
                    ?, ?
                )
            """, (
                job_id,
                owner_id,
                now,
                now,
            ))

            row = conn.execute(
                """
                SELECT *
                FROM upload_jobs
                WHERE job_id = ?
                  AND owner_id = ?
                """,
                (job_id, owner_id),
            ).fetchone()

            return self._public(row)

    def get(
        self,
        owner_id: str,
        job_id: str,
    ) -> dict | None:

        uri = self.path.as_uri() + "?mode=ro"

        with closing(
            sqlite3.connect(
                uri,
                uri=True,
                timeout=10,
            )
        ) as conn:

            conn.row_factory = sqlite3.Row

            row = conn.execute(
                """
                SELECT *
                FROM upload_jobs
                WHERE job_id = ?
                  AND owner_id = ?
                """,
                (job_id, owner_id),
            ).fetchone()

            return (
                self._public(row)
                if row is not None
                else None
            )

    def _update(
        self,
        owner_id,
        job_id,
        allowed,
        **fields,
    ):
        # Only internal callers supply SQL identifiers.

        fields["updated_at"] = time.time()

        assignments = ", ".join(
            f"{key} = ?"
            for key in fields
        )

        placeholders = ", ".join(
            "?"
            for _ in allowed
        )

        with self._transaction() as conn:

            cursor = conn.execute(
                f"UPDATE upload_jobs SET {assignments} "
                f"WHERE job_id = ? AND owner_id = ? "
                f"AND status IN ({placeholders})",
                (
                    *fields.values(),
                    job_id,
                    owner_id,
                    *allowed,
                ),
            )

            if cursor.rowcount != 1:
                raise UploadJobStateError(
                    "Upload job missing or transition rejected"
                )

    def start(
        self,
        owner_id: str,
        job_id: str,
    ) -> None:

        self._update(
            owner_id,
            job_id,
            ("queued",),
            status="processing",
            phase="starting",
        )

    def progress(
        self,
        owner_id: str,
        job_id: str,
        phase: str,
    ) -> None:

        if phase not in PHASES:
            raise ValueError(
                "Unknown upload phase"
            )

        self._update(
            owner_id,
            job_id,
            ("processing",),
            phase=phase,
        )

    def complete(
        self,
        owner_id: str,
        job_id: str,
        result: dict,
    ) -> None:

        # Persist only fields required by
        # the existing upload response.

        exam_id = result.get("exam_id")

        title = result.get("title")

        count = result.get("question_count")

        if (
            not isinstance(exam_id, str)
            or not exam_id.strip()
            or len(exam_id) > 128
            or not isinstance(title, str)
            or len(title) > 1024
            or type(count) is not int
            or count < 0
        ):
            raise ValueError(
                "Invalid upload result"
            )

        payload = json.dumps(
            {
                "exam_id": exam_id,
                "title": title,
                "question_count": count,
            },
            ensure_ascii=False,
            allow_nan=False,
        )

        self._update(
            owner_id,
            job_id,
            ("processing",),
            status="completed",
            phase="completed",
            result_json=payload,
            error=None,
            error_status=None,
        )

    def fail(
        self,
        owner_id: str,
        job_id: str,
        *,
        error_status: int,
        public_error: str,
    ) -> None:

        # Caller must supply a sanitized
        # user-facing message, not str(exc).

        if (
            type(error_status) is not int
            or not 400 <= error_status <= 599
            or not isinstance(public_error, str)
            or not public_error.strip()
            or len(public_error) > 512
        ):
            raise ValueError(
                "Invalid public upload error"
            )

        self._update(
            owner_id,
            job_id,
            ("queued", "processing"),
            status="failed",
            result_json=None,
            error=public_error,
            error_status=error_status,
        )

    def interrupt_active(self) -> int:
        """
        Call at startup ONLY when no
        previous upload worker is alive.
        """

        with self._transaction() as conn:

            cursor = conn.execute("""
                UPDATE upload_jobs

                SET status = 'failed',
                    result_json = NULL,
                    error = ?,
                    error_status = 503,
                    updated_at = ?

                WHERE status IN (
                    'queued',
                    'processing'
                )
            """, (
                INTERRUPTED_MESSAGE,
                time.time(),
            ))

            return cursor.rowcount