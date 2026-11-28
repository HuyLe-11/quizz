"""Coordinate syllabus parsing, embedding, private Storage, and persistence."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .syllabus_ingestion import SyllabusIngestionResult, SyllabusIngestionService
from .syllabus_parser import PDFInput

logger = logging.getLogger("quizpdf")


class SyllabusRAGError(RuntimeError):
    """Actionable error for syllabus RAG orchestration."""


class SyllabusRAGPersistenceError(SyllabusRAGError):
    """Persistence failed; ``prepared`` is safe to retry with the same ID."""

    def __init__(self, prepared: "PreparedSyllabus") -> None:
        super().__init__(
            "Syllabus persistence did not complete. Keep this prepared bundle "
            "and retry with the same document ID."
        )
        self.prepared = prepared


@dataclass(frozen=True)
class PreparedSyllabus:
    """A complete, retryable bundle before any Supabase write occurs."""

    document_id: str
    title: str
    subject: str | None
    source_file: str
    pdf_contents: bytes
    ingestion: SyllabusIngestionResult
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PersistedSyllabus:
    document_id: str
    source_path: str
    chunk_count: int
    ingestion: SyllabusIngestionResult


class SyllabusRAGService:
    """Prepare complete syllabus bundles and persist them through an
    authenticated ``CloudRepository`` instance.

    Split preparation from persistence so callers can retain the prepared
    bundle and retry with the same document ID after an ambiguous network
    outcome without re-running Gemini or changing the Storage path.
    """

    def __init__(self, repository, *, ingestion_service=None) -> None:
        if repository is None or not callable(getattr(repository, "save_syllabus_bundle", None)):
            raise TypeError("repository must provide save_syllabus_bundle().")
        self.repository = repository
        self.ingestion_service = ingestion_service or SyllabusIngestionService()

    @staticmethod
    def _pdf_contents(source: PDFInput) -> tuple[bytes, str | None]:
        if isinstance(source, (bytes, bytearray, memoryview)):
            return bytes(source), None
        if isinstance(source, (str, os.PathLike)):
            path = Path(source)
            try:
                return path.read_bytes(), path.name
            except OSError as exc:
                raise SyllabusRAGError(
                    "Could not read the syllabus PDF file. Check its path and permissions."
                ) from exc
        raise TypeError("source must be PDF bytes or a PDF file path.")

    def prepare(
        self,
        source: PDFInput,
        *,
        title: str,
        subject: str | None = None,
        source_file: str | None = None,
        document_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PreparedSyllabus:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string.")
        if subject is not None and not isinstance(subject, str):
            raise ValueError("subject must be a string or null.")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be an object.")
        if document_id is None:
            resolved_id = str(uuid4())
        else:
            try:
                resolved_id = str(UUID(document_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("document_id must be a UUID.") from exc

        pdf_contents, inferred_name = self._pdf_contents(source)
        if not pdf_contents:
            raise SyllabusRAGError("The syllabus PDF file is empty.")
        resolved_source = source_file or inferred_name
        if not isinstance(resolved_source, str) or not resolved_source.strip():
            resolved_source = "syllabus.pdf"
        try:
            ingestion = self.ingestion_service.ingest(
                pdf_contents,
                source_name=resolved_source,
            )
        except Exception as exc:
            # Preserve actionable parser/embedding errors without exposing
            # provider request details or credentials.
            raise SyllabusRAGError(str(exc)) from exc
        logger.info(
            "rag=syllabus stage=ingestion status=completed document_id=%s chunks=%d",
            resolved_id,
            len(ingestion.chunks),
        )
        return PreparedSyllabus(
            document_id=resolved_id,
            title=title.strip(),
            subject=subject,
            source_file=resolved_source.strip(),
            pdf_contents=pdf_contents,
            ingestion=ingestion,
            metadata=dict(metadata or {}),
        )

    def persist(
        self,
        prepared: PreparedSyllabus,
        *,
        resume_existing: bool = False,
    ) -> PersistedSyllabus:
        if not isinstance(prepared, PreparedSyllabus):
            raise TypeError("prepared must be a PreparedSyllabus result.")
        document = {
            "id": prepared.document_id,
            "title": prepared.title,
            "subject": prepared.subject,
            "source_file": prepared.source_file,
            "processing_status": "completed",
            "metadata": {
                **prepared.metadata,
                "embedding_model": prepared.ingestion.embedding_model,
                "embedding_dimensions": prepared.ingestion.embedding_dimensions,
            },
        }
        chunks = [
            {
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "source_pages": list(chunk.source_pages),
                "section_heading": chunk.section_heading,
                "character_count": chunk.character_count,
                "token_estimate": chunk.token_estimate,
                "embedding": list(chunk.embedding),
                "metadata": {},
            }
            for chunk in prepared.ingestion.chunks
        ]
        try:
            result = self.repository.save_syllabus_bundle(
                document,
                chunks,
                prepared.pdf_contents,
                resume_existing=resume_existing,
            )
        except Exception as exc:
            # No success object is returned on Storage or database failure.
            raise SyllabusRAGPersistenceError(prepared) from exc
        return PersistedSyllabus(
            document_id=result["document_id"],
            source_path=result["source_path"],
            chunk_count=result["chunk_count"],
            ingestion=prepared.ingestion,
        )

    def ingest_and_persist(
        self,
        source: PDFInput,
        *,
        title: str,
        subject: str | None = None,
        source_file: str | None = None,
        document_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        resume_existing: bool = False,
    ) -> PersistedSyllabus:
        prepared = self.prepare(
            source,
            title=title,
            subject=subject,
            source_file=source_file,
            document_id=document_id,
            metadata=metadata,
        )
        return self.persist(prepared, resume_existing=resume_existing)
