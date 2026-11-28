"""Text extraction and retrieval chunking for syllabus PDFs.

This module reads PDF text layers only. It deliberately does not call OCR,
Gemini, Supabase, or any application database.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

try:
    import pymupdf
except ImportError:  # Match the project's existing PyMuPDF compatibility pattern.
    import fitz as pymupdf


PDFInput: TypeAlias = bytes | bytearray | memoryview | str | os.PathLike[str]

_HORIZONTAL_SPACE = re.compile(r"[^\S\r\n]+")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n+")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+")
_HEADING = re.compile(
    r"^(?:#{1,6}\s+.+|"
    r"(?:chapter|unit|section|module|part|chương|phần|mục)\s+"
    r"[\w.-]+(?:\s*[:.-]\s*.+)?|"
    r"\d+(?:\.\d+)*[.)]?\s+[A-ZÀ-Ỵ].{0,100})$",
    re.IGNORECASE,
)


class SyllabusPDFError(ValueError):
    """Base class for invalid, inaccessible, or unusable syllabus PDFs."""


class SyllabusPDFEncryptedError(SyllabusPDFError):
    """Raised when a PDF requires a password."""


class SyllabusPDFNoTextError(SyllabusPDFError):
    """Raised when no page in a PDF has extractable text."""


@dataclass(frozen=True)
class ExtractedPage:
    """Normalized text extracted from one source PDF page."""

    page_number: int
    text: str


@dataclass(frozen=True)
class SyllabusChunk:
    """A retrieval chunk with a stable, document-local index and provenance."""

    chunk_index: int
    content: str
    source_pages: tuple[int, ...]
    section_heading: str | None = None
    character_count: int = 0
    token_estimate: int | None = None


@dataclass(frozen=True)
class _Piece:
    text: str
    page_number: int
    heading: str | None
    separator: str


def normalize_page_text(text: str) -> str:
    """Normalize spacing while preserving single and blank line boundaries."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    lines = [_HORIZONTAL_SPACE.sub(" ", line).strip() for line in text.split("\n")]
    normalized = "\n".join(lines)
    normalized = _PARAGRAPH_BREAK.sub("\n\n", normalized)
    return normalized.strip()


def extract_pdf_pages(source: PDFInput) -> list[ExtractedPage]:
    """Extract text page by page from PDF bytes or a filesystem path.

    Empty pages are retained with an empty string. This method only reads the
    PDF text layer; if the entire document is scanned, it raises an actionable
    error directing callers to OCR it before ingestion.
    """
    if isinstance(source, (bytes, bytearray, memoryview)):
        pdf_bytes = bytes(source)
        if not pdf_bytes:
            raise SyllabusPDFError("The supplied PDF data is empty.")
        open_kwargs = {"stream": pdf_bytes, "filetype": "pdf"}
    elif isinstance(source, (str, os.PathLike)):
        path = Path(source)
        if not path.is_file():
            raise SyllabusPDFError(f"PDF file does not exist: {path}")
        open_kwargs = {"filename": str(path)}
    else:
        raise TypeError("source must be PDF bytes or a PDF file path.")

    try:
        document = pymupdf.open(**open_kwargs)
    except Exception as exc:
        raise SyllabusPDFError(
            "Could not open the supplied PDF. Check that it is a valid, readable PDF."
        ) from exc

    try:
        if document.needs_pass or document.is_encrypted:
            raise SyllabusPDFEncryptedError(
                "This PDF is password-protected. Provide an unlocked PDF before parsing."
            )

        pages = [
            ExtractedPage(
                page_number=page_index + 1,
                text=normalize_page_text(page.get_text("text", sort=True)),
            )
            for page_index, page in enumerate(document)
        ]
    except SyllabusPDFError:
        raise
    except Exception as exc:
        raise SyllabusPDFError(
            "Could not extract text from the PDF. Check that the file is readable."
        ) from exc
    finally:
        document.close()

    if not pages or not any(page.text.strip() for page in pages):
        raise SyllabusPDFNoTextError(
            "No extractable text was found in this PDF. It may be scanned; "
            "OCR it before syllabus ingestion. This parser does not run OCR."
        )
    return pages


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """Split a long paragraph at sentence boundaries, then word boundaries."""
    sentences = _SENTENCE_BREAK.split(text)
    result: list[str] = []
    for sentence in sentences:
        if len(sentence) <= max_chars:
            result.append(sentence)
            continue
        words = sentence.split()
        buffer = ""
        for word in words:
            if len(word) > max_chars:
                if buffer:
                    result.append(buffer)
                    buffer = ""
                # Only split a token when no natural word boundary exists and
                # the configured chunk limit would otherwise be exceeded.
                result.extend(
                    word[index:index + max_chars]
                    for index in range(0, len(word), max_chars)
                )
            elif not buffer:
                buffer = word
            elif len(buffer) + 1 + len(word) <= max_chars:
                buffer += " " + word
            else:
                result.append(buffer)
                buffer = word
        if buffer:
            result.append(buffer)
    return result


def _page_pieces(
    page: ExtractedPage,
    max_chars: int,
    overlap_chars: int,
    heading: str | None,
) -> tuple[list[_Piece], str | None]:
    pieces: list[_Piece] = []
    paragraphs = _PARAGRAPH_BREAK.split(page.text)
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        # PDF text extraction often emits one line per visual row. Join those
        # rows into a paragraph while retaining actual blank-line boundaries.
        paragraph = " ".join(lines)
        heading_candidate = paragraph.lstrip("# ").strip()
        if len(heading_candidate) <= 120 and _HEADING.match(paragraph):
            heading = heading_candidate
        fragment_limit = (
            max(1, max_chars - overlap_chars - 1)
            if overlap_chars
            else max_chars
        )
        fragments = (
            [paragraph]
            if len(paragraph) <= max_chars
            else _split_long_text(paragraph, fragment_limit)
        )
        for fragment_index, fragment in enumerate(fragments):
            pieces.append(_Piece(
                text=fragment,
                page_number=page.page_number,
                heading=heading,
                separator="\n\n" if fragment_index == 0 else " ",
            ))
    return pieces, heading


def _render(pieces: list[_Piece]) -> str:
    if not pieces:
        return ""
    output = pieces[0].text
    for piece in pieces[1:]:
        output += piece.separator + piece.text
    return output


def _overlap_pieces(pieces: list[_Piece], overlap_chars: int) -> list[_Piece]:
    if overlap_chars <= 0:
        return []
    suffix: list[_Piece] = []
    used = 0
    for piece in reversed(pieces):
        take = min(len(piece.text), overlap_chars - used)
        if take <= 0:
            break
        suffix.insert(0, _Piece(
            text=piece.text[-take:],
            page_number=piece.page_number,
            heading=piece.heading,
            separator=piece.separator,
        ))
        used += take
        if used >= overlap_chars:
            break
    if suffix:
        suffix[0] = _Piece(
            text=suffix[0].text.lstrip(),
            page_number=suffix[0].page_number,
            heading=suffix[0].heading,
            separator="",
        )
        suffix = [piece for piece in suffix if piece.text]
    return suffix


def chunk_pages(
    pages: list[ExtractedPage],
    *,
    max_chunk_chars: int = 1200,
    overlap_chars: int = 150,
) -> list[SyllabusChunk]:
    """Chunk extracted pages without dropping non-whitespace source text.

    Paragraphs are preferred boundaries, then sentence boundaries. A sentence
    longer than the configured limit is split on word boundaries. Chunk indexes
    start at zero and are stable for the same ordered document and parameters.
    """
    if type(max_chunk_chars) is not int or max_chunk_chars < 1:
        raise ValueError("max_chunk_chars must be a positive integer.")
    if type(overlap_chars) is not int or overlap_chars < 0:
        raise ValueError("overlap_chars must be a non-negative integer.")
    if overlap_chars >= max_chunk_chars:
        raise ValueError("overlap_chars must be smaller than max_chunk_chars.")
    if not isinstance(pages, list) or any(not isinstance(page, ExtractedPage) for page in pages):
        raise TypeError("pages must be a list of ExtractedPage values.")

    all_pieces: list[_Piece] = []
    current_heading: str | None = None
    for page in pages:
        page_pieces, current_heading = _page_pieces(
            page, max_chunk_chars, overlap_chars, current_heading
        )
        all_pieces.extend(page_pieces)

    chunks: list[SyllabusChunk] = []
    current: list[_Piece] = []

    def emit(pieces: list[_Piece]) -> None:
        content = _render(pieces).strip()
        if not content:
            return
        source_pages = tuple(dict.fromkeys(piece.page_number for piece in pieces))
        section = next(
            (piece.heading for piece in reversed(pieces) if piece.heading),
            None,
        )
        chunks.append(SyllabusChunk(
            chunk_index=len(chunks),
            content=content,
            source_pages=source_pages,
            section_heading=section,
            character_count=len(content),
            token_estimate=math.ceil(len(content) / 4),
        ))

    for piece in all_pieces:
        candidate = current + [piece]
        if current and len(_render(candidate)) > max_chunk_chars:
            previous = current
            emit(previous)
            overlap = _overlap_pieces(previous, overlap_chars)
            # Do not let overlap force a content unit beyond the chunk limit.
            if overlap and len(_render(overlap + [piece])) <= max_chunk_chars:
                current = overlap
                piece = _Piece(
                    piece.text,
                    piece.page_number,
                    piece.heading,
                    " " if current else piece.separator,
                )
            else:
                current = []
            candidate = current + [piece]
        current = candidate

    if current:
        emit(current)
    return chunks


def parse_syllabus_pdf(
    source: PDFInput,
    *,
    max_chunk_chars: int = 1200,
    overlap_chars: int = 150,
) -> tuple[list[ExtractedPage], list[SyllabusChunk]]:
    """Extract and chunk one PDF; chunk indexes are local to this document."""
    pages = extract_pdf_pages(source)
    return pages, chunk_pages(
        pages,
        max_chunk_chars=max_chunk_chars,
        overlap_chars=overlap_chars,
    )
