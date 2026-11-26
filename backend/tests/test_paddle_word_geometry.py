import sqlite3
import tempfile
import unittest

from contextlib import closing
from pathlib import Path

from backend.engine.ocr_job_journal import OCRJobJournal

from backend.paddle_word_geometry import (
    align_words,
    page_report,
    inspect_job,
)


def fixture():
    root = {
        "return_word_box": True,
        "model_settings": {
            "use_doc_preprocessor": False
        },
        "rec_texts": [
            "H₂O + NaCl"
        ],
        "rec_boxes": [
            [10, 10, 100, 30]
        ],
        "text_word": [
            ["H₂O", "+", "NaCl"]
        ],
        "text_word_boxes": [
            [
                [10, 10, 35, 30],
                [40, 10, 50, 30],
                [55, 10, 100, 30],
            ]
        ],
    }

    return {
        "pruned_result": root,
        "raw": {
            "prunedResult": root.copy()
        },
    }


class PaddleWordGeometryTests(unittest.TestCase):

    def test_exact_unicode_offsets(self):

        spans = align_words(
            "  a\u0301  x²",
            [0, 0, 50, 20],
            ["a\u0301", "x²"],
            [
                [0, 0, 10, 20],
                [15, 0, 50, 20],
            ],
        )

        self.assertEqual(
            [
                (s.start, s.end, s.text)
                for s in spans
            ],
            [
                (2, 4, "a\u0301"),
                (6, 8, "x²"),
            ],
        )

    def test_repeated_words(self):

        spans = align_words(
            "a a",
            [0, 0, 30, 20],
            ["a", "a"],
            [
                [0, 0, 10, 20],
                [20, 0, 30, 20],
            ],
        )

        self.assertEqual(
            [
                s.start
                for s in spans
            ],
            [0, 2],
        )

    def test_reject_uncovered_or_changed_text(self):

        for text in (
            "abc",
            "Ac",
            "a c!",
        ):
            with self.assertRaises(ValueError):

                align_words(
                    text,
                    [0, 0, 30, 20],
                    ["a", "c"],
                    [
                        [0, 0, 10, 20],
                        [20, 0, 30, 20],
                    ],
                )

    def test_invalid_geometry(self):

        for box in (
            [5, 0, 30, 20],
            [20, 0, 31, 20],
            [20, 0, 20, 20],
            [True, 0, 30, 20],
        ):
            with self.assertRaises(ValueError):

                align_words(
                    "ab",
                    [0, 0, 30, 20],
                    ["a", "b"],
                    [
                        [0, 0, 10, 20],
                        box,
                    ],
                )

        with self.assertRaisesRegex(
            ValueError,
            "outside_image",
        ):
            align_words(
                "a",
                [0, 0, 30, 20],
                ["a"],
                [[0, 0, 30, 20]],
                size=(29, 20),
            )

    def test_native_not_registered(self):

        report = page_report(
            fixture(),
            (120, 40),
            matches=["NaCl"],
        )

        self.assertEqual(
            report["coordinate_space"],
            "paddle_provider_unregistered",
        )

        self.assertEqual(
            report["line_status_counts"],
            {
                "aligned_native_coordinates": 1
            },
        )

        self.assertEqual(
            report["samples"][0]["spans"][2]["start"],
            6,
        )

        self.assertEqual(
            report["token_codepoint_length_counts"],
            {
                1: 1,
                3: 1,
                4: 1,
            },
        )

    def test_bad_row_preserved_for_diagnostic(self):

        p = fixture()

        p["pruned_result"]["text_word_boxes"][0].pop()

        report = page_report(p)

        self.assertEqual(
            report["line_status_counts"],
            {
                "invalid_word_arrays": 1
            },
        )

        self.assertEqual(
            report["samples"][0]["spans"],
            [],
        )

    def test_missing_configuration_and_conflicting_raw(self):

        p = fixture()

        p["pruned_result"]["return_word_box"] = False

        p["raw"]["prunedResult"]["return_word_box"] = False

        with self.assertRaisesRegex(
            ValueError,
            "configuration",
        ):
            page_report(p)

        p["raw"]["prunedResult"]["return_word_box"] = True

        with self.assertRaisesRegex(
            ValueError,
            "mismatch",
        ):
            page_report(p)

    def test_readonly_cache_and_checksum(self):

        with tempfile.TemporaryDirectory() as directory:

            path = (
                Path(directory)
                / "jobs.sqlite3"
            )

            journal = OCRJobJournal(
                "a" * 64,
                path,
            )

            key = journal.batch_key(
                0,
                1,
                "PP-OCRv6",
            )

            claim = journal.reserve(
                key
            ).record.claim_id

            journal.accept(
                key,
                claim,
                "job-1",
            )

            journal.finish_with_result(
                key,
                claim,
                "job-1",
                [fixture()],
                {
                    "pages": [
                        {
                            "width": 120,
                            "height": 40,
                        }
                    ]
                },
                page_count=1,
            )

            before = path.read_bytes()

            self.assertEqual(
                inspect_job(
                    path,
                    "job-1",
                )["data_info_size"],
                (120, 40),
            )

            self.assertEqual(
                path.read_bytes(),
                before,
            )

            with closing(
                sqlite3.connect(path)
            ) as conn:

                with conn:

                    conn.execute(
                        "UPDATE ocr_results SET sha256='bad'"
                    )

            with self.assertRaisesRegex(
                RuntimeError,
                "checksum",
            ):
                inspect_job(
                    path,
                    "job-1",
                )


if __name__ == "__main__":
    unittest.main()
