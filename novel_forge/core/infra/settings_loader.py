"""Unified configuration loader with multi-source priority.

Supports loading settings from (highest to lowest priority):

1. **cli_args** – runtime arguments passed directly to the loader.
2. **env_vars** – ``NOVEL_FORGE_*`` environment variables.
3. **model_profiles** – ``model_profiles.json`` (task routing & defaults).
4. **cli_json** – ``novel_forge.cli.json`` (``defaults`` section).
5. **dotenv** – ``.env`` file.
6. **defaults** – hard-coded fallback values.

The loader is designed to be **self-contained** so it can eventually replace
``core.config.Settings`` without circular dependencies.  Hot-reload support is
provided via file-mtime tracking for Desktop runtime adjustments.

Example::

    loader = UnifiedSettingsLoader(cli_args={"log_level": "DEBUG"})
    value = loader.load("log_level")          # -> "DEBUG"
    all_cfg = loader.load_all()               # merged dict
    settings = loader.validate()              # UnifiedSettings (Pydantic v2)

"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from novel_forge.common.constants import ModelTier

logger = logging.getLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "storage_root": "./data",
    "default_model_tier": "standard",
    "premium_model": "gpt-4o",
    "standard_model": "gpt-4o-mini",
    "budget_model": "gpt-3.5-turbo",
    "api_trusted_hosts": "localhost,127.0.0.1,::1,testserver",
    "api_local_only": True,
    "api_access_token": "",
    "api_auth_exempt_paths": "/health",
    "openai_api_key": "",
    "anthropic_api_key": "",
    "deepseek_api_key": "",
    "tongyi_api_key": "",
    "tongyi_coding_api_key": "",
    "tongyi_token_plan_api_key": "",
    "kimi_api_key": "",
    "tencent_api_key": "",
    "minimax_api_key": "",
    "opencode_api_key": "",
    "default_provider": "",
    "task_routing": {},
    "task_fallback_routing": {},
    "repair_model": "",
    "llm_format_retry_attempts": 2,
    "llm_format_retry_temperature": 0.0,
    "llm_format_retry_raw_char_limit": 6000,
    "creative_temperature_jitter_enabled": False,
    "creative_temperature_jitter_up_delta": 0.1,
    "creative_temperature_jitter_down_delta": 0.3,
    "creative_temperature_jitter_scope": "recommended",
    "creative_temperature_jitter_custom_tasks": "",
    "llm_format_repair_enabled": True,
    "llm_format_repair_model": "",
    "llm_format_repair_max_tokens": 4096,
    "llm_format_repair_raw_char_limit": 60000,
    "split_tasks_enabled": True,
    "init_fragment_max_parallel": 4,
    "init_character_profile_parallel_min_roster": 11,
    "init_character_profile_batch_size": 5,
    "outline_batch_size": 0,
    "init_outline_beats_min": 4,
    "init_outline_beats_max": 8,
    "init_outline_main_plot_points_min": 2,
    "init_outline_main_plot_points_max": 4,
    "init_outline_subplot_points_max": 3,
    "init_outline_element_focus_max": 3,
    "init_outline_expected_payoffs_min": 1,
    "init_outline_expected_payoffs_max": 3,
    "chapter_contract_batch_size": 0,
    "canon_extract_max_parallel": 4,
    "book_audit_max_parallel": 3,
    "patch_first_repair": True,
    "log_level": "INFO",
    "api_connect_timeout_s": 30.0,
    "api_call_timeout_s": 900.0,
    # Commonly accessed temperatures
    "temp_spec_enrich": 0.7,
    "temp_beats": 0.7,
    "temp_draft": 0.8,
    "temp_edit": 0.5,
    "temp_evaluate": 0.3,
    "temp_init_entity_registry": 0.2,
    "temp_init_narrative_contract": 0.25,
    "temp_plan_chapter_contracts": 0.25,
    "temp_synthesize_init_research_dossier": 0.2,
    "temp_ground_outline_research": 0.15,
    "temp_adjudicate_contract_coherence": 0.1,
    "temp_plan_outline": 0.7,
    "temp_plan_chapter": 0.4,
    "temp_draft_chapter": 1.0,
    "temp_wave_chapter": 0.7,
    "temp_edit_chapter": 0.5,
    "temp_extract_canon": 0.3,
    "temp_check_alignment": 0.2,
    "temp_check_chapter": 0.2,
    "temp_bridge_chapter": 0.3,
    "temp_check_continuity": 0.2,
    "temp_validate_causal": 0.2,
    "temp_repair_continuity": 0.4,
    "temp_volume_audit": 0.3,
    "temp_enrich_character": 0.7,
    "temp_adjudicate_character_introduction": 0.1,
    "temp_generate_config": 0.9,
    "temp_polish_config": 0.75,
    "temp_adjust_outline": 0.7,
    "temp_context_compress": 0.2,
    "temp_plot_guard_judge": 0.2,
    "temp_macro_guard": 0.2,
    "extract_canon_max_existing_thread_ids": 30,
    "extract_canon_max_prior_relationships": 12,
    "extract_canon_recent_character_window_chapters": 5,
    "extract_canon_max_prior_characters": 12,
    "extract_canon_max_prior_plot_threads": 10,
    "extract_canon_prior_plot_thread_summary_chars": 60,
    "extract_canon_output_base_tokens": 4096,
    "extract_canon_output_tokens_per_character": 450,
    "extract_canon_output_tokens_per_2500_chars": 768,
    "extract_canon_output_max_tokens": 12288,
    "extract_canon_abort_on_severe_damage": True,
    "extract_canon_severe_damage_missing_section_threshold": 2,
    "extract_canon_max_character_state_deltas": 8,
    "extract_canon_max_relationship_deltas": 8,
    "extract_canon_max_plot_thread_deltas": 8,
    "extract_canon_max_exit_state_characters": 6,
    "motif_prompt_token_budget": 500,
    "motif_prompt_max_items": 8,
    "motif_forbidden_max_items": 3,
    "motif_repetition_lookback_chapters": 5,
    "motif_repetition_recent_gap_chapters": 2,
    "memory_style_rule_tracking_enabled": True,
    "style_rule_repetition_lookback_chapters": 5,
    "style_rule_repetition_recent_gap_chapters": 2,
    "motif_dormant_callback_min_chapters": 20,
    "motif_auto_forget_ephemeral_enabled": True,
    "motif_ephemeral_forget_after_chapters": 12,
    "motif_ephemeral_max_occurrences": 1,
    "motif_ephemeral_importance_threshold_pct": 35,
    "plot_progression_strictness": "block",
    "long_future_leak_guard_enabled": True,
    "long_contract_audit_enabled": True,
    "long_kb_audit_reuse_enabled": True,
    "long_contract_audit_strictness": "block",
    "long_eval_reuse_enabled": True,
    "long_causal_high_score_skip_enabled": True,
    "long_causal_skip_threshold": 9.5,
    "expression_channel_detection_enabled": True,
    "expression_channel_cooldown_chapters": 3,
    "arc_liveness_window": 6,
    "stage_visibility_debug_enabled": True,
    "long_macro_guard_enabled": True,
    "long_macro_guard_interval": 5,
    "long_macro_guard_max_adjustments_per_book": 3,
    "long_macro_guard_min_chapters_between_adjustments": 8,
    "long_macro_guard_cooldown_chapters": 5,
    "long_macro_guard_drift_threshold_warning": 0.3,
    "long_macro_guard_drift_threshold_alert": 0.5,
    "long_macro_guard_drift_threshold_critical": 0.7,
    "long_macro_guard_auto_apply_hint": True,
    "long_macro_guard_critical_blocks_archive": True,
    "long_macro_guard_max_audit_chapters": 5,
    "long_macro_guard_max_tokens": 2048,
    "long_upstream_compass_enabled": True,
    "long_upstream_compass_blocking": True,
    "long_upstream_compass_min_scenes": 1,
    "long_upstream_compass_word_budget_tolerance": 0.25,
    "long_scene_draft_max_parallel": 5,
    "long_context_compress_quality_min_score": 0.62,
    "long_context_compress_llm_verify_enabled": True,
    "long_context_compress_llm_verify_margin": 0.12,
    "long_context_compress_llm_verify_max_items": 3,
    "long_book_audit_prompt_char_budget": 48000,
    "long_book_audit_max_chapters_per_batch": 12,
}


class UnifiedSettings(BaseModel):
    """Common settings validated via Pydantic v2.

    Fields cover the most frequently accessed configuration keys:
    API keys, model routing, temperatures, and storage root.
    ``extra="allow"`` permits additional keys (e.g. less-common temps)
    to pass through validation when loaded via :meth:`UnifiedSettingsLoader.validate`.
    """

    storage_root: Path = Field(default=Path("./data"))
    default_model_tier: ModelTier = ModelTier.STANDARD
    premium_model: str = "gpt-4o"
    standard_model: str = "gpt-4o-mini"
    budget_model: str = "gpt-3.5-turbo"

    api_trusted_hosts: str | list[str] = "localhost,127.0.0.1,::1,testserver"
    api_local_only: bool = True
    api_access_token: str = ""
    api_auth_exempt_paths: str | list[str] = "/health"

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    tongyi_api_key: str = ""
    tongyi_coding_api_key: str = ""
    tongyi_token_plan_api_key: str = ""
    kimi_api_key: str = ""
    tencent_api_key: str = ""
    minimax_api_key: str = ""
    opencode_api_key: str = ""

    default_provider: str = ""
    task_routing: dict[str, str] = Field(default_factory=dict)
    task_fallback_routing: dict[str, list[str]] = Field(default_factory=dict)
    repair_model: str = ""
    llm_format_retry_attempts: int = 2
    llm_format_retry_temperature: float = 0.0
    llm_format_retry_raw_char_limit: int = 6000
    creative_temperature_jitter_enabled: bool = False
    creative_temperature_jitter_up_delta: float = 0.1
    creative_temperature_jitter_down_delta: float = 0.3
    creative_temperature_jitter_scope: str = "recommended"
    creative_temperature_jitter_custom_tasks: str = ""
    llm_format_repair_enabled: bool = True
    llm_format_repair_model: str = ""
    llm_format_repair_max_tokens: int = 4096
    llm_format_repair_raw_char_limit: int = 60000
    split_tasks_enabled: bool = True
    init_fragment_max_parallel: int = 4
    init_kb_phase_c_parallel: bool = True
    init_entity_reference_max_parallel: int = 3
    init_character_profile_parallel_min_roster: int = 11
    init_character_profile_batch_size: int = 5
    outline_batch_size: int = 0
    init_outline_beats_min: int = 4
    init_outline_beats_max: int = 8
    init_outline_main_plot_points_min: int = 2
    init_outline_main_plot_points_max: int = 4
    init_outline_subplot_points_max: int = 3
    init_outline_element_focus_max: int = 3
    init_outline_expected_payoffs_min: int = 1
    init_outline_expected_payoffs_max: int = 3
    chapter_contract_batch_size: int = 0
    canon_extract_max_parallel: int = 4
    book_audit_max_parallel: int = 3
    patch_first_repair: bool = True
    log_level: str = "INFO"
    api_connect_timeout_s: float = 30.0
    api_call_timeout_s: float = 900.0

    temp_spec_enrich: float = 0.7
    temp_beats: float = 0.7
    temp_draft: float = 0.8
    temp_edit: float = 0.5
    temp_evaluate: float = 0.3
    temp_init_entity_registry: float = 0.2
    temp_init_narrative_contract: float = 0.25
    temp_plan_chapter_contracts: float = 0.25
    temp_init_story_bible: float = 0.7
    temp_init_character_bible: float = 0.7
    temp_plan_outline_batch: float = 0.7
    temp_init_knowledge_boundaries: float = 0.3
    temp_synthesize_init_research_dossier: float = 0.2
    temp_ground_outline_research: float = 0.15
    temp_adjudicate_contract_coherence: float = 0.1
    temp_plan_outline: float = 0.7
    temp_plan_chapter: float = 0.4
    temp_draft_chapter: float = 1.0
    temp_wave_chapter: float = 0.7
    temp_edit_chapter: float = 0.5
    temp_extract_canon: float = 0.3
    temp_check_alignment: float = 0.2
    temp_check_chapter: float = 0.2
    temp_bridge_chapter: float = 0.3
    temp_check_continuity: float = 0.2
    temp_validate_causal: float = 0.2
    temp_repair_continuity: float = 0.4
    temp_volume_audit: float = 0.3
    temp_enrich_character: float = 0.7
    temp_adjudicate_character_introduction: float = 0.1
    temp_generate_config: float = 0.9
    temp_polish_config: float = 0.75
    temp_adjust_outline: float = 0.7
    temp_context_compress: float = 0.2
    temp_plot_guard_judge: float = 0.2
    extract_canon_max_existing_thread_ids: int = 30
    extract_canon_max_prior_relationships: int = 12
    extract_canon_recent_character_window_chapters: int = 5
    extract_canon_max_prior_characters: int = 12
    extract_canon_max_prior_plot_threads: int = 10
    extract_canon_prior_plot_thread_summary_chars: int = 60
    extract_canon_output_base_tokens: int = 4096
    extract_canon_output_tokens_per_character: int = 450
    extract_canon_output_tokens_per_2500_chars: int = 768
    extract_canon_output_max_tokens: int = 12288
    extract_canon_abort_on_severe_damage: bool = True
    extract_canon_severe_damage_missing_section_threshold: int = 2
    extract_canon_max_character_state_deltas: int = 8
    extract_canon_max_relationship_deltas: int = 8
    extract_canon_max_plot_thread_deltas: int = 8
    extract_canon_max_exit_state_characters: int = 6
    motif_prompt_token_budget: int = 500
    motif_prompt_max_items: int = 8
    motif_forbidden_max_items: int = 3
    motif_repetition_lookback_chapters: int = 5
    motif_repetition_recent_gap_chapters: int = 2
    memory_style_rule_tracking_enabled: bool = True
    style_rule_repetition_lookback_chapters: int = 5
    style_rule_repetition_recent_gap_chapters: int = 2
    motif_dormant_callback_min_chapters: int = 20
    motif_auto_forget_ephemeral_enabled: bool = True
    motif_ephemeral_forget_after_chapters: int = 12
    motif_ephemeral_max_occurrences: int = 1
    motif_ephemeral_importance_threshold_pct: int = 35
    plot_progression_strictness: str = "block"
    long_future_leak_guard_enabled: bool = True
    long_contract_audit_enabled: bool = True
    long_kb_audit_reuse_enabled: bool = True
    long_contract_audit_strictness: str = "block"
    long_eval_reuse_enabled: bool = True
    long_causal_high_score_skip_enabled: bool = True
    long_causal_skip_threshold: float = 9.5
    expression_channel_detection_enabled: bool = True
    expression_channel_cooldown_chapters: int = 3
    arc_liveness_window: int = 6
    stage_visibility_debug_enabled: bool = True

    model_config = {"extra": "allow"}

    @field_validator("storage_root", mode="before")
    @classmethod
    def _coerce_storage_root(cls, v: Any) -> Any:
        if isinstance(v, str):
            return Path(v)
        return v


def _get_mtime(path: Path) -> float | None:
    """Return the last-modified timestamp for *path*, or ``None`` on error."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _file_fingerprint(path: Path) -> dict[str, Any]:
    """Return a stable fingerprint payload for a file-backed config source."""
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def _stable_config_hash(payload: Any) -> str:
    """Hash a JSON-ish payload, tolerating Path/Enum values via ``str``."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse_env_file(path: Path) -> dict[str, str]:
    """Read a ``KEY=VALUE`` file and return a flat dict (comments ignored)."""
    result: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return result
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _coerce_string(value: str) -> Any:
    """Best-effort type coercion for env-style string values.

    Order: bool → int → float → JSON → plain string.
    """
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if value.startswith(("{", "[")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _strip_prefix_and_coerce(pairs: dict[str, str], prefix: str) -> dict[str, Any]:
    """Keep keys that start with *prefix*, strip it, lower-case, and coerce values."""
    result: dict[str, Any] = {}
    prefix_len = len(prefix)
    for key, value in pairs.items():
        if not key.startswith(prefix):
            continue
        value = _strip_matching_quotes(value.strip()).strip()
        if not value:
            # Skip empty / whitespace-only values so defaults take over.
            # This prevents bool-typed fields from rejecting ``""`` as
            # unparseable (pydantic v2 raises ValidationError).
            continue
        clean_key = key[prefix_len:].lower()
        result[clean_key] = _coerce_string(value)
    return result


class UnifiedSettingsLoader:
    """Load settings from multiple sources with explicit priority.

    Each source is loaded lazily and cached.  Call :meth:`reload` to invalidate
    caches (e.g. after an external process modifies ``.env``).  File-based
    sources also expose :meth:`has_changed` for lightweight hot-reload checks.
    """

    PRIORITY: list[str] = [
        "cli_args",
        "env_vars",
        "model_profiles",
        "cli_json",
        "dotenv",
        "defaults",
    ]

    def __init__(
        self,
        *,
        cli_args: dict[str, Any] | None = None,
        env_prefix: str = "NOVEL_FORGE_",
        dotenv_path: Path | str | None = None,
        cli_json_path: Path | str | None = None,
        model_profiles_path: Path | str | None = None,
        defaults: dict[str, Any] | None = None,
    ) -> None:
        """Initialise the loader.

        Args:
            cli_args: Explicit CLI overrides (highest priority).
            env_prefix: Prefix used to filter environment variables.
            dotenv_path: Path to ``.env`` (auto-discovered if omitted).
            cli_json_path: Path to ``novel_forge.cli.json`` (default ``./novel_forge.cli.json``).
            model_profiles_path: Path to ``model_profiles.json`` (auto-discovered if omitted).
            defaults: Fallback values (lowest priority).  When ``None`` the built-in
                defaults aligned with ``core.config.Settings`` are used.
        """
        self._cli_args: dict[str, Any] = dict(cli_args) if cli_args else {}
        self._env_prefix: str = env_prefix
        self._dotenv_path: Path = Path(dotenv_path) if dotenv_path else self._auto_dotenv_path()
        self._cli_json_path: Path = (
            Path(cli_json_path) if cli_json_path else Path("novel_forge.cli.json")
        )
        self._model_profiles_path: Path = (
            Path(model_profiles_path) if model_profiles_path else self._auto_profiles_path()
        )
        self._defaults: dict[str, Any] = (
            dict(defaults) if defaults is not None else _DEFAULTS.copy()
        )

        self._cache: dict[str, dict[str, Any]] = {}
        self._file_mtims: dict[str, float | None] = {}
        self._refresh_file_mtims()

    @staticmethod
    def _auto_dotenv_path() -> Path:
        """Return the same dotenv path that desktop settings persistence writes."""
        # Packaged desktop builds must read the per-user runtime config, never
        # the read-only app bundle or its launch directory.
        from novel_forge.core.config import get_writable_env_path

        writable_env = get_writable_env_path()
        if getattr(sys, "frozen", False) or writable_env.is_file():
            return writable_env

        # In source mode keep the .env.example fallback for first launch.
        pkg_root = Path(__file__).resolve().parent.parent.parent.parent
        example = pkg_root / ".env.example"
        return example if example.is_file() else writable_env

    @staticmethod
    def _auto_profiles_path() -> Path:
        """Return ``model_profiles.json`` in the runtime config directory."""
        # Import locally to avoid circular dependencies at module load time.
        from novel_forge.core.config import get_runtime_config_dir

        return get_runtime_config_dir() / "model_profiles.json"

    def _refresh_file_mtims(self) -> None:
        """Snapshot current mtimes for all file-backed sources."""
        self._file_mtims = {
            "dotenv": _get_mtime(self._dotenv_path),
            "cli_json": _get_mtime(self._cli_json_path),
            "model_profiles": _get_mtime(self._model_profiles_path),
        }

    def _load_source(self, name: str) -> dict[str, Any]:
        """Return cached or freshly loaded data for *name*."""
        if name in self._cache:
            return self._cache[name]
        loader_name = f"_load_{name}"
        loader = getattr(self, loader_name, None)
        if loader is None:
            raise ValueError(f"Unknown source: {name!r} (no {loader_name} method)")
        result: dict[str, Any] = loader()
        self._cache[name] = result
        return result

    def _load_defaults(self) -> dict[str, Any]:
        return self._defaults.copy()

    def _load_dotenv(self) -> dict[str, Any]:
        if not self._dotenv_path.is_file():
            return {}
        pairs = _parse_env_file(self._dotenv_path)
        return _strip_prefix_and_coerce(pairs, self._env_prefix)

    def _load_env_vars(self) -> dict[str, Any]:
        return _strip_prefix_and_coerce(dict(os.environ), self._env_prefix)

    def _load_cli_json(self) -> dict[str, Any]:
        if not self._cli_json_path.is_file():
            return {}
        try:
            data = json.loads(self._cli_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("cli_json_load_failed | path=%s | error=%s", self._cli_json_path, exc)
            return {}
        if not isinstance(data, dict):
            return {}
        defaults_section = data.get("defaults")
        if isinstance(defaults_section, dict):
            return dict(defaults_section)
        return {}

    def _load_model_profiles(self) -> dict[str, Any]:
        if not self._model_profiles_path.is_file():
            return {}
        try:
            data = json.loads(self._model_profiles_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug(
                "model_profiles_load_failed | path=%s | error=%s", self._model_profiles_path, exc
            )
            return {}
        if not isinstance(data, dict):
            return {}

        result: dict[str, Any] = {}

        default_profile_id = data.get("default_profile_id", "")
        if default_profile_id:
            for profile in data.get("profiles", []):
                if isinstance(profile, dict) and profile.get("profile_id") == default_profile_id:
                    result["default_provider"] = profile.get("provider", "")
                    break

        routes = data.get("routes", {})
        if isinstance(routes, dict):
            routing: dict[str, str] = {}
            for task_key, route in routes.items():
                if not isinstance(route, dict):
                    continue
                provider = route.get("provider", "")
                model_id = route.get("model_id", "")
                if provider and model_id:
                    spec = f"{provider}:{model_id}"
                    features: list[str] = []
                    if route.get("thinking"):
                        features.append("thinking")
                    if route.get("multi_turn"):
                        features.append("multi_turn")
                    if features:
                        spec += "," + ",".join(features)
                    routing[str(task_key)] = spec
                elif provider:
                    routing[str(task_key)] = provider
            result["task_routing"] = routing

        fallback_routes = data.get("fallback_routes", {})
        if isinstance(fallback_routes, dict):
            fb: dict[str, list[str]] = {}
            for task_key, entries in fallback_routes.items():
                if not isinstance(entries, list):
                    continue
                specs: list[str] = []
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    provider = entry.get("provider", "")
                    model_id = entry.get("model_id", "")
                    if provider and model_id:
                        spec = f"{provider}:{model_id}"
                        features = []
                        if entry.get("thinking"):
                            features.append("thinking")
                        if entry.get("multi_turn"):
                            features.append("multi_turn")
                        if features:
                            spec += "," + ",".join(features)
                        specs.append(spec)
                    elif provider:
                        specs.append(provider)
                if specs:
                    fb[str(task_key)] = specs
            result["task_fallback_routing"] = fb

        return result

    def _load_cli_args(self) -> dict[str, Any]:
        return self._cli_args.copy()

    def load(self, key: str) -> Any:
        """Return the first value found for *key* walking priority highest → lowest.

        Raises:
            KeyError: When *key* is not present in any source.
        """
        for source_name in self.PRIORITY:
            source = self._load_source(source_name)
            if key in source:
                return source[key]
        raise KeyError(key)

    def load_all(self) -> dict[str, Any]:
        """Return a single dict merged from all sources.

        Higher-priority sources overwrite lower-priority ones (shallow merge).
        """
        merged: dict[str, Any] = {}
        for source_name in reversed(self.PRIORITY):
            merged.update(self._load_source(source_name))
        return merged

    def fingerprint(self) -> str:
        """Return a stable fingerprint for the current source state.

        This includes current environment values, CLI overrides, custom defaults,
        and file-backed source stamps.  It is intentionally independent from
        ``load_all()`` caches so callers can cheaply decide whether a cached
        ``Settings`` object is stale.
        """
        return _stable_config_hash(
            {
                "cli_args": self._cli_args,
                "env_vars": self._load_env_vars(),
                "dotenv": _file_fingerprint(self._dotenv_path),
                "cli_json": _file_fingerprint(self._cli_json_path),
                "model_profiles": _file_fingerprint(self._model_profiles_path),
                "defaults": self._defaults,
            }
        )

    def reload(self) -> None:
        """Invalidate all caches and refresh file mtimes."""
        self._cache.clear()
        self._refresh_file_mtims()

    def has_changed(self) -> bool:
        """Return ``True`` if any file-backed source has been modified since last reload."""
        for name, path in (
            ("dotenv", self._dotenv_path),
            ("cli_json", self._cli_json_path),
            ("model_profiles", self._model_profiles_path),
        ):
            current = _get_mtime(path)
            if current != self._file_mtims.get(name):
                return True
        return False

    def validate(self) -> UnifiedSettings:
        """Validate the merged configuration through Pydantic v2.

        Returns:
            A :class:`UnifiedSettings` instance with typed fields and coercion.
        """
        return UnifiedSettings(**self.load_all())
