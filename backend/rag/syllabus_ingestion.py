"""Independent PDF-to-embedding ingestion for syllabus RAG."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .syllabus_parser import (
    ExtractedPage,
    PDFInput,
    SyllabusChunk,
    SyllabusPDFError,
    parse_syllabus_pdf,
)


EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 1536
EMBEDDING_TASK_TYPE = "RETRIEVAL_DOCUMENT"
Parser = Callable[[PDFInput], tuple[list[ExtractedPage], list[SyllabusChunk]]]


class SyllabusIngestionError(RuntimeError):
    """Base error for syllabus parsing and embedding failures."""


class SyllabusEmbeddingError(SyllabusIngestionError):
    """Raised when any chunk cannot be embedded or its vector is invalid."""

    def __init__(
        self,
        message: str,
        *,
        chunk_index: int,
        source_pages: tuple[int, ...],
    ) -> None:
        super().__init__(message)
        self.chunk_index = chunk_index
        self.source_pages = source_pages


@dataclass(frozen=True)
class EmbeddedSyllabusChunk:
    """Parser metadata plus the validated vector for one source chunk."""

    chunk_index: int
    content: str
    source_pages: tuple[int, ...]
    section_heading: str | None
    character_count: int
    token_estimate: int | None
    embedding: tuple[float, ...]


@dataclass(frozen=True)
class SyllabusIngestionResult:
    """Complete result for one document; partial results are never returned."""

    source_name: str | None
    pages: tuple[ExtractedPage, ...]
    chunks: tuple[EmbeddedSyllabusChunk, ...]
    embedding_model: str
    embedding_dimensions: int


class SyllabusIngestionService:
    """Parse one PDF and sequentially embed every chunk with Gemini.

    Parser and embedding service injection keep this layer easy to test and
    independent from Supabase. When no Gemini service is injected, it is
    created lazily with the configured timeout and closed by this service.
    """

    def __init__(
        self,
        *,
        parser: Parser = parse_syllabus_pdf,
        gemini_service=None,
        timeout: float = 30.0,
    ) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number of seconds.")
        if not callable(parser):
            raise TypeError("parser must be callable.")

        self.parser = parser
        self._gemini_service = gemini_service
        self._owns_gemini_service = gemini_service is None
        self.timeout = float(timeout)

    def _service(self):
        if self._gemini_service is None:
            # Lazy import lets callers use the PDF parser and injected test
            # doubles without importing HTTP dependencies or loading a key.
            from .gemini_service import GeminiService

            self._gemini_service = GeminiService(
                timeout=self.timeout,
                embedding_model=EMBEDDING_MODEL,
                embedding_dimensions=EMBEDDING_DIMENSIONS,
            )
        return self._gemini_service

    def close(self) -> None:
        if self._owns_gemini_service and self._gemini_service is not None:
            close = getattr(self._gemini_service, "close", None)
            if callable(close):
                close()
            self._gemini_service = None

    def __enter__(self) -> "SyllabusIngestionService":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _source_name(source: PDFInput, source_name: str | None) -> str | None:
        if source_name is not None:
            if not isinstance(source_name, str) or not source_name.strip():
                raise ValueError("source_name must be a non-empty string when provided.")
            return source_name.strip()
        if isinstance(source, (str, os.PathLike)):
            return Path(source).name
        return None

    @staticmethod
    def _validated_vector(vector, chunk: SyllabusChunk) -> tuple[float, ...]:
        if not isinstance(vector, (list, tuple)) or len(vector) != EMBEDDING_DIMENSIONS:
            actual = len(vector) if isinstance(vector, (list, tuple)) else "invalid"
            raise SyllabusEmbeddingError(
                f"Embedding for chunk {chunk.chunk_index} has {actual} dimensions; "
                f"expected {EMBEDDING_DIMENSIONS}.",
                chunk_index=chunk.chunk_index,
                source_pages=chunk.source_pages,
            )

        values: list[float] = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SyllabusEmbeddingError(
                    f"Embedding for chunk {chunk.chunk_index} contains a non-numeric value.",
                    chunk_index=chunk.chunk_index,
                    source_pages=chunk.source_pages,
                )
            try:
                number = float(value)
            except (OverflowError, ValueError):
                number = math.inf
            if not math.isfinite(number):
                raise SyllabusEmbeddingError(
                    f"Embedding for chunk {chunk.chunk_index} contains a non-finite value.",
                    chunk_index=chunk.chunk_index,
                    source_pages=chunk.source_pages,
                )
            values.append(number)
        return tuple(values)

    def ingest(
        self,
        source: PDFInput,
        *,
        source_name: str | None = None,
    ) -> SyllabusIngestionResult:
        """Ingest one PDF and fail the operation if any chunk fails."""
        resolved_name = self._source_name(source, source_name)
        try:
            pages, parsed_chunks = self.parser(source)
        except SyllabusPDFError:
            raise
        except Exception as exc:
            raise SyllabusIngestionError(
                "Could not parse the syllabus PDF. Confirm it is readable and "
                "contains extractable text."
            ) from exc

        if not parsed_chunks:
            raise SyllabusIngestionError(
                "The syllabus parser returned no chunks; no embeddings were created."
            )

        try:
            service = self._service()
        except Exception:
            raise SyllabusIngestionError(
                "Gemini embedding is not configured. Check that GEMINI_API_KEY "
                "is available to the backend process."
            ) from None
        embedded_chunks: list[EmbeddedSyllabusChunk] = []
        for chunk in parsed_chunks:
            try:
                raw_vector = service.embed_text(
                    chunk.content,
                    task_type=EMBEDDING_TASK_TYPE,
                    output_dimensionality=EMBEDDING_DIMENSIONS,
                    model=EMBEDDING_MODEL,
                )
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                status_hint = (
                    f" (HTTP {status_code})"
                    if type(status_code) is int
                    and 400 <= status_code <= 599
                    else ""
                )
                raise SyllabusEmbeddingError(
                    f"Gemini embedding failed for chunk {chunk.chunk_index}"
                    f"{status_hint}; no complete ingestion result was returned. "
                    "Retry after resolving the Gemini API or network issue.",
                    chunk_index=chunk.chunk_index,
                    source_pages=chunk.source_pages,
                ) from None

            vector = self._validated_vector(raw_vector, chunk)
            embedded_chunks.append(EmbeddedSyllabusChunk(
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                source_pages=chunk.source_pages,
                section_heading=chunk.section_heading,
                character_count=chunk.character_count,
                token_estimate=chunk.token_estimate,
                embedding=vector,
            ))

        embedding_model = getattr(service, "embedding_model", EMBEDDING_MODEL)
        if not isinstance(embedding_model, str) or not embedding_model:
            embedding_model = EMBEDDING_MODEL
        return SyllabusIngestionResult(
            source_name=resolved_name,
            pages=tuple(pages),
            chunks=tuple(embedded_chunks),
            embedding_model=embedding_model,
            embedding_dimensions=EMBEDDING_DIMENSIONS,
        )
