import unittest
from dataclasses import replace

import numpy as np

from backend.engine.advanced_scan_alignment import (
    align_scan_token,
)
from backend.engine.spatial_association import (
    PixelFrame,
    TextRegion,
)
from backend.engine.visual_primitives import (
    HorizontalStroke,
)


class AdvancedScanAlignmentTests(unittest.TestCase):

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

    def align(
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

        return align_scan_token(**args)

    def test_existing_isolated_path(self):
        r = self.align()

        self.assertEqual(
            r.reason,
            "isolated_count_hypothesis",
        )

        self.assertEqual(
            len(r.hypotheses),
            3,
        )

        self.assertEqual(
            r.removed_pixels,
            0,
        )

    def test_touching_glyphs_unique_valley(self):
        self.gray[30:31, 26:32] = 0

        r = self.align()

        self.assertEqual(
            r.initial_reason,
            "ink_group_count_mismatch",
        )

        self.assertEqual(
            r.reason,
            "valley_aligned_hypothesis",
        )

        self.assertEqual(
            r.cuts,
            (29, 41),
        )

        self.assertEqual(
            len(r.hypotheses),
            3,
        )

    def test_connected_rule(self):
        self.gray[40:45, 50:52] = 0

        r = self.align()

        self.assertEqual(
            r.initial_reason,
            "rule_not_isolated",
        )

        self.assertEqual(
            r.reason,
            "valley_aligned_hypothesis",
        )

        self.assertEqual(
            r.removed_pixels,
            40,
        )

    def test_both_connected(self):
        self.gray[30:31, 26:32] = 0
        self.gray[40:45, 50:52] = 0

        self.assertEqual(
            self.align().reason,
            "valley_aligned_hypothesis",
        )

    def test_equal_cost_alternatives(self):
        self.gray[30:31, 26:32] = 0
        self.gray[30:31, 38:45] = 0

        r = self.align("ab")

        self.assertEqual(
            r.reason,
            "ambiguous_segmentation",
        )

        self.assertEqual(
            r.best_cost,
            r.alternative_cost,
        )

        self.assertEqual(
            r.hypotheses,
            (),
        )

    def test_no_invented_cut_in_solid_ink(self):
        self.gray[20:40, 26:45] = 0

        r = self.align()

        self.assertEqual(
            r.reason,
            "no_feasible_segmentation",
        )

        self.assertEqual(
            r.hypotheses,
            (),
        )

    def test_clear_gaps_not_skipped(self):
        r = self.align("ab")

        self.assertEqual(
            r.reason,
            "no_feasible_segmentation",
        )

    def test_insufficient_rule_support(self):
        self.gray[45:47, 45:65] = 255
        self.gray[45:47, 45:49] = 0

        self.assertEqual(
            self.align().reason,
            "insufficient_rule_support",
        )

    def test_rule_through_text_rejected(self):
        self.gray[45:47, 45:65] = 255
        self.gray[29:31, 20:65] = 0

        r = self.align(
            stroke=HorizontalStroke(
                20, 29, 65, 31, 90
            )
        )

        self.assertEqual(
            r.reason,
            "rule_not_near_baseline",
        )

    def test_lower_ink_under_rule_rejected(self):
        self.gray[50:54, 50:54] = 0

        self.assertEqual(
            self.align().reason,
            "ink_below_rule",
        )

    def test_unicode_offsets_not_normalized(self):
        self.gray[30:31, 26:32] = 0

        r = self.align(
            "a\u0301b²"
        )

        self.assertEqual(
            [
                (h.start, h.end)
                for h in r.hypotheses
            ],
            [
                (0, 2),
                (2, 3),
                (3, 4),
            ],
        )

    def test_invalid_frame_and_unsupported_text(self):
        with self.assertRaises(ValueError):
            self.align(
                stroke_frame=replace(
                    self.frame,
                    key="different",
                )
            )

        self.assertEqual(
            self.align("a b").reason,
            "unsupported_token",
        )

    def test_source_identity_retained(self):
        r = self.align()

        self.assertEqual(
            (
                r.frame,
                r.region_id,
                r.text,
            ),
            (
                self.frame,
                "token",
                "ilW",
            ),
        )

    def test_source_image_unchanged(self):
        self.gray[30:31, 26:32] = 0

        before = self.gray.copy()

        self.assertEqual(
            self.align(),
            self.align(),
        )

        np.testing.assert_array_equal(
            self.gray,
            before,
        )


if __name__ == "__main__":
    unittest.main()
