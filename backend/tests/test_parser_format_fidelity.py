import unittest

import pymupdf

from backend.engine.parser_format_fidelity import review_document_format


def pdf_bytes(native=False):

    with pymupdf.open() as doc:

        page = doc.new_page()

        if native:

            page.insert_text(
                (50, 50),
                "Native text",
            )

        return doc.tobytes()


def question(values, instruction="", stem=""):

    return {
        "page_number": 1,
        "section_instruction": instruction,
        "question_content": [
            {
                "type": "text",
                "value": stem,
            }
        ],
        "type": "mcp",
        "mcp_options": [
            {
                "label": chr(65 + i),
                "content": [
                    {
                        "type": "latex",
                        "value": value,
                    }
                ],
                "is_correct": i == 1,
            }
            for i, value in enumerate(values)
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


class ParserFormatFidelityTests(unittest.TestCase):

    def test_fragmented_word(self):

        q = question(
            [
                r"s $ \underline{ch} $olarship",
                r"$ \underline{ch} $oose",
                r"system $ \underline{s} $",
            ],
            "##### QUESTION I. Choose the underlined part.",
        )

        review_document_format(
            pdf_bytes(),
            [q],
        )

        self.assertEqual(
            values(q),
            [
                "sc\u0332h\u0332olarship",
                "c\u0332h\u0332oose",
                "system s\u0332",
            ],
        )

        self.assertEqual(
            q["section_instruction"],
            "QUESTION I. Choose the underlined part.",
        )

        self.assertEqual(
            [
                option["is_correct"]
                for option in q["mcp_options"]
            ],
            [False, True, False],
        )

        self.assertTrue(
            q["requires_source_image"]
        )


    def test_vietnamese_text(self):

        q = question(
            [
                r"văn $ \underline{hóa} $",
            ],
            stem="Xác định phần được gạch chân.",
        )

        review_document_format(
            pdf_bytes(),
            [q],
        )

        self.assertEqual(
            values(q),
            [
                "văn h\u0332ó\u0332a\u0332",
            ],
        )

        self.assertTrue(
            q["parse_fallback"]
        )


    def test_math_preserved(self):

        raw = [
            r"$\underline{x}$",
            r"$\frac{1}{2}$",
            "42",
            r"$x^2$",
        ]

        q = question(
            raw,
            "Xét biểu thức được gạch chân.",
        )

        review_document_format(
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


    def test_science_without_format_reference(self):

        raw = [
            r"$H_2O$",
            "2",
            r"$m/s^2$",
            r"$\underline{x}$",
        ]

        q = question(
            raw,
            "##### PHẦN I. Trắc nghiệm",
        )

        review_document_format(
            pdf_bytes(),
            [q],
        )

        self.assertEqual(
            values(q),
            raw,
        )

        self.assertFalse(
            q["requires_source_image"]
        )

        self.assertEqual(
            q["section_instruction"],
            "PHẦN I. Trắc nghiệm",
        )


    def test_bold_reference(self):

        q = question(
            ["A", "B"],
            stem="Giải thích cụm từ in đậm.",
        )

        review_document_format(
            pdf_bytes(),
            [q],
        )

        self.assertTrue(
            q["parse_fallback"]
        )

        self.assertEqual(
            values(q),
            ["A", "B"],
        )


    def test_missing_and_noisy_content(self):

        raw = [
            r"park $ \underline{s} $ 2",
            "grade",
            "destroy/ed",
        ]

        q = question(
            raw,
            "Choose the underlined part.",
        )

        q["parser_warning"] = "Existing warning"

        review_document_format(
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

        self.assertEqual(
            q["parser_warning"],
            "Existing warning",
        )


    def test_native_text_unchanged(self):

        raw = [
            r"s $ \underline{ch} $ool",
        ]

        q = question(
            raw,
            "##### QUESTION I. Choose the underlined part.",
        )

        review_document_format(
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
            q["section_instruction"].startswith("#")
        )


    def test_idempotent(self):

        q = question(
            [
                r"s $ \underline{ch} $ool",
            ],
            "Choose the underlined part.",
        )

        review_document_format(
            pdf_bytes(),
            [q],
        )

        import copy

        previous = copy.deepcopy(q)

        review_document_format(
            pdf_bytes(),
            [q],
        )

        self.assertEqual(
            q,
            previous,
        )


if __name__ == "__main__":
    unittest.main()