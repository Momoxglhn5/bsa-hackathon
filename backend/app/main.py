"""FastAPI app: read-only data API over the SQLite + Parquet stores.

Run from backend/:  uvicorn app.main:app --reload
Storage is kept up to date separately by `python -m app.ingestion.refresh --loop`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes_data import router
from app.storage.sqlite_store import default_data_dir, default_db_path

# Local frontend dev servers only (Next.js / Vite).
CORS_ORIGINS = ["http://localhost:3000", "http://localhost:5173"]
_ERROR_NAMES = {400: "bad_request", 404: "not_found", 422: "validation_error", 503: "unavailable"}


def create_app(
    db_path: str | Path | None = None,
    timeseries_root: str | Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    app = FastAPI(title="BSA data API", version="1")
    app.state.db_path = db_path or default_db_path()
    app.state.timeseries_root = timeseries_root or default_data_dir() / "timeseries"
    app.state.clock = clock or (lambda: datetime.now(timezone.utc))

    app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET"], allow_headers=["*"])
    app.include_router(router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"error": _ERROR_NAMES.get(exc.status_code, "error"), "detail": exc.detail},
                            status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        detail = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse({"error": "validation_error", "detail": detail}, status_code=422)

    return app


app = create_app()
