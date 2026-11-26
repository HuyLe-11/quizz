import unittest

import numpy as np

from backend.engine.spatial_association import (
    PixelFrame,
    TextRegion,
    associate_horizontal_strokes,
)
from backend.engine.visual_primitives import (
    HorizontalStroke,
    detect_horizontal_strokes,
)


class SpatialAssociationTests(unittest.TestCase):
    def setUp(self):
        self.frame = PixelFrame("page-1:original:crop-0", 320, 160)

    def associate(self, strokes, regions, *, text_frame=None):
        return associate_horizontal_strokes(
            strokes,
            regions,
            stroke_frame=self.frame,
            text_frame=self.frame if text_frame is None else text_frame,
        )

    def test_stroke_below_text(self):
        stroke = HorizontalStroke(50, 43, 70, 45, 40)
        region = TextRegion("word", (30, 20, 110, 40))
        result, = self.associate([stroke], [region])
        self.assertEqual(result.vertical_relation, "below")
        self.assertEqual(result.horizontal_relation, "overlap")
        self.assertAlmostEqual(result.stroke_coverage, 1.0)
        self.assertAlmostEqual(result.region_coverage, .25)
        self.assertAlmostEqual(result.vertical_gap_ratio, .15)
        self.assertAlmostEqual(result.relative_center_y, 1.2)

    def test_stroke_inside_text_box(self):
        result, = self.associate(
            [HorizontalStroke(40, 30, 80, 32, 80)],
            [TextRegion("text", (30, 20, 100, 40))],
        )
        self.assertEqual(result.vertical_relation, "overlap")
        self.assertEqual(result.vertical_gap_ratio, 0)

    def test_fraction_retains_both_regions(self):
        result = self.associate(
            [HorizontalStroke(50, 60, 100, 62, 100)],
            [TextRegion("numerator", (55, 35, 95, 55)),
             TextRegion("denominator", (60, 67, 90, 87))],
        )
        self.assertEqual(
            {a.region_id: a.vertical_relation for a in result},
            {"numerator": "below", "denominator": "above"},
        )

    def test_blank_retains_lateral_neighbors(self):
        result = self.associate(
            [HorizontalStroke(90, 38, 145, 40, 110)],
            [TextRegion("before", (20, 20, 80, 40)),
             TextRegion("after", (155, 20, 230, 40))],
        )
        self.assertEqual(
            {a.region_id: a.horizontal_relation for a in result},
            {"before": "right", "after": "left"},
        )
        self.assertTrue(all(a.horizontal_gap_ratio == .5 for a in result))
        self.assertTrue(all(a.stroke_coverage == 0 for a in result))

    def test_distant_regions_rejected(self):
        self.assertEqual(self.associate(
            [HorizontalStroke(100, 70, 130, 72, 60)],
            [TextRegion("far-y", (90, 10, 140, 30)),
             TextRegion("far-x", (10, 60, 35, 80))],
        ), [])

    def test_tiny_horizontal_overlap_rejected(self):
        self.assertEqual(self.associate(
            [HorizontalStroke(100, 41, 160, 43, 120)],
            [TextRegion("edge", (40, 20, 102, 40))],
        ), [])

    def test_boundary_touching_has_zero_gap(self):
        result, = self.associate(
            [HorizontalStroke(30, 40, 70, 42, 80)],
            [TextRegion("text", (30, 20, 70, 40))],
        )
        self.assertEqual(result.vertical_relation, "below")
        self.assertEqual(result.vertical_gap_ratio, 0)

    def test_frame_mismatch_rejected(self):
        for other in (
            PixelFrame("page-2:original:crop-0", 320, 160),
            PixelFrame(self.frame.key, 640, 320),
        ):
            with self.subTest(frame=other):
                with self.assertRaisesRegex(ValueError, "frames do not match"):
                    self.associate([], [], text_frame=other)

    def test_invalid_boxes_rejected(self):
        for box in ((0, 0, 0, 10), (-1, 0, 20, 20),
                    (0, 0, 321, 20), (0, 0, float("nan"), 20)):
            with self.subTest(box=box):
                with self.assertRaises(ValueError):
                    self.associate([], [TextRegion("bad", box)])

    def test_invalid_stroke_area_rejected(self):
        with self.assertRaises(ValueError):
            self.associate([HorizontalStroke(10, 10, 20, 12, 21)], [])

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            self.associate([], [TextRegion("same", (0, 0, 20, 20))] * 2)

    def test_scaling_preserves_metrics(self):
        for scale in (1, 2, 3):
            with self.subTest(scale=scale):
                frame = PixelFrame("scaled", 320 * scale, 160 * scale)
                result, = associate_horizontal_strokes(
                    [HorizontalStroke(50 * scale, 43 * scale,
                                      70 * scale, 45 * scale, 40 * scale**2)],
                    [TextRegion("word", tuple(v * scale for v in (30, 20, 110, 40)))],
                    stroke_frame=frame,
                    text_frame=frame,
                )
                self.assertAlmostEqual(result.vertical_gap_ratio, .15)
                self.assertAlmostEqual(result.region_coverage, .25)

    def test_visual_primitive_integration(self):
        gray = np.full((160, 320), 255, dtype=np.uint8)
        gray[60:63, 100:116] = 0
        strokes = detect_horizontal_strokes(gray, reference_height=24)
        result, = self.associate(
            strokes,
            [TextRegion("text", (80, 35, 140, 55))],
        )
        self.assertEqual(
            strokes[result.stroke_index].bbox,
            (100, 60, 116, 63),
        )
        self.assertEqual(result.vertical_relation, "below")

    def test_deterministic_order_and_empty(self):
        stroke = HorizontalStroke(40, 43, 60, 45, 40)
        regions = [TextRegion("z", (30, 20, 70, 40)),
                   TextRegion("a", (30, 20, 70, 40))]
        self.assertEqual(
            [a.region_id for a in self.associate([stroke], regions)],
            ["a", "z"],
        )
        self.assertEqual(self.associate([], regions), [])
        self.assertEqual(self.associate([stroke], []), [])

    def test_budget_exceeded(self):
        stroke = HorizontalStroke(40, 43, 60, 45, 40)
        region = TextRegion("word", (30, 20, 70, 40))
        with self.assertRaisesRegex(ValueError, "budget exceeded"):
            self.associate([stroke] * 2500, [region] * 2001)


if __name__ == "__main__":
    unittest.main()