"""Locate exact option-marker boxes without assuming subject or layout."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

import fitz


MARKER = re.compile(
    r"(?:\(([A-H])\)|([A-H])[.):])"
)

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class OptionGeometry:
    anchors: dict[str, Box]
    source: str

    def covers(
        self,
        labels: Iterable[str],
    ) -> bool:

        required = list(labels)

        return (
            len(required) >= 2
            and len(set(required)) == len(required)
            and set(required) == set(self.anchors)
        )


def exact_marker_boxes(
    items: Iterable[dict],
    region: Box,
) -> dict[str, Box]:
    """Only use boxes whose entire text is an explicit option marker."""

    x0, y0, x1, y1 = region

    found = defaultdict(set)

    for item in items:

        text = item.get("text")

        if not isinstance(text, str):
            continue

        match = MARKER.fullmatch(
            text.strip()
        )

        if (
            match is None
            or item.get("inferred_line_bounds")
        ):
            continue

        try:

            box = tuple(
                float(v)
                for v in item["bbox"]
            )

            a, b, c, d = box

        except (
            TypeError,
            ValueError,
            KeyError,
            OverflowError,
        ):
            continue

        if not all(
            math.isfinite(v)
            for v in box
        ):
            continue

        # The entire box must belong to
        # the current question.

        if not (
            x0 <= a < c <= x1
            and y0 <= b < d <= y1
        ):
            continue

        # Reject wide regions pretending
        # to be one option label.

        if c - a > 3 * (d - b):
            continue

        label = (
            match.group(1)
            or match.group(2)
        )

        found[label].add(box)

    # Never choose an arbitrary occurrence
    # of a repeated label.

    if any(
        len(boxes) != 1
        for boxes in found.values()
    ):
        return {}

    result = {
        label: next(iter(boxes))
        for label, boxes in sorted(
            found.items()
        )
    }

    if len(result) < 2:
        return {}

    # Labels sharing the same physical area
    # are not independent evidence.

    boxes = list(result.values())

    for i, first in enumerate(boxes):

        for second in boxes[i + 1:]:

            intersection = (
                fitz.Rect(first)
                & fitz.Rect(second)
            )

            if intersection.get_area() > 0:
                return {}

    return result


def native_marker_geometry(
    page: fitz.Page,
    region: Box,
) -> OptionGeometry:

    items = [
        {
            "text": word[4],
            "bbox": tuple(
                fitz.Rect(word[:4])
                * page.rotation_matrix
            ),
        }
        for word in page.get_text("words")
    ]

    return OptionGeometry(
        exact_marker_boxes(items, region),
        "native_words",
    )


def recognition_marker_geometry(
    representations: Iterable[list[dict]],
    region: Box,
) -> OptionGeometry:
    """Choose a single OCR representation."""

    candidates = [
        exact_marker_boxes(items, region)
        for items in representations
    ]

    count = max(
        (
            len(item)
            for item in candidates
        ),
        default=0,
    )

    if not count:
        return OptionGeometry(
            {},
            "none",
        )

    best = [
        item
        for item in candidates
        if len(item) == count
    ]

    first = best[0]

    # Equally complete but contradictory
    # representations are ambiguous.

    if any(
        item != first
        for item in best[1:]
    ):
        return OptionGeometry(
            {},
            "none",
        )

    return OptionGeometry(
        first,
        "ocr_markers",
    )