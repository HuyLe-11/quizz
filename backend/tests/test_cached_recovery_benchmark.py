import copy
import unittest

from backend.cached_recovery_benchmark import (
    LOW,
    STEMS,
    WORDS,
    score,
)


def fixture():
    questions = []

    for n in range(1, 18):
        words = WORDS.get(
            n,
            ("a", "b", "c", "d"),
        )

        values = list(words)

        if n in WORDS:
            count = 2 if n == 2 else 1

            values = [
                w[:-count]
                + "".join(
                    c + LOW
                    for c in w[-count:]
                )
                for w in words
            ]

        questions.append({
            "page_number": 1,
            "question_id": n,
            "type": "mcp",
            "multiple_choice": False,
            "section_instruction": "Instruction",
            "question_content": [
                {
                    "type": "text",
                    "value": "Instruction",
                },
                {
                    "type": "text",
                    "value": STEMS.get(n, "Stem"),
                },
            ],
            "mcp_options": [
                {
                    "label": label,
                    "content": [
                        {
                            "type": "text",
                            "value": value,
                        },
                    ],
                    "is_correct": False,
                }
                for label, value in zip(
                    "ABCD",
                    values,
                )
            ],
        })

    return questions


class CachedRecoveryBenchmarkTests(unittest.TestCase):

    def test_complete_fixture(self):
        result = score(fixture())

        self.assertEqual(
            (
                result["passed"],
                result["total"],
            ),
            (19, 19),
        )

        self.assertEqual(
            result["structure_errors"],
            [],
        )

    def test_missing_format_does_not_fail_text(self):
        questions = fixture()

        questions[1]["mcp_options"][0]["content"][0][
            "value"
        ] = "destroyed"

        result = score(questions)

        failed = [
            c["check"]
            for c in result["checks"]
            if not c["passed"]
        ]

        self.assertEqual(
            failed,
            ["2:A:format"],
        )

    def test_missing_blank(self):
        questions = fixture()

        questions[6]["question_content"][-1][
            "value"
        ] = STEMS[7].replace(
            " ________",
            "",
        )

        result = score(questions)

        self.assertEqual(
            [
                c["check"]
                for c in result["checks"]
                if not c["passed"]
            ],
            ["7:stem"],
        )

    def test_missing_and_duplicate_question(self):
        for mode in (
            "missing",
            "duplicate",
        ):
            questions = fixture()

            if mode == "missing":
                questions.pop(1)

            else:
                questions.append(
                    copy.deepcopy(questions[1])
                )

            result = score(questions)

            self.assertTrue(
                result["structure_errors"]
            )

            self.assertEqual(
                result["passed"],
                11,
            )

    def test_answers_and_option_order(self):
        questions = fixture()

        questions[0]["mcp_options"][0][
            "is_correct"
        ] = True

        questions[2]["mcp_options"].reverse()

        errors = score(questions)[
            "structure_errors"
        ]

        self.assertEqual(
            len(errors),
            2,
        )

        self.assertTrue(
            any(
                e.startswith("unexpected_answer_data")
                for e in errors
            )
        )

        self.assertTrue(
            any(
                e.startswith("unexpected_structure")
                for e in errors
            )
        )

    def test_does_not_mutate_questions(self):
        questions = fixture()

        before = copy.deepcopy(questions)

        score(questions)

        self.assertEqual(
            questions,
            before,
        )


if __name__ == "__main__":
    unittest.main()
