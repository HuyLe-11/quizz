"""Structural review regressions without live OCR or database writes."""
import copy
import importlib
import os
import unittest

from unittest.mock import patch

import pymupdf

from fastapi.testclient import TestClient

from backend.engine.parser_pipeline import (
    parse_pdf_questions,
)

from backend.engine.parser_structure_review import (
    review_question_structure,
)


def pdf_bytes(count=1):
    with pymupdf.open() as document:
        for _ in range(count):
            document.new_page()

        return document.tobytes()


def question(
    labels="AB",
    instruction="",
    number=1,
    page=1,
):
    return {
        "question_id": number,
        "page_number": page,
        "type": "mcp",
        "section_instruction": instruction,

        "question_content": [
            {
                "type": "text",
                "value": "Choose one.",
            }
        ],

        "mcp_options": [
            {
                "label": label,
                "content": [
                    {
                        "type": "text",
                        "value": label,
                    }
                ],
                "is_correct": False,
            }

            for label in labels
        ],

        "fill_in_answers": [],
        "parse_fallback": False,
        "requires_source_image": False,
        "parser_warning": None,
    }


class StructureReviewTests(unittest.TestCase):

    def test_vietnamese_missing_d_is_flagged(self):
        raw = [
            "Chọn đáp án A, B, C hoặc D.\n"
            "Câu 1: Phát biểu nào đúng?\n"
            "A. One\n"
            "B. Two\n"
            "C. Three"
        ]

        result = parse_pdf_questions(
            pdf_bytes(),
            raw,
            "demo",
        )

        q = result["questions"][0]

        self.assertTrue(q["parse_fallback"])

        self.assertTrue(
            q["requires_source_image"]
        )

        self.assertEqual(
            [
                o["label"]
                for o in q["mcp_options"]
            ],
            list("ABC"),
        )

        self.assertTrue(
            all(
                not o["is_correct"]
                for o in q["mcp_options"]
            )
        )

        self.assertEqual(
            result["raw_page_texts"],
            raw,
        )

        self.assertEqual(
            result["diagnostics"]
            ["review_items"][0]
            ["issue_codes"],

            ["expected_four_choices"],
        )

    def test_english_missing_d_requires_source(self):
        for instruction in (
            "Mark the letter A, B, C, or D.",
            "Mark the letter A,B,C,D.",
            "Chọn đáp án A, B, C hoac D.",
        ):
            with self.subTest(
                instruction=instruction
            ):
                q = question(
                    "ABC",
                    instruction,
                )

                review_question_structure([q])

                self.assertTrue(
                    q["requires_source_image"]
                )

                self.assertTrue(
                    q["parser_warning"]
                )

    def test_two_choices_without_four_choice_instruction_are_valid(self):
        q = question()

        before = copy.deepcopy(q)

        report = review_question_structure([q])

        self.assertEqual(q, before)

        self.assertEqual(
            report["review_items"],
            [],
        )

    def test_complete_four_choices_are_valid(self):
        q = question(
            "ABCD",
            "Chọn đáp án A, B, C hoặc D.",
        )

        self.assertEqual(
            review_question_structure([q])
            ["review_items"],
            [],
        )

    def test_five_choices_are_not_mistaken_for_four(self):
        for instruction in (
            "Choose A, B, C, D, or E.",
            "Choose A,B,C,D,E.",
        ):
            with self.subTest(
                instruction=instruction
            ):
                q = question(
                    "ABCDE",
                    instruction,
                )

                self.assertEqual(
                    review_question_structure([q])
                    ["review_items"],
                    [],
                )

    def test_nonsequential_and_duplicate_labels(self):
        for labels in (
            "ACD",
            "ABB",
            "BAC",
        ):
            with self.subTest(labels=labels):
                q = question(labels)

                report = review_question_structure(
                    [q]
                )

                self.assertIn(
                    "choice_labels_invalid",
                    report["review_items"][0]
                    ["issue_codes"],
                )

                self.assertTrue(
                    q["requires_source_image"]
                )

    def test_empty_choice_and_one_choice(self):
        q = question("A")

        q["mcp_options"][0]["content"] = [
            {
                "type": "text",
                "value": "  ",
            }
        ]

        issues = review_question_structure([q])[
            "review_items"
        ][0]["issue_codes"]

        self.assertEqual(
            issues,
            [
                "choice_count_too_low",
                "choice_content_empty",
            ],
        )

    def test_all_choices_missing_does_not_invent_type_or_answers(self):
        q = question(
            "",
            "Mark the letter A, B, C, or D.",
        )

        q["type"] = "fill_in"

        report = review_question_structure([q])

        self.assertEqual(
            q["type"],
            "fill_in",
        )

        self.assertEqual(
            q["mcp_options"],
            [],
        )

        self.assertEqual(
            q["fill_in_answers"],
            [],
        )

        self.assertEqual(
            report["review_items"][0]
            ["issue_codes"],
            ["expected_four_choices"],
        )

    def test_existing_warning_and_content_preserved(self):
        q = question("AC")

        q["parser_warning"] = "OCR warning"

        q["mcp_options"][0]["is_correct"] = True

        before = copy.deepcopy(q)

        review_question_structure([q])

        self.assertEqual(
            q["parser_warning"],
            before["parser_warning"],
        )

        self.assertEqual(
            q["question_content"],
            before["question_content"],
        )

        self.assertEqual(
            q["mcp_options"],
            before["mcp_options"],
        )

    def test_existing_flag_included_without_structural_issue(self):
        q = question("ABCD")

        q["parse_fallback"] = True

        report = review_question_structure([q])

        self.assertEqual(
            report["review_items"][0]
            ["issue_codes"],

            ["existing_review_flag"],
        )

        self.assertEqual(
            report["review_summary"]
            ["structural_issues"],
            0,
        )

    def test_repeated_question_numbers_have_distinct_review_items(self):
        report = review_question_structure([
            question(
                "AC",
                number=1,
                page=1,
            ),

            question(
                "AC",
                number=1,
                page=2,
            ),
        ])

        self.assertEqual(
            [
                (
                    r["question_index"],
                    r["question_number"],
                    r["page_number"],
                )

                for r in report["review_items"]
            ],
            [
                (1, 1, 1),
                (2, 1, 2),
            ],
        )

    def test_preview_and_ingestion_return_same_review(self):
        raw = [
            "Mark the letter A, B, C, or D.\n"
            "Question 1: Choose one.\n"
            "A. One\n"
            "B. Two"
        ]

        pdf = pdf_bytes()

        preview = parse_pdf_questions(
            pdf,
            raw,
            "demo",
            embed_context=False,
        )

        ingestion = parse_pdf_questions(
            pdf,
            raw,
            "demo",
            embed_context=True,
        )

        self.assertEqual(
            preview["diagnostics"]["review_items"],
            ingestion["diagnostics"]["review_items"],
        )

    def test_text_only_preview_uses_shared_review(self):
        with patch.dict(
            os.environ,
            {"QUIZPDF_PARSER_PREVIEW": "1"},
        ):
            module = importlib.import_module(
                "backend.parser_preview_app"
            )

            with TestClient(module.app) as client:
                response = client.post(
                    "/api/parser-preview/reparse",
                    json={
                        "filename": "demo.pdf",

                        "page_texts": [
                            "Chọn đáp án A, B, C hoặc D.\n"
                            "Câu 1: Which one?\n"
                            "A. One\n"
                            "B. Two\n"
                            "C. Three"
                        ],
                    },
                )

        self.assertEqual(
            response.status_code,
            200,
            response.text,
        )

        data = response.json()["data"]

        self.assertEqual(
            data["diagnostics"]
            ["incomplete_options"],
            [1],
        )

        self.assertEqual(
            data["diagnostics"]
            ["needs_review"],
            [1],
        )

        self.assertEqual(
            data["diagnostics"]
            ["review_items"][0]
            ["issue_codes"],

            ["expected_four_choices"],
        )

        self.assertTrue(
            data["questions"][0]
            ["requires_source_image"]
        )


if __name__ == "__main__":
    unittest.main()