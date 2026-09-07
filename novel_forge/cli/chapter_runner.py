"""Chapter runner for CLI - handles single chapter execution.

This module contains the ChapterRunner class that encapsulates
all logic for running a single chapter generation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from rich.console import Console
from rich.panel import Panel

from novel_forge.cli.display import _make_step_callback
from novel_forge.cli.progress_helpers import (
    _compose_step_callbacks,
    _make_progress_tracker,
    _new_cli_progress,
)
from novel_forge.common.plot_guard import PlotGuardHandler
from novel_forge.obs.project_logger import ProjectRunLogger
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import canon_watermark, stale_chapter_cutoff
from novel_forge.pipeline.progress import cli_step_labels
from novel_forge.workspace import execution as _execution
from novel_forge.workspace.contracts import RunChapterRequest
from novel_forge.workspace.runtime import RuntimeServices

console = Console()
_logger = logging.getLogger(__name__)

_CHAPTER_PROGRESS_STEP_NAMES = cli_step_labels("run_chapter")


class ChapterRunner:
    """Handles execution of a single chapter."""

    def __init__(
        self,
        *,
        project_id: str,
        force: bool,
        mock: bool,
        verbose: bool,
        auto_mode: bool,
        ai_judge_apply_mode: str,
        run_logger: ProjectRunLogger | None,
    ):
        self.project_id = project_id
        self.force = force
        self.mock = mock
        self.verbose = verbose
        self.auto_mode = auto_mode
        self.ai_judge_apply_mode = ai_judge_apply_mode
        self.run_logger = run_logger

    async def run(
        self,
        chapter_number: int,
        *,
        runtime: RuntimeServices,
    ) -> Any:
        """Run a single chapter generation."""
        if self.run_logger is not None:
            self.run_logger.log_event("chapter_started", {"chapter": chapter_number})

        console.print(
            f"\n[bold cyan]📖 生成章节 {chapter_number}:[/bold cyan] {self.project_id}"
        )

        force_msg = (
            "[yellow]⚠️  强制重生成模式（将回滚 canon）[/yellow]"
            if self.force
            else ""
        )
        console.print(
            f"[dim]模式: {'Mock' if self.mock else '真实'} {force_msg}[/dim]\n"
        )

        # ── Pre-flight: word count vs model output capacity check ──
        if not self.mock and not await self._check_feasibility(chapter_number, runtime):
            console.print("[yellow]已取消生成。[/yellow]")
            return None

        # Calculate total steps for progress tracking
        total_steps = 1 + 1 + 1 + 2 + 1 + 1 + 1 + 1 + 1 + 4

        with _new_cli_progress(console) as progress:
            main_task = progress.add_task(
                f"[cyan]生成第 {chapter_number} 章...",
                total=total_steps,
            )
            progress_callback = _make_progress_tracker(
                progress=progress,
                task_id=main_task,
                step_names=_CHAPTER_PROGRESS_STEP_NAMES,
                verbose=self.verbose,
                verbose_callback_factory=_make_step_callback,
            )

            log_callback: Any = None
            if self.run_logger is not None:
                logger = self.run_logger
                log_callback = lambda step, data: logger.log_step(  # noqa: E731
                    step, data, chapter=chapter_number
                )

            track_step = _compose_step_callbacks(
                progress_callback,
                log_callback,
            )

            execution = await _execution.execute_run_chapter(
                runtime,
                RunChapterRequest(
                    project_id=self.project_id,
                    chapter_number=chapter_number,
                    force=self.force,
                ),
                on_step_progress=track_step,
            )
            result = execution.result

            progress.update(
                main_task,
                completed=total_steps,
                description=f"[bold green]✨ 第 {chapter_number} 章生成完成！",
            )

        self._display_result(chapter_number, result)

        if self.run_logger is not None:
            self.run_logger.log_event(
                "chapter_completed",
                {
                    "chapter": chapter_number,
                    "word_count": result.meta.word_count,
                    "score": (
                        result.eval_report.overall_score
                        if result.eval_report
                        else None
                    ),
                },
            )

        return result

    def _display_result(self, chapter_number: int, result: Any) -> None:
        """Display chapter generation result."""
        from novel_forge.cli.display import print_chapter_summary

        text = result.text
        display_text = text[:500] + "\n..." if len(text) > 500 else text

        score = (
            result.eval_report.overall_score
            if result.eval_report
            else "N/A"
        )

        console.print(
            Panel(
                display_text,
                title=f"[bold]Chapter {chapter_number} — {self.project_id}[/bold]",
                subtitle=f"Words: {result.meta.word_count} | Score: {score}/10",
            )
        )
        print_chapter_summary(result)

    async def _check_feasibility(
        self, chapter_number: int, runtime: RuntimeServices
    ) -> bool:
        """Pre-flight word count vs model output capacity check.

        Returns:
            ``True``  — proceed (either OK, or user confirmed in interactive mode).
            ``False`` — user cancelled (only possible in interactive mode).
        """
        import json as _json

        from novel_forge.common.constants import TaskType
        from novel_forge.gateway.profiles import check_draft_feasibility

        # 1. Read target word count from outline.json
        target_chars: int | None = None
        try:
            outline_path = (
                runtime.storage.root / self.project_id / "outline.json"
            )
            data = _json.loads(outline_path.read_text(encoding="utf-8"))
            for ch in data.get("chapters", []):
                if ch.get("chapter_number") == chapter_number:
                    raw = ch.get("expected_word_count")
                    target_chars = int(raw) if raw else None
                    break
        except Exception:
            return True  # can't read outline, don't block

        if not target_chars or target_chars <= 0:
            return True

        # 2. Get draft model from router
        model_id: str | None = None
        thinking = False
        override = runtime.router.task_route_overrides.get(TaskType.DRAFT_CHAPTER)
        if override and override.model_id:
            model_id = override.model_id
            thinking = override.thinking

        if model_id is None:
            return True

        # 3. Feasibility check
        level, message = check_draft_feasibility(model_id, target_chars, thinking=thinking)
        if level == "ok":
            return True

        # 4. Show warning
        border_color = "red" if level == "error" else "yellow"
        title = (
            "⚠️  字数超出上限警告" if level == "error" else "ℹ️  字数接近输出上限"
        )
        console.print(Panel(message, title=title, border_style=border_color))

        if self.auto_mode:
            # In auto mode, just warn and continue
            console.print("[dim]（自动模式：已忽略警告，继续生成）[/dim]")
            return True

        # Interactive confirmation
        console.print(
            "[yellow]是否继续生成？[/yellow] [bold]y[/bold] 继续  [bold]n[/bold] 取消  (默认: y) ",
            end="",
        )
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        return answer not in {"n", "no", "否"}


class AutoChapterRunner:
    """Handles automatic chapter generation across multiple chapters."""

    def __init__(
        self,
        *,
        project_id: str,
        start_chapter: int,
        end_chapter: int,
        force: bool,
        mock: bool,
        verbose: bool,
        ai_judge_apply_mode: str,
        run_logger: ProjectRunLogger | None,
    ):
        self.project_id = project_id
        self.start_chapter = start_chapter
        self.end_chapter = end_chapter
        self.force = force
        self.mock = mock
        self.verbose = verbose
        self.ai_judge_apply_mode = ai_judge_apply_mode
        self.run_logger = run_logger
        self.completed_chapters: list[int] = []
        self.chapter_trace_summaries: list[dict[str, Any]] = []
        self.current_chapter = start_chapter

    @staticmethod
    def _unique_chapter_numbers(chapters: list[Any]) -> list[int]:
        seen: set[int] = set()
        normalized: list[int] = []
        for raw in chapters:
            try:
                chapter = int(raw)
            except (TypeError, ValueError):
                continue
            if chapter < 1 or chapter in seen:
                continue
            seen.add(chapter)
            normalized.append(chapter)
        return sorted(normalized)

    def _mark_completed_chapter(self, chapter_number: int) -> None:
        if chapter_number not in self.completed_chapters:
            self.completed_chapters.append(chapter_number)
            self.completed_chapters.sort()

    async def _run_chapter_with_timeout(
        self,
        chapter_runner: ChapterRunner,
        chapter_number: int,
        runtime: RuntimeServices,
    ) -> Any:
        """Wrap single chapter execution with a configurable timeout.

        Prevents indefinite blocking when the underlying API call hangs
        (e.g. network partition, provider unresponsive). On timeout the
        chapter is treated as failed and auto-run stops gracefully.
        """
        timeout_s = int(
            getattr(runtime.settings, "long_auto_chapter_timeout_seconds", 3600) or 3600
        )
        try:
            return await asyncio.wait_for(
                chapter_runner.run(chapter_number, runtime=runtime),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            _logger.error(
                "CLI 连跑单章超时 | project=%s chapter=%d timeout=%ds",
                self.project_id,
                chapter_number,
                timeout_s,
            )
            console.print(
                f"[red]✖ 第 {chapter_number} 章生成超时（{timeout_s}s），"
                f"已停止自动连跑。请检查网络或 API 状态后重试。[/red]"
            )
            if self.run_logger is not None:
                self.run_logger.log_event(
                    "chapter_timeout",
                    {"chapter": chapter_number, "timeout_seconds": timeout_s},
                    level="ERROR",
                )
            return None

    def _existing_chapter_resume_state(
        self,
        layout: ProjectLayout,
        storage: Any | None,
        chapter_number: int,
        *,
        stale_cutoff: int | None,
    ) -> tuple[bool, dict[str, Any]]:
        """Return whether an existing chapter is safe to skip in auto-resume mode."""
        chapter_path = layout.chapter_path(chapter_number)
        exit_state_path = layout.chapter_exit_state_path(chapter_number)
        creative_report_path = layout.creative_report_path(chapter_number)

        def _exists(path: Any) -> bool:
            exists = getattr(storage, "exists", None)
            if callable(exists):
                return bool(exists(path))
            return bool(path.exists())

        files = {
            "chapter": _exists(chapter_path),
            "exit_state": _exists(exit_state_path),
            "creative_report": _exists(creative_report_path),
        }
        missing = [name for name, ok in files.items() if not ok]
        if missing:
            return False, {"reason": "missing_artifacts", "missing": missing, **files}

        if stale_cutoff is not None and chapter_number >= stale_cutoff:
            return False, {
                "reason": "stale_downstream",
                "stale_cutoff": stale_cutoff,
                **files,
            }

        if storage is not None:
            watermark = canon_watermark(storage, layout)
            if watermark < chapter_number:
                return False, {
                    "reason": "canon_watermark_behind",
                    "canon_watermark": watermark,
                    **files,
                }

        return True, {"reason": "complete", **files}

    async def run(
        self,
        *,
        runtime: RuntimeServices,
    ) -> dict[str, Any]:
        """Run automatic chapter generation."""
        layout = ProjectLayout(runtime.storage.project_path(self.project_id))

        # ── 加载上次中断的进度 ──────────────────────────────────────
        self._load_auto_run_progress(layout, runtime.storage)
        current_stale_cutoff = stale_chapter_cutoff(runtime.storage, layout)

        plot_guard_handler = PlotGuardHandler(
            layout=layout,
            storage=runtime.storage,
            router=runtime.router,
            builder=runtime.builder,
            settings=runtime.settings,
            ai_judge_apply_mode=self.ai_judge_apply_mode,
            auto_mode=True,
        )

        for chapter_number in range(self.start_chapter, self.end_chapter + 1):
            self.current_chapter = chapter_number

            # ── 断点续传：已完成章节自动跳过 ──────────────────────────────
            # 除了检查章节文件存在，还验证 exit_state 和 creative_report 文件完整，
            # 避免 Pipeline 中途崩溃（persist 前）导致的不完整章节被误跳过。
            if not self.force and layout.chapter_path(chapter_number).exists():
                can_skip, resume_state = self._existing_chapter_resume_state(
                    layout,
                    runtime.storage,
                    chapter_number,
                    stale_cutoff=current_stale_cutoff,
                )
                if can_skip:
                    console.print(
                        f"[dim]⏭️  第 {chapter_number} 章已存在，跳过（断点续传）[/dim]"
                    )
                    self._mark_completed_chapter(chapter_number)
                    self.chapter_trace_summaries.append(
                        {
                            "chapter": chapter_number,
                            "trace": {"skipped": True, "reason": "already_exists"},
                        }
                    )
                    # P0-2: 清理残留的 review_progress.json，避免后续单章运行时混淆
                    try:
                        from novel_forge.workspace.sessions.chapter_session_state import (
                            clear_review_progress,
                        )
                        clear_review_progress(runtime.storage, layout, chapter_number)
                    except Exception:
                        pass  # Best-effort cleanup
                    continue
                reason = resume_state.get("reason", "unknown")
                if reason == "missing_artifacts":
                    console.print(
                        f"[yellow]⚠️  第 {chapter_number} 章文件存在但辅助数据不完整"
                        f"（缺少：{', '.join(resume_state.get('missing', []))}），"
                        f"将重新生成[/yellow]"
                    )
                elif reason == "canon_watermark_behind":
                    watermark = resume_state.get("canon_watermark", 0)
                    console.print(
                        f"[yellow]⚠️  第 {chapter_number} 章文件存在，但 canon 仅到第 "
                        f"{watermark} 章，将重新生成以恢复状态链[/yellow]"
                    )
                elif reason == "stale_downstream":
                    console.print(
                        f"[yellow]⚠️  第 {chapter_number} 章位于失效链路内"
                        f"（最早失效第 {resume_state.get('stale_cutoff')} 章），将重新生成[/yellow]"
                    )
                else:
                    console.print(
                        f"[yellow]⚠️  第 {chapter_number} 章无法安全断点跳过（{reason}），"
                        f"将重新生成[/yellow]"
                    )

            chapter_runner = ChapterRunner(
                project_id=self.project_id,
                force=self.force,
                mock=self.mock,
                verbose=self.verbose,
                auto_mode=True,
                ai_judge_apply_mode=self.ai_judge_apply_mode,
                run_logger=self.run_logger,
            )

            result = await self._run_chapter_with_timeout(
                chapter_runner, chapter_number, runtime
            )
            if result is None:
                console.print(
                    f"[yellow]⏸️ 第 {chapter_number} 章未完成，自动模式已停止。[/yellow]"
                )
                break
            self._mark_completed_chapter(chapter_number)
            self.chapter_trace_summaries.append(
                {"chapter": chapter_number, "trace": result.trace_summary}
            )
            current_stale_cutoff = stale_chapter_cutoff(runtime.storage, layout)

            # ── 持久化进度：每章完成后写盘，防止意外中断丢失进度 ──
            self._save_auto_run_progress(layout, runtime.storage)

            should_continue = await plot_guard_handler.handle_major_deviation(
                chapter_number=chapter_number,
                result=result,
            )

            if not should_continue:
                if self.run_logger is not None:
                    self.run_logger.log_event(
                        "auto_generation_paused",
                        {"chapter": chapter_number, "reason": "plot_guard_pause"},
                        level="WARNING",
                    )
                console.print(
                    f"[yellow]⏸️ 已在第 {chapter_number} 章后暂停自动生成。"
                    f"你处理完偏离后可继续运行下一章。[/yellow]"
                )
                break

            # ── 章间冷却 + 下一章静态上下文预取 ─────────────────────────
            cooldown: int = getattr(
                runtime.settings, "long_auto_chapter_cooldown_seconds", 0
            )
            if chapter_number < self.end_chapter:
                # Pre-warm OS file cache for next chapter's static inputs.
                if bool(getattr(runtime.settings, "long_perf_chapter_prefetch_enabled", True)):
                    await self._prefetch_static_context(layout, runtime)
                if cooldown > 0:
                    console.print(
                        f"[dim]⏳ 章间冷却 {cooldown}s（第 {chapter_number} → {chapter_number + 1} 章）...[/dim]"
                    )
                    await asyncio.sleep(cooldown)

        # ── 全书一致性审计 ────────────────────────────────────────────
        # 在所有章节完成后，自动运行 BookConsistencyStep 进行全书级别审计
        if len(self.completed_chapters) >= 2:
            try:
                console.print(
                    "\n[bold cyan]📋 正在执行全书一致性审计...[/bold cyan]"
                )
                drain_shutdowns = getattr(runtime, "drain_memory_shutdowns", None)
                if callable(drain_shutdowns):
                    await drain_shutdowns()
                from novel_forge.workspace.contracts import BookConsistencyRequest

                audit_result = await _execution.execute_book_consistency(
                    runtime,
                    BookConsistencyRequest(
                        project_id=self.project_id,
                        chapter_range=sorted(set(self.completed_chapters)),
                    ),
                )
                audit = audit_result.result
                score = getattr(audit, "consistency_score", 0.0)
                issues = getattr(audit, "issues", []) or []
                console.print(
                    f"[green]✅ 全书一致性审计完成 — "
                    f"一致性分数: {score:.1f}/10 | "
                    f"发现 {len(issues)} 个问题[/green]"
                )
                if issues:
                    for iss in issues[:5]:
                        sev = getattr(iss, "severity", "info")
                        desc = getattr(iss, "description", str(iss))[:120]
                        console.print(f"  [{sev}]{sev.upper()}[/{sev}]: {desc}")
                    if len(issues) > 5:
                        console.print(f"  [dim]... 还有 {len(issues) - 5} 个问题，详见审计报告[/dim]")
            except Exception as exc:
                console.print(
                    f"[yellow]⚠️ 全书一致性审计失败（不影响已完成章节）：{exc}[/yellow]"
                )

        return {
            "completed_chapters": self.completed_chapters,
            "chapter_trace_summaries": self.chapter_trace_summaries,
            "last_chapter": (
                self.completed_chapters[-1] if self.completed_chapters else None
            ),
        }

    def _save_auto_run_progress(self, layout: ProjectLayout, storage: Any) -> None:
        """Save auto-run progress to disk for resume-from-interruption."""
        try:
            progress = {
                "project_id": self.project_id,
                "start_chapter": self.start_chapter,
                "end_chapter": self.end_chapter,
                "current_chapter": self.current_chapter,
                "completed_chapters": self._unique_chapter_numbers(self.completed_chapters),
            }
            layout.states_dir.mkdir(parents=True, exist_ok=True)
            if storage is not None and hasattr(storage, "save_json"):
                storage.save_json(layout.auto_run_progress_path, progress)
            else:
                import json as _json

                layout.auto_run_progress_path.write_text(
                    _json.dumps(progress, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception:
            _logger.warning(
                "CLI 连跑进度保存失败（不影响当前运行，但中断后可能无法恢复进度）",
                exc_info=True,
            )

    def _load_auto_run_progress(self, layout: ProjectLayout, storage: Any) -> None:
        """Load previous auto-run progress if available."""
        try:
            path = layout.auto_run_progress_path
            if not path.exists():
                return
            if storage is not None and hasattr(storage, "load_json"):
                data = storage.load_json(path)
            else:
                import json as _json

                data = _json.loads(path.read_text(encoding="utf-8"))
            if data.get("project_id") not in {None, self.project_id}:
                return
            prev_completed = data.get("completed_chapters", [])
            if prev_completed:
                restored = [
                    chapter
                    for chapter in self._unique_chapter_numbers(prev_completed)
                    if self.start_chapter <= chapter <= self.end_chapter
                ]
                if storage is not None:
                    current_stale_cutoff = stale_chapter_cutoff(storage, layout)
                    restored = [
                        chapter
                        for chapter in restored
                        if self._existing_chapter_resume_state(
                            layout,
                            storage,
                            chapter,
                            stale_cutoff=current_stale_cutoff,
                        )[0]
                    ]
                self.completed_chapters = restored
                if not self.completed_chapters:
                    return
                console.print(
                    f"[dim]📂 检测到上次连跑进度：已完成 {len(self.completed_chapters)} 章 "
                    f"（最后完成第 {max(self.completed_chapters)} 章）[/dim]"
                )
                missing = [
                    ch for ch in self.completed_chapters
                    if not layout.chapter_path(ch).exists()
                ]
                if missing:
                    console.print(
                        f"[yellow]⚠️  警告：已恢复的章节中有 {len(missing)} 章文件丢失："
                        f"{', '.join(str(m) for m in missing[:5])}"
                        f"{'...' if len(missing) > 5 else ''}[/yellow]"
                    )
        except Exception:
            _logger.warning(
                "CLI 连跑进度文件损坏或读取失败，将从头开始连跑",
                exc_info=True,
            )

    async def _prefetch_static_context(
        self,
        layout: ProjectLayout,
        runtime: RuntimeServices,
    ) -> None:
        """Pre-warm OS file cache with next chapter's static inputs.

        Reads static project files (outline, style_profile, character_bible,
        narrative_contract, blueprint) into the OS page cache so that the
        next chapter's Planning stage avoids cold-read latency.  This is
        fire-and-forget: failures are silently ignored.
        """
        static_paths = [
            layout.outline_path,
            layout.style_profile_path,
            layout.characters_path,
            layout.blueprint_path,
        ]
        # narrative_contract may live in a subdirectory
        nc_path = getattr(layout, "narrative_contract_path", None)
        if nc_path is not None:
            static_paths.append(nc_path)

        def _read_all() -> None:
            for path in static_paths:
                try:
                    if path.exists():
                        path.read_bytes()  # warm OS page cache
                except OSError:
                    pass

        try:
            await asyncio.to_thread(_read_all)
        except Exception:
            pass  # best-effort; never block the pipeline
