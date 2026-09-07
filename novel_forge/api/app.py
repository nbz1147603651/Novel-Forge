"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from novel_forge.api import performance_router
from novel_forge.api.routes import (
    admin,
    chapter,
    chapter_tools,
    engine,
    film,
    jobs,
    memory,
    outline_tracker,
    short,
    status,
    tts,
    ui_views,
    works,
)
from novel_forge.api.security import configure_api_security
from novel_forge.common.runtime_identity import EngineRestartRequiredError
from novel_forge.core.exceptions import BudgetExceededError
from novel_forge.core.exceptions_framework import (
    ConfigurationException,
    LLMException,
    NovelForgeException,
    PersistenceException,
    ValidationException,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — startup / shutdown."""
    from novel_forge.core.infra.advanced_cache import shutdown_cache_resources
    from novel_forge.core.infra.async_task_processor import (
        initialize_async_task_processor,
        shutdown_async_task_processor,
    )
    from novel_forge.core.infra.performance_monitor import shutdown_performance_resources

    try:
        await initialize_async_task_processor()
    except Exception as exc:
        logger.error("Async service initialization failed: %s", exc)
        raise

    try:
        yield
    finally:
        try:
            from novel_forge.api.deps import shutdown_job_service

            shutdown_job_service()
        except Exception as exc:
            logger.warning("Job service shutdown warning: %s", exc)
        try:
            from novel_forge.app_service.ollama_control import shutdown_ollama_control_service

            shutdown_ollama_control_service()
        except Exception as exc:
            logger.warning("Ollama control shutdown warning: %s", exc)
        try:
            await shutdown_async_task_processor()
        except Exception as exc:
            logger.warning("Async task processor shutdown warning: %s", exc)
        shutdown_cache_resources()
        shutdown_performance_resources(wait=False)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Novel Forge API",
        description="多模型协作小说创作系统",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.start_time = time.time()

    # ---------- CORS (dev-mode: allow Vite/Tauri frontend origins) ----------
    from novel_forge.core.config import get_settings

    _settings = get_settings()
    _cors_origins = [
        origin.strip()
        for origin in str(
            getattr(_settings, "api_cors_allow_origins", "")
            or (
                "http://localhost:1420,http://127.0.0.1:1420,"
                "tauri://localhost,http://tauri.localhost,https://tauri.localhost"
            )
        ).split(",")
        if origin.strip()
    ]
    if _cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # ---------- Global exception handlers ----------

    @app.exception_handler(EngineRestartRequiredError)
    async def _engine_restart_required_handler(
        request: Request,
        exc: EngineRestartRequiredError,
    ) -> JSONResponse:
        """Make a changed local source tree actionable rather than opaque."""

        return JSONResponse(
            status_code=409,
            content={
                "error": exc.error_code,
                "message": str(exc),
                "bootRevision": exc.boot_revision,
                "currentRevision": exc.current_revision,
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, NovelForgeException):
            if isinstance(exc, BudgetExceededError):
                # Budget errors get dedicated handling with structured body.
                period = getattr(exc, "period", "unknown")
                spent = getattr(exc, "spent", 0.0)
                limit = getattr(exc, "limit", 0.0)
                # 429 when the budget may auto-recover (daily/monthly reset);
                # 402 when user configuration is insufficient.
                is_time_based = period in ("daily", "monthly")
                status_code = 429 if is_time_based else 402
                body: dict[str, object] = {
                    "error": "budget_exceeded",
                    "message": exc.message,
                    "period": period,
                    "spent": round(spent, 4),
                    "limit": round(limit, 2),
                }
                reset_at = getattr(exc, "reset_at", None)
                if reset_at is not None:
                    body["reset_at"] = str(reset_at)
                response = JSONResponse(status_code=status_code, content=body)
                if is_time_based and reset_at is not None:
                    try:
                        from datetime import datetime, timezone

                        if isinstance(reset_at, datetime):
                            now = datetime.now(timezone.utc)
                            retry_after = max(0, int((reset_at - now).total_seconds()))
                            response.headers["Retry-After"] = str(retry_after)
                    except Exception:
                        pass
                return response
            if isinstance(exc, ValidationException):
                status_code = 400  # Bad Request — caller sent invalid input
            elif isinstance(exc, LLMException):
                status_code = 502  # Bad Gateway — upstream model provider error
            elif isinstance(exc, PersistenceException):
                status_code = 503  # Service Unavailable — storage layer failure
            elif isinstance(exc, ConfigurationException):
                status_code = 500  # Internal Server Error — misconfiguration
            else:
                status_code = 500
            return JSONResponse(
                status_code=status_code,
                content={"error": exc.error_code, "message": exc.message},
            )
        logger.exception("Unhandled error for %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_server_error", "message": "An unexpected error occurred"},
        )

    configure_api_security(app)

    # ---------- Routes ----------

    app.include_router(works.router, prefix="/api/v1/works", tags=["works"])
    app.include_router(short.router, prefix="/api/v1/short", tags=["short"])
    app.include_router(chapter.router, prefix="/api/v1/chapters", tags=["chapters"])
    app.include_router(chapter_tools.router, prefix="/api/v1/chapter-tools", tags=["chapter-tools"])
    app.include_router(engine.router, prefix="/api/v1/engine", tags=["engine"])
    app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["jobs"])
    app.include_router(status.router, prefix="/api/v1/status", tags=["status"])
    app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
    app.include_router(memory.router, prefix="/api/v1/memory", tags=["memory"])
    app.include_router(
        outline_tracker.router, prefix="/api/v1/outline-tracker", tags=["outline-tracker"]
    )
    app.include_router(tts.router, prefix="/api/v1/tts", tags=["tts"])
    app.include_router(film.router, prefix="/api/v1/film", tags=["film"])
    app.include_router(ui_views.router, prefix="/api/v1/ui", tags=["ui"])
    app.include_router(performance_router.router)  # prefix="/api/performance" already set

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.1.0"}

    return app


app = create_app()
