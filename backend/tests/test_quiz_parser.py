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

    def test_four_inline_options_are_split_without_losing_rightmost_choice(self):
        question = parse_questions_by_pages(
            ["QUESTION I\nQuestion 4: Pick one A. Alpha, B. Beta, C. Gamma, D. Delta"], "sample"
        )[0]
        self.assertEqual(question["type"], "mcp")
        self.assertEqual([item["label"] for item in question["mcp_options"]], ["A", "B", "C", "D"])
        self.assertIn("Pick one", question["question_content"][0]["value"])
        self.assertEqual(question["mcp_options"][-1]["content"][0]["value"], "Delta")

    def test_two_rows_of_horizontal_options_are_parsed(self):
        question = parse_questions_by_pages(
            ["Question 7. Pick\nA. Alpha   B) Beta\nC, Gamma   D. Delta"], "sample"
        )[0]
        self.assertEqual([item["label"] for item in question["mcp_options"]], ["A", "B", "C", "D"])

    def test_options_inline_with_stem_and_next_question_do_not_mix(self):
        questions = parse_questions_by_pages(
            ["Question 8: Stem a) detail A. one B. two C. three D. four\n"
             "Question 9: Next A. next-one B. next-two"], "sample"
        )
        self.assertEqual([question["question_id"] for question in questions], [8, 9])
        self.assertEqual([item["label"] for item in questions[0]["mcp_options"]], ["A", "B", "C", "D"])
        self.assertIn("a) detail", questions[0]["question_content"][0]["value"])
        self.assertEqual([item["label"] for item in questions[1]["mcp_options"]], ["A", "B"])
        self.assertEqual(questions[1]["mcp_options"][0]["content"][0]["value"], "next-one")

    def test_ocr_split_labels_and_content_across_blocks_keep_partial_options(self):
        question = parse_questions_by_pages(
            ["Question 3: Choose\nA. First\nB. Second\nC. Third"], "sample"
        )[0]
        self.assertEqual([item["label"] for item in question["mcp_options"]], ["A", "B", "C"])
        self.assertNotIn("D", [item["label"] for item in question["mcp_options"]])

    def test_section_titles_and_page_provenance_are_preserved(self):
        questions = parse_questions_by_pages(
            ["QUESTION I\nQuestion 12. A. one B. two", "QUESTION II\nQuestion 12. C. three D. four"], "sample"
        )
        self.assertEqual([(q["question_id"], q["page_number"]) for q in questions], [(12, 1), (12, 2)])

    def test_roman_section_titles_are_not_question_records(self):
        questions = parse_questions_by_pages(
            ["QUESTION I. Choose A-D\nQuestion 2. Next A. one\n"
             "QUESTION II. Complete each sentence\nQuestion 3. Third A. two"],
            "sample",
        )
        self.assertEqual([question["question_id"] for question in questions], [1, 2, 3])
        self.assertTrue(questions[0]["parse_fallback"])
        self.assertEqual(questions[0]["type"], "mcp")
        self.assertEqual(questions[0]["question_content"], [])
        self.assertIn("Choose A-D", questions[1]["question_content"][0]["value"])
        self.assertIn("Complete each sentence", questions[2]["question_content"][0]["value"])

    def test_reference_question_one_inline_choices_parse_as_mcp_with_section_context(self):
        questions = parse_questions_by_pages(
            ["##### QUESTION I. Choose the letter whose underlined part differs.\n"
             "Question 1. A. grade B. paper C. table D. habit\n"
             "Question 2. A. destroyed B. performed C. wanted D. combined"],
            "sample",
        )
        self.assertEqual([question["question_id"] for question in questions], [1, 2])
        self.assertEqual(questions[0]["type"], "mcp")
        self.assertEqual([option["label"] for option in questions[0]["mcp_options"]], list("ABCD"))
        self.assertEqual([option["content"][0]["value"] for option in questions[0]["mcp_options"]],
                         ["grade", "paper", "table", "habit"])
        self.assertIn("Choose the letter", questions[0]["question_content"][0]["value"])
        self.assertEqual(questions[1]["question_id"], 2)
        self.assertNotIn("combined", str(questions[0]["mcp_options"]))

    def test_inline_option_underline_markup_is_preserved(self):
        question = parse_questions_by_pages(
            [r"Question 1. A. s $ \underline{ch} $olarship B. choose"], "sample"
        )[0]
        self.assertEqual(question["type"], "mcp")
        self.assertIn(r"\underline{ch}", question["mcp_options"][0]["content"][0]["value"])

    def test_actual_reference_raw_ocr_omission_creates_blank_mcp_review_record(self):
        # PaddleOCR's saved raw markdown omits Q1 and its options entirely. Keep a
        # blank reviewable Q1 image record from the Q1->Q2 numbering gap; do not copy
        # the section instruction into it or fabricate options.
        questions = parse_questions_by_pages(
            ["##### QUESTION I. Choose the letter A, B, C, or D whose underlined part is pronounced differently.\n"
             "Question 2. A. destroyed B. performed C. wanted"], "sample"
        )
        self.assertEqual([question["question_id"] for question in questions], [1, 2])
        self.assertTrue(questions[0]["parse_fallback"])
        self.assertEqual(questions[0]["type"], "mcp")
        self.assertEqual(questions[0]["question_content"], [])
        self.assertEqual(questions[0]["mcp_options"], [])
        self.assertEqual([option["label"] for option in questions[1]["mcp_options"]], ["A", "B", "C"])
        self.assertIn("Choose the letter", questions[1]["question_content"][0]["value"])

    def test_api_sanitizes_legacy_section_stub_without_changing_response_shape(self):
        from backend.network.main import _question_response

        response = _question_response({
            "id": "legacy-q1", "question_id": 1, "source_question_number": 1,
            "has_source_pdf": True, "type": "fill_in", "multiple_choice": False,
            "question_content": [{"type": "text", "value": "QUESTION I. Choose the letter A, B, C, or D."}],
            "mcp_options": [], "fill_in_answers": [], "match_pairs": [],
            "explanation_content": [], "page_number": 1, "asset": {
                "image_path": "exam/question-1.png", "crop_status": "success",
            }, "red_mark_detection": None,
        })
        self.assertEqual(response["question_id"], 1)
        self.assertEqual(response["pdf_question_number"], 1)
        self.assertEqual(response["type"], "mcp")
        self.assertEqual(response["question"], "")
        self.assertEqual(response["options"], [])
        self.assertEqual(response["crop_status"], "success")
        self.assertEqual(response["question_image_url"], "/api/assets/exam/question-1.png")


if __name__ == "__main__":
    unittest.main()
