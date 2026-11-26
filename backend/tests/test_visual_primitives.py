import unittest

import cv2
import numpy as np

from backend.engine.visual_primitives import (
    detect_horizontal_strokes,
)


class HorizontalStrokeTests(unittest.TestCase):

    def image(self):

        return np.full(
            (160, 320),
            255,
            dtype=np.uint8,
        )

    def test_empty(self):

        self.assertEqual(
            detect_horizontal_strokes(
                self.image(),
                reference_height=24,
            ),
            [],
        )

    def test_short_stroke(self):

        gray = self.image()

        gray[60:63, 100:116] = 0

        strokes = detect_horizontal_strokes(
            gray,
            reference_height=24,
        )

        self.assertEqual(
            [s.bbox for s in strokes],
            [
                (100, 60, 116, 63),
            ],
        )

        self.assertEqual(
            strokes[0].ink_pixels,
            48,
        )

    def test_long_stroke(self):

        gray = self.image()

        gray[70:73, 30:280] = 0

        strokes = detect_horizontal_strokes(
            gray,
            reference_height=24,
        )

        self.assertEqual(
            [s.bbox for s in strokes],
            [
                (30, 70, 280, 73),
            ],
        )

    def test_vertical_stroke(self):

        gray = self.image()

        gray[15:130, 100:103] = 0

        self.assertEqual(
            detect_horizontal_strokes(
                gray,
                reference_height=24,
            ),
            [],
        )

    def test_multiple_scales(self):

        for scale in (1, 2, 3):

            with self.subTest(scale=scale):

                gray = np.full(
                    (
                        160 * scale,
                        320 * scale,
                    ),
                    255,
                    dtype=np.uint8,
                )

                gray[
                    60 * scale:63 * scale,
                    100 * scale:116 * scale,
                ] = 0

                strokes = detect_horizontal_strokes(
                    gray,
                    reference_height=24 * scale,
                )

                self.assertEqual(
                    [s.bbox for s in strokes],
                    [
                        (
                            100 * scale,
                            60 * scale,
                            116 * scale,
                            63 * scale,
                        ),
                    ],
                )

    def test_two_strokes(self):

        gray = self.image()

        gray[60:62, 100:130] = 0
        gray[90:92, 40:120] = 0

        strokes = detect_horizontal_strokes(
            gray,
            reference_height=24,
        )

        self.assertEqual(
            [s.bbox for s in strokes],
            [
                (100, 60, 130, 62),
                (40, 90, 120, 92),
            ],
        )

    def test_input_unchanged(self):

        gray = self.image()

        gray[60:63, 100:116] = 0

        before = gray.copy()

        detect_horizontal_strokes(
            gray,
            reference_height=24,
        )

        np.testing.assert_array_equal(
            gray,
            before,
        )

    def test_invalid_input(self):

        for gray in (
            np.zeros(
                (20, 20, 3),
                dtype=np.uint8,
            ),
            np.zeros(
                (20, 20),
                dtype=np.float32,
            ),
        ):

            with self.assertRaises(ValueError):

                detect_horizontal_strokes(
                    gray,
                    reference_height=24,
                )

        for ref in (
            0,
            float("nan"),
            True,
            513,
        ):

            with self.assertRaises(ValueError):

                detect_horizontal_strokes(
                    self.image(),
                    reference_height=ref,
                )

    def test_small_region(self):

        self.assertEqual(
            detect_horizontal_strokes(
                np.full(
                    (8, 8),
                    255,
                    dtype=np.uint8,
                ),
                reference_height=24,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()