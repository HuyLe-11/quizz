import hashlib
import json
import tempfile
import unittest

from pathlib import Path

import numpy as np
import pymupdf

from backend.paddle_span_pack import (
    make_rows,
    build_atlas,
    pack,
)


def fixture():

    gray = np.full(
        (100, 160),
        255,
        np.uint8,
    )

    gray[30:48, 30:42] = 0
    gray[30:48, 55:72] = 0
    gray[52:54, 55:72] = 0

    report = {
        "status": "registration_established",
        "stroke_coordinate_space": (
            "original_pdf_render_pixels"
        ),
        "render_size": [160, 100],
        "job_id": "test",
        "samples": [
            {
                "line_index": 0,
                "text": "H₂",
                "status": "near_identity_candidate",
                "points": 5,
                "stroke_probe": {
                    "status": "stroke_candidates",
                    "approximate_word_box": [
                        28, 28, 74, 56
                    ],
                    "roi": [
                        20, 20, 85, 65
                    ],
                    "candidates": [
                        {
                            "bbox": [
                                55, 52, 72, 54
                            ],
                            "touches_roi_boundary": False,
                        }
                    ],
                },
            }
        ],
    }

    return gray, report


class PaddleSpanPackTests(unittest.TestCase):

    def test_split_and_preserve_source(self):

        g, r = fixture()

        before = g.copy()

        rows, skipped = make_rows(
            g,
            r,
        )

        self.assertEqual(
            skipped,
            [],
        )

        meta, tiles = rows[0]

        self.assertEqual(
            meta["crop_status"],
            "isolated_crop_candidate",
        )

        self.assertEqual(
            meta["removed_ink_pixels"],
            34,
        )

        self.assertEqual(
            [
                info["role"]
                for info, _ in tiles
            ],
            [
                "control",
                "prefix",
                "selected",
                "suffix",
            ],
        )

        self.assertEqual(
            tiles[-1][0]["ink_pixels"],
            0,
        )

        np.testing.assert_array_equal(
            g,
            before,
        )

        self.assertEqual(
            tiles[0][1].shape[1],
            sum(
                a.shape[1]
                for _, a in tiles[1:]
            ),
        )

    def test_missing_registration_skips(self):

        g, r = fixture()

        r["samples"][0]["status"] = (
            "local_support_missing"
        )

        rows, skipped = make_rows(
            g,
            r,
        )

        self.assertFalse(rows)

        self.assertEqual(
            len(skipped),
            1,
        )

    def test_boundary_ink_flag(self):

        g, r = fixture()

        g[30:48, 20:30] = 0

        rows, _ = make_rows(
            g,
            r,
        )

        self.assertEqual(
            rows[0][0]["crop_status"],
            "edge_ink_unresolved",
        )

    def test_reject_malformed_coordinates(self):

        g, r = fixture()

        r["samples"][0]["stroke_probe"][
            "candidates"
        ][0]["bbox"] = [
            1, 1, 10, 2
        ]

        with self.assertRaisesRegex(
            ValueError,
            "Stroke outside ROI",
        ):
            make_rows(
                g,
                r,
            )

    def test_atlas_tiles_exact(self):

        g, r = fixture()

        rows, _ = make_rows(
            g,
            r,
        )

        pages, entries = build_atlas(
            rows
        )

        for tile, (
            _,
            expected,
        ) in zip(
            entries[0]["tiles"],
            rows[0][1],
        ):
            x, y, a, b = tile["atlas_box"]

            np.testing.assert_array_equal(
                pages[0][y:b, x:a],
                expected,
            )

    def test_pack_pdf_hash_and_no_overwrite(self):

        g, r = fixture()

        with pymupdf.open() as doc:

            page = doc.new_page(
                width=80,
                height=50,
            )

            page.insert_image(
                page.rect,
                pixmap=pymupdf.Pixmap(
                    pymupdf.csGRAY,
                    160,
                    100,
                    g.tobytes(),
                    False,
                ),
            )

            source = doc.tobytes()

        r["pdf_sha256"] = (
            hashlib.sha256(
                source
            ).hexdigest()
        )

        with tempfile.TemporaryDirectory() as tmp:

            out = Path(tmp) / "out"

            result = pack(
                source,
                r,
                out,
            )

            self.assertEqual(
                result["candidates"],
                1,
            )

            m = json.loads(
                (
                    out / "manifest.json"
                ).read_text()
            )

            self.assertEqual(
                hashlib.sha256(
                    (
                        out / "atlas.pdf"
                    ).read_bytes()
                ).hexdigest(),
                m["atlas_pdf_sha256"],
            )

            with pymupdf.open(
                out / "atlas.pdf"
            ) as doc:

                pix = doc[0].get_pixmap(
                    matrix=pymupdf.Matrix(
                        2,
                        2,
                    ),
                    colorspace=pymupdf.csGRAY,
                )

                self.assertEqual(
                    [
                        pix.width,
                        pix.height,
                    ],
                    m["atlas_pages"][0]["size"],
                )

            with self.assertRaises(
                FileExistsError
            ):
                pack(
                    source,
                    r,
                    out,
                )

            r["pdf_sha256"] = "bad"

            with self.assertRaisesRegex(
                ValueError,
                "SHA-256 mismatch",
            ):
                pack(
                    source,
                    r,
                    Path(tmp) / "other",
                )


if __name__ == "__main__":
    unittest.main()
