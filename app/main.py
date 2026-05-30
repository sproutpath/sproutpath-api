"""FastAPI application factory.

Run with::

    uvicorn app.main:app --reload

The factory pattern (``create_app``) keeps testing simple — each test
gets a fresh app instance instead of sharing module-level state.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.videos import router as videos_router
from app.api.study import router as study_router
from app.api.analytics import router as analytics_router
from app.api.app_status import router as app_status_router
from app.api.push_notifications import router as notifications_router
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    # ─── Routes ────────────────────────────────────────────────────
    app.include_router(videos_router)        # /sproutpath/api/v1/getvideos
    app.include_router(study_router)         # /sproutpath/api/v1/getstudy  ← NEW
    app.include_router(analytics_router)     # /sproutpath/api/v1/analytics ← NEW
    app.include_router(app_status_router)    # /sproutpath/api/v1/appstatus
    app.include_router(notifications_router) # /sproutpath/api/v1/notifications

    @app.get("/healthz", tags=["meta"], summary="Liveness probe")
    async def healthz() -> dict:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
