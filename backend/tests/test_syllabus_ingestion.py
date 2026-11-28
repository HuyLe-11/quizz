import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from backend.rag.gemini_service import GeminiAPIError
from backend.rag.syllabus_ingestion import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    EMBEDDING_TASK_TYPE,
    SyllabusEmbeddingError,
    SyllabusIngestionError,
    SyllabusIngestionService,
)
from backend.rag.syllabus_parser import SyllabusPDFError, SyllabusPDFNoTextError


def make_pdf(page_texts):
    document = pymupdf.open()
    for text in page_texts:
        page = document.new_page()
        if text:
            page.insert_textbox(
                pymupdf.Rect(40, 40, 560, 790),
                text,
                fontsize=10,
            )
    contents = document.tobytes()
    document.close()
    return contents


def normalized_vector():
    value = 1 / math.sqrt(EMBEDDING_DIMENSIONS)
    return [value] * EMBEDDING_DIMENSIONS


class FakeGeminiService:
    embedding_model = EMBEDDING_MODEL

    def __init__(self, vector=None, error=None, fail_on_call=None):
        self.vector = normalized_vector() if vector is None else vector
        self.error = error
        self.fail_on_call = fail_on_call
        self.calls = []

    def embed_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        if self.error is not None:
            raise self.error
        if self.fail_on_call == len(self.calls):
            raise GeminiAPIError("provider detail containing secret-key")
        return self.vector


class SyllabusIngestionTests(unittest.TestCase):
    def test_end_to_end_bytes_input_embeds_chunks_sequentially(self):
        pdf_bytes = make_pdf([
            "Course overview and learning objectives.",
            "Assessment methods and weekly course schedule.",
        ])
        gemini = FakeGeminiService()
        result = SyllabusIngestionService(
            gemini_service=gemini,
        ).ingest(pdf_bytes, source_name="intro-syllabus.pdf")

        self.assertEqual(result.source_name, "intro-syllabus.pdf")
        self.assertEqual([page.page_number for page in result.pages], [1, 2])
        self.assertGreater(len(result.chunks), 0)
        self.assertEqual(len(gemini.calls), len(result.chunks))
        for _, kwargs in gemini.calls:
            self.assertEqual(kwargs["task_type"], EMBEDDING_TASK_TYPE)
            self.assertEqual(kwargs["output_dimensionality"], 1536)
            self.assertEqual(kwargs["model"], EMBEDDING_MODEL)
        for chunk in result.chunks:
            self.assertEqual(len(chunk.embedding), 1536)
            self.assertTrue(all(math.isfinite(value) for value in chunk.embedding))
            self.assertEqual(chunk.character_count, len(chunk.content))

    def test_file_path_input_preserves_page_and_source_name(self):
        pdf_bytes = make_pdf(["File path syllabus content."])
        gemini = FakeGeminiService()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "path-syllabus.pdf"
            path.write_bytes(pdf_bytes)
            result = SyllabusIngestionService(
                gemini_service=gemini,
            ).ingest(path)

        self.assertEqual(result.source_name, "path-syllabus.pdf")
        self.assertEqual(result.chunks[0].source_pages, (1,))

    def test_preserves_all_chunk_metadata_and_page_provenance(self):
        text = (
            "Section 1. Learning outcomes cover foundational algebra and geometry. "
            "Students practice equations, proofs, and problem solving. "
            "Assessment includes quizzes, projects, and a comprehensive final exam."
        )
        pdf_bytes = make_pdf([text, "Second page covers course policies and resources."])
        gemini = FakeGeminiService()

        def small_chunks(source):
            from backend.rag.syllabus_parser import parse_syllabus_pdf

            return parse_syllabus_pdf(source, max_chunk_chars=58, overlap_chars=8)

        result = SyllabusIngestionService(
            parser=small_chunks,
            gemini_service=gemini,
        ).ingest(pdf_bytes)

        self.assertGreater(len(result.chunks), 2)
        self.assertEqual(
            [chunk.chunk_index for chunk in result.chunks],
            list(range(len(result.chunks))),
        )
        self.assertTrue(any(1 in chunk.source_pages for chunk in result.chunks))
        self.assertTrue(any(2 in chunk.source_pages for chunk in result.chunks))
        for chunk in result.chunks:
            self.assertTrue(chunk.content.strip())
            self.assertGreaterEqual(chunk.character_count, 1)
            self.assertIsNotNone(chunk.token_estimate)
            self.assertEqual(chunk.character_count, len(chunk.content))
            self.assertEqual(len(chunk.embedding), EMBEDDING_DIMENSIONS)

    def test_rejects_wrong_embedding_dimension(self):
        gemini = FakeGeminiService(vector=[0.1] * (EMBEDDING_DIMENSIONS - 1))
        with self.assertRaisesRegex(SyllabusEmbeddingError, "expected 1536") as raised:
            SyllabusIngestionService(gemini_service=gemini).ingest(
                make_pdf(["A syllabus paragraph."])
            )
        self.assertEqual(raised.exception.chunk_index, 0)
        self.assertEqual(raised.exception.source_pages, (1,))

    def test_rejects_non_finite_embedding_values(self):
        vector = normalized_vector()
        vector[12] = float("inf")
        gemini = FakeGeminiService(vector=vector)
        with self.assertRaisesRegex(SyllabusEmbeddingError, "non-finite"):
            SyllabusIngestionService(gemini_service=gemini).ingest(
                make_pdf(["A syllabus paragraph."])
            )

    def test_gemini_api_error_is_actionable_and_does_not_leak_details(self):
        gemini = FakeGeminiService(
            error=GeminiAPIError("provider error with secret-key", status_code=429)
        )
        with self.assertRaises(SyllabusEmbeddingError) as raised:
            SyllabusIngestionService(gemini_service=gemini).ingest(
                make_pdf(["A syllabus paragraph."])
            )
        self.assertIn("chunk 0", str(raised.exception))
        self.assertIn("HTTP 429", str(raised.exception))
        self.assertIn("Retry", str(raised.exception))
        self.assertNotIn("secret-key", str(raised.exception))

    def test_failed_later_chunk_returns_no_partial_result(self):
        text = "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda."

        def small_chunks(source):
            from backend.rag.syllabus_parser import parse_syllabus_pdf

            return parse_syllabus_pdf(source, max_chunk_chars=24, overlap_chars=4)

        gemini = FakeGeminiService(fail_on_call=2)
        service = SyllabusIngestionService(
            parser=small_chunks,
            gemini_service=gemini,
        )
        with self.assertRaises(SyllabusEmbeddingError) as raised:
            service.ingest(make_pdf([text]))
        self.assertEqual(raised.exception.chunk_index, 1)
        self.assertEqual(len(gemini.calls), 2)

    def test_empty_or_scanned_pdf_fails_before_embedding(self):
        gemini = FakeGeminiService()
        service = SyllabusIngestionService(gemini_service=gemini)
        with self.assertRaises(SyllabusPDFError):
            service.ingest(b"")
        with self.assertRaises(SyllabusPDFNoTextError):
            service.ingest(make_pdf([""]))
        self.assertEqual(gemini.calls, [])

    def test_configurable_timeout_is_passed_to_default_gemini_service(self):
        with patch("backend.rag.gemini_service.GeminiService") as gemini_class:
            service = SyllabusIngestionService(timeout=12.5)
            service._service()
        gemini_class.assert_called_once_with(
            timeout=12.5,
            embedding_model=EMBEDDING_MODEL,
            embedding_dimensions=EMBEDDING_DIMENSIONS,
        )

    def test_invalid_timeout_is_rejected(self):
        with self.assertRaises(ValueError):
            SyllabusIngestionService(timeout=0)


if __name__ == "__main__":
    unittest.main()
