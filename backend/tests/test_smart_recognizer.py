"""Tests for the PaddleOCR-only recognition entrypoint."""

import os
import unittest

from unittest.mock import patch

from backend.engine.smart_recognizer import (
    recognize_pdf_smart,
)


class SmartRecognizerTests(unittest.TestCase):
    def setUp(self):
        self.path = "test-document.pdf"
        self.model = "PaddleOCR-VL-1.6"

        self.raw_response = {
            "job_id": "test-job-123",
            "pages": [
                {
                    "markdown_text": (
                        "Question 1: Example question"
                    ),
                },
            ],
        }

    @patch(
        "backend.engine.smart_recognizer.recognize_pdf"
    )
    def test_paddle_mode_calls_paddleocr(
        self,
        mock_ocr,
    ):
        mock_ocr.return_value = (
            "Question 1",
            self.raw_response,
        )

        with patch.dict(
            os.environ,
            {"QUIZPDF_OCR_MODE": "paddle"},
        ):
            result = recognize_pdf_smart(
                self.path,
                self.model,
            )

        mock_ocr.assert_called_once_with(
            self.path,
            self.model,
        )

        self.assertEqual(
            result[0],
            "Question 1",
        )

    @patch(
        "backend.engine.smart_recognizer.recognize_pdf"
    )
    def test_auto_mode_still_calls_paddleocr(
        self,
        mock_ocr,
    ):
        mock_ocr.return_value = (
            "Recognized text",
            self.raw_response,
        )

        with patch.dict(
            os.environ,
            {"QUIZPDF_OCR_MODE": "auto"},
        ):
            recognize_pdf_smart(
                self.path,
                self.model,
            )

        mock_ocr.assert_called_once_with(
            self.path,
            self.model,
        )

    @patch(
        "backend.engine.smart_recognizer.recognize_pdf"
    )
    def test_text_mode_is_rejected(
        self,
        mock_ocr,
    ):
        with patch.dict(
            os.environ,
            {"QUIZPDF_OCR_MODE": "text"},
        ):
            with self.assertRaises(ValueError):
                recognize_pdf_smart(
                    self.path,
                    self.model,
                )

        mock_ocr.assert_not_called()

    @patch(
        "backend.engine.smart_recognizer.recognize_pdf"
    )
    def test_local_mode_is_rejected(
        self,
        mock_ocr,
    ):
        with patch.dict(
            os.environ,
            {"QUIZPDF_OCR_MODE": "local"},
        ):
            with self.assertRaises(ValueError):
                recognize_pdf_smart(
                    self.path,
                    self.model,
                )

        mock_ocr.assert_not_called()

    @patch(
        "backend.engine.smart_recognizer.recognize_pdf"
    )
    def test_paddleocr_error_is_not_hidden(
        self,
        mock_ocr,
    ):
        mock_ocr.side_effect = TimeoutError(
            "PaddleOCR upload timed out."
        )

        with patch.dict(
            os.environ,
            {"QUIZPDF_OCR_MODE": "paddle"},
        ):
            with self.assertRaises(TimeoutError):
                recognize_pdf_smart(
                    self.path,
                    self.model,
                )

        mock_ocr.assert_called_once()

    @patch(
        "backend.engine.smart_recognizer.recognize_pdf"
    )
    def test_response_metadata_is_preserved(
        self,
        mock_ocr,
    ):
        mock_ocr.return_value = (
            "Question 1",
            self.raw_response,
        )

        with patch.dict(
            os.environ,
            {"QUIZPDF_OCR_MODE": "paddle"},
        ):
            markdown, raw = recognize_pdf_smart(
                self.path,
                self.model,
            )

        self.assertEqual(
            markdown,
            "Question 1",
        )

        self.assertEqual(
            raw["job_id"],
            "test-job-123",
        )

        self.assertEqual(
            len(raw["pages"]),
            1,
        )

        self.assertIs(
            raw,
            self.raw_response,
        )


if __name__ == "__main__":
    unittest.main()
