"""Recover one omitted printed blank using independent OCR and PDF ink."""
from __future__ import annotations

import logging
import re
from collections import Counter

import cv2
import numpy as np
import pymupdf

from .pdf_layout import _sources, _scale_items, bbox

logger = logging.getLogger(__name__)

HEADING = re.compile(
    r"^\s*(?:Question|Câu|Bài)\s*(\d+)\s*[.):]\s*(.*)$",
    re.I,
)

INSTRUCTION = re.compile(
    r"\bcomplete\b.*\b(?:questions?|sentences?)\b"
    r"|\bfill\b.*\bblanks?\b",
    re.I | re.S,
)

PREFIX = re.compile(r"[A-Za-z]+(?: [A-Za-z]+){0,2}")

OPTION = re.compile(r"^[A-H]\s*[.):]")

MARKED_TEXT = re.compile(
    r"\$\s*\\underline\{\s*\\text\{([A-Za-z]+(?:[ \t]+[A-Za-z]+){0,7})\}"
    r"\s*\}\s*\$"
)

LONG_PREFIX = re.compile(
    r"[A-Za-z][A-Za-z0-9 ,.'’\-?!:;()]*"
)

WARNING = (
    "Đã khôi phục chỗ trống dựa trên đường kẻ PDF; "
    "cần đối chiếu ảnh gốc."
)

MARKUP_WARNING = (
    "Đã chuyển biểu thức gạch chân thành văn bản khi phục hồi chỗ trống; "
    "cần kiểm tra định dạng trên ảnh gốc."
)

UNWARPING_WARNING = (
    "OCR bổ sung có hiệu chỉnh hình học; vị trí chỗ trống "
    "được kiểm tra trực tiếp trên PDF gốc và cần xác nhận lại."
)


def _text(value):
    return " ".join(value.split())


def _marked_parts(stem):
    """Accept exactly one narrow, text-only LaTeX underline."""

    matches = list(MARKED_TEXT.finditer(stem))

    if len(matches) != 1:
        return None

    match = matches[0]

    before = stem[:match.start()]
    after = stem[match.end():]

    if any(c in before + after for c in "$\\{}_\u0332"):
        return None

    left = _text(before + " " + match[1])
    right = _text(after)

    if not left or not right:
        return None

    return left, right


def _geometry_mode(other, root):
    """
    Return the permitted use of supplemental geometry.

    Registered geometry can be used normally.

    Unwarped geometry is only a relative search hint.
    It must never be registered as original PDF coordinates.
    """

    if any(
        node is root
        for node, _ in _sources(other)
    ):
        return "registered"

    pre = root.get("doc_preprocessor_res")

    if not isinstance(pre, dict):
        return None

    settings = pre.get("model_settings")

    if not isinstance(settings, dict):
        return None

    root_settings = root.get("model_settings")

    if not isinstance(root_settings, dict):
        return None

    if (
        root_settings.get("use_doc_preprocessor") is not True
        or settings.get("use_doc_unwarping") is not True
        or type(pre.get("angle")) is not int
        or pre["angle"] != 0
    ):
        return None

    raw = other.get("raw")

    if not isinstance(raw, dict):
        raw = {}

    containers = (
        other,
        root,
        raw,
        other.get("prunedResult"),
        raw.get("prunedResult"),
        raw.get("pruned_result"),
    )

    # Reject conflicting rotation metadata.
    for node in containers:

        if not isinstance(node, dict):
            continue

        if node.get("angle") not in (None, 0):
            return None

        node_pre = node.get("doc_preprocessor_res")

        if (
            isinstance(node_pre, dict)
            and node_pre.get("angle") not in (None, 0)
        ):
            return None

    return "unwarped_hint"


def _split(root, number, *, marked=False):
    texts, boxes, scores = (
        root.get(k)
        for k in (
            "rec_texts",
            "rec_boxes",
            "rec_scores",
        )
    )

    if not all(
        isinstance(a, list)
        for a in (texts, boxes, scores)
    ):
        return None

    if (
        not 0 < len(texts) <= 5000
        or not len(texts) == len(boxes) == len(scores)
    ):
        return None

    if not all(
        isinstance(t, str)
        for t in texts
    ):
        return None

    starts = [
        i
        for i, t in enumerate(texts)
        if (m := HEADING.match(t))
        and int(m[1]) == number
    ]

    if (
        len(starts) != 1
        or starts[0] + 2 >= len(texts)
    ):
        return None

    i = starts[0]

    left = _text(
        HEADING.match(texts[i])[2]
    )

    right = _text(
        texts[i + 1]
    )

    prefix_ok = (
        1 <= len(left.split()) <= 32
        and len(left) <= 220
        and LONG_PREFIX.fullmatch(left) is not None
    ) if marked else PREFIX.fullmatch(left) is not None

    if (
        not prefix_ok
        or len(right.split()) < 4
        or not right[0].isalpha()
    ):
        return None

    if (
        HEADING.match(right)
        or OPTION.match(right)
        or not OPTION.match(texts[i + 2].strip())
    ):
        return None

    if not all(
        type(s) in (float, int)
        and .95 <= s <= 1
        for s in scores[i:i + 2]
    ):
        return None

    a, b = (
        bbox(v)
        for v in boxes[i:i + 2]
    )

    if a is None or b is None:
        return None

    unit = min(
        a[3] - a[1],
        b[3] - b[1],
    )

    gap = b[0] - a[2]

    span = b[2] - a[0]

    if (
        a[0] < 0
        or min(a[1], b[1]) < 0
        or min(a[3], b[3]) - max(a[1], b[1]) < .6 * unit
    ):
        return None

    if (
        not 2.2 * unit <= gap <= 7 * unit
        or not 0 < span <= 4000
    ):
        return None

    start = (a[2] - a[0]) / span
    end = (b[0] - a[0]) / span

    limit = .90 if marked else .55

    if not .08 <= start < end <= limit:
        return None

    return left, right, start, end


def _region(page, primary, number, stem):
    """Find a unique untransformed VL block containing the exact stem."""

    matches = set()

    for node, dims in _sources(primary):

        blocks = node.get("parsing_res_list")

        if (
            dims is None
            or not isinstance(blocks, list)
        ):
            continue

        items = _scale_items(
            page,
            [
                (
                    b.get("block_content"),
                    bbox(b.get("block_bbox")),
                )
                for b in blocks
                if isinstance(b, dict)
            ],
            dims,
        )

        for item in items:

            lines = item["text"].splitlines()

            if len(lines) != 2:
                continue

            match = HEADING.match(lines[0])

            if (
                not match
                or int(match[1]) != number
                or _text(match[2]) != stem
            ):
                continue

            if not re.match(
                r"^A\s*[.):]",
                lines[1].strip(),
            ):
                continue

            matches.add(item["bbox"])

    if len(matches) != 1:
        return None

    rect = pymupdf.Rect(
        next(iter(matches))
    )

    if (
        not 12 <= rect.height <= 90
        or rect.width < 100
        or rect.width * rect.height > 60000
    ):
        return None

    return rect


def _has_rule(
    page,
    rect,
    start,
    end,
    *,
    unique_in_region=False,
):
    """
    Verify isolated horizontal ink on the original PDF.

    With unwarped hints, additionally reject competing
    long horizontal rules throughout the inspected band.
    """

    pix = page.get_pixmap(
        matrix=pymupdf.Matrix(3, 3),
        clip=rect,
        colorspace=pymupdf.csGRAY,
        alpha=False,
    )

    gray = np.frombuffer(
        pix.samples,
        np.uint8,
    ).reshape(
        pix.height,
        pix.width,
    )

    h, w = gray.shape

    if (
        min(h, w) < 32
        or w * h > 600000
    ):
        return False

    mask = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        12,
    )

    gap = (end - start) * w

    kernel = max(
        12,
        round(.48 * gap),
    )

    if kernel >= w:
        return False

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
    plausible = []

    for x, y, width, height, area in stats[1:]:

        if (
            .25 * h <= y <= .65 * h
            and width >= .65 * gap
            and 1 <= height <= .12 * h
            and area >= .35 * width * height
        ):
            plausible.append(
                (x, y, width, height)
            )

        if not (
            .25 * h <= y <= .65 * h
            and .65 * gap <= width <= 1.35 * gap
            and 1 <= height <= .12 * h
            and area >= .35 * width * height
            and abs(x / w - start) <= .055
            and abs((x + width) / w - end) <= .055
        ):
            continue

        candidates.append(
            (x, y, width, height)
        )

    if (
        unique_in_region
        and len(plausible) != 1
    ):
        return False

    if len(candidates) != 1:
        return False

    x, y, width, _ = candidates[0]

    region = (
        slice(
            max(0, y - round(.24 * h)),
            max(0, y - 2),
        ),
        slice(
            x + round(.12 * width),
            x + width - round(.12 * width),
        ),
    )

    inner = mask[region]
    pixels = gray[region]

    return bool(
        inner.size
        and np.count_nonzero(inner) / inner.size <= .035
        and np.mean(pixels < 100) <= .035
    )


def recover_scan_blanks(
    pdf_path,
    primary_pages,
    questions,
    *,
    geometry_page_for,
):
    """Review-only repairs before asset creation; no OCR or answer writes."""

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

                n = q.get(
                    "source_question_number",
                    q.get("question_id"),
                )

                p = q.get("page_number")

                if (
                    q.get("type") != "mcp"
                    or q.get("multiple_choice") is not False
                    or type(n) is not int
                    or n < 1
                    or type(p) is not int
                    or not 1 <= p <= len(doc)
                    or counts[(p, n)] != 1
                    or not INSTRUCTION.search(
                        str(q.get("section_instruction") or "")
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

                blocks = q.get("question_content")

                if not isinstance(blocks, list):
                    continue

                context = [
                    q.get(k, "")
                    for k in (
                        "section_instruction",
                        "shared_passage",
                    )
                    if q.get(k, "")
                ]

                if len(blocks) == 1:
                    stem_block = blocks[0]

                elif (
                    len(blocks) == len(context) + 1
                    and context
                    and all(
                        isinstance(b, dict)
                        and b.get("type") == "text"
                        and b.get("value") == v
                        for b, v in zip(blocks, context)
                    )
                ):
                    stem_block = blocks[-1]

                else:
                    continue

                if not isinstance(stem_block, dict):
                    continue

                kind = stem_block.get("type")

                if kind not in ("text", "latex"):
                    continue

                stem = stem_block.get("value")

                if (
                    not isinstance(stem, str)
                    or "\n" in stem
                ):
                    continue

                marked = kind == "latex"

                expected = (
                    _marked_parts(stem)
                    if marked
                    else None
                )

                if marked and expected is None:
                    continue

                if not marked and "_" in stem:
                    continue

                page = doc[p - 1]
                primary = primary_pages[p - 1]

                if (
                    page.rotation
                    or page.get_text().strip()
                    or not page.get_images()
                    or not isinstance(primary, dict)
                ):
                    continue

                other = geometry_page_for(primary)

                if (
                    not isinstance(other, dict)
                    or other is primary
                    or primary.get("source_page_number", p) != p
                    or other.get("source_page_number", p) != p
                ):
                    continue

                root = other.get("pruned_result")

                if not isinstance(root, dict):
                    continue

                mode = "legacy"

                if marked:

                    mode = _geometry_mode(
                        other,
                        root,
                    )

                    if mode is None:

                        logger.info(
                            "ocr_blank_recovery:geometry_rejected "
                            "page=%d question=%d",
                            p,
                            n,
                        )

                        continue

                split = _split(
                    root,
                    n,
                    marked=marked,
                )

                if split is None:
                    continue

                left, right, start, end = split

                if marked:

                    if expected != (left, right):
                        continue

                elif _text(stem) not in (
                    left + " " + right,
                    left + ". " + right,
                ):
                    continue

                rect = _region(
                    page,
                    primary,
                    n,
                    _text(stem),
                )

                if rect is None:
                    continue

                if mode == "unwarped_hint":

                    verified = _has_rule(
                        page,
                        rect,
                        start,
                        end,
                        unique_in_region=True,
                    )

                else:

                    verified = _has_rule(
                        page,
                        rect,
                        start,
                        end,
                    )

                if not verified:

                    logger.info(
                        "ocr_blank_recovery:rule_rejected "
                        "page=%d question=%d mode=%s",
                        p,
                        n,
                        mode,
                    )

                    continue

                stem_block["value"] = (
                    left + " ________ " + right
                )

                if marked:
                    stem_block["type"] = "text"

                q["requires_source_image"] = True
                q["parse_fallback"] = True

                prior = str(
                    q.get("parser_warning") or ""
                )

                warnings = [
                    WARNING,
                ]

                if marked:
                    warnings.append(MARKUP_WARNING)

                if mode == "unwarped_hint":
                    warnings.append(UNWARPING_WARNING)

                q["parser_warning"] = " ".join(
                    filter(
                        None,
                        (
                            prior,
                            *warnings,
                        ),
                    )
                )

                repairs.append({
                    "page_number": p,
                    "source_question_number": n,
                })

                logger.info(
                    "ocr_blank_recovery:repair "
                    "page=%d question=%d mode=%s",
                    p,
                    n,
                    mode,
                )

    except (
        OSError,
        ValueError,
        TypeError,
        RuntimeError,
        cv2.error,
    ) as exc:

        logger.info(
            "ocr_blank_recovery:unavailable type=%s",
            type(exc).__name__,
        )

    return repairs