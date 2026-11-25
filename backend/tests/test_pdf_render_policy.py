"""Regression tests for adaptive PDF crop rasterization."""

import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

import cv2
import fitz
import numpy as np

from backend.engine.pdf_render_policy import (
    plan_crop_render,
)

from backend.engine.pdf_tools import (
    RedMarkConfig,
    crop_question_pdf,
)


class CropRenderPolicyTests(unittest.TestCase):

    def scan(
        self,
        doc,
        width=600,
        height=800,
    ):
        page = doc.new_page(
            width=width,
            height=height,
        )

        pix = fitz.Pixmap(
            fitz.csRGB,
            fitz.IRect(
                0,
                0,
                width,
                height,
            ),
            False,
        )

        pix.clear_with(255)

        page.insert_image(
            page.rect,
            pixmap=pix,
        )

        return page

    def test_native_scan_dpi(self):
        with fitz.open() as doc:

            page = self.scan(doc)

            plan = plan_crop_render(
                doc,
                [
                    (0, tuple(page.rect)),
                ],
                200,
            )

            self.assertEqual(
                (
                    plan.dpi,
                    plan.width,
                    plan.height,
                ),
                (
                    72,
                    600,
                    800,
                ),
            )

            self.assertEqual(
                plan.mode,
                "native_scan",
            )

    def test_vector_keeps_requested_dpi(self):
        with fitz.open() as doc:

            page = doc.new_page(
                width=600,
                height=800,
            )

            page.insert_text(
                (40, 60),
                "Question 1: x",
            )

            plan = plan_crop_render(
                doc,
                [
                    (0, tuple(page.rect)),
                ],
                200,
            )

            self.assertEqual(
                plan.dpi,
                200,
            )

    def test_mixed_page_keeps_requested_dpi(self):
        with fitz.open() as doc:

            page = self.scan(doc)

            page.insert_text(
                (40, 60),
                "Overlay",
            )

            plan = plan_crop_render(
                doc,
                [
                    (0, tuple(page.rect)),
                ],
                200,
            )

            self.assertEqual(
                plan.dpi,
                200,
            )

    def test_vector_marks_keep_requested_dpi(self):
        with fitz.open() as doc:

            page = self.scan(doc)

            page.draw_line(
                (30, 40),
                (40, 60),
                color=(1, 0, 0),
            )

            plan = plan_crop_render(
                doc,
                [
                    (0, tuple(page.rect)),
                ],
                200,
            )

            self.assertEqual(
                plan.dpi,
                200,
            )

    def test_annotation_keeps_requested_dpi(self):
        with fitz.open() as doc:

            page = self.scan(doc)

            page.add_rect_annot(
                fitz.Rect(
                    20,
                    20,
                    50,
                    50,
                )
            )

            plan = plan_crop_render(
                doc,
                [
                    (0, tuple(page.rect)),
                ],
                200,
            )

            self.assertEqual(
                plan.dpi,
                200,
            )

    def test_multipage_cap_includes_white_padding(self):
        with fitz.open() as doc:

            doc.new_page(
                width=600,
                height=800,
            )

            doc.new_page(
                width=300,
                height=900,
            )

            segments = [
                (0, (0, 0, 600, 800)),
                (1, (0, 0, 300, 900)),
            ]

            plan = plan_crop_render(
                doc,
                segments,
                200,
                max_pixels=1_000_000,
            )

            self.assertLess(
                plan.dpi,
                200,
            )

            self.assertLessEqual(
                plan.pixels,
                1_000_000,
            )

            sizes = [
                doc[index].get_pixmap(
                    matrix=fitz.Matrix(
                        plan.dpi / 72,
                        plan.dpi / 72,
                    ),
                    clip=fitz.Rect(box),
                )

                for index, box in segments
            ]

            self.assertEqual(
                plan.width,
                max(
                    pix.width
                    for pix in sizes
                ),
            )

            self.assertEqual(
                plan.height,
                sum(
                    pix.height
                    for pix in sizes
                ),
            )

    def test_crop_integration_retains_coordinates_and_color(self):
        with tempfile.TemporaryDirectory() as tmp:

            pdf = (
                Path(tmp) / "scan.pdf"
            )

            out = (
                Path(tmp) / "crop.png"
            )

            with fitz.open() as doc:

                page = doc.new_page(
                    width=600,
                    height=800,
                )

                image = np.full(
                    (800, 600, 3),
                    255,
                    np.uint8,
                )

                # A red region in OpenCV BGR format.
                image[
                    100:120,
                    100:120,
                ] = (0, 0, 255)

                ok, data = cv2.imencode(
                    ".png",
                    image,
                )

                self.assertTrue(ok)

                page.insert_image(
                    page.rect,
                    stream=data.tobytes(),
                )

                doc.save(pdf)

            with patch.dict(
                "os.environ",
                {
                    "PDF_CROP_MAX_PIXELS":
                        "12000000",
                },
            ):

                asset = crop_question_pdf(
                    pdf,
                    99,
                    1,
                    out,

                    config=RedMarkConfig(
                        dpi=200,
                    ),
                )

            image = cv2.imdecode(
                np.frombuffer(
                    out.read_bytes(),
                    np.uint8,
                ),
                cv2.IMREAD_COLOR,
            )

            self.assertEqual(
                image.shape[:2],
                (800, 600),
            )

            self.assertEqual(
                tuple(image[110, 110]),
                (0, 0, 255),
            )

            self.assertEqual(
                asset["source"],
                "full_page_fallback",
            )

            self.assertEqual(
                asset[
                    "crop_coordinates"
                ][0]["bbox"],

                [0, 0, 600, 800],
            )

    def test_invalid_budget(self):
        with fitz.open() as doc:

            doc.new_page(
                width=600,
                height=800,
            )

            with self.assertRaises(ValueError):

                plan_crop_render(
                    doc,
                    [
                        (0, (0, 0, 600, 800)),
                    ],
                    200,
                    max_pixels=0,
                )


if __name__ == "__main__":
    unittest.main()