"""Test independent upload timeouts in the PaddleOCR client."""

import json
import tempfile
import unittest

from pathlib import Path
from unittest.mock import Mock, patch

import requests

from backend.engine import paddle_client


def mock_submit_response():
    response = Mock()
    response.ok = True
    response.status_code = 200
    response.json.return_value = {
        "data": {"jobId": "test-job-123"}
    }
    return response


def mock_status_response():
    response = Mock()
    response.ok = True
    response.status_code = 200
    response.json.return_value = {
        "data": {
            "state": "done",
            "resultUrl": {
                "jsonUrl": "https://example.test/result.jsonl"
            },
        }
    }
    return response


def mock_result_response():
    response = Mock()
    response.ok = True
    response.status_code = 200
    response.text = json.dumps({
        "result": {
            "ocrResults": [{
                "prunedResult": {
                    "rec_texts": [
                        "Question 1",
                        "A. First answer",
                    ]
                }
            }]
        }
    })
    return response


class PaddleUploadTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()

        self.pdf_path = (
            Path(self.temporary.name) / "sample.pdf"
        )
        self.pdf_path.write_bytes(b"%PDF-1.4\nmock")

        self.batch = paddle_client.PDFBatch(
            path=self.pdf_path,
            start_page=0,
            page_count=1,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def process_batch(self):
        return paddle_client._process_batch(
            self.batch,
            model="PP-OCRv6",
            job_url="https://example.test/jobs",
            headers={"Authorization": "Bearer TEST"},
            timeout=(10.0, 180.0),
            poll_timeout=300.0,
        )

    def test_default_upload_socket_timeout(self):
        with patch.dict(
            "os.environ",
            {"PADDLEOCR_UPLOAD_SOCKET_TIMEOUT": "90"},
        ):
            result = paddle_client._upload_timeout(180.0)

        self.assertEqual(result, (90.0, 180.0))

    def test_custom_upload_socket_timeout(self):
        with patch.dict(
            "os.environ",
            {"PADDLEOCR_UPLOAD_SOCKET_TIMEOUT": "60"},
        ):
            result = paddle_client._upload_timeout(180.0)

        self.assertEqual(result, (60.0, 180.0))

    @patch(
        "backend.engine.paddle_client.requests.get"
    )
    @patch(
        "backend.engine.paddle_client.requests.post"
    )
    def test_post_and_get_use_different_timeouts(
        self,
        mock_post,
        mock_get,
    ):
        mock_post.return_value = mock_submit_response()
        mock_get.side_effect = [
            mock_status_response(),
            mock_result_response(),
        ]

        with patch.dict(
            "os.environ",
            {"PADDLEOCR_UPLOAD_SOCKET_TIMEOUT": "90"},
        ):
            job_id, pages, _ = self.process_batch()

        self.assertEqual(job_id, "test-job-123")
        self.assertEqual(len(pages), 1)

        self.assertEqual(
            mock_post.call_args.kwargs["timeout"],
            (90.0, 180.0),
        )

        self.assertEqual(
            mock_get.call_args_list[0].kwargs["timeout"],
            (10.0, 180.0),
        )

        mock_post.assert_called_once()

    @patch(
        "backend.engine.paddle_client.requests.get"
    )
    @patch(
        "backend.engine.paddle_client.requests.post"
    )
    def test_upload_timeout_does_not_retry_post(
        self,
        mock_post,
        mock_get,
    ):
        mock_post.side_effect = (
            requests.exceptions.ConnectionError(
                "Connection aborted",
                TimeoutError(
                    "The write operation timed out"
                ),
            )
        )

        with self.assertRaises(
            paddle_client.PaddleOCRTimeoutError
        ):
            self.process_batch()

        mock_post.assert_called_once()
        mock_get.assert_not_called()

    @patch(
        "backend.engine.paddle_client.time.sleep"
    )
    @patch(
        "backend.engine.paddle_client.requests.get"
    )
    @patch(
        "backend.engine.paddle_client.requests.post"
    )
    def test_get_timeout_retries_without_resubmitting(
        self,
        mock_post,
        mock_get,
        mock_sleep,
    ):
        mock_post.return_value = mock_submit_response()
        mock_get.side_effect = [
            requests.exceptions.ReadTimeout(
                "Temporary network failure"
            ),
            mock_status_response(),
            mock_result_response(),
        ]

        with patch.dict(
            "os.environ",
            {"PADDLEOCR_GET_RETRIES": "3"},
        ):
            job_id, pages, _ = self.process_batch()

        self.assertEqual(job_id, "test-job-123")
        self.assertEqual(len(pages), 1)

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(mock_get.call_count, 3)
        mock_sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
