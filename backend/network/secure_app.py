"""Authenticated FastAPI entrypoint for QuizPDF."""

import os

from starlette.middleware.cors import CORSMiddleware

try:
    from backend.network.main import app as original_app
    from backend.network.auth_gateway import (
        SupabaseAuthGateway,
    )
except ModuleNotFoundError as exc:
    if exc.name != "backend":
        raise

    from network.main import app as original_app
    from network.auth_gateway import (
        SupabaseAuthGateway,
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
    original_app,
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
