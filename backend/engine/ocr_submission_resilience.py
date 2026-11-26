"""Safe local preflight and durable diagnostics for uncertain OCR submissions."""
from __future__ import annotations

import logging
import re
import time

logger = logging.getLogger("quizpdf")


class LocalSubmissionError(OSError):
    """The PDF could not be opened before any POST in this attempt."""


def open_submission_file(path):
    try:
        return path.open("rb")
    except OSError as exc:
        raise LocalSubmissionError(
            "OCR batch file could not be opened before POST."
        ) from exc


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ocr_submission_issues (
            scope TEXT NOT NULL,
            batch_key TEXT NOT NULL,
            claim_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            error_type TEXT NOT NULL,
            http_status INTEGER,
            observed_job_id TEXT,
            recorded_at REAL NOT NULL,

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


def record_submission_issue(
    journal,
    key,
    claim_id,
    exc,
    *,
    stage="submit",
    observed_job_id=None,
):
    """Best-effort metadata only; never change the
    reservation or mask its error.
    """

    try:
        if stage not in (
            "submit",
            "accept",
        ):
            raise ValueError(
                "Invalid submission stage"
            )

        error_type = type(exc).__name__

        if not re.fullmatch(
            r"[A-Za-z0-9_]{1,100}",
            error_type,
        ):
            error_type = "UnknownError"

        status = getattr(
            exc,
            "status_code",
            None,
        )

        if (
            type(status) is not int
            or not 100 <= status <= 599
        ):
            status = None

        if (
            not isinstance(observed_job_id, str)
            or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,128}",
                observed_job_id,
            )
        ):
            observed_job_id = None

        with journal._transaction() as conn:

            current = journal._read(
                conn,
                key,
            )

            if (
                current is None
                or current.claim_id != claim_id
                or current.state != "submitting"
            ):
                return False

            _ensure_table(conn)

            conn.execute("""
                INSERT INTO ocr_submission_issues (
                    scope,
                    batch_key,
                    claim_id,
                    stage,
                    error_type,
                    http_status,
                    observed_job_id,
                    recorded_at
                )

                VALUES (?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(scope, batch_key)
                DO UPDATE SET
                    claim_id = excluded.claim_id,
                    stage = excluded.stage,
                    error_type = excluded.error_type,
                    http_status = excluded.http_status,
                    observed_job_id = excluded.observed_job_id,
                    recorded_at = excluded.recorded_at

            """, (
                journal.scope,
                key,
                claim_id,
                stage,
                error_type,
                status,
                observed_job_id,
                time.time(),
            ))

        logger.warning(
            "paddleocr:journal status=submission_unconfirmed "
            "stage=%s error_type=%s http=%s observed_job=%s",
            stage,
            error_type,
            status,
            observed_job_id,
        )

        return True

    except Exception as metadata_error:

        logger.warning(
            "paddleocr:journal status=issue_record_failed type=%s",
            type(metadata_error).__name__,
        )

        return False


def report_blocked_submission(
    journal,
    key,
    claim_id,
):
    """Explain a blocked claim without resubmitting
    or exposing raw error text.
    """

    try:

        with journal._transaction() as conn:

            current = journal._read(
                conn,
                key,
            )

            if (
                current is None
                or current.claim_id != claim_id
                or current.state != "submitting"
            ):
                return None

            _ensure_table(conn)

            row = conn.execute("""
                SELECT
                    stage,
                    error_type,
                    http_status,
                    observed_job_id

                FROM ocr_submission_issues

                WHERE scope = ?
                  AND batch_key = ?
                  AND claim_id = ?

            """, (
                journal.scope,
                key,
                claim_id,
            )).fetchone()

        if row is None:

            logger.warning(
                "paddleocr:journal "
                "status=submission_blocked "
                "reason=legacy_or_interrupted"
            )

            return None

        logger.warning(
            "paddleocr:journal status=submission_blocked "
            "stage=%s error_type=%s http=%s observed_job=%s",
            *row,
        )

        return row

    except Exception as metadata_error:

        logger.warning(
            "paddleocr:journal status=issue_read_failed type=%s",
            type(metadata_error).__name__,
        )

        return None