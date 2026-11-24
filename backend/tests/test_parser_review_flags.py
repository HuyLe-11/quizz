"""Tests for clearing only PDF-verified parser review warnings."""
import unittest

from backend.engine.parser_review_flags import (
    clear_verified_correction_warnings,
)

LOW_LINE = "\u0332"


def marked(word: str) -> str:
    return "".join(char + LOW_LINE for char in word)


def question(number: int, words: list[str]) -> dict:
    prompt = (
        f"Some text {marked(words[0])} "
        f"plus {marked(words[1])} "
        f"and {marked(words[2])} "
        f"end {marked(words[3])}."
    )

    return {
        "question_id": number,
        "page_number": 4,
        "type": "mcp",
        "section_instruction": (
            "Mark the letter A, B, C, or D to indicate "
            "the underlined part that needs correction."
        ),
        "question_content": [
            {"type": "text", "value": prompt}
        ],
        "mcp_options": [
            {
                "label": label,
                "content": [
                    {"type": "text", "value": word}
                ],
            }
            for label, word in zip("ABCD", words)
        ],
        "parse_fallback": True,
        "requires_source_image": True,
        "parser_warning": None,
    }


def repair(number: int, source="pdf_text_geometry") -> dict:
    return {
        "page_number": 4,
        "question_number": number,
        "kind": "underlined_error_markers",
        "source": source,
    }


class ReviewFlagTests(unittest.TestCase):

    def setUp(self):
        self.questions = [
            question(45, ["of", "should", "his", "assignments"]),
            question(46, ["The", "organises", "trip", "week"]),
            question(47, [
                "former",
                "perceptive",
                "admirable",
                "impassioned",
            ]),
        ]

        self.repairs = [
            repair(number)
            for number in (45, 46, 47)
        ]

    def test_all_three_clear(self):
        result = clear_verified_correction_warnings(
            self.questions,
            self.repairs,
        )

        self.assertEqual(result, [45, 46, 47])

        for item in self.questions:
            self.assertFalse(item["parse_fallback"])
            self.assertFalse(item["requires_source_image"])

    def test_no_pdf_evidence_keeps_warning(self):
        self.assertEqual(
            clear_verified_correction_warnings(
                self.questions, []
            ),
            [],
        )
        self.assertTrue(self.questions[0]["parse_fallback"])

    def test_unverified_source_keeps_warning(self):
        self.assertEqual(
            clear_verified_correction_warnings(
                self.questions,
                [repair(45, source="ocr_only")],
            ),
            [],
        )

    def test_incomplete_options_keeps_warning(self):
        self.questions[0]["mcp_options"].pop()

        self.assertEqual(
            clear_verified_correction_warnings(
                self.questions[:1], self.repairs
            ),
            [],
        )

    def test_mismatched_underline_keeps_warning(self):
        self.questions[0]["mcp_options"][0]["content"][0][
            "value"
        ] = "with"

        self.assertEqual(
            clear_verified_correction_warnings(
                self.questions[:1], self.repairs
            ),
            [],
        )

    def test_existing_parser_warning_is_preserved(self):
        self.questions[0]["parser_warning"] = "Missing heading"

        self.assertEqual(
            clear_verified_correction_warnings(
                self.questions[:1], self.repairs
            ),
            [],
        )

    def test_other_question_type_unchanged(self):
        self.questions[0]["type"] = "fill_in"

        self.assertEqual(
            clear_verified_correction_warnings(
                self.questions[:1], self.repairs
            ),
            [],
        )

    def test_other_section_unchanged(self):
        self.questions[0][
            "section_instruction"
        ] = "Choose the best response."

        self.assertEqual(
            clear_verified_correction_warnings(
                self.questions[:1], self.repairs
            ),
            [],
        )

    def test_only_verified_questions_are_cleared(self):
        result = clear_verified_correction_warnings(
            self.questions,
            [self.repairs[0]],
        )

        self.assertEqual(result, [45])
        self.assertTrue(self.questions[1]["parse_fallback"])


if __name__ == "__main__":
    unittest.main()
