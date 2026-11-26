"""Map underline hypotheses to exact source offsets using supplied glyph geometry."""
from __future__ import annotations

import unicodedata as ud
from collections.abc import Sequence
from dataclasses import dataclass

from .formatting_classifier import classify_horizontal_strokes
from .spatial_association import BBox, PixelFrame, TextRegion, _validate_box
from .visual_primitives import HorizontalStroke


@dataclass(frozen=True, slots=True)
class GlyphSpan:
    start: int
    end: int
    bbox: BBox | None


@dataclass(frozen=True, slots=True)
class RegionGlyphs:
    region_id: str
    text: str
    glyphs: tuple[GlyphSpan, ...]


@dataclass(frozen=True, slots=True)
class MappedUnderline:
    region_id: str
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class SpanMappingResult:
    stroke_index: int
    span: MappedUnderline | None
    reason: str


def _validate_glyphs(mapping, region, frame):
    text = mapping.text

    if (
        not isinstance(text, str)
        or not text.strip()
        or not isinstance(mapping.glyphs, tuple)
        or not mapping.glyphs
    ):
        raise ValueError(
            "Expected nonempty text and immutable glyph spans."
        )

    if any(
        ud.category(c).startswith("C")
        or ud.bidirectional(c) in ("R", "AL", "AN")
        for c in text
    ):
        raise ValueError(
            "Unsupported control or right-to-left text."
        )

    if ud.category(text[0]).startswith("M"):
        raise ValueError(
            "Text starts with an unattached mark."
        )

    rx0, ry0, rx1, ry1 = region.bbox

    cursor = 0
    previous_center = None

    for g in mapping.glyphs:
        if (
            not isinstance(g, GlyphSpan)
            or type(g.start) is not int
            or type(g.end) is not int
            or g.start != cursor
            or not g.start < g.end <= len(text)
        ):
            raise ValueError(
                "Glyph spans must partition the exact source text."
            )

        if g.start and (
            ud.category(text[g.start]).startswith("M")
            or ud.combining(text[g.start - 1]) == 9
            or 0x1F3FB <= ord(text[g.start]) <= 0x1F3FF
        ):
            raise ValueError(
                "Unsafe glyph boundary."
            )

        part = text[g.start:g.end]

        if part.isspace():
            if g.bbox is not None:
                raise ValueError(
                    "Whitespace must not be ink geometry."
                )

        else:
            if (
                g.bbox is None
                or any(c.isspace() for c in part)
            ):
                raise ValueError(
                    "Missing or mixed glyph geometry."
                )

            _validate_box(g.bbox, frame)

            x0, y0, x1, y1 = g.bbox

            if not (
                rx0 <= x0 < x1 <= rx1
                and ry0 <= y0 < y1 <= ry1
            ):
                raise ValueError(
                    "Glyph outside text region."
                )

            center = (x0 + x1) / 2

            if (
                previous_center is not None
                and center <= previous_center
            ):
                raise ValueError(
                    "Expected left-to-right glyph order."
                )

            previous_center = center

        cursor = g.end

    if cursor != len(text):
        raise ValueError(
            "Incomplete glyph mapping."
        )


def _map(stroke, mapping, region):
    selected = []

    for i, g in enumerate(mapping.glyphs):
        if g.bbox is None:
            continue

        x0, _, x1, _ = g.bbox

        overlap = max(
            0,
            min(stroke.x1, x1)
            - max(stroke.x0, x0),
        )

        coverage = overlap / (x1 - x0)

        if coverage >= .85:
            selected.append(i)

        elif coverage > .10:
            return None, "ambiguous_glyph_overlap"

    if not selected:
        return None, "no_glyph_covered"

    first, last = selected[0], selected[-1]
    selected_set = set(selected)

    if any(
        g.bbox is not None and i not in selected_set
        for i, g in enumerate(mapping.glyphs)
        if first <= i <= last
    ):
        return None, "noncontiguous_glyphs"

    glyphs = [
        mapping.glyphs[i]
        for i in selected
    ]

    left = min(
        g.bbox[0]
        for g in glyphs
    )

    right = max(
        g.bbox[2]
        for g in glyphs
    )

    tolerance = .25 * (
        region.bbox[3] - region.bbox[1]
    )

    if (
        left - stroke.x0 > tolerance
        or stroke.x1 - right > tolerance
    ):
        return None, "excessive_stroke_overhang"

    start, end = glyphs[0].start, glyphs[-1].end

    return (
        MappedUnderline(
            mapping.region_id,
            start,
            end,
            mapping.text[start:end],
        ),
        "geometric_span_candidate",
    )


def map_underline_spans(
    strokes: Sequence[HorizontalStroke],
    regions: Sequence[TextRegion],
    mappings: Sequence[RegionGlyphs],
    *,
    stroke_frame: PixelFrame,
    text_frame: PixelFrame,
    glyph_frame: PixelFrame,
    vertical_rules: Sequence[BBox] = (),
    vertical_frame: PixelFrame | None = None,
) -> tuple[SpanMappingResult, ...]:
    """Return geometric span candidates, never verified edits.

    Offsets are Python code-point indices into exact, unnormalized text.
    Callers must provide actual source glyph/cluster boxes and atomic spans,
    not guessed subdivisions of word boxes. Only horizontal LTR is supported.
    Include unmapped neighboring regions in classification to retain conflicts.
    """

    if glyph_frame != text_frame:
        raise ValueError(
            "Glyph frame does not match text frame."
        )

    classes = classify_horizontal_strokes(
        strokes,
        regions,
        stroke_frame=stroke_frame,
        text_frame=text_frame,
        vertical_rules=vertical_rules,
        vertical_frame=vertical_frame,
    )

    by_region = {
        r.region_id: r
        for r in regions
    }

    by_mapping = {}

    for m in mappings:
        if (
            not isinstance(m, RegionGlyphs)
            or not isinstance(m.region_id, str)
            or m.region_id not in by_region
            or m.region_id in by_mapping
        ):
            raise ValueError(
                "Invalid or duplicate glyph mapping region."
            )

        _validate_glyphs(
            m,
            by_region[m.region_id],
            glyph_frame,
        )

        by_mapping[m.region_id] = m

    results = []
    work = 0

    for c in classes:
        if c.kind != "underline":
            results.append(
                SpanMappingResult(
                    c.stroke_index,
                    None,
                    "not_unique_underline",
                )
            )

            continue

        region_id = c.candidates[0].region_ids[0]
        m = by_mapping.get(region_id)

        if m is None:
            results.append(
                SpanMappingResult(
                    c.stroke_index,
                    None,
                    "glyph_geometry_unavailable",
                )
            )

            continue

        work += len(m.glyphs)

        if work > 5_000_000:
            raise ValueError(
                "Glyph mapping budget exceeded."
            )

        span, reason = _map(
            strokes[c.stroke_index],
            m,
            by_region[region_id],
        )

        results.append(
            SpanMappingResult(
                c.stroke_index,
                span,
                reason,
            )
        )

    return tuple(results)
