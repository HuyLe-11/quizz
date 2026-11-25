import copy
import json
import tempfile
import unittest

from pathlib import Path
from unittest.mock import Mock, patch

import pymupdf

from backend.engine.registered_mark_snapshot import (
    collect_registered_marks,
)

from backend.engine.registered_mark_detection import (
    PageMarkResult,
    RegisteredMark,
)


def question(
    ident,
    source,
    page=1,
    **kw,
):
    return {
        "id": ident,

        "question_id": source + 100,

        "source_question_number": source,

        "page_number": page,

        "type": "mcp",

        "multiple_choice": False,

        "mcp_options": [
            {
                "label": "A",
                "is_correct": True,
            },
            {
                "label": "B",
                "is_correct": False,
            },
        ],

        **kw,
    }


def mark(
    number,
    label="B",
    box=(1., 2., 10., 12.),
):
    return RegisteredMark(
        number,
        label,
        box,
        "local_affine",
        "leading_region",
    )


class SnapshotTests(unittest.TestCase):

    def setUp(self):

        folder = tempfile.TemporaryDirectory()

        self.addCleanup(
            folder.cleanup
        )

        self.pdf = (
            Path(folder.name)
            / "source.pdf"
        )

        with pymupdf.open() as doc:

            doc.new_page(
                width=100,
                height=100,
            )

            doc.new_page(
                width=100,
                height=100,
            )

            doc.save(self.pdf)

        self.pages = [
            {
                "pruned_result": {
                    "rec_boxes": [
                        [1, 2, 10, 12]
                    ]
                }
            }

            for _ in range(2)
        ]

        self.questions = [
            question("q1", 1),

            question("q2", 1, 2),
        ]

        self.cap = Mock()

        self.cap.detect_page.return_value = (
            PageMarkResult(
                "processed",

                suggestions=(
                    mark(1),
                ),
            )
        )

        self.cache = object()

    def collect(self):

        with patch.dict(
            "os.environ",

            {
                "QUIZPDF_REGISTERED_MARKS_ENABLED": "1",
            },
        ):

            return collect_registered_marks(
                self.pdf,

                self.pages,

                self.questions,

                self.cap,

                cache=self.cache,
            )

    def test_disabled(self):

        with patch.dict(
            "os.environ",

            {
                "QUIZPDF_REGISTERED_MARKS_ENABLED": "0",
            },
        ):

            result = collect_registered_marks(
                self.pdf,

                self.pages,

                self.questions,

                self.cap,
            )

        self.assertEqual(
            result["status"],
            "disabled",
        )

        self.cap.detect_page.assert_not_called()

    def test_source_numbers_page_identity_and_no_mutation(
        self,
    ):

        before = copy.deepcopy(
            self.questions
        )

        result = self.collect()

        self.assertEqual(
            set(result["by_question"]),

            {
                "q1",
                "q2",
            },
        )

        self.assertEqual(
            result["by_question"]["q2"]["page_number"],

            2,
        )

        self.assertEqual(
            result["by_question"]["q1"]["detected_answer"],

            "B",
        )

        self.assertEqual(
            self.questions,

            before,
        )

        self.assertEqual(
            self.cap.detect_page.call_count,

            2,
        )

        for i, call in enumerate(
            self.cap.detect_page.call_args_list
        ):

            self.assertIs(
                call.args[1],

                self.pages[i],
            )

            self.assertEqual(
                call.args[2],

                {
                    1: ("A", "B"),
                },
            )

        json.dumps(
            result,

            allow_nan=False,
        )

    def test_conflict_precedes_suggestion(self):

        self.cap.detect_page.return_value = (
            PageMarkResult(
                "processed",

                conflicted_questions=(1,),

                suggestions=(
                    mark(1),
                ),
            )
        )

        result = self.collect()[
            "by_question"
        ]["q1"]

        self.assertEqual(
            result["status"],

            "conflict",
        )

        self.assertNotIn(
            "detected_answer",

            result,
        )

    def test_invalid_label_and_coordinates(self):

        self.cap.detect_page.side_effect = [
            PageMarkResult(
                "processed",

                suggestions=(
                    mark(1, "C"),
                ),
            ),

            PageMarkResult(
                "processed",

                suggestions=(
                    mark(
                        1,

                        box=(-1, 0, 10, 12),
                    ),
                ),
            ),
        ]

        self.assertEqual(
            self.collect()["by_question"],

            {},
        )

    def test_duplicate_source_number_skips_page(self):

        self.questions.append(
            question(
                "q3",
                1,
            )
        )

        result = self.collect()

        self.assertEqual(
            set(result["by_question"]),

            {"q2"},
        )

        self.cap.detect_page.assert_called_once()

    def test_page_count_mismatch(self):

        self.pages.pop()

        self.assertEqual(
            self.collect()["status"],

            "unavailable",
        )

        self.cap.detect_page.assert_not_called()

    def test_wrong_page_binding(self):

        self.pages[0][
            "source_page_number"
        ] = 2

        self.assertEqual(
            set(
                self.collect()["by_question"]
            ),

            {"q2"},
        )

    def test_unavailable_and_budget(self):

        self.cap.detect_page.return_value = (
            PageMarkResult(
                "unavailable",

                "ocr_images_unavailable",
            )
        )

        with patch(
            "backend.engine.registered_mark_snapshot.MAX_PAGES",

            1,
        ):

            result = self.collect()

        self.assertEqual(
            result["status"],

            "partial",
        )

        self.assertEqual(
            result["by_question"],

            {},
        )

        self.cap.detect_page.assert_called_once()

    def test_multi_select_skips_page(self):

        self.questions[0][
            "multiple_choice"
        ] = True

        self.assertEqual(
            set(
                self.collect()["by_question"]
            ),

            {"q2"},
        )


if __name__ == "__main__":
    unittest.main()