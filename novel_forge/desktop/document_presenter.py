"""Shared project-document rendering helpers for desktop pages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from novel_forge.core.utils.version_diff import draft_stem_display_label

_EVAL_DIM_LABELS: dict[str, str] = {
    "consistency": "设定一致",
    "continuity": "场景连贯",
    "character": "人物塑造",
    "style": "文笔风格",
    "engagement": "吸引力",
    "pacing": "节奏控制",
    # legacy / extended
    "coherence": "连贯性",
    "tech_density": "术语密度",
    "plot": "情节逻辑",
    "originality": "独创性",
    "theme": "主题表达",
    "world_building": "世界构建",
    "dialogue": "对话质量",
    "rewrite_compliance": "重写落地",
}


def read_artifact_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return f"无法读取文件：{path}"


def format_json_text(raw: str) -> str:
    try:
        data = json.loads(raw)
        return json.dumps(data, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        return raw


def render_outline_text(data: dict[str, Any]) -> str:
    """Turn outline JSON into a human-readable chapter directory."""
    total = data.get("total_chapters", "?")
    synopsis = data.get("synopsis", "")
    chapters = data.get("chapters", [])
    volume_mode = data.get("volume_mode", False)
    volumes = data.get("volumes", [])

    lines: list[str] = [f"全书大纲 · 共 {total} 章"]
    if synopsis:
        lines += ["", synopsis]
    lines += ["", "─" * 50]

    if volume_mode and volumes:
        vol_map: dict[int, dict[str, Any]] = {}
        for volume in volumes:
            for chapter_num in range(
                volume.get("start_chapter", 1),
                volume.get("end_chapter", 1) + 1,
            ):
                vol_map[chapter_num] = volume
        current_vol: dict[str, Any] | None = None
        for chapter in chapters:
            vol = vol_map.get(chapter.get("chapter_number", 0))
            if vol and vol is not current_vol:
                current_vol = vol
                volume_number = vol.get("volume_number", "?")
                volume_title = vol.get("title", "")
                lines += [f"\n{'═' * 50}", f"第 {volume_number} 卷 · {volume_title}"]
                arc = vol.get("arc_goal", "")
                if arc:
                    lines.append(f"  卷目标：{arc}")
                lines.append("═" * 50)
            append_chapter_entry(lines, chapter)
    else:
        for chapter in chapters:
            append_chapter_entry(lines, chapter)

    return "\n".join(lines)


def append_chapter_entry(lines: list[str], chapter: dict[str, Any]) -> None:
    num = chapter.get("chapter_number", "?")
    title = chapter.get("title", "未命名")
    goal = chapter.get("goal", "")
    pov = chapter.get("pov_character", "")
    pov_switch = chapter.get("pov_switch", False)
    setting = chapter.get("setting", "")
    word_count = chapter.get("expected_word_count", 0)
    beats = chapter.get("beats_summary", [])

    lines.append(f"\n第 {num} 章 · {title}")
    if goal:
        lines.append(f"  ─ 目标：{goal}")
    if pov:
        pov_label = f"{pov}（视角切换）" if pov_switch else pov
        lines.append(f"  ─ 视角：{pov_label}")
    if setting:
        lines.append(f"  ─ 场景：{setting}")
    if word_count:
        lines.append(f"  ─ 预计：{word_count:,} 字")
    if beats:
        lines.append("  ─ 节拍：")
        for beat in beats:
            lines.append(f"      · {beat}")


def render_eval_text(data: dict[str, Any]) -> str:
    """Turn eval report JSON into a readable quality summary."""
    overall = data.get("overall_score")
    passed = data.get("passed")
    threshold = data.get("threshold", 6.0)
    summary = data.get("summary", "")
    scores = data.get("scores", [])

    lines: list[str] = ["质量评估报告", ""]
    if overall is not None:
        mark = "✓ 通过" if passed else "✗ 未通过"
        lines.append(f"总评分：{overall:.1f} / 10  {mark}（阈值 {threshold}）")
        lines.append("")

    if scores:
        lines.append("─" * 40)
        lines.append("各维度评分：")
        lines.append("")
        for score_item in scores:
            dim = score_item.get("dimension", "?")
            score = score_item.get("score", "?")
            comment = score_item.get("comment", "")
            dim_label = _EVAL_DIM_LABELS.get(dim, dim)
            score_str = f"{score:.1f}" if isinstance(score, (int, float)) else str(score)
            lines.append(f"  {dim_label:<8}  {score_str}")
            if comment:
                lines.append(f"    {comment}")
            lines.append("")

    if summary:
        lines += ["─" * 40, "", f"总结：{summary}"]

    return "\n".join(lines)


def smart_artifact_content(path: Path) -> str:
    """Load file and apply smart formatting based on content structure."""
    raw = read_artifact_text(path)
    if path.suffix != ".json":
        return raw

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw

    if isinstance(data, dict):
        if "total_chapters" in data and "chapters" in data:
            return render_outline_text(data)
        if "overall_score" in data and "scores" in data:
            return render_eval_text(data)

    return json.dumps(data, indent=2, ensure_ascii=False)


def draft_display_name(filename: str) -> str:
    """Turn draft filename stem into a readable label."""
    return draft_stem_display_label(filename)
