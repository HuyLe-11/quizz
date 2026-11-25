"""Tests for vector PDF normalization and OCR/parser fallbacks."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from backend.engine import paddle_client
from backend.engine.quiz_parser import (
    parse_questions_by_pages,
)


class VectorOCRPipelineTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdf = self.root / "moodle.pdf"

        with pymupdf.open() as doc:
            vector_page = doc.new_page(
                width=612,
                height=792,
            )

            for index in range(130):
                x = 12 + (index % 20) * 9
                y = 30 + (index // 20) * 11

                vector_page.draw_rect(
                    pymupdf.Rect(
                        x,
                        y,
                        x + 3,
                        y + 4,
                    )
                )

            normal_page = doc.new_page(
                width=612,
                height=792,
            )

            normal_page.insert_text(
                (80, 80),
                "Question 2: Example with text layer",
            )

            doc.save(self.pdf)

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_vector_page_is_rasterized(self):
        output = self.root / "normalized.pdf"

        result, pages = (
            paddle_client._normalize_vector_pdf(
                self.pdf,
                output,
            )
        )

        self.assertEqual(result, output)
        self.assertEqual(pages, [1])

        with pymupdf.open(result) as doc:
            self.assertEqual(len(doc), 2)
            self.assertEqual(
                len(doc[0].get_images()),
                1,
            )
            self.assertEqual(
                len(doc[0].get_drawings()),
                0,
            )
            self.assertIn(
                "Question 2",
                doc[1].get_text(),
            )
            self.assertEqual(
                doc[0].rect,
                pymupdf.Rect(0, 0, 612, 792),
            )

    def test_empty_vl_recognition_falls_back_to_basic_ocr(self):
        models = []

        def fake_batch(batch, **kwargs):
            models.append(kwargs["model"])

            if kwargs["model"] == "PaddleOCR-VL-1.6":
                return (
                    "vl-job",
                    [
                        {"markdown_text": ""}
                        for _ in range(batch.page_count)
                    ],
                    {},
                )

            return (
                "basic-job",
                [
                    {
                        "pruned_result": {
                            "rec_texts": [
                                f"Question {batch.start_page + i + 1}",
                                "a. Yes",
                                "b. No",
                            ],
                        }
                    }
                    for i in range(batch.page_count)
                ],
                {},
            )

        with patch.dict(
            os.environ,
            {
                "PADDLEOCR_ACCESS_TOKEN": "FAKE",
                "PADDLEOCR_MAX_PAGES_PER_JOB": "1",
                "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL": "PP-OCRv6",
            },
        ):
            with patch.object(
                paddle_client,
                "_process_batch",
                side_effect=fake_batch,
            ):
                markdown, result = (
                    paddle_client.recognize_pdf(
                        str(self.pdf),
                        "PaddleOCR-VL-1.6",
                    )
                )

        self.assertEqual(
            models,
            [
                "PaddleOCR-VL-1.6",
                "PP-OCRv6",
            ] * 2,
        )
        self.assertEqual(len(result["pages"]), 2)
        self.assertIn("Question 1", markdown)
        self.assertIn("Question 2", markdown)

        self.assertEqual(
            [
                page["source_page_number"]
                for page in result["pages"]
            ],
            [1, 2],
        )
        self.assertEqual(
            result["rasterized_pages"],
            [1],
        )

    def test_provider_failed_job_uses_fallback_model(self):
        models = []

        def fake_batch(batch, **kwargs):
            models.append(kwargs["model"])

            if kwargs["model"] == "PaddleOCR-VL-1.6":
                raise paddle_client.PaddleOCRJobFailedError(
                    "Provider failed with 500"
                )

            return (
                "basic",
                [
                    {
                        "pruned_result": {
                            "rec_texts": [
                                "Question 1",
                                "a. One",
                                "b. Two",
                            ]
                        }
                    }
                ] * batch.page_count,
                {},
            )

        with patch.dict(
            os.environ,
            {
                "PADDLEOCR_ACCESS_TOKEN": "FAKE",
                "PADDLEOCR_MAX_PAGES_PER_JOB": "1",
            },
        ):
            with patch.object(
                paddle_client,
                "_process_batch",
                side_effect=fake_batch,
            ):
                markdown, _ = (
                    paddle_client.recognize_pdf(
                        str(self.pdf),
                        "PaddleOCR-VL-1.6",
                    )
                )

        self.assertIn("Question 1", markdown)
        self.assertEqual(
            models[:2],
            [
                "PaddleOCR-VL-1.6",
                "PP-OCRv6",
            ],
        )

    def test_two_empty_models_report_failure_instead_of_fake_question(self):
        with patch.dict(
            os.environ,
            {
                "PADDLEOCR_ACCESS_TOKEN": "FAKE",
            },
        ):
            with patch.object(
                paddle_client,
                "_process_batch",
                side_effect=lambda batch, **kwargs: (
                    "job",
                    [
                        {"markdown_text": ""}
                        for _ in range(batch.page_count)
                    ],
                    {},
                ),
            ):
                with self.assertRaises(
                    paddle_client.PaddleOCRNoTextError
                ):
                    paddle_client.recognize_pdf(
                        str(self.pdf),
                        "PaddleOCR-VL-1.6",
                    )

    def test_moodle_question_metadata_and_lowercase_options(self):
        markdown = (
            "Bài kiểm tra thử nhất\n"
            "Question 1\n"
            "Complete\n"
            "Mark 1.00 out of 1.00\n"
            "Hệ thống có 5 luồng T0 và ba loại "
            "tài nguyên A, B, C.\n"
            "Select one:\n"
            "○ a. Chỉ T0\n"
            "○ b. T0, T2 và T4\n"
            "○ c. T0 và T2\n"
            "○ d. Không có\n"
        )

        parsed = parse_questions_by_pages(
            [markdown],
            "Bai 1",
        )

        self.assertEqual(len(parsed), 1)
        self.assertEqual(
            parsed[0]["type"],
            "mcp",
        )
        self.assertEqual(
            len(parsed[0]["mcp_options"]),
            4,
        )
        self.assertNotIn(
            "Mark 1.00",
            parsed[0]["question_content"][0]["value"],
        )
        self.assertTrue(
            all(
                not option["is_correct"]
                for option in parsed[0]["mcp_options"]
            )
        )

    def test_missing_question_heading_creates_reviewable_draft(self):
        parsed = parse_questions_by_pages(
            [
                "Hệ thống có ba tài nguyên. "
                "Chọn phương án đúng.\n"
                "a. Một\n"
                "b. Hai\n"
                "c. Ba"
            ],
            "Bai 1",
        )

        self.assertEqual(len(parsed), 1)
        self.assertTrue(
            parsed[0]["parse_fallback"]
        )
        self.assertEqual(
            [
                option["label"]
                for option in parsed[0]["mcp_options"]
            ],
            ["A", "B", "C"],
        )


if __name__ == "__main__":
    unittest.main()
