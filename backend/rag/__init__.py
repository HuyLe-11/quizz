"""Reusable AI services for retrieval-augmented generation."""

from .syllabus_parser import (
    ExtractedPage,
    SyllabusChunk,
    SyllabusPDFEncryptedError,
    SyllabusPDFError,
    SyllabusPDFNoTextError,
    chunk_pages,
    extract_pdf_pages,
    parse_syllabus_pdf,
)

_GEMINI_EXPORTS = {
    "GeminiAPIError",
    "GeminiConfigurationError",
    "GeminiService",
    "GeminiServiceError",
    "GeminiValidationError",
}


def __getattr__(name):
    """Load the Gemini client only when requested by a caller."""
    if name in _GEMINI_EXPORTS:
        from . import gemini_service

        return getattr(gemini_service, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "GeminiAPIError",
    "GeminiConfigurationError",
    "GeminiService",
    "GeminiServiceError",
    "GeminiValidationError",
    "ExtractedPage",
    "SyllabusChunk",
    "SyllabusPDFEncryptedError",
    "SyllabusPDFError",
    "SyllabusPDFNoTextError",
    "PreparedSyllabus",
    "PersistedSyllabus",
    "SyllabusRAGError",
    "SyllabusRAGPersistenceError",
    "SyllabusRAGService",
    "chunk_pages",
    "extract_pdf_pages",
    "parse_syllabus_pdf",
]

from .syllabus_rag_service import (
    PersistedSyllabus,
    PreparedSyllabus,
    SyllabusRAGError,
    SyllabusRAGPersistenceError,
    SyllabusRAGService,
)
