import sqlite3
import tempfile
import unittest

from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.engine.ocr_job_journal import (
    OCRJobJournal,
)

from backend.engine.ocr_journal_repair import (
    PRIMARY_MODEL,
    SUPPLEMENTAL_MODEL,
    RESUME_CONFIRMATION,
    RETRY_CONFIRMATION,
    RecoveryRepairError,
    inspect_repair,
    repair_submission,
)

from backend.engine.ocr_submission_resilience import (
    _ensure_table,
)


class OCRJournalRepairTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.root = Path(temp.name)

        self.path = (
            self.root / "jobs.sqlite3"
        )

        self.scope = "a" * 64

        self.primary_job_id = "primary-job"

        self.journal = OCRJobJournal(
            self.scope,
            self.path,
        )

        self.primary_key = OCRJobJournal.batch_key(
            0,
            1,
            PRIMARY_MODEL,
        )

        self.target_key = OCRJobJournal.batch_key(
            0,
            1,
            SUPPLEMENTAL_MODEL,
        )

        primary = self.journal.reserve(
            self.primary_key
        ).record

        self.primary_claim = primary.claim_id

        self.journal.accept(
            self.primary_key,
            self.primary_claim,
            self.primary_job_id,
        )

        self.primary_pages = [
            {
                "markdown_text": "Primary OCR result"
            }
        ]

        self.primary_info = {}

        self.journal.finish_with_result(
            self.primary_key,
            self.primary_claim,
            self.primary_job_id,
            self.primary_pages,
            self.primary_info,
            page_count=1,
        )

        self.target_claim = self.journal.reserve(
            self.target_key
        ).record.claim_id

    def inspect(self):
        return inspect_repair(
            path=self.path,
            primary_job_id=self.primary_job_id,
        )

    def repair(self, **overrides):
        params = {
            "path": self.path,
            "primary_job_id": self.primary_job_id,
            "scope": self.scope,
            "expected_claim": self.target_claim,
            "action": "resume",
            "confirmation": RESUME_CONFIRMATION,
            "workers_stopped": True,
            "job_id": "secondary-job",
        }

        params.update(overrides)

        return repair_submission(
            **params
        )

    def retry(self, **overrides):
        params = {
            "action": "retry",
            "confirmation": RETRY_CONFIRMATION,
            "job_id": None,
        }

        params.update(overrides)

        return self.repair(
            **params
        )

    def current_target(self):
        with self.journal._transaction() as conn:

            return self.journal._read(
                conn,
                self.target_key,
            )

    def snapshot(self, path=None):
        path = path or self.path

        with closing(
            sqlite3.connect(path)
        ) as conn:

            return (
                conn.execute(
                    """
                    SELECT *
                    FROM ocr_jobs
                    ORDER BY scope, batch_key
                    """
                ).fetchall(),
                conn.execute(
                    """
                    SELECT *
                    FROM ocr_results
                    ORDER BY scope, batch_key
                    """
                ).fetchall(),
            )

    def backups(self):
        return list(
            self.root.glob(
                "jobs.before-recovery-*.sqlite3"
            )
        )

    def assert_primary_preserved(self):
        cached = self.journal.load_result(
            self.primary_key,
            self.primary_claim,
            self.primary_job_id,
            page_count=1,
        )

        self.assertEqual(
            cached,
            (
                self.primary_pages,
                self.primary_info,
            ),
        )

    def add_issue(
        self,
        *,
        observed_job_id=None,
    ):
        with self.journal._transaction() as conn:

            _ensure_table(
                conn
            )

            conn.execute(
                """
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
                """,
                (
                    self.scope,
                    self.target_key,
                    self.target_claim,
                    (
                        "accept"
                        if observed_job_id
                        else "submit"
                    ),
                    "OperationalError",
                    None,
                    observed_job_id,
                    1.0,
                ),
            )

    def test_inspect_legacy_reservation_is_read_only(self):
        before = self.snapshot()

        result = self.inspect()

        self.assertEqual(
            result["mode"],
            "read_only",
        )

        self.assertIsNone(
            result["submission_issue"]
        )

        self.assertTrue(
            result["submission_metadata_checked"]
        )

        self.assertFalse(
            result["mutation_authorized"]
        )

        self.assertTrue(
            result["structural_checks_passed"]
        )

        self.assertEqual(
            result["recommendation"],
            "locate_existing_job_or_confirm_retry",
        )

        self.assertEqual(
            before,
            self.snapshot(),
        )

        self.assertEqual(
            self.backups(),
            [],
        )

    def test_inspect_observed_job_recommends_resume(self):
        self.add_issue(
            observed_job_id="secondary-job"
        )

        result = self.inspect()

        self.assertEqual(
            result["observed_job_id"],
            "secondary-job",
        )

        self.assertEqual(
            result["recommendation"],
            "verify_and_resume",
        )

        self.assertEqual(
            result["submission_issue"]["stage"],
            "accept",
        )

    def test_resume_preserves_primary_and_creates_backup(self):
        before = self.snapshot()

        result = self.repair()

        current = self.current_target()

        self.assertEqual(
            current.state,
            "accepted",
        )

        self.assertEqual(
            current.job_id,
            "secondary-job",
        )

        self.assertEqual(
            current.claim_id,
            self.target_claim,
        )

        self.assertTrue(
            result["committed"]
        )

        self.assertTrue(
            result["primary_cache_preserved"]
        )

        self.assertFalse(
            result["new_job_submitted"]
        )

        self.assertEqual(
            result["provider_requests"],
            0,
        )

        backup = Path(
            result["backup"]
        )

        self.assertTrue(
            backup.is_file()
        )

        self.assertEqual(
            before,
            self.snapshot(backup),
        )

        self.assert_primary_preserved()

    def test_resume_matching_observed_job(self):
        self.add_issue(
            observed_job_id="secondary-job"
        )

        result = self.repair()

        self.assertEqual(
            result["new_state"],
            "accepted",
        )

        self.assertEqual(
            self.current_target().job_id,
            "secondary-job",
        )

        self.assert_primary_preserved()

    def test_resume_rejects_mismatched_observed_job(self):
        self.add_issue(
            observed_job_id="real-secondary-job"
        )

        before = self.snapshot()

        with self.assertRaises(
            RecoveryRepairError
        ):
            self.repair()

        self.assertEqual(
            before,
            self.snapshot(),
        )

        self.assertEqual(
            self.backups(),
            [],
        )

    def test_retry_releases_only_target_reservation(self):
        before = self.snapshot()

        result = self.retry()

        self.assertIsNone(
            self.current_target()
        )

        self.assertEqual(
            result["new_state"],
            "reservation_released",
        )

        self.assertFalse(
            result["new_job_submitted"]
        )

        self.assertEqual(
            before,
            self.snapshot(
                Path(result["backup"])
            ),
        )

        new = self.journal.reserve(
            self.target_key
        )

        self.assertTrue(
            new.created
        )

        self.assertNotEqual(
            new.record.claim_id,
            self.target_claim,
        )

        self.assert_primary_preserved()

    def test_retry_rejects_observed_job(self):
        self.add_issue(
            observed_job_id="secondary-job"
        )

        before = self.snapshot()

        with self.assertRaises(
            RecoveryRepairError
        ):
            self.retry()

        self.assertEqual(
            before,
            self.snapshot(),
        )

        self.assertEqual(
            self.backups(),
            [],
        )

    def test_invalid_confirmation_prevents_mutation(self):
        before = self.snapshot()

        with self.assertRaises(
            RecoveryRepairError
        ):
            self.repair(
                confirmation="INVALID"
            )

        self.assertEqual(
            before,
            self.snapshot(),
        )

        self.assertEqual(
            self.backups(),
            [],
        )

    def test_workers_must_be_stopped(self):
        before = self.snapshot()

        with self.assertRaises(
            RecoveryRepairError
        ):
            self.repair(
                workers_stopped=False
            )

        self.assertEqual(
            before,
            self.snapshot(),
        )

        self.assertEqual(
            self.backups(),
            [],
        )

    def test_stale_claim_prevents_mutation(self):
        self.journal.release_unconnected(
            self.target_key,
            self.target_claim,
        )

        current = self.journal.reserve(
            self.target_key
        ).record

        before = self.snapshot()

        with self.assertRaises(
            RecoveryRepairError
        ):
            self.repair()

        self.assertEqual(
            before,
            self.snapshot(),
        )

        self.assertEqual(
            self.current_target().claim_id,
            current.claim_id,
        )

        self.assertEqual(
            self.backups(),
            [],
        )

    def test_primary_job_id_cannot_be_reused(self):
        before = self.snapshot()

        with self.assertRaises(
            RecoveryRepairError
        ):
            self.repair(
                job_id=self.primary_job_id
            )

        self.assertEqual(
            before,
            self.snapshot(),
        )

        self.assertEqual(
            self.backups(),
            [],
        )

    def test_duplicate_job_id_in_another_scope_is_rejected(self):
        other = OCRJobJournal(
            "b" * 64,
            self.path,
        )

        claim = other.reserve(
            self.target_key
        ).record.claim_id

        other.accept(
            self.target_key,
            claim,
            "secondary-job",
        )

        before = self.snapshot()

        with self.assertRaises(
            RecoveryRepairError
        ):
            self.repair()

        self.assertEqual(
            before,
            self.snapshot(),
        )

        self.assertEqual(
            self.backups(),
            [],
        )

    def test_corrupted_primary_cache_prevents_repair(self):
        with self.journal._transaction() as conn:

            conn.execute(
                """
                UPDATE ocr_results
                SET sha256 = ?
                WHERE scope = ?
                  AND batch_key = ?
                """,
                (
                    "0" * 64,
                    self.scope,
                    self.primary_key,
                ),
            )

        before = self.snapshot()

        with self.assertRaises(
            RecoveryRepairError
        ):
            self.repair()

        self.assertEqual(
            before,
            self.snapshot(),
        )

        self.assertEqual(
            self.backups(),
            [],
        )

    def test_backup_failure_prevents_mutation(self):
        before = self.snapshot()

        with patch(
            "backend.engine.ocr_journal_repair._backup_database",
            side_effect=OSError(
                "Simulated backup failure"
            ),
        ):
            with self.assertRaises(
                OSError
            ):
                self.repair()

        self.assertEqual(
            before,
            self.snapshot(),
        )

        self.assert_primary_preserved()

    def test_failure_after_update_rolls_back_transaction(self):
        before = self.snapshot()

        from backend.engine import (
            ocr_journal_repair as repair_module,
        )

        original = repair_module._apply_resume

        def fail_after_update(*args, **kwargs):
            original(
                *args,
                **kwargs,
            )

            raise RuntimeError(
                "Simulated interruption"
            )

        with patch.object(
            repair_module,
            "_apply_resume",
            side_effect=fail_after_update,
        ):
            with self.assertRaises(
                RuntimeError
            ):
                self.repair()

        self.assertEqual(
            before,
            self.snapshot(),
        )

        self.assertEqual(
            len(self.backups()),
            1,
        )

        self.assert_primary_preserved()

    def test_repeated_resume_is_rejected(self):
        self.repair()

        before = self.snapshot()

        with self.assertRaises(
            RecoveryRepairError
        ):
            self.repair()

        self.assertEqual(
            before,
            self.snapshot(),
        )

        self.assertEqual(
            len(self.backups()),
            1,
        )

    def test_repeated_retry_is_rejected(self):
        self.retry()

        before = self.snapshot()

        with self.assertRaises(
            RecoveryRepairError
        ):
            self.retry()

        self.assertEqual(
            before,
            self.snapshot(),
        )

        self.assertEqual(
            len(self.backups()),
            1,
        )


if __name__ == "__main__":
    unittest.main()