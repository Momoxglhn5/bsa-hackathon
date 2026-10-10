"""FastAPI app: read-only data API over the SQLite + Parquet stores.

Run from backend/:  uvicorn app.main:app --reload
Storage is kept up to date separately by `python -m app.ingestion.refresh --loop`.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes_data import router
from app.api.routes_delivery import router as delivery_router
from app.analytics.dataset import AnalyticsSnapshot, load_snapshot
from app.analytics.service import AnalyticsService
from app.analytics.provider import StoreAnalyticsProvider
from app.api.routes_analytics import create_router
from app.storage.sqlite_store import default_data_dir, default_db_path

DEFAULT_SNAPSHOT = Path(__file__).parent / "fixtures" / "analytics_demo.json"

# Local frontend dev servers only (Next.js / Vite).
CORS_ORIGINS = ["http://localhost:3000", "http://localhost:5173"]
_ERROR_NAMES = {400: "bad_request", 404: "not_found", 422: "validation_error", 503: "unavailable"}


def create_app(
    db_path: str | Path | None = None,
    timeseries_root: str | Path | None = None,
    clock: Callable[[], datetime] | None = None,
    *, snapshot: AnalyticsSnapshot | None = None, replay: bool | None = None,
) -> FastAPI:
    # Preserve the snapshot injection API used by Core tests and offline tools.
    if isinstance(db_path, AnalyticsSnapshot):
        snapshot, db_path = db_path, None
    if snapshot is None and os.environ.get("ANALYTICS_SNAPSHOT"):
        snapshot = load_snapshot(os.environ["ANALYTICS_SNAPSHOT"])
    replay = (os.environ.get("ANALYTICS_REPLAY", str(snapshot is not None)).lower() == "true") if replay is None else replay
    app = FastAPI(title="DeFi Risk Radar — Data and Core Analytics", version="1.1.0")
    app.state.db_path = db_path or default_db_path()
    app.state.timeseries_root = timeseries_root or default_data_dir() / "timeseries"
    app.state.clock = clock or (lambda: datetime.now(timezone.utc))

    app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["*"])
    app.include_router(router)
    app.include_router(delivery_router)
    app.state.analytics = AnalyticsService(snapshot, replay=replay, clock=app.state.clock) if snapshot else None
    provider = StoreAnalyticsProvider(app.state.db_path, app.state.timeseries_root, app.state.clock, replay=replay, inputs_path=os.environ.get("ANALYTICS_INPUTS"))
    app.include_router(create_router(app.state.analytics, provider=provider))

    @app.exception_handler(KeyError)
    async def analytics_missing(_: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse({"error": "not_found", "detail": str(exc.args[0])}, status_code=404)

    @app.exception_handler(ValueError)
    async def analytics_invalid(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"error": "validation_error", "detail": str(exc)}, status_code=422)

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
