"""Application configuration via pydantic-settings."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import ENV_FILE_SENTINEL, DotenvType

from novel_forge.core.constants import ModelTier

LONG_TOTAL_REPAIR_ROUNDS_CAP_DEFAULT = 5


def _find_env_file() -> str:
    """Return '.env' if it exists, otherwise fall back to '.env.example'.

    Resolves relative to the package root directory (two levels up from this
    file) so the lookup is independent of the current working directory.
    """
    if getattr(sys, "frozen", False):
        return str(get_writable_env_path())

    _pkg_root = Path(__file__).resolve().parent.parent.parent
    if (_pkg_root / ".env").is_file():
        return str(_pkg_root / ".env")
    if (_pkg_root / ".env.example").is_file():
        return str(_pkg_root / ".env.example")
    # Fall back to CWD-based .env as last resort
    return ".env"


def _windows_drive_capacity_bytes(root: str) -> int:
    """Return total capacity (bytes) of a Windows drive root like ``D:\\``."""
    try:
        import ctypes

        total_bytes = ctypes.c_ulonglong(0)
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(root),
            None,
            ctypes.byref(total_bytes),
            None,
        )
        if ok:
            return int(total_bytes.value)
    except Exception:
        return 0
    return 0


def _is_writable_dir(path: Path) -> bool:
    """Best-effort writability check for a directory path."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _pick_windows_data_root() -> Path:
    """Pick a writable Windows data root, preferring the largest non-C fixed drive."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        bitmask = int(kernel32.GetLogicalDrives())
        fixed_roots: list[str] = []
        for i in range(26):
            if not (bitmask & (1 << i)):
                continue
            letter = chr(ord("A") + i)
            root = f"{letter}:\\"
            drive_type = int(kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)))
            if drive_type == 3:  # DRIVE_FIXED
                fixed_roots.append(root)

        if fixed_roots:
            non_c = [r for r in fixed_roots if not r.upper().startswith("C:")]
            pool = non_c if non_c else fixed_roots
            sorted_roots = sorted(pool, key=_windows_drive_capacity_bytes, reverse=True)
            for root in sorted_roots:
                candidate = Path(root) / "Novel Forge" / "data"
                if _is_writable_dir(candidate):
                    return candidate
    except Exception:
        pass

    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Novel Forge" / "data"
    return Path.home() / "AppData" / "Roaming" / "Novel Forge" / "data"


def _default_storage_root() -> Path:
    """Return a writable default storage root.

    - In source/CLI mode: keep historical ``./data`` for backward compatibility.
    - In frozen desktop app mode: use per-user writable application data folder.
    """
    if not getattr(sys, "frozen", False):
        return Path("./data")

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Novel Forge" / "data"
    if sys.platform == "win32":
        return _pick_windows_data_root()

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / "novel-forge" / "data"
    return Path.home() / ".local" / "share" / "novel-forge" / "data"


def get_runtime_config_dir() -> Path:
    """Return config directory used by runtime persistence.

    Source/CLI mode keeps current workspace behavior (``.``).
    Frozen desktop mode stores config under a per-user writable directory.
    """
    if not getattr(sys, "frozen", False):
        return Path(".")
    return _default_storage_root().parent


def get_application_data_dir() -> Path:
    """Return the per-user home for reusable application resources.

    Unlike runtime configuration, this location must never default into the
    source checkout: model weights and shared media can be many gigabytes.
    ``NOVEL_FORGE_APPLICATION_DATA_ROOT`` is an escape hatch for an external
    disk or an organization-managed application-data location.
    """
    configured = os.environ.get("NOVEL_FORGE_APPLICATION_DATA_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    if getattr(sys, "frozen", False):
        return _default_storage_root().parent
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Novel Forge"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return (
            Path(appdata) / "Novel Forge"
            if appdata
            else Path.home() / "AppData" / "Roaming" / "Novel Forge"
        )
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / "novel-forge"
    return Path.home() / ".local" / "share" / "novel-forge"


def get_application_models_dir() -> Path:
    """Return the application-owned root for reusable local model weights.

    Source/CLI mode keeps the shared model library under the repository's
    gitignored ``models/`` directory.  Frozen desktop builds use per-user
    application data instead.  Neither mode ever stores models under an
    individual writing project.
    """
    if not getattr(sys, "frozen", False):
        return Path("models")

    return get_application_data_dir() / "models"


def get_application_assets_dir() -> Path:
    """Return the application-owned root for cross-project reusable assets."""

    return get_application_data_dir() / "assets"


def _default_stable_audio_models_dir() -> str:
    """Stable Audio's provider-specific subdirectory in the app model library."""

    return str(get_application_models_dir() / "audio" / "stable-audio")


def _default_audio_models_root() -> str:
    """Root of the movable, application-owned audio model repository."""

    return str(get_application_models_dir() / "audio")


def _default_tts_voice_store(provider: str) -> str:
    """Return the application-owned store for reusable local voice profiles."""

    return str(get_application_assets_dir() / "tts-voices" / provider)


def _default_openvoice_checkpoint_dir() -> str:
    """Return the application-owned location for OpenVoice model weights."""

    return str(get_application_models_dir() / "openvoice" / "checkpoints_v2")


def get_writable_env_path() -> Path:
    """Return writable .env path used by desktop settings persistence."""
    if getattr(sys, "frozen", False):
        return get_runtime_config_dir() / ".env"
    # Keep desktop writes aligned with both Settings._find_env_file() and the
    # unified loader.  A relative CWD path could write one .env and later load
    # a different package-root .env after launching the app from another cwd.
    return Path(__file__).resolve().parent.parent.parent / ".env"


_DESKTOP_CHOICE_LABEL_ALIASES: dict[str, dict[str, Any]] = {
    "creative_temperature_jitter_scope": {
        "推荐创意任务": "recommended",
        "章节核心": "chapter_core",
        "初始化 + 章节": "init_and_chapter",
        "自定义": "custom",
    },
    "init_continuity_time_notation_profile": {
        "自动（推荐）": "auto",
        "传统时辰刻度（中文古风）": "traditional_cn",
        "本地常规时间表达": "locale_default",
    },
    "plot_progression_strictness": {
        "阻断": "block",
        "只警告": "warn",
        "严格": "strict",
    },
    "long_contract_audit_strictness": {
        "阻断": "block",
        "只警告": "warn",
        "严格": "strict",
    },
    "long_word_count_archive_gate_enabled": {
        "开启": True,
        "开启（推荐）": True,
        "关闭": False,
    },
    "long_wave_word_count_policy": {
        "继承归档字数闸门": "inherit",
        "继承归档字数闸门（推荐）": "inherit",
        "始终执行": "enforce",
        "只警告": "warn",
    },
    "long_reading_power_archive_policy": {
        "仅报告与下章提示": "off",
        "极低分阻断（推荐）": "floor_only",
        "极低分 + 核心高危阻断": "floor_or_core_high",
    },
    "long_guard_archive_policy": {
        "仅告警记录（推荐）": "warn",
        "可行动违约阻断": "block_actionable",
    },
    "long_book_audit_default_mode": {
        "全文深审（推荐）": "full_text",
        "摘要审计（更快）": "summary",
    },
    "long_book_audit_location_strictness": {
        "严格": "strict",
        "严格（优先精确到段）": "strict",
        "平衡": "balanced",
        "平衡（推荐）": "balanced",
        "宽松": "loose",
        "宽松（覆盖更多）": "loose",
    },
    "causal_validation_fail_mode": {
        "告警并标记未知（Recommended）": "warn_unknown",
        "失败时放行": "fail_open",
    },
    "recheck_strategy": {
        "目标点验 + 全局高危守卫（推荐）": "targeted_with_global_guard",
        "严格目标点验（更快）": "strict_targeted",
    },
    "repair_always_reaudit": {
        "关闭": False,
        "关闭（AI 无改动时跳过重审，节省 token）": False,
        "开启": True,
        "开启（始终重审核，确认问题是否真正解决）": True,
    },
    "repair_control_mode": {
        "全手动": "manual",
        "AI 伴随": "ai_assisted",
        "AI 伴随（推荐）": "ai_assisted",
        "AI 全自动": "ai_auto",
    },
}

_DESKTOP_CHOICE_PREFIX_VALUES: dict[str, tuple[str, ...]] = {
    "memory_vector_store_backend": ("zvec", "in_memory"),
    "pronoun_autofix_mode": ("off", "pov_only", "pov_or_many"),
}


def _normalize_desktop_choice_label(field_name: str, value: Any) -> Any:
    """Map historical desktop combo display labels back to config values."""
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw:
        return value

    aliases = _DESKTOP_CHOICE_LABEL_ALIASES.get(field_name, {})
    if raw in aliases:
        return aliases[raw]
    for label, normalized in aliases.items():
        suffix = raw[len(label) :].strip() if raw.startswith(label) else ""
        if suffix.startswith(("(", "（")):
            return normalized

    for choice in sorted(_DESKTOP_CHOICE_PREFIX_VALUES.get(field_name, ()), key=len, reverse=True):
        if raw == choice:
            return raw
        suffix = raw[len(choice) :].strip() if raw.startswith(choice) else ""
        if suffix.startswith(("(", "（")):
            return choice

    return value


class Settings(BaseSettings):
    """Global application settings, loaded from env / .env."""

    model_config = SettingsConfigDict(
        env_prefix="NOVEL_FORGE_",
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    def __init__(
        self,
        _env_file: DotenvType | None = ENV_FILE_SENTINEL,
        **values: Any,
    ) -> None:
        """Expose pydantic-settings private init kwargs to static type checkers."""

        super().__init__(_env_file=_env_file, **values)

    @model_validator(mode="before")
    @classmethod
    def _normalize_desktop_choice_labels(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        for field_name in set(_DESKTOP_CHOICE_LABEL_ALIASES) | set(_DESKTOP_CHOICE_PREFIX_VALUES):
            if field_name in normalized:
                normalized[field_name] = _normalize_desktop_choice_label(
                    field_name,
                    normalized[field_name],
                )
        return normalized

    # ── Storage ────────────────────────────────────────
    storage_root: Path = Field(
        default_factory=_default_storage_root, description="Root directory for project data"
    )
    storage_cache_enabled: bool = True
    storage_cache_max_entries: int = Field(default=256, ge=16, le=4096)
    tts_voice_catalog_cache_ttl_hours: int = Field(
        default=24,
        ge=1,
        le=720,
        description=(
            "供应商音色目录的磁盘缓存 TTL（小时）。目录变更后最长 24h 内"
            "无需网络即可完成团队构建；网络失败时读缓存兜底。"
        ),
    )

    # ── Deployment mode & multi-tenancy (extension points) ────────────────
    deployment_mode: str = Field(
        default="local",
        description=(
            "Deployment topology: 'local' (single-user desktop/CLI, default) or "
            "'cloud' (multi-user SaaS). Cloud mode enables tenant isolation and "
            "expects an external orchestration backend (e.g. Temporal)."
        ),
    )
    tenant_id: str = Field(
        default="",
        description=(
            "Tenant identifier for multi-user deployments. Empty string means "
            "single-user local mode (no isolation). When set, project data is "
            "namespaced under {storage_root}/{tenant_id}/."
        ),
    )

    # ── API boundary ───────────────────────────────────
    api_trusted_hosts: str | list[str] = Field(
        default="localhost,127.0.0.1,::1,testserver",
        description="Comma-separated trusted Host headers. Use '*' only for explicit deployments.",
    )
    api_local_only: bool = Field(
        default=True,
        description="Reject non-loopback API clients unless explicitly disabled for deployment.",
    )
    api_access_token: str = Field(
        default="",
        description="Optional Bearer token required for non-exempt API routes.",
    )
    api_auth_exempt_paths: str | list[str] = Field(
        default="/health",
        description="Comma-separated exact paths or prefix patterns ending with '*' exempt from API token auth.",
    )
    api_cors_allow_origins: str = Field(
        default=(
            "http://localhost:1420,http://127.0.0.1:1420,"
            "tauri://localhost,http://tauri.localhost,https://tauri.localhost"
        ),
        description=(
            "Comma-separated allowed CORS origins for React dev and packaged Tauri clients. "
            "Set to '*' only for explicit deployments. Empty string disables CORS middleware."
        ),
    )

    # ── Model defaults ─────────────────────────────────
    default_model_tier: ModelTier = ModelTier.STANDARD
    premium_model: str = "gpt-4o"
    standard_model: str = "gpt-4o-mini"
    budget_model: str = "gpt-3.5-turbo"

    # ── Provider keys (optional) ───────────────────────
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    tongyi_api_key: str = ""
    tongyi_coding_api_key: str = ""
    tongyi_token_plan_api_key: str = ""
    kimi_api_key: str = ""
    mimo_api_key: str = ""
    tencent_api_key: str = ""
    minimax_api_key: str = ""
    siliconflow_api_key: str = ""
    volcengine_ark_api_key: str = ""
    opencode_api_key: str = ""
    volcengine_ark_base_url: str = Field(
        default="",
        description=(
            "Optional override for the Volcano Ark (火山方舟) base URL. "
            "Empty = use the Agent Plan default (https://ark.cn-beijing.volces.com/api/plan/v3). "
            "Set to https://ark.cn-beijing.volces.com/api/v3 for the standard pay-as-you-go endpoint, "
            "or to a regional URL like https://ark.ap-southeast-1.volces.com/api/v3 for BytePlus."
        ),
    )
    volcengine_ark_default_model: str = Field(
        default="",
        description=(
            "Default Volcano Ark endpoint ID or model name used when a task "
            "route does not pin one. Empty = doubao-seed-2-0-lite-260215 (Agent Plan default)."
        ),
    )

    # ── Ollama settings (local models) ─────────────────
    ollama_base_url: str = Field(
        default="http://localhost:11434/v1",
        description="Ollama API base URL (Ollama provides OpenAI-compatible API)",
    )
    ollama_model: str = Field(
        default="llama3.2",
        description="Default Ollama model for text generation",
    )
    ollama_embedding_model: str = Field(
        default="nomic-embed-text",
        description="Default Ollama model for embeddings",
    )
    ollama_sidecar_enabled: bool = Field(
        default=True,
        description="Enable desktop-managed Ollama sidecar when available.",
    )
    ollama_sidecar_auto_start: bool = Field(
        default=True,
        description="Auto-start bundled Ollama when the local API is unavailable.",
    )
    ollama_sidecar_binary_path: str = Field(
        default="",
        description="Optional explicit path to the bundled Ollama executable or directory.",
    )
    ollama_sidecar_models_dir: str = Field(
        default="",
        description="Optional models directory used by the desktop-managed Ollama sidecar.",
    )
    ollama_sidecar_prefer_local: bool = Field(
        default=True,
        description=(
            "When a local Ollama endpoint is healthy and no explicit provider or routing is set, "
            "prefer Ollama for this desktop process."
        ),
    )
    local_model_resource_budget: str = Field(
        default="medium",
        pattern=r"^(light|medium|high)$",
        description=(
            "Process-wide local model admission budget shared by Ollama, TTS, ASR, alignment, "
            "Stable Audio and local post-processing: light/medium/high."
        ),
    )
    local_model_resource_wait_timeout_s: float = Field(
        default=900.0,
        ge=10.0,
        le=7200.0,
        description="Maximum queue wait for one local model resource lease.",
    )

    # ── Desktop appearance ─────────────────────────────
    desktop_theme: str = Field(
        default="narrative_ember",
        description="Desktop theme id used by the PySide application stylesheet.",
    )
    desktop_ui_font_family: Literal["source_sans", "system_sans", "literary_serif"] = Field(
        default="source_sans",
        description="Desktop UI font profile shared by PySide and React clients.",
    )
    desktop_reading_font_family: Literal["source_serif", "ui_sans", "calligraphy"] = Field(
        default="source_serif",
        description="Desktop prose and heading font profile shared by both UI clients.",
    )
    desktop_font_scale: float = Field(
        default=1.0,
        ge=0.9,
        le=1.2,
        description="Desktop typography scale; 1.0 preserves the PySide parity baseline.",
    )
    desktop_pet_visible: bool = Field(
        default=True,
        description="Show the floating desktop task companion.",
    )

    # ── Desktop notifications ─────────────────────────
    desktop_notification_sound_enabled: bool = Field(
        default=True,
        description="Enable short desktop notification sounds for terminal task states.",
    )
    desktop_notification_success_sound: str = Field(
        default="chime",
        description="Sound key for completed desktop tasks.",
    )
    desktop_notification_decision_sound: str = Field(
        default="bell",
        description="Sound key for desktop tasks waiting for user decision.",
    )
    desktop_notification_failure_sound: str = Field(
        default="alert",
        description="Sound key for failed desktop tasks.",
    )

    # ── Default provider ───────────────────────────────
    default_provider: str = Field(
        default="",
        description="Default provider name when --no-mock. "
        "Leave empty to auto-detect from available keys.",
    )

    # ── Per-task routing (JSON string) ─────────────────
    task_routing: str = Field(
        default="",
        description=(
            "JSON mapping TaskType→route spec. "
            'String spec supports "provider:model,thinking,multi" '
            "(flags optional; defaults off), or object form "
            '{"provider":...,"model_id":...,"thinking":...,"multi_turn":...}. '
            'Example: {"draft":"deepseek:deepseek-chat","evaluate":"tongyi:qwen-turbo,thinking,multi"}'
        ),
    )
    task_fallback_routing: str = Field(
        default="",
        description=(
            "JSON mapping TaskType→fallback route list. "
            "Each value should be an array of route specs "
            '(same format as task_routing, e.g. {"draft_chapter": ["tencent:hunyuan-xxx","deepseek:deepseek-chat"]}). '
            "Fallback routes are tried only after the primary route exhausts retries due to timeout/retriable errors."
        ),
    )
    repair_model: str = Field(
        default="",
        description=(
            "Provider:model override for all repair tasks (repair_continuity, edit_chapter). "
            "Format: 'provider:model', e.g. 'tongyi:qwen-long'. "
            "Ensures long-context model is used so full chapter text is preserved without truncation. "
            "Takes priority over tier defaults but NOT over explicit task_routing entries."
        ),
    )

    # ── Initialization web research ───────────────────
    research_enabled: bool = Field(
        default=False,
        description=(
            "Default switch for optional long-init web research. Desktop long-init forms "
            "use this as their default; CLI/runtime requests may still override it."
        ),
    )
    research_default_provider: str = Field(
        default="auto",
        description=(
            "Default provider for optional long-init web research. "
            "Supported adapters: auto/noop/tavily/brave/searxng/http_json/"
            "bailian_web_search/mcp_search."
        ),
    )
    research_http_endpoint: str = Field(
        default="",
        description=(
            "Optional endpoint for long-init web research. Required for searxng/http_json; "
            "optional for tavily/brave where built-in API endpoints are known."
        ),
    )
    research_api_key: str = Field(
        default="",
        description="Optional API key for Tavily/Brave or a compatible HTTP JSON research endpoint.",
    )
    research_timeout_s: float = Field(
        default=10.0,
        ge=1.0,
        le=120.0,
        description="Timeout in seconds for each external web-research request.",
    )
    research_max_queries: int = Field(
        default=3,
        ge=1,
        le=8,
        description="Maximum deterministic queries planned from one confirmed long-init spec.",
    )
    research_results_per_query: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum raw search results requested per planned query.",
    )
    research_max_results: int = Field(
        default=5,
        ge=1,
        le=30,
        description="Maximum normalized sources kept in init web-research reports.",
    )
    research_retry_attempts: int = Field(
        default=1,
        ge=0,
        le=3,
        description="Retry attempts per research query after the first provider request fails.",
    )
    research_query_max_parallel: int = Field(
        default=2,
        ge=1,
        le=8,
        description=(
            "Maximum concurrent web-research queries per init run. Results are merged in "
            "planned query order, so dedup/truncation stays identical to serial execution."
        ),
    )
    research_include_domains: str = Field(
        default="",
        description="Comma-separated domain allowlist for normalized web-research sources.",
    )
    research_exclude_domains: str = Field(
        default="",
        description="Comma-separated domain blocklist for normalized web-research sources.",
    )
    research_locale: str = Field(
        default="",
        description="Optional search locale/language hint such as zh-CN or en-US.",
    )
    research_search_depth: str = Field(
        default="basic",
        description="Provider search depth when supported, for example Tavily basic/advanced.",
    )
    research_dossier_enabled: bool = Field(
        default=True,
        description=(
            "When long-init web research is enabled and returns sources, synthesize a compact "
            "research dossier for StoryBible prompts."
        ),
    )
    research_dossier_max_sources: int = Field(
        default=8,
        ge=1,
        le=30,
        description="Maximum sources passed from init web research into the dossier synthesis step.",
    )
    outline_research_grounding_enabled: bool = Field(
        default=True,
        description=(
            "When a research dossier is available, align it with the final outline before "
            "chapter-contract generation."
        ),
    )
    outline_research_grounding_notes_per_chapter: int = Field(
        default=3,
        ge=0,
        le=12,
        description="Maximum research reminders/fact risks kept per chapter in outline grounding.",
    )
    outline_research_grounding_batch_size: int = Field(
        default=6,
        ge=1,
        le=30,
        description=(
            "Maximum chapter outlines sent in one research-grounding call. Complete coverage "
            "is preserved across calls."
        ),
    )
    outline_research_grounding_prompt_char_budget: int = Field(
        default=140000,
        ge=20000,
        le=500000,
        description=(
            "Rendered prompt character budget for each outline research-grounding batch. "
            "Oversized authoritative chapters fail instead of being truncated."
        ),
    )
    outline_research_grounding_coverage_retries: int = Field(
        default=1,
        ge=0,
        le=3,
        description="Extra calls used only when a grounding batch omits requested chapters.",
    )
    research_use_llm_planning: bool = Field(
        default=True,
        description=(
            "Use LLM to plan research queries from the full spec. "
            "Only takes effect when research_enabled=true. Falls back to rule-based queries on error."
        ),
    )
    research_model_prior_enabled: bool = Field(
        default=False,
        description=(
            "Enable model prior-knowledge synthesis as a supplement to external research. "
            "Results are tagged as model_prior and do not enter the external sources list."
        ),
    )
    chapter_research_refresh_enabled: bool = Field(
        default=False,
        description=(
            "Allow one bounded factual refresh query before a chapter run when the "
            "chapter contract or current user instruction exposes a must-level fact gap."
        ),
    )
    chapter_research_inspiration_enabled: bool = Field(
        default=False,
        description=(
            "Allow one low-frequency inspiration query at chapter boundaries or after "
            "recent scene-mechanism repetition. Inspiration never becomes canon."
        ),
    )
    chapter_research_inspiration_cooldown: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Minimum chapter distance between external inspiration refreshes.",
    )
    chapter_intent_guard_mode: Literal["off", "warn", "block"] = Field(
        default="warn",
        description=(
            "Long-chapter immutable user-intent verification mode. warn records explicit "
            "conflicts without changing the quality gate; block feeds verified conflicts "
            "into the existing guard gate; off keeps legacy guard-only behavior."
        ),
    )
    research_mcp_command: str = Field(
        default="",
        description=(
            "Command to launch the MCP server for mcp_search research provider "
            "(e.g. 'uvx' or 'npx'). Required when research_default_provider=mcp_search."
        ),
    )
    research_mcp_args: str = Field(
        default="",
        description=(
            "Comma-separated arguments for the MCP server command "
            "(for uvx: 'minimax-coding-plan-mcp'). Kept for backward compatibility; "
            "prefer research_mcp_args_json for new configs."
        ),
    )
    research_mcp_args_json: str = Field(
        default="",
        description='JSON array of MCP command arguments, e.g. ["minimax-coding-plan-mcp"].',
    )
    research_mcp_env: str = Field(
        default="",
        description=(
            "Comma-separated KEY=VALUE environment variables for the MCP server "
            "(e.g. 'MINIMAX_API_KEY=xxx,MINIMAX_API_HOST=https://api.minimax.io'). "
            "Kept for backward compatibility; prefer research_mcp_env_json for new configs. "
            "The research_api_key is auto-injected under research_mcp_api_key_env if not already set."
        ),
    )
    research_mcp_env_json: str = Field(
        default="",
        description=(
            "JSON object of MCP server environment variables, e.g. "
            '{"MINIMAX_API_HOST":"https://api.minimax.io"}.'
        ),
    )
    research_mcp_api_key_env: str = Field(
        default="MINIMAX_API_KEY",
        description=(
            "Environment variable name used to pass research_api_key into the MCP server. "
            "Set to the target server's expected key name, or blank to disable auto-injection."
        ),
    )
    research_mcp_protocol_version: str = Field(
        default="2024-11-05",
        description="MCP protocol version sent in initialize; 2024-11-05 is the compatibility default.",
    )
    research_mcp_stdio_framing: Literal["newline", "content_length"] = Field(
        default="newline",
        description=(
            "MCP stdio framing. Official MCP stdio uses newline-delimited JSON; "
            "content_length is only for legacy/local tools."
        ),
    )
    research_mcp_inherit_environment: bool = Field(
        default=False,
        description=(
            "When false, MCP subprocess receives a minimal environment plus explicit MCP env. "
            "Set true only for trusted local servers that require the full shell environment."
        ),
    )
    research_mcp_tool_name: str = Field(
        default="",
        description=(
            "Optional exact MCP search tool name. If empty, Novel Forge discovers a tool named "
            "web_search/search or containing 'search'."
        ),
    )
    research_mcp_query_argument: str = Field(
        default="query",
        description="Argument name used to pass the query when no tool argument template is set.",
    )
    research_mcp_tool_arguments_json: str = Field(
        default="",
        description=(
            "Optional JSON object template for tools/call arguments. Supports {query} and "
            "{results_per_query} placeholders."
        ),
    )

    # ── Pipeline ───────────────────────────────────────
    short_max_edit_rounds: int = 2
    short_adaptive_revision_enabled: bool = False
    short_candidate_first_repair_enabled: bool = Field(
        default=True,
        description=(
            "短篇完整度/质量修复使用隔离候选、完整复评和更优版本选择；"
            "关闭时仅保留一个发布周期的旧适配器回退点。"
        ),
    )
    repair_real_shadow_enabled: bool = Field(
        default=False,
        description=(
            "显式允许统一修复基座进行真实影子模型抽样。默认关闭；"
            "即使开启也必须同时通过确定性抽样、30 日次数和独立预算门禁。"
        ),
    )
    repair_shadow_sample_rate: float = Field(
        default=0.05,
        ge=0.0,
        le=0.05,
        description="真实修复影子抽样比例上限；不得超过 5%。",
    )
    repair_shadow_max_per_project_30d: int = Field(
        default=10,
        ge=0,
        le=10,
        description="每个项目滚动 30 日最多允许的真实修复影子调用次数。",
    )
    repair_shadow_budget_usd_30d: float = Field(
        default=0.0,
        ge=0.0,
        allow_inf_nan=False,
        description=(
            "真实修复影子调用的独立 30 日预算；0 表示即使开关打开也不允许付费调用。"
        ),
    )
    repair_shadow_estimated_call_usd: float = Field(
        default=0.10,
        gt=0.0,
        allow_inf_nan=False,
        description="真实修复影子调用在结算前占用的保守估算费用。",
    )
    short_quality_gate_enabled: bool = True
    short_quality_gate_min_overall_score: float = Field(default=7.4, ge=0.0, le=10.0)
    short_quality_gate_continuity_floor: float = Field(default=7.0, ge=0.0, le=10.0)
    short_quality_gate_style_floor: float = Field(default=7.0, ge=0.0, le=10.0)
    short_quality_gate_engagement_floor: float = Field(default=7.0, ge=0.0, le=10.0)
    short_segment_mode: Literal["auto", "on", "off"] = "auto"
    short_segment_trigger_words: int = Field(default=5500, ge=1500, le=50000)
    short_segment_target_words: int = Field(default=2200, ge=800, le=20000)
    short_segment_max_count: int = Field(default=4, ge=2, le=8)
    llm_format_retry_attempts: int = Field(
        default=2,
        ge=1,
        le=5,
        description=(
            "JSON/结构化输出格式错误时的总尝试次数。"
            "每次失败会写入 run logs/format_errors 并在桌面任务流中显示。"
        ),
    )
    llm_format_retry_temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="格式重试时使用的低温度，降低漏逗号、尾随说明等结构漂移。",
    )
    llm_format_retry_raw_char_limit: int = Field(
        default=6000,
        ge=1000,
        le=60000,
        description="普通格式重试注入给原任务模型的上一轮错误输出字符预算。",
    )
    creative_temperature_jitter_enabled: bool = Field(
        default=False,
        description="启用创意类任务火候浮动。关闭时所有任务仍使用固定温度。",
    )
    creative_temperature_jitter_up_delta: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        description="创意火候相对基础温度的最大上浮值。",
    )
    creative_temperature_jitter_down_delta: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description="创意火候相对基础温度的最大下浮值。",
    )
    creative_temperature_jitter_scope: str = Field(
        default="recommended",
        description=("创意火候浮动的适用范围：recommended/chapter_core/init_and_chapter/custom。"),
    )
    creative_temperature_jitter_custom_tasks: str = Field(
        default="",
        description="自定义创意火候浮动任务列表，逗号分隔 TaskType key；保护任务会被自动过滤。",
    )
    llm_format_repair_enabled: bool = Field(
        default=True,
        description=(
            "启用专用格式修复模块。普通格式重试全部用尽前仍优先重试原任务；"
            "最后一次失败时会先尝试本地修复策略，无法安全修复再调用专用修复 LLM。"
        ),
    )
    llm_format_repair_model: str = Field(
        default="",
        description=(
            "格式修复 LLM 的 provider:model 覆盖。留空时复用 repair_model；"
            "两者都为空时使用原任务路由。"
        ),
    )
    llm_format_repair_max_tokens: int = Field(
        default=4096,
        ge=512,
        le=32768,
        description="专用格式修复 LLM 的最大输出 token。",
    )
    llm_format_repair_raw_char_limit: int = Field(
        default=60000,
        ge=1000,
        le=200000,
        description="发送给专用格式修复 LLM 的原始错误输出最大字符数。",
    )

    @model_validator(mode="after")
    def _validate_segment_words(self) -> "Settings":
        if self.short_segment_trigger_words < self.short_segment_target_words:
            raise ValueError(
                f"short_segment_trigger_words ({self.short_segment_trigger_words}) "
                f"must be >= short_segment_target_words ({self.short_segment_target_words})"
            )
        return self

    @model_validator(mode="after")
    def _validate_routing_json(self) -> "Settings":
        """Validate task_routing/task_fallback_routing are well-formed JSON (if set)."""
        for field_name in ("task_routing", "task_fallback_routing"):
            raw = getattr(self, field_name, "")
            if raw and raw.strip():
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{field_name} is not valid JSON: {exc}") from exc
                if not isinstance(parsed, dict):
                    raise ValueError(
                        f"{field_name} must be a JSON object, got {type(parsed).__name__}"
                    )
        return self

    @model_validator(mode="after")
    def _validate_audio_storage_boundaries(self) -> "Settings":
        """Keep reusable model weights outside the per-project storage tree."""
        storage_root = self.storage_root.expanduser().resolve(strict=False)
        candidates = {
            "audio_models_root": Path(self.audio_models_root).expanduser(),
            "sound_generation_stable_audio_models_dir": Path(
                self.sound_generation_stable_audio_models_dir
            ).expanduser(),
            "tts_cosyvoice_voice_store": Path(self.tts_cosyvoice_voice_store).expanduser(),
            "tts_openvoice_checkpoint_dir": Path(self.tts_openvoice_checkpoint_dir).expanduser(),
            "tts_openvoice_voice_store": Path(self.tts_openvoice_voice_store).expanduser(),
        }
        for setting_name, raw_path in candidates.items():
            candidate = raw_path.resolve(strict=False)
            try:
                candidate.relative_to(storage_root)
            except ValueError:
                continue
            raise ValueError(
                f"{setting_name} 必须位于 storage_root 之外，模型权重不能保存到 data/<项目>/ 目录。"
            )
        return self

    short_draft_multi_turn: bool = True
    short_draft_multi_turn_providers: str = "tongyi,deepseek"
    short_draft_multi_turn_models: str = ""
    long_alignment_threshold: float = 7.0
    long_word_count_archive_gate_enabled: bool = Field(
        default=True,
        description=(
            "Enable the pre-archive word-count restructure/blocking gate for long chapters. "
            "Critical short-text data-loss protection remains active when disabled."
        ),
    )
    long_word_count_archive_gate_max_rejections: int = Field(
        default=2,
        ge=0,
        le=10,
        description=(
            "Maximum repeated pre-archive word-count rejections per chapter before the gate "
            "degrades to a warning-only archive bypass. 0 means never hard-block on target "
            "word-count drift after the restructure attempt; critical short-text protection "
            "still applies."
        ),
    )
    guardrail_regression_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=10.0,
        description=(
            "Maximum allowed alignment score drop after guardrail ticket repair. "
            "Drops below this threshold emit a warning but allow continuation; "
            "drops at or above this threshold block archiving."
        ),
    )
    guard_ticket_alignment_followup_max_attempts: int = Field(
        default=2,
        ge=1,
        le=5,
        description=(
            "Maximum alignment follow-up repair attempts after a guardrail ticket repair "
            "causes an actionable alignment regression before returning to a checkpoint."
        ),
    )
    long_volume_auto_chapter_threshold: int = 60
    long_volume_auto_word_threshold: int = 250_000
    long_default_chapters_per_volume: int = 20
    long_plot_guard_mode: Literal["free", "balanced", "strict", "ai_judge"] = "balanced"
    long_ai_judge_max_context_chapters: int = 24
    long_ai_judge_max_tokens: int = 1536
    long_ai_judge_apply_entity_actions: bool = True
    long_ai_judge_apply_mode: Literal["assist", "trust"] = "assist"
    long_snapshot_max_disk_mb: int = Field(
        default=500,
        ge=0,
        description="StoryKernel snapshot 总磁盘占用上限 (MB)。超过后按时间从旧到新删除非保护快照。0 = 不限制。",
    )
    long_snapshot_keep_recent: int = Field(
        default=5,
        ge=0,
        description="每章 finalize 后自动 prune 时保留的最近 snapshot 数量。",
    )
    long_volume_audit_block_on_critical: bool = Field(
        default=False,
        description="Volume audit 发现 critical issue 时阻断下一卷首章生成，直到 issue 被解决。",
    )
    long_auto_book_audit_interval: int = Field(
        default=0,
        ge=0,
        description="自动触发 book consistency audit 的章节间隔。0 = 关闭，>0 = 每 N 章触发一次。",
    )
    long_summary_drift_check_enabled: bool = Field(
        default=False,
        description=(
            "卷末启用摘要漂移检查：对卷摘要、章节摘要和 StoryKernel 关键事实做确定性比对，"
            "发现高危问题写入 project issue ledger。默认关闭。"
        ),
    )
    long_quality_trend_tracker_enabled: bool = Field(
        default=False,
        description=(
            "每章 finalize 后记录长期质量趋势指标，用于发现评分、追读力、连贯性和伏笔老化的缓慢下滑。"
            "默认关闭。"
        ),
    )

    # ── Plot progression / contract execution control ──
    plot_progression_strictness: Literal["warn", "block", "strict"] = Field(
        default="block",
        description="剧情推进控制强度：warn 只报告；block 阻断未来泄露/禁用推进；strict 同时阻断必达缺失。",
    )
    long_future_leak_guard_enabled: bool = Field(
        default=True,
        description="章节运行时启用未来剧情泄露与提前兑现检查。",
    )
    long_contract_audit_enabled: bool = Field(
        default=True,
        description="归档前启用 ContractExecutionAudit，核对章节契约、里程碑窗口和推进账本。",
    )
    long_kb_audit_reuse_enabled: bool = Field(
        default=True,
        description="归档前文本未变且已有新鲜无命中知识边界报告时，复用该报告并跳过重复 KB 审计。",
    )
    long_contract_audit_strictness: Literal["warn", "block", "strict"] = Field(
        default="block",
        description="契约执行审计强度：warn 只报告；block 阻断未来泄露/禁用推进；strict 同时阻断必达缺失。",
    )
    expression_channel_detection_enabled: bool = Field(
        default=True,
        description="启用表达通道语义检测，将近义生理反应、动作标签、感官锚点等归入 typed channel。",
    )
    expression_channel_cooldown_chapters: int = Field(
        default=3,
        ge=0,
        le=30,
        description="表达通道冷却窗口。Draft 会收到近期高频通道和替代表达机制提示。",
    )
    expression_channel_zvec_memory_enabled: bool = Field(
        default=True,
        description="启用表达通道观察项的 Zvec 语义证据记忆；不可用时自动降级为本地扫描。",
    )
    expression_channel_zvec_top_k_per_profile: int = Field(
        default=2,
        ge=1,
        le=5,
        description="每个表达通道从语义证据记忆召回的最大短证据数。",
    )
    arc_liveness_window: int = Field(
        default=6,
        ge=1,
        le=60,
        description="副线/角色弧光沉睡检测窗口；超过该章数未推进会给 Plan 阶段轻触提示。",
    )
    stage_visibility_debug_enabled: bool = Field(
        default=True,
        description="记录 Bridge/Plan/Draft/Judge 实际可见的里程碑窗口摘要，供开发期诊断。",
    )

    # ── LLM-adjudicated narrative state ─────────────────
    narrative_state_enabled: bool = Field(
        default=True,
        description="Enable LLM-adjudicated narrative state ledger/projection for long chapters.",
    )
    narrative_state_required: bool = Field(
        default=True,
        description="When enabled, failures in narrative state init/adjudication block the pipeline.",
    )
    narrative_state_max_repair_rounds: int = Field(default=1, ge=0, le=5)
    narrative_state_candidate_max_count: int = Field(
        default=8,
        ge=1,
        le=200,
        description="每章常规进入状态裁判的候选上限；必达契约证据可在此之外保留。",
    )
    narrative_state_pending_tail_items: int = Field(
        default=6,
        ge=0,
        le=100,
        description="注入章节上下文的最近待定状态条数。",
    )
    narrative_state_candidate_evidence_limit: int = Field(
        default=2,
        ge=1,
        le=4,
        description="每个状态候选保留并投喂单候选裁判的正文证据条数。",
    )
    narrative_state_final_context_max_chars: int = Field(
        default=18000,
        ge=8000,
        le=64000,
        description="最终状态合并裁判的输入字符预算；超出时保留全部 ID/状态并压缩说明字段。",
    )
    narrative_state_coverage_gap_tolerance: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of required contract targets allowed to be uncovered without "
            "triggering a coverage warning. 0.0 = any gap is reported (current behavior); "
            "0.2 = up to 20% of targets may be uncovered before warning. "
            "This does NOT bypass archive gates; it only controls whether the coverage "
            "report flags incomplete coverage as a warning vs informational."
        ),
    )
    narrative_state_final_target_max_chars: int = Field(
        default=96,
        ge=40,
        le=240,
        description="最终状态合并裁判中每个契约目标说明的最大字符数。",
    )

    # ── Film / Visual generation ────────────────────────
    film_default_provider: str = Field(
        default="minimax",
        description="映界默认视觉生成平台；MiniMax H3 为首选完整路径。",
    )
    film_minimax_api_key: str = Field(
        default="",
        description="映界 MiniMax 开放平台 API Key；与 TTS 凭据分离。",
    )
    film_minimax_base_url: str = Field(
        default="https://api.minimaxi.com",
        description="映界 MiniMax H3 v2 API 根地址，不包含 /v1 或 /v2。",
    )
    film_project_budget_usd: float = Field(
        default=50.0,
        ge=0,
        description="映界项目级默认预算；付费图运行仍需先估算并确认。",
    )

    # ── TTS / Voice Cloning ─────────────────────────────
    tts_enabled: bool = Field(
        default=False,
        description="启用 TTS 自动配音模块。",
    )
    tts_default_provider: str = Field(
        default="minimax",
        description=(
            "默认 TTS 平台: minimax/bailian/dashscope/tencent/volcengine_ark/"
            "mimo/local/qwen3/cosyvoice/openvoice/mock。"
        ),
    )
    tts_default_model: str = Field(
        default="speech-2.8-hd",
        description="默认 TTS 模型 ID。",
    )
    tts_default_speed: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="项目级默认语速倍率；角色与片段参数在此基础上叠加。",
    )
    tts_minimax_group_id: str = Field(
        default="",
        description="MiniMax 旧版兼容 Group ID；当前 TTS v2/音色接口通常无需填写。",
    )
    tts_minimax_api_key: str = Field(
        default="",
        description="MiniMax TTS API Key（可与 LLM key 不同）。",
    )
    tts_minimax_base_url: str = Field(
        default="https://api.minimax.io/v1",
        description=(
            "MiniMax TTS v2 API 地址；默认使用官方全球端点。"
            "可按账号区域或低首包延迟需求改为官方兼容端点。"
        ),
    )
    tts_minimax_bitrate: int = Field(
        default=128000,
        description="MiniMax MP3 输出比特率: 32000/64000/128000/256000。",
    )
    tts_minimax_channel: int = Field(
        default=1,
        ge=1,
        le=2,
        description="MiniMax 输出声道：1=单声道，2=双声道。",
    )
    tts_minimax_language_boost: str = Field(
        default="auto",
        description="MiniMax 语种/方言增强，默认自动判断。",
    )
    tts_aigc_watermark_default: bool = Field(
        default=False,
        description=(
            "AIGC 水印默认开关。默认 False：MiniMax 在开启水印时会在音频尾部注入可听方波信号"
            "（短试听尤其明显，表现为末尾的“滴滴”声），影响试听体验。"
            "如需为合规传播开启水印，可将此项设为 True，或在单次请求的 "
            "platform_extension.aigc_watermark=True 显式开启。"
        ),
    )
    tts_minimax_continuous_sound_default: bool = Field(
        default=False,
        description=(
            "MiniMax continuous_sound 兼容开关（仅对 speech-2.8 系列生效）。"
            "官方公开 HTTP 契约未稳定列出该字段，因此默认关闭；"
            "可在单次请求的 platform_extension.continuous_sound=True 显式开启。"
        ),
    )
    tts_minimax_english_normalization: bool = Field(
        default=False,
        description=(
            "MiniMax 英文数字、日期与公式规范化；可提高英文读法准确性，但会增加少量处理延迟。"
        ),
    )
    tts_minimax_force_cbr: bool = Field(
        default=True,
        description=(
            "MiniMax 音频输出强制恒定比特率。默认开启以稳定多段 MP3 的时长与拼接行为；"
            "单次请求可用 platform_extension.force_cbr=False 覆盖。"
        ),
    )
    tts_minimax_async_timeout_s: float = Field(
        default=7200.0,
        ge=60.0,
        le=86400.0,
        description=(
            "MiniMax 异步 TTS 任务最长等待秒数。默认 7200（2 小时），"
            "覆盖极端长文本场景；超过该时间未完成的异步任务将被视为失败。"
        ),
    )
    tts_preview_lock_wait_timeout_s: float = Field(
        default=8.0,
        ge=0.1,
        le=60.0,
        description=(
            "交互式音色试听等待项目 TTS 锁的最长秒数。自动配音占用时快速提示重试，"
            "而不是无限显示‘正在合成试听’。"
        ),
    )
    tts_preview_synthesis_timeout_s: float = Field(
        default=45.0,
        ge=1.0,
        le=300.0,
        description="交互式音色试听等待平台返回音频的最长秒数。",
    )
    tts_auto_trigger_after_chapter: bool = Field(
        default=False,
        description="章节生成完成后自动触发 TTS 流程。",
    )
    tts_post_archive_retry_enabled: bool = Field(
        default=True,
        description="post-archive TTS 失败后是否自动延迟重试一次。",
    )
    tts_post_archive_retry_delay_s: int = Field(
        default=60,
        ge=10,
        le=600,
        description="post-archive TTS 失败后延迟重试的秒数。",
    )
    tts_voice_library_scope: str = Field(
        default="project_only",
        description="全局音色库匹配范围：project_only / global_with_names / global_all。",
    )
    tts_parallel_voice_clone: bool = Field(
        default=True,
        description="Init 阶段并行执行音色克隆。",
    )
    tts_voice_design_enabled: bool = Field(
        default=False,
        description=(
            "AI 音色设计为最后兜底手段（按次计费）。默认关闭；仅当音色库与系统目录"
            "均无法提供合格音色时，启用后才会调用 Provider 的 voice_design 接口。"
        ),
    )
    tts_voice_semantic_matching_enabled: bool = Field(
        default=True,
        description=(
            "对全局音色库启用嵌入向量召回；性别、年龄与 Provider 硬约束始终先于语义相似度执行。"
        ),
    )
    tts_voice_semantic_top_k: int = Field(
        default=12,
        ge=2,
        le=100,
        description="音色库向量召回的最大候选数；召回后仍由规则与试听复核。",
    )
    tts_voice_llm_adjudication_enabled: bool = Field(
        default=True,
        description=(
            "对低置信度的音色匹配启用 LLM 受约束裁决。"
            "LLM 只能在已通过性别、年龄、供应商与有效期硬约束的白名单中选择。"
            "默认开启以弥补系统目录元数据不足导致的低匹配分。"
        ),
    )
    tts_voice_llm_adjudication_min_match_score: float = Field(
        default=0.72,
        ge=0.0,
        le=1.0,
        description="匹配分低于此值的音色才进入 LLM 复核候选。",
    )
    tts_voice_llm_adjudication_max_candidates: int = Field(
        default=4,
        ge=1,
        le=12,
        description="单次自动组建最多交给 LLM 复核的角色数，用于控制费用。",
    )
    tts_voice_llm_adjudication_choices_per_character: int = Field(
        default=4,
        ge=2,
        le=8,
        description="每个低置信度角色交给 LLM 比较的有效音色候选数。",
    )
    tts_voice_llm_adjudication_auto_select_score: float = Field(
        default=0.85,
        ge=0.5,
        le=1.0,
        description="LLM 置信度达到此阈值时，才允许在候选白名单内自动改配。",
    )
    tts_voice_llm_adjudication_max_output_tokens: int = Field(
        default=1200,
        ge=256,
        le=4096,
        description="一批音色复核的 LLM 最大输出 token 预算。",
    )
    tts_script_llm_review_enabled: bool = Field(
        default=True,
        description=(
            "在确定性保真检查之后运行配音专用 LLM 审校。"
            "该审校只自动修复安全的情绪、语气和副语言标注；正文与说话人异常转人工复核。"
        ),
    )
    tts_script_llm_review_max_output_tokens: int = Field(
        default=8192,
        ge=2048,
        le=32768,
        description="整章配音脚本专业审校的最大输出 token 预算。",
    )
    tts_script_max_concurrent_batches: int = Field(
        default=2,
        ge=1,
        le=4,
        description=("配音脚本长章节分批改写的最大并发数；默认 2，在提速的同时限制模型请求峰值。"),
    )
    tts_sound_design_enabled: bool = Field(
        default=True,
        description=(
            "在表演脚本定稿后运行独立的声音设计提取步骤，分析叙事内音效、BGM 需求、环境声层和转场。"
        ),
    )
    tts_style_reference_llm_enabled: bool = Field(
        default=True,
        description="上传参考配音脚本后，使用独立 LLM 步骤抽取不可复制原文的抽象表演风格画像。",
    )
    tts_style_reference_min_chars: int = Field(
        default=160,
        ge=80,
        le=5000,
        description="参考配音脚本形成稳定风格画像所需的最少有效字符数。",
    )
    tts_style_reference_max_chars: int = Field(
        default=30_000,
        ge=1000,
        le=200_000,
        description="单次参考配音脚本分析允许进入临时提示上下文的最大字符数；原文不会持久化。",
    )
    tts_style_reference_temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="参考配音脚本风格分析温度；低温确保画像稳定且可重复。",
    )
    tts_style_reference_default_strength: float = Field(
        default=0.65,
        ge=0.0,
        le=1.0,
        description="参考风格对口语改写与声场设计的默认影响强度；事实和角色声纹始终优先。",
    )
    # ── TTS LLM 温度与采样参数 ──
    tts_script_generation_temperature: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="配音脚本生成的 LLM 温度；0.5 在稳定性和创造性之间取得平衡。",
    )
    tts_script_generation_top_p: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="配音脚本生成的 nucleus sampling 阈值。",
    )
    tts_spoken_rewrite_temperature: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        description="口语改写步骤的 LLM 温度；需要一定创造性但不应偏离原文语义。",
    )
    tts_spoken_rewrite_batch_size: int = Field(
        default=30,
        ge=5,
        le=100,
        description="口语改写每批最多处理的片段数。",
    )
    tts_spoken_rewrite_min_length: int = Field(
        default=8,
        ge=1,
        le=100,
        description="口语改写候选片段的最小字符数阈值。",
    )
    tts_spoken_rewrite_min_length_ratio: float = Field(
        default=0.5,
        ge=0.1,
        le=1.0,
        description="口语改写验证：改写文本与原文长度比下限。",
    )
    tts_spoken_rewrite_max_length_ratio: float = Field(
        default=1.3,
        ge=1.0,
        le=3.0,
        description="口语改写验证：改写文本与原文长度比上限。",
    )
    tts_spoken_rewrite_min_sequence_ratio: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="口语改写验证：SequenceMatcher 相似度下限。",
    )
    tts_spoken_rewrite_min_confidence: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="口语改写验收：应用非空 spoken_text 所需的最低模型置信度。",
    )
    tts_spoken_rewrite_context_window: int = Field(
        default=1,
        ge=0,
        le=3,
        description="口语改写时每个片段携带的前后相邻片段数量。",
    )
    tts_spoken_rewrite_narration_sentence_chars: int = Field(
        default=30,
        ge=10,
        le=100,
        description="中文旁白口语改写的建议单句字符上限；提示规则使用该值而非模板常量。",
    )
    tts_spoken_rewrite_dialogue_sentence_chars: int = Field(
        default=20,
        ge=6,
        le=80,
        description="中文对白口语改写的建议单句字符上限。",
    )
    tts_spoken_rewrite_inner_thought_sentence_chars: int = Field(
        default=25,
        ge=6,
        le=80,
        description="中文内心独白口语改写的建议单句字符上限。",
    )
    tts_spoken_rewrite_min_output_tokens: int = Field(
        default=1024,
        ge=256,
        le=16384,
        description="口语改写单批结构化输出的最小 token 预算。",
    )
    tts_spoken_rewrite_output_tokens_per_segment: int = Field(
        default=160,
        ge=64,
        le=1024,
        description="口语改写单批按片段估算结构化输出 token 的系数。",
    )
    # ── 口语改写重试策略 ────────────────────────────────────────────────────────
    tts_spoken_rewrite_max_rounds: int = Field(
        default=3,
        ge=1,
        le=10,
        description="口语改写 LLM 调用失败时的最大重试轮数。",
    )
    tts_spoken_rewrite_retry_backoff_s: float = Field(
        default=2.0,
        ge=0.5,
        le=30.0,
        description="口语改写重试轮间的指数退避基数（秒）。",
    )
    # ── 停顿标记注入 ──────────────────────────────────────────────────────────
    tts_pause_marker_enabled: bool = Field(
        default=True,
        description=(
            "在口语改写完成后确定性注入 MiniMax <#X#> 停顿标记；"
            "规则来自标点/段落/对话结构，零 LLM 成本且幂等。"
        ),
    )
    tts_pause_marker_platforms: list[str] = Field(
        default_factory=lambda: ["minimax"],
        description=(
            "原生支持 <#X#> 停顿标记语法的 TTS 平台列表；其他平台保持纯文本以免标记被字面朗读。"
        ),
    )
    # ── LLM 情绪精标 ────────────────────────────────────────────────────────
    tts_emotion_label_enabled: bool = Field(
        default=True,
        description=(
            "口语改写后由 LLM 对连续窗口内的段执行情绪精标（emotion/sub_emotion/"
            "intensity）；关键词推断保留为确定性回退，未知标签与越界强度会被钳制。"
        ),
    )
    tts_emotion_label_window: int = Field(
        default=6,
        ge=5,
        le=8,
        description="情绪精标连续窗口大小（段数，5-8）。",
    )
    tts_emotion_label_max_concurrent: int = Field(
        default=2,
        ge=1,
        le=4,
        description=("情绪精标窗口批次的最大并发 LLM 调用数；长章节可提速，同时限制模型请求峰值。"),
    )
    # ── 拟声标签规模化 ──────────────────────────────────────────────────────
    tts_onomatopoeia_enabled: bool = Field(
        default=True,
        description=(
            "在停顿标记注入同一步，按 emotion+intensity 规则推断拟声标签"
            "（HAPPY→(laughs)、SAD→(sighs)、WHISPER→(whisper)），仅 speech-2.8 系列注入。"
        ),
    )
    tts_onomatopoeia_min_intensity: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="拟声标签注入所需的最低情绪强度。",
    )
    # ── 多 Take 选择 ─────────────────────────────────────────────────────────
    tts_multi_take_enabled: bool = Field(
        default=True,
        description=(
            "对高情绪强度或关键对白段生成 2-3 个候选样本，按时长合理性与情绪能量一致性评分选优。"
        ),
    )
    tts_multi_take_min_intensity: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="触发多 Take 的最低情绪强度。",
    )
    tts_multi_take_max_takes: int = Field(
        default=3,
        ge=2,
        le=3,
        description="多 Take 最大候选样本数（2-3）。",
    )
    # ── 音色策略：minor 角色优先系统音色 ────────────────────────────────────
    tts_system_voice_minor_chapters: int = Field(
        default=3,
        ge=1,
        le=10,
        description=(
            "出场章节数不超过该值的 minor 角色优先使用系统音色（稳定 + 低成本），"
            "跳过克隆/AI 设计；主角不受此限制。"
        ),
    )
    # ── 叙事弧线：章节级旁白距离规划 ────────────────────────────────────────
    tts_narration_arc_enabled: bool = Field(
        default=True,
        description=(
            "声音设计阶段输出全局节奏规划（开场 close/发展 medium/高潮按情绪强度 "
            "决定 close 或 medium/收束 distant），写入旁白段 narrator_distance，"
            "由 finalize 阶段映射到 vocal_direction.intimacy。"
        ),
    )
    # ── 脚本完整度门禁 ──────────────────────────────────────────────────────────
    tts_script_gate_enabled: bool = Field(
        default=True,
        description="脚本完整度门禁总开关；开发期可关闭以跳过门禁检查。",
    )
    tts_script_gate_spoken_text_coverage: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="可改写段中 spoken_text 非空占比的最低阈值。",
    )
    tts_script_gate_emotion_differentiation: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="非 neutral 情绪段占比的最低阈值（避免全部 neutral/0.5）。",
    )
    tts_script_context_max_items: int = Field(
        default=8,
        ge=1,
        le=50,
        description="脚本阶段每个上游上下文列表/映射最多投影的条目数。",
    )
    tts_script_context_max_text_chars: int = Field(
        default=240,
        ge=40,
        le=2000,
        description="脚本阶段单条非正文上下文文本的最大字符数。",
    )
    tts_review_adjudication_temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="配音脚本审校与裁决的 LLM 温度；建议保持低温以确保判断稳定。",
    )
    tts_narrator_profile_temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="旁白声音画像生成的 LLM 温度。",
    )
    tts_sound_design_temperature: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="声音设计提取的 LLM 温度；适度提高以增加创意多样性。",
    )
    tts_sound_design_top_p: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="声音设计提取的 nucleus sampling 阈值。",
    )
    tts_bgm_palette_minimum_count: int = Field(
        default=3,
        ge=0,
        le=10,
        description=(
            "项目声音库中已批准 BGM 的最低数量。"
            "低于此值时冷启动自动生成基础调色板（状态为 pending，待用户审核）。"
        ),
    )
    tts_background_pipeline_concurrency: int = Field(
        default=1,
        ge=1,
        le=3,
        description=(
            "后台整章配音流水线的并发数；默认 1，让多章配音排队。"
            "调度器始终为小说任务保留至少一个全局 worker，流水线内部仍按片段并行。"
        ),
    )
    tts_max_concurrent_synthesis: int = Field(
        default=4,
        ge=1,
        le=20,
        description="最大并发合成数。",
    )
    tts_synthesis_requests_per_minute: int = Field(
        default=45,
        ge=1,
        le=600,
        description="远程 TTS 合成的最大请求速率（每分钟）。所有重试也计入该额度。",
    )
    tts_synthesis_rate_limit_cooldown_s: int = Field(
        default=60,
        ge=5,
        le=600,
        description="远程 TTS 返回限流后，整条合成队列暂停请求的秒数。",
    )
    tts_synthesis_retry_limit: int = Field(
        default=3,
        ge=0,
        le=10,
        description="单段合成最大重试次数。",
    )
    # ── 波次合成模型（MiniMax）────────────────────────────────────────────────
    tts_wave_pool_enabled: bool = Field(
        default=True,
        description=(
            "MiniMax 合成使用波次池调度：3 并发/波、波间同步等待、失败不内联"
            "重试、波后统一重试（对齐参考 Phase-4 波次生成模型）。"
        ),
    )
    tts_wave_pool_size: int = Field(
        default=3,
        ge=1,
        le=8,
        description="每波最大并发合成调用数（参考模型固定为 3）。",
    )
    tts_wave_pool_retry_rounds: int = Field(
        default=2,
        ge=0,
        le=5,
        description="波后统一重试的最大轮数（每轮仍按波执行）。",
    )
    tts_wave_retry_delay_seconds: float = Field(
        default=20.0,
        ge=0.0,
        le=120.0,
        description="每轮重试前等待的秒数（参考模型 sleep 20-30s）。",
    )
    tts_output_format: str = Field(
        default="mp3",
        description="默认输出格式: mp3/wav/flac/pcm。",
    )
    tts_project_max_storage_mb: int = Field(
        default=1024,
        ge=64,
        le=20480,
        description="单个项目 tts/ 目录的总容量上限（MB）；超过时拒绝写入新的音频产物。",
    )
    tts_project_max_file_mb: int = Field(
        default=256,
        ge=8,
        le=2048,
        description="单个项目音频文件的容量上限（MB）；防止 Provider 异常输出超大文件。",
    )
    tts_sample_rate: int = Field(
        default=32000,
        description="默认采样率。",
    )
    tts_narrator_voice_id: str = Field(
        default="",
        description="旁白默认音色 ID。",
    )
    tts_voice_clone_ttl_days: int = Field(
        default=7,
        ge=1,
        le=30,
        description="克隆音色有效期（天）。",
    )
    tts_segment_quality_enabled: bool = Field(
        default=True,
        description="人声片段进入时间线前检查时长、负载和说话速率。",
    )
    tts_segment_min_duration_ms: int = Field(default=100, ge=20, le=2000)
    tts_segment_max_characters_per_second: float = Field(default=20.0, ge=5.0, le=50.0)
    # Advisory loudness / dead-air thresholds for segment quality (warnings,
    # not hard gates). Defaults disable each check so existing behavior is
    # unchanged until an operator tightens them.
    tts_segment_min_rms_db: float = Field(
        default=-100.0,
        ge=-100.0,
        le=0.0,
        description=(
            "人声片段 RMS 下限（dBFS）。低于该值产生 quality_warning（不阻断）。"
            "默认 -100.0 等于禁用；建议 -35.0 ~ -25.0。"
        ),
    )
    tts_segment_max_peak_db: float = Field(
        default=0.0,
        ge=-12.0,
        le=0.0,
        description=(
            "人声片段峰值上限（dBFS）。高于该值产生 quality_warning（疑似爆音）。"
            "默认 0.0（数字满刻度）等于禁用；建议 -1.0 ~ -0.5。"
        ),
    )
    tts_segment_max_silence_ratio: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "人声片段静音比例上限（0.0-1.0）。超过该值产生 quality_warning（异常停顿）。"
            "默认 1.0 等于禁用；建议 0.3 ~ 0.5。"
        ),
    )
    tts_alignment_repair_rounds: int = Field(default=1, ge=0, le=3)
    tts_min_alignment_coverage: float = Field(default=0.85, ge=0.0, le=1.0)
    tts_max_text_error_rate: float = Field(default=0.12, ge=0.0, le=1.0)
    tts_alignment_max_concurrent: int = Field(
        default=2,
        ge=1,
        le=4,
        description=(
            "ASR 转写与强制对齐的并发段数；本地模型仍由资源租约串行化，云端引擎可获得真实并发。"
        ),
    )
    tts_asr_cache_ttl_hours: int = Field(
        default=24,
        ge=1,
        le=720,
        description=(
            "ASR 转写与强制对齐结果的磁盘缓存 TTL（小时）。修复轮重合成后"
            "相同音频可命中缓存，避免重复调用离线引擎。"
        ),
    )
    tts_asr_cache_enabled: bool = Field(
        default=True,
        description="启用 ASR 转写/对齐结果的磁盘缓存；关闭后每次重算。",
    )
    tts_master_quality_gate_blocking: bool = Field(
        default=True,
        description="master 预设未通过客观音频质量门时不标记产物完成。",
    )
    tts_subtitle_word_level: bool = Field(
        default=False,
        description=(
            "启用字词级 SRT 字幕；MiniMax 同时请求原生 word subtitle，其余平台使用 ASR "
            "对齐的 token 时间线。默认保持片段级字幕，开启后每个字词一行时间轴。"
        ),
    )
    tts_audio_quality_tier: Literal["audition", "standard", "commercial"] = Field(
        default="standard",
        description=(
            "音频质量-成本档位：audition=单遍母带并跳过跨章响度校准（最快最省），"
            "standard=两遍响度母带（默认），commercial=两遍母带并强制跨章一致性。"
        ),
    )
    tts_monthly_cost_budget_usd: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "TTS 月度成本预算上限（美元）。0 表示不限制。"
            "跨项目共享同一进程内预算池；每月 1 号重置。超额时抛出 BudgetExceededError。"
        ),
    )
    tts_book_cost_budget_usd: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "TTS 单书成本预算上限（美元），按 voice_team_hash 维度累计。"
            "0 表示不限制。超额时抛出 BudgetExceededError。"
            "与 tts_monthly_cost_budget_usd 独立，两者任一超额即触发。"
        ),
    )
    tts_dashscope_api_key: str = Field(
        default="",
        description="阿里云百炼 (DashScope) API Key。",
    )
    tts_dashscope_model: str = Field(
        default="qwen-audio-3.0-tts-plus",
        description=(
            "阿里百炼正式 TTS 模型。默认使用支持系统音色、声音复刻和自然语言表演指令的 "
            "qwen-audio-3.0-tts-plus。"
        ),
    )
    tts_dashscope_preview_model: str = Field(
        default="",
        description=(
            "阿里百炼试听模型；留空时沿用正式 TTS 模型。已绑定音色的试听仍优先使用音色所属模型。"
        ),
    )
    tts_dashscope_voice_design_model: str = Field(
        default="cosyvoice-v3.5-plus",
        description="阿里百炼声音设计模型；生成的音色会绑定此模型并随配音团队持久化。",
    )
    tts_dashscope_voice_clone_model: str = Field(
        default="qwen-audio-3.0-tts-plus",
        description="阿里百炼声音复刻模型；留空时沿用正式 TTS 模型。",
    )
    tts_dashscope_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1",
        description=("阿里百炼 HTTP API 根地址。生产环境建议填写业务空间专属的 /api/v1 地址。"),
    )
    tts_dashscope_optimize_instructions: bool = Field(
        default=True,
        description="Qwen3-TTS Instruct 自动优化表演指令；精确可复现的对照试验可关闭。",
    )
    tts_dashscope_voice_clone_enable_preprocess: bool = Field(
        default=False,
        description=("Qwen-Audio/CosyVoice 复刻参考音频的降噪增强。安静录音建议关闭以保留声纹。"),
    )
    tts_dashscope_cny_per_usd: float = Field(
        default=7.2,
        gt=0.0,
        description="将百炼人民币字符计费估算折算为项目 USD 预算时使用的汇率。",
    )
    tts_tencent_secret_id: str = Field(
        default="",
        description="腾讯云 SecretId（用于 TTS 签名鉴权）。",
    )
    tts_tencent_secret_key: str = Field(
        default="",
        description="腾讯云 SecretKey。",
    )
    tts_tencent_voice_type: str = Field(
        default="0",
        description="腾讯云默认音色类型 ID。",
    )
    tts_tencent_project_id: int = Field(
        default=0,
        ge=0,
        description="腾讯云项目 ID，默认 0。",
    )
    tts_tencent_primary_language: int = Field(
        default=1,
        ge=1,
        le=2,
        description="腾讯云主语言：1=中文，2=英文。",
    )
    tts_tencent_emotion_intensity: int = Field(
        default=100,
        ge=50,
        le=200,
        description="腾讯云多情感音色强度，100 为平台默认。",
    )
    tts_tencent_segment_rate: int = Field(
        default=0,
        ge=0,
        le=2,
        description="腾讯云断句敏感度；值越大越倾向只按标点断句。",
    )
    tts_volcengine_model: str = Field(
        default="doubao-seed-tts-2.0",
        description="方舟 Agent Plan 豆包语音合成模型。",
    )
    tts_volcengine_resource_id: str = Field(
        default="seed-tts-2.0",
        description="Agent Plan TTS 2.0 请求头资源 ID。",
    )
    tts_volcengine_base_url: str = Field(
        default="https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional",
        description="方舟 Agent Plan TTS HTTP Chunked 接口地址。",
    )
    tts_mimo_api_key: str = Field(
        default="",
        description="小米 MiMo 开放平台普通 API Key；与 Token Plan 凭据分离。",
    )
    tts_mimo_base_url: str = Field(
        default="https://api.xiaomimimo.com/v1",
        description="小米 MiMo TTS OpenAI 兼容 API 地址。",
    )
    tts_mimo_model: str = Field(
        default="mimo-v2.5-tts",
        description="小米 MiMo 预置音色 TTS 模型；V2 旧模型已下线。",
    )
    tts_local_base_url: str = Field(
        default="http://localhost:8000/v1",
        description="本地 TTS 服务 OpenAI 兼容接口地址。",
    )
    tts_local_api_key: str = Field(
        default="",
        description="本地 TTS 服务 API Key（可选）。",
    )
    tts_local_model: str = Field(
        default="default",
        description="本地 TTS 模型名称，例如 Qwen3-TTS、CosyVoice、Fish Speech。",
    )
    tts_qwen3_base_url: str = Field(
        default="http://127.0.0.1:8011/v1",
        description="Qwen3-TTS 隔离 sidecar 的 OpenAI Speech 兼容地址。",
    )
    tts_qwen3_api_key: str = Field(
        default="",
        description="Qwen3-TTS sidecar Bearer Token；只绑定 127.0.0.1 时可留空。",
    )
    tts_qwen3_formal_model: str = Field(
        default="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        description="正式旁白/角色系统音色使用的 Qwen3-TTS CustomVoice 模型。",
    )
    tts_qwen3_preview_model: str = Field(
        default="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        description="角色与旁白快速试听使用的 Qwen3-TTS CustomVoice 模型。",
    )
    tts_qwen3_design_model: str = Field(
        default="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        description="品牌/角色音色设计使用的 Qwen3-TTS VoiceDesign 模型。",
    )
    tts_qwen3_clone_model: str = Field(
        default="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        description="已授权参考音频克隆及设计后固化音色使用的 Qwen3-TTS Base 模型。",
    )
    tts_cosyvoice_base_url: str = Field(
        default="http://127.0.0.1:50000",
        description="官方 CosyVoice FastAPI 服务地址（不带 /v1）。",
    )
    tts_cosyvoice_model: str = Field(
        default="FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        description="本地 CosyVoice 权重标识，仅用于审计与缓存指纹。",
    )
    tts_cosyvoice_mode: str = Field(
        default="instruct2",
        description="CosyVoice 模式: sft/zero_shot/cross_lingual/instruct2。",
    )
    tts_cosyvoice_voice_store: str = Field(
        default_factory=lambda: _default_tts_voice_store("cosyvoice"),
        description="CosyVoice 参考音频与稳定角色音色档案目录。",
    )
    tts_cosyvoice_sample_rate: int = Field(
        default=22050, ge=8000, le=48000, description="官方 FastAPI PCM 输出采样率。"
    )
    tts_openvoice_model: str = Field(
        default="openvoice-v2",
        description="OpenVoice 运行时版本标识。",
    )
    tts_openvoice_checkpoint_dir: str = Field(
        default_factory=_default_openvoice_checkpoint_dir,
        description="OpenVoice V2 checkpoints_v2 目录。",
    )
    tts_openvoice_voice_store: str = Field(
        default_factory=lambda: _default_tts_voice_store("openvoice"),
        description="OpenVoice 声纹 embedding 与参考音频档案目录。",
    )
    tts_openvoice_device: str = Field(
        default="auto", description="OpenVoice 推理设备: auto/cpu/cuda[:N]。"
    )
    tts_openvoice_language: str = Field(
        default="ZH", description="OpenVoice/MeloTTS 默认基础发声语言。"
    )

    # ── Capability-driven audio model platform ────────────────────────────
    audio_quality_preset: str = Field(
        default="production",
        description=(
            "音频质量目标: quick_preview/production/master/low_resource/custom。"
            "质量与执行位置相互独立。"
        ),
    )
    audio_location_policy: str = Field(
        default="hybrid",
        description=(
            "音频执行位置策略: cloud_only/local_only/prefer_cloud/prefer_local/hybrid。"
            "位置策略独立于质量预设；hybrid 默认让 MiniMax 负责人声、本地负责后期。"
        ),
    )
    audio_accelerator_preference: str = Field(
        default="auto",
        description="音频模型首选加速器: auto/cpu/mps/cuda。",
    )
    audio_memory_budget: str = Field(
        default="high",
        description="音频模型常驻内存预算: light/medium/high。",
    )
    audio_plugin_overrides: str = Field(
        default="{}",
        description="JSON 对象：音频阶段到插件 ID 的高级固定选择。",
    )
    audio_language_overrides: str = Field(
        default="{}",
        description="JSON 对象：语言代码到强制对齐插件 ID 的覆盖。",
    )
    audio_disabled_plugins: str = Field(
        default="",
        description="逗号分隔的禁用音频插件 ID。",
    )
    audio_plugin_manifest_dirs: str = Field(
        default="",
        description="逗号分隔的第三方 *.audio-plugin.json 清单目录。",
    )
    audio_dual_alignment_validation: bool = Field(
        default=False,
        description="正式时间线是否使用不同对齐器做独立复核；master 预设会自动启用。",
    )
    audio_preflight_blocking: bool = Field(
        default=True,
        description="生产前预检发现鉴权、隐私、路由或资源硬错误时阻止开始合成。",
    )
    audio_budget_limit_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="单章音频云服务预算上限（美元）；0 表示不设硬上限但仍记录实际费用。",
    )
    audio_qwen3_asr_base_url: str = Field(
        default="http://127.0.0.1:8012/v1",
        description="Qwen3-ASR / ForcedAligner 隔离 sidecar 地址。",
    )
    audio_qwen3_asr_api_key: str = Field(
        default="",
        description="Qwen3-ASR sidecar Bearer Token。",
    )
    audio_whisperx_base_url: str = Field(
        default="http://127.0.0.1:8013/v1",
        description="WhisperX 独立校验 sidecar 地址。",
    )
    audio_sherpa_base_url: str = Field(
        default="http://127.0.0.1:8014/v1",
        description="Sherpa ONNX 轻量 ASR/VAD sidecar 地址。",
    )
    audio_models_root: str = Field(
        default_factory=_default_audio_models_root,
        description="可迁移的应用级音频模型库根目录；不属于任何项目。",
    )
    audio_mfa_command: str = Field(
        default="mfa",
        description="Montreal Forced Aligner CLI 命令或绝对路径。",
    )

    # ── Generated Sound Assets (BGM / ambience / SFX) ─────────────────────
    tts_automation_mode: Literal["manual", "assisted", "autonomous"] = Field(
        default="assisted",
        description=(
            "配音默认推进模式：manual 全人工、assisted AI 伴随、"
            "autonomous 全 AI 自主。单次请求可覆盖该默认值。"
        ),
    )
    sound_generation_enabled: bool = Field(
        default=False,
        description="启用配音流程中的生成式 BGM、环境声和短音效能力。",
    )
    sound_generation_auto_generate: bool = Field(
        default=False,
        description="素材库未匹配时自动提交声音生成任务；关闭时仅报告缺失素材。",
    )
    sound_generation_auto_approve: bool = Field(
        default=False,
        description="自动批准生成资产并进入正式混音；关闭时资产保留为待审核。",
    )
    sound_generation_default_provider: str = Field(
        default="auto",
        description="声音生成 Provider: auto/minimax_music/stable_audio/自定义扩展 Provider。",
    )
    sound_generation_max_duration_s: int = Field(
        default=120,
        ge=1,
        le=600,
        description="单个生成声音资产的时长上限（秒）。",
    )
    sound_generation_output_format: str = Field(
        default="wav",
        description="生成资产的首选格式: wav/mp3/flac。",
    )
    sound_generation_timeout_s: int = Field(
        default=300,
        ge=10,
        le=1800,
        description="单个声音生成任务的超时秒数。",
    )
    sound_generation_minimax_api_key: str = Field(
        default="",
        description="MiniMax Music API Key；为空时回退复用 TTS/LLM 的 MiniMax Key。",
    )
    sound_generation_minimax_music_endpoint: str = Field(
        default="https://api.minimax.io/v1/music_generation",
        description=(
            "MiniMax Music Generation API 端点；默认使用官方全球端点。"
            "可按账号区域使用官方兼容网关。"
        ),
    )
    sound_generation_minimax_music_model: str = Field(
        default="music-2.6",
        description="MiniMax 纯伴奏生成模型。",
    )
    sound_generation_stable_audio_command: str = Field(
        default="stable-audio",
        description="Stable Audio 3 本地 CLI 命令或可执行文件路径。",
    )
    sound_generation_stable_audio_models_dir: str = Field(
        default_factory=_default_stable_audio_models_dir,
        description="Stable Audio 受管 Hugging Face 模型缓存根目录；属于应用级模型库，供所有项目复用。",
    )
    sound_generation_huggingface_token: str = Field(
        default="",
        description="用于下载需要访问审批的 Stable Audio 模型的 Hugging Face Token。",
    )
    sound_generation_stable_audio_sfx_model: str = Field(
        default="small-sfx",
        description="Stable Audio 本地环境声 / SFX 模型 ID。",
    )
    sound_generation_stable_audio_music_model: str = Field(
        default="small-music",
        description="Stable Audio 本地纯伴奏模型 ID。",
    )

    # ── Sound Generation: Post-processing ───
    sound_generation_postprocess_enabled: bool = Field(
        default=True,
        description="启用生成音频的确定性后处理（高通滤波、尾部裁剪、淡出、响度归一化）。",
    )
    sound_generation_sfx_target_lufs: float = Field(
        default=-18.0,
        ge=-40.0,
        le=-8.0,
        description="SFX 后处理目标响度（LUFS）。",
    )
    sound_generation_soundscape_target_lufs: float = Field(
        default=-20.0,
        ge=-40.0,
        le=-8.0,
        description="环境声后处理目标响度（LUFS）。",
    )
    sound_generation_highpass_hz: int = Field(
        default=40,
        ge=0,
        le=200,
        description="后处理高通滤波截止频率（Hz）；0 表示禁用。",
    )
    sound_generation_tail_fade_sfx_ms: int = Field(
        default=150,
        ge=0,
        le=2000,
        description="SFX 尾部淡出时长（毫秒）。",
    )
    sound_generation_tail_fade_soundscape_ms: int = Field(
        default=500,
        ge=0,
        le=5000,
        description="环境声尾部淡出时长（毫秒）。",
    )

    # ── Sound Generation: Quality Gate & Retry ───
    sound_generation_quality_gate_enabled: bool = Field(
        default=True,
        description="启用生成音频质量门检查（时长偏差、响度范围、削波检测）。",
    )
    sound_generation_max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="质量门不通过时的最大重试次数（使用不同 seed）。",
    )
    tts_sound_generation_max_concurrent: int = Field(
        default=2,
        ge=1,
        le=4,
        description=(
            "声音生成（BGM/SFX/环境声）cue 的最大并发生成数；"
            "网络 IO 为主，并发可显著缩短整章声音阶段耗时。"
        ),
    )

    # ── Sound Generation: ACE-Step Provider ───
    sound_generation_ace_step_command: str = Field(
        default="",
        description="ACE-Step 1.5 本地 CLI 命令或可执行文件路径；为空表示未安装。",
    )
    sound_generation_ace_step_models_dir: str = Field(
        default="",
        description="ACE-Step 模型权重缓存目录；为空时使用默认 Hugging Face 缓存。",
    )

    # ── Macro Guard (Multi-chapter trajectory audit) ───
    long_macro_guard_enabled: bool = Field(
        default=True,
        description="Enable macro-level guard audit after quality checks and before finalize.",
    )
    long_macro_guard_interval: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Trigger full macro audit every N chapters.",
    )
    long_macro_guard_max_adjustments_per_book: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum outline adjustments allowed per book. 0 disables adjustments entirely.",
    )
    long_macro_guard_min_chapters_between_adjustments: int = Field(
        default=8,
        ge=3,
        le=50,
        description="Minimum chapters between two outline adjustments.",
    )
    long_macro_guard_cooldown_chapters: int = Field(
        default=5,
        ge=0,
        le=20,
        description="Chapters to skip macro audit after an adjustment.",
    )
    long_macro_guard_drift_threshold_warning: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Drift score above this triggers warning_hint (non-blocking).",
    )
    long_macro_guard_drift_threshold_alert: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Drift score above this triggers alert_plan (user confirmation in manual mode).",
    )
    long_macro_guard_drift_threshold_critical: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Drift score above this triggers critical_rollback recommendation.",
    )
    long_macro_guard_auto_apply_hint: bool = Field(
        default=True,
        description="In auto-run mode, automatically apply warning_hint constraints to next chapter plan without pausing.",
    )
    long_macro_guard_critical_blocks_archive: bool = Field(
        default=True,
        description=(
            "When MacroGuard emits critical_rollback, block the next planning pass "
            "with a replan marker instead of treating it only as a soft future hint."
        ),
    )
    long_macro_guard_max_audit_chapters: int = Field(
        default=5,
        ge=2,
        le=10,
        description="Number of recent chapters to include in macro audit window.",
    )
    long_macro_guard_max_tokens: int = Field(
        default=2048,
        ge=512,
        le=8192,
        description="Output token limit for macro guard LLM call.",
    )
    long_book_audit_default_mode: Literal["summary", "full_text"] = Field(
        default="full_text",
        description=(
            "全书审计默认分析模式。"
            "summary=仅基于章节摘要审计（更省 token）；"
            "full_text=注入章节全文进行深审（推荐，支持精确定位某章某段）。"
        ),
    )
    long_book_audit_chapter_max_chars: int = Field(
        default=20000,
        ge=1000,
        le=100000,
        description=(
            "全书审计中单章注入的最大字符数（仅 full_text 模式生效）。"
            "过长章节会截断并在报告中标记。"
        ),
    )
    long_book_audit_prompt_char_budget: int = Field(
        default=48000,
        ge=12000,
        le=500000,
        description=(
            "全书审计单次模型请求的提示词字符预算。"
            "full_text 模式会按该预算自动拆分章节全文，避免超过模型上下文窗口。"
        ),
    )
    long_book_audit_max_chapters_per_batch: int = Field(
        default=12,
        ge=1,
        le=500,
        description="全书审计 full_text 模式下单次模型请求最多审查的章节数。",
    )
    long_book_audit_max_tokens: int = Field(
        default=8192,
        ge=1024,
        le=65536,
        description="全书审计任务的输出 token 上限。",
    )
    long_book_audit_max_issues_per_chunk: int = Field(
        default=12,
        ge=1,
        le=50,
        description="全书审计单次模型调用最多返回的问题数，避免 JSON 输出截断。",
    )
    long_book_audit_issue_pool_max_items: int = Field(
        default=160,
        ge=0,
        le=1000,
        description="全书审计注入问题面板问题池的最大条目数，按严重度优先压缩。",
    )
    long_book_audit_issue_pool_ttl_hours: int = Field(
        default=72,
        ge=0,
        description="问题池条目过期时间（小时），超过此时间的条目将被过滤。0=不过滤。默认72小时。",
    )
    long_book_audit_legacy_hash_grace_hours: int = Field(
        default=24,
        ge=0,
        description=(
            "旧报告缺少 chapter_text_hash/canon_state_hash 时的宽限期（小时）。"
            "在此期限内缺失 hash 元数据的旧报告仍可复用，避免升级后首次审计的性能尖峰。"
            "超出宽限期后缺失 hash 的报告被视为过期。"
        ),
    )
    long_book_audit_prompt_hint: str = Field(
        default="",
        description="全书审计附加提示词（可选），用于强调本轮审计偏好和关注点。",
    )
    long_book_audit_location_strictness: Literal["strict", "balanced", "loose"] = Field(
        default="balanced",
        description=(
            "全书审计定位严格度。"
            "strict=优先输出可精确落段的问题；"
            "balanced=精确定位与覆盖面平衡（推荐）；"
            "loose=允许较多范围级问题。"
        ),
    )
    long_book_audit_auto_repair: bool = Field(
        default=False,
        description="全书审计后是否默认自动进入逐章修复模式。",
    )
    long_book_audit_parallel_chunks: bool = Field(
        default=True,
        description=(
            "全书审计分块并行化。启用后多块 LLM 调用同时发出（上限 5），"
            "可将审计耗时降低 2-4 倍，不影响审计质量。"
        ),
    )
    long_book_audit_parallel_dimensions: bool = Field(
        default=True,
        description=(
            "全书审计多维度并行化。启用后按维度裁剪上下文、维度内串行切片、"
            "维度间按依赖 DAG 并行执行，并由全局并发上限统一控制。"
        ),
    )
    long_book_audit_parallel_dimension_limit: int = Field(
        default=3,
        ge=1,
        le=6,
        description="全书审计维度并行化的并发上限。默认 3，避免与分块并行叠加后压垮模型网关。",
    )
    split_tasks_enabled: bool = Field(
        default=True,
        description="启用章节抽取、全书审计等巨型 JSON 任务的分片执行、本地合并与局部重试。",
    )
    init_fragment_max_parallel: int = Field(
        default=4,
        ge=1,
        le=8,
        description="长篇初始化分片任务的并发上限。",
    )
    init_character_profile_parallel_min_roster: int = Field(
        default=11,
        ge=1,
        le=50,
        description=(
            "角色档案达到该人数后才分批并行生成。低于阈值保持单批生成，以提升小角色表的整体自洽。"
        ),
    )
    init_character_profile_batch_size: int = Field(
        default=5,
        ge=1,
        le=12,
        description="角色档案并行生成时每个分片包含的角色数。建议 4-6。",
    )
    init_kb_phase_c_parallel: bool = Field(
        default=True,
        description=(
            "知识边界生成与 Phase C（StyleProfile/EntityGraph）并行执行；关闭则恢复"
            "KB 先行、Style/Entity 后行的串行结构。并行时 Style/Entity 使用 KB 应用前的"
            "character_bible 快照构造 prompt，最终一致性由感官规则后同步兜底。"
        ),
    )
    outline_batch_size: int = Field(
        default=0,
        ge=0,
        le=50,
        description=(
            "章节大纲分批生成时每批章数上限。0=按当前路由模型输出能力自动计算；"
            "为降低长 JSON 截断/漏括号风险，显式值也会受大纲安全上限约束。"
        ),
    )
    init_outline_beats_min: int = Field(
        default=4,
        ge=1,
        le=20,
        description="初始化章节大纲中每章 beats_summary 的建议下限。",
    )
    init_outline_beats_max: int = Field(
        default=8,
        ge=1,
        le=30,
        description="初始化章节大纲中每章 beats_summary 的建议上限。",
    )
    init_outline_main_plot_points_min: int = Field(
        default=2,
        ge=1,
        le=12,
        description="初始化章节大纲中每章 main_plot_points 的建议下限。",
    )
    init_outline_main_plot_points_max: int = Field(
        default=4,
        ge=1,
        le=20,
        description="初始化章节大纲中每章 main_plot_points 的建议上限。",
    )
    init_outline_subplot_points_max: int = Field(
        default=3,
        ge=0,
        le=12,
        description="初始化章节大纲中每章 subplot_points 的建议上限。",
    )
    init_outline_element_focus_max: int = Field(
        default=3,
        ge=0,
        le=3,
        description="初始化章节大纲中每章 element_focus 的上限；schema 最大值为 3。",
    )
    init_outline_expected_payoffs_min: int = Field(
        default=1,
        ge=0,
        le=8,
        description="初始化章节大纲中每章 expected_payoffs 的建议下限。",
    )
    init_outline_expected_payoffs_max: int = Field(
        default=3,
        ge=0,
        le=12,
        description="初始化章节大纲中每章 expected_payoffs 的建议上限。",
    )
    world_rule_count_min: int = Field(
        default=10,
        ge=4,
        le=20,
        description="世界规则账本最少规则数；低于此值触发治理警告。推荐 10。",
    )
    world_rule_count_max: int = Field(
        default=14,
        ge=6,
        le=24,
        description="世界规则账本最多规则数；超出触发治理警告。推荐 14。",
    )
    world_rule_hard_min: int = Field(
        default=3,
        ge=1,
        le=10,
        description="世界规则账本最少硬规则数；低于触发治理警告。推荐 3。",
    )
    world_rule_always_on_hard_cap: int = Field(
        default=4,
        ge=1,
        le=10,
        description="始终生效（always_on=true）的硬规则上限；超出会被自动降级为条件规则并警告。推荐 4，避免分散章节模型注意力。",
    )
    world_rule_category_min: int = Field(
        default=4,
        ge=1,
        le=6,
        description="世界规则至少覆盖的类别数。推荐 4（time_space/social_language/resource_material/information/ability_tech/general 中至少 4 类）。",
    )
    world_rule_ability_required: bool = Field(
        default=True,
        description="当世界包含魔法/异能/科技体系时，规则账本必须覆盖 ability_tech 类别。",
    )
    world_rule_block_on_violation: bool = Field(
        default=False,
        description="世界规则治理违反是否阻断初始化。默认 false（仅警告）；true 时规则数/类别等硬约束违反会中止初始化。",
    )
    chapter_contract_batch_size: int = Field(
        default=0,
        ge=0,
        le=50,
        description="章节契约分批生成时每批章数。0=按当前路由模型输出能力自动计算。",
    )
    canon_extract_max_parallel: int = Field(
        default=4,
        ge=1,
        le=8,
        description="Canon 提取分片任务的并发上限。",
    )
    book_audit_max_parallel: int = Field(
        default=3,
        ge=1,
        le=8,
        description="全书编辑/一致性审计维度任务的并发上限。",
    )
    patch_first_repair: bool = Field(
        default=True,
        description="章节修复优先输出结构化 patch；失败或无法应用时再回退全文修复。",
    )
    long_book_audit_repair_min_severity: Literal["critical", "warning", "info"] = Field(
        default="warning",
        description="全书审计自动修复的最低严重度阈值。",
    )
    long_book_audit_repair_max_chapters: int = Field(
        default=12,
        ge=1,
        le=500,
        description="全书审计自动修复时最多处理的章节数量。",
    )
    long_book_audit_max_continue_batches: int = Field(
        default=10,
        ge=1,
        le=50,
        description="全书审计自动续修最大批次数。达到此批次数后停止续修。",
    )
    long_book_audit_use_issue_panel_pool: bool = Field(
        default=True,
        description=(
            "全书审计时是否注入问题面板问题池（连贯性/因果报告）作为锚点，"
            "用于提升某章某段定位与修复索引匹配精度。"
        ),
    )
    long_book_audit_repair_concurrency: int = Field(
        default=1,
        ge=1,
        le=8,
        description=(
            "全书审计自动修复的并发上限。"
            "1=串行最稳；>1 使用有界并发调度（写入阶段仍受项目锁保护）。"
        ),
    )
    long_book_audit_generate_repair_report: bool = Field(
        default=True,
        description="全书审计后是否生成卷帙可查看的全书修复报告。",
    )
    long_book_audit_panel_first_expansion: bool = Field(
        default=True,
        description=(
            "修复时是否优先处理问题面板中的 high/critical 遗留问题。"
            "开启后，自动修复会额外带入面板中已有的高/关键级别问题（每通道最多 3 条）。"
        ),
    )
    long_book_audit_verify_before_repair: bool = Field(
        default=True,
        description=(
            "修复前是否逐章验证审计问题，过滤幻觉并精确定位段落索引。"
            "可有效提升修复精度，额外成本约每章 15K tokens 输入 + 2K 输出。"
        ),
    )
    long_book_audit_post_repair_targeted_audit: bool = Field(
        default=False,
        description=(
            "全书自动修复后是否对已修章节与传播影响章节再跑小范围全文审计。"
            "默认关闭以避免额外模型成本；终章验收建议开启。"
        ),
    )
    long_book_verify_parallel_chunks: bool = Field(
        default=True,
        description=(
            "验证阶段是否并行执行多章验证。启用后使用 asyncio.gather + Semaphore "
            "并发调度，可将验证耗时降低 2-4 倍，不影响验证质量。"
        ),
    )
    long_book_verify_max_parallel: int = Field(
        default=5,
        ge=1,
        le=16,
        description="验证阶段最大并行并发度。与 repair_concurrency 独立控制。",
    )
    long_book_audit_repair_guard_enabled: bool = Field(
        default=True,
        description="全书自动修复后启用正文污染防护，疑似污染会回滚该章并转人工复核。",
    )
    long_book_audit_repair_guard_max_delta_ratio: float = Field(
        default=0.12,
        ge=0.01,
        le=1.0,
        description="全书自动修复单章最大正文改动比例，超限会自动回滚。",
    )
    long_book_audit_repair_guard_max_added_chars: int = Field(
        default=600,
        ge=100,
        le=10000,
        description="全书自动修复单章最大新增字符数，超限会自动回滚。",
    )
    long_book_audit_two_phase_enabled: bool = Field(
        default=True,
        description="是否启用两阶段审计模式。True=先 summary 扫描再 targeted full_text；False=直接 full_text。",
    )
    long_book_audit_two_phase_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="两阶段审计标记率阈值。超过阈值时按优先级截断目标章节，避免回退全量 full_text。",
    )
    long_book_audit_two_phase_max_target_chapters: int = Field(
        default=24,
        ge=1,
        le=500,
        description="两阶段审计进入定向全文深审时最多审查的目标章节数。",
    )
    long_book_audit_memory_enhancement_enabled: bool = Field(
        default=False,
        description=(
            "是否在全书一致性审计中启用内存增强上下文。"
            "开启后，BookConsistencyStep 会调用 AuditCoordinator 准备跨章节语义检索上下文，"
            "并将其注入 memory_enhancement_context 字段。默认关闭以避免额外开销。"
        ),
    )
    long_book_audit_semantic_verify_enabled: bool = Field(
        default=False,
        description="修复后是否启用 LLM 语义验证，确认问题真正解决。",
    )
    long_book_audit_semantic_check_budget_tokens: int = Field(
        default=5000,
        ge=0,
        description="语义验证的 token 预算，耗尽后降级为字符串匹配。",
    )
    long_auto_chapter_cooldown_seconds: int = Field(
        default=5,
        ge=0,
        le=3600,
        description="章节连跑时每章完成后的冷却间隔（秒），0=不等待。默认 5 秒以缓冲 provider 限流恢复。",
    )
    long_auto_chapter_timeout_seconds: int = Field(
        default=3600,
        ge=60,
        le=14400,
        description=(
            "CLI 自动连跑中单章生成的最大允许时长（秒）。"
            "超时后当前章节将被视为失败并停止连跑，避免网络分区或 API 挂起导致无限阻塞。"
            "默认 3600 秒（1 小时），可通过 NOVEL_FORGE_LONG_AUTO_CHAPTER_TIMEOUT_SECONDS 调整。"
        ),
    )
    long_waiting_job_poll_interval_ms: int = Field(
        default=3000,
        ge=500,
        le=30000,
        description="Desktop waiting job 轮询间隔（毫秒）。poller 定期检查 _waiting_jobs 中依赖已就绪的 job 并 promote。",
    )
    long_polish_reaudit_skip_similarity_threshold: float = Field(
        default=0.99,
        ge=0.0,
        le=1.0,
        description=(
            "精修后跳过 continuity/causal 重新审计的文本相似度阈值。"
            "默认 0.99，仅在近乎无改动时跳过。"
        ),
    )
    long_prompt_max_character_profiles: int = 8
    long_prompt_max_profile_field_chars: int = 240
    long_prompt_profile_source_field_chars: int = 900
    long_prompt_max_relationships_per_profile: int = 6
    long_plan_prev_report_max_deviations: int = 2
    long_plan_prev_report_max_new_characters: int = 3
    long_plan_prev_report_text_chars: int = 260
    long_plan_prev_report_source_chars: int = 1200
    long_plan_max_key_revelations_per_chapter: int = Field(
        default=2,
        ge=1,
        le=8,
        description=(
            "章节规划中的重大揭示上限。超过上限的揭示应降级为伏笔或阶段性线索，避免信息密度过高。"
        ),
    )
    max_revelations_per_chapter: int = Field(
        default=2,
        ge=1,
        le=10,
        description=(
            "单章正文中揭示标记（原来/竟然/才发现等）的数量上限。"
            "超出此上限时会在 review_warnings 中生成警告。"
        ),
    )
    long_plan_max_scene_switches: int = Field(
        default=8,
        ge=2,
        le=12,
        description=(
            "章节规划中的场景切换数量上限（scene_intents 个数上限）。"
            "默认与章节 beats 上限一致，避免把不同时间/地点的节拍压成同一场；"
            "执行时会至少放宽到本章大纲节拍数（最多 12），避免压缩大纲驱动的 scene；"
            "超出有效上限时，才将末尾多余场景合并入倒数第二个场景，防止信息密度过高与跳跃感。"
        ),
    )
    long_plan_sensory_notes_max_items: int = Field(
        default=2,
        ge=1,
        le=2,
        description=(
            "每个 scene 传递给 Draft/Edit 的感官候选锚点上限。"
            "Plan 仍可构思 2-4 个候选，但下游只接收排序后的前 N 个，控制正文细节密度。"
        ),
    )
    long_literary_contract_enabled: bool = Field(
        default=True,
        description=(
            "是否启用章节源头切片中的六要素文学合同。开启后 Plan/Draft/Wave "
            "从 source.literary_contract 消费人物、情节、场景、POV、主题与风格职责。"
        ),
    )
    long_literary_contract_plan_retry_enabled: bool = Field(
        default=True,
        description=(
            "是否在 PLAN_CHAPTER 输出未覆盖六要素关键职责时自动重试一次。"
            "默认开启；重试后仍存在关键缺失时，计划阶段会阻断并交由一致性重规划处理，"
            "不会让缺失关键职责的计划进入正文。"
        ),
    )
    long_literary_contract_review_gate_mode: str = Field(
        default="warn",
        description=(
            "六要素文学合同 review gate 模式。v1 默认 warn，只验证和记录；"
            "后续可切换为 critical/strict。"
        ),
    )
    long_scene_draft_max_parallel: int = Field(
        default=5,
        ge=1,
        le=12,
        description="场景级写作模式下同一拓扑批次内 DRAFT_SCENE 的最大并发数。",
    )
    # ── Performance optimization switches (one-click rollback) ──
    long_perf_memory_parallel_enabled: bool = Field(
        default=True,
        description="Stage Memory Builder 并行收集开关。设为 False 回退到串行收集。",
    )
    long_perf_embedding_prefetch_enabled: bool = Field(
        default=True,
        description="Planning 完成后预生成 Draft 阶段 embedding 查询向量。设为 False 禁用预热。",
    )
    long_perf_chapter_prefetch_enabled: bool = Field(
        default=True,
        description="连跑模式下预取下一章静态文件到 OS 缓存。设为 False 禁用预取。",
    )
    long_perf_adaptive_compress_enabled: bool = Field(
        default=True,
        description="低 budget_pressure 时跳过 LLM 压缩调用。设为 False 始终使用完整压缩。",
    )
    long_perf_storage_async_enabled: bool = Field(
        default=True,
        description="关键路径持久化操作使用 asyncio.to_thread 异步化。设为 False 回退到同步写入。",
    )
    long_guidance_plan_word_budget_tolerance: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="章节 plan 结构审计允许 scene target_words 总和偏离目标字数的比例。",
    )
    long_opening_guard_enabled: bool = Field(
        default=True,
        description=(
            "是否启用开场硬门禁。开启后会在质量检查前对开场承接做本地预筛，"
            "命中高置信度断裂时先尝试窄窗口补丁修复。"
        ),
    )
    long_opening_guard_max_issues: int = Field(
        default=2,
        ge=1,
        le=6,
        description="开场硬门禁单次最多处理的问题数量（按严重度和置信度排序）。",
    )
    long_upstream_compass_enabled: bool = Field(
        default=True,
        description="Enable the early Bridge/Plan direction gate before draft generation.",
    )
    long_upstream_compass_blocking: bool = Field(
        default=True,
        description="When enabled, high-severity Bridge/Plan compass findings force replanning.",
    )
    long_semantic_consistency_enabled: bool = Field(
        default=True,
        description="Project the existing claim-ledger semantic compiler into chapter preflight.",
    )
    long_semantic_consistency_blocking: bool = Field(
        default=False,
        description=(
            "Promote compiled semantic status to a chapter hard gate. False keeps the "
            "model chain in shadow while the legacy fallback remains active."
        ),
    )
    long_semantic_plan_check_enabled: bool = Field(
        default=True,
        description="Re-use claim extraction/adjudication to compare a generated Plan with sources.",
    )
    long_upstream_compass_min_scenes: int = Field(
        default=1,
        ge=1,
        le=20,
        description="Minimum number of scene intents required by the upstream compass gate.",
    )
    long_upstream_compass_word_budget_tolerance: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="Allowed scene target-word drift before the upstream compass blocks a plan.",
    )
    long_context_compress_enabled: bool = Field(
        default=True,
        description="Enable model-based compression for long-chapter prompt context.",
    )
    long_context_compress_quality_min_score: float = Field(
        default=0.62,
        ge=0.0,
        le=1.0,
        description="Minimum deterministic fidelity score for accepting compressed context.",
    )
    long_context_compress_llm_verify_enabled: bool = Field(
        default=True,
        description="Use VERIFY_COMPRESSION for borderline compressed context before accepting it.",
    )
    long_context_compress_llm_verify_margin: float = Field(
        default=0.12,
        ge=0.0,
        le=0.5,
        description="Only run LLM verification when heuristic score is within this margin above the floor.",
    )
    long_context_compress_llm_verify_max_items: int = Field(
        default=3,
        ge=0,
        le=20,
        description="Maximum compressed context items to send to VERIFY_COMPRESSION per chapter.",
    )
    long_context_compress_adaptive_skip_enabled: bool = Field(
        default=True,
        description=(
            "Skip the LLM compression call entirely at low budget pressure; "
            "static field limits already applied then suffice."
        ),
    )
    long_check_chapter_enabled: bool = True
    long_pre_wave_chapter_check_enabled: bool = Field(
        default=False,
        description=(
            "Run a diagnostics-only ChapterRepairStep on raw DRAFT before WAVE. "
            "The authoritative chapter check still runs after WAVE when "
            "long_check_chapter_enabled is true."
        ),
    )
    long_wave_post_condition_policy: Literal["warn", "repair", "block"] = Field(
        default="repair",
        description=(
            "How Review handles WAVE post-condition failures: warn=diagnostics only; "
            "repair=turn blocking failures into one bounded repair pass; "
            "block=block archive when blocking failures remain."
        ),
    )
    long_wave_word_count_policy: Literal["inherit", "enforce", "warn"] = Field(
        default="inherit",
        description=(
            "How Review handles WAVE word-count post-condition failures: "
            "inherit=follow long_word_count_archive_gate_enabled; "
            "enforce=always treat word-count drift as blocking; "
            "warn=diagnostics only. Structural WAVE post-conditions still follow "
            "long_wave_post_condition_policy."
        ),
    )
    long_wave_regression_floor_ratio: float = Field(
        default=0.4,
        ge=0.1,
        le=0.9,
        description=(
            "WAVE 产出字数低于 draft 字数该比例时判定为回退并跳过编织（保留原 draft）。"
            "默认 0.4 比 0.5 更宽松，避免仅差几个字就触发跳过；"
            "同时有 200 字绝对下限保护短章节。"
        ),
    )
    long_eval_repair_enabled: bool = Field(
        default=True,
        description="Route high-priority long-chapter eval suggestions into Review repair tickets.",
    )
    long_eval_reuse_enabled: bool = Field(
        default=True,
        description="Reuse an in-run EvalReport only when both source_text_hash and eval_context_hash match.",
    )
    long_contract_runtime_repair_enabled: bool = Field(
        default=True,
        description="Enable one-shot runtime JSON contract repair before prose fallback when a contract execution audit points to source-contract drift.",
    )
    long_causal_repair_enabled: bool = True
    long_causal_threshold: float = 5.0
    long_causal_high_score_skip_enabled: bool = Field(
        default=True,
        description="因果校验高分且无高危问题时跳过因果修复循环，保留质量阶段校验报告。",
    )
    long_causal_skip_threshold: float = Field(
        default=9.5,
        ge=0.0,
        le=10.0,
        description="因果高分短路阈值；仅在无 high/critical 问题且校验状态正常时生效。",
    )
    long_causal_max_repair_rounds: int = Field(
        default=2,
        ge=0,
        le=5,
        description="因果链修复的最大循环轮次，0=禁用因果修复，每轮修复后若无必修问题或分数达标则提前退出",
    )
    long_continuity_max_repair_rounds: int = Field(
        default=2,
        ge=0,
        le=5,
        description="连贯性修复的最大循环轮次，0=禁用连贯性修复流程，每轮修复后若无必修问题则提前退出",
    )
    dynamic_continuity_filter: bool = Field(
        default=False,
        description=(
            "是否在连贯性评估中启用动态母题上下文。"
            "开启后会将 memory_ctx 中的母题追踪数据（active_motifs / forbidden_repetition / "
            "suggested_callbacks）注入 ContinuityEvalInput.motif_context，"
            "帮助 LLM 在评估时参考母题使用情况。"
        ),
    )
    long_reading_power_repair_enabled: bool = Field(
        default=True,
        description="启用追读力修复流程，对低追读力章节进行定向修复",
    )
    long_reading_power_max_repair_rounds: int = Field(
        default=2,
        ge=0,
        le=5,
        description="追读力修复的最大循环轮次，0=禁用追读力修复",
    )
    long_reading_power_repair_threshold: float = Field(
        default=6.0,
        ge=0.0,
        le=10.0,
        description="追读力分数低于此阈值时触发修复流程",
    )
    long_reading_power_repair_max_change_ratio: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="追读力修复允许的最大文本改动比例，超过此比例将触发告警",
    )
    long_reading_power_archive_policy: Literal[
        "off",
        "floor_only",
        "floor_or_core_high",
    ] = Field(
        default="floor_only",
        description=(
            "追读力归档策略。off=追读力只写报告/下一章约束；"
            "floor_only=仅极低追读力分触发硬阻断；"
            "floor_or_core_high=极低分或核心高优先级追读问题触发硬阻断。"
        ),
    )
    long_reading_power_hard_block_threshold: float = Field(
        default=3.0,
        ge=0.0,
        le=10.0,
        description="追读力归档硬阻断线；设为 0 可关闭分数硬阻断。",
    )
    long_reading_power_archive_block_issue_types: list[str] = Field(
        default_factory=lambda: ["hook_missing", "prev_hook_unfulfilled"],
        description=(
            "当 long_reading_power_archive_policy=floor_or_core_high 时，"
            "这些追读力 issue_type 若为 high/critical 会阻断归档。"
        ),
    )
    long_retrieval_eval_point_a_enabled: bool = Field(
        default=True,
        description=(
            "Enable Phase-0a planning-stage retrieval evaluation. "
            "Sidecar only; does not affect generation decisions."
        ),
    )
    long_retrieval_eval_max_scenes: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Max plan scene_intents to include in retrieval gold-set derivation.",
    )
    long_retrieval_eval_persist_report: bool = Field(
        default=True,
        description="Persist Phase-0a retrieval reports to reports/chapter_NNN_retrieval_eval.json.",
    )
    long_guard_archive_policy: Literal["warn", "block_actionable"] = Field(
        default="warn",
        description=(
            "AI 护栏归档策略。warn=仅写入质量门/决策票据；"
            "block_actionable=高置信、可修复的指定护栏违约阻断归档。"
        ),
    )
    long_guard_archive_block_statuses: list[str] = Field(
        default_factory=lambda: ["non_compliant"],
        description="long_guard_archive_policy=block_actionable 时会阻断的护栏合规状态。",
    )
    long_guard_archive_block_min_confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="AI 护栏硬阻断所需的最低 LLM 判断置信度。",
    )
    forbidden_elements_cross_chapter_window: int = Field(
        default=4,
        ge=0,
        le=30,
        description=(
            "跨章节禁用元素窗口（章数）。"
            "0=不做跨章节硬约束，仅保留上章硬禁；"
            "N>0=仅取最近 N 章的累积禁用元素作为“软约束提示”。"
        ),
    )
    forbidden_elements_hard_max_items: int = Field(
        default=8,
        ge=0,
        le=50,
        description="单章进入生产提示的硬禁修辞项上限；按来源强度与本章语境相关性排序后裁剪。",
    )
    forbidden_elements_soft_max_items: int = Field(
        default=12,
        ge=0,
        le=80,
        description="单章进入生产提示的软禁修辞项上限；按来源强度与本章语境相关性排序后裁剪。",
    )
    forbidden_elements_quota_max_items: int = Field(
        default=6,
        ge=0,
        le=50,
        description="单章有限额复用/回环项上限；按来源强度与本章语境相关性排序后裁剪。",
    )
    forbidden_element_sources_max_items: int = Field(
        default=24,
        ge=0,
        le=160,
        description="单章保留的禁用元素来源追踪记录上限，用于审计来源和最终分级。",
    )
    forbidden_elements_rank_by_relevance: bool = Field(
        default=True,
        description="是否按来源置信度与本章语境相关性排序禁用元素，再取各类上限内的前若干项。",
    )
    init_continuity_protocol_enabled: bool = Field(
        default=True,
        description=(
            "是否在初始化阶段生成并注入“叙事连贯性协议”。"
            "开启后会把跨章承接规则写入 narrative_contract，并传递给 bridge/plan/draft。"
        ),
    )
    init_narrative_contract_llm_enabled: bool = Field(
        default=False,
        description=(
            "是否额外调用 INIT_NARRATIVE_CONTRACT 让 LLM 重写可裁判叙事契约。"
            "关闭时使用规则型 narrative_contract 的确定性派生产物，避免初始化阶段重复 LLM 调用。"
        ),
    )
    init_continuity_location_transition_required: bool = Field(
        default=True,
        description="初始化连贯性协议：地点发生变化时是否强制要求交代位移动作链。",
    )
    init_continuity_location_transition_window_sentences: int = Field(
        default=3,
        ge=1,
        le=6,
        description="初始化连贯性协议：地点切换时，开场前 N 句内必须出现位移动作链。",
    )
    init_continuity_bridge_echo_ratio: float = Field(
        default=0.28,
        ge=0.10,
        le=0.60,
        description="初始化连贯性协议：桥接回声窗口按每章字数乘以该比例计算。",
    )
    init_continuity_bridge_echo_min_chars: int = Field(
        default=450,
        ge=200,
        le=2000,
        description="初始化连贯性协议：桥接回声窗口最小字符数。",
    )
    init_continuity_bridge_echo_max_chars: int = Field(
        default=1400,
        ge=300,
        le=4000,
        description="初始化连贯性协议：桥接回声窗口最大字符数。",
    )
    init_continuity_time_notation_profile: Literal["auto", "traditional_cn", "locale_default"] = (
        Field(
            default="auto",
            description=(
                "初始化连贯性协议：时间表达制式。"
                "auto=按语言自动，traditional_cn=传统时辰刻度，locale_default=本地常规时间表达。"
            ),
        )
    )
    init_continuity_traditional_time_ke_range: str = Field(
        default="一至四刻",
        description="初始化连贯性协议：传统时辰刻度允许范围（仅 traditional_cn 生效）。",
    )
    init_continuity_pov_visibility_rule: str = Field(
        default="限知视角仅描写可观察事实，禁止直接写非POV角色内心。",
        description="初始化连贯性协议：POV 可见性规则文本，会注入 bridge/plan/draft 提示词。",
    )
    init_continuity_forbidden_repetition_rule: str = Field(
        default="禁复用仅针对修辞性意象；人物、实体与剧情锚点不在此限。命中后必须替换为全新意象，不得使用近义改写。",
        description="初始化连贯性协议：禁复用规则文本，会注入 bridge/plan/draft 提示词。",
    )
    init_continuity_max_key_revelations_per_chapter: int = Field(
        default=2,
        ge=1,
        le=8,
        description="初始化连贯性协议：单章重大揭示上限，用于控制信息密度。",
    )
    init_continuity_min_unresolved_threads_to_keep: int = Field(
        default=1,
        ge=0,
        le=6,
        description="初始化连贯性协议：单章至少保留的未决线索数（0=不强制）。",
    )
    long_max_consistency_replans: int = Field(
        default=2,
        ge=0,
        le=5,
        description="一致性校验未通过时自动重新规划的最大次数，0=不重试直接失败",
    )
    long_overused_vocabulary_window: int = Field(
        default=5,
        ge=1,
        le=30,
        description=(
            "用语重复检测的章节回看窗口。"
            "统计最近 N 章的 n-gram 频率，标记过度使用的表达加入禁用列表。"
            "增大窗口可提升全书用语多样性，但会轻微增加预处理耗时。"
        ),
    )
    long_previous_ending_min_chars: int = Field(
        default=600,
        ge=200,
        le=5000,
        description=(
            "上一章结尾文本传递给下一章的最小字符数。"
            "用于构建跨章衔接锚点，值越大锚点越丰富但占用更多 token。"
        ),
    )
    long_previous_ending_max_chars: int = Field(
        default=1500,
        ge=400,
        le=10000,
        description=(
            "上一章结尾文本传递给下一章的最大字符数。超出此长度将截断到最近完整段落边界。"
        ),
    )
    long_boundary_prev_tail_paragraphs: int = Field(
        default=5,
        ge=1,
        le=12,
        description=(
            "跨章边界窗口中注入的上一章末尾段落数。Bridge 优先使用该窗口生成衔接契约，"
            "Edit/Polish 后复审与 Repair 兜底共用该取样范围。"
        ),
    )
    long_boundary_opening_paragraphs: int = Field(
        default=3,
        ge=1,
        le=8,
        description=(
            "跨章边界窗口中检查与修复的本章开头段落数。用于开场硬门禁、Continuity 复审"
            "和开场衔接类 Repair 的目标窗口。"
        ),
    )
    long_element_progress_lookback: int = Field(
        default=0,
        ge=0,
        le=100,
        description=(
            "叙事要素执行追踪的章节回看窗口。"
            "0=扫描全部历史章节（默认）；"
            "N>0=仅扫描最近 N 章的要素缺失/薄弱记录进行推荐。"
            "对超长篇小说（50+ 章），建议设为 10-20 以减少噪音。"
        ),
    )

    # ── 本地检查策略 ─────────────────────────────────
    local_check_as_prescreen: bool = Field(
        default=True,
        description=(
            "是否将本地检查（关键词/正则匹配）作为 LLM 评估的快速预筛选。 "
            "开启时：本地检查先运行，结果作为 context 传给 LLM 做最终判断（推荐）。 "
            "关闭时：仅依赖 LLM 评估，token 消耗更高但更智能。 "
            "关闭后本地检查结果仍会显示在日志中，但不参与最终判定。"
        ),
    )
    local_check_confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description=(
            "本地检查结果的置信度阈值。只有置信度 >= 此值的本地检查结果才会传给 LLM。 "
            "提高阈值会减少误报，但可能遗漏问题；降低阈值会增加检查覆盖面，但可能引入误判。"
        ),
    )
    local_guardrails_trust_level: Literal["strict", "balanced", "llm_first"] = Field(
        default="balanced",
        description=(
            "本地护栏（如 custody/release/transition 信号检测）的信任级别。 "
            "strict: 本地检测到即标记为问题，不依赖 LLM 判断。 "
            "balanced: 本地检测作为提示传给 LLM，由 LLM 最终判定。 "
            "llm_first: 仅在 LLM 评估不确定时参考本地结果。"
        ),
    )
    pronoun_autofix_mode: Literal["off", "pov_only", "pov_or_many"] = Field(
        default="off",
        description=(
            "代词检查后的自动修复策略。"
            "'off'=仅检测并告警（推荐，最稳）；"
            "'pov_only'=仅当 POV 代词错误时触发 LLM 定向修复；"
            "'pov_or_many'=POV 错误或总问题较多时修复，并允许机械代词兜底。"
        ),
    )
    long_continuity_repair_threshold: float = Field(
        default=9.0,
        ge=0.0,
        le=10.0,
        description=(
            "Minimum continuity_score below which repair_continuity is triggered. "
            "If continuity_score >= this value AND no critical/high severity issues exist, "
            "the expensive full-chapter rewrite is skipped. "
            "Set to 0.0 to always run repair (original behaviour). "
            "Recommended range: 8.5–9.5."
        ),
    )
    long_global_repair_budget: int = Field(
        default=3,
        ge=0,
        le=10,
        description=(
            "全局修复预算：所有维度（连续性+因果+追读力）修复轮次的硬上限。"
            "当连续性修复用尽轮次后，因果修复的轮次会被自动压缩至剩余预算。"
            "设为 0 禁用全局预算控制。推荐值：3-4。"
        ),
    )
    long_total_repair_rounds_cap: int = Field(
        default=LONG_TOTAL_REPAIR_ROUNDS_CAP_DEFAULT,
        ge=0,
        le=20,
        description=(
            "所有维度（连贯性+对齐+因果+追读力）修复轮次的绝对上限。"
            "当累计修复轮次达到此值时，剩余维度将被跳过。"
            "设为 0 表示不限制。每次修复循环轮次、对齐修复重检、"
            "post_repair_checks 均计入此上限。推荐值：5。"
        ),
    )
    long_single_final_verify_enabled: bool = Field(
        default=False,
        description=(
            "将所有正文修改收敛到最终验证前，并在归档阶段复用已完成的 preflight。"
            "关闭时保持旧版终结与重试语义。"
        ),
    )
    # ── Repair effectiveness telemetry (Phase 0 feature flags) ──────────
    long_repair_effectiveness_telemetry_enabled: bool = Field(
        default=True,
        description=(
            "启用修复有效性遥测。将 repair round / report refresh / attempt "
            "complete 事件写入 append-only JSONL 账本。纯观察，不影响行为。"
        ),
    )
    long_mutation_refresh_mode: Literal["off", "shadow", "enforce"] = Field(
        default="off",
        description=(
            "TextMutation 感知报告刷新模式。"
            "off=忽略 mutation（当前行为）；"
            "shadow=计算建议 report_kinds 并记录但不改变实际刷新；"
            "enforce=实际使用 mutation 推导的 report_kinds。"
        ),
    )
    long_adaptive_routing_mode: Literal["off", "shadow", "enforce"] = Field(
        default="off",
        description=(
            "自适应路由模式。"
            "off=保持当前路由行为；"
            "shadow=记录建议路由但不改变实际选择；"
            "enforce=在用户授权候选范围内启用严重性/预算压力感知路由。"
        ),
    )
    long_chapter_soft_budget_enabled: bool = Field(
        default=False,
        description=(
            "启用章节级费用软预算。根据本章已消耗 USD 动态调整可选步骤优先级。"
            "硬门禁检查和 critical/high 修复不可跳过；low 修复和非必要 polish "
            "可在软预算不足时降级。日/月硬预算仍由 SpendingTracker 做最终限制。"
        ),
    )
    long_unknown_cost_reserve_usd: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description=(
            "无价格信息模型的保守费用预留（USD/次调用）。"
            "当 cost_source=unknown 时按此值扣减软预算，不参与经济路由决策。"
        ),
    )
    long_repair_repeated_issue_guard_enabled: bool = Field(
        default=True,
        description=(
            "启用重复问题守卫。同一 issue_signature 多次进入 must-fix 后，"
            "只升级修复提示倾向，不跳过复检、账本、回滚或最终硬门。"
        ),
    )
    long_repair_repeated_issue_threshold: int = Field(
        default=3,
        ge=1,
        le=10,
        description="同一必修问题触发重复问题守卫的尝试次数阈值。",
    )
    long_repair_strategy_advisor_mode: Literal["off", "guarded", "always"] = Field(
        default="guarded",
        description=(
            "修复策略诊断调用模式。off=关闭；guarded=仅在重复守卫、连续回滚、"
            "历史成功率过低或高危问题密集时调用；always=每轮调用。"
        ),
    )
    long_repair_strategy_advisor_confidence_floor: float = Field(
        default=0.65,
        ge=0.0,
        le=1.0,
        description="修复策略诊断低于该置信度时丢弃，继续使用确定性策略。",
    )
    repair_control_mode: Literal["manual", "ai_assisted", "ai_auto"] = Field(
        default="ai_assisted",
        description=(
            "统一修复控制模式。manual=全手动，只生成建议和预览；"
            "ai_assisted=低风险自动修复，高风险请求人工确认；"
            "ai_auto=AI 自动执行、复检和回滚，仅在无法验证/连续失败/数据损坏风险时转人工。"
        ),
    )
    long_repair_human_decision_timeout_s: int = Field(
        default=300,
        ge=1,
        le=3600,
        description="高风险修复确认超时时间，超时默认继续但不升级修复策略。",
    )
    long_streaming_text_enabled: bool = Field(
        default=True,
        description=(
            "对长文本 TEXT_ONLY 任务启用可见性 streaming。pipeline 仍只消费最终聚合且"
            "通过文本契约校验后的完整文本。"
        ),
    )
    long_streaming_json_observation_enabled: bool = Field(
        default=True,
        description=(
            "对 JSON 结构化任务启用观察型 streaming。UI 可看到原始 JSON 草稿，"
            "但 pipeline 仍只消费最终聚合并通过格式修复/schema 校验后的完整对象。"
        ),
    )
    long_streaming_json_excluded_tasks: str = Field(
        default="",
        description=(
            "额外排除观察型 streaming 的任务名（逗号分隔，如 '"
            "adjudicate_state_delta,extract_canon'）。默认内置 5 个超大 prompt 任务"
            "始终排除；此处仅追加，不可移除内置项。"
        ),
    )
    init_chapter_contract_resume_strict_noise: bool = Field(
        default=True,
        description=(
            "Keep chapter-contract init resume strict by default. "
            "When disabled, clean cached chapter contracts may be reused while noisy/backfilled chapters are regenerated."
        ),
    )
    long_edit_early_stop_similarity: float = Field(
        default=0.98,
        ge=0.0,
        le=1.0,
        description=(
            "长篇章节编辑轮次的相似度提前收束阈值。"
            "当本轮编辑后的正文与上一轮相似度达到该值，且字数已在目标范围内时，"
            "停止后续编辑轮次。设为 1.0 可基本关闭相似度提前收束。"
        ),
    )
    long_anchor_recalibration_confidence_floor: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "锚点再校准后置信度低于此值时标记为 anchor_degraded。"
            "anchor_degraded 的问题将跳过 patch 路径，直接使用 window/fulltext 修复。"
            "推荐值：0.5。"
        ),
    )
    long_fulltext_upgrade_change_budget_fraction: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description=(
            "累计变更率超过 change_budget 的此比例时，阻止升级到 fulltext 模式。"
            "例如 change_budget=0.15 且此值为 0.50，则累计变更率 > 0.075 时"
            "禁止升级为 fulltext，保留 window 模式以避免预算超限回滚。"
            "设为 1.0 禁用此门控。推荐值：0.50。"
        ),
    )
    repair_display_min_severity: Literal["low", "medium", "high", "critical"] = Field(
        default="low",
        description=(
            "问题面板中显示的最低严重程度过滤器。"
            "'low'=全部显示（默认），'medium'=隐藏轻微(low)问题，"
            "'high'=只显示高/严重问题，'critical'=只显示严重问题。"
            "过滤仅影响 UI 展示，不影响已写入磁盘的评估报告。"
        ),
    )
    repair_always_reaudit: bool = Field(
        default=False,
        description=(
            "手动修复后，无论 LLM 是否实际改动了文本（applied=True/False），均重新运行一次审核。\n"
            "开启后可确保问题面板始终反映当前文本的真实状态，问题消失即代表真正被解决。\n"
            "applied=False 时会额外消耗 token（LLM 未改文本，结果大概率不变），建议在排查"
            "问题是否真正解决时临时开启，平时保持关闭以节省 token。"
        ),
    )
    repair_must_fix_severity: Literal["critical", "high", "medium", "off"] = Field(
        default="critical",
        description=(
            "达到此严重程度的问题将强制继续自动修复，即使已超出最大重试次数限制。"
            "'critical'=只有 critical 级别必修（默认），'high'=high 及以上必修，"
            "'medium'=medium 及以上必修，'off'=关闭，所有问题均受最大重试次数限制。"
        ),
    )
    recheck_strategy: Literal["strict_targeted", "targeted_with_global_guard"] = Field(
        default="targeted_with_global_guard",
        description=(
            "修复后复检的检测范围策略。"
            "'strict_targeted'=只验证目标问题是否解决，不检测新问题（原始行为，更快但可能漏检回归）。"
            "'targeted_with_global_guard'=在目标验证之外，额外检查是否出现了新的 high/critical 问题（推荐）。"
        ),
    )
    causal_validation_fail_mode: Literal["warn_unknown", "fail_open"] = Field(
        default="warn_unknown",
        description=(
            "因果校验调用失败时的策略。"
            "'warn_unknown'=标记为结果未知并给出告警（推荐，避免静默通过）；"
            "'fail_open'=沿用旧行为（视为通过）。"
        ),
    )
    change_budget_threshold: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description=(
            "单轮修复改动占全文的比例上限。超过此阈值将触发全量复审而非仅做点验。"
            "0.15 表示改动超过 15% 时强制全量检查。"
        ),
    )
    minor_change_skip_recheck_ratio: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description=(
            "连贯性修复中，改动低于此比例且现有质检通过时，跳过全部耗时复检。"
            "0.05 表示改动不超过 5% 时可跳过。"
        ),
    )
    moderate_change_skip_chapter_repair_ratio: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description=(
            "连贯性修复中，改动低于此比例且质检通过时，仅跳过最慢的 ChapterRepair 复检，"
            "仍执行 alignment + continuity 检查。"
        ),
    )
    causal_minor_change_skip_recheck_ratio: float = Field(
        default=0.015,
        ge=0.0,
        le=1.0,
        description=(
            "因果修复后跨维度回归检查的跳过阈值。因果修复涉及剧情逻辑，"
            "即使改动量小也可能影响连贯性，因此阈值比连贯性修复更严格。"
        ),
    )
    patch_executor_version: Literal["v1", "v2"] = Field(
        default="v2",
        description=(
            "Patch 执行器版本。"
            "'v1'=旧版逐条应用，静默跳过失败；"
            "'v2'=新版事务化执行器，含唯一匹配约束、冲突检测和失败码回传。"
        ),
    )
    max_auto_repair_attempts: int = Field(
        default=2,
        ge=1,
        le=10,
        description=(
            "低于 repair_must_fix_severity 的问题的最大自动修复次数。"
            "达到上限后停止自动重试，提示用户手动干预。"
            "必修级别（repair_must_fix_severity）的问题不受此限制。"
        ),
    )
    autorun_max_checkpoint_resolve_attempts: int = Field(
        default=5,
        ge=1,
        le=20,
        description=(
            "章节连跑中，同一个归档检查点（checkpoint）允许自动提交解决的最大次数。"
            "同一 checkpoint 反复提交仍未消化（归档失败）时，超过该上限将停止该项目"
            "的连跑并提示人工介入，防止归档失败时无限重提交、堆积工作单元。"
        ),
    )
    autorun_checkpoint_resolve_backoff_base_ms: int = Field(
        default=3000,
        ge=500,
        le=120000,
        description=(
            "章节连跑归档重试的指数退避基础时长（毫秒）。"
            "第 n（n≥2）次重提交同一 checkpoint 前延迟 base*2^(n-2)，"
            "上限 60 秒，避免失败后以固定 tick 频率密集重试。"
        ),
    )
    autorun_max_stage_failures: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "Engine 章节连跑中，每章每阶段允许自动恢复的失败次数。"
            "超过预算后持久化为 failed 并停止跨章推进，必须由用户重新启动。"
        ),
    )
    autorun_failure_backoff_base_ms: int = Field(
        default=3000,
        ge=0,
        le=120000,
        description=(
            "Engine 章节连跑普通任务失败后的指数退避基础时长（毫秒）。"
            "失败预算和下次重试时间都会写入项目会话，重启不会重置。"
        ),
    )
    long_polish_enabled: bool = Field(
        default=False,
        description=(
            "精修润色总开关：在审核后、归档前对正文做文学性打磨。"
            "设为 True 时无论评分如何都强制执行精修；"
            "设为 False 时精修仍可能因自动触发阈值（long_polish_auto_trigger_threshold）而启动。"
        ),
    )
    long_polish_auto_trigger_threshold: float = Field(
        default=7.0,
        ge=0.0,
        le=10.0,
        description=(
            "精修润色自动触发阈值。当评估总分低于此值时自动启动精修流程，"
            "无需手动开启 long_polish_enabled。设为 0.0 可禁用自动触发。"
        ),
    )
    long_min_accept_score: float = Field(
        default=5.0,
        ge=0.0,
        le=10.0,
        description=(
            "章节最低可接受评分。当最终评估总分低于此值时，"
            "将抛出 ConsistencyViolationError 阻止章节保存并强制重新规划。"
        ),
    )
    long_plot_progression_quality_floor: float = Field(
        default=5.5,
        ge=0.0,
        le=10.0,
        description=(
            "章节实质推进质量提示线。EVALUATE 输出的 plot_progression 低于此值时，"
            "质量报告会标记为 WARN，用于识别可能的填充章节；设为 0 可关闭该提示。"
        ),
    )
    long_style_metrics_enabled: bool = Field(
        default=True,
        description=(
            "是否启用风格指标检测（对话比例、重复短语、禁用短语）。"
            "设为 False 可关闭质量门中的 style_dialogue_ratio 维度。"
        ),
    )
    long_style_dialogue_gate_mode: str = Field(
        default="warn",
        description=(
            "风格对话比例质量门模式。'warn' 只记录警告不阻断；"
            "'block' 低于阈值时阻断章节保存。默认 warn，稳定后再切换为 block。"
        ),
    )
    long_style_dialogue_repair_threshold: int = Field(
        default=25,
        ge=0,
        le=100,
        description=(
            "对话比例修复阈值（百分比）。低于此值时触发修复指令；默认 25%，防止模型为达标硬灌对话。"
        ),
    )
    long_prose_quality_floor: float = Field(
        default=0.0,
        ge=0.0,
        le=10.0,
        description=(
            "文学表达质量门禁阈值（来自 eval style 维度）。"
            "设为 0 时禁用；设为 7.5 时启用出版级底线检查。"
            "低于此值时 quality gate 会记录 prose_quality 维度未通过。"
        ),
    )
    long_continuity_hard_block_threshold: float = Field(
        default=4.0,
        ge=0.0,
        le=10.0,
        description=(
            "连贯分硬阻断线。当连贯分低于此值时，"
            "无论修复循环结果如何，都将阻止章节保存并强制重新规划。"
            "默认 4.0，表示连贯性严重不足。"
        ),
    )
    long_causal_hard_block_threshold: float = Field(
        default=4.0,
        ge=0.0,
        le=10.0,
        description=(
            "因果分硬阻断线。当因果分低于此值时，"
            "无论修复循环结果如何，都将阻止章节保存并强制重新规划。"
            "默认 4.0，表示因果逻辑严重断裂。"
        ),
    )
    long_best_effort_accept_floor: float = Field(
        default=6.0,
        ge=0.0,
        le=10.0,
        description=(
            "修复轮次耗尽后允许 best-effort 接受的最低分数线。"
            "最终使用值为该线与各维度硬阻断线的较高者，默认 6.0，"
            "避免 5 分级别章节在仅略有改善时静默归档。"
        ),
    )
    motif_suggestion_min_chapters: int = Field(
        default=5,
        ge=2,
        le=30,
        description=(
            "母题建议触发的最小章数间隔。"
            "母题在此章数内曾使用则不提示，默认 5。"
            "调高可减少母题提示的频率。"
        ),
    )
    motif_suggestion_min_occurrences: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "母题至少出现过此次数才会生成建议。默认 3，避免偶然出现一两次的元素被当作核心母题。"
        ),
    )
    memory_motif_related_lookback_chapters: int = Field(
        default=2,
        ge=0,
        le=20,
        description=(
            "母题关联窗口（章数）。"
            "0=仅本章，2=前两章+本章（默认）。"
            "该值同时影响："
            "1) 后端写作阶段注入的母题上下文；"
            "2) 章台记忆面板显示的母题范围。"
        ),
    )
    motif_prompt_token_budget: int = Field(
        default=500,
        ge=100,
        le=5000,
        description=(
            "母题上下文注入 prompt 的软预算（估算 token）。"
            "默认 500 偏保守；母题密集项目可调到 800-1000。"
        ),
    )
    motif_prompt_max_items: int = Field(
        default=8,
        ge=1,
        le=30,
        description=(
            "母题提示预算超限时每类列表的裁剪上限。"
            "当前主要用于 forbidden_repetition 的预算裁剪，默认 8。"
        ),
    )
    motif_forbidden_max_items: int = Field(
        default=3,
        ge=0,
        le=30,
        description=(
            "每次提示允许进入 prompt 的避重复母题数量上限。默认 3，避免把近期意象全部变成硬性禁用。"
        ),
    )
    motif_repetition_lookback_chapters: int = Field(
        default=5,
        ge=0,
        le=50,
        description=(
            "母题无意识重复检查的回看章数。默认 5。该值只控制重复风险检测，不影响活跃母题注入窗口。"
        ),
    )
    motif_repetition_recent_gap_chapters: int = Field(
        default=2,
        ge=1,
        le=20,
        description=(
            "母题重复检查中判定为'过近复用'的章距阈值。"
            "默认 2 表示只拦截紧邻上一章复用；调高可捕捉更长间隔的表达重复。"
        ),
    )
    memory_style_rule_tracking_enabled: bool = Field(
        default=True,
        description="是否启用跨章写作技法规则频率追踪（style_profile.modules）。",
    )
    style_rule_repetition_lookback_chapters: int = Field(
        default=5,
        ge=0,
        le=50,
        description="写作技法模板化检查的回看章数。默认 5。",
    )
    style_rule_repetition_recent_gap_chapters: int = Field(
        default=2,
        ge=1,
        le=20,
        description="写作技法模板化检查中判定为过近复用的章距阈值。默认 2。",
    )
    motif_dormant_callback_min_chapters: int = Field(
        default=20,
        ge=5,
        le=200,
        description="母题沉睡回调建议的最小间隔章数。默认 20，适合长篇线索回收。",
    )
    motif_auto_forget_ephemeral_enabled: bool = Field(
        default=True,
        description=(
            "是否自动遗忘非重要母题。开启后，低重要度、低出现次数、长期未复现的母题会被自动退役，"
            "不再进入提示词，但历史记录仍保留。"
        ),
    )
    motif_ephemeral_forget_after_chapters: int = Field(
        default=12,
        ge=3,
        le=200,
        description="非重要母题在多少章未复现后自动退役。默认 12。",
    )
    motif_ephemeral_max_occurrences: int = Field(
        default=1,
        ge=0,
        le=10,
        description="自动遗忘只处理出现次数不超过该值的母题。默认 1，避免误退役已形成模式的母题。",
    )
    motif_ephemeral_importance_threshold_pct: int = Field(
        default=35,
        ge=0,
        le=100,
        description="自动遗忘的重要度阈值百分比。默认 35，即 importance_score <= 0.35 才可自动退役。",
    )
    long_draft_character_history_lookback: int = Field(
        default=10,
        ge=1,
        le=50,
        description=(
            "草稿阶段角色历史回溯窗口（章数）。"
            "控制当前章节写草稿时，从记忆系统获取角色状态历史和关系变化的前溯章数。"
            "值越大，角色行为越一致但会消耗更多上下文空间。"
            "默认 10，覆盖 1-50 章。"
        ),
    )
    long_context_compress_min_chars: int = 220
    long_context_compress_max_tokens: int = 2048
    long_chapter_compact_interval: int = 5
    long_chapter_compact_start_chapter: int = 10
    long_chapter_compact_stale_chapters: int = 8
    long_chapter_compact_outline_lookahead: int = 12
    long_chapter_compact_min_active_characters: int = 8
    long_chapter_compact_target_world_facts: int = 120
    long_chapter_compact_keep_recent_world_facts: int = 40
    long_chapter_compact_archive_resolved_foreshadowing_after: int = 6
    long_plan_beats_min: int = 4
    long_plan_beats_max: int = 8
    long_plan_beat_max_chars: int = 260
    long_plan_max_foreshadowing: int = (
        10  # 章节规划步骤中活跃伏笔上限（独立于全局 canon 上限，节省 token）
    )
    long_narrative_evidence_candidate_limit: int = Field(
        default=24,
        ge=1,
        le=128,
        description=(
            "章节规划/复审的 Zvec 动态叙事证据候选池上限。"
            "候选按语义与全文混合检索排序，并在章节时间截止过滤后进入 token 预算。"
        ),
    )
    long_narrative_evidence_token_budget: int = Field(
        default=2400,
        ge=256,
        le=16000,
        description=(
            "章节规划/复审可注入的 Zvec 历史证据 token 预算。"
            "本章 P0 固定约束不计入该预算且始终完整传递。"
        ),
    )
    long_draft_prompt_diagnostics_enabled: bool = Field(
        default=True,
        description="记录 Draft 章节提示词字符量、估算 token 和最大上下文字段，用于调试注意力预算。",
    )
    long_draft_prompt_warn_tokens: int = Field(
        default=32000,
        ge=0,
        le=200000,
        description="Draft 提示词估算 token 达到该阈值时写 warning 日志。0=只记录 info。",
    )
    long_prompt_diagnostics_enabled: bool = Field(
        default=True,
        description="记录 Bridge/Plan/Edit 等章节生成阶段的提示词字符量、估算 token 和最大上下文字段。",
    )
    long_prompt_warn_tokens: int = Field(
        default=32000,
        ge=0,
        le=200000,
        description="非 Draft 章节生成提示词估算 token 达到该阈值时写 warning 日志。0=只记录 info。",
    )
    long_prompt_pressure_info_ratio: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="提示词占模型上下文窗口比例达到该值时在 pressure diagnostics 中标记 info。",
    )
    long_prompt_pressure_warn_ratio: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="提示词占模型上下文窗口比例达到该值时在 pressure diagnostics 中标记 warn。",
    )
    long_prompt_preflight_block_oversized: bool = Field(
        default=True,
        description=(
            "渲染后的提示词加输出预留超过已解析模型窗口时，在网关调用前阻止请求；"
            "完整覆盖任务应捕获 ContextLengthError 并继续分区。"
        ),
    )
    long_adjust_outline_max_chapters_context: int = 30
    chapter_contract_context_window: int = Field(
        default=2,
        ge=0,
        le=12,
        description="章节契约分批生成时注入的前后邻近大纲章节数。",
    )
    chapter_contract_entity_catalog_max_entities: int = Field(
        default=96,
        ge=16,
        le=512,
        description=(
            "章节契约单批提示词中的实体目录上限；"
            "仅投影当前批次明确引用的实体与角色锚点，后置校验仍使用全量目录。"
        ),
    )
    chapter_contract_dynamic_budget_enabled: bool = Field(
        default=True,
        description="章节契约生成与本地回填是否按章节目标字数动态限制硬约束数量。",
    )
    chapter_contract_hard_words_per_item: int = Field(
        default=1000,
        ge=200,
        le=5000,
        description="章节契约每个硬性推进项对应的目标字数；值越大，每章硬约束越少。",
    )
    chapter_contract_hard_min_items: int = Field(
        default=3,
        ge=1,
        le=12,
        description="章节契约每章硬性推进项下限。",
    )
    chapter_contract_hard_max_items: int = Field(
        default=6,
        ge=1,
        le=12,
        description="章节契约每章硬性推进项上限。",
    )
    chapter_contract_soft_words_per_item: int = Field(
        default=900,
        ge=200,
        le=5000,
        description="章节契约每个允许铺垫项对应的目标字数；只限制 allowed_* 数量。",
    )
    chapter_contract_soft_max_items: int = Field(
        default=5,
        ge=1,
        le=12,
        description="章节契约 allowed_changes/allowed_progressions 每章上限。",
    )
    chapter_contract_state_words_per_item: int = Field(
        default=1200,
        ge=200,
        le=5000,
        description="章节契约每个出口状态/完成标准对应的目标字数。",
    )
    chapter_contract_state_max_items: int = Field(
        default=4,
        ge=1,
        le=12,
        description="章节契约 exit_state_targets/completion_criteria 每章上限。",
    )
    chapter_contract_multi_turn: bool = True
    chapter_contract_multi_turn_providers: str = "tongyi,deepseek,minimax"
    chapter_contract_multi_turn_models: str = ""
    contract_coherence_batch_size: int = Field(
        default=12,
        ge=1,
        le=30,
        description="契约裁判分批检查时每批纳入的章节契约数。",
    )
    contract_coherence_context_window: int = Field(
        default=2,
        ge=0,
        le=12,
        description="契约裁判分批检查时注入的前后邻近章节契约数。",
    )
    contract_coherence_max_parallel: int = Field(
        default=2,
        ge=1,
        le=8,
        description="契约裁判分批检查的最大并发批次数。",
    )
    init_coherence_use_memory: bool = Field(
        default=True,
        description="初始化一致性 v2 是否使用记忆向量索引进行语义候选召回。",
    )
    init_coherence_claim_batch_size: int = Field(
        default=8,
        ge=1,
        le=50,
        description="初始化一致性 v2 每批抽取 claims 的初始最大章节/片段数；超出载荷预算会自动拆小。",
    )
    init_coherence_claim_payload_char_budget: int = Field(
        default=18_000,
        ge=1_000,
        le=200_000,
        description="初始化一致性 v2 claims 单块输入 payload 的字符预算，超过后自动缩小章节范围。",
    )
    init_coherence_claim_max_parallel: int = Field(
        default=2,
        ge=1,
        le=8,
        description="初始化一致性 v2 claims 分块抽取的最大并发批次数。",
    )
    init_blueprint_holistic_claims_enabled: bool = Field(
        default=True,
        description="蓝图一致性审查是否额外执行完整蓝图 holistic claims 抽取。",
    )
    init_blueprint_holistic_claim_max_tokens: int = Field(
        default=4096,
        ge=1024,
        le=16000,
        description="完整蓝图 holistic claims 抽取的基础输出 token 上限。",
    )
    init_stream_claim_prefetch_enabled: bool = Field(
        default=True,
        description="章节大纲批次完成后是否流式预取初始化一致性 claims。",
    )
    init_coherence_overlap_chapters: int = Field(
        default=2,
        ge=0,
        le=12,
        description="初始化一致性 v2 分批抽取时注入的前后章节重叠窗口；超出载荷预算时会优先缩小范围。",
    )
    init_coherence_semantic_top_k: int = Field(
        default=12,
        ge=0,
        le=50,
        description="初始化一致性 v2 记忆语义召回每条 claim 的 topK。",
    )
    init_coherence_candidate_max_per_batch: int = Field(
        default=80,
        ge=1,
        le=500,
        description="初始化一致性 v2 每个阶段送入 LLM 裁判的候选冲突上限。",
    )
    init_coherence_llm_candidate_batch_size: int = Field(
        default=8,
        ge=1,
        le=50,
        description="初始化一致性 v2 候选冲突裁判时每次 LLM 调用的候选组数量。",
    )
    init_coherence_llm_candidate_max_parallel: int = Field(
        default=2,
        ge=1,
        le=8,
        description="初始化一致性 v2 候选冲突分批裁判的最大并发批次数。",
    )
    init_entity_reference_batch_evidence_token_budget: int = Field(
        default=3600,
        ge=800,
        le=16000,
        description=(
            "初始化实体指称裁决的共享 EvidencePack token 预算；强制实体卡超额时会"
            "自动拆小 mention 批次。"
        ),
    )
    init_entity_reference_max_parallel: int = Field(
        default=3,
        ge=1,
        le=8,
        description="初始化实体指称裁决的最大并发批次数。",
    )
    init_entity_reference_occurrence_limit: int = Field(
        default=12,
        ge=4,
        le=100,
        description=(
            "每个实体指称发送给裁决模型的代表性 claim occurrence 上限；完整映射仍保留在本地。"
        ),
    )
    init_coherence_confidence_threshold: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="初始化一致性 v2 claims 和候选召回使用的默认置信度阈值。",
    )
    init_coherence_recheck_affected_window: int = Field(
        default=2,
        ge=0,
        le=12,
        description="初始化一致性 v2 局部修复后重查受影响章节前后的窗口大小。",
    )
    init_coherence_recheck_max_claims: int = Field(
        default=240,
        ge=20,
        le=2000,
        description=(
            "初始化一致性 v2 局部复检允许进入候选召回的基础 claim 上限，"
            "防止账本污染导致循环消耗；实际上限会按复检窗口章节数自适应放大。"
        ),
    )
    init_coherence_recheck_claims_per_chapter: int = Field(
        default=40,
        ge=0,
        le=200,
        description=(
            "初始化一致性 v2 局部复检每个窗口章节放宽的 claim 数；"
            "有效上限 = max(基础上限, 该值 × 窗口章节数)，0 表示关闭自适应。"
        ),
    )
    init_coherence_recheck_chunk_chapters: int = Field(
        default=12,
        ge=2,
        le=50,
        description="初始化一致性 v2 局部复检超过该章节数时，按连续章节窗口拆分复检。",
    )
    init_coherence_auto_repair: bool = Field(
        default=True,
        description="初始化一致性裁判发现可定位 high/critical 问题时自动尝试 LLM 局部 JSON Patch 修复。",
    )
    init_coherence_deterministic_repair: bool = Field(
        default=True,
        description="初始化一致性裁判发现可证明的结构化元数据问题时，先尝试本地确定性修复。",
    )
    init_coherence_max_repair_rounds: int = Field(
        default=2,
        ge=0,
        le=3,
        description="每个初始化 artifact 的自动局部修复最大轮次。",
    )
    init_coherence_stop_on_no_progress: bool = Field(
        default=True,
        description="初始化一致性自动修复后阻断问题没有实质减少时，是否提前停止后续修复轮次。",
    )
    init_coherence_max_stagnant_repair_rounds: int = Field(
        default=1,
        ge=0,
        le=3,
        description="初始化一致性自动修复允许连续无进展的轮数；0 表示不启用该提前停止保护。",
    )
    init_coherence_block_min_severity: Literal["low", "medium", "high", "critical"] = Field(
        default="high",
        description="初始化一致性裁判中触发阻断/自动修复的最低严重度。",
    )
    init_coherence_patch_max_ops: int = Field(
        default=40,
        ge=1,
        le=200,
        description="初始化局部修复一次最多允许的 JSON Patch 操作数。",
    )
    init_coherence_target_patch_batch_size: int = Field(
        default=12,
        ge=1,
        le=40,
        description="初始化 target_id 精准修复一次最多下发给模型的定位目标数。",
    )
    init_source_artifact_auto_repair: bool = Field(
        default=True,
        description="初始化源头 artifact 准入发现可定位问题时，是否自动尝试受限修复。",
    )
    init_source_artifact_repair_rounds: int = Field(
        default=2,
        ge=0,
        le=3,
        description="初始化源头 artifact 准入自动修复最大轮次；0 表示只报告不修复。",
    )
    init_claim_coverage_enabled: bool = Field(
        default=True,
        description="初始化章节契约通过后，是否审计高价值一致性 Claims 已被最终 chapter_contracts 覆盖。",
    )
    init_claim_coverage_block_p0: bool = Field(
        default=True,
        description="一致性 Claims 契约覆盖审计发现 P0 一致性 Claim 未覆盖时是否阻断初始化准入。",
    )
    init_claim_coverage_block_p1: bool = Field(
        default=False,
        description="一致性 Claims 契约覆盖审计发现 P1 一致性 Claim 未覆盖时是否阻断初始化准入。",
    )
    init_claim_coverage_block_degraded: bool = Field(
        default=True,
        description="一致性 Claims 契约覆盖审计缺少或无法读取 Claims 账本时是否阻断初始化准入。",
    )
    init_claim_constraints_backfill_enabled: bool = Field(
        default=True,
        description=(
            "PLAN_CHAPTER_CONTRACTS 批次返回后，是否把 LLM 漏写进 cognitive_constraints 的 init_claim_constraints 条目以确定性方式补齐。"
            "关闭后会保留 LLM 原始输出，但章节契约覆盖审计将更容易失败。"
        ),
    )
    init_disable_local_story_fallbacks: bool = Field(
        default=True,
        description="禁用题材/剧情特化的本地兜底修复，只保留通用安全校验与 LLM scope patch。",
    )
    init_creative_refinement_enabled: bool = Field(
        default=False,
        description="蓝图审查前是否启用保守型创意增强。",
    )
    init_adaptive_creative_exploration_enabled: bool = Field(
        default=True,
        description=(
            "初始化是否允许自适应 2+1 创意探索。关闭时将 adaptive 请求"
            "安全降级为原有单路径，用于 A/B 与紧急回退。"
        ),
    )
    init_progressive_planning_enabled: bool = Field(
        default=True,
        description=(
            "初始化是否允许渐进式规划。关闭时将 progressive 请求降级为"
            "全量规划，不修改任何已有项目数据。"
        ),
    )
    init_creative_refinement_auto_apply_low_risk: bool = Field(
        default=True,
        description="创意增强仅当建议为低风险且带可验证 scope 时自动应用 patch。",
    )
    init_readiness_required: bool = Field(
        default=True,
        description="章节生成 preflight 是否要求初始化准入报告通过。",
    )
    outline_thinking: bool = False  # 大纲生成是否开启思考模式（总开关）
    outline_thinking_providers: str = (
        "tongyi,deepseek"  # 允许开启思考模式的 provider 列表（逗号分隔）
    )
    outline_thinking_models: str = ""  # 允许开启思考模式的模型列表（逗号分隔，空=不按模型限制）
    outline_multi_turn: bool = True  # 大纲续写是否开启多轮对话（总开关）
    outline_multi_turn_providers: str = "tongyi,deepseek"  # 允许开启多轮对话的 provider 列表
    outline_multi_turn_models: str = ""  # 允许开启多轮对话的模型列表（空=不按模型限制）
    outline_tracker_enabled: bool = Field(
        default=True,
        description=(
            "启用大纲阶段角色关系追踪器。追踪章节间的关系变化、关键事件、主题出现和待解决悬念，"
            "为后续章节生成提供跨章节上下文。"
        ),
    )
    edit_multi_turn: bool = False  # 章节编辑轮次间是否开启多轮对话（总开关）
    edit_multi_turn_providers: str = "tongyi,deepseek"  # 允许开启编辑多轮对话的 provider 列表
    edit_multi_turn_models: str = ""  # 允许开启编辑多轮对话的模型列表（空=不按模型限制）
    canon_context_max_recent_events: int = 40
    canon_context_max_characters: int = 15
    canon_context_max_foreshadowing: int = 30
    canon_context_max_world_facts: int = 80
    extract_canon_max_existing_thread_ids: int = Field(
        default=30,
        ge=0,
        le=200,
        description="Canon 提取时注入的已有线索 ID 上限。0=不注入。",
    )
    extract_canon_max_prior_relationships: int = Field(
        default=12,
        ge=0,
        le=100,
        description="Canon 提取时注入的前态人物关系条目上限。0=不注入。",
    )
    extract_canon_recent_character_window_chapters: int = Field(
        default=5,
        ge=0,
        le=50,
        description="Canon 提取时前态角色快照的回看章节数。0=不过滤最近章节窗口。",
    )
    extract_canon_max_prior_characters: int = Field(
        default=12,
        ge=0,
        le=100,
        description="Canon 提取时注入的前态角色快照数量上限。0=不注入。",
    )
    extract_canon_max_prior_plot_threads: int = Field(
        default=10,
        ge=0,
        le=100,
        description="Canon 提取时注入的前态活跃线索数量上限。0=不注入。",
    )
    extract_canon_prior_plot_thread_summary_chars: int = Field(
        default=60,
        ge=0,
        le=500,
        description="Canon 提取时每条前态线索摘要保留的最大字符数。0=不保留摘要。",
    )
    extract_canon_output_base_tokens: int = Field(
        default=4096,
        ge=256,
        le=32768,
        description="Canon 提取输出预算的基础 token 数。",
    )
    extract_canon_output_tokens_per_character: int = Field(
        default=450,
        ge=0,
        le=4000,
        description="Canon 提取时每个已知角色额外分配的输出 token。",
    )
    extract_canon_output_tokens_per_2500_chars: int = Field(
        default=768,
        ge=0,
        le=4096,
        description="Canon 提取时每 2500 字正文额外分配的输出 token。",
    )
    extract_canon_output_max_tokens: int = Field(
        default=12288,
        ge=1024,
        le=65536,
        description="Canon 提取单次调用的输出 token 上限。",
    )
    extract_canon_abort_on_severe_damage: bool = Field(
        default=True,
        description="Canon 提取解析后若检测到重度结构损坏，是否立即终止而不是继续污染后续 canon。",
    )
    extract_canon_severe_damage_missing_section_threshold: int = Field(
        default=2,
        ge=1,
        le=4,
        description=(
            "Canon 提取时，若原始响应中出现但解析结果丢失的顶层区块数达到该阈值，"
            "视为重度损坏并中止。"
        ),
    )
    extract_canon_max_character_state_deltas: int = Field(
        default=8,
        ge=1,
        le=50,
        description="Canon 提取时 character_state_deltas 的建议上限。",
    )
    extract_canon_max_relationship_deltas: int = Field(
        default=8,
        ge=1,
        le=50,
        description="Canon 提取时 relationship_deltas 的建议上限。",
    )
    extract_canon_max_plot_thread_deltas: int = Field(
        default=8,
        ge=1,
        le=50,
        description="Canon 提取时 plot_thread_deltas 的建议上限。",
    )
    extract_canon_max_exit_state_characters: int = Field(
        default=6,
        ge=1,
        le=30,
        description="Canon 提取时 chapter_exit_state.character_end_states 的建议上限。",
    )

    # ── Memory Module Settings ─────────────────────────
    memory_episodic_enabled: bool = Field(
        default=True,
        description="Enable episodic memory for semantic retrieval of historical events",
    )
    memory_embedding_profile_id: str = Field(
        default="",
        description="Selected embedding model profile ID for episodic memory",
    )
    memory_use_mock_embeddings: bool = Field(
        default=False,
        description=(
            "Force episodic memory to use deterministic mock embeddings "
            "(recommended for tests/offline runs)."
        ),
    )
    memory_vector_store_backend: Literal["zvec", "in_memory"] = Field(
        default="zvec",
        description=(
            "Vector store backend for episodic semantic search. "
            "zvec is the production backend; in_memory is reserved for tests/mock embeddings."
        ),
    )
    memory_zvec_memory_limit_mb: int = Field(
        default=512,
        ge=64,
        le=65536,
        description="Soft memory cap passed to Zvec initialization when the Zvec backend is used.",
    )
    memory_zvec_index_type: Literal["hnsw", "ivf", "flat", "hnsw_rabitq", "diskann"] = Field(
        default="hnsw",
        description="Zvec dense vector index type for episodic memory.",
    )
    memory_semantic_search_enabled: bool = Field(
        default=True,
        description="Enable semantic search enhancement in CanonRetriever",
    )
    memory_multi_granularity_summary_enabled: bool = Field(
        default=True,
        description="Enable multi-granularity summary service (scene/chapter/volume/arc)",
    )
    memory_chapter_summary_target_words: int = Field(
        default=200,
        ge=50,
        le=2000,
        description="Target character count for chapter-level summaries generated by memory.",
    )
    memory_volume_summary_target_words: int = Field(
        default=1000,
        ge=300,
        le=5000,
        description="卷级全局摘要的目标字数；用于跨卷承接和离线全局记忆。",
    )
    memory_summary_input_token_budget: int = Field(
        default=24000,
        ge=2048,
        le=120000,
        description=(
            "单次摘要 LLM 调用的输入 token 预算。超出时按完整覆盖分块执行层级 map-reduce，"
            "不会截掉后半段正文或章节摘要。"
        ),
    )
    memory_summary_recent_chapters: int = Field(
        default=5,
        ge=1,
        le=20,
        description=(
            "章节规划/生成直接注入的最近章摘要时间窗。"
            "更早历史由 Zvec 语义检索和卷级摘要承担，不再对摘要文本二次截断。"
        ),
    )
    memory_adaptive_compression_enabled: bool = Field(
        default=True,
        description="Enable adaptive compression with quality verification",
    )
    memory_motif_tracking_enabled: bool = Field(
        default=True,
        description="Enable motif/theme tracking for intentional callbacks",
    )
    memory_motif_check_repetition: bool = Field(
        default=True,
        description="Check for unintentional motif repetition",
    )
    memory_motif_re_extract_concurrency: int = Field(
        default=3,
        ge=1,
        le=10,
        description=(
            "Concurrency for Layer 2 motif re-extraction (force re-extract mode). "
            "Controls how many chapters are processed simultaneously via semaphore. "
            "Higher values speed up bulk repair but increase concurrent LLM API calls."
        ),
    )
    memory_motif_warmup_chapters: int = Field(
        default=3,
        ge=1,
        le=10,
        description=(
            "Number of early chapters that use motif warmup (seed-based pre-population) "
            "instead of LLM extraction. During warmup, get_motifs_for_prompt() returns "
            "seed-derived active motifs with no forbidden_repetition or LLM callbacks."
        ),
    )
    memory_concurrent_indexing: bool = Field(
        default=True,
        description="Run post-chapter memory tasks (motif/summary/episodic) concurrently via asyncio.gather; when False, run sequentially",
    )
    memory_volume_summary_inject_threshold: int = Field(
        default=10,
        ge=1,
        le=200,
        description=(
            "卷级摘要注入 prompt 的章节阈值。"
            "当前章节超过此值时，L1_core_memory 层会追加卷级宏观摘要。"
            "默认 10，适合 20 章/卷的项目；若卷更长可调高，更短可调低。"
            "设为 1 则从第 2 章开始始终注入（不推荐，浪费 token）。"
        ),
    )
    memory_critic_agent_enabled: bool = Field(
        default=True,
        description="Enable CriticAgent for independent chapter validation",
    )
    memory_critic_agent_run_async: bool = Field(
        default=False,
        description="Run CriticAgent asynchronously in background (non-blocking)",
    )
    memory_critic_agent_timeout_s: float = Field(
        default=90.0,
        ge=5.0,
        le=300.0,
        description="Soft timeout in seconds for CriticAgent parallel checks; returns partial results on timeout",
    )
    memory_critic_agent_timeout_extend_attempts: int = Field(
        default=1,
        ge=0,
        le=5,
        description=(
            "How many additional wait rounds CriticAgent can auto-extend after the first timeout. "
            "0 disables extension and preserves one-shot timeout behavior."
        ),
    )
    memory_critic_agent_timeout_extend_multiplier: float = Field(
        default=1.5,
        ge=1.0,
        le=3.0,
        description=(
            "Multiplier applied to each subsequent timeout window during CriticAgent auto-extension. "
            "Example: base 60s with 1 retry and 1.5x waits 60s then 90s."
        ),
    )
    memory_critic_agent_cache_enabled: bool = Field(
        default=True,
        description="Enable route-aware in-memory cache for CriticAgent to avoid repeated checks on unchanged input",
    )
    memory_critic_agent_cache_max_entries: int = Field(
        default=24,
        ge=1,
        le=200,
        description="Maximum number of cached CriticAgent chapter reports kept in memory",
    )
    runtime_memory_context_max_entries: int = Field(
        default=64,
        ge=4,
        le=2048,
        description="Maximum number of in-memory MemoryContext objects kept in RuntimeServices",
    )

    # ── StoryKernel (SQLite-backed narrative state) ───
    story_kernel_db_path: str = Field(
        default="",
        description=(
            "SQLite database file path for StoryKernel. "
            "Leave empty to auto-generate from storage_root (e.g. ./data/<project>/story_kernel.db)."
        ),
    )
    story_kernel_wal_mode: bool = Field(
        default=True,
        description="Enable WAL (Write-Ahead Logging) mode for StoryKernel SQLite database.",
    )
    story_kernel_zvec_enabled: bool = Field(
        default=False,
        description="Enable ZVec semantic layer for StoryKernel (requires zvec optional dependency).",
    )
    kernel_contract_mode: Literal["off", "warn", "strict"] = Field(
        default="warn",
        description=(
            "FieldContract 运行时执行模式：off=不校验；warn=违规仅记录日志（默认）；"
            "strict=违规抛出 KernelContractViolationError 并中止写入。"
            "校验发生在 StoryKernelStateWriter 写边界，仅对声明了 step_names 的写入生效。"
        ),
    )

    # ── Runtime Control Plane (cross-project SQLite ledger) ───
    runtime_control_enabled: bool = Field(
        default=True,
        description=(
            "Enable the Runtime Control Plane: a cross-project SQLite ledger "
            "(runtime_control.db) tracking WorkUnit/RunAttempt/StageExecution lifecycle, "
            "immutable artifact lineage, and event audit trail. Shadow-writes only in "
            "early phases; does not affect existing pipeline behavior."
        ),
    )
    runtime_control_db_path: str = Field(
        default="",
        description=(
            "SQLite database file path for the Runtime Control Plane. "
            "Leave empty to auto-generate from storage_root "
            "(e.g. ./data/runtime_control.db). This is a global/cross-project DB, "
            "not per-project."
        ),
    )
    runtime_control_enforcement_mode: Literal["advisory", "enforce"] = Field(
        default="advisory",
        description=(
            "Permission-boundary behavior for the Runtime Control Plane. "
            "'advisory' records violations without blocking existing workflows; "
            "'enforce' rejects a stage operation that violates its declared boundary."
        ),
    )
    runtime_control_heartbeat_timeout_s: int = Field(
        default=300,
        ge=30,
        le=3600,
        description=(
            "Heartbeat timeout in seconds for detecting stale RunAttempts. "
            "Attempts whose heartbeat is older than this threshold are candidates "
            "for startup reconciliation."
        ),
    )

    # ── Temperatures (per stage) ─────────────────────
    temp_spec_enrich: float = Field(default=0.7, ge=0.0, le=2.0)
    temp_beats: float = Field(default=0.7, ge=0.0, le=2.0)
    temp_draft: float = Field(default=0.8, ge=0.0, le=2.0)
    temp_edit: float = Field(default=0.5, ge=0.0, le=2.0)
    temp_evaluate: float = Field(default=0.3, ge=0.0, le=2.0)
    temp_init_creative_direction_candidates: float = Field(default=0.75, ge=0.0, le=2.0)
    temp_init_creative_direction_select: float = Field(default=0.15, ge=0.0, le=2.0)
    temp_init_story_bible: float = Field(default=0.7, ge=0.0, le=2.0)
    temp_init_character_bible: float = Field(default=0.7, ge=0.0, le=2.0)
    temp_blueprint_element_select: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_init_entity_registry: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_init_narrative_contract: float = Field(default=0.25, ge=0.0, le=2.0)
    temp_plan_chapter_contracts: float = Field(default=0.25, ge=0.0, le=2.0)
    temp_synthesize_init_research_dossier: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_ground_outline_research: float = Field(default=0.15, ge=0.0, le=2.0)
    temp_plan_init_research_queries: float = Field(default=0.3, ge=0.0, le=2.0)
    temp_synthesize_model_prior_research: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_refine_init_coherence_profile: float = Field(default=0.1, ge=0.0, le=2.0)
    temp_extract_init_coherence_claims: float = Field(default=0.1, ge=0.0, le=2.0)
    temp_extract_blueprint_holistic_claims: float = Field(default=0.1, ge=0.0, le=2.0)
    temp_adjudicate_init_conflict_candidates: float = Field(default=0.1, ge=0.0, le=2.0)
    temp_adjudicate_blueprint_coherence: float = Field(default=0.1, ge=0.0, le=2.0)
    temp_adjudicate_outline_inheritance: float = Field(default=0.1, ge=0.0, le=2.0)
    temp_adjudicate_contract_coherence: float = Field(default=0.1, ge=0.0, le=2.0)
    temp_repair_init_artifact_patch: float = Field(default=0.15, ge=0.0, le=2.0)
    temp_refine_init_artifacts_from_synopsis: float = Field(default=0.15, ge=0.0, le=2.0)
    temp_plan_outline: float = Field(default=0.7, ge=0.0, le=2.0)
    temp_plan_outline_batch: float = Field(default=0.7, ge=0.0, le=2.0)
    temp_plan_outline_continue: float = Field(default=0.7, ge=0.0, le=2.0)
    temp_plan_chapter: float = Field(default=0.4, ge=0.0, le=2.0)
    temp_draft_chapter: float = Field(default=1.0, ge=0.0, le=2.0)
    temp_wave_chapter: float = Field(default=0.7, ge=0.0, le=2.0)
    temp_edit_chapter: float = Field(default=0.5, ge=0.0, le=2.0)
    temp_polish_chapter: float = Field(
        default=0.35,
        ge=0.0,
        le=2.0,
        description="终稿选段精修温度。右键选段火候建议与候选改写共用该路由温度。",
    )
    temp_extract_canon: float = Field(default=0.3, ge=0.0, le=2.0)
    temp_extract_candidate_state_deltas: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_adjudicate_state_delta: float = Field(default=0.1, ge=0.0, le=2.0)
    temp_adjudicate_contract_completion: float = Field(default=0.1, ge=0.0, le=2.0)
    temp_adjudicate_final_state: float = Field(default=0.1, ge=0.0, le=2.0)
    temp_repair_adjudicated_issue: float = Field(default=0.35, ge=0.0, le=2.0)
    temp_check_alignment: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_humanize_scan: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_humanize_paragraph_rewrite: float = Field(default=0.3, ge=0.0, le=2.0)
    temp_knowledge_boundary_audit: float = Field(default=0.1, ge=0.0, le=2.0)
    temp_init_knowledge_boundaries: float = Field(default=0.3, ge=0.0, le=2.0)
    temp_repair_knowledge_boundary: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_extract_knowledge_deltas: float = Field(default=0.15, ge=0.0, le=2.0)
    temp_macro_guard: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="宏观护栏审计温度。较低温度确保漂移评分稳定一致。",
    )
    temp_check_chapter: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_bridge_chapter: float = Field(default=0.3, ge=0.0, le=2.0)
    temp_check_continuity: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_validate_causal: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_patch_chapter: float = Field(
        default=0.15, ge=0.0, le=2.0, description="因果/连贯补丁修复的温度（越低越精确）。"
    )
    patch_chapter_max_tokens: int = Field(
        default=8192,
        ge=1024,
        le=16384,
        description="章节补丁修复的最大 token 数。多问题补丁需要足够空间返回完整 JSON。",
    )
    temp_repair_continuity: float = Field(default=0.4, ge=0.0, le=2.0)
    temp_repair_causal: float = Field(
        default=0.35,
        ge=0.0,
        le=2.0,
        description=(
            "因果链全文修复的温度。独立于 temp_edit_chapter 以便单独调优。"
            "比 temp_repair_continuity 略低，因为因果修复需要更精确地保持叙事结构。"
        ),
    )
    temp_repair_reading_power: float = Field(
        default=0.35,
        ge=0.0,
        le=2.0,
        description=("追读力修复的温度。独立于因果/连贯修复，以便单独调优章尾钩子和微兑现。"),
    )
    temp_post_repair_review: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description=(
            "修复后复判（post-repair re-evaluation）的温度。"
            "建议使用 0.2 以平衡稳定性和适度灵活性："
            "过低（0.0）会过于严格导致过度报告问题，"
            "过高（>0.5）会降低复判一致性。"
        ),
    )
    long_post_repair_review_independent_prompt: str = Field(
        default="",
        description=(
            "修复后独立审查员提示词（可选）。"
            "当设 strict_review=True 时，此提示词会追加到评估提示末尾，"
            "引导 LLM 以更严格的审查员视角进行复判。"
        ),
    )
    continuity_repair_max_tokens: int = Field(
        default=16384,
        ge=1024,
        le=32768,
        description="连贯性修复任务的最大 token 数上限（现代模型支持更大输出窗口，默认 16384）",
    )
    temp_volume_audit: float = Field(default=0.3, ge=0.0, le=2.0)
    temp_enrich_character: float = Field(default=0.7, ge=0.0, le=2.0)
    temp_adjudicate_character_introduction: float = Field(default=0.1, ge=0.0, le=2.0)
    temp_introduce_character: float = Field(default=0.75, ge=0.0, le=2.0)
    temp_generate_config: float = Field(default=0.9, ge=0.0, le=2.0)
    temp_polish_config: float = Field(default=0.75, ge=0.0, le=2.0)
    temp_profile_style: float = Field(default=0.4, ge=0.0, le=2.0)
    temp_profile_structure: float = Field(default=0.4, ge=0.0, le=2.0)
    temp_evaluate_reading_power: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description="追读力评估温度（建议低温以保证评分稳定性）",
    )
    auto_introduce_characters: bool = Field(
        default=True,
        description="章节生成前自动检测并生成新出场角色的档案",
    )
    long_auto_introduce_max_new_characters: int = Field(
        default=2,
        ge=0,
        le=10,
        description=(
            "每章自动写入 character_bible 的新增角色数量上限。"
            "0 表示不自动新增；建议 1-3，避免临时小角色污染人物设定。"
        ),
    )
    long_auto_introduce_pending_max_attempts: int = Field(
        default=2,
        ge=0,
        le=10,
        description="自动建档 pending 队列的最大重试次数；达到上限后本轮自动跳过。",
    )
    style_profile_enabled: bool = Field(
        default=True,
        description="初始化时根据项目要素自动生成专属写作风格规范",
    )
    style_profile_required: bool = Field(
        default=False,
        description=(
            "风格规范生成失败时是否阻塞后续流程。为 True 时，生成失败将抛出异常而非静默降级。"
        ),
    )

    # ── Reading Power Window Config ───────────────────────────────────
    reading_power_window_size: int = Field(
        default=5,
        ge=3,
        le=10,
        description="追读力窗口大小（覆盖章节数）",
    )
    reading_power_window_left_offset: int = Field(
        default=0,
        ge=-5,
        le=0,
        description="追读力窗口左偏移（负值表示向前扩展）",
    )
    reading_power_window_right_offset: int = Field(
        default=0,
        ge=0,
        le=5,
        description="追读力窗口右偏移（正值表示向后扩展）",
    )
    reading_power_suspense_delay_threshold: int = Field(
        default=3,
        ge=1,
        le=7,
        description="悬念延迟预警阈值（章节）",
    )
    reading_power_force_resolve_threshold: int = Field(
        default=5,
        ge=3,
        le=10,
        description="悬念强制兑现阈值（超过此章数必须兑现）",
    )
    reading_power_hook_alternation_threshold: int = Field(
        default=2,
        ge=1,
        le=4,
        description="钩子类型交替阈值（连续同类型钩子超过此值触发警告）",
    )
    reading_power_tension_deviation_tolerance: float = Field(
        default=1.5,
        ge=0.5,
        le=3.0,
        description="张力偏差容忍度（偏离大纲张力目标的容忍范围）",
    )
    reading_power_enabled: bool = Field(
        default=True,
        description="启用追读力窗口系统（跨章节悬念追踪与钩子优化）",
    )
    element_progress_llm_arbiter_enabled: bool = Field(
        default=False,
        description=(
            "是否启用要素执行灰区 LLM 仲裁。"
            "关闭时完全使用规则判定（默认，0 次额外 LLM 调用）；"
            "开启时仅对灰区结果做小规模复判。"
        ),
    )
    element_progress_llm_arbiter_max_items_per_chapter: int = Field(
        default=1,
        ge=0,
        le=5,
        description="每章最多触发 LLM 仲裁的要素数量，0=禁用。",
    )
    element_progress_llm_gray_score_low: float = Field(
        default=0.8,
        ge=0.0,
        le=10.0,
        description="要素规则分数灰区下界（含）。",
    )
    element_progress_llm_gray_score_high: float = Field(
        default=1.4,
        ge=0.0,
        le=10.0,
        description="要素规则分数灰区上界（含）。",
    )
    element_progress_llm_arbiter_max_tokens: int = Field(
        default=256,
        ge=64,
        le=1024,
        description="要素灰区仲裁的输出 token 上限。",
    )
    element_progress_llm_arbiter_temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="要素灰区仲裁温度（建议低温）。",
    )
    element_progress_hit_threshold: float = Field(
        default=1.6,
        ge=0.0,
        le=10.0,
        description="要素规则判定 hit 阈值（含）。",
    )
    element_progress_weak_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=10.0,
        description="要素规则判定 weak 阈值（含），低于此值为 miss。",
    )
    temp_adjust_outline: float = Field(default=0.7, ge=0.0, le=2.0)
    temp_context_compress: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_verify_compression: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_extract_motifs: float = Field(default=0.3, ge=0.0, le=2.0)
    temp_summarize_chapter: float = Field(default=0.3, ge=0.0, le=2.0)
    temp_summarize_volume: float = Field(default=0.3, ge=0.0, le=2.0)
    temp_summarize_arc: float = Field(default=0.3, ge=0.0, le=2.0)
    temp_summarize_scene: float = Field(default=0.3, ge=0.0, le=2.0)
    temp_critic_continuity: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_critic_character: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_critic_causal: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_critic_strengths: float = Field(default=0.3, ge=0.0, le=2.0)
    temp_plot_guard_judge: float = Field(default=0.2, ge=0.0, le=2.0)
    temp_polish_outline: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="初始化后大纲润色温度。较高温度允许结构建议更灵活，但仍保持章节字段约束。",
    )
    temp_book_consistency: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="全书一致性审计温度（建议低温以稳定定位结果）。",
    )

    # ── Logging ────────────────────────────────────────
    log_level: str = "INFO"
    log_keep_runs: int = Field(
        default=20,
        ge=0,
        description=(
            "Number of most-recent run-log directories to keep per project. "
            "Older directories are pruned when a new run starts. "
            "Set to 0 to disable pruning."
        ),
    )

    # ── API call timeout ───────────────────────────────
    api_connect_timeout_s: float = Field(
        default=30.0,
        ge=0.0,
        description=(
            "TCP/TLS connection timeout for model providers. This is separate from "
            "api_call_timeout_s, which bounds the complete request. Set to 0 to use "
            "the HTTP client's no-timeout behavior."
        ),
    )
    api_call_timeout_s: float = Field(
        default=900.0,
        ge=0.0,
        description=(
            "Per-API-call timeout in seconds. "
            "Applied via asyncio.wait_for() around each model request. "
            "Set to 0 to disable the timeout (not recommended). "
            "900s (15 min) is the default to accommodate repair_continuity and extract_canon tasks "
            "which require full-chapter output and can take 5+ minutes on qwen-max."
        ),
    )
    workflow_timeout_s: float = Field(
        default=3600.0,
        ge=0.0,
        description=(
            "End-to-end workflow timeout in seconds. "
            "Covers the entire pipeline execution (multiple API calls, retries, failovers). "
            "Set to 0 to disable the timeout (not recommended). "
            "3600s (1 hour) is the default to accommodate long-running chapter generation "
            "with multiple edit/repair rounds."
        ),
    )
    stream_idle_timeout_s: float = Field(
        default=120.0,
        ge=0.0,
        description=(
            "Inter-chunk idle timeout for streaming LLM calls in seconds. "
            "If no new token/chunk arrives within this window the stream is "
            "considered stalled and is cancelled. Set to 0 to disable "
            "(falls back to api_call_timeout_s as the only protection). "
            "120s detects half-open connections and model stalls quickly."
        ),
    )
    tts_script_batch_timeout_s: float = Field(
        default=360.0,
        ge=60.0,
        description=(
            "Per-batch timeout for TTS dubbing script LLM generation. "
            "Each batch processes ~1400 chars of chapter text and should "
            "complete within 60-120s normally. 360s (6 min) provides 3x "
            "headroom before falling back to rule-based generation."
        ),
    )

    # ── Dead Letter Queue ──────────────────────────────
    dlq_max_entries: int = Field(
        default=100,
        ge=0,
        le=10000,
        description=(
            "Maximum number of failed requests kept in the dead letter queue. "
            "Oldest entries are purged when this limit is exceeded. "
            "Set to 0 to disable DLQ persistence."
        ),
    )

    # ── Gateway Response Cache ─────────────────────────
    gateway_cache_enabled: bool = Field(
        default=True,
        description=(
            "Enable response caching for idempotent tasks (EVALUATE, CRITIC_*, CHECK_*, etc.). "
            "Creative tasks (DRAFT_*, EDIT_*, POLISH_*, BEATS, PROFILE_*, PLAN_*, INIT_*) "
            "never use the cache. Set to False to disable caching entirely."
        ),
    )
    gateway_cache_hit_telemetry: bool = Field(
        default=True,
        description="Log cache hit/miss events with per-task_type counters for observability.",
    )

    # ── Circuit Breaker ────────────────────────────────
    circuit_breaker_enabled: bool = Field(
        default=True,
        description="Enable per-provider circuit breaker for fault tolerance.",
    )
    circuit_breaker_threshold: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Consecutive failures before opening the circuit.",
    )
    circuit_breaker_recovery_s: float = Field(
        default=30.0,
        ge=1.0,
        le=600.0,
        description="Seconds to wait before transitioning OPEN → HALF_OPEN.",
    )

    # ── Task Circuit Breaker ───────────────────────────
    task_circuit_breaker_enabled: bool = Field(
        default=False,
        description=(
            "Enable per-TaskType circuit breaker. Disabled by default until each "
            "pipeline caller has an explicit safe fallback for TaskCircuitOpenError."
        ),
    )
    task_circuit_breaker_threshold: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Consecutive terminal failures per TaskType before opening its circuit.",
    )
    task_circuit_breaker_recovery_s: float = Field(
        default=60.0,
        ge=1.0,
        le=600.0,
        description="Seconds to wait before a task circuit transitions OPEN → HALF_OPEN.",
    )

    future_planning_mode: Literal["fixed", "proposal", "adaptive"] = Field(
        default="fixed", description="Future outline policy; existing projects remain fixed."
    )
    future_planning_window: int = Field(default=4, ge=1, le=12)
    future_planning_budget_reserve_usd: float = Field(
        default=1.0,
        gt=0,
        description="Required estimated headroom for optional future exploration and validation.",
    )

    # ── Cost budget control ────────────────────────────
    budget_daily_usd: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Daily spending limit in USD. "
            "Set to 0 to disable (no limit). "
            "When exceeded, API calls will be rejected with BudgetExceededError."
        ),
    )
    budget_monthly_usd: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Monthly spending limit in USD. "
            "Set to 0 to disable (no limit). "
            "When exceeded, API calls will be rejected with BudgetExceededError."
        ),
    )
    budget_warn_threshold: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description=(
            "Budget warning threshold as a fraction (0.0–1.0). "
            "Emits a warning log when spending reaches this fraction of the limit. "
            "Default 0.8 = warn at 80% of budget."
        ),
    )

    # ── Humanize (LLM-mediated text humanization) ────────
    humanize_enabled: bool = Field(
        default=False,
        description="启用 LLM 人工化润色（Humanize），使生成文本更自然、减少 AI 痕迹。",
    )
    humanize_change_ratio_cap: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Humanize 单次最大改动比例。默认 0.05（5%），防止过度改写。",
    )
    humanize_structural_change_ratio_cap: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description=(
            "Humanize 结构性重写（模板句式/结构模板/叙事轻重）时的宽松 cap。"
            "默认 0.15（15%），允许较大幅度的句式重构。"
        ),
    )
    humanize_min_text_length: int = Field(
        default=500,
        ge=0,
        description="Humanize 触发的最小文本长度（字符数）。小于此长度的文本跳过润色。",
    )
    humanize_model: str = Field(
        default="",
        description="Humanize 使用的 provider:model 覆盖。留空时复用标准模型路由。",
    )
    humanize_patch_confidence_floor: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description=(
            "Humanize 精确补丁的最低命中置信度。默认 0.80；"
            "低于此值的命中只进入报告，不执行手术式替换。"
        ),
    )
    humanize_paragraph_confidence_floor: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description=(
            "Humanize 段落级改写收集命中的最低置信度。默认 0.70；"
            "低于此值的命中不会触发段落级定向改写。"
        ),
    )
    humanize_paragraph_rewrite_threshold: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "触发段落级改写的不可 patch 命中数阈值。"
            "当 unpatchable_hits >= 此值时，对命中段落执行 LLM 定向改写。默认 3。"
        ),
    )
    humanize_recurrent_structural_min_occurrences: int = Field(
        default=3,
        ge=2,
        le=12,
        description=(
            "Humanize 结构家族的重复密度确认阈值。达到该次数后，即使 LLM 将"
            "不可单句 patch 的候选误判为非命中，本地复核仍保留并路由到段落级改写。"
        ),
    )

    # ── Humanize Library (example-based humanization) ────
    humanize_library_enabled: bool = Field(
        default=True,
        description="启用 Humanize Library（示例库润色），基于优秀段落示例改写。",
    )
    humanize_library_path: str = Field(
        default="",
        description="Humanize Library 路径。留空时默认使用 {storage_root}/_global/humanize_library。",
    )
    humanize_library_top_k: int = Field(
        default=30,
        ge=1,
        le=200,
        description="Humanize Library 检索时返回的最相似段落数量。默认 30。",
    )
    humanize_library_sim_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Humanize Library 段落匹配的最低相似度阈值。默认 0.6。",
    )
    humanize_library_seed_builtin: bool = Field(
        default=True,
        description="是否使用内置示例段落初始化 Humanize Library。默认 True。",
    )

    @property
    def humanize_library_resolved_path(self) -> Path:
        """Return the resolved humanize library directory path.

        If :attr:`humanize_library_path` is set (non-empty), resolve it:
        absolute paths are used as-is, relative paths are resolved against
        :attr:`storage_root`.  Otherwise default to
        ``{storage_root}/_global/humanize_library``.
        """
        raw = self.humanize_library_path
        if raw:
            p = Path(raw)
            if p.is_absolute():
                return p
            return self.storage_root / p
        return self.storage_root / "_global" / "humanize_library"

    @property
    def effective_storage_root(self) -> Path:
        """Return the tenant-aware storage root.

        In single-user local mode (``tenant_id`` empty), this is identical to
        :attr:`storage_root`.  In multi-tenant cloud mode, project data is
        namespaced under ``{storage_root}/{tenant_id}/`` so different tenants
        never share the same project directory.

        Callers that create or resolve project paths should prefer this
        property over raw :attr:`storage_root` to be tenant-safe.
        """
        if self.tenant_id:
            return self.storage_root / self.tenant_id
        return self.storage_root

    @property
    def is_cloud_mode(self) -> bool:
        """True when running in multi-user cloud/SaaS deployment mode."""
        return self.deployment_mode == "cloud"


def _merged_to_settings_kwargs(merged: dict[str, Any]) -> dict[str, Any]:
    """Convert UnifiedSettingsLoader output to :class:`Settings` kwargs.

    The loader eagerly parses env-style JSON strings into Python objects, but
    a few legacy :class:`Settings` fields still expose raw JSON text.  Serialise
    those values back to strings before constructing the settings model.  It
    also parses numeric-looking identifiers (for example a TTS voice type) as
    numbers, so restore scalar values for fields whose schema requires text.
    """
    kwargs = dict(merged)
    for key in (
        "task_routing",
        "task_fallback_routing",
        "research_mcp_args_json",
        "research_mcp_env_json",
        "research_mcp_tool_arguments_json",
        "audio_plugin_overrides",
        "audio_language_overrides",
    ):
        value = kwargs.get(key)
        if isinstance(value, dict | list):
            kwargs[key] = json.dumps(value, ensure_ascii=False)
        elif value is None:
            kwargs.pop(key, None)

    for key, field in Settings.model_fields.items():
        value = kwargs.get(key)
        if field.annotation is not str or not isinstance(value, bool | int | float):
            continue
        if isinstance(value, bool):
            kwargs[key] = "true" if value else "false"
        else:
            kwargs[key] = str(value)
    return kwargs


_settings_instance: Settings | None = None
_settings_source_fingerprint: str | None = None
_settings_lock = threading.Lock()


def get_settings(cli_args: dict[str, Any] | None = None) -> Settings:
    """Return shared Settings singleton.

    When *cli_args* is provided, they are forwarded to
    :class:`UnifiedSettingsLoader` so that CLI overrides participate in the
    merged configuration (highest priority).
    """
    global _settings_instance, _settings_source_fingerprint
    # Lazy import to avoid circular dependency at module load time.
    from novel_forge.core.infra.settings_loader import UnifiedSettingsLoader

    loader = UnifiedSettingsLoader(cli_args=cli_args or {})
    current_fingerprint = loader.fingerprint()
    with _settings_lock:
        if _settings_instance is None or _settings_source_fingerprint != current_fingerprint:
            merged = loader.load_all()
            kwargs = _merged_to_settings_kwargs(merged)
            # Settings is now fed by UnifiedSettingsLoader.  Disable pydantic's
            # own .env reader so source priority stays centralized here.
            _settings_instance = Settings(**kwargs, _env_file=None)
            _settings_source_fingerprint = current_fingerprint
        return _settings_instance


def get_cached_settings() -> Settings:
    """Backwards-compatible alias for the shared Settings singleton."""
    return get_settings()


def reset_settings() -> None:
    """Clear cached Settings so the next access reloads config sources."""
    global _settings_instance, _settings_source_fingerprint
    with _settings_lock:
        _settings_instance = None
        _settings_source_fingerprint = None
