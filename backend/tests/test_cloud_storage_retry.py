"""Regression tests for safe Supabase Storage retries and recovery."""

import unittest
from unittest.mock import patch

import httpx

from backend.cloud_repository import (
    CloudRepository,
    CloudRepositoryError,
)

USER_ID = "11111111-1111-4111-8111-111111111111"
BASE_URL = "https://example.supabase.co"
PDF_CONTENTS = b"%PDF-1.4\nTest document"

PDF_OBJECT = f"{USER_ID}/pdfs/exam-123.pdf"

WRITE_PATH = (
    "/storage/v1/object/quizpdf-private/"
    + PDF_OBJECT
)
READ_PATH = (
    "/storage/v1/object/authenticated/"
    "quizpdf-private/"
    + PDF_OBJECT
)
RPC_PATH = "/rest/v1/rpc/quizpdf_create_exam_bundle"
DELETE_PATH = "/storage/v1/object/quizpdf-private"

NOT_FOUND = {
    "statusCode": "404",
    "error": "not_found",
    "message": "Object not found",
    "code": "NoSuchKey",
}


def make_repository(client):
    return CloudRepository(
        USER_ID,
        "test-access-token",
        client=client,
        base_url=BASE_URL,
        publishable_key="test-publishable-key",
    )


def sample_exam():
    return {
        "id": "exam-123",
        "name": "Test exam",
    }


def rpc_response():
    return httpx.Response(
        200,
        json={
            "exam_id": "exam-123",
            "question_count": 0,
        },
    )


class CloudStorageRetryTests(unittest.TestCase):

    def test_supabase_400_nosuchkey_is_missing(self):
        response = httpx.Response(
            400,
            json=NOT_FOUND,
        )

        self.assertTrue(
            CloudRepository._storage_object_not_found(
                response
            )
        )

    def test_other_400_is_not_missing(self):
        response = httpx.Response(
            400,
            json={
                "statusCode": "400",
                "error": "InvalidJWT",
                "message": "Token is invalid",
            },
        )

        self.assertFalse(
            CloudRepository._storage_object_not_found(
                response
            )
        )

    def test_transport_reset_and_nosuchkey_retries_post(self):
        calls = {
            "post": 0,
            "verify": 0,
        }

        def handler(request):
            if (
                request.method == "POST"
                and request.url.path == WRITE_PATH
            ):
                calls["post"] += 1

                if calls["post"] == 1:
                    raise httpx.ReadError(
                        "Connection reset",
                        request=request,
                    )

                return httpx.Response(
                    200,
                    json={"Key": PDF_OBJECT},
                )

            if (
                request.method == "GET"
                and request.url.path == READ_PATH
            ):
                calls["verify"] += 1

                return httpx.Response(
                    400,
                    json=NOT_FOUND,
                )

            if request.url.path == RPC_PATH:
                return rpc_response()

            raise AssertionError(
                f"Unexpected request: {request.method} "
                f"{request.url.path}"
            )

        with patch(
            "backend.cloud_repository.time.sleep"
        ):
            with httpx.Client(
                transport=httpx.MockTransport(handler)
            ) as client:
                result = make_repository(
                    client
                ).save_exam_bundle(
                    sample_exam(),
                    [],
                    PDF_CONTENTS,
                )

        self.assertEqual(
            result["question_count"],
            0,
        )
        self.assertEqual(calls["post"], 2)
        self.assertEqual(calls["verify"], 2)

    def test_response_lost_but_bytes_present_does_not_retry(self):
        calls = {
            "post": 0,
            "verify": 0,
        }

        stored = {}

        def handler(request):
            if (
                request.method == "POST"
                and request.url.path == WRITE_PATH
            ):
                calls["post"] += 1
                stored["pdf"] = request.content

                raise httpx.ReadError(
                    "Response lost after upload",
                    request=request,
                )

            if (
                request.method == "GET"
                and request.url.path == READ_PATH
            ):
                calls["verify"] += 1

                return httpx.Response(
                    200,
                    content=stored["pdf"],
                )

            if request.url.path == RPC_PATH:
                return rpc_response()

            raise AssertionError(
                f"Unexpected request: {request.method} "
                f"{request.url.path}"
            )

        with httpx.Client(
            transport=httpx.MockTransport(handler)
        ) as client:
            result = make_repository(
                client
            ).save_exam_bundle(
                sample_exam(),
                [],
                PDF_CONTENTS,
            )

        self.assertEqual(
            result["question_count"],
            0,
        )

        # The original POST succeeded remotely.
        # Verification avoids uploading the same PDF again.
        self.assertEqual(calls["post"], 1)
        self.assertEqual(calls["verify"], 1)

    def test_permission_denied_is_not_retried(self):
        methods = []

        def handler(request):
            methods.append(request.method)

            if request.url.path == WRITE_PATH:
                return httpx.Response(
                    403,
                    json={
                        "message": "Permission denied"
                    },
                )

            raise AssertionError(
                "Storage failure must not trigger "
                "RPC or destructive cleanup."
            )

        with httpx.Client(
            transport=httpx.MockTransport(handler)
        ) as client:
            with self.assertRaises(
                CloudRepositoryError
            ):
                make_repository(
                    client
                ).save_exam_bundle(
                    sample_exam(),
                    [],
                    PDF_CONTENTS,
                )

        self.assertEqual(methods, ["POST"])

    def test_unknown_http_400_never_retries_post(self):
        calls = {
            "post": 0,
            "verify": 0,
            "delete": 0,
            "rpc": 0,
        }

        def handler(request):
            if (
                request.method == "POST"
                and request.url.path == WRITE_PATH
            ):
                calls["post"] += 1

                raise httpx.ReadError(
                    "Connection lost",
                    request=request,
                )

            if (
                request.method == "GET"
                and request.url.path == READ_PATH
            ):
                calls["verify"] += 1

                return httpx.Response(
                    400,
                    json={
                        "error": "InvalidJWT",
                        "message": "Cannot verify object",
                    },
                )

            if (
                request.method == "DELETE"
                and request.url.path == DELETE_PATH
            ):
                calls["delete"] += 1

                return httpx.Response(
                    200,
                    json={},
                )

            if request.url.path == RPC_PATH:
                calls["rpc"] += 1
                return rpc_response()

            raise AssertionError(
                f"Unexpected {request.method} "
                f"{request.url.path}"
            )

        with patch(
            "backend.cloud_repository.time.sleep"
        ):
            with httpx.Client(
                transport=httpx.MockTransport(handler)
            ) as client:
                with self.assertRaises(
                    CloudRepositoryError
                ):
                    make_repository(
                        client
                    ).save_exam_bundle(
                        sample_exam(),
                        [],
                        PDF_CONTENTS,
                    )

        self.assertEqual(calls["post"], 1)
        self.assertEqual(calls["verify"], 2)

        # Preserve potentially committed objects.
        # Do not retry POST or delete after an unknown state.
        self.assertEqual(calls["delete"], 0)
        self.assertEqual(calls["rpc"], 0)


if __name__ == "__main__":
    unittest.main()
