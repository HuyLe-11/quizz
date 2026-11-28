"""Console logging setup for QuizPDF application diagnostics."""

from __future__ import annotations

import logging
import sys


_HANDLER_MARKER = "_quizpdf_console_handler"


def configure_quizpdf_logging(
    *,
    logger_name: str = "quizpdf",
    uvicorn_logger_name: str = "uvicorn.error",
) -> logging.Logger:
    """Route the QuizPDF logger to Uvicorn's terminal handler exactly once.

    Named application loggers do not inherit handlers attached specifically to
    ``uvicorn.error``. Uvicorn also commonly leaves the root logger without a
    console handler, so propagation alone can silently drop application logs.
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        handler = next(
            (item for item in uvicorn_logger.handlers if isinstance(item, logging.StreamHandler)),
            None,
        )
        if handler is None:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
            setattr(handler, _HANDLER_MARKER, True)
        logger.addHandler(handler)

    # Prevent the same record from reaching a root handler as well.
    logger.propagate = False
    return logger
