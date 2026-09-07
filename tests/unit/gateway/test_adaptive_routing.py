"""Tests for adaptive routing audit (shadow mode)."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.gateway.adaptive_routing import (
    AdaptiveRoutingAudit,
    RoutingDecisionSource,
    audit_routing_decision,
)


class TestAdaptiveRoutingAudit:
    def test_off_mode_returns_none(self):
        settings = SimpleNamespace(long_adaptive_routing_mode="off")
        result = audit_routing_decision(
            task_type="REPAIR_CONTINUITY",
            resolved_provider="openai",
            resolved_model_id="gpt-4o",
            settings=settings,
        )
        assert result is None

    def test_shadow_mode_returns_audit(self):
        settings = SimpleNamespace(long_adaptive_routing_mode="shadow")
        result = audit_routing_decision(
            task_type="REPAIR_CONTINUITY",
            resolved_provider="openai",
            resolved_model_id="gpt-4o",
            settings=settings,
            severity="high",
            budget_pressure=0.3,
        )
        assert result is not None
        assert isinstance(result, AdaptiveRoutingAudit)
        assert result.task_type == "REPAIR_CONTINUITY"
        assert result.user_explicit_route == "openai:gpt-4o"
        assert result.adaptive_applied is True  # No change needed
        assert result.reason == "no_adaptive_change_needed"

    def test_high_pressure_low_severity_recommends_cheaper(self):
        settings = SimpleNamespace(long_adaptive_routing_mode="shadow")
        result = audit_routing_decision(
            task_type="REPAIR_CONTINUITY",
            resolved_provider="openai",
            resolved_model_id="gpt-4o",
            settings=settings,
            severity="low",
            budget_pressure=0.85,
            candidate_pool=("openai:gpt-4o", "deepseek:deepseek-chat"),
        )
        assert result is not None
        assert result.adaptive_recommended == "deepseek:deepseek-chat"
        assert result.adaptive_applied is False
        assert "budget_pressure" in result.reason

    def test_high_pressure_high_severity_keeps_route(self):
        settings = SimpleNamespace(long_adaptive_routing_mode="shadow")
        result = audit_routing_decision(
            task_type="REPAIR_CONTINUITY",
            resolved_provider="openai",
            resolved_model_id="gpt-4o",
            settings=settings,
            severity="critical",
            budget_pressure=0.95,
            candidate_pool=("openai:gpt-4o", "deepseek:deepseek-chat"),
        )
        assert result is not None
        assert result.adaptive_recommended == "openai:gpt-4o"
        assert result.adaptive_applied is True

    def test_enforce_mode_behaves_like_shadow(self):
        settings = SimpleNamespace(long_adaptive_routing_mode="enforce")
        result = audit_routing_decision(
            task_type="DRAFT_CHAPTER",
            resolved_provider="anthropic",
            resolved_model_id="claude-3",
            settings=settings,
        )
        assert result is not None
        assert result.adaptive_applied is True

    def test_empty_settings_defaults_off(self):
        settings = SimpleNamespace()
        result = audit_routing_decision(
            task_type="DRAFT_CHAPTER",
            resolved_provider="openai",
            resolved_model_id="gpt-4o",
            settings=settings,
        )
        assert result is None


class TestRoutingDecisionSource:
    def test_enum_values(self):
        assert RoutingDecisionSource.EXPLICIT.value == "explicit"
        assert RoutingDecisionSource.ADAPTIVE.value == "adaptive"
