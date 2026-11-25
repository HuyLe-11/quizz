"""Regression tests for partial Supabase Storage persistence."""

import os
import unittest

from unittest.mock import patch

import httpx

from backend.cloud_repository import (
    CloudRepository,
    CloudRepositoryError,
)

USER = "11111111-1111-4111-8111-111111111111"
BASE = "https://example.supabase.co"

PREFIX = (
    f"/storage/v1/object/quizpdf-private/{USER}/"
)
READ_PREFIX = (
    "/storage/v1/object/authenticated/"
    f"quizpdf-private/{USER}/"
)

NOT_FOUND = {
    "statusCode": "404",
    "error": "not_found",
    "message": "Object not found",
    "code": "NoSuchKey",
}


def exam_and_questions():
    return (
        {
            "id": "exam-123",
            "name": "Example",
        },
        [
            {
                "asset": {
                    "image_path": "exam-123/q0.png",
                },
            },
            {
                "asset": {
                    "image_path": "exam-123/q1.png",
                },
            },
        ],
        b"%PDF-1.4 mock contents",
        {
            "exam-123/q0.png": b"image zero",
            "exam-123/q1.png": b"image one",
        },
    )


class FakeStorage:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.fail_once = None
        self.unknown_reads = False
        self.rpc_status = 200
        self.rpc_calls = 0

    def __call__(self, request):
        path = request.url.path
        self.calls.append(
            (request.method, path)
        )

        if path == (
            "/rest/v1/rpc/"
            "quizpdf_create_exam_bundle"
        ):
            self.rpc_calls += 1

            if self.rpc_status != 200:
                return httpx.Response(
                    self.rpc_status,
                    json={"message": "failure"},
                )

            return httpx.Response(
                200,
                json={
                    "exam_id": "exam-123",
                    "question_count": 2,
                },
            )

        if (
            request.method == "GET"
            and path.startswith(READ_PREFIX)
        ):
            if self.unknown_reads:
                return httpx.Response(
                    503,
                    json={"message": "unknown"},
                )

            object_path = path[len(READ_PREFIX):]

            if object_path in self.objects:
                return httpx.Response(
                    200,
                    content=self.objects[object_path],
                )

            return httpx.Response(
                400,
                json=NOT_FOUND,
            )

        if (
            request.method == "POST"
            and path.startswith(PREFIX)
        ):
            object_path = path[len(PREFIX):]

            if object_path == self.fail_once:
                self.fail_once = None

                return httpx.Response(
                    403,
                    json={
                        "message": "blocked temporarily"
                    },
                )

            if object_path in self.objects:
                return httpx.Response(
                    409,
                    json={
                        "message": "already exists"
                    },
                )

            self.objects[object_path] = request.content

            return httpx.Response(
                200,
                json={"Key": object_path},
            )

        if (
            request.method == "DELETE"
            and path
            == "/storage/v1/object/quizpdf-private"
        ):
            return httpx.Response(
                200,
                json={},
            )

        raise AssertionError(
            f"Unexpected {request.method} {path}"
        )


def repository(server, client):
    return CloudRepository(
        USER,
        "token",
        client=client,
        base_url=BASE,
        publishable_key="key",
    )


class ResumeStorageTests(unittest.TestCase):
    def test_retry_reuses_pdf_and_successful_image(self):
        backend = FakeStorage()
        backend.fail_once = "assets/exam-123/q1.png"

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "1"},
        ):
            with httpx.Client(
                transport=httpx.MockTransport(backend)
            ) as client:
                db = repository(backend, client)

                with self.assertRaises(
                    CloudRepositoryError
                ):
                    db.save_exam_bundle(
                        *exam_and_questions()
                    )

                self.assertEqual(backend.rpc_calls, 0)
                self.assertEqual(len(backend.objects), 2)

                self.assertNotIn(
                    (
                        "DELETE",
                        "/storage/v1/object/quizpdf-private",
                    ),
                    backend.calls,
                )

                result = db.save_exam_bundle(
                    *exam_and_questions(),
                    resume_existing=True,
                )

        self.assertEqual(
            result["question_count"],
            2,
        )
        self.assertEqual(len(backend.objects), 3)
        self.assertEqual(backend.rpc_calls, 1)

        for name in (
            "pdfs/exam-123.pdf",
            "assets/exam-123/q0.png",
        ):
            self.assertEqual(
                backend.calls.count(
                    ("POST", PREFIX + name)
                ),
                1,
            )

        self.assertEqual(
            backend.calls.count(
                (
                    "POST",
                    PREFIX + "assets/exam-123/q1.png",
                )
            ),
            2,
        )

    def test_no_new_image_after_first_failure(self):
        backend = FakeStorage()
        backend.fail_once = "assets/exam-123/q0.png"

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "1"},
        ):
            with httpx.Client(
                transport=httpx.MockTransport(backend)
            ) as client:
                db = repository(backend, client)

                with self.assertRaises(
                    CloudRepositoryError
                ):
                    db.save_exam_bundle(
                        *exam_and_questions()
                    )

        self.assertEqual(
            backend.calls.count(
                (
                    "POST",
                    PREFIX + "assets/exam-123/q1.png",
                )
            ),
            0,
        )

        # Only the source PDF was stored.
        self.assertEqual(
            len(backend.objects),
            1,
        )

    def test_mismatch_stops_before_post(self):
        backend = FakeStorage()
        backend.objects[
            "pdfs/exam-123.pdf"
        ] = b"different pdf bytes"

        with httpx.Client(
            transport=httpx.MockTransport(backend)
        ) as client:
            db = repository(backend, client)

            with self.assertRaisesRegex(
                CloudRepositoryError,
                "different content",
            ):
                db.save_exam_bundle(
                    *exam_and_questions(),
                    resume_existing=True,
                )

        self.assertFalse(
            any(
                method == "POST"
                for method, _ in backend.calls
            )
        )

    def test_unknown_verification_does_not_upload(self):
        backend = FakeStorage()
        backend.unknown_reads = True

        with httpx.Client(
            transport=httpx.MockTransport(backend)
        ) as client:
            db = repository(backend, client)

            with self.assertRaisesRegex(
                CloudRepositoryError,
                "Could not verify",
            ):
                db.save_exam_bundle(
                    *exam_and_questions(),
                    resume_existing=True,
                )

        self.assertFalse(
            any(
                method == "POST"
                for method, _ in backend.calls
            )
        )

    def test_uncertain_rpc_keeps_uploaded_objects(self):
        backend = FakeStorage()
        backend.rpc_status = 503

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "1"},
        ):
            with httpx.Client(
                transport=httpx.MockTransport(backend)
            ) as client:
                db = repository(backend, client)

                with self.assertRaises(
                    CloudRepositoryError
                ):
                    db.save_exam_bundle(
                        *exam_and_questions()
                    )

                self.assertEqual(
                    len(backend.objects),
                    3,
                )

                backend.rpc_status = 200

                result = db.save_exam_bundle(
                    *exam_and_questions(),
                    resume_existing=True,
                )

        self.assertEqual(
            result["question_count"],
            2,
        )
        self.assertEqual(
            backend.rpc_calls,
            2,
        )

        storage_posts = [
            call
            for call in backend.calls
            if call[0] == "POST"
            and call[1].startswith(PREFIX)
        ]

        self.assertEqual(
            len(storage_posts),
            3,
        )

    def test_rpc_403_on_resume_preserves_objects(self):
        backend = FakeStorage()

        backend.objects[
            "pdfs/exam-123.pdf"
        ] = exam_and_questions()[2]

        backend.objects[
            "assets/exam-123/q0.png"
        ] = b"image zero"

        backend.objects[
            "assets/exam-123/q1.png"
        ] = b"image one"

        backend.rpc_status = 403

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "1"},
        ):
            with httpx.Client(
                transport=httpx.MockTransport(backend)
            ) as client:
                db = repository(backend, client)

                with self.assertRaises(
                    CloudRepositoryError
                ):
                    db.save_exam_bundle(
                        *exam_and_questions(),
                        resume_existing=True,
                    )

        self.assertEqual(
            len(backend.objects),
            3,
        )

        self.assertFalse(
            any(
                method == "DELETE"
                for method, _ in backend.calls
            )
        )

    def test_definitive_rpc_403_cleans_up(self):
        backend = FakeStorage()
        backend.rpc_status = 403

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "1"},
        ):
            with httpx.Client(
                transport=httpx.MockTransport(backend)
            ) as client:
                db = repository(backend, client)

                with self.assertRaises(
                    CloudRepositoryError
                ):
                    db.save_exam_bundle(
                        *exam_and_questions()
                    )

        self.assertEqual(
            backend.calls[-1],
            (
                "DELETE",
                "/storage/v1/object/quizpdf-private",
            ),
        )


if __name__ == "__main__":
    unittest.main()
