import copy
import tempfile
import unittest

from pathlib import Path

import cv2
import numpy as np
import pymupdf

from backend.engine.ocr_inline_image_verification import (
    _valid_date,
    find_inline_rule,
    recover_inline_scan_blanks,
)


STEM = (
    r"Vietnamese Independence Day, which is celebrated annually  "
    r"$ \underline{\text{2nd}} $, holds great significance "
    r"for all Vietnamese people."
)

EXPECTED = (
    "Vietnamese Independence Day, which is celebrated annually "
    "________ September 2nd, holds great significance "
    "for all Vietnamese people."
)


class OCRInlineImageVerificationTests(unittest.TestCase):

    def setUp(self):

        temp = tempfile.TemporaryDirectory()

        self.addCleanup(temp.cleanup)

        self.pdf = Path(
            temp.name
        ) / "scan.pdf"

        self.image = np.full(
            (220, 1866),
            255,
            dtype=np.uint8,
        )

        cv2.line(
            self.image,
            (1371, 60),
            (1466, 60),
            0,
            4,
        )

        for x in (
            1340,
            1352,
            1477,
            1489,
        ):

            cv2.rectangle(
                self.image,
                (x, 30),
                (x + 7, 49),
                0,
                -1,
            )

        self.primary = {
            "pruned_result": {
                "width": 1866,
                "height": 400,
                "parsing_res_list": [
                    {
                        "block_content": (
                            f"Question 13. {STEM}\n"
                            "A. on B. in C. at D. from"
                        ),
                        "block_bbox": [
                            0,
                            0,
                            1866,
                            220,
                        ],
                    },
                ],
            },
        }

        self.secondary = {
            "pruned_result": {
                "model_settings": {
                    "use_doc_preprocessor": True,
                },
                "doc_preprocessor_res": {
                    "model_settings": {
                        "use_doc_unwarping": True,
                    },
                    "angle": 0,
                },
                "rec_texts": [
                    (
                        "Question 13. Vietnamese Independence Day, "
                        "which is celebrated annuallySeptember2nd, hold"
                    ),
                    "great significance for all Vietnamese people.",
                    "B. in",
                    "C. at",
                    "D. from",
                ],
                "rec_scores": [
                    .9916917681694031,
                    .9970950484275818,
                    .996,
                    .996,
                    .939,
                ],
            },
        }

        instruction = (
            "Choose the letter A, B, C, or D "
            "to complete each question."
        )

        self.question = {
            "page_number": 1,
            "source_question_number": 13,
            "type": "mcp",
            "multiple_choice": False,
            "section_instruction": instruction,
            "question_content": [
                {
                    "type": "text",
                    "value": instruction,
                },
                {
                    "type": "latex",
                    "value": STEM,
                },
            ],
            "mcp_options": [
                {
                    "label": label,
                    "content": [
                        {
                            "type": "text",
                            "value": value,
                        },
                    ],
                }
                for label, value in zip(
                    "ABCD",
                    ("on", "in", "at", "from"),
                )
            ],
            "correct_answer": "A",
        }

    def make_pdf(self):

        image = np.full(
            (400, 1866),
            255,
            dtype=np.uint8,
        )

        image[:220] = self.image

        ok, png = cv2.imencode(
            ".png",
            image,
        )

        self.assertTrue(ok)

        with pymupdf.open() as doc:

            page = doc.new_page(
                width=466.5,
                height=100,
            )

            page.insert_image(
                page.rect,
                stream=png.tobytes(),
            )

            doc.save(self.pdf)

    def recover(self):

        before = copy.deepcopy(
            self.question
        )

        repairs = recover_inline_scan_blanks(
            self.pdf,
            [self.primary],
            [self.question],
            geometry_page_for=lambda _: self.secondary,
        )

        self.assertEqual(
            self.question["mcp_options"],
            before["mcp_options"],
        )

        self.assertEqual(
            self.question["correct_answer"],
            before["correct_answer"],
        )

        self.assertEqual(
            self.question["question_content"][0],
            before["question_content"][0],
        )

        if not repairs:

            self.assertEqual(
                self.question,
                before,
            )

        return repairs

    def test_valid_printed_rule(self):

        self.assertIsNotNone(
            find_inline_rule(
                self.image,
                .775,
            )
        )

    def test_competing_rule_is_rejected(self):

        cv2.line(
            self.image,
            (700, 60),
            (795, 60),
            0,
            4,
        )

        self.assertIsNone(
            find_inline_rule(
                self.image,
                .775,
            )
        )

    def test_occupied_gap_is_rejected(self):

        cv2.rectangle(
            self.image,
            (1390, 35),
            (1445, 50),
            0,
            -1,
        )

        self.assertIsNone(
            find_inline_rule(
                self.image,
                .775,
            )
        )

    def test_wrong_anchor_is_rejected(self):

        self.assertIsNone(
            find_inline_rule(
                self.image,
                .55,
            )
        )

    def test_missing_rule_is_rejected(self):

        self.image[:] = 255

        self.assertIsNone(
            find_inline_rule(
                self.image,
                .775,
            )
        )

    def test_missing_right_text_is_rejected(self):

        self.image[:, 1471:] = 255

        self.assertIsNone(
            find_inline_rule(
                self.image,
                .775,
            )
        )

    def test_valid_dates(self):

        self.assertTrue(
            _valid_date(
                "September",
                "2nd",
            )
        )

        self.assertTrue(
            _valid_date(
                "October",
                "21st",
            )
        )

        self.assertTrue(
            _valid_date(
                "February",
                "29th",
            )
        )

    def test_invalid_dates(self):

        for month, day in (
            ("February", "30th"),
            ("September", "31st"),
            ("September", "2th"),
            ("September", "11st"),
            ("Example", "2nd"),
        ):

            with self.subTest(
                month=month,
                day=day,
            ):

                self.assertFalse(
                    _valid_date(
                        month,
                        day,
                    )
                )

    def test_full_reconstruction(self):

        self.make_pdf()

        repairs = self.recover()

        self.assertEqual(
            len(repairs),
            1,
        )

        self.assertEqual(
            repairs[0]["inserted_word"],
            "September",
        )

        self.assertEqual(
            self.question["question_content"][-1],
            {
                "type": "text",
                "value": EXPECTED,
            },
        )

        self.assertTrue(
            self.question["requires_source_image"]
        )

        self.assertTrue(
            self.question["parse_fallback"]
        )

    def test_full_reconstruction_without_rule(self):

        self.image[:] = 255

        self.make_pdf()

        self.assertEqual(
            self.recover(),
            [],
        )

    def test_full_reconstruction_low_confidence(self):

        self.secondary["pruned_result"][
            "rec_scores"
        ][0] = .80

        self.make_pdf()

        self.assertEqual(
            self.recover(),
            [],
        )

    def test_rotated_geometry_is_rejected(self):

        self.secondary["pruned_result"][
            "doc_preprocessor_res"
        ]["angle"] = 90

        self.make_pdf()

        self.assertEqual(
            self.recover(),
            [],
        )


if __name__ == "__main__":
    unittest.main()