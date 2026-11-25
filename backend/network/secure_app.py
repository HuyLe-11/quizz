"""Authenticated ASGI entrypoint for QuizPDF.

Cloud is the default backend for authenticated accounts.
Legacy SQLite is explicitly opt-in for local development.
"""

import logging
import os

from pathlib import Path

from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware


load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)


try:
    from backend.network.auth_gateway import (
        SupabaseAuthGateway,
    )

except ModuleNotFoundError as exc:
    if exc.name != "backend":
        raise

    from network.auth_gateway import (
        SupabaseAuthGateway,
    )


logger = logging.getLogger("quizpdf")


DATA_BACKEND = os.getenv(
    "QUIZPDF_DATA_BACKEND",
    "cloud",
).strip().lower()


if DATA_BACKEND == "cloud":
    try:
        from backend.network.cloud_api import (
            app as selected_app,
        )

    except ModuleNotFoundError as exc:
        if exc.name != "backend":
            raise

        from network.cloud_api import (
            app as selected_app,
        )

    try:
        from backend.network.parser_review_api import (
            router as parser_review_router,
        )

    except ModuleNotFoundError as exc:
        if exc.name != "backend":
            raise

        from network.parser_review_api import (
            router as parser_review_router,
        )

    selected_app.include_router(
        parser_review_router
    )


elif DATA_BACKEND == "sqlite":
    allow_legacy = (
        os.getenv(
            "QUIZPDF_ALLOW_SHARED_SQLITE",
            "0",
        ).strip() == "1"
    )

    if not allow_legacy:
        raise RuntimeError(
            "Shared SQLite mode is not safe for "
            "multi-user authentication. "
            "Set QUIZPDF_DATA_BACKEND=cloud, or "
            "explicitly allow the legacy demo using "
            "QUIZPDF_ALLOW_SHARED_SQLITE=1."
        )

    logger.warning(
        "quizpdf=data_backend mode=sqlite "
        "security=shared_data "
        "development_only=true"
    )

    try:
        from backend.network.main import (
            app as selected_app,
        )

    except ModuleNotFoundError as exc:
        if exc.name != "backend":
            raise

        from network.main import (
            app as selected_app,
        )


else:
    raise RuntimeError(
        "QUIZPDF_DATA_BACKEND must be "
        "'sqlite' or 'cloud'."
    )


logger.info(
    "quizpdf=data_backend selected=%s",
    DATA_BACKEND,
)


origins = [
    origin.strip()

    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173",
    ).split(",")

    if origin.strip()
]


authenticated_app = SupabaseAuthGateway(
    selected_app,
)


app = CORSMiddleware(
    authenticated_app,

    allow_origins=origins,

    allow_methods=[
        "GET",
        "POST",
        "PATCH",
    ],

    allow_headers=[
        "Authorization",
        "Content-Type",
    ],

    allow_credentials=False,
)


__all__ = [
    "app",
    "DATA_BACKEND",
]