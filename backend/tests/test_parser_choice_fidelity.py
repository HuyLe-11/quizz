"""Regression tests using actual PaddleOCR pronunciation formats."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from backend.engine.pdf_fidelity import LOW_LINE
from backend.engine.parser_choice_fidelity import (
    _letters,
    reconcile_pronunciation_choices,
)

PDF = Path(
    os.getenv("QUIZPDF_TEST_PDF", "test.pdf")
)

INSTRUCTION = (
    "Mark the letter A, B, C or D on your answer sheet to indicate "
    "the word whose underlined part differs [from the other three "
    "in pronunciation in each of the following questions."
)


def question(
    number: int,
    values: list[str],
    section: str = INSTRUCTION,
) -> dict:
    return {
        "question_id": number,
        "page_number": 1,
        "section_instruction": section,
        "mcp_options": [
            {
                "label": label,
                "content": [
                    {
                        "type": "latex",
                        "value": value,
                    }
                ],
            }
            for label, value in zip(
                "ABCD",
                values,
            )
        ],
    }


def values(item: dict) -> list[str]:
    return [
        option["content"][0]["value"]
        for option in item["mcp_options"]
    ]


class ChoiceFidelityTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        if not PDF.is_file():
            raise unittest.SkipTest(
                "Set QUIZPDF_TEST_PDF to original test.pdf"
            )
        cls.pdf = PDF.read_bytes()

    def test_latex_normalizer(self):
        self.assertEqual(
            _letters(r"co $ \underline{oa} $st"),
            "cooast",
        )
        self.assertEqual(
            _letters(r"n $ \underline{a} $ture"),
            "nature",
        )
        self.assertEqual(
            _letters(r"pho $ \underline{to} $"),
            "photo",
        )
        self.assertEqual(
            _letters(
                "co" + LOW_LINE + "a" + LOW_LINE + "st"
            ),
            "coast",
        )
        self.assertEqual(
            _letters(r"\unknown{abc}"),
            "",
        )

    def test_real_q5_latex_from_preview(self):
        item = question(5, [
            r"co $ \underline{oa} $st",
            r"b $ \underline{oa} $rd",
            r"b $ \underline{oa} $t",
            r"r $ \underline{oa} $d",
        ])

        repairs = reconcile_pronunciation_choices(
            self.pdf,
            [item],
        )

        self.assertEqual(values(item), [
            "co" + LOW_LINE + "a" + LOW_LINE + "st",
            "bo" + LOW_LINE + "a" + LOW_LINE + "rd",
            "bo" + LOW_LINE + "a" + LOW_LINE + "t",
            "ro" + LOW_LINE + "a" + LOW_LINE + "d",
        ])

        self.assertEqual(
            repairs[0]["changed_options"],
            list("ABCD"),
        )

        self.assertTrue(all(
            option["content"][0]["type"] == "text"
            for option in item["mcp_options"]
        ))

    def test_real_q6_latex_from_preview(self):
        item = question(6, [
            r"n $ \underline{a} $ture",
            r"ar $ \underline{t} $ist",
            r"w $ \underline{i} $nter",
            r"pho $ \underline{to} $",
        ])

        repairs = reconcile_pronunciation_choices(
            self.pdf,
            [item],
        )

        self.assertEqual(values(item), [
            "nat" + LOW_LINE + "ure",
            "art" + LOW_LINE + "ist",
            "wint" + LOW_LINE + "er",
            "phot" + LOW_LINE + "o",
        ])

        self.assertEqual(
            repairs[0]["question_number"],
            6,
        )
        self.assertEqual(
            repairs[0]["changed_options"],
            list("ABCD"),
        )

    def test_duplicate_o_recovered(self):
        item = question(
            5,
            ["co oast", "board", "boat", "road"],
        )

        repairs = reconcile_pronunciation_choices(
            self.pdf,
            [item],
        )

        self.assertEqual(len(repairs), 1)
        self.assertEqual(
            values(item)[0],
            "co" + LOW_LINE + "a" + LOW_LINE + "st",
        )

    def test_wrong_unicode_underline_recovered(self):
        item = question(6, [
            "na" + LOW_LINE + "ture",
            "ar" + LOW_LINE + "tist",
            "wi" + LOW_LINE + "nter",
            "pho" + LOW_LINE + "to",
        ])

        repairs = reconcile_pronunciation_choices(
            self.pdf,
            [item],
        )

        self.assertEqual(len(repairs), 1)
        self.assertEqual(
            values(item)[0],
            "nat" + LOW_LINE + "ure",
        )

    def test_unrelated_instruction_unchanged(self):
        item = question(
            5,
            ["coast", "board", "boat", "road"],
            "Choose the best response.",
        )

        before = values(item)

        self.assertEqual(
            reconcile_pronunciation_choices(
                self.pdf,
                [item],
            ),
            [],
        )
        self.assertEqual(values(item), before)

    def test_unrelated_text_unchanged(self):
        item = question(
            5,
            [
                "completelyunrelated",
                "board",
                "boat",
                "road",
            ],
        )

        before = values(item)

        self.assertEqual(
            reconcile_pronunciation_choices(
                self.pdf,
                [item],
            ),
            [],
        )
        self.assertEqual(values(item), before)

    def test_invalid_pdf_does_not_guess(self):
        item = question(
            5,
            ["co oast", "board", "boat", "road"],
        )

        before = values(item)

        for contents in (
            b"invalid",
            b"%PDF-invalid",
        ):
            self.assertEqual(
                reconcile_pronunciation_choices(
                    contents,
                    [item],
                ),
                [],
            )

        self.assertEqual(values(item), before)

    def test_idempotent(self):
        item = question(
            6,
            ["nature", "artist", "winter", "photo"],
        )

        self.assertEqual(
            len(reconcile_pronunciation_choices(
                self.pdf,
                [item],
            )),
            1,
        )

        self.assertEqual(
            reconcile_pronunciation_choices(
                self.pdf,
                [item],
            ),
            [],
        )

    def test_other_question_number_unchanged(self):
        item = question(
            7,
            ["coast", "board", "boat", "road"],
        )

        before = values(item)

        self.assertEqual(
            reconcile_pronunciation_choices(
                self.pdf,
                [item],
            ),
            [],
        )
        self.assertEqual(values(item), before)


if __name__ == "__main__":
    unittest.main()
