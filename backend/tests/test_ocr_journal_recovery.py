import hashlib
import io
import json
import sqlite3
import tempfile
import unittest

from contextlib import closing, redirect_stdout
from pathlib import Path

from backend.engine.ocr_job_journal import (
    OCRJobJournal,
)

from backend.engine.ocr_journal_recovery import (
    PRIMARY_MODEL,
    SUPPLEMENTAL_MODEL,
    RecoveryInspectionError,
    inspect_recovery,
    main,
)


class OCRJournalRecoveryTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.root = Path(temp.name)
        self.path = self.root / "jobs.sqlite3"

        self.scope = "a" * 64

        self.primary_job_id = "primary-job"

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

        self.journal = self.create_primary(
            self.scope
        )

        self.target_claim = self.journal.reserve(
            self.target_key
        ).record.claim_id

    def create_primary(
        self,
        scope,
    ):
        journal = OCRJobJournal(
            scope,
            self.path,
        )

        record = journal.reserve(
            self.primary_key
        ).record

        journal.accept(
            self.primary_key,
            record.claim_id,
            self.primary_job_id,
        )

        journal.finish_with_result(
            self.primary_key,
            record.claim_id,
            self.primary_job_id,
            [
                {
                    "markdown_text": "Primary result",
                    "layout_blocks": [],
                }
            ],
            {},
            page_count=1,
        )

        return journal

    def inspect(self, **kwargs):
        params = {
            "path": self.path,
            "primary_job_id": self.primary_job_id,
        }

        params.update(kwargs)

        return inspect_recovery(
            **params
        )

    def execute(
        self,
        sql,
        params=(),
    ):
        with closing(
            sqlite3.connect(self.path)
        ) as conn:

            conn.execute(
                "PRAGMA foreign_keys=ON"
            )

            with conn:
                conn.execute(
                    sql,
                    params,
                )

    def snapshot(self):
        with closing(
            sqlite3.connect(self.path)
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

    def test_valid_primary_and_blocked_target(self):
        result = self.inspect()

        self.assertEqual(
            result["mode"],
            "read_only",
        )

        self.assertEqual(
            result["scope"],
            self.scope,
        )

        self.assertEqual(
            result["primary"]["job"]["state"],
            "completed",
        )

        self.assertTrue(
            result["primary"]["cache"]["valid"]
        )

        self.assertEqual(
            result["primary"]["cache"]["cached_pages"],
            1,
        )

        self.assertEqual(
            result["target"]["classification"],
            "submission_unconfirmed",
        )

        self.assertEqual(
            result["target"]["job"]["claim_id"],
            self.target_claim,
        )

        self.assertTrue(
            result["structural_checks_passed"]
        )

        self.assertEqual(
            result["blockers"],
            [],
        )

        self.assertFalse(
            result["mutation_authorized"]
        )

    def test_inspection_does_not_modify_journal(self):
        before = self.snapshot()

        self.inspect()
        self.inspect()

        after = self.snapshot()

        self.assertEqual(
            before,
            after,
        )

    def test_missing_database_is_not_created(self):
        missing = self.root / "missing.sqlite3"

        with self.assertRaises(
            RecoveryInspectionError
        ):
            self.inspect(path=missing)

        self.assertFalse(
            missing.exists()
        )

    def test_missing_primary_is_rejected(self):
        with self.assertRaises(
            RecoveryInspectionError
        ):
            self.inspect(
                primary_job_id="missing-job"
            )

    def test_ambiguous_scope_is_rejected(self):
        self.create_primary(
            "b" * 64
        )

        with self.assertRaises(
            RecoveryInspectionError
        ):
            self.inspect()

    def test_explicit_scope_resolves_ambiguity(self):
        self.create_primary(
            "b" * 64
        )

        result = self.inspect(
            scope=self.scope
        )

        self.assertEqual(
            result["scope"],
            self.scope,
        )

        self.assertEqual(
            result["target"]["job"]["claim_id"],
            self.target_claim,
        )

    def test_invalid_scope_is_rejected(self):
        with self.assertRaises(
            RecoveryInspectionError
        ):
            self.inspect(
                scope="invalid-scope"
            )

    def test_corrupted_primary_checksum_blocks_recovery(self):
        self.execute(
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

        result = self.inspect()

        self.assertFalse(
            result["primary"]["cache"]["valid"]
        )

        self.assertEqual(
            result["primary"]["cache"]["reason"],
            "checksum_mismatch",
        )

        self.assertFalse(
            result["structural_checks_passed"]
        )

        self.assertIn(
            "primary_cache_not_valid",
            result["blockers"],
        )

    def test_cache_identity_mismatch_blocks_recovery(self):
        self.execute(
            """
            UPDATE ocr_results
            SET claim_id = ?
            WHERE scope = ?
              AND batch_key = ?
            """,
            (
                "wrong-claim",
                self.scope,
                self.primary_key,
            ),
        )

        result = self.inspect()

        self.assertFalse(
            result["primary"]["cache"]["valid"]
        )

        self.assertEqual(
            result["primary"]["cache"]["reason"],
            "identity_mismatch",
        )

        self.assertFalse(
            result["structural_checks_passed"]
        )

    def test_invalid_page_count_blocks_recovery(self):
        payload = json.dumps(
            {
                "version": 1,
                "pages": [],
                "data_info": {},
            }
        ).encode("utf-8")

        self.execute(
            """
            UPDATE ocr_results
            SET payload = ?,
                sha256 = ?
            WHERE scope = ?
              AND batch_key = ?
            """,
            (
                payload,
                hashlib.sha256(
                    payload
                ).hexdigest(),
                self.scope,
                self.primary_key,
            ),
        )

        result = self.inspect()

        self.assertTrue(
            result["primary"]["cache"]["checksum_valid"]
        )

        self.assertFalse(
            result["primary"]["cache"]["structure_valid"]
        )

        self.assertFalse(
            result["structural_checks_passed"]
        )

    def test_missing_primary_cache_blocks_recovery(self):
        self.execute(
            """
            DELETE FROM ocr_results
            WHERE scope = ?
              AND batch_key = ?
            """,
            (
                self.scope,
                self.primary_key,
            ),
        )

        result = self.inspect()

        self.assertEqual(
            result["primary"]["cache"]["reason"],
            "cache_missing",
        )

        self.assertFalse(
            result["structural_checks_passed"]
        )

    def test_missing_target_does_not_require_recovery(self):
        self.journal.release_unconnected(
            self.target_key,
            self.target_claim,
        )

        result = self.inspect()

        self.assertEqual(
            result["target"]["classification"],
            "missing",
        )

        self.assertFalse(
            result["target"]["blocked"]
        )

        self.assertFalse(
            result["structural_checks_passed"]
        )

    def test_accepted_target_is_not_unconfirmed(self):
        self.journal.accept(
            self.target_key,
            self.target_claim,
            "secondary-job",
        )

        result = self.inspect()

        self.assertEqual(
            result["target"]["classification"],
            "existing_job_available",
        )

        self.assertFalse(
            result["target"]["blocked"]
        )

    def test_completed_target_preserves_cache(self):
        self.journal.accept(
            self.target_key,
            self.target_claim,
            "secondary-job",
        )

        self.journal.finish_with_result(
            self.target_key,
            self.target_claim,
            "secondary-job",
            [
                {
                    "markdown_text": "Secondary result"
                }
            ],
            {},
            page_count=1,
        )

        before = self.snapshot()

        result = self.inspect()

        self.assertEqual(
            result["target"]["classification"],
            "completed_with_cache",
        )

        self.assertTrue(
            result["target"]["cache"]["valid"]
        )

        self.assertFalse(
            result["structural_checks_passed"]
        )

        self.assertEqual(
            before,
            self.snapshot(),
        )

    def test_cli_returns_read_only_report(self):
        output = io.StringIO()

        with redirect_stdout(output):
            code = main(
                [
                    "inspect",
                    "--database",
                    str(self.path),
                    "--primary-job-id",
                    self.primary_job_id,
                ]
            )

        self.assertEqual(
            code,
            0,
        )

        report = json.loads(
            output.getvalue()
        )

        self.assertEqual(
            report["mode"],
            "read_only",
        )

        self.assertTrue(
            report["structural_checks_passed"]
        )

        self.assertFalse(
            report["mutation_authorized"]
        )

    def test_cli_missing_database_returns_error(self):
        output = io.StringIO()
        missing = self.root / "missing.sqlite3"

        with redirect_stdout(output):
            code = main(
                [
                    "inspect",
                    "--database",
                    str(missing),
                    "--primary-job-id",
                    self.primary_job_id,
                ]
            )

        self.assertEqual(
            code,
            1,
        )

        report = json.loads(
            output.getvalue()
        )

        self.assertEqual(
            report["error_type"],
            "RecoveryInspectionError",
        )

        self.assertFalse(
            missing.exists()
        )


if __name__ == "__main__":
    unittest.main()