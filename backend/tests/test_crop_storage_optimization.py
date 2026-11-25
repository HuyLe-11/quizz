"""Storage upload optimization tests using mock HTTP, not Supabase."""

import json
import os
import unittest
from unittest.mock import patch

import cv2
import httpx
import numpy as np

from backend.cloud_media import (
    MAX_CROP_UPLOAD_BYTES,
    CropOptimizationError,
    prepare_crop_upload,
)
from backend.cloud_repository import CloudRepository

USER_ID = "11111111-1111-4111-8111-111111111111"


class CropStorageOptimizationTests(unittest.TestCase):
    def large_png(self):
        image = np.random.default_rng(123).integers(
            0, 256, size=(750, 1000, 3), dtype=np.uint8
        )
        ok, encoded = cv2.imencode(".png", image)
        self.assertTrue(ok)
        self.assertGreater(len(encoded), 512 * 1024)
        return encoded.tobytes()

    def test_large_image_is_optimized(self):
        original = self.large_png()
        content, extension, mime = prepare_crop_upload(original)

        self.assertEqual(extension, ".jpg")
        self.assertEqual(mime, "image/jpeg")
        self.assertLess(len(content), len(original))
        self.assertLessEqual(len(content), MAX_CROP_UPLOAD_BYTES)

        decoded = cv2.imdecode(
            np.frombuffer(content, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.shape, (750, 1000, 3))

    def test_small_crop_keeps_png(self):
        content = b"mock-crop-bytes"

        self.assertEqual(
            prepare_crop_upload(content),
            (content, ".png", "image/png"),
        )

    def test_corrupt_large_crop_is_rejected(self):
        with self.assertRaises(CropOptimizationError):
            prepare_crop_upload(b"x" * 600000)

    def test_identical_crops_upload_one_jpeg_and_share_path(self):
        content = self.large_png()
        uploaded = []
        rpc_payload = []

        def handler(request):
            if request.url.path.endswith(
                "/rpc/quizpdf_create_exam_bundle"
            ):
                rpc_payload.append(json.loads(request.content))
                return httpx.Response(
                    200,
                    json={
                        "exam_id": "exam-123",
                        "question_count": 2,
                    },
                )

            if (
                request.method == "POST"
                and "/storage/" in request.url.path
            ):
                uploaded.append(request)
                return httpx.Response(
                    200,
                    json={"Key": "saved"},
                )

            return httpx.Response(404)

        exam = {
            "id": "exam-123",
            "name": "Test",
        }
        questions = [
            {"asset": {"image_path": "exam-123/q0.png"}},
            {"asset": {"image_path": "exam-123/q1.png"}},
        ]
        crops = {
            "exam-123/q0.png": content,
            "exam-123/q1.png": content,
        }

        with patch.dict(
            os.environ,
            {"QUIZPDF_STORAGE_UPLOAD_WORKERS": "1"},
        ):
            with httpx.Client(
                transport=httpx.MockTransport(handler)
            ) as client:
                repository = CloudRepository(
                    USER_ID,
                    "test-access-token",
                    client=client,
                    base_url="https://example.supabase.co",
                    publishable_key="publishable-key",
                )

                result = repository.save_exam_bundle(
                    exam,
                    questions,
                    b"%PDF-1.4\nmock",
                    crops,
                )

        self.assertEqual(result["question_count"], 2)

        # One PDF and one JPEG, instead of one PDF and two PNGs.
        self.assertEqual(len(uploaded), 2)

        image_request = next(
            request
            for request in uploaded
            if "/assets/" in request.url.path
        )

        self.assertTrue(
            image_request.url.path.endswith(".jpg")
        )
        self.assertEqual(
            image_request.headers["content-type"],
            "image/jpeg",
        )
        self.assertLess(
            len(image_request.content),
            len(content),
        )

        stored_questions = rpc_payload[0]["p_questions"]
        first_path = stored_questions[0]["asset"]["image_path"]
        second_path = stored_questions[1]["asset"]["image_path"]

        self.assertEqual(first_path, second_path)
        self.assertTrue(first_path.endswith(".jpg"))

        # Original question objects must not be mutated.
        self.assertEqual(
            questions[0]["asset"]["image_path"],
            "exam-123/q0.png",
        )


if __name__ == "__main__":
    unittest.main()
