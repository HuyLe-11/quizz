"""Associate red ink with observed option geometry; PaddleOCR only."""
from __future__ import annotations

import math
import re
from statistics import median

from .option_geometry import MARKER, exact_marker_boxes


def paddle_recognition_items(page, page_ocr):
    """Use only page-level recognition boxes, never layout blocks or dt_boxes."""
    from .pdf_layout import _sources, _scale_items, bbox

    if page.rotation or not isinstance(page_ocr, dict):
        return []

    representations = []

    for node, dimensions in _sources(page_ocr):

        texts = node.get("rec_texts")

        if dimensions is None or not isinstance(texts, list):
            continue

        scores = node.get("rec_scores")

        if scores is not None and (
            not isinstance(scores, list)
            or len(scores) != len(texts)
        ):
            continue

        for key in ("rec_polys", "rec_boxes"):

            boxes = node.get(key)

            if (
                not isinstance(boxes, list)
                or len(boxes) != len(texts)
            ):
                continue

            pairs = []

            for i, (text, box) in enumerate(zip(texts, boxes)):

                if not isinstance(text, str) or not text.strip():
                    continue

                if scores is not None:

                    try:
                        score = float(scores[i])

                    except (
                        TypeError,
                        ValueError,
                        OverflowError,
                    ):
                        continue

                    if (
                        not math.isfinite(score)
                        or not 0.7 <= score <= 1
                    ):
                        continue

                rect = bbox(box)

                if rect is not None:
                    pairs.append((text, rect))

            items = _scale_items(
                page,
                pairs,
                dimensions,
            )

            if items:
                representations.append(items)
                break

    if not representations:
        return []

    # Do not union possibly incompatible representations.

    best = max(representations, key=len)

    signature = {
        (item["text"], tuple(item["bbox"]))
        for item in best
    }

    if any(
        not {
            (item["text"], tuple(item["bbox"]))
            for item in items
        } <= signature
        for items in representations
    ):
        return []

    return best


def enclosed_anchor_hits(
    anchors,
    region,
    labels,
    components,
    scale,
    min_pixels,
):
    """Require the whole mark and marker box inside the question."""

    hits = []

    if not math.isfinite(scale) or scale <= 0:
        return hits

    x0, y0, x1, y1 = region

    for x, y, w, h, area in components:

        if area < min_pixels or w <= 0 or h <= 0:
            continue

        a, b, c, d = box = (
            x / scale,
            y / scale,
            (x + w) / scale,
            (y + h) / scale,
        )

        if not (
            x0 <= a < c <= x1
            and y0 <= b < d <= y1
        ):
            continue

        for label in labels:

            if label not in anchors:
                continue

            u, v, r, s = anchors[label]

            cx = (u + r) / 2
            cy = (v + s) / 2

            if (
                x0 <= u < r <= x1
                and y0 <= v < s <= y1
                and a <= cx <= c
                and b <= cy <= d
                and c - a <= 4 * (s - v)
                and d - b <= 4 * (s - v)
            ):
                hits.append((label, box))

    return hits


def _inline_run(text, labels):

    # A missing interior delimiter is acceptable
    # only as layout evidence.

    pattern = re.compile(
        r"(?<!\S)([A-H])[.):,][ \t]*"
    )

    valid = 0

    for row in text.splitlines():

        matches = list(pattern.finditer(row))

        seen = [
            match.group(1)
            for match in matches
        ]

        if seen == labels:
            valid += 1
            continue

        missing = [
            label
            for label in labels
            if label not in seen
        ]

        if (
            len(missing) != 1
            or missing[0] in (
                labels[0],
                labels[-1],
            )
            or seen != [
                label
                for label in labels
                if label != missing[0]
            ]
        ):
            continue

        i = labels.index(missing[0])

        fragment = row[
            matches[i - 1].end():
            matches[i].start()
        ]

        if len(
            re.findall(
                r"(?<!\S)" + missing[0] + r"[ \t]+",
                fragment,
            )
        ) == 1:
            valid += 1

    return valid == 1


def infer_repeated_columns(
    lines,
    items,
    labels,
    components,
    scale,
    min_pixels,
):
    """Review-only fallback for independently observed marker columns."""

    from .pdf_layout import question_number

    if (
        not 3 <= len(labels) <= 8
        or labels != list("ABCDEFGH"[:len(labels)])
        or not math.isfinite(scale)
        or scale <= 0
    ):
        return {}

    questions = []

    for line in lines:

        rows = line["text"].splitlines()

        if not rows:
            continue

        number = question_number(rows[0])

        if number is None:
            continue

        if not _inline_run(line["text"], labels):
            return {}

        questions.append(
            (
                number,
                tuple(line["bbox"]),
            )
        )

    questions.sort(
        key=lambda question: question[1][1]
    )

    if not 3 <= len(questions) <= 150:
        return {}

    numbers = [
        question[0]
        for question in questions
    ]

    if numbers != list(
        range(
            numbers[0],
            numbers[0] + len(numbers),
        )
    ):
        return {}

    if max(
        question[1][0]
        for question in questions
    ) >= min(
        question[1][2]
        for question in questions
    ):
        return {}

    centers = {
        label: []
        for label in labels
    }

    heights = []

    for item in items:

        match = MARKER.fullmatch(item["text"])

        if not match:
            continue

        label = match.group(1) or match.group(2)

        if label not in centers:
            continue

        a, b, c, d = item["bbox"]

        x = (a + c) / 2
        y = (b + d) / 2

        owners = [
            number
            for number, (x0, y0, x1, y1) in questions
            if x0 <= x <= x1 and y0 <= y <= y1
        ]

        if (
            len(owners) == 1
            and 0 < c - a <= 3 * (d - b)
        ):
            centers[label].append(
                (x, y, owners[0])
            )

            heights.append(d - b)

    if (
        any(
            len({
                number
                for _, _, number in points
            }) < 2
            for points in centers.values()
        )
        or not heights
    ):
        return {}

    unit = median(heights)

    if not 2 <= unit <= 30:
        return {}

    for left, right in zip(labels, labels[1:]):

        if max(
            x
            for x, _, _ in centers[left]
        ) + unit >= min(
            x
            for x, _, _ in centers[right]
        ):
            return {}

    candidates = []

    for x, y, w, h, area in components:

        if area < min_pixels or w <= 0 or h <= 0:
            continue

        a, b, c, d = box = (
            x / scale,
            y / scale,
            (x + w) / scale,
            (y + h) / scale,
        )

        cx = (a + c) / 2
        cy = (b + d) / 2

        if not (
            0.5 <= w / h <= 2
            and max(c - a, d - b) <= 4 * unit
            and min(c - a, d - b) >= 0.6 * unit
        ):
            continue

        owners = {
            number
            for number, (x0, y0, x1, y1) in questions
            if x0 <= cx <= x1 and y0 <= cy <= y1
        }

        if not owners:
            continue

        possible = [
            label
            for label, points in centers.items()
            if len({
                number
                for px, _, number in points
                if abs(px - cx) <= 1.5 * unit
            }) >= 2
        ]

        if len(possible) != 1:
            return {}

        candidates.append(
            (
                possible[0],
                box,
                owners,
            )
        )

    if len(candidates) != len(questions):
        return {}

    remaining = {
        i: set(item[2])
        for i, item in enumerate(candidates)
    }

    result = {}

    while remaining:

        singles = [
            (i, next(iter(owners)))
            for i, owners in remaining.items()
            if len(owners) == 1
        ]

        if not singles:
            return {}

        for i, number in singles:

            if number in result:
                return {}

            result[number] = candidates[i][:2]

            del remaining[i]

        for owners in remaining.values():

            owners.difference_update(result)

            if not owners:
                return {}

    return result


def _single_option_regions(items, region, labels):
    """Recognition boxes containing exactly one leading option label."""

    pattern = re.compile(
        r"(?<!\S)(?:\(([A-H])\)|([A-H])[.):,])[ \t]*"
    )

    found = {}

    x0, y0, x1, y1 = region

    for item in items:

        text = item["text"]

        if "\n" in text:
            continue

        matches = list(pattern.finditer(text))

        if (
            len(matches) != 1
            or matches[0].start() != 0
        ):
            continue

        label = (
            matches[0].group(1)
            or matches[0].group(2)
        )

        a, b, c, d = item["bbox"]

        if not (
            x0 <= a < c <= x1
            and y0 <= b < d <= y1
        ):
            continue

        if label not in labels:
            continue

        if label in found:
            return {}

        found[label] = (a, b, c, d)

    if set(found) != set(labels):
        return {}

    boxes = list(found.values())

    for i, (a, b, c, d) in enumerate(boxes):

        for u, v, r, s in boxes[i + 1:]:

            if (
                max(a, u) < min(c, r)
                and max(b, v) < min(d, s)
            ):
                return {}

    return found


def _leading_region_hits(
    regions,
    region,
    components,
    scale,
    min_pixels,
):
    # A region is not an exact marker box.
    # Only accept a small mark containing its leading edge.
    # The result always requires confirmation.

    hits = []

    if not math.isfinite(scale) or scale <= 0:
        return hits

    x0, y0, x1, y1 = region

    for x, y, w, h, area in components:

        if (
            area < min_pixels
            or w <= 0
            or h <= 0
            or not 0.5 <= w / h <= 2
        ):
            continue

        a, b, c, d = box = (
            x / scale,
            y / scale,
            (x + w) / scale,
            (y + h) / scale,
        )

        if not (
            x0 <= a < c <= x1
            and y0 <= b < d <= y1
        ):
            continue

        for label, (u, v, r, s) in regions.items():

            unit = s - v

            if (
                a <= u + min(r - u, unit) / 2 <= c
                and b <= (v + s) / 2 <= d
                and abs((a + c) / 2 - u) <= unit
                and max(c - a, d - b) <= 4 * unit
                and min(c - a, d - b) >= 0.6 * unit
            ):
                hits.append((label, box))

    return hits


def paddle_mark_suggestion(
    page,
    number,
    located,
    page_ocr,
    detail_page_ocr,
    labels,
    components,
    scale,
    min_pixels,
):
    """Use separate PaddleOCR evidence without overwriting VL content."""

    from .pdf_layout import locate, ocr_lines

    if (
        page.rotation
        or page.get_text().strip()
        or located["source"] not in {
            "paddleocr",
            "paddleocr_inferred",
        }
        or len(labels) < 2
        or len(set(labels)) != len(labels)
    ):
        return None

    evidence = (
        detail_page_ocr
        if detail_page_ocr is not None
        else page_ocr
    )

    items = paddle_recognition_items(
        page,
        evidence,
    )

    if not items:
        return None

    # Prefer an independently localized question
    # in detailed PaddleOCR data.

    target = locate(
        page,
        number,
        evidence,
    )

    if target and target["source"] == "paddleocr":

        a, b, c, d = target["bbox"]

        x0, y0, x1, y1 = located["bbox"]

        if (
            max(a, x0) < min(c, x1)
            and max(b, y0) < min(d, y1)
        ):

            anchors = exact_marker_boxes(
                items,
                target["bbox"],
            )

            if set(anchors) == set(labels):

                hits = enclosed_anchor_hits(
                    anchors,
                    target["bbox"],
                    labels,
                    components,
                    scale,
                    min_pixels,
                )

                if len(hits) == 1:
                    return hits[0]

                # Never override ambiguous direct association.

                return None

            regions = _single_option_regions(
                items,
                target["bbox"],
                labels,
            )

            if regions:

                hits = _leading_region_hits(
                    regions,
                    target["bbox"],
                    components,
                    scale,
                    min_pixels,
                )

                return (
                    hits[0]
                    if len(hits) == 1
                    else None
                )

    return infer_repeated_columns(
        ocr_lines(page, page_ocr),
        items,
        labels,
        components,
        scale,
        min_pixels,
    ).get(number)