"""Regression coverage for conservative OCR-to-PDF coordinate mapping."""
import unittest

import pymupdf

from backend.engine.pdf_layout import locate, ocr_lines


class IntegratedPDFLayoutTests(unittest.TestCase):
    def setUp(self):
        self.document = pymupdf.open()
        self.addCleanup(self.document.close)
        self.page = self.document.new_page(
            width=600,
            height=800,
        )

    @staticmethod
    def source():
        return {
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

    def test_rotated_ocr_does_not_supply_pdf_coordinates(self):
        source = self.source()

        source["doc_preprocessor_res"] = {
            "angle": 90
        }

        self.assertEqual(
            ocr_lines(
                self.page,
                {"pruned_result": source},
            ),
            [],
        )

    def test_incompatible_image_aspect_ratio_is_rejected(self):
        source = self.source()
        source["height"] = 900

        self.assertEqual(
            ocr_lines(
                self.page,
                {"pruned_result": source},
            ),
            [],
        )

    def test_out_of_bounds_coordinates_are_rejected(self):
        source = self.source()

        source["rec_boxes"][1] = [
            100, 1550, 220, 1700
        ]

        self.assertEqual(
            ocr_lines(
                self.page,
                {"pruned_result": source},
            ),
            [],
        )

    def test_pdf_text_layer_still_works_when_ocr_is_rotated(self):
        self.page.insert_text(
            (50, 100),
            "Question 1: Test",
        )

        self.page.insert_text(
            (50, 130),
            "A. One",
        )

        source = self.source()

        source["doc_preprocessor_res"] = {
            "angle": 90
        }

        found = locate(
            self.page,
            1,
            {"pruned_result": source},
        )

        self.assertIsNotNone(found)
        self.assertEqual(found["source"], "pymupdf")
        self.assertIn("A", found["options"])


if __name__ == "__main__":
    unittest.main()