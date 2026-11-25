"""Supabase authentication gateway for QuizPDF."""

import logging
import os
from uuid import UUID
from typing import Any

import httpx
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("quizpdf")


def _unauthorized(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": message},
    )


def _authorization_token(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() != b"authorization":
            continue

        try:
            header = value.decode("ascii")
        except UnicodeDecodeError:
            return None

        scheme, separator, token = header.partition(" ")

        if (
            separator
            and scheme.lower() == "bearer"
            and token.strip()
        ):
            return token.strip()

    return None


def _public_request(scope: Scope) -> bool:
    path = scope.get("path", "")
    method = scope.get("method", "")

    if method == "OPTIONS":
        return True

    if path == "/api/health":
        return True

    # Existing images are served through regular <img> URLs.
    # This temporary exception must be removed when images
    # are migrated to private Supabase Storage.
    if path.startswith("/api/assets/"):
        return True

    return not path.startswith("/api/")


class SupabaseAuthGateway:
    """Authenticate HTTP API requests before FastAPI handles them."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if _public_request(scope):
            await self.app(scope, receive, send)
            return

        supabase_url = os.getenv(
            "SUPABASE_URL",
            "",
        ).strip().rstrip("/")

        publishable_key = os.getenv(
            "SUPABASE_PUBLISHABLE_KEY",
            "",
        ).strip()

        if not supabase_url or not publishable_key:
            response = JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "Backend chưa được cấu hình "
                        "Supabase Auth."
                    ),
                },
            )

            await response(scope, receive, send)
            return

        token = _authorization_token(scope)

        if not token:
            response = _unauthorized(
                "Bạn cần đăng nhập để sử dụng QuizPDF."
            )

            await response(scope, receive, send)
            return

        try:
            async with httpx.AsyncClient(
                timeout=10.0,
            ) as client:
                result = await client.get(
                    f"{supabase_url}/auth/v1/user",
                    headers={
                        "apikey": publishable_key,
                        "Authorization": f"Bearer {token}",
                    },
                )

        except httpx.RequestError:
            logger.warning(
                "auth=verification_unavailable"
            )

            response = JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "Không thể kết nối Supabase "
                        "để xác thực tài khoản."
                    ),
                },
            )

            await response(scope, receive, send)
            return

        if result.status_code == 429:
            response = JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "Dịch vụ xác thực đang giới hạn "
                        "yêu cầu. Vui lòng thử lại sau."
                    ),
                },
            )

            await response(scope, receive, send)
            return

        if result.status_code >= 500:
            response = JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "Dịch vụ xác thực tạm thời "
                        "không khả dụng."
                    ),
                },
            )

            await response(scope, receive, send)
            return

        if result.status_code != 200:
            response = _unauthorized(
                "Phiên đăng nhập không hợp lệ "
                "hoặc đã hết hạn."
            )

            await response(scope, receive, send)
            return

        try:
            user: Any = result.json()
        except ValueError:
            user = None

        user_id = (
            user.get("id")
            if isinstance(user, dict)
            else None
        )

        if not isinstance(user_id, str):
            response = _unauthorized(
                "Thông tin tài khoản không hợp lệ."
            )

            await response(scope, receive, send)
            return

        try:
            UUID(user_id)
        except ValueError:
            response = _unauthorized(
                "Mã người dùng không hợp lệ."
            )

            await response(scope, receive, send)
            return

        # Attach trusted user identity to the request scope.
        # The existing SQLite routes do not use it yet.
        state = scope.setdefault("state", {})
        state["supabase_user_id"] = user_id

        await self.app(scope, receive, send)
