"""Regression tests for the shared parser, OCR page contract and local preview."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pymupdf
from fastapi.testclient import TestClient

from backend.engine.paddle_client import (
    PaddleOCRTimeoutError,
    extract_page_texts,
)
from backend.engine.parser_pipeline import (
    parse_pdf_questions,
)
from backend.engine.pdf_tools import (
    RedMarkConfig,
    crop_question_pdf,
)


def pdf_bytes(pages=1):
    with pymupdf.open() as document:
        for _ in range(pages):
            document.new_page(width=600, height=800)

        return document.tobytes()


def choices(question):
    return {
        item["label"]: "\n".join(
            block["value"]
            for block in item["content"]
        )
        for item in question["mcp_options"]
    }


class PipelineContractTests(unittest.TestCase):

    def test_units_and_ocr_choice_delimiters(self):
        raw = [
            "Câu 3: Tốc độ trung bình là bao nhiêu?\n"
            "A. 45 km/h. B. 49 km/h. "
            "C. 55 km/h. D. 50 km/h.\n"
            "Question 12: Choose the answer.\n"
            "A, talk B. to talk C. to talking D. talking\n"
            "Question 13: Choose a preposition.\n"
            "A.on B.in C.at D.from"
        ]

        result = parse_pdf_questions(
            pdf_bytes(),
            raw,
            "demo",
        )

        self.assertEqual(
            result["raw_page_texts"],
            raw,
        )

        self.assertEqual(
            choices(result["questions"][0]),
            dict(
                zip(
                    "ABCD",
                    [
                        "45 km/h.",
                        "49 km/h.",
                        "55 km/h.",
                        "50 km/h.",
                    ],
                )
            ),
        )

        self.assertEqual(
            choices(result["questions"][1])["A"],
            "talk",
        )

        self.assertEqual(
            choices(result["questions"][2])["D"],
            "from",
        )

        for question in result["questions"]:
            self.assertTrue(
                all(
                    not option["is_correct"]
                    for option in question["mcp_options"]
                )
            )

            self.assertEqual(
                question["fill_in_answers"],
                [],
            )

    def test_numbered_sections_do_not_contaminate_options(self):
        raw = [
            "PHẦN I. Trắc nghiệm\n"
            "Câu 24: Select one answer.\n"
            "A. a B. b C. c D. last\n"
            "PHÀN II. Trắc nghiệm ĐỨNG, SAI\n"
            "Câu 1: Cho tư liệu sau.\n"
            "a) First statement\n"
            "b) Second statement"
        ]

        first, second = parse_pdf_questions(
            pdf_bytes(),
            raw,
            "demo",
            embed_context=False,
        )["questions"]

        self.assertEqual(choices(first)["D"], "last")
        self.assertEqual(second["type"], "fill_in")
        self.assertEqual(second["mcp_options"], [])
        self.assertTrue(second["parse_fallback"])

        self.assertIn(
            "Second statement",
            str(second["question_content"]),
        )

    def test_context_is_preserved_for_sqlite(self):
        raw = [
            "Read the following passage.\n"
            "Alice visited the river.\n"
            "Question 1: Where did Alice go?\n"
            "A. River B. Lake"
        ]

        document = pdf_bytes()

        preview = parse_pdf_questions(
            document,
            raw,
            "demo",
            embed_context=False,
        )["questions"][0]

        main = parse_pdf_questions(
            document,
            raw,
            "demo",
        )["questions"][0]

        self.assertEqual(
            preview["shared_passage"],
            "Alice visited the river.",
        )

        self.assertIn(
            preview["shared_passage"],
            str(main["question_content"]),
        )

        self.assertEqual(
            choices(preview),
            choices(main),
        )

    def test_blank_pages_keep_source_page_number(self):
        raw = [
            "",
            "Question 7: Choose the best answer.\n"
            "A. one B. two",
        ]

        result = parse_pdf_questions(
            pdf_bytes(2),
            raw,
            "demo",
        )

        self.assertEqual(
            result["questions"][0]["page_number"],
            2,
        )

        self.assertEqual(
            len(result["page_texts"]),
            2,
        )

    def test_suspicious_scan_is_flagged_not_fabricated(self):
        raw = [
            "Question 8: Which statement is correct?\n"
            "A. First complete sentence.\n"
            "B. Second complete sentence.\n"
            "C. This is a long statement whose final words are missing\n"
            "D. Fourth complete sentence."
        ]

        question = parse_pdf_questions(
            pdf_bytes(),
            raw,
            "demo",
        )["questions"][0]

        self.assertTrue(question["parse_fallback"])
        self.assertTrue(question["requires_source_image"])

        self.assertEqual(
            choices(question)["C"],
            "This is a long statement whose final words are missing",
        )

    def test_ocr_page_variants_are_not_duplicated(self):
        raw = {
            "pages": [
                {
                    "markdown_text": "one",
                    "pruned_result": {
                        "text": "duplicate"
                    },
                },
                None,
                {
                    "pruned_result": {
                        "rec_texts": ["two", "three"]
                    }
                },
                {
                    "pruned_result": {
                        "parsing_res_list": [
                            {
                                "block_content": "four"
                            }
                        ]
                    }
                },
            ]
        }

        self.assertEqual(
            extract_page_texts(raw),
            [
                "one",
                "",
                "two\nthree",
                "four",
            ],
        )

    def test_ocr_coordinates_use_image_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.pdf"
            path.write_bytes(pdf_bytes())

            ocr = {
                "pruned_result": {
                    "width": 1200,
                    "height": 1600,
                    "parsing_res_list": [
                        {
                            "block_content": "Question 1: Select",
                            "block_bbox": [80, 200, 800, 240],
                        },
                        {
                            "block_content": "Question 2: Next",
                            "block_bbox": [80, 800, 800, 840],
                        },
                    ],
                }
            }

            result = crop_question_pdf(
                path,
                1,
                1,
                Path(directory) / "crop.png",
                [ocr],
                RedMarkConfig(dpi=72),
            )

            self.assertEqual(
                result["source"],
                "paddleocr",
            )

            self.assertEqual(
                result["crop_coordinates"][0]["bbox"],
                [0.0, 86.0, 600.0, 400.0],
            )

            ocr["pruned_result"]["doc_preprocessor_res"] = {
                "angle": 0,
                "model_settings": {
                    "use_doc_unwarping": True
                },
            }

            result = crop_question_pdf(
                path,
                1,
                1,
                Path(directory) / "fallback.png",
                [ocr],
                RedMarkConfig(dpi=72),
            )

            self.assertEqual(
                result["source"],
                "full_page_fallback",
            )


class PreviewContractTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.database = (
            Path(self.temp.name) / "quiz.sqlite3"
        )

        self.env = patch.dict(
            os.environ,
            {
                "QUIZPDF_PARSER_PREVIEW": "1",
                "QUIZPDF_PREVIEW_OCR_MODE": "cache",
                "DATABASE_PATH": str(self.database),
            },
        )

        self.env.start()
        self.addCleanup(self.env.stop)

        self.module = importlib.import_module(
            "backend.parser_preview_app"
        )

        self.client = TestClient(self.module.app)
        self.addCleanup(self.client.close)

        self.pdf = pdf_bytes()

        self.page = (
            "Question 1: Example question?\n"
            "A. one B. two"
        )

        # The SQLite connection context manager manages
        # transactions but does not close the connection.
        # Explicit closing prevents WinError 32 on Windows.
        with closing(sqlite3.connect(self.database)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE exams (
                        source_sha256 TEXT,
                        extra TEXT,
                        uploaded_at TEXT
                    )
                    """
                )

                connection.execute(
                    """
                    INSERT INTO exams
                    VALUES (?, ?, ?)
                    """,
                    (
                        hashlib.sha256(self.pdf).hexdigest(),
                        json.dumps({
                            "ocr_model": "PaddleOCR-VL-1.6",
                            "ocr_response": {
                                "pages": [
                                    {
                                        "markdown_text": self.page
                                    }
                                ]
                            },
                        }),
                        "2026-01-01",
                    ),
                )

    def upload(
        self,
        pdf=None,
        model="PaddleOCR-VL-1.6",
    ):
        return self.client.post(
            "/api/parser-preview/ocr",
            files={
                "file": (
                    "demo.pdf",
                    self.pdf if pdf is None else pdf,
                    "application/pdf",
                )
            },
            data={
                "model": model
            },
        )

    def test_cache_is_read_only_and_requires_exact_match(self):
        before = self.database.read_bytes()

        with patch.object(
            self.module,
            "recognize_pdf",
            side_effect=AssertionError("No OCR call"),
        ) as ocr:
            response = self.upload()

            self.assertEqual(
                response.status_code,
                200,
                response.text,
            )

            data = response.json()["data"]

            self.assertIsNone(
                data["timings"]["ocr_seconds"]
            )

            self.assertEqual(
                data["raw_page_texts"],
                [self.page],
            )

            self.assertEqual(
                data["diagnostics"]["incomplete_options"],
                [],
            )

            self.assertEqual(
                self.upload(
                    model="PP-OCRv6"
                ).status_code,
                404,
            )

            self.assertEqual(
                self.upload(pdf_bytes(2)).status_code,
                404,
            )

            ocr.assert_not_called()

        self.assertEqual(
            self.database.read_bytes(),
            before,
        )

    def test_reprocess_and_cache_use_same_pipeline(self):
        first = self.upload().json()["data"]

        with patch.object(
            self.module,
            "recognize_pdf",
        ) as ocr:
            response = self.client.post(
                "/api/parser-preview/reprocess",
                files={
                    "file": (
                        "demo.pdf",
                        self.pdf,
                        "application/pdf",
                    )
                },
                data={
                    "model": "PaddleOCR-VL-1.6",
                    "raw_page_texts_json": json.dumps(
                        [self.page]
                    ),
                },
            )

            self.assertEqual(
                response.status_code,
                200,
                response.text,
            )

            self.assertEqual(
                first["questions"],
                response.json()["data"]["questions"],
            )

            ocr.assert_not_called()

    def test_live_timeout_is_http_504(self):
        with (
            patch.dict(
                os.environ,
                {
                    "QUIZPDF_PREVIEW_OCR_MODE": "live"
                },
            ),
            patch.object(
                self.module,
                "recognize_pdf",
                side_effect=PaddleOCRTimeoutError(
                    "upload timeout"
                ),
            ),
        ):
            self.assertEqual(
                self.upload().status_code,
                504,
            )

    def test_reprocess_rejects_wrong_page_count(self):
        response = self.client.post(
            "/api/parser-preview/reprocess",
            files={
                "file": (
                    "demo.pdf",
                    self.pdf,
                    "application/pdf",
                )
            },
            data={
                "raw_page_texts_json": json.dumps(
                    [
                        self.page,
                        self.page,
                    ]
                )
            },
        )

        self.assertEqual(
            response.status_code,
            422,
        )


if __name__ == "__main__":
    unittest.main()