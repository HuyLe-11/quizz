"""Cloud OCR recovery tests: no external network or database."""

import json
import tempfile
import unittest

from pathlib import Path
from unittest.mock import Mock, patch

import pymupdf
import requests

from backend.engine import paddle_resilient as resilient
from backend.engine import paddle_client as client


REAL_GET = client._get_with_retries


def submitted(job_id):
    response = Mock(
        ok=True,
        status_code=200,
    )

    response.json.return_value = {
        "data": {
            "jobId": job_id,
        },
    }

    return response


def done(job_id):
    response = Mock(
        ok=True,
        status_code=200,
    )

    response.json.return_value = {
        "data": {
            "state": "done",
            "resultUrl": {
                "jsonUrl": f"https://storage.example/{job_id}",
            },
        },
    }

    return response


def result(*texts, basic=False):
    response = Mock(
        ok=True,
        status_code=200,
    )

    key = (
        "ocrResults"
        if basic
        else "layoutParsingResults"
    )

    pages = [
        {
            "prunedResult": {
                "rec_texts": [text],
            },
        }
        if basic
        else {
            "markdown": {
                "text": text,
            },
        }
        for text in texts
    ]

    response.text = json.dumps({
        "result": {
            key: pages,
        },
    })

    return response


class ResilientOCRTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()

        self.addCleanup(tmp.cleanup)

        self.path = Path(tmp.name) / "exam.pdf"

        with pymupdf.open() as doc:
            for i in range(3):
                doc.new_page().insert_text(
                    (40, 70),
                    f"Page {i + 1}",
                )

            doc.save(self.path)

        self.start_patch(
            patch.dict(
                "os.environ",
                {
                    "PADDLEOCR_ACCESS_TOKEN":
                        "test-token",

                    "JOB_URL":
                        "https://ocr.example/jobs",

                    "PADDLEOCR_MAX_PAGES_PER_JOB":
                        "2",

                    "PADDLEOCR_MAX_UPLOAD_MB":
                        "1.5",

                    "PADDLEOCR_REQUEST_TIMEOUT":
                        "180",

                    "PADDLEOCR_POLL_TIMEOUT":
                        "300",

                    "PADDLEOCR_RESULT_TIMEOUT":
                        "180",

                    "PADDLEOCR_RECOVERY_TIMEOUT":
                        "120",

                    "PADDLEOCR_SUBMIT_MAX_ATTEMPTS":
                        "2",

                    "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL":
                        "PP-OCRv6",
                },
            )
        )

        self.post = self.start_patch(
            patch.object(
                resilient.requests,
                "post",
            )
        )

        self.get = self.start_patch(
            patch.object(
                client,
                "_get_with_retries",
            )
        )

    def start_patch(self, patcher):
        value = patcher.start()

        self.addCleanup(patcher.stop)

        return value

    def recognize(self):
        return resilient.recognize_pdf(
            str(self.path),
            "PaddleOCR-VL-1.6",
        )

    def test_poll_timeout_recovers_same_job_and_preserves_batch_order(self):
        self.post.side_effect = [
            submitted("j1"),
            submitted("j2"),
        ]

        self.get.side_effect = [
            client.PaddleOCRTimeoutError(
                "deadline"
            ),
            done("j1"),
            result("one", "two"),
            done("j2"),
            result("three"),
        ]

        markdown, raw = self.recognize()

        self.assertEqual(
            markdown,
            "one\n\ntwo\n\nthree",
        )

        self.assertEqual(
            self.post.call_count,
            2,
        )

        self.assertEqual(
            raw["job_ids"],
            ["j1", "j2"],
        )

        self.assertEqual(
            [
                p["source_page_number"]
                for p in raw["pages"]
            ],
            [1, 2, 3],
        )

        calls = self.get.call_args_list

        self.assertEqual(
            calls[0].args[0],
            calls[1].args[0],
        )

        self.assertEqual(
            calls[0].kwargs["timeout"],
            (10.0, 30.0),
        )

        self.assertNotIn(
            "headers",
            calls[2].kwargs,
        )

    def test_real_get_retry_deadline_recovers_with_same_job(self):
        self.post.return_value = (
            submitted("j1")
        )

        self.get.side_effect = REAL_GET

        clock = [0.0]

        def fail_near_deadline(*args, **kwargs):
            clock[0] = 1.2

            raise requests.exceptions.ConnectTimeout(
                "poll"
            )

        with (
            patch.dict(
                "os.environ",
                {
                    "PADDLEOCR_MAX_PAGES_PER_JOB": "5",
                    "PADDLEOCR_POLL_TIMEOUT": "2",
                },
            ),

            patch.object(
                resilient.time,
                "monotonic",
                side_effect=lambda: clock[0],
            ),

            patch.object(
                resilient.requests,
                "get",
            ) as http_get,
        ):
            replies = iter([
                done("j1"),
                result(
                    "one",
                    "two",
                    "three",
                ),
            ])

            def request(*args, **kwargs):
                if http_get.call_count == 1:
                    return fail_near_deadline(
                        *args,
                        **kwargs,
                    )

                return next(replies)

            http_get.side_effect = request

            text, _ = self.recognize()

        self.assertEqual(
            text,
            "one\n\ntwo\n\nthree",
        )

        self.post.assert_called_once()

        self.assertEqual(
            http_get.call_count,
            3,
        )

        self.assertEqual(
            http_get.call_args_list[0].args[0],
            http_get.call_args_list[1].args[0],
        )

        self.assertNotIn(
            "headers",
            http_get.call_args_list[2].kwargs,
        )

    def test_result_timeout_recovers_without_new_post(self):
        self.post.side_effect = [
            submitted("j1"),
            submitted("j2"),
        ]

        self.get.side_effect = [
            done("j1"),

            client.PaddleOCRTimeoutError(
                "result"
            ),

            result("one", "two"),

            done("j2"),

            result("three"),
        ]

        self.recognize()

        self.assertEqual(
            self.post.call_count,
            2,
        )

        self.assertEqual(
            self.get.call_args_list[1].args[0],
            self.get.call_args_list[2].args[0],
        )

        self.assertNotIn(
            "headers",
            self.get.call_args_list[2].kwargs,
        )

    def test_post_read_timeout_not_retried(self):
        self.post.side_effect = (
            requests.exceptions.ReadTimeout(
                "submit"
            )
        )

        with self.assertRaisesRegex(
            client.PaddleOCRTimeoutError,
            "read_timeout.*outcome unknown",
        ):
            self.recognize()

        self.post.assert_called_once()

        self.get.assert_not_called()

    def test_recovery_is_bounded(self):
        self.post.return_value = (
            submitted("j1")
        )

        self.get.side_effect = (
            client.PaddleOCRTimeoutError(
                "deadline"
            )
        )

        with self.assertRaises(
            client.PaddleOCRTimeoutError
        ):
            self.recognize()

        self.post.assert_called_once()

        self.assertEqual(
            self.get.call_count,
            2,
        )

    def test_unauthorized_is_not_retried(self):
        self.post.return_value = (
            submitted("j1")
        )

        self.get.side_effect = (
            client.PaddleOCRAPIError(
                "poll",
                401,
            )
        )

        with self.assertRaises(
            client.PaddleOCRAPIError
        ):
            self.recognize()

        self.post.assert_called_once()

        self.get.assert_called_once()

    def test_page_count_mismatch_stops_before_next_batch(self):
        self.post.return_value = (
            submitted("j1")
        )

        self.get.side_effect = [
            done("j1"),
            result("one"),
        ]

        with self.assertRaisesRegex(
            RuntimeError,
            "page mismatch",
        ):
            self.recognize()

        self.post.assert_called_once()

    def test_explicit_provider_failure_allows_existing_model_fallback(self):
        failed = Mock(
            ok=True,
            status_code=200,
        )

        failed.json.return_value = {
            "data": {
                "state": "failed",
            },
        }

        self.post.side_effect = [
            submitted("j1"),
            submitted("fallback"),
            submitted("j2"),
        ]

        self.get.side_effect = [
            failed,

            done("fallback"),

            result(
                "one",
                "two",
                basic=True,
            ),

            done("j2"),

            result("three"),
        ]

        text, raw = self.recognize()

        self.assertEqual(
            text,
            "one\n\ntwo\n\nthree",
        )

        self.assertEqual(
            self.post.call_args_list[1]
            .kwargs["data"]["model"],
            "PP-OCRv6",
        )

        self.assertEqual(
            raw["job_ids"],
            ["fallback", "j2"],
        )


if __name__ == "__main__":
    unittest.main()