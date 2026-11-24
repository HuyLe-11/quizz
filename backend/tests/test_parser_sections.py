"""Pure parser tests. No Supabase, OCR calls or SQLite required."""

import unittest

from backend.engine.quiz_parser import (
    parse_questions,
    parse_questions_by_pages,
)


def options(question):
    return {
        choice["label"]: " ".join(
            block["value"]
            for block in choice["content"]
        )
        for choice in question["mcp_options"]
    }


class ParserSectionsTests(unittest.TestCase):

    def test_section_heading_not_appended_to_previous_d(self):
        text = """
Question 1: Some question?
A. one B. two C. three D. four
**Mark the letter A, B, C or D to indicate the position of stress in each of the following questions.**
Question 2: A. woman B. teacher C. idea D. orange
"""
        questions = parse_questions(text, "test")

        self.assertEqual(len(questions), 2)
        self.assertEqual(options(questions[0])["D"], "four")
        self.assertIn(
            "position of stress",
            questions[1]["section_instruction"],
        )
        self.assertEqual(
            options(questions[1])["D"],
            "orange",
        )

    def test_directive_merged_into_same_ocr_line_is_split(self):
        text = """
Question 1: Select.
A. one B. two C. three D. four **Mark the letter A, B, C, or D on your sheet to choose the correct answer.**
Question 2: A. cat B. dog C. bird D. duck
"""
        questions = parse_questions(text, "demo")

        self.assertEqual(
            options(questions[0])["D"],
            "four",
        )
        self.assertIn(
            "Mark the letter",
            questions[1]["section_instruction"],
        )

    def test_split_inline_options_over_two_lines(self):
        text = """
Question 1: Example?
A. one B. two
C. three D. four
"""
        question = parse_questions(text, "demo")[0]

        self.assertEqual(
            list(options(question)),
            list("ABCD"),
        )
        self.assertEqual(
            options(question)["D"],
            "four",
        )

    def test_reading_passage_retained_separately(self):
        text = """
Question 1: Before passage?
A. a B. b C. c D. d
Read the following passage and mark the letter A, B, C, or D to indicate the correct answers.
One day, Alice visited the river.
The next day, she returned.
Question 2: Why did Alice visit?
A. a B. b C. c D. d
Question 3: What happened next?
A. a B. b C. c D. d
"""
        questions = parse_questions(text, "demo")

        self.assertEqual(
            options(questions[0])["D"],
            "d",
        )

        for question in questions[1:]:
            self.assertIn(
                "Alice visited",
                question["shared_passage"],
            )
            self.assertNotIn(
                "Alice visited",
                options(question)["D"],
            )

    def test_answer_key_page_not_added_to_last_answer(self):
        pages = [
            "Question 1: Example?\n"
            "A. x B. y C. z D. last",
            "ĐÁP ÁN THAM KHẢO\n"
            "1. B     2. C",
        ]

        questions = parse_questions_by_pages(
            pages,
            "demo",
        )

        self.assertEqual(len(questions), 1)
        self.assertEqual(
            options(questions[0])["D"],
            "last",
        )

    def test_cross_page_last_three_options(self):
        pages = [
            "Question 31: Find the answer?\n"
            "A. first",
            "Trang 1/2 - Mã đề thi 401\n"
            "B. second\n"
            "C. third\n"
            "D. fourth",
        ]

        questions = parse_questions_by_pages(
            pages,
            "demo",
        )

        self.assertEqual(len(questions), 1)
        self.assertEqual(
            questions[0]["page_number"],
            1,
        )
        self.assertEqual(
            list(options(questions[0])),
            list("ABCD"),
        )

    def test_underlined_question_needs_image_and_markers(self):
        text = """
Mark the letter A, B, C, or D on your answer sheet to indicate the underlined part that needs correction in each of the following questions.
Question 45: This is an example mistake.
A     B     C     D
"""
        question = parse_questions(
            text,
            "demo",
        )[0]

        self.assertEqual(
            list(options(question)),
            list("ABCD"),
        )
        self.assertTrue(
            question["parse_fallback"]
        )
        self.assertTrue(
            question["requires_source_image"]
        )

    def test_missing_section_not_invented(self):
        text = (
            "Question 1: Normal example?\n"
            "A. one B. two C. three D. four"
        )
        question = parse_questions(
            text,
            "demo",
        )[0]

        self.assertEqual(
            question["section_instruction"],
            "",
        )
        self.assertEqual(
            options(question)["D"],
            "four",
        )

    def test_missing_question_heading_returns_reviewable_draft(self):
        text = (
            "A long unidentified exam question "
            "that needs manual review.\n"
            "A. one B. two C. three D. four"
        )

        questions = parse_questions(
            text,
            "demo",
        )

        self.assertEqual(
            len(questions),
            1,
        )
        self.assertTrue(
            questions[0]["parse_fallback"]
        )

    def test_fenced_code_does_not_create_false_question(self):
        text = (
            "Question 1: What prints?\n"
            "```\n"
            "Question 2: fake code\n"
            "```\n"
            "A. one B. two C. three D. four"
        )

        self.assertEqual(
            len(parse_questions(text, "demo")),
            1,
        )


if __name__ == "__main__":
    unittest.main()
