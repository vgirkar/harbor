"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from harbor import __version__
from harbor.api.middleware import ApiKeyMiddleware, RateLimitMiddleware, RequestIdMiddleware
from harbor.api.routes import chat, conversations, health, ingest, voice
from harbor.config import Settings, get_settings
from harbor.container import Container, build_container, build_offline_container
from harbor.logging import configure_logging, get_logger

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
log = get_logger(__name__)


def create_app(
    settings: Settings | None = None, container: Container | None = None, *, offline: bool = False
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_logs=settings.app_env == "prod")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if container is not None:
            app.state.container = container
        elif offline:
            app.state.container = build_offline_container(settings)
        else:
            app.state.container = await build_container(settings)
        log.info(
            "app.started",
            version=__version__,
            mode="offline" if app.state.container.engine is None else "postgres",
            llm=settings.llm_provider,
            embeddings=settings.embeddings_provider,
            index=settings.vector_backend,
        )
        try:
            yield
        finally:
            await app.state.container.close()

    app = FastAPI(title="Harbor support assistant", version=__version__, lifespan=lifespan)
    app.add_middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_minute)
    app.add_middleware(ApiKeyMiddleware, settings=settings)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"]
    )

    for r in (health.router, chat.router, voice.router, ingest.router, conversations.router):
        app.include_router(r)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
