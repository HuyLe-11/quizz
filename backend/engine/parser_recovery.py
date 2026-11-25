"""Restore verified OCR question headings from a searchable source PDF.

Read-only preprocessing for Parser Preview. Never invent missing words.
Only insert an exact PDF heading when its first following content line
uniquely matches the OCR text in the expected question interval.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

logger = logging.getLogger("quizpdf.parser_recovery")

HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*"
    r"(?:Question|Câu|Cau|Q)\s*(\d{1,4})\s*[.:)\-]\s*"
    r"(.+?)\s*(?:\*\*|__)?\s*$",
    re.IGNORECASE,
)

BARE_HEADING = re.compile(
    r"\b(?:Question|Câu|Cau|Q)\s*(\d{1,4})\s*[.:)\-]",
    re.IGNORECASE,
)

CHOICE = re.compile(
    r"^\s*[A-H][.):]\s+",
    re.IGNORECASE,
)

SECTION = re.compile(
    r"^\s*(?:Mark the letter|Read the following|"
    r"Choose the|Đọc đoạn|Chọn đáp án)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RecoveredHeading:
    page_number: int
    question_number: int
    heading: str
    matched_next_line: str

    def as_dict(self) -> dict:
        return {
            "page_number": self.page_number,
            "question_number": self.question_number,
            "heading": self.heading,
            "matched_next_line": self.matched_next_line,
            "source": "pdf_text_layer",
        }


def _normalized(text: str) -> str:
    text = unicodedata.normalize(
        "NFKC", text
    ).casefold()

    return " ".join(
        re.findall(r"\w+", text)
    )


def _is_anchor_candidate(line: str) -> bool:
    normalized = _normalized(line)

    return (
        len(normalized) >= 17
        and len(normalized.split()) >= 4
        and HEADING.match(line) is None
        and CHOICE.match(line) is None
        and SECTION.match(line) is None
        and "tailieudieuky" not in normalized
    )


def _anchor_matches(
    source: str,
    ocr: str,
) -> bool:
    left = _normalized(source)
    right = _normalized(ocr)

    if not left or not right:
        return False

    if left == right:
        return True

    shorter, longer = sorted(
        (left, right),
        key=len,
    )

    if (
        len(shorter) >= 20
        and shorter in longer
        and len(shorter) / len(longer) > 0.65
    ):
        return True

    return (
        SequenceMatcher(
            None,
            left,
            right,
            autojunk=False,
        ).ratio() >= 0.87
    )


def _source_headings(
    lines: list[str],
) -> dict[int, tuple[int, str]]:
    found = {}
    duplicates = set()

    for index, line in enumerate(lines):
        match = HEADING.match(line)

        if not match:
            continue

        number = int(match.group(1))

        if number in found:
            duplicates.add(number)
        else:
            found[number] = (
                index,
                line.strip(),
            )

    return {
        number: data
        for number, data in found.items()
        if number not in duplicates
    }


def recover_pdf_headings(
    pdf_contents: bytes,
    page_texts: list[str],
) -> tuple[list[str], list[RecoveredHeading]]:
    """Augment OCR text only when PDF and OCR positions agree.

    Scanned PDFs, ambiguous matches and misaligned pages are left
    unchanged. The original caller-owned page_texts are not modified.
    """
    result = list(page_texts)
    recovered: list[RecoveredHeading] = []

    if not pdf_contents.startswith(b"%PDF-"):
        return result, recovered

    try:
        document = pymupdf.open(
            stream=pdf_contents,
            filetype="pdf",
        )
    except (
        RuntimeError,
        ValueError,
        TypeError,
    ) as exc:
        logger.warning(
            "parser=pdf_recovery status=skipped reason=%s",
            type(exc).__name__,
        )
        return result, recovered

    with document:
        for page_index, original_text in enumerate(
            page_texts
        ):
            if (
                page_index >= len(document)
                or not original_text.strip()
            ):
                continue

            source_lines = document[
                page_index
            ].get_text(
                "text",
                sort=True,
            ).splitlines()

            source = _source_headings(
                source_lines
            )

            if not source:
                continue

            output_lines = original_text.splitlines()

            present = {
                int(match.group(1))
                for line in output_lines
                if (
                    match := HEADING.match(line)
                )
            }

            # We need at least one recognized question
            # to align the page's OCR reading order.
            if not (
                present & source.keys()
            ):
                continue

            for number, (
                source_index,
                heading_text,
            ) in sorted(source.items()):

                if number in present:
                    continue

                # Do not restore an OCR heading that is
                # already present in a different layout.
                if any(
                    int(match.group(1)) == number
                    for line in output_lines
                    if (
                        match := BARE_HEADING.search(line)
                    )
                ):
                    continue

                first_anchor = None

                for candidate in source_lines[
                    source_index + 1:
                ]:
                    if (
                        HEADING.match(candidate)
                        or SECTION.match(candidate)
                    ):
                        break

                    if _is_anchor_candidate(
                        candidate
                    ):
                        first_anchor = (
                            candidate.strip()
                        )
                        break

                if first_anchor is None:
                    continue

                # Only use the first distinctive line.
                # Using a later line might leave content
                # attached to the previous option D.
                locations = [
                    index
                    for index, line in enumerate(
                        output_lines
                    )
                    if _anchor_matches(
                        first_anchor,
                        line,
                    )
                ]

                if len(locations) != 1:
                    continue

                anchor_index = locations[0]

                before = [
                    index
                    for index, line in enumerate(
                        output_lines[:anchor_index]
                    )
                    if (
                        match := HEADING.match(line)
                    )
                    and int(
                        match.group(1)
                    ) < number
                ]

                after = [
                    index
                    for index, line in enumerate(
                        output_lines[anchor_index:],
                        start=anchor_index,
                    )
                    if (
                        match := HEADING.match(line)
                    )
                    and int(
                        match.group(1)
                    ) > number
                ]

                if not before and not after:
                    continue

                nearest_before = max(
                    before,
                    default=-1,
                )
                nearest_after = min(
                    after,
                    default=len(output_lines),
                )

                if not (
                    nearest_before
                    < anchor_index
                    < nearest_after
                ):
                    continue

                output_lines.insert(
                    anchor_index,
                    heading_text,
                )

                present.add(number)

                recovered.append(
                    RecoveredHeading(
                        page_number=page_index + 1,
                        question_number=number,
                        heading=heading_text,
                        matched_next_line=first_anchor,
                    )
                )

            result[page_index] = "\n".join(
                output_lines
            )

    if recovered:
        logger.info(
            "parser=pdf_recovery status=completed "
            "recovered=%d numbers=%s",
            len(recovered),
            [
                item.question_number
                for item in recovered
            ],
        )

    return result, recovered
