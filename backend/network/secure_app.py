"""Authenticated QuizPDF ASGI entrypoint.

Select SQLite or Supabase using QUIZPDF_DATA_BACKEND.
Always use SupabaseAuthGateway for API authentication.
"""

import logging
import os

from starlette.middleware.cors import CORSMiddleware

try:
    from backend.network.main import app as sqlite_app
    from backend.network.auth_gateway import (
        SupabaseAuthGateway,
    )
except ModuleNotFoundError as exc:
    if exc.name != "backend":
        raise

    from network.main import app as sqlite_app
    from network.auth_gateway import (
        SupabaseAuthGateway,
    )


logger = logging.getLogger("quizpdf")

DATA_BACKEND = os.getenv(
    "QUIZPDF_DATA_BACKEND",
    "sqlite",
).strip().lower()

if DATA_BACKEND == "cloud":
    try:
        from backend.network.cloud_api import app as selected_app
    except ModuleNotFoundError as exc:
        if exc.name != "backend":
            raise
        from network.cloud_api import app as selected_app

elif DATA_BACKEND == "sqlite":
    selected_app = sqlite_app

else:
    raise RuntimeError(
        "QUIZPDF_DATA_BACKEND must be 'sqlite' or 'cloud'."
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
    selected_app
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

__all__ = ["app"]
