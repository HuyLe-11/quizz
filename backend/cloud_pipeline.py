"""Cloud PDF pipeline with owner-scoped OCR and Storage resume.

PaddleOCR recognizes the document. Question parsing and image cropping
happen locally; CloudRepository persists the prepared bundle to Supabase.
"""

import hashlib
import logging
import tempfile
import uuid

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

try:
    from backend.cloud_checkpoint import CloudOCRCheckpoint
    from backend.cloud_repository import CloudRepository
    from backend.engine.paddle_client import extract_page_texts
    from backend.engine.smart_recognizer import (
        recognize_pdf_smart as recognize_pdf,
    )
    from backend.engine.pdf_tools import crop_question_pdf
    from backend.engine.quiz_parser import (
        parse_questions,
        parse_questions_by_pages,
    )
    from backend.network.main import (
        ALLOWED_MODELS,
        MAX_PDF_BYTES,
        PDF_LOCK,
        _assign_unique_question_ids,
        _needs_crop_fallback,
    )
except ModuleNotFoundError as exc:
    if exc.name != "backend":
        raise

    from cloud_checkpoint import CloudOCRCheckpoint
    from cloud_repository import CloudRepository
    from engine.paddle_client import extract_page_texts
    from engine.smart_recognizer import (
        recognize_pdf_smart as recognize_pdf,
    )
    from engine.pdf_tools import crop_question_pdf
    from engine.quiz_parser import (
        parse_questions,
        parse_questions_by_pages,
    )
    from network.main import (
        ALLOWED_MODELS,
        MAX_PDF_BYTES,
        PDF_LOCK,
        _assign_unique_question_ids,
        _needs_crop_fallback,
    )

logger = logging.getLogger("quizpdf")
Progress = Callable[[str], None]


def _fallback_question(exam_id: str) -> dict:
    return {
        "composite_id": f"{exam_id}_Q01",
        "question_id": 1,
        "type": "fill_in",
        "multiple_choice": False,
        "question_content": [
            {
                "type": "text",
                "value": (
                    "Chưa trích xuất được nội dung câu hỏi. "
                    "Hãy xem ảnh trang PDF để kiểm tra."
                ),
            },
        ],
        "explanation_content": [],
        "mcp_options": [],
        "match_pairs": [],
        "fill_in_answers": [],
        "page_number": 1,
        "parse_fallback": True,
    }


def _prepare_question(
    question: dict,
    *,
    exam_id: str,
    pdf_path: Path,
    crop_directory: Path,
    ocr_pages: list[dict],
    crop_images: dict[str, bytes],
) -> None:
    """Assign IDs and create an optional cropped question image."""
    question_id = str(uuid.uuid4())

    question["id"] = question_id
    question["created_at"] = (
        datetime.now(timezone.utc).isoformat()
    )

    for field in (
        "mcp_options",
        "match_pairs",
        "fill_in_answers",
    ):
        for index, child in enumerate(
            question.get(field, [])
        ):
            if not child.get("id"):
                child["id"] = str(uuid.uuid4())

            child.setdefault(
                "order_index",
                index,
            )

    default_asset = {
        "image_path": None,
        "page_number": question.get("page_number"),
        "crop_coordinates": None,
        "crop_status": "not_created",
        "source": None,
    }

    if not _needs_crop_fallback(question):
        question["asset"] = default_asset
        return

    relative_image = (
        f"{exam_id}/question-{question_id}.png"
    )
    output_path = (
        crop_directory / relative_image
    )

    try:
        with PDF_LOCK:
            asset = crop_question_pdf(
                pdf_path,
                question.get(
                    "source_question_number",
                    question["question_id"],
                ),
                question.get("page_number"),
                output_path,
                ocr_pages=ocr_pages,
            )

        if (
            asset.get("crop_status") != "success"
            or not output_path.is_file()
        ):
            raise RuntimeError(
                "Question crop was not created successfully."
            )

        image_bytes = output_path.read_bytes()

        if not image_bytes:
            raise RuntimeError(
                "Question crop is empty."
            )

        crop_images[relative_image] = image_bytes
        asset["image_path"] = relative_image
        question["asset"] = asset

        logger.info(
            "cloud=question_crop status=completed "
            "question_id=%s bytes=%d",
            question_id,
            len(image_bytes),
        )

    except Exception as exc:
        logger.warning(
            "cloud=question_crop status=failed "
            "question_id=%s error_type=%s",
            question_id,
            type(exc).__name__,
        )

        question["asset"] = {
            **default_asset,
            "crop_status": "failed",
        }


def process_cloud_pdf(
    contents: bytes,
    filename: str,
    model: str,
    user_id: str,
    access_token: str,
    progress: Progress | None = None,
) -> dict:
    """Prepare and persist a PDF, reusing owner-scoped OCR work on retry."""
    report = progress or (lambda _: None)

    if model not in ALLOWED_MODELS:
        raise ValueError(
            f"Unsupported OCR model: {model}"
        )

    if not contents.startswith(b"%PDF-"):
        raise ValueError(
            "The uploaded file is not a PDF."
        )

    if len(contents) > MAX_PDF_BYTES:
        raise ValueError(
            "The uploaded PDF exceeds the size limit."
        )

    if not access_token or not user_id:
        raise ValueError(
            "An authenticated Supabase user is required."
        )

    checkpoint = CloudOCRCheckpoint(
        user_id,
        filename,
        model,
        contents,
    )
    cached = checkpoint.load()

    if cached is not None:
        exam = cached.exam
        questions = cached.questions
        crop_images = cached.crop_images

        logger.info(
            "cloud=checkpoint status=restored "
            "exam=%s questions=%d cropped=%d",
            exam["id"],
            len(questions),
            len(crop_images),
        )

    else:
        exam_id = str(uuid.uuid4())
        name = (
            Path(filename).stem.strip()
            or "Bài trắc nghiệm PDF"
        )

        logger.info(
            "cloud=upload status=started "
            "exam=%s bytes=%d model=%s",
            exam_id,
            len(contents),
            model,
        )

        with tempfile.TemporaryDirectory(
            prefix="quizpdf-cloud-"
        ) as temporary:
            work_directory = Path(temporary)

            pdf_path = (
                work_directory / "source.pdf"
            )
            pdf_path.write_bytes(contents)

            report("ocr")

            markdown, raw_response = recognize_pdf(
                str(pdf_path),
                model,
            )

            ocr_pages = (
                raw_response.get("pages", [])
                if isinstance(raw_response, dict)
                else []
            )

            page_texts = extract_page_texts(
                raw_response
            )

            extraction_method = (
                raw_response.get(
                    "extraction_method",
                    "paddleocr",
                )
                if isinstance(raw_response, dict)
                else "paddleocr"
            )

            logger.info(
                "cloud=recognition status=completed "
                "exam=%s method=%s pages=%d characters=%d",
                exam_id,
                extraction_method,
                len(page_texts),
                len(markdown or ""),
            )

            report("question_extraction")

            questions = (
                parse_questions_by_pages(
                    page_texts,
                    name,
                )
                if page_texts
                else parse_questions(
                    markdown,
                    name,
                )
            )

            if not questions:
                questions = [
                    _fallback_question(exam_id)
                ]

            _assign_unique_question_ids(
                questions
            )

            logger.info(
                "cloud=question_extraction status=completed "
                "exam=%s questions=%d",
                exam_id,
                len(questions),
            )

            report("question_crops")

            crop_directory = (
                work_directory / "crops"
            )
            crop_images: dict[str, bytes] = {}

            normalized_pages = (
                ocr_pages
                if isinstance(ocr_pages, list)
                else []
            )

            for question in questions:
                _prepare_question(
                    question,
                    exam_id=exam_id,
                    pdf_path=pdf_path,
                    crop_directory=crop_directory,
                    ocr_pages=normalized_pages,
                    crop_images=crop_images,
                )

        exam = {
            "id": exam_id,
            "name": name,
            "subject": None,
            "year": None,
            "author": None,
            "uploaded_by": None,
            "duration_minutes": None,
            "source_file": filename,
            "source_path": None,
            "source_sha256": hashlib.sha256(
                contents
            ).hexdigest(),
            "uploaded_at": (
                datetime.now(
                    timezone.utc
                ).isoformat()
            ),
            "extra": {
                "ocr_model": model,
                "extraction_method": extraction_method,
                "ocr_markdown": markdown,
                "ocr_response": raw_response,
            },
        }

        try:
            checkpoint.save(
                exam,
                questions,
                crop_images,
            )

        except (
            OSError,
            ValueError,
            TypeError,
        ) as exc:
            logger.warning(
                "cloud=checkpoint status=save_failed "
                "error_type=%s",
                type(exc).__name__,
            )

    report("cloud_save")

    with CloudRepository(
        user_id,
        access_token,
    ) as repository:
        if cached is not None:
            # A previous RPC could have committed and lost its response.
            existing = repository.get_exam_record(
                exam["id"]
            )

            if existing is not None:
                checkpoint.delete()

                logger.info(
                    "cloud=checkpoint status=already_saved "
                    "exam=%s",
                    exam["id"],
                )

                return {
                    "exam_id": exam["id"],
                    "title": existing.get(
                        "name",
                        exam["name"],
                    ),
                    "question_count": len(
                        existing.get("questions") or []
                    ),
                }

        result = repository.save_exam_bundle(
            exam,
            questions,
            contents,
            crop_images,
            resume_existing=(cached is not None),
        )

    checkpoint.delete()

    logger.info(
        "cloud=upload status=completed "
        "exam=%s method=%s questions=%d cropped=%d",
        exam["id"],
        exam["extra"]["extraction_method"],
        len(questions),
        len(crop_images),
    )

    return result
