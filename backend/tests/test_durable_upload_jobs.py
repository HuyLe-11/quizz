import os
import tempfile
import unittest

from pathlib import Path
from threading import Event
from unittest.mock import patch

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from backend.network.durable_upload_jobs import (
    DurableUploadJobs,
)

from backend.network.upload_job_registry import (
    UploadJobRegistry,
)

from backend.network.upload_jobs import (
    UploadQueueFull,
)


RESULT = {
    "exam_id": "exam-a",
    "title": "Vật lí",
    "question_count": 9,
}


class DurableUploadJobsTests(unittest.TestCase):

    def setUp(self):

        temp = tempfile.TemporaryDirectory()

        self.addCleanup(temp.cleanup)

        self.path = (
            Path(temp.name)
            / "jobs.sqlite3"
        )

    def manager(self, **kwargs):

        manager = DurableUploadJobs(
            self.path,
            **kwargs,
        )

        self.addCleanup(manager.shutdown)

        return manager

    def test_completed_survives_restart(self):

        manager = self.manager()

        def work(progress):

            progress("ocr")

            progress("cloud_save")

            return RESULT

        job_id = manager.submit(
            "a",
            work,
        )["job_id"]

        manager.shutdown()

        reopened = self.manager()

        job = reopened.get(
            "a",
            job_id,
        )

        self.assertEqual(
            job["status"],
            "completed",
        )

        self.assertEqual(
            job["result"],
            RESULT,
        )

        self.assertIsNone(
            reopened.get("b", job_id)
        )

    def test_interrupted_is_not_reexecuted(self):

        registry = UploadJobRegistry(self.path)

        job_id = registry.create("a")["job_id"]

        registry.start(
            "a",
            job_id,
        )

        registry.progress(
            "a",
            job_id,
            "ocr",
        )

        manager = self.manager()

        job = manager.get(
            "a",
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
                503,
            ),
        )

        self.assertIsNone(job["result"])

    def test_second_manager_refused_while_worker_active(self):

        manager = self.manager()

        entered = Event()

        release = Event()

        def work(progress):

            progress("ocr")

            entered.set()

            if not release.wait(10):

                raise RuntimeError(
                    "test timeout"
                )

            return RESULT

        job_id = manager.submit(
            "a",
            work,
        )["job_id"]

        try:

            self.assertTrue(
                entered.wait(5)
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "registry lock",
            ):

                DurableUploadJobs(self.path)

            self.assertEqual(
                manager.get(
                    "a",
                    job_id,
                )["status"],
                "processing",
            )

        finally:

            release.set()

            manager.shutdown()

        reopened = self.manager()

        self.assertEqual(
            reopened.get(
                "a",
                job_id,
            )["status"],
            "completed",
        )

    def test_queue_limit_and_shutdown(self):

        manager = self.manager(
            max_active=1
        )

        release = Event()

        def work(progress):

            if not release.wait(10):

                raise RuntimeError(
                    "test timeout"
                )

            return RESULT

        manager.submit(
            "a",
            work,
        )

        try:

            with self.assertRaises(
                UploadQueueFull
            ):

                manager.submit(
                    "b",
                    lambda _: RESULT,
                )

        finally:

            release.set()

            manager.shutdown()

        with self.assertRaises(
            UploadQueueFull
        ):

            manager.submit(
                "a",
                lambda _: RESULT,
            )

    def test_failure_detail_not_persisted(self):

        manager = self.manager()

        def work(progress):

            raise HTTPException(
                400,
                detail="private-token-or-url",
            )

        job_id = manager.submit(
            "a",
            work,
        )["job_id"]

        manager.shutdown()

        job = self.manager().get(
            "a",
            job_id,
        )

        self.assertEqual(
            (
                job["status"],
                job["error_status"],
            ),
            (
                "failed",
                400,
            ),
        )

        self.assertNotIn(
            "private-token-or-url",
            job["error"],
        )

        self.assertNotIn(
            b"private-token-or-url",
            self.path.read_bytes(),
        )

    def test_api_status_survives_lifespan_restart(self):

        from backend.network import cloud_api

        def identity(request: Request):

            return (
                request.headers.get(
                    "x-test-user",
                    "a",
                ),
                "test-token",
            )

        def work(
            contents,
            filename,
            model,
            owner,
            token,
            progress,
        ):

            progress("ocr")

            return RESULT

        original = dict(
            cloud_api.app.dependency_overrides
        )

        cloud_api.app.dependency_overrides[
            cloud_api.authenticated_identity
        ] = identity

        try:

            with patch.dict(
                os.environ,
                {
                    "QUIZPDF_UPLOAD_JOB_DB": str(self.path),
                    "VERCEL": "",
                },
            ), patch.object(
                cloud_api,
                "process_cloud_pdf",
                side_effect=work,
            ) as process:

                with TestClient(
                    cloud_api.app
                ) as client:

                    response = client.post(
                        "/api/upload/jobs",
                        files={
                            "file": (
                                "test.pdf",
                                b"%PDF-1.4\n",
                                "application/pdf",
                            ),
                        },
                    )

                    self.assertEqual(
                        response.status_code,
                        202,
                        response.text,
                    )

                    job_id = (
                        response.json()["data"]["job_id"]
                    )

                with TestClient(
                    cloud_api.app
                ) as client:

                    response = client.get(
                        f"/api/upload/jobs/{job_id}"
                    )

                    self.assertEqual(
                        response.status_code,
                        200,
                    )

                    self.assertEqual(
                        response.json()["data"]["result"],
                        RESULT,
                    )

                    self.assertEqual(
                        response.json()["data"]["status"],
                        "completed",
                    )

                    self.assertEqual(
                        client.get(
                            f"/api/upload/jobs/{job_id}",
                            headers={
                                "x-test-user": "b",
                            },
                        ).status_code,
                        404,
                    )

                self.assertEqual(
                    process.call_count,
                    1,
                )

        finally:

            cloud_api.app.dependency_overrides.clear()

            cloud_api.app.dependency_overrides.update(
                original
            )


if __name__ == "__main__":
    unittest.main()