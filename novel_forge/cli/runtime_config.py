"""Runtime CLI config loading and option resolution helpers."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

from novel_forge.core.domain.story_defaults import (
    DEFAULT_GENRE,
    DEFAULT_LONG_PREMISE,
    DEFAULT_SHORT_THEME,
    DEFAULT_TONE,
)
from novel_forge.core.exceptions import RuntimeConfigError as RuntimeConfigError


def get_unified_defaults(cli_args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load unified defaults via :class:`UnifiedSettingsLoader`.

    Merges *cli_args* (highest priority) with environment variables,
    ``model_profiles.json``, ``novel_forge.cli.json`` ``defaults`` section,
    ``.env``, and built-in fallback values.

    # TODO: migrate to UnifiedSettingsLoader — currently returned as a flat
    dict so existing ``resolve_*_option`` helpers can consume it; full
    migration would replace those helpers with direct ``loader.load()`` calls.
    """
    from novel_forge.core.infra.settings_loader import UnifiedSettingsLoader

    loader = UnifiedSettingsLoader(cli_args=cli_args or {})
    return loader.load_all()


DEFAULT_CONFIG_FILENAMES: tuple[str, ...] = (
    "novel_forge.cli.json",
    ".novel-forge.json",
)
ENV_CONFIG_PATH = "NOVEL_FORGE_CLI_CONFIG"


def default_config_path() -> Path:
    return Path(DEFAULT_CONFIG_FILENAMES[0])


def default_config_template() -> dict[str, Any]:
    return {
        "_field_notes": {
            "说明": "该区块仅用于字段说明与示例，不参与程序运行。",
            "run_short": {
                "theme": "故事主题/核心创意。例：一个人在关键抉择中改变自己与他人的命运",
                "genre": "题材类型。例：fantasy / romance / mystery",
                "tone": "叙事基调。例：dark / lyrical / suspenseful",
                "length": "目标字数。例：5000",
                "title": "可选，作品暂定名。例：未命名项目",
                "language": "输出语言代码。例：zh / en",
                "characters_hint": "可选，角色提示。例：主角A（外在目标+内在缺口）；角色B（关系张力）",
                "world_hint": "可选，世界观/场景提示。例：当代城市 / 架空王国 / 近未来空间站",
                "conflict_hint": "可选，核心冲突提示。例：外部阻力 + 内在困境 + 关系代价",
                "pov_hint": "可选，叙事视角。需写清人称+视角结构。例：第三人称限知，女主单主视角，非对话正文禁用我/我们；或第一人称女主自述",
                "opening_style": "可选，开篇方式。例：以异常事件、关键选择或强情绪场面开场",
                "ending_style": "可选，结尾方式。例：开放式、反转式、余韵式或明确收束",
                "extra_instructions": "可选，额外创作约束。例：避免套路台词，强化细节与人物选择",
            },
            "init_long": {
                "premise": "长篇核心前提。例：一个人物在持续变化的世界中追查真相并完成转变",
                "genre": "题材类型。例：mystery, romance, historical, 科幻悬疑",
                "tone": "叙事基调。例：温柔治愈、冷峻悬疑、热血成长、克制现实",
                "total_chapters": "目标章节数。例：20",
                "words_per_chapter": "每章目标字数。例：6000",
                "volume_mode": "分卷模式。例：auto / on / off",
                "chapters_per_volume": "每卷章数，0 为自动。例：0 或 12",
                "title": "可选，作品暂定名。例：未命名长篇",
                "language": "输出语言代码。例：zh / en",
                "characters_hint": "可选，主角群提示。例：主角A、对照角色B、关键反派C",
                "world_hint": "可选，时代/地域与文化背景提示。例：现代都市 / 架空历史 / 异星殖民地",
                "conflict_hint": "可选，主冲突提示。例：外部危机、内在伤口、价值选择三线并进",
                "pov_hint": "可选，视角提示。需写清人称+视角结构。例：第三人称多视角，女主主视角为主，男主/旁观视角按触发条件穿插；非对话正文禁用我/我们",
                "opening_style": "可选，开篇方式。例：高概念事件、关键失控或关系断裂切入",
                "ending_style": "可选，结尾方向。例：HE、BE、开放式或主题回环",
                "extra_instructions": "可选，额外创作要求。例：真实细节优先，避免工具人配角",
                "polish_hint": "可选，初始化后章节大纲精修指令。例：强化前五章悬念递进",
                "research_enabled": "是否在规格确认后执行联网资料检索。默认 false",
                "research_provider": "资料检索 provider。例：auto / tavily / brave / searxng / http_json / bailian_web_search / mcp_search",
                "research_query_hint": "可选，补充检索方向。例：唐代司法制度、市井职业、地方志",
                "blueprint_element_preferences": "可选，叙事蓝图要素偏好 JSON 对象。例：{\"preset_id\":\"mystery\",\"manual_override\":true}",
                "project_id": "项目 ID。例：my_project",
                "edit_rounds": "后续章节默认编辑轮次。例：2",
            },
            "book_audit": {
                "project_id": "要审计的长篇项目 ID。",
                "chapter_range": "章节范围。空=所有已完成章节；也可写 1,2,5-8。",
                "analysis_mode": "审计模式：auto / summary / full_text。",
                "repair": "是否在审计后执行定向自动修复。",
                "continue_from_audit": "是否跳过新审计，继续修复上次审计报告中的剩余问题。",
            },
        },
        "defaults": {
            "mock": False,
            "verbose": False,
            "edit_rounds": 2,
        },
        "run_short": {
            "theme": DEFAULT_SHORT_THEME,
            "genre": DEFAULT_GENRE,
            "tone": DEFAULT_TONE,
            "length": 3000,
            "title": "",
            "language": "zh",
            "characters_hint": "",
            "world_hint": "",
            "conflict_hint": "",
            "pov_hint": "",
            "opening_style": "",
            "ending_style": "",
            "extra_instructions": "",
        },
        "init_long": {
            "premise": DEFAULT_LONG_PREMISE,
            "genre": DEFAULT_GENRE,
            "tone": DEFAULT_TONE,
            "total_chapters": 20,
            "words_per_chapter": 3000,
            "volume_mode": "auto",
            "chapters_per_volume": 0,
            "title": "",
            "language": "zh",
            "characters_hint": "",
            "world_hint": "",
            "conflict_hint": "",
            "pov_hint": "",
            "opening_style": "",
            "ending_style": "",
            "extra_instructions": "",
            "polish_hint": "",
            "research_enabled": False,
            "research_provider": "auto",
            "research_query_hint": "",
            "blueprint_element_preferences": {},
            "project_id": "",
            "edit_rounds": 2,
        },
        "run_chapter": {
            "project_id": "",
            "chapter": 0,
            "edit_rounds": 2,
            "force": False,
            "auto": False,
            "end_chapter": 0,
            "ai_judge_apply_mode": "assist",
        },
        "sync_bible": {
            "project_id": "",
            "chapter": 0,
            "smart": False,
            "no_outline": False,
            "auto_yes": False,
        },
        "book_audit": {
            "project_id": "",
            "chapter_range": "",
            "analysis_mode": "auto",
            "prompt_hint": "",
            "location_strictness": "balanced",
            "repair": False,
            "repair_min_severity": "warning",
            "repair_max_chapters": 20,
            "repair_concurrency": 1,
            "post_repair_targeted_audit": False,
            "continue_from_audit": False,
            "continue_audit_from_checkpoint": False,
            "reset_audit_checkpoint": False,
            "parallel_chunks": True,
            "parallel_dimensions": True,
            "max_tokens": 0,
            "temperature": None,
            "mock": False,
            "verbose": False,
        },
    }


def load_runtime_config(path_option: Optional[str]) -> tuple[dict[str, Any], Path | None]:
    path, strict = _resolve_runtime_config_path(path_option)
    if path is None:
        return {}, None
    if not path.is_file():
        if strict:
            raise RuntimeConfigError(f"配置文件不存在: {path}")
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeConfigError(
            f"配置文件 JSON 解析失败: {path} (line {exc.lineno}, col {exc.colno})"
        ) from exc
    except OSError as exc:
        raise RuntimeConfigError(f"读取配置文件失败: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise RuntimeConfigError(f"配置文件根节点必须是 JSON 对象: {path}")
    return data, path


def resolve_str_option(
    cli_value: Optional[str],
    config: Mapping[str, Any],
    *,
    section: str,
    key: str,
    default: str,
    unified_defaults: Mapping[str, Any] | None = None,
) -> str:
    if cli_value is not None:
        return cli_value
    raw = _lookup(config, section=section, key=key, unified_defaults=unified_defaults)
    if raw is None:
        return default
    if not isinstance(raw, str):
        raise RuntimeConfigError(f"配置项 {section}.{key} 必须是字符串")
    return raw


def resolve_json_dict_option(
    cli_value: Optional[str],
    config: Mapping[str, Any],
    *,
    section: str,
    key: str,
    default: dict[str, Any] | None = None,
    unified_defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve an option that must be a JSON object/dict.

    CLI values are strings and are parsed as JSON. Config files may provide
    either a JSON object directly or a JSON-encoded string for parity with CLI.
    """
    raw: Any
    source = f"{section}.{key}"
    if cli_value is not None:
        raw = cli_value
        source = f"--{key.replace('_', '-')}"
    else:
        raw = _lookup(config, section=section, key=key, unified_defaults=unified_defaults)
        if raw is None:
            return dict(default or {})

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return dict(default or {})
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeConfigError(
                f"配置项 {source} 必须是 JSON 对象 (line {exc.lineno}, col {exc.colno})"
            ) from exc
    if not isinstance(raw, dict):
        raise RuntimeConfigError(f"配置项 {source} 必须是 JSON 对象")
    return dict(raw)


def resolve_int_option(
    cli_value: Optional[int],
    config: Mapping[str, Any],
    *,
    section: str,
    key: str,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
    unified_defaults: Mapping[str, Any] | None = None,
) -> int:
    if cli_value is not None:
        value = cli_value
    else:
        raw = _lookup(config, section=section, key=key, unified_defaults=unified_defaults)
        if raw is None:
            value = default
        else:
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise RuntimeConfigError(f"配置项 {section}.{key} 必须是整数")
            value = raw
    if minimum is not None and value < minimum:
        raise RuntimeConfigError(f"配置项 {section}.{key} 需 >= {minimum}")
    if maximum is not None and value > maximum:
        raise RuntimeConfigError(f"配置项 {section}.{key} 需 <= {maximum}")
    return value


def resolve_float_option(
    cli_value: Optional[float],
    config: Mapping[str, Any],
    *,
    section: str,
    key: str,
    default: float | None,
    minimum: float | None = None,
    maximum: float | None = None,
    unified_defaults: Mapping[str, Any] | None = None,
) -> float | None:
    if cli_value is not None:
        value = cli_value
    else:
        raw = _lookup(config, section=section, key=key, unified_defaults=unified_defaults)
        if raw is None:
            value = default
        else:
            if isinstance(raw, bool) or not isinstance(raw, int | float):
                raise RuntimeConfigError(f"配置项 {section}.{key} 必须是数字")
            value = float(raw)
    if value is None:
        return None
    if minimum is not None and value < minimum:
        raise RuntimeConfigError(f"配置项 {section}.{key} 需 >= {minimum}")
    if maximum is not None and value > maximum:
        raise RuntimeConfigError(f"配置项 {section}.{key} 需 <= {maximum}")
    return value


def resolve_bool_option(
    cli_value: Optional[bool],
    config: Mapping[str, Any],
    *,
    section: str,
    key: str,
    default: bool,
    unified_defaults: Mapping[str, Any] | None = None,
) -> bool:
    if cli_value is not None:
        return cli_value
    raw = _lookup(config, section=section, key=key, unified_defaults=unified_defaults)
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise RuntimeConfigError(f"配置项 {section}.{key} 必须是布尔值")
    return raw


def resolve_choice_option(
    cli_value: Optional[str],
    config: Mapping[str, Any],
    *,
    section: str,
    key: str,
    default: str,
    choices: Sequence[str],
    unified_defaults: Mapping[str, Any] | None = None,
) -> str:
    value = resolve_str_option(
        cli_value,
        config,
        section=section,
        key=key,
        default=default,
        unified_defaults=unified_defaults,
    )
    if value not in choices:
        allowed = ", ".join(choices)
        raise RuntimeConfigError(f"配置项 {section}.{key} 必须是: {allowed}")
    return value


def _resolve_runtime_config_path(path_option: Optional[str]) -> tuple[Path | None, bool]:
    if path_option and path_option.strip():
        return Path(path_option.strip()).expanduser(), True
    env_value = os.getenv(ENV_CONFIG_PATH, "").strip()
    if env_value:
        return Path(env_value).expanduser(), True
    for name in DEFAULT_CONFIG_FILENAMES:
        candidate = Path(name)
        if candidate.is_file():
            return candidate, False
    return None, False


def _lookup(
    config: Mapping[str, Any],
    *,
    section: str,
    key: str,
    unified_defaults: Mapping[str, Any] | None = None,
) -> Any | None:
    section_value = config.get(section)
    if section_value is not None:
        section_map = _ensure_mapping(section_value, name=section)
        if key in section_map:
            return section_map[key]
    defaults_value = config.get("defaults")
    if defaults_value is not None:
        defaults_map = _ensure_mapping(defaults_value, name="defaults")
        if key in defaults_map:
            return defaults_map[key]
    # TODO: migrate to UnifiedSettingsLoader — check unified defaults for
    # global config keys (API keys, model routing, temperatures, etc.).
    if unified_defaults is not None and key in unified_defaults:
        return unified_defaults[key]
    return None


def _ensure_mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeConfigError(f"配置块 {name} 必须是对象")
    return value
