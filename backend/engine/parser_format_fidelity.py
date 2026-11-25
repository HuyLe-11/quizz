"""Review formatting-sensitive questions independently of subject."""
from __future__ import annotations

import re
import unicodedata

import pymupdf


_HEADING = re.compile(
    r"^[ \t]*#{1,6}[ \t]+"
    r"(?=(?:QUESTION|PART|SECTION|PHẦN|PHAN|PHÀN)\s+"
    r"(?:[IVXLCDM]+|\d+)(?:[. :)–—-]|$))",
    re.I,
)

_UNDERLINE = re.compile(
    r"\bunderlined\b|\bgạch\s+(?:chân|dưới)\b",
    re.I,
)

_FORMAT_REFERENCE = re.compile(
    r"\bunderlined\b|\bgạch\s+(?:chân|dưới)\b|"
    r"\bin\s+(?:bold|italics)\b|\bin\s+(?:đậm|nghiêng)\b",
    re.I,
)

_TEXT_UNDERLINE = re.compile(
    r"\bunderlined\s+(?:part|word|phrase|letter|text|passage)s?\b|"
    r"\b(?:phần|từ|cụm\s+từ|chữ|đoạn)\s+"
    r"(?:được\s+)?gạch\s+(?:chân|dưới)\b",
    re.I,
)

_WORD = re.compile(
    r"([^\W\d_]*)([ \t]*)"
    r"\$[ \t]*\\underline\{([^\W\d_]+)\}"
    r"[ \t]*\$([ \t]*)([^\W\d_]*)"
)

_LOW_LINE = "\u0332"


def _underlined_word(content: object) -> str | None:

    if not isinstance(content, list) or len(content) != 1:
        return None

    block = content[0]

    if (
        not isinstance(block, dict)
        or block.get("type") not in {"text", "latex"}
    ):
        return None

    value = block.get("value")

    if not isinstance(value, str):
        return None

    match = _WORD.fullmatch(
        unicodedata.normalize("NFC", value.strip())
    )

    if not match:
        return None

    prefix, left_gap, middle, right_gap, suffix = match.groups()

    # A standalone underlined formula remains LaTeX.
    # Only repair a fragmented alphabetic word.

    if (
        not (prefix or suffix)
        or not all(
            part.isalpha()
            for part in (prefix, middle, suffix)
            if part
        )
        or max(len(prefix), len(middle), len(suffix)) < 2
    ):
        return None

    # Remove an OCR-induced gap only when the marked fragment is
    # visibly attached to the rest of the word on the other side.
    # Otherwise preserve inter-word spacing rather than guessing.

    if len(prefix) == 1 and len(suffix) >= 2 and not right_gap:
        left_gap = ""

    if len(suffix) == 1 and len(prefix) >= 2 and not left_gap:
        right_gap = ""

    return prefix + left_gap + "".join(
        char + _LOW_LINE for char in middle
    ) + right_gap + suffix


def _context(question: dict) -> str:

    blocks = question.get("question_content") or []

    instruction = question.get("section_instruction") or ""

    return "\n".join([
        instruction if isinstance(instruction, str) else "",
        *[
            block["value"]
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("value"), str)
        ],
    ])


def review_document_format(
    pdf_contents: bytes,
    questions: list[dict],
) -> None:

    # Normalize presentation of known section headings for all PDFs.

    for question in questions:

        instruction = question.get("section_instruction")

        if isinstance(instruction, str):

            question["section_instruction"] = _HEADING.sub(
                "",
                instruction,
                count=1,
            )

    try:

        with pymupdf.open(
            stream=pdf_contents,
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

        context = _context(question)

        if not _FORMAT_REFERENCE.search(context):
            continue

        # Formatting conversion is narrower than warning generation.

        if (
            _UNDERLINE.search(context)
            and _TEXT_UNDERLINE.search(context)
        ):

            for option in question.get("mcp_options") or []:

                if not isinstance(option, dict):
                    continue

                word = _underlined_word(
                    option.get("content")
                )

                if word is not None:

                    option["content"] = [
                        {
                            "type": "text",
                            "value": word,
                        }
                    ]

        # Do not guess absent formatting or change answer correctness.

        question["requires_source_image"] = True

        question["parse_fallback"] = True

        if not question.get("parser_warning"):

            question["parser_warning"] = (
                "Câu hỏi phụ thuộc định dạng văn bản trên ảnh scan; "
                "cần đối chiếu PDF gốc."
            )