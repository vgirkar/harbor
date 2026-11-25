"""Request id, API key auth and a per-key token bucket rate limiter.

The limiter is in-process on purpose: one instance per container is the deployment unit here. For a
horizontally scaled deployment move the bucket to Redis (same interface, one class swap).
"""

from __future__ import annotations

import secrets
import time
import uuid
from collections import defaultdict

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from harbor.config import Settings

PUBLIC_PREFIXES = ("/healthz", "/readyz", "/docs", "/openapi.json", "/redoc", "/static", "/")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=rid, path=request.url.path)
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        response.headers["x-response-time-ms"] = str(int((time.perf_counter() - started) * 1000))
        return response


class ApiKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self._keys = set(settings.api_keys)
        self._admin = settings.admin_api_key

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if not path.startswith("/v1/"):
            return await call_next(request)
        provided = request.headers.get("x-api-key", "")
        is_admin = secrets.compare_digest(provided, self._admin) if provided else False
        is_client = any(secrets.compare_digest(provided, k) for k in self._keys) if provided else False
        if path.startswith("/v1/ingest") and not is_admin:
            return JSONResponse({"detail": "admin key required"}, status_code=403)
        if not (is_client or is_admin):
            return JSONResponse({"detail": "invalid or missing X-API-Key"}, status_code=401)
        request.state.api_key = provided
        request.state.is_admin = is_admin
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, per_minute: int):
        super().__init__(app)
        self._capacity = float(per_minute)
        self._refill = per_minute / 60.0
        self._buckets: dict[str, tuple[float, float]] = defaultdict(
            lambda: (self._capacity, time.monotonic())
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        if not request.url.path.startswith("/v1/"):
            return await call_next(request)
        key = request.headers.get("x-api-key") or (request.client.host if request.client else "anon")
        tokens, last = self._buckets[key]
        now = time.monotonic()
        tokens = min(self._capacity, tokens + (now - last) * self._refill)
        if tokens < 1:
            return JSONResponse(
                {"detail": "rate limit exceeded"}, status_code=429, headers={"retry-after": "1"}
            )
        self._buckets[key] = (tokens - 1, now)
        return await call_next(request)
