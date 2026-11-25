"""PaddleOCR-only recognition entrypoint for QuizPDF.

Keep the existing cloud pipeline contract while ensuring
PaddleOCR remains the sole text recognition engine.
"""

import logging
import os

from pathlib import Path
from typing import Any

from .paddle_resilient import recognize_pdf

logger = logging.getLogger("quizpdf")


def recognize_pdf_smart(
    path: str,
    model: str,
    *,
    user_id: str | None = None,
    filename: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Recognize a PDF exclusively through PaddleOCR.

    The legacy 'auto' value is accepted for configuration
    compatibility, but it no longer selects another engine.

    Owner-scoped journal recovery is enabled when both
    user_id and filename are supplied.
    """

    mode = os.getenv(
        "QUIZPDF_OCR_MODE",
        "paddle",
    ).strip().lower()

    if mode not in {"paddle", "auto"}:
        raise ValueError(
            "QUIZPDF_OCR_MODE must be 'paddle' "
            "or 'auto'. Only PaddleOCR is supported."
        )

    logger.info(
        "quizpdf:recognition "
        "engine=paddleocr "
        "mode=%s file=%s",
        mode,
        Path(path).name,
    )

    if user_id is None and filename is None:

        # Preserve the original contract for legacy callers.
        markdown, raw_response = recognize_pdf(
            path,
            model,
        )

    else:

        # The OCR engine validates the paired identity
        # and creates the owner-scoped journal.
        markdown, raw_response = recognize_pdf(
            path,
            model,
            user_id=user_id,
            filename=filename,
        )

    logger.info(
        "quizpdf:recognition "
        "engine=paddleocr "
        "status=completed characters=%d",
        len(markdown),
    )

    return markdown, raw_response