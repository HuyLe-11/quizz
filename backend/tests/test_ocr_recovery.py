"""Offline recovery tests using the current SQLite journal schema."""

import sqlite3
import tempfile
import unittest

from contextlib import closing
from pathlib import Path

from backend.engine.ocr_recovery import (
    list_jobs,
    recover_batch,
)


class OCRRecoveryTests(unittest.TestCase):

    def setUp(self):

        tmp = tempfile.TemporaryDirectory()

        self.addCleanup(tmp.cleanup)

        self.path = (
            Path(tmp.name) / "jobs.sqlite3"
        )

        self.scope = "a" * 64

        self.key = "b" * 64

        self.claim = "c" * 32

        with closing(
            sqlite3.connect(self.path)
        ) as conn, conn:

            conn.executescript("""
                CREATE TABLE ocr_jobs (
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

                    PRIMARY KEY (scope, batch_key),

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
                );

                CREATE TABLE ocr_results (
                    scope TEXT NOT NULL,

                    batch_key TEXT NOT NULL,

                    claim_id TEXT NOT NULL,

                    job_id TEXT NOT NULL,

                    payload BLOB NOT NULL,

                    sha256 TEXT NOT NULL,

                    PRIMARY KEY (scope, batch_key),

                    FOREIGN KEY (scope, batch_key)
                        REFERENCES ocr_jobs(scope, batch_key)
                        ON DELETE CASCADE
                );
            """)

        self.add_job(
            self.scope,
            self.key,
            self.claim,
        )


    def add_job(
        self,
        scope,
        key,
        claim,
        state="submitting",
        job_id=None,
    ):

        with closing(
            sqlite3.connect(self.path)
        ) as conn, conn:

            conn.execute(
                """
                INSERT INTO ocr_jobs
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    scope,
                    key,
                    claim,
                    state,
                    job_id,
                    1000,
                ),
            )


    def recover(self, **changes):

        args = dict(
            scope=self.scope,

            batch_key=self.key,

            claim_id=self.claim,

            expected_state="submitting",

            action="attach",

            new_job_id="job-123",

            reason="Verified in provider records",
        )

        args.update(changes)

        return recover_batch(
            self.path,
            **args,
        )


    def audit(self):

        with closing(
            sqlite3.connect(self.path)
        ) as conn:

            return conn.execute(
                """
                SELECT
                    action,
                    old_state,
                    old_job_id,
                    new_job_id

                FROM ocr_recovery_audit

                ORDER BY id
                """
            ).fetchall()


    def test_list_and_dry_run_do_not_write(self):

        before = self.path.read_bytes()

        self.assertEqual(
            list_jobs(self.path)[0]["state"],
            "submitting",
        )

        self.assertEqual(
            self.recover()["new_state"],
            "accepted",
        )

        self.assertEqual(
            self.path.read_bytes(),
            before,
        )


    def test_attach_preserves_claim_and_records_audit(self):

        self.recover(
            apply=True,
            confirmed=True,
        )

        row = list_jobs(self.path)[0]

        self.assertEqual(
            (
                row["state"],
                row["job_id"],
                row["claim_id"],
            ),
            (
                "accepted",
                "job-123",
                self.claim,
            ),
        )

        self.assertEqual(
            self.audit(),
            [
                (
                    "attach",
                    "submitting",
                    None,
                    "job-123",
                )
            ],
        )


    def test_apply_requires_confirmation(self):

        with self.assertRaisesRegex(
            ValueError,
            "confirmation",
        ):

            self.recover(
                apply=True,
            )

        self.assertEqual(
            list_jobs(self.path)[0]["state"],
            "submitting",
        )


    def test_stale_claim_and_state_rejected(self):

        with self.assertRaises(RuntimeError):

            self.recover(
                claim_id="d" * 32,
                apply=True,
                confirmed=True,
            )

        self.recover(
            apply=True,
            confirmed=True,
        )

        with self.assertRaises(RuntimeError):

            self.recover(
                apply=True,
                confirmed=True,
            )

        self.assertEqual(
            len(self.audit()),
            1,
        )


    def test_job_id_already_assigned_to_another_scope(self):

        self.add_job(
            "d" * 64,
            self.key,
            "e" * 32,
            "accepted",
            "job-123",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "already assigned",
        ):

            self.recover(
                apply=True,
                confirmed=True,
            )


    def test_reset_only_removes_target_and_preserves_audit(self):

        self.add_job(
            self.scope,
            "d" * 64,
            "e" * 32,
            "completed",
            "other",
        )

        self.recover(
            apply=True,
            confirmed=True,
        )

        report = self.recover(
            action="reset",
            expected_state="accepted",
            new_job_id=None,
            apply=True,
            confirmed=True,
        )

        self.assertIsNone(
            report["new_state"]
        )

        self.assertEqual(
            [
                row["job_id"]
                for row in list_jobs(self.path)
            ],
            ["other"],
        )

        self.assertEqual(
            self.audit()[-1],
            (
                "reset",
                "accepted",
                "job-123",
                None,
            ),
        )

        self.add_job(
            self.scope,
            self.key,
            "f" * 32,
        )

        with self.assertRaises(RuntimeError):

            self.recover(
                apply=True,
                confirmed=True,
            )


    def test_reset_refuses_existing_cache(self):

        with closing(
            sqlite3.connect(self.path)
        ) as conn, conn:

            conn.execute("""
                UPDATE ocr_jobs

                SET state = 'completed',
                    job_id = 'j1'
            """)

            conn.execute(
                """
                INSERT INTO ocr_results
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self.scope,
                    self.key,
                    self.claim,
                    "j1",
                    b"{}",
                    "hash",
                ),
            )

        with self.assertRaisesRegex(
            RuntimeError,
            "Cache exists",
        ):

            self.recover(
                action="reset",
                expected_state="completed",
                new_job_id=None,
                apply=True,
                confirmed=True,
            )

        self.assertEqual(
            list_jobs(self.path)[0]["cache_bytes"],
            2,
        )


    def test_mutation_failure_rolls_back_audit(self):

        with closing(
            sqlite3.connect(self.path)
        ) as conn, conn:

            conn.execute("""
                CREATE TRIGGER reject_update

                BEFORE UPDATE ON ocr_jobs

                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'test failure'
                    );
                END
            """)

        with self.assertRaises(
            sqlite3.IntegrityError
        ):

            self.recover(
                apply=True,
                confirmed=True,
            )

        self.assertEqual(
            list_jobs(self.path)[0]["state"],
            "submitting",
        )

        with closing(
            sqlite3.connect(self.path)
        ) as conn:

            self.assertIsNone(
                conn.execute("""
                    SELECT name

                    FROM sqlite_master

                    WHERE name = 'ocr_recovery_audit'
                """).fetchone()
            )


    def test_missing_database_is_not_created(self):

        self.path = self.path.with_name(
            "missing.sqlite3"
        )

        with self.assertRaises(
            sqlite3.OperationalError
        ):

            list_jobs(self.path)

        with self.assertRaises(
            sqlite3.OperationalError
        ):

            self.recover(
                apply=True,
                confirmed=True,
            )

        self.assertFalse(
            self.path.exists()
        )


    def test_invalid_inputs_rejected(self):

        for changes in (
            {"scope": "invalid"},

            {"new_job_id": "../job"},

            {"reason": " "},

            {"claim_id": "invalid"},

            {"action": "unknown"},
        ):

            with (
                self.subTest(changes=changes),
                self.assertRaises(ValueError),
            ):

                self.recover(**changes)


if __name__ == "__main__":
    unittest.main()