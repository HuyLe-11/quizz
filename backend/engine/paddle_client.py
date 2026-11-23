"""PaddleOCR hosted adapter with resilient HTTP transport."""

import json
import logging
import math
import os
import tempfile
import time

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import pymupdf
import requests

logger = logging.getLogger("quizpdf")

RETRYABLE_GET_STATUSES = {
    408,
    429,
    500,
    502,
    503,
    504,
}


class PaddleOCRAPIError(RuntimeError):
    def __init__(
        self,
        stage: str,
        status_code: int,
    ):
        self.stage = stage
        self.status_code = status_code

        super().__init__(
            f"PaddleOCR {stage} request returned "
            f"HTTP {status_code}."
        )


class PaddleOCRTimeoutError(TimeoutError):
    pass


class PaddleOCRNoTextError(RuntimeError):
    pass


class PaddleOCRJobFailedError(RuntimeError):
    """The OCR provider explicitly reported a failed job."""


def _check_response(
    response: requests.Response,
    stage: str,
) -> None:
    if not response.ok:
        raise PaddleOCRAPIError(
            stage,
            response.status_code,
        )


def _text_from(value: Any) -> str:
    """Extract page text without duplicating representations."""

    if not isinstance(value, dict):
        return ""

    for key in (
        "markdown_text",
        "markdownText",
    ):
        candidate = value.get(key)

        if (
            isinstance(candidate, str)
            and candidate.strip()
        ):
            return candidate.strip()

    markdown = value.get("markdown")

    if (
        isinstance(markdown, str)
        and markdown.strip()
    ):
        return markdown.strip()

    if isinstance(markdown, dict):
        candidate = markdown.get("text")

        if (
            isinstance(candidate, str)
            and candidate.strip()
        ):
            return candidate.strip()

    blocks = value.get(
        "parsing_res_list"
    )

    if isinstance(blocks, list):
        text = "\n".join(
            item["block_content"].strip()
            for item in blocks
            if (
                isinstance(item, dict)
                and isinstance(
                    item.get("block_content"),
                    str,
                )
                and item["block_content"].strip()
            )
        )

        if text:
            return text

    for key in (
        "rec_texts",
        "texts",
    ):
        items = value.get(key)

        if isinstance(items, list):
            text = "\n".join(
                item.strip()
                for item in items
                if (
                    isinstance(item, str)
                    and item.strip()
                )
            )

            if text:
                return text

    for key in (
        "text",
        "rec_text",
        "block_content",
    ):
        candidate = value.get(key)

        if (
            isinstance(candidate, str)
            and candidate.strip()
        ):
            return candidate.strip()

    for key in (
        "pruned_result",
        "prunedResult",
        "overall_ocr_res",
        "ocr_result",
        "raw",
        "result",
    ):
        nested = value.get(key)

        if isinstance(nested, dict):
            text = _text_from(nested)

            if text:
                return text

    return ""


def extract_page_texts(
    raw_response: dict[str, Any],
) -> list[str]:
    """Extract text while preserving empty page positions."""

    if not isinstance(
        raw_response,
        dict,
    ):
        return []

    for key in (
        "pages",
        "layoutParsingResults",
        "layout_parsing_results",
        "ocrResults",
        "ocr_results",
    ):
        pages = raw_response.get(key)

        if isinstance(pages, list):
            return [
                _text_from(page)
                for page in pages
            ]

    nested = raw_response.get(
        "result"
    )

    if isinstance(nested, dict):
        pages = extract_page_texts(
            nested
        )

        if pages:
            return pages

    text = _text_from(
        raw_response
    )

    return [text] if text else []


def _normalize_pages(
    result_lines: list[Any],
    model: str,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Normalize PaddleOCR JSONL and preserve OCR geometry."""

    pages: list[dict[str, Any]] = []
    data_info: dict[str, Any] = {}

    result_key = (
        "ocrResults"
        if model == "PP-OCRv6"
        else "layoutParsingResults"
    )

    for line in result_lines:
        result = (
            line.get("result")
            if isinstance(
                line,
                dict,
            )
            else None
        )

        if not isinstance(
            result,
            dict,
        ):
            raise RuntimeError(
                "PaddleOCR returned an "
                "invalid result object."
            )

        info = result.get(
            "dataInfo"
        )

        if isinstance(info, dict):
            data_info.update(info)

        items = result.get(
            result_key
        )

        if not isinstance(
            items,
            list,
        ):
            raise RuntimeError(
                "PaddleOCR result is "
                f"missing {result_key}."
            )

        for item in items:
            if not isinstance(
                item,
                dict,
            ):
                pages.append({
                    "raw": item,
                })

                continue

            page: dict[str, Any] = {
                "pruned_result": item.get(
                    "prunedResult"
                ),
                "input_image_url": item.get(
                    "inputImage"
                ),
                "raw": item,
            }

            if model == "PP-OCRv6":
                page["ocr_image_url"] = (
                    item.get("ocrImage")
                )

            else:
                markdown = item.get(
                    "markdown"
                )

                if not isinstance(
                    markdown,
                    dict,
                ):
                    markdown = {
                        "text": (
                            markdown
                            if isinstance(
                                markdown,
                                str,
                            )
                            else ""
                        )
                    }

                page.update({
                    "markdown_text": (
                        markdown.get("text")
                        or ""
                    ),
                    "markdown_images": (
                        markdown.get(
                            "images",
                            {},
                        )
                    ),
                    "output_images": (
                        item.get(
                            "outputImages",
                            {},
                        )
                    ),
                    "exports": (
                        item.get(
                            "exports",
                            {},
                        )
                    ),
                    "markdown": markdown,
                })

            pages.append(page)

    return pages, data_info


def _positive_number(
    name: str,
    default: str,
) -> float:
    try:
        value = float(
            os.getenv(
                name,
                default,
            )
        )

    except ValueError as exc:
        raise ValueError(
            "Invalid configuration "
            f"value for {name}."
        ) from exc

    if (
        not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(
            f"{name} must be a "
            "positive number."
        )

    return value


def _nested_timeout(
    error: BaseException,
) -> bool:
    """Detect SSL/socket timeouts wrapped by requests."""

    pending: list[Any] = [
        error
    ]

    visited: set[int] = set()

    while pending:
        current = pending.pop()

        if id(current) in visited:
            continue

        visited.add(
            id(current)
        )

        if isinstance(
            current,
            (
                requests.exceptions.Timeout,
                TimeoutError,
            ),
        ):
            return True

        if isinstance(
            current,
            BaseException,
        ):
            pending.extend([
                current.__cause__,
                current.__context__,
                *current.args,
            ])

        elif isinstance(
            current,
            (tuple, list),
        ):
            pending.extend(
                current
            )

    return False


def _request_error(
    exc: BaseException,
    stage: str,
) -> NoReturn:
    if _nested_timeout(exc):
        raise PaddleOCRTimeoutError(
            "PaddleOCR timed out "
            f"during {stage}."
        ) from exc

    raise RuntimeError(
        "Could not connect to "
        f"PaddleOCR during {stage}."
    ) from exc


def _upload_timeout(
    request_read_timeout: float,
) -> tuple[float, float]:
    """Use a separate socket timeout for upload POST.

    The first value is passed as Requests' connect timeout.
    Depending on the urllib3 transport, it can also govern
    SSL socket writes during the multipart upload.
    """

    socket_timeout = _positive_number(
        "PADDLEOCR_UPLOAD_SOCKET_TIMEOUT",
        "90",
    )

    if socket_timeout > 600:
        raise ValueError(
            "PADDLEOCR_UPLOAD_SOCKET_TIMEOUT "
            "must not exceed 600 seconds."
        )

    return (
        socket_timeout,
        request_read_timeout,
    )


def _get_retry_count() -> int:
    value = _positive_number(
        "PADDLEOCR_GET_RETRIES",
        "3",
    )

    if (
        not value.is_integer()
        or value > 10
    ):
        raise ValueError(
            "PADDLEOCR_GET_RETRIES "
            "must be an integer "
            "between 1 and 10."
        )

    return int(value)


def _retry_after_seconds(
    response: requests.Response,
) -> float | None:
    headers = getattr(
        response,
        "headers",
        None,
    )

    if not isinstance(
        headers,
        Mapping,
    ):
        return None

    raw = headers.get(
        "Retry-After"
    )

    try:
        value = float(raw)

    except (
        TypeError,
        ValueError,
    ):
        return None

    if (
        not math.isfinite(value)
        or value < 0
    ):
        return None

    return value


def _sleep_before_retry(
    attempt: int,
    deadline: float,
    *,
    stage: str,
    job_id: str,
    retry_after: float | None = None,
) -> None:
    delay = max(
        min(
            float(2 ** attempt),
            10.0,
        ),
        retry_after or 0.0,
    )

    remaining = (
        deadline
        - time.monotonic()
    )

    if (
        remaining <= 0
        or delay >= remaining
    ):
        raise PaddleOCRTimeoutError(
            f"PaddleOCR {stage} "
            "retry deadline exceeded "
            f"for job {job_id}."
        )

    time.sleep(delay)


def _get_with_retries(
    url: str,
    *,
    stage: str,
    job_id: str,
    timeout: tuple[float, float],
    deadline: float,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """Retry transient GET failures without resubmitting OCR."""

    max_retries = (
        _get_retry_count()
    )

    for attempt in range(
        max_retries + 1
    ):
        remaining = (
            deadline
            - time.monotonic()
        )

        if remaining <= 0:
            raise PaddleOCRTimeoutError(
                f"PaddleOCR {stage} "
                "deadline exceeded "
                f"for job {job_id}."
            )

        effective_timeout = (
            min(
                timeout[0],
                max(
                    1.0,
                    remaining,
                ),
            ),
            min(
                timeout[1],
                max(
                    1.0,
                    remaining,
                ),
            ),
        )

        kwargs: dict[str, Any] = {
            "timeout": effective_timeout,
        }

        if headers is not None:
            kwargs["headers"] = (
                headers
            )

        try:
            response = requests.get(
                url,
                **kwargs,
            )

        except (
            requests.RequestException,
            TimeoutError,
        ) as exc:
            if attempt == max_retries:
                _request_error(
                    exc,
                    f"{stage} "
                    f"(job_id={job_id})",
                )

            logger.warning(
                "paddleocr:get_retry "
                "stage=%s job=%s "
                "attempt=%d/%d "
                "error_type=%s",
                stage,
                job_id,
                attempt + 1,
                max_retries,
                type(exc).__name__,
            )

            _sleep_before_retry(
                attempt,
                deadline,
                stage=stage,
                job_id=job_id,
            )

            continue

        if response.ok:
            return response

        status_code = getattr(
            response,
            "status_code",
            None,
        )

        if (
            isinstance(
                status_code,
                int,
            )
            and status_code
            in RETRYABLE_GET_STATUSES
            and attempt < max_retries
        ):
            logger.warning(
                "paddleocr:get_retry "
                "stage=%s job=%s "
                "attempt=%d/%d "
                "http_status=%d",
                stage,
                job_id,
                attempt + 1,
                max_retries,
                status_code,
            )

            _sleep_before_retry(
                attempt,
                deadline,
                stage=stage,
                job_id=job_id,
                retry_after=(
                    _retry_after_seconds(
                        response
                    )
                ),
            )

            continue

        _check_response(
            response,
            stage,
        )

    raise RuntimeError(
        "Unexpected retry state "
        f"during {stage}."
    )


@dataclass(frozen=True)
class PDFBatch:
    path: Path
    start_page: int
    page_count: int | None


def _requires_raster(
    page: pymupdf.Page,
    min_drawings: int = 120,
) -> bool:
    text = page.get_text(
        "text"
    ).strip()

    return (
        len(text) < 30
        and len(
            page.get_drawings()
        ) >= min_drawings
    )


def _normalize_vector_pdf(
    source: Path,
    output: Path,
) -> tuple[
    Path,
    list[int],
]:
    """Rasterize vector-heavy pages while preserving page sizes."""

    dpi = int(
        _positive_number(
            "PADDLEOCR_VECTOR_DPI",
            "220",
        )
    )

    quality = int(
        _positive_number(
            "PADDLEOCR_VECTOR_JPEG_QUALITY",
            "88",
        )
    )

    if not 100 <= dpi <= 300:
        raise ValueError(
            "PADDLEOCR_VECTOR_DPI "
            "must be between 100 and 300."
        )

    if not 40 <= quality <= 95:
        raise ValueError(
            "PADDLEOCR_VECTOR_JPEG_QUALITY "
            "must be between 40 and 95."
        )

    try:
        document = pymupdf.open(
            source
        )

    except (
        pymupdf.FileDataError,
        ValueError,
        RuntimeError,
    ):
        logger.warning(
            "paddleocr:preprocess "
            "unable_to_open_pdf; "
            "using original"
        )

        return source, []

    with document:
        selected = [
            page.number
            for page in document
            if _requires_raster(
                page
            )
        ]

        if not selected:
            logger.info(
                "paddleocr:preprocess "
                "mode=original pages=%d",
                len(document),
            )

            return source, []

        selected_set = set(
            selected
        )

        with pymupdf.open() as normalized:
            for index, page in enumerate(
                document
            ):
                if index not in selected_set:
                    normalized.insert_pdf(
                        document,
                        from_page=index,
                        to_page=index,
                    )

                    continue

                pix = page.get_pixmap(
                    dpi=dpi,
                    alpha=False,
                )

                jpeg_bytes = (
                    pix.tobytes(
                        "jpeg",
                        jpg_quality=quality,
                    )
                )

                output_page = (
                    normalized.new_page(
                        width=page.rect.width,
                        height=page.rect.height,
                    )
                )

                output_page.insert_image(
                    output_page.rect,
                    stream=jpeg_bytes,
                    keep_proportion=False,
                )

            normalized.save(
                output,
                garbage=4,
                deflate=True,
            )

        logger.info(
            "paddleocr:preprocess "
            "mode=selective_rasterized "
            "pages=%d rasterized=%d "
            "dpi=%d output_bytes=%d",
            len(document),
            len(selected),
            dpi,
            output.stat().st_size,
        )

        return (
            output,
            [
                index + 1
                for index in selected
            ],
        )


def _batches(
    path: Path,
    output_dir: Path,
    *,
    max_pages: int,
    max_bytes: int,
) -> list[PDFBatch]:
    """Split PDF by page count and upload size."""

    try:
        document = pymupdf.open(
            path
        )

    except (
        pymupdf.FileDataError,
        ValueError,
        RuntimeError,
    ):
        return [
            PDFBatch(
                path,
                0,
                None,
            )
        ]

    with document:
        count = len(
            document
        )

        if count == 0:
            raise ValueError(
                "Empty PDF cannot "
                "be recognized."
            )

        if (
            count <= max_pages
            and path.stat().st_size
            <= max_bytes
        ):
            return [
                PDFBatch(
                    path,
                    0,
                    count,
                )
            ]

        batches: list[PDFBatch] = []
        start = 0

        while start < count:
            selected_bytes = None
            selected_end = start

            last_page = min(
                count,
                start + max_pages,
            )

            for end in range(
                start + 1,
                last_page + 1,
            ):
                with pymupdf.open() as part:
                    part.insert_pdf(
                        document,
                        from_page=start,
                        to_page=end - 1,
                    )

                    candidate = (
                        part.tobytes(
                            garbage=4,
                            deflate=True,
                        )
                    )

                if (
                    len(candidate)
                    > max_bytes
                    and selected_bytes
                    is not None
                ):
                    break

                selected_end = end
                selected_bytes = (
                    candidate
                )

                if len(candidate) > max_bytes:
                    break

            if selected_bytes is None:
                raise RuntimeError(
                    "Could not create "
                    "an OCR PDF batch."
                )

            chunk_path = (
                output_dir
                / f"ocr-part-{len(batches) + 1:03d}.pdf"
            )

            chunk_path.write_bytes(
                selected_bytes
            )

            if len(selected_bytes) > max_bytes:
                logger.warning(
                    "paddleocr:single_page_too_large "
                    "page=%d bytes=%d limit=%d",
                    start + 1,
                    len(selected_bytes),
                    max_bytes,
                )

            batches.append(
                PDFBatch(
                    path=chunk_path,
                    start_page=start,
                    page_count=(
                        selected_end - start
                    ),
                )
            )

            start = selected_end

        logger.info(
            "paddleocr:pdf_split "
            "pages=%d batches=%d bytes=%d",
            count,
            len(batches),
            path.stat().st_size,
        )

        return batches


def _process_batch(
    batch: PDFBatch,
    *,
    model: str,
    job_url: str,
    headers: dict[str, str],
    timeout: tuple[float, float],
    poll_timeout: float,
) -> tuple[
    str,
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Submit once, poll job status, then download results."""

    submit_timeout = (
        _upload_timeout(
            timeout[1]
        )
    )

    upload_started = (
        time.monotonic()
    )

    logger.info(
        "paddleocr:upload "
        "status=started "
        "first_page=%d bytes=%d "
        "socket_timeout=%.1f "
        "read_timeout=%.1f",
        batch.start_page + 1,
        batch.path.stat().st_size,
        submit_timeout[0],
        submit_timeout[1],
    )

    # Submit the PDF exactly once.
    try:
        with batch.path.open(
            "rb"
        ) as pdf_file:
            response = requests.post(
                job_url,
                headers=headers,
                data={
                    "model": model,
                    "optionalPayload": (
                        json.dumps({})
                    ),
                },
                files={
                    "file": (
                        batch.path.name,
                        pdf_file,
                        "application/pdf",
                    ),
                },
                timeout=submit_timeout,
            )

    except (
        requests.RequestException,
        TimeoutError,
    ) as exc:
        logger.warning(
            "paddleocr:upload "
            "status=failed "
            "duration_seconds=%.2f "
            "error_type=%s",
            (
                time.monotonic()
                - upload_started
            ),
            type(exc).__name__,
        )

        # A failed POST might already have created a job.
        # Do not submit it a second time automatically.
        _request_error(
            exc,
            "PDF upload",
        )

    _check_response(
        response,
        "submit",
    )

    try:
        submission = (
            response.json()
        )

    except ValueError as exc:
        raise RuntimeError(
            "PaddleOCR returned "
            "invalid submit JSON."
        ) from exc

    payload = (
        submission.get("data")
        if isinstance(
            submission,
            dict,
        )
        else None
    )

    job_id = (
        payload.get("jobId")
        if isinstance(
            payload,
            dict,
        )
        else None
    )

    if not job_id:
        raise RuntimeError(
            "PaddleOCR API response "
            "did not include a job ID."
        )

    job_id = str(
        job_id
    )

    logger.info(
        "paddleocr:upload "
        "status=completed "
        "job=%s "
        "duration_seconds=%.2f",
        job_id,
        (
            time.monotonic()
            - upload_started
        ),
    )

    deadline = (
        time.monotonic()
        + poll_timeout
    )

    interval = 3.0

    while True:
        remaining = (
            deadline
            - time.monotonic()
        )

        if remaining <= 0:
            raise PaddleOCRTimeoutError(
                "Timed out waiting for "
                f"PaddleOCR job {job_id}."
            )

        status_response = (
            _get_with_retries(
                (
                    f"{job_url.rstrip('/')}"
                    f"/{job_id}"
                ),
                stage="job status polling",
                job_id=job_id,
                timeout=timeout,
                deadline=deadline,
                headers=headers,
            )
        )

        try:
            status_json = (
                status_response.json()
            )

        except ValueError as exc:
            raise RuntimeError(
                "PaddleOCR returned "
                "invalid status JSON."
            ) from exc

        status = (
            status_json.get(
                "data"
            )
            if isinstance(
                status_json,
                dict,
            )
            else None
        )

        if not isinstance(
            status,
            dict,
        ):
            raise RuntimeError(
                "PaddleOCR returned "
                "an invalid job status."
            )

        state = status.get(
            "state"
        )

        if state == "done":
            result_urls = (
                status.get(
                    "resultUrl"
                )
                or {}
            )

            json_url = (
                result_urls.get(
                    "jsonUrl"
                )
                if isinstance(
                    result_urls,
                    dict,
                )
                else None
            )

            if not json_url:
                raise RuntimeError(
                    "PaddleOCR completed "
                    "without a result URL."
                )

            result_timeout = (
                _positive_number(
                    "PADDLEOCR_RESULT_TIMEOUT",
                    "300",
                )
            )

            # The result URL may belong to another provider.
            # Never forward the PaddleOCR Bearer token.
            result_response = (
                _get_with_retries(
                    json_url,
                    stage="result download",
                    job_id=job_id,
                    timeout=timeout,
                    deadline=(
                        time.monotonic()
                        + result_timeout
                    ),
                )
            )

            try:
                result_lines = [
                    json.loads(line)
                    for line in
                    result_response.text.splitlines()
                    if line.strip()
                ]

            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "PaddleOCR returned "
                    "malformed JSONL data."
                ) from exc

            pages, info = (
                _normalize_pages(
                    result_lines,
                    model,
                )
            )

            return (
                job_id,
                pages,
                info,
            )

        if state == "failed":
            raise PaddleOCRJobFailedError(
                f"PaddleOCR job {job_id} "
                "failed: "
                f"{status.get('errorMsg') or 'unknown error'}"
            )

        if state not in {
            "pending",
            "running",
        }:
            raise RuntimeError(
                "PaddleOCR returned "
                "an unknown job state: "
                f"{state!r}"
            )

        remaining = (
            deadline
            - time.monotonic()
        )

        if remaining <= 0:
            raise PaddleOCRTimeoutError(
                "Timed out waiting for "
                f"PaddleOCR job {job_id}."
            )

        time.sleep(
            min(
                interval,
                remaining,
            )
        )

        interval = min(
            interval * 1.5,
            15.0,
        )


def recognize_pdf(
    path: str,
    model: str,
) -> tuple[
    str,
    dict[str, Any],
]:
    """Recognize PDF with batching and conditional PaddleOCR fallback."""

    token = os.getenv(
        "PADDLEOCR_ACCESS_TOKEN"
    )

    if not token:
        raise RuntimeError(
            "Backend is missing "
            "PADDLEOCR_ACCESS_TOKEN."
        )

    job_url = os.getenv(
        "JOB_URL"
    )

    if not job_url:
        job_url = (
            os.getenv(
                "PADDLEOCR_BASE_URL",
                "https://paddleocr.aistudio-app.com",
            ).rstrip("/")
            + "/api/v2/ocr/jobs"
        )

    request_timeout = (
        _positive_number(
            "PADDLEOCR_REQUEST_TIMEOUT",
            "180",
        )
    )

    poll_timeout = (
        _positive_number(
            "PADDLEOCR_POLL_TIMEOUT",
            "300",
        )
    )

    max_pages = int(
        _positive_number(
            "PADDLEOCR_MAX_PAGES_PER_JOB",
            "2",
        )
    )

    max_bytes = int(
        _positive_number(
            "PADDLEOCR_MAX_UPLOAD_MB",
            "1.5",
        )
        * 1024
        * 1024
    )

    # This shorter connect timeout is used for GET only.
    # _process_batch selects a separate timeout for POST.
    timeout = (
        min(
            10.0,
            request_timeout,
        ),
        request_timeout,
    )

    headers = {
        "Authorization": (
            f"Bearer {token}"
        )
    }

    alternate_model = (
        os.getenv(
            "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL",
            "PP-OCRv6",
        ).strip()
    )

    if alternate_model not in {
        "",
        "PP-OCRv6",
    }:
        raise ValueError(
            "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL "
            "must be PP-OCRv6 or empty."
        )

    all_pages: list[
        dict[str, Any]
    ] = []

    jobs: list[
        dict[str, Any]
    ] = []

    data_info: dict[
        str,
        Any,
    ] = {}

    with tempfile.TemporaryDirectory(
        prefix="quizpdf-ocr-"
    ) as temporary:
        workdir = Path(
            temporary
        )

        source_path = Path(
            path
        )

        # PyMuPDF is used only for PDF preprocessing.
        # PaddleOCR performs text recognition.
        normalized_path, converted_pages = (
            _normalize_vector_pdf(
                source_path,
                workdir / "normalized.pdf",
            )
        )

        batches = _batches(
            normalized_path,
            workdir,
            max_pages=max_pages,
            max_bytes=max_bytes,
        )

        for index, batch in enumerate(
            batches,
            start=1,
        ):
            logger.info(
                "paddleocr:batch "
                "status=started "
                "index=%d total=%d "
                "pages=%d..%s "
                "size_bytes=%d model=%s",
                index,
                len(batches),
                batch.start_page + 1,
                (
                    batch.start_page
                    + batch.page_count
                    if batch.page_count is not None
                    else "unknown"
                ),
                batch.path.stat().st_size,
                model,
            )

            used_fallback = False

            try:
                job_id, pages, info = (
                    _process_batch(
                        batch,
                        model=model,
                        job_url=job_url,
                        headers=headers,
                        timeout=timeout,
                        poll_timeout=poll_timeout,
                    )
                )

                job_history = [{
                    "job_id": job_id,
                    "model": model,
                }]

            except PaddleOCRJobFailedError:
                if (
                    model != "PaddleOCR-VL-1.6"
                    or not alternate_model
                ):
                    raise

                logger.warning(
                    "paddleocr:batch "
                    "primary_failed "
                    "index=%d "
                    "fallback_model=%s",
                    index,
                    alternate_model,
                )

                job_id, pages, info = (
                    _process_batch(
                        batch,
                        model=alternate_model,
                        job_url=job_url,
                        headers=headers,
                        timeout=timeout,
                        poll_timeout=poll_timeout,
                    )
                )

                used_fallback = True

                job_history = [{
                    "job_id": job_id,
                    "model": alternate_model,
                }]

            if (
                batch.page_count is not None
                and len(pages)
                != batch.page_count
            ):
                if len(batches) > 1:
                    raise RuntimeError(
                        "PaddleOCR returned a "
                        "different page count "
                        f"for batch {index}: "
                        f"expected {batch.page_count}, "
                        f"got {len(pages)}."
                    )

                logger.warning(
                    "paddleocr:single_batch_page_mismatch "
                    "expected=%d got=%d",
                    batch.page_count,
                    len(pages),
                )

            missing = [
                offset
                for offset, page
                in enumerate(pages)
                if not _text_from(
                    page
                )
            ]

            missing_vector_page = any(
                (
                    batch.start_page
                    + offset
                    + 1
                    in converted_pages
                )
                for offset in missing
            )

            should_fallback = (
                bool(missing)
                and not used_fallback
                and model == "PaddleOCR-VL-1.6"
                and bool(alternate_model)
                and (
                    len(missing) == len(pages)
                    or missing_vector_page
                )
            )

            if should_fallback:
                logger.warning(
                    "paddleocr:batch "
                    "empty_text=%d/%d "
                    "index=%d "
                    "fallback_model=%s",
                    len(missing),
                    len(pages),
                    index,
                    alternate_model,
                )

                other_job, other_pages, other_info = (
                    _process_batch(
                        batch,
                        model=alternate_model,
                        job_url=job_url,
                        headers=headers,
                        timeout=timeout,
                        poll_timeout=poll_timeout,
                    )
                )

                if len(other_pages) != len(pages):
                    raise RuntimeError(
                        "Fallback OCR returned "
                        "mismatched page count."
                    )

                for offset in missing:
                    if _text_from(
                        other_pages[offset]
                    ):
                        pages[offset] = (
                            other_pages[offset]
                        )

                info.update(
                    other_info
                )

                job_history.append({
                    "job_id": other_job,
                    "model": alternate_model,
                })

            empty_after = [
                offset
                + batch.start_page
                + 1
                for offset, page
                in enumerate(pages)
                if not _text_from(page)
            ]

            if empty_after:
                logger.warning(
                    "paddleocr:batch "
                    "empty_pages=%s",
                    empty_after,
                )

            if len(batches) > 1:
                for offset, page in enumerate(
                    pages
                ):
                    page["source_page_number"] = (
                        batch.start_page
                        + offset
                        + 1
                    )

            all_pages.extend(
                pages
            )

            data_info.update(
                info
            )

            jobs.append({
                "first_page": (
                    batch.start_page + 1
                ),
                "page_count": (
                    batch.page_count
                ),
                "jobs": job_history,
            })

            logger.info(
                "paddleocr:batch "
                "status=completed "
                "index=%d total=%d "
                "recognized_pages=%d/%d",
                index,
                len(batches),
                (
                    len(pages)
                    - len(empty_after)
                ),
                len(pages),
            )

    raw: dict[str, Any] = {
        "job_id": (
            jobs[0]["jobs"][0]["job_id"]
        ),
        "data_info": data_info,
        "pages": all_pages,
    }

    if len(jobs) > 1:
        raw["job_ids"] = [
            item["jobs"][-1]["job_id"]
            for item in jobs
        ]

        raw["batches"] = jobs

    elif (
        jobs
        and len(jobs[0]["jobs"]) > 1
    ):
        raw["batches"] = jobs

    if converted_pages:
        raw["rasterized_pages"] = (
            converted_pages
        )

    page_texts = (
        extract_page_texts(raw)
    )

    total_characters = sum(
        len(text.strip())
        for text in page_texts
    )

    nonempty_pages = sum(
        bool(text.strip())
        for text in page_texts
    )

    logger.info(
        "paddleocr:text_summary "
        "pages=%d nonempty=%d "
        "characters=%d rasterized=%d",
        len(page_texts),
        nonempty_pages,
        total_characters,
        len(converted_pages),
    )

    if total_characters == 0:
        raise PaddleOCRNoTextError(
            "PaddleOCR completed but "
            "returned no recognizable text. "
            "Check the OCR result format "
            "and PDF quality."
        )

    return (
        "\n\n".join(page_texts),
        raw,
    )
