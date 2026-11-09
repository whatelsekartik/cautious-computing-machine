"""FastAPI application factory and CLI entry point for CivicWatch AI Studio."""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import incidents as incidents_api
from .api import sources as sources_api
from .api import system as system_api
from .config import APP_NAME, APP_VERSION, get_paths, host, port
from .db import init_db
from .security import LocalhostOnlyMiddleware, redact

log = logging.getLogger("civicwatch")


class RedactionFilter(logging.Filter):
    """Strip stream credentials from every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            cleaned = redact(message)
            if cleaned != message:
                record.msg = cleaned
                record.args = ()
        except Exception:  # pragma: no cover
            pass
        return True


def configure_logging() -> None:
    paths = get_paths()
    root = logging.getLogger()
    if any(getattr(handler, "_civicwatch", False) for handler in root.handlers):
        return
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(RedactionFilter())
    stream_handler._civicwatch = True  # type: ignore[attr-defined]
    root.addHandler(stream_handler)
    try:
        file_handler = logging.FileHandler(paths.logs / "civicwatch.log")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(RedactionFilter())
        file_handler._civicwatch = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    except OSError:
        pass
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def create_app() -> FastAPI:
    configure_logging()
    init_db()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        paths = get_paths()
        log.info("%s v%s starting; data=%s", APP_NAME, APP_VERSION, paths.data)
        yield
        from .services.cameras import camera_service
        from .services.jobs import job_manager

        log.info("Shutting down: stopping jobs and camera sessions")
        job_manager.shutdown()
        camera_service.shutdown()

    app = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        description="Local-first civic monitoring. All inference runs on this machine.",
        lifespan=lifespan,
    )
    app.add_middleware(LocalhostOnlyMiddleware)

    app.include_router(system_api.router, prefix="/api", tags=["system"])
    app.include_router(sources_api.router, prefix="/api", tags=["sources"])
    app.include_router(incidents_api.router, prefix="/api", tags=["incidents"])

    @app.exception_handler(KeyError)
    async def key_error_handler(_request: Request, exc: KeyError) -> JSONResponse:
        detail = exc.args[0] if exc.args else "Not found"
        return JSONResponse(status_code=404, content={"detail": str(detail)})

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": redact(str(exc))})

    @app.get("/api", include_in_schema=False)
    def api_root() -> dict:
        return {"app": APP_NAME, "version": APP_VERSION, "docs": "/docs"}

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    dist = get_paths().frontend_dist
    if not dist.exists():
        @app.get("/", include_in_schema=False)
        def missing_frontend() -> HTMLResponse:
            return HTMLResponse(
                "<html><body style='font-family:-apple-system,system-ui;padding:40px'>"
                f"<h2>{APP_NAME} backend is running</h2>"
                "<p>The web interface has not been built yet.</p>"
                "<p>Run <code>npm install &amp;&amp; npm run build</code> in the "
                "<code>frontend</code> folder, or use the setup launcher.</p>"
                f"<p>API docs: <a href='/docs'>/docs</a></p></body></html>",
                status_code=200,
            )
        return

    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(dist / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(("api/", "docs", "openapi.json")):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (dist / full_path).resolve()
        try:
            candidate.relative_to(dist.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found")
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run(
        "civicwatch.main:app",
        host=host(),
        port=port(),
        log_level="info",
        access_log=False,
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
