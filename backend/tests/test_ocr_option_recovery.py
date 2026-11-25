import unittest

from backend.engine.ocr_option_recovery import (
    recover_four_options,
)

from backend.engine.quiz_parser import (
    parse_questions,
)


def options(question):

    return {
        option["label"]: "\n".join(
            block["value"]
            for block in option["content"]
        )
        for option in question["mcp_options"]
    }


class OCROptionRecoveryTests(unittest.TestCase):

    def test_missing_b_in_pronunciation_options(self):

        text = (
            r"Question 3. A. s $ \underline{ch} $olarship "
            r"B $ \underline{ch} $oose "
            r"C. $ \underline{ch} $aracter "
            r"D. s $ \underline{ch} $ool"
        )

        question = parse_questions(
            text,
            "test",
        )[0]

        self.assertEqual(
            question["type"],
            "mcp",
        )

        self.assertEqual(
            options(question),
            {
                "A": r"s $ \underline{ch} $olarship",
                "B": r"$ \underline{ch} $oose",
                "C": r"$ \underline{ch} $aracter",
                "D": r"s $ \underline{ch} $ool",
            },
        )

        self.assertFalse(
            any(
                option["is_correct"]
                for option in question["mcp_options"]
            )
        )

    def test_comma_marker(self):

        question = parse_questions(
            "Question 12. I am interested in ___ . "
            "A, talk B. to talk "
            "C. to talking D. talking",
            "test",
        )[0]

        self.assertEqual(
            list(options(question)),
            list("ABCD"),
        )

        self.assertEqual(
            options(question)["A"],
            "talk",
        )

    def test_no_whitespace_after_period(self):

        question = parse_questions(
            "Question 13. Select a preposition.\n"
            "A.on B.in C.at D.from",
            "test",
        )[0]

        self.assertEqual(
            options(question),
            dict(
                zip(
                    "ABCD",
                    ["on", "in", "at", "from"],
                )
            ),
        )

    def test_section_boundary_and_markup(self):

        questions = parse_questions(
            "##### QUESTION I. Choose the letter A, B, C, or D.\n"
            "Question 4. A. parks B. conditions "
            "C. systems D. letters\n"
            "##### QUESTION II. Choose the letter A, B, C, or D.\n"
            "Question 5. A book ___.\n"
            "A. who B. which C. whom D. whose",
            "test",
        )

        self.assertEqual(
            len(questions),
            2,
        )

        self.assertEqual(
            options(questions[0])["D"],
            "letters",
        )

        self.assertTrue(
            questions[0]["section_instruction"]
            .startswith("QUESTION I.")
        )

        self.assertTrue(
            questions[1]["section_instruction"]
            .startswith("QUESTION II.")
        )

    def test_partial_underline_requires_source_image(self):

        question = parse_questions(
            "##### QUESTION I. Choose the letter A, B, C, or D "
            "whose underlined part is pronounced differently.\n"
            r"Question 4. A. park $ \underline{s} $ 2 "
            r"B. conditions C. system $ \underline{s} $ D. letters",
            "test",
        )[0]

        self.assertTrue(
            question["requires_source_image"]
        )

        self.assertTrue(
            question["parse_fallback"]
        )

        self.assertEqual(
            options(question)["A"],
            r"park $ \underline{s} $ 2",
        )

    def test_ambiguous_b_not_recovered(self):

        self.assertIsNone(
            recover_four_options(
                "A. vitamin B complex C. water D. salt"
            )
        )

    def test_duplicate_b_not_recovered(self):

        self.assertIsNone(
            recover_four_options(
                r"A. $\underline{a}$ "
                r"B $\underline{b}$ "
                r"B $\underline{c}$ "
                r"C. $\underline{d}$ "
                r"D. $\underline{e}$"
            )
        )

    def test_math_marker_ignored(self):

        self.assertIsNone(
            recover_four_options(
                r"A. $B. x$ C. y D. z"
            )
        )

    def test_numeric_content_preserved(self):

        question = parse_questions(
            "Question 2. Choose a value.\n"
            "A.2 B.3 C.4 D.5",
            "test",
        )[0]

        self.assertEqual(
            options(question),
            dict(zip("ABCD", "2345")),
        )

    def test_existing_two_line_options_preserved(self):

        question = parse_questions(
            "Question 2. Select.\n"
            "A. x B. y\n"
            "C. z D. w",
            "test",
        )[0]

        self.assertEqual(
            options(question),
            dict(zip("ABCD", "xyzw")),
        )


if __name__ == "__main__":
    unittest.main()