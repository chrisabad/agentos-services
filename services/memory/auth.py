"""Bearer-token auth middleware. Skips /health; rejects everything else without a valid token."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from services.memory.config import get_auth_token

PUBLIC_PATHS = frozenset({"/health"})


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        expected = get_auth_token()
        if not expected:
            return JSONResponse(
                {"error": "service_misconfigured", "detail": "auth token not set in environment"},
                status_code=503,
            )

        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or token != expected:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        return await call_next(request)
