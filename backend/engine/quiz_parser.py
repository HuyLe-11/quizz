"""Database-independent, conservative exam-question parser.

Recognizes page-ordered OCR Markdown. Keeps section instructions, passages,
question boundaries and choice text distinct; never infers correct answers.
Recognized answer-key material is excluded from question content.
"""
from __future__ import annotations

import re
import unicodedata

QUESTION_START = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*"
    r"(?:Câu(?:\s+hỏi|\s+số)?|Cau(?:\s+hoi|\s+so)?|Question|Q)\s*"
    r"(\d{1,4})\s*[.:)\-]?\s*(?:\*\*|__)?\s*(.*)$", re.I,
)
NUMBERED_START = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?(\d{1,3})[.)]\s*"
    r"(?:\*\*|__)?\s+(.+)$"
)
OPTION_START = re.compile(
    r"^\s*(?:[-*+•○◯◉●]\s*)?(?:\*\*|__)?\s*"
    r"(?:\(([A-H])\)|([A-H])[.):])\s*(?:\*\*|__)?\s*(.*)$", re.I,
)
TABLE_OPTION = re.compile(
    r"^\s*\|\s*(?:\*\*|__)?\s*([A-H])[.):]?\s*"
    r"(?:\*\*|__)?\s*\|\s*(.*?)\s*\|\s*$", re.I,
)
INLINE_OPTION = re.compile(
    r"(?<![\w])(?:\*\*|__)?([A-H])[.):]\s*(?:\*\*|__)?\s+", re.I,
)
SECTION_START = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*(?:"
    r"Mark\s+the\s+letter\b|Read\s+the\s+(?:following\s+)?(?:passage|text)\b|"
    r"Choose\s+the\s+(?:best|correct)\b|Select\s+the\s+(?:best|correct)\b|"
    r"Identify\s+the\b|Chọn\s+(?:đáp\s+án|câu|phương\s+án)\b|"
    r"Đọc\s+(?:đoạn|bài)\b)", re.I,
)
EXAM_END = re.compile(r"^\s*[-–—\s]*HẾT\s*[-–—\s]*$", re.I)
PAGE_NOISE = re.compile(
    r"^\s*(?:(?:Trang|Page)\s+\d+(?:\s*[/|]\s*\d+)?|"
    r"\d+\s*/\s*\d+)\s*$", re.I,
)
LMS_NOISE = re.compile(
    r"^(?:Select\s+one\s*:|Select\s+all\s+that\s+apply\s*:|Complete|"
    r"Not\s+yet\s+answered|Flag\s+question|"
    r"Mark\s+\d+(?:[.,]\d+)?\s+out\s+of\s+\d+(?:[.,]\d+)?|"
    r"Question\s+text|Remove\s+flag)$", re.I,
)
FILL_IN_HINT = re.compile(
    r"(?:điền\s+(?:vào|từ)|trả\s+lời\s+ngắn|fill\s+in|short\s+answer|_{3,})",
    re.I,
)
REFERENCE_MARKER_LINE = re.compile(r"^[A-D](?:\s+[A-D]){0,3}$", re.I)
EMBEDDED_DIRECTIVE = re.compile(
    r"\s+(?=(?:\*\*|__)?(?:Mark\s+the\s+letter|"
    r"Read\s+the\s+(?:following\s+)?passage)\b)", re.I,
)
OCR_PUNCTUATION = str.maketrans(
    "ＡＢＣＤＥＦＧＨａｂｃｄｅｆｇｈ．：）",
    "ABCDEFGHabcdefgh.:)"
)

ANSWER_KEY_HEADING = re.compile(
    r"(?:DAP\s+AN(?:\s+(?:THAM\s+KHAO|CHINH\s+THUC))?|"
    r"BANG\s+DAP\s+AN|HUONG\s+DAN\s+CHAM|"
    r"ANSWER\s+KEYS?|KEY\s+TO\s+(?:THE\s+)?ANSWERS?)",
    re.I,
)
ANSWER_KEY_INSTITUTION = re.compile(
    r"^(?:BO|SO|PHONG)\s+(?:(?:GIAO\s+DUC)|"
    r"(?:GD\s*(?:VA|&)\s*DT))\b",
    re.I,
)


def _slug(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore").decode().lower()
    )
    return re.sub(r"[^a-z0-9]+", "_", ascii_value).strip("_") or "pdf_quiz"


def _block(text: str) -> list[dict[str, str]]:
    text = text.strip()
    if not text:
        return []
    if any(marker in text for marker in ("$", "\\(", "\\[", "\\underline")):
        return [{"type": "latex", "value": text}]
    return [{"type": "text", "value": text}]


def _clean_line(line: str) -> str:
    return (
        line.replace("\ufeff", "").replace("\u200b", "")
        .replace("\u00a0", " ").translate(OCR_PUNCTUATION).strip()
    )


def _normalize_heading(line: str) -> str:
    """Normalize Vietnamese diacritics and heading markup."""
    text = line.replace("Đ", "D").replace("đ", "d")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        char for char in text
        if not unicodedata.combining(char)
    )
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(
        r"^[\s#*_`~:;\-–—]+|[\s#*_`~:;\-–—]+$",
        "",
        text,
    )
    return " ".join(text.upper().split())


def _is_answer_key_heading(line: str) -> bool:
    normalized = _normalize_heading(line)
    return bool(ANSWER_KEY_HEADING.fullmatch(normalized))


def _strip_answer_key_pages(page_texts: list[str]) -> list[str]:
    """Remove answer-key pages and same-page answer-key suffixes.

    Answer-key pages may begin with institution metadata before
    the answer-key heading. A same-page key can follow the final
    question. Neither case may be appended to option D.
    """
    kept: list[str] = []

    for page in page_texts:
        lines = (page or "").splitlines()

        heading = next(
            (
                index
                for index, line in enumerate(lines)
                if _is_answer_key_heading(line)
            ),
            None,
        )

        if heading is None:
            kept.append(page or "")
            continue

        # No question before this header:
        # the entire page is an answer sheet.
        if not any(
            QUESTION_START.match(_clean_line(line))
            for line in lines[:heading]
        ):
            break

        # Otherwise the key follows the exam on the same page.
        cut_at = heading

        probe = heading - 1
        while probe >= 0 and not lines[probe].strip():
            probe -= 1

        if probe >= 0 and ANSWER_KEY_INSTITUTION.match(
            _normalize_heading(lines[probe])
        ):
            cut_at = probe

        kept.append("\n".join(lines[:cut_at]))
        break

    return kept


def _split_embedded_directive(raw_line: str) -> list[str]:
    match = EMBEDDED_DIRECTIVE.search(raw_line)

    if match and re.search(
        r"\bD[.):]\s+",
        raw_line[:match.start()],
        re.I,
    ):
        return [
            raw_line[:match.start()],
            raw_line[match.end():],
        ]

    return [raw_line]


def _is_noise(line: str) -> bool:
    return bool(
        PAGE_NOISE.match(line)
        or LMS_NOISE.match(line)
        or re.fullmatch(r"\|[\s|:-]+\|", line)
        or "TAILIEUDIEUKY ©" in line.upper()
        or re.match(
            r"^Trang\s+\d+\s*/\s*\d+\s*-\s*Mã\s+đề",
            line,
            re.I,
        )
    )


def _inline_options(
    line: str,
    expected_first: str = "A",
) -> tuple[str, list[dict[str, str]]]:
    matches = list(INLINE_OPTION.finditer(line))

    if len(matches) < 2:
        return line, []

    labels = [
        match.group(1).upper()
        for match in matches
    ]

    if labels[0] != expected_first or any(
        ord(right) != ord(left) + 1
        for left, right in zip(labels, labels[1:])
    ):
        return line, []

    choices = []

    for index, match in enumerate(matches):
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(line)
        )

        choices.append({
            "label": labels[index],
            "text": line[match.end():end].strip(),
        })

    return line[:matches[0].start()].strip(), choices


def _match_option(line: str) -> tuple[str, str] | None:
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


def _starts_complete_option_restart(
    lines: list[str],
    start: int,
) -> bool:
    """Detect a second A-D run after a completed question."""
    expected = ord("A")

    for raw in lines[start:start + 5]:
        line = _clean_line(raw)

        if not line or _is_noise(line):
            continue

        if QUESTION_START.match(line) or SECTION_START.match(line):
            return False

        label = chr(expected)
        before, inline = _inline_options(line, label)

        if inline and not before:
            seen = [
                choice["label"]
                for choice in inline
            ]
        else:
            single = _match_option(line)

            if single is None or single[0] != label:
                return False

            seen = [single[0]]

        expected_labels = [
            chr(value)
            for value in range(
                expected,
                expected + len(seen),
            )
        ]

        if seen != expected_labels:
            return False

        expected += len(seen)

        if expected == ord("E"):
            return True

        if expected > ord("E"):
            return False

    return False


def _new_section(start: str) -> dict:
    return {
        "instruction": start,
        "passage": [],
        "reading": bool(
            re.match(r"^(?:Read|Đọc)\b", start, re.I)
        ),
        "instruction_complete": start.rstrip("*_ ").endswith("."),
        "collecting": True,
    }


def _append_section_line(section: dict, line: str) -> None:
    if not section["instruction_complete"]:
        section["instruction"] += "\n" + line

        if line.rstrip("*_ ").endswith("."):
            section["instruction_complete"] = True

    elif section["reading"]:
        section["passage"].append(line)

    else:
        section["instruction"] += "\n" + line


def _finish_question(item: dict, slug: str) -> dict:
    prompt = item["prompt"].strip()
    choices = item["options"]
    instruction = item["section_instruction"]
    passage = item["shared_passage"]
    marker_labels = item["reference_labels"]

    underline_identification = bool(
        re.search(
            r"underlined\s+part\s+that\s+needs\s+correction",
            instruction,
            re.I,
        )
    )

    if (
        underline_identification
        and set(marker_labels) == set("ABCD")
        and not choices
    ):
        choices = [
            {
                "label": label,
                "text": (
                    f"Vị trí {label} "
                    "(xem phần gạch chân trên PDF gốc)"
                ),
            }
            for label in "ABCD"
        ]

    labels = [
        choice["label"]
        for choice in choices
    ]

    expected = [
        chr(ord("A") + index)
        for index in range(len(labels))
    ]

    combined_text = prompt + "\n" + "\n".join(
        choice["text"]
        for choice in choices
    )

    has_underline_markers = bool(
        re.search(
            r"\\underline|<u(?:\s[^>]*)?>|\u0332",
            combined_text,
            re.I,
        )
    )

    recovered_missing_header = item.get(
        "recovered_missing_header",
        False,
    )

    requires_source_image = (
        recovered_missing_header
        or underline_identification
        or (
            "underlined" in instruction.lower()
            and not has_underline_markers
        )
    )

    asks_for_four = bool(
        re.search(
            r"A,?\s+B,?\s+C,?\s+(?:or\s+)?D",
            instruction,
            re.I,
        )
    )

    needs_review = bool(
        (asks_for_four and len(choices) != 4)
        or item["uncertain"]
        or (len(prompt) < 12 and not instruction)
        or len(choices) == 1
        or labels != expected
        or any(
            not choice["text"].strip()
            for choice in choices
        )
        or (
            not choices
            and not FILL_IN_HINT.search(prompt)
        )
        or requires_source_image
    )

    return {
        "composite_id": f"{slug}_Q{item['number']:02d}",
        "question_id": item["number"],
        "page_number": item["page_number"],
        "type": "mcp" if choices else "fill_in",
        "multiple_choice": False,
        "question_content": _block(prompt),
        "explanation_content": [],
        "mcp_options": [
            {
                "label": choice["label"],
                "content": _block(choice["text"]),
                "is_correct": False,
                "order_index": index,
            }
            for index, choice in enumerate(choices)
        ],
        "match_pairs": [],
        "fill_in_answers": [],
        "parse_fallback": needs_review,
        "section_instruction": instruction,
        "shared_passage": passage,
        "requires_source_image": requires_source_image,
        "parser_warning": (
            "PaddleOCR không nhận diện được dòng tiêu đề của câu này; "
            "hãy đối chiếu PDF gốc."
            if recovered_missing_header
            else None
        ),
    }


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
    """Parse ordered OCR pages without database or network operations."""

    # Important: stop at the answer key BEFORE processing options.
    page_texts = _strip_answer_key_pages(page_texts)

    groups: list[dict] = []
    current: dict | None = None
    current_option: dict | None = None
    section: dict | None = None
    in_code_fence = False
    previous_blank = False
    ended = False

    after_completed_options: list[str] = []

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
            (target[field] + "\n" + text).strip()
            if target[field]
            else text
        )

        if (
            current_option is not None
            and [
                item["label"]
                for item in current["options"]
            ] == list("ABCD")
            and current_option["label"] == "D"
        ):
            after_completed_options.append(text)

    def add_option(
        label: str,
        content: str,
    ) -> None:
        nonlocal current_option

        if current is None:
            return

        if any(
            choice["label"] == label
            for choice in current["options"]
        ):
            current["uncertain"] = True
            append(f"{label}. {content}")
            return

        current_option = {
            "label": label,
            "text": content,
        }

        current["options"].append(current_option)

        if (
            label == "D"
            and [
                item["label"]
                for item in current["options"]
            ] == list("ABCD")
        ):
            after_completed_options.clear()

    for page_number, page_text in enumerate(
        page_texts,
        start=1,
    ):
        if ended:
            break

        page_text = page_text or ""

        logical_lines = [
            part
            for raw in page_text.splitlines()
            for part in _split_embedded_directive(raw)
        ]

        for line_index, raw_line in enumerate(logical_lines):
            line = _clean_line(raw_line)

            if not line:
                previous_blank = True
                continue

            if EXAM_END.fullmatch(line):
                ended = True
                current = None
                current_option = None
                break

            if line.startswith("```"):
                in_code_fence = not in_code_fence
                append(line)
                continue

            if in_code_fence:
                append(line)
                continue

            if _is_noise(line):
                continue

            if SECTION_START.match(line):
                section = _new_section(line)
                current = None
                current_option = None
                after_completed_options.clear()
                previous_blank = False
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

                if not (
                    (
                        next_number
                        and (
                            len(current["options"]) >= 2
                            or previous_blank
                            or new_page
                        )
                    )
                    or (
                        candidate == 1
                        and new_page
                        and len(current["options"]) >= 2
                    )
                ):
                    numbered = None

            header = explicit or numbered

            if header is not None:
                if section is not None:
                    section["collecting"] = False

                current = {
                    "number": int(header.group(1)),
                    "page_number": page_number,
                    "prompt": "",
                    "options": [],
                    "uncertain": False,
                    "section_instruction": (
                        section["instruction"]
                        if section
                        else ""
                    ),
                    "shared_passage": (
                        "\n".join(section["passage"])
                        if section
                        else ""
                    ),
                    "reference_labels": [],
                }

                groups.append(current)
                current_option = None
                after_completed_options.clear()

                prompt, inline = _inline_options(
                    header.group(2).strip()
                )

                current["prompt"] = prompt

                for choice in inline:
                    add_option(
                        choice["label"],
                        choice["text"],
                    )

                previous_blank = False
                continue

            if section is not None and section["collecting"]:
                _append_section_line(
                    section,
                    line,
                )
                previous_blank = False
                continue

            if current is None:
                previous_blank = False
                continue

            if (
                "underlined part that needs correction"
                in current["section_instruction"].lower()
                and REFERENCE_MARKER_LINE.fullmatch(line)
            ):
                current["reference_labels"].extend(
                    line.split()
                )
                previous_blank = False
                continue

            if (
                [
                    item["label"]
                    for item in current["options"]
                ] == list("ABCD")
                and after_completed_options
                and _starts_complete_option_restart(
                    logical_lines,
                    line_index,
                )
            ):
                old = current
                d_choice = old["options"][-1]

                trailing = (
                    "\n"
                    + "\n".join(after_completed_options)
                )

                if not d_choice["text"].endswith(trailing):
                    raise AssertionError(
                        "Option D recovery boundary was lost."
                    )

                d_choice["text"] = (
                    d_choice["text"][:-len(trailing)]
                )

                next_number = old["number"] + 1

                next_headers = [
                    QUESTION_START.match(
                        _clean_line(candidate)
                    )
                    for candidate in logical_lines[
                        line_index + 1:
                    ]
                    if QUESTION_START.match(
                        _clean_line(candidate)
                    )
                ]

                if (
                    next_headers
                    and int(next_headers[0].group(1))
                    == next_number
                ):
                    d_choice["text"] += trailing

                else:
                    current = {
                        "number": next_number,
                        "page_number": page_number,
                        "prompt": "\n".join(
                            after_completed_options
                        ),
                        "options": [],
                        "uncertain": True,
                        "recovered_missing_header": True,
                        "section_instruction": (
                            old["section_instruction"]
                        ),
                        "shared_passage": (
                            old["shared_passage"]
                        ),
                        "reference_labels": [],
                    }

                    groups.append(current)
                    current_option = None

                after_completed_options.clear()

            next_label = chr(
                ord("A") + len(current["options"])
            )

            before, inline = _inline_options(
                line,
                next_label,
            )

            if inline:
                if before:
                    append(before)

                for choice in inline:
                    add_option(
                        choice["label"],
                        choice["text"],
                    )

                previous_blank = False
                continue

            option = _match_option(line)

            if option:
                add_option(*option)
            else:
                append(line)

            previous_blank = False

    if not groups:
        # Keep reviewable drafts for OCR pages without headings.
        for page_number, page_text in enumerate(
            page_texts,
            start=1,
        ):
            lines = [
                _clean_line(value)
                for value in (
                    page_text or ""
                ).splitlines()
            ]

            lines = [
                value
                for value in lines
                if value and not _is_noise(value)
            ]

            if (
                not lines
                or sum(map(len, lines)) < 20
            ):
                continue

            prompt_parts: list[str] = []
            recovered: list[dict[str, str]] = []
            last_option: dict[str, str] | None = None

            for line in lines:
                next_label = chr(
                    ord("A") + len(recovered)
                )

                before, inline = _inline_options(
                    line,
                    next_label,
                )

                if inline:
                    if before:
                        if last_option is None:
                            prompt_parts.append(before)
                        else:
                            last_option["text"] += (
                                "\n" + before
                            )

                    recovered.extend(inline)
                    last_option = recovered[-1]
                    continue

                option = _match_option(line)

                if option and not any(
                    choice["label"] == option[0]
                    for choice in recovered
                ):
                    last_option = {
                        "label": option[0],
                        "text": option[1],
                    }

                    recovered.append(last_option)

                elif last_option is not None:
                    last_option["text"] += (
                        "\n" + line
                    )

                else:
                    prompt_parts.append(line)

            if sum(map(len, prompt_parts)) < 20:
                continue

            groups.append({
                "number": len(groups) + 1,
                "page_number": page_number,
                "prompt": "\n".join(prompt_parts),
                "options": recovered,
                "uncertain": True,
                "section_instruction": "",
                "shared_passage": "",
                "reference_labels": [],
            })

    slug = _slug(exam_name)

    return [
        _finish_question(item, slug)
        for item in groups
    ]
