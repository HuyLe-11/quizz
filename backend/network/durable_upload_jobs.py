"""Single-process cloud upload worker with persistent owner-scoped status."""
from __future__ import annotations

import logging
import os
import threading

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import HTTPException

from .upload_job_registry import (
    DEFAULT_PATH,
    UploadJobRegistry,
)

from .upload_jobs import (
    UploadQueueFull,
    Work,
    _public_failure,
)

logger = logging.getLogger("quizpdf")


class _ProcessLock:
    """Lock a permanent sidecar file; the OS releases it on process exit."""

    def __init__(self, path: Path):

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )

        fd = os.open(
            path,
            os.O_CREAT | os.O_RDWR,
            0o600,
        )

        self.file = os.fdopen(
            fd,
            "r+b",
            buffering=0,
        )

        try:

            if os.fstat(fd).st_size == 0:
                self.file.write(b"\0")

            self.file.seek(0)

            if os.name == "nt":

                import msvcrt

                msvcrt.locking(
                    fd,
                    msvcrt.LK_NBLCK,
                    1,
                )

            else:

                import fcntl

                fcntl.flock(
                    fd,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )

        except OSError as exc:

            self.file.close()

            raise RuntimeError(
                "Cannot acquire upload registry lock; "
                "stop the other backend or check permissions."
            ) from exc

        except BaseException:

            self.file.close()

            raise

    def close(self) -> None:

        # Closing the file releases the OS lock.
        # Never unlink the sidecar.

        self.file.close()


class DurableUploadJobs:

    def __init__(
        self,
        path: Path | None = None,
        max_active: int = 4,
    ):

        if (
            type(max_active) is not int
            or max_active < 1
        ):
            raise ValueError(
                "Invalid upload queue limit"
            )

        if path is None:

            path = Path(
                os.getenv("QUIZPDF_UPLOAD_JOB_DB")
                or DEFAULT_PATH
            )

        path = Path(path).resolve()

        self._lock = threading.Lock()

        self._shutdown_lock = threading.Lock()

        self._closed = False

        self._active: set[str] = set()

        self._max_active = max_active

        self._process_lock = _ProcessLock(
            path.with_name(
                path.name + ".lock"
            )
        )

        try:

            self.registry = UploadJobRegistry(path)

            interrupted = (
                self.registry.interrupt_active()
            )

            self._executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="quiz-upload",
            )

        except BaseException:

            self._process_lock.close()

            raise

        logger.info(
            "upload_registry status=ready interrupted=%d",
            interrupted,
        )

    def get(
        self,
        owner_id: str,
        job_id: str,
    ) -> dict | None:

        return self.registry.get(
            owner_id,
            job_id,
        )

    def submit(
        self,
        owner_id: str,
        work: Work,
    ) -> dict:

        with self._lock:

            if (
                self._closed
                or len(self._active) >= self._max_active
            ):
                raise UploadQueueFull(
                    "Upload queue unavailable"
                )

            job = self.registry.create(owner_id)

            job_id = job["job_id"]

            self._active.add(job_id)

            try:

                self._executor.submit(
                    self._run,
                    owner_id,
                    job_id,
                    work,
                )

            except Exception:

                self._active.discard(job_id)

                self.registry.fail(
                    owner_id,
                    job_id,
                    error_status=503,
                    public_error=(
                        "Không thể khởi động "
                        "tác vụ xử lý PDF."
                    ),
                )

                raise UploadQueueFull(
                    "Upload worker unavailable"
                ) from None

            return job

    def _run(
        self,
        owner_id: str,
        job_id: str,
        work: Work,
    ) -> None:

        try:

            self.registry.start(
                owner_id,
                job_id,
            )

            result = work(
                lambda phase: self.registry.progress(
                    owner_id,
                    job_id,
                    phase,
                )
            )

            self.registry.complete(
                owner_id,
                job_id,
                result,
            )

        except Exception as exc:

            if isinstance(exc, HTTPException):

                status = exc.status_code

                if (
                    type(status) is not int
                    or not 400 <= status <= 599
                ):
                    status = 500

                message = (
                    "Không thể xử lý PDF. "
                    "Hãy kiểm tra log backend."
                )

            else:

                status, message = _public_failure(exc)

            logger.warning(
                "job=%s status=failed "
                "http_status=%d error_type=%s",
                job_id,
                status,
                type(exc).__name__,
            )

            try:

                self.registry.fail(
                    owner_id,
                    job_id,
                    error_status=status,
                    public_error=message,
                )

            except Exception as persistence_error:

                logger.error(
                    "job=%s status=persistence_failed "
                    "error_type=%s",
                    job_id,
                    type(persistence_error).__name__,
                )

        else:

            logger.info(
                "job=%s status=completed",
                job_id,
            )

        finally:

            with self._lock:

                self._active.discard(job_id)

    def shutdown(self) -> None:

        with self._shutdown_lock:

            with self._lock:

                if self._closed:
                    return

                self._closed = True

            self._executor.shutdown(
                wait=True
            )

            self._process_lock.close()