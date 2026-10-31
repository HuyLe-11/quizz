import unittest

from backend.engine.quiz_parser import parse_questions_by_pages


class QuizParserPageTests(unittest.TestCase):
    def test_question_keeps_source_page_and_options(self):
        questions = parse_questions_by_pages(
            ["Câu 1: Chọn đáp án\nA. Một\nB. Hai", "Question 2: Next\nA. Three"],
            "Đề demo",
        )
        self.assertEqual([item["page_number"] for item in questions], [1, 2])
        self.assertEqual([item["question_id"] for item in questions], [1, 2])
        self.assertEqual(questions[0]["mcp_options"][1]["label"], "B")


if __name__ == "__main__":
    unittest.main()
