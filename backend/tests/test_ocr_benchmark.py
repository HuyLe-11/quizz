"""Tests for the standalone PaddleOCR benchmark utility."""

import os
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

from scripts.benchmark_paddle_ocr import (
    main,
    run_benchmark,
)


class OCRBenchmarkTests(unittest.TestCase):
    def test_custom_page_limit_is_applied_and_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "sample.pdf"
            pdf.write_bytes(b"%PDF-1.4\nsample")

            def fake_recognize(path, model):
                self.assertEqual(
                    os.environ[
                        "PADDLEOCR_MAX_PAGES_PER_JOB"
                    ],
                    "5",
                )
                return (
                    "Recognized text",
                    {"pages": [{}, {}]},
                )

            with patch.dict(
                os.environ,
                {"PADDLEOCR_MAX_PAGES_PER_JOB": "2"},
            ):
                with patch(
                    "scripts.benchmark_paddle_ocr.recognize_pdf",
                    side_effect=fake_recognize,
                ):
                    result = run_benchmark(
                        pdf,
                        "PaddleOCR-VL-1.6",
                        5,
                    )

                self.assertEqual(
                    os.environ[
                        "PADDLEOCR_MAX_PAGES_PER_JOB"
                    ],
                    "2",
                )

            self.assertEqual(
                result["recognized_pages"],
                2,
            )
            self.assertEqual(
                result["ocr_jobs"],
                1,
            )

    def test_environment_is_restored_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "sample.pdf"
            pdf.write_bytes(b"%PDF-1.4\nsample")

            with patch.dict(
                os.environ,
                {"PADDLEOCR_MAX_PAGES_PER_JOB": "2"},
            ):
                with patch(
                    "scripts.benchmark_paddle_ocr.recognize_pdf",
                    side_effect=RuntimeError("OCR failed"),
                ):
                    with self.assertRaises(RuntimeError):
                        run_benchmark(
                            pdf,
                            "PaddleOCR-VL-1.6",
                            5,
                        )

                self.assertEqual(
                    os.environ[
                        "PADDLEOCR_MAX_PAGES_PER_JOB"
                    ],
                    "2",
                )

    def test_multiple_provider_jobs_are_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "sample.pdf"
            pdf.write_bytes(b"%PDF-1.4\nsample")

            raw = {
                "pages": [{}, {}, {}],
                "batches": [
                    {
                        "jobs": [
                            {"job_id": "first"},
                            {"job_id": "fallback"},
                        ],
                    },
                    {
                        "jobs": [
                            {"job_id": "second"},
                        ],
                    },
                ],
            }

            with patch(
                "scripts.benchmark_paddle_ocr.recognize_pdf",
                return_value=("Result", raw),
            ):
                result = run_benchmark(
                    pdf,
                    "PaddleOCR-VL-1.6",
                    2,
                )

            self.assertEqual(
                result["ocr_jobs"],
                3,
            )
            self.assertEqual(
                result["recognized_pages"],
                3,
            )

    def test_invalid_page_limit_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "sample.pdf"
            pdf.write_bytes(b"%PDF-1.4\nsample")

            with patch(
                "scripts.benchmark_paddle_ocr.load_dotenv"
            ):
                with patch.dict(
                    os.environ,
                    {"PADDLEOCR_ACCESS_TOKEN": "test-token"},
                ):
                    with patch(
                        "scripts.benchmark_paddle_ocr.recognize_pdf"
                    ) as mock_recognize:
                        result = main([
                            str(pdf),
                            "--pages-per-job",
                            "0",
                        ])

                        self.assertEqual(result, 1)
                        mock_recognize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
