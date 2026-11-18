import unittest

from backend.engine.quiz_parser import parse_questions, parse_questions_by_pages


class QuizParserPageTests(unittest.TestCase):

    def test_question_keeps_source_page_and_options(self):
        questions = parse_questions_by_pages(
            ["Câu 1: Chọn đáp án\nA. Một\nB. Hai", "Question 2: Next\nA. Three"],
            "Đề demo",
        )

        self.assertEqual([item["page_number"] for item in questions], [1, 2])
        self.assertEqual([item["question_id"] for item in questions], [1, 2])
        self.assertEqual(questions[0]["mcp_options"][1]["label"], "B")
        self.assertTrue(questions[1]["parse_fallback"])

    def test_options_continue_across_page_boundary(self):
        questions = parse_questions_by_pages(
            [
                "Câu 1: Nội dung câu hỏi tương đối dài\nA. Đáp án thứ nhất",
                "Trang 2 / 2\nB. Đáp án thứ hai\nC. Đáp án thứ ba\n"
                "Câu 2: Câu hỏi tiếp theo\nA. Có\nB. Không",
            ],
            "demo",
        )

        self.assertEqual(len(questions), 2)
        self.assertEqual([q["page_number"] for q in questions], [1, 2])
        self.assertEqual(
            [o["label"] for o in questions[0]["mcp_options"]],
            ["A", "B", "C"],
        )

    def test_inline_options_after_question_and_on_separate_line(self):
        questions = parse_questions(
            "Câu 1: Thủ đô Việt Nam? A. Hà Nội B. Huế C. Đà Nẵng\n"
            "Câu 2: Ngôn ngữ lập trình được dùng nhiều?\n"
            "A. Python B. Java C. Kotlin",
            "demo",
        )

        self.assertEqual(len(questions), 2)
        self.assertEqual(
            questions[0]["question_content"],
            [{"type": "text", "value": "Thủ đô Việt Nam?"}],
        )
        self.assertEqual(
            [o["content"][0]["value"] for o in questions[0]["mcp_options"]],
            ["Hà Nội", "Huế", "Đà Nẵng"],
        )
        self.assertEqual(len(questions[1]["mcp_options"]), 3)

    def test_markdown_bold_and_table_options(self):
        questions = parse_questions(
            "**Câu 3.** Đây là câu hỏi kiểm tra định dạng?\n"
            "- **Ａ．** Một\n- **Ｂ．** Hai\n"
            "Câu 4: Chọn một đáp án phù hợp\n"
            "| A | Đầu tiên |\n|---|---|\n| B | Thứ hai |",
            "demo",
        )

        self.assertEqual(len(questions), 2)
        self.assertEqual(
            [o["label"] for o in questions[0]["mcp_options"]],
            ["A", "B"],
        )
        self.assertEqual(
            [o["label"] for o in questions[1]["mcp_options"]],
            ["A", "B"],
        )
        self.assertNotIn(
            "|---|---|",
            questions[1]["mcp_options"][0]["content"][0]["value"],
        )

    def test_numbered_questions_ignore_code_fence(self):
        questions = parse_questions(
            "1. Which operation comes first?\nA. Input\nB. Output\n"
            "2. Choose the correct result.\n"
            "```python\n"
            "1. list item\n"
            "A. not a choice\n"
            "```\n"
            "A. True\nB. False",
            "demo",
        )

        self.assertEqual(len(questions), 2)
        self.assertIn(
            "1. list item",
            questions[1]["question_content"][0]["value"],
        )
        self.assertEqual(len(questions[1]["mcp_options"]), 2)

    def test_option_continuations_and_latex(self):
        questions = parse_questions(
            "Câu 1: Giá trị của $x^2$ là bao nhiêu?\n"
            "A. $2$\nđơn vị mét\nB. $3$",
            "demo",
        )

        self.assertEqual(questions[0]["type"], "mcp")
        self.assertEqual(questions[0]["question_content"][0]["type"], "latex")
        self.assertEqual(
            questions[0]["mcp_options"][0]["content"][0]["value"],
            "$2$\nđơn vị mét",
        )
        self.assertTrue(
            all(not o["is_correct"] for o in questions[0]["mcp_options"])
        )

    def test_uncertain_question_marks_fallback(self):
        questions = parse_questions(
            "Câu 5: Hãy chọn đáp án đúng dựa trên hình minh họa.",
            "demo",
        )

        self.assertEqual(questions[0]["type"], "fill_in")
        self.assertTrue(questions[0]["parse_fallback"])
        self.assertEqual(questions[0]["fill_in_answers"], [])

    def test_duplicate_choice_label_does_not_break_db_uniqueness(self):
        questions = parse_questions(
            "Câu 1: Đây là câu hỏi có nhãn bị OCR lặp.\n"
            "A. Ý thứ nhất\nB. Ý thứ hai\nB. Nhãn bị lặp",
            "demo",
        )

        self.assertEqual(
            [o["label"] for o in questions[0]["mcp_options"]],
            ["A", "B"],
        )
        self.assertTrue(questions[0]["parse_fallback"])
        self.assertIn(
            "Nhãn bị lặp",
            questions[0]["mcp_options"][1]["content"][0]["value"],
        )

    def test_numbered_sublist_not_always_new_question(self):
        questions = parse_questions(
            "Câu 1: Đọc các phát biểu sau:\n"
            "1. Thứ nhất\n"
            "2. Thứ hai\n"
            "A. Chọn phương án đầu\n"
            "B. Chọn phương án sau",
            "demo",
        )

        self.assertEqual(len(questions), 1)
        self.assertIn(
            "1. Thứ nhất",
            questions[0]["question_content"][0]["value"],
        )
        self.assertIn(
            "2. Thứ hai",
            questions[0]["question_content"][0]["value"],
        )

    def test_numbered_fill_in_with_blank_line(self):
        questions = parse_questions(
            "1. Fill in the missing word ______.\n\n"
            "2. Fill in the next missing word ______.",
            "demo",
        )

        self.assertEqual(len(questions), 2)
        self.assertEqual(
            [q["type"] for q in questions],
            ["fill_in", "fill_in"],
        )

    def test_blank_pages_do_not_shift_page_number(self):
        questions = parse_questions_by_pages(
            [
                "",
                "",
                "Câu 1: Xác định kết quả của phép tính.\nA. Một\nB. Hai",
            ],
            "demo",
        )

        self.assertEqual(questions[0]["page_number"], 3)


if __name__ == "__main__":
    unittest.main()
