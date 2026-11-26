"""Associate horizontal ink with text geometry in a shared pixel frame."""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .visual_primitives import HorizontalStroke


BBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class PixelFrame:
    key: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class TextRegion:
    region_id: str
    bbox: BBox


@dataclass(frozen=True, slots=True)
class StrokeAssociation:
    stroke_index: int
    region_id: str
    horizontal_relation: str
    vertical_relation: str
    stroke_coverage: float
    region_coverage: float
    horizontal_gap_ratio: float
    vertical_gap_ratio: float
    relative_center_y: float


def _validate_frame(frame: PixelFrame) -> None:
    if (
        not isinstance(frame, PixelFrame)
        or not isinstance(frame.key, str)
        or not frame.key.strip()
        or type(frame.width) is not int
        or type(frame.height) is not int
        or frame.width <= 0
        or frame.height <= 0
    ):
        raise ValueError("Invalid pixel frame.")


def _validate_box(box: BBox, frame: PixelFrame) -> None:
    if (
        not isinstance(box, tuple)
        or len(box) != 4
        or not all(
            type(v) in (int, float) and math.isfinite(v)
            for v in box
        )
    ):
        raise ValueError("Invalid bounding box.")

    x0, y0, x1, y1 = box
    if not (
        0 <= x0 < x1 <= frame.width
        and 0 <= y0 < y1 <= frame.height
    ):
        raise ValueError("Bounding box outside pixel frame.")


def _associate(
    index: int,
    stroke: HorizontalStroke,
    region: TextRegion,
) -> StrokeAssociation | None:
    sx0, sy0, sx1, sy1 = stroke.bbox
    tx0, ty0, tx1, ty1 = region.bbox
    h = ty1 - ty0
    w = tx1 - tx0

    overlap = max(0, min(sx1, tx1) - max(sx0, tx0))
    stroke_coverage = overlap / stroke.width
    region_coverage = overlap / w

    if sx1 <= tx0:
        horizontal = "left"
        x_gap = tx0 - sx1
    elif sx0 >= tx1:
        horizontal = "right"
        x_gap = sx0 - tx1
    else:
        horizontal = "overlap"
        x_gap = 0

    if sy1 <= ty0:
        vertical = "above"
        y_gap = ty0 - sy1
    elif sy0 >= ty1:
        vertical = "below"
        y_gap = sy0 - ty1
    else:
        vertical = "overlap"
        y_gap = 0

    x_ratio = x_gap / h
    y_ratio = y_gap / h

    if horizontal == "overlap":
        if y_ratio > .80 or max(stroke_coverage, region_coverage) < .25:
            return None
    elif x_ratio > 1.50 or y_ratio > .65:
        return None

    return StrokeAssociation(
        stroke_index=index,
        region_id=region.region_id,
        horizontal_relation=horizontal,
        vertical_relation=vertical,
        stroke_coverage=stroke_coverage,
        region_coverage=region_coverage,
        horizontal_gap_ratio=x_ratio,
        vertical_gap_ratio=y_ratio,
        relative_center_y=((sy0 + sy1) / 2 - ty0) / h,
    )


def associate_horizontal_strokes(
    strokes: Sequence[HorizontalStroke],
    regions: Sequence[TextRegion],
    *,
    stroke_frame: PixelFrame,
    text_frame: PixelFrame,
) -> list[StrokeAssociation]:
    """Return nearby stroke/region pairs, without semantic classification.

    Both inputs must already use the same original-image pixel frame.
    Boxes are half-open; relations describe the stroke relative to text.
    Coverage is horizontal only. Distances use the local region height.
    Every plausible association is retained, including competing pairs.
    """
    _validate_frame(stroke_frame)
    _validate_frame(text_frame)
    if stroke_frame != text_frame:
        raise ValueError("Coordinate frames do not match.")

    if len(strokes) * len(regions) > 5_000_000:
        raise ValueError("Association budget exceeded; use smaller regions.")

    for stroke in strokes:
        if not isinstance(stroke, HorizontalStroke):
            raise ValueError("Invalid horizontal stroke.")
        _validate_box(stroke.bbox, stroke_frame)
        if (
            type(stroke.ink_pixels) is not int
            or not 0 < stroke.ink_pixels <= stroke.width * stroke.height
        ):
            raise ValueError("Invalid stroke ink area.")

    seen = set()
    for region in regions:
        if (
            not isinstance(region, TextRegion)
            or not isinstance(region.region_id, str)
            or not region.region_id.strip()
            or region.region_id in seen
        ):
            raise ValueError("Invalid or duplicate text region ID.")
        _validate_box(region.bbox, text_frame)
        seen.add(region.region_id)

    results = []
    for index, stroke in enumerate(strokes):
        for region in regions:
            match = _associate(index, stroke, region)
            if match is not None:
                results.append(match)

    return sorted(results, key=lambda a: (a.stroke_index, a.region_id))