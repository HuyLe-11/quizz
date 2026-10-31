"""PaddleOCR hosted API adapter. Credentials stay on the backend."""

import os
import base64
import dataclasses
from datetime import date, datetime
from typing import Any


def _to_dict(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if hasattr(value, "model_dump"):
        return _to_dict(value.model_dump(mode="json", exclude_none=True))
    if hasattr(value, "dict"):
        return _to_dict(value.dict(exclude_none=True))
    if hasattr(value, "to_dict"):
        return _to_dict(value.to_dict())
    if dataclasses.is_dataclass(value):
        return _to_dict(dataclasses.asdict(value))
    if isinstance(value, list):
        return [_to_dict(item) for item in value]
    if isinstance(value, tuple):
        return [_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_dict(item) for key, item in value.items()}
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        return {
            key: _to_dict(item)
            for key, item in attributes.items()
            if not key.startswith("_")
        }
    slots = getattr(type(value), "__slots__", ())
    if isinstance(slots, str):
        slots = (slots,)
    serialized_slots = {
        key: _to_dict(getattr(value, key))
        for key in slots
        if isinstance(key, str) and not key.startswith("_") and hasattr(value, key)
    }
    return serialized_slots or str(value)


def _text_from(value: Any) -> list[str]:
    """Extract page text from SDK response variants while retaining raw JSON separately."""
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
    else:
        for attr in ("markdown_text", "text"):
            item = getattr(value, attr, None)
            if isinstance(item, str) and item.strip():
                result.append(item.strip())
        pages = getattr(value, "pages", None)
        if pages:
            for page in pages:
                result.extend(_text_from(_to_dict(page)))
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
    if not os.getenv("PADDLEOCR_ACCESS_TOKEN"):
        raise RuntimeError("Backend is missing PADDLEOCR_ACCESS_TOKEN.")

    from paddleocr import PaddleOCRClient

    client = PaddleOCRClient(request_timeout=300, poll_timeout=900)
    try:
        if model == "PP-OCRv6":
            response = client.ocr(file_path=path, model=model)
        else:
            response = client.parse_document(file_path=path, model=model)
        raw = _to_dict(response)
        pages_text = _text_from(raw)
        return "\n\n".join(pages_text), raw if isinstance(raw, dict) else {"response": raw}
    finally:
        client.close()
