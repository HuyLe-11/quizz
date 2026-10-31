import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import fitz
from fastapi.testclient import TestClient
from unittest.mock import patch

from backend import db
from backend.network.main import app


class QuizApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.originals = (db.DATABASE_PATH, db.STORAGE_DIR, db.ASSETS_DIR, db.LEGACY_JSON_PATH)
        db.DATABASE_PATH = self.root / "quiz.sqlite3"
        db.STORAGE_DIR = self.root / "storage"
        db.ASSETS_DIR = db.STORAGE_DIR / "assets"
        db.LEGACY_JSON_PATH = self.root / "absent.json"
        db.initialize_database()

        pdf_path = self.root / "marked.pdf"
        document = fitz.open()
        page = document.new_page(width=600, height=400)
        page.insert_text((40, 70), "Question 1: Select an answer", fontsize=12)
        page.insert_text((40, 120), "A. First option", fontsize=12)
        page.insert_text((40, 160), "B. Second option", fontsize=12)
        page.draw_oval(fitz.Rect(30, 143, 50, 164), color=(1, 0, 0), width=2)
        document.save(pdf_path)
        document.close()
        self.exam_id, self.question_id = "integration-exam", "integration-question"
        db.insert_exam({
            "id": self.exam_id, "name": "Đề kiểm thử API", "source_file": "marked.pdf",
            "source_path": str(pdf_path), "uploaded_at": datetime.now(timezone.utc).isoformat(), "extra": {},
        }, [{
            "id": self.question_id, "composite_id": "integration_Q01", "question_id": 1, "type": "mcp",
            "question_content": [{"type": "text", "value": "Chọn đáp án"}], "explanation_content": [],
            "mcp_options": [
                {"id": "integration-A", "label": "A", "content": [{"type": "text", "value": "Một"}], "is_correct": False},
                {"id": "integration-B", "label": "B", "content": [{"type": "text", "value": "Hai"}], "is_correct": False},
            ], "match_pairs": [], "fill_in_answers": [], "page_number": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }])
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        db.DATABASE_PATH, db.STORAGE_DIR, db.ASSETS_DIR, db.LEGACY_JSON_PATH = self.originals
        self.temp_dir.cleanup()

    def test_history_view_red_detection_and_confirmation_endpoints(self):
        history = self.client.get("/api/exams")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["data"][0]["id"], self.exam_id)

        exam = self.client.get(f"/api/exams/{self.exam_id}")
        self.assertEqual(exam.status_code, 200)
        question = exam.json()["data"]["questions"][0]
        self.assertTrue(question["has_source_pdf"])
        self.assertEqual(question["question_record_id"], self.question_id)

        crop = self.client.post(f"/api/questions/{self.question_id}/crop-image")
        self.assertEqual(crop.status_code, 200)
        self.assertTrue(crop.json()["data"]["image_url"].startswith("/api/assets/"))

        detection = self.client.post(f"/api/questions/{self.question_id}/detect-red-mark")
        self.assertEqual(detection.status_code, 200)
        self.assertEqual(detection.json()["data"]["detected_answer"], "B")

        confirmation = self.client.patch(
            f"/api/questions/{self.question_id}/red-mark-confirmation", json={"answer": "A"},
        )
        self.assertEqual(confirmation.status_code, 200)
        self.assertEqual(confirmation.json()["data"]["user_confirmed_answer"], "A")

    def test_upload_saves_questions_when_pdf_repeats_question_number(self):
        pdf = fitz.open()
        page = pdf.new_page(width=600, height=400)
        page.insert_text((40, 60), "Question 1: First question prompt", fontsize=12)
        page.insert_text((40, 100), "A. First choice", fontsize=12)
        page.insert_text((40, 125), "B. Second choice", fontsize=12)
        page.insert_text((40, 190), "Question 1: Second question prompt", fontsize=12)
        page.insert_text((40, 230), "A. Third choice", fontsize=12)
        page.insert_text((40, 255), "B. Fourth choice", fontsize=12)
        pdf_path = self.root / "repeated-number.pdf"
        pdf.save(pdf_path)
        pdf.close()

        markdown = "Question 1: First question prompt\nA. First choice\nB. Second choice\n\nQuestion 1: Second question prompt\nA. Third choice\nB. Fourth choice"
        raw = {"pages": [{"markdown_text": markdown}]}
        with patch("backend.network.main.recognize_pdf", return_value=(markdown, raw)):
            response = self.client.post(
                "/api/upload",
                files={"file": ("repeated-number.pdf", pdf_path.read_bytes(), "application/pdf")},
                data={"model": "PaddleOCR-VL-1.6"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        exam_id = response.json()["data"]["exam_id"]
        saved_exam = self.client.get(f"/api/exams/{exam_id}").json()["data"]
        self.assertEqual([q["question_id"] for q in saved_exam["questions"]], [1, 2])
        self.assertEqual([q["pdf_question_number"] for q in saved_exam["questions"]], [1, 1])


if __name__ == "__main__":
    unittest.main()
