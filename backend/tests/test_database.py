import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend import db


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.originals = (db.DATABASE_PATH, db.STORAGE_DIR, db.ASSETS_DIR, db.LEGACY_JSON_PATH)
        db.DATABASE_PATH = root / "quiz.sqlite3"
        db.STORAGE_DIR = root / "storage"
        db.ASSETS_DIR = db.STORAGE_DIR / "assets"
        db.LEGACY_JSON_PATH = root / "generated_exams.json"
        db.initialize_database()

    def tearDown(self):
        db.DATABASE_PATH, db.STORAGE_DIR, db.ASSETS_DIR, db.LEGACY_JSON_PATH = self.originals
        self.temp_dir.cleanup()

    def test_exam_questions_and_confirmed_red_answer_are_persistent(self):
        exam_id, question_id = "exam-demo", "question-demo"
        db.insert_exam({
            "id": exam_id, "name": "Đề demo", "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "extra": {}, "source_file": "demo.pdf", "source_path": "/tmp/demo.pdf",
        }, [{
            "id": question_id, "composite_id": "demo_Q01", "question_id": 1, "type": "mcp",
            "question_content": [{"type": "text", "value": "Chọn một đáp án"}],
            "explanation_content": [], "mcp_options": [
                {"id": "option-a", "label": "A", "content": [{"type": "text", "value": "Một"}], "is_correct": False, "order_index": 0},
                {"id": "option-b", "label": "B", "content": [{"type": "text", "value": "Hai"}], "is_correct": False, "order_index": 1},
            ], "match_pairs": [], "fill_in_answers": [], "created_at": datetime.now(timezone.utc).isoformat(),
        }])
        db.save_red_detection(question_id, {
            "detected_answer": "B", "confidence": 0.9, "status": "detected", "page_number": 1,
            "mark_coordinates": [10, 20, 30, 40], "config_fingerprint": "config-v1",
        })
        db.confirm_red_answer(question_id, "A")

        exam = db.get_exam_record(exam_id)
        self.assertEqual(exam["questions"][0]["red_mark_detection"]["user_confirmed_answer"], "A")
        self.assertFalse(exam["questions"][0]["mcp_options"][1]["is_correct"])
        self.assertEqual(db.list_exams()[0]["question_count"], 1)

    def test_legacy_json_is_imported_once_without_source_pdf(self):
        import json

        db.LEGACY_JSON_PATH.write_text(json.dumps({
            "exams": [
                {"id": "legacy-exam", "name": "Đề cũ", "source_file": "old.pdf", "extra": {}},
                {"id": "legacy-exam-2", "name": "Đề cũ 2", "source_file": "old-2.pdf", "extra": {}},
            ],
            "questions": [
                {"id": "legacy-q", "composite_id": "same_Q01", "exam_id": "legacy-exam",
                 "question_id": 1, "type": "mcp", "question_content": [], "explanation_content": []},
                {"id": "legacy-q-duplicate", "composite_id": "same_Q01", "exam_id": "legacy-exam",
                 "question_id": 1, "type": "mcp", "question_content": [{"type": "text", "value": "Nội dung đã bổ sung từ đáp án"}], "explanation_content": []},
                {"id": "legacy-q-2", "composite_id": "same_Q01", "exam_id": "legacy-exam-2",
                 "question_id": 1, "type": "mcp", "question_content": [], "explanation_content": []},
            ],
            "mcp_options": [
                {"id": "legacy-option-short", "question_id": "legacy-q", "label": "A", "content": [{"type": "text", "value": "Đáp án"}]},
                {"id": "legacy-option", "question_id": "legacy-q-duplicate", "label": "A", "content": [{"type": "text", "value": "Đáp án đã có lời giải chi tiết"}]},
                {"id": "legacy-option-2", "question_id": "legacy-q-2", "label": "B"},
            ],
            "match_pairs": [], "fill_in_answers": [],
        }), encoding="utf-8")
        db.initialize_database()
        db.initialize_database()
        records = db.list_exams()
        self.assertEqual(sum(record["id"] == "legacy-exam" for record in records), 1)
        self.assertFalse(next(record for record in records if record["id"] == "legacy-exam")["has_source_pdf"])
        imported = db.get_exam_record("legacy-exam-2")
        self.assertEqual(imported["questions"][0]["id"], "legacy-q-2")
        self.assertEqual(imported["questions"][0]["mcp_options"][0]["id"], "legacy-option-2")
        canonical = db.get_exam_record("legacy-exam")["questions"]
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0]["mcp_options"][0]["id"], "legacy-option-short")
        self.assertIn("Nội dung đã bổ sung", canonical[0]["question_content"][0]["value"])
        self.assertIn("lời giải chi tiết", canonical[0]["mcp_options"][0]["content"][0]["value"])


if __name__ == "__main__":
    unittest.main()
