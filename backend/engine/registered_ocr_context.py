"""Upload-local binding of selected OCR pages to durable provider results."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import logging
import sqlite3

from .ocr_image_cache import OCRPageRef, OCRImageCache
from .ocr_image_acquisition import acquire_images
from .registered_mark_detection import (
    PageMarkResult,
    detect_registered_marks,
)

logger = logging.getLogger(__name__)


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

        # The resilient client adds this field
        # after fallback selection.

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

        binding = self._pages.get(
            id(selected_ocr_page)
        )

        if (
            binding is None
            or binding.page is not selected_ocr_page
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

    """Opt-in synchronous upload scope;
    never serialize internal references.
    """

    capture = RegisteredOCRCapture()

    token = _active.set(capture)

    try:

        yield capture

    finally:

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

    """Called only after successful journal
    load or durable result save.
    """

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