import unittest

from backend.engine.registered_option_geometry import (
    RegisteredOptionIndex,
    OptionSuggestion,
)


def build(rows, labels):

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

        labels,

        width=600,
        height=600,
    )


def row(
    n,
    labels="ABCD",
    missing="",
    shift=0,
):

    y = n * 70

    return [
        (
            f"Question {n}. Text",
            (0, y, 200, y + 20),
        )
    ] + [
        (
            f"{l}. value",

            (
                40 + i * 120 + shift,
                y + 25,
                100 + i * 120 + shift,
                y + 45,
            ),
        )

        for i, l in enumerate(labels)
        if l not in missing
    ]


class RegisteredOptionGeometryTests(unittest.TestCase):

    def test_direct_and_partial_page(self):

        index = build(
            row(1) + row(2),
            {
                1: "ABCD",
                2: "ABCD",
            },
        )

        self.assertEqual(
            index.associate(165, 105),

            OptionSuggestion(
                1,
                "B",
                "leading_region",
            ),
        )

        self.assertIsNone(
            index.associate(110, 105)
        )

    def test_missing_label(self):

        index = build(
            row(1)
            + row(2, missing="A")
            + row(3),

            {
                n: "ABCD"
                for n in (1, 2, 3)
            },
        )

        self.assertEqual(
            index.associate(45, 175),

            OptionSuggestion(
                2,
                "A",
                "repeated_columns",
            ),
        )

    def test_insufficient_reference_rows(self):

        index = build(
            row(1)
            + row(2, missing="A"),

            {
                1: "ABCD",
                2: "ABCD",
            },
        )

        self.assertIsNone(
            index.associate(45, 175)
        )

    def test_inconsistent_columns(self):

        index = build(
            row(1)
            + row(2, missing="A")
            + row(3, shift=50),

            {
                n: "ABCD"
                for n in (1, 2, 3)
            },
        )

        self.assertIsNone(
            index.associate(45, 175)
        )

    def test_merged_heading_rejected(self):

        rows = (
            row(1)
            + row(2, missing="A")
            + row(3)
        )

        rows[5] = (
            "Question 2A. value",
            (0, 140, 200, 185),
        )

        index = build(
            rows,

            {
                n: "ABCD"
                for n in (1, 2, 3)
            },
        )

        self.assertIsNone(
            index.associate(45, 175)
        )

    def test_multirow_five_options(self):

        rows = [
            (
                "Câu 1. Nội dung",
                (0, 20, 200, 40),
            ),
            (
                "(A) 1",
                (40, 50, 100, 70),
            ),
            (
                "B. 2",
                (160, 50, 220, 70),
            ),
            (
                "C. 3",
                (280, 50, 340, 70),
            ),
            (
                "D. 4",
                (40, 80, 100, 100),
            ),
            (
                "E. 5",
                (160, 80, 220, 100),
            ),
        ]

        index = build(
            rows,
            {1: "ABCDE"},
        )

        self.assertEqual(
            index.associate(165, 90),

            OptionSuggestion(
                1,
                "E",
                "leading_region",
            ),
        )

        self.assertIsNone(
            index.associate(285, 90)
        )

    def test_duplicate_label_rejected(self):

        rows = row(1) + [
            (
                "B. duplicate",
                (160, 120, 220, 140),
            )
        ]

        index = build(
            rows,
            {1: "ABCD"},
        )

        self.assertIsNone(
            index.associate(165, 105)
        )

    def test_two_competing_regions(self):

        rows = [
            (
                "Question 1. Text",
                (0, 20, 200, 40),
            ),
            (
                "A. 1",
                (40, 50, 100, 70),
            ),
            (
                "B. 2",
                (45, 50, 105, 70),
            ),
        ]

        index = build(
            rows,
            {1: "AB"},
        )

        self.assertIsNone(
            index.associate(50, 60)
        )

    def test_second_heading_lane_rejected(self):

        rows = row(1) + row(2)

        rows[5] = (
            "Question 2. Text",
            (300, 140, 500, 160),
        )

        with self.assertRaises(ValueError):

            build(
                rows,
                {
                    1: "ABCD",
                    2: "ABCD",
                },
            )

    def test_unknown_heading_or_invalid_box(self):

        with self.assertRaises(ValueError):

            build(
                row(1) + row(2),
                {1: "ABCD"},
            )

        with self.assertRaises(ValueError):

            build(
                row(1) + [
                    (
                        "A. text",
                        (-1, 140, 100, 160),
                    )
                ],

                {1: "ABCD"},
            )

    def test_no_cross_label_count_learning(self):

        index = build(
            row(1)
            + row(2, missing="A")
            + row(3, labels="ABC"),

            {
                1: "ABCD",
                2: "ABCD",
                3: "ABC",
            },
        )

        self.assertIsNone(
            index.associate(45, 175)
        )

    def test_missing_two_labels_needs_regions(self):

        rows = (
            row(1)
            + row(2, missing="CD")
            + row(3)
        )

        labels = {
            n: "ABCD"
            for n in (1, 2, 3)
        }

        self.assertIsNone(
            build(
                rows,
                labels,
            ).associate(405, 175)
        )

        rows[8:8] = [
            (
                "unlabeled C",
                (280, 165, 340, 185),
            ),
            (
                "unlabeled D",
                (400, 165, 460, 185),
            ),
        ]

        self.assertEqual(
            build(
                rows,
                labels,
            ).associate(405, 175),

            OptionSuggestion(
                2,
                "D",
                "repeated_columns",
            ),
        )


if __name__ == "__main__":
    unittest.main()