"""Test transient PaddleOCR failures and safe GET retries."""

import json
import tempfile
import unittest

from pathlib import Path
from unittest.mock import Mock, patch

import requests

from backend.engine import paddle_client as client


JOB_URL = "https://example.test/api/v2/ocr/jobs"
RESULT_URL = "https://example.test/result.jsonl"


def submitted_response():
    response = Mock(ok=True)
    response.status_code = 200
    response.json.return_value = {
        "data": {
            "jobId": "test-job-123",
        },
    }
    return response


def status_response(state="done"):
    response = Mock(ok=True)
    response.status_code = 200

    response.json.return_value = {
        "data": {
            "state": state,
            "resultUrl": {
                "jsonUrl": RESULT_URL,
            },
        },
    }

    return response


def result_response():
    response = Mock(ok=True)
    response.status_code = 200

    response.text = json.dumps({
        "result": {
            "ocrResults": [
                {
                    "prunedResult": {
                        "rec_texts": [
                            "Question 1",
                            "A. First answer",
                            "B. Second answer",
                        ],
                    },
                },
            ],
        },
    })

    return response


def error_response(
    status_code: int,
    retry_after: str | None = None,
):
    response = Mock(ok=False)
    response.status_code = status_code

    response.headers = (
        {"Retry-After": retry_after}
        if retry_after is not None
        else {}
    )

    return response


class PaddleRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = (
            tempfile.TemporaryDirectory()
        )

        self.pdf = (
            Path(self.temp_directory.name)
            / "sample.pdf"
        )

        self.pdf.write_bytes(
            b"%PDF-1.4\nmock content"
        )

        self.batch = client.PDFBatch(
            path=self.pdf,
            start_page=0,
            page_count=1,
        )

        self.environment = patch.dict(
            "os.environ",
            {
                "PADDLEOCR_GET_RETRIES": "3",
                "PADDLEOCR_RESULT_TIMEOUT": "30",
            },
        )

        self.environment.start()

        self.sleep_patch = patch(
            "backend.engine.paddle_client.time.sleep"
        )

        self.mock_sleep = (
            self.sleep_patch.start()
        )

    def tearDown(self):
        self.sleep_patch.stop()
        self.environment.stop()
        self.temp_directory.cleanup()

    def process(self):
        return client._process_batch(
            self.batch,
            model="PP-OCRv6",
            job_url=JOB_URL,
            headers={
                "Authorization": "Bearer TEST",
            },
            timeout=(3.0, 10.0),
            poll_timeout=30.0,
        )

    @patch(
        "backend.engine.paddle_client.requests.post"
    )
    @patch(
        "backend.engine.paddle_client.requests.get"
    )
    def test_polling_timeout_recovers(
        self,
        mock_get,
        mock_post,
    ):
        mock_post.return_value = submitted_response()

        mock_get.side_effect = [
            requests.exceptions.ReadTimeout(
                "Temporary polling timeout"
            ),
            status_response(),
            result_response(),
        ]

        job_id, pages, info = self.process()

        self.assertEqual(
            job_id,
            "test-job-123",
        )

        self.assertEqual(len(pages), 1)

        self.assertEqual(
            pages[0]["pruned_result"]["rec_texts"][0],
            "Question 1",
        )

        self.assertEqual(info, {})

        self.assertEqual(
            mock_get.call_count,
            3,
        )

        mock_post.assert_called_once()
        self.mock_sleep.assert_called_once()

    @patch(
        "backend.engine.paddle_client.requests.post"
    )
    @patch(
        "backend.engine.paddle_client.requests.get"
    )
    def test_result_download_timeout_recovers(
        self,
        mock_get,
        mock_post,
    ):
        mock_post.return_value = submitted_response()

        mock_get.side_effect = [
            status_response(),
            requests.exceptions.ReadTimeout(
                "Temporary result timeout"
            ),
            result_response(),
        ]

        job_id, pages, _ = self.process()

        self.assertEqual(
            job_id,
            "test-job-123",
        )

        self.assertEqual(
            len(pages),
            1,
        )

        self.assertEqual(
            mock_get.call_count,
            3,
        )

        mock_post.assert_called_once()

        result_calls = [
            call
            for call in mock_get.call_args_list
            if RESULT_URL in str(call)
        ]

        self.assertEqual(
            len(result_calls),
            2,
        )

        for call in result_calls:
            self.assertNotIn(
                "headers",
                call.kwargs,
            )

    @patch(
        "backend.engine.paddle_client.requests.post"
    )
    @patch(
        "backend.engine.paddle_client.requests.get"
    )
    def test_http_429_and_503_are_retried(
        self,
        mock_get,
        mock_post,
    ):
        mock_post.return_value = submitted_response()

        mock_get.side_effect = [
            error_response(
                429,
                retry_after="2",
            ),
            error_response(503),
            status_response(),
            result_response(),
        ]

        job_id, pages, _ = self.process()

        self.assertEqual(
            job_id,
            "test-job-123",
        )

        self.assertEqual(
            len(pages),
            1,
        )

        self.assertEqual(
            mock_get.call_count,
            4,
        )

        mock_post.assert_called_once()

        self.assertEqual(
            self.mock_sleep.call_count,
            2,
        )

        self.assertGreaterEqual(
            self.mock_sleep.call_args_list[0].args[0],
            2,
        )

    @patch(
        "backend.engine.paddle_client.requests.post"
    )
    @patch(
        "backend.engine.paddle_client.requests.get"
    )
    def test_repeated_polling_timeout_exhausts_retries(
        self,
        mock_get,
        mock_post,
    ):
        mock_post.return_value = submitted_response()

        mock_get.side_effect = (
            requests.exceptions.ReadTimeout(
                "Persistent polling timeout"
            )
        )

        with self.assertRaises(
            client.PaddleOCRTimeoutError
        ) as captured:
            self.process()

        self.assertIn(
            "test-job-123",
            str(captured.exception),
        )

        self.assertEqual(
            mock_get.call_count,
            4,
        )

        mock_post.assert_called_once()

    @patch(
        "backend.engine.paddle_client.requests.post"
    )
    @patch(
        "backend.engine.paddle_client.requests.get"
    )
    def test_http_401_is_not_retried(
        self,
        mock_get,
        mock_post,
    ):
        mock_post.return_value = submitted_response()

        mock_get.return_value = error_response(
            401
        )

        with self.assertRaises(
            client.PaddleOCRAPIError
        ) as captured:
            self.process()

        self.assertEqual(
            captured.exception.status_code,
            401,
        )

        self.assertEqual(
            mock_get.call_count,
            1,
        )

        self.mock_sleep.assert_not_called()
        mock_post.assert_called_once()

    @patch(
        "backend.engine.paddle_client.requests.post"
    )
    @patch(
        "backend.engine.paddle_client.requests.get"
    )
    def test_upload_timeout_is_not_retried(
        self,
        mock_get,
        mock_post,
    ):
        mock_post.side_effect = (
            requests.exceptions.ReadTimeout(
                "Upload request timed out"
            )
        )

        with self.assertRaises(
            client.PaddleOCRTimeoutError
        ) as captured:
            self.process()

        self.assertIn(
            "PDF upload",
            str(captured.exception),
        )

        mock_post.assert_called_once()
        mock_get.assert_not_called()

    @patch(
        "backend.engine.paddle_client.requests.post"
    )
    @patch(
        "backend.engine.paddle_client.requests.get"
    )
    def test_pending_job_continues_polling(
        self,
        mock_get,
        mock_post,
    ):
        mock_post.return_value = submitted_response()

        mock_get.side_effect = [
            status_response("pending"),
            status_response("running"),
            status_response("done"),
            result_response(),
        ]

        job_id, pages, _ = self.process()

        self.assertEqual(
            job_id,
            "test-job-123",
        )

        self.assertEqual(
            len(pages),
            1,
        )

        self.assertEqual(
            mock_get.call_count,
            4,
        )

        mock_post.assert_called_once()

        self.assertEqual(
            self.mock_sleep.call_count,
            2,
        )


if __name__ == "__main__":
    unittest.main()
