"""Performance API routes — health check, metrics, and cache management."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from novel_forge.core.infra.advanced_cache import cache_manager
from novel_forge.core.infra.async_task_processor import (
    async_task_processor_initialized,
    get_async_task_processor,
)
from novel_forge.core.infra.performance_monitor import (
    get_performance_monitor,
    performance_context,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/performance", tags=["performance"])

# Performance monitor instance
perf_monitor = get_performance_monitor()


class HealthCheckResponse(BaseModel):
    """Health check response model."""

    status: str
    timestamp: datetime
    uptime: float
    version: str
    services: dict[str, str]
    processor_initialized: bool = False


class PerformanceMetricsResponse(BaseModel):
    """Performance metrics response model."""

    timestamp: datetime
    metrics: dict[str, Any]
    cache_stats: dict[str, Any]
    task_stats: dict[str, Any]


@router.get("/health", response_model=HealthCheckResponse)
async def health_check(request: Request) -> HealthCheckResponse:
    """Health check endpoint with performance metrics."""
    start_time = time.time()

    # Perform basic health checks
    services_status: dict[str, str] = {}

    # Check cache connectivity
    try:
        cache_health = cache_manager.get_stats()
        services_status["cache"] = "healthy" if cache_health else "unhealthy"
    except Exception as e:
        services_status["cache"] = f"error: {str(e)}"

    # Check task processor
    try:
        task_stats = await get_async_task_processor().get_stats()
        services_status["task_processor"] = "healthy" if task_stats else "unhealthy"
    except Exception as e:
        services_status["task_processor"] = f"error: {str(e)}"

    # Calculate uptime
    app_start_time = float(getattr(request.app.state, "start_time", start_time))
    uptime = time.time() - app_start_time

    # Determine overall status
    processor_init = async_task_processor_initialized()
    overall_status = "healthy"
    if any("error" in str(status).lower() for status in services_status.values()):
        overall_status = "degraded"
    if not processor_init:
        overall_status = "unhealthy"
        services_status["task_processor"] = "not_initialized"

    return HealthCheckResponse(
        status=overall_status,
        timestamp=datetime.now(),
        uptime=uptime,
        version=str(getattr(request.app, "version", "")),
        services=services_status,
        processor_initialized=processor_init,
    )


@router.get("/metrics", response_model=PerformanceMetricsResponse)
async def get_performance_metrics() -> PerformanceMetricsResponse:
    """Get comprehensive performance metrics."""
    with performance_context("get_performance_metrics"):
        # Get system metrics
        system_stats = perf_monitor.get_current_system_stats()

        # Get cache metrics
        cache_stats = cache_manager.get_stats()

        # Get task processor metrics
        task_stats = await get_async_task_processor().get_stats()

        # Combine all metrics
        combined_metrics = {
            "system": system_stats,
            "cache": cache_stats,
            "tasks": task_stats,
            "timestamp": time.time(),
        }

        return PerformanceMetricsResponse(
            timestamp=datetime.now(),
            metrics=combined_metrics,
            cache_stats=cache_stats,
            task_stats=task_stats,
        )


@router.get("/cache/stats")
async def get_cache_stats() -> dict[str, Any]:
    """Get cache statistics."""
    with performance_context("get_cache_stats"):
        return cast(dict[str, Any], cache_manager.get_stats())


@router.post("/cache/clear/{cache_name}")
async def clear_cache(cache_name: str) -> dict[str, str]:
    """Clear a specific cache."""
    with performance_context("clear_cache"):
        if cache_manager.invalidate_cache(cache_name):
            return {"status": "success", "message": f"Cache '{cache_name}' cleared"}
        else:
            raise HTTPException(status_code=404, detail=f"Cache '{cache_name}' not found")
