import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pymupdf

from backend.paddle_span_pack import (
    pack,
    make_rows,
    build_atlas,
)

from backend.paddle_visual_evidence import (
    analyze,
    group_summary,
    stroke_features,
    verify_tiles,
    sha,
)

from backend.tests.test_paddle_span_pack import fixture


class VisualEvidenceTests(unittest.TestCase):

    def test_below_body_and_internal_stroke(self):

        ink = np.zeros(
            (40, 80),
            dtype=bool,
        )

        ink[5:25, 10:25] = True
        ink[5:25, 40:60] = True
        ink[27:29, 40:60] = True

        before = ink.copy()

        self.assertEqual(
            stroke_features(
                ink,
                (40, 27, 60, 29),
            )["visual_status"],
            "below_body_candidate",
        )

        self.assertEqual(
            stroke_features(
                ink,
                (40, 15, 60, 17),
            )["visual_status"],
            "geometry_unresolved",
        )

        np.testing.assert_array_equal(
            ink,
            before,
        )

    def test_no_baseline_support(self):

        ink = np.zeros(
            (40, 80),
            dtype=bool,
        )

        ink[5:25, 40:60] = True
        ink[27:29, 40:60] = True

        result = stroke_features(
            ink,
            (40, 27, 60, 29),
        )

        self.assertIsNone(
            result["baseline_gap_px"]
        )

        self.assertEqual(
            result["visual_status"],
            "geometry_unresolved",
        )

    def test_invalid_box(self):

        with self.assertRaises(ValueError):

            stroke_features(
                np.zeros(
                    (40, 80),
                    dtype=bool,
                ),
                (40, 27, 81, 29),
            )

    def test_tile_pixels_and_ink(self):

        gray, report = fixture()

        rows, _ = make_rows(
            gray,
            report,
        )

        pages, entries = build_atlas(
            rows
        )

        self.assertEqual(
            verify_tiles(
                pages[0],
                entries[0],
            )["visual_status"],
            "below_body_candidate",
        )

        a, b, _, _ = (
            entries[0]["tiles"][2]["atlas_box"]
        )

        pages[0][b, a] ^= 1

        with self.assertRaisesRegex(
            ValueError,
            "Tile pixel or ink mismatch",
        ):
            verify_tiles(
                pages[0],
                entries[0],
            )

    def test_competing_visual_candidate_blocks_text_choice(self):

        row = {
            "line_index": 0,
            "text": "H₂O",
            "control_source_box": [
                0, 0, 30, 20
            ],
            "visual_status": "below_body_candidate",
            "status": "exact_text_candidate",
            "crop_status": "isolated_crop_candidate",
            "candidate_id": "c1",
            "span": [1, 3],
        }

        other = {
            **row,
            "candidate_id": "c2",
            "status": "reconstruction_mismatch",
            "span": None,
        }

        self.assertEqual(
            group_summary(
                [row, other]
            )[0]["status"],
            "competing_visual_candidates",
        )

        self.assertEqual(
            group_summary(
                [other]
            )[0]["status"],
            "visual_candidate_text_unresolved",
        )

        self.assertEqual(
            group_summary([
                {
                    **row,
                    "crop_status": "edge_ink_unresolved",
                }
            ])[0]["status"],
            "visual_candidate_crop_unresolved",
        )

    def test_end_to_end_and_hash(self):

        gray, report = fixture()

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
                    gray.tobytes(),
                    False,
                ),
            )

            source = doc.tobytes()

        report["pdf_sha256"] = sha(
            source
        )

        with tempfile.TemporaryDirectory() as temp:

            out = Path(temp) / "pack"

            pack(
                source,
                report,
                out,
            )

            mb = (
                out / "manifest.json"
            ).read_bytes()

            pdf = (
                out / "atlas.pdf"
            ).read_bytes()

            m = json.loads(mb)

            size = (
                m["atlas_pages"][0]["size"]
            )

            lines = [
                {
                    "index": i,
                    "text": text,
                    "bbox": tile["atlas_box"],
                    "score": .99,
                }
                for i, (
                    tile,
                    text,
                ) in enumerate(
                    zip(
                        m["candidates"][0]["tiles"],
                        ["H₂", "H", "₂"],
                    )
                )
            ]

            r = {
                "manifest_sha256": sha(mb),
                "atlas_sha256": sha(pdf),
                "atlas_bytes": len(pdf),
                "atlas_sizes": [size],
                "candidate_count": 1,
                "page_count": 1,
                "status": "recognized_unregistered",
                "model": "PP-OCRv6",
                "requested_options": {
                    "returnWordBox": True,
                    "useDocOrientationClassify": False,
                    "useDocUnwarping": False,
                },
                "job_id": "atlas",
                "pages": [
                    {
                        "page": 1,
                        "provider_size": size,
                        "line_count": len(lines),
                        "lines": lines,
                    }
                ],
            }

            rb = json.dumps(r).encode()

            result = analyze(
                pdf,
                mb,
                rb,
            )

            self.assertEqual(
                result["groups"][0]["status"],
                "unique_visual_text_candidate",
            )

            self.assertEqual(
                result["groups"][0]["span"],
                [1, 2],
            )

            self.assertEqual(
                result["coordinate_space"],
                "atlas_unregistered",
            )

            with self.assertRaisesRegex(
                ValueError,
                "Atlas PDF identity mismatch",
            ):
                analyze(
                    pdf + b"bad",
                    mb,
                    rb,
                )


if __name__ == "__main__":
    unittest.main()
