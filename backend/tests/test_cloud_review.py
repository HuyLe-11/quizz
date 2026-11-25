"""Unit tests for private cloud PDF review operations."""

import copy
import unittest

from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.cloud_review import (
    CloudReviewError,
    CloudReviewService,
)

USER_ID = "11111111-1111-4111-8111-111111111111"
EXAM_ID = "22222222-2222-4222-8222-222222222222"
QUESTION_ID = "33333333-3333-4333-8333-333333333333"

SOURCE_PATH = f"{USER_ID}/pdfs/{EXAM_ID}.pdf"

IMAGE_PATH = (
    f"{USER_ID}/assets/{EXAM_ID}/"
    f"question-{QUESTION_ID}.png"
)


def sample_question():
    return {
        "id": QUESTION_ID,
        "exam_id": EXAM_ID,
        "question_id": 1,
        "source_question_number": 1,
        "page_number": 1,
        "mcp_options": [
            {"label": "A"},
            {"label": "B"},
        ],
        "asset": None,
        "red_mark_detection": None,
    }


def sample_exam():
    return {
        "id": EXAM_ID,
        "source_path": SOURCE_PATH,
        "extra": {
            "ocr_response": {
                "pages": [
                    {
                        "markdown_text": (
                            "Question 1\n"
                            "A. First\n"
                            "B. Second"
                        ),
                    },
                ],
            },
        },
        "questions": [
            sample_question(),
        ],
    }


class CloudReviewTests(unittest.TestCase):
    def setUp(self):
        self.repository = MagicMock()

        self.repository.user_id = USER_ID

        self.repository.base_url = (
            "https://example.supabase.co"
        )

        self.repository.get_exam_record.return_value = (
            copy.deepcopy(sample_exam())
        )

        self.repository.download_object.return_value = (
            b"%PDF-1.4\nmock-pdf"
        )

        self.requests_seen = []

        def request(
            method,
            path,
            **kwargs,
        ):
            self.requests_seen.append(
                (method, path, kwargs)
            )

            if (
                method == "GET"
                and path == "/rest/v1/questions"
            ):
                return [{
                    "id": QUESTION_ID,
                    "exam_id": EXAM_ID,
                }]

            if path.startswith(
                "/storage/v1/object/sign/"
            ):
                return {
                    "signedURL": (
                        "/object/sign/"
                        "quizpdf-private/"
                        f"{IMAGE_PATH}"
                        "?token=example"
                    ),
                }

            if (
                method == "POST"
                and path == "/rest/v1/red_mark_detections"
            ):
                return [
                    kwargs["json_data"]
                ]

            if (
                method == "PATCH"
                and path == "/rest/v1/red_mark_detections"
            ):
                return [{
                    "question_id": QUESTION_ID,
                    "user_confirmed_answer": (
                        kwargs["json_data"][
                            "user_confirmed_answer"
                        ]
                    ),
                }]

            return None

        self.repository._request.side_effect = request

        self.service = CloudReviewService(
            self.repository
        )

    def test_unowned_question_is_not_accessible(self):
        self.repository._request.side_effect = (
            lambda method, path, **kwargs: []
        )

        with self.assertRaises(
            CloudReviewError
        ) as captured:
            self.service.crop_image(QUESTION_ID)

        self.assertEqual(
            captured.exception.status_code,
            404,
        )

        self.repository.download_object.assert_not_called()

    def test_cached_crop_returns_signed_url(self):
        exam = sample_exam()

        exam["questions"][0]["asset"] = {
            "image_path": IMAGE_PATH,
            "crop_status": "success",
            "page_number": 1,
        }

        self.repository.get_exam_record.return_value = exam

        result = self.service.crop_image(
            QUESTION_ID
        )

        self.assertTrue(result["cached"])

        self.assertIn(
            "?token=example",
            result["image_url"],
        )

        self.repository.download_object.assert_not_called()

    @patch(
        "backend.cloud_review.crop_question_pdf"
    )
    def test_crop_uploads_private_image(
        self,
        mock_crop,
    ):
        def create_crop(
            pdf_path,
            question_number,
            page_number,
            output_path,
            **kwargs,
        ):
            Path(output_path).write_bytes(
                b"fake-image-data"
            )

            return {
                "crop_status": "success",
                "page_number": 1,
                "crop_coordinates": [],
                "source": "test",
            }

        mock_crop.side_effect = create_crop

        result = self.service.crop_image(
            QUESTION_ID,
            force=True,
        )

        self.assertFalse(result["cached"])

        self.assertEqual(
            result["image_path"],
            IMAGE_PATH,
        )

        uploads = [
            (method, path, options)
            for method, path, options
            in self.requests_seen
            if (
                method == "POST"
                and path.startswith(
                    "/storage/v1/object/quizpdf-private/"
                )
            )
        ]

        self.assertEqual(
            len(uploads),
            1,
        )

        self.assertEqual(
            uploads[0][2]["content"],
            b"fake-image-data",
        )

        self.assertEqual(
            uploads[0][2]["extra_headers"]["x-upsert"],
            "true",
        )

        self.repository.download_object.assert_called_once_with(
            SOURCE_PATH
        )

    @patch(
        "backend.cloud_review.detect_red_mark_pdf"
    )
    def test_detection_is_saved(
        self,
        mock_detector,
    ):
        mock_detector.return_value = {
            "detected_answer": "B",
            "confidence": 0.91,
            "status": "detected",
            "page_number": 1,
            "mark_coordinates": [1, 2, 3, 4],
            "config_fingerprint": "test-config",
        }

        result = self.service.detect_red_mark(
            QUESTION_ID
        )

        self.assertEqual(
            result["detected_answer"],
            "B",
        )

        self.assertFalse(result["cached"])

        saves = [
            options
            for method, path, options
            in self.requests_seen
            if (
                method == "POST"
                and path == (
                    "/rest/v1/red_mark_detections"
                )
            )
        ]

        self.assertEqual(len(saves), 1)

        self.assertEqual(
            saves[0]["json_data"]["question_id"],
            QUESTION_ID,
        )

        mock_detector.assert_called_once()

    def test_confirmation_rejects_invalid_option(self):
        exam = sample_exam()

        exam["questions"][0][
            "red_mark_detection"
        ] = {
            "detected_answer": "A",
            "status": "detected",
        }

        self.repository.get_exam_record.return_value = exam

        with self.assertRaises(
            CloudReviewError
        ) as captured:
            self.service.confirm_red_answer(
                QUESTION_ID,
                "Z",
            )

        self.assertEqual(
            captured.exception.status_code,
            422,
        )

    def test_confirmation_requires_detection(self):
        with self.assertRaises(
            CloudReviewError
        ) as captured:
            self.service.confirm_red_answer(
                QUESTION_ID,
                "A",
            )

        self.assertEqual(
            captured.exception.status_code,
            409,
        )

    def test_confirmation_updates_selected_answer(self):
        exam = sample_exam()

        exam["questions"][0][
            "red_mark_detection"
        ] = {
            "detected_answer": "A",
            "status": "detected",
        }

        self.repository.get_exam_record.return_value = exam

        result = self.service.confirm_red_answer(
            QUESTION_ID,
            "B",
        )

        self.assertEqual(
            result["user_confirmed_answer"],
            "B",
        )

        updates = [
            options
            for method, path, options
            in self.requests_seen
            if (
                method == "PATCH"
                and path == (
                    "/rest/v1/red_mark_detections"
                )
            )
        ]

        self.assertEqual(
            len(updates),
            1,
        )

        self.assertEqual(
            updates[0]["json_data"][
                "user_confirmed_answer"
            ],
            "B",
        )


if __name__ == "__main__":
    unittest.main()
