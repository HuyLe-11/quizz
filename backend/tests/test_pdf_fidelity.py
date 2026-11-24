"""Regression tests for OCR fidelity using original PDF geometry."""

import os
import re
import unittest
from pathlib import Path

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

from backend.engine.pdf_fidelity import (
    LOW_LINE,
    enhance_ocr_with_pdf_geometry,
)


class PDFFidelityTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        path = Path(
            os.getenv(
                "QUIZPDF_TEST_PDF",
                "test.pdf",
            )
        )

        if not path.is_file():
            raise unittest.SkipTest(
                "test.pdf not found; set QUIZPDF_TEST_PDF"
            )

        cls.pdf = path.read_bytes()

        with pymupdf.open(
            stream=cls.pdf,
            filetype="pdf",
        ) as document:
            cls.pages = [
                page.get_text(sort=True)
                for page in document
            ]

    @classmethod
    def patched(cls, page_texts=None):
        return enhance_ocr_with_pdf_geometry(
            cls.pdf,
            cls.pages if page_texts is None else page_texts,
        )

    @staticmethod
    def block(text, number):
        start = re.search(
            rf"Question {number}:.*",
            text,
        )
        assert start, f"Missing Question {number}"

        following = re.search(
            r"\nQuestion \d+:",
            text[start.end():],
        )

        end = (
            start.end() + following.start()
            if following
            else len(text)
        )

        return text[start.start():end]

    def test_pronunciation_underlines_are_exact(self):
        pages, repairs = self.patched()

        five = self.block(pages[0], 5)
        six = self.block(pages[0], 6)

        self.assertIn(
            "co" .replace("o", "o" + LOW_LINE)
            + "a" + LOW_LINE + "st",
            five,
        )
        self.assertIn(
            "nat" + LOW_LINE + "ure",
            six,
        )
        self.assertIn(
            "phot" + LOW_LINE + "o",
            six,
        )

        self.assertEqual(
            [
                (item.question_number, item.kind)
                for item in repairs
                if item.page_number == 1
            ],
            [
                (5, "pronunciation_underlines"),
                (6, "pronunciation_underlines"),
            ],
        )

    def test_existing_vocabulary_questions_unchanged(self):
        pages, _ = self.patched()

        for number in (7, 8):
            self.assertEqual(
                self.block(pages[0], number),
                self.block(self.pages[0], number),
            )

    def test_missing_option_D_restored_only_with_matching_ABC(self):
        pages = list(self.pages)
        pages[0] = pages[0].replace(
            "D. the fastest",
            "",
        )

        enhanced, repairs = self.patched(pages)

        self.assertIn(
            "D. the fastest",
            self.block(enhanced[0], 11),
        )

        self.assertIn(
            (11, "missing_option_D"),
            [
                (item.question_number, item.kind)
                for item in repairs
            ],
        )

    def test_mismatched_option_A_blocks_recovery(self):
        pages = list(self.pages)
        pages[0] = pages[0].replace(
            "D. the fastest",
            "",
        ).replace(
            "A. faster than",
            "A. NOT IN PDF",
        )

        enhanced, repairs = self.patched(pages)

        self.assertNotIn(
            "D. the fastest",
            self.block(enhanced[0], 11),
        )

        self.assertNotIn(
            11,
            [item.question_number for item in repairs],
        )

    def test_underlined_correction_choices(self):
        pages, repairs = self.patched()

        expected = {
            45: ("of", "should", "his", "assignments"),
            46: ("The", "organises", "trip", "week"),
            47: (
                "former",
                "perceptive",
                "admirable",
                "impassioned",
            ),
        }

        for number, words in expected.items():
            text = self.block(
                pages[3],
                number,
            )

            for label, word in zip("ABCD", words):
                self.assertIn(
                    f"\n{label}. {word}",
                    text,
                )

            self.assertIn(
                LOW_LINE,
                text,
            )

        self.assertEqual(
            [
                item.question_number
                for item in repairs
                if item.page_number == 4
            ],
            [45, 46, 47],
        )

    def test_enriched_text_can_be_reparsed_without_pdf(self):
        once, _ = self.patched()

        twice, _ = enhance_ocr_with_pdf_geometry(
            b"",
            once,
        )

        self.assertEqual(
            once,
            twice,
        )

    def test_scanned_pdf_does_not_invent_underlines(self):
        with pymupdf.open() as document:
            document.new_page()
            contents = document.tobytes()

        text = "Question 1: A. a B. b C. c D. d"

        result, repairs = enhance_ocr_with_pdf_geometry(
            contents,
            [text],
        )

        self.assertEqual(
            result,
            [text],
        )
        self.assertFalse(repairs)

    def test_invalid_pdf_is_ignored(self):
        result, repairs = enhance_ocr_with_pdf_geometry(
            b"invalid",
            ["hello"],
        )

        self.assertEqual(
            result,
            ["hello"],
        )
        self.assertFalse(repairs)


if __name__ == "__main__":
    unittest.main()