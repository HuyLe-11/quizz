import tempfile
import unittest
from pathlib import Path

import fitz

from backend.rag.syllabus_parser import (
    ExtractedPage,
    SyllabusPDFEncryptedError,
    SyllabusPDFError,
    SyllabusPDFNoTextError,
    chunk_pages,
    extract_pdf_pages,
    parse_syllabus_pdf,
)


def make_pdf(page_texts):
    document = fitz.open()
    for text in page_texts:
        page = document.new_page()
        if text:
            page.insert_textbox(
                fitz.Rect(50, 50, 550, 780),
                text,
                fontsize=11,
            )
    payload = document.tobytes()
    document.close()
    return payload


class SyllabusParserTests(unittest.TestCase):
    def test_extracts_multiple_pages_from_bytes_and_preserves_empty_page(self):
        pdf_bytes = make_pdf([
            "Course learning outcomes\n\nStudents understand core concepts.",
            "",
            "Chapter 2\n\nCourse material and final assessment.",
        ])
        pages = extract_pdf_pages(pdf_bytes)
        self.assertEqual([page.page_number for page in pages], [1, 2, 3])
        self.assertIn("Course learning outcomes", pages[0].text)
        self.assertEqual(pages[1].text, "")
        self.assertIn("Chapter 2", pages[2].text)

    def test_extracts_pdf_path(self):
        pdf_bytes = make_pdf(["Syllabus file input."])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "syllabus.pdf"
            path.write_bytes(pdf_bytes)
            pages = extract_pdf_pages(path)
        self.assertEqual(len(pages), 1)
        self.assertIn("Syllabus file input", pages[0].text)

    def test_chunks_keep_page_provenance_and_local_stable_indexes(self):
        pages = [
            ExtractedPage(1, "First page content is about algebra foundations."),
            ExtractedPage(2, "Second page content is about geometry foundations."),
        ]
        chunks = chunk_pages(pages, max_chunk_chars=38, overlap_chars=5)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual([chunk.chunk_index for chunk in chunks], list(range(len(chunks))))
        self.assertIn(1, chunks[0].source_pages)
        self.assertIn(2, tuple(page for chunk in chunks for page in chunk.source_pages))
        self.assertEqual(
            chunks,
            chunk_pages(pages, max_chunk_chars=38, overlap_chars=5),
        )

    def test_chunk_overlap_repeats_context_at_boundary(self):
        text = "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda."
        chunks = chunk_pages(
            [ExtractedPage(1, text)],
            max_chunk_chars=32,
            overlap_chars=10,
        )
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(
            any(
                max(
                    (length for length in range(1, min(len(left.content), len(right.content)) + 1)
                     if left.content.endswith(right.content[:length])),
                    default=0,
                ) >= 5
                for left, right in zip(chunks, chunks[1:])
            )
        )

    def test_unicode_vietnamese_survives_chunking(self):
        text = "Chương trình đào tạo gồm lý thuyết, thực hành và đánh giá cuối kỳ."
        chunks = chunk_pages(
            [ExtractedPage(1, text)], max_chunk_chars=90, overlap_chars=10
        )
        self.assertIn("đánh giá", " ".join(chunk.content for chunk in chunks))

    def test_empty_pages_are_kept_when_document_has_text(self):
        pages = extract_pdf_pages(make_pdf(["Có nội dung.", "", "Trang cuối."]))
        self.assertEqual(len(pages), 3)
        self.assertEqual(pages[1].text, "")

    def test_rejects_invalid_pdf_bytes(self):
        with self.assertRaises(SyllabusPDFError):
            extract_pdf_pages(b"not a pdf")

    def test_rejects_encrypted_pdf_with_actionable_error(self):
        document = fitz.open()
        document.new_page().insert_text((50, 50), "Protected syllabus")
        encrypted = document.tobytes(
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="owner-secret",
            user_pw="user-secret",
        )
        document.close()
        with self.assertRaises(SyllabusPDFEncryptedError):
            extract_pdf_pages(encrypted)

    def test_rejects_pdf_without_extractable_text_and_suggests_ocr(self):
        with self.assertRaisesRegex(SyllabusPDFNoTextError, "OCR"):
            extract_pdf_pages(make_pdf(["", "\n  "]))

    def test_all_source_words_are_represented_in_output_chunks(self):
        source = (
            "Learning outcomes include critical thinking and communication.\n\n"
            "Assessment includes weekly exercises, projects, and a final examination."
        )
        _, chunks = parse_syllabus_pdf(
            make_pdf([source]), max_chunk_chars=42, overlap_chars=8
        )
        output = " ".join(chunk.content for chunk in chunks).casefold()
        for word in source.replace(".", "").replace(",", "").split():
            self.assertIn(word.casefold(), output)

    def test_separate_calls_have_independent_chunk_indexes_and_content(self):
        _, first = parse_syllabus_pdf(make_pdf(["First document only."]))
        _, second = parse_syllabus_pdf(make_pdf(["Second document only."]))
        self.assertEqual(first[0].chunk_index, 0)
        self.assertEqual(second[0].chunk_index, 0)
        self.assertNotIn("Second", first[0].content)
        self.assertNotIn("First", second[0].content)


if __name__ == "__main__":
    unittest.main()
