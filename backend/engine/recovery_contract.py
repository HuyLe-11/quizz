"""Immutable, subject-independent contracts for text recovery proposals.

Construction validates structure and identity only, not factual correctness.
No proposal authorizes changes; a separate validator must decide that.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .spatial_association import PixelFrame


SourceKind = Literal[
    "primary_ocr", "supplemental_ocr", "pdf_text", "pdf_vector", "pdf_pixels"
]
RecoveryKind = Literal[
    "text_reconciliation", "blank_recovery", "format_recovery", "inline_reconstruction"
]
BlockKind = Literal["text", "latex"]
BBox = tuple[float, float, float, float]


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("Invalid identifier.")


def _document(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Invalid document SHA-256.")


def _page(value: int) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("Invalid page number.")


def _geometry(frame: PixelFrame, bbox: BBox) -> None:
    if not isinstance(frame, PixelFrame):
        raise ValueError("Invalid pixel frame.")
    _identifier(frame.key)
    if (type(frame.width) is not int or type(frame.height) is not int
            or frame.width < 1 or frame.height < 1):
        raise ValueError("Invalid frame dimensions.")
    if (not isinstance(bbox, tuple) or len(bbox) != 4
            or any(type(v) not in (int, float) for v in bbox)):
        raise ValueError("Invalid evidence bounding box.")
    x0, y0, x1, y1 = bbox
    if not (0 <= x0 < x1 <= frame.width and 0 <= y0 < y1 <= frame.height):
        raise ValueError("Evidence box outside pixel frame.")


@dataclass(frozen=True, slots=True)
class RecoveryTarget:
    """One block in one upload-local question snapshot.

    question_key must uniquely identify the question within that snapshot;
    the caller must not substitute an ambiguous printed question number.
    option_label=None refers to question_content, otherwise option content.
    """
    document_sha256: str
    question_key: str
    page_number: int
    block_index: int
    option_label: str | None = None

    def __post_init__(self) -> None:
        _document(self.document_sha256)
        _identifier(self.question_key)
        _page(self.page_number)
        if type(self.block_index) is not int or self.block_index < 0:
            raise ValueError("Invalid block index.")
        if self.option_label is not None:
            _identifier(self.option_label)


@dataclass(frozen=True, slots=True)
class TextBlock:
    kind: BlockKind
    value: str

    def __post_init__(self) -> None:
        if self.kind not in ("text", "latex"):
            raise ValueError("Unsupported block kind.")
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("Expected nonempty block text.")


@dataclass(frozen=True, slots=True)
class RecoveryEvidence:
    """An observation, not a decision or proof of registration.

    source_id identifies the underlying source, not the observation. Multiple
    observations of one source do not constitute independent OCR results.
    Geometry is in the declared pixel frame, which may be provider-local.
    """
    document_sha256: str
    page_number: int
    source_kind: SourceKind
    source_id: str
    observed_text: str | None = None
    frame: PixelFrame | None = None
    bbox: BBox | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        _document(self.document_sha256)
        _page(self.page_number)
        _identifier(self.source_id)
        if self.source_kind not in (
            "primary_ocr", "supplemental_ocr", "pdf_text", "pdf_vector", "pdf_pixels"
        ):
            raise ValueError("Invalid evidence source kind.")
        if self.observed_text is not None and (
            not isinstance(self.observed_text, str) or not self.observed_text.strip()
        ):
            raise ValueError("Invalid observed text.")
        if (self.frame is None) != (self.bbox is None):
            raise ValueError("Evidence geometry requires both frame and box.")
        if self.frame is not None:
            _geometry(self.frame, self.bbox)
        if self.observed_text is None and self.bbox is None:
            raise ValueError("Evidence must contain an observation.")
        if self.source_kind in ("pdf_pixels", "pdf_vector") and self.bbox is None:
            raise ValueError("Visual evidence requires geometry.")
        if self.confidence is not None and (
            type(self.confidence) not in (int, float)
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("Invalid confidence.")


@dataclass(frozen=True, slots=True)
class RecoveryProposal:
    """A proposed replacement of an existing block; never an applied edit."""
    proposal_id: str
    target: RecoveryTarget
    operation: RecoveryKind
    original: TextBlock
    replacement: TextBlock
    evidence: tuple[RecoveryEvidence, ...]

    def __post_init__(self) -> None:
        _identifier(self.proposal_id)
        if not isinstance(self.target, RecoveryTarget):
            raise ValueError("Invalid recovery target.")
        if self.operation not in (
            "text_reconciliation", "blank_recovery", "format_recovery", "inline_reconstruction"
        ):
            raise ValueError("Invalid recovery operation.")
        if not isinstance(self.original, TextBlock) or not isinstance(self.replacement, TextBlock):
            raise ValueError("Invalid recovery blocks.")
        if self.original == self.replacement:
            raise ValueError("Recovery proposal contains no change.")
        if not isinstance(self.evidence, tuple) or not self.evidence:
            raise ValueError("Expected nonempty immutable evidence.")
        for item in self.evidence:
            if not isinstance(item, RecoveryEvidence):
                raise ValueError("Invalid recovery evidence.")
            if (item.document_sha256 != self.target.document_sha256
                    or item.page_number != self.target.page_number):
                raise ValueError("Evidence belongs to another document or page.")
