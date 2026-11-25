"""Conservative final review for scanned pronunciation questions."""
from __future__ import annotations

import re

import pymupdf


_HEADING = re.compile(
    r"^[ \t]*#{1,6}[ \t]+"
    r"(?=(?:QUESTION|PART|SECTION|PHẦN|PHAN|PHÀN)\s+"
    r"(?:[IVXLCDM]+|\d+)(?:[. :)–—-]|$))",
    re.I,
)

_PRONUNCIATION = re.compile(
    r"\bunderlined\s+part\b[^\n]{0,100}"
    r"\b(?:pronounced|pronunciation|differs?)\b",
    re.I,
)

_WORD = re.compile(
    r"([A-Za-z]*)[ \t]*"
    r"\$[ \t]*\\underline\{([A-Za-z]+)\}"
    r"[ \t]*\$[ \t]*([A-Za-z]*)"
)

_LOW_LINE = "\u0332"


def _underlined_word(
    content: object,
) -> str | None:

    if (
        not isinstance(content, list)
        or len(content) != 1
    ):
        return None

    block = content[0]

    if (
        not isinstance(block, dict)
        or block.get("type") not in {
            "text",
            "latex",
        }
    ):
        return None

    value = block.get("value")

    if not isinstance(value, str):
        return None

    match = _WORD.fullmatch(
        value.strip()
    )

    if not match:
        return None

    prefix, middle, suffix = match.groups()

    return (
        prefix
        + "".join(
            char + _LOW_LINE
            for char in middle
        )
        + suffix
    )


def review_scan_pronunciation(
    pdf_contents: bytes,
    questions: list[dict],
) -> None:

    # Strip Markdown only from recognized
    # section-heading prefixes.

    for question in questions:

        instruction = question.get(
            "section_instruction"
        )

        if isinstance(instruction, str):

            question["section_instruction"] = (
                _HEADING.sub(
                    "",
                    instruction,
                    count=1,
                )
            )

    try:

        with pymupdf.open(
            stream=pdf_contents,
            filetype="pdf",
        ) as document:

            scanned = {
                index
                for index, page in enumerate(
                    document,
                    1,
                )
                if not page.get_text().strip()
            }

    except (
        RuntimeError,
        ValueError,
        TypeError,
    ):
        return

    for question in questions:

        instruction = (
            question.get("section_instruction")
            or ""
        )

        if (
            question.get("page_number") not in scanned
            or not _PRONUNCIATION.search(instruction)
        ):
            continue

        options = question.get(
            "mcp_options",
            [],
        )

        if (
            question.get("type") == "mcp"
            and [
                option.get("label")
                for option in options
            ] == list("ABCD")
        ):

            for option in options:

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

        # Scan OCR cannot prove that every
        # underline was read faithfully.
        #
        # Preserve earlier flags and never
        # infer missing letters.

        question["requires_source_image"] = True

        question["parse_fallback"] = True

        if not question.get("parser_warning"):

            question["parser_warning"] = (
                "Câu phát âm từ ảnh scan; "
                "cần đối chiếu phần gạch chân "
                "và nội dung lựa chọn với PDF gốc."
            )