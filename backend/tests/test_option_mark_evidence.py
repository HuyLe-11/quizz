import tempfile
import unittest

from pathlib import Path

import fitz

from backend.engine.option_mark_evidence import (
    paddle_recognition_items,
    enclosed_anchor_hits,
    infer_repeated_columns,
)

from backend.engine.pdf_tools import (
    RedMarkConfig,
    detect_red_mark_pdf,
)


def detail_data(texts, boxes, **extra):

    return {
        "pruned_result": {
            "width": 400,
            "height": 300,
            "rec_texts": texts,
            "rec_boxes": boxes,
            **extra,
        }
    }


def fixture():

    lines = []
    items = []
    components = []

    for i in range(4):

        y = 30 + i * 45

        lines.append({
            "text": (
                f"Question {i + 1}. "
                "A. 1 B. 2 C. 3 D. 4"
            ),

            "bbox": (
                10,
                y - 5,
                340,
                y + 25,
            ),
        })

        for j, x in enumerate(
            (40, 100, 180, 280)
        ):

            if i == j:

                components.append(
                    (
                        x - 4,
                        y - 4,
                        16,
                        18,
                        100,
                    )
                )

            else:

                items.append({
                    "text": "ABCD"[j] + ".",

                    "bbox": (
                        x,
                        y,
                        x + 8,
                        y + 10,
                    ),
                })

    return lines, items, components


class OptionMarkEvidenceTests(unittest.TestCase):

    def setUp(self):

        self.doc = fitz.open()

        self.addCleanup(
            self.doc.close
        )

        self.page = self.doc.new_page(
            width=400,
            height=300,
        )

    def test_recognition_scores_and_coordinates(self):

        data = detail_data(
            [
                "A.",
                "B.",
                "C.",
            ],
            [
                [20, 40, 30, 50],
                [60, 40, 70, 50],
                [100, 40, 110, 50],
            ],

            rec_scores=[
                0.9,
                0.2,
                float("nan"),
            ],
        )

        self.assertEqual(
            paddle_recognition_items(
                self.page,
                data,
            ),
            [
                {
                    "text": "A.",
                    "bbox": (20, 40, 30, 50),
                }
            ],
        )

    def test_layout_blocks_are_not_recognition(self):

        data = {
            "pruned_result": {
                "width": 400,
                "height": 300,
                "parsing_res_list": [
                    {
                        "block_content": "A.",
                        "block_bbox": [
                            20, 40, 30, 50
                        ],
                    }
                ],
            }
        }

        self.assertEqual(
            paddle_recognition_items(
                self.page,
                data,
            ),
            [],
        )

    def test_unsafe_transform(self):

        data = detail_data(
            ["A."],
            [[20, 40, 30, 50]],

            model_settings={
                "use_doc_unwarping": True,
            },
        )

        self.assertEqual(
            paddle_recognition_items(
                self.page,
                data,
            ),
            [],
        )

    def test_conflicting_representation(self):

        data = detail_data(
            ["A."],
            [[20, 40, 30, 50]],
        )

        data["raw"] = {
            "prunedResult": detail_data(
                ["A."],
                [[80, 40, 90, 50]],
            )["pruned_result"]
        }

        self.assertEqual(
            paddle_recognition_items(
                self.page,
                data,
            ),
            [],
        )

    def test_anchor_boundary(self):

        self.assertEqual(
            enclosed_anchor_hits(
                {
                    "A": (20, 20, 28, 30)
                },
                (0, 0, 100, 32),
                ["A"],
                [
                    (16, 16, 16, 18, 100)
                ],
                1,
                24,
            ),
            [],
        )

    def infer(
        self,
        lines,
        items,
        components,
    ):

        return infer_repeated_columns(
            lines,
            items,
            list("ABCD"),
            components,
            1,
            24,
        )

    def test_repeated_columns(self):

        lines, items, components = fixture()

        result = self.infer(
            lines,
            items,
            components,
        )

        self.assertEqual(
            [
                result[i][0]
                for i in range(1, 5)
            ],
            list("ABCD"),
        )

    def test_missing_or_extra_mark_rejected(self):

        lines, items, components = fixture()

        self.assertEqual(
            self.infer(
                lines,
                items,
                components[:-1],
            ),
            {},
        )

        self.assertEqual(
            self.infer(
                lines,
                items,
                components + [
                    (96, 26, 16, 18, 100)
                ],
            ),
            {},
        )

    def test_mixed_layout_rejected(self):

        lines, items, components = fixture()

        lines[0]["text"] = (
            "Question 1. A. 1 B. 2\n"
            "C. 3 D. 4"
        )

        self.assertEqual(
            self.infer(
                lines,
                items,
                components,
            ),
            {},
        )

    def test_native_and_scan_integration(self):

        with tempfile.TemporaryDirectory() as folder:

            path = Path(folder) / "scan.pdf"

            self.page.draw_oval(
                fitz.Rect(
                    172,
                    66,
                    190,
                    84,
                ),

                color=(1, 0, 0),

                width=2,
            )

            self.doc.save(path)

            coarse = {
                "pruned_result": {
                    "width": 400,
                    "height": 300,

                    "parsing_res_list": [
                        {
                            "block_content": (
                                "Question 1. "
                                "A. 1 B. 2 C. 3 D. 4"
                            ),

                            "block_bbox": [
                                10, 30, 350, 100
                            ],
                        }
                    ],
                }
            }

            boxes = [
                [10, 30, 160, 43],
                [40, 70, 49, 81],
                [100, 70, 109, 81],
                [180, 70, 189, 81],
                [280, 70, 289, 81],
            ]

            detail = detail_data(
                [
                    "Question 1. Select",
                    "A.",
                    "B.",
                    "C.",
                    "D.",
                ],
                boxes,
            )

            config = RedMarkConfig()

            off = detect_red_mark_pdf(
                path,
                1,
                1,
                list("ABCD"),
                coarse,
                config,
            )

            self.assertEqual(
                off["status"],
                "not_found",
            )

            on = detect_red_mark_pdf(
                path,
                1,
                1,
                list("ABCD"),
                coarse,
                config,

                detail_page_ocr=detail,
            )

            self.assertEqual(
                (
                    on["detected_answer"],
                    on["status"],
                ),
                ("C", "uncertain"),
            )

            regions = detail_data(
                [
                    "Question 1. Select",
                    "A. 10",
                    "B. 20",
                    "C. 30",
                    "D. 40",
                ],

                [
                    boxes[0],
                    *[
                        [
                            box[0],
                            box[1],
                            box[2] + 28,
                            box[3],
                        ]
                        for box in boxes[1:]
                    ],
                ],
            )

            on = detect_red_mark_pdf(
                path,
                1,
                1,
                list("ABCD"),
                coarse,
                config,

                detail_page_ocr=regions,
            )

            self.assertEqual(
                (
                    on["detected_answer"],
                    on["status"],
                ),
                ("C", "uncertain"),
            )


if __name__ == "__main__":
    unittest.main()