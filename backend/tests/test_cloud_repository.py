"""Unit tests for authenticated Supabase persistence."""

import json
import unittest

import httpx

from backend.cloud_repository import (
    CloudRepository,
    CloudRepositoryError,
)

USER_ID = "11111111-1111-4111-8111-111111111111"
BASE_URL = "https://example.supabase.co"


def sample_exam():
    return {
        "id": "exam-123",
        "name": "Đề kiểm thử Supabase",
        "source_file": "sample.pdf",
        "uploaded_at": "2026-11-22T00:00:00Z",
        "extra": {
            "ocr_model": "PaddleOCR-VL-1.6",
        },
    }


def sample_questions():
    return [{
        "id": "question-123",
        "composite_id": "Q01",
        "question_id": 1,
        "type": "mcp",
        "multiple_choice": False,
        "question_content": [{
            "type": "text",
            "value": "Câu hỏi kiểm thử",
        }],
        "explanation_content": [],
        "mcp_options": [{
            "id": "option-123",
            "label": "A",
            "content": [{
                "type": "text",
                "value": "Đáp án A",
            }],
            "is_correct": False,
            "order_index": 0,
        }],
        "match_pairs": [],
        "fill_in_answers": [],
        "asset": {
            "image_path": "exam-123/question-123.png",
            "crop_status": "success",
            "page_number": 1,
            "crop_coordinates": None,
        },
    }]


class CloudRepositoryTests(unittest.TestCase):
    def test_upload_and_atomic_exam_insert(self):
        requests_seen = []

        def handler(request):
            requests_seen.append(request)

            self.assertEqual(
                request.headers["authorization"],
                "Bearer test-access-token",
            )

            if request.url.path.startswith(
                "/storage/v1/object/"
            ):
                return httpx.Response(
                    200,
                    json={"Key": "uploaded"},
                )

            if request.url.path.endswith(
                "/rpc/quizpdf_create_exam_bundle"
            ):
                payload = json.loads(request.content)

                self.assertEqual(
                    payload["p_exam"]["source_path"],
                    f"{USER_ID}/pdfs/exam-123.pdf",
                )

                self.assertEqual(
                    payload["p_questions"][0]["asset"][
                        "image_path"
                    ],
                    f"{USER_ID}/assets/"
                    "exam-123/question-123.png",
                )

                return httpx.Response(
                    200,
                    json={
                        "exam_id": "exam-123",
                        "question_count": 1,
                    },
                )

            return httpx.Response(404)

        client = httpx.Client(
            transport=httpx.MockTransport(handler)
        )

        with CloudRepository(
            USER_ID,
            "test-access-token",
            client=client,
            base_url=BASE_URL,
            publishable_key="test-publishable-key",
        ) as repository:
            result = repository.save_exam_bundle(
                sample_exam(),
                sample_questions(),
                b"%PDF-1.4 test PDF",
                {
                    "exam-123/question-123.png":
                        b"fake-png-data",
                },
            )

        client.close()

        self.assertEqual(
            result["exam_id"],
            "exam-123",
        )

        self.assertEqual(
            result["question_count"],
            1,
        )

        self.assertEqual(
            len(requests_seen),
            3,
        )

    def test_rpc_error_triggers_storage_cleanup(self):
        methods_seen = []

        def handler(request):
            methods_seen.append(request.method)

            if request.url.path.endswith(
                "/rpc/quizpdf_create_exam_bundle"
            ):
                return httpx.Response(
                    403,
                    json={"message": "Permission denied"},
                )

            return httpx.Response(
                200,
                json={"success": True},
            )

        client = httpx.Client(
            transport=httpx.MockTransport(handler)
        )

        with CloudRepository(
            USER_ID,
            "test-access-token",
            client=client,
            base_url=BASE_URL,
            publishable_key="test-publishable-key",
        ) as repository:
            with self.assertRaises(
                CloudRepositoryError
            ):
                repository.save_exam_bundle(
                    sample_exam(),
                    [],
                    b"%PDF test",
                )

        client.close()

        self.assertEqual(
            methods_seen,
            ["POST", "POST", "DELETE"],
        )

    def test_missing_crop_bytes_prevents_upload(self):
        requests_seen = []

        def handler(request):
            requests_seen.append(request)
            return httpx.Response(200)

        client = httpx.Client(
            transport=httpx.MockTransport(handler)
        )

        with CloudRepository(
            USER_ID,
            "test-access-token",
            client=client,
            base_url=BASE_URL,
            publishable_key="test-publishable-key",
        ) as repository:
            with self.assertRaises(
                CloudRepositoryError
            ):
                repository.save_exam_bundle(
                    sample_exam(),
                    sample_questions(),
                    b"%PDF test",
                )

        client.close()

        self.assertEqual(
            len(requests_seen),
            0,
        )

    def test_list_exams_preserves_frontend_contract(self):
        def handler(request):
            self.assertEqual(
                request.url.path,
                "/rest/v1/exams",
            )

            return httpx.Response(
                200,
                json=[{
                    "id": "exam-123",
                    "name": "Đề kiểm thử",
                    "subject": None,
                    "source_file": "sample.pdf",
                    "source_path":
                        f"{USER_ID}/pdfs/exam-123.pdf",
                    "uploaded_at":
                        "2026-11-22T00:00:00Z",
                    "questions": [{
                        "count": 2,
                    }],
                }],
            )

        client = httpx.Client(
            transport=httpx.MockTransport(handler)
        )

        with CloudRepository(
            USER_ID,
            "test-access-token",
            client=client,
            base_url=BASE_URL,
            publishable_key="test-publishable-key",
        ) as repository:
            exams = repository.list_exams()

        client.close()

        self.assertEqual(
            exams[0]["question_count"],
            2,
        )

        self.assertTrue(
            exams[0]["has_source_pdf"],
        )

    def test_invalid_user_id_is_rejected(self):
        with self.assertRaises(ValueError):
            CloudRepository(
                "not-a-uuid",
                "test-access-token",
                base_url=BASE_URL,
                publishable_key="test-publishable-key",
            )


if __name__ == "__main__":
    unittest.main()
