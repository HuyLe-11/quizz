import copy
import tempfile
import unittest
from pathlib import Path

import pymupdf

from backend.engine.ocr_text_reconciliation import (
    reconcile_scan_choices,
)


class ReconciliationTests(unittest.TestCase):

    def setUp(self):

        temp = tempfile.TemporaryDirectory()

        self.addCleanup(temp.cleanup)

        self.pdf = Path(temp.name) / "scan.pdf"

        self.make_pdf()

        self.primary = {
            "markdown_text": "original OCR",
        }

        self.secondary = {
            "pruned_result": {
                "rec_texts": [
                    "Question 2. A. destroyed",
                    "B. performed",
                    "C.vanted 1",
                    "D.combined",
                ],
                "rec_scores": [
                    .98, .99, .86, .99,
                ],
                "rec_boxes": [
                    [0, 10, 250, 55],
                    [280, 25, 410, 50],
                    [490, 20, 630, 52],
                    [710, 24, 840, 50],
                ],
            },
        }

        self.q = {
            "type": "mcp",
            "multiple_choice": False,
            "question_id": 12,
            "source_question_number": 2,
            "page_number": 1,
            "section_instruction": (
                "Choose the letter whose underlined "
                "part is pronounced differently."
            ),
            "mcp_options": [
                {
                    "label": label,
                    "content": [
                        {
                            "type": "text",
                            "value": value,
                        },
                    ],
                    "is_correct": label == "C",
                }
                for label, value in zip(
                    "ABCD",
                    [
                        "destroy/ed",
                        "perform/ed",
                        "wanted",
                        "combined",
                    ],
                )
            ],
        }

    def make_pdf(self, native=False):

        with pymupdf.open() as doc:

            page = doc.new_page()

            if native:
                page.insert_text(
                    (40, 50),
                    "Native text",
                )

            doc.save(self.pdf)

    def values(self):

        return [
            o["content"][0]["value"]
            for o in self.q["mcp_options"]
        ]

    def run_repair(
        self,
        questions=None,
        resolver=None,
    ):

        return reconcile_scan_choices(
            self.pdf,
            [self.primary],
            [self.q] if questions is None else questions,
            geometry_page_for=(
                resolver
                or (lambda p: self.secondary)
            ),
        )

    def test_corrects_only_corroborated_split_words(self):

        before = copy.deepcopy(self.q)

        raw = copy.deepcopy(
            (self.primary, self.secondary)
        )

        result = self.run_repair()

        self.assertEqual(
            self.values(),
            [
                "destroyed",
                "performed",
                "wanted",
                "combined",
            ],
        )

        self.assertEqual(
            result,
            [
                {
                    "page_number": 1,
                    "source_question_number": 2,
                    "changed_options": ["A", "B"],
                },
            ],
        )

        self.assertEqual(
            [
                o["is_correct"]
                for o in self.q["mcp_options"]
            ],
            [
                o["is_correct"]
                for o in before["mcp_options"]
            ],
        )

        self.assertEqual(
            (self.primary, self.secondary),
            raw,
        )

        self.assertTrue(
            self.q["requires_source_image"]
        )

        self.assertTrue(
            self.q["parse_fallback"]
        )

        self.assertEqual(
            self.run_repair(),
            [],
        )

    def test_no_independent_ocr_only_flags(self):

        self.assertEqual(
            self.run_repair(
                resolver=lambda p: p
            ),
            [],
        )

        self.assertEqual(
            self.values()[0],
            "destroy/ed",
        )

        self.assertTrue(
            self.q["requires_source_image"]
        )

    def test_single_match_and_low_confidence(self):

        self.secondary[
            "pruned_result"
        ]["rec_scores"][1] = .94

        self.assertEqual(
            self.run_repair(),
            [],
        )

        self.assertEqual(
            self.values()[:2],
            [
                "destroy/ed",
                "perform/ed",
            ],
        )

    def test_duplicate_heading_or_question_rejected(self):

        root = self.secondary["pruned_result"]

        for k in (
            "rec_texts",
            "rec_scores",
            "rec_boxes",
        ):
            root[k].append(root[k][0])

        self.assertEqual(
            self.run_repair(),
            [],
        )

        for k in (
            "rec_texts",
            "rec_scores",
            "rec_boxes",
        ):
            root[k].pop()

        self.assertEqual(
            self.run_repair(
                [
                    self.q,
                    copy.deepcopy(self.q),
                ]
            ),
            [],
        )

    def test_misaligned_and_duplicate_option_rejected(self):

        root = self.secondary["pruned_result"]

        root["rec_boxes"][1] = [
            280, 100, 410, 125,
        ]

        self.assertEqual(
            self.run_repair(),
            [],
        )

        root["rec_boxes"][1] = [
            280, 25, 410, 50,
        ]

        root["rec_texts"][2] = "B. performed"

        self.assertEqual(
            self.run_repair(),
            [],
        )

    def test_existing_latex_and_warning_preserved(self):

        self.q["mcp_options"][0][
            "content"
        ][0]["type"] = "latex"

        self.q["parser_warning"] = (
            "previous warning"
        )

        self.assertEqual(
            self.run_repair(),
            [],
        )

        self.assertEqual(
            self.values()[0],
            "destroy/ed",
        )

        self.assertEqual(
            self.q["parser_warning"],
            "previous warning",
        )

    def test_non_pronunciation_and_native_page_not_modified(self):

        old = self.q["section_instruction"]

        self.q["section_instruction"] = (
            "Choose the correct answer."
        )

        before = copy.deepcopy(self.q)

        self.assertEqual(
            self.run_repair(),
            [],
        )

        self.assertEqual(
            self.q,
            before,
        )

        self.q["section_instruction"] = old

        self.make_pdf(native=True)

        before = copy.deepcopy(self.q)

        self.assertEqual(
            self.run_repair(),
            [],
        )

        self.assertEqual(
            self.q,
            before,
        )

    def test_wrong_page_rejected(self):

        self.secondary["source_page_number"] = 2

        self.assertEqual(
            self.run_repair(),
            [],
        )

        self.assertEqual(
            self.values()[0],
            "destroy/ed",
        )


if __name__ == "__main__":
    unittest.main()