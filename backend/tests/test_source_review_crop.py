import unittest

import fitz

from backend.engine.pdf_layout import question_segments


def block(text, box):
    return {
        "block_content": text,
        "block_bbox": box,
    }


class SourceReviewCropTests(unittest.TestCase):

    def setUp(self):
        self.doc = fitz.open()

        self.addCleanup(self.doc.close)

        self.page = self.doc.new_page(
            width=500,
            height=700,
        )

        self.root = {
            "width": 1000,
            "height": 1400,
            "parsing_res_list": [
                block(
                    "Choose an answer",
                    [30, 140, 900, 208],
                ),
                block(
                    "Question 1. A. alpha B. beta\n"
                    "Question 2. A. one B. two",
                    [30, 200, 900, 280],
                ),
                block(
                    "Question 3. A. red B. blue",
                    [30, 270, 900, 320],
                ),
                block(
                    "Question 4. A. x B. y",
                    [30, 310, 900, 360],
                ),
                block(
                    "Question 5. A. m B. n",
                    [30, 400, 900, 445],
                ),
                block(
                    "Question 6. A. p B. q",
                    [30, 500, 900, 550],
                ),
            ],
        }

    def locate(self, number, **kwargs):
        return question_segments(
            self.doc,
            number,
            0,
            14,
            [
                {
                    "pruned_result": self.root,
                }
            ],
            **kwargs,
        )

    def test_merged_and_overlapping_blocks_are_preserved(
        self,
    ):
        for number in range(1, 5):

            with self.subTest(number=number):

                segments, location = self.locate(
                    number,
                    review_group=True,
                )

                self.assertEqual(
                    location["source"],
                    "paddleocr_review_group",
                )

                self.assertEqual(
                    segments,
                    [
                        (
                            0,
                            (
                                0.0,
                                56.0,
                                500.0,
                                194.0
                                if number < 4
                                else 200.0,
                            ),
                        )
                    ],
                )

    def test_default_preserves_individual_crops(
        self,
    ):
        expected = [
            (
                1,
                "paddleocr_inferred",
                118.0,
            ),
            (
                2,
                "paddleocr_inferred",
                133.0,
            ),
            (
                3,
                "paddleocr",
                155.0,
            ),
            (
                4,
                "paddleocr",
                200.0,
            ),
        ]

        for number, source, bottom in expected:

            with self.subTest(number=number):

                segments, location = self.locate(
                    number
                )

                self.assertEqual(
                    location["source"],
                    source,
                )

                self.assertEqual(
                    segments[0][1][3],
                    bottom,
                )

                self.assertEqual(
                    self.locate(
                        number,
                        review_group=False,
                    ),
                    (
                        segments,
                        location,
                    ),
                )

    def test_unrelated_question_keeps_standard_crop(
        self,
    ):
        self.assertEqual(
            self.locate(
                5,
                review_group=True,
            ),
            self.locate(5),
        )

    def test_inferred_without_dimensions_falls_back(
        self,
    ):
        self.root.pop("width")

        self.root.pop("height")

        self.root["parsing_res_list"] = [
            block(
                "Question 1. A. alpha B. beta\n"
                "Question 2. A. one B. two",
                [20, 100, 450, 140],
            ),
            block(
                "Question 3. A. red B. blue",
                [20, 160, 450, 180],
            ),
        ]

        segments, location = self.locate(
            1,
            review_group=True,
        )

        self.assertIsNone(
            location
        )

        self.assertEqual(
            segments,
            [
                (
                    0,
                    tuple(self.page.rect),
                )
            ],
        )

        self.assertEqual(
            self.locate(1)[1]["source"],
            "paddleocr_inferred",
        )

    def test_unwarped_coordinates_not_used(
        self,
    ):
        self.root["model_settings"] = {
            "use_doc_unwarping": True,
        }

        segments, location = self.locate(
            1,
            review_group=True,
        )

        self.assertIsNone(
            location
        )

        self.assertEqual(
            segments,
            [
                (
                    0,
                    tuple(self.page.rect),
                )
            ],
        )

    def test_native_page_unchanged(
        self,
    ):
        self.page.insert_text(
            (30, 70),
            "Question 1. Native question",
        )

        self.page.insert_text(
            (30, 90),
            "A. alpha",
        )

        self.page.insert_text(
            (30, 150),
            "Question 2. Native next question",
        )

        segments, location = self.locate(
            1,
            review_group=True,
        )

        self.assertEqual(
            location["source"],
            "pymupdf",
        )

        self.assertLess(
            segments[0][1][3],
            150,
        )

        self.assertEqual(
            self.locate(1),
            (
                segments,
                location,
            ),
        )


if __name__ == "__main__":
    unittest.main()