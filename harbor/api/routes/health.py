from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from harbor import __version__
from harbor.api.deps import get_container
from harbor.container import Container

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/readyz")
async def readyz(response: Response, c: Container = Depends(get_container)) -> dict:
    checks = await c.ready()
    ok = all(checks.values())
    response.status_code = 200 if ok else 503
    return {
        "status": "ready" if ok else "degraded",
        "checks": checks,
        "mode": "offline" if c.engine is None else "postgres",
    }
