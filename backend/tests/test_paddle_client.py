import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.engine.paddle_client import (
    extract_page_texts,
    recognize_pdf,
)
from backend.engine.quiz_parser import parse_questions_by_pages


class PaddleClientTests(unittest.TestCase):

    def test_markdown_wins_over_duplicate_representations(self):
        page = {
            "markdown_text": "Câu 1: Chọn đáp án",
            "markdown": {
                "text": "Câu 1: Chọn đáp án",
            },
            "pruned_result": {
                "rec_texts": [
                    "Câu 1: Chọn đáp án",
                ],
            },
            "raw": {
                "markdown": {
                    "text": "Câu 1: Chọn đáp án",
                },
            },
        }

        self.assertEqual(
            extract_page_texts({"pages": [page]}),
            ["Câu 1: Chọn đáp án"],
        )

    def test_empty_markdown_uses_layout_blocks(self):
        page = {
            "markdown_text": "  ",
            "pruned_result": {
                "parsing_res_list": [
                    {
                        "block_content": "Câu 1: Chọn đáp án",
                    },
                    {
                        "block_content": "A. Một",
                    },
                    {
                        "block_content": "B. Hai",
                    },
                ],
                "rec_texts": ["duplicate"],
            },
        }

        self.assertEqual(
            extract_page_texts({"pages": [page]}),
            ["Câu 1: Chọn đáp án\nA. Một\nB. Hai"],
        )

    def test_basic_ocr_and_repeated_lines(self):
        page = {
            "prunedResult": {
                "rec_texts": [
                    "A. Một",
                    "A. Một",
                    None,
                    12,
                    " ",
                    "B. Hai",
                ],
            },
        }

        self.assertEqual(
            extract_page_texts({"pages": [page]}),
            ["A. Một\nA. Một\nB. Hai"],
        )

    def test_empty_and_invalid_slots_preserve_page_numbers(self):
        raw = {
            "pages": [
                None,
                {},
                {
                    "markdown_text": (
                        "Câu 1: Chọn câu trả lời\n"
                        "A. Một\n"
                        "B. Hai"
                    ),
                },
            ],
        }

        texts = extract_page_texts(raw)

        self.assertEqual(
            texts[:2],
            ["", ""],
        )

        questions = parse_questions_by_pages(
            texts,
            "demo",
        )

        self.assertEqual(
            questions[0]["page_number"],
            3,
        )

    def test_nested_result_preserves_pages(self):
        raw = {
            "result": {
                "layoutParsingResults": [
                    {
                        "markdown": {
                            "text": "First",
                        },
                    },
                    {
                        "markdownText": "Second",
                    },
                ],
            },
        }

        self.assertEqual(
            extract_page_texts(raw),
            ["First", "Second"],
        )

    def test_document_with_text_only(self):
        self.assertEqual(
            extract_page_texts({
                "text": "Single page",
            }),
            ["Single page"],
        )

        self.assertEqual(
            extract_page_texts({}),
            [],
        )

    def _recognize(self, model, results):
        """Simulate submission, polling and result download."""

        submitted = Mock(ok=True)

        submitted.json.return_value = {
            "data": {
                "jobId": "test-job",
            },
        }

        status = Mock(ok=True)

        status.json.return_value = {
            "data": {
                "state": "done",
                "resultUrl": {
                    "jsonUrl": "https://example.test/result",
                },
            },
        }

        downloaded = Mock(ok=True)

        downloaded.text = "\n".join(
            json.dumps(result)
            for result in results
        )

        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "sample.pdf"
            pdf.write_bytes(b"mock PDF")

            with (
                patch.dict(
                    "os.environ",
                    {
                        "PADDLEOCR_ACCESS_TOKEN": "test-token",
                        "JOB_URL": "https://example.test/jobs",
                    },
                ),
                patch(
                    "backend.engine.paddle_client.requests.post",
                    return_value=submitted,
                ),
                patch(
                    "backend.engine.paddle_client.requests.get",
                    side_effect=[status, downloaded],
                ) as get,
            ):
                output = recognize_pdf(
                    str(pdf),
                    model,
                )

                self.assertNotIn(
                    "headers",
                    get.call_args.kwargs,
                )

                return output

    def test_recognize_vl_keeps_geometry_and_page_slots(self):
        item = {
            "markdown": {
                "text": "First",
                "images": {
                    "figure.png": "image",
                },
            },
            "prunedResult": {
                "parsing_res_list": [
                    {
                        "block_content": "First",
                        "block_bbox": [1, 2, 3, 4],
                    },
                ],
            },
        }

        text, raw = self._recognize(
            "PaddleOCR-VL-1.6",
            [
                {
                    "result": {
                        "dataInfo": {
                            "numPages": 3,
                        },
                        "layoutParsingResults": [
                            item,
                            None,
                        ],
                    },
                },
                {
                    "result": {
                        "layoutParsingResults": [
                            {
                                "markdown": {
                                    "text": "Third",
                                },
                            },
                        ],
                    },
                },
            ],
        )

        self.assertEqual(
            extract_page_texts(raw),
            ["First", "", "Third"],
        )

        self.assertEqual(
            text,
            "First\n\n\n\nThird",
        )

        self.assertEqual(
            raw["pages"][0]["pruned_result"],
            item["prunedResult"],
        )

        self.assertEqual(
            raw["pages"][0]["raw"],
            item,
        )

        self.assertEqual(
            raw["pages"][0]["markdown_images"],
            item["markdown"]["images"],
        )

    def test_recognize_pp_ocr(self):
        item = {
            "prunedResult": {
                "rec_texts": [
                    "A. One",
                    "B. Two",
                ],
                "rec_boxes": [
                    [0, 0, 10, 10],
                    [0, 11, 10, 20],
                ],
            },
        }

        text, raw = self._recognize(
            "PP-OCRv6",
            [
                {
                    "result": {
                        "ocrResults": [item],
                    },
                },
            ],
        )

        self.assertEqual(
            text,
            "A. One\nB. Two",
        )

        self.assertEqual(
            raw["pages"][0]["pruned_result"],
            item["prunedResult"],
        )

    def test_invalid_result_schema_fails_explicitly(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "missing layoutParsingResults",
        ):
            self._recognize(
                "PaddleOCR-VL-1.6",
                [
                    {
                        "result": {
                            "unexpected": [],
                        },
                    },
                ],
            )


if __name__ == "__main__":
    unittest.main()
