"""Factory for creating ModelRouter and Adapters."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, cast

from novel_forge.core.config import Settings
from novel_forge.core.constants import ModelTier, TaskType
from novel_forge.core.local_model_resources import configure_local_model_resources
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.cache import CachePolicy
from novel_forge.gateway.circuit_breaker import CircuitBreaker
from novel_forge.gateway.dead_letter_queue import DeadLetterQueue
from novel_forge.gateway.embedding_config import is_embedding_model
from novel_forge.gateway.pricing import SpendingTracker
from novel_forge.gateway.profiles import (
    TONGYI_CODING_PLAN_BASE_URL,
    TONGYI_TOKEN_PLAN_BASE_URL,
    ModelProfile,
    ProfilesConfig,
    load_or_import_profiles,
)
from novel_forge.gateway.router import ModelRouter, TaskRouteOverride
from novel_forge.gateway.task_circuit_breaker import TaskTypeCircuitBreaker
from novel_forge.obs.logger import get_logger

_log = get_logger("gateway.factory")

_EDITORIAL_CONTRACT_PART_TASKS: tuple[TaskType, ...] = (
    TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES,
    TaskType.DERIVE_EDITORIAL_STRUCTURE,
    TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS,
    TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES,
)

_STRICT_JSON_CHILD_TASKS: dict[TaskType, tuple[TaskType, ...]] = {
    TaskType.INIT_STORY_BIBLE: (
        TaskType.INIT_STORY_CORE_PREMISE,
        TaskType.INIT_STORY_WORLD_RULES,
        TaskType.INIT_STORY_CONTINUITY_RULES,
        TaskType.INIT_STORY_THEMES_AND_SYMBOLS,
    ),
    TaskType.INIT_CHARACTER_BIBLE: (
        TaskType.INIT_CHARACTER_ROSTER,
        TaskType.INIT_CHARACTER_PROFILE_BATCH,
        TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
        TaskType.INIT_CHARACTER_ARC_PLAN,
    ),
    TaskType.REFINE_INIT_COHERENCE_PROFILE: (
        TaskType.INIT_COHERENCE_ONTOLOGY,
        TaskType.INIT_COHERENCE_EXTRACTION_GUIDE,
        TaskType.INIT_COHERENCE_CONFLICT_RULES,
        TaskType.INIT_COHERENCE_PAYOFF_RULES,
    ),
    TaskType.EXTRACT_CANON: (
        TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT,
        TaskType.EXTRACT_CANON_DELTA,
        TaskType.EXTRACT_CREATIVE_REPORT,
        TaskType.EXTRACT_CHARACTER_STATE_DELTAS,
        TaskType.EXTRACT_RELATIONSHIP_DELTAS,
        TaskType.EXTRACT_PLOT_THREAD_DELTAS,
    ),
    TaskType.BOOK_EDITORIAL_AUDIT: (
        TaskType.BOOK_EDITORIAL_STRUCTURE_AUDIT,
        TaskType.BOOK_EDITORIAL_VOICE_AUDIT,
        TaskType.BOOK_EDITORIAL_LANGUAGE_AUDIT,
        TaskType.BOOK_EDITORIAL_THEME_SYMBOL_AUDIT,
        TaskType.BOOK_EDITORIAL_ELEMENT_AUDIT,
    ),
    TaskType.BOOK_CONSISTENCY: (
        TaskType.BOOK_CONSISTENCY_NAMING,
        TaskType.BOOK_CONSISTENCY_TIMELINE,
        TaskType.BOOK_CONSISTENCY_WORLD_RULE,
        TaskType.BOOK_CONSISTENCY_CHARACTER_STATE,
        TaskType.BOOK_CONSISTENCY_PLOT_THREAD,
        TaskType.BOOK_CONSISTENCY_NARRATIVE_DRIFT,
    ),
    TaskType.PLAN_CHAPTER: (
        TaskType.PLAN_CHAPTER_SCENES,
        TaskType.VALIDATE_SCENE_PLAN,
    ),
}

# provider -> (module_path, class_name)
_ADAPTER_MODULES: dict[str, tuple[str, str]] = {
    "deepseek": ("novel_forge.gateway.adapters.deepseek", "DeepSeekAdapter"),
    "tongyi": ("novel_forge.gateway.adapters.tongyi", "TongyiAdapter"),
    "tongyi_coding": ("novel_forge.gateway.adapters.tongyi", "TongyiAdapter"),
    "tongyi_token_plan": ("novel_forge.gateway.adapters.tongyi", "TongyiAdapter"),
    "kimi": ("novel_forge.gateway.adapters.kimi", "KimiAdapter"),
    "mimo": ("novel_forge.gateway.adapters.mimo", "MiMoAdapter"),
    "openai": ("novel_forge.gateway.adapters.openai", "OpenAIAdapter"),
    "anthropic": ("novel_forge.gateway.adapters.anthropic", "AnthropicAdapter"),
    "tencent": ("novel_forge.gateway.adapters.tencent_hunyuan", "TencentHunyuanAdapter"),
    "ollama": ("novel_forge.gateway.adapters.ollama", "OllamaAdapter"),
    "minimax": ("novel_forge.gateway.adapters.minimax", "MiniMaxAdapter"),
    "siliconflow": ("novel_forge.gateway.adapters.siliconflow", "SiliconFlowAdapter"),
    "volcengine_ark": (
        "novel_forge.gateway.adapters.volcengine_ark",
        "VolcengineArkAdapter",
    ),
    "opencode": ("novel_forge.gateway.adapters.opencode", "OpenCodeAdapter"),
}


class TaskRoutingParseError(ValueError):
    """Raised when NOVEL_FORGE_TASK_ROUTING is malformed."""


def _apply_internal_task_route_defaults(
    task_providers: dict[TaskType, TaskRouteOverride] | None,
) -> dict[TaskType, TaskRouteOverride] | None:
    """Synchronize hidden internal routes from their visible parent flow."""
    if not task_providers:
        return task_providers

    resolved = dict(task_providers)
    outline_route = resolved.get(TaskType.PLAN_OUTLINE)
    if outline_route is not None:
        if TaskType.PLAN_OUTLINE_BATCH not in resolved:
            resolved[TaskType.PLAN_OUTLINE_BATCH] = TaskRouteOverride(
                provider=outline_route.provider,
                model_id=outline_route.model_id,
                thinking=outline_route.thinking,
                thinking_mode=outline_route.thinking_mode,
                multi_turn=False,
            )
        batch_route = resolved.get(TaskType.PLAN_OUTLINE_BATCH)
        if batch_route is not None and TaskType.PLAN_OUTLINE_CONTINUE not in resolved:
            resolved[TaskType.PLAN_OUTLINE_CONTINUE] = TaskRouteOverride(
                provider=batch_route.provider,
                model_id=batch_route.model_id,
                thinking=False,
                thinking_mode="off",
                multi_turn=outline_route.multi_turn,
            )

    # Compatibility default:
    # If element-progress arbiter is not explicitly routed, follow alignment route.
    # This keeps previous user expectation that both audits share the same model,
    # while still allowing the new task to be overridden independently in UI.
    if TaskType.ELEMENT_PROGRESS_ARBITER not in resolved and TaskType.CHECK_ALIGNMENT in resolved:
        alignment = resolved[TaskType.CHECK_ALIGNMENT]
        resolved[TaskType.ELEMENT_PROGRESS_ARBITER] = TaskRouteOverride(
            provider=alignment.provider,
            model_id=alignment.model_id,
            thinking=alignment.thinking,
            thinking_mode=alignment.thinking_mode,
            multi_turn=alignment.multi_turn,
        )

    # PROFILE_STRUCTURE is an internal sibling of PROFILE_STYLE.
    # If only PROFILE_STYLE is explicitly configured, inherit it to avoid
    # route drift that can make parallel style generation partially fail.
    if TaskType.PROFILE_STRUCTURE not in resolved and TaskType.PROFILE_STYLE in resolved:
        style_route = resolved[TaskType.PROFILE_STYLE]
        resolved[TaskType.PROFILE_STRUCTURE] = TaskRouteOverride(
            provider=style_route.provider,
            model_id=style_route.model_id,
            thinking=style_route.thinking,
            thinking_mode=style_route.thinking_mode,
            multi_turn=style_route.multi_turn,
        )

    # The editorial contract now runs as four smaller JSON-only subtasks. Inherit
    # the visible parent provider/model, but keep reasoning and multi-turn off so
    # providers do not spend output budget on hidden deliberation for strict JSON.
    editorial_route = resolved.get(TaskType.DERIVE_EDITORIAL_CONTRACT)
    if editorial_route is not None:
        for task_type in _EDITORIAL_CONTRACT_PART_TASKS:
            if task_type not in resolved:
                resolved[task_type] = TaskRouteOverride(
                    provider=editorial_route.provider,
                    model_id=editorial_route.model_id,
                    thinking=False,
                    thinking_mode="off",
                    multi_turn=False,
                )

    for parent_task, child_tasks in _STRICT_JSON_CHILD_TASKS.items():
        parent_route = resolved.get(parent_task)
        if parent_route is None:
            continue
        for task_type in child_tasks:
            if task_type not in resolved:
                resolved[task_type] = TaskRouteOverride(
                    provider=parent_route.provider,
                    model_id=parent_route.model_id,
                    thinking=False,
                    thinking_mode="off",
                    multi_turn=False,
                )

    draft_route = resolved.get(TaskType.DRAFT_CHAPTER)
    if draft_route is not None:
        for task_type in (TaskType.DRAFT_SCENE, TaskType.WAVE_CHAPTER):
            if task_type not in resolved:
                resolved[task_type] = TaskRouteOverride(
                    provider=draft_route.provider,
                    model_id=draft_route.model_id,
                    thinking=draft_route.thinking,
                    thinking_mode=draft_route.thinking_mode,
                    multi_turn=False,
                )
    return resolved


def _apply_internal_task_fallback_defaults(
    task_fallbacks: dict[TaskType, list[TaskRouteOverride]] | None,
) -> dict[TaskType, list[TaskRouteOverride]] | None:
    """Synchronize hidden internal fallback chains from visible parent flow."""
    if not task_fallbacks:
        return task_fallbacks

    def _clone_routes(routes: list[TaskRouteOverride]) -> list[TaskRouteOverride]:
        return [
            TaskRouteOverride(
                provider=route.provider,
                model_id=route.model_id,
                thinking=route.thinking,
                thinking_mode=route.thinking_mode,
                multi_turn=route.multi_turn,
            )
            for route in routes
        ]

    resolved: dict[TaskType, list[TaskRouteOverride]] = {
        task: _clone_routes(routes) for task, routes in task_fallbacks.items()
    }

    outline_fallbacks = resolved.get(TaskType.PLAN_OUTLINE)
    if outline_fallbacks and TaskType.PLAN_OUTLINE_BATCH not in resolved:
        resolved[TaskType.PLAN_OUTLINE_BATCH] = _clone_routes(outline_fallbacks)

    batch_fallbacks = resolved.get(TaskType.PLAN_OUTLINE_BATCH) or outline_fallbacks
    if batch_fallbacks and TaskType.PLAN_OUTLINE_CONTINUE not in resolved:
        resolved[TaskType.PLAN_OUTLINE_CONTINUE] = _clone_routes(batch_fallbacks)

    if TaskType.PROFILE_STRUCTURE not in resolved and TaskType.PROFILE_STYLE in resolved:
        resolved[TaskType.PROFILE_STRUCTURE] = _clone_routes(resolved[TaskType.PROFILE_STYLE])

    editorial_fallbacks = resolved.get(TaskType.DERIVE_EDITORIAL_CONTRACT)
    if editorial_fallbacks:
        for task_type in _EDITORIAL_CONTRACT_PART_TASKS:
            if task_type not in resolved:
                resolved[task_type] = [
                    TaskRouteOverride(
                        provider=route.provider,
                        model_id=route.model_id,
                        thinking=False,
                        thinking_mode="off",
                        multi_turn=False,
                    )
                    for route in editorial_fallbacks
                ]

    for parent_task, child_tasks in _STRICT_JSON_CHILD_TASKS.items():
        parent_fallbacks = resolved.get(parent_task)
        if not parent_fallbacks:
            continue
        for task_type in child_tasks:
            if task_type not in resolved:
                resolved[task_type] = [
                    TaskRouteOverride(
                        provider=route.provider,
                        model_id=route.model_id,
                        thinking=False,
                        thinking_mode="off",
                        multi_turn=False,
                    )
                    for route in parent_fallbacks
                ]

    draft_fallbacks = resolved.get(TaskType.DRAFT_CHAPTER)
    if draft_fallbacks:
        for task_type in (TaskType.DRAFT_SCENE, TaskType.WAVE_CHAPTER):
            if task_type not in resolved:
                resolved[task_type] = [
                    TaskRouteOverride(
                        provider=route.provider,
                        model_id=route.model_id,
                        thinking=route.thinking,
                        thinking_mode=route.thinking_mode,
                        multi_turn=False,
                    )
                    for route in draft_fallbacks
                ]

    return resolved


def _split_provider_model(spec: str) -> tuple[str, str | None, bool, bool]:
    """Split route spec into (provider, model_id, thinking, multi_turn).

    Supported forms:
    - ``provider``
    - ``provider:model``
    - ``provider:model,thinking,multi``

    Backward compatibility:
    - ``provider:model:thinking`` remains supported.
    - ``multi`` aliases: ``multi_turn``, ``multi-turn``, ``multiturn``.
    """

    def _normalize_feature(token: str) -> str | None:
        normalized = token.strip().lower().replace("-", "_")
        aliases = {
            "think": "thinking",
            "thinking": "thinking",
            "multi": "multi_turn",
            "multiturn": "multi_turn",
            "multi_turn": "multi_turn",
        }
        return aliases.get(normalized)

    text = spec.strip()
    if not text:
        return "", None, False, False

    # New-style capability list: provider:model,thinking,multi
    segments = [seg.strip() for seg in text.split(",") if seg.strip()]
    head = segments[0]
    parts = [part.strip() for part in head.split(":")]
    provider_name = parts[0].lower() if parts else ""
    model_name = (parts[1] or None) if len(parts) > 1 else None

    raw_features: list[str] = []
    # Backward-compatible suffixes: provider:model:thinking[:multi]
    if len(parts) > 2:
        raw_features.extend(parts[2:])
    if len(segments) > 1:
        raw_features.extend(segments[1:])

    features = {
        normalized
        for token in raw_features
        if (normalized := _normalize_feature(token)) is not None
    }
    return provider_name, model_name, ("thinking" in features), ("multi_turn" in features)


class ModelRouterBuilder:
    """Builder for constructing a ModelRouter with configured adapters."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        configure_local_model_resources(settings)
        self.adapters: dict[str, object] = {}
        self._spending_tracker = self._build_spending_tracker()

    def _build_spending_tracker(self) -> SpendingTracker | None:
        s = self.settings
        if s.budget_daily_usd <= 0 and s.budget_monthly_usd <= 0:
            return None
        return SpendingTracker(
            daily_limit=s.budget_daily_usd,
            monthly_limit=s.budget_monthly_usd,
            warn_threshold=s.budget_warn_threshold,
        )

    def _build_circuit_breakers(
        self, adapters: dict[str, object]
    ) -> dict[str, CircuitBreaker] | None:
        s = self.settings
        if not s.circuit_breaker_enabled:
            return None
        return {
            name: CircuitBreaker(
                provider=name,
                failure_threshold=s.circuit_breaker_threshold,
                recovery_timeout_s=s.circuit_breaker_recovery_s,
            )
            for name in adapters
        }

    def _build_task_circuit_breaker(self) -> TaskTypeCircuitBreaker | None:
        """Create one shared per-task breaker for the router's LLM services."""
        settings = self.settings
        if not settings.task_circuit_breaker_enabled:
            return None
        return TaskTypeCircuitBreaker(
            threshold=settings.task_circuit_breaker_threshold,
            cooldown_seconds=settings.task_circuit_breaker_recovery_s,
        )

    def _build_dlq(self) -> DeadLetterQueue | None:
        s = self.settings
        if s.dlq_max_entries <= 0:
            return None
        try:
            return DeadLetterQueue(
                storage_dir=s.storage_root,
                max_entries=s.dlq_max_entries,
            )
        except Exception as exc:
            _log.warning(
                "dlq_init_failed | storage_dir=%s | error=%s",
                s.storage_root,
                exc,
            )
            return None

    def _build_cache(self) -> CachePolicy | None:
        s = self.settings
        if not s.gateway_cache_enabled:
            return None
        return CachePolicy(
            enabled=True,
            telemetry_enabled=s.gateway_cache_hit_telemetry,
        )

    def _build_control_plane_observers(
        self,
        provider_names: Iterable[str],
    ) -> list[Callable[[str, dict[str, Any]], None]]:
        """Attach the optional shared health view without coupling routing to it."""

        if not self.settings.runtime_control_enabled:
            return []
        from novel_forge.control_plane.health import (  # noqa: PLC0415
            get_global_health_registry,
            make_router_health_observer,
        )

        registry = get_global_health_registry()
        for provider_name in provider_names:
            if provider_name:
                registry.register_provider(str(provider_name))
        return [make_router_health_observer(registry)]

    def build(self, *, mock: bool = False) -> ModelRouter:
        """Build the router, optionally forcing mock mode."""
        if mock:
            return self._build_mock()

        profile_build = self._build_from_profiles()
        if profile_build is not None:
            adapters, default_provider, tier_to_model, task_providers, profile_fallbacks = (
                profile_build
            )
            if adapters:
                task_providers = self._apply_repair_model_override(task_providers)
                env_fallbacks = self._parse_task_fallback_routing() or {}
                merged_fallbacks = dict(profile_fallbacks or {})
                merged_fallbacks.update(env_fallbacks)
                merged_fallbacks = _apply_internal_task_fallback_defaults(merged_fallbacks) or {}
                circuit_breakers = self._build_circuit_breakers(adapters)
                task_circuit_breaker = self._build_task_circuit_breaker()
                dlq = self._build_dlq()
                return ModelRouter(
                    adapters=adapters,  # type: ignore[arg-type]
                    tier_to_model=tier_to_model,
                    default_tier=self.settings.default_model_tier,
                    default_provider=default_provider,
                    task_providers=task_providers,
                    task_fallbacks=merged_fallbacks or None,
                    observers=self._build_control_plane_observers(adapters),
                    request_timeout_s=self.settings.api_call_timeout_s,
                    stream_idle_timeout_s=self.settings.stream_idle_timeout_s,
                    workflow_timeout_s=self.settings.workflow_timeout_s,
                    spending_tracker=self._spending_tracker,
                    circuit_breakers=circuit_breakers,
                    task_circuit_breaker=task_circuit_breaker,
                    dlq=dlq,
                    creative_temperature_jitter_enabled=(
                        self.settings.creative_temperature_jitter_enabled
                    ),
                    creative_temperature_jitter_up_delta=(
                        self.settings.creative_temperature_jitter_up_delta
                    ),
                    creative_temperature_jitter_down_delta=(
                        self.settings.creative_temperature_jitter_down_delta
                    ),
                    creative_temperature_jitter_scope=(
                        self.settings.creative_temperature_jitter_scope
                    ),
                    creative_temperature_jitter_custom_tasks=(
                        self.settings.creative_temperature_jitter_custom_tasks
                    ),
                    cache=self._build_cache(),
                )

        unregistered = self._register_real_adapters()

        # Fallback if no real adapters configured
        if not self.adapters:
            providers_str = ", ".join(f"{p}: {r}" for p, r in unregistered.items())
            _log.warning(
                "mock_fallback | unregistered_providers=[%s] | note=configure .env or model_profiles.json",
                providers_str,
            )
            return self._build_mock()

        # Determine default provider
        default_provider = self._determine_default_provider()

        # Parse task overrides
        task_providers = _apply_internal_task_route_defaults(self._parse_task_routing())
        task_providers = self._apply_repair_model_override(task_providers)

        circuit_breakers = self._build_circuit_breakers(self.adapters)
        task_circuit_breaker = self._build_task_circuit_breaker()
        dlq = self._build_dlq()

        return ModelRouter(
            adapters=self.adapters,  # type: ignore[arg-type]
            tier_to_model=self._build_tier_to_model(),
            default_tier=self.settings.default_model_tier,
            default_provider=default_provider,
            task_providers=task_providers,
            task_fallbacks=_apply_internal_task_fallback_defaults(
                self._parse_task_fallback_routing()
            ),
            observers=self._build_control_plane_observers(self.adapters),
            request_timeout_s=self.settings.api_call_timeout_s,
            stream_idle_timeout_s=self.settings.stream_idle_timeout_s,
            workflow_timeout_s=self.settings.workflow_timeout_s,
            spending_tracker=self._spending_tracker,
            circuit_breakers=circuit_breakers,
            task_circuit_breaker=task_circuit_breaker,
            dlq=dlq,
            creative_temperature_jitter_enabled=self.settings.creative_temperature_jitter_enabled,
            creative_temperature_jitter_up_delta=self.settings.creative_temperature_jitter_up_delta,
            creative_temperature_jitter_down_delta=self.settings.creative_temperature_jitter_down_delta,
            creative_temperature_jitter_scope=self.settings.creative_temperature_jitter_scope,
            creative_temperature_jitter_custom_tasks=(
                self.settings.creative_temperature_jitter_custom_tasks
            ),
            cache=self._build_cache(),
        )

    def _build_mock(self) -> ModelRouter:
        """Create a router with only the MockAdapter."""
        return ModelRouter(
            adapters={"mock": MockAdapter()},
            tier_to_model=self._build_tier_to_model(),
            default_tier=self.settings.default_model_tier,
            default_provider="mock",
            observers=self._build_control_plane_observers(("mock",)),
            request_timeout_s=self.settings.api_call_timeout_s,
            stream_idle_timeout_s=self.settings.stream_idle_timeout_s,
            workflow_timeout_s=self.settings.workflow_timeout_s,
            circuit_breakers=None,
            task_circuit_breaker=None,
            dlq=None,
            creative_temperature_jitter_enabled=self.settings.creative_temperature_jitter_enabled,
            creative_temperature_jitter_up_delta=self.settings.creative_temperature_jitter_up_delta,
            creative_temperature_jitter_down_delta=self.settings.creative_temperature_jitter_down_delta,
            creative_temperature_jitter_scope=self.settings.creative_temperature_jitter_scope,
            creative_temperature_jitter_custom_tasks=(
                self.settings.creative_temperature_jitter_custom_tasks
            ),
            cache=self._build_cache(),
        )

    def _build_tier_to_model(self) -> dict[ModelTier, str]:
        return {
            ModelTier.PREMIUM: self.settings.premium_model,
            ModelTier.STANDARD: self.settings.standard_model,
            ModelTier.BUDGET: self.settings.budget_model,
        }

    def _build_from_profiles(
        self,
    ) -> (
        tuple[
            dict[str, object],
            str,
            dict[ModelTier, str],
            dict[TaskType, TaskRouteOverride] | None,
            dict[TaskType, list[TaskRouteOverride]] | None,
        ]
        | None
    ):
        config = load_or_import_profiles(self.settings)
        if not config.profiles:
            return None

        adapters: dict[str, object] = {}
        provider_aliases: dict[str, str] = {}
        preferred_profile_by_provider: dict[str, str] = {}

        default_profile = (
            config.get_profile(config.default_profile_id) if config.default_profile_id else None
        )
        if default_profile and default_profile.is_key_configured:
            preferred_profile_by_provider[default_profile.provider] = default_profile.profile_id

        for profile in config.profiles:
            if not profile.is_key_configured:
                continue
            adapter = self._create_profile_adapter(profile)
            if adapter is None:
                continue
            adapters[profile.profile_id] = adapter
            provider_aliases.setdefault(profile.provider, profile.profile_id)

        if not adapters:
            return None

        for provider, profile_id in provider_aliases.items():
            preferred = preferred_profile_by_provider.get(provider, profile_id)
            adapter = adapters.get(preferred) or adapters[profile_id]
            adapters.setdefault(provider, adapter)

        profile_routes = self._routes_from_profiles(config)
        profile_fallback_routes = self._fallback_routes_from_profiles(config)
        default_provider = self._determine_profile_default_provider(config, adapters)
        tier_to_model = self._build_profile_tier_to_model(config, adapters, default_provider)
        return (
            adapters,
            default_provider,
            tier_to_model,
            _apply_internal_task_route_defaults(profile_routes or None),
            _apply_internal_task_fallback_defaults(profile_fallback_routes or None),
        )

    def _determine_profile_default_provider(
        self,
        config: ProfilesConfig,
        adapters: dict[str, object],
    ) -> str:
        default_profile = (
            config.get_profile(config.default_profile_id) if config.default_profile_id else None
        )
        if default_profile and default_profile.provider in adapters:
            return default_profile.provider
        for profile in config.profiles:
            if profile.provider in adapters:
                return profile.provider
        return next(iter(adapters))

    def _build_profile_tier_to_model(
        self,
        config: ProfilesConfig,
        adapters: dict[str, object],
        default_provider: str,
    ) -> dict[ModelTier, str]:
        """Use the chosen default profile model as the unrouted-task fallback."""
        default_profile = (
            config.get_profile(config.default_profile_id) if config.default_profile_id else None
        )
        default_model = ""
        if default_profile and default_profile.provider == default_provider:
            default_model = (default_profile.model_id or "").strip()
        if not default_model:
            adapter = adapters.get(default_provider)
            default_model = str(getattr(adapter, "default_model", "") or "").strip()
        if not default_model:
            default_model = self.settings.standard_model or "gpt-4o-mini"
        return {
            ModelTier.PREMIUM: default_model,
            ModelTier.STANDARD: default_model,
            ModelTier.BUDGET: default_model,
        }

    def _create_profile_adapter(self, profile: ModelProfile) -> object | None:
        provider = profile.provider.strip().lower()
        if provider == "custom":
            if not profile.base_url.strip():
                _log.warning(
                    "custom_profile_base_url_missing | profile_id=%s",
                    profile.profile_id,
                )
                return None
            return self._build_adapter(
                "novel_forge.gateway.adapters.openai_compat",
                "OpenAICompatibleAdapter",
                profile.api_key,
                profile.base_url,
                provider,
                profile.model_id,
                connect_timeout_s=self.settings.api_connect_timeout_s,
                read_timeout_s=self.settings.api_call_timeout_s + 30.0,
            )
        entry = _ADAPTER_MODULES.get(provider)
        if entry is None:
            _log.warning(
                "unknown_profile_provider | profile_id=%s | provider=%s",
                profile.profile_id,
                provider,
            )
            return None
        module_path, class_name = entry
        timeout_kwargs = {
            "connect_timeout_s": self.settings.api_connect_timeout_s,
            "read_timeout_s": self.settings.api_call_timeout_s + 30.0,
        }
        if provider == "anthropic":
            if profile.base_url:
                _log.warning(
                    "anthropic_profile_base_url_ignored | profile_id=%s | base_url=%s",
                    profile.profile_id,
                    profile.base_url,
                )
            return self._build_adapter(
                module_path,
                class_name,
                profile.api_key,
                default_model=profile.model_id or "claude-sonnet-4-6",
                **timeout_kwargs,
            )
        if provider == "ollama":
            return self._build_adapter(
                module_path,
                class_name,
                base_url=profile.base_url or "http://localhost:11434/v1",
                default_model=profile.model_id or "llama3.2",
                resource_wait_timeout_s=self.settings.local_model_resource_wait_timeout_s,
                **timeout_kwargs,
            )
        tongyi_plan_defaults = {
            "tongyi_coding": (TONGYI_CODING_PLAN_BASE_URL, "qwen3-coder-plus"),
            "tongyi_token_plan": (TONGYI_TOKEN_PLAN_BASE_URL, "qwen3.8-max"),
        }
        plan_defaults = tongyi_plan_defaults.get(provider)
        if plan_defaults is not None:
            default_base_url, default_model = plan_defaults
            return self._build_adapter(
                module_path,
                class_name,
                profile.api_key,
                base_url=profile.base_url or default_base_url,
                default_model=profile.model_id or default_model,
                **timeout_kwargs,
            )
        return self._build_adapter(
            module_path,
            class_name,
            profile.api_key,
            base_url=profile.base_url or ("" if provider == "openai" else None),
            default_model=profile.model_id or ("gpt-4o-mini" if provider == "openai" else None),
            **timeout_kwargs,
        )

    def _build_adapter(
        self,
        module_path: str,
        class_name: str,
        *args: object,
        **kwargs: object,
    ) -> object | None:
        from novel_forge.common.adapter_loader import load_adapter_class  # noqa: PLC0415

        def _on_load_error(mod: str, cls: str, exc: BaseException) -> None:
            _log.warning(
                "profile_adapter_init_failed | module=%s | class=%s | error=%s",
                mod,
                cls,
                str(exc),
            )

        adapter_cls = load_adapter_class(module_path, class_name, on_error=_on_load_error)
        if adapter_cls is None:
            return None
        try:
            filtered_kwargs = {
                key: value for key, value in kwargs.items() if value not in (None, "")
            }
            return cast(Callable[..., object], adapter_cls)(*args, **filtered_kwargs)
        except Exception as exc:
            _log.warning(
                "profile_adapter_init_failed | module=%s | class=%s | error=%s",
                module_path,
                class_name,
                str(exc),
            )
            return None

    def _routes_from_profiles(self, config: ProfilesConfig) -> dict[TaskType, TaskRouteOverride]:
        result: dict[TaskType, TaskRouteOverride] = {}
        for task_name, entry in config.routes.items():
            try:
                task_type = TaskType(task_name)
            except ValueError:
                continue
            profile = config.get_profile(entry.profile_id)
            if profile is None or not profile.is_key_configured:
                continue
            result[task_type] = TaskRouteOverride(
                provider=profile.profile_id,
                model_id=profile.model_id or None,
                thinking=entry.thinking,
                thinking_mode=entry.thinking_mode,
                multi_turn=entry.multi_turn,
            )
        return result

    def _fallback_routes_from_profiles(
        self,
        config: ProfilesConfig,
    ) -> dict[TaskType, list[TaskRouteOverride]]:
        result: dict[TaskType, list[TaskRouteOverride]] = {}
        for task_name, entries in config.fallback_routes.items():
            try:
                task_type = TaskType(task_name)
            except ValueError:
                continue
            parsed_entries: list[TaskRouteOverride] = []
            for entry in entries:
                profile = config.get_profile(entry.profile_id)
                if profile is None or not profile.is_key_configured:
                    continue
                # Skip embedding-only models: they are registered as generation
                # fallback entries but cannot produce text (OpenAI-compat mode
                # rejects them with 404/Unsupported model).
                if is_embedding_model(profile.provider, profile.model_id):
                    continue
                parsed_entries.append(
                    TaskRouteOverride(
                        provider=profile.profile_id,
                        model_id=profile.model_id or None,
                        thinking=entry.thinking,
                        thinking_mode=entry.thinking_mode,
                        multi_turn=entry.multi_turn,
                    )
                )
            if parsed_entries:
                result[task_type] = parsed_entries[:3]
        return result

    def _register_real_adapters(self) -> dict[str, str]:
        """Register adapters based on available API keys.

        Returns:
            Dictionary mapping unregistered provider names to the reason they were skipped.
        """
        s = self.settings
        unregistered: dict[str, str] = {}
        timeout_kwargs = {
            "connect_timeout_s": s.api_connect_timeout_s,
            "read_timeout_s": s.api_call_timeout_s + 30.0,
        }
        _entries: list[tuple[str, str | None, str, dict[str, object]]] = [
            ("deepseek", s.deepseek_api_key, "sk-...", {}),
            ("tongyi", s.tongyi_api_key, "sk-...", {}),
            (
                "tongyi_coding",
                s.tongyi_coding_api_key,
                "sk-...",
                {
                    "base_url": TONGYI_CODING_PLAN_BASE_URL,
                    "default_model": "qwen3-coder-plus",
                },
            ),
            (
                "tongyi_token_plan",
                s.tongyi_token_plan_api_key,
                "sk-sp-...",
                {
                    "base_url": TONGYI_TOKEN_PLAN_BASE_URL,
                    "default_model": "qwen3.8-max",
                },
            ),
            ("kimi", s.kimi_api_key, "sk-...", {}),
            ("mimo", s.mimo_api_key, "", {}),
            (
                "openai",
                s.openai_api_key,
                "sk-...",
                {"base_url": "", "default_model": s.standard_model or "gpt-4o-mini"},
            ),
            (
                "anthropic",
                s.anthropic_api_key,
                "sk-ant-...",
                {"default_model": "claude-sonnet-4-6"},
            ),
            ("tencent", s.tencent_api_key, "sk-...", {"default_model": "hy3"}),
            (
                "minimax",
                s.minimax_api_key,
                "sk-...",
                {"default_model": "MiniMax-M2.7-highspeed"},
            ),
            (
                "siliconflow",
                s.siliconflow_api_key,
                "sk-...",
                {
                    "base_url": "https://api.siliconflow.cn/v1",
                    "default_model": "deepseek-ai/DeepSeek-V3.2",
                },
            ),
            (
                "volcengine_ark",
                s.volcengine_ark_api_key,
                "sk-...",
                {
                    "base_url": s.volcengine_ark_base_url
                    or "https://ark.cn-beijing.volces.com/api/plan/v3",
                    "default_model": s.volcengine_ark_default_model
                    or "doubao-seed-2-0-lite-260215",
                },
            ),
            (
                "opencode",
                s.opencode_api_key,
                "sk-...",
                {
                    "base_url": "https://opencode.ai/zen/go/v1",
                    "default_model": "deepseek-v4-flash",
                },
            ),
            # Ollama doesn't require API key - always available if service is running
            (
                "ollama",
                None,
                "",
                {
                    "base_url": s.ollama_base_url or "http://localhost:11434/v1",
                    "default_model": s.ollama_model or "llama3.2",
                    "resource_wait_timeout_s": s.local_model_resource_wait_timeout_s,
                },
            ),
        ]
        for name, api_key, sentinel, extra_kwargs in _entries:
            # Ollama doesn't require API key - always try to register if service might be running
            if name == "ollama":
                module_path, class_name = _ADAPTER_MODULES[name]
                adapter = self._build_adapter(
                    module_path, class_name, **extra_kwargs, **timeout_kwargs
                )
                if adapter is not None:
                    self.adapters[name] = adapter
                    _log.info(
                        "ollama_adapter_registered | base_url=%s | default_model=%s",
                        extra_kwargs.get("base_url"),
                        extra_kwargs.get("default_model"),
                    )
                else:
                    unregistered[name] = "adapter_init_failed"
            elif api_key and api_key != sentinel:
                module_path, class_name = _ADAPTER_MODULES[name]
                adapter = self._build_adapter(
                    module_path, class_name, api_key, **extra_kwargs, **timeout_kwargs
                )
                if adapter is not None:
                    self.adapters[name] = adapter
                else:
                    unregistered[name] = "adapter_init_failed"
            else:
                if name != "ollama":
                    unregistered[name] = "missing_api_key"
        return unregistered

    def _determine_default_provider(
        self, *, adapters_override: dict[str, object] | None = None
    ) -> str:
        """Select default provider preference or fallback."""
        adapters = adapters_override or self.adapters
        configured = (self.settings.default_provider or "").strip()
        if configured:
            provider_name, model_hint, _, _ = _split_provider_model(configured)
            if model_hint:
                _log.warning(
                    "default_provider_model_ignored | provider=%s | model=%s | "
                    "note=use NOVEL_FORGE_TASK_ROUTING to pin models per task",
                    provider_name,
                    model_hint,
                )
            if provider_name and provider_name in adapters:
                return provider_name
            if provider_name:
                _log.warning(
                    "default_provider_not_registered | configured=%s | available=%s",
                    provider_name,
                    sorted(adapters),
                )
        return next(iter(adapters))

    def _parse_task_routing(self) -> dict[TaskType, TaskRouteOverride] | None:
        """Parse the JSON routing string from settings."""
        if not self.settings.task_routing:
            return None
        return parse_task_routing(self.settings.task_routing)

    def _parse_task_fallback_routing(self) -> dict[TaskType, list[TaskRouteOverride]] | None:
        """Parse fallback routing JSON string from settings."""
        if not self.settings.task_fallback_routing:
            return None
        return parse_task_fallback_routing(self.settings.task_fallback_routing)

    def _apply_repair_model_override(
        self,
        task_providers: dict[TaskType, TaskRouteOverride] | None,
    ) -> dict[TaskType, TaskRouteOverride] | None:
        """Apply repair_model setting as a fallback override for repair tasks.

        Only fills in tasks NOT already covered by explicit task_routing entries,
        so user's explicit overrides always win.
        """
        repair_spec = (self.settings.repair_model or "").strip()
        if not repair_spec:
            return task_providers
        provider, model_id, thinking, multi_turn = _split_provider_model(repair_spec)
        if not provider:
            return task_providers
        override = TaskRouteOverride(
            provider=provider,
            model_id=model_id,
            thinking=thinking,
            multi_turn=multi_turn,
        )
        result: dict[TaskType, TaskRouteOverride] = dict(task_providers or {})
        result.setdefault(TaskType.REPAIR_CONTINUITY, override)
        result.setdefault(TaskType.EDIT_CHAPTER, override)
        return result


def parse_task_routing(routing_json: str) -> dict[TaskType, TaskRouteOverride]:
    """Parse a task routing JSON string into overrides map."""
    import json

    result: dict[TaskType, TaskRouteOverride] = {}
    if not routing_json:
        return result

    try:
        mapping = json.loads(routing_json)
    except json.JSONDecodeError as exc:
        raise TaskRoutingParseError(
            "Invalid NOVEL_FORGE_TASK_ROUTING JSON at "
            f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(mapping, dict):
        raise TaskRoutingParseError(
            "Invalid NOVEL_FORGE_TASK_ROUTING: root value must be a JSON object"
        )

    for task_name, route_spec in mapping.items():
        try:
            task_type = TaskType(task_name)
        except ValueError:
            continue

        provider: str = ""
        model_id: str | None = None
        thinking: bool = False
        multi_turn: bool = False
        if isinstance(route_spec, str):
            provider, model_id, thinking, multi_turn = _split_provider_model(route_spec)
        elif isinstance(route_spec, dict):
            raw_provider = route_spec.get("provider")
            if isinstance(raw_provider, str):
                provider = raw_provider.strip().lower()
            raw_model = route_spec.get("model_id", route_spec.get("model"))
            if isinstance(raw_model, str):
                model_id = raw_model.strip() or None
            thinking = bool(route_spec.get("thinking", False))
            multi_turn = bool(
                route_spec.get(
                    "multi_turn",
                    route_spec.get("multi", route_spec.get("multiTurn", False)),
                )
            )
        if not provider:
            continue
        result[task_type] = TaskRouteOverride(
            provider=provider,
            model_id=model_id,
            thinking=thinking,
            multi_turn=multi_turn,
        )

    return result


def parse_task_fallback_routing(
    routing_json: str,
) -> dict[TaskType, list[TaskRouteOverride]]:
    """Parse fallback routing JSON into task -> ordered fallback routes.

    Expected shape:
    {
      "draft_chapter": ["provider:model", "provider2:model2,thinking"],
      "repair_continuity": [{"provider":"tongyi","model_id":"qwen-max"}]
    }
    """
    import json

    result: dict[TaskType, list[TaskRouteOverride]] = {}
    if not routing_json:
        return result

    try:
        mapping = json.loads(routing_json)
    except json.JSONDecodeError as exc:
        raise TaskRoutingParseError(
            "Invalid NOVEL_FORGE_TASK_FALLBACK_ROUTING JSON at "
            f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(mapping, dict):
        raise TaskRoutingParseError(
            "Invalid NOVEL_FORGE_TASK_FALLBACK_ROUTING: root value must be a JSON object"
        )

    for task_name, route_specs in mapping.items():
        try:
            task_type = TaskType(task_name)
        except ValueError:
            continue
        if not isinstance(route_specs, list):
            continue
        parsed_routes: list[TaskRouteOverride] = []
        for item in route_specs:
            provider: str = ""
            model_id: str | None = None
            thinking = False
            multi_turn = False
            if isinstance(item, str):
                provider, model_id, thinking, multi_turn = _split_provider_model(item)
            elif isinstance(item, dict):
                raw_provider = item.get("provider")
                if isinstance(raw_provider, str):
                    provider = raw_provider.strip().lower()
                raw_model = item.get("model_id", item.get("model"))
                if isinstance(raw_model, str):
                    model_id = raw_model.strip() or None
                thinking = bool(item.get("thinking", False))
                multi_turn = bool(
                    item.get(
                        "multi_turn",
                        item.get("multi", item.get("multiTurn", False)),
                    )
                )
            if not provider:
                continue
            parsed_routes.append(
                TaskRouteOverride(
                    provider=provider,
                    model_id=model_id,
                    thinking=thinking,
                    multi_turn=multi_turn,
                )
            )
        if parsed_routes:
            result[task_type] = parsed_routes[:3]
    return result
