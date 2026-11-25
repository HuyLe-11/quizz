"""Regression tests for OCR coordinate preservation."""

import copy
import json
import tempfile
import unittest

from pathlib import Path
from unittest.mock import Mock, patch

import fitz

from backend.engine import paddle_client as client
from backend.engine import paddle_resilient as resilient

from backend.engine.pdf_layout import (
    locate,
    question_segments,
)


# Bounding boxes from the actual vatlli.pdf OCR export.
BOXES = [
    [282, 1160, 3259, 1741],
    [290, 1786, 3719, 2136],
    [297, 2161, 3802, 2625],
    [304, 2668, 3799, 3045],
    [307, 3053, 3802, 3390],
    [304, 3407, 3085, 3736],
    [301, 3753, 3804, 4084],
    [297, 4094, 3794, 4651],
    [276, 5016, 3696, 5245],
]


def geometry(unwarping):
    blocks = [
        {
            "block_label": "text",
            "block_content": f"Câu {i}: Nội dung câu hỏi",
            "block_bbox": box,
        }
        for i, box in enumerate(BOXES, 1)
    ]

    blocks.insert(
        8,
        {
            "block_label": "text",
            "block_content": "D. Phương án cuối của câu 8",
            "block_bbox": [283, 4733, 3807, 5009],
        },
    )

    root = {
        "width": 4000,
        "height": 5364,

        "doc_preprocessor_res": {
            "angle": 0,

            "model_settings": {
                "use_doc_unwarping": unwarping,
                "use_doc_orientation_classify": unwarping,
            },
        },

        "parsing_res_list": blocks,
    }

    return {
        "pruned_result": root,

        "raw": {
            "prunedResult": copy.deepcopy(root),
        },
    }


class PaddleGeometryTests(unittest.TestCase):

    def test_unwarped_geometry_is_rejected(self):
        with fitz.open() as doc:
            page = doc.new_page(
                width=2000,
                height=2682,
            )

            data = geometry(True)

            for number in range(1, 10):
                with self.subTest(number=number):
                    self.assertIsNone(
                        locate(
                            page,
                            number,
                            data,
                        )
                    )

    def test_layout_blocks_if_provider_preserves_coordinates(self):
        # Hypothetical geometry-preserving response,
        # not a modification to stored metadata or
        # evidence of real provider behavior.
        with fitz.open() as doc:
            page = doc.new_page(
                width=2000,
                height=2682,
            )

            data = geometry(False)

            for number in range(1, 10):
                with self.subTest(number=number):
                    self.assertEqual(
                        locate(
                            page,
                            number,
                            data,
                        )["source"],
                        "paddleocr",
                    )

            segments, _ = question_segments(
                doc,
                8,
                0,
                14,
                [data],
            )

            self.assertGreaterEqual(
                segments[0][1][3],
                5009 / 2,
            )

            self.assertEqual(
                segments[0][1][3],
                5016 / 2,
            )

    def test_vl_options_sent_and_actual_metadata_preserved_on_recovery(self):
        submitted = Mock(ok=True)

        submitted.json.return_value = {
            "data": {
                "jobId": "test-job",
            },
        }

        done = Mock(ok=True)

        done.json.return_value = {
            "data": {
                "state": "done",

                "resultUrl": {
                    "jsonUrl": "https://storage.example/result",
                },
            },
        }

        result = Mock(ok=True)

        # Simulate a provider which ignores
        # the requested flags.
        result.text = json.dumps({
            "result": {
                "layoutParsingResults": [
                    {
                        "markdown": {
                            "text": "Câu 1: Nội dung",
                        },

                        "prunedResult": geometry(True)[
                            "pruned_result"
                        ],
                    },
                ],
            },
        })

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"

            path.write_bytes(b"%PDF-test")

            batch = client.PDFBatch(
                path,
                0,
                1,
            )

            with (
                patch.object(
                    resilient.requests,
                    "post",
                    return_value=submitted,
                ) as post,

                patch.object(
                    client,
                    "_get_with_retries",
                    side_effect=[
                        client.PaddleOCRTimeoutError(
                            "deadline"
                        ),
                        done,
                        result,
                    ],
                ) as get,

                self.assertLogs(
                    "quizpdf",
                    level="INFO",
                ) as logs,
            ):
                job_id, pages, _ = (
                    resilient._process_batch(
                        batch,
                        model="PaddleOCR-VL-1.6",
                        job_url="https://ocr.example/jobs",
                        headers={
                            "Authorization": "Bearer test",
                        },
                        timeout=(10.0, 180.0),
                        poll_timeout=300,
                        recovery=120,
                        result_timeout=180,
                    )
                )

            post.assert_called_once()

            self.assertEqual(
                json.loads(
                    post.call_args.kwargs["data"][
                        "optionalPayload"
                    ]
                ),
                {
                    "useDocOrientationClassify": False,
                    "useDocUnwarping": False,
                },
            )

            self.assertEqual(
                job_id,
                "test-job",
            )

            self.assertEqual(
                get.call_args_list[0].args[0],
                get.call_args_list[1].args[0],
            )

            self.assertNotIn(
                "headers",
                get.call_args_list[2].kwargs,
            )

            self.assertTrue(
                pages[0]["pruned_result"][
                    "doc_preprocessor_res"
                ]["model_settings"][
                    "use_doc_unwarping"
                ]
            )

            self.assertTrue(
                any(
                    "unwarping=True" in line
                    for line in logs.output
                )
            )

    def test_other_model_payload_unchanged(self):
        self.assertEqual(
            resilient._optional_payload(
                "PP-OCRv6"
            ),
            {},
        )


if __name__ == "__main__":
    unittest.main()