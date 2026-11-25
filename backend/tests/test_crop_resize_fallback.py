"""Regression tests for oversized scanned-page crop optimization."""

import unittest
from unittest.mock import patch

import cv2
import numpy as np

from backend import cloud_media


class CropResizeFallbackTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        image = np.random.default_rng(123).integers(
            0,
            256,
            size=(2200, 1800, 3),
            dtype=np.uint8,
        )

        ok, png = cv2.imencode(
            ".png",
            image,
        )

        assert ok

        cls.contents = png.tobytes()
        cls.shape = image.shape

    def test_oversized_crop_resizes_without_distortion(self):
        contents, suffix, mime = (
            cloud_media.prepare_crop_upload(
                self.contents
            )
        )

        self.assertEqual(
            (suffix, mime),
            (".jpg", "image/jpeg"),
        )

        self.assertLessEqual(
            len(contents),
            cloud_media.MAX_CROP_UPLOAD_BYTES,
        )

        image = cv2.imdecode(
            np.frombuffer(contents, np.uint8),
            cv2.IMREAD_COLOR,
        )

        self.assertIsNotNone(image)

        height, width = image.shape[:2]

        self.assertLess(
            width,
            self.shape[1],
        )

        self.assertLess(
            height,
            self.shape[0],
        )

        self.assertAlmostEqual(
            width / height,
            self.shape[1] / self.shape[0],
            places=3,
        )

    def test_optimization_has_a_bounded_number_of_attempts(self):
        with (
            patch.object(
                cloud_media,
                "MAX_CROP_UPLOAD_BYTES",
                1,
            ),
            patch.object(
                cloud_media.cv2,
                "imencode",
                wraps=cv2.imencode,
            ) as encode,
        ):
            with self.assertRaises(
                cloud_media.CropOptimizationError
            ):
                cloud_media.prepare_crop_upload(
                    self.contents
                )

        self.assertEqual(
            encode.call_count,
            (
                cloud_media.MAX_RESIZE_STEPS + 1
            ) * len(
                cloud_media.JPEG_QUALITIES
            ),
        )

    def test_empty_crop_rejected(self):
        with self.assertRaises(
            cloud_media.CropOptimizationError
        ):
            cloud_media.prepare_crop_upload(b"")


if __name__ == "__main__":
    unittest.main()