# In-memory job registry for a single long-running backend instance.
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from fastapi import HTTPException


logger = logging.getLogger("quizpdf")

Progress = Callable[[str], None]
Work = Callable[[Progress], dict[str, Any]]

ACTIVE = {"queued", "processing"}


class UploadQueueFull(RuntimeError):
    pass


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

    def get(
        self,
        job_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)

            return (
                dict(job)
                if job is not None
                else None
            )

    def _update(
        self,
        job_id: str,
        **fields: Any,
    ) -> None:
        with self._lock:
            self._jobs[job_id].update(fields)

    def submit(
        self,
        work: Work,
    ) -> dict[str, Any]:
        with self._lock:
            active_count = sum(
                job["status"] in ACTIVE
                for job in self._jobs.values()
            )

            if (
                self._closed
                or active_count >= self._max_active
            ):
                raise UploadQueueFull(
                    "Upload queue unavailable"
                )

            # Remove old completed jobs to bound memory usage.
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

            self._executor.submit(
                self._run,
                job_id,
                work,
            )

            return dict(job)

    def _run(
        self,
        job_id: str,
        work: Work,
    ) -> None:
        self._update(
            job_id,
            status="processing",
            phase="starting",
        )

        try:
            result = work(
                lambda phase: self._update(
                    job_id,
                    phase=phase,
                )
            )

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

        except Exception:
            logger.exception(
                "job=%s status=failed",
                job_id,
            )

            self._update(
                job_id,
                status="failed",
                error=(
                    "Không thể xử lý PDF. "
                    "Hãy kiểm tra log backend."
                ),
                error_status=500,
            )

        else:
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
