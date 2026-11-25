"""Maintenance regression tests with a temporary real SQLite journal."""

import sqlite3
import tempfile
import unittest

from contextlib import closing
from pathlib import Path

from backend.engine.ocr_cache_maintenance import (
    DAY,
    maintain_cache,
)

from backend.engine.ocr_job_journal import (
    OCRJobJournal,
)


class OCRCacheMaintenanceTests(unittest.TestCase):

    NOW = 2_000_000_000

    def setUp(self):

        tmp = tempfile.TemporaryDirectory()

        self.addCleanup(tmp.cleanup)

        self.path = (
            Path(tmp.name)
            / "journal"
            / "jobs.sqlite3"
        )


    def add_job(
        self,
        scope,
        start,
        age,
        state="completed",
    ):

        journal = OCRJobJournal(
            scope,
            self.path,
        )

        key = journal.batch_key(
            start,
            1,
            "PaddleOCR-VL-1.6",
        )

        claim = (
            journal.reserve(key)
            .record.claim_id
        )

        job_id = f"job-{start}"

        if state != "submitting":

            journal.accept(
                key,
                claim,
                job_id,
            )

        if state == "completed":

            journal.finish_with_result(
                key,
                claim,
                job_id,
                [{"text": "example"}],
                {},
                page_count=1,
            )

        elif state == "failed":

            journal.finish(
                key,
                claim,
                failed=True,
            )

        with closing(
            sqlite3.connect(self.path)
        ) as conn, conn:

            conn.execute(
                """
                UPDATE ocr_jobs

                SET updated_at = ?

                WHERE scope = ?
                    AND batch_key = ?
                """,
                (
                    self.NOW - age * DAY,
                    scope,
                    key,
                ),
            )

        return journal, key, claim, job_id


    def run_policy(self, **kwargs):

        return maintain_cache(
            self.path,
            now=self.NOW,
            **kwargs,
        )


    def rows(self, table):

        with closing(
            sqlite3.connect(self.path)
        ) as conn:

            return conn.execute(
                f"""
                SELECT *
                FROM {table}
                ORDER BY scope, batch_key
                """
            ).fetchall()


    def test_dry_run_never_mutates(self):

        self.add_job(
            "a" * 64,
            0,
            8,
        )

        before = self.rows(
            "ocr_results"
        )

        report = self.run_policy()

        self.assertEqual(
            report["mode"],
            "dry-run",
        )

        self.assertEqual(
            report["results_selected"],
            1,
        )

        self.assertEqual(
            self.rows("ocr_results"),
            before,
        )


    def test_expired_payload_removed_but_job_id_preserved(self):

        journal, key, claim, job_id = (
            self.add_job(
                "a" * 64,
                0,
                8,
            )
        )

        self.add_job(
            "b" * 64,
            0,
            2,
        )

        jobs = self.rows(
            "ocr_jobs"
        )

        report = self.run_policy(
            apply=True
        )

        self.assertEqual(
            report["results_selected"],
            1,
        )

        self.assertEqual(
            self.rows("ocr_jobs"),
            jobs,
        )

        self.assertEqual(
            journal.reserve(key).record.job_id,
            job_id,
        )

        self.assertIsNone(
            journal.load_result(
                key,
                claim,
                job_id,
                page_count=1,
            )
        )

        self.assertEqual(
            len(self.rows("ocr_results")),
            1,
        )


    def test_budget_evicts_oldest_terminal_scope_first(self):

        self.add_job(
            "a" * 64,
            0,
            4,
        )

        self.add_job(
            "b" * 64,
            0,
            2,
        )

        total = self.run_policy()[
            "payload_bytes_before"
        ]

        report = self.run_policy(
            apply=True,
            max_bytes=total // 2,
        )

        self.assertTrue(
            report["budget_met"]
        )

        self.assertEqual(
            report["results_selected"],
            1,
        )

        self.assertEqual(
            self.rows("ocr_results")[0][0],
            "b" * 64,
        )


    def test_recent_scope_protected_even_under_budget_pressure(self):

        self.add_job(
            "a" * 64,
            0,
            8,
        )

        self.add_job(
            "a" * 64,
            1,
            0.5,
        )

        report = self.run_policy(
            apply=True,
            max_bytes=0,
        )

        self.assertEqual(
            report["results_selected"],
            0,
        )

        self.assertFalse(
            report["budget_met"]
        )

        self.assertEqual(
            len(self.rows("ocr_results")),
            2,
        )


    def test_unresolved_scopes_and_stale_jobs_are_retained(self):

        for scope, state in (
            ("a" * 64, "accepted"),
            ("b" * 64, "submitting"),
        ):

            self.add_job(
                scope,
                0,
                20,
            )

            self.add_job(
                scope,
                1,
                19,
                state,
            )

        jobs = self.rows(
            "ocr_jobs"
        )

        report = self.run_policy(
            apply=True,
            max_bytes=0,
        )

        self.assertEqual(
            report["results_selected"],
            0,
        )

        self.assertEqual(
            report["stale_accepted"],
            1,
        )

        self.assertEqual(
            report["stale_submitting"],
            1,
        )

        self.assertEqual(
            self.rows("ocr_jobs"),
            jobs,
        )

        self.assertEqual(
            len(self.rows("ocr_results")),
            2,
        )


    def test_scope_evicted_as_unit(self):

        self.add_job(
            "a" * 64,
            0,
            8,
        )

        self.add_job(
            "a" * 64,
            1,
            8,
        )

        self.add_job(
            "a" * 64,
            2,
            8,
            "failed",
        )

        report = self.run_policy(
            apply=True
        )

        self.assertEqual(
            report["scopes_selected"],
            1,
        )

        self.assertEqual(
            report["results_selected"],
            2,
        )

        self.assertEqual(
            len(self.rows("ocr_jobs")),
            3,
        )

        self.assertEqual(
            self.rows("ocr_results"),
            [],
        )


    def test_delete_failure_rolls_back_all_scopes(self):

        self.add_job(
            "a" * 64,
            0,
            10,
        )

        self.add_job(
            "b" * 64,
            0,
            9,
        )

        with closing(
            sqlite3.connect(self.path)
        ) as conn, conn:

            conn.execute(
                f"""
                CREATE TRIGGER reject_delete

                BEFORE DELETE ON ocr_results

                WHEN OLD.scope = '{'b' * 64}'

                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'test failure'
                    );
                END
                """
            )

        before = self.rows(
            "ocr_results"
        )

        with self.assertRaises(
            sqlite3.IntegrityError
        ):

            self.run_policy(
                apply=True
            )

        self.assertEqual(
            self.rows("ocr_results"),
            before,
        )


    def test_missing_database_not_created(self):

        for apply in (False, True):

            with self.subTest(apply=apply):

                with self.assertRaises(
                    sqlite3.OperationalError
                ):

                    self.run_policy(
                        apply=apply
                    )

                self.assertFalse(
                    self.path.exists()
                )


    def test_invalid_configuration(self):

        for kwargs in (
            {"ttl_days": 0},
            {"max_bytes": -1},
            {"grace_hours": 0},
        ):

            with (
                self.subTest(kwargs=kwargs),
                self.assertRaises(ValueError),
            ):

                self.run_policy(
                    **kwargs
                )


if __name__ == "__main__":
    unittest.main()