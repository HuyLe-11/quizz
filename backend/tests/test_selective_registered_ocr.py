import copy
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pymupdf

from backend.engine import paddle_resilient as resilient
from backend.engine import registered_ocr_context as ctx

from backend.engine.registered_mark_snapshot import (
    collect_registered_marks,
)

from backend.engine.registered_mark_detection import (
    PageMarkResult,
    RegisteredMark,
)


class SelectiveRegisteredOCRTests(unittest.TestCase):

    def setUp(self):

        tmp = tempfile.TemporaryDirectory()

        self.addCleanup(tmp.cleanup)

        self.folder = Path(tmp.name)

        self.pdf = self.folder / "scan.pdf"

        self.primary = {
            "markdown_text":
                "Question 1. Test\nA. Alpha\nB. Beta",

            "pruned_result": {
                "parsing_res_list": [{}],
            },
        }

        self.secondary = {
            "markdown_text": "different text",

            "pruned_result": {
                "rec_boxes": [
                    [1, 2, 10, 12],
                ],
            },
        }

        self.calls = []

        self.fail = False

        self.make_pdf(red=True)

        env = patch.dict(
            "os.environ",
            {
                "PADDLEOCR_ACCESS_TOKEN":
                    "test-only",

                "QUIZPDF_REGISTERED_MARKS_ENABLED":
                    "1",

                "QUIZPDF_REGISTERED_MARKS_SUPPLEMENTAL_OCR":
                    "1",

                "PADDLEOCR_IMAGE_ALLOWED_HOSTS":
                    "images.example.com",

                "PADDLEOCR_MAX_PAGES_PER_JOB":
                    "2",

                "PADDLEOCR_MAX_UPLOAD_MB":
                    "1.5",

                "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL":
                    "PP-OCRv6",
            },
        )

        env.start()

        self.addCleanup(env.stop)

        process = patch.object(
            resilient,
            "_process_batch",
            side_effect=self.process,
        )

        process.start()

        self.addCleanup(process.stop)

    def make_pdf(
        self,
        *,
        red,
        count=1,
    ):

        pixels = np.full(
            (400, 300, 3),
            255,
            np.uint8,
        )

        cv2.circle(
            pixels,
            (60, 100),
            15,
            (0, 0, 255) if red else (0, 0, 0),
            3,
        )

        ok, data = cv2.imencode(
            ".png",
            pixels,
        )

        self.assertTrue(ok)

        with pymupdf.open() as doc:

            for _ in range(count):

                page = doc.new_page(
                    width=300,
                    height=400,
                )

                page.insert_image(
                    page.rect,
                    stream=data.tobytes(),
                )

            doc.save(self.pdf)

    def process(
        self,
        batch,
        *,
        model,
        journal,
        **kwargs,
    ):

        self.calls.append(model)

        if (
            model == "PP-OCRv6"
            and self.fail
        ):

            raise RuntimeError(
                "simulated optional failure"
            )

        template = (
            self.secondary
            if model == "PP-OCRv6"
            else self.primary
        )

        pages = [
            copy.deepcopy(template)
            for _ in range(batch.page_count)
        ]

        info = {
            "pages": [
                {
                    "width": 300,
                    "height": 400,
                }
                for _ in pages
            ],
        }

        if model == "PP-OCRv6":

            key = journal.batch_key(
                batch.start_page,
                batch.page_count,
                model,
            )

            job = f"job-{batch.start_page}"

            claim = journal.reserve(
                key
            ).record.claim_id

            journal.accept(
                key,
                claim,
                job,
            )

            journal.finish_with_result(
                key,
                claim,
                job,
                pages,
                info,
                page_count=len(pages),
            )

            ctx.remember_completed_batch(
                journal,
                batch,
                model,
                claim,
                job,
                pages,
            )

        return model, pages, info

    def recognize(self):

        return resilient.recognize_pdf(
            str(self.pdf),
            "PaddleOCR-VL-1.6",
            user_id="11111111-1111-4111-8111-111111111111",
            filename="scan.pdf",
            journal_path=self.folder / "journal.sqlite3",
        )

    def test_red_page_keeps_vl_text_and_exposes_registered_snapshot(
        self,
    ):

        with (
            ctx.capture_registered_pages() as capture,

            patch.object(
                ctx,
                "acquire_images",
                return_value=object(),
            ),

            patch.object(
                ctx,
                "detect_registered_marks",
                return_value=PageMarkResult(
                    "processed",
                    suggestions=(
                        RegisteredMark(
                            1,
                            "B",
                            (1, 2, 10, 12),
                            "local_affine",
                            "leading_region",
                        ),
                    ),
                ),
            ) as detect,
        ):

            text, raw = self.recognize()

            self.assertEqual(
                text,
                self.primary["markdown_text"],
            )

            self.assertEqual(
                raw["pages"][0],
                self.primary,
            )

            self.assertEqual(
                self.calls,
                [
                    "PaddleOCR-VL-1.6",
                    "PP-OCRv6",
                ],
            )

            copied = copy.deepcopy(
                raw["pages"][0]
            )

            self.assertIsNone(
                ctx.geometry_page_for(
                    copied
                ).get(
                    "pruned_result"
                ).get(
                    "rec_boxes"
                )
            )

            question = {
                "id": "q1",
                "question_id": 1,
                "page_number": 1,
                "type": "mcp",
                "multiple_choice": False,

                "mcp_options": [
                    {"label": "A"},
                    {"label": "B"},
                ],
            }

            snapshot = collect_registered_marks(
                self.pdf,
                raw["pages"],
                [question],
                capture,
                cache=object(),
            )

            self.assertEqual(
                snapshot[
                    "by_question"
                ][
                    "q1"
                ][
                    "detected_answer"
                ],
                "B",
            )

            self.assertEqual(
                detect.call_args.args[1],
                self.secondary["pruned_result"],
            )

        self.assertIs(
            ctx.geometry_page_for(
                raw["pages"][0]
            ),
            raw["pages"][0],
        )

    def test_black_page_skips_supplemental(self):

        self.make_pdf(red=False)

        with ctx.capture_registered_pages():

            self.recognize()

        self.assertEqual(
            self.calls,
            ["PaddleOCR-VL-1.6"],
        )

    def test_disabled_and_missing_hosts(self):

        for key, value in (
            (
                "QUIZPDF_REGISTERED_MARKS_ENABLED",
                "0",
            ),
            (
                "QUIZPDF_REGISTERED_MARKS_SUPPLEMENTAL_OCR",
                "0",
            ),
            (
                "PADDLEOCR_IMAGE_ALLOWED_HOSTS",
                "",
            ),
        ):

            self.calls.clear()

            with (
                patch.dict(
                    "os.environ",
                    {key: value},
                ),

                ctx.capture_registered_pages(),
            ):

                self.recognize()

            self.assertEqual(
                self.calls,
                ["PaddleOCR-VL-1.6"],
            )

    def test_no_context_skips_supplemental(self):

        self.recognize()

        self.assertEqual(
            self.calls,
            ["PaddleOCR-VL-1.6"],
        )

    def test_optional_failure_does_not_fail_primary(self):

        self.fail = True

        with ctx.capture_registered_pages():

            text, raw = self.recognize()

            self.assertIs(
                ctx.geometry_page_for(
                    raw["pages"][0]
                ),
                raw["pages"][0],
            )

        self.assertEqual(
            text,
            self.primary["markdown_text"],
        )

        self.assertEqual(
            self.calls,
            [
                "PaddleOCR-VL-1.6",
                "PP-OCRv6",
            ],
        )

    def test_at_most_one_supplemental_batch(self):

        self.make_pdf(
            red=True,
            count=4,
        )

        with ctx.capture_registered_pages():

            _, raw = self.recognize()

            self.assertEqual(
                len(raw["pages"]),
                4,
            )

            self.assertIn(
                "rec_boxes",

                ctx.geometry_page_for(
                    raw["pages"][1]
                )["pruned_result"],
            )

            self.assertNotIn(
                "rec_boxes",

                ctx.geometry_page_for(
                    raw["pages"][2]
                )["pruned_result"],
            )

        self.assertEqual(
            self.calls,
            [
                "PaddleOCR-VL-1.6",
                "PP-OCRv6",
                "PaddleOCR-VL-1.6",
            ],
        )

    def test_wrong_page_alias_rejected(self):

        with ctx.capture_registered_pages() as cap:

            _, raw = self.recognize()

            with pymupdf.open() as doc:

                doc.new_page()

                page = doc.new_page()

                result = cap.detect_page(
                    page,
                    raw["pages"][0],
                    {1: "AB"},
                    cache=object(),
                )

                self.assertEqual(
                    result.reason,
                    "page_number_mismatch",
                )


if __name__ == "__main__":

    unittest.main()