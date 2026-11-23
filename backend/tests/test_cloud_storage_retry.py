"""Regression tests for Supabase Storage NoSuchKey and interrupted POST."""

import logging
import unittest
from unittest.mock import patch

import httpx

from backend.cloud_repository import CloudRepository, CloudRepositoryError

USER_ID = "11111111-1111-4111-8111-111111111111"
BASE_URL = "https://example.supabase.co"
PDF = b"%PDF-1.4 test content"
NOT_FOUND = {
    "statusCode": "404",
    "error": "not_found",
    "message": "Object not found",
    "code": "NoSuchKey",
}


def sample_exam():
    return {"id": "exam-123", "name": "Test", "source_file": "sample.pdf"}


def make_repository(client):
    return CloudRepository(
        USER_ID,
        "access-token",
        client=client,
        base_url=BASE_URL,
        publishable_key="publishable-key",
    )


def rpc_response():
    return httpx.Response(
        200,
        json={"exam_id": "exam-123", "question_count": 0},
    )


class CloudStorageRetryTests(unittest.TestCase):
    def test_supabase_400_nosuchkey_is_missing(self):
        response = httpx.Response(400, json=NOT_FOUND)
        self.assertEqual(len(response.content), 88)
        self.assertTrue(CloudRepository._storage_object_not_found(response))

    def test_other_400_is_not_missing(self):
        for data in (
            {
                "statusCode": "400",
                "error": "InvalidJWT",
                "message": "Object not found",
            },
            {
                "statusCode": "404",
                "error": "not_found",
                "message": "Object not found",
            },
            {
                "statusCode": "404",
                "code": "NoSuchKey",
                "error": "access_denied",
                "message": "Object not found",
            },
        ):
            self.assertFalse(
                CloudRepository._storage_object_not_found(
                    httpx.Response(400, json=data)
                )
            )

    def test_transport_reset_and_nosuchkey_retries_post(self):
        posts, reads = [], []

        def handler(request):
            if request.method == "POST" and "/storage/" in request.url.path:
                posts.append(request)
                if len(posts) == 1:
                    raise httpx.ReadError("reset", request=request)
                return httpx.Response(200, json={"Key": "saved"})

            if request.method == "GET":
                reads.append(request)
                return httpx.Response(400, json=NOT_FOUND)

            if request.url.path.endswith("/quizpdf_create_exam_bundle"):
                return rpc_response()

            return httpx.Response(404)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with patch("backend.cloud_repository.time.sleep"):
                result = make_repository(client).save_exam_bundle(
                    sample_exam(), [], PDF
                )

        self.assertEqual(result["question_count"], 0)
        self.assertEqual(len(posts), 2)
        self.assertEqual(len(reads), 2)

    def test_response_lost_but_bytes_present_does_not_retry(self):
        posts, reads = [], []

        def handler(request):
            if request.method == "POST" and "/storage/" in request.url.path:
                posts.append(request)
                raise httpx.ReadError("reset", request=request)

            if request.method == "GET":
                reads.append(request)
                return httpx.Response(200, content=PDF)

            if request.url.path.endswith("/quizpdf_create_exam_bundle"):
                return rpc_response()

            return httpx.Response(404)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = make_repository(client).save_exam_bundle(
                sample_exam(), [], PDF
            )

        self.assertEqual(result["exam_id"], "exam-123")
        self.assertEqual(len(posts), 1)
        self.assertEqual(len(reads), 1)

    def test_unknown_http_400_never_retries_post(self):
        posts, deletes = [], []

        def handler(request):
            if request.method == "POST" and "/storage/" in request.url.path:
                posts.append(request)
                raise httpx.ReadError("reset", request=request)

            if request.method == "GET":
                return httpx.Response(400, json={"error": "InvalidJWT"})

            if request.method == "DELETE":
                deletes.append(request)
                return httpx.Response(200, json={})

            return httpx.Response(404)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with patch("backend.cloud_repository.time.sleep"):
                with self.assertRaises(CloudRepositoryError):
                    make_repository(client).save_exam_bundle(
                        sample_exam(), [], PDF
                    )

        self.assertEqual(len(posts), 1)
        self.assertEqual(len(deletes), 1)

    def test_permission_denied_is_not_retried(self):
        posts = []

        def handler(request):
            if request.method == "POST" and "/storage/" in request.url.path:
                posts.append(request)
                return httpx.Response(403, json={"message": "Forbidden"})

            if request.method == "DELETE":
                return httpx.Response(200, json={})

            return httpx.Response(404)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(CloudRepositoryError):
                make_repository(client).save_exam_bundle(
                    sample_exam(), [], PDF
                )

        self.assertEqual(len(posts), 1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    unittest.main()
