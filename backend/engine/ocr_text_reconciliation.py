"""Conservative text-only repairs for scanned pronunciation choices."""
from __future__ import annotations

import logging
import math
import re
from collections import Counter

import pymupdf

logger = logging.getLogger(__name__)

INSTRUCTION = re.compile(
    r"\bunderlined\s+part\b.*?\b(?:pronounced|pronunciation)\b",
    re.I | re.S,
)

HEADING = re.compile(
    r"^\s*(?:Question|Câu|Bài)\s*(\d+)"
    r"(?=\b|[A-H][.):])\s*[.):]?\s*(.*)$",
    re.I,
)

OPTION = re.compile(
    r"^([A-H])(?:[.):]\s*|\s+)(.+)$"
)

SPLIT_WORD = re.compile(
    r"([A-Za-z]{2,})/([A-Za-z]{1,4})"
)

WARNING = (
    "Câu phát âm từ PDF scan: cần đối chiếu "
    "phần gạch chân với ảnh gốc."
)


def _row(root, number, labels):

    arrays = [
        root.get(k)
        for k in (
            "rec_texts",
            "rec_boxes",
            "rec_scores",
        )
    ]

    if not all(isinstance(a, list) for a in arrays):
        return None

    if (
        not 0 < len(arrays[0]) <= 5000
        or len({len(a) for a in arrays}) != 1
    ):
        return None

    entries = []

    for text, box, score in zip(*arrays):

        if not isinstance(text, str):
            return None

        match = HEADING.match(text)

        entries.append(
            (match, text.strip(), box, score)
        )

    starts = [
        i
        for i, e in enumerate(entries)
        if e[0] and int(e[0].group(1)) == number
    ]

    if len(starts) != 1:
        return None

    row = {}

    for i in range(starts[0], len(entries)):

        heading, text, box, score = entries[i]

        if i == starts[0]:

            text = heading.group(2).strip()

        elif heading:

            break

        match = OPTION.fullmatch(text)

        if not match:
            continue

        label, value = match.groups()

        if label not in labels or label in row:
            return None

        if (
            not isinstance(box, (tuple, list))
            or len(box) != 4
            or any(
                type(v) not in (int, float)
                or not math.isfinite(v)
                for v in box
            )
            or not (
                0 <= box[0] < box[2]
                and 0 <= box[1] < box[3]
            )
            or type(score) not in (int, float)
            or not 0 <= score <= 1
        ):
            return None

        row[label] = (
            value.strip(),
            box,
            score,
        )

    if set(row) != set(labels):
        return None

    boxes = [
        row[label][1]
        for label in labels
    ]

    if any(
        a[2] > b[0]
        for a, b in zip(boxes, boxes[1:])
    ):
        return None

    if max(b[1] for b in boxes) >= min(
        b[3] for b in boxes
    ):
        return None

    return row


def reconcile_scan_choices(
    pdf_path,
    primary_pages,
    questions,
    *,
    geometry_page_for,
):
    """Mutate only verified option text and conservative review flags.

    Geometry resolver is the upload-local registered OCR context.
    Caller holds PDF_LOCK. No provider calls or answer updates.
    """

    repairs = []

    try:

        with pymupdf.open(pdf_path) as doc:

            if len(primary_pages) != len(doc):
                return repairs

            scanned = {
                i
                for i, page in enumerate(doc, 1)
                if not page.rotation
                and not page.get_text().strip()
            }

    except (
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
    ):
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

    for q in questions:

        page = q.get("page_number")

        number = q.get(
            "source_question_number",
            q.get("question_id"),
        )

        if (
            type(page) is not int
            or page not in scanned
            or not INSTRUCTION.search(
                str(q.get("section_instruction") or "")
            )
        ):
            continue

        # Text agreement does not verify the original underline.

        q["requires_source_image"] = True
        q["parse_fallback"] = True

        if not q.get("parser_warning"):
            q["parser_warning"] = WARNING

        if (
            q.get("type") != "mcp"
            or q.get("multiple_choice") is not False
            or type(number) is not int
            or number < 1
            or counts[(page, number)] != 1
        ):
            continue

        options = q.get("mcp_options")

        if (
            not isinstance(options, list)
            or not all(
                isinstance(o, dict)
                for o in options
            )
        ):
            continue

        labels = [
            o.get("label")
            for o in options
        ]

        if labels != list("ABCD"):
            continue

        primary = primary_pages[page - 1]

        if not isinstance(primary, dict):
            continue

        secondary = geometry_page_for(primary)

        if (
            not isinstance(secondary, dict)
            or secondary is primary
        ):
            continue

        # No independent second OCR observation.

        if (
            primary.get("source_page_number", page) != page
            or secondary.get("source_page_number", page) != page
        ):
            continue

        root = secondary.get("pruned_result")

        if not isinstance(root, dict):
            continue

        row = _row(root, number, labels)

        if row is None:
            continue

        proposed = []

        for o in options:

            blocks = o.get("content")

            if (
                not isinstance(blocks, list)
                or len(blocks) != 1
                or not isinstance(blocks[0], dict)
                or blocks[0].get("type") != "text"
            ):
                continue

            value = blocks[0].get("value")

            match = (
                SPLIT_WORD.fullmatch(value)
                if isinstance(value, str)
                else None
            )

            other, _, score = row[o["label"]]

            if (
                match
                and score >= .95
                and other == "".join(match.groups())
            ):
                proposed.append(
                    (o, other, match.group(2))
                )

        # A repeated split suffix in a pronunciation row is stronger
        # than a single slash disagreement. Never rewrite other words.

        if (
            len(proposed) < 2
            or len({s for _, _, s in proposed}) != 1
        ):
            continue

        for option, text, _ in proposed:

            option["content"][0]["value"] = text

        repair = {
            "page_number": page,
            "source_question_number": number,
            "changed_options": [
                o["label"]
                for o, _, _ in proposed
            ],
        }

        repairs.append(repair)

        logger.info(
            "ocr_text_reconciliation:repair "
            "page=%d question=%d labels=%s",
            page,
            number,
            repair["changed_options"],
        )

    return repairs