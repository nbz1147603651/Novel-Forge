"""Guardrails for schema-first prompt output contracts."""

from __future__ import annotations

import re
from pathlib import Path

from novel_forge.core.format_contracts import ContractMode, OutputKind, resolve_task_format_contract
from novel_forge.prompts.registry import _TASK_TEMPLATE_MAP

_PROMPTS_ROOT = Path("novel_forge/prompts/prompts")


def _strip_jinja_comments(text: str) -> str:
    return re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)


def test_templates_do_not_use_legacy_output_protocols() -> None:
    offenders: list[str] = []
    markers = (
        "standard_json_output(",
        "standard_text_output(",
        "output_contract(",
        "causal_output_format(",
        "_output_formats.j2",
        "## 输出格式",
        "## 输出协议",
    )

    for path in _PROMPTS_ROOT.rglob("*.j2"):
        text = _strip_jinja_comments(path.read_text(encoding="utf-8"))
        if any(marker in text for marker in markers):
            offenders.append(str(path))

    assert not offenders, f"Legacy output protocol markers found in: {offenders}"


def test_templates_do_not_contain_illegal_numeric_range_json_examples() -> None:
    offenders: list[str] = []
    pattern = re.compile(
        r'"(?:length_target|max_edit_rounds|total_chapters|words_per_chapter|chapters_per_volume)"\s*:\s*\d+\s*-\s*\d+'
    )

    for path in _PROMPTS_ROOT.rglob("*.j2"):
        if pattern.search(_strip_jinja_comments(path.read_text(encoding="utf-8"))):
            offenders.append(str(path))

    assert not offenders, f"Illegal JSON numeric range examples found in: {offenders}"


def test_schema_first_templates_do_not_reintroduce_visible_raw_protocols() -> None:
    offenders: list[str] = []
    markers = (
        "严格 JSON",
        "只返回 JSON",
        "只返回纯 JSON",
        "只输出 JSON",
        "仅返回 JSON",
        "输出必须是纯 JSON",
        "输出必须为 JSON 对象",
        "必须输出一个 JSON 对象",
        "不要任何额外文本",
        "顶层只能输出",
        "顶层必填",
        "格式白名单",
        "JSON 自检",
        "可被 `json.loads`",
        "可被 json.loads",
    )

    for path in _PROMPTS_ROOT.rglob("*.j2"):
        text = _strip_jinja_comments(path.read_text(encoding="utf-8"))
        for marker in markers:
            if marker in text:
                offenders.append(f"{path}: {marker}")

    assert not offenders, f"Visible handwritten output protocols found: {offenders}"


def test_audit_templates_do_not_reintroduce_visible_json_examples() -> None:
    offenders: list[str] = []
    targets = (
        "checking/book_consistency.j2",
        "checking/book_consistency_verify.j2",
        "checking/book_editorial_audit.j2",
        "checking/check_editorial.j2",
    )

    for template_name in targets:
        text = _strip_jinja_comments((_PROMPTS_ROOT / template_name).read_text(encoding="utf-8"))
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("{") and not stripped.startswith(("{%", "{{", "{#")):
                offenders.append(f"{template_name}:{line_number}:{stripped[:80]}")

    assert not offenders, f"Visible JSON examples remain in audit templates: {offenders}"


def test_templates_do_not_reintroduce_visible_output_json_examples() -> None:
    offenders: list[str] = []
    allowed_input_heading_markers = (
        "输入",
        "当前大纲",
        "已完成蓝图片段",
    )

    for path in _PROMPTS_ROOT.rglob("*.j2"):
        text = _strip_jinja_comments(path.read_text(encoding="utf-8"))
        current_heading = ""
        in_fence = False
        fence_allowed = False
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("## "):
                current_heading = stripped
            if stripped.startswith("```"):
                if not in_fence:
                    in_fence = True
                    fence_allowed = any(
                        marker in current_heading for marker in allowed_input_heading_markers
                    )
                    if stripped.startswith("```json") and not fence_allowed:
                        offenders.append(f"{path}:{line_number}:{current_heading}")
                else:
                    in_fence = False
                    fence_allowed = False
                continue
            if in_fence:
                continue
            if stripped.startswith("{") and not stripped.startswith(("{%", "{{", "{#")):
                offenders.append(f"{path}:{line_number}:{stripped[:80]}")

    assert not offenders, f"Visible output JSON examples remain in templates: {offenders}"


def test_schema_first_templates_do_not_reintroduce_handwritten_protocols() -> None:
    checks = {
        "planning/plan_chapter.j2": (
            '"scene_intents":',
            "顶层必填",
        ),
        "checking/check_chapter.j2": (
            "`risk_level` 只能是",
            "`repair_actions` 必须",
        ),
        "kernel/extract_canon_delta.j2": (
            "必须输出这 7 个顶层字段",
            "缺项会导致下游解析退化",
        ),
    }

    offenders: list[str] = []
    for template_name, forbidden_markers in checks.items():
        text = (_PROMPTS_ROOT / template_name).read_text(encoding="utf-8")
        for marker in forbidden_markers:
            if marker in text:
                offenders.append(f"{template_name}: {marker}")

    assert not offenders, f"Handwritten output protocols found: {offenders}"


def test_all_registered_non_partial_json_tasks_are_strict_contracts() -> None:
    offenders: list[str] = []

    for task_type in _TASK_TEMPLATE_MAP:
        contract = resolve_task_format_contract(task_type, {"mode": "short"})
        if contract is None or contract.output_kind != OutputKind.JSON:
            continue
        if contract.effective_contract_mode == ContractMode.PARTIAL_OBJECT:
            continue
        if not contract.enforce_required_keys or not contract.required_top_level_keys:
            offenders.append(task_type.value)

    assert not offenders, f"Registered JSON tasks without strict required keys: {offenders}"
