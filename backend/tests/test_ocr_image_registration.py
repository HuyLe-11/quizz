import unittest
from unittest.mock import patch

import cv2 as cv
import numpy as np
import pymupdf

from backend.engine import (
    ocr_image_registration as reg,
)


class ImageRegistrationTests(unittest.TestCase):

    def setUp(self):

        self.doc = pymupdf.open()

        self.addCleanup(
            self.doc.close
        )

        self.page = self.doc.new_page(
            width=480,
            height=480,
        )

        rng = np.random.default_rng(123)

        for i in range(120):

            x, y = rng.integers(
                15,
                425,
                size=2,
            )

            self.page.insert_text(
                (int(x), int(y)),
                f"{i:03d} X",
                fontsize=10,
            )

        pix = self.page.get_pixmap(
            alpha=False
        )

        self.image = cv.cvtColor(
            np.frombuffer(
                pix.samples,
                np.uint8,
            ).reshape(480, 480, 3),

            cv.COLOR_RGB2BGR,
        )

        self.target = cv.warpAffine(
            self.image,

            np.float32([
                [1, 0, 8],
                [0, 1, 5],
            ]),

            (480, 480),

            borderValue=(
                255,
                255,
                255,
            ),
        )

        self.combined = np.concatenate(
            (
                self.target,
                self.image,
            ),
            axis=1,
        )

    def register(
        self,
        image=None,
        combined=None,
    ):

        return reg.register_page(
            self.page,

            (
                self.image
                if image is None
                else image
            ),

            (
                self.combined
                if combined is None
                else combined
            ),

            width=480,
            height=480,
            unit=15,
        )

    def test_translation(self):

        result = self.register()

        point = result.map_point(
            240,
            240,
        )

        self.assertIsNotNone(point)

        self.assertAlmostEqual(
            point.x,
            248,
            delta=2,
        )

        self.assertAlmostEqual(
            point.y,
            245,
            delta=2,
        )

        self.assertEqual(
            result.input_mae,
            0,
        )

    def test_different_input(self):

        with self.assertRaises(
            reg.RegistrationError
        ):

            self.register(
                image=np.zeros_like(
                    self.image
                )
            )

    def test_wrong_panel_size(self):

        with self.assertRaises(
            reg.RegistrationError
        ):

            self.register(
                combined=self.target,
            )

    def test_blank_target(self):

        with self.assertRaises(
            reg.RegistrationError
        ):

            self.register(
                combined=np.full_like(
                    self.combined,
                    255,
                )
            )

    def test_rotated_page(self):

        self.page.set_rotation(90)

        with self.assertRaises(
            reg.RegistrationError
        ):

            self.register()

    def test_invalid_points(self):

        result = self.register()

        for x, y in (
            (-1, 0),
            (480, 0),
            (0, 480),
            (float("nan"), 20),
        ):

            self.assertIsNone(
                result.map_point(x, y)
            )

    def test_conflict_and_missing_support(self):

        result = self.register()

        with (
            patch.object(
                reg,
                "_local_affine",

                return_value=np.array([
                    248.,
                    245.,
                ]),
            ),

            patch.object(
                reg,
                "_template_match",

                return_value=np.array([
                    270.,
                    245.,
                ]),
            ),
        ):

            self.assertIsNone(
                result.map_point(
                    240,
                    240,
                )
            )

        with (
            patch.object(
                reg,
                "_local_affine",
                return_value=None,
            ),

            patch.object(
                reg,
                "_template_match",
                return_value=None,
            ),
        ):

            self.assertIsNone(
                result.map_point(
                    240,
                    240,
                )
            )

    def test_no_affine_extrapolation(self):

        p = np.float32([
            (x, y)
            for x in range(10, 40, 5)
            for y in range(10, 40, 5)
        ])

        q = p + np.float32([
            8,
            5,
        ])

        self.assertIsNone(
            reg._local_affine(
                np.array([
                    42.,
                    42.,
                ]),

                p,
                q,
                q,

                15,
            )
        )


if __name__ == "__main__":
    unittest.main()