"""Regression tests for batch signing private Supabase images."""

import unittest
from unittest.mock import MagicMock, patch

from backend.cloud_repository import (
    BUCKET,
    CloudRepositoryError,
)
from backend.network.cloud_api import (
    _cloud_exam_response,
    _signed_image_urls,
)


USER_ID = "11111111-1111-4111-8111-111111111111"
OTHER_USER = "22222222-2222-4222-8222-222222222222"
BASE_URL = "https://example.supabase.co"

IMAGE_A = f"{USER_ID}/assets/exam-123/question-a.png"
IMAGE_B = f"{USER_ID}/assets/exam-123/question-b.png"
IMAGE_C = f"{USER_ID}/assets/exam-123/question-c.png"


def make_repository():
    repository = MagicMock()
    repository.user_id = USER_ID
    repository.base_url = BASE_URL
    return repository


def signed_entry(path, token="test-token"):
    return {
        "path": path,
        "signedURL": (
            f"/object/sign/{BUCKET}/{path}"
            f"?token={token}"
        ),
    }


def sample_exam(image_paths):
    return {
        "id": "exam-123",
        "name": "Batch signing test",
        "subject": "uploaded",
        "source_file": "sample.pdf",
        "source_path": f"{USER_ID}/pdfs/exam-123.pdf",
        "questions": [
            {
                "id": f"record-{index}",
                "question_id": index,
                "asset": (
                    {"image_path": path}
                    if path
                    else None
                ),
            }
            for index, path in enumerate(image_paths, start=1)
        ],
    }


class SignedImageBatchTests(unittest.TestCase):
    def test_multiple_images_use_one_request(self):
        repository = make_repository()

        # The response order must not affect image assignment.
        repository._request.return_value = [
            signed_entry(IMAGE_C, "token-c"),
            signed_entry(IMAGE_A, "token-a"),
            signed_entry(IMAGE_B, "token-b"),
        ]

        result = _signed_image_urls(
            repository,
            [IMAGE_A, IMAGE_B, IMAGE_C],
        )

        repository._request.assert_called_once_with(
            "POST",
            f"/storage/v1/object/sign/{BUCKET}",
            json_data={
                "expiresIn": 600,
                "paths": [IMAGE_A, IMAGE_B, IMAGE_C],
            },
        )

        self.assertIn("token-a", result[IMAGE_A])
        self.assertIn("token-b", result[IMAGE_B])
        self.assertIn("token-c", result[IMAGE_C])

    def test_exam_response_preserves_question_mapping(self):
        repository = make_repository()
        repository._request.return_value = [
            signed_entry(IMAGE_B, "second"),
            signed_entry(IMAGE_A, "first"),
        ]

        with patch(
            "backend.network.cloud_api._question_response",
            side_effect=lambda question: {
                "question_id": question["question_id"],
            },
        ):
            result = _cloud_exam_response(
                repository,
                sample_exam([IMAGE_A, None, IMAGE_B]),
            )

        self.assertEqual(result["question_count"], 3)
        self.assertIn(
            "token=first",
            result["questions"][0]["question_image_url"],
        )
        self.assertIsNone(
            result["questions"][1]["question_image_url"]
        )
        self.assertIn(
            "token=second",
            result["questions"][2]["question_image_url"],
        )

        self.assertEqual(
            repository._request.call_count,
            1,
        )

    def test_no_images_makes_no_storage_requests(self):
        repository = make_repository()

        with patch(
            "backend.network.cloud_api._question_response",
            side_effect=lambda question: {
                "question_id": question["question_id"],
            },
        ):
            result = _cloud_exam_response(
                repository,
                sample_exam([None, None]),
            )

        self.assertEqual(result["question_count"], 2)
        self.assertTrue(
            all(
                item["question_image_url"] is None
                for item in result["questions"]
            )
        )

        repository._request.assert_not_called()

    def test_duplicate_images_are_signed_once(self):
        repository = make_repository()

        # One unique image retains the existing single-sign endpoint.
        repository._request.return_value = {
            "signedURL": (
                f"/object/sign/{BUCKET}/{IMAGE_A}"
                "?token=duplicate-test"
            )
        }

        urls = _signed_image_urls(
            repository,
            [IMAGE_A, IMAGE_A],
        )

        self.assertEqual(len(urls), 1)
        self.assertIn(
            "duplicate-test",
            urls[IMAGE_A],
        )

        repository._request.assert_called_once_with(
            "POST",
            f"/storage/v1/object/sign/{BUCKET}/{IMAGE_A}",
            json_data={"expiresIn": 600},
        )

    def test_other_users_image_is_rejected_before_signing(self):
        repository = make_repository()
        foreign_image = (
            f"{OTHER_USER}/assets/exam-123/foreign.png"
        )

        with self.assertRaises(CloudRepositoryError):
            _signed_image_urls(
                repository,
                [IMAGE_A, foreign_image],
            )

        repository._request.assert_not_called()

    def test_partial_batch_response_is_rejected(self):
        repository = make_repository()
        repository._request.return_value = [
            signed_entry(IMAGE_A),
        ]

        with self.assertRaisesRegex(
            CloudRepositoryError,
            "incomplete",
        ):
            _signed_image_urls(
                repository,
                [IMAGE_A, IMAGE_B],
            )

    def test_duplicate_response_entry_is_rejected(self):
        repository = make_repository()
        repository._request.return_value = [
            signed_entry(IMAGE_A),
            signed_entry(IMAGE_A),
        ]

        with self.assertRaisesRegex(
            CloudRepositoryError,
            "duplicate",
        ):
            _signed_image_urls(
                repository,
                [IMAGE_A, IMAGE_B],
            )

    def test_missing_signed_url_is_rejected(self):
        repository = make_repository()
        repository._request.return_value = [
            signed_entry(IMAGE_A),
            {
                "path": IMAGE_B,
                "error": "Object not found",
            },
        ]

        with self.assertRaises(CloudRepositoryError):
            _signed_image_urls(
                repository,
                [IMAGE_A, IMAGE_B],
            )

    def test_wrong_image_url_is_rejected(self):
        repository = make_repository()
        repository._request.return_value = [
            {
                "path": IMAGE_A,
                "signedURL": signed_entry(
                    IMAGE_B
                )["signedURL"],
            },
            signed_entry(IMAGE_B),
        ]

        with self.assertRaisesRegex(
            CloudRepositoryError,
            "does not match",
        ):
            _signed_image_urls(
                repository,
                [IMAGE_A, IMAGE_B],
            )

    def test_external_url_is_rejected(self):
        repository = make_repository()
        repository._request.return_value = [
            {
                "path": IMAGE_A,
                "signedURL": (
                    "https://untrusted.example/"
                    "image.png?token=example"
                ),
            },
            signed_entry(IMAGE_B),
        ]

        with self.assertRaisesRegex(
            CloudRepositoryError,
            "unexpected",
        ):
            _signed_image_urls(
                repository,
                [IMAGE_A, IMAGE_B],
            )

    def test_relative_signed_path_is_supported(self):
        repository = make_repository()
        repository._request.return_value = [
            {
                "path": IMAGE_A,
                "signedURL": (
                    f"{IMAGE_A}?token=relative-a"
                ),
            },
            {
                "path": IMAGE_B,
                "signedURL": (
                    f"{IMAGE_B}?token=relative-b"
                ),
            },
        ]

        urls = _signed_image_urls(
            repository,
            [IMAGE_A, IMAGE_B],
        )

        self.assertEqual(
            urls[IMAGE_A],
            (
                f"{BASE_URL}/storage/v1/object/sign/"
                f"{BUCKET}/{IMAGE_A}?token=relative-a"
            ),
        )

        self.assertIn(
            "token=relative-b",
            urls[IMAGE_B],
        )


if __name__ == "__main__":
    unittest.main()
