"""Test safe, bounded OCR submission recovery without provider calls."""

import json
import tempfile
import unittest

from pathlib import Path
from unittest.mock import Mock, call, patch

import requests

from urllib3.exceptions import ReadTimeoutError

from backend.engine import paddle_client as client
from backend.engine import paddle_resilient as resilient


class PaddleSubmissionTests(unittest.TestCase):

    def setUp(self):
        directory = tempfile.TemporaryDirectory()

        self.addCleanup(
            directory.cleanup
        )

        self.path = (
            Path(directory.name) / "sample.pdf"
        )

        self.contents = b"%PDF-test"

        self.path.write_bytes(
            self.contents
        )

        self.batch = client.PDFBatch(
            self.path,
            0,
            1,
        )

        env = patch.dict(
            "os.environ",
            {
                "PADDLEOCR_SUBMIT_MAX_ATTEMPTS": "3",
                "PADDLEOCR_UPLOAD_SOCKET_TIMEOUT": "90",
            },
        )

        env.start()

        self.addCleanup(env.stop)

        for name, target in (
            (
                "post",
                patch.object(
                    resilient.requests,
                    "post",
                ),
            ),
            (
                "sleep",
                patch.object(
                    resilient.time,
                    "sleep",
                ),
            ),
            (
                "get",
                patch.object(
                    client,
                    "_get_with_retries",
                ),
            ),
        ):
            setattr(
                self,
                name,
                target.start(),
            )

            self.addCleanup(
                target.stop
            )

    def response(
        self,
        status=200,
        job_id="job-123",
    ):
        result = Mock(
            status_code=status,
            ok=status < 400,
        )

        result.json.return_value = {
            "data": {
                "jobId": job_id,
            },
        }

        return result

    def submit(self):
        return resilient._submit_job(
            self.batch,
            model="PaddleOCR-VL-1.6",
            job_url="https://ocr.example/jobs",
            headers={
                "Authorization": "Bearer TEST",
            },
            timeout=(10.0, 180.0),
        )

    def process(self):
        return resilient._process_batch(
            self.batch,
            model="PaddleOCR-VL-1.6",
            job_url="https://ocr.example/jobs",
            headers={
                "Authorization": "Bearer TEST",
            },
            timeout=(10.0, 180.0),
            poll_timeout=300,
            recovery=120,
            result_timeout=180,
        )

    def test_connect_timeout_retries_and_reopens_file(self):
        payloads = []

        def post(*args, **kwargs):
            pdf = kwargs["files"]["file"][1]

            payloads.append(
                pdf.read()
            )

            self.assertFalse(
                kwargs["allow_redirects"]
            )

            self.assertEqual(
                kwargs["timeout"],
                (90.0, 180.0),
            )

            self.assertEqual(
                json.loads(
                    kwargs["data"][
                        "optionalPayload"
                    ]
                ),
                {
                    "useDocOrientationClassify": False,
                    "useDocUnwarping": False,
                },
            )

            if len(payloads) < 3:
                raise requests.exceptions.ConnectTimeout(
                    "connect"
                )

            return self.response()

        self.post.side_effect = post

        self.assertEqual(
            self.submit(),
            "job-123",
        )

        self.assertEqual(
            payloads,
            [self.contents] * 3,
        )

        self.assertEqual(
            self.sleep.call_args_list,
            [
                call(1.0),
                call(2.0),
            ],
        )

    def test_connection_retries_are_bounded(self):
        self.post.side_effect = (
            requests.exceptions.ConnectTimeout(
                "connect"
            )
        )

        with self.assertRaises(
            client.PaddleOCRTimeoutError
        ):
            self.process()

        self.assertEqual(
            self.post.call_count,
            3,
        )

        self.assertEqual(
            self.sleep.call_count,
            2,
        )

        self.get.assert_not_called()

    def test_attempts_one_disables_retry(self):
        with patch.dict(
            "os.environ",
            {
                "PADDLEOCR_SUBMIT_MAX_ATTEMPTS": "1",
            },
        ):
            self.post.side_effect = (
                requests.exceptions.ConnectTimeout()
            )

            with self.assertRaises(
                client.PaddleOCRTimeoutError
            ):
                self.submit()

        self.post.assert_called_once()

        self.sleep.assert_not_called()

    def test_read_timeout_does_not_retry(self):
        self.post.side_effect = (
            requests.exceptions.ReadTimeout(
                "read"
            )
        )

        with self.assertRaisesRegex(
            client.PaddleOCRTimeoutError,
            "read_timeout.*unknown",
        ):
            self.process()

        self.post.assert_called_once()

        self.get.assert_not_called()

        self.sleep.assert_not_called()

    def test_nested_read_timeout_does_not_retry(self):
        self.post.side_effect = (
            requests.exceptions.ConnectionError(
                ReadTimeoutError(
                    None,
                    "/jobs",
                    "read timed out",
                )
            )
        )

        with self.assertRaisesRegex(
            client.PaddleOCRTimeoutError,
            "read_timeout",
        ):
            self.submit()

        self.post.assert_called_once()

    def test_nested_write_timeout_does_not_retry(self):
        self.post.side_effect = (
            requests.exceptions.ConnectionError(
                "Connection aborted",
                TimeoutError(
                    "The write operation timed out"
                ),
            )
        )

        with self.assertRaisesRegex(
            client.PaddleOCRTimeoutError,
            "write_timeout",
        ):
            self.submit()

        self.post.assert_called_once()

        self.sleep.assert_not_called()

    def test_ambiguous_transport_errors_do_not_retry(self):
        for exc in (
            requests.exceptions.ConnectionError(
                "reset"
            ),
            requests.exceptions.SSLError(
                "TLS"
            ),
            requests.exceptions.ConnectionError(
                requests.exceptions.ConnectTimeout()
            ),
        ):
            with self.subTest(
                error=type(exc).__name__
            ):
                self.post.reset_mock()

                self.post.side_effect = exc

                with self.assertRaisesRegex(
                    (
                        RuntimeError,
                        client.PaddleOCRTimeoutError,
                    ),
                    "unknown",
                ):
                    self.submit()

                self.post.assert_called_once()

        self.sleep.assert_not_called()

    def test_http_errors_and_redirects_do_not_retry(self):
        for status in (
            307,
            308,
            401,
            408,
            429,
            500,
            502,
            503,
            504,
        ):
            with self.subTest(status=status):

                self.post.reset_mock()

                self.post.return_value = (
                    self.response(status)
                )

                with self.assertRaises(
                    client.PaddleOCRAPIError
                ) as caught:

                    self.submit()

                self.assertEqual(
                    caught.exception.status_code,
                    status,
                )

                self.post.assert_called_once()

        self.sleep.assert_not_called()

    def test_missing_job_id_is_not_resubmitted(self):
        self.post.return_value = (
            self.response(job_id=None)
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "outcome unknown",
        ):
            self.process()

        self.post.assert_called_once()

        self.get.assert_not_called()

    def test_invalid_json_is_not_resubmitted(self):
        response = self.response()

        response.json.side_effect = (
            ValueError("invalid json")
        )

        self.post.return_value = response

        with self.assertRaisesRegex(
            RuntimeError,
            "outcome unknown",
        ):
            self.submit()

        self.post.assert_called_once()

    def test_invalid_configuration_fails_before_post(self):
        for value in (
            "0",
            "4",
            "1.5",
            "invalid",
        ):
            with (
                self.subTest(value=value),
                patch.dict(
                    "os.environ",
                    {
                        "PADDLEOCR_SUBMIT_MAX_ATTEMPTS": value,
                    },
                ),
            ):
                with self.assertRaises(
                    ValueError
                ):
                    self.submit()

        self.post.assert_not_called()

    def test_connect_retry_then_poll_recovery_uses_same_job(self):
        self.post.side_effect = [
            requests.exceptions.ConnectTimeout(),
            self.response(),
        ]

        done = Mock()

        done.json.return_value = {
            "data": {
                "state": "done",
                "resultUrl": {
                    "jsonUrl": (
                        "https://storage.example/result"
                    ),
                },
            },
        }

        result = Mock()

        result.text = json.dumps({
            "result": {
                "layoutParsingResults": [
                    {
                        "markdown": {
                            "text": "Question 1: Example",
                        },
                    },
                ],
            },
        })

        self.get.side_effect = [
            client.PaddleOCRTimeoutError(
                "deadline"
            ),
            done,
            result,
        ]

        job_id, pages, _ = self.process()

        self.assertEqual(
            job_id,
            "job-123",
        )

        self.assertEqual(
            len(pages),
            1,
        )

        self.assertEqual(
            self.post.call_count,
            2,
        )

        self.assertEqual(
            self.get.call_count,
            3,
        )

        self.assertEqual(
            self.get.call_args_list[0].args[0],
            self.get.call_args_list[1].args[0],
        )

        self.assertNotIn(
            "headers",
            self.get.call_args_list[2].kwargs,
        )


if __name__ == "__main__":
    unittest.main()