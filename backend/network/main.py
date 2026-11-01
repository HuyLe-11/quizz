"""QuizPDF API: PaddleOCR ingestion, SQLite history, PDF crops, and red-mark review."""

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    # Repository-root imports used by local development and Render.
    from backend import db
    from backend.engine.paddle_client import extract_page_texts, recognize_pdf
    from backend.engine.pdf_tools import RedMarkConfig, crop_question_pdf, detect_red_mark_pdf
    from backend.engine.quiz_parser import parse_questions, parse_questions_by_pages
except ModuleNotFoundError as exc:
    # Vercel runs this service with `backend/` as its import root.
    if exc.name != "backend":
        raise
    import db
    from engine.paddle_client import extract_page_texts, recognize_pdf
    from engine.pdf_tools import RedMarkConfig, crop_question_pdf, detect_red_mark_pdf
    from engine.quiz_parser import parse_questions, parse_questions_by_pages

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
MAX_PDF_BYTES = 30 * 1024 * 1024
ALLOWED_MODELS = {"PaddleOCR-VL-1.6", "PP-OCRv6"}
logger = logging.getLogger("quizpdf")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info(
        "phase=startup_database status=started database_path=%s storage_path=%s",
        db.DATABASE_PATH, db.STORAGE_DIR,
    )
    try:
        db.initialize_database()
    except PermissionError as exc:
        logger.exception(
            "phase=startup_database status=failed reason=storage_not_writable database_path=%s storage_path=%s",
            db.DATABASE_PATH, db.STORAGE_DIR,
        )
        raise RuntimeError(
            "Không thể ghi vào thư mục SQLite/storage. Kiểm tra DATABASE_PATH và STORAGE_DIR; "
            "Render cần Persistent Disk, còn Vercel chỉ hỗ trợ filesystem tạm trong /tmp."
        ) from exc
    except Exception:
        logger.exception("phase=startup_database status=failed")
        raise
    logger.info("phase=startup_database status=completed")
    yield


app = FastAPI(title="QuizPDF API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if origin.strip()],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type"],
)
app.mount("/api/assets", StaticFiles(directory=str(db.ASSETS_DIR), check_dir=False), name="assets")


class AttemptPayload(BaseModel):
    score: int = Field(ge=0, le=100)
    total_questions: int = Field(ge=0)
    correct_answers_count: int = Field(ge=0)
    time_spent: int = Field(ge=0)
    answers: dict[str, Any]
    question_results: list[dict[str, Any]]
    completed_at: int


class RedAnswerConfirmation(BaseModel):
    answer: str | None


def _json_blocks(blocks: list[dict]) -> str:
    return "\n".join(block.get("value", "") for block in blocks if isinstance(block, dict))


def _asset_url(relative_path: str | None) -> str | None:
    if not relative_path:
        return None
    return "/api/assets/" + "/".join(Path(relative_path).parts)


def _needs_crop_fallback(question: dict) -> bool:
    if question.get("parse_fallback"):
        return True
    text = " ".join(
        str(block.get("value", "")) for block in question.get("question_content", [])
        if isinstance(block, dict)
    ).strip()
    if len(text) < 40:
        return True
    if question.get("type") == "mcp" and len(question.get("mcp_options", [])) < 2:
        return True
    if question.get("type") == "matching" and len(question.get("match_pairs", [])) < 2:
        return True
    if question.get("type") == "fill_in" and not question.get("fill_in_answers"):
        return True
    return False


def _assign_unique_question_ids(questions: list[dict]) -> None:
    """Keep page-local PDF numbering while assigning DB/UI-safe IDs per exam."""
    source_numbers = [int(question.get("question_id") or index) for index, question in enumerate(questions, 1)]
    reserved = set(source_numbers)
    used: set[int] = set()
    next_number = max(reserved, default=0) + 1
    for index, question in enumerate(questions, 1):
        source_number = int(question.get("question_id") or index)
        question["source_question_number"] = source_number
        if source_number in used:
            while next_number in reserved or next_number in used:
                next_number += 1
            question["question_id"] = next_number
            question["composite_id"] = f"{question['composite_id']}__page{question.get('page_number') or 1}_{next_number}"
            used.add(next_number)
            next_number += 1
        else:
            question["question_id"] = source_number
            used.add(source_number)


def _question_response(question: dict) -> dict:
    detection = question.get("red_mark_detection")
    asset = question.get("asset")
    return {
        "question_id": question["question_id"],
        "question_record_id": question["id"],
        "has_source_pdf": bool(question.get("has_source_pdf")),
        "pdf_question_number": question.get("source_question_number", question["question_id"]),
        "type": question["type"],
        "multiple_choice": bool(question["multiple_choice"]),
        "question": _json_blocks(question["question_content"]),
        "options": [
            {"label": option["label"], "content": _json_blocks(option["content"])}
            for option in question["mcp_options"]
        ],
        "option_answer": [option["label"] for option in question["mcp_options"] if option["is_correct"]],
        "fill_in_answers": [_json_blocks(answer["content"]) for answer in question["fill_in_answers"]],
        "matches": [
            {"description": _json_blocks(pair["description_content"] or []), "answer": _json_blocks(pair["answer_content"])}
            for pair in question["match_pairs"]
        ],
        "explanation": _json_blocks(question["explanation_content"]),
        "page_number": question.get("page_number"),
        "question_image_url": _asset_url(asset.get("image_path") if asset else None),
        "crop_status": asset.get("crop_status") if asset else "not_created",
        "red_mark_detection": detection,
        "user_confirmed_answer": detection.get("user_confirmed_answer") if detection else None,
    }


@app.get("/api/health")
def health():
    return {"success": True, "data": {"status": "ok", "database": "sqlite"}}


@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...), model: str = Form("PaddleOCR-VL-1.6")):
    if file.content_type != "application/pdf" and not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Vui lòng tải lên tệp PDF.")
    if model not in ALLOWED_MODELS:
        raise HTTPException(400, f"Mô hình phải là một trong các lựa chọn: {', '.join(sorted(ALLOWED_MODELS))}.")

    contents = await file.read(MAX_PDF_BYTES + 1)
    if len(contents) > MAX_PDF_BYTES:
        raise HTTPException(413, "Dung lượng PDF không được vượt quá 30 MB.")
    if not contents.startswith(b"%PDF-"):
        raise HTTPException(400, "Tệp tải lên không phải PDF hợp lệ.")

    exam_id = str(uuid.uuid4())
    name = Path(file.filename or "bai_trac_nghiem.pdf").stem.strip() or "Bài trắc nghiệm PDF"
    logger.info("upload=%s phase=received bytes=%d model=%s", exam_id, len(contents), model)
    pdf_directory = db.STORAGE_DIR / "pdfs"
    pdf_directory.mkdir(parents=True, exist_ok=True)
    stored_pdf = pdf_directory / f"{exam_id}.pdf"
    tmp_path = None
    phase_started = time.perf_counter()
    logger.info("upload=%s phase=ocr status=started", exam_id)
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp_file:
            temp_file.write(contents)
            tmp_path = temp_file.name
        markdown, raw_response = recognize_pdf(tmp_path, model)
    except Exception as exc:
        logger.exception("upload=%s phase=ocr status=failed", exam_id)
        if "PADDLEOCR_ACCESS_TOKEN" in str(exc):
            raise HTTPException(503, "PaddleOCR chưa được cấu hình. Hãy đặt PADDLEOCR_ACCESS_TOKEN trong backend/.env.") from exc
        raise HTTPException(502, f"Không thể xử lý PDF bằng PaddleOCR ({type(exc).__name__}). Hãy kiểm tra cấu hình API và log backend.") from exc
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    pages = raw_response.get("pages", []) if isinstance(raw_response, dict) else []
    logger.info(
        "upload=%s phase=ocr status=completed duration_seconds=%.2f pages=%d markdown_chars=%d",
        exam_id, time.perf_counter() - phase_started, len(pages), len(markdown or ""),
    )

    phase_started = time.perf_counter()
    logger.info("upload=%s phase=question_extraction status=started", exam_id)
    page_texts = extract_page_texts(raw_response)
    questions = parse_questions_by_pages(page_texts, name) if page_texts else parse_questions(markdown, name)
    if not questions:
        # Keep a reviewable record even when OCR cannot identify numbered questions.
        questions = [{
            "composite_id": f"{exam_id}_Q01", "question_id": 1, "type": "fill_in",
            "multiple_choice": False,
            "question_content": [{"type": "text", "value": "Chưa trích xuất được nội dung câu hỏi. Hãy xem ảnh trang PDF để kiểm tra."}],
            "explanation_content": [], "mcp_options": [], "match_pairs": [],
            "fill_in_answers": [], "page_number": 1, "parse_fallback": True,
        }]
    _assign_unique_question_ids(questions)
    logger.info(
        "upload=%s phase=question_extraction status=completed duration_seconds=%.2f pages=%d questions=%d",
        exam_id, time.perf_counter() - phase_started, len(page_texts), len(questions),
    )

    phase_started = time.perf_counter()
    logger.info("upload=%s phase=source_pdf_save status=started", exam_id)
    try:
        stored_pdf.write_bytes(contents)
    except Exception:
        logger.exception("upload=%s phase=source_pdf_save status=failed", exam_id)
        raise HTTPException(500, "Không thể lưu PDF gốc trên máy chủ.") from None
    logger.info("upload=%s phase=source_pdf_save status=completed duration_seconds=%.2f", exam_id, time.perf_counter() - phase_started)

    questions_for_db = []
    ocr_pages = pages if isinstance(pages, list) else []
    phase_started = time.perf_counter()
    logger.info("upload=%s phase=question_crops status=started questions=%d", exam_id, len(questions))
    for index, question in enumerate(questions, 1):
        question_id = str(uuid.uuid4())
        question["id"] = question_id
        question["created_at"] = datetime.now(timezone.utc).isoformat()
        for field in ("mcp_options", "match_pairs", "fill_in_answers"):
            for child_index, child in enumerate(question.get(field, [])):
                if not child.get("id"):
                    child["id"] = str(uuid.uuid4())
                child.setdefault("order_index", child_index)
        # Keep the normal parser path cheap; create fallback crops for incomplete questions.
        if _needs_crop_fallback(question):
            crop_started = time.perf_counter()
            logger.info(
                "upload=%s phase=question_crop status=started question_index=%d question_number=%d source_question_number=%d page=%s",
                exam_id, index, question["question_id"], question["source_question_number"], question.get("page_number"),
            )
            try:
                relative_image = Path(exam_id) / f"question-{question_id}.png"
                image_path = db.ASSETS_DIR / relative_image
                asset = crop_question_pdf(
                    stored_pdf, question["source_question_number"], question.get("page_number"), image_path,
                    ocr_pages=ocr_pages,
                )
                asset["image_path"] = relative_image.as_posix()
                logger.info(
                    "upload=%s phase=question_crop status=completed question_index=%d source=%s duration_seconds=%.2f",
                    exam_id, index, asset.get("source"), time.perf_counter() - crop_started,
                )
            except Exception as exc:
                logger.exception(
                    "upload=%s phase=question_crop status=failed question_index=%d source_question_number=%d page=%s error_type=%s",
                    exam_id, index, question["source_question_number"], question.get("page_number"), type(exc).__name__,
                )
                asset = {"image_path": None, "page_number": question.get("page_number"),
                         "crop_coordinates": None, "crop_status": "failed", "source": None}
        else:
            asset = {"image_path": None, "page_number": question.get("page_number"),
                     "crop_coordinates": None, "crop_status": "not_created", "source": None}
        question["asset"] = asset
        questions_for_db.append(question)
    logger.info("upload=%s phase=question_crops status=completed duration_seconds=%.2f", exam_id, time.perf_counter() - phase_started)

    exam = {
        "id": exam_id, "name": name, "subject": None, "year": None, "author": None,
        "uploaded_by": None, "duration_minutes": None, "source_file": file.filename,
        "source_path": str(stored_pdf), "source_sha256": hashlib.sha256(contents).hexdigest(),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "extra": {"ocr_model": model, "ocr_markdown": markdown, "ocr_response": raw_response},
    }
    phase_started = time.perf_counter()
    logger.info("upload=%s phase=sqlite_save status=started questions=%d", exam_id, len(questions_for_db))
    try:
        db.insert_exam(exam, questions_for_db)
    except Exception as exc:
        stored_pdf.unlink(missing_ok=True)
        shutil.rmtree(db.ASSETS_DIR / exam_id, ignore_errors=True)
        logger.exception(
            "upload=%s phase=sqlite_save status=failed questions=%d error_type=%s error=%s",
            exam_id, len(questions_for_db), type(exc).__name__, str(exc),
        )
        raise HTTPException(500, f"Không thể lưu đề thi vào SQLite ({type(exc).__name__}). Hãy kiểm tra log backend theo mã {exam_id}.") from exc
    logger.info("upload=%s phase=sqlite_save status=completed duration_seconds=%.2f", exam_id, time.perf_counter() - phase_started)

    return {"success": True, "data": {"exam_id": exam_id, "title": name, "question_count": len(questions_for_db)}}


@app.get("/api/exams")
def list_processed_exams():
    return {"success": True, "data": db.list_exams()}


@app.get("/api/exams/{exam_id}")
def get_exam(exam_id: str):
    exam = db.get_exam_record(exam_id)
    if not exam:
        raise HTTPException(404, "Không tìm thấy đề thi.")
    questions = [_question_response(question) for question in exam["questions"]]
    return {
        "success": True,
        "data": {
            "id": exam["id"], "subject_id": exam.get("subject") or "uploaded",
            "title": exam["name"], "question_count": len(questions), "questions": questions,
            "source_file": exam.get("source_file"), "uploaded_at": exam.get("uploaded_at"),
            "has_source_pdf": bool(exam.get("source_path") and Path(exam["source_path"]).is_file()),
        },
    }


@app.post("/api/questions/{question_record_id}/crop-image")
def crop_question_image(question_record_id: str, force: bool = False):
    found = db.get_question_record(question_record_id)
    if not found:
        raise HTTPException(404, "Không tìm thấy câu hỏi.")
    exam, question = found
    pdf_path = exam.get("source_path")
    if not pdf_path or not Path(pdf_path).is_file():
        raise HTTPException(409, "Bản ghi này không có PDF gốc. Chỉ các PDF tải lên sau khi bật lưu trữ mới tạo được ảnh câu hỏi.")

    existing = question.get("asset")
    if not force and existing and existing["crop_status"] == "success" and existing.get("image_path"):
        if (db.ASSETS_DIR / existing["image_path"]).is_file():
            return {"success": True, "data": {**existing, "image_url": _asset_url(existing["image_path"]), "cached": True}}

    relative_image = Path(exam["id"]) / f"question-{question_record_id}.png"
    image_path = db.ASSETS_DIR / relative_image
    try:
        asset = crop_question_pdf(
            pdf_path, question.get("source_question_number", question["question_id"]), question.get("page_number"), image_path,
            ocr_pages=(exam.get("extra", {}).get("ocr_response", {}).get("pages", [])),
        )
        asset["image_path"] = relative_image.as_posix()
    except Exception as exc:
        asset = {"image_path": None, "page_number": question.get("page_number"),
                 "crop_coordinates": None, "crop_status": "failed", "source": None}
        db.save_question_asset(question_record_id, asset)
        raise HTTPException(422, f"Không thể tạo ảnh câu hỏi ({type(exc).__name__}).") from exc
    db.save_question_asset(question_record_id, asset)
    return {"success": True, "data": {**asset, "image_url": _asset_url(asset["image_path"]), "cached": False}}


@app.post("/api/questions/{question_record_id}/detect-red-mark")
def detect_question_red_mark(question_record_id: str, force: bool = False):
    found = db.get_question_record(question_record_id)
    if not found:
        raise HTTPException(404, "Không tìm thấy câu hỏi.")
    exam, question = found
    pdf_path = exam.get("source_path")
    if not pdf_path or not Path(pdf_path).is_file():
        raise HTTPException(409, "Bản ghi này không có PDF gốc để nhận diện dấu đỏ.")
    labels = [option["label"] for option in question["mcp_options"]]
    if not labels:
        raise HTTPException(422, "Câu hỏi này chưa có các lựa chọn để đối chiếu dấu đỏ.")

    config = RedMarkConfig.from_environment()
    cached = question.get("red_mark_detection")
    if not force and cached and cached["config_fingerprint"] == config.fingerprint:
        return {"success": True, "data": cached | {"cached": True}}

    raw_pages = exam.get("extra", {}).get("ocr_response", {}).get("pages", [])
    page_index = max(0, (question.get("page_number") or 1) - 1)
    page_ocr = raw_pages[page_index] if page_index < len(raw_pages) and isinstance(raw_pages[page_index], dict) else None
    try:
        result = detect_red_mark_pdf(
            pdf_path, question.get("source_question_number", question["question_id"]), question.get("page_number"), labels,
            page_ocr=page_ocr, config=config,
        )
    except Exception as exc:
        raise HTTPException(422, f"Không thể nhận diện dấu đỏ ({type(exc).__name__}).") from exc
    db.save_red_detection(question_record_id, result)
    saved = db.get_red_detection(question_record_id) or result
    return {"success": True, "data": saved | {"cached": False}}


@app.patch("/api/questions/{question_record_id}/red-mark-confirmation")
def confirm_question_red_mark(question_record_id: str, payload: RedAnswerConfirmation):
    found = db.get_question_record(question_record_id)
    if not found:
        raise HTTPException(404, "Không tìm thấy câu hỏi.")
    result = db.confirm_red_answer(question_record_id, payload.answer)
    return {"success": True, "data": result}


@app.post("/api/exams/{exam_id}/attempts")
def create_attempt(exam_id: str, payload: AttemptPayload):
    attempt = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    attempt["id"] = str(uuid.uuid4())
    attempt["exam_id"] = exam_id
    db.save_attempt(attempt)
    return {"success": True, "data": {"id": attempt["id"], "completed_at": attempt["completed_at"]}}


@app.get("/api/attempts")
def get_attempt_history():
    return {"success": True, "data": db.list_attempts()}
