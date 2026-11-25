"""Offline regression tests for restoring missing OCR headings."""

import unittest

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

from backend.engine.parser_recovery import recover_pdf_headings


def pdf_bytes(*, include_second=True):
    document = pymupdf.open()
    page = document.new_page(
        width=600,
        height=800,
    )

    lines = [
        "Question 1: Sophia and Jenny are talking about solar energy.",
        "A. no B. yes C. maybe D. wrong",
    ]

    if include_second:
        lines += [
            "Question 2: Brett is talking to Julie after work.",
            "- Brett: Let's eat out tonight.",
            "- Julie: That's a great idea.",
            "A. one B. two C. three D. four",
        ]

    lines += [
        "Question 3: Select the correct answer.",
        "A. red B. blue C. black D. white",
    ]

    for index, line in enumerate(lines):
        page.insert_text(
            (30, 40 + index * 28),
            line,
            fontsize=10,
        )

    result = document.tobytes()
    document.close()

    return result


OCR_MISSING_2 = "\n".join([
    "Question 1: Sophia and Jenny are talking about solar energy.",
    "A. no B. yes C. maybe D. wrong",
    '- Brett: "Let\'s eat out tonight."',
    '- Julie: "That\'s a great idea."',
    "A. one B. two C. three D. four",
    "Question 3: Select the correct answer.",
    "A. red B. blue C. black D. white",
])


class ParserRecoveryTests(unittest.TestCase):

    def test_restores_heading_from_pdf_text_layer(self):
        augmented, records = recover_pdf_headings(
            pdf_bytes(),
            [OCR_MISSING_2],
        )

        self.assertEqual(
            [item.question_number for item in records],
            [2],
        )

        self.assertIn(
            "Question 2: Brett is talking to Julie after work.",
            augmented[0],
        )

        self.assertLess(
            augmented[0].index("Question 2:"),
            augmented[0].index("- Brett"),
        )

        self.assertEqual(
            records[0].page_number,
            1,
        )

    def test_does_not_duplicate_existing_heading(self):
        text = OCR_MISSING_2.replace(
            "- Brett:",
            "Question 2: Brett is talking to Julie after work.\n- Brett:",
        )

        augmented, records = recover_pdf_headings(
            pdf_bytes(),
            [text],
        )

        self.assertEqual(augmented, [text])
        self.assertFalse(records)

    def test_does_not_recover_heading_absent_from_pdf(self):
        augmented, records = recover_pdf_headings(
            pdf_bytes(include_second=False),
            [OCR_MISSING_2],
        )

        self.assertEqual(
            augmented,
            [OCR_MISSING_2],
        )
        self.assertFalse(records)

    def test_skips_when_ocr_anchor_is_missing(self):
        text = OCR_MISSING_2.replace(
            '- Brett: "Let\'s eat out tonight."',
            "",
        ).replace(
            '- Julie: "That\'s a great idea."',
            "",
        )

        augmented, records = recover_pdf_headings(
            pdf_bytes(),
            [text],
        )

        self.assertEqual(augmented, [text])
        self.assertFalse(records)

    def test_skips_ambiguous_anchor(self):
        text = (
            OCR_MISSING_2
            + '\n- Brett: "Let\'s eat out tonight."'
        )

        augmented, records = recover_pdf_headings(
            pdf_bytes(),
            [text],
        )

        self.assertEqual(augmented, [text])
        self.assertFalse(records)

    def test_scanned_pdf_has_no_text_layer(self):
        document = pymupdf.open()
        document.new_page()

        raw = document.tobytes()
        document.close()

        augmented, records = recover_pdf_headings(
            raw,
            [OCR_MISSING_2],
        )

        self.assertEqual(
            augmented,
            [OCR_MISSING_2],
        )
        self.assertFalse(records)

    def test_invalid_pdf_skipped(self):
        augmented, records = recover_pdf_headings(
            b"not-a-pdf",
            [OCR_MISSING_2],
        )

        self.assertEqual(
            augmented,
            [OCR_MISSING_2],
        )
        self.assertFalse(records)

    def test_empty_page_preserved(self):
        augmented, records = recover_pdf_headings(
            pdf_bytes(),
            [""],
        )

        self.assertEqual(augmented, [""])
        self.assertFalse(records)

    def test_no_recognized_neighbor_skips(self):
        text = OCR_MISSING_2.replace(
            "Question 1:",
            "Intro:",
        ).replace(
            "Question 3:",
            "Finally:",
        )

        augmented, records = recover_pdf_headings(
            pdf_bytes(),
            [text],
        )

        self.assertEqual(augmented, [text])
        self.assertFalse(records)


if __name__ == "__main__":
    unittest.main()
