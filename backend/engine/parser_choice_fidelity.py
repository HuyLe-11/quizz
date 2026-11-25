"""Verify final pronunciation choices against original PDF geometry.

Runs AFTER quiz_parser creates mcp_options. Uses verified PDF glyphs
to replace complete option strings, never concatenate OCR fragments.
No OCR, database, or network calls.
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

from .pdf_fidelity import LOW_LINE
from .parser_text_normalizer import (
    _options,
    _source_pronunciation_options,
)

logger = logging.getLogger("quizpdf.parser_choice_fidelity")

PRONUNCIATION_INSTRUCTION = re.compile(
    r"word\s+whose\s+underlined\s+part\s+differs",
    re.I,
)

LABELS = tuple("ABCD")


def _letters(value: str) -> str:
    r"""Extract visible letters for PDF comparison.

    PaddleOCR may produce `co $ \underline{oa} $st`.
    The OCR text can itself contain a duplicated character,
    so normalization is used for approximate verification,
    not as the corrected output.

    Unknown LaTeX commands are rejected instead of guessed.
    """
    text = unicodedata.normalize(
        "NFKC",
        value,
    ).replace(LOW_LINE, "")

    text = re.sub(
        r"</?u(?:\s[^>]*)?>",
        "",
        text,
        flags=re.I,
    )

    # Remove known formatting commands but retain their arguments.
    text = re.sub(
        r"\\(?:underline|text|mathrm|mathbf|textbf)\s*\{",
        "{",
        text,
        flags=re.I,
    )

    if re.search(r"\\[A-Za-z]+", text):
        return ""

    text = (
        text.replace("$", "")
        .replace("{", "")
        .replace("}", "")
        .replace("*", "")
        .replace("_", "")
    )

    return "".join(
        char
        for char in text.casefold()
        if char.isalpha()
    )


def _text(blocks: Any) -> str:
    if not isinstance(blocks, list):
        return ""

    return "".join(
        str(block.get("value", ""))
        for block in blocks
        if isinstance(block, dict)
    )


def _close_enough(
    observed: str,
    source: str,
) -> bool:
    left = _letters(observed)
    right = _letters(source)

    if not left or not right or len(right) < 3:
        return False

    if left == right:
        return True

    return SequenceMatcher(
        None,
        left,
        right,
        autojunk=False,
    ).ratio() >= 0.75


def reconcile_pronunciation_choices(
    pdf_contents: bytes,
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Correct final mcp_options with verified source PDF content.

    Requirements:
    - Question belongs to the pronunciation section.
    - PDF has a corresponding question with four underlined options.
    - All four OCR options approximately match source options.

    Questions without sufficient evidence are left unchanged.
    """
    repairs: list[dict[str, Any]] = []

    if not pdf_contents.startswith(b"%PDF-"):
        return repairs

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
        return repairs

    with document:
        source_by_page: dict[int, dict[int, str]] = {}
        seen: set[tuple[int, int]] = set()

        for question in questions:
            if not isinstance(question, dict):
                continue

            instruction = str(
                question.get("section_instruction") or ""
            )

            if not PRONUNCIATION_INSTRUCTION.search(
                instruction
            ):
                continue

            try:
                page_number = int(question["page_number"])
                question_number = int(question["question_id"])
            except (
                KeyError,
                ValueError,
                TypeError,
            ):
                continue

            if not 1 <= page_number <= len(document):
                continue

            key = (page_number, question_number)

            if key in seen:
                continue

            seen.add(key)

            if page_number not in source_by_page:
                source_by_page[page_number] = (
                    _source_pronunciation_options(
                        document[page_number - 1]
                    )
                )

            source_line = source_by_page[
                page_number
            ].get(question_number)

            if not source_line:
                continue

            source_options = _options(source_line)

            if tuple(source_options) != LABELS:
                continue

            if any(
                LOW_LINE not in source_options[label]
                or not _letters(
                    source_options[label]
                ).isalpha()
                for label in LABELS
            ):
                continue

            current = question.get("mcp_options")

            if not isinstance(current, list) or len(current) != 4:
                continue

            if any(
                not isinstance(item, dict)
                for item in current
            ):
                continue

            if [
                item.get("label")
                for item in current
            ] != list(LABELS):
                continue

            observed = [
                _text(item.get("content"))
                for item in current
            ]

            if not all(
                _close_enough(
                    observed[index],
                    source_options[label],
                )
                for index, label in enumerate(LABELS)
            ):
                continue

            changed = []

            for index, label in enumerate(LABELS):
                verified = source_options[label]

                if observed[index] != verified:
                    current[index]["content"] = [
                        {
                            "type": "text",
                            "value": verified,
                        }
                    ]
                    changed.append(label)

            if changed:
                repairs.append({
                    "page_number": page_number,
                    "question_number": question_number,
                    "kind": "final_pronunciation_choices",
                    "changed_options": changed,
                })

    if repairs:
        logger.info(
            "parser=choice_fidelity repairs=%s",
            repairs,
        )

    return repairs
