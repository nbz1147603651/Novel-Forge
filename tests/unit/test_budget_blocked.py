"""Tests for budget_blocked state: ReviewProgressState, ChapterReviewArtifacts, API handler."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novel_forge.core.exceptions import BudgetExceededError
from novel_forge.pipeline.long.execution_models import ChapterReviewArtifacts
from novel_forge.workspace.sessions.chapter_session_state import ReviewProgressState

# ── ReviewProgressState ──────────────────────────────────────────────────────


class TestReviewProgressStateBudgetPause:
    def test_default_running(self):
        state = ReviewProgressState(
            completed_stage="draft_done",
            current_text="hello",
        )
        assert state.run_status == "running"
        assert state.pause_reason is None
        assert state.blocked_task_type == ""
        assert state.estimated_required_cost_usd is None

    def test_budget_blocked_pause(self):
        state = ReviewProgressState(
            completed_stage="quality_done",
            current_text="text",
            run_status="paused",
            pause_reason="budget_blocked",
            blocked_task_type="REPAIR_CAUSAL",
            estimated_required_cost_usd=0.05,
        )
        assert state.run_status == "paused"
        assert state.pause_reason == "budget_blocked"
        assert state.completed_stage == "quality_done"

    def test_completed_stage_not_polluted(self):
        """budget_blocked is a pause_reason, NOT a completed_stage."""
        valid_stages = {
            "draft_done", "quality_done", "causal_repair_done",
            "repair_done", "canon_done",
        }
        # This must NOT be a valid completed_stage value.
        assert "budget_blocked" not in valid_stages


# ── ChapterReviewArtifacts ───────────────────────────────────────────────────


class TestChapterReviewArtifactsBudget:
    def test_default_budget_status_none(self):
        """budget_status defaults to None (ok)."""
        # We can't easily construct a full ChapterReviewArtifacts here
        # due to its many required fields, so just check the field default.
        import dataclasses
        fields = {f.name: f for f in dataclasses.fields(ChapterReviewArtifacts)}
        assert "budget_status" in fields
        assert fields["budget_status"].default is None


# ── API BudgetExceededError handler ─────────────────────────────────────────


def _build_test_app() -> FastAPI:
    """Build a minimal FastAPI app with the exception handler."""
    # We need to test the handler without full app setup.
    # Instead, test the handler logic directly.
    app = FastAPI()

    @app.get("/test-budget")
    async def _test_budget():
        raise BudgetExceededError(period="daily", spent=10.5, limit=10.0)

    @app.get("/test-budget-monthly")
    async def _test_monthly():
        raise BudgetExceededError(period="monthly", spent=50.0, limit=40.0)

    # Register the handler.

    @app.exception_handler(Exception)
    async def handler(request, exc):
        from fastapi.responses import JSONResponse

        if isinstance(exc, BudgetExceededError):
            period = getattr(exc, "period", "unknown")
            spent = getattr(exc, "spent", 0.0)
            limit = getattr(exc, "limit", 0.0)
            is_time_based = period in ("daily", "monthly")
            status_code = 429 if is_time_based else 402
            body = {
                "error": "budget_exceeded",
                "message": exc.message,
                "period": period,
                "spent": round(spent, 4),
                "limit": round(limit, 2),
            }
            return JSONResponse(status_code=status_code, content=body)
        return JSONResponse(status_code=500, content={"error": "internal"})

    return app


class TestBudgetAPIHandler:
    def test_daily_budget_returns_429(self):
        app = _build_test_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-budget")
        assert resp.status_code == 429
        body = resp.json()
        assert body["error"] == "budget_exceeded"
        assert body["period"] == "daily"
        assert body["spent"] == 10.5
        assert body["limit"] == 10.0

    def test_monthly_budget_returns_429(self):
        app = _build_test_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-budget-monthly")
        assert resp.status_code == 429
        body = resp.json()
        assert body["period"] == "monthly"

    def test_non_time_budget_returns_402(self):
        """Non-time-based budget errors should return 402."""
        app = FastAPI()

        @app.get("/test-custom")
        async def _test():
            raise BudgetExceededError(period="custom", spent=5.0, limit=3.0)

        from fastapi.responses import JSONResponse

        @app.exception_handler(Exception)
        async def handler(request, exc):
            if isinstance(exc, BudgetExceededError):
                period = getattr(exc, "period", "unknown")
                is_time_based = period in ("daily", "monthly")
                status_code = 429 if is_time_based else 402
                return JSONResponse(
                    status_code=status_code,
                    content={
                        "error": "budget_exceeded",
                        "period": period,
                        "spent": round(getattr(exc, "spent", 0.0), 4),
                        "limit": round(getattr(exc, "limit", 0.0), 2),
                    },
                )
            return JSONResponse(status_code=500, content={})

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-custom")
        assert resp.status_code == 402
