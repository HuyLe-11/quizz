"""Collect review-only registered marks for a prepared cloud exam."""
from __future__ import annotations

import logging
import math
import os
import sqlite3
from collections import Counter, defaultdict

import pymupdf

from .ocr_image_cache import OCRImageCache

logger = logging.getLogger(__name__)

MAX_PAGES = 4


def enabled():
    return os.getenv(
        "QUIZPDF_REGISTERED_MARKS_ENABLED",
        "0",
    ).strip() == "1"


def _page_questions(questions, id_counts):
    """Return source numbering, never the renumbered database IDs."""

    result = {}

    for question in questions:

        number = question.get(
            "source_question_number",
            question.get("question_id"),
        )

        ident = question.get("id")

        options = question.get("mcp_options")

        if (
            question.get("type") != "mcp"
            or question.get("multiple_choice")
            or type(number) is not int
            or number < 1
            or number in result
            or not isinstance(ident, str)
            or not ident
            or id_counts[ident] != 1
            or not isinstance(options, list)
            or not 2 <= len(options) <= 8
            or not all(
                isinstance(o, dict)
                for o in options
            )
        ):
            return {}

        labels = tuple(
            o.get("label")
            for o in options
        )

        if (
            any(
                l not in tuple("ABCDEFGH")
                for l in labels
            )
            or len(set(labels)) != len(labels)
        ):
            return {}

        result[number] = (
            ident,
            labels,
        )

    return result


def _coordinates(value, rect):

    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
    ):
        return None

    if any(
        type(v) not in (int, float)
        or not math.isfinite(v)
        for v in value
    ):
        return None

    x0, y0, x1, y1 = value

    if not (
        rect.x0 <= x0 < x1 <= rect.x1
        and rect.y0 <= y0 < y1 <= rect.y1
    ):
        return None

    return [
        float(v)
        for v in value
    ]


def collect_registered_marks(
    pdf_path,
    ocr_pages,
    questions,
    capture,
    *,
    cache=None,
):
    """Caller holds the existing PDF lock.

    Never mutate questions or OCR pages.
    """

    snapshot = {
        "version": 1,
        "status": "disabled",
        "by_question": {},
        "pages": {},
    }

    if not enabled():
        return snapshot

    snapshot["status"] = "processed"

    if (
        not isinstance(ocr_pages, list)
        or not all(
            isinstance(q, dict)
            for q in questions
        )
    ):
        snapshot["status"] = "unavailable"

        return snapshot

    id_counts = Counter(
        q["id"]

        for q in questions

        if isinstance(q.get("id"), str)
    )

    grouped = defaultdict(list)

    for q in questions:

        n = q.get("page_number")

        if (
            type(n) is not int
            or n < 1
        ):
            snapshot["status"] = "unavailable"

            return snapshot

        grouped[n].append(q)

    try:

        with pymupdf.open(pdf_path) as doc:

            if (
                len(ocr_pages) != len(doc)
                or any(
                    n > len(doc)
                    for n in grouped
                )
            ):

                snapshot["status"] = "unavailable"

                return snapshot

            attempted = 0

            for n, page_questions in sorted(
                grouped.items()
            ):

                selected = ocr_pages[n - 1]

                if not isinstance(selected, dict):
                    continue

                source_number = selected.get(
                    "source_page_number",
                    n,
                )

                if (
                    type(source_number) is not int
                    or source_number != n
                ):
                    continue

                root = selected.get(
                    "pruned_result"
                )

                if (
                    not isinstance(root, dict)
                    or not root.get("rec_boxes")
                ):
                    continue

                page = doc[n - 1]

                if (
                    page.rotation
                    or page.get_text().strip()
                ):
                    continue

                owners = _page_questions(
                    page_questions,
                    id_counts,
                )

                if not owners:
                    continue

                if attempted >= MAX_PAGES:

                    snapshot["status"] = "partial"

                    break

                attempted += 1

                if cache is None:

                    cache = OCRImageCache()

                outcome = capture.detect_page(
                    page,

                    selected,

                    {
                        number: labels

                        for number, (_, labels)
                        in owners.items()
                    },

                    cache=cache,
                )

                snapshot["pages"][str(n)] = {
                    "status": outcome.status,
                    "reason": outcome.reason,
                }

                if outcome.status != "processed":
                    continue

                conflicts = set(
                    outcome.conflicted_questions
                )

                hits = defaultdict(list)

                for mark in outcome.suggestions:

                    hits[
                        mark.question_number
                    ].append(mark)

                conflicts.update(
                    number

                    for number, values
                    in hits.items()

                    if len(values) != 1
                )

                for number, (
                    ident,
                    labels,
                ) in owners.items():

                    entry = {
                        "page_number": n,

                        "source_question_number": number,

                        "labels": list(labels),
                    }

                    if number in conflicts:

                        snapshot["by_question"][ident] = {
                            **entry,

                            "status": "conflict",
                        }

                        continue

                    values = hits.get(
                        number,
                        [],
                    )

                    if len(values) != 1:
                        continue

                    mark = values[0]

                    box = _coordinates(
                        mark.mark_coordinates,
                        page.rect,
                    )

                    if (
                        mark.label not in labels

                        or mark.status != "uncertain"

                        or box is None

                        or mark.registration_method
                        not in (
                            "local_affine",
                            "template",
                        )

                        or mark.evidence
                        not in (
                            "leading_region",
                            "repeated_columns",
                        )
                    ):
                        continue

                    snapshot["by_question"][ident] = {
                        **entry,

                        "status": "uncertain",

                        "detected_answer": mark.label,

                        "mark_coordinates": box,

                        "registration_method":
                            mark.registration_method,

                        "evidence": mark.evidence,
                    }

    except (
        OSError,
        ValueError,
        TypeError,
        RuntimeError,
        sqlite3.Error,
    ) as exc:

        logger.info(
            "registered_marks:snapshot_unavailable "
            "type=%s",

            type(exc).__name__,
        )

        snapshot["status"] = "unavailable"

        snapshot["by_question"].clear()

        snapshot["pages"].clear()

    return snapshot