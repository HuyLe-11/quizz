"""Local Parser Preview: real PaddleOCR, no database persistence.

Start with QUIZPDF_PARSER_PREVIEW=1 and bind Uvicorn to 127.0.0.1.
The browser retains processed OCR text for parser-only retries.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import time

from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
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
from backend.engine.parser_recovery import recover_pdf_headings
from backend.engine.quiz_parser import parse_questions_by_pages

logger = logging.getLogger("quizpdf.parser_preview")

MAX_PDF_BYTES = 30 * 1024 * 1024
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
            "recovered_headings": (
                recovered_headings or []
            ),
        },
        "timings": {
            "ocr_seconds": (
                round(ocr_seconds, 2)
                if ocr_seconds is not None
                else None
            ),
            "parser_seconds": round(
                parse_seconds,
                4,
            ),
        },
    }


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
    """Use real OCR, restore verified headers, then parse."""
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
            path = (
                Path(directory) / "source.pdf"
            )
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

    elapsed = (
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

    # Only restore headings directly supported
    # by the original PDF text layer.
    enhanced, recovered = (
        await run_in_threadpool(
            recover_pdf_headings,
            contents,
            page_texts,
        )
    )

    logger.info(
        "preview=ocr status=completed "
        "pages=%d ocr_seconds=%.2f "
        "headings_recovered=%d",
        len(page_texts),
        elapsed,
        len(recovered),
    )

    result = _parse_result(
        filename,
        model,
        enhanced,
        elapsed,
        recovered_headings=[
            item.as_dict()
            for item in recovered
        ],
    )

    # Preserve unmodified provider text for later comparison.
    result["raw_page_texts"] = original_texts

    return {
        "success": True,
        "data": result,
    }


@app.post("/api/parser-preview/reparse")
def reparse_existing_ocr(
    payload: ReparseRequest,
):
    """Parse existing text without running OCR again."""
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
