"""Validation helpers for the narrative element library."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from .elements import (
    external_element_library_path,
    get_element_library,
    get_element_library_version,
    get_genre_heuristic_map,
    get_genre_preset_hint_map,
    get_genre_presets,
    get_library_by_id,
    get_quality_config_ids,
    get_required_core_ids,
)

IssueSeverity = Literal["error", "warning"]

_ALLOWED_UI_HINTS: frozenset[str] = frozenset(
    {
        "checklist",
        "timeline",
        "map",
        "card",
        "ledger",
        "curve",
        "ladder",
        "graph",
        "matrix",
        "config",
    }
)
_ALLOWED_TIERS: frozenset[str] = frozenset({"required", "extension"})
_REQUIRED_EXTERNAL_FIELDS: tuple[str, ...] = (
    "element_id",
    "name",
    "category",
    "tier",
    "description",
    "recommended_genres",
    "prompt_hint",
    "ui_hint",
)


@dataclass(frozen=True)
class ElementLibraryIssue:
    """One validation issue for library maintainers."""

    severity: IssueSeverity
    code: str
    message: str


@dataclass(frozen=True)
class ElementLibraryValidationReport:
    """Validation summary for the merged built-in/external element library."""

    version: str
    total_elements: int
    builtin_count: int
    external_count: int
    required_count: int
    extension_count: int
    errors: tuple[ElementLibraryIssue, ...]
    warnings: tuple[ElementLibraryIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "total_elements": self.total_elements,
            "builtin_count": self.builtin_count,
            "external_count": self.external_count,
            "required_count": self.required_count,
            "extension_count": self.extension_count,
            "ok": self.ok,
            "errors": [issue.__dict__ for issue in self.errors],
            "warnings": [issue.__dict__ for issue in self.warnings],
        }


def _issue(severity: IssueSeverity, code: str, message: str) -> ElementLibraryIssue:
    return ElementLibraryIssue(severity=severity, code=code, message=message)


def _load_external_payload(issues: list[ElementLibraryIssue]) -> dict[str, Any]:
    path = external_element_library_path()
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:  # pragma: no cover - defensive for local maintenance.
        issues.append(
            _issue(
                "error",
                "external_json_invalid",
                f"外部叙事要素库无法解析：{path} ({exc})",
            )
        )
        return {}
    if not isinstance(payload, dict):
        issues.append(
            _issue("error", "external_root_invalid", "外部叙事要素库根节点必须是 JSON object。")
        )
        return {}
    return payload


def _validate_external_payload(
    payload: dict[str, Any],
    issues: list[ElementLibraryIssue],
) -> None:
    if not payload:
        return
    version = str(payload.get("version") or "").strip()
    if version and version != get_element_library_version():
        issues.append(
            _issue(
                "warning",
                "external_version_mismatch",
                f"外部库版本 {version} 与内置库版本 {get_element_library_version()} 不一致。",
            )
        )
    elements = payload.get("elements")
    if not isinstance(elements, list):
        issues.append(_issue("error", "external_elements_invalid", "外部库 elements 必须是数组。"))
        return

    seen: Counter[str] = Counter()
    for index, item in enumerate(elements):
        prefix = f"外部库 elements[{index}]"
        if not isinstance(item, dict):
            issues.append(_issue("error", "external_item_invalid", f"{prefix} 必须是 object。"))
            continue
        element_id = str(item.get("element_id") or "").strip()
        if element_id:
            seen[element_id] += 1
        for field_name in _REQUIRED_EXTERNAL_FIELDS:
            value = item.get(field_name)
            if value in ("", None, []):
                issues.append(
                    _issue(
                        "error",
                        "external_field_missing",
                        f"{prefix} 缺少必填字段 {field_name}。",
                    )
                )
        if element_id and not element_id.startswith("custom_"):
            issues.append(
                _issue(
                    "warning",
                    "external_id_not_custom",
                    f"外部要素 {element_id} 建议使用 custom_ 前缀，避免误覆盖内置库。",
                )
            )
        tier = str(item.get("tier") or "").strip()
        if tier and tier not in _ALLOWED_TIERS:
            issues.append(_issue("error", "external_tier_invalid", f"{prefix} tier 非法：{tier}。"))
        ui_hint = str(item.get("ui_hint") or "").strip()
        if ui_hint and ui_hint not in _ALLOWED_UI_HINTS:
            issues.append(
                _issue("error", "external_ui_hint_invalid", f"{prefix} ui_hint 非法：{ui_hint}。")
            )
        genres = item.get("recommended_genres")
        if genres is not None and (
            not isinstance(genres, list) or not all(str(genre).strip() for genre in genres)
        ):
            issues.append(
                _issue(
                    "error",
                    "external_recommended_genres_invalid",
                    f"{prefix} recommended_genres 必须是非空字符串数组。",
                )
            )

    for element_id, count in seen.items():
        if count > 1:
            issues.append(
                _issue("error", "external_duplicate_id", f"外部库要素 ID 重复：{element_id}。")
            )


def validate_element_library() -> ElementLibraryValidationReport:
    """Validate the merged narrative element library and all genre references."""

    issues: list[ElementLibraryIssue] = []
    external_payload = _load_external_payload(issues)
    _validate_external_payload(external_payload, issues)

    library = get_element_library()
    library_by_id = get_library_by_id()
    ids = [element.element_id for element in library]
    required_ids = set(get_required_core_ids())
    quality_config_ids = set(get_quality_config_ids())

    for element_id, count in Counter(ids).items():
        if count > 1:
            issues.append(_issue("error", "duplicate_id", f"合并后要素 ID 重复：{element_id}。"))

    for element in library:
        if not element.element_id:
            issues.append(_issue("error", "element_id_missing", "存在空 element_id 的叙事要素。"))
            continue
        label = f"{element.element_id}({element.name})"
        if element.tier not in _ALLOWED_TIERS:
            issues.append(_issue("error", "tier_invalid", f"{label} tier 非法：{element.tier}。"))
        if not element.name.strip():
            issues.append(_issue("error", "name_missing", f"{label} 缺少 name。"))
        if not element.category.strip():
            issues.append(_issue("error", "category_missing", f"{label} 缺少 category。"))
        if not element.description.strip():
            issues.append(_issue("error", "description_missing", f"{label} 缺少 description。"))
        if not element.prompt_hint.strip():
            issues.append(_issue("error", "prompt_hint_missing", f"{label} 缺少 prompt_hint。"))
        if not element.recommended_genres:
            issues.append(
                _issue("warning", "recommended_genres_empty", f"{label} 没有 recommended_genres。")
            )
        if element.ui_hint not in _ALLOWED_UI_HINTS:
            issues.append(
                _issue("error", "ui_hint_invalid", f"{label} ui_hint 非法：{element.ui_hint}。")
            )

    for element_id in required_ids:
        required_element = library_by_id.get(element_id)
        if required_element is None:
            issues.append(
                _issue("error", "required_missing", f"required core 不存在：{element_id}。")
            )
        elif required_element.tier != "required":
            issues.append(
                _issue(
                    "error",
                    "required_tier_invalid",
                    f"required core 必须是 required：{element_id}。",
                )
            )

    for element_id in quality_config_ids:
        quality_element = library_by_id.get(element_id)
        if quality_element is None:
            issues.append(
                _issue("error", "quality_config_missing", f"质量配置要素不存在：{element_id}。")
            )
            continue
        if quality_element.ui_hint != "config":
            issues.append(
                _issue(
                    "error",
                    "quality_config_ui_hint",
                    f"质量配置要素必须使用 config UI：{element_id}。",
                )
            )
        if quality_element.tier != "extension":
            issues.append(
                _issue(
                    "error", "quality_config_tier", f"质量配置要素必须是 extension：{element_id}。"
                )
            )

    for keywords, element_ids, weight in get_genre_heuristic_map():
        if not keywords:
            issues.append(_issue("error", "heuristic_keywords_empty", "题材启发式存在空关键词组。"))
        if weight <= 0:
            issues.append(
                _issue("error", "heuristic_weight_invalid", f"题材启发式权重必须为正数：{weight}。")
            )
        for element_id in element_ids:
            heuristic_element = library_by_id.get(element_id)
            if heuristic_element is None:
                issues.append(
                    _issue(
                        "error", "heuristic_ref_missing", f"题材启发式引用不存在：{element_id}。"
                    )
                )
            elif heuristic_element.tier != "extension":
                issues.append(
                    _issue(
                        "error",
                        "heuristic_ref_tier",
                        f"题材启发式只能引用 extension：{element_id}。",
                    )
                )

    preset_ids: set[str] = set()
    for preset in get_genre_presets():
        if not preset.preset_id.strip():
            issues.append(_issue("error", "preset_id_missing", "存在空 preset_id 的预置。"))
            continue
        if preset.preset_id in preset_ids:
            issues.append(
                _issue("error", "preset_duplicate_id", f"预置 ID 重复：{preset.preset_id}。")
            )
        preset_ids.add(preset.preset_id)
        if not preset.label.strip():
            issues.append(
                _issue("error", "preset_label_missing", f"{preset.preset_id} 缺少 label。")
            )
        if not preset.recommended_genres:
            issues.append(
                _issue(
                    "warning",
                    "preset_genres_empty",
                    f"{preset.preset_id} 没有 recommended_genres。",
                )
            )
        for element_id in (*preset.default_enabled, *preset.default_weights.keys()):
            preset_element = library_by_id.get(element_id)
            if preset_element is None:
                issues.append(
                    _issue(
                        "error",
                        "preset_ref_missing",
                        f"预置 {preset.preset_id} 引用不存在：{element_id}。",
                    )
                )
            elif preset_element.tier != "extension":
                issues.append(
                    _issue(
                        "error",
                        "preset_ref_tier",
                        f"预置 {preset.preset_id} 只能引用 extension：{element_id}。",
                    )
                )

    for keywords, preset_id in get_genre_preset_hint_map():
        if not keywords:
            issues.append(_issue("error", "preset_hint_keywords_empty", "预置推荐存在空关键词组。"))
        if preset_id not in preset_ids:
            issues.append(
                _issue("error", "preset_hint_ref_missing", f"预置推荐引用不存在：{preset_id}。")
            )

    errors = tuple(issue for issue in issues if issue.severity == "error")
    warnings = tuple(issue for issue in issues if issue.severity == "warning")
    builtin_count = sum(1 for element in library if element.source == "builtin")
    external_count = sum(1 for element in library if element.source == "external")
    required_count = sum(1 for element in library if element.tier == "required")
    extension_count = sum(1 for element in library if element.tier == "extension")
    return ElementLibraryValidationReport(
        version=get_element_library_version(),
        total_elements=len(library),
        builtin_count=builtin_count,
        external_count=external_count,
        required_count=required_count,
        extension_count=extension_count,
        errors=errors,
        warnings=warnings,
    )


__all__ = [
    "ElementLibraryIssue",
    "ElementLibraryValidationReport",
    "validate_element_library",
]
