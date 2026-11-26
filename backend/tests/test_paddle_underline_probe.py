import unittest
import tempfile
import hashlib

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pymupdf

from backend.paddle_underline_probe import (
    run,
    token_strokes,
)


class PaddleUnderlineProbeTests(unittest.TestCase):

    def setUp(self):

        self.gray = np.full(
            (160, 200),
            255,
            np.uint8,
        )

        self.sample = {
            "status": "near_identity_candidate",
            "points": 5,
            "max_displacement_px": .4,
            "bbox": [
                20, 20, 70, 40
            ],
        }

        self.gray[48:65, 55:60] = 0
        self.gray[48:65, 75:80] = 0
        self.gray[48:65, 105:110] = 0

        self.gray[73:76, 100:130] = 0

    def test_original_pixel_candidate(self):

        before = self.gray.copy()

        r = token_strokes(
            self.gray,
            self.sample,
        )

        self.assertEqual(
            r["approximate_word_box"],
            [40, 40, 140, 80],
        )

        self.assertEqual(
            r["status"],
            "stroke_candidates",
        )

        self.assertIn(
            [100, 73, 130, 76],
            [
                s["bbox"]
                for s in r["candidates"]
            ],
        )

        np.testing.assert_array_equal(
            self.gray,
            before,
        )

    def test_missing_support(self):

        with patch(
            "backend.paddle_underline_probe.detect_horizontal_strokes"
        ) as detector:

            r = token_strokes(
                self.gray,
                {
                    **self.sample,
                    "status": "local_support_missing",
                    "points": 0,
                },
            )

            self.assertEqual(
                r["status"],
                "registration_unresolved",
            )

            detector.assert_not_called()

    def test_no_stroke(self):

        self.gray[:] = 255

        self.assertEqual(
            token_strokes(
                self.gray,
                self.sample,
            )["status"],
            "no_stroke_candidates",
        )

    def test_multiple_candidates_not_collapsed(self):

        self.gray[78:80, 60:90] = 0

        r = token_strokes(
            self.gray,
            self.sample,
        )

        self.assertGreaterEqual(
            len(r["candidates"]),
            2,
        )

        self.assertNotIn(
            "span",
            r,
        )

    def test_cache_only_render(self):

        with tempfile.TemporaryDirectory() as tmp:

            pdf = Path(tmp) / "test.pdf"

            with pymupdf.open() as doc:

                page = doc.new_page(
                    width=100,
                    height=80,
                )

                page.draw_line(
                    (50, 37),
                    (65, 37),
                    width=1.5,
                )

                doc.save(pdf)

            sha = hashlib.sha256(
                pdf.read_bytes()
            ).hexdigest()

            reg = {
                "status": "registration_established",
                "size": [100, 80],
                "samples": [
                    self.sample
                ],
            }

            with patch(
                "backend.paddle_underline_probe.inspect",
                return_value=reg,
            ) as call:

                result = run(
                    pdf,
                    sha,
                    Path(tmp) / "jobs.sqlite3",
                    "job",
                    matches=["test"],
                )

                self.assertFalse(
                    call.call_args.kwargs[
                        "fetch_images"
                    ]
                )

                self.assertEqual(
                    result["render_size"],
                    [200, 160],
                )

                self.assertEqual(
                    result["samples"][0]["stroke_probe"]["status"],
                    "stroke_candidates",
                )

    def test_invalid_displacement_and_box(self):

        for update in (
            {
                "max_displacement_px": float("nan")
            },
            {
                "max_displacement_px": 2.1
            },
            {
                "bbox": [
                    20, 20, 101, 40
                ]
            },
        ):
            with self.subTest(
                update=update
            ), self.assertRaises(ValueError):

                token_strokes(
                    self.gray,
                    {
                        **self.sample,
                        **update,
                    },
                )


if __name__ == "__main__":
    unittest.main()
