import unittest

import fitz

from backend.engine.option_geometry import (
    exact_marker_boxes,
    recognition_marker_geometry,
)

from backend.engine.pdf_layout import locate


class OptionGeometryTests(unittest.TestCase):

    def setUp(self):

        self.doc = fitz.open()

        self.addCleanup(
            self.doc.close
        )

        self.page = self.doc.new_page(
            width=600,
            height=800,
        )

    def test_native_inline(self):

        self.page.insert_text(
            (40, 70),
            "Question 1: Select.",
        )

        for x, label in zip(
            (40, 130, 310, 475),
            "ABCD",
        ):

            self.page.insert_text(
                (x, 100),
                f"{label}. 12",
            )

        self.page.insert_text(
            (40, 130),
            "Question 2: Next.",
        )

        self.page.insert_text(
            (40, 150),
            "A. Another",
        )

        result = locate(
            self.page,
            1,
        )

        self.assertEqual(
            result["option_anchor_source"],
            "native_words",
        )

        self.assertEqual(
            set(result["option_anchors"]),
            set("ABCD"),
        )

        self.assertEqual(
            [
                round(
                    result["option_anchors"][label][0]
                )
                for label in "ABCD"
            ],
            [40, 130, 310, 475],
        )

    def test_two_rows_unequal_spacing(self):

        self.page.insert_text(
            (40, 70),
            "Question 1: Select.",
        )

        for (x, y), label in zip(
            [
                (40, 100),
                (240, 100),
                (55, 155),
                (350, 155),
            ],
            "ABCD",
        ):

            self.page.insert_text(
                (x, y),
                f"{label}. 12",
            )

        result = locate(
            self.page,
            1,
        )

        self.assertEqual(
            set(result["option_anchors"]),
            set("ABCD"),
        )

        self.assertGreater(
            result["option_anchors"]["C"][1],
            result["option_anchors"]["B"][3],
        )

    def test_native_same_line_as_question(self):

        self.page.insert_text(
            (40, 70),
            "Question 1: A. one B. two C. three D. four",
        )

        result = locate(
            self.page,
            1,
        )

        self.assertEqual(
            set(result["option_anchors"]),
            set("ABCD"),
        )

    def test_recognition_boxes_are_scaled(self):

        data = {
            "pruned_result": {

                "width": 1200,

                "height": 1600,

                "rec_texts": [
                    "Question 1: Test",
                    "A.",
                    "B.",
                    "C.",
                    "D.",
                    "Question 2: Test",
                ],

                "rec_boxes": [
                    [80, 100, 420, 130],
                    [80, 180, 100, 210],
                    [240, 180, 260, 210],
                    [620, 180, 640, 210],
                    [960, 180, 980, 210],
                    [80, 260, 420, 290],
                ],
            }
        }

        result = locate(
            self.page,
            1,
            data,
        )

        self.assertEqual(
            result["option_anchor_source"],
            "ocr_markers",
        )

        self.assertEqual(
            result["option_anchors"]["C"],
            (310, 90, 320, 105),
        )

    def test_coarse_layout_not_promoted(self):

        data = {
            "pruned_result": {

                "width": 600,

                "height": 800,

                "parsing_res_list": [
                    {
                        "block_content": (
                            "Question 1: "
                            "A. x B. y C. z D. w"
                        ),

                        "block_bbox": [
                            40, 80, 550, 120
                        ],
                    }
                ],
            }
        }

        result = locate(
            self.page,
            1,
            data,
        )

        self.assertEqual(
            result["option_anchors"],
            {},
        )

        self.assertEqual(
            result["option_anchor_source"],
            "none",
        )

    def test_duplicate_label_is_ambiguous(self):

        items = [
            {
                "text": "A.",
                "bbox": (20, 20, 30, 35),
            },
            {
                "text": "A.",
                "bbox": (40, 20, 50, 35),
            },
            {
                "text": "B.",
                "bbox": (60, 20, 70, 35),
            },
        ]

        self.assertEqual(
            exact_marker_boxes(
                items,
                (0, 0, 100, 100),
            ),
            {},
        )

    def test_no_cross_boundary_or_inferred_marker(self):

        items = [
            {
                "text": "A.",
                "bbox": (20, 20, 30, 35),
            },
            {
                "text": "B.",
                "bbox": (60, 20, 70, 35),
            },
            {
                "text": "C.",
                "bbox": (20, 90, 30, 110),
            },
            {
                "text": "D.",
                "bbox": (60, 50, 70, 65),
                "inferred_line_bounds": True,
            },
        ]

        geometry = recognition_marker_geometry(
            [items],
            (0, 0, 100, 100),
        )

        self.assertTrue(
            geometry.covers("AB")
        )

        self.assertFalse(
            geometry.covers("ABCD")
        )

    def test_conflicting_sources_are_not_merged(self):

        first = [
            {
                "text": "A.",
                "bbox": (20, 20, 30, 35),
            },
            {
                "text": "B.",
                "bbox": (60, 20, 70, 35),
            },
        ]

        second = [
            first[0],
            {
                "text": "B.",
                "bbox": (80, 20, 90, 35),
            },
        ]

        self.assertEqual(
            recognition_marker_geometry(
                [first, second],
                (0, 0, 100, 100),
            ).anchors,
            {},
        )

    def test_unsafe_ocr_transform(self):

        data = {
            "pruned_result": {

                "width": 600,

                "height": 800,

                "model_settings": {
                    "use_doc_unwarping": True,
                },

                "rec_texts": [
                    "Question 1: Test",
                    "A.",
                    "B.",
                ],

                "rec_boxes": [
                    [40, 70, 200, 85],
                    [40, 100, 50, 115],
                    [200, 100, 210, 115],
                ],
            }
        }

        self.assertIsNone(
            locate(
                self.page,
                1,
                data,
            )
        )


if __name__ == "__main__":
    unittest.main()