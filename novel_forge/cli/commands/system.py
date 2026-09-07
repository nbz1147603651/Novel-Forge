"""System-oriented CLI commands: healthcheck, show-params, init-config."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from novel_forge.cli.runtime_config import (
    default_config_path,
    default_config_template,
)
from novel_forge.cli.runtime_helpers import (
    _build_router,
    _get_storage,
)
from novel_forge.core.config import get_settings
from novel_forge.core.constants import TaskType
from novel_forge.core.domain.story_defaults import (
    DEFAULT_GENRE,
    DEFAULT_LONG_PREMISE,
    DEFAULT_SHORT_THEME,
    DEFAULT_TONE,
)
from novel_forge.gateway.types import ModelRequest
from novel_forge.prompts.builder import PromptBuilder

console = Console()


ParamRow = tuple[str, str, str]
ExampleRow = tuple[str, str]


_SHOW_PARAMS_SHORT_ROWS: tuple[ParamRow, ...] = (
    ("--theme", f'"{DEFAULT_SHORT_THEME}"', "【最小必填】故事主题/核心创意。"),
    ("--genre", DEFAULT_GENRE, "【最小必填】题材类型，如 fantasy/scifi/romance。"),
    ("--tone", DEFAULT_TONE, "【最小必填】叙事基调，如 dark/lyrical/suspenseful。"),
    ("--length", "3000", "【最小必填】目标字数。"),
    ("--title", "空", "【进阶可选】作品暂定名。"),
    ("--language", "zh", "【进阶可选】输出语言代码（zh/en）。"),
    ("--characters-hint", "空", "【进阶可选】角色提示（性格+动机）。"),
    ("--world-hint", "空", "【进阶可选】世界观/场景提示。"),
    ("--conflict-hint", "空", "【进阶可选】核心冲突（建议外部+内在）。"),
    ("--pov-hint", "空", "【进阶可选】叙事视角提示。"),
    ("--opening-style", "空", "【进阶可选】开篇方式提示。"),
    ("--ending-style", "空", "【进阶可选】结尾方式提示。"),
    ("--extra-instructions", "空", "【进阶可选】额外写作约束。"),
    ("--edit-rounds\n-e", "3", "【运行控制】编辑轮次；设为 0 跳过编辑。"),
    ("--project-id", "自动生成", "【运行控制】项目 ID；留空自动生成。"),
    ("--mock", "False", "【运行控制】Mock 调试模式。"),
    ("--verbose\n-v", "False", "【运行控制】显示详细步骤输出。"),
    ("--config", "自动探测", "【运行控制】指定运行配置 JSON。"),
)


_SHOW_PARAMS_INIT_ROWS: tuple[ParamRow, ...] = (
    ("--premise", f'"{DEFAULT_LONG_PREMISE}"', "【最小必填】长篇核心前提/世界观梗概。"),
    ("--genre", DEFAULT_GENRE, "【最小必填】题材类型。"),
    ("--tone", DEFAULT_TONE, "【最小必填】叙事基调。"),
    ("--title", "空", "【进阶可选】作品暂定名。"),
    ("--language", "zh", "【进阶可选】输出语言代码（zh/en）。"),
    ("--characters-hint", "空", "【进阶可选】角色提示（主角群/关键配角）。"),
    ("--world-hint", "空", "【进阶可选】世界观提示（时代/地域/规则/文化）。"),
    ("--conflict-hint", "空", "【进阶可选】核心冲突提示（外部+内在）。"),
    ("--pov-hint", "空", "【进阶可选】叙事视角提示。"),
    ("--opening-style", "空", "【进阶可选】开篇方式提示。"),
    ("--ending-style", "空", "【进阶可选】结尾方向提示。"),
    ("--extra-instructions", "空", "【进阶可选】额外创作约束。"),
    ("--total-chapters", "20", "【最小必填】总章节数。"),
    ("--words-per-chapter", "3000", "【最小必填】每章目标字数。"),
    ("--volume-mode", "auto", "【运行控制】分卷模式：auto/on/off。"),
    ("--chapters-per-volume", "0", "【运行控制】每卷章节数；0 表示自动。"),
    ("--edit-rounds\n-e", "2", "【运行控制】后续 run-chapter 默认编辑轮次。"),
    ("--project-id", "自动生成", "【运行控制】项目 ID；留空自动生成。"),
    ("--mock", "False", "【运行控制】Mock 调试模式。"),
    ("--verbose\n-v", "False", "【运行控制】显示详细初始化过程。"),
    ("--config", "自动探测", "【运行控制】指定运行配置 JSON。"),
)


_SHOW_PARAMS_CHAPTER_ROWS: tuple[ParamRow, ...] = (
    (
        "--project-id",
        "（必填）",
        "已初始化的项目 ID。必须先运行 init-long 创建项目后，才能使用此命令生成章节。",
    ),
    ("--chapter", "自动推断下一章", "要生成的章节编号。未传时自动推断下一章；传 0 也会触发自动推断。"),
    ("--edit-rounds\n-e", "2", "本章编辑轮次（覆盖 init-long 的默认值）。质量分 ≥8.0 时自动早停。"),
    ("--auto\n-a", "False", "自动连续生成后续章节（直到大纲末章或 --end-chapter）。"),
    ("--end-chapter", "0", "自动模式结束章节（0=使用 outline.total_chapters）。"),
    (
        "--ai-judge-apply-mode",
        "assist",
        "AI Judge 应用策略：assist（先给建议再确认）/ trust（自动应用）。",
    ),
    ("--mock", "False", "强制使用 Mock 模式。⚠️ 必须与 init-long 时的模式保持一致！"),
    ("--verbose\n-v", "False", "显示详细过程。展示 Retrieve/Plan/Draft/Edit/Extract/Evaluate 每步输出和创作报告。"),
    ("--config", "自动探测", "运行参数配置文件（JSON）。可在配置中预设 project_id/chapter/edit_rounds 等参数。"),
)


_SHOW_PARAMS_EXAMPLES: tuple[ExampleRow, ...] = (
    (
        "🎯 快速生成短篇",
        'novel-forge run-short --theme "失忆青年在异常小镇的探索" --genre mystery --tone mysterious --length 5000 -e 2',
    ),
    (
        "📚 初始化长篇项目",
        'novel-forge init-long --premise "末日后的地下城市" --genre scifi --tone dark --total-chapters 15 --words-per-chapter 4000 --project-id underground',
    ),
    ("📖 生成第一章", "novel-forge run-chapter --project-id underground --chapter 1 -e 2 -v"),
    ("🔄 继续后续章节", "novel-forge run-chapter --project-id underground --chapter 2"),
    ("🔍 查看详细过程", 'novel-forge run-short --theme "雨夜车站的一次重逢" -v'),
    ("🧪 测试流水线", 'novel-forge run-short --theme "测试故事" --mock'),
    ("⚙️  初始化配置文件", "novel-forge init-config --path novel_forge.cli.json"),
    ("📁 使用配置文件运行", "novel-forge run-chapter --config novel_forge.cli.json --chapter 12"),
)


_SHOW_PARAMS_NOTES = (
    "[bold yellow]📋 重要提示[/bold yellow]\n\n"
    "• [cyan]genre 可选值[/cyan]：fantasy, scifi, mystery, romance, literary, horror, thriller, historical 等\n"
    "• [cyan]tone 可选值[/cyan]：epic, dark, humorous, lyrical, suspenseful, pessimistic, optimistic, neutral 等\n"
    "• [cyan]智能早停[/cyan]：编辑轮次中若质量分达到 8.0，自动跳过剩余轮次，节省成本\n"
    "• [cyan]断点续跑[/cyan]：init-long 支持从中断处恢复，已完成步骤自动跳过\n"
    "• [cyan]中断控制[/cyan]：随时按 Ctrl+C 优雅中断，已完成的工作会自动保存\n"
    "• [cyan]Mock 隔离[/cyan]：Mock 模式和真实模式数据不兼容，切勿在同一项目中混用\n"
    "• [cyan]运行配置[/cyan]：`init-config` 可生成模板；命令会自动读取 `novel_forge.cli.json`\n"
    "• [cyan]查看帮助[/cyan]：novel-forge run-short --help 或 novel-forge init-long --help\n"
    "• [cyan]系统健康检查[/cyan]：novel-forge healthcheck 查看适配器连通性和配置状态"
)


def _build_show_params_table(*, title: str, rows: tuple[ParamRow, ...]) -> Table:
    table = Table(title=title, show_lines=True, expand=True)
    table.add_column("参数名", style="cyan", no_wrap=True)
    table.add_column("默认值", style="yellow", no_wrap=True)
    table.add_column("说明", style="white")
    for name, default, desc in rows:
        table.add_row(name, default, desc)
    return table


def _build_examples_table(*, title: str, rows: tuple[ExampleRow, ...]) -> Table:
    table = Table(title=title, show_lines=True, expand=True)
    table.add_column("场景", style="cyan bold", no_wrap=True)
    table.add_column("命令示例", style="green")
    for scene, command in rows:
        table.add_row(scene, command)
    return table


# ── Command implementations ──────────────────────────────


def healthcheck(mock: bool = typer.Option(False, "--mock", help="Force mock mode (for testing/debugging)")) -> None:
    """Check system health — adapters, storage, templates."""

    def _mask_key(key: str) -> str:
        """Mask an API key for display: sk-abc...xyz"""
        if not key or key.startswith("sk-...") or key == "sk-ant-...":
            return "[dim]未配置[/dim]"
        if len(key) <= 10:
            return key[:3] + "***"
        return key[:6] + "..." + key[-4:]

    async def _check() -> None:
        settings = get_settings()
        router = _build_router(mock=mock)
        builder = PromptBuilder()
        storage = _get_storage()

        # ── 1. Configuration overview ──
        config_table = Table(title="📋 配置概览", show_lines=True)
        config_table.add_column("配置项", style="cyan", min_width=20)
        config_table.add_column("值", style="white")

        from novel_forge.core.config import _find_env_file
        env_file = _find_env_file()
        config_table.add_row("环境文件", f"[green]{env_file}[/green]" if env_file else "[red]未找到[/red]")

        key_info = [
            ("DeepSeek", settings.deepseek_api_key),
            ("通义千问 (Tongyi)", settings.tongyi_api_key),
            ("Kimi (Moonshot)", settings.kimi_api_key),
            ("OpenAI", settings.openai_api_key),
            ("Anthropic", settings.anthropic_api_key),
        ]
        for name, key in key_info:
            masked = _mask_key(key)
            if "未配置" in masked:
                config_table.add_row(f"{name} API Key", f"[dim]{masked}[/dim]")
            else:
                config_table.add_row(f"{name} API Key", f"[green]{masked}[/green]")

        mode_str = "[yellow]Mock 模式[/yellow]" if mock else "[green]真实模式[/green]"
        config_table.add_row("运行模式", mode_str)
        default_prov = router.default_provider
        config_table.add_row("默认提供商", f"[bold]{default_prov}[/bold]")

        if settings.task_routing:
            config_table.add_row("任务路由", "[green]已配置[/green]")
        else:
            config_table.add_row("任务路由", "[dim]未配置（使用默认 Tier 体系）[/dim]")

        console.print(config_table)
        console.print()

        # ── 2. Adapter health check ──
        adapter_table = Table(title="🔌 适配器连通性检查", show_lines=True)
        adapter_table.add_column("提供商", style="cyan", min_width=18)
        adapter_table.add_column("状态", min_width=10)
        adapter_table.add_column("默认模型", style="white")
        adapter_table.add_column("备注", style="dim")

        for name, adapter in router.adapters.items():
            model_info = adapter.default_model or ("mock-model" if name == "mock" else "")
            try:
                healthy = await adapter.health_check()
                if healthy:
                    status = "[green]✅ 连通[/green]"
                    note = ""
                else:
                    status = "[red]❌ 失败[/red]"
                    err_msg = adapter.last_health_error
                    if "401" in err_msg or "auth" in err_msg.lower() or "Incorrect API key" in err_msg:
                        note = "API Key 无效"
                    elif "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
                        note = "连接超时"
                    elif "connect" in err_msg.lower() or "network" in err_msg.lower():
                        note = "网络连接失败"
                    elif err_msg:
                        first_line = err_msg.split("\n")[0][:100]
                        note = first_line
                    else:
                        note = "认证失败或网络不通"
            except Exception as e:
                status = "[red]❌ 异常[/red]"
                note = str(e).split("\n")[0][:100]

            marker = " ⭐" if name == default_prov else ""
            adapter_table.add_row(f"{name}{marker}", status, model_info, note)

        console.print(adapter_table)
        console.print()

        # ── 3. Task routing details ──
        if router.task_route_overrides:
            route_table = Table(title="🗺️  任务路由映射", show_lines=True)
            route_table.add_column("任务", style="cyan")
            route_table.add_column("提供商", style="green")
            route_table.add_column("模型", style="white")
            route_table.add_column("联通性", min_width=10)
            route_table.add_column("备注", style="dim")

            for task_type, override in router.task_route_overrides.items():
                provider_name = override.provider
                model_id = override.model_id or "[dim]默认[/dim]"
                route_adapter = router.get_adapter(provider_name)
                if route_adapter:
                    try:
                        if override.model_id:
                            test_req = ModelRequest(
                                task_type=task_type,
                                messages=[{"role": "user", "content": "ping"}],
                                model_id=override.model_id,
                                max_tokens=1,
                                temperature=0.0,
                            )
                            await asyncio.wait_for(
                                route_adapter.complete(test_req),
                                timeout=20.0,
                            )
                            healthy = True
                        else:
                            healthy = await route_adapter.health_check()
                        if healthy:
                            status = "[green]✅ 可用[/green]"
                            note = "模型探测成功" if override.model_id else ""
                        else:
                            status = "[red]❌ 失败[/red]"
                            err_msg = route_adapter.last_health_error
                            if "401" in err_msg or "auth" in err_msg.lower():
                                note = "API Key 无效"
                            elif "timeout" in err_msg.lower():
                                note = "连接超时"
                            elif err_msg:
                                note = err_msg.split("\n")[0][:60]
                            else:
                                note = "认证或网络错误"
                    except Exception as e:
                        status = "[yellow]⚠️  异常[/yellow]"
                        err_msg = str(e)
                        if "model_not_found" in err_msg or "does not exist" in err_msg:
                            note = "模型不存在或无权限"
                        elif "401" in err_msg or "auth" in err_msg.lower():
                            note = "API Key 无效"
                        elif "timeout" in err_msg.lower():
                            note = "请求超时"
                        else:
                            note = err_msg.split("\n")[0][:60]
                else:
                    status = "[red]❌ 未找到[/red]"
                    note = f"适配器 '{provider_name}' 未注册"

                route_table.add_row(
                    task_type.value,
                    provider_name,
                    model_id,
                    status,
                    note,
                )
            console.print(route_table)
            console.print()

        # ── 4. Other components ──
        other_table = Table(title="🧩 其他组件", show_lines=True)
        other_table.add_column("组件", style="cyan", min_width=15)
        other_table.add_column("状态", style="green")

        other_table.add_row("存储目录", f"✅ {storage.root}")
        try:
            builder.build(
                TaskType.BEATS,
                {
                    "spec": {
                        "genre": DEFAULT_GENRE,
                        "theme": "test",
                        "tone": DEFAULT_TONE,
                        "length_target": 1000,
                        "language": "zh",
                        "characters_hint": "",
                        "world_hint": "",
                        "extra_instructions": "",
                    }
                },
            )
            other_table.add_row("提示词模板", "✅ OK")
        except Exception as e:
            other_table.add_row("提示词模板", f"❌ {e}")

        console.print(other_table)

    asyncio.run(_check())


def show_params() -> None:
    """显示短篇和长篇创作的所有参数说明（美观表格格式）。"""
    quick_start = Panel(
        "[bold cyan]短篇最小必填[/bold cyan]：`--theme --genre --tone --length`\n"
        "[bold cyan]长篇最小必填[/bold cyan]：`--premise --genre --tone --total-chapters --words-per-chapter`\n"
        "[dim]其余参数均可按需要逐步添加（角色/世界观/冲突/视角/开篇/结尾等）。[/dim]",
        title="⚡ 日常推荐用法",
        border_style="cyan",
    )
    console.print(quick_start)
    console.print()

    console.print(_build_show_params_table(title="📝 短篇模式 (run-short) 参数说明", rows=_SHOW_PARAMS_SHORT_ROWS))
    console.print()
    console.print(_build_show_params_table(title="📚 长篇初始化 (init-long) 参数说明", rows=_SHOW_PARAMS_INIT_ROWS))
    console.print()
    console.print(_build_show_params_table(title="📖 长篇章节生成 (run-chapter) 参数说明", rows=_SHOW_PARAMS_CHAPTER_ROWS))
    console.print()
    console.print(_build_examples_table(title="💡 完整使用示例", rows=_SHOW_PARAMS_EXAMPLES))
    console.print()

    notes_panel = Panel(_SHOW_PARAMS_NOTES, title="💡 使用建议", border_style="blue")
    console.print(notes_panel)


def init_config(
    path: str = typer.Option(
        str(default_config_path()),
        "--path",
        "-p",
        help="运行参数配置文件路径（JSON）",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="覆盖已存在的配置文件"),
) -> None:
    """Generate a CLI runtime config template JSON file."""
    target = Path(path).expanduser()
    if target.exists() and not force:
        raise typer.BadParameter(
            f"配置文件已存在: {target}（使用 --force 覆盖）",
            param_hint="--path",
        )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(default_config_template(), ensure_ascii=False, indent=2)
        target.write_text(payload + "\n", encoding="utf-8")
    except OSError as exc:
        raise typer.BadParameter(f"写入配置文件失败: {target} ({exc})", param_hint="--path") from exc
    console.print(
        Panel(
            f"已创建配置文件: [cyan]{target}[/cyan]\n"
            "运行命令会自动读取 `novel_forge.cli.json` 或使用 `--config` 指定路径。",
            title="[bold green]✅ CLI 配置已初始化[/bold green]",
            border_style="green",
        )
    )


def register(app: typer.Typer) -> None:
    """Register system and utility commands."""
    app.command(name="healthcheck")(healthcheck)
    app.command(name="show-params")(show_params)
    app.command(name="init-config")(init_config)
