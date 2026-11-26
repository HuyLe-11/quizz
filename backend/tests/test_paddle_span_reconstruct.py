import hashlib
import json
import unittest

from backend.paddle_span_reconstruct import reconstruct


def fixture():

    tiles = []

    for i, (role, source, ink) in enumerate([
        ("control", [0, 0, 30, 20], 100),
        ("prefix", [0, 0, 10, 20], 40),
        ("selected", [10, 0, 29, 20], 50),
        ("suffix", [29, 0, 30, 20], 0),
    ]):
        x = 40 * i + 5

        tiles.append({
            "role": role,
            "source_box": source,
            "ink_pixels": ink,
            "masked": role == "selected",
            "atlas_box": [
                x,
                5,
                x + source[2] - source[0],
                25,
            ],
            "cell_box": [
                40 * i,
                0,
                40 * (i + 1),
                30,
            ],
        })

    m = {
        "scope": "diagnostic_only",
        "source_sha256": "a" * 64,
        "source_job_id": "source",
        "render_size": [100, 100],
        "atlas_pdf_sha256": "b" * 64,
        "atlas_pdf_bytes": 100,
        "atlas_pages": [
            {
                "page": 1,
                "size": [160, 30],
            }
        ],
        "skipped": [],
        "candidates": [
            {
                "candidate_id": "c1",
                "page": 1,
                "line_index": 0,
                "text": "H₂O",
                "stroke_box": [
                    10, 15, 29, 16
                ],
                "removed_ink_pixels": 10,
                "crop_status": "isolated_crop_candidate",
                "tiles": tiles,
            }
        ],
    }

    lines = [
        {
            "index": i,
            "text": text,
            "bbox": [
                40 * i + 4,
                4,
                40 * i + 16,
                26,
            ],
            "score": .99,
        }
        for i, text in enumerate([
            "H₂O",
            "H",
            "₂O",
        ])
    ]

    r = {
        "atlas_sha256": "b" * 64,
        "atlas_bytes": 100,
        "atlas_sizes": [
            [160, 30]
        ],
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
                "provider_size": [160, 30],
                "line_count": 3,
                "lines": lines,
            }
        ],
    }

    return m, r


def run(m, r):

    data = json.dumps(m).encode()

    r["manifest_sha256"] = (
        hashlib.sha256(data).hexdigest()
    )

    return reconstruct(
        data,
        json.dumps(r).encode(),
    )


class SpatialReconstructionTests(unittest.TestCase):

    def test_exact_offsets_and_unregistered_status(self):

        m, r = fixture()

        r["pages"][0]["lines"].reverse()

        out = run(m, r)

        c = out["candidates"][0]

        self.assertEqual(
            c["span"],
            [1, 3],
        )

        self.assertEqual(
            c["status"],
            "exact_text_candidate",
        )

        self.assertEqual(
            c["tiles"]["suffix"]["status"],
            "empty_ink",
        )

        self.assertEqual(
            out["coordinate_space"],
            "atlas_unregistered",
        )

    def test_missing_nonempty_suffix(self):

        m, r = fixture()

        m["candidates"][0]["tiles"][3][
            "ink_pixels"
        ] = 3

        m["candidates"][0]["tiles"][0][
            "ink_pixels"
        ] += 3

        self.assertEqual(
            run(m, r)["candidates"][0]["status"],
            "unresolved_crop",
        )

    def test_empty_recognition_with_ink(self):

        m, r = fixture()

        r["pages"][0]["lines"][2]["text"] = ""

        c = run(m, r)["candidates"][0]

        self.assertEqual(
            c["tiles"]["selected"]["status"],
            "missing_text_with_ink",
        )

        self.assertIsNone(
            c["span"]
        )

    def test_case_sensitive(self):

        m, r = fixture()

        r["pages"][0]["lines"][2]["text"] = "₂o"

        self.assertEqual(
            run(m, r)["candidates"][0]["status"],
            "reconstruction_mismatch",
        )

    def test_control_mismatch(self):

        m, r = fixture()

        r["pages"][0]["lines"][0]["text"] = "H2O"

        self.assertEqual(
            run(m, r)["candidates"][0]["status"],
            "control_mismatch",
        )

    def test_cross_cell_box_rejected(self):

        m, r = fixture()

        r["pages"][0]["lines"][0]["bbox"] = [
            20, 4, 50, 26
        ]

        with self.assertRaisesRegex(
            ValueError,
            "assigned uniquely",
        ):
            run(m, r)

    def test_multiple_lines_unresolved(self):

        m, r = fixture()

        page = r["pages"][0]

        page["lines"].append({
            **page["lines"][2],
            "index": 3,
        })

        page["line_count"] = 4

        c = run(m, r)["candidates"][0]

        self.assertEqual(
            c["tiles"]["selected"]["status"],
            "multiple_ocr_lines",
        )

        self.assertIsNone(
            c["span"]
        )

    def test_wrong_hash_and_size(self):

        m, r = fixture()

        run(m, r)

        with self.assertRaisesRegex(
            ValueError,
            "identity",
        ):
            reconstruct(
                json.dumps(m).encode() + b" ",
                json.dumps(r).encode(),
            )

        r["pages"][0]["provider_size"] = [
            161, 30
        ]

        with self.assertRaisesRegex(
            ValueError,
            "geometry",
        ):
            run(m, r)

    def test_ink_accounting(self):

        m, r = fixture()

        m["candidates"][0][
            "removed_ink_pixels"
        ] = 9

        with self.assertRaisesRegex(
            ValueError,
            "Ink accounting",
        ):
            run(m, r)

    def test_combining_mark_boundary(self):

        m, r = fixture()

        m["candidates"][0]["text"] = "a\u0301b"

        for line, text in zip(
            r["pages"][0]["lines"],
            [
                "a\u0301b",
                "a",
                "\u0301b",
            ],
        ):
            line["text"] = text

        self.assertEqual(
            run(m, r)["candidates"][0]["status"],
            "unsupported_span_boundary",
        )

    def test_edge_warning_retained(self):

        m, r = fixture()

        m["candidates"][0]["crop_status"] = (
            "edge_ink_unresolved"
        )

        c = run(m, r)["candidates"][0]

        self.assertEqual(
            c["status"],
            "exact_text_candidate",
        )

        self.assertEqual(
            c["crop_status"],
            "edge_ink_unresolved",
        )


if __name__ == "__main__":
    unittest.main()
