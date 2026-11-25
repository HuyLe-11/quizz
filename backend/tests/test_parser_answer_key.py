"""Regression tests: answer-key pages must not contaminate final choices."""
import unittest

from backend.engine.quiz_parser import (
    parse_questions,
    parse_questions_by_pages,
)

EXAM = (
    "Question 50: It is possible that he will redecorate his house this year.\n"
    "A. He might redecorate his house this year. "
    "B. He can't redecorate his house this year.\n"
    "C. He must redecorate his house this year. "
    "D. He won't redecorate his house this year."
)

ANSWER_TABLE = (
    "<table><tr><td>1. B</td><td>2. C</td><td>3. B</td></tr>"
    "<tr><td>49. C</td><td>50. A</td></tr></table>"
)


def option_d(question):
    return "\n".join(
        item["value"]
        for option in question["mcp_options"]
        if option["label"] == "D"
        for item in option["content"]
    )


class AnswerKeyBoundaryTests(unittest.TestCase):

    def test_real_ocr_header_without_tone_mark(self):
        pages = [
            EXAM,
            (
                "BỘ GIÁO DỤC VÀ ĐÀO TẠO\n\n"
                "ĐÁP AN THAM KHẢO\n"
                "(Đáp án có 01 trang)\n"
                + ANSWER_TABLE
            ),
        ]

        questions = parse_questions_by_pages(pages, "test")

        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["question_id"], 50)
        self.assertEqual(
            option_d(questions[0]),
            "He won't redecorate his house this year.",
        )
        self.assertEqual(questions[0]["page_number"], 1)

    def test_accent_variants_and_english_headings(self):
        for title in (
            "ĐÁP ÁN THAM KHẢO",
            "ĐÁP AN THAM KHẢO",
            "DAP AN THAM KHAO",
            "BẢNG ĐÁP ÁN",
            "ANSWER KEY",
            "Key to the Answers",
            "HƯỚNG DẪN CHẤM",
            "## **ĐÁP AN THAM KHẢO**",
        ):
            with self.subTest(title=title):
                questions = parse_questions_by_pages(
                    [EXAM, title + "\n" + ANSWER_TABLE],
                    "test",
                )

                self.assertEqual(len(questions), 1)
                self.assertEqual(
                    option_d(questions[0]),
                    "He won't redecorate his house this year.",
                )

    def test_key_starts_on_same_page(self):
        page = (
            EXAM
            + "\n\nBỘ GIÁO DỤC VÀ ĐÀO TẠO\n"
            + "ĐÁP AN THAM KHẢO\n"
            + ANSWER_TABLE
        )

        questions = parse_questions(page, "test")

        self.assertEqual(len(questions), 1)
        self.assertEqual(
            option_d(questions[0]),
            "He won't redecorate his house this year.",
        )

    def test_key_on_same_page_without_institution(self):
        page = EXAM + "\n\nANSWER KEY\n" + ANSWER_TABLE
        questions = parse_questions(page, "test")

        self.assertEqual(
            option_d(questions[0]),
            "He won't redecorate his house this year.",
        )

    def test_key_with_html_header(self):
        questions = parse_questions_by_pages(
            [
                EXAM,
                "<h2>ANSWER KEY</h2>\n" + ANSWER_TABLE,
            ],
            "test",
        )

        self.assertEqual(len(questions), 1)
        self.assertEqual(
            option_d(questions[0]),
            "He won't redecorate his house this year.",
        )

    def test_no_key_heading_keeps_regular_later_page(self):
        questions = parse_questions_by_pages(
            [
                "Question 1: Test?\nA. first",
                (
                    "B. second\nC. third\nD. fourth\n"
                    "Question 2: Next?\n"
                    "A. x B. y C. z D. w"
                ),
            ],
            "test",
        )

        self.assertEqual(
            [q["question_id"] for q in questions],
            [1, 2],
        )
        self.assertEqual(
            [
                option["label"]
                for option in questions[0]["mcp_options"]
            ],
            list("ABCD"),
        )
        self.assertEqual(option_d(questions[0]), "fourth")

    def test_answer_key_phrase_inside_choice_not_a_header(self):
        questions = parse_questions(
            (
                "Question 1: What do you need?\n"
                "A. One B. Two C. Three "
                "D. The answer key is missing."
            ),
            "test",
        )

        self.assertEqual(len(questions), 1)
        self.assertEqual(
            option_d(questions[0]),
            "The answer key is missing.",
        )

    def test_no_questions_on_key_only_page(self):
        questions = parse_questions_by_pages(
            ["ĐÁP AN THAM KHẢO\n" + ANSWER_TABLE],
            "test",
        )

        self.assertEqual(questions, [])

    def test_key_page_does_not_infer_correct_answers(self):
        questions = parse_questions_by_pages(
            [EXAM, "ĐÁP AN THAM KHẢO\n50. A"],
            "test",
        )

        self.assertTrue(
            all(
                not option["is_correct"]
                for option in questions[0]["mcp_options"]
            )
        )

    def test_same_page_key_followed_by_new_page_is_ignored(self):
        questions = parse_questions_by_pages(
            [
                EXAM + "\nĐÁP ÁN THAM KHẢO\n50. A",
                (
                    "Question 51: Bogus answer sheet question?\n"
                    "A. a B. b C. c D. d"
                ),
            ],
            "test",
        )

        self.assertEqual(
            [q["question_id"] for q in questions],
            [50],
        )


if __name__ == "__main__":
    unittest.main()
