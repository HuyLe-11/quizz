"""PaddleOCR hosted adapter with selective vector-PDF rasterization."""

import json
import logging
import math
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import pymupdf
import requests

logger = logging.getLogger("quizpdf")


class PaddleOCRAPIError(RuntimeError):
    def __init__(self, stage: str, status_code: int):
        self.stage = stage
        self.status_code = status_code
        super().__init__(
            f"PaddleOCR {stage} request returned HTTP {status_code}."
        )


class PaddleOCRTimeoutError(TimeoutError):
    pass


class PaddleOCRNoTextError(RuntimeError):
    pass


class PaddleOCRJobFailedError(RuntimeError):
    """The provider explicitly reported a terminal failed job state."""


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
    """Extract one page's OCR text without duplicating representations."""

    if not isinstance(value, dict):
        return ""

    for key in ("markdown_text", "markdownText"):
        if (
            isinstance(value.get(key), str)
            and value[key].strip()
        ):
            return value[key].strip()

    markdown = value.get("markdown")

    if (
        isinstance(markdown, str)
        and markdown.strip()
    ):
        return markdown.strip()

    if isinstance(markdown, dict):
        text = markdown.get("text")

        if isinstance(text, str) and text.strip():
            return text.strip()

    blocks = value.get("parsing_res_list")

    if isinstance(blocks, list):
        text = "\n".join(
            block.get("block_content", "").strip()
            for block in blocks
            if (
                isinstance(block, dict)
                and isinstance(
                    block.get("block_content"),
                    str,
                )
                and block["block_content"].strip()
            )
        )

        if text:
            return text

    for key in ("rec_texts", "texts"):
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
        text = value.get(key)

        if isinstance(text, str) and text.strip():
            return text.strip()

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
    """Extract texts while retaining positions of empty OCR pages."""

    if not isinstance(raw_response, dict):
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

    nested = raw_response.get("result")

    if isinstance(nested, dict):
        pages = extract_page_texts(nested)

        if pages:
            return pages

    text = _text_from(raw_response)

    return [text] if text else []


def _normalize_pages(
    result_lines: list[Any],
    model: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize provider JSONL and preserve OCR geometry."""

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
            if isinstance(line, dict)
            else None
        )

        if not isinstance(result, dict):
            raise RuntimeError(
                "PaddleOCR returned an invalid result object."
            )

        info = result.get("dataInfo")

        if isinstance(info, dict):
            data_info.update(info)

        items = result.get(result_key)

        if not isinstance(items, list):
            raise RuntimeError(
                f"PaddleOCR result is missing {result_key}."
            )

        for item in items:
            if not isinstance(item, dict):
                pages.append({"raw": item})
                continue

            page: dict[str, Any] = {
                "pruned_result": item.get("prunedResult"),
                "input_image_url": item.get("inputImage"),
                "raw": item,
            }

            if model == "PP-OCRv6":
                page["ocr_image_url"] = item.get(
                    "ocrImage"
                )

            else:
                markdown = item.get("markdown")

                if not isinstance(markdown, dict):
                    markdown = {
                        "text": (
                            markdown
                            if isinstance(markdown, str)
                            else ""
                        )
                    }

                page.update({
                    "markdown_text": (
                        markdown.get("text") or ""
                    ),
                    "markdown_images": markdown.get(
                        "images",
                        {},
                    ),
                    "output_images": item.get(
                        "outputImages",
                        {},
                    ),
                    "exports": item.get(
                        "exports",
                        {},
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
            os.getenv(name, default)
        )

    except ValueError as exc:
        raise ValueError(
            f"Invalid configuration value for {name}."
        ) from exc

    if (
        not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(
            f"{name} must be a positive number."
        )

    return value


def _nested_timeout(
    error: BaseException,
) -> bool:
    """Detect SSL write timeouts wrapped in requests.ConnectionError."""

    pending: list[Any] = [error]
    visited: set[int] = set()

    while pending:
        current = pending.pop()

        if id(current) in visited:
            continue

        visited.add(id(current))

        if isinstance(
            current,
            (
                requests.exceptions.Timeout,
                TimeoutError,
            ),
        ):
            return True

        if isinstance(current, BaseException):
            pending.extend([
                current.__cause__,
                current.__context__,
                *current.args,
            ])

        elif isinstance(current, (tuple, list)):
            pending.extend(current)

    return False


def _request_error(
    exc: BaseException,
    stage: str,
) -> NoReturn:
    if _nested_timeout(exc):
        raise PaddleOCRTimeoutError(
            f"PaddleOCR timed out during {stage}."
        ) from exc

    raise RuntimeError(
        f"Could not connect to PaddleOCR during {stage}."
    ) from exc


@dataclass(frozen=True)
class PDFBatch:
    path: Path
    start_page: int
    page_count: int | None


def _requires_raster(
    page: pymupdf.Page,
    min_drawings: int = 120,
) -> bool:
    """Detect vector-outlined pages lacking a normal text layer."""

    text = page.get_text("text").strip()

    return (
        len(text) < 30
        and len(page.get_drawings()) >= min_drawings
    )


def _normalize_vector_pdf(
    source: Path,
    output: Path,
) -> tuple[Path, list[int]]:
    """Rasterize vector-heavy pages without changing PDF page sizes."""

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
            "PADDLEOCR_VECTOR_DPI must be between 100 and 300."
        )

    if not 40 <= quality <= 95:
        raise ValueError(
            "PADDLEOCR_VECTOR_JPEG_QUALITY must be between 40 and 95."
        )

    try:
        document = pymupdf.open(source)

    except (
        pymupdf.FileDataError,
        ValueError,
        RuntimeError,
    ):
        logger.warning(
            "paddleocr:preprocess "
            "unable_to_open_pdf; using original"
        )

        return source, []

    with document:
        selected = [
            page.number
            for page in document
            if _requires_raster(page)
        ]

        if not selected:
            logger.info(
                "paddleocr:preprocess "
                "mode=original pages=%d",
                len(document),
            )

            return source, []

        selected_set = set(selected)

        with pymupdf.open() as normalized:
            for index, page in enumerate(document):
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

                jpeg_bytes = pix.tobytes(
                    "jpeg",
                    jpg_quality=quality,
                )

                output_page = normalized.new_page(
                    width=page.rect.width,
                    height=page.rect.height,
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

        return output, [
            index + 1
            for index in selected
        ]


def _batches(
    path: Path,
    output_dir: Path,
    *,
    max_pages: int,
    max_bytes: int,
) -> list[PDFBatch]:
    """Split a PDF by page count and approximate upload bytes."""

    try:
        document = pymupdf.open(path)

    except (
        pymupdf.FileDataError,
        ValueError,
        RuntimeError,
    ):
        return [
            PDFBatch(path, 0, None)
        ]

    with document:
        count = len(document)

        if count == 0:
            raise ValueError(
                "Empty PDF cannot be recognized."
            )

        if (
            count <= max_pages
            and path.stat().st_size <= max_bytes
        ):
            return [
                PDFBatch(path, 0, count)
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

                    candidate = part.tobytes(
                        garbage=4,
                        deflate=True,
                    )

                if (
                    len(candidate) > max_bytes
                    and selected_bytes is not None
                ):
                    break

                selected_end = end
                selected_bytes = candidate

                if len(candidate) > max_bytes:
                    break

            if selected_bytes is None:
                raise RuntimeError(
                    "Could not create an OCR PDF batch."
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
                    page_count=selected_end - start,
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
    """Submit one job, poll status and download normalized OCR results."""

    try:
        with batch.path.open("rb") as pdf_file:
            response = requests.post(
                job_url,
                headers=headers,
                data={
                    "model": model,
                    "optionalPayload": json.dumps({}),
                },
                files={
                    "file": (
                        batch.path.name,
                        pdf_file,
                        "application/pdf",
                    ),
                },
                timeout=timeout,
            )

    except (
        requests.RequestException,
        TimeoutError,
    ) as exc:
        # Never retry an ambiguous POST automatically.
        _request_error(
            exc,
            "PDF upload",
        )

    _check_response(
        response,
        "submit",
    )

    try:
        submission = response.json()

    except ValueError as exc:
        raise RuntimeError(
            "PaddleOCR returned invalid submit JSON."
        ) from exc

    payload = (
        submission.get("data")
        if isinstance(submission, dict)
        else None
    )

    job_id = (
        payload.get("jobId")
        if isinstance(payload, dict)
        else None
    )

    if not job_id:
        raise RuntimeError(
            "PaddleOCR API response did not include a job ID."
        )

    deadline = (
        time.monotonic() + poll_timeout
    )

    interval = 3.0

    while True:
        remaining = (
            deadline - time.monotonic()
        )

        if remaining <= 0:
            raise PaddleOCRTimeoutError(
                f"Timed out waiting for PaddleOCR job {job_id}."
            )

        try:
            status_response = requests.get(
                f"{job_url.rstrip('/')}/{job_id}",
                headers=headers,
                timeout=(
                    timeout[0],
                    min(
                        timeout[1],
                        max(1.0, remaining),
                    ),
                ),
            )

        except (
            requests.RequestException,
            TimeoutError,
        ) as exc:
            _request_error(
                exc,
                "job status polling",
            )

        _check_response(
            status_response,
            "status",
        )

        try:
            status_json = status_response.json()

        except ValueError as exc:
            raise RuntimeError(
                "PaddleOCR returned invalid status JSON."
            ) from exc

        status = (
            status_json.get("data")
            if isinstance(status_json, dict)
            else None
        )

        if not isinstance(status, dict):
            raise RuntimeError(
                "PaddleOCR returned an invalid job status."
            )

        state = status.get("state")

        if state == "done":
            result_urls = (
                status.get("resultUrl") or {}
            )

            json_url = (
                result_urls.get("jsonUrl")
                if isinstance(result_urls, dict)
                else None
            )

            if not json_url:
                raise RuntimeError(
                    "PaddleOCR completed without a result URL."
                )

            try:
                # Do not send Bearer token to the result URL.
                result_response = requests.get(
                    json_url,
                    timeout=timeout,
                )

            except (
                requests.RequestException,
                TimeoutError,
            ) as exc:
                _request_error(
                    exc,
                    "result download",
                )

            _check_response(
                result_response,
                "result download",
            )

            try:
                result_lines = [
                    json.loads(line)
                    for line in result_response.text.splitlines()
                    if line.strip()
                ]

            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "PaddleOCR returned malformed JSONL data."
                ) from exc

            pages, info = _normalize_pages(
                result_lines,
                model,
            )

            return str(job_id), pages, info

        if state == "failed":
            raise PaddleOCRJobFailedError(
                f"PaddleOCR job {job_id} failed: "
                f"{status.get('errorMsg') or 'unknown error'}"
            )

        if state not in {
            "pending",
            "running",
        }:
            raise RuntimeError(
                "PaddleOCR returned an unknown "
                f"job state: {state!r}"
            )

        remaining = deadline - time.monotonic()

        if remaining <= 0:
            raise PaddleOCRTimeoutError(
                f"Timed out waiting for PaddleOCR job {job_id}."
            )

        time.sleep(
            min(interval, remaining)
        )

        interval = min(
            interval * 1.5,
            15.0,
        )


def recognize_pdf(
    path: str,
    model: str,
) -> tuple[str, dict[str, Any]]:
    """Recognize source PDF with batching and conditional model fallback."""

    token = os.getenv(
        "PADDLEOCR_ACCESS_TOKEN"
    )

    if not token:
        raise RuntimeError(
            "Backend is missing PADDLEOCR_ACCESS_TOKEN."
        )

    job_url = os.getenv("JOB_URL")

    if not job_url:
        job_url = (
            os.getenv(
                "PADDLEOCR_BASE_URL",
                "https://paddleocr.aistudio-app.com",
            ).rstrip("/")
            + "/api/v2/ocr/jobs"
        )

    request_timeout = _positive_number(
        "PADDLEOCR_REQUEST_TIMEOUT",
        "180",
    )

    poll_timeout = _positive_number(
        "PADDLEOCR_POLL_TIMEOUT",
        "300",
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
        ) * 1024 * 1024
    )

    timeout = (
        min(10.0, request_timeout),
        request_timeout,
    )

    headers = {
        "Authorization": f"Bearer {token}"
    }

    alternate_model = os.getenv(
        "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL",
        "PP-OCRv6",
    ).strip()

    if alternate_model not in {
        "",
        "PP-OCRv6",
    }:
        raise ValueError(
            "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL "
            "must be PP-OCRv6 or empty."
        )

    all_pages: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    data_info: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(
        prefix="quizpdf-ocr-"
    ) as temporary:
        workdir = Path(temporary)
        source_path = Path(path)

        # Normalize vector-heavy PDF pages.
        normalized_path, converted_pages = (
            _normalize_vector_pdf(
                source_path,
                workdir / "normalized.pdf",
            )
        )

        # Split into smaller PDF jobs.
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
                "paddleocr:batch status=started "
                "index=%d total=%d pages=%d..%s "
                "size_bytes=%d model=%s",
                index,
                len(batches),
                batch.start_page + 1,
                (
                    batch.start_page + batch.page_count
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
                    "primary_failed index=%d "
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

            # Preserve old single-batch behavior.
            # For multiple batches, enforce exact page mapping.
            if (
                batch.page_count is not None
                and len(pages) != batch.page_count
            ):
                if len(batches) > 1:
                    raise RuntimeError(
                        "PaddleOCR returned a different "
                        f"page count for batch {index}: "
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
                for offset, page in enumerate(pages)
                if not _text_from(page)
            ]

            # Empty pages can be intentional.
            # Only invoke fallback when:
            # - the entire batch is empty, OR
            # - an empty page was rasterized from vector content.
            missing_vector_page = any(
                (
                    batch.start_page + offset + 1
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
                    "empty_text=%d/%d index=%d "
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
                    if _text_from(other_pages[offset]):
                        pages[offset] = other_pages[offset]

                info.update(other_info)

                job_history.append({
                    "job_id": other_job,
                    "model": alternate_model,
                })

            empty_after = [
                offset + batch.start_page + 1
                for offset, page in enumerate(pages)
                if not _text_from(page)
            ]

            if empty_after:
                logger.warning(
                    "paddleocr:batch empty_pages=%s",
                    empty_after,
                )

            # The multi-batch parser needs global page numbers.
            # Keep single-batch output backwards compatible.
            if len(batches) > 1:
                for offset, page in enumerate(pages):
                    page["source_page_number"] = (
                        batch.start_page + offset + 1
                    )

            all_pages.extend(pages)
            data_info.update(info)

            jobs.append({
                "first_page": batch.start_page + 1,
                "page_count": batch.page_count,
                "jobs": job_history,
            })

            logger.info(
                "paddleocr:batch status=completed "
                "index=%d total=%d "
                "recognized_pages=%d/%d",
                index,
                len(batches),
                len(pages) - len(empty_after),
                len(pages),
            )

    # Preserve existing API contract.
    raw: dict[str, Any] = {
        "job_id": jobs[0]["jobs"][0]["job_id"],
        "data_info": data_info,
        "pages": all_pages,
    }

    # Keep metadata expected by previous multi-batch tests.
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
        raw["rasterized_pages"] = converted_pages

    page_texts = extract_page_texts(raw)

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
            "PaddleOCR completed but returned "
            "no recognizable text. "
            "Check the OCR result format and PDF quality."
        )

    return (
        "\n\n".join(page_texts),
        raw,
    )
