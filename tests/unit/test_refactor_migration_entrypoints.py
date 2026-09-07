"""Boundary tests for staged large-module refactor entrypoints."""

from __future__ import annotations


def test_memory_facade_exports_existing_integration_types() -> None:
    from novel_forge.memory.facade import (
        MemoryContext as FacadeMemoryContext,
    )
    from novel_forge.memory.facade import (
        MemoryIntegrationConfig as FacadeMemoryIntegrationConfig,
    )
    from novel_forge.memory.integration import MemoryContext, MemoryIntegrationConfig
    from novel_forge.memory.integration_config import (
        MemoryIntegrationConfig as ConfigModuleMemoryIntegrationConfig,
    )

    assert FacadeMemoryContext is MemoryContext
    assert FacadeMemoryIntegrationConfig is MemoryIntegrationConfig
    assert ConfigModuleMemoryIntegrationConfig is MemoryIntegrationConfig


def test_memory_integration_config_from_settings_mapping() -> None:
    from types import SimpleNamespace

    from novel_forge.memory.integration_config import MemoryIntegrationConfig

    settings = SimpleNamespace(
        memory_semantic_search_enabled=False,
        memory_adaptive_compression_enabled=False,
        memory_motif_tracking_enabled=True,
        memory_motif_check_repetition=False,
        memory_critic_agent_enabled=True,
        memory_critic_agent_run_async=True,
    )

    config = MemoryIntegrationConfig.from_settings(settings)

    assert config.enable_semantic_search is False
    assert config.enable_adaptive_compression is False
    assert config.enable_motif_extraction is True
    assert config.enable_repetition_checking is False
    assert config.enable_critic_validation is True
    assert config.critic_run_async is True


def test_memory_context_public_service_properties_are_stable_facade() -> None:
    from novel_forge.memory.facade import MemoryContext

    ctx = MemoryContext()
    episodic = object()
    summary = object()
    compression = object()
    motif = object()
    critic = object()
    ctx._episodic_memory = episodic
    ctx._summary_service = summary
    ctx._compression_service = compression
    ctx._motif_tracker = motif
    ctx._critic_agent = critic

    assert ctx.episodic_memory is episodic
    assert ctx.summary_service is summary
    assert ctx.compression_service is compression
    assert ctx.motif_tracker is motif
    assert ctx.critic_agent is critic


def test_memory_integration_utils_match_legacy_private_entrypoints() -> None:
    from types import SimpleNamespace

    from novel_forge.memory import integration
    from novel_forge.memory.integration_utils import plan_field, plan_list_field

    plan = SimpleNamespace(scene_intents=[{"summary": "A"}, "B"], title="第1章")

    assert integration._plan_field is plan_field
    assert integration._plan_list_field is plan_list_field
    assert integration._plan_field(plan, "title") == "第1章"
    assert integration._plan_list_field(plan, "scene_intents") == [
        {"summary": "A"},
        {"summary": "B"},
    ]


def test_init_coherence_entrypoints_make_v2_gate_primary_and_v1_readiness_explicit() -> None:
    from novel_forge.pipeline.long.services.init import (
        init_coherence,
        init_coherence_entrypoints,
        init_coherence_v2,
    )

    assert init_coherence_entrypoints.run_init_coherence_v2_gate is (
        init_coherence_v2.run_init_coherence_v2_gate
    )
    assert init_coherence_entrypoints.refine_init_coherence_profile is (
        init_coherence_v2.refine_init_coherence_profile
    )
    assert init_coherence_entrypoints.build_init_readiness_report is (
        init_coherence.build_init_readiness_report
    )
    assert init_coherence_entrypoints.readiness_payload_allows is (
        init_coherence.readiness_payload_allows
    )
