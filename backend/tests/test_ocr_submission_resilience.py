import sqlite3
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from backend.engine import (
    paddle_resilient as resilient,
)

from backend.engine.ocr_job_journal import (
    OCRJobJournal,
    JournalStateError,
)

from backend.engine.ocr_submission_resilience import (
    LocalSubmissionError,
    record_submission_issue,
    report_blocked_submission,
)


class OCRSubmissionResilienceTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        root = Path(temp.name)

        self.journal = OCRJobJournal(
            "a" * 64,
            root / "jobs.sqlite3",
        )

        self.batch = SimpleNamespace(
            path=root / "batch.pdf",
            start_page=0,
            page_count=1,
        )

        self.batch.path.write_bytes(b"%PDF-test")

        self.model = "PP-OCRv6"

        self.key = self.journal.batch_key(
            0,
            1,
            self.model,
        )

        self.primary_key = self.journal.batch_key(
            0,
            1,
            "PaddleOCR-VL-1.6",
        )

        primary = self.journal.reserve(
            self.primary_key
        ).record

        self.journal.accept(
            self.primary_key,
            primary.claim_id,
            "primary-job",
        )

        self.primary = (
            [
                {
                    "markdown_text": "primary"
                }
            ],
            {},
        )

        self.journal.finish_with_result(
            self.primary_key,
            primary.claim_id,
            "primary-job",
            *self.primary,
            page_count=1,
        )

        self.primary_claim = primary.claim_id

        env = patch.dict(
            "os.environ",
            {
                "PADDLEOCR_SUBMIT_MAX_ATTEMPTS": "2",
                "PADDLEOCR_UPLOAD_SOCKET_TIMEOUT": "90",
            },
        )

        env.start()
        self.addCleanup(env.stop)

    def run_batch(self):
        return resilient._process_batch(
            self.batch,
            model=self.model,
            job_url="https://example.invalid/jobs",
            headers={},
            timeout=(5, 10),
            poll_timeout=10,
            recovery=10,
            result_timeout=10,
            journal=self.journal,
        )

    def current(self):
        with self.journal._transaction() as conn:
            return self.journal._read(
                conn,
                self.key,
            )

    def assert_primary_preserved(self):
        self.assertEqual(
            self.journal.load_result(
                self.primary_key,
                self.primary_claim,
                "primary-job",
                page_count=1,
            ),
            self.primary,
        )

    def test_missing_file_releases_claim_without_post(self):
        self.batch.path.unlink()

        with patch.object(
            resilient.requests,
            "post",
        ) as post:
            with self.assertRaises(LocalSubmissionError):
                self.run_batch()

            post.assert_not_called()

        self.assertIsNone(self.current())
        self.assert_primary_preserved()

    def test_connect_timeout_bounded_and_releases(self):
        with patch.object(
            resilient.requests,
            "post",
            side_effect=requests.ConnectTimeout,
        ) as post, patch.object(
            resilient.time,
            "sleep",
        ):
            with self.assertRaises(
                resilient.UnconnectedSubmissionError
            ):
                self.run_batch()

            self.assertEqual(post.call_count, 2)

        self.assertIsNone(self.current())
        self.assert_primary_preserved()

    def test_read_timeout_is_persisted_and_not_retried(self):
        with patch.object(
            resilient.requests,
            "post",
            side_effect=requests.ReadTimeout,
        ) as post:
            with self.assertRaises(
                resilient.client.PaddleOCRTimeoutError
            ):
                self.run_batch()

            record = self.current()

            self.assertEqual(
                record.state,
                "submitting",
            )

            self.assertEqual(
                report_blocked_submission(
                    self.journal,
                    self.key,
                    record.claim_id,
                ),
                (
                    "submit",
                    "PaddleOCRTimeoutError",
                    None,
                    None,
                ),
            )

            with self.assertRaises(JournalStateError):
                self.run_batch()

            post.assert_called_once()

        self.assert_primary_preserved()

    def test_http_error_remains_uncertain(self):
        response = Mock(
            status_code=429,
            ok=False,
        )

        error = resilient.client.PaddleOCRAPIError(
            "submit",
            429,
        )

        with patch.object(
            resilient.requests,
            "post",
            return_value=response,
        ) as post, patch.object(
            resilient.client,
            "_check_response",
            side_effect=error,
        ):
            with self.assertRaises(
                resilient.client.PaddleOCRAPIError
            ):
                self.run_batch()

            post.assert_called_once()

        record = self.current()

        self.assertEqual(
            report_blocked_submission(
                self.journal,
                self.key,
                record.claim_id,
            ),
            (
                "submit",
                "PaddleOCRAPIError",
                429,
                None,
            ),
        )

        self.assert_primary_preserved()

    def test_accept_failure_records_observed_job_id(self):
        response = Mock(
            status_code=200,
            ok=True,
        )

        response.json.return_value = {
            "data": {
                "jobId": "secondary-job",
            },
        }

        with patch.object(
            resilient.requests,
            "post",
            return_value=response,
        ) as post, patch.object(
            self.journal,
            "accept",
            side_effect=sqlite3.OperationalError,
        ):
            with self.assertRaises(
                sqlite3.OperationalError
            ):
                self.run_batch()

            post.assert_called_once()

        record = self.current()

        self.assertEqual(
            report_blocked_submission(
                self.journal,
                self.key,
                record.claim_id,
            ),
            (
                "accept",
                "OperationalError",
                None,
                "secondary-job",
            ),
        )

        self.assert_primary_preserved()

    def test_legacy_reservation_remains_blocked(self):
        claim = self.journal.reserve(
            self.key
        ).record.claim_id

        with patch.object(
            resilient.requests,
            "post",
        ) as post:
            with self.assertRaises(JournalStateError):
                self.run_batch()

            post.assert_not_called()

        self.assertEqual(
            self.current().claim_id,
            claim,
        )

        self.assertIsNone(
            report_blocked_submission(
                self.journal,
                self.key,
                claim,
            )
        )

    def test_stale_claim_cannot_record_issue(self):
        claim = self.journal.reserve(
            self.key
        ).record.claim_id

        self.journal.release_unconnected(
            self.key,
            claim,
        )

        new = self.journal.reserve(
            self.key
        ).record.claim_id

        self.assertFalse(
            record_submission_issue(
                self.journal,
                self.key,
                claim,
                RuntimeError(),
            )
        )

        self.assertIsNone(
            report_blocked_submission(
                self.journal,
                self.key,
                new,
            )
        )

        self.assertNotEqual(claim, new)

    def test_completed_cache_bypasses_post_and_poll(self):
        claim = self.journal.reserve(
            self.key
        ).record.claim_id

        self.journal.accept(
            self.key,
            claim,
            "secondary-job",
        )

        pages = [
            {
                "markdown_text": "secondary",
            }
        ]

        self.journal.finish_with_result(
            self.key,
            claim,
            "secondary-job",
            pages,
            {},
            page_count=1,
        )

        with patch.object(
            resilient.requests,
            "post",
        ) as post, patch.object(
            resilient,
            "_poll",
        ) as poll:
            self.assertEqual(
                self.run_batch(),
                (
                    "secondary-job",
                    pages,
                    {},
                ),
            )

            post.assert_not_called()
            poll.assert_not_called()

        self.assert_primary_preserved()

    def test_missing_job_id_stays_blocked(self):
        response = Mock(
            status_code=200,
            ok=True,
        )

        response.json.return_value = {
            "data": {}
        }

        with patch.object(
            resilient.requests,
            "post",
            return_value=response,
        ) as post:
            with self.assertRaises(RuntimeError):
                self.run_batch()

            with self.assertRaises(JournalStateError):
                self.run_batch()

            post.assert_called_once()

        self.assertEqual(
            self.current().state,
            "submitting",
        )

        self.assert_primary_preserved()

    def test_file_disappears_after_connect_timeout(self):
        def lose_file_during_backoff(_delay):
            self.batch.path.unlink()

        with patch.object(
            resilient.requests,
            "post",
            side_effect=requests.ConnectTimeout,
        ) as post, patch.object(
            resilient.time,
            "sleep",
            side_effect=lose_file_during_backoff,
        ) as sleep:
            with self.assertRaises(LocalSubmissionError):
                self.run_batch()

            post.assert_called_once()
            sleep.assert_called_once()

        self.assertIsNone(self.current())
        self.assert_primary_preserved()

    def test_metadata_failure_does_not_mask_original_error(self):
        with patch.object(
            resilient.requests,
            "post",
            side_effect=requests.ReadTimeout,
        ), patch(
            "backend.engine.ocr_submission_resilience._ensure_table",
            side_effect=sqlite3.OperationalError,
        ):
            with self.assertRaises(
                resilient.client.PaddleOCRTimeoutError
            ):
                self.run_batch()

        self.assertEqual(
            self.current().state,
            "submitting",
        )

        self.assert_primary_preserved()


if __name__ == "__main__":
    unittest.main()