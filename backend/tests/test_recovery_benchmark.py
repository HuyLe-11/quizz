import copy
import unittest

from backend.engine.parser_pipeline import (
    normalize_option_layout,
    parse_text_questions,
)

from backend.recovery_benchmark import (
    fixtures,
    run,
    score_case,
    summarize,
)


class RecoveryBenchmarkTests(unittest.TestCase):

    def setUp(self):
        self.clean, self.damaged = fixtures()[:2]

    def parse(self, case):
        return parse_text_questions(
            normalize_option_layout(
                [case.observed.page()]
            ),
            "benchmark",
        )

    def status(self, result, key):
        return next(
            f["status"]
            for f in result["fields"]
            if f["field"] == key
        )

    def test_fixture_pairs_cover_eight_subjects(self):
        cases = fixtures()

        self.assertEqual(len(cases), 16)

        self.assertEqual(
            len({c.case_id for c in cases}),
            16,
        )

        self.assertEqual(
            len({c.subject for c in cases}),
            8,
        )

        for clean, damaged in zip(
            cases[::2],
            cases[1::2],
        ):
            self.assertEqual(
                clean.observed,
                clean.expected,
            )

            self.assertEqual(
                clean.expected,
                damaged.expected,
            )

            self.assertNotEqual(
                damaged.observed,
                damaged.expected,
            )

    def test_clean_preserved(self):
        result = score_case(
            self.clean,
            self.parse(self.clean),
        )

        self.assertTrue(result["exact"])

        self.assertTrue(
            all(
                f["status"] == "preserved"
                for f in result["fields"]
            )
        )

    def test_unrecovered(self):
        result = score_case(
            self.damaged,
            self.parse(self.damaged),
        )

        self.assertEqual(
            self.status(result, "option:A"),
            "unrecovered",
        )

    def test_recovered(self):
        result = score_case(
            self.damaged,
            self.parse(self.clean),
        )

        self.assertTrue(result["exact"])

        self.assertEqual(
            self.status(result, "option:A"),
            "recovered",
        )

    def test_incorrect_repair(self):
        questions = self.parse(self.damaged)

        questions[0]["mcp_options"][0]["content"][0][
            "value"
        ] = "destroy"

        result = score_case(
            self.damaged,
            questions,
        )

        self.assertEqual(
            self.status(result, "option:A"),
            "incorrect_repair",
        )

    def test_clean_corrupted(self):
        questions = self.parse(self.clean)

        questions[0]["mcp_options"][0]["content"][0][
            "value"
        ] = "destroy"

        result = score_case(
            self.clean,
            questions,
        )

        self.assertEqual(
            self.status(result, "option:A"),
            "clean_corrupted",
        )

    def test_kind_matters(self):
        questions = self.parse(self.clean)

        questions[0]["mcp_options"][0]["content"][0][
            "type"
        ] = "latex"

        self.assertEqual(
            self.status(
                score_case(self.clean, questions),
                "option:A",
            ),
            "clean_corrupted",
        )

    def test_missing_and_duplicate_questions(self):
        q = self.parse(self.clean)

        for output in ([], q + q):
            result = score_case(
                self.clean,
                output,
            )

            self.assertFalse(result["exact"])

            self.assertIn(
                "expected_one_question",
                result["errors"],
            )

            self.assertTrue(
                all(
                    f["status"] == "missing"
                    for f in result["fields"]
                )
            )

    def test_wrong_question_identity(self):
        questions = self.parse(self.clean)

        questions[0]["page_number"] = 2

        result = score_case(
            self.clean,
            questions,
        )

        self.assertIn(
            "wrong_question_identity",
            result["errors"],
        )

        self.assertFalse(result["exact"])

    def test_missing_duplicate_and_reordered_options(self):
        for mode in (
            "missing",
            "duplicate",
            "reordered",
        ):
            with self.subTest(mode=mode):
                questions = self.parse(self.clean)
                options = questions[0]["mcp_options"]

                if mode == "missing":
                    options.pop(0)

                elif mode == "duplicate":
                    options.append(
                        copy.deepcopy(options[0])
                    )

                else:
                    options.reverse()

                result = score_case(
                    self.clean,
                    questions,
                )

                self.assertIn(
                    "wrong_option_labels_or_order",
                    result["errors"],
                )

                self.assertFalse(result["exact"])

                if mode != "reordered":
                    self.assertEqual(
                        self.status(
                            result,
                            "option:A",
                        ),
                        "missing",
                    )

    def test_answer_and_question_type_protected(self):
        questions = self.parse(self.clean)

        questions[0]["mcp_options"][0][
            "is_correct"
        ] = True

        questions[0]["multiple_choice"] = True

        result = score_case(
            self.clean,
            questions,
        )

        self.assertIn(
            "unexpected_answer_data",
            result["errors"],
        )

        self.assertIn(
            "wrong_question_type",
            result["errors"],
        )

        self.assertFalse(result["exact"])

    def test_scoring_does_not_mutate_inputs(self):
        questions = self.parse(self.clean)

        before = copy.deepcopy(questions)

        score_case(
            self.clean,
            questions,
        )

        self.assertEqual(
            questions,
            before,
        )

    def test_baseline_denominators_and_determinism(self):
        first = run()

        self.assertEqual(
            first,
            run(),
        )

        summary = first["summary"]

        self.assertEqual(
            summary["cases"],
            16,
        )

        self.assertEqual(
            summary["damaged_fields"],
            8,
        )

        self.assertEqual(
            summary["clean_fields"],
            72,
        )

        self.assertEqual(
            sum(
                summary[k]
                for k in (
                    "recovered_fields",
                    "preserved_fields",
                    "unrecovered_fields",
                    "incorrect_repairs",
                    "clean_corrupted_fields",
                    "missing_fields",
                )
            ),
            80,
        )

    def test_summary_empty(self):
        self.assertTrue(
            all(
                value == 0
                for value in summarize([]).values()
            )
        )


if __name__ == "__main__":
    unittest.main()
