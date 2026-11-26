import copy
import tempfile
import unittest

from pathlib import Path

import cv2
import numpy as np
import pymupdf

from backend.engine.ocr_blank_recovery import (
    UNWARPING_WARNING,
    _geometry_mode,
    recover_scan_blanks,
)

from backend.engine.pdf_layout import _sources


STEM = (
    r"A fire-fighter was injured severely "
    r"$ \underline{\text{while he}} $ to put out a fire."
)

EXPECTED = (
    "A fire-fighter was injured severely while he "
    "________ to put out a fire."
)


class OCRUnwarpedBlankRecoveryTests(unittest.TestCase):

    def setUp(self):

        temp = tempfile.TemporaryDirectory()

        self.addCleanup(temp.cleanup)

        self.pdf = Path(temp.name) / "scan.pdf"

        self.root = {
            "model_settings": {
                "use_doc_preprocessor": True,
                "use_textline_orientation": True,
            },
            "doc_preprocessor_res": {
                "model_settings": {
                    "use_doc_orientation_classify": True,
                    "use_doc_unwarping": True,
                },
                "angle": 0,
            },
            "rec_texts": [
                "Question 7. A fire-fighter was injured severely while he",
                "to put out a fire.",
                "B. helped",
                "C. has helped",
                "D. helps",
                "A.vas helping 4",
            ],
            "rec_boxes": [
                [0, 550, 538, 583],
                [651, 547, 819, 577],
                [289, 581, 386, 610],
                [516, 579, 655, 606],
                [732, 576, 827, 605],
                [34, 583, 224, 614],
            ],
            "rec_scores": [
                .997220695,
                .987146676,
                .999405205,
                .999964774,
                .990821003,
                .931734264,
            ],
        }

        self.secondary = {
            "pruned_result": self.root,
        }

        self.primary = {
            "pruned_result": {
                "width": 959,
                "height": 1310,
                "parsing_res_list": [
                    {
                        "block_content": (
                            f"Question 7. {STEM}\n"
                            "A. was helping B. helped "
                            "C. has helped D. helps"
                        ),
                        "block_bbox": [
                            0,
                            540,
                            819,
                            610,
                        ],
                    },
                ],
            },
        }

        instruction = (
            "Choose the letter A, B, C, or D "
            "to complete each question."
        )

        self.question = {
            "page_number": 1,
            "source_question_number": 7,
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
                    (
                        "was helping",
                        "helped",
                        "has helped",
                        "helps",
                    ),
                )
            ],
            "correct_answer": "A",
        }

    def make_pdf(
        self,
        *,
        rule=True,
        competing_rule=False,
        occupied=False,
    ):

        image = np.full(
            (1310, 959),
            255,
            dtype=np.uint8,
        )

        if rule:

            cv2.line(
                image,
                (538, 575),
                (651, 575),
                0,
                2,
            )

        if competing_rule:

            cv2.line(
                image,
                (300, 575),
                (413, 575),
                0,
                2,
            )

        if occupied:

            cv2.rectangle(
                image,
                (550, 559),
                (639, 570),
                0,
                -1,
            )

        ok, png = cv2.imencode(
            ".png",
            image,
        )

        self.assertTrue(ok)

        with pymupdf.open() as doc:

            page = doc.new_page(
                width=959,
                height=1310,
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

        repairs = recover_scan_blanks(
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

    def test_unwarped_geometry_remains_unregistered(self):

        self.assertFalse(
            any(
                node is self.root
                for node, _ in _sources(
                    self.secondary
                )
            )
        )

        self.assertEqual(
            _geometry_mode(
                self.secondary,
                self.root,
            ),
            "unwarped_hint",
        )

    def test_unwarped_hint_recovers_unique_printed_rule(self):

        self.make_pdf()

        repairs = self.recover()

        self.assertEqual(
            repairs,
            [
                {
                    "page_number": 1,
                    "source_question_number": 7,
                }
            ],
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

        self.assertIn(
            UNWARPING_WARNING,
            self.question["parser_warning"],
        )

    def test_unwarped_hint_rejects_competing_rules(self):

        self.make_pdf(
            competing_rule=True,
        )

        self.assertEqual(
            self.recover(),
            [],
        )

    def test_unwarped_hint_rejects_missing_rule(self):

        self.make_pdf(
            rule=False,
        )

        self.assertEqual(
            self.recover(),
            [],
        )

    def test_unwarped_hint_rejects_occupied_gap(self):

        self.make_pdf(
            occupied=True,
        )

        self.assertEqual(
            self.recover(),
            [],
        )

    def test_nonzero_rotation_is_rejected(self):

        self.root[
            "doc_preprocessor_res"
        ]["angle"] = 90

        self.make_pdf()

        self.assertIsNone(
            _geometry_mode(
                self.secondary,
                self.root,
            )
        )

        self.assertEqual(
            self.recover(),
            [],
        )

    def test_missing_angle_is_rejected(self):

        del self.root[
            "doc_preprocessor_res"
        ]["angle"]

        self.make_pdf()

        self.assertEqual(
            self.recover(),
            [],
        )

    def test_conflicting_raw_rotation_is_rejected(self):

        self.secondary["raw"] = {
            "prunedResult": {
                "doc_preprocessor_res": {
                    "angle": 180,
                },
            },
        }

        self.make_pdf()

        self.assertEqual(
            self.recover(),
            [],
        )

    def test_low_confidence_is_rejected(self):

        self.root["rec_scores"][1] = .80

        self.make_pdf()

        self.assertEqual(
            self.recover(),
            [],
        )

    def test_unsafe_primary_geometry_is_rejected(self):

        self.primary["pruned_result"][
            "doc_preprocessor_res"
        ] = {
            "angle": 0,
            "model_settings": {
                "use_doc_unwarping": True,
            },
        }

        self.make_pdf()

        self.assertEqual(
            self.recover(),
            [],
        )


if __name__ == "__main__":
    unittest.main()