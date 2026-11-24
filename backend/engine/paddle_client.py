"""PaddleOCR hosted API adapter. Keep credentials on the backend."""
from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any

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


def _timeout(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()

    try:
        value = float(raw) if raw else default
    except ValueError as exc:
        raise ValueError(
            f"{name} must be a positive finite number."
        ) from exc

    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"{name} must be a positive finite number."
        )

    return value


def _is_timeout(exc: BaseException) -> bool:
    pending = [exc]
    visited = set()

    while pending:
        error = pending.pop()

        if id(error) in visited:
            continue

        visited.add(id(error))

        if isinstance(
            error,
            (TimeoutError, requests.exceptions.Timeout),
        ):
            return True

        pending.extend(
            x for x in (
                error.__cause__,
                error.__context__,
                *error.args,
            )
            if isinstance(x, BaseException)
        )

    return "timed out" in str(exc).lower()


def _check_response(
    response: requests.Response,
    stage: str,
) -> None:
    if not response.ok:
        raise PaddleOCRAPIError(
            stage,
            response.status_code,
        )


def _get(
    url: str,
    timeout: float,
    stage: str,
    **kwargs,
) -> requests.Response:
    try:
        response = requests.get(
            url,
            timeout=timeout,
            **kwargs,
        )
    except requests.RequestException as exc:
        if _is_timeout(exc):
            raise PaddleOCRTimeoutError(
                f"Timed out during PaddleOCR {stage}."
            ) from exc

        raise RuntimeError(
            f"Could not connect to PaddleOCR during {stage}."
        ) from exc

    _check_response(response, stage)

    return response


def _text_from(value: Any) -> list[str]:
    """Prefer a single text representation instead of duplicating OCR data."""
    if isinstance(value, list):
        return [
            text
            for item in value
            for text in _text_from(item)
        ]

    if not isinstance(value, dict):
        return []

    for key in ("markdown_text", "markdownText"):
        text = value.get(key)

        if isinstance(text, str) and text.strip():
            return [text]

    markdown = value.get("markdown")

    if isinstance(markdown, dict):
        text = markdown.get("text")

        if isinstance(text, str) and text.strip():
            return [text]

    for key in (
        "rec_texts",
        "rec_text",
        "block_content",
        "text",
    ):
        text = value.get(key)

        if isinstance(text, str) and text.strip():
            return [text]

        if isinstance(text, list):
            strings = [
                part
                for part in text
                if isinstance(part, str) and part.strip()
            ]

            if strings:
                return strings

    for key in (
        "parsing_res_list",
        "pruned_result",
        "prunedResult",
        "pages",
        "layout_parsing_results",
        "layoutParsingResults",
        "ocr_results",
        "ocrResults",
        "overall_ocr_res",
        "overallOcrRes",
        "result",
        "raw",
    ):
        text = _text_from(value.get(key))

        if text:
            return text

    return []


def extract_page_texts(
    raw_response: dict[str, Any],
) -> list[str]:
    pages = raw_response.get("pages")

    if isinstance(pages, list):
        # Empty or malformed pages must not shift
        # subsequent source page numbers.
        return [
            "\n".join(_text_from(page))
            if isinstance(page, dict)
            else ""
            for page in pages
        ]

    text = "\n".join(_text_from(raw_response))

    return [text] if text else []


def recognize_pdf(
    path: str,
    model: str,
) -> tuple[str, dict[str, Any]]:
    token = os.getenv("PADDLEOCR_ACCESS_TOKEN")

    if not token:
        raise RuntimeError(
            "Backend is missing PADDLEOCR_ACCESS_TOKEN."
        )

    base = os.getenv(
        "PADDLEOCR_BASE_URL",
        "https://paddleocr.aistudio-app.com",
    ).rstrip("/")

    job_url = (
        os.getenv("JOB_URL")
        or f"{base}/api/v2/ocr/jobs"
    )

    submit_timeout = _timeout(
        "PADDLEOCR_SUBMIT_TIMEOUT",
        90.0,
    )

    request_timeout = _timeout(
        "PADDLEOCR_REQUEST_TIMEOUT",
        20.0,
    )

    poll_timeout = _timeout(
        "PADDLEOCR_POLL_TIMEOUT",
        75.0,
    )

    headers = {
        "Authorization": f"Bearer {token}"
    }

    started = time.monotonic()
    filename = Path(path).name
    size = Path(path).stat().st_size

    logger.info(
        "ocr=submit status=started bytes=%d timeout=%.1f",
        size,
        submit_timeout,
    )

    try:
        with open(path, "rb") as pdf_file:
            response = requests.post(
                job_url,
                headers=headers,
                data={
                    "model": model,
                    "optionalPayload": json.dumps({}),
                },
                files={
                    "file": (
                        filename,
                        pdf_file,
                        "application/pdf",
                    )
                },
                timeout=submit_timeout,
            )

    except requests.RequestException as exc:
        if _is_timeout(exc):
            logger.warning(
                "ocr=submit status=timeout bytes=%d elapsed=%.2f",
                size,
                time.monotonic() - started,
            )

            # The provider may already have created a job.
            # Never retry POST blindly.
            raise PaddleOCRTimeoutError(
                "Timed out uploading PDF to PaddleOCR; "
                "submission unconfirmed."
            ) from exc

        raise RuntimeError(
            "Could not connect to PaddleOCR API "
            "while submitting PDF."
        ) from exc

    _check_response(response, "submit")

    submitted = response.json()

    data = (
        submitted.get("data")
        if isinstance(submitted, dict)
        else None
    )

    job_id = (
        data.get("jobId")
        if isinstance(data, dict)
        else None
    )

    if not job_id:
        raise RuntimeError(
            "PaddleOCR API response did not include a job ID."
        )

    logger.info(
        "ocr=submit status=completed elapsed=%.2f",
        time.monotonic() - started,
    )

    deadline = time.monotonic() + poll_timeout
    interval = 3.0
    last_state = None

    while True:
        remaining = deadline - time.monotonic()

        if remaining <= 0:
            raise PaddleOCRTimeoutError(
                "Timed out waiting for PaddleOCR job."
            )

        response = _get(
            f"{job_url.rstrip('/')}/{job_id}",
            min(request_timeout, remaining),
            "status",
            headers=headers,
        )

        payload = response.json()

        status = (
            payload.get("data")
            if isinstance(payload, dict)
            else None
        )

        if not isinstance(status, dict):
            raise RuntimeError(
                "PaddleOCR API returned an invalid job status."
            )

        state = status.get("state")

        if state != last_state:
            logger.info("ocr=poll state=%s", state)
            last_state = state

        if state == "done":
            result_url = status.get("resultUrl")

            json_url = (
                result_url.get("jsonUrl")
                if isinstance(result_url, dict)
                else None
            )

            if not json_url:
                raise RuntimeError(
                    "PaddleOCR completed without a result URL."
                )

            response = _get(
                json_url,
                request_timeout,
                "result download",
            )

            try:
                result_lines = [
                    json.loads(line)
                    for line in response.text.splitlines()
                    if line.strip()
                ]
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "PaddleOCR returned malformed JSONL data."
                ) from exc

            break

        if state == "failed":
            raise RuntimeError("PaddleOCR job failed.")

        if state not in {"pending", "running"}:
            raise RuntimeError(
                f"PaddleOCR returned an unknown job state: {state!r}"
            )

        remaining = deadline - time.monotonic()

        if remaining > 0:
            time.sleep(min(interval, remaining))

        interval = min(interval * 1.5, 15.0)

    pages: list[dict[str, Any]] = []
    data_info: dict[str, Any] = {}

    for line in result_lines:
        result = (
            line.get("result")
            if isinstance(line, dict)
            else None
        )

        if not isinstance(result, dict):
            raise RuntimeError(
                "Invalid PaddleOCR result record."
            )

        if isinstance(result.get("dataInfo"), dict):
            data_info.update(result["dataInfo"])

        key = (
            "ocrResults"
            if model == "PP-OCRv6"
            else "layoutParsingResults"
        )

        items = result.get(key, [])

        if not isinstance(items, list):
            raise RuntimeError(
                "Invalid PaddleOCR page list."
            )

        for item in items:
            if not isinstance(item, dict):
                raise RuntimeError(
                    "Invalid PaddleOCR page record."
                )

            page = {
                "pruned_result": item.get("prunedResult"),
                "input_image_url": item.get("inputImage"),
                "raw": item,
            }

            if model == "PP-OCRv6":
                page["ocr_image_url"] = item.get("ocrImage")

            else:
                md = item.get("markdown")
                md = md if isinstance(md, dict) else {}

                page.update(
                    markdown_text=md.get("text", ""),
                    markdown_images=md.get("images", {}),
                    output_images=item.get("outputImages", {}),
                    exports=item.get("exports", {}),
                    markdown=md,
                )

            pages.append(page)

    raw = {
        "job_id": job_id,
        "data_info": data_info,
        "pages": pages,
    }

    logger.info(
        "ocr=recognize status=completed pages=%d elapsed=%.2f",
        len(pages),
        time.monotonic() - started,
    )

    return "\n\n".join(extract_page_texts(raw)), raw