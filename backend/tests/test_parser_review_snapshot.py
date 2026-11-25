"""Review metadata must survive cloud persistence and checkpoint resume."""

import copy
import json
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

import pymupdf

from backend.cloud_checkpoint import CloudOCRCheckpoint

from backend.cloud_pipeline import process_cloud_pdf

from backend.cloud_repository import CloudRepositoryError

from backend.engine.parser_review_snapshot import (
    build_parser_review_snapshot,
)


USER = "11111111-1111-4111-8111-111111111111"

MODEL = "PaddleOCR-VL-1.6"

NAME = "review.pdf"

TOKEN = "test-token"


def make_pdf(count=2):
    with pymupdf.open() as document:
        for _ in range(count):
            document.new_page()

        return document.tobytes()


def draft(
    record_id,
    number,
    labels="ABC",
    page=1,
):
    return {
        "id": record_id,

        "question_id": number,

        "source_question_number": 1,

        "page_number": page,

        "type": "mcp",

        "section_instruction":
            "Mark the letter A, B, C, or D.",

        "mcp_options": [
            {
                "label": label,

                "content": [
                    {
                        "type": "text",
                        "value": label,
                    },
                ],

                "is_correct": False,
            }

            for label in labels
        ],

        "asset": {
            "crop_status": "failed",
        },
    }


class ParserReviewSnapshotTests(unittest.TestCase):

    def test_repeated_source_numbers_have_distinct_records(self):
        questions = [
            draft("q1", 1),

            draft(
                "q2",
                2,
                page=2,
            ),
        ]

        original = copy.deepcopy(questions)

        report = build_parser_review_snapshot(
            questions
        )

        self.assertEqual(
            questions,
            original,
        )

        self.assertEqual(
            json.loads(json.dumps(report)),
            report,
        )

        items = report["review_items"]

        self.assertEqual(
            [
                item["question_record_id"]
                for item in items
            ],

            ["q1", "q2"],
        )

        self.assertEqual(
            [
                item["question_number"]
                for item in items
            ],

            [1, 1],
        )

        self.assertEqual(
            [
                item["question_id"]
                for item in items
            ],

            [1, 2],
        )

        self.assertEqual(
            [
                item["page_number"]
                for item in items
            ],

            [1, 2],
        )

        self.assertEqual(
            items[0]["issue_codes"],

            ["expected_four_choices"],
        )

        self.assertEqual(
            items[0]["crop_status"],

            "failed",
        )

    def test_clean_exam_has_explicit_zero_review_count(self):
        report = build_parser_review_snapshot([
            draft(
                "q1",
                1,
                "ABCD",
            ),
        ])

        self.assertEqual(
            report["review_items"],
            [],
        )

        self.assertEqual(
            report["review_summary"],

            {
                "total_questions": 1,

                "needs_review": 0,

                "structural_issues": 0,
            },
        )

        self.assertEqual(
            report["stage"],

            "import",
        )

    def test_duplicate_record_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            build_parser_review_snapshot([
                draft("q1", 1),

                draft("q1", 2),
            ])


class CloudParserReviewTests(unittest.TestCase):

    def setUp(self):
        directory = tempfile.TemporaryDirectory()

        self.addCleanup(directory.cleanup)

        cache = patch(
            "backend.cloud_checkpoint.CHECKPOINT_ROOT",

            Path(directory.name),
        )

        cache.start()

        self.addCleanup(cache.stop)

        self.pdf = make_pdf(1)

        self.text = (
            "Mark the letter A, B, C, or D.\n"

            "Question 1: Which statement is correct?\n"

            "A. First\n"

            "B. Second\n"

            "C. Third"
        )

        self.raw = {
            "pages": [
                {
                    "markdown_text": self.text,
                },
            ],
        }

    def upload(self):
        return process_cloud_pdf(
            self.pdf,

            NAME,

            MODEL,

            USER,

            TOKEN,
        )

    def test_report_persists_and_resumes_without_ocr(self):
        with (
            patch(
                "backend.cloud_pipeline.recognize_pdf",

                return_value=(
                    self.text,
                    self.raw,
                ),

            ) as ocr,

            patch(
                "backend.cloud_pipeline.CloudRepository",

            ) as cloud,

            patch(
                "backend.cloud_pipeline.crop_question_pdf",

                side_effect=RuntimeError(
                    "crop failed"
                ),
            ),
        ):
            repository = (
                cloud.return_value
                .__enter__.return_value
            )

            repository.get_exam_record.return_value = None

            repository.save_exam_bundle.side_effect = [
                CloudRepositoryError(
                    "temporary failure"
                ),

                {
                    "exam_id": "saved",
                },
            ]

            with self.assertRaises(
                CloudRepositoryError
            ):
                self.upload()

            checkpoint = CloudOCRCheckpoint(
                USER,

                NAME,

                MODEL,

                self.pdf,
            )

            saved = checkpoint.load()

            self.assertIsNotNone(saved)

            report = saved.exam[
                "extra"
            ][
                "parser_review"
            ]

            self.assertEqual(
                report[
                    "review_summary"
                ][
                    "needs_review"
                ],

                1,
            )

            item = report["review_items"][0]

            self.assertEqual(
                item["question_record_id"],

                saved.questions[0]["id"],
            )

            self.assertEqual(
                item["actual_labels"],

                list("ABC"),
            )

            self.assertEqual(
                item["expected_labels"],

                list("ABCD"),
            )

            self.assertEqual(
                item["crop_status"],

                "failed",
            )

            self.assertTrue(
                saved.questions[0][
                    "requires_source_image"
                ]
            )

            self.assertFalse(
                any(
                    option["is_correct"]

                    for option in saved.questions[0][
                        "mcp_options"
                    ]
                )
            )

            with patch(
                "backend.cloud_pipeline._parse_upload_questions"

            ) as parser:
                self.upload()

                parser.assert_not_called()

            ocr.assert_called_once()

            self.assertEqual(
                repository.save_exam_bundle
                .call_args.args[0],

                saved.exam,
            )

            self.assertTrue(
                repository.save_exam_bundle
                .call_args.kwargs[
                    "resume_existing"
                ]
            )

            self.assertIsNone(
                checkpoint.load()
            )

    def test_legacy_checkpoint_remains_unknown(self):
        checkpoint = CloudOCRCheckpoint(
            USER,

            NAME,

            MODEL,

            self.pdf,
        )

        exam = {
            "id": "legacy",

            "name": "legacy",

            "source_sha256":
                checkpoint.source_sha256,

            "extra": {
                "extraction_method": "paddleocr",
            },
        }

        checkpoint.save(
            exam,

            [
                draft(
                    "legacy-q",
                    1,
                ),
            ],

            {},
        )

        with (
            patch(
                "backend.cloud_pipeline.CloudRepository",

            ) as cloud,

            patch(
                "backend.cloud_pipeline.recognize_pdf",

            ) as ocr,

            patch(
                "backend.cloud_pipeline.build_parser_review_snapshot",

            ) as report,
        ):
            repository = (
                cloud.return_value
                .__enter__.return_value
            )

            repository.get_exam_record.return_value = None

            repository.save_exam_bundle.return_value = {
                "exam_id": "legacy",
            }

            self.upload()

            report.assert_not_called()

            ocr.assert_not_called()

            self.assertNotIn(
                "parser_review",

                repository.save_exam_bundle
                .call_args.args[0]["extra"],
            )


if __name__ == "__main__":
    unittest.main()