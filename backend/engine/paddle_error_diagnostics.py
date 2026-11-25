"""Bounded, privacy-preserving diagnostics for failed PaddleOCR HTTP calls."""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("quizpdf")

MAX_BODY_BYTES = 64 * 1024

MAX_MESSAGE_CHARS = 2048

MESSAGE_KEYS = (
    "errorMsg",
    "errorMessage",
    "message",
    "msg",
    "detail",
    "error",
)

STAGES = {
    "submit": "submit",
    "job status polling": "poll",
    "status": "poll",
    "result download": "result",
}

RULES = (
    (
        "quota",
        r"\b(quota|insufficient balance|credits? exhausted)\b|额度|余额不足",
    ),
    (
        "authentication",
        r"\b(unauthori[sz]ed|authentication failed|invalid token|expired token|invalid access token|token expired|invalid api key)\b|令牌无效|鉴权失败",
    ),
    (
        "rate_limit",
        r"\b(rate limit|too many requests|throttl\w*)\b|限流",
    ),
    (
        "optional_payload",
        r"optionalpayload|optional_payload|usedocorientationclassify|usedocunwarping",
    ),
    (
        "model",
        r"\b(invalid model|unsupported model|unknown model|model not found|model is not supported)\b",
    ),
    (
        "file",
        r"\b(invalid file|invalid pdf|unsupported file|file too large|file size|multipart|missing file)\b",
    ),
    (
        "service",
        r"\b(service unavailable|internal server error|bad gateway|gateway timeout)\b",
    ),
)


def _messages(body: dict):

    for key in MESSAGE_KEYS:

        value = body.get(key)

        if isinstance(value, str):

            yield value[:MAX_MESSAGE_CHARS]

        elif isinstance(value, dict):

            for child in MESSAGE_KEYS:

                message = value.get(child)

                if isinstance(message, str):

                    yield message[:MAX_MESSAGE_CHARS]


def _category(status: int, body: dict) -> str:

    if status in (401, 403):
        return "authentication_or_permission"

    if status == 429:
        return "rate_limit"

    if status == 413:
        return "payload_too_large"

    if status == 415:
        return "unsupported_media_type"

    messages = tuple(_messages(body))

    for category, pattern in RULES:

        if any(
            re.search(
                pattern,
                text,
                re.IGNORECASE,
            )
            for text in messages
        ):
            return category

    if status == 400:
        return "bad_request_unknown"

    if status == 404:
        return "not_found"

    if 500 <= status <= 599:
        return "server_error"

    return "http_error_unknown"


def log_http_error(response, stage: str) -> None:
    """Never log provider text, headers, URLs, tokens or request data."""

    try:

        status = response.status_code

        if (
            type(status) is not int
            or not 100 <= status <= 599
            or response.ok
        ):
            return

        raw = response.content

        size = (
            len(raw)
            if isinstance(raw, bytes)
            else None
        )

        media = (
            response.headers
            .get("Content-Type", "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )

        family = (
            "json"
            if (
                media == "application/json"
                or media.endswith("+json")
            )
            else "html"
            if media == "text/html"
            else "other"
        )

        body = {}

        if size is not None and size > MAX_BODY_BYTES:

            shape = "oversized"

        elif not raw:

            shape = "empty"

        elif family == "json" and isinstance(raw, bytes):

            try:

                parsed = json.loads(raw)

                if isinstance(parsed, dict):

                    body = parsed

                    shape = "json_object"

                else:

                    shape = "json_other"

            except (
                ValueError,
                UnicodeError,
                RecursionError,
            ):

                shape = "invalid_json"

        else:

            shape = "non_json"

        logger.warning(
            "paddleocr:http_error "
            "stage=%s "
            "status=%d "
            "category=%s "
            "content_type=%s "
            "body_shape=%s "
            "response_bytes=%s",

            STAGES.get(stage, "other"),
            status,
            _category(status, body),
            family,
            shape,
            size,
        )

    except Exception:

        # Diagnostics are best-effort;
        # the original HTTP exception must win.

        return