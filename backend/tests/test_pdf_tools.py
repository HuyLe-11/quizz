import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import fitz
import cv2
import numpy as np

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
        self.assertEqual(result["status"], "not_found")
        self.assertIsNone(result["detected_answer"])

    def test_red_underline_near_answer(self):
        self._create_pdf("underline")
        result = detect_red_mark_pdf(self.pdf, 1, 1, ["A", "B", "C"])
        self.assertEqual(result["status"], "not_found")
        self.assertIsNone(result["detected_answer"])

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
        self.assertEqual(result["crop_status"], "review_required")
        self.assertTrue(result["review_required"])

    def test_horizontal_choices_keep_outer_edges_marks_and_stop_at_next_question(self):
        doc = fitz.open()
        page = doc.new_page(width=600, height=800)
        page.insert_text((35, 70), "Question 4: choose one", fontsize=12)
        page.insert_text((35, 150), "A. Alpha", fontsize=12)
        page.insert_text((180, 150), "B. Beta", fontsize=12)
        page.insert_text((330, 150), "C. Gamma", fontsize=12)
        page.insert_text((500, 150), "D. Delta", fontsize=12)
        page.draw_oval(fitz.Rect(28, 132, 48, 153), color=(1, 0, 0), width=2)
        page.draw_line((35, 157), (105, 157), color=(1, 0, 0), width=2)
        page.insert_text((35, 260), "Question 5: next question", fontsize=12)
        doc.save(self.pdf)
        doc.close()

        result = crop_question_pdf(self.pdf, 4, 1, self.root / "horizontal.png")
        self.assertEqual(result["page_number"], 1)
        self.assertEqual(result["source"], "pymupdf")
        bbox = result["crop_coordinates"][0]["bbox"]
        self.assertEqual(bbox[0], 0)
        self.assertGreaterEqual(bbox[2], 550)
        self.assertLess(bbox[3], 260)
        image = cv2.imread(str(self.root / "horizontal.png"))
        self.assertGreater(image.shape[1], 1200)
        red = (image[:, :, 2] > 150) & (image[:, :, 1] < 100) & (image[:, :, 0] < 100)
        self.assertGreater(int(red.sum()), 10)

    def test_ocr_horizontal_option_block_expands_crop_to_rightmost_choice(self):
        doc = fitz.open()
        page = doc.new_page(width=600, height=800)
        page.draw_rect(page.rect, color=(0, 0, 0), width=0.1)  # Keep the page image-only for OCR fallback.
        doc.save(self.pdf)
        doc.close()
        ocr_page = {"width": 600, "height": 800, "parsing_res_list": [
            {"block_content": "Question 12: choose", "block_bbox": [30, 45, 250, 65]},
            {"block_content": "A. left B. mid C. mid-right D. far-right", "block_bbox": [30, 120, 575, 145]},
            {"block_content": "Question 13: following", "block_bbox": [30, 230, 250, 250]},
        ]}
        result = crop_question_pdf(self.pdf, 12, 1, self.root / "ocr-horizontal.png", ocr_pages=[ocr_page])
        self.assertEqual(result["source"], "paddleocr")
        bbox = result["crop_coordinates"][0]["bbox"]
        self.assertGreaterEqual(bbox[2], 575)
        self.assertLess(bbox[3], 230)

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

    def test_question_one_section_fallback_uses_next_question_boundary_and_scaled_ocr(self):
        # Image-only page: OCR has the section heading and Q2, but misses Q1's
        # numbered heading/options. The source scan still has both outer choices.
        source = fitz.open()
        source_page = source.new_page(width=600, height=800)
        source_page.insert_text((30, 65), "QUESTION I. Choose A, B, C, or D", fontsize=12)
        for label, x, value in [("A", 30, "grade"), ("B", 180, "paper"),
                                ("C", 330, "table"), ("D", 480, "habit")]:
            source_page.insert_text((x, 120), f"{label}. {value}", fontsize=12)
        source_page.draw_oval(fitz.Rect(470, 101, 555, 128), color=(1, 0, 0), width=2)
        source_page.insert_text((30, 170), "Question 2. Next question", fontsize=12)
        pixmap = source_page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_image(page.rect, pixmap=pixmap)
        document.save(self.pdf)
        document.close()
        source.close()
        ocr_page = {"image_width": 1200, "image_height": 1600, "parsing_res_list": [
            {"block_content": "QUESTION I. Choose A B C D", "block_bbox": [20, 100, 1160, 300]},
            {"block_content": "Question 2. Next question", "block_bbox": [30, 350, 600, 390]},
        ]}
        result = crop_question_pdf(self.pdf, 1, 1, self.root / "q1-section-fallback.png", [ocr_page])
        self.assertEqual(result["source"], "paddleocr")
        self.assertEqual(result["page_number"], 1)
        self.assertEqual(result["crop_status"], "success")
        self.assertFalse(result["review_required"])
        x0, y0, x1, y1 = result["crop_coordinates"][0]["bbox"]
        self.assertEqual((x0, x1), (0, 600))
        self.assertAlmostEqual(y1, 159, delta=1)
        self.assertLess(y0, 50)
        crop = cv2.imread(str(self.root / "q1-section-fallback.png"))
        row_y = int((120 - y0) * 200 / 72)
        self.assertGreater(int(np.count_nonzero(crop[row_y - 25:row_y + 25, 20:280, :].min(axis=2) < 100)), 10)
        self.assertGreater(int(np.count_nonzero(crop[row_y - 35:row_y + 35, -350:-20, 2] > 150)), 10)
        self.assertLess(y1, 160)  # Q2's OCR row starts outside the crop.

    def test_ocr_pixel_boxes_are_converted_to_pdf_points(self):
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        page.draw_rect(page.rect, color=(0, 0, 0), width=0.1)
        doc.save(self.pdf)
        doc.close()
        ocr_page = {"width": 600, "height": 800, "parsing_res_list": [
            {"block_content": "Question 1: choose", "block_bbox": [40, 80, 300, 120]},
            {"block_content": "A. one B. two", "block_bbox": [40, 180, 500, 220]},
            {"block_content": "Question 2: next", "block_bbox": [40, 400, 300, 440]},
        ]}
        result = crop_question_pdf(self.pdf, 1, 1, self.root / "scaled.png", [ocr_page])
        self.assertEqual(result["source"], "paddleocr")
        bbox = result["crop_coordinates"][0]["bbox"]
        # OCR is 600x800 while the PDF page is 300x400 points.
        self.assertAlmostEqual(bbox[1], 26, delta=1)
        self.assertAlmostEqual(bbox[2], 300, delta=1)
        self.assertAlmostEqual(bbox[3], 193, delta=1)  # half-padding before Q2

    def _create_options_fixture(self, marks=(), layout="vertical", scan=False, irregular=False):
        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_text((35, 70), "Question 1: Select one answer", fontsize=12)
        positions = {}
        if layout == "vertical":
            for index, label in enumerate("ABCD"):
                y = 120 + index * 42
                page.insert_text((40, y), f"{label}. Option {label}", fontsize=12)
                positions[label] = (40, 51, y - 18, y + 4)
        elif layout == "horizontal":
            for index, label in enumerate("ABCD"):
                x = 35 + index * 140
                page.insert_text((x, 150), f"{label}. Option {label}", fontsize=12)
                positions[label] = (x, x + 11, 132, 154)
        elif layout == "two_columns":
            for label, x, y in [("A", 40, 130), ("B", 330, 130), ("C", 40, 190), ("D", 330, 190)]:
                page.insert_text((x, y), f"{label}. Option {label}", fontsize=12)
                positions[label] = (x, x + 11, y - 18, y + 4)

        for label in marks:
            x0, x1, y0, y1 = positions[label]
            if irregular:
                center_x, center_y = (x0 + x1) / 2, (y0 + y1) / 2
                radius_x, radius_y = (x1 - x0) / 2 + 7, (y1 - y0) / 2 + 7
                angles = np.linspace(0.12, 2 * np.pi - 0.12, 70)
                points = []
                for index, angle in enumerate(angles):
                    wobble = 1 + 0.08 * np.sin(index * 0.7)
                    points.append((center_x + radius_x * wobble * np.cos(angle),
                                   center_y + radius_y * wobble * np.sin(angle)))
                page.draw_polyline(points, color=(1, 0, 0), width=2)
            else:
                # For a content circle include both the printed label and its answer text.
                if layout == "vertical":
                    page.draw_oval(fitz.Rect(x0 - 8, y0 - 4, x0 + 96, y1 + 4), color=(1, 0, 0), width=2)
                else:
                    page.draw_oval(fitz.Rect(x0 - 8, y0 - 4, x0 + 96, y1 + 4), color=(1, 0, 0), width=2)
        page.insert_text((35, 380), "Question 2: Next question", fontsize=12)
        source = self.root / f"{layout}-{'-'.join(marks) or 'none'}.pdf"
        document.save(source)
        document.close()
        if not scan:
            return source, None

        original = fitz.open(source)
        scanned = fitz.open()
        scanned_page = scanned.new_page(width=600, height=800)
        pixmap = original[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        scanned_page.insert_image(scanned_page.rect, pixmap=pixmap)
        scan_path = self.root / f"{layout}-scan.pdf"
        scanned.save(scan_path)
        original.close()
        scanned.close()
        ocr_page = {"parsing_res_list": [
            {"block_content": "Question 1: Select one answer", "block_bbox": [35, 52, 230, 74]},
            *[
                {"block_content": f"{label}. Option {label}",
                 "block_bbox": [box[0], box[2], box[0] + 100, box[3]]}
                for label, box in positions.items()
            ],
            {"block_content": "Question 2: Next question", "block_bbox": [35, 360, 230, 382]},
        ]}
        return scan_path, ocr_page

    def test_each_option_a_through_d_can_be_detected(self):
        for label in "ABCD":
            with self.subTest(label=label):
                path, _ = self._create_options_fixture([label])
                result = detect_red_mark_pdf(path, 1, 1, list("ABCD"))
                self.assertEqual(result["status"], "detected")
                self.assertEqual(result["detected_answer"], label)

    def test_circle_around_option_label_is_detected(self):
        path, _ = self._create_options_fixture([])
        # Replace the wide content circle with a tight ring around the label glyph.
        document = fitz.open(path)
        page = document[0]
        page.draw_oval(fitz.Rect(33, 180, 59, 204), color=(1, 0, 0), width=2)
        label_path = self.root / "label-only.pdf"
        document.save(label_path)
        document.close()
        result = detect_red_mark_pdf(label_path, 1, 1, list("ABCD"))
        self.assertEqual(result["detected_answer"], "C")
        self.assertEqual(result["status"], "detected")

    def test_circle_around_label_and_full_content_is_detected(self):
        path, _ = self._create_options_fixture(["D"])
        result = detect_red_mark_pdf(path, 1, 1, list("ABCD"))
        self.assertEqual(result["detected_answer"], "D")
        self.assertEqual(result["status"], "detected")

    def test_irregular_broken_circle_is_detected(self):
        path, _ = self._create_options_fixture(["B"], irregular=True)
        result = detect_red_mark_pdf(path, 1, 1, list("ABCD"))
        self.assertEqual(result["detected_answer"], "B")
        self.assertEqual(result["status"], "detected")

    def test_two_circled_options_return_uncertain_without_answer(self):
        path, _ = self._create_options_fixture(["A", "B"])
        result = detect_red_mark_pdf(path, 1, 1, list("ABCD"))
        self.assertEqual(result["status"], "uncertain")
        self.assertIsNone(result["detected_answer"])

    def test_circle_between_two_options_is_uncertain(self):
        path, _ = self._create_options_fixture([])
        document = fitz.open(path)
        document[0].draw_oval(fitz.Rect(30, 123, 70, 151), color=(1, 0, 0), width=2)
        between_path = self.root / "between-options.pdf"
        document.save(between_path)
        document.close()
        result = detect_red_mark_pdf(between_path, 1, 1, list("ABCD"))
        self.assertEqual(result["status"], "uncertain")
        self.assertIsNone(result["detected_answer"])

    def test_horizontal_and_two_column_options_are_detectable(self):
        for layout, label in (("horizontal", "D"), ("two_columns", "C")):
            with self.subTest(layout=layout):
                path, _ = self._create_options_fixture([label], layout=layout)
                result = detect_red_mark_pdf(path, 1, 1, list("ABCD"))
                self.assertEqual(result["status"], "detected")
                self.assertEqual(result["detected_answer"], label)

    def test_red_circle_on_scan_uses_ocr_boxes(self):
        path, ocr_page = self._create_options_fixture(["D"], scan=True)
        result = detect_red_mark_pdf(path, 1, 1, list("ABCD"), page_ocr=ocr_page)
        self.assertEqual(result["status"], "detected")
        self.assertEqual(result["detected_answer"], "D")

    def test_red_circle_on_slightly_tilted_scan_uses_transformed_ocr_boxes(self):
        source, _ = self._create_options_fixture(["D"])
        source_doc = fitz.open(source)
        source_pixmap = source_doc[0].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
        raster = np.frombuffer(source_pixmap.samples, dtype=np.uint8).reshape(
            source_pixmap.height, source_pixmap.width, source_pixmap.n
        )
        matrix = cv2.getRotationMatrix2D((300, 400), 2.0, 1.0)
        tilted = cv2.warpAffine(raster, matrix, (600, 800), borderValue=(255, 255, 255))
        ok, encoded = cv2.imencode(".png", cv2.cvtColor(tilted, cv2.COLOR_RGB2BGR))
        self.assertTrue(ok)
        scanned = fitz.open()
        page = scanned.new_page(width=600, height=800)
        page.insert_image(page.rect, stream=encoded.tobytes())
        scan_path = self.root / "tilted-scan.pdf"
        scanned.save(scan_path)
        scanned.close()
        source_doc.close()

        def rotated_box(box):
            x0, y0, x1, y1 = box
            points = np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]]) @ matrix.T
            return [float(points[:, 0].min()), float(points[:, 1].min()),
                    float(points[:, 0].max()), float(points[:, 1].max())]

        ocr_page = {"width": 600, "height": 800, "parsing_res_list": [
            {"block_content": "Question 1: Select one answer", "block_bbox": rotated_box([35, 55, 230, 74])},
            *[
                {"block_content": f"{label}. Option {label}",
                 "block_bbox": rotated_box([box[0], box[2], box[0] + 100, box[3]])}
                for label, box in [(label, (40, 51, 120 + index * 42 - 18, 120 + index * 42 + 4))
                                   for index, label in enumerate("ABCD")]
            ],
            {"block_content": "Question 2: Next question", "block_bbox": rotated_box([35, 360, 230, 382])},
        ]}
        result = detect_red_mark_pdf(scan_path, 1, 1, list("ABCD"), page_ocr=ocr_page)
        self.assertEqual(result["status"], "detected")
        self.assertEqual(result["detected_answer"], "D")

    def test_marked_english_reference_missing_q1_option_boxes_stays_uncertain(self):
        mock_dir = Path(__file__).parents[1] / "data" / "mock_pdf"
        reference = next(mock_dir.glob("multiple choice*.pdf"), None)
        if reference is None:
            self.skipTest("Reference marked PDF is not present in this checkout")
        # OCR omitted the Q1 heading/options in this scan. Even though the image has
        # a manually verified D mark, no answer is assigned without option geometry.
        ocr_page = {"parsing_res_list": [
            {"block_content": "QUESTION I. Choose the letter A, B, C, or D", "block_bbox": [0, 204, 925, 276]},
            {"block_content": "Question 2. A. destroyed B. performed C. wanted", "block_bbox": [8, 300, 640, 333]},
        ]}
        result = detect_red_mark_pdf(reference, 1, 1, list("ABCD"), page_ocr=ocr_page)
        self.assertEqual(result["status"], "uncertain")
        self.assertIsNone(result["detected_answer"])
        self.assertIsNotNone(result["mark_coordinates"])

    def test_user_confirmation_is_separate_from_detection_and_original_options(self):
        from backend import db

        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        originals = (db.DATABASE_PATH, db.STORAGE_DIR, db.ASSETS_DIR, db.LEGACY_JSON_PATH)
        try:
            db.DATABASE_PATH = root / "quiz.sqlite3"
            db.STORAGE_DIR = root / "storage"
            db.ASSETS_DIR = db.STORAGE_DIR / "assets"
            db.LEGACY_JSON_PATH = root / "absent.json"
            db.initialize_database()
            timestamp = datetime.now(timezone.utc).isoformat()
            db.insert_exam({"id": "review-exam", "name": "Review", "uploaded_at": timestamp,
                            "extra": {}, "source_file": "review.pdf", "source_path": ""}, [{
                "id": "review-question", "composite_id": "review_Q01", "question_id": 1, "type": "mcp",
                "question_content": [], "explanation_content": [], "mcp_options": [
                    {"id": f"option-{label.lower()}", "label": label, "content": [],
                     "is_correct": False, "order_index": index}
                    for index, label in enumerate("ABCD")
                ], "match_pairs": [], "fill_in_answers": [], "created_at": timestamp,
            }])
            detection = {"detected_answer": "B", "confidence": 0.91, "status": "detected",
                         "page_number": 1, "mark_coordinates": [20, 20, 40, 40],
                         "config_fingerprint": "test-config"}
            db.save_red_detection("review-question", detection)
            self.assertEqual(db.get_red_detection("review-question")["user_confirmed_answer"], None)
            confirmed = db.confirm_red_answer("review-question", "C")
            self.assertEqual(confirmed["detected_answer"], "B")
            self.assertEqual(confirmed["user_confirmed_answer"], "C")
            saved = db.get_question_record("review-question")[1]
            self.assertFalse(any(option["is_correct"] for option in saved["mcp_options"]))
        finally:
            db.DATABASE_PATH, db.STORAGE_DIR, db.ASSETS_DIR, db.LEGACY_JSON_PATH = originals
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
