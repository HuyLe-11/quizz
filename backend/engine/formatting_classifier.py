"""Subject-independent geometric formatting hypotheses, not verified edits."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .spatial_association import (
    BBox, PixelFrame, TextRegion, _validate_box, associate_horizontal_strokes,
)
from .visual_primitives import HorizontalStroke


Kind = Literal["underline", "blank", "fraction_bar", "strikethrough", "table_border"]
MAX_NEIGHBORS = 48


@dataclass(frozen=True, slots=True)
class FormattingCandidate:
    kind: Kind
    region_ids: tuple[str, ...] = ()
    vertical_rule_indices: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class StrokeClassification:
    stroke_index: int
    kind: Kind | Literal["unknown"]
    candidates: tuple[FormattingCandidate, ...]
    reason: str


def _height(region):
    return region.bbox[3] - region.bbox[1]


def _center_x(region):
    return (region.bbox[0] + region.bbox[2]) / 2


def _thin(stroke, region):
    return stroke.height <= .20 * _height(region)


def _text_candidates(stroke, matches, regions):
    result = []
    upper, lower, left, right = [], [], [], []
    occupied = False

    for a in matches:
        r = regions[a.region_id]
        h = _height(r)
        if a.horizontal_relation == "overlap":
            if .55 <= a.relative_center_y <= 1.25:
                occupied = True
            if not _thin(stroke, r):
                continue
            if (a.stroke_coverage >= .75
                    and .85 <= a.relative_center_y <= 1.25
                    and a.vertical_gap_ratio <= .25):
                result.append(FormattingCandidate("underline", (r.region_id,)))
            if (a.stroke_coverage >= .80
                    and a.vertical_relation == "overlap"
                    and .30 <= a.relative_center_y <= .65
                    and stroke.width >= .75 * h):
                result.append(FormattingCandidate("strikethrough", (r.region_id,)))
            if (a.stroke_coverage >= .35 and a.region_coverage >= .75
                    and a.vertical_gap_ratio <= .60):
                if a.vertical_relation == "below":
                    upper.append(r)
                elif a.vertical_relation == "above":
                    lower.append(r)
        elif (_thin(stroke, r) and a.horizontal_gap_ratio <= 1.0
              and .80 <= a.relative_center_y <= 1.20
              and stroke.width >= .90 * h):
            # Relations describe the stroke relative to the text.
            (left if a.horizontal_relation == "right" else right).append(r)

    for a in upper:
        for b in lower:
            if abs(_center_x(a) - _center_x(b)) <= .20 * stroke.width:
                result.append(FormattingCandidate("fraction_bar", (a.region_id, b.region_id)))

    if not occupied:
        for a in left:
            for b in right:
                if abs(a.bbox[3] - b.bbox[3]) <= .25 * min(_height(a), _height(b)):
                    result.append(FormattingCandidate("blank", (a.region_id, b.region_id)))
    return result


def _table_candidates(stroke, rules):
    if stroke.width < 8 * stroke.height:
        return []
    left, right = [], []
    y = (stroke.y0 + stroke.y1) / 2
    extension = 4 * stroke.height
    for index, (x0, y0, x1, y1) in enumerate(rules):
        tolerance = max(stroke.height, x1 - x0)
        if not y0 - tolerance <= y <= y1 + tolerance:
            continue
        sides = (y - y0 >= extension, y1 - y >= extension)
        if not any(sides):
            continue
        x = (x0 + x1) / 2
        if abs(x - stroke.x0) <= tolerance:
            left.append((index, sides))
        if abs(x - stroke.x1) <= tolerance:
            right.append((index, sides))
    if len(left) + len(right) > MAX_NEIGHBORS:
        return None
    return [FormattingCandidate("table_border", vertical_rule_indices=(i, j))
            for i, a in left for j, b in right
            if i != j and any(x and z for x, z in zip(a, b))]


def classify_horizontal_strokes(
    strokes: Sequence[HorizontalStroke],
    regions: Sequence[TextRegion],
    *,
    stroke_frame: PixelFrame,
    text_frame: PixelFrame,
    vertical_rules: Sequence[BBox] = (),
    vertical_frame: PixelFrame | None = None,
) -> tuple[StrokeClassification, ...]:
    """Classify geometric candidates in one validated original-image frame.

    Supply local, non-duplicated text boxes, not entire question boxes.
    Optional vertical rule boxes must already be in the same frame.
    A unique candidate is a hypothesis only; no text or answers are edited.
    """
    matches = associate_horizontal_strokes(strokes, regions,
                                           stroke_frame=stroke_frame, text_frame=text_frame)
    if (vertical_rules or vertical_frame is not None) and vertical_frame != stroke_frame:
        raise ValueError("Vertical rule frame does not match.")
    if len(strokes) * len(vertical_rules) > 5_000_000:
        raise ValueError("Vertical association budget exceeded.")
    for box in vertical_rules:
        _validate_box(box, stroke_frame)
        if box[3] - box[1] < 3 * (box[2] - box[0]):
            raise ValueError("Expected a vertical rule box.")
    by_id = {r.region_id: r for r in regions}
    by_stroke = defaultdict(list)
    for match in matches:
        by_stroke[match.stroke_index].append(match)

    results = []
    for i, stroke in enumerate(strokes):
        nearby = by_stroke[i]
        if len(nearby) > MAX_NEIGHBORS:
            results.append(StrokeClassification(i, "unknown", (), "too_many_neighbors"))
            continue
        table = _table_candidates(stroke, vertical_rules)
        if table is None:
            results.append(StrokeClassification(i, "unknown", (), "too_many_neighbors"))
            continue
        candidates = tuple(_text_candidates(stroke, nearby, by_id) + table)
        if len(candidates) == 1:
            kind, reason = candidates[0].kind, "unique_geometry_candidate"
        elif candidates:
            kind, reason = "unknown", "ambiguous_geometry"
        else:
            kind, reason = "unknown", "no_geometry_match"
        results.append(StrokeClassification(i, kind, candidates, reason))
    return tuple(results)
