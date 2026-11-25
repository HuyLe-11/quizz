import unittest

import pymupdf

from backend.engine.parser_pipeline import (
    parse_pdf_questions,
)

from backend.engine.scan_pronunciation_review import (
    review_scan_pronunciation,
)


HEADING = (
    "##### QUESTION I. "
    "Choose the letter A, B, C, or D whose "
    "underlined part is pronounced differently "
    "from that of the others."
)


def pdf_bytes(native=False):

    with pymupdf.open() as document:

        page = document.new_page()

        if native:

            page.insert_text(
                (50, 50),
                "Native text",
            )

        return document.tobytes()


def question(
    values,
    instruction=HEADING,
):

    return {
        "page_number": 1,

        "section_instruction": instruction,

        "type": "mcp",

        "mcp_options": [
            {
                "label": label,

                "content": [
                    {
                        "type": "latex",
                        "value": value,
                    }
                ],

                "is_correct": False,
            }
            for label, value in zip(
                "ABCD",
                values,
            )
        ],

        "requires_source_image": False,

        "parse_fallback": False,

        "parser_warning": None,
    }


def values(q):

    return [
        option["content"][0]["value"]
        for option in q["mcp_options"]
    ]


class ScanPronunciationReviewTests(
    unittest.TestCase
):

    def test_explicit_underlines_join_word(self):

        q = question([
            r"s $ \underline{ch} $olarship",
            r"$ \underline{ch} $oose",
            r"$ \underline{ch} $aracter",
            r"s $ \underline{ch} $ool",
        ])

        review_scan_pronunciation(
            pdf_bytes(),
            [q],
        )

        self.assertEqual(
            values(q),
            [
                "sc\u0332h\u0332olarship",
                "c\u0332h\u0332oose",
                "c\u0332h\u0332aracter",
                "sc\u0332h\u0332ool",
            ],
        )

        self.assertTrue(
            all(
                option["content"][0]["type"] == "text"
                for option in q["mcp_options"]
            )
        )

        self.assertTrue(
            all(
                not option["is_correct"]
                for option in q["mcp_options"]
            )
        )

    def test_missing_and_noisy_content_preserved(self):

        raw = [
            r"park $ \underline{s} $ 2",
            "conditions",
            "destroy/ed",
            "letters",
        ]

        q = question(raw)

        review_scan_pronunciation(
            pdf_bytes(),
            [q],
        )

        self.assertEqual(
            values(q),
            raw,
        )

        self.assertTrue(
            q["requires_source_image"]
        )

        self.assertTrue(
            q["parse_fallback"]
        )

        self.assertTrue(
            q["parser_warning"]
        )

    def test_native_text_not_changed(self):

        raw = [
            r"s $ \underline{ch} $ool"
        ] * 4

        q = question(raw)

        review_scan_pronunciation(
            pdf_bytes(native=True),
            [q],
        )

        self.assertEqual(
            values(q),
            raw,
        )

        self.assertFalse(
            q["requires_source_image"]
        )

        self.assertFalse(
            q["parse_fallback"]
        )

        self.assertFalse(
            q["section_instruction"].startswith("#")
        )

    def test_other_subject_not_changed(self):

        q = question(
            [
                r"$\underline{x}$",
                "2",
                "3",
                "4",
            ],
            "##### PHẦN I. Trắc nghiệm",
        )

        before = values(q)

        review_scan_pronunciation(
            pdf_bytes(),
            [q],
        )

        self.assertEqual(
            values(q),
            before,
        )

        self.assertFalse(
            q["requires_source_image"]
        )

        self.assertEqual(
            q["section_instruction"],
            "PHẦN I. Trắc nghiệm",
        )

    def test_existing_warning_retained(self):

        q = question([
            "a",
            "b",
            "c",
            "d",
        ])

        q["parser_warning"] = (
            "Existing warning"
        )

        review_scan_pronunciation(
            pdf_bytes(),
            [q],
        )

        self.assertEqual(
            q["parser_warning"],
            "Existing warning",
        )

    def test_pipeline_attaches_section_before_review(self):

        raw = [
            HEADING
            + "\nQuestion 1. "
            "A. grade B. paper C. table D. habit\n"
            "##### QUESTION II. "
            "Choose the letter A, B, C, or D.\n"
            "Question 2. A book ___.\n"
            "A. who B. which C. whom D. whose"
        ]

        result = parse_pdf_questions(
            pdf_bytes(),
            raw,
            "demo",
        )

        first, second = result["questions"]

        self.assertEqual(
            result["raw_page_texts"],
            raw,
        )

        self.assertTrue(
            first["requires_source_image"]
        )

        self.assertFalse(
            first["question_content"][0]["value"]
            .startswith("#")
        )

        self.assertEqual(
            values(first),
            [
                "grade",
                "paper",
                "table",
                "habit",
            ],
        )

        self.assertFalse(
            second["requires_source_image"]
        )

        self.assertFalse(
            second["question_content"][0]["value"]
            .startswith("#")
        )


if __name__ == "__main__":
    unittest.main()