"""Validated page-local mapping from PDF input pixels to OCR pixels."""
from __future__ import annotations

from dataclasses import dataclass
import math

import cv2 as cv
import numpy as np
import pymupdf


class RegistrationError(ValueError):
    """Registration could not be established safely."""


@dataclass(frozen=True)
class MappedPoint:
    x: float
    y: float
    method: str


def _local_affine(center, p, q, projected, unit):
    predictions = []

    for radius in (3 * unit, 4 * unit):

        keep = (
            (
                np.linalg.norm(
                    p - center,
                    axis=1,
                ) <= radius
            )
            &
            (
                np.linalg.norm(
                    projected - q,
                    axis=1,
                ) <= 1.5 * unit
            )
        )

        src, dst = p[keep], q[keep]

        if len(src) < 10:
            return None

        matrix, mask = cv.estimateAffine2D(
            src,
            dst,
            method=cv.RANSAC,
            ransacReprojThreshold=.10 * unit,
            maxIters=2000,
        )

        if (
            matrix is None
            or mask is None
            or not np.isfinite(matrix).all()
        ):
            return None

        inside = mask.ravel().astype(bool)

        if (
            inside.sum() < 10
            or inside.mean() < .75
        ):
            return None

        hull = cv.convexHull(src[inside])

        if cv.pointPolygonTest(
            hull,
            tuple(map(float, center)),
            False,
        ) < 0:
            return None

        linear = matrix[:, :2]

        scales = np.linalg.svd(
            linear,
            compute_uv=False,
        )

        if (
            np.linalg.det(linear) <= 0
            or min(scales) < .65
            or max(scales) > 1.5
        ):
            return None

        predictions.append(
            linear @ center + matrix[:, 2]
        )

    if np.linalg.norm(
        predictions[0] - predictions[1]
    ) > .15 * unit:
        return None

    return np.mean(
        predictions,
        axis=0,
    )


def _template_match(
    a,
    b,
    center,
    radius,
    margin,
    exclusion,
):
    if not np.isfinite(center).all():
        return None

    x, y = np.round(center).astype(int)

    h, w = a.shape

    if not (
        radius <= x < w - radius
        and radius <= y < h - radius
    ):
        return None

    template = a[
        y-radius:y+radius+1,
        x-radius:x+radius+1,
    ]

    x0 = max(0, x-radius-margin)
    y0 = max(0, y-radius-margin)

    search = b[
        y0:y+radius+margin+1,
        x0:x+radius+margin+1,
    ]

    if min(
        template.std(),
        search.std(),
    ) < 5:
        return None

    scores = cv.matchTemplate(
        search,
        template,
        cv.TM_CCOEFF_NORMED,
    )

    _, score, _, (lx, ly) = cv.minMaxLoc(
        scores
    )

    scores[
        max(0, ly-exclusion):ly+exclusion+1,
        max(0, lx-exclusion):lx+exclusion+1,
    ] = -1

    if (
        score < .60
        or score - float(scores.max()) < .10
    ):
        return None

    return np.array(
        [
            x0 + lx + radius,
            y0 + ly + radius,
        ],
        dtype=float,
    )


@dataclass(frozen=True)
class PageRegistration:
    width: int
    height: int
    unit: float

    mutual_matches: int
    inliers: int
    input_mae: float

    _H: np.ndarray
    _p: np.ndarray
    _q: np.ndarray
    _projected: np.ndarray
    _warped: np.ndarray
    _target: np.ndarray

    def map_point(
        self,
        x: float,
        y: float,
    ) -> MappedPoint | None:
        """Return review-only evidence; never use homography alone."""

        if not (
            math.isfinite(x)
            and math.isfinite(y)
            and 0 <= x < self.width
            and 0 <= y < self.height
        ):
            return None

        center = np.array(
            [x, y],
            dtype=float,
        )

        unit = self.unit

        affine = _local_affine(
            center,
            self._p,
            self._q,
            self._projected,
            unit,
        )

        global_pt = cv.perspectiveTransform(
            np.float32([[center]]),
            self._H,
        )[0, 0]

        margin = max(
            1,
            round(40 * unit / 30),
        )

        exclusion = max(
            1,
            round(12 * unit / 30),
        )

        small, large = (
            _template_match(
                self._warped,
                self._target,
                global_pt,
                max(
                    1,
                    round(r * unit / 30),
                ),
                margin,
                exclusion,
            )
            for r in (25, 45)
        )

        template = None

        if (
            small is not None
            and large is not None
        ):
            if np.linalg.norm(
                small - large
            ) <= 4 * unit / 30:

                template = (
                    small + large
                ) / 2

        if (
            affine is not None
            and template is not None
        ):
            if np.linalg.norm(
                affine - template
            ) > .35 * unit:

                return None

        point = (
            affine
            if affine is not None
            else template
        )

        if (
            point is None
            or not np.isfinite(point).all()
        ):
            return None

        px, py = map(float, point)

        if not (
            0 <= px < self.width
            and 0 <= py < self.height
        ):
            return None

        return MappedPoint(
            px,
            py,
            (
                "local_affine"
                if affine is not None
                else "template"
            ),
        )


def register_page(
    page,
    input_image,
    ocr_image,
    *,
    width,
    height,
    unit,
):
    """Verify PDF/input identity and register a known 2-panel OCR visualization.

    Caller must bind images and OCR metadata to the same provider job/page.
    All images are uint8 BGR. Coordinates returned are OCR pixels.
    """

    if (
        type(width) is not int
        or type(height) is not int
        or min(width, height) < 128
        or width * height > 4_000_000
        or not math.isfinite(unit)
        or not 6 <= unit <= min(width, height) / 5
        or page.rotation
    ):
        raise RegistrationError(
            "Unsupported page geometry"
        )

    for image, shape in (
        (input_image, (height, width, 3)),
        (ocr_image, (height, 2 * width, 3)),
    ):

        if (
            not isinstance(image, np.ndarray)
            or image.dtype != np.uint8
            or image.shape != shape
        ):
            raise RegistrationError(
                "Unexpected image format or dimensions"
            )

    rect = page.rect

    if abs(
        (width / height)
        / (rect.width / rect.height)
        - 1
    ) > .01:

        raise RegistrationError(
            "Page aspect ratio mismatch"
        )

    pix = page.get_pixmap(
        matrix=pymupdf.Matrix(
            width / rect.width,
            height / rect.height,
        ),
        alpha=False,
        colorspace=pymupdf.csRGB,
    )

    if (pix.width, pix.height) != (width, height):
        raise RegistrationError(
            "Rendered dimensions mismatch"
        )

    rendered = np.frombuffer(
        pix.samples,
        np.uint8,
    ).reshape(
        height,
        width,
        3,
    )

    rendered = cv.cvtColor(
        rendered,
        cv.COLOR_RGB2BGR,
    )

    mae = float(
        np.abs(
            rendered.astype(np.float32)
            - input_image
        ).mean()
    )

    if mae > 5:
        raise RegistrationError(
            "Input image differs from PDF"
        )

    ga = cv.cvtColor(
        input_image,
        cv.COLOR_BGR2GRAY,
    )

    gb = cv.cvtColor(
        ocr_image[:, :width],
        cv.COLOR_BGR2GRAY,
    )

    sift = cv.SIFT_create(
        nfeatures=6000
    )

    ka, da = sift.detectAndCompute(
        ga,
        None,
    )

    kb, db = sift.detectAndCompute(
        gb,
        None,
    )

    if (
        da is None
        or db is None
        or min(len(da), len(db)) < 2
    ):
        raise RegistrationError(
            "Insufficient features"
        )

    matcher = cv.BFMatcher()

    def matches(d1, d2):

        return {
            m.queryIdx: m.trainIdx

            for pair in matcher.knnMatch(
                d1,
                d2,
                k=2,
            )

            if len(pair) == 2

            for m, n in [pair]

            if m.distance < .75 * n.distance
        }

    fw = matches(da, db)
    rv = matches(db, da)

    pairs = [
        (i, j)
        for i, j in fw.items()
        if rv.get(j) == i
    ]

    if len(pairs) < 30:
        raise RegistrationError(
            "Insufficient mutual matches"
        )

    p = np.float32([
        ka[i].pt
        for i, _ in pairs
    ])

    q = np.float32([
        kb[j].pt
        for _, j in pairs
    ])

    H, mask = cv.findHomography(
        p,
        q,
        cv.RANSAC,
        4 * unit / 30,
    )

    if (
        H is None
        or mask is None
        or not np.isfinite(H).all()
    ):
        raise RegistrationError(
            "Global registration failed"
        )

    keep = mask.ravel().astype(bool)

    if (
        keep.sum() < 30
        or keep.mean() < .5
        or min(
            np.ptp(p[keep], axis=0)
            / [width, height]
        ) < .6
    ):
        raise RegistrationError(
            "Insufficient global support"
        )

    projected = cv.perspectiveTransform(
        p[:, None, :],
        H,
    )[:, 0]

    warped = cv.warpPerspective(
        ga,
        H,
        (width, height),
    )

    for array in (
        H,
        p,
        q,
        projected,
        warped,
        gb,
    ):
        array.setflags(write=False)

    return PageRegistration(
        width,
        height,
        float(unit),
        len(pairs),
        int(keep.sum()),
        mae,
        H,
        p,
        q,
        projected,
        warped,
        gb,
    )