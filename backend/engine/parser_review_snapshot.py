"""Build a stable, JSON-safe import-time review report for a saved exam."""
from __future__ import annotations

from .parser_structure_review import review_question_structure


def build_parser_review_snapshot(
    questions: list[dict],
) -> dict:
    """Use persisted question IDs rather than source numbering as identity.

    Validation runs on shallow copies: the shared validator only writes
    top-level flags. The imported questions and their answers are untouched.
    """
    ids = [
        question["id"]
        for question in questions
    ]

    if any(
        not isinstance(value, str) or not value
        for value in ids
    ):
        raise ValueError(
            "Review snapshot requires assigned question IDs."
        )

    if len(set(ids)) != len(ids):
        raise ValueError(
            "Review snapshot requires unique question IDs."
        )

    report = review_question_structure([
        dict(question)
        for question in questions
    ])

    items = []

    for item in report["review_items"]:
        question = questions[
            item["question_index"] - 1
        ]

        asset = question.get("asset") or {}

        items.append({
            **item,

            "question_record_id": question["id"],

            "question_id": question["question_id"],

            "crop_status": asset.get(
                "crop_status"
            ),
        })

    return {
        "schema_version": 1,

        "stage": "import",

        "review_summary": report[
            "review_summary"
        ],

        "review_items": items,
    }