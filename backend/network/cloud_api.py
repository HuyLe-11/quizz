"""Authenticated Supabase API for QuizPDF.

Use this app through backend.network.secure_app.
The authentication gateway verifies the caller's JWT.
"""

import logging
import hashlib
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
        MAX_PDF_BYTES,
        _question_response,
        _read_upload,
    )
    from backend.rag.gemini_service import (
        GeminiAPIError,
        GeminiService,
        GeminiServiceError,
        GeminiValidationError,
    )
    from backend.rag.syllabus_ingestion import SyllabusEmbeddingError
    from backend.rag.syllabus_parser import (
        SyllabusPDFEncryptedError,
        SyllabusPDFError,
        SyllabusPDFNoTextError,
    )
    from backend.rag.syllabus_rag_service import (
        SyllabusRAGError,
        SyllabusRAGPersistenceError,
        SyllabusRAGService,
    )
    from backend.rag.question_generation import (
        MAX_TOP_K as MAX_QUESTION_TOP_K,
        QuestionGenerationService,
        QuestionOutputError,
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
        MAX_PDF_BYTES,
        _question_response,
        _read_upload,
    )
    from rag.gemini_service import (
        GeminiAPIError,
        GeminiService,
        GeminiServiceError,
        GeminiValidationError,
    )
    from rag.syllabus_ingestion import SyllabusEmbeddingError
    from rag.syllabus_parser import (
        SyllabusPDFEncryptedError,
        SyllabusPDFError,
        SyllabusPDFNoTextError,
    )
    from rag.syllabus_rag_service import (
        SyllabusRAGError,
        SyllabusRAGPersistenceError,
        SyllabusRAGService,
    )
    from rag.question_generation import (
        MAX_TOP_K as MAX_QUESTION_TOP_K,
        QuestionGenerationService,
        QuestionOutputError,
    )
    from network.upload_jobs import (
        Progress,
        UploadJobManager,
        UploadQueueFull,
    )


logger = logging.getLogger("quizpdf")
logger.info("app=quizpdf cloud_api_loaded module=%s", __file__)

Identity = tuple[str, str]
SIGNED_URL_EXPIRATION_SECONDS = 600
MAX_SYLLABUS_PDF_BYTES = MAX_PDF_BYTES


class SyllabusSearchPayload(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=10, ge=1, le=100)


class SyllabusQuestionGenerationPayload(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    question_count: int = Field(default=5, ge=1, le=10)
    difficulty: str = Field(default="medium", pattern="^(easy|medium|hard)$")
    language: str = Field(default="Vietnamese", min_length=1, max_length=40)
    top_k: int = Field(default=8, ge=1, le=MAX_QUESTION_TOP_K)


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
    if os.getenv("VERCEL"):
        app.state.cloud_jobs = ScopedUploadJobs()
    else:
        from .durable_upload_jobs import DurableUploadJobs
        app.state.cloud_jobs = await run_in_threadpool(DurableUploadJobs)

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


async def _read_syllabus_upload(file: UploadFile) -> tuple[bytes, str]:
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    media_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if not filename.lower().endswith(".pdf") or media_type != "application/pdf":
        await file.close()
        raise HTTPException(
            status_code=415,
            detail="Upload a PDF file with the application/pdf content type.",
        )

    try:
        contents = await file.read(MAX_SYLLABUS_PDF_BYTES + 1)
    finally:
        await file.close()
    if len(contents) > MAX_SYLLABUS_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Syllabus PDF exceeds the {MAX_SYLLABUS_PDF_BYTES // (1024 * 1024)} MiB limit.",
        )
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded PDF is empty.")
    return contents, filename


def _syllabus_public_document(document: dict[str, Any]) -> dict[str, Any]:
    """Return safe metadata only; never expose storage paths or vectors."""
    metadata = document.get("metadata") or {}
    public_metadata = {
        key: metadata[key]
        for key in ("embedding_model", "embedding_dimensions")
        if isinstance(metadata, dict) and key in metadata
    }
    return {
        "document_id": document.get("id"),
        "title": document.get("title"),
        "subject": document.get("subject"),
        "source_file": document.get("source_file"),
        "processing_status": document.get("processing_status"),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
        "metadata": public_metadata,
    }


def _syllabus_http_error(exc: SyllabusRAGError) -> HTTPException:
    cause: BaseException | None = exc
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        message = str(cause)
        if isinstance(cause, SyllabusPDFNoTextError):
            return HTTPException(status_code=422, detail=message)
        if isinstance(cause, (SyllabusPDFEncryptedError, SyllabusPDFError)):
            return HTTPException(status_code=400, detail=message)
        if isinstance(cause, SyllabusEmbeddingError):
            if "HTTP 429" in message:
                return HTTPException(
                    status_code=503,
                    detail="Gemini quota is temporarily exhausted. Retry later.",
                    headers={"Retry-After": "30"},
                )
            return HTTPException(
                status_code=502,
                detail="Gemini could not create all syllabus embeddings. Retry later.",
            )
        if "GEMINI_API_KEY" in message:
            return HTTPException(
                status_code=503,
                detail="Syllabus embedding is not configured on the backend.",
            )
        cause = cause.__cause__ or cause.__context__
    if isinstance(exc, SyllabusRAGPersistenceError):
        return HTTPException(
            status_code=502,
            detail="Syllabus persistence did not complete. Retry the same prepared operation.",
        )
    return HTTPException(
        status_code=502,
        detail="Could not complete syllabus ingestion. Retry later.",
    )


def _ingest_syllabus_sync(
    identity: Identity,
    contents: bytes,
    filename: str,
    title: str,
    subject: str | None,
    document_id: str | None,
):
    with CloudRepository(*identity) as repository:
        source_digest = hashlib.sha256(contents).hexdigest()
        if document_id is not None:
            existing = repository.get_syllabus_document(document_id)
            if existing is not None:
                metadata = existing.get("metadata") or {}
                if (
                    metadata.get("source_sha256") != source_digest
                    or existing.get("title") != title
                    or existing.get("subject") != subject
                    or existing.get("source_file") != filename
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="document_id is already associated with different syllabus input.",
                    )
                counts = existing.get("syllabus_chunks") or []
                count = counts[0].get("count", 0) if counts and isinstance(counts[0], dict) else 0
                return {
                    "document_id": existing["id"],
                    "status": existing.get("processing_status", "completed"),
                    "chunk_count": count,
                }
        service = SyllabusRAGService(repository)
        result = service.ingest_and_persist(
            contents,
            title=title,
            subject=subject,
            source_file=filename,
            document_id=document_id,
            metadata={"source_sha256": source_digest},
        )
        return {
            "document_id": result.document_id,
            "status": "completed",
            "chunk_count": result.chunk_count,
        }


@app.post("/api/syllabi")
async def create_syllabus(
    identity: CurrentIdentity,
    file: UploadFile = File(...),
    title: str = Form(...),
    subject: str | None = Form(None),
    document_id: str | None = Form(None),
):
    if not title.strip():
        await file.close()
        raise HTTPException(status_code=422, detail="title must not be empty.")
    if subject is not None and not subject.strip():
        subject = None
    if document_id is not None:
        try:
            document_id = str(uuid.UUID(document_id))
        except (ValueError, TypeError, AttributeError) as exc:
            await file.close()
            raise HTTPException(status_code=422, detail="document_id must be a UUID.") from exc
    contents, filename = await _read_syllabus_upload(file)
    try:
        result = await run_in_threadpool(
            _ingest_syllabus_sync,
            identity,
            contents,
            filename,
            title.strip(),
            subject,
            document_id,
        )
    except SyllabusRAGError as exc:
        http_error = _syllabus_http_error(exc)
        if isinstance(exc, SyllabusRAGPersistenceError):
            cause = exc.__cause__
            logger.error(
                "rag=syllabus stage=api_persistence status=failed document_id=%s "
                "http_status=%s error_code=%s error_type=%s",
                exc.prepared.document_id,
                getattr(cause, "status_code", None) or "none",
                getattr(cause, "error_code", None) or "unknown",
                type(cause).__name__ if cause is not None else "unknown",
            )
            http_error.headers = {
                **(http_error.headers or {}),
                "X-Syllabus-Document-ID": exc.prepared.document_id,
            }
        raise http_error from exc

    return {
        "success": True,
        "data": {
            "document_id": result["document_id"],
            "title": title.strip(),
            "subject": subject,
            "status": result["status"],
            "chunk_count": result["chunk_count"],
        },
    }


@app.get("/api/syllabi")
def list_syllabi(identity: CurrentIdentity):
    with CloudRepository(*identity) as repository:
        documents = repository.list_syllabus_documents()
    return {
        "success": True,
        "data": [_syllabus_public_document(document) for document in documents],
    }


@app.get("/api/syllabi/{document_id}")
def get_syllabus(document_id: str, identity: CurrentIdentity):
    try:
        with CloudRepository(*identity) as repository:
            document = repository.get_syllabus_document(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="document_id must be a UUID.") from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Syllabus not found.")
    return {"success": True, "data": _syllabus_public_document(document)}


def _search_syllabus_sync(
    identity: Identity,
    document_id: str,
    query: str,
    top_k: int,
) -> list[dict[str, Any]] | None:
    with CloudRepository(*identity) as repository:
        document = repository.get_syllabus_document(document_id)
        if document is None:
            return None
        gemini = GeminiService()
        try:
            vector = gemini.embed_text(
                query,
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=1536,
            )
        finally:
            gemini.close()
        return repository.match_syllabus_chunks(
            vector,
            match_count=top_k,
            document_id=document_id,
        )


@app.post("/api/syllabi/{document_id}/search")
async def search_syllabus(
    document_id: str,
    payload: SyllabusSearchPayload,
    identity: CurrentIdentity,
):
    if not payload.query.strip():
        raise HTTPException(status_code=422, detail="query must not be empty.")
    try:
        matches = await run_in_threadpool(
            _search_syllabus_sync,
            identity,
            document_id,
            payload.query.strip(),
            payload.top_k,
        )
    except GeminiAPIError as exc:
        if exc.status_code == 429:
            raise HTTPException(
                status_code=503,
                detail="Gemini quota is temporarily exhausted. Retry later.",
                headers={"Retry-After": "30"},
            ) from exc
        raise HTTPException(
            status_code=502,
            detail="Gemini query embedding failed. Retry later.",
        ) from exc
    except GeminiValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GeminiServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail="Gemini query embedding is unavailable. Retry later.",
        ) from exc

    if matches is None:
        raise HTTPException(status_code=404, detail="Syllabus not found.")
    return {
        "success": True,
        "data": {
            "document_id": document_id,
            "query": payload.query.strip(),
            "matches": [
                {
                    "chunk_id": match.get("chunk_id"),
                    "chunk_index": match.get("chunk_index"),
                    "content": match.get("content"),
                    "similarity": match.get("similarity"),
                    "source_pages": match.get("source_pages"),
                    "section_heading": match.get("section_heading"),
                }
                for match in matches
            ],
        },
    }


def _generate_syllabus_questions_sync(
    identity: Identity,
    document_id: str,
    payload: SyllabusQuestionGenerationPayload,
) -> Any:
    with CloudRepository(*identity) as repository:
        service = QuestionGenerationService()
        try:
            return service.generate(
                repository,
                document_id=document_id,
                topic=payload.topic.strip(),
                question_count=payload.question_count,
                difficulty=payload.difficulty,
                language=payload.language.strip(),
                top_k=payload.top_k,
            )
        finally:
            service.close()


@app.post("/api/syllabi/{document_id}/generate")
async def generate_syllabus_questions(
    document_id: str,
    payload: SyllabusQuestionGenerationPayload,
    identity: CurrentIdentity,
):
    if not payload.topic.strip() or not payload.language.strip():
        raise HTTPException(status_code=422, detail="topic and language must not be empty.")
    try:
        result = await run_in_threadpool(
            _generate_syllabus_questions_sync, identity, document_id, payload
        )
    except GeminiAPIError as exc:
        if exc.status_code == 429:
            raise HTTPException(
                status_code=503,
                detail="Gemini quota is temporarily exhausted. Retry later.",
                headers={"Retry-After": "30"},
            ) from exc
        raise HTTPException(
            status_code=502,
            detail="Question generation service failed. Retry later.",
        ) from exc
    except QuestionOutputError as exc:
        logger.warning("rag=question_generation output_validation=failed reason=%s", str(exc))
        raise HTTPException(
            status_code=502,
            detail="Question generation returned an invalid response. Retry later.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GeminiServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail="Question generation service is unavailable. Retry later.",
        ) from exc

    if result is None:
        raise HTTPException(status_code=404, detail="Syllabus not found.")
    return {
        "success": True,
        "data": {
            "document_id": result.document_id,
            "document_title": result.document_title,
            "topic": result.topic,
            "status": result.status,
            "message": result.message,
            "questions": [
                {
                    "question": question.question,
                    "options": [
                        {"label": chr(ord("A") + index), "content": option}
                        for index, option in enumerate(question.options)
                    ],
                    "correct_answer_index": question.correct_answer_index,
                    "correct_answer": chr(ord("A") + question.correct_answer_index),
                    "explanation": question.explanation,
                    "difficulty": question.difficulty,
                    "grounding_type": question.grounding_type,
                    "source_references": list(question.source_references),
                }
                for question in result.questions
            ],
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
