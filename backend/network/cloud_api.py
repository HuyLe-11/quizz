"""Authenticated Supabase API for QuizPDF.

Use this app through backend.network.secure_app.
The authentication gateway verifies the caller's JWT.
"""

import logging
import os
import threading
import time
import uuid

from contextlib import asynccontextmanager
from typing import Annotated, Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

try:
    from backend.cloud_pipeline import process_cloud_pdf
    from backend.cloud_repository import (
        BUCKET,
        CloudRepository,
        CloudRepositoryError,
    )
    from backend.cloud_review import (
        CloudReviewError,
        CloudReviewService,
    )
    from backend.network.main import (
        _question_response,
        _read_upload,
    )
    from backend.network.upload_jobs import (
        Progress,
        UploadJobManager,
        UploadQueueFull,
    )
except ModuleNotFoundError as exc:
    if exc.name != "backend":
        raise

    from cloud_pipeline import process_cloud_pdf
    from cloud_repository import (
        BUCKET,
        CloudRepository,
        CloudRepositoryError,
    )
    from cloud_review import (
        CloudReviewError,
        CloudReviewService,
    )
    from network.main import (
        _question_response,
        _read_upload,
    )
    from network.upload_jobs import (
        Progress,
        UploadJobManager,
        UploadQueueFull,
    )


logger = logging.getLogger("quizpdf")

Identity = tuple[str, str]
SIGNED_URL_EXPIRATION_SECONDS = 600


class AttemptPayload(BaseModel):
    score: int = Field(ge=0, le=100)
    total_questions: int = Field(ge=0)
    correct_answers_count: int = Field(ge=0)
    time_spent: int = Field(ge=0)
    answers: dict[str, Any]
    question_results: list[dict[str, Any]]
    completed_at: int = Field(ge=0)


class RedAnswerConfirmation(BaseModel):
    answer: str | None


def authenticated_identity(
    request: Request,
) -> Identity:
    """Read identity set by the verified authentication gateway."""

    user_id = getattr(
        request.state,
        "supabase_user_id",
        None,
    )

    authorization = request.headers.get(
        "Authorization",
        "",
    )

    scheme, separator, token = (
        authorization.partition(" ")
    )

    if (
        not isinstance(user_id, str)
        or not user_id
        or not separator
        or scheme.lower() != "bearer"
        or not token.strip()
    ):
        raise HTTPException(
            status_code=401,
            detail="Phiên đăng nhập không hợp lệ.",
        )

    return user_id, token.strip()


CurrentIdentity = Annotated[
    Identity,
    Depends(authenticated_identity),
]


class ScopedUploadJobs:
    """Bind in-memory upload jobs to their submitting owner."""

    def __init__(self) -> None:
        self.manager = UploadJobManager()
        self.lock = threading.Lock()
        self.owners: dict[str, str] = {}

    def submit(
        self,
        owner_id: str,
        work,
    ) -> dict:
        with self.lock:
            job = self.manager.submit(work)
            self.owners[job["job_id"]] = owner_id

            if len(self.owners) > 256:
                for job_id in list(self.owners):
                    if self.manager.get(job_id) is None:
                        del self.owners[job_id]

            return job

    def get(
        self,
        owner_id: str,
        job_id: str,
    ) -> dict | None:
        with self.lock:
            if self.owners.get(job_id) != owner_id:
                return None

            return self.manager.get(job_id)

    def shutdown(self) -> None:
        self.manager.shutdown()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.cloud_jobs = ScopedUploadJobs()

    try:
        yield
    finally:
        await run_in_threadpool(
            app.state.cloud_jobs.shutdown
        )


app = FastAPI(
    title="QuizPDF Cloud API",
    lifespan=lifespan,
)


@app.exception_handler(CloudRepositoryError)
async def handle_cloud_repository_error(
    request: Request,
    exc: CloudRepositoryError,
):
    logger.error(
        "cloud=request_failed path=%s error=%s",
        request.url.path,
        exc,
    )

    return JSONResponse(
        status_code=502,
        content={
            "detail": (
                "Không thể truy cập Supabase. "
                "Hãy kiểm tra log backend."
            ),
        },
    )


@app.exception_handler(CloudReviewError)
async def handle_cloud_review_error(
    request: Request,
    exc: CloudReviewError,
):
    logger.warning(
        "cloud=review_failed path=%s "
        "status=%d detail=%s",
        request.url.path,
        exc.status_code,
        str(exc),
    )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": str(exc),
        },
    )


def _validate_image_path(
    repository: CloudRepository,
    image_path: str,
) -> None:
    """Reject any image outside the authenticated user's prefix."""
    prefix = f"{repository.user_id}/assets/"

    if (
        not isinstance(image_path, str)
        or not image_path.startswith(prefix)
        or len(image_path) <= len(prefix)
    ):
        raise CloudRepositoryError(
            "Question image is outside the user's Storage path."
        )


def _signed_image_url(
    repository: CloudRepository,
    image_path: str | None,
) -> str | None:
    """Create one signed URL for the existing review endpoints."""
    if not image_path:
        return None

    _validate_image_path(repository, image_path)
    encoded = quote(image_path, safe="/")

    result = repository._request(
        "POST",
        f"/storage/v1/object/sign/{BUCKET}/{encoded}",
        json_data={
            "expiresIn": SIGNED_URL_EXPIRATION_SECONDS,
        },
    )

    if not isinstance(result, dict):
        raise CloudRepositoryError(
            "Invalid Storage signed URL response."
        )

    signed_path = (
        result.get("signedURL")
        or result.get("signedUrl")
    )

    if not isinstance(signed_path, str):
        raise CloudRepositoryError(
            "Storage did not return a signed URL."
        )

    if signed_path.startswith("/storage/v1/"):
        return repository.base_url + signed_path

    if signed_path.startswith("/object/sign/"):
        return (
            repository.base_url
            + "/storage/v1"
            + signed_path
        )

    expected_prefix = (
        repository.base_url
        + "/storage/v1/object/sign/"
    )

    if signed_path.startswith(expected_prefix):
        return signed_path

    raise CloudRepositoryError(
        "Storage returned an unexpected URL."
    )


def _normalize_batch_signed_url(
    repository: CloudRepository,
    image_path: str,
    signed_path: str,
) -> str:
    """Verify that each signed URL targets its requested private image.

    The Storage API may return a path relative to /storage/v1,
    a full Storage path, or an absolute URL. Never accept an
    unrelated origin or an image path belonging to another item.
    """
    if signed_path.startswith("/storage/v1/object/sign/"):
        url = repository.base_url + signed_path
    elif signed_path.startswith("/object/sign/"):
        url = (
            repository.base_url
            + "/storage/v1"
            + signed_path
        )
    elif signed_path.startswith(
        repository.base_url + "/storage/v1/object/sign/"
    ):
        url = signed_path
    elif signed_path.startswith(
        quote(image_path, safe="/") + "?"
    ):
        url = (
            f"{repository.base_url}/storage/v1/object/sign/"
            f"{BUCKET}/{signed_path}"
        )
    else:
        raise CloudRepositoryError(
            "Storage returned an unexpected batch signed URL."
        )

    parsed = urlsplit(url)
    base = urlsplit(repository.base_url)

    expected_path = (
        f"/storage/v1/object/sign/{BUCKET}/{image_path}"
    )

    if (
        parsed.scheme != base.scheme
        or parsed.netloc != base.netloc
        or unquote(parsed.path) != expected_path
        or not parse_qs(parsed.query).get("token")
    ):
        raise CloudRepositoryError(
            "Storage signed URL does not match its requested image."
        )

    return url


def _signed_image_urls(
    repository: CloudRepository,
    image_paths: list[str],
) -> dict[str, str]:
    """Sign all distinct private image paths in one Storage request.

    The result is mapped by object path, never by response order.
    The single-image path remains backward compatible.
    """
    paths = list(dict.fromkeys(image_paths))

    for image_path in paths:
        _validate_image_path(repository, image_path)

    if not paths:
        return {}

    if len(paths) == 1:
        path = paths[0]
        signed_url = _signed_image_url(repository, path)

        if signed_url is None:
            raise CloudRepositoryError(
                "Storage did not return a signed URL."
            )

        return {path: signed_url}

    started = time.monotonic()

    result = repository._request(
        "POST",
        f"/storage/v1/object/sign/{BUCKET}",
        json_data={
            "expiresIn": SIGNED_URL_EXPIRATION_SECONDS,
            "paths": paths,
        },
    )

    if not isinstance(result, list):
        raise CloudRepositoryError(
            "Storage returned an invalid batch signing response."
        )

    requested = set(paths)
    signed_urls: dict[str, str] = {}

    for entry in result:
        if not isinstance(entry, dict):
            raise CloudRepositoryError(
                "Storage returned an invalid signed URL entry."
            )

        path = entry.get("path")

        if (
            not isinstance(path, str)
            or path not in requested
            or path in signed_urls
        ):
            raise CloudRepositoryError(
                "Storage returned an unexpected or duplicate image path."
            )

        if entry.get("error"):
            raise CloudRepositoryError(
                "Storage failed to sign one or more images."
            )

        signed_path = (
            entry.get("signedURL")
            or entry.get("signedUrl")
        )

        if not isinstance(signed_path, str):
            raise CloudRepositoryError(
                "Storage omitted a signed image URL."
            )

        signed_urls[path] = _normalize_batch_signed_url(
            repository,
            path,
            signed_path,
        )

    if len(signed_urls) != len(requested):
        raise CloudRepositoryError(
            "Storage returned an incomplete set of signed URLs."
        )

    logger.info(
        "cloud=image_signing status=completed images=%d "
        "requests=1 duration_seconds=%.2f",
        len(paths),
        time.monotonic() - started,
    )

    return signed_urls


def _cloud_exam_response(
    repository: CloudRepository,
    exam: dict,
) -> dict:
    prepared: list[tuple[dict, str | None]] = []
    image_paths: list[str] = []

    for question in exam["questions"]:
        item = _question_response(question)
        asset = question.get("asset")

        image_path = (
            asset.get("image_path")
            if isinstance(asset, dict)
            else None
        )

        if image_path is not None:
            _validate_image_path(repository, image_path)

        if image_path:
            image_paths.append(image_path)

        prepared.append((item, image_path))

    signed_urls = _signed_image_urls(
        repository,
        image_paths,
    )

    questions = []

    for item, image_path in prepared:
        item["question_image_url"] = (
            signed_urls[image_path]
            if image_path
            else None
        )
        questions.append(item)

    return {
        "id": exam["id"],
        "subject_id": (
            exam.get("subject") or "uploaded"
        ),
        "title": exam["name"],
        "question_count": len(questions),
        "questions": questions,
        "source_file": exam.get("source_file"),
        "uploaded_at": exam.get("uploaded_at"),
        "has_source_pdf": bool(
            exam.get("source_path")
        ),
    }


@app.get("/api/health")
def health():
    return {
        "success": True,
        "data": {
            "status": "ok",
            "storage_backend": "supabase",
        },
    }


@app.get("/api/exams")
def list_exams(
    identity: CurrentIdentity,
):
    with CloudRepository(*identity) as repository:
        exams = repository.list_exams()

    return {
        "success": True,
        "data": exams,
    }


@app.get("/api/exams/{exam_id}")
def get_exam(
    exam_id: str,
    identity: CurrentIdentity,
):
    started = time.monotonic()

    with CloudRepository(*identity) as repository:
        exam = repository.get_exam_record(exam_id)

        if exam is None:
            raise HTTPException(
                status_code=404,
                detail="Không tìm thấy đề thi.",
            )

        result = _cloud_exam_response(
            repository,
            exam,
        )

    logger.info(
        "cloud=exam_read status=completed exam=%s "
        "questions=%d duration_seconds=%.2f",
        exam_id,
        result["question_count"],
        time.monotonic() - started,
    )

    return {
        "success": True,
        "data": result,
    }


@app.post("/api/upload")
async def upload_pdf(
    identity: CurrentIdentity,
    file: UploadFile = File(...),
    model: str = Form("PaddleOCR-VL-1.6"),
):
    """Legacy synchronous upload using Supabase persistence."""

    contents, filename = await _read_upload(
        file,
        model,
    )

    result = await run_in_threadpool(
        process_cloud_pdf,
        contents,
        filename,
        model,
        identity[0],
        identity[1],
    )

    return {
        "success": True,
        "data": result,
    }


@app.post(
    "/api/upload/jobs",
    status_code=202,
)
async def create_upload_job(
    request: Request,
    response: Response,
    identity: CurrentIdentity,
    file: UploadFile = File(...),
    model: str = Form("PaddleOCR-VL-1.6"),
):
    if os.getenv("VERCEL"):
        raise HTTPException(
            status_code=503,
            detail=(
                "Upload jobs yêu cầu backend chạy liên tục. "
                "Không hỗ trợ worker nền trên Vercel Functions."
            ),
        )

    contents, filename = await _read_upload(
        file,
        model,
    )

    owner_id, access_token = identity

    jobs: ScopedUploadJobs = (
        request.app.state.cloud_jobs
    )

    def run_pipeline(progress: Progress):
        return process_cloud_pdf(
            contents,
            filename,
            model,
            owner_id,
            access_token,
            progress,
        )

    try:
        job = jobs.submit(
            owner_id,
            run_pipeline,
        )
    except UploadQueueFull as exc:
        raise HTTPException(
            status_code=429,
            detail="Hàng đợi PDF đã đầy.",
            headers={"Retry-After": "5"},
        ) from exc

    response.headers["Location"] = (
        f"/api/upload/jobs/{job['job_id']}"
    )
    response.headers["Retry-After"] = "2"
    response.headers["Cache-Control"] = "no-store"

    return {
        "success": True,
        "data": job,
    }


@app.get("/api/upload/jobs/{job_id}")
def get_upload_job(
    job_id: str,
    request: Request,
    response: Response,
    identity: CurrentIdentity,
):
    jobs: ScopedUploadJobs = (
        request.app.state.cloud_jobs
    )

    job = jobs.get(
        identity[0],
        job_id,
    )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy tác vụ xử lý PDF.",
        )

    response.headers["Cache-Control"] = "no-store"

    if job["status"] in {"queued", "processing"}:
        response.headers["Retry-After"] = "2"

    return {
        "success": True,
        "data": job,
    }


@app.post(
    "/api/questions/{question_record_id}/crop-image"
)
def crop_question_image(
    question_record_id: str,
    identity: CurrentIdentity,
    force: bool = False,
):
    """Create or retrieve an owner-scoped cropped image."""

    with CloudRepository(*identity) as repository:
        service = CloudReviewService(repository)

        result = service.crop_image(
            question_record_id,
            force=force,
        )

    return {
        "success": True,
        "data": result,
    }


@app.post(
    "/api/questions/{question_record_id}/detect-red-mark"
)
def detect_question_red_mark(
    question_record_id: str,
    identity: CurrentIdentity,
    force: bool = False,
):
    """Detect red answer markings in the private source PDF."""

    with CloudRepository(*identity) as repository:
        service = CloudReviewService(repository)

        result = service.detect_red_mark(
            question_record_id,
            force=force,
        )

    return {
        "success": True,
        "data": result,
    }


@app.patch(
    "/api/questions/{question_record_id}/red-mark-confirmation"
)
def confirm_question_red_mark(
    question_record_id: str,
    payload: RedAnswerConfirmation,
    identity: CurrentIdentity,
):
    """Persist an explicitly selected answer with owner RLS."""

    with CloudRepository(*identity) as repository:
        service = CloudReviewService(repository)

        result = service.confirm_red_answer(
            question_record_id,
            payload.answer,
        )

    return {
        "success": True,
        "data": result,
    }


@app.post("/api/exams/{exam_id}/attempts")
def create_attempt(
    exam_id: str,
    payload: AttemptPayload,
    identity: CurrentIdentity,
):
    with CloudRepository(*identity) as repository:
        exam = repository.get_exam_record(exam_id)

        if exam is None:
            raise HTTPException(
                status_code=404,
                detail="Không tìm thấy đề thi.",
            )

        attempt = payload.model_dump()
        attempt["id"] = str(uuid.uuid4())
        attempt["exam_id"] = exam_id

        result = repository.save_attempt(
            attempt
        )

    return {
        "success": True,
        "data": result,
    }


@app.get("/api/attempts")
def list_attempts(
    identity: CurrentIdentity,
):
    with CloudRepository(*identity) as repository:
        attempts = repository.list_attempts()

    return {
        "success": True,
        "data": attempts,
    }
