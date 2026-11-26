"""Offline, deterministic text-parser baseline. No provider or database calls."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, replace

from .engine.parser_pipeline import normalize_option_layout, parse_text_questions


Block = tuple[str, str]
Choice = tuple[str, Block]


@dataclass(frozen=True)
class QuestionSpec:
    stem: Block
    choices: tuple[Choice, ...]

    def fields(self):
        return {
            "stem": (self.stem,),
            **{
                f"option:{label}": (block,)
                for label, block in self.choices
            },
        }

    def page(self):
        return "\n".join(
            [f"Câu 1. {self.stem[1]}"]
            + [
                f"{label}. {block[1]}"
                for label, block in self.choices
            ]
        )


@dataclass(frozen=True)
class Case:
    case_id: str
    subject: str
    observed: QuestionSpec
    expected: QuestionSpec


def text(value):
    return ("text", value)


def spec(stem, *options):
    return QuestionSpec(
        stem,
        tuple(
            (chr(65 + i), block)
            for i, block in enumerate(options)
        ),
    )


def change_option(question, label, block):
    if label not in dict(question.choices):
        raise ValueError("Unknown option label.")

    return replace(
        question,
        choices=tuple(
            (key, block if key == label else value)
            for key, value in question.choices
        ),
    )


def fixtures():
    # Ground truth is authored independently.
    # Never derive it from parser output.

    english = spec(
        text("Choose the correct word."),
        text("destroyed"),
        text("performed"),
        text("wanted"),
        text("combined"),
    )

    literature = spec(
        text("Chọn từ được gạch chân."),
        text("người\u0332"),
        text("nhà"),
        text("cây"),
    )

    math = spec(
        ("latex", "Tính $x/2$ khi x = 6."),
        text("1"),
        text("2"),
        text("3"),
        text("4"),
        text("5"),
    )

    chemistry = spec(
        text("Chọn công thức hóa học."),
        text("H₂SO₄"),
        text("H₂O"),
        text("NaCl"),
        text("CO₂"),
    )

    physics = spec(
        text("Đơn vị của lực là ________ trong hệ SI."),
        text("N"),
        text("Pa"),
        text("J"),
        text("W"),
    )

    biology = spec(
        text("Quá trình quang hợp diễn ra ở đâu?"),
        text("Lá"),
        text("Rễ"),
        text("Thân"),
        text("Hoa"),
    )

    history = spec(
        text("Sự kiện diễn ra năm 1954."),
        text("1945"),
        text("1954"),
        text("1975"),
        text("1986"),
    )

    computing = spec(
        text("Chọn độ phức tạp của thuật toán."),
        text("O(n²)"),
        text("O(n)"),
        text("O(1)"),
        text("O(log n)"),
    )

    pairs = (
        (
            "en",
            "English",
            english,
            change_option(
                english, "A", text("destroy/ed")
            ),
        ),
        (
            "vi",
            "Literature",
            literature,
            change_option(
                literature, "A", text("người")
            ),
        ),
        (
            "ma",
            "Math",
            math,
            replace(
                math,
                stem=("latex", "Tính $x/2$ khi = 6."),
            ),
        ),
        (
            "ch",
            "Chemistry",
            chemistry,
            change_option(
                chemistry, "A", text("H₂S0₄")
            ),
        ),
        (
            "ph",
            "Physics",
            physics,
            replace(
                physics,
                stem=text(
                    "Đơn vị của lực là trong hệ SI."
                ),
            ),
        ),
        (
            "bi",
            "Biology",
            biology,
            replace(
                biology,
                stem=text(
                    "Quá trình quanghợp diễn ra ở đâu?"
                ),
            ),
        ),
        (
            "hi",
            "History",
            history,
            replace(
                history,
                stem=text(
                    "Sự kiện diễn ra năm 195 4."
                ),
            ),
        ),
        (
            "it",
            "Computing",
            computing,
            change_option(
                computing, "A", text("O(n2)")
            ),
        ),
    )

    return tuple(
        case
        for key, subject, truth, damaged in pairs
        for case in (
            Case(
                key + "-clean",
                subject,
                truth,
                truth,
            ),
            Case(
                key + "-damaged",
                subject,
                damaged,
                truth,
            ),
        )
    )


def _blocks(items):
    if not isinstance(items, list) or not all(
        isinstance(b, dict)
        and isinstance(b.get("type"), str)
        and isinstance(b.get("value"), str)
        for b in items
    ):
        return None

    return tuple(
        (b["type"], b["value"])
        for b in items
    )


def score_case(case, questions):
    """
    Compare exact block type/value.

    Never silently normalize OCR content.
    Structural failures are reported separately.
    """
    before = case.observed.fields()
    truth = case.expected.fields()

    if before.keys() != truth.keys():
        raise ValueError(
            "Fixture must use matching field identities."
        )

    errors = []
    actual = {}

    if (
        not isinstance(questions, list)
        or len(questions) != 1
        or not isinstance(questions[0], dict)
    ):
        errors.append("expected_one_question")

    else:
        q = questions[0]

        if (
            q.get("page_number") != 1
            or q.get("question_id") != 1
        ):
            errors.append("wrong_question_identity")

        else:
            actual["stem"] = _blocks(
                q.get("question_content")
            )

            options = q.get("mcp_options")

            if not isinstance(options, list) or not all(
                isinstance(o, dict)
                and isinstance(o.get("label"), str)
                for o in options
            ):
                errors.append("invalid_options")
                options = []

            labels = [
                o["label"]
                for o in options
            ]

            expected_labels = [
                label
                for label, _ in case.expected.choices
            ]

            if labels != expected_labels:
                errors.append(
                    "wrong_option_labels_or_order"
                )

            for option in options:
                label = option["label"]

                if labels.count(label) == 1:
                    actual[f"option:{label}"] = _blocks(
                        option.get("content")
                    )

            if (
                q.get("type") != "mcp"
                or q.get("multiple_choice") is not False
            ):
                errors.append("wrong_question_type")

            if (
                any(
                    o.get("is_correct") is not False
                    for o in options
                )
                or q.get("fill_in_answers")
                or q.get("match_pairs")
            ):
                errors.append("unexpected_answer_data")

    fields = []

    for key, expected in truth.items():
        damaged = before[key] != expected
        value = actual.get(key)

        if value is None:
            status = "missing"

        elif value == expected:
            status = (
                "recovered"
                if damaged
                else "preserved"
            )

        elif not damaged:
            status = "clean_corrupted"

        elif value == before[key]:
            status = "unrecovered"

        else:
            status = "incorrect_repair"

        fields.append({
            "field": key,
            "damaged": damaged,
            "status": status,
            "observed": before[key],
            "expected": expected,
            "actual": value,
        })

    return {
        "case_id": case.case_id,
        "subject": case.subject,
        "errors": errors,
        "fields": fields,
        "exact": (
            not errors
            and all(
                f["status"] in ("recovered", "preserved")
                for f in fields
            )
        ),
    }


def summarize(results):
    fields = [
        f
        for result in results
        for f in result["fields"]
    ]

    counts = Counter(
        f["status"]
        for f in fields
    )

    damaged = sum(
        f["damaged"]
        for f in fields
    )

    clean = len(fields) - damaged

    return {
        "cases": len(results),
        "exact_cases": sum(
            r["exact"]
            for r in results
        ),
        "structural_error_cases": sum(
            bool(r["errors"])
            for r in results
        ),
        "damaged_fields": damaged,
        "recovered_fields": counts["recovered"],
        "clean_fields": clean,
        "preserved_fields": counts["preserved"],
        "unrecovered_fields": counts["unrecovered"],
        "incorrect_repairs": counts["incorrect_repair"],
        "clean_corrupted_fields": counts["clean_corrupted"],
        "missing_fields": counts["missing"],
    }


def run(parser=parse_text_questions):
    results = []

    for case in fixtures():
        pages = normalize_option_layout(
            [case.observed.page()]
        )

        questions = parser(
            pages,
            "benchmark",
        )

        results.append(
            score_case(case, questions)
        )

    return {
        "version": 1,
        "scope": "synthetic_text_parser",
        "summary": summarize(results),
        "cases": results,
    }


def main():
    print(
        json.dumps(
            run(),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
