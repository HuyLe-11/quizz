"""Private PDF review operations backed by Supabase.

Question ownership is enforced by PostgreSQL RLS.
All Supabase requests use the authenticated user's JWT.
"""

import logging
import tempfile

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

from backend.cloud_repository import (
    BUCKET,
    CloudRepository,
)
from backend.engine.pdf_tools import (
    RedMarkConfig,
    crop_question_pdf,
    detect_red_mark_pdf,
)

logger = logging.getLogger("quizpdf")


class CloudReviewError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


class CloudReviewService:
    def __init__(self, repository: CloudRepository):
        self.repository = repository

    def _owned_question(
        self,
        question_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            question_id = str(UUID(question_id))
        except ValueError as exc:
            raise CloudReviewError(
                404,
                "Không tìm thấy câu hỏi.",
            ) from exc

        rows = self.repository._request(
            "GET",
            "/rest/v1/questions",
            params={
                "id": f"eq.{question_id}",
                "select": "id,exam_id",
            },
        )

        if not isinstance(rows, list) or not rows:
            raise CloudReviewError(
                404,
                "Không tìm thấy câu hỏi.",
            )

        exam = self.repository.get_exam_record(
            rows[0]["exam_id"]
        )

        if not exam:
            raise CloudReviewError(
                404,
                "Không tìm thấy đề thi.",
            )

        question = next(
            (
                item
                for item in exam.get("questions", [])
                if item["id"] == question_id
            ),
            None,
        )

        if question is None:
            raise CloudReviewError(
                404,
                "Không tìm thấy câu hỏi.",
            )

        return exam, question

    def _download_pdf(
        self,
        exam: dict[str, Any],
    ) -> bytes:
        path = exam.get("source_path")

        if not isinstance(path, str) or not path:
            raise CloudReviewError(
                409,
                "Đề thi không có PDF gốc.",
            )

        # CloudRepository validates the owner-specific
        # Storage prefix before downloading.
        pdf = self.repository.download_object(path)

        if not pdf.startswith(b"%PDF-"):
            raise CloudReviewError(
                409,
                "PDF gốc không hợp lệ.",
            )

        return pdf

    def _signed_image_url(
        self,
        object_path: str,
    ) -> str:
        required_prefix = (
            f"{self.repository.user_id}/assets/"
        )

        if not object_path.startswith(required_prefix):
            raise CloudReviewError(
                403,
                "Không có quyền truy cập ảnh.",
            )

        encoded = quote(
            object_path,
            safe="/",
        )

        result = self.repository._request(
            "POST",
            f"/storage/v1/object/sign/{BUCKET}/{encoded}",
            json_data={
                "expiresIn": 600,
            },
        )

        if not isinstance(result, dict):
            raise CloudReviewError(
                502,
                "Storage không trả về signed URL.",
            )

        url = (
            result.get("signedURL")
            or result.get("signedUrl")
        )

        if not isinstance(url, str):
            raise CloudReviewError(
                502,
                "Signed URL không hợp lệ.",
            )

        if url.startswith("/object/sign/"):
            return (
                self.repository.base_url
                + "/storage/v1"
                + url
            )

        if url.startswith("/storage/v1/"):
            return (
                self.repository.base_url
                + url
            )

        expected_prefix = (
            self.repository.base_url
            + "/storage/v1/object/sign/"
        )

        if url.startswith(expected_prefix):
            return url

        raise CloudReviewError(
            502,
            "Storage trả về địa chỉ ảnh không hợp lệ.",
        )

    @staticmethod
    def _ocr_pages(
        exam: dict[str, Any],
    ) -> list[dict[str, Any]]:
        extra = exam.get("extra") or {}

        if not isinstance(extra, dict):
            return []

        raw = extra.get("ocr_response") or {}

        if not isinstance(raw, dict):
            return []

        pages = raw.get("pages") or []

        return pages if isinstance(pages, list) else []

    def crop_image(
        self,
        question_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        exam, question = self._owned_question(
            question_id
        )

        existing = question.get("asset") or {}

        if (
            not force
            and existing.get("crop_status") == "success"
            and existing.get("image_path")
        ):
            return {
                **existing,
                "image_url": self._signed_image_url(
                    existing["image_path"]
                ),
                "cached": True,
            }

        pdf_contents = self._download_pdf(exam)

        object_path = (
            f"{self.repository.user_id}/assets/"
            f"{exam['id']}/question-{question_id}.png"
        )

        with tempfile.TemporaryDirectory(
            prefix="quizpdf-cloud-crop-"
        ) as directory:
            pdf_path = Path(directory) / "source.pdf"
            image_path = Path(directory) / "crop.png"

            pdf_path.write_bytes(pdf_contents)

            try:
                asset = crop_question_pdf(
                    pdf_path,
                    question.get(
                        "source_question_number",
                        question["question_id"],
                    ),
                    question.get("page_number"),
                    image_path,
                    ocr_pages=self._ocr_pages(exam),
                )

                if (
                    asset.get("crop_status") != "success"
                    or not image_path.is_file()
                ):
                    raise RuntimeError(
                        "The crop was not generated."
                    )

                image_bytes = image_path.read_bytes()

                if not image_bytes:
                    raise RuntimeError(
                        "The cropped image is empty."
                    )

            except Exception as exc:
                logger.exception(
                    "cloud=question_crop status=failed "
                    "question=%s",
                    question_id,
                )

                raise CloudReviewError(
                    422,
                    "Không thể tạo ảnh câu hỏi.",
                ) from exc

        # Upsert is allowed only for objects within the
        # user's private Storage directory by Storage RLS.
        encoded = quote(
            object_path,
            safe="/",
        )

        self.repository._request(
            "POST",
            f"/storage/v1/object/{BUCKET}/{encoded}",
            content=image_bytes,
            extra_headers={
                "Content-Type": "image/png",
                "x-upsert": "true",
            },
        )

        record = {
            "question_id": question_id,
            "image_path": object_path,
            "page_number": asset.get("page_number"),
            "crop_coordinates": asset.get(
                "crop_coordinates"
            ),
            "crop_status": "success",
            "source": asset.get("source"),
            "updated_at": (
                datetime.now(timezone.utc).isoformat()
            ),
        }

        self.repository._request(
            "POST",
            "/rest/v1/question_assets",
            params={
                "on_conflict": "question_id",
            },
            json_data=record,
            extra_headers={
                "Prefer": (
                    "resolution=merge-duplicates,"
                    "return=minimal"
                ),
            },
        )

        logger.info(
            "cloud=question_crop status=completed "
            "question=%s bytes=%d",
            question_id,
            len(image_bytes),
        )

        return {
            **record,
            "image_url": self._signed_image_url(
                object_path
            ),
            "cached": False,
        }

    def detect_red_mark(
        self,
        question_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        exam, question = self._owned_question(
            question_id
        )

        labels = [
            option["label"]
            for option in question.get(
                "mcp_options",
                [],
            )
        ]

        if not labels:
            raise CloudReviewError(
                422,
                "Câu hỏi không có lựa chọn để "
                "nhận diện dấu đỏ.",
            )

        config = RedMarkConfig.from_environment()

        cached = (
            question.get("red_mark_detection")
            or {}
        )

        if (
            not force
            and cached
            and cached.get("config_fingerprint")
                == config.fingerprint
        ):
            return {
                **cached,
                "cached": True,
            }

        pdf_contents = self._download_pdf(exam)

        pages = self._ocr_pages(exam)

        page_index = max(
            0,
            (question.get("page_number") or 1) - 1,
        )

        page_ocr = (
            pages[page_index]
            if page_index < len(pages)
            and isinstance(pages[page_index], dict)
            else None
        )

        with tempfile.TemporaryDirectory(
            prefix="quizpdf-cloud-red-",
        ) as directory:
            pdf_path = Path(directory) / "source.pdf"
            pdf_path.write_bytes(pdf_contents)

            try:
                detection = detect_red_mark_pdf(
                    pdf_path,
                    question.get(
                        "source_question_number",
                        question["question_id"],
                    ),
                    question.get("page_number"),
                    labels,
                    page_ocr=page_ocr,
                    config=config,
                )

            except Exception as exc:
                logger.exception(
                    "cloud=red_detection status=failed "
                    "question=%s",
                    question_id,
                )

                raise CloudReviewError(
                    422,
                    "Không thể nhận diện dấu đỏ.",
                ) from exc

        record = {
            **detection,
            "question_id": question_id,
            "user_confirmed_answer": cached.get(
                "user_confirmed_answer"
            ),
            "updated_at": (
                datetime.now(timezone.utc).isoformat()
            ),
        }

        result = self.repository._request(
            "POST",
            "/rest/v1/red_mark_detections",
            params={
                "on_conflict": "question_id",
            },
            json_data=record,
            extra_headers={
                "Prefer": (
                    "resolution=merge-duplicates,"
                    "return=representation"
                ),
            },
        )

        saved = (
            result[0]
            if isinstance(result, list) and result
            else record
        )

        return {
            **saved,
            "cached": False,
        }

    def confirm_red_answer(
        self,
        question_id: str,
        answer: str | None,
    ) -> dict[str, Any]:
        _, question = self._owned_question(
            question_id
        )

        labels = {
            option["label"]
            for option in question.get(
                "mcp_options",
                [],
            )
        }

        if answer is not None and answer not in labels:
            raise CloudReviewError(
                422,
                "Đáp án xác nhận không tồn tại "
                "trong câu hỏi.",
            )

        if not question.get("red_mark_detection"):
            raise CloudReviewError(
                409,
                "Hãy chạy nhận diện dấu đỏ "
                "trước khi xác nhận đáp án.",
            )

        result = self.repository._request(
            "PATCH",
            "/rest/v1/red_mark_detections",
            params={
                "question_id": f"eq.{question_id}",
            },
            json_data={
                "user_confirmed_answer": answer,
                "updated_at": (
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                ),
            },
            extra_headers={
                "Prefer": "return=representation",
            },
        )

        if not isinstance(result, list) or not result:
            raise CloudReviewError(
                409,
                "Không thể cập nhật xác nhận đáp án.",
            )

        return result[0]
