"""Verbose step-callback formatting for CLI pipeline output."""

from __future__ import annotations

import json
from typing import Any, Callable

from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.table import Table

console = Console()

# Dimension display labels for CLI summary
_DIM_LABELS: dict[str, str] = {
    "consistency": "设定一致",
    "continuity": "场景连贯",
    "character": "人物塑造",
    "style": "文笔风格",
    "engagement": "吸引力",
    "pacing": "节奏感",
    "rewrite_compliance": "重写落地",
    # legacy
    "coherence": "连贯性",
    "tech_density": "术语密度",
}


def print_chapter_summary(result: Any) -> None:
    """Print a rich chapter completion summary to the CLI.

    Expects a ChapterResult-like object with meta, eval_report, and trace_summary.
    """
    meta = getattr(result, "meta", None)
    eval_report = getattr(result, "eval_report", None)
    trace_summary = getattr(result, "trace_summary", None)

    # ── Header ──
    ch = getattr(meta, "chapter_number", "?") if meta else "?"
    title = getattr(meta, "title", "") if meta else ""
    console.print(f"\n[bold green]{'=' * 60}[/bold green]")
    console.print(f"[bold green]  第 {ch} 章完成：{title}[/bold green]")
    console.print(f"[bold green]{'=' * 60}[/bold green]")

    # ── Basic stats ──
    if meta:
        console.print(
            f"  字数: [bold]{getattr(meta, 'word_count', '?')}[/bold]  "
            f"编辑轮次: {getattr(meta, 'edit_rounds', '?')}  "
            f"Token: {getattr(meta, 'tokens_used', '?')}  "
            f"费用: ${getattr(meta, 'cost_usd', 0):.4f}"
        )

    # ── Eval scores table ──
    if eval_report is not None:
        scores = getattr(eval_report, "scores", []) or []
        if scores:
            table = Table(title="评估得分", show_header=True, header_style="bold cyan", width=60)
            table.add_column("维度", style="bold", width=14)
            table.add_column("得分", justify="center", width=8)
            table.add_column("评语", width=34)
            for s in scores:
                dim = getattr(s, "dimension", "?")
                label = _DIM_LABELS.get(dim, dim)
                score = getattr(s, "score", 0.0)
                comment = getattr(s, "comment", "")
                # Colorize score
                if score >= 8.0:
                    score_str = f"[green]{score:.1f}[/green]"
                elif score >= 6.0:
                    score_str = f"[yellow]{score:.1f}[/yellow]"
                else:
                    score_str = f"[red]{score:.1f}[/red]"
                table.add_row(label, score_str, comment[:34])
            overall = getattr(eval_report, "overall_score", 0.0)
            passed = getattr(eval_report, "passed", False)
            status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
            table.add_row("─" * 14, "─" * 6, "─" * 34)
            table.add_row("[bold]总分[/bold]", f"[bold]{overall:.1f}[/bold]", status)
            console.print(table)

        # Repair suggestions
        suggestions = getattr(eval_report, "repair_suggestions", []) or []
        if suggestions:
            console.print("\n[bold yellow]修复建议:[/bold yellow]")
            for sug in suggestions[:5]:
                dim = getattr(sug, "dimension", "")
                prio = getattr(sug, "priority", "medium")
                issue = getattr(sug, "issue", "")
                loc = getattr(sug, "location", "")
                prio_color = {"high": "red", "medium": "yellow", "low": "dim"}.get(prio, "white")
                console.print(f"  [{prio_color}][{prio}][/{prio_color}] {issue} @ {loc} ({dim})")

    # ── Stage cost breakdown ──
    if isinstance(trace_summary, dict):
        stages = trace_summary.get("stages", {})
        if stages:
            cost_table = Table(
                title="成本分布", show_header=True, header_style="bold magenta", width=60
            )
            cost_table.add_column("阶段", style="bold", width=12)
            cost_table.add_column("Token", justify="right", width=10)
            cost_table.add_column("费用", justify="right", width=12)
            cost_table.add_column("耗时", justify="right", width=10)
            cost_table.add_column("步骤", justify="center", width=6)
            stage_labels = {
                "planning": "规划",
                "drafting": "起草",
                "editing": "编辑",
                "quality": "质检",
                "finalize": "定稿",
                "other": "其他",
            }
            for stage_key, stage_data in stages.items():
                if not isinstance(stage_data, dict):
                    continue
                cost_table.add_row(
                    stage_labels.get(stage_key, stage_key),
                    str(stage_data.get("tokens", 0)),
                    f"${stage_data.get('cost_usd', 0):.4f}",
                    f"{stage_data.get('duration_ms', 0) / 1000:.1f}s",
                    str(stage_data.get("steps", 0)),
                )
            console.print(cost_table)

    console.print()


def _make_step_callback(verbose: bool) -> Callable[[str, Any], None]:
    """Create a reusable on_step callback for verbose pipeline output."""
    from rich.json import JSON

    def on_step(step: str, data: Any) -> None:
        if not verbose:
            return

        if step.startswith("llm_stream_") and isinstance(data, dict):
            if step == "llm_stream_delta":
                delta = str(data.get("delta") or "")
                if delta:
                    console.out(delta, end="")
                return
            task = str(data.get("task") or "?")
            attempt = data.get("attempt", "?")
            if step == "llm_stream_start":
                console.print(
                    f"\n[bold cyan]LLM stream started[/bold cyan] {task} attempt={attempt}"
                )
                return
            if step == "llm_stream_end":
                chars = data.get("chars", data.get("text_length", "?"))
                finish_reason = str(data.get("finish_reason") or "stop")
                console.print(
                    f"\n[dim]LLM stream complete {task} chars={chars} finish={finish_reason}[/dim]"
                )
                return
            if step == "llm_stream_restart":
                next_attempt = data.get("next_attempt", "?")
                backoff = data.get("backoff_seconds", 0)
                console.print(
                    f"\n[yellow]LLM stream restarting[/yellow] {task} "
                    f"attempt={attempt} next={next_attempt} backoff={backoff}s"
                )
                return
            if step == "llm_stream_error":
                error = str(data.get("error") or data.get("message") or "")
                console.print(f"\n[red]LLM stream error[/red] {task} attempt={attempt}: {error}")
                return

        title = f"Step Completed: {step.upper()}"
        renderable: RenderableType | None = None

        # Resume: indicate a step was loaded from disk
        if step.endswith("_resumed"):
            base_step = step.replace("_resumed", "").upper()
            title = f"⏩ Step Resumed: {base_step} (loaded from disk)"

        # Token escalation: show old/new limits
        elif step == "token_escalation" and isinstance(data, dict):
            title = "⚠️  Token Escalation"
            renderable = (
                f"[bold yellow]Task:[/bold yellow] {data.get('task', '?')}\n"
                f"[bold yellow]Attempt:[/bold yellow] {data.get('attempt', '?')}\n"
                f"[bold]Token limit:[/bold] {data.get('old_max_tokens', '?')} → {data.get('new_max_tokens', '?')}\n"
                f"[dim]Error: {data.get('error', '?')}[/dim]"
            )

        # Consistency warnings: show suspicious patterns (non-blocking)
        elif step == "consistency_warnings" and isinstance(data, dict):
            title = "⚠️  一致性警告（非阻塞）"
            warnings = data.get("warnings", [])
            if warnings:
                parts = ["[bold yellow]检测到以下可疑模式，请确认：[/bold yellow]\n"]
                for i, warning in enumerate(warnings, 1):
                    parts.append(f"{i}. {warning}\n")
                renderable = "\n".join(parts)
            else:
                renderable = "[dim]无警告[/dim]"

        # Early-stop
        elif step == "early_stop" and isinstance(data, dict):
            title = "✅ Early Stop"
            renderable = (
                f"Quality score {data.get('score', '?')} ≥ 8.0 at round {data.get('round', '?')}, "
                f"skipping remaining edit rounds."
            )

        elif step == "alignment_repair" and isinstance(data, dict):
            title = "🛠️ 对齐修复编辑"
            parts = [
                f"[bold]修复轮次:[/bold] {data.get('repair_iteration', '?')}",
                f"[bold]分数变化:[/bold] {data.get('previous_score', '?')} → {data.get('new_score', '?')} "
                f"[dim](threshold={data.get('threshold', '?')})[/dim]",
                f"[bold]风险变化:[/bold] {data.get('previous_risk', '?')} → {data.get('new_risk', '?')}",
            ]
            summary = data.get("subplot_summary", {})
            if isinstance(summary, dict):
                preserved = summary.get("preserved_supportive_subplots", [])
                converged = summary.get("converged_disruptive_subplots", [])
                remaining = summary.get("remaining_disruptive_subplots", [])
                newly_supported = summary.get("newly_supported_subplots", [])
                newly_disruptive = summary.get("newly_detected_disruptive_subplots", [])

                if isinstance(preserved, list) and preserved:
                    parts.append("\n[bold green]✅ 保留的赋能支线:[/bold green]")
                    parts.extend(f"- {item}" for item in preserved[:5])
                else:
                    parts.append(
                        "\n[bold green]✅ 保留的赋能支线:[/bold green] [dim]无明确命中[/dim]"
                    )

                if isinstance(converged, list) and converged:
                    parts.append("\n[bold cyan]🔧 收敛的干扰支线:[/bold cyan]")
                    parts.extend(f"- {item}" for item in converged[:5])
                else:
                    parts.append("\n[bold cyan]🔧 收敛的干扰支线:[/bold cyan] [dim]无[/dim]")

                if isinstance(remaining, list) and remaining:
                    parts.append("\n[bold yellow]⚠️ 仍待收敛的干扰支线:[/bold yellow]")
                    parts.extend(f"- {item}" for item in remaining[:5])

                if isinstance(newly_supported, list) and newly_supported:
                    parts.append("\n[bold blue]➕ 新识别的赋能支线:[/bold blue]")
                    parts.extend(f"- {item}" for item in newly_supported[:3])

                if isinstance(newly_disruptive, list) and newly_disruptive:
                    parts.append("\n[bold red]🆕 新识别的干扰支线:[/bold red]")
                    parts.extend(f"- {item}" for item in newly_disruptive[:3])

            renderable = "\n".join(parts)

        elif step == "context_compress" and isinstance(data, dict):
            from rich.markup import escape

            title = "🗜️ 上下文压缩"
            parts = [
                f"[bold]候选片段:[/bold] {data.get('candidates', 0)}",
                f"[bold]模型压缩:[/bold] {data.get('compressed', 0)}",
                f"[bold]回退截断:[/bold] {data.get('fallback', 0)}",
                f"[bold]直接截断:[/bold] {data.get('direct_trim', 0)}",
                f"[bold]字符数:[/bold] {data.get('before_chars', 0)} → {data.get('after_chars', 0)}",
            ]
            samples = data.get("samples", [])
            if isinstance(samples, list) and samples:
                parts.append("\n[bold yellow]压缩样例（前/后）:[/bold yellow]")
                for idx, sample in enumerate(samples[:6], 1):
                    if not isinstance(sample, dict):
                        continue
                    tag = str(sample.get("tag", "")).strip() or "unknown"
                    method = str(sample.get("method", "")).strip() or "unknown"
                    before_len = sample.get("before_len", "?")
                    after_len = sample.get("after_len", "?")
                    before_text = escape(str(sample.get("before", "")).strip())
                    after_text = escape(str(sample.get("after", "")).strip())
                    parts.append(
                        f"\n{idx}. [cyan]{tag}[/cyan] [dim]({method}, {before_len}→{after_len})[/dim]"
                    )
                    parts.append(f"   [dim]前:[/dim] {before_text}")
                    parts.append(f"   [dim]后:[/dim] {after_text}")
            renderable = "\n".join(parts)

        elif step == "bridge":
            title = "🌉 章节桥接契约"
            bridge_summary = getattr(data, "bridge_summary", "") or "无摘要"
            renderable = (
                f"[bold]来源章节:[/bold] {getattr(data, 'from_chapter', '?')}\n"
                f"[bold]目标章节:[/bold] {getattr(data, 'to_chapter', '?')}\n"
                f"[bold]开场时间:[/bold] {getattr(data, 'opening_time', '') or '未指定'}\n"
                f"[bold]开场地点:[/bold] {getattr(data, 'opening_location', '') or '未指定'}\n"
                f"[bold]开场 POV:[/bold] {getattr(data, 'opening_pov', '') or '未指定'}\n"
                f"[bold]过渡方式:[/bold] {getattr(data, 'transition_mode', '') or '未指定'}\n"
                f"[bold]摘要:[/bold] {bridge_summary}"
            )

        elif step in {"continuity_eval", "continuity_eval_after_repair"}:
            title = "🪢 连贯性检查"
            score = getattr(data, "continuity_score", "?")
            summary = getattr(data, "summary", "") or "无摘要"
            issues = getattr(data, "issues", []) or []
            parts = [
                f"[bold]分数:[/bold] {score}/10",
                f"[bold]摘要:[/bold] {summary}",
                f"[bold]问题数:[/bold] {len(issues)}",
            ]
            if issues:
                parts.append("\n[bold yellow]问题列表:[/bold yellow]")
                for issue in issues[:6]:
                    issue_type = getattr(issue, "issue_type", "unknown")
                    severity = getattr(issue, "severity", "medium")
                    issue_summary = getattr(issue, "summary", "") or "无摘要"
                    parts.append(f"- [{severity}] {issue_type}: {issue_summary}")
            renderable = "\n".join(parts)

        elif step == "continuity_repair" and hasattr(data, "repair_plan"):
            title = "🩹 连贯性修复"
            repair_plan = getattr(data, "repair_plan", None)
            applied = getattr(data, "applied", False)
            patch_only = getattr(data, "patch_only", False)
            if repair_plan is not None:
                mode_hint = "（仅 patch）" if patch_only else "（全文修复）"
                renderable = (
                    f"[bold]执行状态:[/bold] {'已应用' if applied else 'no-op'} {mode_hint}\n"
                    f"[bold]问题数:[/bold] {len(getattr(repair_plan, 'issues', []) or [])}\n"
                    f"[bold]目标片段数:[/bold] {len(getattr(repair_plan, 'target_sections', []) or [])}\n"
                    f"[bold]预期结果:[/bold] {getattr(repair_plan, 'expected_outcome', '') or '无'}"
                )

        elif step in ("continuity_issue_ledger", "causal_issue_ledger") and isinstance(data, dict):
            stage_label = "连贯性" if "continuity" in step else "因果链"
            title = f"📊 {stage_label}修复效果"
            resolved = data.get("resolved", 0)
            new_issues = data.get("new_issues", 0)
            new_hc = data.get("new_high_critical", 0)
            resolved_hc = data.get("resolved_high_critical", 0)
            net_hc = data.get("net_high_critical_fix_count", resolved_hc - new_hc)
            has_regr = data.get("has_regression", False)
            status_icon = "✅" if not has_regr else "⚠️"
            renderable = (
                f"{status_icon} 第 {data.get('round', '?')} 轮\n"
                f"[bold]已解决:[/bold] {resolved}　[bold]新增:[/bold] {new_issues}　"
                f"[bold]净收益(高/严重):[/bold] {net_hc:+d}\n"
                f"[bold]降级:[/bold] {data.get('downgraded', 0)}　"
                f"[bold]升级:[/bold] {data.get('upgraded', 0)}"
            )
            if has_regr:
                renderable += (
                    "\n[bold red]回归检测触发：新增严重问题多于解决问题，将回滚[/bold red]"
                )

        elif step in ("continuity_repair_rollback", "causal_repair_rollback") and isinstance(
            data, dict
        ):
            stage_label = "连贯性" if "continuity" in step else "因果链"
            title = f"⏪ {stage_label}修复回滚"
            renderable = (
                f"[bold]章节:[/bold] {data.get('chapter', '?')}　"
                f"[bold]轮次:[/bold] {data.get('round', '?')}\n"
                f"[bold]原因:[/bold] {data.get('reason', '未知')}\n"
                f"[bold]已解决:[/bold] {data.get('resolved_count', '?')}　"
                f"[bold]新增严重问题:[/bold] {data.get('new_count', '?')}"
            )

        elif step == "chapter_compact" and isinstance(data, dict):
            title = "🧹 章节级 Canon 压缩"
            renderable = (
                f"[bold]章节:[/bold] {data.get('chapter', '?')}\n"
                f"[bold]归档角色:[/bold] {len(data.get('archived_characters', []))}\n"
                f"[bold]归档世界事实:[/bold] {len(data.get('archived_world_fact_keys', []))}\n"
                f"[bold]归档伏笔:[/bold] {len(data.get('archived_foreshadowing_ids', []))}\n"
                f"[bold]活跃规模:[/bold] chars={data.get('active_characters', '?')}, "
                f"facts={data.get('active_world_facts', '?')}, fs={data.get('active_foreshadowing', '?')}"
            )

        # Creative Report: 美化显示创作分析
        elif step == "creative_report":
            from novel_forge.core.schemas.canon import CreativeReport

            if isinstance(data, CreativeReport):
                title = "📊 创作报告"
                parts = []

                # 新增角色
                if data.new_characters:
                    parts.append("[bold yellow]🆕 新增角色：[/bold yellow]")
                    for char in data.new_characters:
                        icon = "⭐" if char.should_add_to_bible else "  "
                        parts.append(f"  {icon} [bold]{char.name}[/bold] ({char.role_in_story})")
                        if char.description:
                            parts.append(f"     {char.description}")
                        if char.relationship_to_existing:
                            rel_str = ", ".join(
                                f"{k}→{v}" for k, v in char.relationship_to_existing.items()
                            )
                            parts.append(f"     [dim]关系: {rel_str}[/dim]")
                        if char.should_add_to_bible:
                            parts.append("     [green]💡 建议加入角色设定[/green]")
                    parts.append("")

                # 新增地点
                if data.new_locations:
                    locations_str = "、".join(data.new_locations)
                    parts.append(f"[bold cyan]📍 新增地点：[/bold cyan]{locations_str}")
                    parts.append("")

                # 新增物品
                if data.new_key_items:
                    items_str = "、".join(data.new_key_items)
                    parts.append(f"[bold magenta]🔑 关键物品：[/bold magenta]{items_str}")
                    parts.append("")

                # 剧情偏离
                if data.plot_deviations:
                    parts.append("[bold red]📖 剧情偏离分析：[/bold red]")
                    for dev in data.plot_deviations:
                        level_color = {"minor": "green", "moderate": "yellow", "major": "red"}.get(
                            dev.deviation_level, "white"
                        )
                        parts.append(
                            f"  [{level_color}]● {dev.deviation_level.upper()}[/{level_color}]"
                        )
                        parts.append(f"     原计划：{dev.outline_plan}")
                        parts.append(f"     实际情节：{dev.actual_plot}")
                        if dev.reason:
                            parts.append(f"     原因：{dev.reason}")
                        if dev.impact_on_future:
                            parts.append(f"     影响：{dev.impact_on_future}")
                    parts.append("")

                # 创作亮点
                if data.creative_highlights:
                    parts.append("[bold green]✨ 创作亮点：[/bold green]")
                    for highlight in data.creative_highlights:
                        parts.append(f"  • {highlight}")
                    parts.append("")

                # 下一章建议
                if data.suggestions_for_next_chapter:
                    parts.append("[bold blue]💡 下一章建议：[/bold blue]")
                    parts.append(f"  {data.suggestions_for_next_chapter}")

                renderable = "\n".join(parts) if parts else "[dim]无特别分析[/dim]"

        # Draft: show word count + text preview
        if renderable is None and step == "draft" and hasattr(data, "text"):
            count = getattr(data, "word_count", "?")
            text = getattr(data, "text", "")
            text_preview = text[:2000] + ("\n..." if len(text) > 2000 else "")
            renderable = f"[bold]Draft Generated (Words: {count})[/bold]\n\n{text_preview}"

        # Edit: show notes + revised text preview
        elif renderable is None and step.startswith("edit") and hasattr(data, "revised_text"):
            notes = getattr(data, "edit_notes", [])
            notes_str = "\n".join(f"- {n}" for n in notes) if notes else "(No specific notes)"
            revised_text = getattr(data, "revised_text", "")
            text_preview = revised_text[:2000] + ("\n..." if len(revised_text) > 2000 else "")
            renderable = (
                f"[bold yellow]Edit Notes:[/bold yellow]\n{notes_str}\n\n"
                f"[bold green]Revised Text:[/bold green]\n{text_preview}"
            )

        # Dict with nested models (e.g. init_character_bible)
        elif renderable is None and isinstance(data, dict):
            parts = []
            for key, val in data.items():
                if hasattr(val, "model_dump_json"):
                    try:
                        json_str = val.model_dump_json(indent=2)
                        parts.append(f"[bold]{key}:[/bold]\n{json_str}")
                    except Exception:
                        parts.append(f"[bold]{key}:[/bold] {str(val)[:1000]}")
                else:
                    parts.append(f"[bold]{key}:[/bold] {str(val)[:500]}")
            renderable = "\n\n".join(parts)

        # Fallback: try JSON serialization
        elif renderable is None:
            if hasattr(data, "model_dump_json"):
                try:
                    json_method = data.model_dump_json
                    renderable = JSON(json_method(indent=2))
                except Exception:
                    pass
            elif isinstance(data, (list,)):
                try:
                    renderable = JSON(json.dumps(data, ensure_ascii=False, indent=2))
                except Exception:
                    pass

        # Last resort: plain string
        if renderable is None:
            s_data = str(data)
            if len(s_data) > 2000:
                renderable = f"[dim]Length: {len(s_data)} chars[/dim]\n\n{s_data[:2000]}..."
            else:
                renderable = s_data

        console.print(
            Panel(renderable, title=f"[bold cyan]{title}[/bold cyan]", border_style="cyan")
        )

    return on_step
