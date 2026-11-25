"""Regression tests for missing question headings in OCR output."""

import unittest

from backend.engine.quiz_parser import parse_questions_by_pages


def options(question):
    return {
        option["label"]: " ".join(
            block["value"]
            for block in option["content"]
        )
        for option in question["mcp_options"]
    }


class MissingHeadingTests(unittest.TestCase):

    def test_screenshot_case(self):
        page = """
## Mark the letter A, B, C or D on your answer sheet to indicate the sentence that best completes each of the following exchanges.
Question 1: Sophia and Jenny are talking about solar energy.
- Sophia: "I think we should use solar energy."
- Jenny: "______. It's clean and renewable."
A. I don't think so B. I agree with you C. Of course not D. You're wrong
- Brett: "Let's eat out tonight."
- Julie: "______."
A. Yes, it's true B. Yes, I do C. That's a great idea D. Here you are
## Mark the letter A, B, C or D to indicate the word that differs from the other three in the position of stress.
Question 3: A. different B. creative C. possible D. national
"""
        questions = parse_questions_by_pages(
            [page],
            "test",
        )

        self.assertEqual(
            [q["question_id"] for q in questions],
            [1, 2, 3],
        )

        self.assertEqual(
            options(questions[0])["D"],
            "You're wrong",
        )
        self.assertEqual(
            options(questions[1])["D"],
            "Here you are",
        )
        self.assertIn(
            "Brett",
            questions[1]["question_content"][0]["value"],
        )

        self.assertFalse(
            questions[0]["parse_fallback"]
        )
        self.assertTrue(
            questions[1]["parse_fallback"]
        )
        self.assertTrue(
            questions[1]["requires_source_image"]
        )
        self.assertIsNotNone(
            questions[1]["parser_warning"]
        )

    def test_options_across_four_lines(self):
        page = """
Question 1: Sample question with four options.
A. 1
B. 2
C. 3
D. 4
- Speaker: "New prompt for next question"
- Another: "Answer?"
A. x
B. y
C. z
D. w
Question 3: Third question?
A. a B. b C. c D. d
"""
        questions = parse_questions_by_pages(
            [page],
            "test",
        )

        self.assertEqual(
            [q["question_id"] for q in questions],
            [1, 2, 3],
        )
        self.assertEqual(
            options(questions[0])["D"],
            "4",
        )
        self.assertEqual(
            options(questions[1])["D"],
            "w",
        )

    def test_multiline_d_without_new_options(self):
        page = """
Question 1: Explain the answer.
A. 1 B. 2 C. 3 D. Introduction
- The second explanation line
- The third explanation line
Question 2: Next question.
A. yes B. no C. maybe D. none
"""
        questions = parse_questions_by_pages(
            [page],
            "test",
        )

        self.assertEqual(len(questions), 2)
        self.assertIn(
            "second explanation",
            options(questions[0])["D"],
        )
        self.assertEqual(
            options(questions[1])["D"],
            "none",
        )

    def test_explicit_heading_avoids_duplicate(self):
        page = """
Question 1: Explain the answer.
A. one B. two C. three D. Four
- Follow up explaining choices.
A. beta B. gamma C. delta D. epsilon
Question 2: New
A. a B. b C. c D. d
"""
        questions = parse_questions_by_pages(
            [page],
            "test",
        )

        self.assertEqual(
            [q["question_id"] for q in questions],
            [1, 2],
        )
        self.assertIn(
            "Follow up",
            options(questions[0])["D"],
        )


if __name__ == "__main__":
    unittest.main()
