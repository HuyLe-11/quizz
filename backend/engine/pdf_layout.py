"""Locate question boundaries in PDF text and PaddleOCR geometry."""
from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from typing import Any

import fitz

logger = logging.getLogger("quizpdf")

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

INLINE_OPTION_RE = re.compile(
    r"(?<![\w])(?:A\s*[.)]|\(A\))\s*\S",
    re.I,
)

PREFIX_RE = re.compile(
    r"^(?:câu(?:\s+hỏi|\s+số)?|cau(?:\s+hoi|\s+so)?|question|q)$",
    re.I,
)

NUMBER_FRAGMENT = re.compile(
    r"^\d{1,4}\s*[:.)](?:\s*|\s+.*)$"
)

NESTED_KEYS = (
    "overall_ocr_res",
    "overallOcrRes",
    "ocr_res",
    "ocrResult",
    "ocr_result",
    "res",
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

    except (TypeError, ValueError, IndexError, KeyError):
        return None

    if not all(math.isfinite(number) for number in numbers):
        return None

    if len(numbers) == 4:
        x0, y0, x1, y1 = numbers

    elif len(numbers) >= 8 and len(numbers) % 2 == 0:
        x0, x1 = min(numbers[::2]), max(numbers[::2])
        y0, y1 = min(numbers[1::2]), max(numbers[1::2])

    else:
        return None

    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _sort(lines: list[dict]) -> list[dict]:
    return sorted(
        lines,
        key=lambda line: (
            line["bbox"][1],
            line["bbox"][0],
        ),
    )


def pdf_lines(page: fitz.Page) -> list[dict]:
    grouped = defaultdict(list)

    for word in page.get_text("words"):
        grouped[(word[5], word[6])].append(word)

    lines = []

    for words in grouped.values():
        words.sort(key=lambda word: word[0])

        rect = fitz.Rect(
            min(word[0] for word in words),
            min(word[1] for word in words),
            max(word[2] for word in words),
            max(word[3] for word in words),
        ) * page.rotation_matrix

        lines.append({
            "text": " ".join(word[4] for word in words),
            "bbox": tuple(rect),
        })

    return _sort(lines)


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


def _recover_separate_question_rows(
    lines: list[dict],
) -> list[dict]:
    """Retain the existing conservative estimated-row fallback."""

    recovered = []

    for line in lines:
        rows = [
            part.strip()
            for part in line["text"].splitlines()
            if part.strip()
        ]

        numbers = [
            question_number(row)
            for row in rows
        ]

        if (
            not 2 <= len(rows) <= 4
            or numbers[0] is None
            or numbers != list(
                range(
                    numbers[0],
                    numbers[0] + len(rows),
                )
            )
            or any(
                INLINE_OPTION_RE.search(row) is None
                for row in rows
            )
        ):
            recovered.append(line)
            continue

        x0, y0, x1, y1 = line["bbox"]

        increment = (y1 - y0) / len(rows)

        if increment < 8.0:
            recovered.append(line)
            continue

        for index, row in enumerate(rows):
            recovered.append({
                **line,
                "text": row,
                "bbox": (
                    x0,
                    y0 + increment * index,
                    x1,
                    y0 + increment * (index + 1),
                ),
                "inferred_line_bounds": True,
            })

    return recovered


def _unsafe_geometry(node: dict) -> bool:
    pre = node.get("doc_preprocessor_res")

    pre = pre if isinstance(pre, dict) else {}

    for item in (node, pre):
        if item.get("angle", 0) not in (0, None):
            return True

        settings = item.get("model_settings")

        if (
            isinstance(settings, dict)
            and settings.get("use_doc_unwarping")
        ):
            return True

    return False


def _dimensions(
    node: dict,
) -> tuple[float, float] | None:
    width = _positive(node.get("width"))
    height = _positive(node.get("height"))

    return (width, height) if width and height else None


def _sources(page_ocr: dict):
    """Traverse known page-level OCR containers.

    Do not recursively consume arbitrary block-local boxes:
    their coordinates may refer to a cropped image.
    """

    seen = set()

    def walk(node, dimensions=None, depth=0):
        if (
            not isinstance(node, dict)
            or depth > 6
            or id(node) in seen
        ):
            return

        seen.add(id(node))

        if _unsafe_geometry(node):
            return

        dimensions = _dimensions(node) or dimensions

        # Exact recognition lines generally provide finer
        # geometry than complete layout blocks.
        for key in NESTED_KEYS:
            yield from walk(
                node.get(key),
                dimensions,
                depth + 1,
            )

        yield node, dimensions

    if _unsafe_geometry(page_ocr):
        return

    raw = page_ocr.get("raw")

    raw = raw if isinstance(raw, dict) else {}

    roots = (
        page_ocr.get("pruned_result"),
        page_ocr.get("prunedResult"),
        raw.get("prunedResult"),
        raw.get("pruned_result"),
    )

    # A page-level transform invalidates all coordinate
    # representations without an inverse transform.
    if (
        _unsafe_geometry(raw)
        or any(
            isinstance(root, dict)
            and _unsafe_geometry(root)
            for root in roots
        )
    ):
        return

    outer_dims = _dimensions(page_ocr)

    for root in roots:
        yield from walk(
            root,
            _dimensions(raw) or outer_dims,
        )

    yield from walk(raw, outer_dims)

    yield from walk(page_ocr, outer_dims)


def _item_sets(node: dict):
    """Extract recognition boxes and layout blocks separately."""

    texts = node.get("rec_texts")

    if isinstance(texts, list):
        for key in (
            "rec_boxes",
            "rec_polys",
            "dt_boxes",
            "dt_polys",
        ):
            rects = node.get(key)

            if (
                isinstance(rects, list)
                and len(rects) == len(texts)
            ):
                yield [
                    (text, bbox(rect))
                    for text, rect in zip(texts, rects)
                ]

    blocks = node.get("parsing_res_list")

    if isinstance(blocks, list):
        yield [
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


def _scale_items(page, items, dimensions):
    """Convert OCR coordinates into PDF page coordinates."""

    items = [
        (text.strip(), rect)
        for text, rect in items
        if (
            isinstance(text, str)
            and text.strip()
            and rect is not None
        )
    ]

    if not items:
        return []

    width = page.rect.width
    height = page.rect.height

    if dimensions:
        iw, ih = dimensions

        # Reject incompatible aspect ratios.
        if abs(
            (iw / ih) / (width / height) - 1
        ) > 0.05:
            return []

        # Reject coordinates outside the OCR image.
        if any(
            x0 < 0
            or y0 < 0
            or x1 > iw * 1.02
            or y1 > ih * 1.02
            for _, (x0, y0, x1, y1) in items
        ):
            return []

        sx, sy = width / iw, height / ih

    elif all(
        0 <= value <= 1
        for _, rect in items
        for value in rect
    ):
        sx, sy = width, height

    elif all(
        0 <= x0 < x1 <= width
        and 0 <= y0 < y1 <= height
        for _, (x0, y0, x1, y1) in items
    ):
        # Preserve legacy point-coordinate responses.
        # Never infer scale from the largest occupied box.
        sx = sy = 1.0

    else:
        return []

    lines = []
    seen = set()

    for text, rect in items:
        scaled = fitz.Rect(
            rect[0] * sx,
            rect[1] * sy,
            rect[2] * sx,
            rect[3] * sy,
        ) & page.rect

        key = (text, tuple(scaled))

        if not scaled.is_empty and key not in seen:
            seen.add(key)

            lines.append({
                "text": text,
                "bbox": tuple(scaled),
            })

    return _sort(lines)


def _join_heading_fragments(
    page: fitz.Page,
    lines: list[dict],
) -> list[dict]:
    """Join 'Câu' + '1:' only when adjacent on one physical row."""

    used = set()
    joined = []

    for i, left in enumerate(lines):
        if (
            i in used
            or not PREFIX_RE.fullmatch(
                left["text"].strip("*#_ ")
            )
        ):
            continue

        x0, y0, x1, y1 = left["bbox"]

        candidates = []

        for j, right in enumerate(lines):
            if (
                j in used
                or j == i
                or not NUMBER_FRAGMENT.fullmatch(
                    right["text"].strip("*#_ ")
                )
            ):
                continue

            a, b, c, d = right["bbox"]

            gap = a - x1

            overlap = min(y1, d) - max(y0, b)

            height = min(y1 - y0, d - b)

            if (
                0 <= gap <= min(
                    1.5 * height,
                    page.rect.width * 0.04,
                )
                and overlap >= 0.6 * height
            ):
                candidates.append(j)

        # Do not merge an ambiguous heading.
        if len(candidates) != 1:
            continue

        j = candidates[0]

        a, b, c, d = lines[j]["bbox"]

        joined.append({
            "text": (
                left["text"].strip("*#_ ")
                + " "
                + lines[j]["text"].strip("*#_ ")
            ),
            "bbox": (
                x0,
                min(y0, b),
                c,
                max(y1, d),
            ),
        })

        used.update((i, j))

    return _sort(
        joined + [
            line
            for i, line in enumerate(lines)
            if i not in used
        ]
    )


def _headings(line: dict) -> list[int]:
    return [
        number
        for text in line["text"].splitlines()
        if (number := question_number(text)) is not None
    ]


def _score(
    lines: list[dict],
) -> tuple[int, int, int, int]:
    """Prefer more usable question anchors and exact geometry."""

    counts = Counter(
        number
        for line in lines
        for number in _headings(line)
    )

    unique = sum(
        count == 1
        for count in counts.values()
    )

    exact = sum(
        len(_headings(line)) == 1
        and not line.get("inferred_line_bounds")
        for line in lines
    )

    options = sum(
        bool(OPTION_RE.match(line["text"]))
        for line in lines
    )

    duplicates = sum(
        count > 1
        for count in counts.values()
    )

    return unique, exact, -duplicates, options


def ocr_lines(
    page: fitz.Page,
    page_ocr: dict | None,
) -> list[dict]:
    if not isinstance(page_ocr, dict):
        return []

    candidates = []

    for source, dimensions in _sources(page_ocr):
        for items in _item_sets(source):
            lines = _scale_items(
                page,
                items,
                dimensions,
            )

            if lines:
                lines = _join_heading_fragments(
                    page,
                    lines,
                )

                candidates.append(
                    _sort(
                        _recover_separate_question_rows(
                            lines
                        )
                    )
                )

    # Select one coordinate representation rather than
    # mixing boxes from incompatible coordinate systems.
    return max(
        candidates,
        key=_score,
        default=[],
    )


def _column(
    page: fitz.Page,
    lines: list[dict],
    heading: dict,
) -> tuple[float, float]:
    width = page.rect.width

    anchors = [
        line
        for line in lines
        if (
            _headings(line)
            or OPTION_RE.match(line["text"])
        )
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
        heading["bbox"][0]
        + heading["bbox"][2]
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

    if question_number(
        heading["text"].splitlines()[0]
    ) != number:
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
            line["bbox"][0]
            + line["bbox"][2]
        ) / 2 <= x1
    ]

    following = [
        line["bbox"][1]
        for line in same_column
        if (
            line["bbox"][1] > y0
            and _headings(line)
        )
    ]

    next_y = min(following) if following else None

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

    inferred = (
        source == "paddleocr"
        and heading.get("inferred_line_bounds")
    )

    return {
        "bbox": (x0, y0, x1, end_y),
        "question_start_y": y0,
        "next_start_y": next_y,
        "column_x": (x0, x1),
        "options": options,
        "source": (
            "paddleocr_inferred"
            if inferred
            else source
        ),
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
        raise ValueError(
            "Invalid source page number"
        )

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
        logger.info(
            "pdf=question_localization "
            "page=%d question=%d source=full_page_fallback",
            first_page + 1,
            number,
        )

        return [
            (first_page, tuple(page.rect))
        ], None

    logger.info(
        "pdf=question_localization "
        "page=%d question=%d source=%s "
        "start_y=%.2f next_y=%s",
        first_page + 1,
        number,
        located["source"],
        located["question_start_y"],
        located["next_start_y"],
    )

    x0, x1 = located["column_x"]

    if located["source"] == "paddleocr_inferred":
        padding = min(padding, 4.0)

    start = max(
        0.0,
        located["question_start_y"] - padding,
    )

    end = located["next_start_y"]

    if end is not None:
        # Exact OCR geometry must not lose the final option
        # when it is close to the next question heading.
        stop = (
            end
            if located["source"] == "paddleocr"
            else end - padding / 2
        )

        return [
            (
                first_page,
                (
                    x0,
                    start,
                    x1,
                    max(start + 1, stop),
                ),
            )
        ], located

    segments = [
        (
            first_page,
            (
                x0,
                start,
                x1,
                page.rect.height,
            ),
        )
    ]

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