"""Offline spatial text evidence; never verifies or applies formatting."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import unicodedata

from collections import Counter, defaultdict
from pathlib import Path


ROLES = (
    "control",
    "prefix",
    "selected",
    "suffix",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pixel_box(value, size):
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(type(v) is not int for v in value)
    ):
        raise ValueError("Invalid pixel box")

    a, b, c, d = value

    if not (
        0 <= a < c <= size[0]
        and 0 <= b < d <= size[1]
    ):
        raise ValueError("Pixel box outside frame")

    return tuple(value)


def size_pair(value):
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(
            type(v) is not int or v <= 0
            for v in value
        )
        or value[0] * value[1] > 12_000_000
    ):
        raise ValueError("Invalid frame size")

    return value


def inside(a, b):
    return (
        b[0] <= a[0]
        and b[1] <= a[1]
        and a[2] <= b[2]
        and a[3] <= b[3]
    )


def area(b):
    return (
        (b[2] - b[0])
        * (b[3] - b[1])
    )


def overlap(a, b):
    return (
        max(
            0,
            min(a[2], b[2]) - max(a[0], b[0]),
        )
        * max(
            0,
            min(a[3], b[3]) - max(a[1], b[1]),
        )
    )


def boundary_ok(text, index):
    # Conservative subset, not a complete
    # Unicode grapheme implementation.
    if index in (0, len(text)):
        return True

    left = text[index - 1]
    right = text[index]

    return (
        not unicodedata.category(right).startswith("M")
        and unicodedata.combining(left) != 9
        and left != "\u200d"
        and right != "\u200d"
    )


def reconstruct(
    manifest_bytes,
    recognition_bytes,
):
    m = json.loads(manifest_bytes)
    r = json.loads(recognition_bytes)

    if (
        r.get("manifest_sha256") != sha(manifest_bytes)
        or m.get("atlas_pdf_sha256") != r.get("atlas_sha256")
        or m.get("atlas_pdf_bytes") != r.get("atlas_bytes")
        or m.get("scope") != "diagnostic_only"
        or r.get("status") != "recognized_unregistered"
        or r.get("model") != "PP-OCRv6"
        or r.get("requested_options") != {
            "returnWordBox": True,
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
        }
    ):
        raise ValueError(
            "Manifest or recognition identity mismatch"
        )

    source_size = size_pair(
        m["render_size"]
    )

    pages = m["atlas_pages"]
    entries = m["candidates"]

    if (
        not 1 <= len(pages) <= 2
        or not 1 <= len(entries) <= 42
        or r["page_count"] != len(pages)
        or len(r["pages"]) != len(pages)
        or r["candidate_count"] != len(entries)
        or r["atlas_sizes"] != [
            p["size"] for p in pages
        ]
    ):
        raise ValueError("Atlas count mismatch")

    sizes = {}

    for i, (p, q) in enumerate(
        zip(pages, r["pages"]),
        1,
    ):
        size = size_pair(
            p["size"]
        )

        if (
            p["page"] != i
            or q["page"] != i
            or q["provider_size"] != size
            or q["line_count"] != len(q["lines"])
            or len(q["lines"]) > 5000
        ):
            raise ValueError(
                "Page geometry mismatch"
            )

        sizes[i] = size

    cells = defaultdict(list)
    ids = set()

    for e in entries:

        cid = e["candidate_id"]
        p = e["page"]
        text = e["text"]

        if (
            not isinstance(cid, str)
            or not cid
            or cid in ids
            or type(p) is not int
            or p not in sizes
            or type(e["line_index"]) is not int
            or e["line_index"] < 0
            or not isinstance(text, str)
            or not 1 <= len(text) <= 128
            or any(c.isspace() for c in text)
            or e["crop_status"] not in (
                "isolated_crop_candidate",
                "edge_ink_unresolved",
            )
            or [
                t["role"] for t in e["tiles"]
            ] != list(ROLES)
        ):
            raise ValueError("Invalid candidate")

        ids.add(cid)

        stroke = pixel_box(
            e["stroke_box"],
            source_size,
        )

        source_boxes = []

        for tile in e["tiles"]:

            role = tile["role"]

            source = pixel_box(
                tile["source_box"],
                source_size,
            )

            atlas = pixel_box(
                tile["atlas_box"],
                sizes[p],
            )

            cell = pixel_box(
                tile["cell_box"],
                sizes[p],
            )

            ink = tile["ink_pixels"]

            if (
                not inside(atlas, cell)
                or (
                    source[2] - source[0],
                    source[3] - source[1],
                ) != (
                    atlas[2] - atlas[0],
                    atlas[3] - atlas[1],
                )
                or type(ink) is not int
                or not 0 <= ink <= area(source)
                or tile["masked"] is not (
                    role == "selected"
                )
                or any(
                    overlap(cell, item[2])
                    for item in cells[p]
                )
            ):
                raise ValueError(
                    "Invalid or overlapping tile"
                )

            cells[p].append(
                (cid, role, cell, atlas)
            )

            source_boxes.append(source)

        a, b, c, d = source_boxes[0]
        x, y, z, w = stroke

        if (
            not inside(stroke, source_boxes[0])
            or not a < x < z < c
            or source_boxes[1:] != [
                (a, b, x, d),
                (x, b, z, d),
                (z, b, c, d),
            ]
        ):
            raise ValueError(
                "Source crop partition mismatch"
            )

        removed = e["removed_ink_pixels"]

        if (
            type(removed) is not int
            or not 0 <= removed <= area(stroke)
            or e["tiles"][0]["ink_pixels"] != (
                removed
                + sum(
                    t["ink_pixels"]
                    for t in e["tiles"][1:]
                )
            )
        ):
            raise ValueError(
                "Ink accounting mismatch"
            )

    assigned = defaultdict(list)

    for p in r["pages"]:

        seen = set()

        for line in p["lines"]:

            b = pixel_box(
                line["bbox"],
                sizes[p["page"]],
            )

            text = line["text"]
            score = line["score"]
            idx = line["index"]

            if (
                type(idx) is not int
                or idx < 0
                or idx in seen
                or not isinstance(text, str)
                or type(score) not in (int, float)
                or not math.isfinite(score)
                or not 0 <= score <= 1
            ):
                raise ValueError("Invalid OCR line")

            seen.add(idx)

            matches = [
                (cid, role, atlas)
                for cid, role, cell, atlas
                in cells[p["page"]]
                if inside(b, cell)
                and overlap(b, atlas)
            ]

            if len(matches) != 1:
                raise ValueError(
                    "OCR box cannot be assigned uniquely"
                )

            cid, role, _ = matches[0]

            assigned[cid, role].append(line)

    outcomes = []

    tile_counts = Counter()

    for e in entries:

        cid = e["candidate_id"]
        tiles = {}

        for t in e["tiles"]:

            role = t["role"]
            ink = t["ink_pixels"]

            lines = assigned[cid, role]

            text = None
            score = None

            if len(lines) > 1:

                status = "multiple_ocr_lines"

            elif not lines or not lines[0]["text"]:

                if ink == 0:
                    status = "empty_ink"
                    text = ""
                else:
                    status = "missing_text_with_ink"

            elif ink == 0:

                status = "ocr_text_without_ink"

            else:

                status = "recognized"
                text = lines[0]["text"]
                score = lines[0]["score"]

            tile_counts[status] += 1

            tiles[role] = {
                "status": status,
                "text": text,
                "score": score,
                "ink_pixels": ink,
                "ocr_indices": [
                    line["index"]
                    for line in lines
                ],
            }

        control, prefix, selected, suffix = (
            tiles[k]["text"]
            for k in ROLES
        )

        span = None

        if any(
            v is None
            for v in (
                control,
                prefix,
                selected,
                suffix,
            )
        ):
            status = "unresolved_crop"

        elif control != e["text"]:

            status = "control_mismatch"

        elif (
            not selected
            or prefix + selected + suffix != control
        ):
            status = "reconstruction_mismatch"

        elif not (
            boundary_ok(control, len(prefix))
            and boundary_ok(
                control,
                len(prefix) + len(selected),
            )
        ):
            status = "unsupported_span_boundary"

        else:

            span = [
                len(prefix),
                len(prefix) + len(selected),
            ]

            status = "exact_text_candidate"

        outcomes.append({
            "candidate_id": cid,
            "page": e["page"],
            "line_index": e["line_index"],
            "text": e["text"],
            "stroke_box": e["stroke_box"],
            "crop_status": e["crop_status"],
            "status": status,
            "span": span,
            "tiles": tiles,
            "min_score": min(
                (
                    t["score"]
                    for t in tiles.values()
                    if t["score"] is not None
                ),
                default=None,
            ),
        })

    return {
        "scope": "diagnostic_only",
        "coordinate_space": "atlas_unregistered",
        "source_sha256": m["source_sha256"],
        "source_job_id": m["source_job_id"],
        "atlas_job_id": r["job_id"],
        "manifest_sha256": sha(manifest_bytes),
        "recognition_sha256": sha(recognition_bytes),
        "tile_status_counts": dict(tile_counts),
        "candidate_status_counts": dict(
            Counter(
                e["status"]
                for e in outcomes
            )
        ),
        "exact_candidate_crop_counts": dict(
            Counter(
                e["crop_status"]
                for e in outcomes
                if e["span"] is not None
            )
        ),
        "skipped": m["skipped"],
        "candidates": outcomes,
    }


def main():
    p = argparse.ArgumentParser(
        description=__doc__
    )

    p.add_argument(
        "--manifest",
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
            reconstruct(
                a.manifest.read_bytes(),
                a.recognition.read_bytes(),
            ),
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
