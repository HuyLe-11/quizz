import copy
import tempfile
import unittest

from pathlib import Path

import cv2
import numpy as np
import pymupdf

from backend.engine.ocr_blank_recovery import (
    recover_scan_blanks,
)


class BlankRecoveryTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()

        self.addCleanup(temp.cleanup)

        self.pdf = (
            Path(temp.name) / "scan.pdf"
        )

        self.stem = (
            "We. need more time to finish the task."
        )

        self.primary = {
            "pruned_result": {
                "width": 1000,

                "height": 1400,

                "parsing_res_list": [
                    {
                        "block_content": (
                            "Question 8. "
                            + self.stem
                            + "\nA. will B. can C. may D. must"
                        ),

                        "block_bbox": [
                            20, 200, 980, 300,
                        ],
                    },
                ],
            },
        }

        self.secondary = {
            "pruned_result": {
                "rec_texts": [
                    "Question 8. We",

                    "need more time to finish the task.",

                    "A. will",
                ],

                "rec_boxes": [
                    [0, 200, 150, 232],

                    [250, 200, 800, 232],

                    [20, 240, 130, 270],
                ],

                "rec_scores": [
                    .98, .99, .99,
                ],
            },
        }

        self.q = {
            "type": "mcp",

            "multiple_choice": False,

            "question_id": 18,

            "source_question_number": 8,

            "page_number": 1,

            "section_instruction": (
                "Choose the letter to complete "
                "each of the following sentences."
            ),

            "question_content": [
                {
                    "type": "text",

                    "value": self.stem,
                }
            ],

            "mcp_options": [
                {
                    "label": x,

                    "is_correct": x == "A",
                }

                for x in "ABCD"
            ],

            "parser_warning": "Previous warning.",
        }

        self.make_pdf()

    def make_pdf(
        self,
        rule=True,
        occupied=False,
        duplicate=False,
        native=False,
    ):
        image = np.full(
            (1400, 1000, 3),
            255,
            np.uint8,
        )

        if rule:
            cv2.line(
                image,
                (200, 245),
                (320, 245),
                (0, 0, 0),
                2,
            )

        if occupied:
            cv2.rectangle(
                image,
                (215, 226),
                (300, 239),
                (0, 0, 0),
                -1,
            )

        if duplicate:
            cv2.line(
                image,
                (200, 260),
                (320, 260),
                (0, 0, 0),
                2,
            )

        _, data = cv2.imencode(
            ".png",
            image,
        )

        with pymupdf.open() as doc:
            page = doc.new_page(
                width=500,
                height=700,
            )

            page.insert_image(
                page.rect,
                stream=data.tobytes(),
            )

            if native:
                page.insert_text(
                    (20, 20),
                    "native text",
                )

            doc.save(self.pdf)

    def recover(
        self,
        resolver=None,
        questions=None,
    ):
        return recover_scan_blanks(
            self.pdf,

            [self.primary],

            (
                [self.q]
                if questions is None
                else questions
            ),

            geometry_page_for=(
                resolver
                or (lambda _: self.secondary)
            ),
        )

    def assert_unchanged(self, **kwargs):
        old = copy.deepcopy(self.q)

        self.assertEqual(
            self.recover(**kwargs),
            [],
        )

        self.assertEqual(
            self.q,
            old,
        )

    def test_recovery_preserves_answers_and_raw(
        self,
    ):
        raw = copy.deepcopy(
            (
                self.primary,
                self.secondary,
            )
        )

        options = copy.deepcopy(
            self.q["mcp_options"]
        )

        self.assertEqual(
            self.recover(),
            [
                {
                    "page_number": 1,

                    "source_question_number": 8,
                }
            ],
        )

        self.assertEqual(
            self.q["question_content"][0]["value"],

            "We ________ need more time to finish the task.",
        )

        self.assertEqual(
            self.q["mcp_options"],
            options,
        )

        self.assertEqual(
            (
                self.primary,
                self.secondary,
            ),
            raw,
        )

        self.assertTrue(
            self.q["requires_source_image"]
        )

        self.assertTrue(
            self.q["parse_fallback"]
        )

        self.assertIn(
            "Previous warning.",
            self.q["parser_warning"],
        )

        self.assert_unchanged()

    def test_no_printed_rule(self):
        self.make_pdf(
            rule=False
        )

        self.assert_unchanged()

    def test_underline_with_ink_above_rejected(
        self,
    ):
        self.make_pdf(
            occupied=True
        )

        self.assert_unchanged()

    def test_two_rules_rejected(self):
        self.make_pdf(
            duplicate=True
        )

        self.assert_unchanged()

    def test_wrong_text_and_low_score(self):
        root = self.secondary[
            "pruned_result"
        ]

        root["rec_texts"][1] = (
            "need less time to finish the task."
        )

        self.assert_unchanged()

        root["rec_texts"][1] = (
            "need more time to finish the task."
        )

        root["rec_scores"][1] = .94

        self.assert_unchanged()

    def test_no_secondary_and_wrong_page(self):
        self.assert_unchanged(
            resolver=lambda p: p
        )

        self.secondary[
            "source_page_number"
        ] = 2

        self.assert_unchanged()

    def test_transformed_primary_rejected(
        self,
    ):
        self.primary[
            "pruned_result"
        ]["model_settings"] = {
            "use_doc_unwarping": True
        }

        self.assert_unchanged()

    def test_existing_blank_or_no_instruction(
        self,
    ):
        self.q[
            "question_content"
        ][0]["value"] = (
            "We ___ need more time to finish the task."
        )

        self.assert_unchanged()

        self.q[
            "question_content"
        ][0]["value"] = self.stem

        self.q["section_instruction"] = (
            "Choose the correct answer."
        )

        self.assert_unchanged()

    def test_duplicate_question_and_native_page(
        self,
    ):
        self.assert_unchanged(
            questions=[
                self.q,

                copy.deepcopy(self.q),
            ]
        )

        self.make_pdf(
            native=True
        )

        self.assert_unchanged()


if __name__ == "__main__":
    unittest.main()