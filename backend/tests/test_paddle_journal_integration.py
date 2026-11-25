"""Exercise journal and result-cache integration without provider calls."""

import sqlite3
import tempfile
import unittest

from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pymupdf
import requests

from backend.engine import paddle_client as client
from backend.engine import paddle_resilient as resilient
from backend.engine import ocr_job_journal as journal_module

from backend.engine.ocr_job_journal import (
    JournalStateError,
)

from backend.tests.test_paddle_resilient import (
    submitted,
    done,
    result,
)


class PaddleJournalIntegrationTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()

        self.addCleanup(tmp.cleanup)

        self.pdf = Path(tmp.name) / "source.pdf"

        self.db = Path(tmp.name) / "jobs.sqlite3"

        self.owner = str(uuid4())

        with pymupdf.open() as doc:
            for _ in range(2):
                doc.new_page().insert_text(
                    (40, 70),
                    "Question 1: Example",
                )

            doc.save(self.pdf)

        self.env = patch.dict(
            "os.environ",
            {
                "PADDLEOCR_ACCESS_TOKEN":
                    "test-token",

                "JOB_URL":
                    "https://ocr.example/jobs",

                "PADDLEOCR_MAX_PAGES_PER_JOB":
                    "1",

                "PADDLEOCR_MAX_UPLOAD_MB":
                    "1.5",

                "PADDLEOCR_SUBMIT_MAX_ATTEMPTS":
                    "2",

                "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL":
                    "PP-OCRv6",
            },
        )

        self.env.start()

        self.addCleanup(self.env.stop)

        for name, target in (
            (
                "post",
                patch.object(
                    resilient.requests,
                    "post",
                ),
            ),
            (
                "get",
                patch.object(
                    client,
                    "_get_with_retries",
                ),
            ),
            (
                "sleep",
                patch.object(
                    resilient.time,
                    "sleep",
                ),
            ),
        ):
            setattr(
                self,
                name,
                target.start(),
            )

            self.addCleanup(target.stop)


    def run_ocr(self, owner=None):
        return resilient.recognize_pdf(
            str(self.pdf),
            "PaddleOCR-VL-1.6",
            user_id=owner or self.owner,
            filename="exam.pdf",
            journal_path=self.db,
        )


    def test_resume_completed_and_accepted_batches(self):

        self.post.side_effect = [
            submitted("j1"),
            submitted("j2"),
        ]

        self.get.side_effect = [
            done("j1"),
            result("one"),

            client.PaddleOCRTimeoutError(
                "poll"
            ),

            client.PaddleOCRTimeoutError(
                "poll"
            ),
        ]

        with self.assertRaises(
            client.PaddleOCRTimeoutError
        ):
            self.run_ocr()

        self.get.reset_mock()

        # Batch 1 must be loaded locally.
        # Only batch 2 should contact PaddleOCR.
        self.get.side_effect = [
            done("j2"),
            result("two"),
        ]

        text, raw = self.run_ocr()

        self.assertEqual(
            self.get.call_count,
            2,
        )

        self.assertTrue(
            self.get.call_args_list[0]
            .args[0].endswith("/j2")
        )

        self.assertEqual(
            text,
            "one\n\ntwo",
        )

        self.assertEqual(
            raw["job_ids"],
            ["j1", "j2"],
        )

        self.assertEqual(
            self.post.call_count,
            2,
        )


    def test_ambiguous_post_blocks_next_invocation(self):

        self.post.side_effect = (
            requests.exceptions.ReadTimeout(
                "submit"
            )
        )

        with self.assertRaises(
            client.PaddleOCRTimeoutError
        ):
            self.run_ocr()

        with self.assertRaisesRegex(
            JournalStateError,
            "unconfirmed",
        ):
            self.run_ocr()

        self.post.assert_called_once()

        self.get.assert_not_called()


    def test_exhausted_connect_timeout_releases_claim(self):

        self.post.side_effect = (
            requests.exceptions.ConnectTimeout(
                "connect"
            )
        )

        with self.assertRaises(
            client.PaddleOCRTimeoutError
        ):
            self.run_ocr()

        self.post.side_effect = [
            submitted("j1"),
            submitted("j2"),
        ]

        self.get.side_effect = [
            done("j1"),
            result("one"),
            done("j2"),
            result("two"),
        ]

        self.run_ocr()

        self.assertEqual(
            self.post.call_count,
            4,
        )


    def test_mismatched_pages_can_be_fetched_again(self):

        self.post.side_effect = [
            submitted("j1"),
            submitted("j2"),
        ]

        self.get.side_effect = [
            done("j1"),
            result("one", "extra"),
        ]

        with self.assertRaisesRegex(
            RuntimeError,
            "page mismatch",
        ):
            self.run_ocr()

        self.get.side_effect = [
            done("j1"),
            result("one"),
            done("j2"),
            result("two"),
        ]

        self.run_ocr()

        self.assertEqual(
            self.post.call_count,
            2,
        )


    def test_owner_isolation(self):

        self.post.side_effect = [
            submitted("j1"),
            submitted("j2"),
            submitted("j3"),
            submitted("j4"),
        ]

        self.get.side_effect = [
            done("j1"),
            result("one"),
            done("j2"),
            result("two"),
            done("j3"),
            result("three"),
            done("j4"),
            result("four"),
        ]

        self.run_ocr()

        text, _ = self.run_ocr(
            str(uuid4())
        )

        self.assertEqual(
            text,
            "three\n\nfour",
        )

        self.assertEqual(
            self.post.call_count,
            4,
        )


    def test_explicit_failed_job_reuses_fallback(self):

        from unittest.mock import Mock

        failed = Mock()

        failed.json.return_value = {
            "data": {
                "state": "failed",
            },
        }

        self.post.side_effect = [
            submitted("j1"),
            submitted("f1"),
            submitted("j2"),
        ]

        self.get.side_effect = [
            failed,
            done("f1"),
            result(
                "one",
                basic=True,
            ),
            done("j2"),
            result("two"),
        ]

        self.run_ocr()

        self.get.reset_mock()

        self.get.side_effect = AssertionError(
            "Unexpected network GET"
        )

        _, raw = self.run_ocr()

        self.assertEqual(
            raw["job_ids"],
            ["f1", "j2"],
        )

        self.assertEqual(
            self.post.call_count,
            3,
        )

        self.get.assert_not_called()


    def complete(self):

        self.post.side_effect = [
            submitted("j1"),
            submitted("j2"),
        ]

        self.get.side_effect = [
            done("j1"),
            result("one"),
            done("j2"),
            result("two"),
        ]

        return self.run_ocr()


    def test_completed_results_survive_reopen_without_network(self):

        first = self.complete()

        self.get.reset_mock()

        self.get.side_effect = AssertionError(
            "Unexpected network GET"
        )

        self.assertEqual(
            self.run_ocr(),
            first,
        )

        self.get.assert_not_called()

        self.assertEqual(
            self.post.call_count,
            2,
        )


    def test_corrupted_cache_fetches_same_job(self):

        self.complete()

        with closing(
            sqlite3.connect(self.db)
        ) as conn, conn:

            conn.execute(
                """
                UPDATE ocr_results
                SET payload = ?
                WHERE job_id = 'j1'
                """,
                (b"{}",),
            )

        self.get.reset_mock()

        self.get.side_effect = [
            done("j1"),
            result("one"),
        ]

        self.assertEqual(
            self.run_ocr()[0],
            "one\n\ntwo",
        )

        self.assertEqual(
            self.get.call_count,
            2,
        )

        self.assertEqual(
            self.post.call_count,
            2,
        )

        self.get.reset_mock()

        self.get.side_effect = AssertionError(
            "Unexpected GET"
        )

        self.run_ocr()

        self.get.assert_not_called()


    def test_old_completed_row_without_cache_is_recovered(self):

        self.complete()

        # Simulate the journal schema before
        # persistent result caching was introduced.
        with closing(
            sqlite3.connect(self.db)
        ) as conn, conn:

            conn.execute(
                "DROP TABLE ocr_results"
            )

        self.get.reset_mock()

        self.get.side_effect = [
            done("j1"),
            result("one"),
            done("j2"),
            result("two"),
        ]

        self.assertEqual(
            self.run_ocr()[0],
            "one\n\ntwo",
        )

        self.assertEqual(
            self.get.call_count,
            4,
        )

        self.assertEqual(
            self.post.call_count,
            2,
        )


    def test_oversized_cache_is_skipped_without_resubmission(self):

        with patch.object(
            journal_module,
            "MAX_RESULT_BYTES",
            1,
        ):

            self.complete()

            with closing(
                sqlite3.connect(self.db)
            ) as conn, conn:

                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM ocr_results"
                    ).fetchone()[0],
                    0,
                )

                self.assertEqual(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM ocr_jobs
                        WHERE state = 'completed'
                        """
                    ).fetchone()[0],
                    2,
                )

            self.get.side_effect = [
                done("j1"),
                result("one"),
                done("j2"),
                result("two"),
            ]

            self.assertEqual(
                self.run_ocr()[0],
                "one\n\ntwo",
            )

            self.assertEqual(
                self.post.call_count,
                2,
            )


    def test_result_insert_failure_does_not_commit_completed(self):

        self.post.side_effect = [
            submitted("j1"),
            submitted("j2"),
        ]

        # Stop after the Job ID has been persisted.
        self.get.side_effect = [
            client.PaddleOCRTimeoutError(
                "poll"
            )
        ] * 2

        with self.assertRaises(
            client.PaddleOCRTimeoutError
        ):
            self.run_ocr()

        # Force SQLite to reject result insertion.
        with closing(
            sqlite3.connect(self.db)
        ) as conn, conn:

            conn.execute("""
                CREATE TRIGGER reject_result
                BEFORE INSERT ON ocr_results

                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'test failure'
                    );
                END
            """)

        self.get.side_effect = [
            done("j1"),
            result("one"),
        ]

        with self.assertRaises(
            sqlite3.IntegrityError
        ):
            self.run_ocr()

        # A failed cache write must leave the
        # previously accepted Job ID intact.
        with closing(
            sqlite3.connect(self.db)
        ) as conn, conn:

            self.assertEqual(
                conn.execute(
                    "SELECT state FROM ocr_jobs"
                ).fetchone()[0],
                "accepted",
            )

            conn.execute(
                "DROP TRIGGER reject_result"
            )

        self.get.side_effect = [
            done("j1"),
            result("one"),
            done("j2"),
            result("two"),
        ]

        self.assertEqual(
            self.run_ocr()[0],
            "one\n\ntwo",
        )

        self.assertEqual(
            self.post.call_count,
            2,
        )


if __name__ == "__main__":
    unittest.main()