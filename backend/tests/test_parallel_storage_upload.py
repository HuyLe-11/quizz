"""Regression tests for bounded concurrent Supabase image uploads."""

import json
import os
import threading
import time
import unittest
from unittest.mock import patch

import httpx

from backend.cloud_repository import (
    CloudRepository,
    CloudRepositoryError,
)

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
        publishable_key="test-publishable-key",
    )


def sample_bundle(count=3):
    questions = [
        {
            "id": f"question-{index}",
            "asset": {
                "image_path": f"exam-123/q{index}.png"
            },
        }
        for index in range(count)
    ]

    images = {
        f"exam-123/q{index}.png":
            f"image-{index}".encode()
        for index in range(count)
    }

    exam = {
        "id": "exam-123",
        "name": "Test exam",
    }

    return (
        exam,
        questions,
        b"%PDF-1.4\nmock",
        images,
    )


def is_crop_post(request):
    return (
        request.method == "POST"
        and "/storage/v1/object/quizpdf-private/"
        in request.url.path
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

    return httpx.Response(
        200,
        json={"Key": "saved"},
    )


class ParallelStorageUploadTests(unittest.TestCase):

    def test_three_crop_posts_are_concurrent_and_rpc_is_last(self):
        barrier = threading.Barrier(
            3,
            timeout=3,
        )

        lock = threading.Lock()
        events = []

        def record(value):
            with lock:
                events.append(value)

        def handler(request):
            if (
                request.method == "POST"
                and "/pdfs/" in request.url.path
            ):
                record("pdf")
                return ok_response(request)

            if is_crop_post(request):
                record("image-start")
                barrier.wait()
                record("image-end")
                return ok_response(request)

            if request.url.path.endswith(
                "/rpc/quizpdf_create_exam_bundle"
            ):
                record("rpc")
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
                ).save_exam_bundle(
                    *sample_bundle()
                )

        self.assertEqual(
            result["exam_id"],
            "exam-123",
        )
        self.assertEqual(events[0], "pdf")
        self.assertEqual(events[-1], "rpc")

        self.assertEqual(
            events.count("image-start"),
            3,
        )
        self.assertEqual(
            events.count("image-end"),
            3,
        )

        # Every crop starts before any crop completes.
        self.assertLess(
            max(
                index
                for index, event in enumerate(events)
                if event == "image-start"
            ),
            min(
                index
                for index, event in enumerate(events)
                if event == "image-end"
            ),
        )

    def test_rollback_waits_for_all_crop_workers(self):
        """Legacy test name: cleanup is now replaced by preservation."""
        barrier = threading.Barrier(
            2,
            timeout=3,
        )

        slow_done = threading.Event()
        events = []

        def handler(request):
            if is_crop_post(request):
                if "/q0.png" in request.url.path:
                    barrier.wait()

                    events.append("first-failed")

                    return httpx.Response(
                        403,
                        json={"error": "Forbidden"},
                    )

                if "/q1.png" in request.url.path:
                    barrier.wait()

                    time.sleep(0.05)
                    slow_done.set()

                    events.append("second-completed")

                    return ok_response(request)

            if request.method == "DELETE":
                events.append("delete")
                return httpx.Response(200, json={})

            if request.url.path.endswith(
                "/rpc/quizpdf_create_exam_bundle"
            ):
                events.append("rpc")
                return ok_response(request)

            return ok_response(request)

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "2"},
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
                        *sample_bundle(count=2)
                    )

        # All active workers must have finished before failure returns.
        self.assertTrue(slow_done.is_set())
        self.assertIn("second-completed", events)

        # New behavior: no DELETE; successful objects are reusable.
        self.assertNotIn("delete", events)

        # The database is never written after a Storage failure.
        self.assertNotIn("rpc", events)

    def test_interrupted_post_is_retried_only_after_missing_verified(self):
        lock = threading.Lock()
        attempts = {}
        checks = []

        def handler(request):
            if is_crop_post(request):
                with lock:
                    attempts[request.url.path] = (
                        attempts.get(
                            request.url.path,
                            0,
                        ) + 1
                    )

                    number = attempts[
                        request.url.path
                    ]

                if (
                    "/q0.png" in request.url.path
                    and number == 1
                ):
                    raise httpx.ReadError(
                        "Connection reset",
                        request=request,
                    )

                return ok_response(request)

            if request.method == "GET":
                checks.append(
                    request.url.path
                )

                return httpx.Response(
                    400,
                    json=NOT_FOUND,
                )

            return ok_response(request)

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "3"},
        ):
            with patch(
                "backend.cloud_repository.time.sleep"
            ):
                with httpx.Client(
                    transport=httpx.MockTransport(handler)
                ) as client:
                    result = make_repository(
                        client
                    ).save_exam_bundle(
                        *sample_bundle()
                    )

        self.assertEqual(
            result["exam_id"],
            "exam-123",
        )
        self.assertEqual(
            sum(attempts.values()),
            4,
        )
        self.assertEqual(
            len(checks),
            2,
        )

    def test_unknown_400_never_retries_post(self):
        counts = {
            "failed_image_post": 0,
            "verify": 0,
            "delete": 0,
            "rpc": 0,
        }

        def handler(request):
            if (
                is_crop_post(request)
                and "/q0.png" in request.url.path
            ):
                counts["failed_image_post"] += 1

                raise httpx.ReadError(
                    "Connection reset",
                    request=request,
                )

            if request.method == "GET":
                counts["verify"] += 1

                return httpx.Response(
                    400,
                    json={"error": "InvalidJWT"},
                )

            if request.method == "DELETE":
                counts["delete"] += 1
                return httpx.Response(200, json={})

            if request.url.path.endswith(
                "/rpc/quizpdf_create_exam_bundle"
            ):
                counts["rpc"] += 1
                return ok_response(request)

            return ok_response(request)

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "3"},
        ):
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
                            *sample_bundle()
                        )

        # The ambiguous POST must not be submitted again.
        self.assertEqual(
            counts["failed_image_post"],
            1,
        )
        self.assertEqual(counts["verify"], 2)

        # Preserve objects for resume rather than deleting them.
        self.assertEqual(counts["delete"], 0)

        # Never persist incomplete exams.
        self.assertEqual(counts["rpc"], 0)

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
                ).save_exam_bundle(
                    *sample_bundle()
                )

        self.assertEqual(
            result["question_count"],
            3,
        )
        self.assertEqual(
            len(observed_threads),
            1,
        )

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
                    make_repository(
                        client
                    ).save_exam_bundle(
                        *sample_bundle()
                    )

        self.assertEqual(observed, [])

    def test_duplicate_crop_is_uploaded_once(self):
        posts = []

        def handler(request):
            if is_crop_post(request):
                posts.append(
                    request.url.path
                )

            return ok_response(request)

        exam, questions, pdf, images = (
            sample_bundle(count=1)
        )

        questions.append({
            "asset": {
                "image_path": "exam-123/q0.png"
            },
        })

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "3"},
        ):
            with httpx.Client(
                transport=httpx.MockTransport(handler)
            ) as client:
                make_repository(
                    client
                ).save_exam_bundle(
                    exam,
                    questions,
                    pdf,
                    images,
                )

        self.assertEqual(len(posts), 1)


if __name__ == "__main__":
    unittest.main()
