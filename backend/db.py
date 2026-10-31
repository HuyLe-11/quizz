"""SQLite persistence for exams, source PDFs, question media, and red-mark review."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")
DATA_DIR = BACKEND_DIR / "data"
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "quiz.sqlite3"))
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", DATA_DIR / "storage"))
ASSETS_DIR = STORAGE_DIR / "assets"
LEGACY_JSON_PATH = Path(os.getenv("QUIZ_DATA_FILE", DATA_DIR / "generated_exams.json"))


@contextmanager
def connect():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS exams (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                subject TEXT,
                year INTEGER,
                author TEXT,
                uploaded_by TEXT,
                duration_minutes INTEGER,
                source_file TEXT,
                source_path TEXT,
                source_sha256 TEXT,
                uploaded_at TEXT NOT NULL,
                extra TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS questions (
                id TEXT PRIMARY KEY,
                composite_id TEXT NOT NULL UNIQUE,
                exam_id TEXT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
                question_id INTEGER NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('mcp','true_false_ng','matching','fill_in')),
                multiple_choice INTEGER NOT NULL DEFAULT 0,
                question_content TEXT NOT NULL DEFAULT '[]',
                explanation_content TEXT NOT NULL DEFAULT '[]',
                source_question_number INTEGER,
                page_number INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE(exam_id, question_id)
            );
            CREATE TABLE IF NOT EXISTS mcp_options (
                id TEXT PRIMARY KEY,
                question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '[]',
                is_correct INTEGER NOT NULL DEFAULT 0,
                order_index INTEGER NOT NULL DEFAULT 0,
                UNIQUE(question_id, label)
            );
            CREATE TABLE IF NOT EXISTS match_pairs (
                id TEXT PRIMARY KEY,
                question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                description_content TEXT,
                answer_content TEXT NOT NULL DEFAULT '[]',
                is_distractor INTEGER NOT NULL DEFAULT 0,
                order_index INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS fill_in_answers (
                id TEXT PRIMARY KEY,
                question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                content TEXT NOT NULL DEFAULT '[]',
                order_index INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS question_assets (
                question_id TEXT PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
                image_path TEXT,
                page_number INTEGER,
                crop_coordinates TEXT,
                crop_status TEXT NOT NULL DEFAULT 'not_created',
                source TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS red_mark_detections (
                question_id TEXT PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
                detected_answer TEXT,
                confidence REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                page_number INTEGER,
                mark_coordinates TEXT,
                user_confirmed_answer TEXT,
                config_fingerprint TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quiz_attempts (
                id TEXT PRIMARY KEY,
                exam_id TEXT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
                score INTEGER NOT NULL,
                total_questions INTEGER NOT NULL,
                correct_answers_count INTEGER NOT NULL,
                time_spent INTEGER NOT NULL,
                answers TEXT NOT NULL,
                question_results TEXT NOT NULL,
                completed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_questions_exam ON questions(exam_id, question_id);
            CREATE INDEX IF NOT EXISTS idx_options_question ON mcp_options(question_id, order_index);
            CREATE INDEX IF NOT EXISTS idx_attempts_exam ON quiz_attempts(exam_id, completed_at DESC);
            """
        )
        question_columns = {row["name"] for row in connection.execute("PRAGMA table_info(questions)")}
        if "source_question_number" not in question_columns:
            connection.execute("ALTER TABLE questions ADD COLUMN source_question_number INTEGER")
        connection.execute("UPDATE questions SET source_question_number=question_id WHERE source_question_number IS NULL")
    migrate_legacy_json()


def _json(value: Any, default: Any) -> str:
    if value is None:
        value = default
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except json.JSONDecodeError:
            return json.dumps(default, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def migrate_legacy_json() -> None:
    """Import existing generated_exams.json once; keep the source file intact."""
    migration_name = "import_generated_exams_json_v1"
    if not LEGACY_JSON_PATH.exists():
        return
    with connect() as connection:
        if connection.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (migration_name,)).fetchone():
            return
        try:
            legacy = json.loads(LEGACY_JSON_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Could not import existing quiz history JSON.") from exc

        for exam in legacy.get("exams", []):
            connection.execute(
                """INSERT OR IGNORE INTO exams
                (id,name,subject,year,author,uploaded_by,duration_minutes,source_file,uploaded_at,extra)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    exam["id"], exam.get("name") or exam.get("title") or "Bài trắc nghiệm",
                    exam.get("subject"), exam.get("year"), exam.get("author"),
                    exam.get("uploaded_by"), exam.get("duration_minutes"), exam.get("source_file"),
                    exam.get("uploaded_at") or datetime.now(timezone.utc).isoformat(),
                    _json(exam.get("extra"), {}),
                ),
            )
        legacy_question_ids: dict[str, str] = {}
        for question in legacy.get("questions", []):
            same_number = connection.execute(
                "SELECT id FROM questions WHERE exam_id = ? AND question_id = ?",
                (question["exam_id"], question["question_id"]),
            ).fetchone()
            if same_number and same_number["id"] != question["id"]:
                # Old exports can contain the same question set twice under one exam.
                legacy_question_ids[question["id"]] = same_number["id"]
                incoming_question = _json(question.get("question_content"), [])
                incoming_explanation = _json(question.get("explanation_content"), [])
                connection.execute(
                    """UPDATE questions SET
                    question_content=CASE WHEN length(?) > length(question_content) THEN ? ELSE question_content END,
                    explanation_content=CASE WHEN length(?) > length(explanation_content) THEN ? ELSE explanation_content END,
                    multiple_choice=MAX(multiple_choice, ?) WHERE id=?""",
                    (incoming_question, incoming_question, incoming_explanation, incoming_explanation,
                     int(bool(question.get("multiple_choice"))), same_number["id"]),
                )
                continue

            composite_id = question["composite_id"]
            collision = connection.execute(
                "SELECT id FROM questions WHERE composite_id = ?", (composite_id,)
            ).fetchone()
            if collision and collision["id"] != question["id"]:
                # Legacy IDs were generated from filenames, which can repeat across exams.
                composite_id = f"{composite_id}__{question['exam_id']}__{question['question_id']}"
                suffix = 2
                while True:
                    collision = connection.execute(
                        "SELECT id FROM questions WHERE composite_id = ?", (composite_id,)
                    ).fetchone()
                    if not collision or collision["id"] == question["id"]:
                        break
                    composite_id = f"{question['composite_id']}__{question['exam_id']}__{question['question_id']}__{suffix}"
                    suffix += 1
            legacy_question_ids[question["id"]] = question["id"]
            connection.execute(
                """INSERT OR IGNORE INTO questions
                (id,composite_id,exam_id,question_id,type,multiple_choice,question_content,explanation_content,source_question_number,page_number,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    question["id"], composite_id, question["exam_id"],
                    question["question_id"], question["type"], int(bool(question.get("multiple_choice"))),
                    _json(question.get("question_content"), []),
                    _json(question.get("explanation_content"), []),
                    question.get("source_question_number", question["question_id"]), question.get("page_number"),
                    question.get("created_at") or datetime.now(timezone.utc).isoformat(),
                ),
            )
        for option in legacy.get("mcp_options", []):
            parent_id = legacy_question_ids.get(option["question_id"], option["question_id"])
            connection.execute(
                """INSERT INTO mcp_options (id,question_id,label,content,is_correct,order_index) VALUES (?,?,?,?,?,?)
                ON CONFLICT(question_id,label) DO UPDATE SET
                content=CASE WHEN length(excluded.content) > length(mcp_options.content) THEN excluded.content ELSE mcp_options.content END,
                is_correct=MAX(mcp_options.is_correct, excluded.is_correct)""",
                (option["id"], parent_id, option["label"], _json(option.get("content"), []),
                 int(bool(option.get("is_correct"))), option.get("order_index", 0)),
            )
        for index, pair in enumerate(legacy.get("match_pairs", [])):
            parent_id = legacy_question_ids.get(pair["question_id"], pair["question_id"])
            connection.execute(
                "INSERT OR IGNORE INTO match_pairs (id,question_id,description_content,answer_content,is_distractor,order_index) VALUES (?,?,?,?,?,?)",
                (pair.get("id") or f"legacy-match-{index}", parent_id,
                 _json(pair.get("description_content"), None) if pair.get("description_content") is not None else None,
                 _json(pair.get("answer_content"), []), int(bool(pair.get("is_distractor"))), pair.get("order_index", index)),
            )
        for index, answer in enumerate(legacy.get("fill_in_answers", [])):
            parent_id = legacy_question_ids.get(answer["question_id"], answer["question_id"])
            connection.execute(
                "INSERT OR IGNORE INTO fill_in_answers (id,question_id,content,order_index) VALUES (?,?,?,?)",
                (answer.get("id") or f"legacy-fill-{index}", parent_id,
                 _json(answer.get("content"), []), answer.get("order_index", index)),
            )
        connection.execute(
            "INSERT INTO schema_migrations (name,applied_at) VALUES (?,?)",
            (migration_name, datetime.now(timezone.utc).isoformat()),
        )


def insert_exam(exam: dict, questions: list[dict]) -> None:
    with connect() as connection:
        connection.execute(
            """INSERT INTO exams (id,name,subject,year,author,uploaded_by,duration_minutes,source_file,source_path,source_sha256,uploaded_at,extra)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (exam["id"], exam["name"], exam.get("subject"), exam.get("year"), exam.get("author"),
             exam.get("uploaded_by"), exam.get("duration_minutes"), exam.get("source_file"),
             exam.get("source_path"), exam.get("source_sha256"), exam["uploaded_at"], _json(exam.get("extra"), {})),
        )
        for item in questions:
            connection.execute(
                """INSERT INTO questions
            (id,composite_id,exam_id,question_id,type,multiple_choice,question_content,explanation_content,source_question_number,page_number,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (item["id"], item["composite_id"], exam["id"], item["question_id"], item["type"],
                 int(bool(item.get("multiple_choice"))), _json(item.get("question_content"), []),
             _json(item.get("explanation_content"), []), item.get("source_question_number", item["question_id"]),
             item.get("page_number"), item["created_at"]),
            )
            for option in item.get("mcp_options", []):
                connection.execute(
                    "INSERT INTO mcp_options (id,question_id,label,content,is_correct,order_index) VALUES (?,?,?,?,?,?)",
                    (option["id"], item["id"], option["label"], _json(option.get("content"), []),
                     int(bool(option.get("is_correct"))), option.get("order_index", 0)),
                )
            for pair in item.get("match_pairs", []):
                connection.execute(
                    "INSERT INTO match_pairs (id,question_id,description_content,answer_content,is_distractor,order_index) VALUES (?,?,?,?,?,?)",
                    (pair["id"], item["id"], _json(pair.get("description_content"), None) if pair.get("description_content") is not None else None,
                     _json(pair.get("answer_content"), []), int(bool(pair.get("is_distractor"))), pair.get("order_index", 0)),
                )
            for answer in item.get("fill_in_answers", []):
                connection.execute(
                    "INSERT INTO fill_in_answers (id,question_id,content,order_index) VALUES (?,?,?,?)",
                    (answer["id"], item["id"], _json(answer.get("content"), []), answer.get("order_index", 0)),
                )
            asset = item.get("asset")
            if asset:
                connection.execute(
                    """INSERT INTO question_assets
                    (question_id,image_path,page_number,crop_coordinates,crop_status,source,updated_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (item["id"], asset.get("image_path"), asset.get("page_number"),
                     json.dumps(asset.get("crop_coordinates"), ensure_ascii=False) if asset.get("crop_coordinates") else None,
                     asset.get("crop_status", "not_created"), asset.get("source"), datetime.now(timezone.utc).isoformat()),
                )


def list_exams() -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """SELECT e.id,e.name,e.subject,e.source_file,e.uploaded_at,
            e.source_path, e.source_path IS NOT NULL AS has_source_pdf, COUNT(q.id) AS question_count
            FROM exams e LEFT JOIN questions q ON q.exam_id=e.id
            GROUP BY e.id ORDER BY e.uploaded_at DESC"""
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["has_source_pdf"] = bool(item["has_source_pdf"] and item["source_path"] and Path(item["source_path"]).is_file())
            item.pop("source_path", None)
            result.append(item)
        return result


def get_exam_record(exam_id: str) -> dict | None:
    with connect() as connection:
        exam = connection.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
        if exam is None:
            return None
        questions = connection.execute(
            "SELECT * FROM questions WHERE exam_id = ? ORDER BY question_id", (exam_id,)
        ).fetchall()
        result = dict(exam)
        result["extra"] = json.loads(result["extra"] or "{}")
        source_available = bool(result.get("source_path") and Path(result["source_path"]).is_file())
        result["questions"] = []
        for question in questions:
            item = dict(question)
            item["has_source_pdf"] = source_available
            item["question_content"] = json.loads(item["question_content"] or "[]")
            item["explanation_content"] = json.loads(item["explanation_content"] or "[]")
            item["mcp_options"] = [dict(row) for row in connection.execute(
                "SELECT * FROM mcp_options WHERE question_id = ? ORDER BY order_index", (item["id"],)
            )]
            for option in item["mcp_options"]:
                option["content"] = json.loads(option["content"] or "[]")
            item["match_pairs"] = [dict(row) for row in connection.execute(
                "SELECT * FROM match_pairs WHERE question_id = ? ORDER BY order_index", (item["id"],)
            )]
            for pair in item["match_pairs"]:
                pair["description_content"] = json.loads(pair["description_content"]) if pair["description_content"] else None
                pair["answer_content"] = json.loads(pair["answer_content"] or "[]")
            item["fill_in_answers"] = [dict(row) for row in connection.execute(
                "SELECT * FROM fill_in_answers WHERE question_id = ? ORDER BY order_index", (item["id"],)
            )]
            for answer in item["fill_in_answers"]:
                answer["content"] = json.loads(answer["content"] or "[]")
            asset = connection.execute("SELECT * FROM question_assets WHERE question_id = ?", (item["id"],)).fetchone()
            item["asset"] = dict(asset) if asset else None
            if item["asset"] and item["asset"].get("crop_coordinates"):
                item["asset"]["crop_coordinates"] = json.loads(item["asset"]["crop_coordinates"])
            detection = connection.execute("SELECT * FROM red_mark_detections WHERE question_id = ?", (item["id"],)).fetchone()
            item["red_mark_detection"] = dict(detection) if detection else None
            if item["red_mark_detection"] and item["red_mark_detection"].get("mark_coordinates"):
                item["red_mark_detection"]["mark_coordinates"] = json.loads(item["red_mark_detection"]["mark_coordinates"])
            result["questions"].append(item)
        return result


def get_question_record(question_id: str) -> tuple[dict, dict] | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
        if row is None:
            return None
    exam = get_exam_record(row["exam_id"])
    if exam is None:
        return None
    question = next((item for item in exam["questions"] if item["id"] == question_id), None)
    return (exam, question) if question else None


def save_question_asset(question_id: str, asset: dict) -> None:
    with connect() as connection:
        connection.execute(
            """INSERT INTO question_assets (question_id,image_path,page_number,crop_coordinates,crop_status,source,updated_at)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT(question_id) DO UPDATE SET
            image_path=excluded.image_path,page_number=excluded.page_number,crop_coordinates=excluded.crop_coordinates,
            crop_status=excluded.crop_status,source=excluded.source,updated_at=excluded.updated_at""",
            (question_id, asset.get("image_path"), asset.get("page_number"),
             json.dumps(asset.get("crop_coordinates"), ensure_ascii=False) if asset.get("crop_coordinates") else None,
             asset.get("crop_status", "failed"), asset.get("source"), datetime.now(timezone.utc).isoformat()),
        )


def get_red_detection(question_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM red_mark_detections WHERE question_id = ?", (question_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        if result.get("mark_coordinates"):
            result["mark_coordinates"] = json.loads(result["mark_coordinates"])
        return result


def save_red_detection(question_id: str, detection: dict) -> None:
    with connect() as connection:
        connection.execute(
            """INSERT INTO red_mark_detections
            (question_id,detected_answer,confidence,status,page_number,mark_coordinates,user_confirmed_answer,config_fingerprint,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(question_id) DO UPDATE SET
            detected_answer=excluded.detected_answer,confidence=excluded.confidence,status=excluded.status,
            page_number=excluded.page_number,mark_coordinates=excluded.mark_coordinates,
            config_fingerprint=excluded.config_fingerprint,updated_at=excluded.updated_at""",
            (question_id, detection.get("detected_answer"), detection.get("confidence", 0), detection["status"],
             detection.get("page_number"), json.dumps(detection.get("mark_coordinates")) if detection.get("mark_coordinates") else None,
             None, detection["config_fingerprint"], datetime.now(timezone.utc).isoformat()),
        )


def confirm_red_answer(question_id: str, answer: str | None) -> dict:
    with connect() as connection:
        if answer is not None:
            option = connection.execute(
                "SELECT 1 FROM mcp_options WHERE question_id = ? AND label = ?", (question_id, answer)
            ).fetchone()
            if not option:
                raise HTTPException(422, "Đáp án xác nhận không tồn tại trong câu hỏi.")
        row = connection.execute("SELECT * FROM red_mark_detections WHERE question_id = ?", (question_id,)).fetchone()
        if row is None:
            raise HTTPException(409, "Hãy chạy nhận diện dấu đỏ trước khi xác nhận đáp án.")
        connection.execute(
            "UPDATE red_mark_detections SET user_confirmed_answer=?,updated_at=? WHERE question_id=?",
            (answer, datetime.now(timezone.utc).isoformat(), question_id),
        )
    return get_red_detection(question_id) or {}


def save_attempt(attempt: dict) -> None:
    with connect() as connection:
        if not connection.execute("SELECT 1 FROM exams WHERE id=?", (attempt["exam_id"],)).fetchone():
            raise HTTPException(404, "Không tìm thấy đề thi.")
        connection.execute(
            """INSERT INTO quiz_attempts
            (id,exam_id,score,total_questions,correct_answers_count,time_spent,answers,question_results,completed_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (attempt["id"], attempt["exam_id"], attempt["score"], attempt["total_questions"],
             attempt["correct_answers_count"], attempt["time_spent"], json.dumps(attempt["answers"], ensure_ascii=False),
             json.dumps(attempt["question_results"], ensure_ascii=False), attempt["completed_at"]),
        )


def list_attempts() -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """SELECT a.*, e.name AS exam_title FROM quiz_attempts a JOIN exams e ON e.id=a.exam_id
            ORDER BY a.completed_at DESC"""
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["answers"] = json.loads(item["answers"])
            item["question_results"] = json.loads(item["question_results"])
            items.append(item)
        return items
