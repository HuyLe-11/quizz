import unittest
from dataclasses import replace

import numpy as np

from backend.engine.spatial_association import (
    PixelFrame,
    TextRegion,
)
from backend.engine.text_span_mapping import (
    GlyphSpan,
    RegionGlyphs,
    map_underline_spans,
)
from backend.engine.visual_primitives import (
    HorizontalStroke,
    detect_horizontal_strokes,
)


class TextSpanMappingTests(unittest.TestCase):

    def setUp(self):
        self.frame = PixelFrame(
            "original", 320, 160
        )

        self.region = TextRegion(
            "word", (20, 20, 100, 40)
        )

        self.mapping = RegionGlyphs(
            "word",
            "ilW",
            (
                GlyphSpan(0, 1, (20, 20, 28, 40)),
                GlyphSpan(1, 2, (28, 20, 36, 40)),
                GlyphSpan(2, 3, (36, 20, 100, 40)),
            ),
        )

    def run_map(
        self,
        x0=36,
        x1=100,
        *,
        mappings=None,
        regions=None,
        **kwargs,
    ):
        frames = dict(
            stroke_frame=self.frame,
            text_frame=self.frame,
            glyph_frame=self.frame,
        )

        frames.update(kwargs)

        result, = map_underline_spans(
            [
                HorizontalStroke(
                    x0, 42, x1, 44,
                    (x1 - x0) * 2,
                )
            ],
            [self.region] if regions is None else regions,
            [self.mapping] if mappings is None else mappings,
            **frames,
        )

        return result

    def test_nonuniform_width(self):
        span = self.run_map().span

        self.assertEqual(
            (span.start, span.end, span.text),
            (2, 3, "W"),
        )

    def test_middle_character(self):
        span = self.run_map(28, 36).span

        self.assertEqual(
            (span.start, span.end, span.text),
            (1, 2, "l"),
        )

    def test_combining_mark_atomic(self):
        m = RegionGlyphs(
            "word",
            "a\u0301b",
            (
                GlyphSpan(0, 2, (20, 20, 50, 40)),
                GlyphSpan(2, 3, (50, 20, 100, 40)),
            ),
        )

        span = self.run_map(
            20, 50,
            mappings=[m],
        ).span

        self.assertEqual(
            (span.start, span.end, span.text),
            (0, 2, "a\u0301"),
        )

    def test_multisubject_unicode(self):
        for text in (
            "ườ",
            "₂O",
            "x²",
            "1954",
            "n²",
        ):
            with self.subTest(text=text):
                m = RegionGlyphs(
                    "word",
                    text,
                    tuple(
                        GlyphSpan(
                            i,
                            i + 1,
                            (
                                20 + 80*i/len(text),
                                20,
                                20 + 80*(i+1)/len(text),
                                40,
                            ),
                        )
                        for i in range(len(text))
                    ),
                )

                span = self.run_map(
                    20, 100,
                    mappings=[m],
                ).span

                self.assertEqual(
                    span.text, text
                )

    def test_whitespace_inside_span(self):
        m = RegionGlyphs(
            "word",
            "x y",
            (
                GlyphSpan(0, 1, (20, 20, 40, 40)),
                GlyphSpan(1, 2, None),
                GlyphSpan(2, 3, (60, 20, 100, 40)),
            ),
        )

        self.assertEqual(
            self.run_map(
                20, 100,
                mappings=[m],
            ).span.text,
            "x y",
        )

    def test_partial_glyph_is_ambiguous(self):
        self.assertEqual(
            self.run_map(40, 70).reason,
            "ambiguous_glyph_overlap",
        )

    def test_partial_ligature_is_ambiguous(self):
        m = RegionGlyphs(
            "word",
            "fi",
            (
                GlyphSpan(
                    0, 2,
                    (20, 20, 100, 40),
                ),
            ),
        )

        self.assertEqual(
            self.run_map(
                20, 60,
                mappings=[m],
            ).reason,
            "ambiguous_glyph_overlap",
        )

        self.assertEqual(
            self.run_map(
                20, 100,
                mappings=[m],
            ).span.text,
            "fi",
        )

    def test_no_geometry(self):
        self.assertEqual(
            self.run_map(mappings=[]).reason,
            "glyph_geometry_unavailable",
        )

    def test_unmapped_competitor_is_not_hidden(self):
        other = TextRegion(
            "other",
            (20, 20, 100, 40),
        )

        self.assertEqual(
            self.run_map(
                regions=[self.region, other]
            ).reason,
            "not_unique_underline",
        )

    def test_table_conflict(self):
        self.assertEqual(
            self.run_map(
                vertical_rules=[
                    (35, 20, 37, 80),
                    (99, 20, 101, 80),
                ],
                vertical_frame=self.frame,
            ).reason,
            "not_unique_underline",
        )

    def test_overhang(self):
        m = RegionGlyphs(
            "word",
            "a",
            (
                GlyphSpan(
                    0, 1,
                    (40, 20, 80, 40),
                ),
            ),
        )

        self.assertEqual(
            self.run_map(
                30, 80,
                mappings=[m],
            ).reason,
            "excessive_stroke_overhang",
        )

    def test_wrong_frame(self):
        with self.assertRaisesRegex(
            ValueError,
            "Glyph frame",
        ):
            self.run_map(
                glyph_frame=PixelFrame(
                    "unwarped", 320, 160
                )
            )

    def test_invalid_mappings(self):
        for m in (
            replace(
                self.mapping,
                region_id="unknown",
            ),
            replace(
                self.mapping,
                glyphs=self.mapping.glyphs[:-1],
            ),
            replace(
                self.mapping,
                glyphs=(
                    GlyphSpan(0, 1, None),
                    *self.mapping.glyphs[1:],
                ),
            ),
            replace(
                self.mapping,
                glyphs=(
                    GlyphSpan(
                        0, 1,
                        (0, 20, 28, 40),
                    ),
                    *self.mapping.glyphs[1:],
                ),
            ),
            replace(
                self.mapping,
                glyphs=(
                    GlyphSpan(
                        0, 1,
                        (60, 20, 80, 40),
                    ),
                    *self.mapping.glyphs[1:],
                ),
            ),
        ):
            with self.subTest(mapping=m):
                with self.assertRaises(ValueError):
                    self.run_map(mappings=[m])

        with self.assertRaises(ValueError):
            self.run_map(
                mappings=[
                    self.mapping,
                    self.mapping,
                ]
            )

    def test_split_mark_rejected(self):
        m = RegionGlyphs(
            "word",
            "a\u0301",
            (
                GlyphSpan(0, 1, (20, 20, 50, 40)),
                GlyphSpan(1, 2, (50, 20, 100, 40)),
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "Unsafe glyph boundary",
        ):
            self.run_map(mappings=[m])

    def test_unsupported_direction(self):
        m = RegionGlyphs(
            "word",
            "אב",
            (
                GlyphSpan(
                    0, 2,
                    (20, 20, 100, 40),
                ),
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "right-to-left",
        ):
            self.run_map(mappings=[m])

    def test_detector_integration(self):
        gray = np.full(
            (160, 320),
            255,
            dtype=np.uint8,
        )

        gray[42:44, 36:100] = 0

        strokes = detect_horizontal_strokes(
            gray,
            reference_height=24,
        )

        result, = map_underline_spans(
            strokes,
            [self.region],
            [self.mapping],
            stroke_frame=self.frame,
            text_frame=self.frame,
            glyph_frame=self.frame,
        )

        self.assertEqual(
            result.span.text,
            "W",
        )

    def test_empty(self):
        self.assertEqual(
            map_underline_spans(
                [],
                [],
                [],
                stroke_frame=self.frame,
                text_frame=self.frame,
                glyph_frame=self.frame,
            ),
            (),
        )


if __name__ == "__main__":
    unittest.main()
