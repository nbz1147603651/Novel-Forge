"""Error classification and presentation helpers for the CLI."""

from __future__ import annotations

import functools
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()


def _normalize_errmsg(exc: Exception) -> str:
    msg = str(exc).strip()
    if not msg:
        msg = f"{type(exc).__name__}"
    return " ".join(msg.split())


def _classify_cli_error(exc: Exception) -> tuple[str, str, list[str]]:
    """Classify raw exception into concise, actionable CLI message."""
    raw = _normalize_errmsg(exc)
    text = f"{type(exc).__name__}: {raw}".lower()

    if "requesttimeout" in text or "timed out" in text or "timeout" in text:
        return (
            "模型请求超时",
            "模型提供商在限定时间内未返回结果。",
            [
                "稍后重试同一命令（系统会尽量从断点继续）。",
                "在 .env 中切换可用的默认提供商或模型后重试。",
                "缩小本次任务规模（如减少章节数或每章字数）再试。",
            ],
        )

    if "401" in text or "api key" in text or "authentication" in text or "unauthorized" in text:
        return (
            "鉴权失败",
            "API Key 无效、缺失或未被当前提供商接受。",
            [
                "运行 `novel-forge healthcheck` 检查当前 key 状态。",
                "确认 .env 中对应提供商的 API Key 配置正确。",
                "确认该 key 对当前模型有访问权限。",
            ],
        )

    if "429" in text or "rate limit" in text or "too many requests" in text:
        return (
            "请求频率受限",
            "模型提供商触发了限流。",
            [
                "等待 30-60 秒后重试。",
                "减少并发请求或切换到负载更低的模型。",
            ],
        )

    if "model_not_found" in text or "does not exist" in text or "invalid model" in text:
        return (
            "模型不可用",
            "当前配置的模型不存在或无访问权限。",
            [
                "运行 `novel-forge healthcheck` 查看模型探测结果。",
                "修改模型配置为当前账号可用模型后重试。",
            ],
        )

    if "network" in text or "connection" in text or "dns" in text:
        return (
            "网络连接失败",
            "无法连接到模型服务端。",
            [
                "检查本机网络、代理和 DNS 设置。",
                "确认提供商服务状态正常后重试。",
            ],
        )

    return (
        f"{type(exc).__name__}",
        raw,
        [
            "可先运行 `novel-forge healthcheck` 定位配置或连通性问题。",
            "查看本次运行日志目录中的详情以进一步排查。",
        ],
    )


def _print_cli_error_panel(*, command: str, exc: Exception, run_dir: Path | None = None) -> None:
    title, reason, actions = _classify_cli_error(exc)
    lines = [
        f"[bold red]❌ {title}[/bold red]",
        f"[white]{reason}[/white]",
        "",
        f"[dim]命令: {command}[/dim]",
        f"[dim]异常类型: {type(exc).__name__}[/dim]",
    ]
    if run_dir is not None:
        lines.append(f"[dim]运行日志: {run_dir}[/dim]")
    lines.append("")
    lines.append("[bold]建议操作[/bold]")
    for idx, action in enumerate(actions, start=1):
        lines.append(f"{idx}. {action}")
    console.print(
        Panel(
            "\n".join(lines),
            title="⚠️ 命令执行失败",
            border_style="red",
        )
    )


def handle_cli_errors(func):
    """Decorator that wraps CLI commands for consistent error handling.

    Captures exceptions, renders a Rich error panel, and exits with code 1.
    Re-raises ``typer.Exit`` and ``typer.BadParameter`` without interception.
    """

    @functools.wraps(func)
    def wrapper(*args: object, **kwargs: object) -> None:
        try:
            return func(*args, **kwargs)
        except (typer.Exit, typer.BadParameter):
            raise
        except Exception as exc:
            _print_cli_error_panel(command=func.__name__, exc=exc)
            raise typer.Exit(code=1) from exc

    return wrapper


def _build_error_trace_summary(step_history: list[str]) -> dict[str, object] | None:
    """Build a compact progress summary for failed runs."""
    if not step_history:
        return None
    return {
        "completed_step_count": len(step_history),
        "completed_steps": step_history,
        "last_step": step_history[-1],
    }
