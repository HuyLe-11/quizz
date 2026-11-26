import copy
import tempfile
import unittest
from pathlib import Path

import pymupdf

from backend.engine.ocr_text_reconciliation import (
    reconcile_scan_choices,
)


class SuffixReconciliationTests(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()

        self.addCleanup(folder.cleanup)

        self.pdf = Path(folder.name) / "scan.pdf"

        with pymupdf.open() as doc:
            doc.new_page()
            doc.save(self.pdf)

        self.primary = {
            "markdown_text": "primary"
        }

        self.root = {
            "rec_texts": [
                "Question 4A.parks 2",
                "B. conditions",
                "C. systems",
                "D. letters",
            ],

            "rec_boxes": [
                [0, 352, 236, 393],
                [281, 363, 409, 388],
                [499, 359, 608, 383],
                [717, 356, 815, 385],
            ],

            "rec_scores": [
                .972,
                .995,
                .997,
                .996,
            ],
        }

        self.secondary = {
            "pruned_result": self.root
        }

        self.q = {
            "type": "mcp",

            "multiple_choice": False,

            "question_id": 14,

            "source_question_number": 4,

            "page_number": 1,

            "section_instruction": (
                "Choose the letter whose underlined "
                "part is pronounced differently."
            ),

            "parser_warning": "Previous warning.",

            "mcp_options": [
                {
                    "label": label,

                    "content": [
                        {
                            "type": kind,
                            "value": text,
                        },
                    ],

                    "is_correct": label == "A",
                }

                for label, kind, text in [
                    (
                        "A",
                        "latex",
                        r"park $ \underline{s} $ 2",
                    ),
                    (
                        "B",
                        "text",
                        "conditions",
                    ),
                    (
                        "C",
                        "latex",
                        r"system $ \underline{s} $",
                    ),
                    (
                        "D",
                        "text",
                        "letters",
                    ),
                ]
            ],
        }

    def repair(self):
        return reconcile_scan_choices(
            self.pdf,

            [self.primary],

            [self.q],

            geometry_page_for=lambda page: self.secondary,
        )

    def block(self, index):
        return self.q["mcp_options"][index]["content"][0]

    def assert_no_change(self):
        original = copy.deepcopy(
            self.q["mcp_options"]
        )

        self.assertEqual(
            self.repair(),
            [],
        )

        self.assertEqual(
            self.q["mcp_options"],
            original,
        )

        self.assertTrue(
            self.q["requires_source_image"]
        )

    def test_normalizes_markup_and_separates_probable_annotation(self):
        raw = copy.deepcopy(
            (
                self.primary,
                self.secondary,
            )
        )

        result = self.repair()

        self.assertEqual(
            result,
            [
                {
                    "page_number": 1,

                    "source_question_number": 4,

                    "changed_options": ["A", "C"],
                }
            ],
        )

        self.assertEqual(
            self.block(0),

            {
                "type": "text",

                "value": "parks\u0332",
            },
        )

        self.assertEqual(
            self.block(2),

            {
                "type": "text",

                "value": "systems\u0332",
            },
        )

        self.assertEqual(
            self.block(1)["value"],
            "conditions",
        )

        self.assertEqual(
            self.block(3)["value"],
            "letters",
        )

        self.assertEqual(
            [
                o["is_correct"]
                for o in self.q["mcp_options"]
            ],

            [
                True,
                False,
                False,
                False,
            ],
        )

        self.assertIn(
            "Previous warning.",

            self.q["parser_warning"],
        )

        self.assertIn(
            "số 2",

            self.q["parser_warning"],
        )

        self.assertTrue(
            self.q["requires_source_image"]
        )

        self.assertEqual(
            (
                self.primary,
                self.secondary,
            ),

            raw,
        )

        self.assertEqual(
            self.repair(),
            [],
        )

    def test_no_digit_still_normalizes(self):
        self.block(0)["value"] = (
            r"park $ \underline{s} $"
        )

        self.root["rec_texts"][0] = (
            "Question 4A.parks"
        )

        self.assertEqual(
            self.repair()[0]["changed_options"],

            ["A", "C"],
        )

        self.assertEqual(
            self.q["parser_warning"],

            "Previous warning.",
        )

    def test_secondary_disagrees_with_digit(self):
        self.root["rec_texts"][0] = (
            "Question 4A.parks"
        )

        self.assert_no_change()

    def test_multi_digit_or_multiple_annotations_rejected(self):
        self.block(0)["value"] = (
            r"park $ \underline{s} $ 12"
        )

        self.root["rec_texts"][0] = (
            "Question 4A.parks 12"
        )

        self.assert_no_change()

        self.block(0)["value"] = (
            r"park $ \underline{s} $ 2"
        )

        self.root["rec_texts"][0] = (
            "Question 4A.parks 2"
        )

        self.block(2)["value"] = (
            r"system $ \underline{s} $ 3"
        )

        self.root["rec_texts"][2] = (
            "C. systems 3"
        )

        self.assert_no_change()

    def test_other_word_disagreement_or_suffix_mismatch(self):
        self.root["rec_texts"][1] = (
            "B. condition"
        )

        self.assert_no_change()

        self.block(1)["value"] = (
            "condition"
        )

        self.assert_no_change()

    def test_low_confidence_or_one_marked_suffix_rejected(self):
        self.root["rec_scores"][2] = .94

        self.assert_no_change()

        self.root["rec_scores"][2] = .99

        self.block(2).update(
            type="text",
            value="systems",
        )

        self.assert_no_change()

    def test_unknown_markup_and_multiblock_content_rejected(self):
        self.block(2)["value"] = (
            r"system $ \text{s} $"
        )

        self.assert_no_change()

        self.block(2)["value"] = (
            r"system $ \underline{s} $"
        )

        self.q["mcp_options"][2]["content"].append(
            {
                "type": "text",
                "value": "extra",
            }
        )

        self.assert_no_change()


if __name__ == "__main__":
    unittest.main()