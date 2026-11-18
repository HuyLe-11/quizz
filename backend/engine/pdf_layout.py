# Locate PDF question boundaries without estimating image size from text extents.
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

import fitz


QUESTION_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*"
    r"(?:câu(?:\s+hỏi|\s+số)?|cau(?:\s+hoi|\s+so)?|question|q)\s*(\d+)\b",
    re.I,
)

ORDINAL_RE = re.compile(
    r"^\s*(\d{1,3})\s*[.)]\s+\S"
)

OPTION_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\*\*|__)?\s*"
    r"(?:\(([A-H])\)|([A-H])\s*[.):])",
    re.I,
)


def question_number(text: str) -> int | None:
    match = QUESTION_RE.match(text) or ORDINAL_RE.match(text)
    return int(match.group(1)) if match else None


def bbox(value: Any) -> tuple[float, float, float, float] | None:
    try:
        if value and isinstance(value[0], (list, tuple)):
            value = [
                coordinate
                for point in value
                for coordinate in point
            ]

        numbers = [float(item) for item in value]

    except (TypeError, ValueError, IndexError):
        return None

    if not all(math.isfinite(n) for n in numbers):
        return None

    if len(numbers) == 4:
        x0, y0, x1, y1 = numbers

    elif len(numbers) >= 8 and len(numbers) % 2 == 0:
        x0, x1 = min(numbers[::2]), max(numbers[::2])
        y0, y1 = min(numbers[1::2]), max(numbers[1::2])

    else:
        return None

    return (
        (x0, y0, x1, y1)
        if x1 > x0 and y1 > y0
        else None
    )


def pdf_lines(page: fitz.Page) -> list[dict]:
    grouped = defaultdict(list)

    for word in page.get_text("words"):
        grouped[(word[5], word[6])].append(word)

    lines = []

    for words in grouped.values():
        words.sort(key=lambda w: w[0])

        rect = fitz.Rect(
            min(w[0] for w in words),
            min(w[1] for w in words),
            max(w[2] for w in words),
            max(w[3] for w in words),
        )

        rect = rect * page.rotation_matrix

        lines.append({
            "text": " ".join(w[4] for w in words),
            "bbox": tuple(rect),
        })

    return sorted(
        lines,
        key=lambda line: (
            line["bbox"][1],
            line["bbox"][0],
        ),
    )


def _positive(value: Any) -> float | None:
    try:
        result = float(value)

        return (
            result
            if math.isfinite(result) and result > 0
            else None
        )

    except (TypeError, ValueError):
        return None


def ocr_lines(
    page: fitz.Page,
    page_ocr: dict | None,
) -> list[dict]:
    if not isinstance(page_ocr, dict):
        return []

    # Normalized and raw representations may contain identical boxes.
    raw = page_ocr.get("raw")
    raw = raw if isinstance(raw, dict) else {}

    sources = [
        page_ocr.get("pruned_result"),
        page_ocr.get("prunedResult"),
        raw.get("prunedResult"),
        raw.get("pruned_result"),
        raw,
        page_ocr,
    ]

    for source in sources:
        if not isinstance(source, dict):
            continue

        items = []
        texts = source.get("rec_texts")

        for key in (
            "rec_boxes",
            "rec_polys",
            "dt_boxes",
            "dt_polys",
        ):
            rects = source.get(key)

            if (
                isinstance(texts, list)
                and isinstance(rects, list)
                and len(texts) == len(rects)
            ):
                items = [
                    (text, bbox(rect))
                    for text, rect in zip(texts, rects)
                ]

                if any(
                    rect is not None
                    for _, rect in items
                ):
                    break

        if not any(
            rect is not None
            for _, rect in items
        ):
            blocks = source.get("parsing_res_list")

            if isinstance(blocks, list):
                items = [
                    (
                        block.get("block_content"),
                        bbox(
                            block.get("block_bbox")
                            or block.get("block_polygon_points")
                        ),
                    )
                    for block in blocks
                    if isinstance(block, dict)
                ]

        items = [
            (text.strip(), rect)
            for text, rect in items
            if isinstance(text, str)
            and text.strip()
            and rect is not None
        ]

        if not items:
            continue

        width = (
            _positive(source.get("width"))
            or _positive(page_ocr.get("width"))
        )

        height = (
            _positive(source.get("height"))
            or _positive(page_ocr.get("height"))
        )

        if width and height:
            sx = page.rect.width / width
            sy = page.rect.height / height

        elif all(
            0 <= value <= 1
            for _, rect in items
            for value in rect
        ):
            sx = page.rect.width
            sy = page.rect.height

        elif all(
            0 <= rect[0] < rect[2] <= page.rect.width
            and 0 <= rect[1] < rect[3] <= page.rect.height
            for _, rect in items
        ):
            sx = sy = 1.0

        else:
            # Without dimensions, pixel-to-point scaling is uncertain.
            continue

        lines = []
        seen = set()

        for text, rect in items:
            scaled = fitz.Rect(
                rect[0] * sx,
                rect[1] * sy,
                rect[2] * sx,
                rect[3] * sy,
            ) & page.rect

            if scaled.is_empty:
                continue

            key = (text, tuple(scaled))

            if key not in seen:
                seen.add(key)

                lines.append({
                    "text": text,
                    "bbox": tuple(scaled),
                })

        if lines:
            return sorted(
                lines,
                key=lambda line: (
                    line["bbox"][1],
                    line["bbox"][0],
                ),
            )

    return []


def _headings(line: dict) -> list[int]:
    return [
        number
        for text in line["text"].splitlines()
        if (number := question_number(text)) is not None
    ]


def _column(
    page: fitz.Page,
    lines: list[dict],
    heading: dict,
) -> tuple[float, float]:
    # Split only when both sides contain question headings
    # and their bounding boxes are clearly separated.
    width = page.rect.width

    anchors = [
        line
        for line in lines
        if _headings(line)
        or OPTION_RE.match(line["text"])
    ]

    intervals = sorted(
        (line["bbox"][0], line["bbox"][2])
        for line in anchors
    )

    candidates = []
    right = 0.0

    for left, end in intervals:
        gap = left - right
        middle = (left + right) / 2

        if (
            gap >= width * 0.055
            and width * 0.2 < middle < width * 0.8
        ):
            lhs = [
                line
                for line in anchors
                if line["bbox"][2] <= middle
            ]

            rhs = [
                line
                for line in anchors
                if line["bbox"][0] >= middle
            ]

            if (
                len(lhs) >= 2
                and len(rhs) >= 2
                and any(_headings(line) for line in lhs)
                and any(_headings(line) for line in rhs)
            ):
                candidates.append((gap, middle))

        right = max(right, end)

    if not candidates:
        return 0.0, width

    _, middle = max(candidates)

    center = (
        heading["bbox"][0] + heading["bbox"][2]
    ) / 2

    return (
        (0.0, middle)
        if center < middle
        else (middle, width)
    )


def locate_in_lines(
    page: fitz.Page,
    number: int,
    lines: list[dict],
    source: str,
) -> dict | None:
    # A single block containing multiple question numbers
    # cannot be split reliably using its bounding box.
    targets = [
        line
        for line in lines
        if number in _headings(line)
    ]

    if (
        len(targets) != 1
        or _headings(targets[0]) != [number]
    ):
        return None

    heading = targets[0]

    if (
        question_number(
            heading["text"].splitlines()[0]
        ) != number
    ):
        return None

    x0, x1 = _column(
        page,
        lines,
        heading,
    )

    y0 = heading["bbox"][1]

    same_column = [
        line
        for line in lines
        if x0 <= (
            line["bbox"][0] + line["bbox"][2]
        ) / 2 <= x1
    ]

    following = [
        line["bbox"][1]
        for line in same_column
        if line["bbox"][1] > y0
        and _headings(line)
    ]

    next_y = (
        min(following)
        if following
        else None
    )

    end_y = (
        next_y
        if next_y is not None
        else page.rect.height
    )

    options = {}
    duplicates = set()

    for line in same_column:
        if (
            not y0 < line["bbox"][1] < end_y
            or "\n" in line["text"]
        ):
            continue

        match = OPTION_RE.match(line["text"])

        if match:
            label = (
                match.group(1)
                or match.group(2)
            ).upper()

            if label in options:
                duplicates.add(label)

            options[label] = line["bbox"]

    for label in duplicates:
        options.pop(label, None)

    return {
        "bbox": (x0, y0, x1, end_y),
        "question_start_y": y0,
        "next_start_y": next_y,
        "column_x": (x0, x1),
        "options": options,
        "source": source,
    }


def locate(
    page: fitz.Page,
    number: int,
    page_ocr: dict | None = None,
) -> dict | None:
    return (
        locate_in_lines(
            page,
            number,
            pdf_lines(page),
            "pymupdf",
        )
        or locate_in_lines(
            page,
            number,
            ocr_lines(page, page_ocr),
            "paddleocr",
        )
    )


def question_segments(
    document: fitz.Document,
    number: int,
    first_page: int,
    padding: float,
    pages: list | None = None,
) -> tuple[list, dict | None]:
    if not 0 <= first_page < len(document):
        raise ValueError("Invalid source page number")

    pages = pages or []
    page = document[first_page]

    located = locate(
        page,
        number,
        pages[first_page]
        if first_page < len(pages)
        else None,
    )

    if located is None:
        return [
            (first_page, tuple(page.rect))
        ], None

    x0, x1 = located["column_x"]

    start = max(
        0.0,
        located["question_start_y"] - padding,
    )

    end = located["next_start_y"]

    if end is not None:
        return [
            (
                first_page,
                (
                    x0,
                    start,
                    x1,
                    max(start + 1, end - padding / 2),
                ),
            )
        ], located

    segments = [
        (
            first_page,
            (x0, start, x1, page.rect.height),
        )
    ]

    # Do not guess transitions between columns and pages.
    if (x0, x1) != (0.0, page.rect.width):
        return segments, located

    for index in range(
        first_page + 1,
        min(len(document), first_page + 3),
    ):
        next_page = document[index]
        lines = pdf_lines(next_page)

        if not any(
            _headings(line)
            or OPTION_RE.match(line["text"])
            for line in lines
        ):
            lines = ocr_lines(
                next_page,
                pages[index]
                if index < len(pages)
                else None,
            )

        if not lines:
            break

        headings = [
            line["bbox"][1]
            for line in lines
            if _headings(line)
        ]

        stop = (
            min(headings)
            if headings
            else next_page.rect.height
        )

        # Require evidence that the question continues:
        # an option before the next question heading.
        if not any(
            line["bbox"][1] < stop
            and OPTION_RE.match(line["text"])
            for line in lines
        ):
            break

        segments.append((
            index,
            (
                0.0,
                0.0,
                next_page.rect.width,
                min(next_page.rect.height, stop),
            ),
        ))

        if headings:
            break

    return segments, located
