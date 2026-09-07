"""Credential-free Engine projections and configuration mutations for Ollama."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from novel_forge.app_service.contracts import JobCommand, JobKind, JobRecord, JobScope, JobState
from novel_forge.app_service.engine_views import (
    OllamaCapabilitiesView,
    OllamaConfiguredRolesView,
    OllamaManagerView,
    OllamaModelView,
    OllamaOperationView,
    OllamaRoutingImpactView,
    OllamaRuntimeStatusView,
    OllamaSidecarView,
    OllamaStorageView,
)
from novel_forge.app_service.job_service import JobService
from novel_forge.app_service.ollama_control import (
    OllamaControlError,
    get_ollama_control_service,
    is_loopback_endpoint,
    validate_model_name,
)
from novel_forge.app_service.settings_environment import (
    clear_env_keys,
    persist_settings_environment,
)
from novel_forge.core.config import reset_settings
from novel_forge.gateway.profiles import ModelProfile, get_profiles_path, load_or_import_profiles

_DELETE_TOKEN_TTL_S = 300
_DELETE_TOKEN_SECRET = secrets.token_bytes(32)
_OLLAMA_JOB_KINDS = frozenset(
    {
        JobKind.OLLAMA_RUNTIME_CONTROL.value,
        JobKind.OLLAMA_PULL_MODEL.value,
        JobKind.OLLAMA_DELETE_MODEL.value,
    }
)


class OllamaConfigurationError(ValueError):
    """A safe, user-actionable configuration mutation error."""


@dataclass(frozen=True)
class OllamaConfigurationChange:
    revision: str
    removed_profile_ids: list[str]


def validate_ollama_base_url(value: str) -> str:
    """Accept only a credential-free HTTP(S) endpoint owned by the Engine."""

    endpoint = str(value or "").strip().rstrip("/")
    if not endpoint or len(endpoint) > 1024:
        raise OllamaConfigurationError("Ollama 地址不能为空且不能超过 1024 个字符。")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OllamaConfigurationError("Ollama 地址必须是完整的 HTTP(S) 地址。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise OllamaConfigurationError("Ollama 地址不能包含凭据、查询参数或片段。")
    return endpoint


def ollama_manager_view(settings: object, jobs: JobService | None = None) -> OllamaManagerView:
    """Build one Engine-hosted view without leaking local paths or credentials."""

    control = get_ollama_control_service(settings)
    config = load_or_import_profiles()
    revision = ollama_revision(settings, config)
    try:
        version = control.version()
        models = control.list_models()
        available = True
        error_detail = ""
    except OllamaControlError as exc:
        version = ""
        models = []
        available = False
        error_detail = str(exc)

    sidecar = control.sidecar
    owned = sidecar.owned_process
    if available:
        runtime_status = "healthy" if owned else "external"
        runtime_detail = "Engine 托管的 Ollama 可用。" if owned else "Engine 主机上的外部 Ollama 可用。"
        ownership = "engine_owned" if owned else "external"
    else:
        status_map = {
            "disabled": "disabled",
            "missing-binary": "missing_binary",
            "failed": "failed",
            "started": "starting",
            "healthy": "healthy",
        }
        runtime_status = status_map.get(sidecar.last_result.status, "degraded")
        runtime_detail = sidecar.last_result.detail or error_detail or "Ollama 当前不可用。"
        ownership = "unavailable"

    local_endpoint = is_loopback_endpoint(control.base_url)
    binary_available = sidecar.resolve_binary() is not None
    if not local_endpoint:
        storage = OllamaStorageView(scope="external", display_label="由 Engine 主机上的外部服务管理")
    elif owned:
        storage = OllamaStorageView(scope="engine_managed", display_label="Engine 托管的模型目录")
    else:
        storage = OllamaStorageView(scope="engine_local", display_label="Engine 主机的本地 Ollama 目录")

    impacts = _routing_impacts(config, settings, revision, [model.name for model in models])
    model_views = [
        OllamaModelView(
            name=model.name,
            size=max(0, model.size),
            modified_at=model.modified_at,
            details=model.details or {},
            roles=_model_roles(model.name, impacts.get(model.name)),
        )
        for model in models
    ]
    job_records = jobs.list() if jobs is not None else []
    active = [
        OllamaOperationView(
            task_id=record.job_id,
            kind=str(getattr(record.kind, "value", record.kind)),
            state=_job_state(record.status),
            model=str(record.result.get("model") or record.current_step_payload.get("model") or ""),
            operation=str(
                record.result.get("operation") or record.current_step_payload.get("operation") or ""
            ),
        )
        for record in job_records
        if record.scope == JobScope.SYSTEM
        and str(getattr(record.kind, "value", record.kind)) in _OLLAMA_JOB_KINDS
        and record.status in {JobState.QUEUED, JobState.RUNNING, JobState.PAUSED, JobState.FAILED}
    ]
    profile_ids = [profile.profile_id for profile in config.profiles if profile.provider == "ollama"]
    return OllamaManagerView(
        revision=revision,
        endpoint_scope="engine_host" if local_endpoint else "external_host",
        ownership=ownership,
        runtime=OllamaRuntimeStatusView(
            status=runtime_status,
            version=version,
            detail=runtime_detail,
        ),
        sidecar=OllamaSidecarView(
            enabled=sidecar.enabled,
            auto_start=sidecar.auto_start,
            prefer_local=sidecar.prefer_local_route,
            binary_available=binary_available,
        ),
        storage=storage,
        capabilities=OllamaCapabilitiesView(
            can_ensure=sidecar.enabled and local_endpoint,
            can_restart=sidecar.enabled and local_endpoint and owned,
            can_stop=owned,
            can_pull=available,
            can_delete=available,
            can_configure_paths=local_endpoint,
        ),
        models=model_views,
        configured_roles=OllamaConfiguredRolesView(
            generation_model=str(getattr(settings, "ollama_model", "") or ""),
            embedding_model=str(getattr(settings, "ollama_embedding_model", "") or ""),
            managed_profile_ids=profile_ids,
        ),
        routing_impact_by_model=impacts,
        active_operations=active,
    )


def ollama_revision(settings: object, config: Any | None = None) -> str:
    """Stable revision for optimistic writes and delete confirmations."""

    profiles = config if config is not None else load_or_import_profiles()
    payload = {
        "endpoint": str(getattr(settings, "ollama_base_url", "") or ""),
        "generation": str(getattr(settings, "ollama_model", "") or ""),
        "embedding": str(getattr(settings, "ollama_embedding_model", "") or ""),
        "sidecar": {
            field: getattr(settings, field, "")
            for field in (
                "ollama_sidecar_enabled",
                "ollama_sidecar_auto_start",
                "ollama_sidecar_binary_path",
                "ollama_sidecar_models_dir",
                "ollama_sidecar_prefer_local",
            )
        },
        "profiles": [
            {
                "id": profile.profile_id,
                "provider": profile.provider,
                "model": profile.model_id,
                "base": profile.base_url,
            }
            for profile in profiles.profiles
            if profile.provider == "ollama"
        ],
        "routes": {
            task_id: route.profile_id
            for task_id, route in profiles.routes.items()
            if str(route.profile_id).startswith("ollama:")
        },
        "fallbacks": {
            task_id: [entry.profile_id for entry in entries if entry.profile_id.startswith("ollama:")]
            for task_id, entries in profiles.fallback_routes.items()
        },
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


def verify_delete_confirmation(*, model: str, revision: str, token: str) -> None:
    """Require an unexpired, revision-bound deletion confirmation from the read model."""

    normalized = validate_model_name(model)
    try:
        expiry_text, signature = str(token or "").split(".", 1)
        expiry = int(expiry_text)
    except (TypeError, ValueError):
        raise OllamaConfigurationError("删除确认已失效，请刷新模型列表后重试。") from None
    if expiry < int(time.time()):
        raise OllamaConfigurationError("删除确认已过期，请刷新模型列表后重试。")
    expected = _delete_token(normalized, revision, expiry)
    if not hmac.compare_digest(token, expected):
        raise OllamaConfigurationError("删除确认与当前模型配置不匹配，请刷新后重试。")


def set_ollama_model_roles(
    settings: object,
    *,
    expected_revision: str,
    model: str,
    managed: bool,
    generation: bool,
    embedding: bool,
) -> OllamaConfigurationChange:
    """Atomically update the profile and configured generation/embedding roles."""

    normalized = validate_model_name(model)
    if not managed and (generation or embedding):
        raise OllamaConfigurationError("启用生成或嵌入角色前必须先纳入模型管理。")
    config = load_or_import_profiles()
    _assert_revision(settings, config, expected_revision)
    profile_ids = _model_profile_ids(config, normalized)
    profile_id = f"ollama:{normalized}"
    if managed:
        config.add_profile(
            ModelProfile(
                profile_id=profile_id,
                display_name=f"Ollama · {normalized}",
                provider="ollama",
                model_id=normalized,
                api_key="",
                base_url=str(getattr(settings, "ollama_base_url", "") or ""),
            )
        )
    else:
        for existing_id in profile_ids:
            config.remove_profile(existing_id)
    _persist_config_and_roles(
        config,
        settings,
        model=normalized,
        generation=generation,
        embedding=embedding,
    )
    reset_settings()
    return OllamaConfigurationChange(
        revision=ollama_revision(settings, config),
        removed_profile_ids=[] if managed else profile_ids,
    )


def configure_ollama_runtime(
    settings: object,
    *,
    expected_revision: str,
    base_url: str | None = None,
    enabled: bool | None = None,
    auto_start: bool | None = None,
    prefer_local: bool | None = None,
    binary_path: str | None = None,
    models_dir: str | None = None,
    allow_path_configuration: bool = False,
    allow_endpoint_configuration: bool = False,
) -> OllamaConfigurationChange:
    """Persist trusted Engine runtime settings without exposing paths on reads."""

    config = load_or_import_profiles()
    _assert_revision(settings, config, expected_revision)
    if (binary_path is not None or models_dir is not None) and not allow_path_configuration:
        raise OllamaConfigurationError("只有本机 Engine 会话可以修改 sidecar 目录。")
    if base_url is not None and not allow_endpoint_configuration:
        raise OllamaConfigurationError("只有本机 Engine 会话可以修改 Ollama 地址。")
    endpoint = (
        validate_ollama_base_url(base_url)
        if base_url is not None
        else str(getattr(settings, "ollama_base_url", "") or "")
    )
    values = {
        "ollama-url": endpoint,
        "ollama-sidecar": _bool_text(
            enabled if enabled is not None else bool(getattr(settings, "ollama_sidecar_enabled", True))
        ),
        "ollama-sidecar-auto-start": _bool_text(
            auto_start
            if auto_start is not None
            else bool(getattr(settings, "ollama_sidecar_auto_start", True))
        ),
        "ollama-sidecar-prefer-local": _bool_text(
            prefer_local
            if prefer_local is not None
            else bool(getattr(settings, "ollama_sidecar_prefer_local", True))
        ),
        "ollama-sidecar-binary-path": _validate_optional_engine_path(binary_path),
        "ollama-sidecar-models-dir": _validate_optional_engine_path(models_dir),
    }
    if binary_path is None:
        values["ollama-sidecar-binary-path"] = str(
            getattr(settings, "ollama_sidecar_binary_path", "") or ""
        )
    if models_dir is None:
        values["ollama-sidecar-models-dir"] = str(
            getattr(settings, "ollama_sidecar_models_dir", "") or ""
        )
    _persist_creation_values(values)
    reset_settings()
    return OllamaConfigurationChange(revision=ollama_revision(settings, config), removed_profile_ids=[])


def detach_ollama_model_configuration(
    settings: object,
    *,
    expected_revision: str,
    model: str,
) -> OllamaConfigurationChange:
    """Persist the configuration checkpoint before native model deletion starts."""

    normalized = validate_model_name(model)
    config = load_or_import_profiles()
    _assert_revision(settings, config, expected_revision)
    profile_ids = _model_profile_ids(config, normalized)
    for profile_id in profile_ids:
        config.remove_profile(profile_id)
    _persist_config_and_roles(
        config,
        settings,
        model=normalized,
        generation=False,
        embedding=False,
    )
    reset_settings()
    return OllamaConfigurationChange(
        revision=ollama_revision(settings, config), removed_profile_ids=profile_ids
    )


def _persist_config_and_roles(
    config: Any,
    settings: object,
    *,
    model: str,
    generation: bool,
    embedding: bool,
) -> None:
    config.save(get_profiles_path())
    current_generation = str(getattr(settings, "ollama_model", "") or "")
    current_embedding = str(getattr(settings, "ollama_embedding_model", "") or "")
    generation_model = model if generation else "" if current_generation == model else current_generation
    embedding_model = model if embedding else "" if current_embedding == model else current_embedding
    _persist_creation_values(
        {
            "ollama-model": generation_model,
            "ollama-embedding-model": embedding_model,
        }
    )


def _assert_revision(settings: object, config: Any, expected_revision: str) -> None:
    current = ollama_revision(settings, config)
    if not expected_revision or not hmac.compare_digest(expected_revision, current):
        raise OllamaConfigurationError("Ollama 配置已变化，请刷新后重试。")


def _persist_creation_values(values: dict[str, str]) -> None:
    env_by_field = {
        "ollama-model": "NOVEL_FORGE_OLLAMA_MODEL",
        "ollama-embedding-model": "NOVEL_FORGE_OLLAMA_EMBEDDING_MODEL",
        "ollama-url": "NOVEL_FORGE_OLLAMA_BASE_URL",
        "ollama-sidecar": "NOVEL_FORGE_OLLAMA_SIDECAR_ENABLED",
        "ollama-sidecar-auto-start": "NOVEL_FORGE_OLLAMA_SIDECAR_AUTO_START",
        "ollama-sidecar-prefer-local": "NOVEL_FORGE_OLLAMA_SIDECAR_PREFER_LOCAL",
        "ollama-sidecar-binary-path": "NOVEL_FORGE_OLLAMA_SIDECAR_BINARY_PATH",
        "ollama-sidecar-models-dir": "NOVEL_FORGE_OLLAMA_SIDECAR_MODELS_DIR",
    }
    present = {field_id: value for field_id, value in values.items() if value}
    if present:
        persist_settings_environment(
            creation_parameters=present,
            creative_temperature=None,
            routes=None,
            theme_id=None,
            font_preferences=None,
        )
    clear_env_keys({env_by_field[field_id] for field_id, value in values.items() if not value})


def _validate_optional_engine_path(value: str | None) -> str:
    if value is None:
        return ""
    path = str(value).strip()
    if not path:
        return ""
    if len(path) > 4096 or "\n" in path or "\r" in path:
        raise OllamaConfigurationError("sidecar 路径无效。")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise OllamaConfigurationError("sidecar 路径必须是 Engine 主机上的绝对路径。")
    return str(candidate)


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _routing_impacts(
    config: Any,
    settings: object,
    revision: str,
    model_names: list[str],
) -> dict[str, OllamaRoutingImpactView]:
    names = set(model_names)
    for profile in config.profiles:
        if profile.provider == "ollama":
            names.add(profile.model_id)
    impacts: dict[str, OllamaRoutingImpactView] = {}
    for name in sorted(names):
        profile_ids = _model_profile_ids(config, name)
        profile_id_set = set(profile_ids)
        primary = sorted(
            task_id for task_id, route in config.routes.items() if route.profile_id in profile_id_set
        )
        fallback = sorted(
            task_id
            for task_id, entries in config.fallback_routes.items()
            if any(entry.profile_id in profile_id_set for entry in entries)
        )
        generation = str(getattr(settings, "ollama_model", "") or "") == name
        embedding = str(getattr(settings, "ollama_embedding_model", "") or "") == name
        impacts[name] = OllamaRoutingImpactView(
            profile_ids=profile_ids,
            primary_route_ids=primary,
            fallback_route_ids=fallback,
            generation_selected=generation,
            embedding_selected=embedding,
            confirmation_token=_delete_token(name, revision, int(time.time()) + _DELETE_TOKEN_TTL_S),
        )
    return impacts


def _model_roles(name: str, impact: OllamaRoutingImpactView | None) -> list[str]:
    if impact is None:
        return []
    roles: list[str] = []
    if impact.generation_selected:
        roles.append("generation")
    if impact.embedding_selected:
        roles.append("embedding")
    if impact.profile_ids:
        roles.append("managed")
    return roles


def _model_profile_ids(config: Any, model: str) -> list[str]:
    return sorted(
        profile.profile_id
        for profile in config.profiles
        if profile.provider == "ollama" and profile.model_id == model
    )


def _delete_token(model: str, revision: str, expiry: int) -> str:
    message = f"{model}\n{revision}\n{expiry}".encode("utf-8")
    signature = hmac.new(_DELETE_TOKEN_SECRET, message, hashlib.sha256).hexdigest()
    return f"{expiry}.{signature}"


def _job_state(value: JobState) -> str:
    return "failed" if value == JobState.FAILED else value.value


class OllamaEngineCommandService:
    """Headless command facade shared by HTTP and embedded desktop hosts.

    It owns neither a UI nor an Ollama process.  The singleton control service
    owns the native client/sidecar, while this facade applies the versioned
    management contract and submits only system-scoped durable jobs.
    """

    def __init__(self, jobs: JobService | None = None) -> None:
        self._jobs = jobs

    def view(self, settings: object) -> OllamaManagerView:
        return ollama_manager_view(settings, self._jobs)

    def configure_runtime(
        self,
        settings: object,
        *,
        expected_revision: str,
        base_url: str | None = None,
        enabled: bool | None = None,
        auto_start: bool | None = None,
        prefer_local: bool | None = None,
        binary_path: str | None = None,
        models_dir: str | None = None,
        allow_path_configuration: bool = False,
        allow_endpoint_configuration: bool = False,
    ) -> OllamaManagerView:
        configure_ollama_runtime(
            settings,
            expected_revision=expected_revision,
            base_url=base_url,
            enabled=enabled,
            auto_start=auto_start,
            prefer_local=prefer_local,
            binary_path=binary_path,
            models_dir=models_dir,
            allow_path_configuration=allow_path_configuration,
            allow_endpoint_configuration=allow_endpoint_configuration,
        )
        from novel_forge.core.config import get_settings

        return self.view(get_settings())

    def set_model_roles(
        self,
        settings: object,
        *,
        expected_revision: str,
        model: str,
        managed: bool,
        generation: bool,
        embedding: bool,
    ) -> OllamaManagerView:
        set_ollama_model_roles(
            settings,
            expected_revision=expected_revision,
            model=model,
            managed=managed,
            generation=generation,
            embedding=embedding,
        )
        from novel_forge.core.config import get_settings

        return self.view(get_settings())

    def submit_runtime(
        self,
        settings: object,
        *,
        expected_revision: str,
        idempotency_key: str,
        operation: str,
    ) -> JobRecord:
        view = self._assert_current_view(settings, expected_revision)
        allowed = {
            "ensure": view.capabilities.can_ensure,
            "restart": view.capabilities.can_restart,
            "stop": view.capabilities.can_stop,
        }.get(operation, False)
        if not allowed:
            raise OllamaConfigurationError("当前 Ollama 运行态不允许此操作。")
        if operation in {"restart", "stop"} and any(
            item.kind in {JobKind.OLLAMA_PULL_MODEL.value, JobKind.OLLAMA_DELETE_MODEL.value}
            and item.state in {"queued", "running"}
            for item in view.active_operations
        ):
            raise OllamaConfigurationError("模型下载或删除仍在执行，暂不能重启或停止 Ollama。")
        return self._submit(
            JobKind.OLLAMA_RUNTIME_CONTROL,
            label=f"Ollama · {operation}",
            payload={"operation": operation},
            idempotency_key=idempotency_key,
        )

    def submit_pull(
        self,
        settings: object,
        *,
        expected_revision: str,
        idempotency_key: str,
        model: str,
    ) -> JobRecord:
        view = self._assert_current_view(settings, expected_revision)
        if not view.capabilities.can_pull:
            raise OllamaConfigurationError("Ollama 不可用，请先检查或启动运行时。")
        normalized = validate_model_name(model)
        return self._submit(
            JobKind.OLLAMA_PULL_MODEL,
            label=f"下载 Ollama 模型 · {normalized}",
            payload={"model": normalized},
            idempotency_key=idempotency_key,
        )

    def submit_delete(
        self,
        settings: object,
        *,
        expected_revision: str,
        idempotency_key: str,
        model: str,
        confirmation_token: str,
        cascade_configuration: bool,
    ) -> JobRecord:
        view = self._assert_current_view(settings, expected_revision)
        if not view.capabilities.can_delete:
            raise OllamaConfigurationError("当前 Ollama 不可用，无法删除模型。")
        normalized = validate_model_name(model)
        impact = view.routing_impact_by_model.get(normalized)
        if impact is None:
            raise OllamaConfigurationError("模型不存在或模型列表已过期。")
        has_impact = bool(
            impact.profile_ids
            or impact.primary_route_ids
            or impact.fallback_route_ids
            or impact.generation_selected
            or impact.embedding_selected
        )
        if has_impact and not cascade_configuration:
            raise OllamaConfigurationError("模型仍被配置或路由引用；请确认清理配置后再删除。")
        verify_delete_confirmation(
            model=normalized,
            revision=expected_revision,
            token=confirmation_token,
        )
        detached = detach_ollama_model_configuration(
            settings,
            expected_revision=expected_revision,
            model=normalized,
        )
        return self._submit(
            JobKind.OLLAMA_DELETE_MODEL,
            label=f"删除 Ollama 模型 · {normalized}",
            payload={"model": normalized},
            idempotency_key=idempotency_key,
            metadata={
                "configuration_checkpoint": "config_detached",
                "removed_profile_ids": detached.removed_profile_ids,
            },
        )

    def _assert_current_view(self, settings: object, expected_revision: str) -> OllamaManagerView:
        view = self.view(settings)
        if not expected_revision or not hmac.compare_digest(view.revision, expected_revision):
            raise OllamaConfigurationError("Ollama 配置已变化，请刷新后重试。")
        return view

    def _submit(
        self,
        kind: JobKind,
        *,
        label: str,
        payload: dict[str, str],
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> JobRecord:
        if self._jobs is None:
            raise OllamaConfigurationError("Engine 系统任务尚未就绪，请稍后重试。")
        return self._jobs.submit(
            JobCommand(
                kind=kind,
                scope=JobScope.SYSTEM,
                label=label,
                payload=payload,
                metadata={"idempotency_key": idempotency_key, **(metadata or {})},
            )
        )


__all__ = [
    "OllamaConfigurationChange",
    "OllamaConfigurationError",
    "OllamaEngineCommandService",
    "configure_ollama_runtime",
    "detach_ollama_model_configuration",
    "ollama_manager_view",
    "ollama_revision",
    "set_ollama_model_roles",
    "validate_ollama_base_url",
    "verify_delete_confirmation",
]
