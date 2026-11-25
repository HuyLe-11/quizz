import copy
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf

from backend.engine import (
    paddle_resilient as resilient,
)

from backend.engine import (
    registered_ocr_context as ctx,
)

from backend.engine.ocr_job_journal import (
    OCRJobJournal,
)

from backend.engine.registered_mark_detection import (
    PageMarkResult,
)


class RegisteredOCRContextTests(unittest.TestCase):

    def setUp(self):

        tmp = tempfile.TemporaryDirectory()

        self.addCleanup(tmp.cleanup)

        self.journal = OCRJobJournal(
            "a" * 64,
            Path(tmp.name) / "jobs.sqlite3",
        )

        self.batch = SimpleNamespace(
            start_page=1,
            page_count=2,
        )

        self.key = self.journal.batch_key(
            1,
            2,
            "PP-OCRv6",
        )

        self.pages = [
            {
                "pruned_result": {
                    "rec_boxes": [[1, 1, 5, 5]]
                }
            },
            {
                "pruned_result": {
                    "rec_boxes": [[2, 2, 6, 6]]
                }
            },
        ]

        self.info = {
            "pages": [
                {
                    "width": 128,
                    "height": 256,
                },
                {
                    "width": 256,
                    "height": 128,
                },
            ]
        }

        self.doc = pymupdf.open()

        self.addCleanup(self.doc.close)

        for _ in range(3):

            self.doc.new_page()

        self.cache = object()

        self.labels = {
            1: "AB"
        }

    def finish(self):

        claim = self.journal.reserve(
            self.key
        ).record.claim_id

        self.journal.accept(
            self.key,
            claim,
            "job-1",
        )

        self.journal.finish_with_result(
            self.key,
            claim,
            "job-1",
            self.pages,
            self.info,
            page_count=2,
        )

        return claim

    def process(self):

        return resilient._process_batch(
            self.batch,
            model="PP-OCRv6",
            job_url="https://example.invalid",
            headers={},
            timeout=(1, 1),
            poll_timeout=1,
            recovery=1,
            result_timeout=1,
            journal=self.journal,
        )

    def detect(
        self,
        capture,
        selected,
        number=1,
    ):

        return capture.detect_page(
            self.doc[number],
            selected,
            self.labels,
            cache=self.cache,
        )

    def test_cached_batch_exact_offset_and_dimensions(self):

        claim = self.finish()

        with (
            ctx.capture_registered_pages() as cap,

            patch.object(
                resilient,
                "_submit_job",
            ) as submit,

            patch.object(
                ctx,
                "acquire_images",
                return_value=object(),
            ) as acquire,

            patch.object(
                ctx,
                "detect_registered_marks",
                return_value=PageMarkResult(
                    "processed"
                ),
            ) as detect,
        ):

            _, pages, _ = self.process()

            pages[1][
                "source_page_number"
            ] = 3

            self.assertEqual(
                self.detect(
                    cap,
                    pages[1],
                    2,
                ).status,
                "processed",
            )

            ref = acquire.call_args.args[1]

            self.assertEqual(
                (
                    ref.start_page,
                    ref.page_offset,
                    ref.claim_id,
                ),
                (1, 1, claim),
            )

            self.assertEqual(
                detect.call_args.kwargs,
                {
                    "width": 256,
                    "height": 128,
                },
            )

            self.assertEqual(
                detect.call_args.args[1],
                self.pages[1]["pruned_result"],
            )

            submit.assert_not_called()

    def test_fresh_batch_hook(self):

        with (
            ctx.capture_registered_pages() as cap,

            patch.object(
                resilient,
                "_submit_job",
                return_value="job-1",
            ) as submit,

            patch.object(
                resilient,
                "_with_recovery",
                side_effect=[
                    "url",
                    SimpleNamespace(text="{}"),
                ],
            ),

            patch.object(
                resilient.client,
                "_normalize_pages",
                return_value=(
                    self.pages,
                    self.info,
                ),
            ),

            patch.object(
                ctx,
                "acquire_images",
                return_value=None,
            ) as acquire,
        ):

            _, pages, _ = self.process()

            self.assertEqual(
                self.detect(
                    cap,
                    pages[0],
                ).reason,
                "ocr_images_unavailable",
            )

            acquire.assert_called_once()

            submit.assert_called_once()

    def test_unselected_fallback_page_and_copy_rejected(self):

        self.finish()

        with (
            ctx.capture_registered_pages() as cap,

            patch.object(
                ctx,
                "acquire_images",
            ) as acquire,
        ):

            _, pages, _ = self.process()

            # Simulate a mixed final batch:
            # first page retained from VL.

            selected = [
                {
                    "markdown_text": "VL"
                },
                pages[1],
            ]

            self.assertEqual(
                self.detect(
                    cap,
                    selected[0],
                ).reason,
                "no_selected_ppocr_page",
            )

            self.assertEqual(
                self.detect(
                    cap,
                    copy.deepcopy(pages[1]),
                    2,
                ).reason,
                "no_selected_ppocr_page",
            )

            self.assertEqual(
                self.detect(
                    cap,
                    selected[1],
                ).reason,
                "page_number_mismatch",
            )

            acquire.assert_not_called()

    def test_mutated_page_rejected(self):

        self.finish()

        with (
            ctx.capture_registered_pages() as cap,

            patch.object(
                ctx,
                "acquire_images",
            ) as acquire,
        ):

            _, pages, _ = self.process()

            pages[0][
                "pruned_result"
            ][
                "rec_boxes"
            ] = [
                [3, 3, 9, 9]
            ]

            self.assertEqual(
                self.detect(
                    cap,
                    pages[0],
                ).reason,
                "ocr_binding_changed",
            )

            acquire.assert_not_called()

    def test_result_replaced_during_acquisition(self):

        claim = self.finish()

        def changed(*args):

            self.pages[0][
                "pruned_result"
            ][
                "rec_boxes"
            ] = [
                [3, 3, 9, 9]
            ]

            self.journal.finish_with_result(
                self.key,
                claim,
                "job-1",
                self.pages,
                self.info,
                page_count=2,
            )

            return object()

        with (
            ctx.capture_registered_pages() as cap,

            patch.object(
                ctx,
                "acquire_images",
                side_effect=changed,
            ),

            patch.object(
                ctx,
                "detect_registered_marks",
            ) as detect,
        ):

            _, pages, _ = self.process()

            self.assertEqual(
                self.detect(
                    cap,
                    pages[0],
                ).reason,
                "ocr_binding_changed",
            )

            detect.assert_not_called()

    def test_capture_isolation_and_cleanup(self):

        self.finish()

        with ctx.capture_registered_pages() as outer:

            _, pages, _ = self.process()

            with ctx.capture_registered_pages() as inner:

                self.assertEqual(
                    self.detect(
                        inner,
                        pages[0],
                    ).reason,
                    "no_selected_ppocr_page",
                )

                self.assertEqual(
                    self.detect(
                        outer,
                        pages[0],
                    ).reason,
                    "capture_inactive",
                )

            with patch.object(
                ctx,
                "acquire_images",
                return_value=None,
            ):

                self.assertEqual(
                    self.detect(
                        outer,
                        pages[0],
                    ).reason,
                    "ocr_images_unavailable",
                )

        self.assertEqual(
            self.detect(
                outer,
                pages[0],
            ).reason,
            "capture_inactive",
        )

        self.assertEqual(
            outer._pages,
            {},
        )

    def test_vl_batch_not_captured(self):

        claim = self.finish()

        with ctx.capture_registered_pages() as cap:

            ctx.remember_completed_batch(
                self.journal,
                self.batch,
                "PaddleOCR-VL-1.6",
                claim,
                "job-1",
                self.pages,
            )

            self.assertEqual(
                self.detect(
                    cap,
                    self.pages[0],
                ).reason,
                "no_selected_ppocr_page",
            )


if __name__ == "__main__":
    unittest.main()