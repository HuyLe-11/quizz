"""Regression tests for resumable QuizPDF cloud preprocessing."""

import tempfile
import unittest
import zipfile

from pathlib import Path
from unittest.mock import patch

from backend.cloud_checkpoint import (
    CHECKPOINT_TTL_SECONDS,
    CloudOCRCheckpoint,
)
from backend.cloud_repository import CloudRepositoryError
from backend.cloud_pipeline import process_cloud_pdf

USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
PDF = b"%PDF-1.4\nexample"
MODEL = "PaddleOCR-VL-1.6"


def prepared(checkpoint):
    exam = {
        "id": "33333333-3333-4333-8333-333333333333",
        "name": "example",
        "source_sha256": checkpoint.source_sha256,
    }
    questions = [
        {"id": "question-1", "question_id": 1}
    ]
    images = {
        "exam/image.png": b"PNG mock data"
    }
    return exam, questions, images


def parsed_question():
    return {
        "question_id": 1,
        "type": "mcp",
        "multiple_choice": False,
        "question_content": [
            {"type": "text", "value": "Test question"}
        ],
        "explanation_content": [],
        "mcp_options": [],
        "match_pairs": [],
        "fill_in_answers": [],
        "page_number": 1,
    }


class CheckpointFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root_patch = patch(
            "backend.cloud_checkpoint.CHECKPOINT_ROOT",
            Path(self.temp.name),
        )
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def test_round_trip(self):
        checkpoint = CloudOCRCheckpoint(
            USER_A, "example.pdf", MODEL, PDF
        )
        exam, questions, images = prepared(checkpoint)

        checkpoint.save(exam, questions, images)

        self.assertTrue(checkpoint.path.is_file())
        self.assertEqual(
            checkpoint.load().crop_images,
            images,
        )
        self.assertEqual(
            checkpoint.load().exam,
            exam,
        )
        self.assertEqual(
            checkpoint.load().questions,
            questions,
        )

        checkpoint.delete()
        self.assertIsNone(checkpoint.load())

    def test_owner_model_pdf_and_name_are_isolated(self):
        first = CloudOCRCheckpoint(
            USER_A, "example.pdf", MODEL, PDF
        )
        first.save(*prepared(first))

        self.assertIsNone(
            CloudOCRCheckpoint(
                USER_B, "example.pdf", MODEL, PDF
            ).load()
        )
        self.assertIsNone(
            CloudOCRCheckpoint(
                USER_A, "example.pdf", "PP-OCRv6", PDF
            ).load()
        )
        self.assertIsNone(
            CloudOCRCheckpoint(
                USER_A, "example.pdf", MODEL, PDF + b"other"
            ).load()
        )
        self.assertIsNone(
            CloudOCRCheckpoint(
                USER_A, "renamed.pdf", MODEL, PDF
            ).load()
        )

    def test_expiry_invalidates_cache(self):
        checkpoint = CloudOCRCheckpoint(
            USER_A, "example.pdf", MODEL, PDF
        )

        with patch(
            "backend.cloud_checkpoint.time.time",
            return_value=1_800_000_000,
        ):
            checkpoint.save(*prepared(checkpoint))

        with patch(
            "backend.cloud_checkpoint.time.time",
            return_value=(
                1_800_000_000
                + CHECKPOINT_TTL_SECONDS
                + 1
            ),
        ):
            self.assertIsNone(checkpoint.load())

        self.assertFalse(checkpoint.path.exists())

    def test_corrupt_archive_is_ignored(self):
        checkpoint = CloudOCRCheckpoint(
            USER_A, "example.pdf", MODEL, PDF
        )
        checkpoint.save(*prepared(checkpoint))

        checkpoint.path.write_bytes(b"corrupt")

        self.assertIsNone(checkpoint.load())
        self.assertFalse(checkpoint.path.exists())

    def test_no_raw_pdf_or_token_is_written(self):
        checkpoint = CloudOCRCheckpoint(
            USER_A, "example.pdf", MODEL, PDF
        )
        checkpoint.save(*prepared(checkpoint))

        with zipfile.ZipFile(checkpoint.path) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "manifest.json",
                    "crops/0000.bin",
                },
            )
            self.assertNotIn(
                b"test-access-token",
                archive.read("manifest.json"),
            )
            self.assertNotIn(
                "source.pdf",
                archive.namelist(),
            )

    @patch("backend.cloud_pipeline.CloudRepository")
    @patch(
        "backend.cloud_pipeline._needs_crop_fallback",
        return_value=False,
    )
    @patch("backend.cloud_pipeline._parse_upload_questions")
    @patch("backend.cloud_pipeline.recognize_pdf")
    def test_retry_same_pdf_skips_ocr(
        self,
        mock_ocr,
        mock_parse,
        unused_needs_crop,
        mock_repository,
    ):
        mock_ocr.return_value = (
            "Question 1: Test",
            {
                "pages": [
                    {"markdown_text": "Question 1: Test"}
                ],
            },
        )
        mock_parse.return_value = [parsed_question()]

        repository = (
            mock_repository.return_value
            .__enter__.return_value
        )
        repository.get_exam_record.return_value = None
        repository.save_exam_bundle.side_effect = [
            CloudRepositoryError(
                "Simulated Storage failure"
            ),
            {
                "exam_id": "saved",
                "title": "example",
                "question_count": 1,
            },
        ]

        with self.assertRaises(CloudRepositoryError):
            process_cloud_pdf(
                PDF, "example.pdf", MODEL, USER_A, "token"
            )

        self.assertEqual(mock_ocr.call_count, 1)
        self.assertEqual(
            len(
                list(
                    Path(self.temp.name).glob("*.zip")
                )
            ),
            1,
        )

        result = process_cloud_pdf(
            PDF, "example.pdf", MODEL, USER_A, "token"
        )

        self.assertEqual(result["question_count"], 1)
        self.assertEqual(mock_ocr.call_count, 1)
        self.assertEqual(mock_parse.call_count, 1)
        self.assertEqual(
            repository.save_exam_bundle.call_count,
            2,
        )

        first_exam = (
            repository.save_exam_bundle
            .call_args_list[0].args[0]
        )
        second_exam = (
            repository.save_exam_bundle
            .call_args_list[1].args[0]
        )

        self.assertEqual(
            first_exam["id"],
            second_exam["id"],
        )
        self.assertEqual(
            list(Path(self.temp.name).glob("*.zip")),
            [],
        )

    @patch("backend.cloud_pipeline.CloudRepository")
    @patch(
        "backend.cloud_pipeline._needs_crop_fallback",
        return_value=False,
    )
    @patch("backend.cloud_pipeline._parse_upload_questions")
    @patch("backend.cloud_pipeline.recognize_pdf")
    def test_already_committed_exam_is_not_uploaded_again(
        self,
        mock_ocr,
        mock_parse,
        unused_needs_crop,
        mock_repository,
    ):
        mock_ocr.return_value = (
            "Question 1: Test",
            {
                "pages": [
                    {"markdown_text": "Question 1: Test"}
                ],
            },
        )
        mock_parse.return_value = [parsed_question()]

        repository = (
            mock_repository.return_value
            .__enter__.return_value
        )
        repository.save_exam_bundle.side_effect = (
            CloudRepositoryError("Lost response")
        )

        with self.assertRaises(CloudRepositoryError):
            process_cloud_pdf(
                PDF, "example.pdf", MODEL, USER_A, "token"
            )

        repository.save_exam_bundle.reset_mock()
        repository.get_exam_record.return_value = {
            "name": "example",
            "questions": [{"id": "question-1"}],
        }

        result = process_cloud_pdf(
            PDF, "example.pdf", MODEL, USER_A, "token"
        )

        self.assertEqual(result["question_count"], 1)
        repository.save_exam_bundle.assert_not_called()
        self.assertEqual(mock_ocr.call_count, 1)
        self.assertEqual(
            list(Path(self.temp.name).glob("*.zip")),
            [],
        )


if __name__ == "__main__":
    unittest.main()