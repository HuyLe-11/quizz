"""Offline, page-level, review-only red circle detection."""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

import cv2 as cv
import numpy as np
import pymupdf

from .ocr_image_cache import CachedImages
from .ocr_image_registration import register_page
from .registered_option_geometry import RegisteredOptionIndex

Box = tuple[float, float, float, float]

MAX_CANDIDATES = 150


@dataclass(frozen=True)
class RegisteredMark:
    question_number: int
    label: str
    mark_coordinates: Box
    registration_method: str
    evidence: str
    status: str = "uncertain"


@dataclass(frozen=True)
class PageMarkResult:
    status: str
    reason: str = ""
    red_components: int = 0
    mapped_components: int = 0
    unassociated_components: int = 0
    conflicted_questions: tuple[int, ...] = ()
    suggestions: tuple[RegisteredMark, ...] = ()


def _render_original(page, width, height):

    rect = page.rect

    pix = page.get_pixmap(
        matrix=pymupdf.Matrix(
            width / rect.width,
            height / rect.height,
        ),

        alpha=False,

        colorspace=pymupdf.csRGB,
    )

    if (
        pix.width,
        pix.height,
    ) != (width, height):

        raise ValueError(
            "Unexpected rendered dimensions"
        )

    rgb = np.frombuffer(
        pix.samples,
        np.uint8,
    ).reshape(
        height,
        width,
        3,
    )

    return cv.cvtColor(
        rgb,
        cv.COLOR_RGB2BGR,
    )


def _red_components(image, unit):

    hsv = cv.cvtColor(
        image,
        cv.COLOR_BGR2HSV,
    )

    red = cv.inRange(
        hsv,
        (0, 100, 50),
        (10, 255, 255),
    )

    red |= cv.inRange(
        hsv,
        (170, 100, 50),
        (179, 255, 255),
    )

    kernel = max(
        1,
        2 * round(unit / 30) + 1,
    )

    red = cv.morphologyEx(
        red,

        cv.MORPH_CLOSE,

        np.ones(
            (kernel, kernel),
            np.uint8,
        ),
    )

    _, _, stats, centers = (
        cv.connectedComponentsWithStats(
            red,
            connectivity=8,
        )
    )

    area_scale = (unit / 30) ** 2

    result = []

    for (
        x, y, w, h, area
    ), center in zip(
        stats[1:],
        centers[1:],
    ):

        if not (
            100 * area_scale
            < area
            < 10000 * area_scale

            and .5 <= w / h <= 2

            and .6 * unit <= min(w, h)

            and max(w, h) <= 4 * unit
        ):

            continue

        result.append(
            (
                (
                    int(x),
                    int(y),
                    int(w),
                    int(h),
                ),

                tuple(
                    map(float, center)
                ),
            )
        )

    return result


def detect_registered_marks(
    page,
    recognition,
    labels_by_question,
    images: CachedImages,
    *,
    width: int,
    height: int,
) -> PageMarkResult:

    """Caller must bind recognition and images to the same job/page.

    No OCR, network, journal writes, or automatic answer confirmation.
    labels_by_question must cover all recognized headings on this page.
    """

    if (
        not isinstance(recognition, dict)

        or not isinstance(
            labels_by_question,
            Mapping,
        )

        or not labels_by_question

        or not isinstance(
            images,
            CachedImages,
        )
    ):

        return PageMarkResult(
            "unavailable",
            "missing_evidence",
        )

    try:

        index = RegisteredOptionIndex(
            recognition,

            labels_by_question,

            width=width,

            height=height,
        )

    except (ValueError, TypeError):

        return PageMarkResult(
            "unavailable",
            "invalid_option_index",
        )

    try:

        registration = register_page(
            page,

            images.input_image,

            images.ocr_image,

            width=width,

            height=height,

            unit=index.unit,
        )

        original = _render_original(
            page,
            width,
            height,
        )

        components = _red_components(
            original,
            index.unit,
        )

    except (
        ValueError,
        cv.error,
        np.linalg.LinAlgError,
        RuntimeError,
    ):

        return PageMarkResult(
            "unavailable",
            "registration_or_render_rejected",
        )

    count = len(components)

    if count > MAX_CANDIDATES:

        return PageMarkResult(
            "unavailable",

            "too_many_components",

            count,
        )

    if not count:

        return PageMarkResult(
            "processed",
            "no_eligible_components",
        )

    rect = page.rect

    sx = rect.width / width

    sy = rect.height / height

    hits = defaultdict(list)

    mapped = 0

    unassociated = 0

    for (
        x, y, w, h
    ), center in components:

        try:

            point = registration.map_point(
                *center
            )

        except (
            ValueError,
            cv.error,
            np.linalg.LinAlgError,
        ):

            point = None

        if point is None:

            continue

        mapped += 1

        if not (
            math.isfinite(point.x)

            and math.isfinite(point.y)

            and point.method in (
                "local_affine",
                "template",
            )
        ):

            unassociated += 1

            continue

        match = index.associate(
            point.x,
            point.y,
        )

        if match is None:

            unassociated += 1

            continue

        coordinates = (
            rect.x0 + x * sx,

            rect.y0 + y * sy,

            rect.x0 + (x + w) * sx,

            rect.y0 + (y + h) * sy,
        )

        hits[
            match.question_number
        ].append(
            RegisteredMark(
                match.question_number,

                match.label,

                coordinates,

                point.method,

                match.evidence,
            )
        )

    # Even repeated marks for the same label
    # are ambiguous at this stage.

    conflicts = tuple(
        sorted(
            n

            for n, marks in hits.items()

            if len(marks) != 1
        )
    )

    suggestions = tuple(
        hits[n][0]

        for n in sorted(hits)

        if len(hits[n]) == 1
    )

    return PageMarkResult(
        "processed",

        "",

        count,

        mapped,

        unassociated,

        conflicts,

        suggestions,
    )