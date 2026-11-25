"""Conservative structural review of parsed questions, without guessing answers."""
from __future__ import annotations

import re
from typing import Any

FOUR_CHOICES = re.compile(
    r"\bA(?:\s*[,，]\s*|\s+)B(?:\s*[,，]\s*|\s+)"
    r"C(?:\s*[,，]\s*|\s+)"
    r"(?:(?:or|and|hoặc|hoac|và|va)\s+)?D\b"
    r"(?!\s*(?:[,，]\s*)?(?:(?:or|and|hoặc|hoac|và|va)\s+)?E\b)",
    re.IGNORECASE,
)

MESSAGES = {
    "choice_count_too_low":
        "Câu trắc nghiệm có ít hơn hai lựa chọn.",

    "choice_labels_invalid":
        "Nhãn lựa chọn bị thiếu, trùng hoặc sai thứ tự.",

    "choice_content_empty":
        "Có lựa chọn không có nội dung.",

    "expected_four_choices":
        "Chỉ dẫn yêu cầu A–D nhưng lựa chọn "
        "không đầy đủ hoặc không đúng thứ tự.",
}


def _has_content(blocks: Any) -> bool:
    return isinstance(blocks, list) and any(
        isinstance(block, dict)
        and isinstance(block.get("value"), str)
        and bool(block["value"].strip())
        for block in blocks
    )


def review_question_structure(
    questions: list[dict],
) -> dict:
    """Add review flags only; never modify content, options or answers."""

    items = []
    structural_count = 0

    for index, question in enumerate(
        questions,
        start=1,
    ):
        options = question.get("mcp_options") or []

        labels = [
            option.get("label")
            for option in options
        ]

        instruction = str(
            question.get("section_instruction") or ""
        )

        expected_four = bool(
            FOUR_CHOICES.search(instruction)
        )

        issues = []

        if question.get("type") == "mcp":
            if len(options) < 2:
                issues.append(
                    "choice_count_too_low"
                )

            if labels != list(
                "ABCDEFGH"[:len(labels)]
            ):
                issues.append(
                    "choice_labels_invalid"
                )

            if any(
                not _has_content(option.get("content"))
                for option in options
            ):
                issues.append(
                    "choice_content_empty"
                )

        # A question with all choices missing may
        # have been represented as fill_in.
        # Preserve its original type.
        if expected_four and (
            question.get("type") == "mcp"
            or not options
        ) and labels != list("ABCD"):
            issues.append(
                "expected_four_choices"
            )

        if issues:
            structural_count += 1

            question["parse_fallback"] = True
            question["requires_source_image"] = True

            if not question.get("parser_warning"):
                question["parser_warning"] = " ".join(
                    MESSAGES[issue]
                    for issue in issues
                )

        if not (
            question.get("parse_fallback")
            or question.get("requires_source_image")
            or question.get("parser_warning")
        ):
            continue

        items.append({
            "question_index": index,

            "question_number": question.get(
                "source_question_number",
                question.get("question_id"),
            ),

            "page_number": question.get(
                "page_number"
            ),

            "issue_codes": (
                issues or ["existing_review_flag"]
            ),

            "actual_labels": labels,

            "expected_labels": (
                list("ABCD")
                if expected_four
                else None
            ),

            "warning": question.get(
                "parser_warning"
            ),

            "requires_source_image": bool(
                question.get("requires_source_image")
            ),
        })

    return {
        "review_items": items,

        "review_summary": {
            "total_questions": len(questions),
            "needs_review": len(items),
            "structural_issues": structural_count,
        },
    }