"""Conservative ink segmentation hypotheses; never verified glyph geometry."""
from __future__ import annotations

import unicodedata as ud
from dataclasses import dataclass

import cv2
import numpy as np

from .spatial_association import (
    PixelFrame,
    TextRegion,
    _validate_box,
    _validate_frame,
)
from .visual_primitives import HorizontalStroke


@dataclass(frozen=True, slots=True)
class InkSpanHypothesis:
    start: int
    end: int
    bbox: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class ScanInkResult:
    frame: PixelFrame
    region_id: str
    text: str
    reason: str
    ink_group_count: int | None = None
    hypotheses: tuple[InkSpanHypothesis, ...] = ()


def _clusters(text):
    if not isinstance(text, str) or not 1 <= len(text) <= 128:
        return None

    spans = []

    for i, c in enumerate(text):
        cat = ud.category(c)

        if (
            c.isspace()
            or cat[0] not in "LNPSM"
            or ud.bidirectional(c) in ("R", "AL", "AN")
            or 0x1F1E6 <= ord(c) <= 0x1F1FF
            or 0x1F3FB <= ord(c) <= 0x1F3FF
            or ud.combining(c) == 9
        ):
            return None

        if cat.startswith("M"):
            if not spans:
                return None

            spans[-1] = (
                spans[-1][0],
                i + 1,
            )
        else:
            spans.append(
                (i, i + 1)
            )

    return spans


def inspect_scan_token(
    gray: np.ndarray,
    region: TextRegion,
    text: str,
    stroke: HorizontalStroke,
    *,
    image_frame: PixelFrame,
    stroke_frame: PixelFrame,
) -> ScanInkResult:
    """Inspect a padded single-token ROI including a suspected underline.

    The caller must independently localize the ROI on the original image.
    Both frame identities must refer to that exact image. Count alignment
    alone does not identify glyphs. Do not pass these hypotheses as trusted
    RegionGlyphs or authorize a RecoveryProposal from them alone.
    """

    _validate_frame(image_frame)

    if stroke_frame != image_frame:
        raise ValueError(
            "Stroke frame mismatch."
        )

    if (
        not isinstance(gray, np.ndarray)
        or gray.dtype != np.uint8
        or gray.shape != (
            image_frame.height,
            image_frame.width,
        )
    ):
        raise ValueError(
            "Image does not match frame."
        )

    if (
        not isinstance(region, TextRegion)
        or not isinstance(region.region_id, str)
        or not region.region_id.strip()
    ):
        raise ValueError(
            "Invalid region."
        )

    _validate_box(
        region.bbox,
        image_frame,
    )

    if not all(
        type(v) is int
        for v in region.bbox
    ):
        raise ValueError(
            "Expected integer pixel ROI."
        )

    if not isinstance(
        stroke,
        HorizontalStroke,
    ):
        raise ValueError(
            "Expected horizontal stroke."
        )

    _validate_box(
        stroke.bbox,
        image_frame,
    )

    if (
        not all(
            type(v) is int
            for v in stroke.bbox
        )
        or type(stroke.ink_pixels) is not int
        or not (
            0 < stroke.ink_pixels
            <= stroke.width * stroke.height
        )
    ):
        raise ValueError(
            "Invalid stroke pixels."
        )

    x0, y0, x1, y1 = region.bbox

    w, h = x1 - x0, y1 - y0

    if (
        w * h > 250_000
        or min(w, h) < 8
    ):
        raise ValueError(
            "Unsupported ROI dimensions."
        )

    sx0, sy0, sx1, sy1 = stroke.bbox

    if not (
        x0 < sx0 < sx1 < x1
        and y0 < sy0 < sy1 < y1
    ):
        raise ValueError(
            "Stroke must be inside padded ROI."
        )

    def result(
        reason,
        count=None,
        hypotheses=(),
    ):
        return ScanInkResult(
            image_frame,
            region.region_id,
            text,
            reason,
            count,
            hypotheses,
        )

    clusters = _clusters(text)

    if clusters is None:
        return result(
            "unsupported_token"
        )

    if stroke.width < 3 * stroke.height:
        return result(
            "unsupported_stroke_shape"
        )

    crop = gray[y0:y1, x0:x1]

    _, binary = cv2.threshold(
        crop,
        0,
        255,
        cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU,
    )

    mask = binary != 0

    fraction = float(mask.mean())

    if (
        not .01 <= fraction <= .65
        or (
            float(crop[~mask].mean())
            - float(crop[mask].mean())
        ) < 35
    ):
        return result(
            "unreliable_binarization"
        )

    if (
        mask[0].any()
        or mask[-1].any()
        or mask[:, 0].any()
        or mask[:, -1].any()
    ):
        return result(
            "ink_at_roi_boundary"
        )

    _, labels, stats, _ = (
        cv2.connectedComponentsWithStats(
            binary,
            connectivity=8,
        )
    )

    # Remove only one isolated component agreeing
    # with the supplied horizontal stroke.
    tolerance = max(
        1,
        stroke.height,
    )

    local = (
        sx0 - x0,
        sy0 - y0,
        sx1 - x0,
        sy1 - y0,
    )

    candidates = []

    for index, (x, y, cw, ch, area) in enumerate(
        stats[1:],
        1,
    ):
        box = (
            x,
            y,
            x + cw,
            y + ch,
        )

        if (
            cw >= 3 * ch
            and area >= .65 * cw * ch
            and all(
                abs(a - b) <= tolerance
                for a, b in zip(box, local)
            )
        ):
            candidates.append(index)

    if len(candidates) != 1:
        return result(
            "rule_not_isolated"
        )

    ink = mask & (
        labels != candidates[0]
    )

    ys, xs = np.nonzero(ink)

    if not len(ys):
        return result(
            "no_text_ink"
        )

    if int(ys.max()) >= local[1]:
        return result(
            "ink_not_above_rule"
        )

    columns = ink.any(axis=0)

    edges = np.flatnonzero(
        np.diff(
            np.r_[
                False,
                columns,
                False,
            ]
        )
    )

    runs = list(
        zip(
            edges[::2],
            edges[1::2],
        )
    )

    if len(runs) != len(clusters):
        return result(
            "ink_group_count_mismatch",
            len(runs),
        )

    hypotheses = []

    for (start, end), (left, right) in zip(
        clusters,
        runs,
    ):
        rows = np.flatnonzero(
            ink[:, left:right].any(axis=1)
        )

        box = (
            x0 + int(left),
            y0 + int(rows[0]),
            x0 + int(right),
            y0 + int(rows[-1]) + 1,
        )

        hypotheses.append(
            InkSpanHypothesis(
                start,
                end,
                box,
            )
        )

    return result(
        "count_aligned_hypothesis",
        len(runs),
        tuple(hypotheses),
    )
