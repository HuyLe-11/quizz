"""Cache-only PaddleOCR token and original-PDF stroke diagnostics."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pymupdf

from .engine.visual_primitives import detect_horizontal_strokes
from .paddle_registration_probe import inspect

SCALE = 2


def token_strokes(gray, sample):
    """List original-image candidates, never infer a character span."""

    if (
        not isinstance(gray, np.ndarray)
        or gray.ndim != 2
        or gray.dtype != np.uint8
    ):
        raise ValueError(
            "Expected grayscale uint8 image"
        )

    if (
        sample.get("status") != "near_identity_candidate"
        or sample.get("points") != 5
    ):
        return {
            "status": "registration_unresolved",
            "candidates": [],
        }

    displacement = sample.get(
        "max_displacement_px"
    )

    if (
        type(displacement) not in (int, float)
        or not math.isfinite(displacement)
        or not 0 <= displacement <= 2
    ):
        raise ValueError(
            "Invalid registration displacement"
        )

    box = sample.get("bbox")

    if (
        not isinstance(box, (tuple, list))
        or len(box) != 4
        or not all(type(v) is int for v in box)
    ):
        raise ValueError(
            "Invalid word box"
        )

    x, y, r, b = (
        v * SCALE
        for v in box
    )

    H, W = gray.shape

    if not (
        0 <= x < r <= W
        and 0 <= y < b <= H
    ):
        raise ValueError(
            "Word box outside render"
        )

    h = b - y

    pad = max(
        4,
        math.ceil(.30 * h),
    )

    l = max(0, x - pad)
    t = max(0, y - pad)

    rr = min(W, r + pad)
    bb = min(H, b + pad)

    if (
        not 9 <= h <= 512
        or (rr - l) * (bb - t) > 250_000
    ):
        return {
            "status": "unsupported_roi",
            "candidates": [],
        }

    roi = gray[t:bb, l:rr]

    candidates = []

    for s in detect_horizontal_strokes(
        roi,
        reference_height=max(
            6.,
            .7 * h,
        ),
    ):

        a = s.x0 + l
        c = s.y0 + t
        d = s.x1 + l
        e = s.y1 + t

        center = (
            (c + e) / 2 - y
        ) / h

        overlap = max(
            0,
            min(r, d) - max(x, a),
        )

        if (
            not .55 <= center <= 1.25
            or overlap / s.width < .5
        ):
            continue

        candidates.append({
            "bbox": [
                a, c, d, e
            ],
            "stroke_coverage": round(
                overlap / s.width,
                4,
            ),
            "token_coverage": round(
                overlap / (r - x),
                4,
            ),
            "relative_center_y": round(
                center,
                4,
            ),
            "fill_ratio": round(
                s.fill_ratio,
                4,
            ),
            "touches_roi_boundary": (
                s.x0 == 0
                or s.y0 == 0
                or s.x1 == roi.shape[1]
                or s.y1 == roi.shape[0]
            ),
        })

    return {
        "status": (
            "stroke_candidates"
            if candidates
            else "no_stroke_candidates"
        ),
        "approximate_word_box": [
            x, y, r, b
        ],
        "roi": [
            l, t, rr, bb
        ],
        "candidates": candidates,
    }


def run(
    pdf,
    sha256,
    journal,
    job_id,
    *,
    matches,
):

    source = Path(pdf).read_bytes()

    if (
        hashlib.sha256(source).hexdigest()
        != sha256
    ):
        raise ValueError(
            "PDF SHA-256 mismatch"
        )

    result = inspect(
        pdf,
        sha256,
        journal,
        job_id,
        matches=matches,
        fetch_images=False,
    )

    if (
        result["status"]
        != "registration_established"
    ):
        return result

    w, h = (
        v * SCALE
        for v in result["size"]
    )

    if w * h > 12_000_000:
        raise ValueError(
            "Render exceeds pixel budget"
        )

    with pymupdf.open(
        stream=source,
        filetype="pdf",
    ) as doc:

        if (
            doc.needs_pass
            or len(doc) != 1
            or doc[0].rotation
        ):
            raise ValueError(
                "Expected one unrotated PDF page"
            )

        page = doc[0]

        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(
                w / page.rect.width,
                h / page.rect.height,
            ),
            colorspace=pymupdf.csGRAY,
            alpha=False,
        )

        if (
            pix.width,
            pix.height,
        ) != (w, h):
            raise ValueError(
                "Render dimensions mismatch"
            )

        gray = np.frombuffer(
            pix.samples,
            np.uint8,
        ).reshape(h, w)

    return {
        **result,
        "scope": "diagnostic_only",
        "render_size": [
            w, h
        ],
        "stroke_coordinate_space": (
            "original_pdf_render_pixels"
        ),
        "samples": [
            {
                **s,
                "stroke_probe": token_strokes(
                    gray,
                    s,
                ),
            }
            for s in result["samples"]
        ],
    }


def main():

    p = argparse.ArgumentParser(
        description=__doc__
    )

    p.add_argument(
        "--pdf",
        type=Path,
        required=True,
    )

    p.add_argument(
        "--sha256",
        required=True,
    )

    p.add_argument(
        "--journal",
        type=Path,
        required=True,
    )

    p.add_argument(
        "--job-id",
        required=True,
    )

    p.add_argument(
        "--match",
        action="append",
        required=True,
    )

    a = p.parse_args()

    print(
        json.dumps(
            run(
                a.pdf,
                a.sha256,
                a.journal,
                a.job_id,
                matches=a.match,
            ),
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
