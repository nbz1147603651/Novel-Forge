"""Auditable model-capability registry shared by gateway policy and UI helpers.

The JSON registry deliberately distinguishes verified provider capabilities from
runtime-safe limits.  Exact model entries override family rules; unknown models
remain unknown so callers can apply conservative defaults instead of guessing.
"""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROFILE_PATH = _REPO_ROOT / "config" / "model_capability_profile.json"


@dataclass(frozen=True)
class ModelCapabilityRecord:
    """Resolved, provenance-aware capability record for one provider/model pair."""

    provider: str
    model_id: str
    profile_match: str = ""
    availability: str = "unknown"
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    supports_thinking: bool | None = None
    thinking_control: str = ""
    thinking_modes: tuple[str, ...] = ()
    default_thinking_mode: str = ""
    supports_multi_turn: bool | None = None
    supports_function_calling: bool | None = None
    structured_output: Mapping[str, Any] = field(default_factory=dict)
    verified_at: str = ""
    source_urls: tuple[str, ...] = ()
    notes: str = ""

    @property
    def is_available(self) -> bool:
        return self.availability in {"active", "legacy", "runtime_discovered"}


def capability_profile_path() -> Path:
    """Return the repository capability-registry path."""

    return _DEFAULT_PROFILE_PATH


@lru_cache(maxsize=4)
def load_model_capability_registry(path: str | None = None) -> dict[str, Any]:
    """Load the capability registry, returning an empty object on read failure."""

    profile_path = Path(path) if path else _DEFAULT_PROFILE_PATH
    try:
        data = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_provider(provider: str) -> str:
    value = str(provider or "").strip().lower()
    return {
        "aliyun": "tongyi",
        "dashscope": "tongyi",
        "alibaba": "tongyi",
        "moonshot": "kimi",
        "moonshotai": "kimi",
        "xiaomi": "mimo",
        "xiaomi_mimo": "mimo",
        "xiaomimimo": "mimo",
        "hunyuan": "tencent",
        "tencent_hunyuan": "tencent",
        "ark": "volcengine_ark",
        "volcengine": "volcengine_ark",
        "volcengineark": "volcengine_ark",
        "doubao": "volcengine_ark",
    }.get(value, value)


def _casefold_mapping(value: Mapping[str, Any]) -> dict[str, str]:
    return {str(key).casefold(): str(key) for key in value}


def _matching_family_entries(
    registry: Mapping[str, Any],
    *,
    provider: str,
    model_id: str,
) -> list[tuple[int, int, str, dict[str, Any]]]:
    raw_rules = registry.get("model_families")
    if not isinstance(raw_rules, list):
        return []
    matches: list[tuple[int, int, str, dict[str, Any]]] = []
    provider_key = provider.casefold()
    model_key = model_id.casefold()
    for index, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, dict):
            continue
        rule_provider = str(raw_rule.get("provider") or "*").strip().casefold()
        pattern = str(raw_rule.get("model_pattern") or "").strip()
        if not pattern or rule_provider not in {"*", provider_key}:
            continue
        if not fnmatch.fnmatchcase(model_key, pattern.casefold()):
            continue
        rule_id = str(raw_rule.get("id") or f"family:{index}")
        specificity = len(pattern.replace("*", "").replace("?", ""))
        matches.append((specificity, index, rule_id, raw_rule))
    matches.sort(key=lambda item: (item[0], item[1]))
    return matches


def _merge_entry(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in {"id", "provider", "model_pattern"}:
            continue
        if key == "structured_output" and isinstance(value, Mapping):
            current = merged.get(key)
            nested = dict(current) if isinstance(current, Mapping) else {}
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def _record_from_entry(
    provider: str,
    model_id: str,
    profile_match: str,
    entry: Mapping[str, Any],
) -> ModelCapabilityRecord:
    def optional_int(key: str) -> int | None:
        value = entry.get(key)
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
        )

    def optional_bool(key: str) -> bool | None:
        value = entry.get(key)
        return value if isinstance(value, bool) else None

    raw_sources = entry.get("source_urls")
    sources = (
        tuple(str(item) for item in raw_sources if str(item).strip())
        if isinstance(raw_sources, list)
        else ()
    )
    structured_output = entry.get("structured_output")
    raw_thinking_modes = entry.get("thinking_modes")
    thinking_modes = (
        tuple(str(item).strip().lower() for item in raw_thinking_modes if str(item).strip())
        if isinstance(raw_thinking_modes, list)
        else ()
    )
    return ModelCapabilityRecord(
        provider=provider,
        model_id=model_id,
        profile_match=profile_match,
        availability=str(entry.get("availability") or "unknown"),
        context_window_tokens=optional_int("context_window_tokens"),
        max_output_tokens=optional_int("max_output_tokens"),
        supports_thinking=optional_bool("supports_thinking"),
        thinking_control=str(entry.get("thinking_control") or ""),
        thinking_modes=thinking_modes,
        default_thinking_mode=str(entry.get("default_thinking_mode") or ""),
        supports_multi_turn=optional_bool("supports_multi_turn"),
        supports_function_calling=optional_bool("supports_function_calling"),
        structured_output=(
            dict(structured_output) if isinstance(structured_output, Mapping) else {}
        ),
        verified_at=str(entry.get("verified_at") or ""),
        source_urls=sources,
        notes=str(entry.get("notes") or ""),
    )


def resolve_model_capability(
    provider: str,
    model_id: str | None,
    *,
    registry: Mapping[str, Any] | None = None,
) -> ModelCapabilityRecord:
    """Resolve exact and family capability declarations for one model.

    Family rules are merged from least to most specific.  An exact
    ``provider/model`` or model-only entry is then applied as the final override.
    """

    provider_key = _normalize_provider(provider)
    model_key = str(model_id or "").strip()
    data = registry if registry is not None else load_model_capability_registry()
    if not isinstance(data, Mapping) or not model_key:
        return ModelCapabilityRecord(provider=provider_key, model_id=model_key)

    merged: dict[str, Any] = {}
    match_ids: list[str] = []
    for _specificity, _index, rule_id, rule in _matching_family_entries(
        data,
        provider=provider_key,
        model_id=model_key,
    ):
        merged = _merge_entry(merged, rule)
        match_ids.append(rule_id)

    raw_models = data.get("models")
    if isinstance(raw_models, Mapping):
        lower_to_key = _casefold_mapping(raw_models)
        candidates = (f"{provider_key}/{model_key}", model_key)
        for candidate in candidates:
            exact_key = lower_to_key.get(candidate.casefold())
            if not exact_key:
                continue
            raw_entry = raw_models.get(exact_key)
            if isinstance(raw_entry, Mapping):
                merged = _merge_entry(merged, raw_entry)
                match_ids.append(exact_key)
            break

    if not merged:
        return ModelCapabilityRecord(provider=provider_key, model_id=model_key)
    return _record_from_entry(
        provider_key,
        model_key,
        "+".join(match_ids),
        merged,
    )


def resolve_model_capability_any_provider(
    model_id: str,
    *,
    registry: Mapping[str, Any] | None = None,
) -> ModelCapabilityRecord:
    """Resolve a model when the legacy caller has no provider information.

    If multiple providers match, choose conservative numeric limits and boolean
    capabilities.  This prevents an OpenAI-compatible host from inheriting a
    more permissive native-provider limit by accident.
    """

    data = registry if registry is not None else load_model_capability_registry()
    providers: set[str] = set()
    if isinstance(data, Mapping):
        for raw_rule in data.get("model_families") or []:
            if isinstance(raw_rule, Mapping):
                provider = str(raw_rule.get("provider") or "").strip()
                if provider and provider != "*":
                    providers.add(_normalize_provider(provider))
        raw_models = data.get("models")
        if isinstance(raw_models, Mapping):
            for key in raw_models:
                text = str(key)
                if "/" in text:
                    providers.add(_normalize_provider(text.split("/", 1)[0]))

    matches = [
        resolve_model_capability(provider, model_id, registry=data)
        for provider in sorted(providers)
    ]
    matches = [record for record in matches if record.profile_match]
    if not matches:
        return ModelCapabilityRecord(provider="", model_id=model_id)
    if len(matches) == 1:
        return matches[0]

    contexts = [record.context_window_tokens for record in matches if record.context_window_tokens]
    outputs = [record.max_output_tokens for record in matches if record.max_output_tokens]
    return ModelCapabilityRecord(
        provider="*",
        model_id=model_id,
        profile_match="|".join(record.profile_match for record in matches),
        availability=("active" if any(record.is_available for record in matches) else "unknown"),
        context_window_tokens=min(contexts) if contexts else None,
        max_output_tokens=min(outputs) if outputs else None,
        supports_thinking=(
            all(record.supports_thinking is True for record in matches)
            if any(record.supports_thinking is not None for record in matches)
            else None
        ),
        thinking_control=(
            matches[0].thinking_control
            if all(record.thinking_control == matches[0].thinking_control for record in matches)
            else ""
        ),
        thinking_modes=(
            matches[0].thinking_modes
            if all(record.thinking_modes == matches[0].thinking_modes for record in matches)
            else ()
        ),
        default_thinking_mode=(
            matches[0].default_thinking_mode
            if all(
                record.default_thinking_mode == matches[0].default_thinking_mode
                for record in matches
            )
            else ""
        ),
        supports_multi_turn=(
            all(record.supports_multi_turn is True for record in matches)
            if any(record.supports_multi_turn is not None for record in matches)
            else None
        ),
        supports_function_calling=(
            all(record.supports_function_calling is True for record in matches)
            if any(record.supports_function_calling is not None for record in matches)
            else None
        ),
        notes="Conservative aggregate across provider-specific capability records.",
    )


__all__ = [
    "ModelCapabilityRecord",
    "capability_profile_path",
    "load_model_capability_registry",
    "resolve_model_capability",
    "resolve_model_capability_any_provider",
]
