import copy
import threading
import unittest

from contextlib import ExitStack, nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf

from backend import cloud_pipeline

from backend.engine.ocr_inline_image_verification import (
    recover_inline_scan_blanks as real_inline_recovery,
)


ORIGINAL_STEM = (
    r"Vietnamese Independence Day, which is celebrated annually "
    r"$ \underline{\text{2nd}} $, holds great significance "
    r"for all Vietnamese people."
)

RECOVERED_STEM = (
    "Vietnamese Independence Day, which is celebrated annually "
    "________ September 2nd, holds great significance "
    "for all Vietnamese people."
)


class OCRRecoveryIntegrationTests(unittest.TestCase):

    def setUp(self):

        self.stack = ExitStack()

        self.addCleanup(self.stack.close)

        self.events = []

        self.lock = threading.Lock()

        self.model = "PaddleOCR-VL-1.6"

        self.contents = self.make_scan_pdf()

        self.primary = {
            "pruned_result": {},
        }

        self.pages = [
            self.primary,
        ]

        self.secondary = {
            "pruned_result": {
                "rec_texts": [],
            },
        }

        self.question = {
            "question_id": 13,
            "source_question_number": 13,
            "page_number": 1,
            "type": "mcp",
            "multiple_choice": False,
            "section_instruction": (
                "Choose the letter A, B, C, or D "
                "to complete each question."
            ),
            "question_content": [
                {
                    "type": "latex",
                    "value": ORIGINAL_STEM,
                },
            ],
            "mcp_options": [
                {
                    "label": label,
                    "content": [
                        {
                            "type": "text",
                            "value": text,
                        },
                    ],
                }
                for label, text in zip(
                    "ABCD",
                    ("on", "in", "at", "from"),
                )
            ],
            "correct_answer": "A",
        }

        self.questions = [
            self.question,
        ]

        self.original_question = copy.deepcopy(
            self.question
        )

        self.stack.enter_context(
            patch.object(
                cloud_pipeline,
                "PDF_LOCK",
                self.lock,
            )
        )

        self.checkpoint_class = self.mock(
            "CloudOCRCheckpoint"
        )

        self.checkpoint = (
            self.checkpoint_class.return_value
        )

        self.checkpoint.load.return_value = None

        self.checkpoint.save.side_effect = (
            self.save_checkpoint
        )

        self.repository_class = self.mock(
            "CloudRepository"
        )

        self.repository = (
            self.repository_class
            .return_value
            .__enter__
            .return_value
        )

        self.repository.get_exam_record.return_value = None

        self.repository.save_exam_bundle.side_effect = (
            self.save_bundle
        )

        self.capture = self.mock(
            "capture_registered_pages"
        )

        self.capture.return_value = nullcontext(
            object()
        )

        self.recognize = self.mock(
            "recognize_pdf"
        )

        self.recognize.return_value = (
            "OCR markdown",
            {
                "pages": self.pages,
                "extraction_method": "paddleocr",
            },
        )

        self.extract = self.mock(
            "extract_page_texts"
        )

        self.extract.return_value = [
            "OCR page text",
        ]

        self.parse = self.mock(
            "_parse_upload_questions"
        )

        self.parse.return_value = self.questions

        self.assign = self.mock(
            "_assign_unique_question_ids"
        )

        self.geometry = self.mock(
            "geometry_page_for"
        )

        self.geometry.return_value = self.secondary

        self.reconcile = self.mock(
            "reconcile_scan_choices"
        )

        self.reconcile.side_effect = (
            self.reconcile_choices
        )

        self.blank = self.mock(
            "recover_scan_blanks"
        )

        self.blank.side_effect = (
            self.recover_blanks
        )

        self.inline = self.mock(
            "recover_inline_scan_blanks"
        )

        self.inline.side_effect = (
            self.recover_inline
        )

        self.prepare = self.mock(
            "_prepare_question"
        )

        self.prepare.side_effect = (
            self.prepare_question
        )

        self.collect = self.mock(
            "collect_registered_marks"
        )

        self.collect.side_effect = (
            self.collect_marks
        )

        self.snapshot = self.mock(
            "build_parser_review_snapshot"
        )

        self.snapshot.side_effect = (
            self.build_snapshot
        )

    def mock(self, name):

        return self.stack.enter_context(
            patch.object(
                cloud_pipeline,
                name,
            )
        )

    @staticmethod
    def make_scan_pdf():

        with pymupdf.open() as doc:

            page = doc.new_page(
                width=480,
                height=650,
            )

            image = pymupdf.Pixmap(
                pymupdf.csRGB,
                pymupdf.IRect(0, 0, 48, 65),
                False,
            )

            image.clear_with(255)

            page.insert_image(
                page.rect,
                pixmap=image,
            )

            return doc.tobytes()

    def assert_recovery_context(
        self,
        pdf_path,
        pages,
        questions,
        geometry_page_for,
    ):

        self.assertTrue(
            self.lock.locked()
        )

        self.assertTrue(
            pdf_path.is_file()
        )

        self.assertIs(
            questions,
            self.questions,
        )

        self.assertIs(
            geometry_page_for,
            self.geometry,
        )

    def reconcile_choices(
        self,
        pdf_path,
        pages,
        questions,
        *,
        geometry_page_for,
    ):

        self.assert_recovery_context(
            pdf_path,
            pages,
            questions,
            geometry_page_for,
        )

        self.events.append(
            "text_reconciliation"
        )

        return []

    def recover_blanks(
        self,
        pdf_path,
        pages,
        questions,
        *,
        geometry_page_for,
    ):

        self.assert_recovery_context(
            pdf_path,
            pages,
            questions,
            geometry_page_for,
        )

        self.events.append(
            "blank_recovery"
        )

        return []

    def recover_inline(
        self,
        pdf_path,
        pages,
        questions,
        *,
        geometry_page_for,
    ):

        self.assert_recovery_context(
            pdf_path,
            pages,
            questions,
            geometry_page_for,
        )

        self.events.append(
            "inline_recovery"
        )

        question = questions[0]

        question["question_content"][0] = {
            "type": "text",
            "value": RECOVERED_STEM,
        }

        question["requires_source_image"] = True

        question["parse_fallback"] = True

        question["parser_warning"] = (
            "Inline reconstruction requires review."
        )

        return [
            {
                "page_number": 1,
                "source_question_number": 13,
                "inserted_word": "September",
            },
        ]

    def prepare_question(
        self,
        question,
        **kwargs,
    ):

        self.assertFalse(
            self.lock.locked()
        )

        self.events.append(
            "prepare_question"
        )

        self.assertIs(
            question,
            self.question,
        )

    def collect_marks(
        self,
        pdf_path,
        pages,
        questions,
        capture,
    ):

        self.assertTrue(
            self.lock.locked()
        )

        self.events.append(
            "collect_marks"
        )

        return {}

    def build_snapshot(self, questions):

        self.events.append(
            "review_snapshot"
        )

        return {
            "questions": copy.deepcopy(
                questions
            ),
        }

    def save_checkpoint(
        self,
        exam,
        questions,
        crop_images,
    ):

        self.events.append(
            "checkpoint_save"
        )

    def save_bundle(
        self,
        exam,
        questions,
        contents,
        crop_images,
        *,
        resume_existing,
    ):

        self.events.append(
            "bundle_save"
        )

        return {
            "exam_id": exam["id"],
            "title": exam["name"],
            "question_count": len(questions),
        }

    def process(self):

        return cloud_pipeline.process_cloud_pdf(
            self.contents,
            "scan.pdf",
            self.model,
            "test-owner",
            "test-access-token",
        )

    def make_cached_bundle(self):

        return SimpleNamespace(
            exam={
                "id": "cached-exam",
                "name": "Cached exam",
                "extra": {
                    "extraction_method": "paddleocr",
                    "parser_pipeline_version": "legacy",
                },
            },
            questions=copy.deepcopy(
                self.questions
            ),
            crop_images={
                "cached.png": b"cached-image",
            },
        )

    def test_recovery_order_before_assets_and_persistence(self):

        result = self.process()

        self.assertEqual(
            self.events,
            [
                "text_reconciliation",
                "blank_recovery",
                "inline_recovery",
                "prepare_question",
                "collect_marks",
                "review_snapshot",
                "checkpoint_save",
                "bundle_save",
            ],
        )

        self.assertEqual(
            result["question_count"],
            1,
        )

        self.recognize.assert_called_once()

        self.reconcile.assert_called_once()

        self.blank.assert_called_once()

        self.inline.assert_called_once()

        self.repository.save_exam_bundle.assert_called_once()

    def test_inline_recovery_receives_same_ocr_context(self):

        self.process()

        args, kwargs = (
            self.inline.call_args
        )

        self.assertIs(
            args[1],
            self.pages,
        )

        self.assertIs(
            args[2],
            self.questions,
        )

        self.assertIs(
            kwargs["geometry_page_for"],
            self.geometry,
        )

    def test_recovered_content_reaches_assets_and_storage(self):

        self.process()

        prepared = (
            self.prepare.call_args.args[0]
        )

        saved = (
            self.repository
            .save_exam_bundle
            .call_args.args[1][0]
        )

        for question in (
            prepared,
            saved,
        ):

            self.assertEqual(
                question["question_content"][0],
                {
                    "type": "text",
                    "value": RECOVERED_STEM,
                },
            )

            self.assertTrue(
                question["requires_source_image"]
            )

            self.assertTrue(
                question["parse_fallback"]
            )

            self.assertIn(
                "parser_warning",
                question,
            )

            self.assertEqual(
                question["mcp_options"],
                self.original_question["mcp_options"],
            )

            self.assertEqual(
                question["correct_answer"],
                self.original_question["correct_answer"],
            )

        saved_exam = (
            self.repository
            .save_exam_bundle
            .call_args.args[0]
        )

        reviewed = (
            saved_exam["extra"]
            ["parser_review"]
            ["questions"][0]
        )

        self.assertEqual(
            reviewed["question_content"][0]["value"],
            RECOVERED_STEM,
        )

    def test_missing_supplemental_ocr_preserves_question(self):

        self.geometry.return_value = (
            self.primary
        )

        self.inline.side_effect = (
            real_inline_recovery
        )

        result = self.process()

        self.assertEqual(
            self.question,
            self.original_question,
        )

        self.assertEqual(
            result["question_count"],
            1,
        )

        self.geometry.assert_called_once_with(
            self.primary
        )

        self.repository.save_exam_bundle.assert_called_once()

    def test_missing_ocr_pages_are_handled(self):

        self.recognize.return_value = (
            "OCR markdown",
            {
                "pages": None,
                "extraction_method": "paddleocr",
            },
        )

        self.inline.side_effect = (
            real_inline_recovery
        )

        result = self.process()

        self.assertEqual(
            self.inline.call_args.args[1],
            [],
        )

        self.assertEqual(
            self.question,
            self.original_question,
        )

        self.assertEqual(
            result["question_count"],
            1,
        )

        self.geometry.assert_not_called()

    def test_checkpoint_resume_does_not_repeat_recovery(self):

        cached = self.make_cached_bundle()

        before = copy.deepcopy(
            cached.questions
        )

        self.checkpoint.load.return_value = cached

        result = self.process()

        self.assertEqual(
            result["exam_id"],
            "cached-exam",
        )

        self.recognize.assert_not_called()

        self.parse.assert_not_called()

        self.reconcile.assert_not_called()

        self.blank.assert_not_called()

        self.inline.assert_not_called()

        self.prepare.assert_not_called()

        self.snapshot.assert_not_called()

        self.checkpoint.save.assert_not_called()

        self.assertEqual(
            cached.questions,
            before,
        )

        args, kwargs = (
            self.repository
            .save_exam_bundle
            .call_args
        )

        self.assertIs(
            args[1],
            cached.questions,
        )

        self.assertIs(
            args[3],
            cached.crop_images,
        )

        self.assertTrue(
            kwargs["resume_existing"]
        )

        self.checkpoint.delete.assert_called_once()

    def test_already_saved_checkpoint_skips_duplicate_save(self):

        cached = self.make_cached_bundle()

        self.checkpoint.load.return_value = cached

        self.repository.get_exam_record.return_value = {
            "name": "Previously saved exam",
            "questions": cached.questions,
        }

        result = self.process()

        self.assertEqual(
            result,
            {
                "exam_id": "cached-exam",
                "title": "Previously saved exam",
                "question_count": 1,
            },
        )

        self.recognize.assert_not_called()

        self.inline.assert_not_called()

        self.repository.save_exam_bundle.assert_not_called()

        self.checkpoint.delete.assert_called_once()


if __name__ == "__main__":
    unittest.main()