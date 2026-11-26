import unittest
from dataclasses import replace

import numpy as np

from backend.engine.scan_ink_geometry import inspect_scan_token
from backend.engine.spatial_association import (
    PixelFrame,
    TextRegion,
)
from backend.engine.visual_primitives import HorizontalStroke


class ScanInkGeometryTests(unittest.TestCase):

    def setUp(self):
        self.frame = PixelFrame(
            "original",
            100,
            80,
        )

        self.region = TextRegion(
            "token",
            (10, 10, 90, 70),
        )

        self.gray = np.full(
            (80, 100),
            255,
            dtype=np.uint8,
        )

        self.gray[20:40, 20:26] = 0
        self.gray[20:40, 32:38] = 0
        self.gray[20:40, 45:65] = 0

        self.gray[45:47, 45:65] = 0

        self.stroke = HorizontalStroke(
            45, 45, 65, 47, 40
        )

    def inspect(
        self,
        text="ilW",
        **changes,
    ):
        args = dict(
            gray=self.gray,
            region=self.region,
            text=text,
            stroke=self.stroke,
            image_frame=self.frame,
            stroke_frame=self.frame,
        )

        args.update(changes)

        return inspect_scan_token(**args)

    def test_nonuniform_ink_boxes(self):
        r = self.inspect()

        self.assertEqual(
            r.reason,
            "count_aligned_hypothesis",
        )

        self.assertEqual(
            r.ink_group_count,
            3,
        )

        self.assertEqual(
            [
                s.bbox
                for s in r.hypotheses
            ],
            [
                (20, 20, 26, 40),
                (32, 20, 38, 40),
                (45, 20, 65, 40),
            ],
        )

        self.assertEqual(
            [
                (s.start, s.end)
                for s in r.hypotheses
            ],
            [
                (0, 1),
                (1, 2),
                (2, 3),
            ],
        )

    def test_unicode_combining_mark(self):
        self.gray[15:17, 21:24] = 0

        r = self.inspect("a\u0301b²")

        self.assertEqual(
            [
                (s.start, s.end)
                for s in r.hypotheses
            ],
            [
                (0, 2),
                (2, 3),
                (3, 4),
            ],
        )

        self.assertEqual(
            r.hypotheses[0].bbox,
            (20, 15, 26, 40),
        )

    def test_multisubject_symbols(self):
        for text in (
            "H₂O",
            "x²+",
            "195",
            "ườn",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    self.inspect(text).reason,
                    "count_aligned_hypothesis",
                )

    def test_touching_glyphs(self):
        self.gray[25:29, 26:32] = 0

        r = self.inspect()

        self.assertEqual(
            (
                r.reason,
                r.ink_group_count,
            ),
            (
                "ink_group_count_mismatch",
                2,
            ),
        )

        self.assertEqual(
            r.hypotheses,
            (),
        )

    def test_rule_touches_glyph(self):
        self.gray[40:45, 50:52] = 0

        self.assertEqual(
            self.inspect().reason,
            "rule_not_isolated",
        )

    def test_wrong_expected_count(self):
        self.assertEqual(
            self.inspect("ab").reason,
            "ink_group_count_mismatch",
        )

    def test_unrelated_lower_ink(self):
        self.gray[50:53, 25:28] = 0

        self.assertEqual(
            self.inspect().reason,
            "ink_not_above_rule",
        )

    def test_clipped_roi(self):
        self.gray[20:25, 10:15] = 0

        self.assertEqual(
            self.inspect().reason,
            "ink_at_roi_boundary",
        )

    def test_low_contrast_and_empty(self):
        for img in (
            np.full_like(
                self.gray,
                255,
            ),
            np.where(
                self.gray == 0,
                230,
                245,
            ).astype(np.uint8),
        ):
            self.assertEqual(
                self.inspect(gray=img).reason,
                "unreliable_binarization",
            )

    def test_unsupported_text(self):
        for text in (
            "a b",
            "אב",
            "\u0301a",
            "a\u200db",
            "",
        ):
            self.assertEqual(
                self.inspect(text).reason,
                "unsupported_token",
            )

    def test_input_validation(self):
        for changes in (
            dict(
                stroke_frame=PixelFrame(
                    "other",
                    100,
                    80,
                )
            ),
            dict(
                gray=self.gray.astype(float)
            ),
            dict(
                region=TextRegion(
                    "r",
                    (10.5, 10, 90, 70),
                )
            ),
            dict(
                stroke=replace(
                    self.stroke,
                    x0=10,
                )
            ),
        ):
            with self.subTest(
                changes=list(changes)
            ), self.assertRaises(ValueError):
                self.inspect(**changes)

    def test_no_mutation_or_nondeterminism(self):
        before = self.gray.copy()

        self.assertEqual(
            self.inspect(),
            self.inspect(),
        )

        np.testing.assert_array_equal(
            self.gray,
            before,
        )


if __name__ == "__main__":
    unittest.main()
