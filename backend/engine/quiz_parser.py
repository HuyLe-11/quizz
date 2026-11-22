"""Parse OCR text into questions without guessing correct answers.

Keeps state across pages and marks uncertain results for source review.
"""

import re
import unicodedata


QUESTION_START = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*"
    r"(?:Câu(?:\s+hỏi|\s+số)?|Cau(?:\s+hoi|\s+so)?|Question|Q)\s*"
    r"(\d{1,4})\s*[.:)\-]?\s*(?:\*\*|__)?\s*(.*)$",
    re.IGNORECASE,
)

NUMBERED_START = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?(\d{1,3})[.)]\s*"
    r"(?:\*\*|__)?\s+(.+)$"
)

OPTION_START = re.compile(
    r"^\s*(?:[-*+•○◯◉●]\s*)?(?:\*\*|__)?\s*"
    r"(?:\(([A-H])\)|([A-H])[.):])\s*(?:\*\*|__)?\s*(.*)$",
    re.IGNORECASE,
)

TABLE_OPTION = re.compile(
    r"^\s*\|\s*(?:\*\*|__)?\s*([A-H])[.):]?\s*"
    r"(?:\*\*|__)?\s*\|\s*(.*?)\s*\|\s*$",
    re.IGNORECASE,
)

INLINE_OPTION = re.compile(
    r"(?<![\w])(?:\*\*|__)?([A-H])[.):]\s*(?:\*\*|__)?\s+",
    re.IGNORECASE,
)

PAGE_NOISE = re.compile(
    r"^\s*(?:(?:Trang|Page)\s+\d+(?:\s*[/|]\s*\d+)?"
    r"|\d+\s*/\s*\d+)\s*$",
    re.IGNORECASE,
)

# Text from Moodle/LMS review screens, not question content.
LMS_NOISE = re.compile(
    r"^(?:Select\s+one\s*:|"
    r"Select\s+all\s+that\s+apply\s*:|"
    r"Complete|"
    r"Not\s+yet\s+answered|"
    r"Flag\s+question|"
    r"Mark\s+\d+(?:[.,]\d+)?\s+out\s+of\s+\d+(?:[.,]\d+)?|"
    r"Question\s+text|"
    r"Remove\s+flag)$",
    re.IGNORECASE,
)

FILL_IN_HINT = re.compile(
    r"(?:điền\s+(?:vào|từ)|trả\s+lời\s+ngắn|"
    r"fill\s+in|short\s+answer|_{3,})",
    re.IGNORECASE,
)

OCR_PUNCTUATION = str.maketrans(
    "ＡＢＣＤＥＦＧＨａｂｃｄｅｆｇｈ．：）",
    "ABCDEFGHabcdefgh.:)",
)


def _slug(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode()
        .lower()
    )
    return (
        re.sub(r"[^a-z0-9]+", "_", ascii_value).strip("_")
        or "pdf_quiz"
    )


def _block(text: str) -> list[dict[str, str]]:
    text = text.strip()
    if not text:
        return []

    if "$" in text or "\\(" in text or "\\[" in text:
        return [{
            "type": "latex",
            "value": text,
        }]

    return [{
        "type": "text",
        "value": text,
    }]


def _clean_line(line: str) -> str:
    return (
        line.replace("\ufeff", "")
        .replace("\u200b", "")
        .replace("\u00a0", " ")
        .translate(OCR_PUNCTUATION)
        .strip()
    )


def _inline_options(
    line: str,
) -> tuple[str, list[dict[str, str]]]:
    """Split consecutive A. ... B. ... options from one line."""

    matches = list(INLINE_OPTION.finditer(line))
    if len(matches) < 2:
        return line, []

    labels = [
        match.group(1).upper()
        for match in matches
    ]

    if labels[0] != "A" or any(
        ord(right) != ord(left) + 1
        for left, right in zip(labels, labels[1:])
    ):
        return line, []

    options = []

    for index, match in enumerate(matches):
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(line)
        )

        options.append({
            "label": labels[index],
            "text": line[match.end():end].strip(),
        })

    return (
        line[:matches[0].start()].strip(),
        options,
    )


def _match_option(
    line: str,
) -> tuple[str, str] | None:
    table = TABLE_OPTION.match(line)

    if table:
        return (
            table.group(1).upper(),
            table.group(2).strip(),
        )

    option = OPTION_START.match(line)

    if option:
        return (
            (option.group(1) or option.group(2)).upper(),
            option.group(3).strip(),
        )

    return None


def parse_questions(
    markdown: str,
    exam_name: str,
) -> list[dict]:
    return parse_questions_by_pages(
        [markdown],
        exam_name,
    )


def parse_questions_by_pages(
    page_texts: list[str],
    exam_name: str,
) -> list[dict]:
    """Parse a document while retaining each question's first page."""

    groups: list[dict] = []
    current: dict | None = None
    current_option: dict | None = None
    in_code_fence = False
    previous_blank = False

    def append(text: str) -> None:
        if current is None:
            return

        target = (
            current_option
            if current_option is not None
            else current
        )
        field = (
            "text"
            if current_option is not None
            else "prompt"
        )

        target[field] = (
            f"{target[field]}\n{text}".strip()
            if target[field]
            else text
        )

    def add_option(
        label: str,
        content: str,
    ) -> None:
        nonlocal current_option

        if current is None:
            return

        if any(
            option["label"] == label
            for option in current["options"]
        ):
            current["uncertain"] = True
            append(f"{label}. {content}")
            return

        current_option = {
            "label": label,
            "text": content,
        }

        current["options"].append(current_option)

    for page_number, page_text in enumerate(
        page_texts,
        start=1,
    ):
        for raw_line in (page_text or "").splitlines():
            line = _clean_line(raw_line)

            if not line:
                previous_blank = True
                continue

            if line.startswith("```"):
                in_code_fence = not in_code_fence
                append(line)
                previous_blank = False
                continue

            if in_code_fence:
                append(line)
                previous_blank = False
                continue

            if (
                PAGE_NOISE.match(line)
                or LMS_NOISE.match(line)
            ):
                continue

            if re.fullmatch(r"\|[\s|:-]+\|", line):
                continue

            explicit = QUESTION_START.match(line)
            numbered = (
                NUMBERED_START.match(line)
                if explicit is None
                else None
            )

            if numbered is not None and current is not None:
                candidate = int(numbered.group(1))
                next_number = (
                    candidate == current["number"] + 1
                )
                new_page = (
                    page_number != current["page_number"]
                )
                has_options = (
                    len(current["options"]) >= 2
                )

                if not (
                    (
                        next_number
                        and (
                            has_options
                            or previous_blank
                            or new_page
                        )
                    )
                    or (
                        candidate == 1
                        and new_page
                        and has_options
                    )
                ):
                    numbered = None

            question_header = explicit or numbered

            if question_header is not None:
                current = {
                    "number": int(
                        question_header.group(1)
                    ),
                    "page_number": page_number,
                    "prompt": "",
                    "options": [],
                    "uncertain": False,
                }
                groups.append(current)
                current_option = None

                prompt, inline = _inline_options(
                    question_header.group(2).strip()
                )

                current["prompt"] = prompt

                for option in inline:
                    add_option(
                        option["label"],
                        option["text"],
                    )

                previous_blank = False
                continue

            if current is None:
                previous_blank = False
                continue

            before, inline = _inline_options(line)

            if inline:
                if before:
                    append(before)

                for option in inline:
                    add_option(
                        option["label"],
                        option["text"],
                    )

                previous_blank = False
                continue

            option = _match_option(line)

            if option:
                add_option(*option)
            else:
                append(line)

            previous_blank = False

    # If OCR missed the small question headings entirely,
    # salvage nonempty content as reviewable per-page drafts.
    # Do not invent correct answers.
    if not groups:
        for page_number, page_text in enumerate(
            page_texts,
            start=1,
        ):
            prompt_parts: list[str] = []
            recovered_options: list[dict[str, str]] = []
            last_option: dict[str, str] | None = None

            for raw_line in (
                page_text or ""
            ).splitlines():
                line = _clean_line(raw_line)

                if (
                    not line
                    or PAGE_NOISE.match(line)
                    or LMS_NOISE.match(line)
                ):
                    continue

                before, inline = _inline_options(line)

                if inline:
                    if before:
                        if last_option:
                            last_option["text"] += (
                                "\n" + before
                            )
                        else:
                            prompt_parts.append(before)

                    for item in inline:
                        if not any(
                            option["label"] == item["label"]
                            for option in recovered_options
                        ):
                            recovered_options.append(item)
                            last_option = item
                    continue

                choice = _match_option(line)

                if choice:
                    label, answer_text = choice

                    if not any(
                        option["label"] == label
                        for option in recovered_options
                    ):
                        last_option = {
                            "label": label,
                            "text": answer_text,
                        }
                        recovered_options.append(
                            last_option
                        )
                    continue

                if last_option:
                    last_option["text"] = (
                        last_option["text"]
                        + "\n"
                        + line
                    ).strip()
                else:
                    prompt_parts.append(line)

            if len(" ".join(prompt_parts)) >= 20:
                groups.append({
                    "number": len(groups) + 1,
                    "page_number": page_number,
                    "prompt": "\n".join(prompt_parts),
                    "options": recovered_options,
                    "uncertain": True,
                })

    slug = _slug(exam_name)
    questions: list[dict] = []

    for item in groups:
        options = item["options"]
        labels = [
            option["label"]
            for option in options
        ]
        expected = [
            chr(ord("A") + index)
            for index in range(len(labels))
        ]
        prompt = item["prompt"].strip()

        needs_review = (
            item["uncertain"]
            or len(prompt) < 12
            or len(options) == 1
            or labels != expected
            or any(
                not option["text"].strip()
                for option in options
            )
            or (
                not options
                and not FILL_IN_HINT.search(prompt)
            )
        )

        questions.append({
            "composite_id": (
                f"{slug}_Q{item['number']:02d}"
            ),
            "question_id": item["number"],
            "page_number": item["page_number"],
            "type": (
                "mcp"
                if options
                else "fill_in"
            ),
            "multiple_choice": False,
            "question_content": _block(prompt),
            "explanation_content": [],
            "mcp_options": [
                {
                    "label": option["label"],
                    "content": _block(option["text"]),
                    "is_correct": False,
                    "order_index": index,
                }
                for index, option in enumerate(options)
            ],
            "match_pairs": [],
            "fill_in_answers": [],
            "parse_fallback": needs_review,
        })

    return questions
