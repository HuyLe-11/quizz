"""Tests for the cloud-native PDF processing pipeline."""

import unittest

from pathlib import Path
from unittest.mock import patch

from backend.cloud_pipeline import process_cloud_pdf

USER_ID = "11111111-1111-4111-8111-111111111111"
TOKEN = "test-access-token"

PDF_BYTES = b"%PDF-1.4 test document"


def make_question(
    number: int,
) -> dict:
    return {
        "composite_id": f"question-{number}",
        "question_id": number,
        "type": "mcp",
        "multiple_choice": False,
        "question_content": [
            {
                "type": "text",
                "value": (
                    "This is a sufficiently long "
                    "test question for the QuizPDF "
                    "cloud processing pipeline."
                ),
            },
        ],
        "explanation_content": [],
        "mcp_options": [
            {
                "label": "A",
                "content": [
                    {
                        "type": "text",
                        "value": "First answer",
                    },
                ],
                "is_correct": False,
            },
            {
                "label": "B",
                "content": [
                    {
                        "type": "text",
                        "value": "Second answer",
                    },
                ],
                "is_correct": False,
            },
        ],
        "match_pairs": [],
        "fill_in_answers": [],
        "page_number": 1,
    }


class CloudPipelineTests(unittest.TestCase):
    def setUp(self):
        self.raw_response = {
            "pages": [
                {
                    "markdown_text": (
                        "Question 1: Example question\n"
                        "A. First answer\n"
                        "B. Second answer"
                    ),
                },
            ],
        }

    @patch(
        "backend.cloud_pipeline.CloudRepository"
    )
    @patch(
        "backend.cloud_pipeline._needs_crop_fallback",
        return_value=False,
    )
    @patch(
        "backend.cloud_pipeline._parse_upload_questions"
    )
    @patch(
        "backend.cloud_pipeline.recognize_pdf"
    )
    def test_upload_saves_to_cloud_not_sqlite(
        self,
        mock_ocr,
        mock_parser,
        mock_needs_crop,
        mock_repository,
    ):
        mock_ocr.return_value = (
            "Question 1: Example question",
            self.raw_response,
        )

        mock_parser.return_value = [
            make_question(1)
        ]

        repository = (
            mock_repository.return_value
            .__enter__.return_value
        )

        repository.save_exam_bundle.return_value = {
            "exam_id": "saved-exam",
            "title": "example",
            "question_count": 1,
        }

        phases = []

        with patch(
            "backend.db.insert_exam",
            side_effect=AssertionError(
                "SQLite must not be used."
            ),
        ) as sqlite_insert:
            result = process_cloud_pdf(
                PDF_BYTES,
                "example.pdf",
                "PaddleOCR-VL-1.6",
                USER_ID,
                TOKEN,
                progress=phases.append,
            )

        sqlite_insert.assert_not_called()

        mock_repository.assert_called_once_with(
            USER_ID,
            TOKEN,
        )

        self.assertEqual(
            result["question_count"],
            1,
        )

        self.assertEqual(
            phases,
            [
                "ocr",
                "question_extraction",
                "question_crops",
                "cloud_save",
            ],
        )

        args = (
            repository.save_exam_bundle
            .call_args.args
        )

        exam, questions, contents, images = args

        self.assertEqual(
            exam["source_file"],
            "example.pdf",
        )

        self.assertEqual(
            contents,
            PDF_BYTES,
        )

        self.assertEqual(
            len(questions),
            1,
        )

        self.assertEqual(images, {})

        self.assertEqual(
            questions[0]["asset"]["crop_status"],
            "not_created",
        )

    @patch(
        "backend.cloud_pipeline.CloudRepository"
    )
    @patch(
        "backend.cloud_pipeline._needs_crop_fallback",
        return_value=False,
    )
    @patch(
        "backend.cloud_pipeline._parse_upload_questions"
    )
    @patch(
        "backend.cloud_pipeline.recognize_pdf"
    )
    def test_duplicate_numbers_preserve_source_number(
        self,
        mock_ocr,
        mock_parser,
        mock_needs_crop,
        mock_repository,
    ):
        mock_ocr.return_value = (
            "OCR text",
            self.raw_response,
        )

        mock_parser.return_value = [
            make_question(1),
            make_question(1),
        ]

        repository = (
            mock_repository.return_value
            .__enter__.return_value
        )

        repository.save_exam_bundle.return_value = {
            "exam_id": "exam",
            "title": "example",
            "question_count": 2,
        }

        process_cloud_pdf(
            PDF_BYTES,
            "example.pdf",
            "PaddleOCR-VL-1.6",
            USER_ID,
            TOKEN,
        )

        questions = (
            repository.save_exam_bundle
            .call_args.args[1]
        )

        self.assertEqual(
            [
                question["question_id"]
                for question in questions
            ],
            [1, 2],
        )

        self.assertEqual(
            [
                question["source_question_number"]
                for question in questions
            ],
            [1, 1],
        )

        self.assertNotEqual(
            questions[0]["id"],
            questions[1]["id"],
        )

        self.assertNotEqual(
            questions[0]["composite_id"],
            questions[1]["composite_id"],
        )

    @patch(
        "backend.cloud_pipeline.CloudRepository"
    )
    @patch(
        "backend.cloud_pipeline.crop_question_pdf"
    )
    @patch(
        "backend.cloud_pipeline._needs_crop_fallback",
        return_value=True,
    )
    @patch(
        "backend.cloud_pipeline._parse_upload_questions"
    )
    @patch(
        "backend.cloud_pipeline.recognize_pdf"
    )
    def test_cropped_image_is_prepared_for_storage(
        self,
        mock_ocr,
        mock_parser,
        mock_needs_crop,
        mock_crop,
        mock_repository,
    ):
        mock_ocr.return_value = (
            "OCR text",
            self.raw_response,
        )

        mock_parser.return_value = [
            make_question(1)
        ]

        def fake_crop(
            pdf_path,
            question_number,
            page_number,
            output_path,
            **kwargs,
        ):
            path = Path(output_path)

            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            path.write_bytes(
                b"fake-image-bytes"
            )

            return {
                "image_path": str(path),
                "page_number": 1,
                "crop_coordinates": [],
                "crop_status": "success",
                "source": "test",
            }

        mock_crop.side_effect = fake_crop

        repository = (
            mock_repository.return_value
            .__enter__.return_value
        )

        repository.save_exam_bundle.return_value = {
            "exam_id": "exam",
            "title": "example",
            "question_count": 1,
        }

        process_cloud_pdf(
            PDF_BYTES,
            "example.pdf",
            "PaddleOCR-VL-1.6",
            USER_ID,
            TOKEN,
        )

        args = (
            repository.save_exam_bundle
            .call_args.args
        )

        questions = args[1]
        images = args[3]

        asset = questions[0]["asset"]

        self.assertEqual(
            asset["crop_status"],
            "success",
        )

        self.assertIn(
            asset["image_path"],
            images,
        )

        self.assertEqual(
            images[asset["image_path"]],
            b"fake-image-bytes",
        )

        mock_crop.assert_called_once()

    @patch(
        "backend.cloud_pipeline.CloudRepository"
    )
    @patch(
        "backend.cloud_pipeline.recognize_pdf",
        side_effect=RuntimeError(
            "OCR service failed"
        ),
    )
    def test_failed_ocr_does_not_save_cloud_data(
        self,
        mock_ocr,
        mock_repository,
    ):
        with self.assertRaisesRegex(
            RuntimeError,
            "OCR service failed",
        ):
            process_cloud_pdf(
                PDF_BYTES,
                "example.pdf",
                "PaddleOCR-VL-1.6",
                USER_ID,
                TOKEN,
            )

        mock_repository.assert_not_called()

    @patch(
        "backend.cloud_pipeline.CloudRepository"
    )
    def test_invalid_pdf_is_rejected(
        self,
        mock_repository,
    ):
        with self.assertRaises(ValueError):
            process_cloud_pdf(
                b"not-a-pdf",
                "example.pdf",
                "PaddleOCR-VL-1.6",
                USER_ID,
                TOKEN,
            )

        mock_repository.assert_not_called()


if __name__ == "__main__":
    unittest.main()
