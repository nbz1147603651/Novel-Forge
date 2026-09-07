"""Render-method mixin for ChapterStudioPage.

Extracted to keep chapter_studio_page.py focused on coordination logic.
All methods use ``self`` and are mixed into ``ChapterStudioPage`` via
inheritance — no free-standing functions, no stub delegates needed.
"""

from __future__ import annotations

import ast
import math
import re
import time
from pathlib import Path
from typing import Any

from novel_forge.core.config import get_settings
from novel_forge.core.review.review_orchestration import format_review_score_parts
from novel_forge.desktop.constants import LABEL_CHAPTER_RE as _JOB_LABEL_CH_RE
from novel_forge.desktop.jobs import DesktopJobState
from novel_forge.persistence.models import ProjectLayout

from .action_panel import ActionPanelRenderContext
from .contract import ChapterStudioMixinBase


def _render_job_chapter(job: Any) -> int | None:
    """Best-effort chapter extraction for refresh-guard checks in render helpers.

    Mirrors the logic in _job_chapter_number (chapter_studio_page.py) without
    creating a cross-module import cycle.
    """
    raw = (getattr(job, "result", None) or {}).get("chapter_number")
    try:
        if raw is not None and str(raw).strip():
            return int(raw)
    except (TypeError, ValueError):
        pass
    m = _JOB_LABEL_CH_RE.search(getattr(job, "label", "") or "")
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def _context_item_text(item: Any) -> str:
    """Return the UI-facing text from a carry-forward/suggestion payload."""
    value = item
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = ast.literal_eval(stripped)
            except (SyntaxError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                value = parsed
            else:
                return stripped
        else:
            return stripped
    if isinstance(value, dict):
        for key in ("text", "summary", "description", "content", "title"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def _format_context_items(items: list[Any], *, empty: str, limit: int = 4) -> str:
    """Format compact bullet rows for the chapter-studio overview cards."""
    rows: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _context_item_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append(text)
    if not rows:
        return empty
    visible = rows[:limit]
    suffix = f"\n另 {len(rows) - limit} 条已折叠。" if len(rows) > limit else ""
    return "\n".join(f"• {row}" for row in visible) + suffix


def _format_context_blob(value: Any, *, empty: str, limit: int = 4) -> str:
    """Clean a free-form context blob that may contain repr(dict) payloads."""
    text = str(value or "").strip()
    if not text:
        return empty
    matches = list(re.finditer(r"\{[^{}]*\}", text))
    if not matches:
        return text
    parsed: list[str] = []
    for match in matches:
        try:
            payload = ast.literal_eval(match.group(0))
        except (SyntaxError, ValueError):
            continue
        if isinstance(payload, dict):
            item_text = _context_item_text(payload)
            if item_text:
                parsed.append(item_text)
    if not parsed:
        return text
    prefix = text[: matches[0].start()].strip(" \n;；")
    bullet_text = _format_context_items(parsed, empty="", limit=limit)
    if prefix:
        return f"{prefix}\n{bullet_text}"
    return bullet_text or empty


class ChapterStudioRenderMixin(ChapterStudioMixinBase):
    """Mixin providing all ``_render_*`` panel methods for ChapterStudioPage."""

    def _project_layout_from_workspace(self, project_id: str) -> ProjectLayout | None:
        """Resolve a project layout from the current desktop workspace snapshot."""
        workspace = self._workspace
        if workspace is None:
            return None
        get_layout = getattr(workspace, "get_project_layout", None)
        if callable(get_layout):
            layout = get_layout(project_id)
            return layout if isinstance(layout, ProjectLayout) else None
        storage_root = getattr(workspace, "storage_root", None)
        if storage_root is None:
            return None
        return ProjectLayout(Path(storage_root) / project_id)

    # ── Warning helpers ─────────────────────────────────────────

    def _warning_key(self) -> tuple[str, int]:
        if self._studio is None:
            return ("", 0)
        return (self._studio.project_id, self._studio.chapter_number)

    @staticmethod
    def _warning_fingerprint(items: list[str]) -> str:
        return "\n".join(items)

    def _raw_chapter_warnings(self) -> list[str]:
        if self._studio is None:
            return []
        return [str(item).strip() for item in (self._studio.warnings or []) if str(item).strip()]

    def _effective_chapter_warnings(self) -> list[str]:
        items = self._raw_chapter_warnings()
        if not items:
            return []
        key = self._warning_key()
        dismissed = self._state.dismissed_warning_fingerprints.get(key, "")
        if dismissed and dismissed == self._warning_fingerprint(items):
            return []
        return items

    # ── Rail / Compass ────────────────────────────────────────────

    def _render_rail(self) -> None:
        if self._studio is None:
            self._rail.reset()
            return
        if hasattr(self, "_all_jobs") and self._all_jobs is not None:
            self._rail.bind_jobs(self._all_jobs, project_id=self._studio.project_id)
        self._rail.update_meta(
            self._studio.project_title,
            self._studio.total_chapters,
            self._studio.chapter_number,
        )
        chapters_data = [
            {
                "chapter_number": ch.chapter_number,
                "status": ch.status,
                "status_label": ch.status_label or "待写",
                "title": ch.title or "未命名",
            }
            for ch in self._studio.chapters
        ]
        self._rail.render_chapters(chapters_data, self._studio.chapter_number)

    def _render_compass(self) -> None:
        if self._studio is None:
            self._compass.reset()
            return

        # Get motif data from memory status
        motif_suggestions = None
        motif_warnings = None
        try:
            from novel_forge.desktop.state.store import get_ui_store

            store = get_ui_store()
            project_id = self._studio.project_id
            memory_status = store.memory_status(project_id)

            if memory_status:
                motif_suggestions = memory_status.get("motif_suggestions", None)
                motif_warnings = memory_status.get("repetition_warnings", None)
        except Exception as e:
            from novel_forge.obs.logger import get_logger

            _log = get_logger("chapter_studio_page")
            _log.warning("获取记忆状态失败: %s", e)

        previous_summary = _format_context_blob(
            self._studio.previous_summary,
            empty="上一章尚无正文，可直接围绕本章目标起笔。",
        )
        previous_exit_summary = _format_context_blob(
            self._studio.previous_exit_summary or self._studio.previous_title,
            empty="",
        )
        self._compass.update_context(
            previous_summary=previous_summary,
            previous_exit_summary=previous_exit_summary,
            current_goal=self._studio.current_goal or "当前章尚未配置目标。",
            current_outline=self._studio.current_outline_summary,
            next_goal=self._studio.next_goal or "下一章暂未设定，先聚焦当前章。",
            next_title=self._studio.next_title,
            motif_suggestions=motif_suggestions,
            motif_warnings=motif_warnings,
            current_chapter=self._studio.chapter_number,
        )

    # ── Action panel ─────────────────────────────────────────────

    def _render_action_panel(self) -> None:
        notes_expanded = False
        if self._studio is not None:
            key = (self._studio.project_id, self._studio.chapter_number)
            notes_expanded = self._notes_expanded_by_chapter.get(key, False)
        stale_with_content, stale_word_count = self._current_chapter_stale_data()
        # Compute retry countdown from state
        _scheduled_at: float = getattr(self._state, "scheduled_retry_at", 0.0)
        _retry_countdown = (
            max(0, math.ceil(_scheduled_at - time.monotonic())) if _scheduled_at > 0 else -1
        )
        latest_job = self._latest_relevant_job()
        if self._mode == self.MODE_BOOK_AUTO and self._auto_started:
            active_project_job = self._latest_active_project_job()
            if active_project_job is not None:
                latest_job = active_project_job
        downstream_count = 0
        if self._studio is not None:
            downstream_count = sum(
                1
                for chapter in self._studio.chapters
                if chapter.chapter_number > self._studio.chapter_number
                and (
                    chapter.word_count > 0
                    or chapter.status in {"done", "stale", "rewriting"}
                )
            )
        self._action_presenter.render(
            ActionPanelRenderContext(
                mode=self._mode,
                auto_started=self._auto_started,
                stopped_from_auto=self._stopped_from_auto,
                latest_job=latest_job,
                studio=self._studio,
                workspace=self._workspace,
                current_chapter_done=self._is_current_chapter_done(),
                notes_expanded=notes_expanded,
                resume_auto_label=self._auto_mode_label(),
                current_chapter_stale_with_content=stale_with_content,
                current_chapter_word_count=stale_word_count,
                effective_warning_count=len(self._effective_chapter_warnings()),
                retry_countdown_secs=_retry_countdown,
                downstream_chapter_count=downstream_count,
            )
        )

    # ── Artifacts / Inspector / Memory ───────────────────────────

    def _render_artifacts(self) -> None:
        self._artifact_presenter.render(
            workspace=self._workspace,
            studio=self._studio,
        )

    def _render_inspector(self) -> None:
        if self._memory_presenter is None:
            return

        if self._studio is None:
            self._memory_presenter.reset()
            return

        project_id = self._studio.project_id
        chapter_number = self._studio.chapter_number
        self._memory_presenter.bind_project(project_id, chapter_number)

        carry_forward_text = _format_context_items(
            list(self._studio.carry_forward or []),
            empty="上一章暂无明确承接项。",
        )
        self._memory_presenter.update_carry_forward(carry_forward_text)

        suggestion_text = (
            self._studio.suggestions_for_next_chapter or ""
        ).strip() or "上一章暂无对下一章的额外建议。"
        self._memory_presenter.update_suggestions(suggestion_text)

        _active_job = self._latest_relevant_job()
        _job_running = _active_job is not None and _active_job.status in {
            DesktopJobState.RUNNING,
            DesktopJobState.QUEUED,
        }

        score_parts = format_review_score_parts(
            alignment_score=self._studio.alignment_score,
            continuity_score=self._studio.continuity_score,
            overall_score=self._studio.overall_score,
            causal_score=self._studio.causal_score,
            include_suffix=True,
        )
        if not score_parts:
            score_parts.extend(
                [
                    "对齐 · 正在检查…" if _job_running else "对齐 · 等待章节检查",
                    "连贯 · 正在评估…" if _job_running else "连贯 · 等待初稿生成",
                    "质量 · 正在评估…" if _job_running else "质量 · 等待初稿生成",
                    "因果 · 正在检查…" if _job_running else "因果 · 等待章节检查",
                ]
            )

        rp_score = (_active_job.result or {}).get("reading_power_score") if _active_job else None
        if rp_score is not None:
            score_parts.append(f"追读 {rp_score:.1f} / 10")
        elif _job_running:
            score_parts.append("追读 · 正在评估…")
        else:
            rp_score = self._load_disk_reading_power_score()
            if rp_score is not None:
                score_parts.append(f"追读 {rp_score:.1f} / 10")
            else:
                score_parts.append("追读 · 等待初稿生成")

        state_note = self._load_state_adjudication_score_note()
        if state_note:
            score_parts.append(state_note)

        meta_bits = []
        tokens = getattr(self._studio, "tokens_used", 0)
        cost = getattr(self._studio, "cost_usd", 0.0)
        if self._studio.continuity_issue_count:
            meta_bits.append(f"连贯问题 {self._studio.continuity_issue_count} 条")
        if self._studio.causal_issue_count:
            meta_bits.append(f"因果问题 {self._studio.causal_issue_count} 条")
        if tokens:
            meta_bits.append(f"{tokens:,} tokens")
        if cost and cost > 0:
            meta_bits.append(f"${cost:.4f}")

        score_text = "\n".join(score_parts)
        if meta_bits:
            score_text += "\n" + " · ".join(meta_bits)

        warnings = self._effective_chapter_warnings()
        if warnings:
            suffix = f"（另 {len(warnings) - 1} 条）" if len(warnings) > 1 else ""
            score_text += f"\n提醒：{warnings[0]}{suffix}"

        self._memory_presenter.update_scores(score_text)

        if self._studio.pending_checkpoint is None:
            self._memory_presenter.update_checkpoint("暂无待处理的决策节点。")
        else:
            _cp = self._studio.pending_checkpoint
            checkpoint_text = _cp.summary or _cp.prompt
            if _cp.prompt and _cp.summary and _cp.prompt != _cp.summary:
                checkpoint_text = f"{_cp.summary}\n\n{_cp.prompt}"
            if warnings:
                checkpoint_text = f"{checkpoint_text}\n\n提醒：{warnings[0]}"
            self._memory_presenter.update_checkpoint(checkpoint_text)

    def _render_memory_panel(self) -> None:
        """Update the memory panel with current project/chapter state."""
        if self._memory_presenter is None:
            return

        if self._studio is None:
            self._memory_presenter.reset()
            return

        sync_lookback = getattr(self, "_sync_motif_lookback_from_settings", None)
        if callable(sync_lookback):
            sync_lookback()

        project_id = self._studio.project_id
        chapter_number = self._studio.chapter_number

        self._memory_presenter.bind_project(project_id, chapter_number)
        warning_items = self._effective_chapter_warnings()
        self._memory_presenter.update_chapter_warnings(warning_items)

        from novel_forge.desktop.state.store import get_ui_store

        store = get_ui_store()
        memory_status = store.memory_status(project_id)

        def _safe_count(value: Any) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        def _has_meaningful_motifs(status: dict[str, Any] | None) -> bool:
            if not status:
                return False
            motifs = status.get("motifs")
            if not isinstance(motifs, list):
                return False
            for motif in motifs:
                if not isinstance(motif, dict):
                    continue
                if (
                    _safe_count(motif.get("occurrence_count")) > 0
                    or _safe_count(motif.get("last_chapter")) > 0
                    or _safe_count(motif.get("first_chapter")) > 0
                ):
                    return True
            return False

        # Determine if disk has fresher data than the store.
        # Cases that require a disk reload:
        #  1. Store has no entry at all.
        #  2. Store has an entry with no meaningful motif occurrences
        #     (stale payload from before the tracker/cache merge).
        #  3. Disk last_indexed_chapter is strictly greater than the store's —
        #     async motif/summary tasks finished after the last memory_updated
        #     job event was processed, so the store is behind disk.
        store_motifs_empty = not _has_meaningful_motifs(memory_status)
        store_last = _safe_count(memory_status.get("last_indexed_chapter")) if memory_status else 0
        need_disk_reload = not memory_status or store_motifs_empty

        # Case 4: A repair_motif_history job just succeeded — disk data was
        # updated (stats rebuilt / motifs re-extracted) but last_indexed_chapter
        # did not change, so the generic freshness probe would miss it.
        # Force a disk reload so the motif tab reflects the repaired data.
        _motif_repair_succeeded = any(
            j.kind == "repair_motif_history" and j.status.value == "succeeded"
            for j in getattr(self, "_jobs", [])
        )
        if _motif_repair_succeeded:
            need_disk_reload = True

        if not need_disk_reload:
            # Cheap probe: read just enough to compare last_indexed_chapter.
            disk_status = self._load_memory_status_from_disk(project_id)
            if disk_status:
                disk_last = _safe_count(disk_status.get("last_indexed_chapter"))
                disk_motif_count = len(disk_status.get("motifs", []))
                store_motif_count = len(memory_status.get("motifs", []))
                # Promote disk data when disk is either newer (higher
                # last_indexed_chapter) or has significantly more motifs
                # than the store — the latter catches the case where event
                # payloads were truncated by _compact_memory_status_payload.
                if disk_last > store_last or disk_motif_count > store_motif_count:
                    # Disk is newer — promote disk data, preserving runtime fields
                    # that are only in the store (e.g. audit results).
                    memory_status = {
                        **disk_status,
                        **{k: v for k, v in memory_status.items() if k not in disk_status},
                    }
                    store.set_memory_status(project_id, memory_status)
        else:
            disk_status = self._load_memory_status_from_disk(project_id)
            if disk_status:
                if store_motifs_empty and memory_status and disk_status.get("motifs"):
                    # Merge: promote persisted memory fields while preserving runtime-only store fields.
                    memory_status = {
                        **disk_status,
                        **{k: v for k, v in memory_status.items() if k not in disk_status},
                    }
                else:
                    memory_status = disk_status
                store.set_memory_status(project_id, memory_status)

        if memory_status:
            self._memory_presenter.update_memory_status(memory_status)

        self._render_reading_power_panel(project_id, chapter_number)
        self._render_guardrail_panel(project_id, chapter_number)
        self._render_story_control_panel(project_id, chapter_number)

    def _render_reading_power_panel(self, project_id: str, chapter_number: int) -> None:
        """Update the reading power tab with the previous chapter's evaluation."""
        if self._memory_presenter is None:
            return

        if chapter_number <= 1:
            self._memory_presenter.update_reading_power()
            return

        prev_chapter = chapter_number - 1
        rp_report_path = None

        layout = self._project_layout_from_workspace(project_id)
        if layout:
            rp_report_path = layout.reading_power_report_path(prev_chapter)

        if rp_report_path is None or not rp_report_path.exists():
            self._memory_presenter.update_reading_power()
            return

        import json

        try:
            rp_data = json.loads(rp_report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._memory_presenter.update_reading_power()
            return

        score = rp_data.get("overall_score")
        hook_type = rp_data.get("hook_type", "")
        hook_strength = rp_data.get("hook_strength", "")
        hook_description = rp_data.get("hook_description", "")
        outline_hook_match = rp_data.get("outline_hook_match")
        outline_payoff_coverage = rp_data.get("outline_payoff_coverage")
        suggestions = rp_data.get("suggestions", [])

        hook_history = []
        timeline_path = None
        layout = self._project_layout_from_workspace(project_id)
        if layout:
            timeline_path = layout.plans_dir / "reading_power_timeline_state.json"

        if timeline_path and timeline_path.exists():
            try:
                timeline_data = json.loads(timeline_path.read_text(encoding="utf-8"))
                hook_type_history = timeline_data.get("hook_type_history", [])
                for entry in hook_type_history[-10:]:
                    ht = entry.get("hook_type", "")
                    hs = entry.get("hook_strength", "")
                    ch = entry.get("chapter", 0)
                    hook_history.append(f"第{ch}章: {ht} ({hs})")
            except (OSError, json.JSONDecodeError):
                pass

        self._memory_presenter.update_reading_power(
            score=score,
            hook_type=hook_type,
            hook_strength=hook_strength,
            hook_description=hook_description,
            outline_hook_match=outline_hook_match,
            outline_payoff_coverage=outline_payoff_coverage,
            suggestions=suggestions,
            hook_history=hook_history,
        )

    def _render_guardrail_panel(self, project_id: str, chapter_number: int) -> None:
        """Update the guardrail tab with constraint data from guard reports."""
        if self._memory_presenter is None:
            return

        layout = self._project_layout_from_workspace(project_id)
        if not layout:
            self._memory_presenter.update_guardrail()
            return

        from .memory import load_guardrail_data

        guard_data = load_guardrail_data(layout.root, chapter_number)
        self._memory_presenter.update_guardrail(
            prev_constraints=guard_data.get("prev_constraints"),
            prev_compliance=guard_data.get("prev_compliance"),
            next_constraints=guard_data.get("next_constraints"),
            next_handoff_status=guard_data.get("next_handoff_status"),
        )

    def _render_story_control_panel(self, project_id: str, chapter_number: int) -> None:
        """Update the chapter-studio control tab with progression diagnostics."""
        if self._memory_presenter is None:
            return

        layout = self._project_layout_from_workspace(project_id)
        if not layout:
            self._memory_presenter.update_story_control("")
            return

        import json

        def _load(path: Path) -> dict[str, Any]:
            if not path.exists():
                return {}
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            return payload if isinstance(payload, dict) else {}

        def _esc(value: Any) -> str:
            return (
                str(value or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

        def _list(items: Any) -> str:
            values = [str(item).strip() for item in list(items or []) if str(item).strip()]
            if not values:
                return '<span style="color:#8b7460;">无</span>'
            return "<ul style='margin:4px 0; padding-left:18px;'>" + "".join(
                f"<li>{_esc(item)}</li>" for item in values[:8]
            ) + "</ul>"

        packet = _load(layout.chapter_state_packet_path(chapter_number))
        contract = packet.get("chapter_contract", {}) if isinstance(packet, dict) else {}
        milestone_window = packet.get("milestone_window", {}) if isinstance(packet, dict) else {}
        visibility = (
            packet.get("stage_visibility_diagnostics", {}) if isinstance(packet, dict) else {}
        )
        contract_report = _load(layout.contract_execution_report_path(chapter_number))
        expression_report = _load(layout.expression_repetition_report_path(chapter_number))
        arc_report = _load(layout.arc_liveness_report_path(chapter_number))

        parts: list[str] = []
        if isinstance(contract, dict) and contract:
            parts.append("<b>剧情控制</b>")
            parts.append("必须推进：" + _list(contract.get("required_progressions", [])))
            parts.append("允许推进：" + _list(contract.get("allowed_progressions", [])))
            parts.append("禁止推进：" + _list(contract.get("forbidden_progressions", [])))
            parts.append("未来泄露风险：" + _list(contract.get("future_leak_risks", [])))

        if isinstance(milestone_window, dict) and milestone_window:
            parts.append("<b>里程碑窗口</b>")
            parts.append(
                f"当前 {len(milestone_window.get('current', []) or [])} 个；"
                f"未来护栏 {len(milestone_window.get('future_guardrails', []) or [])} 个；"
                f"隐藏未来 {int(milestone_window.get('withheld_future_count', 0) or 0)} 个"
            )

        if isinstance(visibility, dict) and visibility:
            parts.append("<b>阶段可见性</b>")
            for stage, item in visibility.items():
                if not isinstance(item, dict):
                    continue
                parts.append(
                    f"{_esc(stage)}：当前 {item.get('visible_current_milestones', 0)}，"
                    f"未来护栏 {item.get('visible_future_guardrails', 0)}，"
                    f"隐藏 {item.get('withheld_future_count', 0)}"
                )

        if isinstance(contract_report, dict) and contract_report:
            parts.append("<b>契约执行</b>")
            parts.append(
                f"{_esc(contract_report.get('verdict', ''))} / "
                f"{_esc(contract_report.get('severity', ''))}；"
                f"完成分 {contract_report.get('contract_completion_score', 'N/A')}；"
                f"决定 {_esc(contract_report.get('repair_or_replan_decision', ''))}"
            )
            parts.append("未来泄露：" + _list(contract_report.get("future_leak_hits", [])))
            parts.append("禁用推进：" + _list(contract_report.get("forbidden_progression_hits", [])))

        if isinstance(expression_report, dict) and expression_report:
            parts.append("<b>表达通道</b>")
            records = expression_report.get("records", []) or []
            hits = expression_report.get("hits", []) or []
            parts.append(f"冷却记录 {len(records)} 条；本章命中 {len(hits)} 条")
            parts.append(_list([item.get("summary", item) if isinstance(item, dict) else item for item in hits]))

        if isinstance(arc_report, dict) and arc_report:
            parts.append("<b>角色弧光</b>")
            dormant = arc_report.get("dormant_arcs", []) or []
            parts.append(f"沉睡弧光 {len(dormant)} 条")
            parts.append(
                _list(
                    [
                        f"{item.get('name', '')}：{item.get('planning_hint', '')}"
                        for item in dormant
                        if isinstance(item, dict)
                    ]
                )
            )

        self._memory_presenter.update_story_control("<br/>".join(parts))

    def _load_memory_status_from_disk(self, project_id: str) -> dict[str, Any] | None:
        """Load memory status from disk if available."""
        from .memory import load_memory_status_from_disk

        storage_root: Path | None = None
        if self._workspace:
            storage_root = self._workspace.storage_root
        else:
            try:
                from novel_forge.desktop.workspace import DesktopWorkspaceService

                service = DesktopWorkspaceService.from_settings()
                if service:
                    storage_root = service.runtime.storage.root
            except Exception:
                pass

        return load_memory_status_from_disk(project_id, storage_root)

    def _load_disk_reading_power_score(self) -> float | None:
        """Load reading power score from stored report on disk.

        Used for completed chapters whose evaluation was persisted in a
        previous session where the active-job result is no longer available.
        """
        if self._studio is None:
            return None
        project_id = self._studio.project_id
        chapter_number = self._studio.chapter_number
        layout = self._project_layout_from_workspace(project_id)
        if layout is None:
            return None
        rp_report_path = layout.reading_power_report_path(chapter_number)
        if not rp_report_path.exists():
            return None
        try:
            import json

            rp_data = json.loads(rp_report_path.read_text(encoding="utf-8"))
            score = rp_data.get("overall_score") if isinstance(rp_data, dict) else None
            return float(score) if score is not None else None
        except (TypeError, ValueError, OSError, json.JSONDecodeError):
            return None

    def _load_state_adjudication_score_note(self) -> str:
        """Load the blocking state-adjudication reason for the score overview."""
        if self._studio is None:
            return ""
        layout = self._project_layout_from_workspace(self._studio.project_id)
        if layout is None:
            return ""

        import json

        chapter_number = self._studio.chapter_number
        report_paths = [
            layout.state_adjudication_report_path(chapter_number),
            layout.narrative_state_dir / f"chapter_{chapter_number:03d}_state_adjudication.json",
        ]
        payload: dict[str, Any] = {}
        for path in report_paths:
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict):
                payload = raw
                break
        if not payload:
            return ""

        final = payload.get("final_adjudication")
        if not isinstance(final, dict):
            return ""
        verdict = str(final.get("verdict") or "").strip()
        repair_ids = final.get("repair_candidate_ids")
        repair_issues = final.get("repair_issues")
        repair_count = len(repair_ids) if isinstance(repair_ids, list) else 0
        if not repair_count and isinstance(repair_issues, list):
            repair_count = len(repair_issues)
        should_block = bool(final.get("should_block_archive"))
        if verdict not in {"needs_repair", "reject"} and not should_block and not repair_count:
            return ""

        tags: list[str] = []
        if should_block:
            tags.append("阻断")
        if repair_count:
            tags.append(f"待修 {repair_count}")
        if not tags and verdict:
            tags.append(verdict)
        summary = str(final.get("summary") or "").strip()
        suffix = f"：{summary[:80]}" if summary else ""
        return f"状态裁判 · {' / '.join(tags)}{suffix}"

    # ── Continuity / Causal checklists ────────────────────────────

    def _render_relationship_card(self) -> None:
        """Populate the compact relationship card in the memory panel."""
        if self._memory_presenter is None:
            return
        self._memory_presenter.render_relationship_card(
            workspace=self._workspace,
            studio=self._studio,
        )

    # ── Issue-refresh helpers ─────────────────────────────────────
    # Maps each job kind to which issue reports it will overwrite.
    # When one of these jobs is actively running or queued, the
    # corresponding panel shows "正在执行，等待刷新…" instead of stale data.
    _CONTINUITY_REFRESH_KINDS: frozenset[str] = frozenset(
        {
            "run_chapter",
            "resolve_chapter_checkpoint",
            "resolve_chapter_checkpoint_finalize",
            "repair_continuity",
            "repair_issues",
            "reevaluate_chapter",
        }
    )
    _CAUSAL_REFRESH_KINDS: frozenset[str] = frozenset(
        {
            "run_chapter",
            "resolve_chapter_checkpoint",
            "resolve_chapter_checkpoint_finalize",
            "repair_causal",
            "repair_issues",
            "reevaluate_chapter",
        }
    )

    def _continuity_refreshing(self) -> bool:
        """Return True when an active job for the *current* chapter will overwrite the continuity report.

        ``self._jobs`` is filtered by chapter in ``bind_jobs``, but that filter
        runs with whichever chapter was current at the time of the call.  When the
        user navigates to a different chapter *before* ``bind_jobs`` fires again,
        the list may still contain jobs from the previously-viewed chapter.  The
        studio-chapter guard below prevents that stale list from forcing the
        "正在执行，等待刷新…" placeholder onto an already-completed chapter whose
        issues should be shown immediately.
        """
        studio_ch = self._studio.chapter_number if self._studio is not None else None
        _active = {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        return any(
            j.kind in self._CONTINUITY_REFRESH_KINDS
            and j.status in _active
            and (studio_ch is None or _render_job_chapter(j) == studio_ch)
            for j in self._jobs
        )

    def _causal_refreshing(self) -> bool:
        """Return True when an active job for the *current* chapter will overwrite the causal report.

        See ``_continuity_refreshing`` for the rationale behind the studio-chapter guard.
        """
        studio_ch = self._studio.chapter_number if self._studio is not None else None
        _active = {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        return any(
            j.kind in self._CAUSAL_REFRESH_KINDS
            and j.status in _active
            and (studio_ch is None or _render_job_chapter(j) == studio_ch)
            for j in self._jobs
        )

    def _render_continuity_checklist(self) -> None:
        """Populate the continuity issues checklist with styled cards."""
        if self._memory_presenter is None:
            return
        if self._studio is None:
            # 项目切换/清空 studio 时，仍需调用渲染以确保旧问题卡片被清空。
            self._memory_presenter.render_continuity_checklist(
                studio=None,
                mode=self._mode,
                jobs=self._jobs,
                auto_repair_attempts=self._auto_repair_attempts,
                max_attempts=get_settings().max_auto_repair_attempts,
                ctx=self._build_autopilot_context(),
                refreshing=False,
            )
            return
        _refreshing = self._continuity_refreshing()
        self._memory_presenter.render_continuity_checklist(
            studio=self._studio,
            mode=self._mode,
            jobs=self._jobs,
            auto_repair_attempts=self._auto_repair_attempts,
            max_attempts=get_settings().max_auto_repair_attempts,
            ctx=self._build_autopilot_context(),
            queue_auto_submit_repair=self._queue_auto_submit_repair,
            refreshing=_refreshing,
        )

    def _render_causal_checklist(self) -> None:
        """Populate the causal chain issues panel with styled cards."""
        if self._memory_presenter is None:
            return
        _refreshing = self._causal_refreshing()
        self._memory_presenter.render_causal_checklist(
            studio=self._studio,
            refreshing=_refreshing,
        )
        # When only causal issues exist (no continuity issues), the auto-repair
        # trigger in render_continuity_checklist won't fire because it returns
        # early.  Check here as a fallback so causal-only chapters get repaired.
        if (
            not _refreshing
            and self._auto_pilot
            and self._auto_started
            and not self._auto_repair_pending
            and self._studio is not None
            and not (self._studio.continuity_issues or [])
            and (self._studio.causal_issues or [])
        ):
            from novel_forge.desktop.pages.chapter_studio.autorun import should_auto_submit_repair

            if should_auto_submit_repair(self._build_autopilot_context()):
                self._queue_auto_submit_repair()
