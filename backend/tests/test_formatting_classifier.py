import unittest

import numpy as np

from backend.engine.formatting_classifier import classify_horizontal_strokes
from backend.engine.spatial_association import PixelFrame, TextRegion
from backend.engine.visual_primitives import HorizontalStroke, detect_horizontal_strokes


class FormattingClassifierTests(unittest.TestCase):
    def setUp(self):
        self.frame = PixelFrame("original:page-1", 320, 160)

    def classify(self, strokes, regions=(), **kwargs):
        return classify_horizontal_strokes(strokes, regions, stroke_frame=self.frame,
                                           text_frame=self.frame, **kwargs)

    def one(self, box, regions=(), **kwargs):
        x0, y0, x1, y1 = box
        stroke = HorizontalStroke(*box, (x1-x0)*(y1-y0))
        result, = self.classify([stroke], regions, **kwargs)
        return result

    def test_underline_partial_word(self):
        r = self.one((60, 42, 80, 44), [TextRegion("word", (20, 20, 100, 40))])
        self.assertEqual(r.kind, "underline")
        self.assertEqual(r.candidates[0].region_ids, ("word",))

    def test_underline_inside_bottom_of_box(self):
        r = self.one((60, 38, 80, 40), [TextRegion("word", (20, 20, 100, 40))])
        self.assertEqual(r.kind, "underline")

    def test_blank_between_aligned_regions(self):
        r = self.one((90, 38, 145, 40), [TextRegion("left", (20, 20, 80, 40)),
                                      TextRegion("right", (155, 20, 230, 40))])
        self.assertEqual(r.kind, "blank")
        self.assertEqual(r.candidates[0].region_ids, ("left", "right"))

    def test_one_sided_blank_remains_unknown(self):
        r = self.one((90, 38, 145, 40), [TextRegion("left", (20, 20, 80, 40))])
        self.assertEqual(r.kind, "unknown")

    def test_occupied_blank_not_proposed(self):
        regions = [TextRegion("left", (20, 20, 80, 40)),
                   TextRegion("right", (155, 20, 230, 40)),
                   TextRegion("inside", (100, 20, 130, 40))]
        r = self.one((90, 38, 145, 40), regions)
        self.assertNotIn("blank", [c.kind for c in r.candidates])

    def test_fraction(self):
        r = self.one((50, 60, 100, 62), [TextRegion("top", (55, 34, 95, 54)),
                                      TextRegion("bottom", (60, 68, 90, 88))])
        self.assertEqual(r.kind, "fraction_bar")
        self.assertEqual(r.candidates[0].region_ids, ("top", "bottom"))

    def test_fraction_underline_ambiguity(self):
        r = self.one((50, 60, 100, 62), [TextRegion("top", (50, 40, 100, 60)),
                                      TextRegion("bottom", (55, 68, 95, 88))])
        self.assertEqual(r.kind, "unknown")
        self.assertEqual({c.kind for c in r.candidates}, {"underline", "fraction_bar"})

    def test_strikethrough(self):
        r = self.one((30, 29, 80, 31), [TextRegion("word", (25, 20, 90, 40))])
        self.assertEqual(r.kind, "strikethrough")

    def test_table_with_two_junctions(self):
        r = self.one((30, 50, 130, 52), vertical_rules=[(29, 20, 31, 80),
                     (129, 20, 131, 80)], vertical_frame=self.frame)
        self.assertEqual(r.kind, "table_border")
        self.assertEqual(r.candidates[0].vertical_rule_indices, (0, 1))

    def test_length_or_single_junction_not_enough_for_table(self):
        for rules in ([], [(29, 20, 31, 80)]):
            r = self.one((30, 50, 130, 52), vertical_rules=rules, vertical_frame=self.frame)
            self.assertEqual(r.kind, "unknown")

    def test_table_and_underline_conflict(self):
        r = self.one((30, 42, 130, 44), [TextRegion("text", (30, 20, 130, 40))],
                     vertical_rules=[(29, 20, 31, 80), (129, 20, 131, 80)],
                     vertical_frame=self.frame)
        self.assertEqual(r.kind, "unknown")
        self.assertEqual({c.kind for c in r.candidates}, {"underline", "table_border"})

    def test_two_possible_text_targets(self):
        r = self.one((60, 42, 80, 44), [TextRegion("a", (20, 20, 100, 40)),
                                      TextRegion("b", (20, 21, 100, 41))])
        self.assertEqual(r.kind, "unknown")
        self.assertEqual(len(r.candidates), 2)

    def test_wrong_frames_and_invalid_vertical_geometry(self):
        with self.assertRaises(ValueError):
            classify_horizontal_strokes([], [], stroke_frame=self.frame,
                                        text_frame=PixelFrame("other", 320, 160))
        with self.assertRaises(ValueError):
            self.one((30, 50, 130, 52), vertical_rules=[(29, 20, 31, 80)])
        with self.assertRaises(ValueError):
            self.one((30, 50, 130, 52), vertical_rules=[(29, 20, 60, 22)],
                     vertical_frame=self.frame)

    def test_scale_invariant(self):
        for s in (1, 2, 3):
            frame = PixelFrame("scaled", 320*s, 160*s)
            r, = classify_horizontal_strokes(
                [HorizontalStroke(60*s, 42*s, 80*s, 44*s, 40*s*s)],
                [TextRegion("word", (20*s, 20*s, 100*s, 40*s))],
                stroke_frame=frame, text_frame=frame)
            self.assertEqual(r.kind, "underline")

    def test_neighbor_budget(self):
        r = self.one((60, 42, 80, 44), [TextRegion(str(i), (20, 20, 100, 40))
                                      for i in range(49)])
        self.assertEqual((r.kind, r.reason), ("unknown", "too_many_neighbors"))

    def test_empty_and_deterministic(self):
        self.assertEqual(self.classify([]), ())
        strokes = [HorizontalStroke(60, 42, 80, 44, 40)]
        regions = [TextRegion("word", (20, 20, 100, 40))]
        self.assertEqual(self.classify(strokes, regions), self.classify(strokes, regions))

    def test_integration_with_detector(self):
        gray = np.full((160, 320), 255, dtype=np.uint8)
        gray[60:63, 100:116] = 0
        strokes = detect_horizontal_strokes(gray, reference_height=24)
        r, = self.classify(strokes, [TextRegion("word", (80, 36, 140, 58))])
        self.assertEqual(r.kind, "underline")
        self.assertEqual(strokes[r.stroke_index].bbox, (100, 60, 116, 63))


if __name__ == "__main__":
    unittest.main()
