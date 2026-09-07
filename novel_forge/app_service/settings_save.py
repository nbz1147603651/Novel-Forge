"""Engine-owned application service for model-routing settings writes.

The command resolves the profile registry, task routes and runtime reload in
one place.  A transport supplies the allowlisted environment persistence
callback so the service never accepts arbitrary environment variable names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Literal

from novel_forge.app_service.chapter_runtime_policy import (
    chapter_runtime_policy_creation_parameters,
)
from novel_forge.app_service.settings_environment import (
    persist_settings_environment,
    validate_creation_parameters,
)
from novel_forge.app_service.settings_store import SettingsStore
from novel_forge.gateway.embedding_config import is_embedding_model
from novel_forge.gateway.profiles import (
    ModelProfile,
    ProfilesConfig,
    TaskRouteEntry,
    get_model_capabilities,
    load_or_import_profiles,
)


@dataclass(frozen=True)
class SettingsRouteCommand:
    """One task-routing change submitted through the Engine boundary."""

    primary_profile_id: str = ""
    fallback_routes: list[dict[str, Any]] | None = None
    thinking_enabled: bool = False
    multi_turn_enabled: bool = False
    temperature: float | None = None


@dataclass(frozen=True)
class SettingsSaveCommand:
    """Sanitized settings mutation independent of any UI framework."""

    default_profile_id: str | None = None
    profiles: list[dict[str, Any]] | None = None
    routes: dict[str, SettingsRouteCommand] | None = None
    creative_temperature: dict[str, Any] | None = None
    theme_id: str | None = None
    font_preferences: dict[str, Any] | None = None
    creation_parameters: dict[str, str] | None = None
    chapter_runtime_policy: dict[str, Any] | None = None


@dataclass(frozen=True)
class SettingsSaveCommandResult:
    """The stable acknowledgement returned by Engine settings commands."""

    status: Literal["saved", "partial", "validation_error"]
    persistence: Literal["persisted", "accepted_only"]
    message: str
    accepted_route_ids: list[str]
    rejected_routes: list[dict[str, str]]
    saved_at_label: str
    runtime_reload_status: Literal["reloaded", "current", "unavailable", "failed"] = "unavailable"


def _is_generation_profile(profile: ModelProfile | None) -> bool:
    """Return whether a profile can be used by a text-generation task route."""

    return profile is not None and not is_embedding_model(profile.provider, profile.model_id)


def _first_generation_profile(profiles: list[ModelProfile]) -> ModelProfile | None:
    """Choose the first text-generation profile for a repaired default route."""

    return next((profile for profile in profiles if _is_generation_profile(profile)), None)


def _sanitize_generation_routing(config: ProfilesConfig) -> list[dict[str, str]]:
    """Remove stale embedding references from all persisted generation routes.

    Older Web settings could save embedding profiles as a text route or in a
    group fallback.  Keep the profile itself (it remains valid for memory),
    but make every generation-routing representation safe before persistence.
    """

    rejected: list[dict[str, str]] = []
    for task_key, route in list(config.routes.items()):
        profile = config.get_profile(route.profile_id)
        if _is_generation_profile(profile):
            continue
        config.routes.pop(task_key, None)
        config.fallback_routes.pop(task_key, None)
        if profile is not None:
            rejected.append(
                {
                    "route_id": task_key,
                    "reason": "non-generation profile removed from text-generation route",
                }
            )

    for task_key, entries in list(config.fallback_routes.items()):
        primary = config.routes.get(task_key)
        primary_profile_id = primary.profile_id if primary is not None else ""
        seen_profile_ids: set[str] = set()
        filtered_entries: list[TaskRouteEntry] = []
        for entry in entries:
            if (
                entry.profile_id == primary_profile_id
                or entry.profile_id in seen_profile_ids
                or not _is_generation_profile(config.get_profile(entry.profile_id))
            ):
                continue
            filtered_entries.append(entry)
            seen_profile_ids.add(entry.profile_id)
            if len(filtered_entries) >= 3:
                break
        if filtered_entries:
            config.fallback_routes[task_key] = filtered_entries
        else:
            config.fallback_routes.pop(task_key, None)

    cleaned_group_routes: dict[str, dict[str, object]] = {}
    for group_key, raw in config.group_bulk_routes.items():
        if not isinstance(raw, dict):
            continue
        primary_profile_id = str(raw.get("profile_id", "") or "").strip()
        primary_profile = config.get_profile(primary_profile_id)
        if primary_profile_id and not _is_generation_profile(primary_profile):
            if primary_profile is not None:
                rejected.append(
                    {
                        "route_id": f"__group__:{group_key}",
                        "reason": "non-generation profile removed from text-generation group route",
                    }
                )
            continue
        clean_raw = dict(raw)
        fallback_routes: list[dict[str, object]] = []
        seen_profile_ids: set[str] = set()
        for fallback in raw.get("fallback_routes", []) or []:
            if not isinstance(fallback, dict):
                continue
            fallback_profile_id = str(fallback.get("profile_id", "") or "").strip()
            if (
                not fallback_profile_id
                or fallback_profile_id == primary_profile_id
                or fallback_profile_id in seen_profile_ids
                or not _is_generation_profile(config.get_profile(fallback_profile_id))
            ):
                continue
            fallback_routes.append(dict(fallback))
            seen_profile_ids.add(fallback_profile_id)
            if len(fallback_routes) >= 3:
                break
        clean_raw["fallback_routes"] = fallback_routes
        cleaned_group_routes[group_key] = clean_raw
    config.group_bulk_routes = cleaned_group_routes

    if not _is_generation_profile(config.get_profile(config.default_profile_id)):
        fallback_default = _first_generation_profile(config.profiles)
        config.default_profile_id = fallback_default.profile_id if fallback_default else ""
    return rejected


def synchronize_profile_commands(
    config: ProfilesConfig,
    profile_commands: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Apply profile drafts without persisting or exposing protected keys."""

    existing = {profile.profile_id: profile for profile in config.profiles}
    next_profiles: list[ModelProfile] = []
    rejected: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for raw in profile_commands:
        profile_id = str(raw.get("id", "") or "").strip()
        label = str(raw.get("label", "") or "").strip()
        provider = str(raw.get("provider", "") or "").strip().lower()
        model = str(raw.get("model", "") or "").strip()
        if (
            not profile_id
            or not label
            or not provider
            or not model
            or len(profile_id) > 160
            or len(label) > 160
            or len(provider) > 80
            or len(model) > 200
            or profile_id in seen_ids
        ):
            rejected.append(
                {
                    "route_id": f"__profile__:{profile_id or 'invalid'}",
                    "reason": "invalid profile",
                }
            )
            continue

        previous_id = str(raw.get("previous_id", raw.get("previousId", "")) or "").strip()
        previous = existing.get(previous_id or profile_id)
        api_key_action = (
            str(raw.get("api_key_action", raw.get("apiKeyAction", "preserve")) or "preserve")
            .strip()
            .lower()
        )
        if api_key_action not in {"preserve", "replace", "clear"}:
            rejected.append(
                {"route_id": f"__profile__:{profile_id}", "reason": "invalid api key action"}
            )
            continue
        submitted_api_key = str(raw.get("api_key", raw.get("apiKey", "")) or "").strip()
        if api_key_action == "replace" and (
            not submitted_api_key or len(submitted_api_key) > 4096 or "\n" in submitted_api_key
        ):
            rejected.append({"route_id": f"__profile__:{profile_id}", "reason": "invalid api key"})
            continue
        api_key = (
            submitted_api_key
            if api_key_action == "replace"
            else ""
            if api_key_action == "clear"
            else previous.api_key
            if previous is not None
            else ""
        )
        submitted_base_url = raw.get("base_url", raw.get("baseUrl"))
        base_url = (
            str(submitted_base_url or "").strip()
            if submitted_base_url is not None
            else previous.base_url
            if previous is not None
            else ""
        )
        if len(base_url) > 2048 or "\n" in base_url or "\r" in base_url:
            rejected.append({"route_id": f"__profile__:{profile_id}", "reason": "invalid base url"})
            continue
        next_profiles.append(
            ModelProfile(
                profile_id=profile_id,
                display_name=label,
                provider=provider,
                model_id=model,
                api_key=api_key,
                base_url=base_url,
            )
        )
        seen_ids.add(profile_id)

    config.profiles = next_profiles
    rejected.extend(_sanitize_generation_routing(config))
    return rejected


async def save_settings_command(
    command: SettingsSaveCommand,
    *,
    reload_runtime: Callable[[], None] | None = None,
) -> SettingsSaveCommandResult:
    """Persist a complete settings draft and refresh future runtime services.

    The application service owns validation and persistence.  A host which
    caches runtime dependencies (the Engine API) supplies ``reload_runtime``
    so the service does not reach upward into a transport-specific singleton.
    """

    from novel_forge.core.task_catalog import MULTI_TURN_TASK_KEYS, ROUTING_GROUPS

    config = load_or_import_profiles()
    previous_managed_api_keys = config.managed_api_key_env_vars()
    accepted_route_ids: list[str] = []
    rejected_routes: list[dict[str, str]] = []
    requested_creation_parameters = dict(command.creation_parameters or {})
    if command.chapter_runtime_policy is not None:
        try:
            requested_creation_parameters.update(
                chapter_runtime_policy_creation_parameters(command.chapter_runtime_policy)
            )
        except ValueError as exc:
            rejected_routes.append(
                {"route_id": "__setting__:chapter-runtime-policy", "reason": str(exc)}
            )
    validated_creation_parameters = requested_creation_parameters or None
    if requested_creation_parameters:
        validated_creation_parameters, parameter_rejections = validate_creation_parameters(
            requested_creation_parameters
        )
        rejected_routes.extend(parameter_rejections)

    if command.profiles is not None:
        rejected_routes.extend(synchronize_profile_commands(config, command.profiles))

    valid_task_keys = {task.key for group in ROUTING_GROUPS for task in group.tasks}
    if command.default_profile_id:
        default_profile = config.get_profile(command.default_profile_id)
        if default_profile is None:
            rejected_routes.append({"route_id": "__default__", "reason": "profile not found"})
        elif not _is_generation_profile(default_profile):
            rejected_routes.append(
                {
                    "route_id": "__default__",
                    "reason": "embedding profile cannot be the default generation model",
                }
            )
        elif not default_profile.is_key_configured:
            rejected_routes.append({"route_id": "__default__", "reason": "profile unavailable"})
        else:
            config.default_profile_id = command.default_profile_id

    if command.routes is not None:
        for task_key, route_command in command.routes.items():
            if task_key not in valid_task_keys:
                rejected_routes.append({"route_id": task_key, "reason": "unknown task"})
                continue
            # An empty primary route is deliberate: it clears the task-specific
            # override so the task inherits the configured default generation
            # model.  Treating it as an unavailable profile left stale routes
            # in place and made the saved UI diverge from the live router.
            if not route_command.primary_profile_id:
                config.routes.pop(task_key, None)
                config.fallback_routes.pop(task_key, None)
                accepted_route_ids.append(task_key)
                continue
            profile = config.get_profile(route_command.primary_profile_id)
            if profile is None or not profile.is_key_configured:
                rejected_routes.append({"route_id": task_key, "reason": "profile unavailable"})
                continue
            if not _is_generation_profile(profile):
                rejected_routes.append(
                    {
                        "route_id": task_key,
                        "reason": "embedding profile cannot be used for a text-generation task",
                    }
                )
                continue
            supports_thinking, supports_multi_turn = get_model_capabilities(
                profile.provider, profile.model_id
            )
            config.routes[task_key] = TaskRouteEntry(
                profile_id=route_command.primary_profile_id,
                thinking=route_command.thinking_enabled and supports_thinking,
                multi_turn=(
                    route_command.multi_turn_enabled
                    and supports_multi_turn
                    and task_key in MULTI_TURN_TASK_KEYS
                ),
            )
            fallbacks: list[TaskRouteEntry] = []
            seen_fallback_ids: set[str] = set()
            for fallback in route_command.fallback_routes or []:
                if len(fallbacks) >= 3:
                    break
                fallback_profile_id = str(fallback.get("profile_id", "") or "").strip()
                fallback_profile = config.get_profile(fallback_profile_id)
                if (
                    fallback_profile is None
                    or not fallback_profile.is_key_configured
                    or fallback_profile_id == route_command.primary_profile_id
                    or fallback_profile_id in seen_fallback_ids
                ):
                    continue
                if not _is_generation_profile(fallback_profile):
                    rejected_routes.append(
                        {
                            "route_id": task_key,
                            "reason": (
                                "embedding profile removed from text-generation fallback: "
                                f"{fallback_profile_id}"
                            ),
                        }
                    )
                    continue
                fallback_thinking, fallback_multi_turn = get_model_capabilities(
                    fallback_profile.provider, fallback_profile.model_id
                )
                fallbacks.append(
                    TaskRouteEntry(
                        profile_id=fallback_profile_id,
                        thinking=bool(fallback.get("thinking_enabled", False)) and fallback_thinking,
                        multi_turn=(
                            bool(fallback.get("multi_turn_enabled", False))
                            and fallback_multi_turn
                            and task_key in MULTI_TURN_TASK_KEYS
                        ),
                    )
                )
                seen_fallback_ids.add(fallback_profile_id)
            if fallbacks:
                config.fallback_routes[task_key] = fallbacks
            else:
                config.fallback_routes.pop(task_key, None)
            accepted_route_ids.append(task_key)

    rejected_routes.extend(_sanitize_generation_routing(config))

    persistence: Literal["persisted", "accepted_only"] = "accepted_only"
    profiles_persisted = False
    try:
        current_managed_api_keys = config.managed_api_key_env_vars()
        SettingsStore().save(
            config=config,
            env_pairs=config.to_env_pairs(),
            strip_env_keys=previous_managed_api_keys - current_managed_api_keys,
        )
        persistence = "persisted"
        profiles_persisted = True
    except OSError:
        pass

    try:
        persist_settings_environment(
            creation_parameters=validated_creation_parameters,
            creative_temperature=command.creative_temperature,
            routes=command.routes,
            theme_id=command.theme_id,
            font_preferences=command.font_preferences,
        )
    except OSError:
        persistence = "accepted_only"

    runtime_reload_status: Literal["reloaded", "current", "unavailable", "failed"] = (
        "unavailable"
    )
    if profiles_persisted and reload_runtime is not None:
        try:
            # This invalidates the API's settings/router/runtime caches.  New
            # jobs receive a fresh router; in-flight model calls are never
            # mutated halfway through a request.
            reload_runtime()
            runtime_reload_status = "reloaded"
        except Exception:  # noqa: BLE001
            runtime_reload_status = "failed"

    now = datetime.now()
    status: Literal["saved", "partial", "validation_error"] = (
        "saved" if not rejected_routes else "partial"
    )
    if runtime_reload_status == "failed":
        status = "partial"
    accepted_non_route_change = any(
        (
            command.default_profile_id,
            command.profiles,
            validated_creation_parameters,
            command.creative_temperature,
            command.theme_id,
            command.font_preferences,
            command.chapter_runtime_policy,
        )
    )
    if rejected_routes and not accepted_route_ids and not accepted_non_route_change:
        status = "validation_error"
    runtime_message = {
        "reloaded": "运行时已重载；新提交任务将使用此配置。",
        "current": "运行时已是最新配置；新提交任务将使用此配置。",
        "unavailable": "当前没有活动运行时；下次新任务会读取此配置。",
        "failed": "配置已写入，但运行时重载失败；请在新任务前重启本地引擎。",
    }[runtime_reload_status]
    return SettingsSaveCommandResult(
        status=status,
        persistence=persistence,
        message=(
            f"已保存本地配置；{runtime_message}"
            if persistence == "persisted"
            else f"已提交，等待持久化。{runtime_message}"
        ),
        accepted_route_ids=accepted_route_ids,
        rejected_routes=rejected_routes,
        saved_at_label=f"{now:%H:%M:%S}",
        runtime_reload_status=runtime_reload_status,
    )
