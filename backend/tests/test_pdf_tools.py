import tempfile
import unittest
from pathlib import Path

import fitz

from backend.engine.pdf_tools import RedMarkConfig, crop_question_pdf, detect_red_mark_pdf


class PdfToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.pdf = self.root / "marked.pdf"
        self._create_pdf("circle")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_pdf(self, mark):
        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_text((40, 70), "Question 1: Select one answer", fontsize=12)
        page.insert_text((40, 120), "A. Alpha option", fontsize=12)
        page.insert_text((40, 160), "B. Beta option", fontsize=12)
        page.insert_text((40, 200), "C. Gamma option", fontsize=12)
        page.insert_text((40, 280), "Question 2: Another question", fontsize=12)
        page.insert_text((40, 320), "A. Next alpha", fontsize=12)
        if mark == "circle":
            page.draw_oval(fitz.Rect(30, 143, 50, 164), color=(1, 0, 0), width=2)
        elif mark == "check":
            page.draw_line((25, 114), (32, 121), color=(1, 0, 0), width=2)
            page.draw_line((32, 121), (44, 105), color=(1, 0, 0), width=2)
        elif mark == "underline":
            page.draw_line((40, 167), (115, 167), color=(1, 0, 0), width=2)
        elif mark == "between":
            page.draw_line((30, 136), (47, 136), color=(1, 0, 0), width=2)
        elif mark == "unrelated":
            page.draw_line((30, 400), (55, 400), color=(1, 0, 0), width=2)
            page.draw_line((30, 400), (45, 420), color=(1, 0, 0), width=2)
        document.save(self.pdf)
        document.close()

    def test_red_circle_is_associated_with_nearest_option(self):
        result = detect_red_mark_pdf(self.pdf, 1, 1, ["A", "B", "C"])
        self.assertEqual(result["detected_answer"], "B")
        self.assertIn(result["status"], {"detected", "uncertain"})
        self.assertIsNotNone(result["mark_coordinates"])
        self.assertGreaterEqual(result["confidence"], RedMarkConfig.from_environment().medium_confidence)

    def test_unrelated_red_mark_is_not_an_answer(self):
        self._create_pdf("unrelated")
        result = detect_red_mark_pdf(self.pdf, 1, 1, ["A", "B", "C"])
        self.assertEqual(result["status"], "not_found")

    def test_red_check_mark_next_to_answer(self):
        self._create_pdf("check")
        result = detect_red_mark_pdf(self.pdf, 1, 1, ["A", "B", "C"])
        self.assertEqual(result["detected_answer"], "A")

    def test_red_underline_near_answer(self):
        self._create_pdf("underline")
        result = detect_red_mark_pdf(self.pdf, 1, 1, ["A", "B", "C"])
        self.assertEqual(result["detected_answer"], "B")

    def test_mark_between_two_answers_requires_confirmation(self):
        self._create_pdf("between")
        result = detect_red_mark_pdf(self.pdf, 1, 1, ["A", "B", "C"])
        self.assertIn(result["status"], {"uncertain", "not_found"})

    def test_missing_question_returns_not_found(self):
        result = detect_red_mark_pdf(self.pdf, 99, 1, ["A", "B"])
        self.assertEqual(result["status"], "not_found")
        self.assertIsNone(result["detected_answer"])

    def test_question_crop_stops_before_next_question(self):
        result = crop_question_pdf(self.pdf, 1, 1, self.root / "question-1.png")
        self.assertEqual(result["crop_status"], "success")
        self.assertEqual(result["source"], "pymupdf")
        image_doc = fitz.open(self.root / "question-1.png")
        try:
            pixmap = image_doc[0].get_pixmap()
            self.assertLess(pixmap.height, 800 * 200 / 72)
        finally:
            image_doc.close()

    def test_full_page_fallback_preserves_page_when_question_boundary_is_missing(self):
        result = crop_question_pdf(self.pdf, 99, 1, self.root / "fallback.png")
        self.assertEqual(result["source"], "full_page_fallback")
        self.assertEqual(result["crop_status"], "success")

    def test_scanned_page_uses_paddleocr_boxes_as_crop_fallback(self):
        document = fitz.open()
        page = document.new_page(width=600, height=800)
        source = fitz.open(self.pdf)
        pixmap = source[0].get_pixmap()
        page.insert_image(page.rect, pixmap=pixmap)
        scanned_pdf = self.root / "scan.pdf"
        document.save(scanned_pdf)
        document.close()
        source.close()
        ocr_page = {"pruned_result": {"parsing_res_list": [
            {"block_content": "Question 1: Select one answer", "block_bbox": [40, 55, 260, 75]},
            {"block_content": "A. Alpha option", "block_bbox": [40, 105, 170, 125]},
            {"block_content": "B. Beta option", "block_bbox": [40, 145, 170, 165]},
            {"block_content": "Question 2: Another question", "block_bbox": [40, 265, 260, 285]},
        ]}}
        result = crop_question_pdf(
            scanned_pdf, 1, 1, self.root / "scan-crop.png", ocr_pages=[ocr_page]
        )
        self.assertEqual(result["source"], "paddleocr")


if __name__ == "__main__":
    unittest.main()
