"""Local parser preview: live OCR or explicit read-only replay of saved OCR."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pymupdf
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.engine.paddle_client import (
    PaddleOCRAPIError,
    PaddleOCRTimeoutError,
    extract_page_texts,
    recognize_pdf,
)

from backend.engine.parser_pipeline import (
    normalize_option_layout,
    parse_pdf_questions,
    parse_text_questions,
)


BACKEND_DIR = Path(__file__).resolve().parent

load_dotenv(BACKEND_DIR / ".env")

if os.getenv("QUIZPDF_PARSER_PREVIEW") != "1":
    raise RuntimeError(
        "Parser Preview is disabled. "
        "Set QUIZPDF_PARSER_PREVIEW=1."
    )

logger = logging.getLogger(
    "quizpdf.parser_preview"
)

MAX_PDF_BYTES = 30 * 1024 * 1024
MAX_REPROCESS_TEXT_BYTES = 20 * 1024 * 1024
MAX_REPARSE_PAGES = 100
MAX_TEXT_PER_PAGE = 1_000_000

ALLOWED_MODELS = {
    "PaddleOCR-VL-1.6",
    "PP-OCRv6",
}

SECTION_IN_OPTION = re.compile(
    r"(?:Mark\s+the\s+letter\b|"
    r"Read\s+the\s+following\s+passage\b)",
    re.I,
)

EXPECT_FOUR = re.compile(
    r"\bA\s*,?\s*B\s*,?\s*C\s*,?\s*"
    r"(?:or|hoặc)?\s*D\b",
    re.I,
)

app = FastAPI(
    title="QuizPDF Parser Preview"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class ReparseRequest(BaseModel):
    filename: str
    model: str = "PaddleOCR-VL-1.6"
    page_texts: list[str]


def _mode() -> str:
    mode = os.getenv(
        "QUIZPDF_PREVIEW_OCR_MODE",
        "live",
    ).strip().lower()

    if mode not in {"live", "cache"}:
        raise HTTPException(
            500,
            "QUIZPDF_PREVIEW_OCR_MODE phải là live hoặc cache.",
        )

    return mode


def _check_model(model: str) -> None:
    if model not in ALLOWED_MODELS:
        raise HTTPException(
            400,
            "Mô hình OCR không hợp lệ.",
        )


def _check_pages(pages: Any) -> None:
    if (
        not isinstance(pages, list)
        or not 1 <= len(pages) <= MAX_REPARSE_PAGES
    ):
        raise HTTPException(
            422,
            "Số lượng trang OCR không hợp lệ.",
        )

    if any(
        not isinstance(page, str)
        or len(page) > MAX_TEXT_PER_PAGE
        for page in pages
    ):
        raise HTTPException(
            422,
            "Một trang OCR không hợp lệ hoặc quá dài.",
        )


async def _read_pdf(
    file: UploadFile,
) -> tuple[str, bytes]:
    filename = Path(
        file.filename or "test.pdf"
    ).name

    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            400,
            "Chỉ chấp nhận PDF.",
        )

    contents = await file.read(
        MAX_PDF_BYTES + 1
    )

    if len(contents) > MAX_PDF_BYTES:
        raise HTTPException(
            413,
            "PDF vượt quá 30 MB.",
        )

    if not contents.startswith(b"%PDF-"):
        raise HTTPException(
            400,
            "Nội dung PDF không hợp lệ.",
        )

    try:
        with pymupdf.open(
            stream=contents,
            filetype="pdf",
        ) as document:
            if (
                document.needs_pass
                or not len(document)
            ):
                raise ValueError(
                    "Encrypted or empty PDF"
                )

    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            400,
            "Không thể mở PDF hoặc PDF có mật khẩu.",
        ) from exc

    return filename, contents


def _load_saved_ocr(
    contents: bytes,
    model: str,
) -> list[str]:
    """Read an exact PDF hash/model match without modifying SQLite."""
    path = Path(
        os.getenv(
            "DATABASE_PATH",
            str(
                BACKEND_DIR
                / "data"
                / "quiz.sqlite3"
            ),
        )
    ).resolve()

    if not path.is_file():
        raise HTTPException(
            404,
            "Không tìm thấy SQLite chứa OCR cũ.",
        )

    digest = hashlib.sha256(
        contents
    ).hexdigest()

    try:
        with closing(
            sqlite3.connect(
                path.as_uri() + "?mode=ro",
                uri=True,
            )
        ) as connection:
            rows = connection.execute(
                """
                SELECT extra
                FROM exams
                WHERE source_sha256 = ?
                ORDER BY uploaded_at DESC
                """,
                (digest,),
            )

            for (extra,) in rows:
                try:
                    data = json.loads(extra)
                except (TypeError, ValueError):
                    continue

                if (
                    not isinstance(data, dict)
                    or data.get("ocr_model") != model
                ):
                    continue

                raw = data.get("ocr_response")

                if isinstance(raw, dict):
                    pages = extract_page_texts(raw)

                    if pages and any(
                        page.strip() for page in pages
                    ):
                        return pages

    except sqlite3.Error as exc:
        raise HTTPException(
            500,
            "Không đọc được OCR đã lưu trong SQLite.",
        ) from exc

    raise HTTPException(
        404,
        "Không có OCR đã lưu khớp SHA-256 và model. "
        "Hãy dùng chế độ live.",
    )


def _block_text(blocks: list[dict]) -> str:
    return "\n".join(
        str(block.get("value", ""))
        for block in blocks
    ).strip()


def _response(
    filename: str,
    model: str,
    parsed: dict,
    ocr_seconds: float | None,
) -> dict:
    incomplete = []
    contaminated = []
    review = []

    for question in parsed["questions"]:
        number = question["question_id"]

        choices = question.get(
            "mcp_options",
            [],
        )

        labels = [
            choice.get("label")
            for choice in choices
        ]

        expected = [
            chr(ord("A") + index)
            for index in range(len(labels))
        ]

        four = bool(
            EXPECT_FOUR.search(
                question.get(
                    "section_instruction",
                    "",
                )
            )
        )

        if (
            question["type"] == "mcp"
            and (
                len(labels) < 2
                or labels != expected
                or (four and len(labels) != 4)
                or any(
                    not _block_text(choice["content"])
                    for choice in choices
                )
            )
        ):
            incomplete.append(number)

        if any(
            SECTION_IN_OPTION.search(
                _block_text(choice["content"])
            )
            for choice in choices
        ):
            contaminated.append(number)

        if question.get("parse_fallback"):
            review.append(number)

    return {
        "filename": filename,
        "model": model,
        "page_count": len(
            parsed["page_texts"]
        ),
        "page_texts": parsed["page_texts"],
        "raw_page_texts": parsed[
            "raw_page_texts"
        ],
        "question_count": len(
            parsed["questions"]
        ),
        "questions": parsed["questions"],
        "diagnostics": {
            **parsed["diagnostics"],
            "incomplete_options": incomplete,
            "contaminated_options": contaminated,
            "needs_review": review,
        },
        "timings": {
            **parsed["timings"],
            "ocr_seconds": (
                round(ocr_seconds, 2)
                if ocr_seconds is not None
                else None
            ),
        },
    }


def _rebuild_from_pdf(
    contents: bytes,
    filename: str,
    model: str,
    pages: list[str],
    ocr_seconds: float | None,
) -> dict:
    _check_pages(pages)

    with pymupdf.open(
        stream=contents,
        filetype="pdf",
    ) as document:
        if len(document) != len(pages):
            raise HTTPException(
                422,
                "Số trang OCR không khớp PDF gốc; "
                "không thể gán tọa độ an toàn.",
            )

    parsed = parse_pdf_questions(
        contents,
        pages,
        Path(filename).stem,
        embed_context=False,
    )

    return _response(
        filename,
        model,
        parsed,
        ocr_seconds,
    )


@app.get("/api/health")
def health() -> dict:
    return {
        "success": True,
        "data": {
            "status": "ok",
            "mode": "parser-preview",
            "database": (
                "read-only-cache"
                if _mode() == "cache"
                else "disabled"
            ),
            "ocr_mode": _mode(),
        },
    }


@app.post("/api/parser-preview/ocr")
async def recognize_and_preview(
    file: UploadFile = File(...),
    model: str = Form("PaddleOCR-VL-1.6"),
) -> dict:
    _check_model(model)

    filename, contents = await _read_pdf(
        file
    )

    if _mode() == "cache":
        pages = await run_in_threadpool(
            _load_saved_ocr,
            contents,
            model,
        )

        elapsed = None

    else:
        started = time.perf_counter()

        try:
            with tempfile.TemporaryDirectory(
                prefix="quizpdf-parser-preview-"
            ) as directory:
                path = (
                    Path(directory)
                    / "source.pdf"
                )

                path.write_bytes(contents)

                _, raw = await run_in_threadpool(
                    recognize_pdf,
                    str(path),
                    model,
                )

            pages = extract_page_texts(raw)

        except PaddleOCRTimeoutError as exc:
            logger.warning(
                "preview=ocr status=timeout reason=%s",
                str(exc),
            )

            raise HTTPException(
                504,
                f"PaddleOCR vượt thời gian chờ: {exc}",
            ) from exc

        except PaddleOCRAPIError as exc:
            raise HTTPException(
                502,
                f"PaddleOCR trả HTTP {exc.status_code} "
                f"ở bước {exc.stage}.",
            ) from exc

        except Exception as exc:
            logger.exception(
                "preview=ocr status=failed error_type=%s",
                type(exc).__name__,
            )

            raise HTTPException(
                502,
                "PaddleOCR không xử lý được PDF. "
                "Kiểm tra backend log.",
            ) from exc

        elapsed = time.perf_counter() - started

    if not pages or not any(
        page.strip() for page in pages
    ):
        raise HTTPException(
            422,
            "PaddleOCR không trả về văn bản trang nào.",
        )

    result = await run_in_threadpool(
        _rebuild_from_pdf,
        contents,
        filename,
        model,
        pages,
        elapsed,
    )

    logger.info(
        "preview=completed mode=%s pages=%d questions=%d",
        _mode(),
        result["page_count"],
        result["question_count"],
    )

    return {
        "success": True,
        "data": result,
    }


@app.post("/api/parser-preview/reprocess")
async def reprocess_without_ocr(
    file: UploadFile = File(...),
    raw_page_texts_json: str = Form(...),
    model: str = Form("PaddleOCR-VL-1.6"),
) -> dict:
    _check_model(model)

    filename, contents = await _read_pdf(
        file
    )

    if (
        len(
            raw_page_texts_json.encode("utf-8")
        )
        > MAX_REPROCESS_TEXT_BYTES
    ):
        raise HTTPException(
            413,
            "Dữ liệu OCR quá lớn.",
        )

    try:
        pages = json.loads(
            raw_page_texts_json
        )

    except json.JSONDecodeError as exc:
        raise HTTPException(
            422,
            "JSON OCR không hợp lệ.",
        ) from exc

    _check_pages(pages)

    result = await run_in_threadpool(
        _rebuild_from_pdf,
        contents,
        filename,
        model,
        pages,
        None,
    )

    return {
        "success": True,
        "data": result,
    }


@app.post("/api/parser-preview/reparse")
def reparse_existing_ocr(
    payload: ReparseRequest,
) -> dict:
    _check_model(payload.model)
    _check_pages(payload.page_texts)

    started = time.perf_counter()

    pages = normalize_option_layout(
        payload.page_texts
    )

    questions = parse_text_questions(
        pages,
        Path(payload.filename).stem,
    )

    parsed = {
        "raw_page_texts": payload.page_texts,
        "page_texts": pages,
        "questions": questions,
        "diagnostics": {
            "recovered_headings": [],
            "pdf_fidelity_repairs": [],
            "normalized_repairs": [],
            "final_choice_repairs": [],
            "verified_correction_questions": [],
        },
        "timings": {
            "preprocess_seconds": None,
            "parser_seconds": round(
                time.perf_counter() - started,
                4,
            ),
        },
    }

    return {
        "success": True,
        "data": _response(
            Path(payload.filename).name,
            payload.model,
            parsed,
            None,
        ),
    }