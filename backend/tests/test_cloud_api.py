"""Tests for the authenticated cloud API endpoints."""

import time
import unittest

from unittest.mock import patch

from fastapi import Request
from fastapi.testclient import TestClient

from backend.network.cloud_api import (
    app,
    authenticated_identity,
)


def fake_identity(request: Request):
    user_id = request.headers.get(
        "x-test-user",
        "user-a",
    )

    return user_id, "test-access-token"


class CloudApiTests(unittest.TestCase):
    def setUp(self):
        self.original_overrides = dict(
            app.dependency_overrides
        )

        app.dependency_overrides[
            authenticated_identity
        ] = fake_identity

        self.client_context = TestClient(app)
        self.client = (
            self.client_context.__enter__()
        )

    def tearDown(self):
        self.client_context.__exit__(
            None,
            None,
            None,
        )

        app.dependency_overrides.clear()
        app.dependency_overrides.update(
            self.original_overrides
        )

    def test_health_endpoint(self):
        response = self.client.get(
            "/api/health"
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.json()["data"][
                "storage_backend"
            ],
            "supabase",
        )

    @patch(
        "backend.network.cloud_api.CloudRepository"
    )
    def test_history_uses_cloud_repository(
        self,
        mock_repository,
    ):
        repository = (
            mock_repository.return_value
            .__enter__.return_value
        )

        repository.list_exams.return_value = [
            {
                "id": "exam-a",
                "name": "Đề của tài khoản A",
                "question_count": 2,
            },
        ]

        response = self.client.get(
            "/api/exams"
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.json()["data"][0]["id"],
            "exam-a",
        )

        mock_repository.assert_called_once_with(
            "user-a",
            "test-access-token",
        )

    @patch(
        "backend.network.cloud_api.CloudRepository"
    )
    def test_exam_uses_signed_image_url(
        self,
        mock_repository,
    ):
        repository = (
            mock_repository.return_value
            .__enter__.return_value
        )

        repository.user_id = "user-a"

        repository.base_url = (
            "https://example.supabase.co"
        )

        repository.get_exam_record.return_value = {
            "id": "exam-a",
            "name": "Đề kiểm thử",
            "subject": None,
            "source_file": "sample.pdf",
            "source_path": "user-a/pdfs/exam-a.pdf",
            "questions": [
                {
                    "id": "question-a",
                    "question_id": 1,
                    "type": "mcp",
                    "multiple_choice": False,
                    "question_content": [
                        {
                            "type": "text",
                            "value": "Test question",
                        },
                    ],
                    "explanation_content": [],
                    "mcp_options": [],
                    "match_pairs": [],
                    "fill_in_answers": [],
                    "red_mark_detection": None,
                    "asset": {
                        "image_path": (
                            "user-a/assets/"
                            "exam-a/question-a.png"
                        ),
                        "crop_status": "success",
                    },
                },
            ],
        }

        repository._request.return_value = {
            "signedURL": (
                "/object/sign/quizpdf-private/"
                "user-a/assets/exam-a/question-a.png"
                "?token=signed-example"
            ),
        }

        response = self.client.get(
            "/api/exams/exam-a"
        )

        self.assertEqual(
            response.status_code,
            200,
            response.text,
        )

        question = response.json()[
            "data"
        ]["questions"][0]

        image_url = question[
            "question_image_url"
        ]

        self.assertTrue(
            image_url.startswith(
                "https://example.supabase.co/"
            )
        )

        self.assertIn(
            "?token=signed-example",
            image_url,
        )

    def test_upload_job_is_private_to_owner(self):
        with patch(
            "backend.network.cloud_api.process_cloud_pdf",
            return_value={
                "exam_id": "exam-a",
                "title": "sample",
                "question_count": 1,
            },
        ):
            response = self.client.post(
                "/api/upload/jobs",
                headers={
                    "x-test-user": "user-a",
                },
                files={
                    "file": (
                        "sample.pdf",
                        b"%PDF-1.4\nsample",
                        "application/pdf",
                    ),
                },
            )

            self.assertEqual(
                response.status_code,
                202,
                response.text,
            )

            job_id = response.json()[
                "data"
            ]["job_id"]

            url = f"/api/upload/jobs/{job_id}"

            other_user = self.client.get(
                url,
                headers={
                    "x-test-user": "user-b",
                },
            )

            self.assertEqual(
                other_user.status_code,
                404,
            )

            deadline = time.monotonic() + 3

            while time.monotonic() < deadline:
                owner_response = self.client.get(
                    url,
                    headers={
                        "x-test-user": "user-a",
                    },
                )

                self.assertEqual(
                    owner_response.status_code,
                    200,
                )

                status = owner_response.json()[
                    "data"
                ]["status"]

                if status in {
                    "completed",
                    "failed",
                }:
                    break

                time.sleep(0.01)

            self.assertEqual(
                status,
                "completed",
            )

    @patch(
        "backend.network.cloud_api.CloudRepository"
    )
    def test_attempt_requires_owned_exam(
        self,
        mock_repository,
    ):
        repository = (
            mock_repository.return_value
            .__enter__.return_value
        )

        repository.get_exam_record.return_value = None

        response = self.client.post(
            "/api/exams/unknown-exam/attempts",
            json={
                "score": 50,
                "total_questions": 2,
                "correct_answers_count": 1,
                "time_spent": 30,
                "answers": {},
                "question_results": [],
                "completed_at": 1760000000000,
            },
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        repository.save_attempt.assert_not_called()

    def test_unverified_request_is_rejected(self):
        app.dependency_overrides.pop(
            authenticated_identity,
            None,
        )

        response = self.client.get(
            "/api/exams"
        )

        self.assertEqual(
            response.status_code,
            401,
        )

    def test_old_public_assets_are_not_served(self):
        response = self.client.get(
            "/api/assets/example.png"
        )

        self.assertEqual(
            response.status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
