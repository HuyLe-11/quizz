"""Cloud OCR journal wiring; no live network or cloud writes."""

import tempfile
import unittest

from contextlib import ExitStack
from functools import partial
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pymupdf

from backend import cloud_pipeline as pipeline

from backend.engine import paddle_client as client

from backend.engine import paddle_resilient as resilient

from backend.engine import smart_recognizer

from backend.tests.test_paddle_resilient import (
    submitted,
    done,
    result,
)


MODEL = "PaddleOCR-VL-1.6"


class CloudOCRJournalTests(unittest.TestCase):

    def setUp(self):
        stack = ExitStack()

        self.addCleanup(stack.close)

        root = Path(
            stack.enter_context(
                tempfile.TemporaryDirectory()
            )
        )

        self.owner = str(uuid4())

        self.filename = "vatlli.pdf"

        with pymupdf.open() as doc:
            doc.new_page().insert_text(
                (40, 70),
                "Question 1: Example",
            )

            self.pdf = doc.tobytes()

        stack.enter_context(
            patch.dict(
                "os.environ",
                {
                    "QUIZPDF_OCR_MODE": "paddle",

                    "PADDLEOCR_ACCESS_TOKEN":
                        "test-token",

                    "JOB_URL":
                        "https://ocr.example/jobs",

                    "PADDLEOCR_MAX_PAGES_PER_JOB":
                        "5",

                    "PADDLEOCR_MAX_UPLOAD_MB":
                        "1.5",

                    "PADDLEOCR_SUBMIT_MAX_ATTEMPTS":
                        "2",

                    "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL":
                        "PP-OCRv6",
                },
            )
        )

        stack.enter_context(
            patch(
                "backend.cloud_checkpoint.CHECKPOINT_ROOT",
                root / "bundles",
            )
        )

        # Use the real OCR engine and journal,
        # but isolate journal files from runtime storage.
        self.engine = stack.enter_context(
            patch.object(
                smart_recognizer,
                "recognize_pdf",
                side_effect=partial(
                    resilient.recognize_pdf,
                    journal_path=root / "jobs.sqlite3",
                ),
            )
        )

        self.post = stack.enter_context(
            patch.object(
                resilient.requests,
                "post",
            )
        )

        self.get = stack.enter_context(
            patch.object(
                client,
                "_get_with_retries",
            )
        )

        self.parser = stack.enter_context(
            patch.object(
                pipeline,
                "_parse_upload_questions",
                return_value=[],
            )
        )

        stack.enter_context(
            patch.object(
                pipeline,
                "_assign_unique_question_ids",
            )
        )

        stack.enter_context(
            patch.object(
                pipeline,
                "_needs_crop_fallback",
                return_value=False,
            )
        )

        self.review = stack.enter_context(
            patch.object(
                pipeline,
                "build_parser_review_snapshot",
                return_value={
                    "test": True,
                },
            )
        )

        repo = stack.enter_context(
            patch.object(
                pipeline,
                "CloudRepository",
            )
        )

        self.repo = (
            repo.return_value
            .__enter__.return_value
        )

        self.repo.get_exam_record.return_value = None

        self.repo.save_exam_bundle.return_value = {
            "exam_id": "saved",
        }


    def upload(self, owner=None):
        return pipeline.process_cloud_pdf(
            self.pdf,
            self.filename,
            MODEL,
            owner or self.owner,
            "test-access-token",
        )


    def test_polling_failure_resumes_same_provider_job(self):

        self.post.return_value = (
            submitted("j1")
        )

        self.get.side_effect = [
            client.PaddleOCRTimeoutError(
                "poll"
            ),
        ] * 2

        # The initial upload fails after the
        # provider Job ID has been persisted.
        with self.assertRaises(
            client.PaddleOCRTimeoutError
        ):
            self.upload()

        self.parser.assert_not_called()

        self.repo.save_exam_bundle.assert_not_called()

        # Re-upload the identical document.
        self.get.side_effect = [
            done("j1"),
            result("Question 1: Example"),
        ]

        self.assertEqual(
            self.upload()["exam_id"],
            "saved",
        )

        # The second invocation must not create
        # another provider OCR Job.
        self.post.assert_called_once()

        self.assertEqual(
            self.engine.call_args.kwargs,
            {
                "user_id": self.owner,
                "filename": self.filename,
            },
        )

        self.assertEqual(
            self.get.call_args_list[0].args[0],
            self.get.call_args_list[2].args[0],
        )

        self.review.assert_called_once()

        exam = (
            self.repo.save_exam_bundle
            .call_args.args[0]
        )

        self.assertEqual(
            exam["extra"]["parser_review"],
            {"test": True},
        )

        self.assertEqual(
            exam["extra"]["ocr_response"]["job_id"],
            "j1",
        )


    def test_prepared_checkpoint_bypasses_engine_on_storage_retry(self):

        self.post.return_value = (
            submitted("j1")
        )

        self.get.side_effect = [
            done("j1"),
            result("Question 1: Example"),
        ]

        self.repo.save_exam_bundle.side_effect = [
            OSError("storage"),
            {
                "exam_id": "saved",
            },
        ]

        with self.assertRaises(OSError):
            self.upload()

        self.assertEqual(
            self.upload()["exam_id"],
            "saved",
        )

        # An existing Cloud Checkpoint must take
        # priority over the OCR Job Journal.
        self.engine.assert_called_once()

        self.post.assert_called_once()

        self.parser.assert_called_once()

        self.assertTrue(
            self.repo.save_exam_bundle
            .call_args.kwargs["resume_existing"]
        )

        calls = (
            self.repo.save_exam_bundle
            .call_args_list
        )

        self.assertEqual(
            calls[0].args[0]["id"],
            calls[1].args[0]["id"],
        )


    def test_different_owner_does_not_reuse_job(self):

        self.post.side_effect = [
            submitted("j1"),
            submitted("j2"),
        ]

        self.get.side_effect = [
            done("j1"),
            result("one"),
            done("j2"),
            result("two"),
        ]

        self.upload()

        other = str(uuid4())

        self.upload(other)

        self.assertEqual(
            self.post.call_count,
            2,
        )

        self.assertEqual(
            self.engine.call_args.kwargs["user_id"],
            other,
        )


if __name__ == "__main__":
    unittest.main()