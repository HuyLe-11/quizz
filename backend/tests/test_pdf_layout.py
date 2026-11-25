import tempfile
import unittest
from pathlib import Path

import fitz

from backend.engine.pdf_layout import (
    ocr_lines,
    locate,
)

from backend.engine.pdf_tools import (
    RedMarkConfig,
    crop_question_pdf,
    detect_red_mark_pdf,
)


class PdfLayoutTests(unittest.TestCase):

    def test_pixel_coordinates_use_actual_image_dimensions(self):
        with fitz.open() as doc:
            page = doc.new_page(
                width=600,
                height=800,
            )

            data = {
                "width": 1200,
                "height": 1600,
                "rec_texts": [
                    "Question 1: Test",
                    "A. One",
                ],
                "rec_boxes": [
                    [100, 200, 400, 240],
                    [100, 300, 220, 340],
                ],
            }

            result = ocr_lines(
                page,
                {
                    "pruned_result": data,
                    "raw": {
                        "prunedResult": data,
                    },
                },
            )

            self.assertEqual(
                len(result),
                2,
            )

            self.assertEqual(
                result[0]["bbox"],
                (50, 100, 200, 120),
            )

    def test_polygon_coordinates(self):
        with fitz.open() as doc:
            page = doc.new_page(
                width=600,
                height=800,
            )

            data = {
                "width": 1200,
                "height": 1600,
                "rec_texts": [
                    "Question 1: Test",
                ],
                "rec_polys": [
                    [
                        [100, 200],
                        [400, 200],
                        [400, 240],
                        [100, 240],
                    ],
                ],
            }

            self.assertEqual(
                ocr_lines(page, data)[0]["bbox"],
                (50, 100, 200, 120),
            )

    def test_unknown_pixel_scale_not_guessed(self):
        with fitz.open() as doc:
            page = doc.new_page(
                width=600,
                height=800,
            )

            self.assertEqual(
                ocr_lines(
                    page,
                    {
                        "rec_texts": [
                            "Question 1",
                        ],
                        "rec_boxes": [
                            [100, 200, 900, 1000],
                        ],
                    },
                ),
                [],
            )

    def test_two_columns_do_not_mix_options(self):
        with fitz.open() as doc:
            page = doc.new_page(
                width=600,
                height=800,
            )

            for x, number in [
                (40, 1),
                (340, 2),
            ]:
                page.insert_text(
                    (x, 80),
                    f"Question {number}: Test",
                )

                page.insert_text(
                    (x, 120),
                    "A. One",
                )

                page.insert_text(
                    (x, 160),
                    "B. Two",
                )

            found = locate(page, 1)

            self.assertLess(
                found["column_x"][1],
                340,
            )

            self.assertLess(
                found["options"]["A"][0],
                340,
            )

            self.assertEqual(
                set(found["options"]),
                {"A", "B"},
            )

    def test_multiline_block_with_two_questions_is_ambiguous(self):
        with fitz.open() as doc:
            page = doc.new_page(
                width=600,
                height=800,
            )

            data = {
                "parsing_res_list": [
                    {
                        "block_content": (
                            "Question 1: One\n"
                            "Question 2: Two"
                        ),
                        "block_bbox": [
                            40,
                            50,
                            200,
                            200,
                        ],
                    },
                ],
            }

            self.assertIsNone(
                locate(
                    page,
                    1,
                    data,
                )
            )

    def test_cross_page_crop_stops_at_nonconsecutive_question(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "sample.pdf"

            with fitz.open() as doc:
                page = doc.new_page(
                    width=600,
                    height=800,
                )

                page.insert_text(
                    (40, 700),
                    "Question 3: Test",
                )

                page.insert_text(
                    (40, 750),
                    "A. One",
                )

                page = doc.new_page(
                    width=600,
                    height=800,
                )

                page.insert_text(
                    (40, 70),
                    "B. Two",
                )

                page.insert_text(
                    (40, 200),
                    "Question 8: Next",
                )

                doc.save(pdf)

            result = crop_question_pdf(
                pdf,
                3,
                1,
                Path(directory) / "câu hỏi.png",
                config=RedMarkConfig(dpi=72),
            )

            self.assertEqual(
                [
                    item["page_number"]
                    for item in result["crop_coordinates"]
                ],
                [1, 2],
            )

            self.assertLess(
                result["crop_coordinates"][1]["bbox"][3],
                200,
            )

            self.assertTrue(
                Path(result["image_path"]).is_file()
            )

    def test_no_evidence_of_continuation_and_invalid_page(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "sample.pdf"

            with fitz.open() as doc:
                page = doc.new_page(
                    width=600,
                    height=800,
                )

                page.insert_text(
                    (40, 70),
                    "Question 3: Test",
                )

                page = doc.new_page(
                    width=600,
                    height=800,
                )

                page.insert_text(
                    (40, 70),
                    "Question 8: Next",
                )

                doc.save(pdf)

            output = Path(directory) / "crop.png"

            result = crop_question_pdf(
                pdf,
                3,
                1,
                output,
                config=RedMarkConfig(dpi=72),
            )

            self.assertEqual(
                len(result["crop_coordinates"]),
                1,
            )

            with self.assertRaises(ValueError):
                crop_question_pdf(
                    pdf,
                    3,
                    99,
                    output,
                )

            self.assertEqual(
                detect_red_mark_pdf(
                    pdf,
                    3,
                    99,
                    ["A"],
                )["status"],
                "not_found",
            )


if __name__ == "__main__":
    unittest.main()
