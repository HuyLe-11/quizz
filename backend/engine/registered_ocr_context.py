"""Upload-local binding of primary pages to journal-backed PP-OCR geometry."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import logging
import os
import sqlite3

import cv2
import numpy as np
import pymupdf

from .ocr_image_cache import OCRPageRef, OCRImageCache
from .ocr_image_acquisition import acquire_images, allowed_hosts_from_env
from .registered_mark_detection import PageMarkResult, detect_registered_marks

logger = logging.getLogger(__name__)

MAX_SUPPLEMENTAL_BATCH_PAGES = 2
MAX_SCAN_PAGES = 4


@dataclass(frozen=True)
class _Binding:
    journal: object
    ref: OCRPageRef
    page: dict


_active = ContextVar(
    "registered_ocr_capture",
    default=None,
)


class RegisteredOCRCapture:

    def __init__(self):
        self._pages = {}
        self._aliases = {}

    def _snapshot(self, binding):

        ref = binding.ref
        journal = binding.journal

        key = journal.batch_key(
            ref.start_page,
            ref.page_count,
            "PP-OCRv6",
        )

        result = journal.load_result(
            key,
            ref.claim_id,
            ref.job_id,
            page_count=ref.page_count,
        )

        if result is None:
            return None

        pages, info = result

        number = (
            ref.start_page
            + ref.page_offset
            + 1
        )

        selected = dict(binding.page)

        if selected.pop(
            "source_page_number",
            number,
        ) != number:
            return None

        if selected != pages[ref.page_offset]:
            return None

        return result

    def detect_page(
        self,
        page,
        selected_ocr_page,
        labels_by_question,
        *,
        cache: OCRImageCache,
    ) -> PageMarkResult:

        if _active.get() is not self:
            return PageMarkResult(
                "unavailable",
                "capture_inactive",
            )

        geometry = geometry_page_for(
            selected_ocr_page
        )

        binding = self._pages.get(
            id(geometry)
        )

        if (
            binding is None
            or binding.page is not geometry
        ):
            return PageMarkResult(
                "unavailable",
                "no_selected_ppocr_page",
            )

        ref = binding.ref

        if page.number != (
            ref.start_page + ref.page_offset
        ):
            return PageMarkResult(
                "unavailable",
                "page_number_mismatch",
            )

        try:

            snapshot = self._snapshot(binding)

            if snapshot is None:
                return PageMarkResult(
                    "unavailable",
                    "ocr_binding_changed",
                )

            pages, info = snapshot

            sizes = info.get("pages")

            if (
                not isinstance(sizes, list)
                or len(sizes) != ref.page_count
            ):
                return PageMarkResult(
                    "unavailable",
                    "missing_batch_dimensions",
                )

            size = sizes[ref.page_offset]

            root = pages[
                ref.page_offset
            ].get("pruned_result")

            if (
                not isinstance(size, dict)
                or not isinstance(root, dict)
            ):
                return PageMarkResult(
                    "unavailable",
                    "missing_recognition",
                )

            images = acquire_images(
                binding.journal,
                ref,
                cache,
            )

            if images is None:
                return PageMarkResult(
                    "unavailable",
                    "ocr_images_unavailable",
                )

            if self._snapshot(binding) != snapshot:
                return PageMarkResult(
                    "unavailable",
                    "ocr_binding_changed",
                )

            result = detect_registered_marks(
                page,
                root,
                labels_by_question,
                images,
                width=size.get("width"),
                height=size.get("height"),
            )

            if self._snapshot(binding) != snapshot:
                return PageMarkResult(
                    "unavailable",
                    "ocr_binding_changed",
                )

            return result

        except (
            sqlite3.Error,
            OSError,
            ValueError,
            TypeError,
            RuntimeError,
        ) as exc:

            logger.info(
                "registered_ocr:unavailable type=%s",
                type(exc).__name__,
            )

            return PageMarkResult(
                "unavailable",
                "evidence_error",
            )


@contextmanager
def capture_registered_pages():

    capture = RegisteredOCRCapture()

    token = _active.set(capture)

    try:
        yield capture

    finally:

        capture._aliases.clear()

        capture._pages.clear()

        _active.reset(token)


def remember_completed_batch(
    journal,
    batch,
    model,
    claim_id,
    job_id,
    pages,
):

    capture = _active.get()

    if (
        capture is None
        or journal is None
        or model != "PP-OCRv6"
    ):
        return

    if len(pages) != batch.page_count:
        return

    for offset, page in enumerate(pages):

        if not isinstance(page, dict):
            continue

        ref = OCRPageRef(
            batch.start_page,
            batch.page_count,
            offset,
            claim_id,
            job_id,
        )

        capture._pages[id(page)] = _Binding(
            journal,
            ref,
            page,
        )


def geometry_page_for(primary):
    """Resolve explicit upload-local aliases, never page number alone."""

    capture = _active.get()

    if capture is not None:

        pair = capture._aliases.get(
            id(primary)
        )

        if (
            pair is not None
            and pair[0] is primary
        ):
            return pair[1]

    return primary


def remember_supplemental_batch(
    journal,
    batch,
    primary,
    secondary,
    offsets,
):

    capture = _active.get()

    if (
        capture is None
        or journal is None
        or len(primary) != batch.page_count
        or len(secondary) != batch.page_count
    ):
        return 0

    linked = 0

    for offset in offsets:

        if (
            type(offset) is not int
            or not 0 <= offset < batch.page_count
        ):
            continue

        original = primary[offset]
        geometry = secondary[offset]

        if (
            not isinstance(original, dict)
            or not isinstance(geometry, dict)
        ):
            continue

        binding = capture._pages.get(
            id(geometry)
        )

        if (
            binding is None
            or binding.page is not geometry
            or binding.journal is not journal
            or binding.ref.start_page != batch.start_page
            or binding.ref.page_count != batch.page_count
            or binding.ref.page_offset != offset
        ):
            continue

        root = geometry.get("pruned_result")

        if (
            not isinstance(root, dict)
            or not root.get("rec_boxes")
        ):
            continue

        capture._aliases[id(original)] = (
            original,
            geometry,
        )

        linked += 1

    return linked


def supplemental_candidates(
    path,
    batch,
    pages,
    converted,
):
    """Cheap red-ink screen. A quota gate, not answer recognition."""

    if (
        _active.get() is None
        or os.getenv(
            "QUIZPDF_REGISTERED_MARKS_ENABLED",
            "0",
        ).strip() != "1"
        or os.getenv(
            "QUIZPDF_REGISTERED_MARKS_SUPPLEMENTAL_OCR",
            "0",
        ).strip() != "1"
        or not allowed_hosts_from_env()
        or type(batch.page_count) is not int
        or not 1 <= batch.page_count <= MAX_SUPPLEMENTAL_BATCH_PAGES
        or len(pages) != batch.page_count
    ):
        return ()

    offsets = []

    try:

        with pymupdf.open(path) as doc:

            for offset, selected in enumerate(pages):

                n = batch.start_page + offset

                if (
                    not 0 <= n < min(
                        len(doc),
                        MAX_SCAN_PAGES,
                    )
                    or n + 1 in converted
                ):
                    continue

                if not isinstance(selected, dict):
                    continue

                root = selected.get(
                    "pruned_result"
                )

                if (
                    not isinstance(root, dict)
                    or root.get("rec_boxes")
                ):
                    continue

                page = doc[n]

                if (
                    page.rotation
                    or page.get_text().strip()
                    or not page.get_images()
                ):
                    continue

                scale = min(
                    2.0,
                    1200 / max(
                        page.rect.width,
                        page.rect.height,
                    ),
                )

                pix = page.get_pixmap(
                    matrix=pymupdf.Matrix(
                        scale,
                        scale,
                    ),
                    colorspace=pymupdf.csRGB,
                    alpha=False,
                )

                rgb = np.frombuffer(
                    pix.samples,
                    np.uint8,
                ).reshape(
                    pix.height,
                    pix.width,
                    3,
                )

                hsv = cv2.cvtColor(
                    rgb,
                    cv2.COLOR_RGB2HSV,
                )

                red = cv2.inRange(
                    hsv,
                    (0, 100, 50),
                    (10, 255, 255),
                )

                red |= cv2.inRange(
                    hsv,
                    (170, 100, 50),
                    (179, 255, 255),
                )

                if cv2.countNonZero(red) >= 40:

                    offsets.append(offset)

    except (
        OSError,
        ValueError,
        RuntimeError,
        cv2.error,
    ) as exc:

        logger.info(
            "registered_ocr:screen_unavailable type=%s",
            type(exc).__name__,
        )

        return ()

    return tuple(offsets)