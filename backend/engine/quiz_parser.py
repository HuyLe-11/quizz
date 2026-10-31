"""Conservative Markdown-to-quiz parser; answer keys are never inferred."""

import re
import unicodedata


QUESTION_START = re.compile(r"^\s*(?:#{1,6}\s*)?(?:Câu|Cau|Question|Q)\s*(\d+)\s*[.:)]?\s*(.*)$", re.I)
NUMBERED_START = re.compile(r"^\s*(\d{1,3})[.)]\s+(.+)$")
OPTION_START = re.compile(r"^\s*(?:[-*+]\s*)?(?:\(([A-H])\)|([A-H])[.)])\s*(.+)$", re.I)


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


def parse_questions(markdown: str, exam_name: str) -> list[dict]:
    groups: list[dict] = []
    current = None
    current_option = None
    for line in markdown.splitlines():
        qmatch = QUESTION_START.match(line)
        if not qmatch:
            qmatch = NUMBERED_START.match(line)
        if qmatch:
            current = {"number": int(qmatch.group(1)), "prompt": qmatch.group(2).strip(), "options": []}
            groups.append(current)
            current_option = None
            continue
        if current is None:
            continue
        omatch = OPTION_START.match(line)
        if omatch:
            current_option = {"label": (omatch.group(1) or omatch.group(2)).upper(), "text": omatch.group(3).strip()}
            current["options"].append(current_option)
        elif line.strip():
            if current_option:
                current_option["text"] += "\n" + line.strip()
            else:
                current["prompt"] += "\n" + line.strip()

    slug = _slug(exam_name)
    questions = []
    for item in groups:
        options = item["options"]
        qtype = "mcp" if options else "fill_in"
        questions.append({
            "composite_id": f"{slug}_Q{item['number']:02d}",
            "question_id": item["number"],
            "type": qtype,
            "multiple_choice": False,
            "question_content": _block(item["prompt"]),
            "explanation_content": [],
            "mcp_options": [
                {"label": option["label"], "content": _block(option["text"]), "is_correct": False, "order_index": idx}
                for idx, option in enumerate(options)
            ],
            "match_pairs": [],
            "fill_in_answers": [],
        })
    return questions


def parse_questions_by_pages(page_texts: list[str], exam_name: str) -> list[dict]:
    """Parse each OCR page separately so page provenance survives extraction."""
    questions: list[dict] = []
    for page_number, page_text in enumerate(page_texts, start=1):
        page_questions = parse_questions(page_text, exam_name)
        for question in page_questions:
            question["page_number"] = page_number
            questions.append(question)
    return questions
