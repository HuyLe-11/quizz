"""Bounded scan segmentation candidates, never verified character geometry."""
from __future__ import annotations

from dataclasses import dataclass
import math

import cv2
import numpy as np

from .scan_ink_geometry import (
    InkSpanHypothesis,
    _clusters,
    inspect_scan_token,
)
from .spatial_association import PixelFrame


@dataclass(frozen=True, slots=True)
class AdvancedScanResult:
    frame: PixelFrame
    region_id: str
    text: str
    reason: str
    initial_reason: str
    hypotheses: tuple[InkSpanHypothesis, ...] = ()
    cuts: tuple[int, ...] = ()
    best_cost: float | None = None
    alternative_cost: float | None = None
    removed_pixels: int = 0
    candidate_cut_count: int | None = None


def _valleys(projection, first, last, height):
    """One representative per local minimum plateau; zero gaps are mandatory."""
    candidates = []
    i = first + 1
    radius = max(2, round(height / 3))

    while i < last - 1:
        j = i + 1

        while j < last and projection[j] == projection[i]:
            j += 1

        value = int(projection[i])

        if (
            j < last
            and value < projection[i - 1]
            and value < projection[j]
            and value <= .18 * height
        ):
            left = int(
                projection[max(first, i-radius):i].max()
            )

            right = int(
                projection[j:min(last, j+radius)].max()
            )

            if (
                value == 0
                or min(left, right) - value
                >= max(2, .12 * height)
            ):
                candidates.append(
                    (
                        (i + j) // 2,
                        value / height,
                        value == 0,
                    )
                )

        i = j

    return candidates


def _best_paths(
    projection,
    first,
    last,
    height,
    count,
    valleys,
):
    """Keep the two lowest-cost distinct paths per (segment count, endpoint)."""
    points = [
        (first, 0., False),
        *valleys,
        (last, 0., False),
    ]

    n = len(points)

    if (
        n > 130
        or count > 64
        or count * n * n > 1_000_000
    ):
        return None

    prefix = np.r_[
        0,
        np.cumsum(
            projection,
            dtype=np.int64,
        ),
    ]

    gap_prefix = np.r_[
        0,
        np.cumsum(
            [p[2] for p in points]
        ),
    ]

    min_width = max(
        2,
        math.ceil(.08 * height),
    )

    max_width = max(
        min_width,
        math.floor(1.8 * height),
    )

    states = {
        0: [(0., ())]
    }

    for step in range(count):
        next_states = {}

        for j in range(1, n):
            if (
                (j == n - 1)
                != (step == count - 1)
            ):
                continue

            end, cost, _ = points[j]

            paths = []

            for i, previous in states.items():
                start = points[i][0]

                if not (
                    min_width
                    <= end - start
                    <= max_width
                ):
                    continue

                if (
                    gap_prefix[j]
                    - gap_prefix[i + 1]
                    != 0
                ):
                    continue

                if (
                    prefix[end] - prefix[start]
                    < max(3, .15 * height)
                ):
                    continue

                paths.extend(
                    (
                        c + cost,
                        cuts + (end,),
                    )
                    for c, cuts in previous
                )

            if paths:
                next_states[j] = sorted(paths)[:2]

        states = next_states

        if not states:
            break

    return states.get(n - 1, [])


def align_scan_token(
    gray,
    region,
    text,
    stroke,
    *,
    image_frame,
    stroke_frame,
):
    """Try conservative segmentation first, then low-ink cut hypotheses.

    Only for independently localized single-token ROIs. The supplied stroke
    must be an original-image hypothesis. Never pass output to a verifier as
    proof of character identity, or as measured font/character boxes.
    """

    base = inspect_scan_token(
        gray,
        region,
        text,
        stroke,
        image_frame=image_frame,
        stroke_frame=stroke_frame,
    )

    def result(reason, **kwargs):
        return AdvancedScanResult(
            base.frame,
            base.region_id,
            base.text,
            reason,
            base.reason,
            **kwargs,
        )

    if base.hypotheses:
        return result(
            "isolated_count_hypothesis",
            hypotheses=base.hypotheses,
        )

    if base.reason not in (
        "rule_not_isolated",
        "ink_group_count_mismatch",
        "ink_not_above_rule",
    ):
        return result(base.reason)

    clusters = _clusters(text)

    if len(clusters) > 64:
        return result(
            "alignment_budget_exceeded"
        )

    x0, y0, x1, y1 = region.bbox

    sx0, sy0, sx1, sy1 = stroke.bbox

    a, b, c, d = (
        sx0 - x0,
        sy0 - y0,
        sx1 - x0,
        sy1 - y0,
    )

    _, binary = cv2.threshold(
        gray[y0:y1, x0:x1],
        0,
        255,
        cv2.THRESH_BINARY_INV
        | cv2.THRESH_OTSU,
    )

    ink = binary != 0

    band = ink[b:d, a:c]

    removed = int(band.sum())

    if (
        float(band.mean()) < .65
        or float(band.any(axis=0).mean()) < .90
        or float(band.mean(axis=1).max()) < .80
    ):
        return result(
            "insufficient_rule_support"
        )

    if removed > .25 * int(ink.sum()):
        return result(
            "excessive_ink_removal"
        )

    # Mask only the supplied band on a private
    # analysis mask, not the PDF image.
    ink[b:d, a:c] = False

    ys, xs = np.nonzero(ink)

    if not len(xs):
        return result(
            "no_text_ink"
        )

    top = int(ys.min())
    bottom = int(ys.max()) + 1

    height = bottom - top

    if (
        height < 8
        or stroke.height > .25 * height
        or not (
            top + .65 * height
            <= b
            <= bottom + .35 * height
        )
    ):
        return result(
            "rule_not_near_baseline",
            removed_pixels=removed,
        )

    if (
        int(ink[d:, a:c].sum())
        > max(2, .03 * removed)
    ):
        return result(
            "ink_below_rule",
            removed_pixels=removed,
        )

    if not ink[:b, a:c].any():
        return result(
            "no_text_above_rule",
            removed_pixels=removed,
        )

    p = ink.sum(axis=0)

    first = int(xs.min())
    last = int(xs.max()) + 1

    valleys = _valleys(
        p,
        first,
        last,
        height,
    )

    paths = _best_paths(
        p,
        first,
        last,
        height,
        len(clusters),
        valleys,
    )

    if paths is None:
        return result(
            "alignment_budget_exceeded",
            removed_pixels=removed,
            candidate_cut_count=len(valleys),
        )

    if not paths:
        return result(
            "no_feasible_segmentation",
            removed_pixels=removed,
            candidate_cut_count=len(valleys),
        )

    cost, ends = paths[0]

    alternative = (
        paths[1][0]
        if len(paths) > 1
        else None
    )

    metrics = dict(
        best_cost=cost,
        alternative_cost=alternative,
        removed_pixels=removed,
        candidate_cut_count=len(valleys),
    )

    if (
        alternative is not None
        and alternative - cost < .08
    ):
        return result(
            "ambiguous_segmentation",
            **metrics,
        )

    hypotheses = []

    for (
        (start, end),
        left,
        right,
    ) in zip(
        clusters,
        (first, *ends[:-1]),
        ends,
    ):
        yy, xx = np.nonzero(
            ink[:, left:right]
        )

        if not len(xx):
            return result(
                "empty_segment",
                **metrics,
            )

        hypotheses.append(
            InkSpanHypothesis(
                start,
                end,
                (
                    x0 + left + int(xx.min()),
                    y0 + int(yy.min()),
                    x0 + left + int(xx.max()) + 1,
                    y0 + int(yy.max()) + 1,
                ),
            )
        )

    return result(
        "valley_aligned_hypothesis",
        hypotheses=tuple(hypotheses),
        cuts=tuple(
            x0 + v
            for v in ends[:-1]
        ),
        **metrics,
    )
