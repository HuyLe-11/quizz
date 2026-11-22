import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
import requests

from backend.engine import paddle_client as client


class PaddleLargePDFTests(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.pdf = self.root / "exam.pdf"

        with pymupdf.open() as document:
            for index in range(5):
                page = document.new_page()
                page.insert_text(
                    (40, 70),
                    f"Question on original page {index + 1}",
                )
            document.save(self.pdf)

    def tearDown(self):
        self.folder.cleanup()

    def test_page_splitting_preserves_original_order(self):
        batches = client._batches(
            self.pdf,
            self.root,
            max_pages=2,
            max_bytes=1024 * 1024,
        )

        self.assertEqual(
            [batch.page_count for batch in batches],
            [2, 2, 1],
        )

        for batch in batches:
            with pymupdf.open(batch.path) as document:
                self.assertEqual(
                    len(document),
                    batch.page_count,
                )
                self.assertIn(
                    f"page {batch.start_page + 1}",
                    document[0].get_text(),
                )

    def test_merged_ocr_pages_keep_global_page_numbers(self):
        def fake_process(batch, **kwargs):
            pages = [
                {
                    "markdown_text": f"page {number + 1}",
                }
                for number in range(
                    batch.start_page,
                    batch.start_page + batch.page_count,
                )
            ]
            return (
                f"job-{batch.start_page + 1}",
                pages,
                {"hello": "world"},
            )

        with patch.dict(
            "os.environ",
            {
                "PADDLEOCR_ACCESS_TOKEN": "fake",
                "PADDLEOCR_MAX_PAGES_PER_JOB": "2",
            },
        ):
            with patch.object(
                client,
                "_process_batch",
                side_effect=fake_process,
            ) as mocked:
                markdown, raw = client.recognize_pdf(
                    str(self.pdf),
                    "PaddleOCR-VL-1.6",
                )

        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(len(raw["pages"]), 5)

        self.assertEqual(
            [
                page["source_page_number"]
                for page in raw["pages"]
            ],
            [1, 2, 3, 4, 5],
        )

        self.assertEqual(
            markdown,
            "\n\n".join(
                f"page {number}"
                for number in range(1, 6)
            ),
        )

        self.assertEqual(
            raw["job_ids"],
            ["job-1", "job-3", "job-5"],
        )

        self.assertFalse(
            any(self.root.glob("ocr-part-*.pdf"))
        )

    def test_small_file_uses_legacy_single_request(self):
        with patch.dict(
            "os.environ",
            {
                "PADDLEOCR_ACCESS_TOKEN": "fake",
                "PADDLEOCR_MAX_PAGES_PER_JOB": "10",
                "PADDLEOCR_MAX_UPLOAD_MB": "10",
            },
        ):
            with patch.object(
                client,
                "_process_batch",
                return_value=(
                    "job-1",
                    [{"markdown_text": "text"}],
                    {},
                ),
            ) as mocked:
                markdown, raw = client.recognize_pdf(
                    str(self.pdf),
                    "PaddleOCR-VL-1.6",
                )

        self.assertEqual(mocked.call_count, 1)

        self.assertEqual(
            mocked.call_args.args[0].path,
            self.pdf,
        )

        self.assertEqual(
            raw,
            {
                "job_id": "job-1",
                "data_info": {},
                "pages": [{"markdown_text": "text"}],
            },
        )

        self.assertEqual(markdown, "text")

    def test_nested_write_timeout_is_classified_correctly(self):
        nested_error = requests.exceptions.ConnectionError(
            "Connection aborted.",
            TimeoutError("The write operation timed out"),
        )

        batch = client.PDFBatch(
            self.pdf,
            0,
            5,
        )

        with patch.object(
            client.requests,
            "post",
            side_effect=nested_error,
        ) as mocked_post:
            with self.assertRaises(
                client.PaddleOCRTimeoutError
            ):
                client._process_batch(
                    batch,
                    model="PaddleOCR-VL-1.6",
                    job_url="https://example.invalid/jobs",
                    headers={
                        "Authorization": "Bearer FAKE",
                    },
                    timeout=(10, 120),
                    poll_timeout=300,
                )

        self.assertEqual(
            mocked_post.call_count,
            1,
        )

    def test_missing_ocr_page_is_not_silently_merged(self):
        with patch.dict(
            "os.environ",
            {
                "PADDLEOCR_ACCESS_TOKEN": "fake",
                "PADDLEOCR_MAX_PAGES_PER_JOB": "2",
            },
        ):
            with patch.object(
                client,
                "_process_batch",
                return_value=(
                    "job",
                    [{"markdown_text": "only one page"}],
                    {},
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "different page count",
                ):
                    client.recognize_pdf(
                        str(self.pdf),
                        "PaddleOCR-VL-1.6",
                    )


if __name__ == "__main__":
    unittest.main()
