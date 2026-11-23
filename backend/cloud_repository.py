"""Authenticated Supabase persistence for QuizPDF.

PDF uploads run first. Crops are optimized and deduplicated before
bounded concurrent upload. Storage retries verify ambiguous writes.
"""

import copy
import hashlib
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import quote
from uuid import UUID

import httpx

try:
    from backend.cloud_media import (
        CropOptimizationError,
        prepare_crop_upload,
    )
except ModuleNotFoundError as exc:
    if exc.name != "backend":
        raise
    from cloud_media import (
        CropOptimizationError,
        prepare_crop_upload,
    )

logger = logging.getLogger("quizpdf")
BUCKET = "quizpdf-private"


class CloudRepositoryError(RuntimeError):
    """Supabase persistence or Storage operation failed."""


class CloudRepositoryHTTPError(CloudRepositoryError):
    """Supabase request failed with a response status."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        super().__init__(detail)


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
            raise ValueError("Invalid authenticated user ID.") from exc

        if not access_token:
            raise ValueError("Supabase access token is required.")

        self.base_url = (
            base_url or os.getenv("SUPABASE_URL", "")
        ).rstrip("/")
        self.publishable_key = (
            publishable_key
            or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
        )
        if not self.base_url or not self.publishable_key:
            raise CloudRepositoryError(
                "SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are required."
            )

        self.access_token = access_token
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=60.0)

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

    @staticmethod
    def _storage_object_not_found(response: httpx.Response) -> bool:
        """Only accept HTTP 400 NoSuchKey as definite missing object."""
        if response.status_code == 404:
            return True
        if response.status_code != 400:
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        if not isinstance(payload, dict):
            return False
        return (
            str(payload.get("code") or "").strip() == "NoSuchKey"
            and str(payload.get("error") or "").strip().lower()
            == "not_found"
            and str(payload.get("message") or "").strip().lower()
            == "object not found"
            and str(payload.get("statusCode") or "").strip() == "404"
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_data: Any = None,
        content: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
        not_found_ok: bool = False,
    ) -> Any:
        headers = {
            "apikey": self.publishable_key,
            "Authorization": f"Bearer {self.access_token}",
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

        if not_found_ok and self._storage_object_not_found(response):
            return None
        if response.status_code >= 400:
            detail = response.text[:400]
            raise CloudRepositoryHTTPError(
                response.status_code,
                f"Supabase {method} {path} failed "
                f"(HTTP {response.status_code}): {detail}",
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.content

    def _object_path(self, category: str, name: str) -> str:
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or not name
            or "\\" in name
            or ".." in relative.parts
            or "." in relative.parts
        ):
            raise ValueError("Invalid Supabase Storage object path.")
        if category not in {"pdfs", "assets"}:
            raise ValueError("Unsupported Storage category.")
        return f"{self.user_id}/{category}/{relative.as_posix()}"

    def upload_object(
        self,
        object_path: str,
        contents: bytes,
        content_type: str,
    ) -> None:
        encoded = quote(object_path, safe="/")
        self._request(
            "POST",
            f"/storage/v1/object/{BUCKET}/{encoded}",
            content=contents,
            extra_headers={
                "Content-Type": content_type,
                "x-upsert": "false",
            },
        )

    def _verify_uploaded_object(
        self,
        object_path: str,
        expected: bytes,
    ) -> str:
        """Return matched, missing, mismatched, or unknown."""
        encoded = quote(object_path, safe="/")
        try:
            actual = self._request(
                "GET",
                f"/storage/v1/object/authenticated/{BUCKET}/{encoded}",
                not_found_ok=True,
            )
        except CloudRepositoryHTTPError as exc:
            logger.warning(
                "cloud=storage_verify status=unknown object=%s http_status=%d",
                object_path,
                exc.status_code,
            )
            return "unknown"
        except CloudRepositoryError as exc:
            logger.warning(
                "cloud=storage_verify status=unknown object=%s error_type=%s",
                object_path,
                type(exc).__name__,
            )
            return "unknown"

        if actual is None:
            logger.info(
                "cloud=storage_verify status=missing object=%s",
                object_path,
            )
            return "missing"
        if isinstance(actual, bytes) and actual == expected:
            return "matched"
        return "mismatched"

    def _upload_object_resilient(
        self,
        object_path: str,
        contents: bytes,
        content_type: str,
    ) -> None:
        """Retry transient POST only when previous outcome is known."""
        max_attempts = 3
        category = "pdf" if content_type == "application/pdf" else "image"

        for attempt in range(1, max_attempts + 1):
            started = time.monotonic()
            logger.info(
                "cloud=storage_upload status=started "
                "object=%s category=%s bytes=%d attempt=%d/%d",
                object_path,
                category,
                len(contents),
                attempt,
                max_attempts,
            )
            try:
                self.upload_object(object_path, contents, content_type)
            except CloudRepositoryError as exc:
                duration = time.monotonic() - started
                retryable_http = (
                    isinstance(exc, CloudRepositoryHTTPError)
                    and exc.status_code in {409, 429, 500, 502, 503, 504}
                )
                transient_transport = isinstance(
                    exc.__cause__, httpx.RequestError
                )
                if not (retryable_http or transient_transport):
                    logger.error(
                        "cloud=storage_upload status=failed object=%s "
                        "attempt=%d duration=%.2f error_type=%s",
                        object_path,
                        attempt,
                        duration,
                        type(exc).__name__,
                    )
                    raise

                # A failed POST can have committed. Verify before retry.
                verification = "unknown"
                for check in range(2):
                    verification = self._verify_uploaded_object(
                        object_path, contents
                    )
                    if verification in {"matched", "mismatched"}:
                        break
                    if check == 0:
                        time.sleep(0.5)

                logger.warning(
                    "cloud=storage_upload status=uncertain object=%s "
                    "attempt=%d duration=%.2f verification=%s",
                    object_path,
                    attempt,
                    duration,
                    verification,
                )
                if verification == "matched":
                    logger.info(
                        "cloud=storage_upload status=recovered "
                        "object=%s attempt=%d",
                        object_path,
                        attempt,
                    )
                    return
                if verification == "mismatched":
                    raise CloudRepositoryError(
                        "Storage object exists with different content; "
                        "upload was not retried."
                    ) from exc
                if verification == "unknown":
                    raise
                if attempt >= max_attempts:
                    raise
                if (
                    isinstance(exc, CloudRepositoryHTTPError)
                    and exc.status_code == 409
                ):
                    raise
                logger.warning(
                    "cloud=storage_upload status=retrying "
                    "object=%s next_attempt=%d",
                    object_path,
                    attempt + 1,
                )
                time.sleep(0.5 * attempt)
            else:
                logger.info(
                    "cloud=storage_upload status=completed "
                    "object=%s attempt=%d duration=%.2f",
                    object_path,
                    attempt,
                    time.monotonic() - started,
                )
                return

    @staticmethod
    def _storage_upload_workers() -> int:
        raw = os.getenv("QUIZPDF_STORAGE_UPLOAD_WORKERS", "3")
        try:
            workers = int(raw)
        except ValueError as exc:
            raise CloudRepositoryError(
                "QUIZPDF_STORAGE_UPLOAD_WORKERS must be an integer from 1 to 4."
            ) from exc
        if not 1 <= workers <= 4:
            raise CloudRepositoryError(
                "QUIZPDF_STORAGE_UPLOAD_WORKERS must be from 1 to 4."
            )
        return workers

    def _upload_images_concurrently(
        self,
        uploads: list[tuple[str, bytes, str]],
        workers: int,
    ) -> None:
        """Wait for every scheduled upload before success or rollback."""
        if not uploads:
            return

        started = time.monotonic()
        logger.info(
            "cloud=storage_batch status=started images=%d workers=%d",
            len(uploads),
            workers,
        )
        failures: list[tuple[str, Exception]] = []

        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="quiz-storage",
        ) as executor:
            futures = {
                executor.submit(self._upload_object_resilient, *upload): upload[0]
                for upload in uploads
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    failures.append((futures[future], exc))
                    logger.error(
                        "cloud=storage_batch status=image_failed "
                        "object=%s error_type=%s",
                        futures[future],
                        type(exc).__name__,
                    )

        if failures:
            logger.error(
                "cloud=storage_batch status=failed images=%d "
                "failures=%d duration_seconds=%.2f",
                len(uploads),
                len(failures),
                time.monotonic() - started,
            )
            raise failures[0][1]

        logger.info(
            "cloud=storage_batch status=completed images=%d "
            "workers=%d duration_seconds=%.2f",
            len(uploads),
            workers,
            time.monotonic() - started,
        )

    def download_object(self, object_path: str) -> bytes:
        if not object_path.startswith(f"{self.user_id}/"):
            raise ValueError("Cannot download another user's object.")
        encoded = quote(object_path, safe="/")
        data = self._request(
            "GET",
            f"/storage/v1/object/authenticated/{BUCKET}/{encoded}",
        )
        if not isinstance(data, bytes):
            raise CloudRepositoryError("Storage returned an invalid file.")
        return data

    def _remove_objects(self, paths: list[str]) -> None:
        if not paths:
            return
        self._request(
            "DELETE",
            f"/storage/v1/object/{BUCKET}",
            json_data={"prefixes": paths},
        )

    def save_exam_bundle(
        self,
        exam: dict[str, Any],
        questions: list[dict[str, Any]],
        pdf_contents: bytes,
        crop_images: Mapping[str, bytes] | None = None,
    ) -> dict[str, Any]:
        """Upload PDF, distinct optimized crops, then insert DB rows."""
        exam_id = str(exam["id"])
        if not exam_id or "/" in exam_id or "\\" in exam_id:
            raise ValueError("Invalid exam identifier.")

        exam_data = copy.deepcopy(exam)
        question_data = copy.deepcopy(questions)
        images = crop_images or {}

        pdf_path = self._object_path("pdfs", f"{exam_id}.pdf")
        exam_data["source_path"] = pdf_path
        image_uploads: list[tuple[str, bytes, str]] = []
        seen_paths: dict[str, bytes] = {}
        seen_digests: dict[str, tuple[bytes, str]] = {}

        for question in question_data:
            asset = question.get("asset")
            if not isinstance(asset, dict):
                continue
            original_path = asset.get("image_path")
            if not original_path:
                continue
            if original_path not in images:
                raise CloudRepositoryError(
                    f"Missing cropped image bytes for {original_path}."
                )

            original_bytes = images[original_path]
            if not isinstance(original_bytes, bytes):
                raise CloudRepositoryError("Crop bytes must be immutable bytes.")

            digest = hashlib.sha256(original_bytes).hexdigest()
            cached_upload = seen_digests.get(digest)
            if (
                cached_upload is not None
                and cached_upload[0] == original_bytes
            ):
                asset["image_path"] = cached_upload[1]
                logger.info(
                    "cloud=crop_prepare status=deduplicated "
                    "bytes_saved=%d",
                    len(original_bytes),
                )
                continue

            try:
                optimized, suffix, content_type = prepare_crop_upload(
                    original_bytes
                )
            except CropOptimizationError as exc:
                raise CloudRepositoryError(
                    f"Could not optimize cropped image: {exc}"
                ) from exc

            remote_name = str(
                PurePosixPath(original_path).with_suffix(suffix)
            )
            cloud_path = self._object_path("assets", remote_name)

            if cloud_path in seen_paths:
                if seen_paths[cloud_path] != optimized:
                    raise CloudRepositoryError(
                        "Conflicting image contents for one Storage object."
                    )
                asset["image_path"] = cloud_path
                seen_digests[digest] = (original_bytes, cloud_path)
                continue

            seen_paths[cloud_path] = optimized
            seen_digests[digest] = (original_bytes, cloud_path)
            asset["image_path"] = cloud_path
            image_uploads.append((cloud_path, optimized, content_type))

        workers = self._storage_upload_workers()
        attempted_paths = [pdf_path]

        try:
            self._upload_object_resilient(
                pdf_path,
                pdf_contents,
                "application/pdf",
            )
            attempted_paths.extend(path for path, _, _ in image_uploads)
            self._upload_images_concurrently(image_uploads, workers)

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
            if attempted_paths:
                try:
                    self._remove_objects(attempted_paths)
                    logger.info(
                        "cloud=storage_cleanup status=request_completed "
                        "exam=%s objects=%d",
                        exam_id,
                        len(attempted_paths),
                    )
                except Exception:
                    logger.exception(
                        "cloud=storage_cleanup status=failed exam=%s",
                        exam_id,
                    )
            raise

    def list_exams(self) -> list[dict[str, Any]]:
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
            raise CloudRepositoryError("Invalid Supabase exam list.")
        exams = []
        for exam in result:
            count_data = exam.get("questions") or []
            count = count_data[0].get("count", 0) if count_data else 0
            exams.append({
                "id": exam["id"],
                "name": exam["name"],
                "subject": exam.get("subject"),
                "source_file": exam.get("source_file"),
                "uploaded_at": exam["uploaded_at"],
                "has_source_pdf": bool(exam.get("source_path")),
                "question_count": count,
            })
        return exams

    def get_exam_record(self, exam_id: str) -> dict[str, Any] | None:
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
                    key=lambda item: item.get("order_index", 0),
                )
            for relation, target in (
                ("question_assets", "asset"),
                ("red_mark_detections", "red_mark_detection"),
            ):
                value = question.pop(relation, None)
                if isinstance(value, list):
                    question[target] = value[0] if value else None
                else:
                    question[target] = value
            question["has_source_pdf"] = bool(exam.get("source_path"))
        exam["questions"] = questions
        return exam

    def save_attempt(self, attempt: dict[str, Any]) -> dict[str, Any]:
        payload = {**attempt, "owner_id": self.user_id}
        self._request(
            "POST",
            "/rest/v1/quiz_attempts",
            json_data=payload,
            extra_headers={"Prefer": "return=minimal"},
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
            item["exam_title"] = exam.get("name", "Bài trắc nghiệm")
            attempts.append(item)
        return attempts
