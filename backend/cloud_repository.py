"""Authenticated Supabase persistence with safe resumable object uploads.

All requests use the authenticated user's JWT. A temporary Storage failure
preserves verified objects so a checkpoint retry can skip completed uploads.
"""

import copy
import hashlib
import logging
import math
import os
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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
    from cloud_media import CropOptimizationError, prepare_crop_upload

logger = logging.getLogger("quizpdf")
BUCKET = "quizpdf-private"
SYLLABUS_EMBEDDING_DIMENSIONS = 1536
logger.info("app=quizpdf cloud_repository_loaded module=%s", __file__)


class CloudRepositoryError(RuntimeError):
    """Supabase persistence or Storage operation failed."""


class CloudRepositoryHTTPError(CloudRepositoryError):
    """Supabase request failed with a response status."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ):
        self.status_code = status_code
        self.error_code = error_code
        self.error_message = error_message
        super().__init__(detail)


def _safe_error_field(value: Any, *, limit: int = 240) -> str | None:
    """Redact common credential/value shapes before an error reaches logs."""
    if not isinstance(value, str):
        return None
    text = re.sub(r"(?i)authorization\s*[:=]\s*[^\r\n]+", "Authorization: [redacted]", value)
    text = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [redacted]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[redacted-jwt]", text)
    text = re.sub(r"\bsb_(?:publishable|secret)_[A-Za-z0-9_-]{8,}\b", "[redacted-key]", text)
    text = re.sub(r"\bAIza[0-9A-Za-z_-]{20,}\b", "[redacted-key]", text)
    text = re.sub(r"(?i)(api[_ -]?key|token|password)\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", text)
    text = re.sub(r"https?://\S+", "[url-redacted]", text)
    # Database and gateway errors can echo rejected values. Drop quoted content.
    text = re.sub(r"'[^']*'|\"[^\"]*\"", "[value]", text)
    text = "".join(char if char.isprintable() else " " for char in text)
    return text[:limit] or None


def _supabase_error_details(response: httpx.Response) -> tuple[str | None, str | None]:
    """Extract only bounded, filtered error fields; never retain response bodies."""
    try:
        payload = response.json()
    except ValueError:
        return None, "Non-JSON Supabase error response (body omitted)"
    if not isinstance(payload, dict):
        return None, "Supabase returned a non-object error (body omitted)"
    raw_code = payload.get("code")
    code = raw_code.strip() if isinstance(raw_code, str) else None
    if code and not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", code):
        code = None
    message = _safe_error_field(payload.get("message"))
    return code, message or "Supabase error message omitted"


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
            error_code, error_message = _supabase_error_details(response)
            raise CloudRepositoryHTTPError(
                response.status_code,
                f"Supabase {method} {path} failed "
                f"(HTTP {response.status_code}; code={error_code or 'unknown'}): "
                f"{error_message}",
                error_code=error_code,
                error_message=error_message,
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

        if category not in {"pdfs", "assets", "syllabi"}:
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
        """Return matched, missing, mismatched, or unknown.

        Download and compare exact bytes before reuse.
        A metadata-only HEAD cannot establish content equality.
        """
        encoded = quote(object_path, safe="/")

        try:
            actual = self._request(
                "GET",
                f"/storage/v1/object/authenticated/{BUCKET}/{encoded}",
                not_found_ok=True,
            )
        except CloudRepositoryHTTPError as exc:
            logger.warning(
                "cloud=storage_verify status=unknown "
                "object=%s http_status=%d",
                object_path,
                exc.status_code,
            )
            return "unknown"

        except CloudRepositoryError as exc:
            logger.warning(
                "cloud=storage_verify status=unknown "
                "object=%s error_type=%s",
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
    ) -> bool:
        """Retry transient POSTs only when verified; return whether reused."""
        max_attempts = 3
        category = (
            "pdf"
            if content_type == "application/pdf"
            else "image"
        )

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
                self.upload_object(
                    object_path,
                    contents,
                    content_type,
                )

            except CloudRepositoryError as exc:
                duration = time.monotonic() - started

                retryable_http = (
                    isinstance(exc, CloudRepositoryHTTPError)
                    and exc.status_code in {
                        409, 429, 500, 502, 503, 504
                    }
                )
                transient_transport = isinstance(
                    exc.__cause__,
                    httpx.RequestError,
                )

                if not (retryable_http or transient_transport):
                    logger.error(
                        "cloud=storage_upload status=failed "
                        "object=%s attempt=%d duration=%.2f "
                        "error_type=%s",
                        object_path,
                        attempt,
                        duration,
                        type(exc).__name__,
                    )
                    raise

                verification = "unknown"

                for check in range(2):
                    verification = self._verify_uploaded_object(
                        object_path,
                        contents,
                    )

                    if verification in {"matched", "mismatched"}:
                        break

                    if check == 0:
                        time.sleep(0.5)

                logger.warning(
                    "cloud=storage_upload status=uncertain "
                    "object=%s attempt=%d duration=%.2f "
                    "verification=%s",
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
                    return True

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
                return False

    def _ensure_object(
        self,
        object_path: str,
        contents: bytes,
        content_type: str,
        *,
        check_existing: bool,
    ) -> bool:
        """Ensure exact content; return True if object was reused."""
        if check_existing:
            state = self._verify_uploaded_object(
                object_path,
                contents,
            )

            if state == "matched":
                logger.info(
                    "cloud=storage_resume status=reused "
                    "object=%s bytes=%d",
                    object_path,
                    len(contents),
                )
                return True

            if state == "mismatched":
                raise CloudRepositoryError(
                    "Storage object exists with different content; "
                    "refusing to replace it."
                )

            if state == "unknown":
                raise CloudRepositoryError(
                    "Could not verify whether Storage object "
                    "already exists."
                )

        return self._upload_object_resilient(
            object_path,
            contents,
            content_type,
        )

    @staticmethod
    def _storage_upload_workers() -> int:
        raw = os.getenv(
            "QUIZPDF_STORAGE_UPLOAD_WORKERS",
            "3",
        )

        try:
            workers = int(raw)
        except ValueError as exc:
            raise CloudRepositoryError(
                "QUIZPDF_STORAGE_UPLOAD_WORKERS must be "
                "an integer from 1 to 4."
            ) from exc

        if not 1 <= workers <= 4:
            raise CloudRepositoryError(
                "QUIZPDF_STORAGE_UPLOAD_WORKERS "
                "must be from 1 to 4."
            )

        return workers

    def _upload_images_concurrently(
        self,
        uploads: list[tuple[str, bytes, str]],
        workers: int,
        *,
        resume_existing: bool = False,
    ) -> None:
        """Stop scheduling on failure; wait for running uploads."""
        if not uploads:
            return

        started = time.monotonic()

        logger.info(
            "cloud=storage_batch status=started "
            "images=%d workers=%d resume=%s",
            len(uploads),
            workers,
            resume_existing,
        )

        failures: list[tuple[str, Exception]] = []
        reused = 0

        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="quiz-storage",
        ) as executor:
            remaining = iter(uploads)
            active = {}

            def submit_next() -> bool:
                upload = next(remaining, None)

                if upload is None:
                    return False

                future = executor.submit(
                    self._ensure_object,
                    *upload,
                    check_existing=resume_existing,
                )

                active[future] = upload[0]
                return True

            for _ in range(min(workers, len(uploads))):
                submit_next()

            while active:
                finished, _ = wait(
                    tuple(active),
                    return_when=FIRST_COMPLETED,
                )

                for future in finished:
                    path = active.pop(future)

                    try:
                        if future.result():
                            reused += 1

                    except Exception as exc:
                        failures.append((path, exc))

                        logger.error(
                            "cloud=storage_batch status=image_failed "
                            "object=%s error_type=%s",
                            path,
                            type(exc).__name__,
                        )

                # No new uploads after a failure.
                # Already running requests must finish before retry.
                if not failures:
                    while (
                        len(active) < workers
                        and submit_next()
                    ):
                        pass

        if failures:
            logger.error(
                "cloud=storage_batch status=failed "
                "images=%d failures=%d reused=%d "
                "duration_seconds=%.2f",
                len(uploads),
                len(failures),
                reused,
                time.monotonic() - started,
            )
            raise failures[0][1]

        logger.info(
            "cloud=storage_batch status=completed "
            "images=%d workers=%d reused=%d "
            "duration_seconds=%.2f",
            len(uploads),
            workers,
            reused,
            time.monotonic() - started,
        )

    def download_object(self, object_path: str) -> bytes:
        if not object_path.startswith(f"{self.user_id}/"):
            raise ValueError(
                "Cannot download another user's object."
            )

        encoded = quote(object_path, safe="/")

        data = self._request(
            "GET",
            f"/storage/v1/object/authenticated/{BUCKET}/{encoded}",
        )

        if not isinstance(data, bytes):
            raise CloudRepositoryError(
                "Storage returned an invalid file."
            )

        return data

    def _remove_objects(self, paths: list[str]) -> None:
        if not paths:
            return

        self._request(
            "DELETE",
            f"/storage/v1/object/{BUCKET}",
            json_data={"prefixes": paths},
        )

    @staticmethod
    def _validated_embedding(vector: Any, *, field: str) -> list[float]:
        if not isinstance(vector, (list, tuple)) or len(vector) != SYLLABUS_EMBEDDING_DIMENSIONS:
            actual = len(vector) if isinstance(vector, (list, tuple)) else "invalid"
            raise ValueError(
                f"{field} must contain exactly {SYLLABUS_EMBEDDING_DIMENSIONS} values; got {actual}."
            )
        values: list[float] = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field} values must be finite numbers.")
            try:
                number = float(value)
            except (OverflowError, ValueError) as exc:
                raise ValueError(f"{field} values must be finite numbers.") from exc
            if not math.isfinite(number):
                raise ValueError(f"{field} values must be finite numbers.")
            values.append(number)
        return values

    def save_syllabus_bundle(
        self,
        document: dict[str, Any],
        chunks: list[dict[str, Any]],
        pdf_contents: bytes,
        *,
        resume_existing: bool = False,
    ) -> dict[str, Any]:
        """Upload a syllabus PDF and atomically persist its document/chunks.

        The caller supplies a stable document UUID so an ambiguous RPC outcome
        can be retried with ``resume_existing=True``. The database RPC is
        idempotent for an identical bundle.
        """
        if not isinstance(document, dict):
            raise ValueError("Syllabus document must be an object.")
        if not isinstance(pdf_contents, bytes) or not pdf_contents:
            raise ValueError("Syllabus PDF contents must be non-empty bytes.")
        document_data = copy.deepcopy(document)
        try:
            document_id = str(UUID(str(document_data.get("id", ""))))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Syllabus document id must be a UUID.") from exc
        document_data["id"] = document_id
        supplied_owner = document_data.pop("owner_id", self.user_id)
        try:
            supplied_owner = str(UUID(str(supplied_owner)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Syllabus owner_id must be a UUID.") from exc
        if supplied_owner != self.user_id:
            raise ValueError("Syllabus document is not owned by the authenticated user.")

        for field in ("title", "source_file"):
            if not isinstance(document_data.get(field), str) or not document_data[field].strip():
                raise ValueError(f"Syllabus {field} must be a non-empty string.")
        subject = document_data.get("subject")
        if subject is not None and not isinstance(subject, str):
            raise ValueError("Syllabus subject must be a string or null.")
        metadata = document_data.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("Syllabus metadata must be an object.")
        document_data["metadata"] = metadata
        document_data["source_path"] = self._object_path(
            "syllabi", f"{document_id}/{PurePosixPath(document_data['source_file']).name}"
        )

        if not isinstance(chunks, list) or not chunks:
            raise ValueError("Syllabus bundle must contain at least one chunk.")
        chunk_data: list[dict[str, Any]] = []
        for expected_index, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                raise ValueError("Every syllabus chunk must be an object.")
            item = copy.deepcopy(chunk)
            owner_id = item.pop("owner_id", self.user_id)
            try:
                owner_id = str(UUID(str(owner_id)))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("Syllabus chunk owner_id must be a UUID.") from exc
            if owner_id != self.user_id:
                raise ValueError("Syllabus chunk is not owned by the authenticated user.")
            if item.get("chunk_index") != expected_index:
                raise ValueError("Syllabus chunk indexes must be contiguous and start at zero.")
            if not isinstance(item.get("content"), str) or not item["content"].strip():
                raise ValueError("Syllabus chunk content must be non-empty text.")
            pages = item.get("source_pages")
            if not isinstance(pages, (list, tuple)) or not pages or any(type(p) is not int or p < 1 for p in pages):
                raise ValueError("Syllabus chunk source_pages must contain positive page numbers.")
            if item.get("character_count") != len(item["content"]):
                raise ValueError("Syllabus chunk character_count does not match its content.")
            heading = item.get("section_heading")
            if heading is not None and not isinstance(heading, str):
                raise ValueError("Syllabus chunk section_heading must be a string or null.")
            token_estimate = item.get("token_estimate")
            if token_estimate is not None and (type(token_estimate) is not int or token_estimate < 0):
                raise ValueError("Syllabus chunk token_estimate must be a non-negative integer or null.")
            item["source_pages"] = list(pages)
            item["embedding"] = self._validated_embedding(
                item.get("embedding"), field=f"chunk {expected_index} embedding"
            )
            chunk_metadata = item.get("metadata", {})
            if not isinstance(chunk_metadata, dict):
                raise ValueError("Syllabus chunk metadata must be an object.")
            item["metadata"] = chunk_metadata
            chunk_data.append(item)

        path = document_data["source_path"]
        # Track reused objects so a definite RPC rejection never deletes a
        # Storage object that predated this persistence attempt.
        logger.info(
            "rag=syllabus stage=storage_upload status=started document_id=%s",
            document_id,
        )
        try:
            storage_reused = self._ensure_object(
                path, pdf_contents, "application/pdf", check_existing=resume_existing
            )
        except Exception as exc:
            logger.error(
                "rag=syllabus stage=storage_upload status=failed document_id=%s "
                "http_status=%s error_code=%s error_message=%s error_type=%s",
                document_id,
                getattr(exc, "status_code", None) or "none",
                getattr(exc, "error_code", None) or "unknown",
                _safe_error_field(getattr(exc, "error_message", None)) or "omitted",
                type(exc).__name__,
            )
            raise
        logger.info(
            "rag=syllabus stage=storage_upload status=%s document_id=%s",
            "reused" if storage_reused else "completed",
            document_id,
        )
        logger.info(
            "rag=syllabus stage=rpc_persist status=started document_id=%s chunks=%d",
            document_id,
            len(chunk_data),
        )
        try:
            result = self._request(
                "POST",
                "/rest/v1/rpc/quizpdf_create_syllabus_bundle",
                json_data={"p_document": document_data, "p_chunks": chunk_data},
            )
        except CloudRepositoryHTTPError as exc:
            logger.error(
                "rag=syllabus stage=rpc_persist status=failed document_id=%s "
                "chunks=%d http_status=%d error_code=%s error_message=%s",
                document_id,
                len(chunk_data),
                exc.status_code,
                exc.error_code or "unknown",
                _safe_error_field(exc.error_message) or "omitted",
            )
            if exc.status_code in {400, 401, 403, 404, 409, 422} and not storage_reused:
                try:
                    self._remove_objects([path])
                except Exception as cleanup_exc:
                    logger.error(
                        "rag=syllabus stage=storage_cleanup status=failed document_id=%s "
                        "http_status=%s error_code=%s error_message=%s error_type=%s",
                        document_id,
                        getattr(cleanup_exc, "status_code", None) or "none",
                        getattr(cleanup_exc, "error_code", None) or "unknown",
                        _safe_error_field(getattr(cleanup_exc, "error_message", None)) or "omitted",
                        type(cleanup_exc).__name__,
                    )
                else:
                    logger.info(
                        "rag=syllabus stage=storage_cleanup status=completed document_id=%s",
                        document_id,
                    )
            else:
                logger.warning("cloud=syllabus_persist status=uncertain document=%s", document_id)
            raise
        except CloudRepositoryError:
            # A transport failure may have happened after Postgres committed.
            # Keep the PDF and the caller's stable ID for an idempotent retry.
            logger.warning("cloud=syllabus_persist status=uncertain document=%s", document_id)
            raise

        logger.info(
            "rag=syllabus stage=rpc_persist status=response_received document_id=%s chunks=%d",
            document_id,
            len(chunk_data),
        )

        if not isinstance(result, dict) or result.get("document_id") != document_id:
            # The RPC may have committed; never delete Storage on an invalid or
            # truncated success response because the outcome is ambiguous.
            logger.error(
                "rag=syllabus stage=rpc_persist status=invalid_response "
                "document_id=%s chunks=%d",
                document_id,
                len(chunk_data),
            )
            raise CloudRepositoryError("Database returned an invalid syllabus result.")
        if result.get("chunk_count") != len(chunk_data):
            logger.error(
                "rag=syllabus stage=rpc_persist status=incomplete_response "
                "document_id=%s expected_chunks=%d",
                document_id,
                len(chunk_data),
            )
            raise CloudRepositoryError("Database returned an incomplete syllabus result.")
        logger.info(
            "rag=syllabus stage=rpc_persist status=completed document_id=%s chunks=%d",
            document_id,
            len(chunk_data),
        )
        return {
            "document_id": document_id,
            "source_path": path,
            "chunk_count": len(chunk_data),
        }

    def match_syllabus_chunks(
        self,
        query_embedding: list[float] | tuple[float, ...],
        *,
        match_count: int = 10,
        subject: str | None = None,
        document_id: str | None = None,
    ) -> list[dict[str, Any]]:
        vector = self._validated_embedding(query_embedding, field="query_embedding")
        if type(match_count) is not int or not 1 <= match_count <= 100:
            raise ValueError("match_count must be an integer from 1 to 100.")
        if subject is not None and not isinstance(subject, str):
            raise ValueError("subject must be a string or null.")
        if document_id is not None:
            try:
                document_id = str(UUID(document_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("document_id must be a UUID.") from exc
        result = self._request(
            "POST",
            "/rest/v1/rpc/quizpdf_match_syllabus_chunks",
            json_data={
                "query_embedding": vector,
                "p_match_count": match_count,
                "p_subject": subject,
                "p_document_id": document_id,
            },
        )
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            raise CloudRepositoryError("Database returned invalid syllabus matches.")
        return result

    def list_syllabus_documents(self) -> list[dict[str, Any]]:
        """List documents visible to this authenticated JWT under RLS."""
        result = self._request(
            "GET",
            "/rest/v1/syllabus_documents",
            params={
                "select": (
                    "id,title,subject,source_file,processing_status,"
                    "created_at,updated_at,metadata"
                ),
                "order": "created_at.desc",
            },
        )
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            raise CloudRepositoryError("Database returned invalid syllabus documents.")
        return result

    def get_syllabus_document(self, document_id: str) -> dict[str, Any] | None:
        """Fetch one document; RLS makes another owner's document invisible."""
        try:
            resolved_id = str(UUID(document_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("document_id must be a UUID.") from exc
        result = self._request(
            "GET",
            "/rest/v1/syllabus_documents",
            params={
                "select": (
                    "id,title,subject,source_file,processing_status,"
                    "created_at,updated_at,metadata,syllabus_chunks(count)"
                ),
                "id": f"eq.{resolved_id}",
                "limit": "1",
            },
        )
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            raise CloudRepositoryError("Database returned an invalid syllabus document.")
        return result[0] if result else None

    def save_exam_bundle(
        self,
        exam: dict[str, Any],
        questions: list[dict[str, Any]],
        pdf_contents: bytes,
        crop_images: Mapping[str, bytes] | None = None,
        *,
        resume_existing: bool = False,
    ) -> dict[str, Any]:
        """Persist only missing objects, then insert exam metadata.

        Transient failures preserve uploaded objects for a checkpoint retry.
        A definite RPC rejection on a new upload triggers cleanup.
        """
        exam_id = str(exam["id"])

        if (
            not exam_id
            or "/" in exam_id
            or "\\" in exam_id
        ):
            raise ValueError("Invalid exam identifier.")

        exam_data = copy.deepcopy(exam)
        question_data = copy.deepcopy(questions)
        images = crop_images or {}

        pdf_path = self._object_path(
            "pdfs",
            f"{exam_id}.pdf",
        )
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
                    "Missing cropped image bytes "
                    f"for {original_path}."
                )

            original_bytes = images[original_path]

            if not isinstance(original_bytes, bytes):
                raise CloudRepositoryError(
                    "Crop bytes must be immutable bytes."
                )

            digest = hashlib.sha256(
                original_bytes
            ).hexdigest()

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
                optimized, suffix, content_type = (
                    prepare_crop_upload(original_bytes)
                )

            except CropOptimizationError as exc:
                raise CloudRepositoryError(
                    "Could not optimize cropped image: "
                    f"{exc}"
                ) from exc

            remote_name = str(
                PurePosixPath(original_path).with_suffix(
                    suffix
                )
            )
            cloud_path = self._object_path(
                "assets",
                remote_name,
            )

            if cloud_path in seen_paths:
                if seen_paths[cloud_path] != optimized:
                    raise CloudRepositoryError(
                        "Conflicting image contents "
                        "for one Storage object."
                    )

                asset["image_path"] = cloud_path
                seen_digests[digest] = (
                    original_bytes,
                    cloud_path,
                )
                continue

            seen_paths[cloud_path] = optimized
            seen_digests[digest] = (
                original_bytes,
                cloud_path,
            )
            asset["image_path"] = cloud_path

            image_uploads.append(
                (
                    cloud_path,
                    optimized,
                    content_type,
                )
            )

        workers = self._storage_upload_workers()

        # Fresh uploads use the original POST-only behavior.
        # Checkpoint retries verify objects before uploading.
        try:
            self._ensure_object(
                pdf_path,
                pdf_contents,
                "application/pdf",
                check_existing=resume_existing,
            )

            self._upload_images_concurrently(
                image_uploads,
                workers,
                resume_existing=resume_existing,
            )

        except Exception:
            logger.warning(
                "cloud=storage_bundle "
                "status=preserved_for_retry exam=%s",
                exam_id,
            )
            raise

        try:
            result = self._request(
                "POST",
                "/rest/v1/rpc/quizpdf_create_exam_bundle",
                json_data={
                    "p_exam": exam_data,
                    "p_questions": question_data,
                },
            )

        except CloudRepositoryHTTPError as exc:
            if (
                not resume_existing
                and exc.status_code in {
                    400, 401, 403, 404, 422
                }
            ):
                # On a new exam, definite RPC rejection allows cleanup.
                # On resume, objects may predate this attempt.
                try:
                    paths = [
                        pdf_path,
                        *(
                            path
                            for path, _, _ in image_uploads
                        ),
                    ]

                    self._remove_objects(paths)

                    logger.info(
                        "cloud=storage_cleanup "
                        "status=request_completed "
                        "exam=%s objects=%d",
                        exam_id,
                        len(paths),
                    )

                except Exception:
                    logger.exception(
                        "cloud=storage_cleanup "
                        "status=failed exam=%s",
                        exam_id,
                    )

            else:
                logger.warning(
                    "cloud=storage_bundle "
                    "status=rpc_uncertain_preserved "
                    "exam=%s",
                    exam_id,
                )

            raise

        except Exception:
            logger.warning(
                "cloud=storage_bundle "
                "status=rpc_uncertain_preserved "
                "exam=%s",
                exam_id,
            )
            raise

        if not isinstance(result, dict):
            raise CloudRepositoryError(
                "Database returned an invalid exam result."
            )

        return {
            "exam_id": result["exam_id"],
            "title": exam_data["name"],
            "question_count": result["question_count"],
        }

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
        payload = {
            **attempt,
            "owner_id": self.user_id,
        }

        self._request(
            "POST",
            "/rest/v1/quiz_attempts",
            json_data=payload,
            extra_headers={
                "Prefer": "return=minimal"
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
