"""Authenticated Supabase persistence for QuizPDF.

This module does not replace SQLite until the API pipeline is
connected in the next integration phase.
"""

import copy
import logging
import os
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import quote
from uuid import UUID

import httpx

logger = logging.getLogger("quizpdf")

BUCKET = "quizpdf-private"


class CloudRepositoryError(RuntimeError):
    """Supabase persistence or Storage operation failed."""


class CloudRepository:
    def __init__(
        self,
        user_id: str,
        access_token: str,
        *,
        client: httpx.Client | None = None,
        base_url: str | None = None,
        publishable_key: str | None = None,
    ):
        try:
            self.user_id = str(UUID(user_id))
        except ValueError as exc:
            raise ValueError(
                "Invalid authenticated user ID."
            ) from exc

        if not access_token:
            raise ValueError(
                "Supabase access token is required."
            )

        self.base_url = (
            base_url
            or os.getenv("SUPABASE_URL", "")
        ).rstrip("/")

        self.publishable_key = (
            publishable_key
            or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
        )

        if not self.base_url or not self.publishable_key:
            raise CloudRepositoryError(
                "SUPABASE_URL and "
                "SUPABASE_PUBLISHABLE_KEY are required."
            )

        self.access_token = access_token

        self._owns_client = client is None

        self.client = client or httpx.Client(
            timeout=60.0
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "CloudRepository":
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_data: Any = None,
        content: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        headers = {
            "apikey": self.publishable_key,
            "Authorization": (
                f"Bearer {self.access_token}"
            ),
        }

        if extra_headers:
            headers.update(extra_headers)

        try:
            response = self.client.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                params=params,
                json=json_data,
                content=content,
            )

        except httpx.RequestError as exc:
            raise CloudRepositoryError(
                f"Could not reach Supabase: {type(exc).__name__}"
            ) from exc

        if response.status_code >= 400:
            detail = response.text[:400]

            raise CloudRepositoryError(
                f"Supabase {method} {path} failed "
                f"(HTTP {response.status_code}): {detail}"
            )

        if not response.content:
            return None

        try:
            return response.json()
        except ValueError:
            return response.content

    def _object_path(
        self,
        category: str,
        name: str,
    ) -> str:
        relative = PurePosixPath(name)

        if (
            relative.is_absolute()
            or not name
            or "\\" in name
            or ".." in relative.parts
            or "." in relative.parts
        ):
            raise ValueError(
                "Invalid Supabase Storage object path."
            )

        if category not in {"pdfs", "assets"}:
            raise ValueError(
                "Unsupported Storage category."
            )

        return (
            f"{self.user_id}/{category}/"
            f"{relative.as_posix()}"
        )

    def upload_object(
        self,
        object_path: str,
        contents: bytes,
        content_type: str,
    ) -> None:
        encoded = quote(
            object_path,
            safe="/",
        )

        self._request(
            "POST",
            f"/storage/v1/object/{BUCKET}/{encoded}",
            content=contents,
            extra_headers={
                "Content-Type": content_type,
                "x-upsert": "false",
            },
        )

    def download_object(
        self,
        object_path: str,
    ) -> bytes:
        if not object_path.startswith(
            f"{self.user_id}/"
        ):
            raise ValueError(
                "Cannot download another user's object."
            )

        encoded = quote(
            object_path,
            safe="/",
        )

        data = self._request(
            "GET",
            f"/storage/v1/object/authenticated/"
            f"{BUCKET}/{encoded}",
        )

        if not isinstance(data, bytes):
            raise CloudRepositoryError(
                "Storage returned an invalid file."
            )

        return data

    def _remove_objects(
        self,
        paths: list[str],
    ) -> None:
        if not paths:
            return

        self._request(
            "DELETE",
            f"/storage/v1/object/{BUCKET}",
            json_data={
                "prefixes": paths,
            },
        )

    def save_exam_bundle(
        self,
        exam: dict[str, Any],
        questions: list[dict[str, Any]],
        pdf_contents: bytes,
        crop_images: Mapping[str, bytes] | None = None,
    ) -> dict[str, Any]:
        """Upload files and atomically insert an OCR exam bundle.

        crop_images maps the existing relative image_path
        (for example, exam-id/question-id.png) to image bytes.
        """

        exam_id = str(exam["id"])

        if not exam_id or "/" in exam_id or "\\" in exam_id:
            raise ValueError(
                "Invalid exam identifier."
            )

        exam_data = copy.deepcopy(exam)
        question_data = copy.deepcopy(questions)

        images = crop_images or {}

        uploads: list[tuple[str, bytes, str]] = []

        pdf_path = self._object_path(
            "pdfs",
            f"{exam_id}.pdf",
        )

        exam_data["source_path"] = pdf_path

        uploads.append((
            pdf_path,
            pdf_contents,
            "application/pdf",
        ))

        for question in question_data:
            asset = question.get("asset")

            if not isinstance(asset, dict):
                continue

            original_path = asset.get("image_path")

            if not original_path:
                continue

            if original_path not in images:
                raise CloudRepositoryError(
                    "Missing cropped image bytes for "
                    f"{original_path}."
                )

            cloud_path = self._object_path(
                "assets",
                original_path,
            )

            asset["image_path"] = cloud_path

            uploads.append((
                cloud_path,
                images[original_path],
                "image/png",
            ))

        uploaded_paths: list[str] = []

        try:
            for path, contents, content_type in uploads:
                self.upload_object(
                    path,
                    contents,
                    content_type,
                )

                uploaded_paths.append(path)

            result = self._request(
                "POST",
                "/rest/v1/rpc/quizpdf_create_exam_bundle",
                json_data={
                    "p_exam": exam_data,
                    "p_questions": question_data,
                },
            )

            if not isinstance(result, dict):
                raise CloudRepositoryError(
                    "Database returned an invalid exam result."
                )

            return {
                "exam_id": result["exam_id"],
                "title": exam_data["name"],
                "question_count": result["question_count"],
            }

        except Exception:
            if uploaded_paths:
                try:
                    self._remove_objects(uploaded_paths)
                except Exception:
                    logger.exception(
                        "cloud=storage_cleanup status=failed "
                        "exam=%s",
                        exam_id,
                    )

            raise

    def list_exams(self) -> list[dict[str, Any]]:
        """Get only exams visible to the authenticated user."""

        result = self._request(
            "GET",
            "/rest/v1/exams",
            params={
                "select": (
                    "id,name,subject,source_file,"
                    "uploaded_at,source_path,questions(count)"
                ),
                "order": "uploaded_at.desc",
            },
        )

        if not isinstance(result, list):
            raise CloudRepositoryError(
                "Invalid Supabase exam list."
            )

        exams = []

        for exam in result:
            count_data = exam.get("questions") or []

            count = (
                count_data[0].get("count", 0)
                if count_data
                else 0
            )

            exams.append({
                "id": exam["id"],
                "name": exam["name"],
                "subject": exam.get("subject"),
                "source_file": exam.get("source_file"),
                "uploaded_at": exam["uploaded_at"],
                "has_source_pdf": bool(
                    exam.get("source_path")
                ),
                "question_count": count,
            })

        return exams

    def get_exam_record(
        self,
        exam_id: str,
    ) -> dict[str, Any] | None:
        """Return the existing db.get_exam_record shape."""

        result = self._request(
            "GET",
            "/rest/v1/exams",
            params={
                "id": f"eq.{exam_id}",
                "select": (
                    "*,questions("
                    "*,mcp_options(*),match_pairs(*),"
                    "fill_in_answers(*),question_assets(*),"
                    "red_mark_detections(*)"
                    ")"
                ),
                "questions.order": "question_id.asc",
            },
        )

        if not result:
            return None

        exam = result[0]

        questions = exam.get("questions") or []

        for question in questions:
            for key in (
                "mcp_options",
                "match_pairs",
                "fill_in_answers",
            ):
                question[key] = sorted(
                    question.get(key) or [],
                    key=lambda item: item.get(
                        "order_index",
                        0,
                    ),
                )

            for relation, target in (
                ("question_assets", "asset"),
                (
                    "red_mark_detections",
                    "red_mark_detection",
                ),
            ):
                value = question.pop(
                    relation,
                    None,
                )

                if isinstance(value, list):
                    question[target] = (
                        value[0]
                        if value
                        else None
                    )
                else:
                    question[target] = value

            question["has_source_pdf"] = bool(
                exam.get("source_path")
            )

        exam["questions"] = questions

        return exam

    def save_attempt(
        self,
        attempt: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a completed attempt owned by this user."""

        payload = {
            **attempt,
            "owner_id": self.user_id,
        }

        self._request(
            "POST",
            "/rest/v1/quiz_attempts",
            json_data=payload,
            extra_headers={
                "Prefer": "return=minimal",
            },
        )

        return {
            "id": attempt["id"],
            "completed_at": attempt["completed_at"],
        }

    def list_attempts(self) -> list[dict[str, Any]]:
        result = self._request(
            "GET",
            "/rest/v1/quiz_attempts",
            params={
                "select": "*,exams(name)",
                "order": "completed_at.desc",
            },
        )

        if not isinstance(result, list):
            raise CloudRepositoryError(
                "Invalid Supabase attempt list."
            )

        attempts = []

        for item in result:
            exam = item.pop("exams", None) or {}

            item["exam_title"] = exam.get(
                "name",
                "Bài trắc nghiệm",
            )

            attempts.append(item)

        return attempts
