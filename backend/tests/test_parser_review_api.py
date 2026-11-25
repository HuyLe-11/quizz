"""Regression tests for parser review retrieval."""

import copy
import unittest

from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.network.cloud_api import (
    authenticated_identity,
)

from backend.network.parser_review_api import (
    router,
)


def exam_record():
    return {
        "id": "exam-a",

        "owner_id": "user-a",

        "questions": [
            {"id": "q1"},
            {"id": "q2"},
        ],

        "extra": {
            "ocr_response": {
                "secret": "not returned",
            },

            "parser_review": {
                "schema_version": 1,

                "stage": "import",

                "review_summary": {
                    "total_questions": 2,

                    "needs_review": 1,

                    "structural_issues": 1,
                },

                "review_items": [
                    {
                        "question_record_id": "q2",

                        "issue_codes": [
                            "expected_four_choices",
                        ],

                        "warning": "Thiếu lựa chọn D",

                        "requires_source_image": True,

                        "actual_labels": [
                            "A",
                            "B",
                            "C",
                        ],
                    },
                ],
            },
        },
    }


class ParserReviewApiTests(unittest.TestCase):

    def setUp(self):
        self.app = FastAPI()

        self.app.include_router(router)

        self.client = TestClient(self.app)

        self.mock = patch(
            "backend.network.parser_review_api.CloudRepository"
        )

        self.repository_class = (
            self.mock.start()
        )

        self.addCleanup(
            self.mock.stop
        )

        self.addCleanup(
            self.client.close
        )

        self.repository = (
            self.repository_class.return_value
            .__enter__.return_value
        )

        self.repository.get_exam_record.return_value = (
            exam_record()
        )

    def authorize(self):
        def identity(request: Request):
            return (
                request.headers.get(
                    "x-test-user",
                    "user-a",
                ),

                "test-token",
            )

        self.app.dependency_overrides[
            authenticated_identity
        ] = identity

    def get(self, **kwargs):
        return self.client.get(
            "/api/exams/exam-a/parser-review",

            **kwargs,
        )

    def test_requires_auth(self):
        self.assertEqual(
            self.get().status_code,

            401,
        )

        self.repository_class.assert_not_called()

    def test_returns_only_review_fields_without_ocr(self):
        self.authorize()

        original = copy.deepcopy(
            self.repository.get_exam_record.return_value
        )

        response = self.get()

        self.assertEqual(
            response.status_code,

            200,
        )

        data = response.json()["data"]

        self.assertEqual(
            data["status"],

            "available",
        )

        self.assertEqual(
            data["review_items"][0][
                "question_record_id"
            ],

            "q2",
        )

        self.assertEqual(
            data["review_summary"][
                "needs_review"
            ],

            1,
        )

        self.assertNotIn(
            "ocr_response",

            response.text,
        )

        self.assertNotIn(
            "secret",

            response.text,
        )

        self.assertEqual(
            self.repository.get_exam_record.return_value,

            original,
        )

        self.repository_class.assert_called_once_with(
            "user-a",

            "test-token",
        )

    def test_other_owner_cannot_read(self):
        self.authorize()

        response = self.get(
            headers={
                "x-test-user": "user-b",
            },
        )

        self.assertEqual(
            response.status_code,

            404,
        )

    def test_missing_exam(self):
        self.authorize()

        self.repository.get_exam_record.return_value = (
            None
        )

        self.assertEqual(
            self.get().status_code,

            404,
        )

    def test_legacy_and_clean_are_distinct(self):
        self.authorize()

        record = (
            self.repository
            .get_exam_record.return_value
        )

        snapshot = record["extra"].pop(
            "parser_review"
        )

        self.assertEqual(
            self.get().json()["data"],

            {
                "status": "unavailable",

                "reason": "missing",
            },
        )

        snapshot["review_items"] = []

        snapshot["review_summary"].update(
            needs_review=0,

            structural_issues=0,
        )

        record["extra"]["parser_review"] = (
            snapshot
        )

        data = self.get().json()["data"]

        self.assertEqual(
            data["status"],

            "available",
        )

        self.assertEqual(
            data["review_summary"][
                "needs_review"
            ],

            0,
        )

    def test_stale_record(self):
        self.authorize()

        record = (
            self.repository
            .get_exam_record.return_value
        )

        record["questions"][1]["id"] = (
            "new-q"
        )

        self.assertEqual(
            self.get().json()["data"],

            {
                "status": "unavailable",

                "reason": "stale",
            },
        )

    def test_invalid_and_unsupported_report(self):
        self.authorize()

        record = (
            self.repository
            .get_exam_record.return_value
        )

        snapshot = record["extra"][
            "parser_review"
        ]

        snapshot["review_summary"][
            "needs_review"
        ] = 2

        self.assertEqual(
            self.get().json()["data"][
                "reason"
            ],

            "invalid",
        )

        snapshot["schema_version"] = 2

        self.assertEqual(
            self.get().json()["data"][
                "reason"
            ],

            "unsupported",
        )


if __name__ == "__main__":
    unittest.main()