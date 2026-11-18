"""PaddleOCR hosted API adapter. Credentials stay on the backend."""

import json
import os
import time
from pathlib import Path
from typing import Any

import requests


class PaddleOCRAPIError(RuntimeError):
    def __init__(self, stage: str, status_code: int):
        self.stage = stage
        self.status_code = status_code
        super().__init__(
            f"PaddleOCR {stage} request returned HTTP {status_code}."
        )


class PaddleOCRTimeoutError(TimeoutError):
    pass


def _check_response(response: requests.Response, stage: str) -> None:
    if response.ok:
        return

    raise PaddleOCRAPIError(stage, response.status_code)


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _join_strings(values: list[Any]) -> str:
    return "\n".join(
        text for value in values
        if (text := _string(value))
    )


def _text_from(value: Any) -> str:
    """Pick one representation of a single page, in OCR reading order."""
    if not isinstance(value, dict):
        return ""

    # Priority 1: Direct Markdown representation.
    for key in ("markdown_text", "markdownText"):
        if text := _string(value.get(key)):
            return text

    markdown = value.get("markdown")

    if isinstance(markdown, dict):
        if text := _string(markdown.get("text")):
            return text
    elif text := _string(markdown):
        return text

    # Priority 2: Structured layout parsing blocks.
    blocks = value.get("parsing_res_list")

    if isinstance(blocks, list):
        text = _join_strings([
            block.get("block_content")
            for block in blocks
            if isinstance(block, dict)
        ])

        if text:
            return text

    # Priority 3: Basic OCR recognition results.
    texts = value.get("rec_texts")

    if isinstance(texts, list):
        if text := _join_strings(texts):
            return text

    # Priority 4: Alternative single-text representations.
    for key in ("text", "rec_text", "block_content"):
        if text := _string(value.get(key)):
            return text

    # Priority 5: Nested response representations.
    for key in (
        "pruned_result",
        "prunedResult",
        "raw",
        "result",
    ):
        if text := _text_from(value.get(key)):
            return text

    return ""


def extract_page_texts(
    raw_response: dict[str, Any],
) -> list[str]:
    """Return one string per reported page, including empty page slots."""
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

    result = raw_response.get("result")

    if isinstance(result, dict):
        pages = extract_page_texts(result)

        if pages:
            return pages

    text = _text_from(raw_response)

    return [text] if text else []


def _normalize_pages(
    result_lines: list[Any],
    model: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep the public page structure and raw geometry used by pdf_tools."""

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

        if isinstance(result.get("dataInfo"), dict):
            data_info.update(result["dataInfo"])

        items = result.get(result_key)

        if not isinstance(items, list):
            raise RuntimeError(
                f"PaddleOCR result is missing {result_key}."
            )

        for item in items:
            # Preserve the page position even if OCR returned
            # an invalid or empty page entry.
            if not isinstance(item, dict):
                pages.append({"raw": item})
                continue

            page = {
                "pruned_result": item.get("prunedResult"),
                "input_image_url": item.get("inputImage"),
                "raw": item,
            }

            if model == "PP-OCRv6":
                page["ocr_image_url"] = item.get("ocrImage")

            else:
                markdown = item.get("markdown")

                if not isinstance(markdown, dict):
                    markdown = {
                        "text": _string(markdown)
                    }

                page.update({
                    "markdown_text": _string(
                        markdown.get("text")
                    ),
                    "markdown_images": markdown.get(
                        "images", {}
                    ),
                    "output_images": item.get(
                        "outputImages", {}
                    ),
                    "exports": item.get("exports", {}),
                    "markdown": markdown,
                })

            pages.append(page)

    return pages, data_info


def recognize_pdf(
    path: str,
    model: str,
) -> tuple[str, dict[str, Any]]:
    """Submit a PDF to PaddleOCR and normalize its recognition result."""

    token = os.getenv("PADDLEOCR_ACCESS_TOKEN")

    if not token:
        raise RuntimeError(
            "Backend is missing PADDLEOCR_ACCESS_TOKEN."
        )

    job_url = os.getenv("JOB_URL")

    if not job_url:
        base_url = os.getenv(
            "PADDLEOCR_BASE_URL",
            "https://paddleocr.aistudio-app.com",
        ).rstrip("/")

        job_url = f"{base_url}/api/v2/ocr/jobs"

    # Preserve the original application's timeout configuration.
    timeout = float(
        os.getenv("PADDLEOCR_REQUEST_TIMEOUT", "20")
    )

    poll_timeout = float(
        os.getenv("PADDLEOCR_POLL_TIMEOUT", "75")
    )

    headers = {
        "Authorization": f"Bearer {token}"
    }

    optional_payload = json.dumps({})

    # Step 1: Submit the PDF.
    try:
        with open(path, "rb") as pdf_file:
            response = requests.post(
                job_url,
                headers=headers,
                data={
                    "model": model,
                    "optionalPayload": optional_payload,
                },
                files={
                    "file": (
                        Path(path).name,
                        pdf_file,
                        "application/pdf",
                    )
                },
                timeout=timeout,
            )

    except requests.Timeout as exc:
        raise PaddleOCRTimeoutError(
            "Timed out submitting PDF to PaddleOCR."
        ) from exc

    except requests.RequestException as exc:
        raise RuntimeError(
            "Could not connect to PaddleOCR API while submitting PDF."
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

    # Step 2: Poll the asynchronous OCR job.
    deadline = time.monotonic() + poll_timeout
    interval = 3.0

    while True:
        try:
            status_response = requests.get(
                f"{job_url.rstrip('/')}/{job_id}",
                headers=headers,
                timeout=timeout,
            )

        except requests.Timeout as exc:
            raise PaddleOCRTimeoutError(
                "Timed out checking PaddleOCR job status."
            ) from exc

        except requests.RequestException as exc:
            raise RuntimeError(
                "Could not connect to PaddleOCR API while checking job status."
            ) from exc

        _check_response(status_response, "status")

        status_payload = status_response.json()

        status = (
            status_payload.get("data")
            if isinstance(status_payload, dict)
            else None
        )

        if not isinstance(status, dict):
            raise RuntimeError(
                "PaddleOCR API returned an invalid job status."
            )

        state = status.get("state")

        # Step 3: Download the completed OCR result.
        if state == "done":
            result_url = status.get("resultUrl") or {}

            json_url = (
                result_url.get("jsonUrl")
                if isinstance(result_url, dict)
                else None
            )

            if not json_url:
                raise RuntimeError(
                    "PaddleOCR completed without a result URL."
                )

            try:
                # Do not forward the PaddleOCR access token
                # to the result download URL.
                result_response = requests.get(
                    json_url,
                    timeout=timeout,
                )

            except requests.Timeout as exc:
                raise PaddleOCRTimeoutError(
                    "Timed out downloading PaddleOCR result."
                ) from exc

            except requests.RequestException as exc:
                raise RuntimeError(
                    "Could not download PaddleOCR result."
                ) from exc

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

            break

        if state == "failed":
            raise RuntimeError(
                f"PaddleOCR job failed: "
                f"{status.get('errorMsg') or 'unknown error'}"
            )

        if state not in {"pending", "running"}:
            raise RuntimeError(
                f"PaddleOCR returned an unknown job state: {state!r}"
            )

        remaining = deadline - time.monotonic()

        if remaining <= 0:
            raise PaddleOCRTimeoutError(
                f"Timed out waiting for PaddleOCR job {job_id}."
            )

        time.sleep(min(interval, remaining))
        interval = min(interval * 1.5, 15.0)

    # Step 4: Normalize the results while preserving page geometry.
    pages, data_info = _normalize_pages(
        result_lines,
        model,
    )

    raw = {
        "job_id": job_id,
        "data_info": data_info,
        "pages": pages,
    }

    # Step 5: Use the same extraction logic for both the full
    # document Markdown and the page-aware question parser.
    page_texts = extract_page_texts(raw)

    return "\n\n".join(page_texts), raw
