"""Regression tests for safe asynchronous PDF job failures."""

import unittest

import requests
from fastapi import HTTPException

from backend.cloud_repository import CloudRepositoryError
from backend.engine.paddle_client import (
    PaddleOCRAPIError,
    PaddleOCRJobFailedError,
    PaddleOCRNoTextError,
    PaddleOCRTimeoutError,
)
from backend.network.upload_jobs import UploadJobManager


class UploadJobErrorTests(unittest.TestCase):
    def run_failed_job(self, error):
        manager = UploadJobManager()
        calls = []

        def fail(progress):
            calls.append("started")
            progress("ocr")
            raise error

        try:
            job = manager.submit(fail)
            manager.shutdown()
            result = manager.get(job["job_id"])
        finally:
            # shutdown() is idempotent for ThreadPoolExecutor.
            manager.shutdown()

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["phase"], "ocr")
        self.assertIsNone(result["result"])
        self.assertEqual(calls, ["started"])
        return result

    def test_ocr_timeout_returns_504(self):
        job = self.run_failed_job(
            PaddleOCRTimeoutError("sensitive provider details")
        )
        self.assertEqual(job["error_status"], 504)
        self.assertIn("PaddleOCR", job["error"])
        self.assertNotIn("sensitive", job["error"])

    def test_ocr_no_text_returns_422(self):
        job = self.run_failed_job(PaddleOCRNoTextError("empty"))
        self.assertEqual(job["error_status"], 422)

    def test_ocr_provider_rate_limit_returns_503(self):
        job = self.run_failed_job(PaddleOCRAPIError("status", 429))
        self.assertEqual(job["error_status"], 503)

    def test_ocr_provider_auth_error_is_not_user_401(self):
        job = self.run_failed_job(PaddleOCRAPIError("submit", 401))
        self.assertEqual(job["error_status"], 502)

    def test_ocr_job_failed_returns_502(self):
        job = self.run_failed_job(PaddleOCRJobFailedError("secret"))
        self.assertEqual(job["error_status"], 502)
        self.assertNotIn("secret", job["error"])

    def test_supabase_failure_returns_502(self):
        job = self.run_failed_job(
            CloudRepositoryError("database key details")
        )
        self.assertEqual(job["error_status"], 502)
        self.assertNotIn("database key details", job["error"])

    def test_missing_backend_token_returns_503(self):
        job = self.run_failed_job(
            RuntimeError("Backend is missing PADDLEOCR_ACCESS_TOKEN.")
        )
        self.assertEqual(job["error_status"], 503)

    def test_nested_network_error_returns_503(self):
        try:
            raise requests.ConnectionError("connection down")
        except requests.ConnectionError as cause:
            try:
                raise RuntimeError("could not connect") from cause
            except RuntimeError as wrapper:
                job = self.run_failed_job(wrapper)
        self.assertEqual(job["error_status"], 503)

    def test_unknown_error_remains_safe_500(self):
        job = self.run_failed_job(KeyError("private content"))
        self.assertEqual(job["error_status"], 500)
        self.assertNotIn("private content", job["error"])

    def test_http_exception_preserves_existing_contract(self):
        job = self.run_failed_job(
            HTTPException(403, detail="Not allowed")
        )
        self.assertEqual(job["error_status"], 403)
        self.assertEqual(job["error"], "Not allowed")


if __name__ == "__main__":
    unittest.main()
