"""Cloud OCR with bounded recovery and geometry-preserving VL requests."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import requests

from . import paddle_client as client

logger = logging.getLogger("quizpdf")


def _optional_payload(model: str) -> dict[str, bool]:
    if model == "PaddleOCR-VL-1.6":
        return {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
        }
    return {}


def _log_geometry(pages: list[dict], job_id: str) -> None:
    for index, page in enumerate(pages, 1):
        root = page.get("pruned_result")
        root = root if isinstance(root, dict) else {}

        pre = root.get("doc_preprocessor_res")
        pre = pre if isinstance(pre, dict) else {}

        settings = pre.get("model_settings")
        settings = settings if isinstance(settings, dict) else {}

        blocks = root.get("parsing_res_list")

        root_settings = root.get("model_settings")
        root_settings = (
            root_settings
            if isinstance(root_settings, dict)
            else {}
        )

        logger.info(
            "paddleocr:geometry job=%s page=%d width=%s height=%s "
            "preprocessor=%s angle=%s unwarping=%s orientation=%s blocks=%d",
            job_id,
            index,
            root.get("width"),
            root.get("height"),
            root_settings.get("use_doc_preprocessor"),
            pre.get("angle"),
            settings.get("use_doc_unwarping"),
            settings.get("use_doc_orientation_classify"),
            len(blocks) if isinstance(blocks, list) else 0,
        )


def _recoverable(error: Exception) -> bool:
    if isinstance(error, client.PaddleOCRTimeoutError):
        return True

    if isinstance(error, client.PaddleOCRAPIError):
        return error.status_code in client.RETRYABLE_GET_STATUSES

    cause = error.__cause__

    return isinstance(
        cause, requests.exceptions.ConnectionError
    ) and not isinstance(
        cause, requests.exceptions.SSLError
    )


def _with_recovery(
    operation: Callable[[float], Any],
    *,
    job_id: str,
    stage: str,
    seconds: float,
    recovery: float,
) -> Any:
    try:
        return operation(time.monotonic() + seconds)

    except Exception as exc:
        if not _recoverable(exc):
            raise

        logger.warning(
            "paddleocr:recover job=%s stage=%s "
            "extra_seconds=%.1f error_type=%s",
            job_id,
            stage,
            recovery,
            type(exc).__name__,
        )

    return operation(time.monotonic() + recovery)


def _poll(
    job_url,
    job_id,
    headers,
    timeout,
    deadline,
):
    interval = 3.0

    while True:
        response = client._get_with_retries(
            f"{job_url.rstrip('/')}/{job_id}",
            stage="job status polling",
            job_id=job_id,
            headers=headers,
            timeout=timeout,
            deadline=deadline,
        )

        data = response.json()

        status = (
            data.get("data")
            if isinstance(data, dict)
            else None
        )

        if not isinstance(status, dict):
            raise RuntimeError(
                "PaddleOCR returned invalid job status."
            )

        state = status.get("state")

        if state == "done":
            urls = status.get("resultUrl")

            url = (
                urls.get("jsonUrl")
                if isinstance(urls, dict)
                else None
            )

            if not isinstance(url, str) or not url:
                raise RuntimeError(
                    "PaddleOCR completed without a result URL."
                )

            return url

        if state == "failed":
            raise client.PaddleOCRJobFailedError(
                f"PaddleOCR job {job_id} failed."
            )

        if state not in {"pending", "running"}:
            raise RuntimeError(
                f"Unknown PaddleOCR job state: {state!r}"
            )

        remaining = deadline - time.monotonic()

        if remaining <= 0:
            raise client.PaddleOCRTimeoutError(
                f"Timed out waiting for PaddleOCR job {job_id}."
            )

        time.sleep(min(interval, remaining))

        interval = min(interval * 1.5, 15.0)


def _process_batch(
    batch,
    *,
    model,
    job_url,
    headers,
    timeout,
    poll_timeout,
    recovery,
    result_timeout,
):
    payload = _optional_payload(model)

    logger.info(
        "paddleocr:submit_options model=%s options=%s",
        model,
        json.dumps(payload),
    )

    # Submit exactly once; POST timeouts must not
    # cause duplicate OCR jobs.
    try:
        with batch.path.open("rb") as pdf:
            response = requests.post(
                job_url,
                headers=headers,
                data={
                    "model": model,
                    "optionalPayload": json.dumps(payload),
                },
                files={
                    "file": (
                        batch.path.name,
                        pdf,
                        "application/pdf",
                    ),
                },
                timeout=client._upload_timeout(
                    timeout[1]
                ),
            )

    except (
        requests.RequestException,
        TimeoutError,
    ) as exc:
        client._request_error(
            exc,
            "PDF upload",
        )

    client._check_response(
        response,
        "submit",
    )

    data = response.json()

    result = (
        data.get("data")
        if isinstance(data, dict)
        else None
    )

    job_id = (
        result.get("jobId")
        if isinstance(result, dict)
        else None
    )

    if not job_id:
        raise RuntimeError(
            "PaddleOCR did not return a job ID."
        )

    job_id = str(job_id)

    logger.info(
        "paddleocr:upload status=completed job=%s first_page=%d",
        job_id,
        batch.start_page + 1,
    )

    status_timeout = (
        timeout[0],
        min(timeout[1], 30.0),
    )

    url = _with_recovery(
        lambda deadline: _poll(
            job_url,
            job_id,
            headers,
            status_timeout,
            deadline,
        ),
        job_id=job_id,
        stage="poll",
        seconds=poll_timeout,
        recovery=recovery,
    )

    # Never send the provider's Bearer token
    # to the result URL.
    response = _with_recovery(
        lambda deadline: client._get_with_retries(
            url,
            stage="result download",
            job_id=job_id,
            timeout=timeout,
            deadline=deadline,
        ),
        job_id=job_id,
        stage="result",
        seconds=result_timeout,
        recovery=recovery,
    )

    lines = [
        json.loads(line)
        for line in response.text.splitlines()
        if line.strip()
    ]

    pages, info = client._normalize_pages(
        lines,
        model,
    )

    # Log actual metadata. Never replace server geometry
    # flags with requested values.
    _log_geometry(
        pages,
        job_id,
    )

    return job_id, pages, info


def recognize_pdf(
    path: str,
    model: str,
) -> tuple[str, dict[str, Any]]:
    token = os.getenv("PADDLEOCR_ACCESS_TOKEN")

    if not token:
        raise RuntimeError(
            "Backend is missing PADDLEOCR_ACCESS_TOKEN."
        )

    job_url = os.getenv("JOB_URL") or (
        os.getenv(
            "PADDLEOCR_BASE_URL",
            "https://paddleocr.aistudio-app.com",
        ).rstrip("/")
        + "/api/v2/ocr/jobs"
    )

    request_timeout = client._positive_number(
        "PADDLEOCR_REQUEST_TIMEOUT",
        "180",
    )

    poll_timeout = client._positive_number(
        "PADDLEOCR_POLL_TIMEOUT",
        "300",
    )

    result_timeout = client._positive_number(
        "PADDLEOCR_RESULT_TIMEOUT",
        "300",
    )

    recovery = client._positive_number(
        "PADDLEOCR_RECOVERY_TIMEOUT",
        "120",
    )

    if recovery > 300:
        raise ValueError(
            "PADDLEOCR_RECOVERY_TIMEOUT must not "
            "exceed 300 seconds."
        )

    max_pages = int(
        client._positive_number(
            "PADDLEOCR_MAX_PAGES_PER_JOB",
            "2",
        )
    )

    max_bytes = int(
        client._positive_number(
            "PADDLEOCR_MAX_UPLOAD_MB",
            "1.5",
        )
        * 1024
        * 1024
    )

    if max_pages < 1 or max_bytes < 1:
        raise ValueError(
            "Invalid OCR batch size."
        )

    alternate = os.getenv(
        "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL",
        "PP-OCRv6",
    ).strip()

    if alternate not in {"", "PP-OCRv6"}:
        raise ValueError(
            "PADDLEOCR_EMPTY_TEXT_FALLBACK_MODEL "
            "must be PP-OCRv6 or empty."
        )

    headers = {
        "Authorization": f"Bearer {token}",
    }

    timeout = (
        min(10.0, request_timeout),
        request_timeout,
    )

    all_pages = []
    jobs = []
    data_info = {}

    with tempfile.TemporaryDirectory(
        prefix="quizpdf-ocr-"
    ) as directory:
        workdir = Path(directory)

        normalized, converted = (
            client._normalize_vector_pdf(
                Path(path),
                workdir / "normalized.pdf",
            )
        )

        batches = client._batches(
            normalized,
            workdir,
            max_pages=max_pages,
            max_bytes=max_bytes,
        )

        for index, batch in enumerate(
            batches,
            1,
        ):
            logger.info(
                "paddleocr:batch status=started "
                "index=%d total=%d first_page=%d",
                index,
                len(batches),
                batch.start_page + 1,
            )

            def run(selected_model):
                return _process_batch(
                    batch,
                    model=selected_model,
                    job_url=job_url,
                    headers=headers,
                    timeout=timeout,
                    poll_timeout=poll_timeout,
                    recovery=recovery,
                    result_timeout=result_timeout,
                )

            def check_count(pages):
                if (
                    batch.page_count is not None
                    and len(pages) != batch.page_count
                ):
                    raise RuntimeError(
                        f"OCR page mismatch for batch {index}: "
                        f"expected {batch.page_count}, "
                        f"got {len(pages)}."
                    )

            used_fallback = False

            try:
                job_id, pages, info = run(model)

                history = [
                    {
                        "job_id": job_id,
                        "model": model,
                    }
                ]

            except client.PaddleOCRJobFailedError:
                if (
                    model != "PaddleOCR-VL-1.6"
                    or not alternate
                ):
                    raise

                job_id, pages, info = run(
                    alternate
                )

                used_fallback = True

                history = [
                    {
                        "job_id": job_id,
                        "model": alternate,
                    }
                ]

            check_count(pages)

            missing = [
                offset
                for offset, page in enumerate(pages)
                if not client._text_from(page)
            ]

            missing_vector = any(
                batch.start_page + offset + 1
                in converted
                for offset in missing
            )

            if (
                missing
                and not used_fallback
                and model == "PaddleOCR-VL-1.6"
                and alternate
                and (
                    len(missing) == len(pages)
                    or missing_vector
                )
            ):
                other_job, other_pages, other_info = (
                    run(alternate)
                )

                check_count(other_pages)

                if len(other_pages) != len(pages):
                    raise RuntimeError(
                        "Fallback OCR returned "
                        "mismatched page count."
                    )

                for offset in missing:
                    if client._text_from(
                        other_pages[offset]
                    ):
                        pages[offset] = (
                            other_pages[offset]
                        )

                info.update(other_info)

                history.append({
                    "job_id": other_job,
                    "model": alternate,
                })

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
                "jobs": history,
            })

            logger.info(
                "paddleocr:batch status=completed "
                "index=%d total=%d",
                index,
                len(batches),
            )

    raw = {
        "job_id": jobs[0]["jobs"][0]["job_id"],
        "data_info": data_info,
        "pages": all_pages,
    }

    if len(jobs) > 1:
        raw["job_ids"] = [
            item["jobs"][-1]["job_id"]
            for item in jobs
        ]

        raw["batches"] = jobs

    elif len(jobs[0]["jobs"]) > 1:
        raw["batches"] = jobs

    if converted:
        raw["rasterized_pages"] = converted

    texts = client.extract_page_texts(raw)

    if not any(
        text.strip()
        for text in texts
    ):
        raise client.PaddleOCRNoTextError(
            "PaddleOCR returned no recognizable text."
        )

    return "\n\n".join(texts), raw