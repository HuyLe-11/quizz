import tempfile
import unittest
from pathlib import Path

import fitz

from backend.engine.pdf_layout import (
    locate,
    ocr_lines,
    question_segments,
)

from backend.engine.pdf_tools import (
    RedMarkConfig,
    crop_question_pdf,
)


def nested_page():
    return {
        "pruned_result": {
            "width": 1200,
            "height": 1600,
            "overall_ocr_res": {
                "rec_texts": [
                    "Câu",
                    "1:",
                    "Đề bài",
                    "A. Một",
                    "B. Hai",
                    "Câu 2: Tiếp theo",
                    "A. Ba",
                ],
                "rec_boxes": [
                    [80, 200, 130, 230],
                    [140, 201, 165, 231],
                    [180, 200, 500, 230],
                    [100, 260, 300, 290],
                    [100, 350, 300, 397],
                    [80, 400, 550, 430],
                    [100, 470, 300, 500],
                ],
            },
            "parsing_res_list": [
                {
                    "block_content": (
                        "Câu 1: Đề bài\n"
                        "Câu 2: Tiếp theo"
                    ),
                    "block_bbox": [
                        80,
                        200,
                        550,
                        500,
                    ],
                },
            ],
        },
    }


class ScanQuestionLocalizationTests(
    unittest.TestCase
):
    def setUp(self):
        self.doc = fitz.open()

        self.addCleanup(
            self.doc.close
        )

        self.page = self.doc.new_page(
            width=600,
            height=800,
        )

    def test_nested_lines_and_split_heading(self):
        data = nested_page()

        found = locate(
            self.page,
            1,
            data,
        )

        self.assertEqual(
            found["source"],
            "paddleocr",
        )

        self.assertEqual(
            found["question_start_y"],
            100,
        )

        self.assertEqual(
            found["next_start_y"],
            200,
        )

        self.assertEqual(
            set(found["options"]),
            {"A", "B"},
        )

    def test_dense_crop_keeps_final_option(self):
        segments, found = question_segments(
            self.doc,
            1,
            0,
            14,
            [nested_page()],
        )

        self.assertGreaterEqual(
            segments[0][1][3],
            found["options"]["B"][3],
        )

        self.assertEqual(
            segments[0][1][3],
            200,
        )

    def test_fragments_on_different_rows_not_joined(self):
        data = nested_page()

        data["pruned_result"][
            "overall_ocr_res"
        ]["rec_boxes"][1] = [
            140,
            310,
            165,
            340,
        ]

        self.assertIsNone(
            locate(
                self.page,
                1,
                data,
            )
        )

    def test_duplicate_question_number_is_ambiguous(self):
        data = {
            "width": 1200,
            "height": 1600,
            "rec_texts": [
                "Câu 1: Một",
                "Câu 1: Hai",
            ],
            "rec_boxes": [
                [100, 200, 400, 240],
                [100, 400, 400, 440],
            ],
        }

        self.assertIsNone(
            locate(
                self.page,
                1,
                data,
            )
        )

    def test_rotation_and_unwarping_rejected(self):
        cases = (
            {
                "angle": 90,
            },
            {
                "model_settings": {
                    "use_doc_unwarping": True,
                },
            },
        )

        for settings in cases:
            with self.subTest(
                settings=settings
            ):
                data = nested_page()

                data["pruned_result"][
                    "doc_preprocessor_res"
                ] = settings

                self.assertEqual(
                    ocr_lines(
                        self.page,
                        data,
                    ),
                    [],
                )

    def test_incompatible_dimensions_rejected(self):
        data = nested_page()

        data["pruned_result"]["width"] = 2000

        self.assertEqual(
            ocr_lines(
                self.page,
                data,
            ),
            [],
        )

    def test_out_of_bounds_rejected(self):
        data = nested_page()

        root = data["pruned_result"]

        root.pop("parsing_res_list")

        root["overall_ocr_res"][
            "rec_boxes"
        ][0] = [
            80,
            200,
            5000,
            230,
        ]

        self.assertEqual(
            ocr_lines(
                self.page,
                data,
            ),
            [],
        )

    def test_pdf_text_preferred_when_ocr_rotated(self):
        self.page.insert_text(
            (40, 60),
            "Question 1: Native text",
        )

        data = nested_page()

        data["pruned_result"][
            "doc_preprocessor_res"
        ] = {
            "angle": 90,
        }

        self.assertEqual(
            locate(
                self.page,
                1,
                data,
            )["source"],
            "pymupdf",
        )

    def test_real_crop_with_simulated_nested_ocr_on_image_only_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)

            self.page.draw_rect(
                fitz.Rect(
                    40,
                    100,
                    250,
                    195,
                ),
                color=(0, 0, 0),
            )

            scan_path = (
                folder / "scan.pdf"
            )

            with fitz.open() as scanned:
                page = scanned.new_page(
                    width=600,
                    height=800,
                )

                page.insert_image(
                    page.rect,
                    pixmap=self.page.get_pixmap(),
                )

                scanned.save(scan_path)

            asset = crop_question_pdf(
                scan_path,
                1,
                1,
                folder / "crop.png",
                ocr_pages=[nested_page()],
                config=RedMarkConfig(
                    dpi=72
                ),
            )

            self.assertEqual(
                asset["source"],
                "paddleocr",
            )

            self.assertEqual(
                asset["crop_status"],
                "success",
            )

            self.assertLess(
                asset["crop_coordinates"][0]["bbox"][3],
                800,
            )

            with fitz.open(
                asset["image_path"]
            ) as cropped:
                self.assertLess(
                    cropped[0].rect.height,
                    800,
                )


if __name__ == "__main__":
    unittest.main()