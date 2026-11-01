"""PaddleOCR hosted API adapter. Credentials stay on the backend."""

import json
import os
import time
from pathlib import Path
from typing import Any

import requests


def _text_from(value: Any) -> list[str]:
    """Extract page text from PaddleOCR response variants."""
    result: list[str] = []
    if isinstance(value, dict):
        has_document_markdown = False
        for key in ("markdown_text", "markdownText", "text", "rec_text", "rec_texts"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                result.append(item.strip())
                if key in ("markdown_text", "markdownText"):
                    has_document_markdown = True
            elif isinstance(item, list):
                result.extend(str(part).strip() for part in item if str(part).strip())
        markdown = value.get("markdown")
        if not has_document_markdown and isinstance(markdown, dict) and isinstance(markdown.get("text"), str):
            result.append(markdown["text"].strip())
        for key in ("pages", "layout_parsing_results", "layoutParsingResults", "ocr_results", "ocrResults", "prunedResult", "pruned_result"):
            if has_document_markdown and key in ("prunedResult", "pruned_result"):
                continue
            nested = value.get(key)
            if isinstance(nested, list):
                for page in nested:
                    result.extend(_text_from(page))
            elif isinstance(nested, dict):
                result.extend(_text_from(nested))
        if isinstance(value.get("result"), (dict, list)):
            result.extend(_text_from(value["result"]))
    elif isinstance(value, list):
        for item in value:
            result.extend(_text_from(item))
    return result


def extract_page_texts(raw_response: dict[str, Any]) -> list[str]:
    """Return one OCR text string per input page for page-aware question records."""
    pages = raw_response.get("pages")
    if not isinstance(pages, list):
        text = "\n\n".join(_text_from(raw_response))
        return [text] if text else []

    output: list[str] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_text = page.get("markdown_text") or page.get("markdownText")
        if not page_text and isinstance(page.get("markdown"), dict):
            page_text = page["markdown"].get("text")
        if not page_text:
            page_text = "\n".join(_text_from(page.get("pruned_result") or page.get("prunedResult") or {}))
        output.append(str(page_text or ""))
    return output


def recognize_pdf(path: str, model: str) -> tuple[str, dict[str, Any]]:
    token = os.getenv("PADDLEOCR_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("Backend is missing PADDLEOCR_ACCESS_TOKEN.")
    job_url = os.getenv("JOB_URL")
    if not job_url:
        base_url = os.getenv("PADDLEOCR_BASE_URL", "https://paddleocr.aistudio-app.com").rstrip("/")
        job_url = f"{base_url}/api/v2/ocr/jobs"

    timeout = float(os.getenv("PADDLEOCR_REQUEST_TIMEOUT", "45"))
    poll_timeout = float(os.getenv("PADDLEOCR_POLL_TIMEOUT", "240"))
    headers = {"Authorization": f"Bearer {token}"}
    optional_payload = json.dumps({})

    with open(path, "rb") as pdf_file:
        response = requests.post(
            job_url,
            headers=headers,
            data={"model": model, "optionalPayload": optional_payload},
            files={"file": (Path(path).name, pdf_file, "application/pdf")},
            timeout=timeout,
        )
    response.raise_for_status()
    submitted = response.json()
    data = submitted.get("data") if isinstance(submitted, dict) else None
    job_id = data.get("jobId") if isinstance(data, dict) else None
    if not job_id:
        raise RuntimeError("PaddleOCR API response did not include a job ID.")

    deadline = time.monotonic() + poll_timeout
    interval = 3.0
    while True:
        status_response = requests.get(f"{job_url.rstrip('/')}/{job_id}", headers=headers, timeout=timeout)
        status_response.raise_for_status()
        status_payload = status_response.json()
        status = status_payload.get("data") if isinstance(status_payload, dict) else None
        if not isinstance(status, dict):
            raise RuntimeError("PaddleOCR API returned an invalid job status.")
        state = status.get("state")
        if state == "done":
            result_url = status.get("resultUrl") or {}
            json_url = result_url.get("jsonUrl") if isinstance(result_url, dict) else None
            if not json_url:
                raise RuntimeError("PaddleOCR completed without a result URL.")
            result_response = requests.get(json_url, timeout=timeout)
            result_response.raise_for_status()
            try:
                result_lines = [json.loads(line) for line in result_response.text.splitlines() if line.strip()]
            except json.JSONDecodeError as exc:
                raise RuntimeError("PaddleOCR returned malformed JSONL data.") from exc
            break
        if state == "failed":
            raise RuntimeError(f"PaddleOCR job failed: {status.get('errorMsg') or 'unknown error'}")
        if state not in {"pending", "running"}:
            raise RuntimeError(f"PaddleOCR returned an unknown job state: {state!r}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for PaddleOCR job {job_id}.")
        time.sleep(min(interval, remaining))
        interval = min(interval * 1.5, 15.0)

    pages: list[dict[str, Any]] = []
    data_info: dict[str, Any] = {}
    for line in result_lines:
        result = line.get("result", {}) if isinstance(line, dict) else {}
        if isinstance(result.get("dataInfo"), dict):
            data_info.update(result["dataInfo"])
        if model == "PP-OCRv6":
            for item in result.get("ocrResults", []):
                pages.append({
                    "pruned_result": item.get("prunedResult"),
                    "ocr_image_url": item.get("ocrImage"),
                    "input_image_url": item.get("inputImage"),
                    "raw": item,
                })
        else:
            for item in result.get("layoutParsingResults", []):
                markdown = item.get("markdown", {})
                pages.append({
                    "markdown_text": markdown.get("text", ""),
                    "markdown_images": markdown.get("images", {}),
                    "output_images": item.get("outputImages", {}),
                    "pruned_result": item.get("prunedResult"),
                    "input_image_url": item.get("inputImage"),
                    "exports": item.get("exports", {}),
                    "markdown": markdown,
                    "raw": item,
                })

    raw = {"job_id": job_id, "data_info": data_info, "pages": pages}
    return "\n\n".join(_text_from(page) for page in pages if _text_from(page)), raw
