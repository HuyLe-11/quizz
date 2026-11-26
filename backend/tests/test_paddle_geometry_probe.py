import os
import sqlite3
import tempfile
import unittest

from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pymupdf

from backend.engine import paddle_resilient

from backend.engine.ocr_job_journal import (
    OCRJobJournal,
    DEFAULT_PATH,
)

from backend.paddle_geometry_probe import (
    MODEL,
    OPTIONS,
    PROBE_OWNER,
    describe_page,
    inspect_existing,
    run_experiment,
)


def page_fixture():
    return {
        "pruned_result": {
            "return_word_box": False,
            "rec_texts": [
                "PRIVATE TEXT"
            ],
            "rec_boxes": [
                [1, 2, 3, 4]
            ],
            "doc_preprocessor_res": {
                "angle": 0,
                "model_settings": {
                    "use_doc_unwarping": True
                }
            }
        }
    }


class PaddleGeometryProbeTests(unittest.TestCase):

    def test_report_actual_values_not_text(self):

        r = describe_page(
            page_fixture()
        )

        self.assertIs(
            r["return_word_box"],
            False,
        )

        self.assertIs(
            r["use_doc_unwarping"],
            True,
        )

        self.assertEqual(
            r["rec_text_count"],
            1,
        )

        self.assertEqual(
            r["possible_fine_geometry_fields"],
            [],
        )

        self.assertNotIn(
            "PRIVATE TEXT",
            str(r),
        )

    def test_candidate_fields_not_verified(self):

        p = page_fixture()

        p["pruned_result"]["word_boxes"] = [
            [[1, 2, 3, 4]]
        ]

        r = describe_page(p)

        self.assertTrue(
            r["possible_fine_geometry_fields"]
        )

        self.assertIs(
            r["return_word_box"],
            False,
        )

    def test_missing_settings_not_assumed_false(self):

        r = describe_page({})

        self.assertIsNone(
            r["return_word_box"]
        )

        self.assertIsNone(
            r["use_doc_unwarping"]
        )

    def test_readonly_cache_and_checksum(self):

        with tempfile.TemporaryDirectory() as directory:

            path = (
                Path(directory)
                / "jobs.sqlite3"
            )

            scope = "a" * 64

            journal = OCRJobJournal(
                scope,
                path,
            )

            key = journal.batch_key(
                0,
                1,
                MODEL,
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
                [page_fixture()],
                {},
                page_count=1,
            )

            before = path.read_bytes()

            r = inspect_existing(
                path,
                scope,
                "job-1",
            )

            self.assertIs(
                r["pages"][0]["return_word_box"],
                False,
            )

            self.assertEqual(
                path.read_bytes(),
                before,
            )

            with self.assertRaises(RuntimeError):

                inspect_existing(
                    path,
                    "b" * 64,
                    "job-1",
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
                inspect_existing(
                    path,
                    scope,
                    "job-1",
                )

    def test_isolated_options_and_restore(self):

        with tempfile.TemporaryDirectory() as directory:

            pdf = (
                Path(directory)
                / "sample.pdf"
            )

            journal = (
                Path(directory)
                / "probe"
                / "jobs.sqlite3"
            )

            with pymupdf.open() as doc:

                doc.new_page()
                doc.save(pdf)

            previous = (
                paddle_resilient._optional_payload
            )

            previous_env = os.environ.get(
                "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL"
            )

            def fake(path, model, **kwargs):

                self.assertEqual(
                    Path(path).read_bytes(),
                    pdf.read_bytes(),
                )

                self.assertEqual(
                    model,
                    MODEL,
                )

                self.assertEqual(
                    kwargs["journal_path"],
                    journal,
                )

                self.assertEqual(
                    kwargs["user_id"],
                    PROBE_OWNER,
                )

                self.assertEqual(
                    paddle_resilient._optional_payload(model),
                    OPTIONS,
                )

                self.assertEqual(
                    os.environ[
                        "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL"
                    ],
                    "",
                )

                self.assertEqual(
                    os.environ[
                        "PADDLEOCR_SUBMIT_MAX_ATTEMPTS"
                    ],
                    "1",
                )

                return "text", {
                    "job_id": "job-2",
                    "pages": [
                        page_fixture()
                    ],
                }

            with patch.object(
                paddle_resilient,
                "recognize_pdf",
                side_effect=fake,
            ) as call:

                r = run_experiment(
                    pdf,
                    journal,
                )

            self.assertEqual(
                call.call_count,
                1,
            )

            self.assertEqual(
                r["job_id"],
                "job-2",
            )

            self.assertIs(
                paddle_resilient._optional_payload,
                previous,
            )

            self.assertEqual(
                os.environ.get(
                    "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL"
                ),
                previous_env,
            )

    def test_reject_production_journal(self):

        with self.assertRaisesRegex(
            ValueError,
            "separate",
        ):
            run_experiment(
                "missing.pdf",
                DEFAULT_PATH,
            )

    def test_reject_multiple_pages_before_ocr(self):

        with tempfile.TemporaryDirectory() as directory:

            pdf = (
                Path(directory)
                / "sample.pdf"
            )

            with pymupdf.open() as doc:

                doc.new_page()
                doc.new_page()
                doc.save(pdf)

            with patch.object(
                paddle_resilient,
                "recognize_pdf",
            ) as call:

                with self.assertRaises(ValueError):

                    run_experiment(
                        pdf,
                        Path(directory) / "jobs.sqlite3",
                    )

                call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
