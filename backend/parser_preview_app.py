"""Local Parser Preview: real PaddleOCR, no database persistence.

Run with QUIZPDF_PARSER_PREVIEW=1, bound to localhost.
The reprocess endpoint reuses original OCR and PDF bytes without
calling PaddleOCR again.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time

from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv(
    Path(__file__).resolve().parent / ".env"
)

if os.getenv("QUIZPDF_PARSER_PREVIEW") != "1":
    raise RuntimeError(
        "Parser Preview is disabled. "
        "Set QUIZPDF_PARSER_PREVIEW=1."
    )

from backend.engine.paddle_client import (
    PaddleOCRAPIError,
    PaddleOCRTimeoutError,
    extract_page_texts,
    recognize_pdf,
)
from backend.engine.parser_recovery import (
    recover_pdf_headings,
)
from backend.engine.pdf_fidelity import (
    enhance_ocr_with_pdf_geometry,
)
from backend.engine.parser_text_normalizer import (
    normalize_pdf_backed_ocr,
)
from backend.engine.quiz_parser import (
    parse_questions_by_pages,
)

logger = logging.getLogger("quizpdf.parser_preview")

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
    re.IGNORECASE,
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


def _question_text(blocks: Any) -> str:
    if not isinstance(blocks, list):
        return ""

    return "\n".join(
        str(block.get("value", ""))
        for block in blocks
        if isinstance(block, dict)
    )


def _parse_result(
    filename: str,
    model: str,
    page_texts: list[str],
    ocr_seconds: float | None,
    *,
    recovered_headings: list[dict] | None = None,
    fidelity_repairs: list[dict] | None = None,
    normalized_repairs: list[dict] | None = None,
    preprocess_seconds: float | None = None,
) -> dict:
    if model not in ALLOWED_MODELS:
        raise HTTPException(
            400,
            "Mô hình OCR không hợp lệ.",
        )

    if (
        not page_texts
        or len(page_texts) > MAX_REPARSE_PAGES
    ):
        raise HTTPException(
            422,
            "Số lượng trang OCR không hợp lệ.",
        )

    if any(
        not isinstance(page, str)
        or len(page) > MAX_TEXT_PER_PAGE
        for page in page_texts
    ):
        raise HTTPException(
            413,
            "Một trang OCR không hợp lệ hoặc quá dài.",
        )

    started = time.perf_counter()

    questions = parse_questions_by_pages(
        page_texts,
        Path(filename).stem or "Parser Preview",
    )

    parse_seconds = (
        time.perf_counter() - started
    )

    incomplete = []
    contaminated = []
    needs_review = []

    for question in questions:
        number = question["question_id"]
        choices = question.get(
            "mcp_options",
            [],
        )

        if [
            choice.get("label")
            for choice in choices
        ] != list("ABCD"):
            incomplete.append(number)

        if any(
            SECTION_IN_OPTION.search(
                _question_text(
                    choice.get("content")
                )
            )
            for choice in choices
        ):
            contaminated.append(number)

        if question.get("parse_fallback"):
            needs_review.append(number)

    return {
        "filename": filename,
        "model": model,
        "page_count": len(page_texts),
        "page_texts": page_texts,
        "question_count": len(questions),
        "questions": questions,
        "diagnostics": {
            "incomplete_options": incomplete,
            "contaminated_options": contaminated,
            "needs_review": needs_review,
            "recovered_headings": recovered_headings or [],
            "pdf_fidelity_repairs": fidelity_repairs or [],
            "normalized_repairs": normalized_repairs or [],
        },
        "timings": {
            "ocr_seconds": (
                round(ocr_seconds, 2)
                if ocr_seconds is not None
                else None
            ),
            "parser_seconds": round(
                parse_seconds, 4
            ),
            "preprocess_seconds": (
                round(preprocess_seconds, 4)
                if preprocess_seconds is not None
                else None
            ),
        },
    }


def _rebuild_from_pdf(
    contents: bytes,
    filename: str,
    model: str,
    original_texts: list[str],
    ocr_seconds: float | None,
) -> dict:
    """Replay all local transformations without running OCR."""
    preprocess_started = time.perf_counter()

    enhanced, recovered = recover_pdf_headings(
        contents,
        original_texts,
    )

    enriched, fidelity_repairs = (
        enhance_ocr_with_pdf_geometry(
            contents,
            enhanced,
        )
    )

    stabilized, normalized_repairs = (
        normalize_pdf_backed_ocr(
            contents,
            enriched,
        )
    )

    preprocess_seconds = (
        time.perf_counter() - preprocess_started
    )

    result = _parse_result(
        filename,
        model,
        stabilized,
        ocr_seconds,
        recovered_headings=[
            item.as_dict()
            for item in recovered
        ],
        fidelity_repairs=[
            item.as_dict()
            for item in fidelity_repairs
        ],
        normalized_repairs=normalized_repairs,
        preprocess_seconds=preprocess_seconds,
    )

    result["raw_page_texts"] = original_texts
    return result


@app.get("/api/health")
def health():
    return {
        "success": True,
        "data": {
            "status": "ok",
            "mode": "parser-preview",
            "database": "disabled",
        },
    }


@app.post("/api/parser-preview/ocr")
async def recognize_and_preview(
    file: UploadFile = File(...),
    model: str = Form("PaddleOCR-VL-1.6"),
):
    """Call real PaddleOCR once, then run the local parser."""
    if model not in ALLOWED_MODELS:
        raise HTTPException(
            400,
            "Mô hình OCR không hợp lệ.",
        )

    filename = Path(
        file.filename or "test.pdf"
    ).name

    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            400,
            "Chỉ chấp nhận file PDF.",
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

    started = time.perf_counter()

    try:
        with tempfile.TemporaryDirectory(
            prefix="quizpdf-parser-preview-"
        ) as directory:
            path = Path(directory) / "source.pdf"
            path.write_bytes(contents)

            markdown, raw_response = (
                await run_in_threadpool(
                    recognize_pdf,
                    str(path),
                    model,
                )
            )

    except PaddleOCRTimeoutError as exc:
        logger.warning(
            "preview=ocr status=timeout"
        )
        raise HTTPException(
            504,
            "PaddleOCR vượt thời gian chờ.",
        ) from exc

    except PaddleOCRAPIError as exc:
        logger.warning(
            "preview=ocr status=provider_error http=%d",
            exc.status_code,
        )
        raise HTTPException(
            502,
            f"PaddleOCR trả HTTP {exc.status_code}.",
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

    ocr_seconds = (
        time.perf_counter() - started
    )

    page_texts = extract_page_texts(
        raw_response
    )

    if not page_texts and markdown:
        page_texts = [markdown]

    if not page_texts:
        raise HTTPException(
            422,
            "PaddleOCR không trả về văn bản trang nào.",
        )

    original_texts = list(page_texts)

    result = await run_in_threadpool(
        _rebuild_from_pdf,
        contents,
        filename,
        model,
        original_texts,
        ocr_seconds,
    )

    logger.info(
        "preview=completed pages=%d questions=%d "
        "ocr_seconds=%.2f preprocess_seconds=%.3f "
        "parser_seconds=%.4f",
        result["page_count"],
        result["question_count"],
        ocr_seconds,
        result["timings"]["preprocess_seconds"],
        result["timings"]["parser_seconds"],
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
):
    """Rebuild from original OCR and PDF without another OCR API call."""
    if model not in ALLOWED_MODELS:
        raise HTTPException(
            400,
            "Mô hình OCR không hợp lệ.",
        )

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

    if (
        len(contents) > MAX_PDF_BYTES
        or not contents.startswith(b"%PDF-")
    ):
        raise HTTPException(
            400,
            "Tệp PDF không hợp lệ hoặc quá lớn.",
        )

    if (
        len(raw_page_texts_json.encode("utf-8"))
        > MAX_REPROCESS_TEXT_BYTES
    ):
        raise HTTPException(
            413,
            "Dữ liệu OCR quá lớn.",
        )

    try:
        original_texts = json.loads(
            raw_page_texts_json
        )
    except json.JSONDecodeError as exc:
        raise HTTPException(
            422,
            "JSON OCR không hợp lệ.",
        ) from exc

    if (
        not isinstance(original_texts, list)
        or not 1 <= len(original_texts) <= MAX_REPARSE_PAGES
        or any(
            not isinstance(page, str)
            or len(page) > MAX_TEXT_PER_PAGE
            for page in original_texts
        )
    ):
        raise HTTPException(
            422,
            "Danh sách trang OCR không hợp lệ.",
        )

    result = await run_in_threadpool(
        _rebuild_from_pdf,
        contents,
        filename,
        model,
        original_texts,
        None,
    )

    logger.info(
        "preview=reprocess status=completed "
        "questions=%d preprocess_seconds=%.3f "
        "parser_seconds=%.4f ocr_calls=0",
        result["question_count"],
        result["timings"]["preprocess_seconds"],
        result["timings"]["parser_seconds"],
    )

    return {
        "success": True,
        "data": result,
    }


@app.post("/api/parser-preview/reparse")
def reparse_existing_ocr(
    payload: ReparseRequest,
):
    """Legacy parser-only reparse; does not recompute PDF geometry."""
    result = _parse_result(
        Path(payload.filename).name,
        payload.model,
        payload.page_texts,
        None,
    )

    logger.info(
        "preview=reparse status=completed questions=%d",
        result["question_count"],
    )

    return {
        "success": True,
        "data": result,
    }