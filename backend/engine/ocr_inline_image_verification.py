"""Verify inline OCR reconstruction proposals using original PDF pixels."""
from __future__ import annotations

import logging
import math
import re
from collections import Counter

import cv2
import numpy as np
import pymupdf

from .ocr_blank_recovery import (
    INSTRUCTION,
    _geometry_mode,
    _region,
    _text,
)

from .ocr_inline_reconstruction import (
    HEADING,
    propose_inline_reconstruction,
)

logger = logging.getLogger(__name__)

MONTH_DAYS = {
    "January": 31,
    "February": 29,
    "March": 31,
    "April": 30,
    "May": 31,
    "June": 30,
    "July": 31,
    "August": 31,
    "September": 30,
    "October": 31,
    "November": 30,
    "December": 31,
}

WARNING = (
    "Đã phục hồi nội dung và chỗ trống bằng đối chiếu "
    "hai OCR model và đường kẻ trên PDF gốc; "
    "cần kiểm tra lại nội dung và định dạng."
)


def _compact(value):
    return "".join(value.split())


def _valid_date(month, ordinal):

    if month not in MONTH_DAYS:
        return False

    match = re.fullmatch(
        r"([1-9]\d?)(st|nd|rd|th)",
        ordinal,
    )

    if match is None:
        return False

    day = int(match[1])

    if not 1 <= day <= MONTH_DAYS[month]:
        return False

    if 11 <= day % 100 <= 13:
        expected = "th"
    else:
        expected = {
            1: "st",
            2: "nd",
            3: "rd",
        }.get(day % 10, "th")

    return match[2] == expected


def _anchor_fraction(root, number, proposal):
    """
    Estimate the horizontal blank position from the first
    supplemental recognition line.

    Character counts are only approximate search hints,
    not registered PDF coordinates.
    """

    texts = root.get("rec_texts")

    if not isinstance(texts, list):
        return None

    matches = []

    for value in texts:

        if not isinstance(value, str):
            return None

        heading = HEADING.match(value)

        if heading and int(heading[1]) == number:
            matches.append(
                (value, heading)
            )

    if len(matches) != 1:
        return None

    first_line, heading = matches[0]

    prefix = proposal["prefix"]

    reconstructed = (
        prefix
        + proposal["inserted_word"]
        + proposal["ordinal"]
    )

    if not _compact(
        heading[2]
    ).startswith(
        _compact(reconstructed)
    ):
        return None

    anchor = (
        first_line[:heading.start(2)]
        + prefix
    )

    total = len(
        _compact(first_line)
    )

    if total < 25:
        return None

    fraction = len(
        _compact(anchor)
    ) / total

    if not .40 <= fraction <= .90:
        return None

    return fraction


def find_inline_rule(gray, anchor_fraction):
    """
    Find a unique isolated rule in the first text-line band.

    Returns pixel coordinates relative to the supplied image,
    or None when the evidence is insufficient.
    """

    if (
        not isinstance(gray, np.ndarray)
        or gray.ndim != 2
        or gray.dtype != np.uint8
        or not isinstance(anchor_fraction, (float, int))
        or not math.isfinite(anchor_fraction)
        or not .40 <= anchor_fraction <= .90
    ):
        return None

    h, w = gray.shape

    if not (
        80 <= h <= 400
        and 400 <= w <= 4000
        and w * h <= 1000000
    ):
        return None

    mask = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        12,
    )

    kernel = max(
        15,
        round(.035 * w),
    )

    lines = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        np.ones(
            (1, kernel),
            np.uint8,
        ),
    )

    _, _, stats, _ = (
        cv2.connectedComponentsWithStats(lines)
    )

    candidates = []

    for x, y, width, height, area in stats[1:]:

        if not (
            .12 * h <= y <= .42 * h
            and .04 * w <= width <= .12 * w
            and 1 <= height <= .045 * h
            and area >= .55 * width * height
        ):
            continue

        candidates.append((
            int(x),
            int(y),
            int(width),
            int(height),
        ))

    # Reject multiple competing rules in the first-line band.

    if len(candidates) != 1:
        return None

    x, y, width, height = candidates[0]

    if abs(
        x / (w - width) - anchor_fraction
    ) > .065:
        return None

    depth = round(.16 * h)

    pad = max(
        2,
        round(.12 * width),
    )

    top = y - depth
    bottom = y - 3

    if (
        top < 0
        or bottom <= top
        or x - depth < 0
        or x + width + depth > w
    ):
        return None

    inner = mask[
        top:bottom,
        x + pad:x + width - pad,
    ]

    pixels = gray[
        top:bottom,
        x + pad:x + width - pad,
    ]

    left = mask[
        top:y + height,
        x - depth:x - 3,
    ]

    right = mask[
        top:y + height,
        x + width + 3:x + width + depth,
    ]

    if not all(
        area.size
        for area in (
            inner,
            pixels,
            left,
            right,
        )
    ):
        return None

    # Require empty space above the rule and ink on both sides.

    if (
        np.mean(inner > 0) > .035
        or np.mean(pixels < 100) > .035
        or min(
            np.mean(left > 0),
            np.mean(right > 0),
        ) < .06
    ):
        return None

    return x, y, width, height


def _verify_pdf_rule(
    page,
    rect,
    anchor_fraction,
):

    if (
        rect.width * 4 > 4000
        or rect.height * 4 > 400
        or rect.width * rect.height * 16 > 1000000
    ):
        return None

    pix = page.get_pixmap(
        matrix=pymupdf.Matrix(4, 4),
        clip=rect,
        colorspace=pymupdf.csGRAY,
        alpha=False,
    )

    gray = np.frombuffer(
        pix.samples,
        dtype=np.uint8,
    ).reshape(
        pix.height,
        pix.width,
    )

    return find_inline_rule(
        gray,
        anchor_fraction,
    )


def _stem_block(question):

    blocks = question.get(
        "question_content"
    )

    if not isinstance(blocks, list):
        return None

    context = [
        question.get(key)
        for key in (
            "section_instruction",
            "shared_passage",
        )
        if question.get(key)
    ]

    if len(blocks) == 1:

        block = blocks[0]

    elif (
        context
        and len(blocks) == len(context) + 1
        and all(
            isinstance(item, dict)
            and item.get("type") == "text"
            and item.get("value") == value
            for item, value in zip(
                blocks,
                context,
            )
        )
    ):

        block = blocks[-1]

    else:

        return None

    if (
        not isinstance(block, dict)
        or block.get("type") != "latex"
        or not isinstance(
            block.get("value"),
            str,
        )
    ):
        return None

    return block


def recover_inline_scan_blanks(
    pdf_path,
    primary_pages,
    questions,
    *,
    geometry_page_for,
):
    """
    Apply a narrow date-related reconstruction proposal
    only when original PDF pixels corroborate the blank.

    No OCR provider calls, database writes or answer changes.
    """

    repairs = []

    if not isinstance(primary_pages, list):
        return repairs

    counts = Counter(
        (
            q.get("page_number"),
            q.get(
                "source_question_number",
                q.get("question_id"),
            ),
        )
        for q in questions
    )

    try:

        with pymupdf.open(pdf_path) as doc:

            if len(primary_pages) != len(doc):
                return repairs

            for q in questions:

                p = q.get("page_number")

                n = q.get(
                    "source_question_number",
                    q.get("question_id"),
                )

                if (
                    q.get("type") != "mcp"
                    or q.get("multiple_choice") is not False
                    or type(p) is not int
                    or not 1 <= p <= len(doc)
                    or type(n) is not int
                    or n < 1
                    or counts[(p, n)] != 1
                    or not INSTRUCTION.search(
                        str(
                            q.get(
                                "section_instruction"
                            ) or ""
                        )
                    )
                ):
                    continue

                options = q.get("mcp_options")

                if (
                    not isinstance(options, list)
                    or not all(
                        isinstance(o, dict)
                        for o in options
                    )
                    or [
                        o.get("label")
                        for o in options
                    ] != list("ABCD")
                ):
                    continue

                block = _stem_block(q)

                if block is None:
                    continue

                stem = block["value"]

                page = doc[p - 1]

                primary = primary_pages[p - 1]

                if (
                    page.rotation
                    or page.get_text().strip()
                    or not page.get_images()
                    or not isinstance(primary, dict)
                ):
                    continue

                secondary = geometry_page_for(
                    primary
                )

                if (
                    not isinstance(secondary, dict)
                    or secondary is primary
                    or primary.get("source_page_number", p) != p
                    or secondary.get("source_page_number", p) != p
                ):
                    continue

                root = secondary.get(
                    "pruned_result"
                )

                if not isinstance(root, dict):
                    continue

                mode = _geometry_mode(
                    secondary,
                    root,
                )

                if mode is None:
                    continue

                proposal = propose_inline_reconstruction(
                    stem,
                    root,
                    n,
                )

                if proposal is None:
                    continue

                if not _valid_date(
                    proposal["inserted_word"],
                    proposal["ordinal"],
                ):
                    continue

                fraction = _anchor_fraction(
                    root,
                    n,
                    proposal,
                )

                if fraction is None:
                    continue

                rect = _region(
                    page,
                    primary,
                    n,
                    _text(stem),
                )

                if rect is None:
                    continue

                rule = _verify_pdf_rule(
                    page,
                    rect,
                    fraction,
                )

                if rule is None:

                    logger.info(
                        "ocr_inline_reconstruction:rule_rejected "
                        "page=%d question=%d",
                        p,
                        n,
                    )

                    continue

                block["type"] = "text"

                block["value"] = (
                    proposal["prefix"]
                    + " ________ "
                    + proposal["inserted_word"]
                    + " "
                    + proposal["ordinal"]
                    + proposal["suffix"]
                )

                q["requires_source_image"] = True
                q["parse_fallback"] = True

                previous = str(
                    q.get("parser_warning") or ""
                )

                if WARNING not in previous:

                    q["parser_warning"] = " ".join(
                        filter(
                            None,
                            (previous, WARNING),
                        )
                    )

                repairs.append({
                    "page_number": p,
                    "source_question_number": n,
                    "inserted_word": proposal["inserted_word"],
                    "geometry_mode": mode,
                })

                logger.info(
                    "ocr_inline_reconstruction:repair "
                    "page=%d question=%d mode=%s",
                    p,
                    n,
                    mode,
                )

    except (
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        cv2.error,
    ) as exc:

        logger.info(
            "ocr_inline_reconstruction:unavailable type=%s",
            type(exc).__name__,
        )

    return repairs