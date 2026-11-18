import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from fastapi.testclient import TestClient

from backend import db
from backend.network.main import app
from backend.network.upload_jobs import (
    UploadJobManager,
    UploadQueueFull,
)


class UploadJobTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

        root = Path(self.temp.name)

        self.originals = (
            db.DATABASE_PATH,
            db.STORAGE_DIR,
            db.ASSETS_DIR,
            db.LEGACY_JSON_PATH,
        )

        db.DATABASE_PATH = root / "quiz.sqlite3"
        db.STORAGE_DIR = root / "storage"
        db.ASSETS_DIR = db.STORAGE_DIR / "assets"
        db.LEGACY_JSON_PATH = root / "absent.json"

        with fitz.open() as document:
            page = document.new_page()

            page.insert_text(
                (40, 70),
                "Question 1: Which statement is "
                "a valid example of an answer?",
            )

            page.insert_text(
                (40, 120),
                "A. First option",
            )

            page.insert_text(
                (40, 160),
                "B. Second option",
            )

            self.pdf = document.tobytes()

        self.context = TestClient(app)

        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(
            None,
            None,
            None,
        )

        (
            db.DATABASE_PATH,
            db.STORAGE_DIR,
            db.ASSETS_DIR,
            db.LEGACY_JSON_PATH,
        ) = self.originals

        self.temp.cleanup()

    def submit(self, content=None):
        return self.client.post(
            "/api/upload/jobs",
            files={
                "file": (
                    "example.pdf",
                    self.pdf
                    if content is None
                    else content,
                    "application/pdf",
                ),
            },
        )

    def wait_status(
        self,
        url,
        terminal_only=True,
    ):
        deadline = time.monotonic() + 5

        while time.monotonic() < deadline:
            response = self.client.get(url)

            self.assertEqual(
                response.status_code,
                200,
            )

            job = response.json()["data"]

            if (
                not terminal_only
                or job["status"] in {
                    "completed",
                    "failed",
                }
            ):
                return job

            time.sleep(0.02)

        self.fail(
            "Job did not finish within test deadline"
        )

    def test_accepted_health_and_completed_exam(self):
        entered = threading.Event()
        release = threading.Event()

        markdown = (
            "Question 1: Which statement is "
            "a valid example of an answer?\n"
            "A. First option\n"
            "B. Second option"
        )

        def fake_ocr(*args):
            entered.set()

            if not release.wait(5):
                raise RuntimeError(
                    "Test OCR did not release"
                )

            return markdown, {
                "pages": [
                    {
                        "markdown_text": markdown,
                    },
                ],
            }

        with patch(
            "backend.network.main.recognize_pdf",
            side_effect=fake_ocr,
        ):
            try:
                response = self.submit()

                self.assertEqual(
                    response.status_code,
                    202,
                    response.text,
                )

                self.assertTrue(
                    entered.wait(2)
                )

                url = response.headers["Location"]

                self.assertEqual(
                    response.json()["data"]["status"],
                    "queued",
                )

                health = self.client.get(
                    "/api/health"
                )

                self.assertEqual(
                    health.status_code,
                    200,
                )

                job = self.wait_status(
                    url,
                    terminal_only=False,
                )

                self.assertEqual(
                    job["phase"],
                    "ocr",
                )

                self.assertEqual(
                    job["status"],
                    "processing",
                )

            finally:
                release.set()

            completed = self.wait_status(url)

        self.assertEqual(
            completed["status"],
            "completed",
            completed,
        )

        exam_id = completed["result"]["exam_id"]

        exam = self.client.get(
            f"/api/exams/{exam_id}"
        )

        self.assertEqual(
            exam.status_code,
            200,
        )

        self.assertEqual(
            exam.json()["data"]["question_count"],
            1,
        )

    def test_failure_is_reported_without_creating_exam(self):
        with patch(
            "backend.network.main.recognize_pdf",
            side_effect=RuntimeError(
                "Backend is missing "
                "PADDLEOCR_ACCESS_TOKEN."
            ),
        ):
            response = self.submit()

            self.assertEqual(
                response.status_code,
                202,
            )

            job = self.wait_status(
                response.headers["Location"]
            )

        self.assertEqual(
            job["status"],
            "failed",
        )

        self.assertEqual(
            job["phase"],
            "ocr",
        )

        self.assertEqual(
            job["error_status"],
            503,
        )

        self.assertIsNone(
            job["result"]
        )

        exams = self.client.get(
            "/api/exams"
        )

        self.assertEqual(
            exams.json()["data"],
            [],
        )

    def test_invalid_pdf_and_unknown_job(self):
        invalid = self.submit(
            b"invalid"
        )

        self.assertEqual(
            invalid.status_code,
            400,
        )

        unknown = self.client.get(
            "/api/upload/jobs/nonexistent"
        )

        self.assertEqual(
            unknown.status_code,
            404,
        )

    def test_queue_capacity(self):
        manager = UploadJobManager(
            max_active=1
        )

        release = threading.Event()

        try:
            job = manager.submit(
                lambda progress: {
                    "done": release.wait(5),
                }
            )

            with self.assertRaises(
                UploadQueueFull
            ):
                manager.submit(
                    lambda progress: {}
                )

        finally:
            release.set()
            manager.shutdown()

        self.assertEqual(
            manager.get(
                job["job_id"]
            )["status"],
            "completed",
        )


if __name__ == "__main__":
    unittest.main()
