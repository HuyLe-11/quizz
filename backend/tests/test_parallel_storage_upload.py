"""Regression tests for bounded concurrent Supabase crop uploads."""

import json
import os
import threading
import time
import unittest
from unittest.mock import patch

import httpx

from backend.cloud_repository import CloudRepository, CloudRepositoryError

USER_ID = "11111111-1111-4111-8111-111111111111"
BASE_URL = "https://example.supabase.co"
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
        publishable_key="test-key",
    )


def sample_bundle(count=3):
    questions = [
        {
            "id": f"question-{i}",
            "asset": {"image_path": f"exam-123/q{i}.png"},
        }
        for i in range(count)
    ]
    images = {
        f"exam-123/q{i}.png": f"png-{i}".encode()
        for i in range(count)
    }
    exam = {
        "id": "exam-123",
        "name": "Test",
        "source_file": "sample.pdf",
    }
    return exam, questions, b"%PDF-1.4\nmock", images


def is_crop_post(request):
    return (
        request.method == "POST"
        and "/storage/v1/object/quizpdf-private/" in request.url.path
        and "/assets/" in request.url.path
    )


def ok_response(request):
    if request.url.path.endswith(
        "/rpc/quizpdf_create_exam_bundle"
    ):
        return httpx.Response(
            200,
            json={
                "exam_id": "exam-123",
                "question_count": 3,
            },
        )
    return httpx.Response(200, json={"Key": "saved"})


class ParallelStorageUploadTests(unittest.TestCase):
    def test_three_crop_posts_are_concurrent_and_rpc_is_last(self):
        barrier = threading.Barrier(3, timeout=3)
        lock = threading.Lock()
        events = []

        def handler(request):
            if (
                request.method == "POST"
                and "/pdfs/" in request.url.path
            ):
                with lock:
                    events.append("pdf")
                return ok_response(request)

            if is_crop_post(request):
                with lock:
                    events.append("image-start")
                barrier.wait()
                with lock:
                    events.append("image-end")
                return ok_response(request)

            if request.url.path.endswith(
                "/rpc/quizpdf_create_exam_bundle"
            ):
                with lock:
                    events.append("rpc")
                return ok_response(request)

            return httpx.Response(404)

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "3"},
        ):
            with httpx.Client(
                transport=httpx.MockTransport(handler)
            ) as client:
                result = make_repository(
                    client
                ).save_exam_bundle(*sample_bundle())

        self.assertEqual(result["exam_id"], "exam-123")
        self.assertEqual(events[0], "pdf")
        self.assertEqual(events[-1], "rpc")
        self.assertEqual(events.count("image-start"), 3)
        self.assertEqual(events.count("image-end"), 3)

        self.assertLess(
            max(
                i for i, e in enumerate(events)
                if e == "image-start"
            ),
            min(
                i for i, e in enumerate(events)
                if e == "image-end"
            ),
        )

    def test_rollback_waits_for_all_crop_workers(self):
        barrier = threading.Barrier(2, timeout=3)
        slow_done = threading.Event()
        calls = []
        lock = threading.Lock()

        def handler(request):
            if is_crop_post(request):
                if "/q0.png" in request.url.path:
                    barrier.wait()
                    return httpx.Response(
                        403, json={"error": "Forbidden"}
                    )

                if "/q1.png" in request.url.path:
                    barrier.wait()
                    time.sleep(0.05)
                    slow_done.set()
                    return ok_response(request)

            if request.method == "DELETE":
                self.assertTrue(
                    slow_done.is_set(),
                    "Rollback raced with unfinished crop",
                )
                with lock:
                    calls.append("delete")
                data = json.loads(request.content)
                self.assertEqual(len(data["prefixes"]), 3)
                return httpx.Response(200, json={})

            if request.url.path.endswith(
                "/rpc/quizpdf_create_exam_bundle"
            ):
                self.fail(
                    "Database RPC must not run after upload failure"
                )
            return ok_response(request)

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "2"},
        ):
            with httpx.Client(
                transport=httpx.MockTransport(handler)
            ) as client:
                with self.assertRaises(CloudRepositoryError):
                    make_repository(client).save_exam_bundle(
                        *sample_bundle(count=2)
                    )

        self.assertEqual(calls, ["delete"])

    def test_interrupted_post_is_retried_only_after_missing_verified(self):
        lock = threading.Lock()
        attempts = {}
        checks = []

        def handler(request):
            if is_crop_post(request):
                with lock:
                    attempts[request.url.path] = (
                        attempts.get(request.url.path, 0) + 1
                    )
                    number = attempts[request.url.path]

                if (
                    "/q0.png" in request.url.path
                    and number == 1
                ):
                    raise httpx.ReadError(
                        "Connection reset", request=request
                    )
                return ok_response(request)

            if request.method == "GET":
                with lock:
                    checks.append(request.url.path)
                return httpx.Response(
                    400, json=NOT_FOUND
                )

            return ok_response(request)

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "3"},
        ):
            with patch("backend.cloud_repository.time.sleep"):
                with httpx.Client(
                    transport=httpx.MockTransport(handler)
                ) as client:
                    result = make_repository(
                        client
                    ).save_exam_bundle(*sample_bundle())

        self.assertEqual(result["exam_id"], "exam-123")
        self.assertEqual(sum(attempts.values()), 4)
        self.assertEqual(len(checks), 2)

    def test_unknown_400_never_retries_post(self):
        counts = {
            "failed_image_post": 0,
            "delete": 0,
        }

        def handler(request):
            if (
                is_crop_post(request)
                and "/q0.png" in request.url.path
            ):
                counts["failed_image_post"] += 1
                raise httpx.ReadError(
                    "connection reset", request=request
                )

            if request.method == "GET":
                return httpx.Response(
                    400,
                    json={"error": "InvalidJWT"},
                )

            if request.method == "DELETE":
                counts["delete"] += 1
                return httpx.Response(200, json={})

            return ok_response(request)

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "3"},
        ):
            with patch("backend.cloud_repository.time.sleep"):
                with httpx.Client(
                    transport=httpx.MockTransport(handler)
                ) as client:
                    with self.assertRaises(
                        CloudRepositoryError
                    ):
                        make_repository(client).save_exam_bundle(
                            *sample_bundle()
                        )

        self.assertEqual(
            counts,
            {"failed_image_post": 1, "delete": 1},
        )

    def test_single_worker_mode_still_works(self):
        observed_threads = set()

        def handler(request):
            if is_crop_post(request):
                observed_threads.add(
                    threading.current_thread().name
                )
            return ok_response(request)

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "1"},
        ):
            with httpx.Client(
                transport=httpx.MockTransport(handler)
            ) as client:
                result = make_repository(
                    client
                ).save_exam_bundle(*sample_bundle())

        self.assertEqual(result["question_count"], 3)
        self.assertEqual(len(observed_threads), 1)

    def test_invalid_worker_limit_does_not_upload(self):
        observed = []

        def handler(request):
            observed.append(request)
            return ok_response(request)

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "99"},
        ):
            with httpx.Client(
                transport=httpx.MockTransport(handler)
            ) as client:
                with self.assertRaises(
                    CloudRepositoryError
                ):
                    make_repository(client).save_exam_bundle(
                        *sample_bundle()
                    )

        self.assertEqual(observed, [])

    def test_duplicate_crop_is_uploaded_once(self):
        posts = []

        def handler(request):
            if is_crop_post(request):
                posts.append(request.url.path)
            return ok_response(request)

        exam, questions, pdf, images = sample_bundle(1)
        questions.append({
            "asset": {"image_path": "exam-123/q0.png"}
        })

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "3"},
        ):
            with httpx.Client(
                transport=httpx.MockTransport(handler)
            ) as client:
                make_repository(client).save_exam_bundle(
                    exam, questions, pdf, images
                )

        self.assertEqual(len(posts), 1)


if __name__ == "__main__":
    unittest.main()
