"""Conservative Markdown-to-quiz parser; answer keys are never inferred."""

import re
import unicodedata


QUESTION_START = re.compile(r"^\s*(?:#{1,6}\s*)?(?:Câu|Cau|CÃ¢u|Question|Q)\s*(\d+)\s*[.:)]?\s*(.*)$", re.I)
NUMBERED_START = re.compile(r"^\s*(\d{1,3})[.)]\s+(.+)$")
# Deliberately uppercase: lowercase a)-e) are commonly sub-items in question stems.
OPTION_MARKER = re.compile(r"(?<![A-Za-z0-9])(?:\(([A-D])\)|([A-D])\s*[.,:)])\s*", re.ASCII)
SECTION_START = re.compile(
    r"^\s*(?:#{1,6}\s*)?QUESTION\s+([IVXLCDM]+)\b[.:)]?\s*(.*)$", re.I
)


def _slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", ascii_value).strip("_") or "pdf_quiz"


def _block(text: str) -> list[dict[str, str]]:
    text = text.strip()
    if not text:
        return []
    if "$" in text or "\\(" in text or "\\[" in text:
        return [{"type": "latex", "value": text}]
    return [{"type": "text", "value": text}]


def _option_parts(text: str) -> list[tuple[str, str]]:
    """Split an OCR line only when it contains an option sequence, not stem sub-items."""
    markers = list(OPTION_MARKER.finditer(text))
    if not markers:
        return []
    labels = [(match.group(1) or match.group(2)) for match in markers]
    # Inline labels are accepted as a group when they form an ascending run (A/B, C/D,
    # or A-D). A lone label is accepted only at the start of an OCR line.
    run = any(ord(right) == ord(left) + 1 for left, right in zip(labels, labels[1:]))
    starts_with_option = bool(text[:markers[0].start()].strip(" \t-*")) is False
    if len(markers) == 1 and not starts_with_option:
        return []
    if len(markers) > 1 and not run:
        return []
    parts = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        label = marker.group(1) or marker.group(2)
        content = text[marker.end():end].strip()
        if content:
            parts.append((label, content))
    return parts


def parse_questions(markdown: str, exam_name: str) -> list[dict]:
    groups: list[dict] = []
    current = None
    current_option = None
    section_instruction = ""
    section_number = None

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        section_match = SECTION_START.match(raw_line)
        if section_match:
            # Roman numerals identify section headings. Keep the instruction as
            # context for actual numbered questions; never turn it into a question.
            section_number = section_match.group(1).upper()
            section_instruction = section_match.group(2).strip()
            continue
        qmatch = QUESTION_START.match(raw_line)
        if not qmatch:
            qmatch = NUMBERED_START.match(raw_line)
        if qmatch:
            current = {
                "number": int(qmatch.group(1)), "prompt": "", "options": [],
                "section_instruction": section_instruction,
                "section_number": section_number,
            }
            groups.append(current)
            current_option = None
            line = qmatch.group(2).strip()
            if not line:
                continue
        if current is None:
            continue

        option_parts = _option_parts(line)
        if option_parts:
            # Include stem text preceding inline A. in the question prompt.
            first_marker = OPTION_MARKER.search(line)
            prefix = line[:first_marker.start()].strip(" \t-*:#") if first_marker else ""
            if prefix:
                current["prompt"] += ("\n" if current["prompt"] else "") + prefix
            for label, content in option_parts:
                if any(option["label"] == label for option in current["options"]):
                    # A repeated label is ambiguous; preserve its raw text as continuation.
                    target = current_option or current["options"][-1]
                    target["text"] += "\n" + f"{label}. {content}"
                    continue
                current_option = {"label": label, "text": content}
                current["options"].append(current_option)
        elif current_option:
            current_option["text"] += "\n" + line
        else:
            current["prompt"] += ("\n" if current["prompt"] else "") + line

    if (groups and groups[0]["number"] == 2 and groups[0].get("section_number") == "I"
            and groups[0].get("section_instruction") and not any(item["number"] == 1 for item in groups)):
        # The section instruction explicitly describes letter choices and OCR's
        # numbering gap indicates Q1 is missing. Keep an empty, reviewable MCP record
        # so its image can be cropped, but never copy the section heading into Q1 or
        # fabricate question text/options.
        groups.insert(0, {
            "number": 1, "prompt": "", "options": [], "section_instruction": "",
            "section_number": "I", "parse_fallback": True,
        })

    slug = _slug(exam_name)
    questions = []
    for item in groups:
        options = item["options"]
        prompt = item["prompt"].strip()
        instruction = item.get("section_instruction", "").strip()
        if instruction:
            prompt = f"{instruction}\n{prompt}" if prompt else instruction
        is_mcp = bool(options) or bool(item.get("parse_fallback") and item.get("section_number") == "I")
        question = {
            "composite_id": f"{slug}_Q{item['number']:02d}",
            "question_id": item["number"],
            "type": "mcp" if is_mcp else "fill_in",
            "multiple_choice": False,
            "question_content": _block(prompt),
            "explanation_content": [],
            "mcp_options": [
                {"label": option["label"], "content": _block(option["text"]), "is_correct": False, "order_index": idx}
                for idx, option in enumerate(options)
            ],
            "match_pairs": [],
            "fill_in_answers": [],
        }
        if item.get("parse_fallback"):
            question["parse_fallback"] = True
        questions.append(question)
    return questions


def parse_questions_by_pages(page_texts: list[str], exam_name: str) -> list[dict]:
    """Parse each OCR page separately so page provenance survives extraction."""
    questions: list[dict] = []
    for page_number, page_text in enumerate(page_texts, start=1):
        for question in parse_questions(page_text, exam_name):
            question["page_number"] = page_number
            questions.append(question)
    return questions
