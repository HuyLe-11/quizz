"""Conservative PDF text fidelity repairs for the QuizPDF Parser Preview.

Read-only: use glyph bounding boxes and genuine PDF underline strokes to
repair OCR output. Does not call PaddleOCR or access any database.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

logger = logging.getLogger("quizpdf.pdf_fidelity")
LOW_LINE = "\u0332"

HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*"
    r"(?:Question|Câu|Cau|Q)\s*(\d{1,4})\s*[.:)\-]?\s*",
    re.I,
)

SECTION = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*"
    r"(?:Mark\s+the\s+letter|Read\s+the\s+"
    r"(?:following\s+)?(?:passage|text)|"
    r"Choose\s+the\s+(?:best|correct)|"
    r"Select\s+the\s+(?:best|correct)|"
    r"Identify\s+the|Chọn\s+đáp\s+án|Đọc\s+đoạn)\b",
    re.I,
)

OPTION = re.compile(
    r"(?<![\w])([A-D])[.):]\s+",
    re.I,
)

PRONUNCIATION = re.compile(
    r"word\s+whose\s+underlined\s+part\s+differs",
    re.I,
)

CORRECTION = re.compile(
    r"underlined\s+part\s+that\s+needs\s+correction",
    re.I,
)


@dataclass(frozen=True)
class FidelityRepair:
    page_number: int
    question_number: int
    kind: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "question_number": self.question_number,
            "kind": self.kind,
            "source": "pdf_text_geometry",
        }


def _normalized(text: str) -> str:
    text = unicodedata.normalize(
        "NFKC", text
    ).replace(LOW_LINE, "")

    text = re.sub(
        r"</?u>",
        "",
        text,
        flags=re.I,
    )

    return "".join(
        re.findall(
            r"\w+",
            text.casefold(),
        )
    )


def _underline_rectangles(page: Any) -> list[Any]:
    """Read thin vector fills or strokes, not thick decorative lines."""
    result: list[Any] = []

    for drawing in page.get_drawings():
        bounds = drawing.get("rect")

        if (
            drawing.get("type") in {"f", "fs"}
            and bounds is not None
            and 2 <= bounds.width <= page.rect.width * 0.6
            and 0 < bounds.height <= 2.5
        ):
            result.append(
                pymupdf.Rect(bounds)
            )

        if "s" in str(
            drawing.get("type", "")
        ):
            for item in drawing.get("items", []):
                if not item or item[0] != "l":
                    continue

                a, b = item[1:3]

                if abs(a.y - b.y) > 0.8:
                    continue

                left, right = sorted(
                    (a.x, b.x)
                )

                if (
                    2 <= right - left
                    <= page.rect.width * 0.6
                ):
                    result.append(
                        pymupdf.Rect(
                            left,
                            a.y - 0.2,
                            right,
                            a.y + 0.2,
                        )
                    )

    unique = {}

    for rect in result:
        key = tuple(
            round(value, 2)
            for value in rect
        )
        unique[key] = rect

    return list(unique.values())


def _page_rows(
    page: Any,
) -> tuple[list[dict], list[dict]]:
    """Build physical rows and underline fragments from PDF glyphs."""
    underlines = _underline_rectangles(page)

    parts: list[dict] = []
    glyphs: list[dict] = []

    for block in page.get_text("rawdict").get("blocks", []):
        if block.get("type") != 0:
            continue

        for line in block.get("lines", []):
            chars = [
                char
                for span in line.get("spans", [])
                for char in span.get("chars", [])
            ]

            if not chars:
                continue

            bounds = pymupdf.Rect(
                line["bbox"]
            )

            plain = "".join(
                char["c"]
                for char in chars
            ).strip()

            if not plain:
                continue

            rich_parts = []

            for char in chars:
                mark = ""

                if not char["c"].isspace():
                    box = pymupdf.Rect(
                        char["bbox"]
                    )

                    midpoint = (
                        box.x0 + box.x1
                    ) / 2

                    if any(
                        abs(
                            rect.y0 - box.y1
                        ) <= 3.5
                        and (
                            rect.x0 - 0.4
                            <= midpoint
                            <= rect.x1 + 0.4
                        )
                        for rect in underlines
                    ):
                        mark = LOW_LINE

                rich_parts.append(
                    char["c"] + mark
                )
                glyphs.append(char)

            parts.append({
                "text": plain,
                "rich": "".join(
                    rich_parts
                ).strip(),
                "x": bounds.x0,
                "y": bounds.y0,
                "rect": bounds,
            })

    parts.sort(
        key=lambda item: (
            item["y"],
            item["x"],
        )
    )

    rows: list[dict] = []

    for part in parts:
        if (
            rows
            and abs(
                rows[-1]["y"]
                - part["y"]
            ) <= 1.3
        ):
            rows[-1]["lines"].append(
                part
            )
        else:
            rows.append({
                "y": part["y"],
                "lines": [part],
            })

    for row in rows:
        row["lines"].sort(
            key=lambda part: part["x"]
        )

        row["text"] = "  ".join(
            part["text"]
            for part in row["lines"]
        )

        row["rich"] = "  ".join(
            part["rich"]
            for part in row["lines"]
        )

    fragments: list[dict] = []

    for index, rect in enumerate(
        underlines
    ):
        matching = []

        for char in glyphs:
            if char["c"].isspace():
                continue

            box = pymupdf.Rect(
                char["bbox"]
            )

            midpoint = (
                box.x0 + box.x1
            ) / 2

            if (
                abs(
                    rect.y0 - box.y1
                ) <= 3.5
                and (
                    rect.x0 - 0.4
                    <= midpoint
                    <= rect.x1 + 0.4
                )
            ):
                matching.append(
                    (
                        box.x0,
                        char["c"],
                    )
                )

        if matching:
            matching.sort()

            fragments.append({
                "id": index,
                "rect": rect,
                "text": "".join(
                    char
                    for _, char in matching
                ),
            })

    return rows, fragments


def _source_question_rows(
    rows: list[dict],
) -> dict[int, list[dict]]:
    positions = [
        (
            index,
            int(found.group(1)),
        )
        for index, row in enumerate(rows)
        if (
            found := HEADING.match(
                row["text"]
            )
        ) is not None
    ]

    groups: dict[int, list[dict]] = {}

    for index, (start, number) in enumerate(
        positions
    ):
        end = (
            positions[index + 1][0]
            if index + 1 < len(positions)
            else len(rows)
        )

        for candidate in range(
            start + 1,
            end,
        ):
            if SECTION.match(
                rows[candidate]["text"]
            ):
                end = candidate
                break

        if number in groups:
            # Duplicate question numbers are ambiguous.
            groups[number] = []
        else:
            groups[number] = rows[
                start:end
            ]

    return groups


def _ocr_blocks(
    text: str,
) -> tuple[
    list[str],
    dict[int, tuple[int, int]],
]:
    lines = text.splitlines()

    positions = [
        (
            index,
            int(match.group(1)),
        )
        for index, line in enumerate(lines)
        if (
            match := HEADING.match(line)
        ) is not None
    ]

    blocks = {}

    for index, (start, number) in enumerate(
        positions
    ):
        end = (
            positions[index + 1][0]
            if index + 1 < len(positions)
            else len(lines)
        )

        for candidate in range(
            start + 1,
            end,
        ):
            if SECTION.match(
                lines[candidate]
            ):
                end = candidate
                break

        blocks[number] = (
            (start, end)
            if number not in blocks
            else (-1, -1)
        )

    return lines, blocks


def _choices(text: str) -> dict[str, str]:
    matches = list(
        OPTION.finditer(text)
    )
    result: dict[str, str] = {}

    for index, match in enumerate(
        matches
    ):
        label = match.group(1).upper()

        if label in result:
            return {}

        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(text)
        )

        result[label] = text[
            match.end():end
        ].strip()

    return result


def _correction_choices(
    page: Any,
    rows: list[dict],
    fragments: list[dict],
) -> dict[str, str]:
    """Match labels A-D to underlines, even across multiple text rows."""
    if not rows:
        return {}

    first_y = rows[0]["y"]
    last_y = max(
        part["rect"].y1
        for row in rows
        for part in row["lines"]
    )

    words = [
        {
            "label": item[4].upper(),
            "rect": pymupdf.Rect(
                item[:4]
            ),
        }
        for item in page.get_text("words")
        if (
            re.fullmatch(
                r"[A-D]",
                item[4],
                re.I,
            )
            and first_y <= item[1] <= last_y + 1
        )
    ]

    # A-D can lie on different rows:
    # Q47: A/B/C below line one, D below line two.
    candidates_by_label: dict[str, list[dict]] = {
        key: []
        for key in "ABCD"
    }

    for marker in words:
        center = (
            marker["rect"].x0
            + marker["rect"].x1
        ) / 2

        matches = [
            fragment
            for fragment in fragments
            if (
                first_y
                <= fragment["rect"].y0
                <= last_y
                and -1
                <= (
                    marker["rect"].y0
                    - fragment["rect"].y1
                )
                <= 12
                and (
                    fragment["rect"].x0 - 5
                    <= center
                    <= fragment["rect"].x1 + 5
                )
            )
        ]

        if len(matches) == 1:
            candidates_by_label[
                marker["label"]
            ].append(matches[0])

    if any(
        len(candidates_by_label[label]) != 1
        for label in "ABCD"
    ):
        return {}

    selected = [
        candidates_by_label[label][0]
        for label in "ABCD"
    ]

    if len({
        fragment["id"]
        for fragment in selected
    }) != 4:
        return {}

    return {
        label: candidates_by_label[
            label
        ][0]["text"]
        for label in "ABCD"
    }


def _rich_rows(
    rows: list[dict],
    *,
    remove_markers: bool = False,
) -> str:
    selected = []

    for row in rows:
        if remove_markers:
            tokens = row["text"].split()

            # Q45/46: A B C D on a row.
            # Q47: A B C on one row, D on another.
            if (
                tokens
                and all(
                    len(token) == 1
                    and token in "ABCD"
                    for token in tokens
                )
                and len(tokens)
                == len(set(tokens))
            ):
                continue

        selected.append(
            row["rich"]
        )

    return "\n".join(selected)


def enhance_ocr_with_pdf_geometry(
    pdf_contents: bytes,
    page_texts: list[str],
) -> tuple[
    list[str],
    list[FidelityRepair],
]:
    """Repair OCR only when source PDF geometry supports the changes."""
    result = list(page_texts)
    repairs: list[FidelityRepair] = []

    if not pdf_contents.startswith(
        b"%PDF-"
    ):
        return result, repairs

    try:
        document = pymupdf.open(
            stream=pdf_contents,
            filetype="pdf",
        )
    except (
        RuntimeError,
        ValueError,
        TypeError,
    ):
        return result, repairs

    with document:
        for page_index, text in enumerate(
            page_texts
        ):
            if (
                page_index >= len(document)
                or not text.strip()
            ):
                continue

            page = document[
                page_index
            ]

            rows, fragments = _page_rows(
                page
            )

            if not rows:
                continue

            sources = _source_question_rows(
                rows
            )

            ocr_lines, ocr_blocks = (
                _ocr_blocks(text)
            )

            patches: list[
                tuple[
                    int,
                    int,
                    str,
                    FidelityRepair,
                ]
            ] = []

            for number, (start, end) in (
                ocr_blocks.items()
            ):
                if (
                    start < 0
                    or not sources.get(number)
                ):
                    continue

                source_rows = sources[
                    number
                ]

                ocr_text = "\n".join(
                    ocr_lines[start:end]
                )

                source_text = "\n".join(
                    row["text"]
                    for row in source_rows
                )

                instruction_start = next(
                    (
                        index
                        for index in range(
                            start - 1,
                            -1,
                            -1,
                        )
                        if SECTION.match(
                            ocr_lines[index]
                        )
                    ),
                    None,
                )

                section = (
                    "\n".join(
                        ocr_lines[
                            instruction_start:start
                        ]
                    )
                    if instruction_start
                    is not None
                    else ""
                )

                # Pronunciation: recover exact underlined letters.
                if PRONUNCIATION.search(
                    section
                ):
                    source_options = _choices(
                        source_text
                    )

                    current_options = _choices(
                        ocr_text
                    )

                    rich = _rich_rows(
                        source_rows
                    )

                    rich_options = _choices(
                        rich
                    )

                    if (
                        list(source_options)
                        == list("ABCD")
                        and list(current_options)
                        == list("ABCD")
                        and list(rich_options)
                        == list("ABCD")
                        and all(
                            _normalized(
                                source_options[label]
                            )
                            == _normalized(
                                current_options[label]
                            )
                            for label in "ABCD"
                        )
                        and all(
                            LOW_LINE
                            in rich_options[label]
                            for label in "ABCD"
                        )
                    ):
                        patches.append((
                            start,
                            end,
                            rich,
                            FidelityRepair(
                                page_index + 1,
                                number,
                                "pronunciation_underlines",
                            ),
                        ))
                        continue

                # Error correction: map A-D by spatial location.
                if CORRECTION.search(
                    section
                ):
                    choices = _correction_choices(
                        page,
                        source_rows,
                        fragments,
                    )

                    if (
                        list(choices)
                        == list("ABCD")
                    ):
                        rich = _rich_rows(
                            source_rows,
                            remove_markers=True,
                        )

                        rich += "\n" + "\n".join(
                            f"{label}. {choices[label]}"
                            for label in "ABCD"
                        )

                        patches.append((
                            start,
                            end,
                            rich,
                            FidelityRepair(
                                page_index + 1,
                                number,
                                "underlined_error_markers",
                            ),
                        ))
                        continue

                # Missing D: only restore when A-C match PDF.
                source_options = _choices(
                    source_text
                )

                current_options = _choices(
                    ocr_text
                )

                if (
                    list(source_options)
                    == list("ABCD")
                    and list(current_options)
                    == list("ABC")
                    and all(
                        _normalized(
                            source_options[label]
                        )
                        == _normalized(
                            current_options[label]
                        )
                        for label in "ABC"
                    )
                ):
                    patches.append((
                        start,
                        end,
                        (
                            ocr_text.rstrip()
                            + "\nD. "
                            + source_options["D"]
                        ),
                        FidelityRepair(
                            page_index + 1,
                            number,
                            "missing_option_D",
                        ),
                    ))

            # Apply backwards to preserve original line indices.
            for (
                start,
                end,
                replacement,
                repair,
            ) in reversed(patches):
                ocr_lines[start:end] = (
                    replacement.splitlines()
                )

                repairs.append(repair)

            result[page_index] = "\n".join(
                ocr_lines
            )

    repairs.sort(
        key=lambda item: (
            item.page_number,
            item.question_number,
        )
    )

    if repairs:
        logger.info(
            "parser=pdf_fidelity repairs=%s",
            [
                repair.as_dict()
                for repair in repairs
            ],
        )

    return result, repairs
