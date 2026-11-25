"""Regression tests for separating merged PaddleOCR question blocks."""

import unittest

import fitz

from backend.engine.pdf_layout import (
    locate,
    ocr_lines,
    question_segments,
)


class LayoutRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.doc = fitz.open()
        self.page = self.doc.new_page(
            width=479.25,
            height=654.75,
        )

        self.data = {
            "pruned_result": {
                "width": 960,
                "height": 1310,
                "parsing_res_list": [
                    {
                        "block_content": (
                            "Question 2. A. One B. Two"
                        ),
                        "block_bbox": [8, 300, 640, 333],
                    },
                    {
                        "block_content": (
                            "Question 3. A. scholarship B. choose "
                            "C. character D. school\n"
                            "Question 4. A. parks B. conditions "
                            "C. systems D. letters"
                        ),
                        "block_bbox": [0, 328, 829, 392],
                    },
                    {
                        "block_content": (
                            "Question 5. A. good B. bad"
                        ),
                        "block_bbox": [0, 432, 914, 493],
                    },
                ],
            }
        }

    def tearDown(self):
        self.doc.close()

    def test_merged_block_recovers_two_questions(self):
        first = locate(
            self.page,
            3,
            self.data,
        )
        second = locate(
            self.page,
            4,
            self.data,
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)

        self.assertEqual(
            first["source"],
            "paddleocr_inferred",
        )
        self.assertEqual(
            second["source"],
            "paddleocr_inferred",
        )
        self.assertGreater(
            second["question_start_y"],
            first["question_start_y"],
        )

        first_segments, _ = question_segments(
            self.doc,
            3,
            0,
            14,
            [self.data],
        )
        second_segments, _ = question_segments(
            self.doc,
            4,
            0,
            14,
            [self.data],
        )

        for segments in (
            first_segments,
            second_segments,
        ):
            y0 = segments[0][1][1]
            y1 = segments[0][1][3]

            self.assertLess(
                y1 - y0,
                self.page.rect.height / 10,
            )

    def test_ambiguous_short_block_is_not_split(self):
        data = {
            "parsing_res_list": [
                {
                    "block_content": (
                        "Question 1: One\n"
                        "Question 2: Two"
                    ),
                    "block_bbox": [40, 50, 200, 200],
                }
            ]
        }

        self.assertIsNone(
            locate(
                self.page,
                1,
                data,
            )
        )

    def test_wrapped_lines_are_not_split(self):
        data = {
            "parsing_res_list": [
                {
                    "block_content": (
                        "Question 3. A. One B. Two\n"
                        "continued text\n"
                        "Question 4. A. Three B. Four"
                    ),
                    "block_bbox": [40, 50, 300, 220],
                }
            ]
        }

        self.assertIsNone(
            locate(
                self.page,
                3,
                data,
            )
        )

    def test_nonconsecutive_questions_are_not_split(self):
        data = {
            "parsing_res_list": [
                {
                    "block_content": (
                        "Question 3. A. One B. Two\n"
                        "Question 6. A. Three B. Four"
                    ),
                    "block_bbox": [40, 50, 300, 220],
                }
            ]
        }

        self.assertIsNone(
            locate(
                self.page,
                3,
                data,
            )
        )

    def test_normal_ocr_blocks_keep_original_source(self):
        self.assertEqual(
            locate(
                self.page,
                2,
                self.data,
            )["source"],
            "paddleocr",
        )
        self.assertEqual(
            locate(
                self.page,
                5,
                self.data,
            )["source"],
            "paddleocr",
        )

    def test_two_column_layout_still_works(self):
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
            self.assertEqual(
                set(found["options"]),
                {"A", "B"},
            )

    def test_pixel_coordinates_are_preserved(self):
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

            lines = ocr_lines(
                page,
                {"pruned_result": data},
            )

            self.assertEqual(
                lines[0]["bbox"],
                (50, 100, 200, 120),
            )


if __name__ == "__main__":
    unittest.main()
