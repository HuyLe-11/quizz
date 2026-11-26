import copy
import unittest

from backend.tests import (
    test_ocr_blank_recovery as blank_tests,
)

from backend.tests import (
    test_ocr_suffix_reconciliation as suffix_tests,
)


class OCRPipelineCompatibilityTests(unittest.TestCase):

    def fixture(self, cls):
        case = cls()

        self.addCleanup(
            case.doCleanups
        )

        case.setUp()

        return case

    def test_blank_with_embedded_section_instruction(
        self,
    ):
        f = self.fixture(
            blank_tests.BlankRecoveryTests
        )

        context = {
            "type": "text",

            "value": f.q["section_instruction"],
        }

        f.q["question_content"].insert(
            0,
            context,
        )

        self.assertEqual(
            len(f.recover()),
            1,
        )

        self.assertEqual(
            f.q["question_content"],

            [
                context,

                {
                    "type": "text",

                    "value": (
                        "We ________ need more "
                        "time to finish the task."
                    ),
                },
            ],
        )

        f.assert_unchanged()

    def test_blank_with_shared_context(
        self,
    ):
        f = self.fixture(
            blank_tests.BlankRecoveryTests
        )

        f.q["shared_passage"] = (
            "Shared passage."
        )

        prefix = [
            {
                "type": "text",

                "value": f.q[key],
            }

            for key in (
                "section_instruction",
                "shared_passage",
            )
        ]

        f.q["question_content"][:0] = (
            copy.deepcopy(prefix)
        )

        self.assertEqual(
            len(f.recover()),
            1,
        )

        self.assertEqual(
            f.q["question_content"][:2],

            prefix,
        )

        self.assertIn(
            "________",

            f.q["question_content"][-1]["value"],
        )

    def test_blank_rejects_unknown_or_extra_context(
        self,
    ):
        f = self.fixture(
            blank_tests.BlankRecoveryTests
        )

        f.q["question_content"].insert(
            0,

            {
                "type": "text",

                "value": "Unrelated text",
            },
        )

        f.assert_unchanged()

        f.q["question_content"][0]["value"] = (
            f.q["section_instruction"]
        )

        f.q["question_content"].append(
            {
                "type": "text",

                "value": "Unrelated text",
            }
        )

        f.assert_unchanged()

    def test_suffix_with_combining_low_line(
        self,
    ):
        f = self.fixture(
            suffix_tests.SuffixReconciliationTests
        )

        f.block(0).update(
            type="text",

            value="park s\u0332 2",
        )

        f.block(2).update(
            type="text",

            value="system s\u0332",
        )

        self.assertEqual(
            f.repair()[0]["changed_options"],

            ["A", "C"],
        )

        self.assertEqual(
            f.block(0)["value"],

            "parks\u0332",
        )

        self.assertEqual(
            f.block(2)["value"],

            "systems\u0332",
        )

        self.assertIn(
            "số 2",

            f.q["parser_warning"],
        )

        self.assertEqual(
            f.repair(),

            [],
        )

    def test_suffix_without_explicit_underline_rejected(
        self,
    ):
        f = self.fixture(
            suffix_tests.SuffixReconciliationTests
        )

        f.block(0).update(
            type="text",

            value="park s 2",
        )

        f.block(2).update(
            type="text",

            value="system s",
        )

        f.assert_no_change()

    def test_suffix_mixed_markup_changes_only_needed_block(
        self,
    ):
        f = self.fixture(
            suffix_tests.SuffixReconciliationTests
        )

        f.block(2).update(
            type="text",

            value="systems\u0332",
        )

        self.assertEqual(
            f.repair()[0]["changed_options"],

            ["A"],
        )

        self.assertEqual(
            f.block(0)["value"],

            "parks\u0332",
        )

        self.assertEqual(
            f.block(2)["value"],

            "systems\u0332",
        )

        self.assertEqual(
            f.repair(),

            [],
        )


if __name__ == "__main__":
    unittest.main()