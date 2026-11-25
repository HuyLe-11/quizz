"""In-memory upload job registry for one long-running backend process.

The manager preserves the existing asynchronous upload API contract.
It must not be used as a durable worker queue on serverless hosts.
"""

import logging
import threading
import uuid

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import httpx
import requests
from fastapi import HTTPException

try:
    from backend.cloud_repository import CloudRepositoryError
    from backend.engine.paddle_client import (
        PaddleOCRAPIError,
        PaddleOCRJobFailedError,
        PaddleOCRNoTextError,
        PaddleOCRTimeoutError,
    )
except ModuleNotFoundError as exc:
    if exc.name != "backend":
        raise

    from cloud_repository import CloudRepositoryError
    from engine.paddle_client import (
        PaddleOCRAPIError,
        PaddleOCRJobFailedError,
        PaddleOCRNoTextError,
        PaddleOCRTimeoutError,
    )

logger = logging.getLogger("quizpdf")

Progress = Callable[[str], None]
Work = Callable[[Progress], dict[str, Any]]

ACTIVE = {"queued", "processing"}


class UploadQueueFull(RuntimeError):
    pass


def _network_cause(error: Exception) -> Exception | None:
    """Inspect chained exceptions without exposing their details."""
    current: BaseException | None = error
    visited: set[int] = set()

    while isinstance(current, BaseException):
        if id(current) in visited:
            break
        visited.add(id(current))

        if isinstance(
            current,
            (requests.exceptions.RequestException, httpx.RequestError),
        ):
            return current

        current = current.__cause__

    return None


def _public_failure(error: Exception) -> tuple[int, str]:
    """Convert service failures into safe, actionable job errors.

    HTTP statuses describe the background task result. They do not
    change the HTTP 200 envelope returned by GET /api/upload/jobs/:id.
    """
    if isinstance(error, PaddleOCRTimeoutError):
        return (
            504,
            "PaddleOCR phản hồi quá lâu. PDF chưa được xử lý "
            "thành công; vui lòng kiểm tra trạng thái trước "
            "khi tải lại.",
        )

    if isinstance(error, PaddleOCRNoTextError):
        return (
            422,
            "PaddleOCR không nhận diện được văn bản trong PDF. "
            "Hãy kiểm tra chất lượng tài liệu và thử lại.",
        )

    if isinstance(error, PaddleOCRAPIError):
        if error.status_code == 429:
            return (
                503,
                "PaddleOCR đang giới hạn số yêu cầu. "
                "Hãy thử lại sau.",
            )
        if error.status_code in {401, 403}:
            return (
                502,
                "PaddleOCR từ chối xác thực từ backend. "
                "Hãy kiểm tra cấu hình máy chủ.",
            )
        return (
            502,
            "PaddleOCR trả về lỗi từ nhà cung cấp. "
            "Hãy kiểm tra log backend.",
        )

    if isinstance(error, PaddleOCRJobFailedError):
        return (
            502,
            "PaddleOCR báo tác vụ nhận dạng thất bại. "
            "Hãy kiểm tra tệp PDF hoặc thử model khác.",
        )

    if isinstance(error, CloudRepositoryError):
        return (
            502,
            "Không lưu được đề vào Supabase. "
            "Hãy kiểm tra kết nối và log backend.",
        )

    if (
        isinstance(error, RuntimeError)
        and "PADDLEOCR_ACCESS_TOKEN" in str(error)
    ):
        return (
            503,
            "Backend chưa cấu hình token PaddleOCR.",
        )

    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return (
            504,
            "Một dịch vụ xử lý PDF phản hồi quá lâu. "
            "Hãy kiểm tra log backend.",
        )

    network_error = _network_cause(error)
    if network_error is not None:
        if isinstance(
            network_error,
            (requests.exceptions.Timeout, httpx.TimeoutException),
        ):
            return (
                504,
                "Kết nối tới dịch vụ xử lý PDF đã hết thời gian. "
                "Hãy kiểm tra log backend.",
            )
        return (
            503,
            "Không thể kết nối dịch vụ xử lý PDF. "
            "Hãy kiểm tra kết nối và thử lại.",
        )

    return (
        500,
        "Không thể xử lý PDF. Hãy kiểm tra log backend.",
    )


class UploadJobManager:
    def __init__(
        self,
        max_active: int = 4,
        max_history: int = 128,
    ):
        if not 1 <= max_active <= max_history:
            raise ValueError("Invalid upload queue limits")

        self._max_active = max_active
        self._max_history = max_history
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="quiz-upload",
        )
        self._closed = False

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job is not None else None

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(fields)

    def submit(self, work: Work) -> dict[str, Any]:
        with self._lock:
            active_count = sum(
                job["status"] in ACTIVE
                for job in self._jobs.values()
            )

            if self._closed or active_count >= self._max_active:
                raise UploadQueueFull("Upload queue unavailable")

            for job_id in list(self._jobs):
                if len(self._jobs) < self._max_history:
                    break
                if self._jobs[job_id]["status"] not in ACTIVE:
                    del self._jobs[job_id]

            job_id = str(uuid.uuid4())
            job = {
                "job_id": job_id,
                "status": "queued",
                "phase": "queued",
                "result": None,
                "error": None,
                "error_status": None,
            }
            self._jobs[job_id] = job
            self._executor.submit(self._run, job_id, work)
            return dict(job)

    def _run(self, job_id: str, work: Work) -> None:
        self._update(job_id, status="processing", phase="starting")

        try:
            result = work(lambda phase: self._update(job_id, phase=phase))
        except HTTPException as exc:
            logger.info(
                "job=%s status=failed http_status=%s",
                job_id,
                exc.status_code,
            )
            self._update(
                job_id,
                status="failed",
                error=str(exc.detail),
                error_status=exc.status_code,
            )
        except Exception as exc:
            status, message = _public_failure(exc)
            logger.exception(
                "job=%s status=failed http_status=%d error_type=%s",
                job_id,
                status,
                type(exc).__name__,
            )
            self._update(
                job_id,
                status="failed",
                error=message,
                error_status=status,
            )
        else:
            logger.info("job=%s status=completed", job_id)
            self._update(
                job_id,
                status="completed",
                phase="completed",
                result=result,
            )

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True)
