"""Tests for OCR upload timeouts without real API requests."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from backend.engine.paddle_client import (
    PaddleOCRTimeoutError,
    recognize_pdf,
)


class SubmitTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "vatlli.pdf"
        self.path.write_bytes(
            b"%PDF-" + b"a" * 1023424
        )

        self.env = patch.dict(os.environ, {
            "PADDLEOCR_ACCESS_TOKEN": "dummy-test-token",
            "JOB_URL": "https://example.invalid/jobs",
            "PADDLEOCR_SUBMIT_TIMEOUT": "90",
            "PADDLEOCR_REQUEST_TIMEOUT": "20",
            "PADDLEOCR_POLL_TIMEOUT": "75",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_wrapped_ssl_write_timeout(self):
        error = requests.ConnectionError((
            "Connection aborted.",
            TimeoutError("The write operation timed out"),
        ))

        with patch(
            "backend.engine.paddle_client.requests.post",
            side_effect=error,
        ) as post:
            with self.assertRaises(PaddleOCRTimeoutError):
                recognize_pdf(
                    str(self.path),
                    "PaddleOCR-VL-1.6",
                )

            # Do not create duplicate remote jobs.
            self.assertEqual(post.call_count, 1)
            self.assertEqual(
                post.call_args.kwargs["timeout"],
                90.0,
            )

    def test_normal_submit_and_result(self):
        submit = Mock(
            ok=True,
            json=lambda: {
                "data": {"jobId": "job42"}
            },
        )

        status = Mock(
            ok=True,
            json=lambda: {
                "data": {
                    "state": "done",
                    "resultUrl": {
                        "jsonUrl": "https://example.invalid/output"
                    },
                }
            },
        )

        output = Mock(
            ok=True,
            text=(
                '{"result":{"layoutParsingResults":'
                '[{"markdown":{"text":"Câu 1: nội dung"}}]}}\n'
            ),
        )

        with (
            patch(
                "backend.engine.paddle_client.requests.post",
                return_value=submit,
            ) as post,
            patch(
                "backend.engine.paddle_client.requests.get",
                side_effect=[status, output],
            ) as get,
        ):
            markdown, raw = recognize_pdf(
                str(self.path),
                "PaddleOCR-VL-1.6",
            )

        self.assertEqual(raw["job_id"], "job42")
        self.assertIn("Câu 1: nội dung", markdown)

        self.assertEqual(
            post.call_args.kwargs["timeout"],
            90.0,
        )
        self.assertEqual(
            get.call_args.kwargs["timeout"],
            20.0,
        )

    def test_other_connection_error(self):
        with patch(
            "backend.engine.paddle_client.requests.post",
            side_effect=requests.ConnectionError(
                "connection refused"
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Could not connect",
            ):
                recognize_pdf(
                    str(self.path),
                    "PaddleOCR-VL-1.6",
                )

    def test_invalid_timeout_configuration(self):
        with patch.dict(
            os.environ,
            {"PADDLEOCR_SUBMIT_TIMEOUT": "NaN"},
        ):
            with self.assertRaisesRegex(
                ValueError,
                "PADDLEOCR_SUBMIT_TIMEOUT",
            ):
                recognize_pdf(
                    str(self.path),
                    "PaddleOCR-VL-1.6",
                )


if __name__ == "__main__":
    unittest.main()
