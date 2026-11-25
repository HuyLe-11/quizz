import tempfile
import unittest

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from backend.network.upload_job_registry import (
    INTERRUPTED_MESSAGE,
    UploadJobRegistry,
    UploadJobStateError,
)


class UploadJobRegistryTests(unittest.TestCase):

    def setUp(self):

        tmp = tempfile.TemporaryDirectory()

        self.addCleanup(tmp.cleanup)

        self.path = (
            Path(tmp.name)
            / "upload-jobs"
            / "jobs.sqlite3"
        )

        self.registry = UploadJobRegistry(
            self.path
        )

        self.owner = "user-a"

        self.result = {
            "exam_id": "exam-a",
            "title": "Vật lí",
            "question_count": 9,
        }

    def create(self):

        return self.registry.create(
            self.owner
        )["job_id"]

    def start(self):

        job_id = self.create()

        self.registry.start(
            self.owner,
            job_id,
        )

        return job_id

    def test_initial_contract(self):

        job = self.registry.create(
            self.owner
        )

        self.assertEqual(
            job,
            {
                "job_id": job["job_id"],
                "status": "queued",
                "phase": "queued",
                "result": None,
                "error": None,
                "error_status": None,
            },
        )

        self.assertEqual(
            self.registry.get(
                self.owner,
                job["job_id"],
            ),
            job,
        )

    def test_completed_survives_reopen(self):

        job_id = self.start()

        self.registry.progress(
            self.owner,
            job_id,
            "cloud_save",
        )

        self.registry.complete(
            self.owner,
            job_id,
            self.result,
        )

        reopened = UploadJobRegistry(
            self.path
        )

        self.assertEqual(
            reopened.interrupt_active(),
            0,
        )

        job = reopened.get(
            self.owner,
            job_id,
        )

        self.assertEqual(
            job["status"],
            "completed",
        )

        self.assertEqual(
            job["result"],
            self.result,
        )

    def test_failed_survives_reopen(self):

        job_id = self.start()

        self.registry.progress(
            self.owner,
            job_id,
            "ocr",
        )

        self.registry.fail(
            self.owner,
            job_id,
            error_status=504,
            public_error="OCR timeout",
        )

        job = UploadJobRegistry(
            self.path
        ).get(
            self.owner,
            job_id,
        )

        self.assertEqual(
            (
                job["status"],
                job["phase"],
                job["error_status"],
            ),
            (
                "failed",
                "ocr",
                504,
            ),
        )

        self.assertEqual(
            job["error"],
            "OCR timeout",
        )

    def test_owner_isolation(self):

        job_id = self.start()

        self.assertIsNone(
            self.registry.get(
                "user-b",
                job_id,
            )
        )

        self.assertIsNone(
            self.registry.get(
                self.owner,
                "missing",
            )
        )

        with self.assertRaises(
            UploadJobStateError
        ):

            self.registry.complete(
                "user-b",
                job_id,
                self.result,
            )

        self.assertEqual(
            self.registry.get(
                self.owner,
                job_id,
            )["status"],
            "processing",
        )

    def test_restart_marks_only_active_jobs(self):

        queued = self.create()

        running = self.start()

        self.registry.progress(
            self.owner,
            running,
            "cloud_save",
        )

        completed = self.start()

        self.registry.complete(
            self.owner,
            completed,
            self.result,
        )

        failed = self.start()

        self.registry.fail(
            self.owner,
            failed,
            error_status=502,
            public_error="Failed",
        )

        reopened = UploadJobRegistry(
            self.path
        )

        self.assertEqual(
            reopened.interrupt_active(),
            2,
        )

        for job_id, phase in (
            (queued, "queued"),
            (running, "cloud_save"),
        ):

            job = reopened.get(
                self.owner,
                job_id,
            )

            self.assertEqual(
                (
                    job["status"],
                    job["phase"],
                    job["error_status"],
                ),
                (
                    "failed",
                    phase,
                    503,
                ),
            )

            self.assertEqual(
                job["error"],
                INTERRUPTED_MESSAGE,
            )

        self.assertEqual(
            reopened.get(
                self.owner,
                completed,
            )["result"],
            self.result,
        )

        self.assertEqual(
            reopened.get(
                self.owner,
                failed,
            )["error_status"],
            502,
        )

        self.assertEqual(
            reopened.interrupt_active(),
            0,
        )

    def test_terminal_status_cannot_be_overwritten(self):

        job_id = self.start()

        self.registry.complete(
            self.owner,
            job_id,
            self.result,
        )

        for action in (
            lambda: self.registry.start(
                self.owner,
                job_id,
            ),

            lambda: self.registry.progress(
                self.owner,
                job_id,
                "ocr",
            ),

            lambda: self.registry.fail(
                self.owner,
                job_id,
                error_status=500,
                public_error="Failed",
            ),
        ):

            with self.assertRaises(
                UploadJobStateError
            ):

                action()

        self.assertEqual(
            self.registry.get(
                self.owner,
                job_id,
            )["result"],
            self.result,
        )

    def test_invalid_result_does_not_change_state(self):

        job_id = self.start()

        with self.assertRaises(
            ValueError
        ):

            self.registry.complete(
                self.owner,
                job_id,
                {
                    **self.result,
                    "question_count": True,
                },
            )

        self.assertEqual(
            self.registry.get(
                self.owner,
                job_id,
            )["status"],
            "processing",
        )

    def test_result_whitelist(self):

        job_id = self.start()

        self.registry.complete(
            self.owner,
            job_id,
            {
                **self.result,
                "access_token":
                    "test-token-not-for-storage",

                "pdf":
                    "test-raw-pdf-not-for-storage",
            },
        )

        self.assertEqual(
            self.registry.get(
                self.owner,
                job_id,
            )["result"],
            self.result,
        )

        self.assertNotIn(
            b"test-token-not-for-storage",
            self.path.read_bytes(),
        )

        self.assertNotIn(
            b"test-raw-pdf-not-for-storage",
            self.path.read_bytes(),
        )

    def test_queued_cannot_complete(self):

        job_id = self.create()

        with self.assertRaises(
            UploadJobStateError
        ):

            self.registry.complete(
                self.owner,
                job_id,
                self.result,
            )

        self.assertEqual(
            self.registry.get(
                self.owner,
                job_id,
            )["status"],
            "queued",
        )

    def test_concurrent_terminal_transitions_only_one_wins(self):

        job_id = self.start()

        barrier = Barrier(2)

        def finish(success):

            barrier.wait(timeout=5)

            try:

                if success:

                    self.registry.complete(
                        self.owner,
                        job_id,
                        self.result,
                    )

                else:

                    self.registry.fail(
                        self.owner,
                        job_id,
                        error_status=500,
                        public_error="Failed",
                    )

                return True

            except UploadJobStateError:

                return False

        with ThreadPoolExecutor(
            max_workers=2
        ) as pool:

            futures = [
                pool.submit(
                    finish,
                    value,
                )
                for value in (True, False)
            ]

            self.assertEqual(
                sorted(
                    f.result(timeout=10)
                    for f in futures
                ),
                [False, True],
            )

        self.assertIn(
            self.registry.get(
                self.owner,
                job_id,
            )["status"],
            {"completed", "failed"},
        )


if __name__ == "__main__":
    unittest.main()