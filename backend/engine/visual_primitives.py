"""Subject-independent visual primitive detection for document images."""
from __future__ import annotations

from dataclasses import dataclass
import math

import cv2
import numpy as np


@dataclass(frozen=True, slots=True)
class HorizontalStroke:
    """A horizontal ink candidate in image pixel coordinates."""

    x0: int
    y0: int
    x1: int
    y1: int
    ink_pixels: int

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0

    @property
    def fill_ratio(self):
        return self.ink_pixels / (
            self.width * self.height
        )

    @property
    def bbox(self):
        return (
            self.x0,
            self.y0,
            self.x1,
            self.y1,
        )


def _odd(value):
    n = max(3, round(value))
    return n if n % 2 else n + 1


def detect_horizontal_strokes(
    gray: np.ndarray,
    *,
    reference_height: float,
) -> list[HorizontalStroke]:
    """
    Detect approximately horizontal ink components.

    Parameters
    ----------
    gray:
        A two-dimensional uint8 grayscale image.

    reference_height:
        Estimated local text height in image pixels.

    Returns
    -------
    list[HorizontalStroke]:
        Candidate strokes in image pixel coordinates.
        Bounding boxes use exclusive right/bottom bounds.

    This function does not classify underlines, blanks,
    fraction bars, table borders or answer annotations.

    It does not inspect text, question numbers, subjects,
    OCR providers or answer choices.

    The input image is never modified.
    """

    if (
        not isinstance(gray, np.ndarray)
        or gray.ndim != 2
        or gray.dtype != np.uint8
    ):
        raise ValueError(
            "Expected a uint8 grayscale image."
        )

    if (
        isinstance(reference_height, bool)
        or not isinstance(reference_height, (int, float))
        or not math.isfinite(reference_height)
        or not 6 <= reference_height <= 512
    ):
        raise ValueError(
            "Invalid reference height."
        )

    h, w = gray.shape

    if (
        h < 3
        or w < 3
        or h * w > 12_000_000
    ):
        raise ValueError(
            "Unsupported image dimensions."
        )

    kernel_width = _odd(
        .35 * reference_height
    )

    block = _odd(
        reference_height
    )

    if (
        kernel_width > w
        or block > min(h, w)
    ):
        return []

    mask = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        block,
        10,
    )

    horizontal = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        np.ones(
            (1, kernel_width),
            dtype=np.uint8,
        ),
    )

    _, _, stats, _ = (
        cv2.connectedComponentsWithStats(
            horizontal
        )
    )

    results = []

    for x, y, width, height, area in stats[1:]:

        x, y, width, height, area = map(
            int,
            (x, y, width, height, area),
        )

        if (
            width < kernel_width
            or height > max(
                2,
                round(.22 * reference_height),
            )
            or width < 3 * height
            or area < .45 * width * height
        ):
            continue

        results.append(
            HorizontalStroke(
                x,
                y,
                x + width,
                y + height,
                area,
            )
        )

    return sorted(
        results,
        key=lambda s: (
            s.y0,
            s.x0,
            s.y1,
            s.x1,
        ),
    )