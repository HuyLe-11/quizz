import unittest

from backend.engine.registered_option_geometry import (
    OptionSuggestion,
    RegisteredOptionIndex,
)


def row(
    n,
    *,
    prefix=None,
    shift=0,
    missing="",
    first_box=None,
):
    y = 60 * n

    text = prefix or f"Question {n}. A. value"

    box = first_box or (
        0,
        y,
        200,
        y + 30,
    )

    return [(text, box)] + [
        (
            f"{label}. value",
            (
                240 + i * 150 + shift,
                y + 5,
                320 + i * 150 + shift,
                y + 25,
            ),
        )
        for i, label in enumerate("BCD")
        if label not in missing
    ]


def build(rows, numbers=(1, 2, 3)):
    return RegisteredOptionIndex(
        {
            "rec_texts": [
                t for t, b in rows
            ],
            "rec_boxes": [
                b for t, b in rows
            ],
            "rec_scores": [
                .99
            ] * len(rows),
        },
        {
            n: "ABCD"
            for n in numbers
        },
        width=900,
        height=600,
    )


class RegisteredMergedHeadingTests(unittest.TestCase):

    def test_merged_first_option_with_two_references(self):
        index = build(
            row(1) + row(2) + row(3)
        )

        self.assertEqual(
            index.associate(124, 195),
            OptionSuggestion(
                3,
                "A",
                "repeated_columns",
            ),
        )

        self.assertEqual(
            index.associate(245, 195),
            OptionSuggestion(
                3,
                "B",
                "leading_region",
            ),
        )

    def test_question_number_and_answer_body_are_not_labels(self):
        index = build(
            row(1) + row(2) + row(3)
        )

        for x in (25, 95, 160, 190):
            with self.subTest(x=x):
                self.assertIsNone(
                    index.associate(x, 195)
                )

    def test_only_one_reference_rejected(self):
        index = build(
            row(1) + row(3),
            (1, 3),
        )

        self.assertIsNone(
            index.associate(124, 195)
        )

    def test_inconsistent_peer_columns_rejected(self):
        index = build(
            row(1)
            + row(2, shift=40)
            + row(3)
        )

        self.assertIsNone(
            index.associate(124, 195)
        )

    def test_inconsistent_first_label_position_rejected(self):
        index = build(
            row(1)
            + row(
                2,
                first_box=(
                    0,
                    120,
                    150,
                    150,
                ),
            )
            + row(3)
        )

        self.assertIsNone(
            index.associate(124, 195)
        )

    def test_plain_heading_or_multiple_embedded_options_rejected(self):
        for text in (
            "Question 3. A value",
            "Question 3. A. value B. other",
            "Question 3. B. value",
        ):
            with self.subTest(text=text):
                index = build(
                    row(1)
                    + row(2)
                    + row(
                        3,
                        prefix=text,
                    )
                )

                self.assertIsNone(
                    index.associate(124, 195)
                )

    def test_missing_peer_or_outside_common_row_rejected(self):
        index = build(
            row(1)
            + row(2)
            + row(3, missing="D")
        )

        self.assertIsNone(
            index.associate(124, 195)
        )

        index = build(
            row(1)
            + row(2)
            + row(3)
        )

        self.assertIsNone(
            index.associate(124, 182)
        )


if __name__ == "__main__":
    unittest.main()