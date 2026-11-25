import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend import db


class CompositeIdTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

        root = Path(self.temp_dir.name)

        self.originals = (
            db.DATABASE_PATH,
            db.STORAGE_DIR,
            db.ASSETS_DIR,
            db.LEGACY_JSON_PATH,
        )

        db.DATABASE_PATH = root / "quiz.sqlite3"
        db.STORAGE_DIR = root / "storage"
        db.ASSETS_DIR = db.STORAGE_DIR / "assets"
        db.LEGACY_JSON_PATH = root / "absent.json"

        db.initialize_database()

    def tearDown(self):
        (
            db.DATABASE_PATH,
            db.STORAGE_DIR,
            db.ASSETS_DIR,
            db.LEGACY_JSON_PATH,
        ) = self.originals

        self.temp_dir.cleanup()

    def _create_exam(
        self,
        exam_id,
        composite_ids,
        name="DeThi",
    ):
        timestamp = datetime.now(
            timezone.utc
        ).isoformat()

        exam = {
            "id": exam_id,
            "name": name,
            "source_file": f"{name}.pdf",
            "uploaded_at": timestamp,
            "extra": {},
        }

        questions = []

        for index, composite_id in enumerate(
            composite_ids,
            start=1,
        ):
            questions.append({
                "id": str(uuid.uuid4()),
                "composite_id": composite_id,
                "question_id": index,
                "source_question_number": index,
                "page_number": 1,
                "type": "mcp",
                "multiple_choice": False,
                "question_content": [
                    {
                        "type": "text",
                        "value": f"Question {index}",
                    },
                ],
                "explanation_content": [],
                "mcp_options": [
                    {
                        "id": str(uuid.uuid4()),
                        "label": "A",
                        "content": [
                            {
                                "type": "text",
                                "value": "Option A",
                            },
                        ],
                        "is_correct": False,
                        "order_index": 0,
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "label": "B",
                        "content": [
                            {
                                "type": "text",
                                "value": "Option B",
                            },
                        ],
                        "is_correct": False,
                        "order_index": 1,
                    },
                ],
                "match_pairs": [],
                "fill_in_answers": [],
                "created_at": timestamp,
            })

        db.insert_exam(
            exam,
            questions,
        )

        return db.get_exam_record(exam_id)

    def test_original_id_is_preserved_without_collision(self):
        exam = self._create_exam(
            "exam-1",
            ["dethi_Q01"],
        )

        self.assertEqual(
            exam["questions"][0]["composite_id"],
            "dethi_Q01",
        )

    def test_upload_same_exam_name_twice(self):
        composite_ids = [
            "dethi_Q01",
            "dethi_Q02",
            "dethi_Q03",
            "dethi_Q04",
        ]

        first = self._create_exam(
            "exam-1",
            composite_ids,
        )

        second = self._create_exam(
            "exam-2",
            composite_ids,
        )

        self.assertEqual(
            len(first["questions"]),
            4,
        )

        self.assertEqual(
            len(second["questions"]),
            4,
        )

        first_ids = {
            question["composite_id"]
            for question in first["questions"]
        }

        second_ids = {
            question["composite_id"]
            for question in second["questions"]
        }

        self.assertEqual(
            first_ids,
            set(composite_ids),
        )

        self.assertTrue(
            first_ids.isdisjoint(second_ids)
        )

        self.assertEqual(
            len(db.list_exams()),
            2,
        )

        self.assertEqual(
            [
                question["source_question_number"]
                for question in second["questions"]
            ],
            [1, 2, 3, 4],
        )

        for question in second["questions"]:
            self.assertEqual(
                len(question["mcp_options"]),
                2,
            )

        reloaded = db.get_exam_record("exam-1")

        self.assertEqual(
            [
                question["composite_id"]
                for question in reloaded["questions"]
            ],
            composite_ids,
        )

    def test_collision_with_existing_namespaced_id(self):
        self._create_exam(
            "exam-1",
            ["dethi_Q01"],
        )

        self._create_exam(
            "exam-2",
            ["dethi_Q01__exam-3__1"],
        )

        third = self._create_exam(
            "exam-3",
            ["dethi_Q01"],
        )

        self.assertEqual(
            third["questions"][0]["composite_id"],
            "dethi_Q01__exam-3__1__2",
        )

        self.assertEqual(
            len(db.list_exams()),
            3,
        )


if __name__ == "__main__":
    unittest.main()
