"""Offline atlas visual evidence; no OCR requests and no formatting mutation."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from .paddle_span_reconstruct import reconstruct, sha, pixel_box


def crop(image, box):
    a, b, c, d = box
    return image[b:d, a:c]


def ink_mask(gray):
    _, binary = cv2.threshold(
        gray, 0, 255,
        cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
    )

    return binary != 0


def stroke_features(ink, box):
    if (
        not isinstance(ink, np.ndarray)
        or ink.dtype != np.bool_
        or ink.ndim != 2
    ):
        raise ValueError(
            "Expected boolean ink mask"
        )

    h, w = ink.shape

    a, b, c, d = pixel_box(
        list(box),
        (w, h),
    )

    band = ink[b:d, a:c]

    width = c - a
    height = d - b

    outside = ink.copy()

    outside[:, a:c] = False

    cols = np.flatnonzero(
        outside.any(axis=0)
    )

    baseline = None
    gap = None

    if len(cols) >= max(
        8,
        int(.15 * w),
    ):
        baseline = float(
            np.median([
                np.flatnonzero(
                    outside[:, x]
                )[-1]
                for x in cols
            ])
        )

        gap = b - baseline

    coverage = float(
        band.any(axis=0).mean()
    )

    below = float(
        ink[d:, a:c].any(axis=0).mean()
    )

    above_contact = float(
        ink[
            max(0, b - 2):b,
            a:c,
        ].any(axis=0).mean()
    )

    below_contact = float(
        ink[
            d:min(h, d + 2),
            a:c,
        ].any(axis=0).mean()
    )

    # Exploratory screen only.
    # This is not a calibrated underline classifier.
    supported = (
        gap is not None
        and 0 <= gap <= .25 * h
        and width >= 3 * height
        and coverage >= .85
        and below <= .40
    )

    return {
        "baseline_y_local": baseline,
        "baseline_support_columns": int(
            len(cols)
        ),
        "baseline_gap_px": gap,
        "stroke_width": width,
        "stroke_height": height,
        "column_coverage": round(
            coverage, 6
        ),
        "fill_ratio": round(
            float(band.mean()), 6
        ),
        "above_contact": round(
            above_contact, 6
        ),
        "below_contact": round(
            below_contact, 6
        ),
        "below_ink_column_fraction": round(
            below, 6
        ),
        "visual_status": (
            "below_body_candidate"
            if supported
            else "geometry_unresolved"
        ),
    }


def verify_tiles(image, entry):
    tiles = entry["tiles"]

    control = crop(
        image,
        tiles[0]["atlas_box"],
    )

    ink = ink_mask(control)

    if not ink.any() or ink.all():
        raise ValueError(
            "Unreliable control mask"
        )

    x, y, _, _ = tiles[0]["source_box"]

    a, b, c, d = entry["stroke_box"]

    local = (
        a - x,
        b - y,
        c - x,
        d - y,
    )

    l, t, r, bottom = local

    removed = int(
        ink[t:bottom, l:r].sum()
    )

    if (
        int(ink.sum()) != tiles[0]["ink_pixels"]
        or removed != entry["removed_ink_pixels"]
    ):
        raise ValueError(
            "Control ink mismatch"
        )

    masked = control.copy()

    masked[t:bottom, l:r] = int(
        np.median(control[~ink])
    )

    residual = ink.copy()

    residual[t:bottom, l:r] = False

    for tile in tiles[1:]:

        u, v, z, w = tile["source_box"]

        region = (
            u - x,
            v - y,
            z - x,
            w - y,
        )

        expected = crop(
            masked,
            region,
        )

        actual = crop(
            image,
            tile["atlas_box"],
        )

        if (
            not np.array_equal(
                expected,
                actual,
            )
            or int(
                crop(
                    residual,
                    region,
                ).sum()
            ) != tile["ink_pixels"]
        ):
            raise ValueError(
                "Tile pixel or ink mismatch"
            )

    return stroke_features(
        ink,
        local,
    )


def group_summary(rows):
    groups = defaultdict(list)

    for row in rows:

        # The source PDF has one page in this
        # experiment. Atlas page is NOT source page.

        key = (
            row["line_index"],
            row["text"],
            tuple(
                row["control_source_box"]
            ),
        )

        groups[key].append(row)

    results = []

    for (
        line,
        text,
        box,
    ), group in groups.items():

        candidates = [
            r for r in group
            if r["visual_status"]
            == "below_body_candidate"
        ]

        selected = None

        if len(candidates) != 1:

            status = (
                "no_visual_candidate"
                if not candidates
                else "competing_visual_candidates"
            )

        else:

            selected = candidates[0]

            if (
                selected["status"]
                != "exact_text_candidate"
            ):
                status = (
                    "visual_candidate_text_unresolved"
                )

            elif (
                selected["crop_status"]
                != "isolated_crop_candidate"
            ):
                status = (
                    "visual_candidate_crop_unresolved"
                )

            else:

                status = (
                    "unique_visual_text_candidate"
                )

        results.append({
            "line_index": line,
            "text": text,
            "control_source_box": list(box),
            "status": status,
            "visual_candidate_ids": [
                r["candidate_id"]
                for r in candidates
            ],
            "span": (
                selected["span"]
                if selected
                else None
            ),
        })

    return results


def analyze(
    pdf_bytes,
    manifest_bytes,
    recognition_bytes,
):
    evidence = reconstruct(
        manifest_bytes,
        recognition_bytes,
    )

    manifest = json.loads(
        manifest_bytes
    )

    if (
        sha(pdf_bytes)
        != manifest["atlas_pdf_sha256"]
        or len(pdf_bytes)
        != manifest["atlas_pdf_bytes"]
    ):
        raise ValueError(
            "Atlas PDF identity mismatch"
        )

    images = []

    with pymupdf.open(
        stream=pdf_bytes,
        filetype="pdf",
    ) as doc:

        if (
            doc.needs_pass
            or len(doc)
            != len(manifest["atlas_pages"])
        ):
            raise ValueError(
                "Atlas page count mismatch"
            )

        for page, meta in zip(
            doc,
            manifest["atlas_pages"],
        ):
            w, h = meta["size"]

            if (
                page.rotation
                or abs(page.rect.width * 2 - w) > .01
                or abs(page.rect.height * 2 - h) > .01
                or w * h > 4_000_000
            ):
                raise ValueError(
                    "Atlas frame mismatch"
                )

            pix = page.get_pixmap(
                matrix=pymupdf.Matrix(2, 2),
                colorspace=pymupdf.csGRAY,
                alpha=False,
            )

            if (
                pix.width,
                pix.height,
            ) != (w, h):
                raise ValueError(
                    "Atlas render mismatch"
                )

            images.append(
                np.frombuffer(
                    pix.samples,
                    np.uint8,
                ).reshape(h, w)
            )

    rows = []

    for e, c in zip(
        manifest["candidates"],
        evidence["candidates"],
    ):
        features = verify_tiles(
            images[e["page"] - 1],
            e,
        )

        rows.append({
            **c,
            "control_source_box": (
                e["tiles"][0]["source_box"]
            ),
            **features,
        })

    groups = group_summary(rows)

    return {
        "scope": "diagnostic_only",
        "coordinate_space": "atlas_unregistered",
        "atlas_sha256": sha(pdf_bytes),
        "manifest_sha256": sha(manifest_bytes),
        "recognition_sha256": sha(recognition_bytes),
        "source_sha256": evidence["source_sha256"],
        "source_job_id": evidence["source_job_id"],
        "atlas_job_id": evidence["atlas_job_id"],
        "skipped": evidence["skipped"],
        "visual_status_counts": dict(
            Counter(
                r["visual_status"]
                for r in rows
            )
        ),
        "group_status_counts": dict(
            Counter(
                g["status"]
                for g in groups
            )
        ),
        "groups": groups,
        "candidates": rows,
    }


def main():
    p = argparse.ArgumentParser(
        description=__doc__
    )

    p.add_argument(
        "--pack-dir",
        type=Path,
        required=True,
    )

    p.add_argument(
        "--recognition",
        type=Path,
        required=True,
    )

    a = p.parse_args()

    print(
        json.dumps(
            analyze(
                (
                    a.pack_dir / "atlas.pdf"
                ).read_bytes(),
                (
                    a.pack_dir / "manifest.json"
                ).read_bytes(),
                a.recognition.read_bytes(),
            ),
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
