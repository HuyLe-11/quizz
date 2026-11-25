"""Local SQLite tests; no provider calls or external database."""

import hashlib
import tempfile
import unittest

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from backend.engine.ocr_job_journal import (
    JournalStateError,
    OCRJobJournal,
    upload_scope,
)


class OCRJobJournalTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()

        self.addCleanup(
            tmp.cleanup
        )

        self.path = (
            Path(tmp.name)
            / "journal"
            / "jobs.sqlite3"
        )

        self.identity = {
            "user_id": str(uuid4()),

            "filename": "vatlli.pdf",

            "source_sha256":
                hashlib.sha256(
                    b"%PDF-test"
                ).hexdigest(),

            "model": "PaddleOCR-VL-1.6",

            "plan_fingerprint":
                hashlib.sha256(
                    b"plan-v1"
                ).hexdigest(),
        }

        self.scope = upload_scope(
            **self.identity
        )

        self.key = OCRJobJournal.batch_key(
            0,
            1,
            self.identity["model"],
        )

        self.journal = self.reopen()


    def reopen(self):
        return OCRJobJournal(
            self.scope,
            self.path,
        )


    def test_reservation_survives_reopen(self):

        first = self.journal.reserve(
            self.key
        )

        second = self.reopen().reserve(
            self.key
        )

        self.assertTrue(
            first.created
        )

        self.assertFalse(
            second.created
        )

        self.assertEqual(
            first.record,
            second.record,
        )

        self.assertEqual(
            second.record.state,
            "submitting",
        )

        self.assertIsNone(
            second.record.job_id
        )


    def test_known_job_survives_reopen(self):

        claim = (
            self.journal.reserve(self.key)
            .record.claim_id
        )

        self.journal.accept(
            self.key,
            claim,
            "job-123",
        )

        found = self.reopen().reserve(
            self.key
        )

        self.assertFalse(
            found.created
        )

        self.assertEqual(
            found.record.state,
            "accepted",
        )

        self.assertEqual(
            found.record.job_id,
            "job-123",
        )


    def test_completed_job_is_retained(self):

        claim = (
            self.journal.reserve(self.key)
            .record.claim_id
        )

        self.journal.accept(
            self.key,
            claim,
            "job-123",
        )

        self.journal.finish(
            self.key,
            claim,
        )

        self.journal.finish(
            self.key,
            claim,
        )

        found = self.reopen().reserve(
            self.key
        )

        self.assertEqual(
            found.record.state,
            "completed",
        )

        self.assertEqual(
            found.record.job_id,
            "job-123",
        )

        self.assertFalse(
            found.created
        )


    def test_explicit_provider_failure_is_retained(self):

        claim = (
            self.journal.reserve(self.key)
            .record.claim_id
        )

        self.journal.accept(
            self.key,
            claim,
            "job-123",
        )

        self.journal.finish(
            self.key,
            claim,
            failed=True,
        )

        self.assertEqual(
            self.reopen()
            .reserve(self.key)
            .record.state,
            "failed",
        )

        with self.assertRaises(
            JournalStateError
        ):
            self.journal.finish(
                self.key,
                claim,
            )


    def test_scope_isolation(self):

        self.journal.reserve(
            self.key
        )

        for change in (
            {
                "user_id":
                    str(uuid4())
            },
            {
                "filename":
                    "renamed.pdf"
            },
            {
                "source_sha256":
                    "a" * 64
            },
            {
                "model":
                    "PP-OCRv6"
            },
            {
                "plan_fingerprint":
                    "b" * 64
            },
        ):
            with self.subTest(
                change=change
            ):
                scope = upload_scope(
                    **(
                        self.identity | change
                    )
                )

                other = OCRJobJournal(
                    scope,
                    self.path,
                )

                self.assertTrue(
                    other.reserve(self.key)
                    .created
                )


    def test_batch_and_fallback_model_isolation(self):

        self.journal.reserve(
            self.key
        )

        for key in (
            OCRJobJournal.batch_key(
                1,
                1,
                self.identity["model"],
            ),

            OCRJobJournal.batch_key(
                0,
                2,
                self.identity["model"],
            ),

            OCRJobJournal.batch_key(
                0,
                1,
                "PP-OCRv6",
            ),
        ):
            self.assertTrue(
                self.journal.reserve(key)
                .created
            )


    def test_concurrent_reservation_has_single_winner(self):

        with ThreadPoolExecutor(
            max_workers=8
        ) as executor:

            results = list(
                executor.map(
                    lambda _:
                        self.reopen()
                        .reserve(self.key),

                    range(16),
                )
            )

        self.assertEqual(
            sum(
                r.created
                for r in results
            ),
            1,
        )

        self.assertEqual(
            len({
                r.record.claim_id
                for r in results
            }),
            1,
        )


    def test_release_requires_original_claim_and_submitting_state(self):

        old = (
            self.journal.reserve(self.key)
            .record.claim_id
        )

        with self.assertRaises(
            JournalStateError
        ):
            self.journal.release_unconnected(
                self.key,
                "wrong",
            )

        self.journal.release_unconnected(
            self.key,
            old,
        )

        new = (
            self.journal.reserve(self.key)
            .record.claim_id
        )

        self.assertNotEqual(
            old,
            new,
        )

        with self.assertRaises(
            JournalStateError
        ):
            self.journal.accept(
                self.key,
                old,
                "old-job",
            )

        self.journal.accept(
            self.key,
            new,
            "new-job",
        )

        with self.assertRaises(
            JournalStateError
        ):
            self.journal.release_unconnected(
                self.key,
                new,
            )


    def test_job_id_cannot_be_replaced(self):

        claim = (
            self.journal.reserve(self.key)
            .record.claim_id
        )

        self.journal.accept(
            self.key,
            claim,
            "job-123",
        )

        self.journal.accept(
            self.key,
            claim,
            "job-123",
        )

        with self.assertRaises(
            JournalStateError
        ):
            self.journal.accept(
                self.key,
                claim,
                "different",
            )

        self.assertEqual(
            self.reopen()
            .reserve(self.key)
            .record.job_id,
            "job-123",
        )


    def test_cannot_finish_unknown_submission(self):

        claim = (
            self.journal.reserve(self.key)
            .record.claim_id
        )

        with self.assertRaises(
            JournalStateError
        ):
            self.journal.finish(
                self.key,
                claim,
            )

        with self.assertRaises(
            ValueError
        ):
            self.journal.accept(
                self.key,
                claim,
                "../job",
            )

        self.assertEqual(
            self.reopen()
            .reserve(self.key)
            .record.state,
            "submitting",
        )


if __name__ == "__main__":
    unittest.main()