"""Build offline PaddleOCR spatial-span experiment inputs; never submit OCR."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from collections import Counter

import cv2
import numpy as np
import pymupdf

ROWS = 21
MAX_CANDIDATES = 42
PADDING = 16


def digest(data):
    return hashlib.sha256(data).hexdigest()


def box(value, w, h):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or not all(type(v) is int for v in value)
    ):
        raise ValueError("Invalid pixel box")

    x0, y0, x1, y1 = value

    if not (
        0 <= x0 < x1 <= w
        and 0 <= y0 < y1 <= h
    ):
        raise ValueError("Pixel box outside image")

    return x0, y0, x1, y1


def make_rows(gray, report):
    if (
        gray.ndim != 2
        or gray.dtype != np.uint8
    ):
        raise ValueError(
            "Expected grayscale uint8 image"
        )

    h, w = gray.shape

    if (
        report.get("status")
        != "registration_established"
        or report.get("stroke_coordinate_space")
        != "original_pdf_render_pixels"
        or report.get("render_size") != [w, h]
    ):
        raise ValueError(
            "Registration report does not match render"
        )

    rows, skipped = [], []

    for sample_index, sample in enumerate(
        report["samples"]
    ):
        idx = sample["line_index"]
        text = sample["text"]

        probe = sample["stroke_probe"]

        if (
            sample["status"]
            != "near_identity_candidate"
            or sample.get("points") != 5
            or probe["status"] != "stroke_candidates"
        ):
            skipped.append({
                "line_index": idx,
                "text": text,
                "reason": probe["status"],
            })
            continue

        l, t, r, b = box(
            probe["roi"],
            w,
            h,
        )

        x0, y0, x1, y1 = box(
            probe["approximate_word_box"],
            w,
            h,
        )

        if not (
            l <= x0 < x1 <= r
            and t <= y0 < y1 <= b
            and (r - l) * (b - t) <= 250_000
        ):
            raise ValueError("Invalid token ROI")

        # Restrict vertical context to word box
        # and all stroke candidates.
        # Find empty columns outside their
        # horizontal envelope.
        strokes = [
            box(c["bbox"], w, h)
            for c in probe["candidates"]
        ]

        if not strokes or any(
            not (
                l <= a < d <= r
                and t <= c < e <= b
            )
            for a, c, d, e in strokes
        ):
            raise ValueError("Stroke outside ROI")

        original_top, original_bottom = t, b

        t = min(
            y0,
            *(c for _, c, _, _ in strokes),
        )

        b = max(
            y1,
            *(e for _, _, _, e in strokes),
        )

        low = min(
            x0,
            *(a for a, _, _, _ in strokes),
        )

        high = max(
            x1,
            *(d for _, _, d, _ in strokes),
        )

        band = gray[t:b, l:r]

        _, mask0 = cv2.threshold(
            band,
            0,
            255,
            cv2.THRESH_BINARY_INV
            | cv2.THRESH_OTSU,
        )

        gaps = (
            np.flatnonzero(
                ~mask0.any(axis=0)
            ) + l
        )

        left = gaps[gaps < low]
        right = gaps[gaps >= high]

        l = int(left[-1]) if len(left) else l
        r = int(right[0]) + 1 if len(right) else r

        vertical = gray[
            original_top:original_bottom,
            l:r,
        ]

        _, vmask = cv2.threshold(
            vertical,
            0,
            255,
            cv2.THRESH_BINARY_INV
            | cv2.THRESH_OTSU,
        )

        row_gaps = (
            np.flatnonzero(
                ~vmask.any(axis=1)
            ) + original_top
        )

        above = row_gaps[row_gaps < t]
        below = row_gaps[row_gaps >= b]

        t = int(above[-1]) if len(above) else t
        b = int(below[0]) + 1 if len(below) else b

        original = gray[t:b, l:r].copy()

        _, binary = cv2.threshold(
            original,
            0,
            255,
            cv2.THRESH_BINARY_INV
            | cv2.THRESH_OTSU,
        )

        ink = binary != 0

        if not ink.any() or ink.all():
            skipped.append({
                "line_index": idx,
                "text": text,
                "reason": "unreliable_mask",
            })
            continue

        crop_status = (
            "edge_ink_unresolved"
            if (
                not len(left)
                or not len(right)
                or ink[0].any()
                or ink[-1].any()
                or ink[:, 0].any()
                or ink[:, -1].any()
            )
            else "isolated_crop_candidate"
        )

        for j, candidate in enumerate(
            probe["candidates"]
        ):
            a, c, d, e = box(
                candidate["bbox"],
                w,
                h,
            )

            if not (
                l <= a < d <= r
                and t <= c < e <= b
            ):
                raise ValueError(
                    "Stroke outside ROI"
                )

            cid = (
                f"line-{idx}"
                f"-sample-{sample_index}"
                f"-stroke-{j}"
            )

            if (
                candidate.get(
                    "touches_roi_boundary"
                )
                or not (
                    l < a < d < r
                    and t <= c < e <= b
                )
            ):
                skipped.append({
                    "candidate_id": cid,
                    "line_index": idx,
                    "reason": "clipped_stroke",
                })
                continue

            masked = original.copy()
            mask = ink.copy()

            band = (
                slice(c - t, e - t),
                slice(a - l, d - l),
            )

            removed = int(
                mask[band].sum()
            )

            # Diagnostic intervention only:
            # can erase part of a glyph.
            masked[band] = int(
                np.median(original[~ink])
            )

            mask[band] = False

            tiles = []

            for role, left, right in (
                ("control", l, r),
                ("prefix", l, a),
                ("selected", a, d),
                ("suffix", d, r),
            ):
                sl = slice(
                    left - l,
                    right - l,
                )

                pixels = (
                    original[:, sl]
                    if role == "control"
                    else masked[:, sl]
                )

                count = int(
                    (
                        ink
                        if role == "control"
                        else mask
                    )[:, sl].sum()
                )

                tiles.append((
                    {
                        "role": role,
                        "source_box": [
                            left,
                            t,
                            right,
                            b,
                        ],
                        "ink_pixels": count,
                        "masked": role == "selected",
                    },
                    pixels.copy(),
                ))

            if len(rows) >= MAX_CANDIDATES:
                raise ValueError(
                    "Candidate budget exceeded; "
                    "split the diagnostic selection"
                )

            rows.append((
                {
                    "candidate_id": cid,
                    "line_index": idx,
                    "text": text,
                    "stroke_box": [a, c, d, e],
                    "removed_ink_pixels": removed,
                    "crop_status": crop_status,
                },
                tiles,
            ))

    return rows, skipped


def build_atlas(rows):
    if not rows:
        raise ValueError(
            "No eligible candidates"
        )

    cw = (
        max(
            tile.shape[1]
            for _, tiles in rows
            for _, tile in tiles
        )
        + 2 * PADDING
    )

    ch = (
        max(
            tile.shape[0]
            for _, tiles in rows
            for _, tile in tiles
        )
        + 2 * PADDING
    )

    pages, entries = [], []

    for start in range(
        0, len(rows), ROWS
    ):
        group = rows[
            start:start + ROWS
        ]

        if (
            4 * cw * ch * len(group)
            > 4_000_000
        ):
            raise ValueError(
                "Atlas page exceeds pixel budget"
            )

        image = np.full(
            (len(group) * ch, 4 * cw),
            255,
            np.uint8,
        )

        for i, (
            meta,
            tiles,
        ) in enumerate(group):

            entry = {
                **meta,
                "page": len(pages) + 1,
                "tiles": [],
            }

            for col, (
                info,
                pixels,
            ) in enumerate(tiles):

                hh, ww = pixels.shape

                x = (
                    col * cw
                    + (cw - ww) // 2
                )

                y = (
                    i * ch
                    + (ch - hh) // 2
                )

                image[
                    y:y + hh,
                    x:x + ww,
                ] = pixels

                entry["tiles"].append({
                    **info,
                    "atlas_box": [
                        x,
                        y,
                        x + ww,
                        y + hh,
                    ],
                    "cell_box": [
                        col * cw,
                        i * ch,
                        (col + 1) * cw,
                        (i + 1) * ch,
                    ],
                })

            entries.append(entry)

        pages.append(image)

    return pages, entries


def pack(source, report, out):
    sha = digest(source)

    if sha != report.get("pdf_sha256"):
        raise ValueError(
            "PDF SHA-256 mismatch"
        )

    w, h = report["render_size"]

    if not (
        type(w) is int
        and type(h) is int
        and 0 < w * h <= 12_000_000
        and min(w, h) > 0
    ):
        raise ValueError(
            "Invalid render size"
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

    rows, skipped = make_rows(
        gray,
        report,
    )

    pages, entries = build_atlas(
        rows
    )

    manifest = {
        "scope": "diagnostic_only",
        "source_sha256": sha,
        "source_job_id": report["job_id"],
        "render_sha256": digest(
            gray.tobytes()
        ),
        "render_size": [w, h],
        "candidates": entries,
        "skipped": skipped,
        "atlas_pages": [],
    }

    pngs = []

    with pymupdf.open() as doc:

        for i, img in enumerate(
            pages,
            1,
        ):
            ok, encoded = cv2.imencode(
                ".png",
                img,
            )

            if not ok:
                raise RuntimeError(
                    "PNG encoding failed"
                )

            data = encoded.tobytes()

            pngs.append(data)

            hh, ww = img.shape

            page = doc.new_page(
                width=ww / 2,
                height=hh / 2,
            )

            page.insert_image(
                page.rect,
                stream=data,
            )

            manifest["atlas_pages"].append({
                "page": i,
                "size": [ww, hh],
                "png_sha256": digest(data),
            })

        pdf = doc.tobytes(
            garbage=4,
            deflate=True,
        )

    manifest["atlas_pdf_sha256"] = digest(
        pdf
    )

    manifest["atlas_pdf_bytes"] = len(
        pdf
    )

    out = Path(out)

    out.mkdir(
        parents=True,
        exist_ok=False,
    )

    for i, data in enumerate(
        pngs,
        1,
    ):
        (
            out / f"atlas-{i}.png"
        ).write_bytes(data)

    (
        out / "atlas.pdf"
    ).write_bytes(pdf)

    (
        out / "manifest.json"
    ).write_text(
        json.dumps(
            manifest,
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "candidates": len(entries),
        "pages": len(pages),
        "skipped": skipped,
        "atlas_pdf_bytes": len(pdf),
        "crop_status_counts": dict(
            Counter(
                e["crop_status"]
                for e in entries
            )
        ),
        "atlas_sizes": [
            p["size"]
            for p in manifest["atlas_pages"]
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
        "--report",
        type=Path,
        required=True,
    )

    p.add_argument(
        "--out-dir",
        type=Path,
        required=True,
    )

    args = p.parse_args()

    report = json.loads(
        args.report.read_text(
            encoding="utf-8-sig"
        )
    )

    print(
        json.dumps(
            pack(
                args.pdf.read_bytes(),
                report,
                args.out_dir,
            ),
            indent=2,
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
