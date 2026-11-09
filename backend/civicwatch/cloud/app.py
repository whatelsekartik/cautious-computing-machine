"""FastAPI application for the Vercel deployment.

The function handles dashboard traffic only. It imports no inference libraries,
so the bundle stays small and cold starts are fast. Video never passes through
it: browsers upload directly to object storage with presigned URLs, and the
local connector posts incidents and evidence references over HTTPS.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..config import APP_NAME, APP_VERSION
from ..db import init_db
from . import api as cloud_api

log = logging.getLogger("civicwatch.cloud")

_schema_ready = False

# Vercel/Lambda rejects oversized request bodies before they reach us; answering
# with a clear 413 (and no partial writes) is friendlier and keeps the limit
# documented in one place. Large media never travels through this function.
MAX_JSON_REQUEST_BYTES = 4 * 1024 * 1024


def _ensure_schema_once() -> dict[str, Any]:
    global _schema_ready
    if _schema_ready:
        return {"applied": [], "cached": True}
    applied = init_db()
    _schema_ready = True
    if applied:
        log.info("Applied migrations on cold start: %s", applied)
    return {"applied": applied, "cached": False}


def create_cloud_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    app = FastAPI(
        title=f"{APP_NAME} — cloud API",
        version=APP_VERSION,
        description=(
            "Hosted dashboard API. Inference (YOLO/ByteTrack/OpenCV) runs on a local "
            "connector; this service stores incidents, metadata and evidence references."
        ),
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    allowed = [o.strip() for o in (os.environ.get("CIVICWATCH_CORS_ORIGINS") or "").split(",") if o.strip()]
    if allowed:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH"}:
            raw_length = request.headers.get("content-length")
            if raw_length and raw_length.isdigit() and int(raw_length) > MAX_JSON_REQUEST_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": (
                            "Request body is larger than 4 MB. Upload videos directly to "
                            "storage with /api/media/upload-url instead of posting them here."
                        )
                    },
                )
        # CSRF: cookie-authenticated writes must carry a custom header, which a
        # cross-site form cannot set.
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            has_bearer = (request.headers.get("authorization") or "").lower().startswith("bearer ")
            if not has_bearer and request.headers.get("x-civicwatch-csrf") != "1":
                if request.url.path.startswith("/api/") and not request.url.path.startswith(
                    ("/api/connector/", "/api/auth/session")
                ):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Missing X-CivicWatch-CSRF header"},
                    )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.exception_handler(KeyError)
    async def key_error(_request: Request, exc: KeyError) -> JSONResponse:
        detail = exc.args[0] if exc.args else "Not found"
        return JSONResponse(status_code=404, content={"detail": str(detail)})

    @app.exception_handler(ValueError)
    async def value_error(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    app.include_router(cloud_api.public, prefix="/api", tags=["public"])
    app.include_router(cloud_api.auth, prefix="/api", tags=["auth"])
    app.include_router(cloud_api.dashboard, prefix="/api", tags=["dashboard"])
    app.include_router(cloud_api.connector_api, prefix="/api", tags=["connector"])

    @app.on_event("startup")
    def startup() -> None:
        try:
            _ensure_schema_once()
        except Exception:  # noqa: BLE001 - surfaced by /api/health instead
            log.exception("Database migrations failed; the API will report an unhealthy database")

    @app.get("/api", include_in_schema=False)
    def root() -> dict[str, Any]:
        return {
            "app": APP_NAME,
            "version": APP_VERSION,
            "mode": "cloud",
            "docs": "/api/docs",
            "frontend": "served as Vercel static assets",
        }

    @app.get("/", include_in_schema=False)
    def index() -> JSONResponse:
        return JSONResponse(
            {
                "app": APP_NAME,
                "mode": "cloud",
                "message": "CivicWatch cloud API. The dashboard is served by Vercel as static assets.",
                "health": "/api/health",
                "docs": "/api/docs",
            }
        )

    return app


app = create_cloud_app()
