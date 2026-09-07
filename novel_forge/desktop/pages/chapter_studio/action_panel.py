"""Action-panel presenter for chapter studio."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QTextEdit, QVBoxLayout, QWidget

from novel_forge.core.review.review_orchestration import format_review_score_parts
from novel_forge.desktop.constants import JOB_STATUS_TONE
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.progress import display_step_name_for_job
from novel_forge.desktop.task_flow_errors import task_flow_error_recovery_text
from novel_forge.desktop.widgets import (
    ActionButton,
    Badge,
    CollapsibleSection,
    build_checkpoint_summary_text,
    clear_layout,
)
from novel_forge.desktop.workspace import DesktopWorkspaceSnapshot
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.contracts import (
    ChapterWorkspaceSnapshot,
    DecisionCheckpoint,
    DecisionOption,
)

# ── Network-error classification ──────────────────────────────────────────
_NETWORK_ERROR_MARKERS = (
    "modelsgatewayerror",
    "modelgatewayerror",
    "readerror",
    "read error",
    "readtimeout",
    "connecterror",
    "timeouterror",
    "timed out",
    "timeout",
    "remotepro",
    "connection",
    "network",
    "httpx",
    "asyncio",
    "服务不可用",
    "网络",
    "超时",
)


def _is_network_error(error: str) -> bool:
    """Return True when the error string looks like a transient network/timeout failure."""
    if not error:
        return False
    low = error.lower()
    return any(m in low for m in _NETWORK_ERROR_MARKERS)


def _is_upstream_semantic_block(job: DesktopJobRecord) -> bool:
    """Honor Engine semantic recovery ownership, including legacy source jobs."""

    summary = job.error_summary if isinstance(job.error_summary, dict) else {}
    context = summary.get("context")
    error_context = context if isinstance(context, dict) else {}
    violation_kind = str(
        error_context.get("violation_kind") or summary.get("cause_code") or ""
    )
    recovery_target = str(error_context.get("replan_target") or "manual")
    if violation_kind in {
        "upstream_source_conflict",
        "upstream_source_review_required",
        "upstream_semantic_not_ready",
    } and recovery_target in {"manual", "semantic"}:
        return True
    legacy_source_marker = ""
    if "审查上游的" in job.error and "大纲=[" in job.error:
        legacy_source_marker = job.error.split("大纲=[", 1)[1].split("]", 1)[0]
    return "," in legacy_source_marker


def _latest_replan_reason(job: DesktopJobRecord) -> str:
    """Extract consistency-replan reason from recent job events when available."""
    for event in reversed(job.events):
        if event.step != "consistency_replan":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        reason = str(payload.get("reason") or payload.get("message") or "").strip()
        if reason:
            return reason
        violations = payload.get("violations")
        if isinstance(violations, list):
            for item in violations:
                text = str(item or "").strip()
                if text:
                    return text
    return ""


@dataclass(frozen=True)
class ActionPanelRenderContext:
    """Read-only state needed to render the chapter-studio action panel."""

    mode: str
    auto_started: bool
    stopped_from_auto: bool
    latest_job: DesktopJobRecord | None
    studio: ChapterWorkspaceSnapshot | None
    workspace: DesktopWorkspaceSnapshot | None
    current_chapter_done: bool
    notes_expanded: bool
    resume_auto_label: str
    # 当前章节为「失效」状态且含旧版存档文字（word_count > 0）
    current_chapter_stale_with_content: bool = False
    current_chapter_word_count: int = 0  # 失效章节的旧版存档字数
    # 过滤「已清理」点击后屏蔽的警告，后左中间面板展示实际有效条数
    effective_warning_count: int = 0
    # 定时重试：剩余秒数；<= 0 表示未排程
    retry_countdown_secs: int = -1
    downstream_chapter_count: int = 0


class ChapterStudioActionPanelPresenter:
    """Render chapter-studio action panel widgets from page state + callbacks."""

    def __init__(
        self,
        *,
        action_title: QLabel,
        action_summary: QLabel,
        action_badge: Badge,
        job_hint: QLabel,
        ai_suggestion_frame: QWidget,
        ai_suggestion_label: QLabel,
        notes_section: CollapsibleSection,
        notes: QTextEdit,
        rewrite_strategy_row: QWidget,
        action_buttons: QVBoxLayout,
        on_submit_prepare: Callable[..., None],
        on_cancel_running_job: Callable[[], None],
        on_stop_auto_pilot: Callable[[], None],
        on_start_auto_pilot: Callable[[], None],
        on_switch_to_suggest: Callable[[], None],
        on_handle_checkpoint_option: Callable[[DecisionOption], None],
        on_resume_from_progress: Callable[..., None],
        on_resume_auto_pilot: Callable[[], None],
        on_submit_regen_with_continuity_check: Callable[[], None],
        on_submit_polish_with_continuity_check: Callable[[], None],
        on_go_to_next_chapter: Callable[[], None],
        on_submit_book_consistency: Callable[[], None] = lambda: None,
        on_submit_export: Callable[[], None] = lambda: None,
        on_schedule_retry: Callable[[int], None] = lambda secs: None,
        on_cancel_retry: Callable[[], None] = lambda: None,
        on_show_checkpoint_dialog: Callable[[DecisionCheckpoint, str], None] | None = None,
        is_checkpoint_dialog_dismissed: Callable[[DecisionCheckpoint], bool] | None = None,
    ) -> None:
        self._action_title = action_title
        self._action_summary = action_summary
        self._action_badge = action_badge
        self._job_hint = job_hint
        self._ai_suggestion_frame = ai_suggestion_frame
        self._ai_suggestion_label = ai_suggestion_label
        self._notes_section = notes_section
        self._notes = notes
        self._rewrite_strategy_row = rewrite_strategy_row
        self._action_buttons = action_buttons

        self._on_submit_prepare = on_submit_prepare
        self._on_cancel_running_job = on_cancel_running_job
        self._on_stop_auto_pilot = on_stop_auto_pilot
        self._on_start_auto_pilot = on_start_auto_pilot
        self._on_switch_to_suggest = on_switch_to_suggest
        self._on_handle_checkpoint_option = on_handle_checkpoint_option
        self._on_resume_from_progress = on_resume_from_progress
        self._on_resume_auto_pilot = on_resume_auto_pilot
        self._on_submit_regen_with_continuity_check = on_submit_regen_with_continuity_check
        self._on_submit_polish_with_continuity_check = on_submit_polish_with_continuity_check
        self._on_go_to_next_chapter = on_go_to_next_chapter
        self._on_submit_book_consistency = on_submit_book_consistency
        self._on_submit_export = on_submit_export
        self._on_schedule_retry = on_schedule_retry
        self._on_cancel_retry = on_cancel_retry
        self._on_show_checkpoint_dialog = on_show_checkpoint_dialog
        self._is_checkpoint_dialog_dismissed = is_checkpoint_dialog_dismissed

        # Track checkpoint IDs that the user has manually dismissed the dialog for.
        # Prevents re-popping the dialog on subsequent render() calls.
        self._dismissed_checkpoint_ids: set[str] = set()

    def render(self, context: ActionPanelRenderContext) -> None:
        """Render the action panel based on the supplied page context."""
        clear_layout(self._action_buttons)
        is_auto = context.mode in {"auto", "book_auto"}
        is_book_auto = context.mode == "book_auto"
        is_suggest = context.mode == "suggest"
        auto_label = "章节连跑" if is_book_auto else "本章自动"

        self._ai_suggestion_frame.setVisible(False)
        self._rewrite_strategy_row.setVisible(False)
        self._notes_section.set_title("编写备注与补充约束")
        self._notes.setPlaceholderText("在这里写本章补充约束、方案备注，或对 AI 选项的人工说明。")

        if self._render_job_state(context, is_auto=is_auto, auto_label=auto_label):
            return

        auto_idle = is_auto and context.auto_started
        self._action_badge.setText(f"{auto_label}中" if auto_idle else "待命")
        self._action_badge.set_tone("warning" if auto_idle else "default")
        self._job_hint.setText("章节工作台当前无运行中任务。")

        if context.studio is None:
            self._render_unloaded_state()
            return

        if context.studio.pending_checkpoint is not None:
            self._render_checkpoint_state(
                context,
                checkpoint=context.studio.pending_checkpoint,
                is_auto=is_auto,
                is_suggest=is_suggest,
                auto_label=auto_label,
            )
            return

        if context.current_chapter_done:
            self._render_done_state(context, is_auto=is_auto, auto_label=auto_label)
            return

        if context.current_chapter_stale_with_content:
            self._render_stale_chapter_state(
                context, is_auto=is_auto, is_book_auto=is_book_auto, auto_label=auto_label
            )
            return

        self._render_prepare_state(
            context,
            is_auto=is_auto,
            is_book_auto=is_book_auto,
            auto_label=auto_label,
        )

    def _render_job_state(
        self,
        context: ActionPanelRenderContext,
        *,
        is_auto: bool,
        auto_label: str,
    ) -> bool:
        job = context.latest_job
        if job is None:
            return False

        auto_active = is_auto and context.auto_started
        badge_text = {
            DesktopJobState.QUEUED: f"{auto_label}中" if auto_active else "排队中",
            DesktopJobState.RUNNING: f"{auto_label}中" if auto_active else "执行中",
            DesktopJobState.PAUSED: "自动中" if auto_active else "等待决策",
            DesktopJobState.SUCCEEDED: "已完成",
            DesktopJobState.FAILED: "失败",
        }
        tone = JOB_STATUS_TONE.get(job.status, "default")
        self._action_badge.setText(badge_text.get(job.status, job.status))
        self._action_badge.set_tone(tone)
        step_display = display_step_name_for_job(job)
        self._job_hint.setText(f"最近任务：{job.label} · 当前步骤：{step_display}")

        if job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}:
            self._notes_section.setVisible(False)
            replan_reason = _latest_replan_reason(job)
            if not replan_reason and any(
                event.step == "consistency_replan" for event in job.events
            ):
                replan_reason = "上一轮未通过质量校验，系统正在自动重规划后重试。"
            reason_suffix = f"\n\n触发原因：{replan_reason}" if replan_reason else ""
            if auto_active:
                self._action_title.setText(f"{auto_label}推进中…")
                self._action_summary.setText(
                    f"正在执行：{job.label}\n步骤：{step_display}{reason_suffix}"
                )
                stop_btn = ActionButton(f"⏹ 停止{auto_label}", variant="danger")
                stop_btn.clicked.connect(self._on_stop_auto_pilot)
                self._add_btn_row(stop_btn)
            else:
                self._action_title.setText("正在执行…")
                self._action_summary.setText(
                    f"{job.label}\n步骤：{step_display}，请稍候。{reason_suffix}"
                )
                cancel_btn = ActionButton("⏹ 取消任务", variant="secondary")
                cancel_btn.clicked.connect(self._on_cancel_running_job)
                self._add_btn_row(cancel_btn)
            return True

        # PAUSED job without a visible studio checkpoint means the workspace has
        # just written a new checkpoint and the studio snapshot hasn't refreshed
        # yet.  Show a brief "syncing" state rather than falling through to the
        # misleading stale-chapter text ("正在重新生成本章").
        if (
            job.status == DesktopJobState.PAUSED
            and context.studio is not None
            and context.studio.pending_checkpoint is None
        ):
            self._notes_section.setVisible(False)
            if auto_active:
                self._action_title.setText(f"{auto_label}推进中…")
                self._action_summary.setText(f"已完成「{job.label}」，正在同步决策方案…")
                stop_btn = ActionButton(f"⏹ 停止{auto_label}", variant="danger")
                stop_btn.clicked.connect(self._on_stop_auto_pilot)
                self._add_btn_row(stop_btn)
            else:
                self._action_title.setText("等待同步")
                self._action_summary.setText(f"「{job.label}」已完成，正在载入决策方案，请稍候…")
            return True

        if job.status == DesktopJobState.FAILED:
            return self._render_failed_job_state(
                context, job=job, is_auto=is_auto, auto_label=auto_label
            )

        return False

    def _render_failed_job_state(
        self,
        context: ActionPanelRenderContext,
        *,
        job: DesktopJobRecord,
        is_auto: bool,
        auto_label: str,
    ) -> bool:
        # 如果失败原因是 Canon 丢失，但当前 canon 文件已经存在（说明用户已重新初始化），
        # 则不再展示旧错误，直接降级为正常「准备方案」状态，避免误导用户以为还需要再次初始化。
        if (
            job.error
            and "canon_current.json" in job.error
            and context.workspace is not None
            and context.studio is not None
        ):
            canon_path = (
                context.workspace.storage_root
                / context.studio.project_id
                / "canon"
                / "canon_current.json"
            )
            if canon_path.exists():
                self._render_unloaded_state()
                return True

        optional_job = job.kind in {"repair_continuity", "polish_chapter", "book_consistency"}
        auto_was_running = is_auto and context.auto_started
        skip_failure_ui = (
            context.studio is not None
            and context.current_chapter_done
            and (optional_job or not auto_was_running)
        )
        if skip_failure_ui:
            return False

        if _is_upstream_semantic_block(job):
            self._action_title.setText("上游语义一致性需处理")
            self._action_summary.setText(
                "权威来源未通过预编译语义门禁，章节 provider 调用已停止。\n\n"
                f"错误：{(job.error or '上游语义未就绪')[:320]}\n\n"
                "请在 NIMO 共创侧栏/修复工作台刷新、确认证据或审批来源修订；"
                "直接重做章节 Plan 不会绕过源问题。"
            )
            self._notes_section.setVisible(True)
            blocked = ActionButton("请先处理上游语义", variant="secondary")
            blocked.setEnabled(False)
            blocked.setToolTip("使用 NIMO 查看语义证据和版本化候选；PySide 只同步安全阻断。")
            self._add_btn_row(blocked)
            return True

        checkpoint = context.studio.pending_checkpoint if context.studio else None
        if is_auto and context.auto_started:
            # User-cancelled jobs (断点续传) should not stop the autopilot.
            # Render "continuing" UI and let decide_autopilot_action choose the next step.
            if job.current_step == "cancelled":
                self._action_title.setText(f"{auto_label}推进中…")
                self._action_summary.setText("任务已被用户取消，正在重新规划…")
                stop_btn = ActionButton(f"⏹ 停止{auto_label}", variant="danger")
                stop_btn.clicked.connect(self._on_stop_auto_pilot)
                self._add_btn_row(stop_btn)
                return True
            has_recommended = checkpoint is not None and any(
                option.is_recommended for option in (checkpoint.options or [])
            )
            if has_recommended and job.kind == "repair_continuity":
                self._action_title.setText(f"{auto_label}推进中…")
                self._action_summary.setText(
                    f"上次「{job.label}」失败，已跳过，正在自动继续…\n"
                    f"错误提示：{(job.error or '未知错误')[:120]}"
                )
                stop_btn = ActionButton(f"⏹ 停止{auto_label}", variant="danger")
                stop_btn.clicked.connect(self._on_stop_auto_pilot)
                self._add_btn_row(stop_btn)
                return True
            if has_recommended:
                self._action_title.setText(f"{auto_label}推进中…")
                self._action_summary.setText(
                    f"上次「{job.label}」失败，AI 将自动处理现有检查点…\n"
                    f"错误提示：{(job.error or '未知错误')[:120]}"
                )
                stop_btn = ActionButton(f"⏹ 停止{auto_label}", variant="danger")
                stop_btn.clicked.connect(self._on_stop_auto_pilot)
                self._add_btn_row(stop_btn)
                return True
            # Auto-resume from progress if available
            if context.studio and context.studio.has_review_progress and checkpoint is not None:
                self._action_title.setText(f"{auto_label}推进中…")
                self._action_summary.setText(
                    f"上次「{job.label}」失败，正在从断点自动恢复…\n"
                    f"错误提示：{(job.error or '未知错误')[:120]}"
                )
                stop_btn = ActionButton(f"⏹ 停止{auto_label}", variant="danger")
                stop_btn.clicked.connect(self._on_stop_auto_pilot)
                self._add_btn_row(stop_btn)
                self._on_resume_from_progress(checkpoint)
                return True
            self._on_stop_auto_pilot()
            return True

        self._action_title.setText(
            "章节方案待确认"
            if (not checkpoint or checkpoint.checkpoint_type == "plan_checkpoint")
            else "章节归档待确认"
        )
        err_summary = job.error or ""
        err_detail = task_flow_error_recovery_text(job.error_summary)
        err_display = err_summary
        if err_detail and err_detail not in err_summary:
            err_display = err_summary + f"\n\n详情：{err_detail[:180]}"

        # ── 定时重试（网络错误）──────────────────────────────────
        is_net_err = _is_network_error(err_display)
        if is_net_err and context.retry_countdown_secs > 0:
            # 显示倒计时状态
            mins = context.retry_countdown_secs // 60
            secs = context.retry_countdown_secs % 60
            countdown_str = f"{mins}:{secs:02d}" if mins else f"{secs}s"
            self._action_title.setText("网络错误 · 定时重试中")
            self._action_summary.setText(
                f"网络连接失败，将在 {countdown_str} 后自动重试。\n\n错误：{err_display[:200]}"
            )
            cancel_btn = ActionButton("✕ 取消定时重试", variant="secondary")
            cancel_btn.clicked.connect(lambda: self._on_cancel_retry())
            self._add_btn_row(cancel_btn)
            return True

        self._action_summary.setText(
            f"上次执行失败：\n{err_display[:400]}\n\n可重新尝试或调整参数后再试。"
        )
        self._notes_section.setVisible(True)

        # ── 网络错误：提供定时重试选项 ────────────────────────────
        if is_net_err:
            self._action_title.setText("网络错误 · 执行失败")
            self._action_summary.setText(
                f"检测到网络/超时错误，可等待一段时间后自动重试。\n\n错误：{err_display[:200]}"
            )
            delay_row_1 = QHBoxLayout()
            delay_row_1.setSpacing(6)
            for label, secs in (("10 分钟", 600), ("20 分钟", 1200)):
                btn = ActionButton(f"⏱ {label}后重试", variant="secondary")
                btn.clicked.connect(lambda checked=False, s=secs: self._on_schedule_retry(s))
                delay_row_1.addWidget(btn)
            delay_row_1.addStretch()
            self._action_buttons.addLayout(delay_row_1)

            delay_row_2 = QHBoxLayout()
            delay_row_2.setSpacing(6)
            for label, secs in (("30 分钟", 1800), ("1 小时", 3600)):
                btn = ActionButton(f"⏱ {label}后重试", variant="secondary")
                btn.clicked.connect(lambda checked=False, s=secs: self._on_schedule_retry(s))
                delay_row_2.addWidget(btn)
            delay_row_2.addStretch()
            self._action_buttons.addLayout(delay_row_2)

        if checkpoint:
            self._render_checkpoint_buttons(checkpoint, is_manual=context.mode == "manual")
        else:
            retry = ActionButton("立即重试 →" if is_net_err else "重新准备方案 →")
            retry.clicked.connect(lambda: self._on_submit_prepare())
            self._add_btn_row(retry)
        # ── 断点续传按钮 ──
        if context.studio and context.studio.has_review_progress and checkpoint:
            _stage_label = {
                "draft_done": "初稿成章已完成，跳过 DRAFT/WAVE",
                "quality_done": "质量检查已完成，跳过检查与修复",
                "repair_done": "因果修复已完成，跳过全部检查",
            }.get(context.studio.review_progress_stage, "部分步骤已完成")
            resume_btn = ActionButton("从断点恢复 →", variant="primary")
            resume_btn.setToolTip(f"上次进度：{_stage_label}。从上次失败的步骤继续执行。")
            resume_btn.clicked.connect(lambda: self._on_resume_from_progress(checkpoint))
            self._add_btn_row(resume_btn)
        self._append_resume_button(context)
        return True

    def _render_unloaded_state(self) -> None:
        self._action_title.setText("准备章节方案")
        self._action_summary.setText("载入一个长篇项目后，即可先生成章节方案。")
        self._notes_section.setVisible(True)
        launch = ActionButton("准备章节方案 →")
        launch.clicked.connect(lambda: self._on_submit_prepare())
        self._add_btn_row(launch)

    def _render_checkpoint_state(
        self,
        context: ActionPanelRenderContext,
        *,
        checkpoint: DecisionCheckpoint,
        is_auto: bool,
        is_suggest: bool,
        auto_label: str,
    ) -> None:
        self._action_title.setText(
            "章节方案待确认"
            if checkpoint.checkpoint_type == "plan_checkpoint"
            else "章节归档待确认"
        )
        studio_warnings: list[str] = context.studio.warnings if context.studio else []
        summary_text = build_checkpoint_summary_text(
            checkpoint,
            studio_warnings=studio_warnings,
        )
        self._action_summary.setText(summary_text)
        recommended = next((option for option in checkpoint.options if option.is_recommended), None)

        if is_auto:
            if context.auto_started:
                self._notes_section.setVisible(False)
                if recommended:
                    self._action_title.setText(f"{auto_label}推进中…")
                    self._action_summary.setText(
                        f"{summary_text}\nAI 正在确认：{recommended.label}"
                    )
                    stop_btn = ActionButton(f"⏹ 停止{auto_label}", variant="danger")
                    stop_btn.clicked.connect(self._on_stop_auto_pilot)
                    self._add_btn_row(stop_btn)
                else:
                    self._on_stop_auto_pilot()
                return

            self._notes_section.setVisible(False)
            self._action_badge.setText("待启动")
            self._action_badge.set_tone("default")
            if recommended:
                self._action_summary.setText(
                    f"{summary_text}\n\n"
                    f"AI 预备方案：《{recommended.label}》\n"
                    f"{recommended.description or ''}"
                )
            start_btn = ActionButton(f"启动{auto_label} ▶", variant="primary")
            start_btn.clicked.connect(self._on_start_auto_pilot)
            switch_btn = ActionButton("手动处理此节点", variant="secondary")
            switch_btn.clicked.connect(self._on_switch_to_suggest)
            self._add_btn_row(start_btn, switch_btn)
            return

        # book_auto/auto 刚停止（stopped_from_auto=True, mode 已切回 manual）：
        # 遗留的 checkpoint 不应弹出伴随模式浮窗，以内联方式展示选项 + 恢复连跑按钮。
        if context.stopped_from_auto:
            self._notes_section.setVisible(True)
            if recommended and recommended.description:
                self._ai_suggestion_label.setText(recommended.description)
                self._ai_suggestion_frame.setVisible(True)
            self._render_checkpoint_buttons(checkpoint, is_manual=True)
            self._append_resume_button(context)
            return

        # manual/suggest: keep checkpoint decisions in a movable floating panel.
        if self._on_show_checkpoint_dialog is not None:
            checkpoint_key = checkpoint.checkpoint_id
            is_dismissed = checkpoint_key in self._dismissed_checkpoint_ids
            if self._is_checkpoint_dialog_dismissed is not None:
                is_dismissed = is_dismissed or self._is_checkpoint_dialog_dismissed(checkpoint)
            if not is_dismissed and self._is_checkpoint_worth_dialog(checkpoint):
                self._on_show_checkpoint_dialog(checkpoint, summary_text)
                self._action_summary.setText(
                    "AI 建议浮窗已打开。可拖动浮窗标题栏调整位置，在浮窗中备注并选择方案。"
                )
                self._notes_section.setVisible(False)
                focus_btn = ActionButton("定位 AI 建议浮窗 →", variant="secondary")
                focus_btn.clicked.connect(
                    lambda: self._on_show_checkpoint_dialog(checkpoint, summary_text)
                )
                self._add_btn_row(focus_btn)
                return
            if is_dismissed:
                self._action_summary.setText(
                    "AI 建议浮窗已收起。点击下方按钮可重新打开自由移动的建议浮窗。"
                )
                self._notes_section.setVisible(False)
                open_btn = ActionButton("打开 AI 建议浮窗 →", variant="primary")
                open_btn.clicked.connect(
                    lambda: self._on_show_checkpoint_dialog(checkpoint, summary_text)
                )
                self._add_btn_row(open_btn)
                self._append_resume_button(context)
                return
            # 低价值检查点：不弹窗，直接内联展示选项按钮
            self._notes_section.setVisible(True)
            if recommended and recommended.description:
                self._ai_suggestion_label.setText(recommended.description)
                self._ai_suggestion_frame.setVisible(True)
            self._render_checkpoint_buttons(checkpoint, is_manual=context.mode == "manual")
            self._append_resume_button(context)
            return

        if is_suggest and recommended and recommended.description:
            self._ai_suggestion_label.setText(recommended.description)
            self._ai_suggestion_frame.setVisible(True)
        self._notes_section.setVisible(True)
        self._render_checkpoint_buttons(checkpoint, is_manual=context.mode == "manual")
        self._append_resume_button(context)

    def _render_done_state(
        self,
        context: ActionPanelRenderContext,
        *,
        is_auto: bool,
        auto_label: str,
    ) -> None:
        assert context.studio is not None
        self._action_badge.setText("✓ 已完成")
        self._action_badge.set_tone("success")
        is_terminal = context.studio.chapter_number >= context.studio.total_chapters > 0
        self._action_title.setText("全书章节已完成" if is_terminal else "本章已完成归档")
        self._action_summary.setText(self._completed_summary(context))

        self._notes_section.set_title("重写方向（可选，同时适用于重新生成与精修润色）")
        self._notes.setPlaceholderText(
            "描述需要调整的方向，例如：「加强开头衔接」「删减支线 B」「加深人物 X 的内心独白」。\n"
            "留空则重新生成按原大纲进行，精修仅优化文学性。"
        )
        self._notes_section.setVisible(True)
        self._notes_section.set_expanded(context.notes_expanded)
        self._rewrite_strategy_row.setVisible(context.downstream_chapter_count > 0)

        main_row = QHBoxLayout()
        main_row.setSpacing(6)

        regen_btn = ActionButton("🔄 重新生成章节", variant="primary")
        regen_btn.setProperty("compact", True)
        regen_btn.setToolTip(
            "按原大纲重新走完整流程：准备 → 起草 → 审查 → 归档。\n"
            "上方重写方向的内容将作为生成参考。"
        )
        regen_btn.clicked.connect(self._on_submit_regen_with_continuity_check)
        main_row.addWidget(regen_btn)

        polish_btn = ActionButton("✨ 精修润色", variant="secondary")
        polish_btn.setProperty("compact", True)
        polish_btn.setToolTip(
            "不重写剧情，仅对当前正文做文学性打磨。\n上方重写方向的内容将作为润色参考。"
        )
        polish_btn.clicked.connect(self._on_submit_polish_with_continuity_check)
        main_row.addWidget(polish_btn)

        if context.studio.chapter_number < context.studio.total_chapters:
            next_btn = ActionButton(
                f"前往第 {context.studio.chapter_number + 1} 章 →", variant="primary"
            )
            next_btn.setProperty("compact", True)
            next_btn.clicked.connect(self._on_go_to_next_chapter)
            main_row.addWidget(next_btn)
        else:
            audit_btn = ActionButton("📋 全书审计", variant="primary")
            audit_btn.setProperty("compact", True)
            audit_btn.setToolTip("章节全部完成后的下一阶段：生成终章验收式全书审计报告。")
            audit_btn.clicked.connect(self._on_submit_book_consistency)
            main_row.addWidget(audit_btn)

            export_btn = ActionButton("📤 导出", variant="secondary")
            export_btn.setProperty("compact", True)
            export_btn.setToolTip("审计通过后导出 Markdown / 纯文本 / EPUB。")
            export_btn.clicked.connect(self._on_submit_export)
            main_row.addWidget(export_btn)

        if is_auto or (context.stopped_from_auto and not is_auto):
            if is_auto:
                if context.auto_started:
                    stop_btn = ActionButton(f"⏹ 停止{auto_label}", variant="danger")
                    stop_btn.setProperty("compact", True)
                    stop_btn.clicked.connect(self._on_stop_auto_pilot)
                    main_row.addWidget(stop_btn)
                else:
                    start_btn = ActionButton(f"启动{auto_label} ▶", variant="primary")
                    start_btn.setProperty("compact", True)
                    start_btn.clicked.connect(self._on_start_auto_pilot)
                    main_row.addWidget(start_btn)
            if context.stopped_from_auto and not is_auto:
                resume_btn = ActionButton(f"继续{context.resume_auto_label} ▶", variant="secondary")
                resume_btn.setProperty("compact", True)
                resume_btn.clicked.connect(self._on_resume_auto_pilot)
                main_row.addWidget(resume_btn)

        main_row.addStretch()
        self._action_buttons.addLayout(main_row)

    def _render_stale_chapter_state(
        self,
        context: ActionPanelRenderContext,
        *,
        is_auto: bool,
        is_book_auto: bool,
        auto_label: str,
    ) -> None:
        """章节 status=stale 但含旧版存档正文时的专属 UI。"""
        assert context.studio is not None
        self._action_badge.setText("已失效")
        self._action_badge.set_tone("warning")
        self._action_title.setText("章节已失效（含旧版存档）")

        word_count = context.current_chapter_word_count
        stale_summary = (
            f"本章此前已生成过正文（约 {word_count:,} 字），因大纲或 Canon 水位变更已被标为失效。\n"
            "重新生成将遵循最新大纲，旧版内容仅作参考。"
        )

        self._notes_section.set_title("重写方向（可选）")
        self._notes.setPlaceholderText(
            "描述需要调整的方向，例如：「保留旧版开头风格」「按新大纲全面重写」。\n"
            "留空则按最新章节大纲重新生成。"
        )

        if is_auto and context.auto_started:
            self._action_summary.setText(
                f"{stale_summary}\n\n{auto_label}推进中，正在重新生成本章…"
            )
            self._notes_section.setVisible(False)
            stop_btn = ActionButton(f"⏹ 停止{auto_label}", variant="danger")
            stop_btn.setProperty("compact", True)
            stop_btn.clicked.connect(self._on_stop_auto_pilot)
            self._add_btn_row(stop_btn)
            return

        self._action_summary.setText(stale_summary)
        self._notes_section.setVisible(True)
        self._notes_section.set_expanded(context.notes_expanded)
        self._rewrite_strategy_row.setVisible(context.downstream_chapter_count > 0)

        main_row = QHBoxLayout()
        main_row.setSpacing(6)

        regen_btn = ActionButton("🔄 重新生成章节", variant="primary")
        regen_btn.setProperty("compact", True)
        regen_btn.setToolTip(
            "按最新大纲重走完整流程：准备 → 起草 → 审查 → 归档。\n"
            "上方重写方向的内容将作为生成参考。"
        )
        regen_btn.clicked.connect(self._on_submit_regen_with_continuity_check)
        main_row.addWidget(regen_btn)

        if context.studio.chapter_number < context.studio.total_chapters:
            next_btn = ActionButton(
                f"跳到第 {context.studio.chapter_number + 1} 章 →", variant="secondary"
            )
            next_btn.setProperty("compact", True)
            next_btn.setToolTip("跳过本章（保留旧版存档），继续处理下一章。")
            next_btn.clicked.connect(self._on_go_to_next_chapter)
            main_row.addWidget(next_btn)

        if is_auto and not context.auto_started:
            start_btn = ActionButton(f"启动{auto_label} ▶", variant="primary")
            start_btn.setProperty("compact", True)
            start_btn.clicked.connect(self._on_start_auto_pilot)
            main_row.addWidget(start_btn)

        if context.stopped_from_auto and not is_auto:
            resume_btn = ActionButton(f"继续{context.resume_auto_label} ▶", variant="secondary")
            resume_btn.setProperty("compact", True)
            resume_btn.clicked.connect(self._on_resume_auto_pilot)
            main_row.addWidget(resume_btn)

        main_row.addStretch()
        self._action_buttons.addLayout(main_row)

    def _render_prepare_state(
        self,
        context: ActionPanelRenderContext,
        *,
        is_auto: bool,
        is_book_auto: bool,
        auto_label: str,
    ) -> None:
        self._action_title.setText("准备章节方案")
        if is_auto and not context.auto_started:
            hint = (
                "章节连跑模式：点击「启动章节连跑」后，系统将从当前章自动推进，直到全书完成。"
                if is_book_auto
                else "本章自动模式：点击「启动本章自动」后，系统将自动完成当前章节的方案与写作，完成后停止。"
            )
            self._action_summary.setText(hint)
            self._notes_section.setVisible(False)
            start_btn = ActionButton(f"启动{auto_label} ▶", variant="primary")
            start_btn.clicked.connect(self._on_start_auto_pilot)
            self._add_btn_row(start_btn)
        elif is_auto and context.auto_started:
            self._action_title.setText(f"{auto_label}推进中…")
            self._action_summary.setText(f"{auto_label}推进中，正在准备章节方案…")
            self._notes_section.setVisible(False)
            stop_btn = ActionButton(f"⏹ 停止{auto_label}", variant="danger")
            stop_btn.clicked.connect(self._on_stop_auto_pilot)
            self._add_btn_row(stop_btn)
        else:
            self._action_summary.setText(
                "系统将先构建章节上下文、桥接与章节计划，生成方案后供你确认。"
            )
            self._notes_section.setVisible(True)
            launch = ActionButton("准备章节方案 →")
            launch.clicked.connect(lambda: self._on_submit_prepare())
            self._add_btn_row(launch)
        if context.stopped_from_auto and not is_auto:
            resume_btn = ActionButton(f"继续{context.resume_auto_label} ▶", variant="secondary")
            resume_btn.clicked.connect(self._on_resume_auto_pilot)
            self._add_btn_row(resume_btn)

    def _render_checkpoint_buttons(
        self, checkpoint: DecisionCheckpoint, *, is_manual: bool
    ) -> None:
        buttons: list[ActionButton] = []
        for option in checkpoint.options:
            label = option.label + (" →" if option.is_recommended and not is_manual else "")
            button = ActionButton(
                label,
                variant="primary" if option.is_recommended and not is_manual else "secondary",
            )
            button.clicked.connect(
                lambda checked=False, current_option=option: self._on_handle_checkpoint_option(
                    current_option
                )
            )
            button.setToolTip(option.description or "")
            buttons.append(button)
        self._add_btn_row(*buttons)

    def _append_resume_button(self, context: ActionPanelRenderContext) -> None:
        if not context.stopped_from_auto:
            return
        resume_btn = ActionButton(f"继续{context.resume_auto_label} ▶", variant="secondary")
        resume_btn.clicked.connect(self._on_resume_auto_pilot)
        self._add_btn_row(resume_btn)

    def _add_btn_row(self, *buttons: ActionButton) -> None:
        # Dynamic action buttons are often rebuilt as a consequence of their
        # own click handlers (notably "停止章节连跑").  Give each button the
        # action-panel host immediately, before it enters the temporary row
        # layout, so Qt never treats it as an independent native window.
        host = self._action_buttons.parentWidget()
        row = QHBoxLayout()
        row.setSpacing(8)
        for button in buttons:
            if host is not None and button.parentWidget() is None:
                button.setParent(host)
            row.addWidget(button)
        row.addStretch()
        self._action_buttons.addLayout(row)

    def _completed_summary(self, context: ActionPanelRenderContext) -> str:
        assert context.studio is not None
        summary_bits = format_review_score_parts(
            alignment_score=context.studio.alignment_score,
            continuity_score=context.studio.continuity_score,
            overall_score=context.studio.overall_score,
            causal_score=context.studio.causal_score,
            include_suffix=True,
        )
        if context.effective_warning_count:
            summary_bits.append(f"{context.effective_warning_count} 条提醒")
        summary_text = "本章已归档完成。" + (
            "　" + "  ·  ".join(summary_bits) if summary_bits else ""
        )
        if context.studio.chapter_number >= context.studio.total_chapters > 0:
            summary_text = (
                "章节正文已全部归档。下一阶段建议运行全书审计，生成终章验收报告；"
                "审计通过后即可导出成书。"
                + ("　" + "  ·  ".join(summary_bits) if summary_bits else "")
            )

        if context.workspace is None:
            return summary_text

        try:
            from novel_forge.core.utils.edit_tracker import check_for_manual_edits

            layout = ProjectLayout(context.workspace.storage_root / context.studio.project_id)
            edit_record = check_for_manual_edits(
                layout.chapter_path(context.studio.chapter_number),
                layout.states_dir,
                context.studio.chapter_number,
            )
            if edit_record is not None:
                delta = edit_record.word_count_delta
                sign = "+" if delta >= 0 else ""
                return (
                    f"⚠️ 检测到手动编辑（字数变化: {sign}{delta}，"
                    f"相似度: {edit_record.similarity_ratio:.0%}）。"
                    "可精修润色以融合修改。"
                )
        except Exception:
            return summary_text

        return summary_text

    # ── Checkpoint dialog dismissed tracking ────────────────────────────────

    def mark_checkpoint_dismissed(self, checkpoint_id: str) -> None:
        """Record that the user manually closed the dialog for this checkpoint."""
        self._dismissed_checkpoint_ids.add(checkpoint_id)

    def clear_dismissed_checkpoints(self) -> None:
        """Clear all dismissed checkpoint IDs (called on chapter switch)."""
        self._dismissed_checkpoint_ids.clear()

    # ── Critical-node filter for dialog popup ────────────────────────────

    @staticmethod
    def _is_checkpoint_worth_dialog(checkpoint: DecisionCheckpoint) -> bool:
        """Return True only for checkpoints that justify popping the floating dialog.

        Low-value routine checkpoints (e.g. simple archive confirmations) are shown
        inline instead, eliminating unnecessary user interruptions.
        """
        # 1. plan_checkpoint is always important (chapter plan confirmation)
        if checkpoint.checkpoint_type == "plan_checkpoint":
            return True
        # 2. Checkpoint with high/critical risk options
        if any(
            getattr(opt, "risk_level", "") in {"high", "critical"}
            for opt in (checkpoint.options or [])
        ):
            return True
        # 3. Plot-guard / coherence related keywords in prompt
        prompt_lower = (checkpoint.prompt or "").lower()
        if any(kw in prompt_lower for kw in ("plot_guard", "偏离", "deviation", "因果", "causal", "连贯")):
            return True
        # 4. guard_checkpoint with repair-related options
        if checkpoint.checkpoint_type == "guard_checkpoint" and checkpoint.options:
            has_repair = any(
                "repair" in (opt.option_id or "").lower() or "修复" in (opt.label or "")
                for opt in checkpoint.options
            )
            if has_repair:
                return True
        # 5. Routine checkpoints → inline buttons, no dialog
        return False
