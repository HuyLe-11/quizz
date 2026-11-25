import unittest
from unittest.mock import patch

import cv2 as cv
import numpy as np
import pymupdf

from backend.engine import (
    registered_mark_detection as det,
)

from backend.engine.ocr_image_cache import (
    CachedImages,
)

from backend.engine.ocr_image_registration import (
    MappedPoint,
    RegistrationError,
)


class RegisteredMarkDetectionTests(unittest.TestCase):

    def setUp(self):

        self.doc = pymupdf.open()

        self.addCleanup(
            self.doc.close
        )

        self.page = self.doc.new_page(
            width=300,

            height=300,
        )

        self.root = {
            "rec_texts": [
                "Question 1. Text",
                "A. value",
                "B. value",
                "Question 2. Text",
                "A. value",
                "B. value",
            ],

            "rec_boxes": [
                [20, 20, 200, 50],
                [40, 80, 150, 110],
                [300, 80, 410, 110],
                [20, 240, 200, 270],
                [40, 300, 150, 330],
                [300, 300, 410, 330],
            ],

            "rec_scores": [.99] * 6,
        }

        self.labels = {
            1: "AB",

            2: "AB",
        }

        self.images = CachedImages(
            np.full(
                (600, 600, 3),

                255,

                np.uint8,
            ),

            np.full(
                (600, 1200, 3),

                255,

                np.uint8,
            ),
        )

    def mark(
        self,

        x,

        y,

        color=(1, 0, 0),
    ):

        # x and y are OCR/input pixels;
        # page is rendered at 2x.

        self.page.draw_circle(
            (
                x / 2,

                y / 2,
            ),

            9,

            color=color,

            width=2,
        )

    def detect(self):

        return det.detect_registered_marks(
            self.page,

            self.root,

            self.labels,

            self.images,

            width=600,

            height=600,
        )

    def identity(self, registration):

        registration.return_value.map_point.side_effect = (
            lambda x, y: MappedPoint(
                x,
                y,
                "local_affine",
            )
        )

    def test_single_registration_multiple_questions(self):

        self.mark(50, 95)

        self.mark(310, 315)

        with patch.object(
            det,
            "register_page",
        ) as register:

            self.identity(register)

            result = self.detect()

        register.assert_called_once()

        self.assertEqual(
            result.status,

            "processed",
        )

        self.assertEqual(
            result.red_components,

            2,
        )

        self.assertEqual(
            result.mapped_components,

            2,
        )

        self.assertEqual(
            result.unassociated_components,

            0,
        )

        self.assertEqual(
            [
                (m.question_number, m.label)

                for m in result.suggestions
            ],

            [
                (1, "A"),

                (2, "B"),
            ],
        )

        self.assertTrue(
            all(
                m.status == "uncertain"

                for m in result.suggestions
            )
        )

        x0, y0, x1, y1 = (
            result.suggestions[0].mark_coordinates
        )

        self.assertAlmostEqual(
            (x0 + x1) / 2,

            25,

            delta=1,
        )

        self.assertAlmostEqual(
            (y0 + y1) / 2,

            47.5,

            delta=1,
        )

    def test_black_mark_ignored(self):

        self.mark(
            50,

            95,

            color=(0, 0, 0),
        )

        with patch.object(
            det,

            "register_page",
        ) as register:

            self.identity(register)

            result = self.detect()

        self.assertEqual(
            result.reason,

            "no_eligible_components",
        )

        self.assertEqual(
            result.suggestions,

            (),
        )

        register.return_value.map_point.assert_not_called()

    def test_provider_only_red_mark_not_used(self):

        cv.circle(
            self.images.input_image,

            (50, 95),

            18,

            (0, 0, 255),

            4,
        )

        with patch.object(
            det,

            "register_page",
        ) as register:

            self.identity(register)

            result = self.detect()

        self.assertEqual(
            result.red_components,

            0,
        )

        self.assertEqual(
            result.suggestions,

            (),
        )

    def test_multiple_choices_for_question_rejected(self):

        self.mark(50, 95)

        self.mark(310, 95)

        self.mark(310, 315)

        with patch.object(
            det,

            "register_page",
        ) as register:

            self.identity(register)

            result = self.detect()

        self.assertEqual(
            result.conflicted_questions,

            (1,),
        )

        self.assertEqual(
            [
                (m.question_number, m.label)

                for m in result.suggestions
            ],

            [(2, "B")],
        )

    def test_mapping_failure_does_not_guess(self):

        self.mark(50, 95)

        with patch.object(
            det,

            "register_page",
        ) as register:

            register.return_value.map_point.return_value = None

            result = self.detect()

        self.assertEqual(
            result.red_components,

            1,
        )

        self.assertEqual(
            result.mapped_components,

            0,
        )

        self.assertEqual(
            result.suggestions,

            (),
        )

    def test_unassociated_mark(self):

        self.mark(200, 160)

        with patch.object(
            det,

            "register_page",
        ) as register:

            self.identity(register)

            result = self.detect()

        self.assertEqual(
            result.mapped_components,

            1,
        )

        self.assertEqual(
            result.unassociated_components,

            1,
        )

        self.assertEqual(
            result.suggestions,

            (),
        )

    def test_registration_failure(self):

        with patch.object(
            det,

            "register_page",

            side_effect=RegistrationError,
        ):

            result = self.detect()

        self.assertEqual(
            result.status,

            "unavailable",
        )

        self.assertEqual(
            result.suggestions,

            (),
        )

    def test_invalid_index_rejected_before_registration(self):

        self.labels = {
            1: "AB",
        }

        with patch.object(
            det,

            "register_page",
        ) as register:

            result = self.detect()

        register.assert_not_called()

        self.assertEqual(
            result.reason,

            "invalid_option_index",
        )

    def test_candidate_limit(self):

        self.mark(50, 95)

        self.mark(310, 315)

        with (
            patch.object(
                det,

                "MAX_CANDIDATES",

                1,
            ),

            patch.object(
                det,

                "register_page",
            ) as register,
        ):

            self.identity(register)

            result = self.detect()

        self.assertEqual(
            result.reason,

            "too_many_components",
        )

        self.assertEqual(
            result.red_components,

            2,
        )

        self.assertEqual(
            result.suggestions,

            (),
        )

        register.return_value.map_point.assert_not_called()

    def test_unsupported_mapping_method(self):

        self.mark(50, 95)

        with patch.object(
            det,

            "register_page",
        ) as register:

            register.return_value.map_point.return_value = (
                MappedPoint(
                    50,

                    95,

                    "homography",
                )
            )

            result = self.detect()

        self.assertEqual(
            result.unassociated_components,

            1,
        )

        self.assertEqual(
            result.suggestions,

            (),
        )


if __name__ == "__main__":

    unittest.main()