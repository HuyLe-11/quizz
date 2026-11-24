"""Conservative cleanup of PDF-backed OCR text before parsing.

Correct pronunciation choices only when all four are verifiable
against PDF text and vector underlines. Remove unwanted physical
line wraps in error-correction stems. No OCR or database calls.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

from .pdf_fidelity import LOW_LINE, _page_rows

logger = logging.getLogger("quizpdf.parser_normalizer")

HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*"
    r"(?:Question|Câu|Cau|Q)\s*(\d{1,4})\s*[.:)\-]\s*",
    re.I,
)

SECTION_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*"
    r"(?:Mark\s+the\s+letter|"
    r"Read\s+the\s+(?:following\s+)?(?:passage|text)|"
    r"Choose\s+the|Select\s+the|Identify\s+the)",
    re.I,
)

OPTION_RE = re.compile(
    r"(?<![\w])([A-D])[.):]\s+",
    re.I,
)

PRONUNCIATION_RE = re.compile(
    r"word\s+whose\s+underlined\s+part\s+differs",
    re.I,
)

CORRECTION_RE = re.compile(
    r"underlined\s+part\s+that\s+needs\s+correction",
    re.I,
)


def _options(text: str) -> dict[str, str]:
    matches = list(OPTION_RE.finditer(text))
    found: dict[str, str] = {}

    for index, match in enumerate(matches):
        label = match.group(1).upper()

        if label in found:
            return {}

        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(text)
        )

        found[label] = text[match.end():end].strip()

    return found


def _bare(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(LOW_LINE, "")

    text = re.sub(
        r"</?u>|\*\*|__",
        "",
        text,
        flags=re.I,
    )

    return "".join(
        char for char in text.casefold()
        if char.isalnum()
    )


def _similar(original: str, source: str) -> bool:
    left = _bare(original)
    right = _bare(source)

    if not left or not right:
        return False

    if left == right:
        return True

    return (
        len(left) >= 3
        and len(right) >= 3
        and SequenceMatcher(
            None,
            left,
            right,
            autojunk=False,
        ).ratio() >= 0.83
    )


def _source_pronunciation_options(
    page: Any,
) -> dict[int, str]:
    """Find one PDF row with four independently underlined options."""
    result: dict[int, str] = {}

    rows, _ = _page_rows(page)

    for row in rows:
        found = HEADING_RE.match(row["text"])

        if not found:
            continue

        number = int(found.group(1))
        raw = row["text"][found.end():]
        rich = row["rich"]

        # Only replace simple pronunciation questions consisting
        # of four choices. Never overwrite a substantial stem.
        if not OPTION_RE.match(raw):
            continue

        plain_choices = _options(row["text"])
        rich_choices = _options(rich)

        if (
            list(plain_choices) != list("ABCD")
            or list(rich_choices) != list("ABCD")
        ):
            continue

        if any(
            LOW_LINE not in rich_choices[label]
            for label in "ABCD"
        ):
            continue

        if number in result:
            # Duplicated numbering is ambiguous.
            result[number] = ""
            continue

        result[number] = rich

    return {
        number: rich
        for number, rich in result.items()
        if rich
    }


def _find_blocks(
    lines: list[str],
) -> list[tuple[int, int, int, str]]:
    positions = []

    for index, line in enumerate(lines):
        found = HEADING_RE.match(line)

        if found:
            positions.append(
                (index, int(found.group(1)))
            )

    blocks = []

    for index, (start, number) in enumerate(positions):
        end = (
            positions[index + 1][0]
            if index + 1 < len(positions)
            else len(lines)
        )

        for position in range(start + 1, end):
            if SECTION_RE.match(lines[position]):
                end = position
                break

        previous_sections = [
            position
            for position in range(start)
            if SECTION_RE.match(lines[position])
        ]

        instruction = (
            "\n".join(
                lines[previous_sections[-1]:start]
            )
            if previous_sections
            else ""
        )

        blocks.append(
            (start, end, number, instruction)
        )

    return blocks


def normalize_pdf_backed_ocr(
    pdf_contents: bytes,
    page_texts: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Normalize verified text before the main question parser.

    Corrections are conservative:
    - every source pronunciation option must have an underline;
    - all four OCR choices must approximately match the source;
    - unrelated question text is never replaced;
    - error-correction stems may lose forced PDF line breaks,
      but their answer-option boundaries are preserved.
    """
    result = list(page_texts)
    repairs: list[dict[str, Any]] = []

    if not pdf_contents.startswith(b"%PDF-"):
        return result, repairs

    try:
        document = pymupdf.open(
            stream=pdf_contents,
            filetype="pdf",
        )
    except (RuntimeError, ValueError, TypeError):
        return result, repairs

    with document:
        for page_index, original_text in enumerate(page_texts):
            if (
                page_index >= len(document)
                or not original_text.strip()
            ):
                continue

            source_options = _source_pronunciation_options(
                document[page_index]
            )

            lines = original_text.splitlines()
            patches = []

            for start, end, number, instruction in _find_blocks(
                lines
            ):
                block = "\n".join(lines[start:end])

                if (
                    PRONUNCIATION_RE.search(instruction)
                    and number in source_options
                ):
                    pdf_line = source_options[number]
                    pdf_choices = _options(pdf_line)
                    ocr_choices = _options(block)

                    if (
                        list(ocr_choices) == list("ABCD")
                        and all(
                            _similar(
                                ocr_choices[label],
                                pdf_choices[label],
                            )
                            for label in "ABCD"
                        )
                    ):
                        patches.append(
                            (
                                start,
                                end,
                                [pdf_line],
                                number,
                                "pronunciation_choices",
                            )
                        )
                        continue

                if CORRECTION_RE.search(instruction):
                    option_matches = list(
                        OPTION_RE.finditer(block)
                    )

                    labels = [
                        match.group(1).upper()
                        for match in option_matches
                    ]

                    if labels != list("ABCD"):
                        continue

                    before_options = block[
                        :option_matches[0].start()
                    ]

                    clean = re.sub(
                        r"\s*\n\s*",
                        " ",
                        before_options,
                    ).strip()

                    remaining = block[
                        option_matches[0].start():
                    ].strip()

                    if (
                        clean
                        and clean != before_options.strip()
                    ):
                        replacement = (
                            clean + "\n" + remaining
                        ).splitlines()

                        patches.append(
                            (
                                start,
                                end,
                                replacement,
                                number,
                                "unwrap_correction_stem",
                            )
                        )

            for (
                start,
                end,
                replacement,
                number,
                kind,
            ) in reversed(patches):
                lines[start:end] = replacement

                repairs.append({
                    "page_number": page_index + 1,
                    "question_number": number,
                    "kind": kind,
                })

            result[page_index] = "\n".join(lines)

    repairs.sort(
        key=lambda item: (
            item["page_number"],
            item["question_number"],
        )
    )

    if repairs:
        logger.info(
            "parser=text_normalize repairs=%s",
            repairs,
        )

    return result, repairs