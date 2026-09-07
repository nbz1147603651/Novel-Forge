"""Prompt template catalog and maintainability lint helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import OutputKind, get_task_format_contract
from novel_forge.prompts.registry import _PROMPTS_DIR, _TASK_TEMPLATE_MAP

_OUTPUT_HELPER_TOKENS = (
    "standard_json_output(",
    "standard_text_output(",
    "output_contract(",
    "causal_output_format(",
)
_LEGACY_FORMAT_SECTION_TOKENS = (
    "## 输出格式",
    "## 输出协议",
)
_RAW_JSON_PROTOCOL_TOKENS = (
    "仅返回 JSON",
    "只返回 JSON",
    "只输出 JSON",
    "只返回纯 JSON",
    "只输出纯JSON",
)
_TEXT_PROTOCOL_TOKENS = (
    "不要返回 JSON",
    "不要返回 JSON 格式",
    "直接返回",
    "正文结束即止",
)
_GUIDANCE_MARKERS = (
    "指导层",
    "@layer guide",
    "## 任务",
    "## 修复原则",
    "## 判定要求",
)
_FORMAT_MARKERS = (
    "统一格式契约（系统注入）",
)


@dataclass(frozen=True)
class PromptTemplateRecord:
    """Static catalog entry for one Jinja2 prompt template."""

    template: str
    path: Path
    category: str
    task_types: tuple[TaskType, ...]
    registered: bool
    partial: bool
    line_count: int
    output_kinds: tuple[OutputKind, ...]
    required_top_level_keys: tuple[str, ...]
    enforce_required_keys: bool
    purpose: str
    has_remark_layer: bool
    has_guidance_layer: bool
    has_format_layer: bool
    output_helper_count: int
    uses_raw_json_protocol: bool
    uses_text_protocol: bool
    format_boundary_sources: tuple[str, ...]
    missing_required_keys: tuple[str, ...]

    @property
    def task_names(self) -> tuple[str, ...]:
        """Return stable TaskType values for display."""
        return tuple(task_type.value for task_type in self.task_types)

    @property
    def expects_json(self) -> bool:
        """Return True if any mapped task expects JSON."""
        return OutputKind.JSON in self.output_kinds

    @property
    def expects_text(self) -> bool:
        """Return True if any mapped task expects pure text."""
        return OutputKind.TEXT in self.output_kinds

    @property
    def format_profile(self) -> str:
        """Return a compact output-format profile for audits."""
        if self.expects_text:
            return "text"
        if not self.expects_json:
            return "unregistered"
        if self.enforce_required_keys:
            return "strict-json"
        if self.required_top_level_keys:
            return "json-with-recommended-keys"
        return "freeform-json"


@dataclass(frozen=True)
class PromptLintIssue:
    """One prompt maintainability issue."""

    template: str
    code: str
    message: str
    severity: str = "error"


def _is_partial_template(rel_path: str) -> bool:
    path = Path(rel_path)
    return path.name.startswith("_") or any(part.startswith("_") for part in path.parts)


def _category_for_template(rel_path: str) -> str:
    if _is_partial_template(rel_path):
        return "shared"
    first = Path(rel_path).parts[0]
    if first.startswith("_"):
        return "shared"
    return first.removesuffix(".j2")


def _template_task_map() -> dict[str, tuple[TaskType, ...]]:
    mapping: dict[str, list[TaskType]] = {}
    for task_type, template in _TASK_TEMPLATE_MAP.items():
        mapping.setdefault(template, []).append(task_type)
    return {template: tuple(tasks) for template, tasks in mapping.items()}


def _unique_output_kinds(task_types: tuple[TaskType, ...]) -> tuple[OutputKind, ...]:
    kinds: list[OutputKind] = []
    seen: set[OutputKind] = set()
    for task_type in task_types:
        contract = get_task_format_contract(task_type)
        if contract is None or contract.output_kind in seen:
            continue
        seen.add(contract.output_kind)
        kinds.append(contract.output_kind)
    return tuple(kinds)


def _merge_required_keys(task_types: tuple[TaskType, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for task_type in task_types:
        contract = get_task_format_contract(task_type)
        if contract is None:
            continue
        for key in contract.required_top_level_keys:
            if key in seen:
                continue
            seen.add(key)
            merged.append(key)
    return tuple(merged)


def _has_strict_required_keys(task_types: tuple[TaskType, ...]) -> bool:
    for task_type in task_types:
        contract = get_task_format_contract(task_type)
        if contract and contract.enforce_required_keys:
            return True
    return False


def _extract_purpose(text: str) -> str:
    match = re.search(r"作用简述：(.+?)(?:\n上游输入：|\n下游输出：|\n消费链优先级：|\n={8,})", text, re.S)
    if not match:
        return ""
    return " ".join(part.strip() for part in match.group(1).splitlines() if part.strip())


def _format_boundary_sources(text: str) -> tuple[str, ...]:
    sources: list[str] = []
    if any(token in text for token in _OUTPUT_HELPER_TOKENS):
        sources.append("legacy-template-helper")
    if any(token in text for token in _LEGACY_FORMAT_SECTION_TOKENS):
        sources.append("legacy-format-section")
    return tuple(dict.fromkeys(sources))


def _load_imported_template_texts(text: str, *, prompts_dir: Path) -> tuple[str, ...]:
    imported_paths = set(
        re.findall(r'{%-?\s*(?:from|import)\s+"([^"]+)"(?:\s+import|\s+as)', text)
    )
    imported: list[str] = []
    for imported_path in sorted(imported_paths):
        path = prompts_dir / imported_path
        if path.exists():
            imported.append(path.read_text(encoding="utf-8"))
    return tuple(imported)


def _missing_required_keys(
    text: str,
    required_keys: tuple[str, ...],
    *,
    prompts_dir: Path,
) -> tuple[str, ...]:
    # Schema-first prompts do not duplicate the contract in Jinja templates.
    # Required keys are rendered by PromptBuilder from TaskFormatContract.
    if not required_keys:
        return ()
    return ()


def build_prompt_record(template_path: Path, *, prompts_dir: Path = _PROMPTS_DIR) -> PromptTemplateRecord:
    """Build a catalog entry from one template file."""
    rel_path = template_path.relative_to(prompts_dir).as_posix()
    text = template_path.read_text(encoding="utf-8")
    tasks = _template_task_map().get(rel_path, ())
    output_kinds = _unique_output_kinds(tasks)
    output_helper_count = sum(text.count(token) for token in _OUTPUT_HELPER_TOKENS)
    legacy_format_section_count = sum(text.count(token) for token in _LEGACY_FORMAT_SECTION_TOKENS)
    uses_raw_json_protocol = any(token in text for token in _RAW_JSON_PROTOCOL_TOKENS)
    uses_text_protocol = any(token in text for token in _TEXT_PROTOCOL_TOKENS)
    required_keys = _merge_required_keys(tasks)
    boundary_sources = _format_boundary_sources(text)
    if tasks and output_kinds and not boundary_sources:
        boundary_sources = ("builder-contract",)

    return PromptTemplateRecord(
        template=rel_path,
        path=template_path,
        category=_category_for_template(rel_path),
        task_types=tasks,
        registered=bool(tasks),
        partial=_is_partial_template(rel_path),
        line_count=text.count("\n") + 1,
        output_kinds=output_kinds,
        required_top_level_keys=required_keys,
        enforce_required_keys=_has_strict_required_keys(tasks),
        purpose=_extract_purpose(text),
        has_remark_layer="【备注】" in text or "@layer remark" in text or "prompt_id:" in text,
        has_guidance_layer=any(marker in text for marker in _GUIDANCE_MARKERS),
        has_format_layer=bool(tasks and output_kinds),
        output_helper_count=output_helper_count + legacy_format_section_count,
        uses_raw_json_protocol=uses_raw_json_protocol,
        uses_text_protocol=uses_text_protocol,
        format_boundary_sources=boundary_sources,
        missing_required_keys=_missing_required_keys(
            text,
            required_keys,
            prompts_dir=prompts_dir,
        ),
    )


def collect_prompt_templates(*, prompts_dir: Path = _PROMPTS_DIR) -> tuple[PromptTemplateRecord, ...]:
    """Collect all prompt templates into stable catalog records."""
    return tuple(
        build_prompt_record(path, prompts_dir=prompts_dir)
        for path in sorted(prompts_dir.rglob("*.j2"))
    )


def lint_prompt_record(record: PromptTemplateRecord) -> tuple[PromptLintIssue, ...]:
    """Return maintainability lint issues for one prompt template."""
    if not record.registered:
        return ()

    issues: list[PromptLintIssue] = []
    if not record.has_remark_layer:
        issues.append(
            PromptLintIssue(
                record.template,
                "missing-remark-layer",
                "registered task template must document purpose, inputs, outputs, and consumers",
            )
        )
    if not record.has_guidance_layer:
        issues.append(
            PromptLintIssue(
                record.template,
                "missing-guidance-layer",
                "registered task template must have an explicit guidance layer",
            )
        )
    if not record.has_format_layer:
        issues.append(
            PromptLintIssue(
                record.template,
                "missing-format-layer",
                "registered task template must have a TaskFormatContract for Builder injection",
            )
        )
    if record.output_helper_count:
        issues.append(
            PromptLintIssue(
                record.template,
                "legacy-output-protocol",
                "template must not contain output helpers or legacy output sections; Builder injects the contract",
            )
        )
    if record.uses_raw_json_protocol or record.uses_text_protocol:
        issues.append(
            PromptLintIssue(
                record.template,
                "template-local-output-protocol",
                "template must not contain local JSON/TEXT output protocol; Builder injects the contract",
            )
        )

    if record.missing_required_keys:
        missing = "、".join(record.missing_required_keys)
        issues.append(
            PromptLintIssue(
                record.template,
                "missing-contract-keys",
                f"format layer must mention required top-level keys: {missing}",
            )
        )

    return tuple(issues)


def lint_prompt_catalog(
    records: tuple[PromptTemplateRecord, ...] | None = None,
) -> tuple[PromptLintIssue, ...]:
    """Lint the full prompt catalog."""
    catalog = records if records is not None else collect_prompt_templates()
    issues: list[PromptLintIssue] = []
    for record in catalog:
        issues.extend(lint_prompt_record(record))
    return tuple(issues)
