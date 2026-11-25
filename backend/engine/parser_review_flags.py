"""Clear review warnings only for PDF-verified correction questions."""
from __future__ import annotations

import re
from typing import Any

LOW_LINE = "\u0332"

CORRECTION_INSTRUCTION = re.compile(
    r"underlined\s+part\s+that\s+needs\s+correction",
    re.IGNORECASE,
)


def _content_text(blocks: Any) -> str:
    if not isinstance(blocks, list):
        return ""

    return "".join(
        str(block.get("value", ""))
        for block in blocks
        if isinstance(block, dict)
    )


def _underlined_words(text: str) -> list[str]:
    """Extract consecutive letters carrying U+0332."""
    runs: list[str] = []
    current: list[str] = []

    index = 0

    while index < len(text):
        if (
            index + 1 < len(text)
            and text[index].isalpha()
            and text[index + 1] == LOW_LINE
        ):
            current.append(text[index])
            index += 2
        else:
            if current:
                runs.append("".join(current))
                current = []

            index += 1

    if current:
        runs.append("".join(current))

    return runs


def clear_verified_correction_warnings(
    questions: list[dict[str, Any]],
    fidelity_repairs: list[dict[str, Any]],
) -> list[int]:
    """Clear review flags only when PDF geometry and choices agree.

    OCR-only results, incomplete options and uncertain questions
    retain their original warnings.
    """
    verified = {
        (
            repair.get("page_number"),
            repair.get("question_number"),
        )
        for repair in fidelity_repairs
        if (
            isinstance(repair, dict)
            and repair.get("kind")
            == "underlined_error_markers"
            and repair.get("source")
            == "pdf_text_geometry"
        )
    }

    cleared: list[int] = []

    for question in questions:
        key = (
            question.get("page_number"),
            question.get("question_id"),
        )

        if key not in verified:
            continue

        if (
            question.get("type") != "mcp"
            or question.get("parser_warning")
        ):
            continue

        if not CORRECTION_INSTRUCTION.search(
            str(
                question.get(
                    "section_instruction"
                ) or ""
            )
        ):
            continue

        options = question.get("mcp_options")

        if (
            not isinstance(options, list)
            or len(options) != 4
            or any(
                not isinstance(choice, dict)
                for choice in options
            )
            or [
                choice.get("label")
                for choice in options
            ] != list("ABCD")
        ):
            continue

        marked = _underlined_words(
            _content_text(
                question.get("question_content")
            )
        )

        choices = [
            _content_text(
                choice.get("content")
            ).strip()
            for choice in options
        ]

        if (
            len(marked) != 4
            or any(not value for value in choices)
            or [
                word.casefold()
                for word in marked
            ] != [
                value.casefold()
                for value in choices
            ]
        ):
            continue

        question["requires_source_image"] = False
        question["parse_fallback"] = False

        cleared.append(
            int(question["question_id"])
        )

    return cleared
