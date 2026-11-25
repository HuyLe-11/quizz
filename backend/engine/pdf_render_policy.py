"""Conservative crop rasterization policy; no change to OCR geometry."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

import fitz


DEFAULT_MAX_PIXELS = 12_000_000

MAX_SIDE = 16_384


@dataclass(frozen=True)
class CropRenderPlan:
    dpi: int
    width: int
    height: int
    mode: str

    @property
    def pixels(self) -> int:
        return self.width * self.height


def _native_scan_dpi(
    page: fitz.Page,
) -> float | None:
    """
    Identify a conservative pure scanned page.

    A pure scan must contain one axis-aligned raster
    covering the entire page, without text, vector
    overlays, annotations or form widgets.
    """

    if (
        page.rotation != 0
        or page.get_text("words")
        or page.get_drawings()
    ):
        return None

    if (
        page.first_annot is not None
        or page.first_widget is not None
    ):
        return None

    images = page.get_image_info()

    if len(images) != 1:
        return None

    info = images[0]

    box = fitz.Rect(info["bbox"])

    if (
        box.is_empty
        or page.rect.is_empty
    ):
        return None

    tolerance = 0.5

    if any(
        abs(a - b) > tolerance
        for a, b in zip(box, page.rect)
    ):
        return None

    a, b, c, d, _, _ = info["transform"]

    if (
        a <= 0
        or d <= 0
        or abs(b) > 1e-4
        or abs(c) > 1e-4
    ):
        return None

    dpi = min(
        info["width"] / box.width,
        info["height"] / box.height,
    ) * 72

    return (
        dpi
        if math.isfinite(dpi) and dpi > 0
        else None
    )


def plan_crop_render(
    document,
    segments,
    requested_dpi: int,
    max_pixels: int | None = None,
) -> CropRenderPlan:
    """
    Select a common DPI for all crop segments.

    The output pixel budget includes white padding
    used when joining segments of different widths.
    """

    if max_pixels is None:
        max_pixels = int(
            os.getenv(
                "PDF_CROP_MAX_PIXELS",
                str(DEFAULT_MAX_PIXELS),
            )
        )

    if (
        type(requested_dpi) is not int
        or requested_dpi < 1
    ):
        raise ValueError(
            "PDF render DPI must be a positive integer"
        )

    if (
        type(max_pixels) is not int
        or max_pixels < 1
    ):
        raise ValueError(
            "PDF_CROP_MAX_PIXELS must be a positive integer"
        )

    if not segments:
        raise ValueError(
            "No PDF crop segments"
        )

    rects = []

    native = {}

    for index, clip in segments:
        if not 0 <= index < len(document):
            raise ValueError(
                "Invalid PDF crop page"
            )

        page = document[index]

        rect = (
            fitz.Rect(clip)
            & page.rect
        )

        if (
            rect.is_empty
            or not rect.is_valid
        ):
            raise ValueError(
                "Invalid PDF crop rectangle"
            )

        rects.append(rect)

        if index not in native:
            native[index] = (
                _native_scan_dpi(page)
            )

    all_scan = all(
        value is not None
        for value in native.values()
    )

    upper = requested_dpi

    mode = (
        "native_scan"
        if all_scan
        else "vector_or_mixed"
    )

    if all_scan:
        upper = min(
            upper,
            max(
                1,
                math.floor(
                    min(native.values()) + 1e-6
                ),
            ),
        )

    def size(dpi):
        matrix = fitz.Matrix(
            dpi / 72,
            dpi / 72,
        )

        boxes = [
            (rect * matrix).irect
            for rect in rects
        ]

        return (
            max(box.width for box in boxes),
            sum(box.height for box in boxes),
        )

    def fits(dpi):
        width, height = size(dpi)

        return (
            0 < width <= MAX_SIDE
            and 0 < height <= MAX_SIDE
            and width * height <= max_pixels
        )

    if not fits(1):
        raise ValueError(
            "PDF crop exceeds render budget even at 1 DPI"
        )

    # Find a valid DPI without rendering intermediate images.
    lo, hi = 1, upper

    while lo < hi:
        mid = (lo + hi + 1) // 2

        if fits(mid):
            lo = mid

        else:
            hi = mid - 1

    width, height = size(lo)

    if lo < upper:
        mode += "+pixel_cap"

    return CropRenderPlan(
        lo,
        width,
        height,
        mode,
    )