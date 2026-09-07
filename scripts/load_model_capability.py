#!/usr/bin/env python3
"""加载并查询 model_capability_profile.json 中的模型能力档案。

用法:
    python3 scripts/load_model_capability.py minimax/MiniMax-M3
    python3 scripts/load_model_capability.py --model tongyi/qwen-turbo
    python3 scripts/load_model_capability.py --all
    python3 scripts/load_model_capability.py --check-avoid REPAIR_INIT_ARTIFACT_PATCH

输出示例:
    instruction_following=weak
    structured_output_reliability=low
    context_window_tokens=128000
    supports_function_calling=False
    preferred_for=[]
    avoid_for=[REPAIR_INIT_ARTIFACT_PATCH, ...]
    notes=Self-contradicting JSON outputs...
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PROFILE_PATH = _REPO_ROOT / "config" / "model_capability_profile.json"

_FIELD_LABELS: dict[str, str] = {
    "instruction_following": "指令遵循度",
    "structured_output_reliability": "结构化输出可靠性",
    "structured_output": "结构化输出能力声明",
    "context_window_tokens": "上下文窗口 (tokens)",
    "supports_thinking": "支持思考/推理",
    "thinking_control": "思考控制类型",
    "thinking_modes": "官方思考档位",
    "default_thinking_mode": "默认思考档位",
    "supports_function_calling": "支持 function calling",
    "preferred_for": "适合的任务",
    "avoid_for": "应避免的任务",
    "notes": "备注",
}
_STRUCTURED_OUTPUT_KEYS: tuple[str, ...] = (
    "json_schema",
    "json_mode",
    "tool_calling",
    "strict_schema",
    "constrained_decoding",
)
_STRUCTURED_OUTPUT_VALUES = {"supported", "unsupported", "unknown"}
_RATING_VALUES = {"excellent", "good", "medium", "weak", "high", "low", "unknown"}
_AVAILABILITY_VALUES = {"active", "legacy", "retired", "runtime_discovered", "unknown"}
_THINKING_CONTROL_VALUES = {"unsupported", "toggle", "adaptive", "effort", "budget", "forced"}
_THINKING_MODE_VALUES = {
    "off",
    "on",
    "adaptive",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "forced",
}


@dataclass
class ModelCapability:
    """单个模型的能力档案。"""

    model_id: str
    instruction_following: str
    structured_output_reliability: str
    context_window_tokens: int
    supports_thinking: bool
    thinking_control: str
    thinking_modes: list[str]
    default_thinking_mode: str
    supports_function_calling: bool
    structured_output: dict[str, str] = field(default_factory=dict)
    preferred_for: list[str] = field(default_factory=list)
    avoid_for: list[str] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_dict(cls, model_id: str, data: dict[str, Any]) -> ModelCapability:
        return cls(
            model_id=model_id,
            instruction_following=data.get("instruction_following", "unknown"),
            structured_output_reliability=data.get("structured_output_reliability", "unknown"),
            structured_output=dict(data.get("structured_output") or {}),
            context_window_tokens=data.get("context_window_tokens", 0),
            supports_thinking=data.get("supports_thinking", False),
            thinking_control=str(data.get("thinking_control") or "unsupported"),
            thinking_modes=list(data.get("thinking_modes") or []),
            default_thinking_mode=str(data.get("default_thinking_mode") or "off"),
            supports_function_calling=data.get("supports_function_calling", False),
            preferred_for=data.get("preferred_for", []),
            avoid_for=data.get("avoid_for", []),
            notes=data.get("notes", ""),
        )

    def format_key_value(self) -> str:
        """格式化为 key=value 行。"""
        lines: list[str] = []
        for field_name in [
            "instruction_following",
            "structured_output_reliability",
            "structured_output",
            "context_window_tokens",
            "supports_thinking",
            "thinking_control",
            "thinking_modes",
            "default_thinking_mode",
            "supports_function_calling",
        ]:
            val = getattr(self, field_name)
            lines.append(f"{field_name}={val}")
        lines.append(f"preferred_for={self.preferred_for}")
        lines.append(f"avoid_for={self.avoid_for}")
        if self.notes:
            lines.append(f"notes={self.notes}")
        return "\n".join(lines)

    def format_table_row(self) -> str:
        """单行汇总。"""
        avoid = ",".join(self.avoid_for[:3])
        if len(self.avoid_for) > 3:
            avoid += f",+{len(self.avoid_for) - 3}"
        pref = ",".join(self.preferred_for[:2])
        if len(self.preferred_for) > 2:
            pref += f",+{len(self.preferred_for) - 2}"
        return (
            f"{self.model_id:<42} "
            f"inst={self.instruction_following:<10} "
            f"json={self.structured_output_reliability:<10} "
            f"mode={self._structured_output_summary():<22} "
            f"ctx={self.context_window_tokens:>8,} "
            f"fc={str(self.supports_function_calling):<6} "
            f"pref=[{pref}] "
            f"avoid=[{avoid}]"
        )

    def _structured_output_summary(self) -> str:
        caps = self.structured_output or {}
        ordered = [
            ("schema", caps.get("json_schema", "unknown")),
            ("json", caps.get("json_mode", "unknown")),
            ("strict", caps.get("strict_schema", "unknown")),
        ]
        return ",".join(f"{key}:{value}" for key, value in ordered)


def load_profiles(profile_path: Path | None = None) -> dict[str, Any]:
    """加载 model_capability_profile.json。"""
    path = profile_path or _PROFILE_PATH
    if not path.is_file():
        print(f"ERROR: {path} not found", file=sys.stderr)
        sys.exit(1)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON in {path}: {exc}", file=sys.stderr)
        sys.exit(1)
    return data if isinstance(data, dict) else {}


def resolve_model(profiles: dict[str, Any], model_name: str) -> ModelCapability | None:
    """按名称查找模型档案（支持模糊匹配）。"""
    raw_models = profiles.get("models", {})
    models: dict[str, dict[str, Any]] = (
        {str(key): value for key, value in raw_models.items()}
        if isinstance(raw_models, dict)
        else {}
    )
    # 精确匹配
    if model_name in models:
        return ModelCapability.from_dict(model_name, models[model_name])
    # 模糊匹配（忽略大小写）
    model_lower = model_name.lower()
    for key, data in models.items():
        if key.lower() == model_lower:
            return ModelCapability.from_dict(key, data)
    # 部分匹配
    candidates = [k for k in models if model_lower in k.lower()]
    if len(candidates) == 1:
        return ModelCapability.from_dict(candidates[0], models[candidates[0]])
    if len(candidates) > 1:
        print(f"多个匹配: {candidates}", file=sys.stderr)
    return None


def list_all(profiles: dict[str, Any]) -> list[ModelCapability]:
    """列出所有模型档案。"""
    raw_models = profiles.get("models", {})
    models: dict[str, dict[str, Any]] = (
        {str(key): value for key, value in raw_models.items()}
        if isinstance(raw_models, dict)
        else {}
    )
    return [ModelCapability.from_dict(k, v) for k, v in models.items()]


def check_avoid(profiles: dict[str, Any], task_type: str) -> list[tuple[str, ModelCapability]]:
    """检查哪些模型的 avoid_for 包含指定 task_type。"""
    caps = list_all(profiles)
    result: list[tuple[str, ModelCapability]] = []
    task_lower = task_type.lower()
    for cap in caps:
        for avoid in cap.avoid_for:
            if avoid.lower() == task_lower:
                result.append((cap.model_id, cap))
                break
    return result


def validate_profiles(profiles: dict[str, Any]) -> list[str]:
    """Return capability profile validation errors."""

    errors: list[str] = []
    version = str(profiles.get("version") or "")
    if version != "1.2.0":
        errors.append(f"version must be 1.2.0, got {version or '<missing>'}")
    models = profiles.get("models")
    if not isinstance(models, dict) or not models:
        return [*errors, "models must be a non-empty object"]

    families = profiles.get("model_families")
    if not isinstance(families, list) or not families:
        errors.append("model_families must be a non-empty array")
        families = []

    def validate_common(entry_id: str, raw_entry: dict[str, Any], *, exact: bool) -> None:
        availability = str(raw_entry.get("availability") or "unknown")
        if availability not in _AVAILABILITY_VALUES:
            errors.append(f"{entry_id}: availability has invalid value {availability!r}")
        for field_name in ("context_window_tokens", "max_output_tokens"):
            value = raw_entry.get(field_name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value <= 0
            ):
                errors.append(f"{entry_id}: {field_name} must be a positive integer")
        for field_name in (
            "supports_thinking",
            "supports_multi_turn",
            "supports_function_calling",
        ):
            value = raw_entry.get(field_name)
            if value is not None and not isinstance(value, bool):
                errors.append(f"{entry_id}: {field_name} must be a boolean when declared")

        thinking_control = str(raw_entry.get("thinking_control") or "")
        raw_thinking_modes = raw_entry.get("thinking_modes")
        default_thinking_mode = str(raw_entry.get("default_thinking_mode") or "")
        if thinking_control and thinking_control not in _THINKING_CONTROL_VALUES:
            errors.append(
                f"{entry_id}: thinking_control has invalid value {thinking_control!r}"
            )
        if raw_thinking_modes is not None:
            if not isinstance(raw_thinking_modes, list) or not raw_thinking_modes:
                errors.append(f"{entry_id}: thinking_modes must be a non-empty array")
            else:
                thinking_modes = [str(mode) for mode in raw_thinking_modes]
                invalid_modes = sorted(set(thinking_modes) - _THINKING_MODE_VALUES)
                if invalid_modes:
                    errors.append(
                        f"{entry_id}: thinking_modes has invalid values {invalid_modes}"
                    )
                if len(thinking_modes) != len(set(thinking_modes)):
                    errors.append(f"{entry_id}: thinking_modes must not contain duplicates")
                if default_thinking_mode and default_thinking_mode not in thinking_modes:
                    errors.append(
                        f"{entry_id}: default_thinking_mode must occur in thinking_modes"
                    )
        if thinking_control and raw_entry.get("supports_thinking") is not True:
            errors.append(
                f"{entry_id}: thinking_control requires supports_thinking=true"
            )
        if thinking_control and raw_thinking_modes is None:
            errors.append(f"{entry_id}: thinking_control requires thinking_modes")
        if raw_thinking_modes is not None and not default_thinking_mode:
            errors.append(f"{entry_id}: thinking_modes requires default_thinking_mode")
        if exact and not isinstance(raw_entry.get("context_window_tokens"), int):
            errors.append(f"{entry_id}: context_window_tokens must be an integer")
        if exact and not isinstance(raw_entry.get("max_output_tokens"), int):
            errors.append(f"{entry_id}: max_output_tokens must be an integer")

        verified_at = str(raw_entry.get("verified_at") or "")
        if len(verified_at) != 10 or verified_at[4:5] != "-" or verified_at[7:8] != "-":
            errors.append(f"{entry_id}: verified_at must use YYYY-MM-DD")
        sources = raw_entry.get("source_urls")
        if not isinstance(sources, list) or not sources:
            errors.append(f"{entry_id}: source_urls must be a non-empty array")
        elif any(not str(url).startswith("https://") for url in sources):
            errors.append(f"{entry_id}: every source URL must use https://")

        structured_output = raw_entry.get("structured_output")
        if not isinstance(structured_output, dict):
            errors.append(f"{entry_id}: structured_output must be an object")
            return
        for key in _STRUCTURED_OUTPUT_KEYS:
            structured_value = structured_output.get(key)
            if structured_value not in _STRUCTURED_OUTPUT_VALUES:
                errors.append(
                    f"{entry_id}: structured_output.{key} must be one of "
                    f"{sorted(_STRUCTURED_OUTPUT_VALUES)}, got {structured_value!r}"
                )
        extra_keys = sorted(set(structured_output) - {*_STRUCTURED_OUTPUT_KEYS, "notes"})
        if extra_keys:
            errors.append(f"{entry_id}: structured_output has unknown keys {extra_keys}")

    family_ids: set[str] = set()
    for index, raw_entry in enumerate(families):
        entry_id = f"model_families[{index}]"
        if not isinstance(raw_entry, dict):
            errors.append(f"{entry_id}: family entry must be an object")
            continue
        rule_id = str(raw_entry.get("id") or "")
        if not rule_id:
            errors.append(f"{entry_id}: id is required")
        elif rule_id in family_ids:
            errors.append(f"{entry_id}: duplicate id {rule_id!r}")
        family_ids.add(rule_id)
        if not str(raw_entry.get("provider") or "").strip():
            errors.append(f"{entry_id}: provider is required")
        if not str(raw_entry.get("model_pattern") or "").strip():
            errors.append(f"{entry_id}: model_pattern is required")
        validate_common(rule_id or entry_id, raw_entry, exact=False)

    for model_id, raw_entry in sorted(models.items()):
        if not isinstance(raw_entry, dict):
            errors.append(f"{model_id}: model entry must be an object")
            continue
        for field_name in ("instruction_following", "structured_output_reliability"):
            value = str(raw_entry.get(field_name) or "unknown")
            if value not in _RATING_VALUES:
                errors.append(f"{model_id}: {field_name} has invalid value {value!r}")
        validate_common(str(model_id), raw_entry, exact=True)
    return errors


def _format_rating(rating: str) -> str:
    """给评级加颜色标记。"""
    colors = {
        "excellent": "✅",
        "good": "🟢",
        "medium": "🟡",
        "weak": "🟠",
        "low": "🔴",
    }
    marker = colors.get(rating, "")
    return f"{marker}{rating}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="查询 model_capability_profile.json 中的模型能力档案",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "model",
        nargs="?",
        default=None,
        help="模型名称 (如 minimax/MiniMax-M3)",
    )
    parser.add_argument(
        "--model",
        "-m",
        dest="model_name",
        default=None,
        help="模型名称 (命名参数形式)",
    )
    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="列出所有模型档案",
    )
    parser.add_argument(
        "--check-avoid",
        type=str,
        default=None,
        metavar="TASK_TYPE",
        help="检查哪些模型应避免用于指定任务类型",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="校验 capability profile schema 和 structured_output 声明完整性",
    )
    parser.add_argument(
        "--profile-path",
        type=Path,
        default=_PROFILE_PATH,
        help=f"capability profile JSON 路径 (默认: {_PROFILE_PATH})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    profiles = load_profiles(args.profile_path)
    model_name = args.model or args.model_name

    if args.verify:
        errors = validate_profiles(profiles)
        if errors:
            print("模型能力档案校验失败:")
            for error in errors:
                print(f"  - {error}")
            return 1
        print("RESULT: PASS — model capability profile is valid")
        return 0

    # --check-avoid 模式
    if args.check_avoid:
        results = check_avoid(profiles, args.check_avoid)
        if not results:
            print(f"任务类型 '{args.check_avoid}' 不在任何模型的 avoid_for 中")
            return 0
        print(f"应避免使用以下模型执行 {args.check_avoid}:")
        for model_id, _cap in results:
            print(f"  - {model_id} (avoid_for 包含 {args.check_avoid})")
        return 1

    # --all 模式
    if args.all:
        caps = list_all(profiles)
        print(f"# 模型能力档案 v{profiles.get('version', '?')}")
        print(f"# 共 {len(caps)} 个模型\n")
        print(
            f"{'Model':<42} "
            f"{'Inst':<10} "
            f"{'JSON':<10} "
            f"{'Mode':<22} "
            f"{'Context':>9} "
            f"{'FC':<6} "
            f"{'Preferred':<35} "
            f"{'Avoid'}"
        )
        print("-" * 150)
        for cap in caps:
            print(cap.format_table_row())
        return 0

    # 单模型模式
    if model_name:
        selected_cap = resolve_model(profiles, model_name)
        if selected_cap is None:
            print(f"ERROR: 未找到模型 '{model_name}'", file=sys.stderr)
            print(f"可用的模型: {list(profiles.get('models', {}).keys())}", file=sys.stderr)
            return 1
        print(f"# {selected_cap.model_id}")
        print(f"# 版本: {profiles.get('version', '?')}  更新: {profiles.get('last_updated', '?')}")
        print()
        for field_name in [
            "instruction_following",
            "structured_output_reliability",
            "structured_output",
            "context_window_tokens",
            "supports_thinking",
            "thinking_control",
            "thinking_modes",
            "default_thinking_mode",
            "supports_function_calling",
            "preferred_for",
            "avoid_for",
            "notes",
        ]:
            val = getattr(selected_cap, field_name)
            label = _FIELD_LABELS.get(field_name, field_name)
            if isinstance(val, list):
                val = ", ".join(val) if val else "(无)"
            elif isinstance(val, dict):
                val = json.dumps(val, ensure_ascii=False, sort_keys=True)
            elif isinstance(val, bool):
                val = "是" if val else "否"
            elif isinstance(val, str) and field_name in (
                "instruction_following",
                "structured_output_reliability",
            ):
                val = _format_rating(val)
            print(f"  {label}: {val}")
        return 0

    # 无参数：显示帮助
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
