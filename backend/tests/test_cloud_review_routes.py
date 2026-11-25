"""Integration tests for CloudReviewService HTTP endpoints."""

import unittest

from unittest.mock import patch

from fastapi import Request
from fastapi.testclient import TestClient

from backend.cloud_review import CloudReviewError
from backend.network.cloud_api import (
    app,
    authenticated_identity,
)


QUESTION_ID = (
    "33333333-3333-4333-8333-333333333333"
)

USER_ID = (
    "11111111-1111-4111-8111-111111111111"
)

TOKEN = "test-access-token"


def fake_identity(request: Request):
    return USER_ID, TOKEN


class CloudReviewRouteTests(unittest.TestCase):
    def setUp(self):
        self.previous_overrides = dict(
            app.dependency_overrides
        )

        app.dependency_overrides[
            authenticated_identity
        ] = fake_identity

        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(
            None,
            None,
            None,
        )

        app.dependency_overrides.clear()
        app.dependency_overrides.update(
            self.previous_overrides
        )

    @patch(
        "backend.network.cloud_api.CloudReviewService"
    )
    @patch(
        "backend.network.cloud_api.CloudRepository"
    )
    def test_crop_route_supports_force(
        self,
        mock_repository,
        mock_service_class,
    ):
        service = mock_service_class.return_value

        service.crop_image.return_value = {
            "image_path": (
                f"{USER_ID}/assets/test.png"
            ),
            "image_url": (
                "https://example.supabase.co/"
                "storage/v1/object/sign/"
                "quizpdf-private/test.png?token=signed"
            ),
            "crop_status": "success",
            "page_number": 1,
            "source": "test",
            "cached": False,
        }

        response = self.client.post(
            f"/api/questions/{QUESTION_ID}/crop-image",
            params={"force": "true"},
        )

        self.assertEqual(
            response.status_code,
            200,
            response.text,
        )

        self.assertTrue(
            response.json()["success"]
        )

        self.assertEqual(
            response.json()["data"]["crop_status"],
            "success",
        )

        service.crop_image.assert_called_once_with(
            QUESTION_ID,
            force=True,
        )

        mock_repository.assert_called_once_with(
            USER_ID,
            TOKEN,
        )

    @patch(
        "backend.network.cloud_api.CloudReviewService"
    )
    @patch(
        "backend.network.cloud_api.CloudRepository"
    )
    def test_crop_failure_preserves_http_status(
        self,
        mock_repository,
        mock_service_class,
    ):
        mock_service_class.return_value.crop_image.side_effect = (
            CloudReviewError(
                404,
                "Không tìm thấy câu hỏi.",
            )
        )

        response = self.client.post(
            f"/api/questions/{QUESTION_ID}/crop-image"
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.assertEqual(
            response.json()["detail"],
            "Không tìm thấy câu hỏi.",
        )

    @patch(
        "backend.network.cloud_api.CloudReviewService"
    )
    @patch(
        "backend.network.cloud_api.CloudRepository"
    )
    def test_detect_red_mark_route(
        self,
        mock_repository,
        mock_service_class,
    ):
        service = mock_service_class.return_value

        service.detect_red_mark.return_value = {
            "detected_answer": "B",
            "confidence": 0.91,
            "status": "detected",
            "page_number": 1,
            "mark_coordinates": [1, 2, 3, 4],
            "user_confirmed_answer": None,
            "cached": False,
        }

        response = self.client.post(
            f"/api/questions/{QUESTION_ID}/detect-red-mark",
            params={"force": "true"},
        )

        self.assertEqual(
            response.status_code,
            200,
            response.text,
        )

        data = response.json()["data"]

        self.assertEqual(
            data["detected_answer"],
            "B",
        )

        service.detect_red_mark.assert_called_once_with(
            QUESTION_ID,
            force=True,
        )

    @patch(
        "backend.network.cloud_api.CloudReviewService"
    )
    @patch(
        "backend.network.cloud_api.CloudRepository"
    )
    def test_confirm_answer_route(
        self,
        mock_repository,
        mock_service_class,
    ):
        service = mock_service_class.return_value

        service.confirm_red_answer.return_value = {
            "question_id": QUESTION_ID,
            "detected_answer": "A",
            "confidence": 0.85,
            "status": "detected",
            "page_number": 1,
            "mark_coordinates": None,
            "user_confirmed_answer": "B",
        }

        response = self.client.patch(
            (
                f"/api/questions/{QUESTION_ID}"
                "/red-mark-confirmation"
            ),
            json={"answer": "B"},
        )

        self.assertEqual(
            response.status_code,
            200,
            response.text,
        )

        self.assertEqual(
            response.json()["data"][
                "user_confirmed_answer"
            ],
            "B",
        )

        service.confirm_red_answer.assert_called_once_with(
            QUESTION_ID,
            "B",
        )

    @patch(
        "backend.network.cloud_api.CloudReviewService"
    )
    @patch(
        "backend.network.cloud_api.CloudRepository"
    )
    def test_clear_confirmed_answer(
        self,
        mock_repository,
        mock_service_class,
    ):
        service = mock_service_class.return_value

        service.confirm_red_answer.return_value = {
            "question_id": QUESTION_ID,
            "user_confirmed_answer": None,
        }

        response = self.client.patch(
            (
                f"/api/questions/{QUESTION_ID}"
                "/red-mark-confirmation"
            ),
            json={"answer": None},
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        service.confirm_red_answer.assert_called_once_with(
            QUESTION_ID,
            None,
        )

    def test_confirmation_body_is_required(self):
        response = self.client.patch(
            (
                f"/api/questions/{QUESTION_ID}"
                "/red-mark-confirmation"
            ),
            json={},
        )

        self.assertEqual(
            response.status_code,
            422,
        )

    def test_missing_identity_is_rejected(self):
        app.dependency_overrides.pop(
            authenticated_identity,
            None,
        )

        response = self.client.post(
            f"/api/questions/{QUESTION_ID}/crop-image"
        )

        self.assertEqual(
            response.status_code,
            401,
        )

    @patch(
        "backend.network.cloud_api.CloudReviewService"
    )
    @patch(
        "backend.network.cloud_api.CloudRepository"
    )
    def test_existing_cloud_exam_endpoint_is_preserved(
        self,
        mock_repository,
        mock_service_class,
    ):
        repository = (
            mock_repository.return_value
            .__enter__.return_value
        )

        repository.list_exams.return_value = [
            {
                "id": "exam-123",
                "name": "Test exam",
                "question_count": 2,
                "has_source_pdf": True,
                "uploaded_at": (
                    "2026-11-22T00:00:00Z"
                ),
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
            "exam-123",
        )

        repository.list_exams.assert_called_once()


if __name__ == "__main__":
    unittest.main()
