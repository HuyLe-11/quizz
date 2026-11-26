import hashlib
import json
import os
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

import pymupdf

from backend.paddle_span_recognize import (
    recognize,
    preflight,
    summarize,
    MODEL,
    OPTIONS,
)

from backend.engine import paddle_resilient


def fake_result():

    root = {
        "return_word_box": True,
        "model_settings": {
            "use_doc_preprocessor": False
        },
        "rec_texts": [
            "H₂O"
        ],
        "rec_boxes": [
            [1, 2, 30, 20]
        ],
        "rec_scores": [
            .99
        ],
    }

    return {
        "job_id": "job-1",
        "data_info": {
            "pages": [
                {
                    "width": 100,
                    "height": 100,
                }
            ] * 2
        },
        "pages": [
            {
                "pruned_result": root,
                "raw": {
                    "prunedResult": root
                },
            }
            for _ in range(2)
        ],
    }


class SpanRecognitionTests(unittest.TestCase):

    def setUp(self):

        tmp = tempfile.TemporaryDirectory()

        self.addCleanup(
            tmp.cleanup
        )

        self.folder = Path(
            tmp.name
        )

        self.journal = (
            self.folder
            / "span-atlas-jobs.sqlite3"
        )

        png = pymupdf.Pixmap(
            pymupdf.csGRAY,
            100,
            100,
            bytes([255]) * 10000,
            False,
        ).tobytes("png")

        with pymupdf.open() as doc:

            for i in (1, 2):

                page = doc.new_page(
                    width=50,
                    height=50,
                )

                page.insert_image(
                    page.rect,
                    stream=png,
                )

                (
                    self.folder
                    / f"atlas-{i}.png"
                ).write_bytes(png)

            pdf = doc.tobytes()

        (
            self.folder / "atlas.pdf"
        ).write_bytes(pdf)

        self.manifest = {
            "scope": "diagnostic_only",
            "atlas_pdf_sha256": hashlib.sha256(
                pdf
            ).hexdigest(),
            "atlas_pdf_bytes": len(pdf),
            "atlas_pages": [
                {
                    "page": i,
                    "size": [100, 100],
                    "png_sha256": hashlib.sha256(
                        png
                    ).hexdigest(),
                }
                for i in (1, 2)
            ],
            "candidates": [
                {
                    "candidate_id": "test"
                }
            ],
        }

        (
            self.folder / "manifest.json"
        ).write_text(
            json.dumps(self.manifest),
            encoding="utf-8",
        )

    def test_preflight_no_ocr(self):

        with patch.object(
            paddle_resilient,
            "recognize_pdf",
        ) as call:

            report = recognize(
                self.folder,
                self.journal,
            )

        call.assert_not_called()

        self.assertEqual(
            report["page_count"],
            2,
        )

        self.assertFalse(
            self.journal.exists()
        )

    def test_tampered_pdf(self):

        (
            self.folder / "atlas.pdf"
        ).write_bytes(b"bad")

        with self.assertRaisesRegex(
            ValueError,
            "identity",
        ):
            preflight(self.folder)

    def test_tampered_png(self):

        (
            self.folder / "atlas-1.png"
        ).write_bytes(b"bad")

        with self.assertRaisesRegex(
            ValueError,
            "checksum",
        ):
            preflight(self.folder)

    def test_execute_single_call_and_restore(self):

        old = paddle_resilient._optional_payload

        old_env = os.environ.get(
            "PADDLEOCR_MAX_PAGES_PER_JOB"
        )

        def fake(path, model, **kwargs):

            self.assertEqual(
                Path(path).read_bytes(),
                (
                    self.folder / "atlas.pdf"
                ).read_bytes(),
            )

            self.assertEqual(
                model,
                MODEL,
            )

            self.assertEqual(
                kwargs["journal_path"],
                self.journal.resolve(),
            )

            self.assertEqual(
                paddle_resilient._optional_payload(model),
                OPTIONS,
            )

            self.assertEqual(
                os.environ[
                    "PADDLEOCR_MAX_PAGES_PER_JOB"
                ],
                "2",
            )

            self.assertEqual(
                os.environ[
                    "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL"
                ],
                "",
            )

            self.assertEqual(
                os.environ[
                    "PADDLEOCR_SUBMIT_MAX_ATTEMPTS"
                ],
                "1",
            )

            return "text", fake_result()

        with patch.object(
            paddle_resilient,
            "recognize_pdf",
            side_effect=fake,
        ) as call:

            report = recognize(
                self.folder,
                self.journal,
                execute=True,
            )

        self.assertEqual(
            call.call_count,
            1,
        )

        self.assertEqual(
            report["pages"][1]["provider_size"],
            [100, 100],
        )

        self.assertEqual(
            report["pages"][0]["lines"][0]["text"],
            "H₂O",
        )

        self.assertIs(
            paddle_resilient._optional_payload,
            old,
        )

        self.assertEqual(
            os.environ.get(
                "PADDLEOCR_MAX_PAGES_PER_JOB"
            ),
            old_env,
        )

    def test_wrong_journal_before_ocr(self):

        with patch.object(
            paddle_resilient,
            "recognize_pdf",
        ) as call:

            with self.assertRaisesRegex(
                ValueError,
                "dedicated",
            ):
                recognize(
                    self.folder,
                    self.folder / "jobs.sqlite3",
                    execute=True,
                )

        call.assert_not_called()

    def test_unknown_sizes_not_inferred(self):

        raw = fake_result()

        raw["data_info"]["pages"].pop()

        self.assertTrue(
            all(
                p["provider_size"] is None
                for p in summarize(
                    raw,
                    2,
                )["pages"]
            )
        )

    def test_reject_multi_batch_or_preprocessor(self):

        raw = fake_result()

        raw["batches"] = [1, 2]

        with self.assertRaises(ValueError):
            summarize(raw, 2)

        raw = fake_result()

        raw["pages"][0]["pruned_result"][
            "model_settings"
        ]["use_doc_preprocessor"] = True

        with self.assertRaises(ValueError):
            summarize(raw, 2)
