"""Shared, read-only parsing pipeline for PDF ingestion and Parser Preview."""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Any

import pymupdf

from .parser_recovery import recover_pdf_headings
from .pdf_fidelity import enhance_ocr_with_pdf_geometry
from .parser_text_normalizer import normalize_pdf_backed_ocr
from .parser_choice_fidelity import reconcile_pronunciation_choices
from .parser_review_flags import clear_verified_correction_warnings
from .parser_structure_review import review_question_structure
from .scan_pronunciation_review import review_scan_pronunciation
from .quiz_parser import (
    QUESTION_START,
    NUMBERED_START,
    parse_questions_by_pages,
    _strip_answer_key_pages,
)

# Uppercase markers after whitespace:
# never treat the h. in km/h. as H.
MARKER = re.compile(
    r"(?<!\S)(?:\*\*|__)?"
    r"(?:\(([A-H])\)|([A-H])[.):,])"
    r"(?:\*\*|__)?[ \t]*"
)

SECTION_HEADING = re.compile(
    r"^(?:(?:PHAN|PART|SECTION)\s+"
    r"(?:[IVXLCDM]+|\d+)|"
    r"QUESTION\s+[IVXLCDM]+)"
    r"(?:[. :)–—-]|$)"
)

UNSUPPORTED_SECTION = re.compile(
    r"\b(?:DUNG\s*[,/–-]?\s*SAI|"
    r"TRUE\s*[/–-]?\s*FALSE|"
    r"TRA LOI NGAN|SHORT ANSWER)\b"
)


def normalize_option_layout(
    pages: list[str],
) -> list[str]:
    """Split only anchored, consecutive multi-option runs; preserve content."""
    output = []
    in_code = False

    for page in pages:
        lines = []

        for line in page.splitlines():
            if line.strip().startswith("```"):
                in_code = not in_code
                lines.append(line)
                continue

            if in_code:
                lines.append(line)
                continue

            matches = list(MARKER.finditer(line))

            labels = [
                match.group(1) or match.group(2)
                for match in matches
            ]

            if len(matches) < 2 or any(
                ord(b) != ord(a) + 1
                for a, b in zip(labels, labels[1:])
            ):
                lines.append(line)
                continue

            prefix = line[:matches[0].start()].strip()

            if prefix and not (
                QUESTION_START.match(prefix)
                or NUMBERED_START.match(prefix)
            ):
                lines.append(line)
                continue

            parts = [
                line[
                    match.end():
                    matches[index + 1].start()
                    if index + 1 < len(matches)
                    else len(line)
                ].strip()
                for index, match in enumerate(matches)
            ]

            if not all(parts):
                lines.append(line)
                continue

            if prefix:
                lines.append(prefix)

            lines.extend(
                f"{label}. {part}"
                for label, part in zip(labels, parts)
            )

        output.append("\n".join(lines))

    return output


def _heading_text(line: str) -> str:
    value = (
        line.strip()
        .lstrip("#*_ ")
        .replace("Đ", "D")
        .replace("đ", "d")
    )

    value = unicodedata.normalize("NFKD", value)

    return "".join(
        char
        for char in value
        if not unicodedata.combining(char)
    ).upper()


def _text(blocks: list[dict]) -> str:
    return "\n".join(
        str(block.get("value", ""))
        for block in blocks
    ).strip()


def parse_text_questions(
    pages: list[str],
    exam_name: str,
) -> list[dict]:
    """Respect numbered section boundaries while retaining physical page numbers."""

    # Apply answer-key boundary globally before splitting into sections.
    pages = _strip_answer_key_pages(pages)

    segments: list[tuple[str, list[list[str]]]] = [
        ("", [[] for _ in pages])
    ]

    in_code = False

    for index, page in enumerate(pages):
        for line in page.splitlines():
            if line.strip().startswith("```"):
                in_code = not in_code

            if (
                not in_code
                and SECTION_HEADING.match(_heading_text(line))
            ):
                segments.append(
                    (line.strip(), [[] for _ in pages])
                )

            else:
                segments[-1][1][index].append(line)

    result = []

    for heading, segment in segments:
        text = [
            "\n".join(lines)
            for lines in segment
        ]

        if not any(page.strip() for page in text):
            continue

        if (
            not heading
            and len(segments) > 1
            and not any(
                QUESTION_START.match(line)
                or NUMBERED_START.match(line)
                for page in text
                for line in page.splitlines()
            )
        ):
            # Document metadata before a numbered
            # section is not a question.
            continue

        questions = parse_questions_by_pages(
            text,
            exam_name,
        )

        for question in questions:
            if heading:
                question["section_instruction"] = "\n".join(
                    part
                    for part in (
                        heading,
                        question.get("section_instruction", ""),
                    )
                    if part
                )

            if UNSUPPORTED_SECTION.search(
                _heading_text(heading)
            ):
                # The current parser contract does not support
                # per-statement true/false.
                # Preserve statements instead of inventing answers.
                for option in question["mcp_options"]:
                    question["question_content"].append({
                        "type": "text",
                        "value": (
                            f"{option['label']}. "
                            f"{_text(option['content'])}"
                        ),
                    })

                question.update(
                    type="fill_in",
                    mcp_options=[],
                    multiple_choice=False,
                    parse_fallback=True,
                    requires_source_image=True,
                    parser_warning=(
                        "Dạng câu này chưa được parser hỗ trợ; "
                        "cần đối chiếu PDF gốc."
                    ),
                )

        result.extend(questions)

    return result


def _review_unresolved_media(
    questions: list[dict],
) -> None:
    for question in questions:
        text = (
            _text(question.get("question_content", []))
            + "\n"
            + question.get("shared_passage", "")
        )

        text += "\n" + "\n".join(
            _text(option.get("content", []))
            for option in question.get("mcp_options", [])
        )

        if re.search(
            r"<img\b|!\[[^\]]*\]"
            r"\((?!https?://|/api/assets/)[^)]+\)",
            text,
            re.I,
        ):
            question["parse_fallback"] = True
            question["requires_source_image"] = True

            if not question.get("parser_warning"):
                question["parser_warning"] = (
                    "Ảnh OCR chưa có đường dẫn lưu trữ ổn định; "
                    "cần xem PDF gốc."
                )


def _review_uncertain_scan(
    pdf: bytes,
    questions: list[dict],
) -> None:
    """Flag suspicious truncation without inventing missing content."""
    try:
        with pymupdf.open(
            stream=pdf,
            filetype="pdf",
        ) as document:
            scanned = {
                index
                for index, page in enumerate(document, 1)
                if not page.get_text().strip()
            }

    except (RuntimeError, ValueError, TypeError):
        return

    for question in questions:
        if question.get("page_number") not in scanned:
            continue

        options = [
            _text(option.get("content", []))
            for option in question.get("mcp_options", [])
        ]

        if len(options) < 3:
            continue

        ends = [
            bool(re.search(r"[.!?…]$", option))
            for option in options
        ]

        suspicious = (
            sum(ends) == len(options) - 1
            and any(
                len(option) >= 40 and not end
                for option, end in zip(options, ends)
            )
        )

        if suspicious:
            question["parse_fallback"] = True
            question["requires_source_image"] = True
            question.setdefault("parser_warning", None)

            if not question["parser_warning"]:
                question["parser_warning"] = (
                    "OCR có thể thiếu dòng ở một lựa chọn; "
                    "cần đối chiếu PDF gốc."
                )


def parse_pdf_questions(
    pdf_contents: bytes,
    raw_page_texts: list[str],
    exam_name: str,
    *,
    embed_context: bool = True,
) -> dict[str, Any]:
    """Apply evidence-based repairs; keep original OCR and never infer answers.

    The main API embeds shared context in existing content blocks
    because SQLite has no separate passage column.
    Preview renders the same context separately.
    """
    original = list(raw_page_texts)

    started = time.perf_counter()

    enhanced, recovered = recover_pdf_headings(
        pdf_contents,
        original,
    )

    enriched, fidelity = enhance_ocr_with_pdf_geometry(
        pdf_contents,
        enhanced,
    )

    normalized, repairs = normalize_pdf_backed_ocr(
        pdf_contents,
        enriched,
    )

    normalized = normalize_option_layout(normalized)

    preprocess_seconds = time.perf_counter() - started

    started = time.perf_counter()

    questions = parse_text_questions(
        normalized,
        exam_name,
    )

    final_choices = reconcile_pronunciation_choices(
        pdf_contents,
        questions,
    )

    fidelity_dicts = [
        item.as_dict()
        for item in fidelity
    ]

    verified = clear_verified_correction_warnings(
        questions,
        fidelity_dicts,
    )

    _review_uncertain_scan(
        pdf_contents,
        questions,
    )

    _review_unresolved_media(questions)

    review_scan_pronunciation(
        pdf_contents,
        questions,
    )

    structure_review = review_question_structure(
        questions
    )

    if embed_context:
        for question in questions:
            context = [
                question.get(key, "")
                for key in (
                    "section_instruction",
                    "shared_passage",
                )
            ]

            question["question_content"] = (
                [
                    {
                        "type": "text",
                        "value": value,
                    }
                    for value in context
                    if value
                ]
                + question["question_content"]
            )

    return {
        "raw_page_texts": original,
        "page_texts": normalized,
        "questions": questions,
        "diagnostics": {
            "recovered_headings": [
                item.as_dict()
                for item in recovered
            ],
            "pdf_fidelity_repairs": fidelity_dicts,
            "normalized_repairs": repairs,
            "final_choice_repairs": final_choices,
            "verified_correction_questions": verified,
            **structure_review,
        },
        "timings": {
            "preprocess_seconds": round(
                preprocess_seconds,
                4,
            ),
            "parser_seconds": round(
                time.perf_counter() - started,
                4,
            ),
        },
    }