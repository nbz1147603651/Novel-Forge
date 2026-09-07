"""Memory panel presenter for chapter studio memory integration."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Slot

from novel_forge.core.config import get_settings
from novel_forge.core.review.review_orchestration import coerce_score, format_review_score_parts
from novel_forge.desktop.components.memory_components import (
    MemoryBadge,
    UnifiedMemoryPanel,
)
from novel_forge.desktop.pages.chapter_studio.memory import (
    load_chapter_focus_characters,
)
from novel_forge.desktop.state.store import UIStore

JsonDict = dict[str, Any]

__all__ = ["UnifiedMemoryPanelPresenter"]


def _resolve_causal_status_hint(studio: Any, issues: list[dict[str, Any]]) -> str:
    """Return a user-facing hint for the causal issue panel state."""
    warning_items = []
    if studio is not None:
        warning_items = [
            str(item).strip()
            for item in (getattr(studio, "warnings", None) or [])
            if str(item).strip()
        ]

    stale_warning = next(
        (
            warning
            for warning in warning_items
            if "因果校验" in warning and ("基于旧版正文" in warning or "未绑定正文版本" in warning)
        ),
        "",
    )
    if stale_warning:
        return f"⚠️ {stale_warning}"

    if not issues:
        return ""
    high_or_critical = any(
        str((issue.get("severity")) or "").lower() in {"critical", "high"}
        for issue in issues
        if isinstance(issue, dict)
    )
    if not high_or_critical:
        return "当前剩余因果问题均为中低优先级，不阻断归档。可按需手动修复。"
    return ""


class UnifiedMemoryPanelPresenter(QObject):
    """Presenter for the unified memory panel combining context overview and memory features.

    This presenter:
    1. Binds the UnifiedMemoryPanel to the current project/chapter
    2. Receives audit results from UIStore and updates the panel
    3. Manages memory status display (motifs, suggestions, warnings)
    """

    def __init__(
        self,
        unified_panel: UnifiedMemoryPanel,
        memory_badge: MemoryBadge,
        store: UIStore,
    ) -> None:
        super().__init__(unified_panel)
        self._panel = unified_panel
        self._badge = memory_badge
        self._store = store
        self._current_project: str | None = None
        self._current_chapter: int = 0
        raw_lookback = getattr(get_settings(), "memory_motif_related_lookback_chapters", None)
        self._motif_related_lookback_chapters: int = max(
            0,
            int(2 if raw_lookback is None else raw_lookback),
        )

        self._store.audit_result_changed.connect(self._on_audit_result_changed)
        self._store.memory_status_changed.connect(self._on_memory_status_changed)

    def bind_project(self, project_id: str, chapter_number: int) -> None:
        """Bind to a project and chapter."""
        changed = project_id != self._current_project or chapter_number != self._current_chapter
        self._current_project = project_id
        self._current_chapter = chapter_number
        if changed:
            self._update_from_store()

    def update_checkpoint(self, content: str) -> None:
        """Update the checkpoint card."""
        self._panel.update_checkpoint(content)

    def update_carry_forward(self, content: str) -> None:
        """Update the carry forward card."""
        self._panel.update_carry_forward(content)

    def update_suggestions(self, content: str) -> None:
        """Update the suggestions card."""
        self._panel.update_suggestions(content)

    def update_unresolved(self, content: str) -> None:
        """Update the 全书悬念 card."""
        self._panel.update_unresolved(content)

    def update_scores(self, content: str) -> None:
        """Update the scores card."""
        self._panel.update_scores(content)

    def update_chapter_warnings(self, warnings: list[str]) -> None:
        """Update chapter-level warning list in issues tab."""
        self._panel.update_chapter_warnings(warnings)

    def update_guardrail(
        self,
        prev_constraints: list[str] | None = None,
        prev_compliance: list[JsonDict] | None = None,
        next_constraints: list[str] | None = None,
        next_handoff_status: str | None = None,
    ) -> None:
        self._panel.update_guardrail(
            prev_constraints,
            prev_compliance,
            next_constraints,
            next_handoff_status,
        )

    def update_story_control(self, content: str) -> None:
        """Update plot/control diagnostics in the chapter-studio memory panel."""
        self._panel.update_story_control(content)

    def update_relations(self, content: str) -> None:
        """Update the relations card."""
        self._panel.update_relations(content)

    def update_reading_power(
        self,
        score: float | None = None,
        hook_type: str = "",
        hook_strength: str = "",
        hook_description: str = "",
        outline_hook_match: JsonDict | None = None,
        outline_payoff_coverage: JsonDict | None = None,
        suggestions: list[str] | None = None,
        hook_history: list[str] | None = None,
    ) -> None:
        """Update the reading power tab with evaluation results."""
        self._panel.update_reading_power(
            score=score,
            hook_type=hook_type,
            hook_strength=hook_strength,
            hook_description=hook_description,
            outline_hook_match=outline_hook_match,
            outline_payoff_coverage=outline_payoff_coverage,
            suggestions=suggestions,
            hook_history=hook_history,
        )

    def update_memory_status(self, status: dict[str, Any]) -> None:
        """Update memory panel with new status data."""
        if not self._current_project:
            return

        motifs = status.get("motifs", [])
        suggestions = status.get("motif_suggestions", [])
        warnings = status.get("repetition_warnings", [])
        if not isinstance(motifs, list):
            motifs = []
        if not isinstance(suggestions, list):
            suggestions = []
        if not isinstance(warnings, list):
            warnings = []
        raw_chapter_motifs = status.get("chapter_motifs", {})

        def _normalize_chapter_motifs(raw: Any) -> dict[int, set[str]]:
            if not isinstance(raw, dict):
                return {}
            result: dict[int, set[str]] = {}
            for chapter_key, motif_ids in raw.items():
                try:
                    chapter_no = int(chapter_key)
                except (TypeError, ValueError):
                    continue
                if chapter_no <= 0:
                    continue
                if not isinstance(motif_ids, (list, tuple, set)):
                    continue
                ids = {str(item).strip() for item in motif_ids if str(item).strip()}
                if ids:
                    result[chapter_no] = ids
            return result

        # Filter motifs by configurable related window (default: previous 2 + current).
        current = self._current_chapter
        start_chapter = max(1, current - self._motif_related_lookback_chapters)
        motif_ids_by_chapter = _normalize_chapter_motifs(raw_chapter_motifs)
        has_chapter_motif_index = bool(motif_ids_by_chapter)
        _STATUS_ORDER = {"active": 0, "recall": 1, "": 2}

        def _to_positive_int(raw: Any) -> int:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return 0
            return value if value > 0 else 0

        filtered_motifs: list[JsonDict] = []
        meaningful_motifs: list[JsonDict] = []
        for m in motifs:
            if not isinstance(m, dict):
                continue
            first = _to_positive_int(m.get("first_chapter", 0))
            last = _to_positive_int(m.get("last_chapter", 0))
            occ = _to_positive_int(m.get("occurrence_count", 0))
            if last <= 0 and first > 0:
                # Backward compatibility for historical data where only
                # first_chapter is present.
                last = first
            has_timeline = first > 0 or last > 0
            if not has_timeline and occ <= 0:
                # Skip blueprint placeholders that were never activated in chapters.
                continue
            # Hide motifs that belong to chapters not yet reached
            if current > 0 and first > current:
                continue
            meaningful_motifs.append(m)

            if current > 0:
                motif_id = str(m.get("motif_id", "")).strip()
                indexed_chapters = [
                    chapter_no
                    for chapter_no, motif_ids in motif_ids_by_chapter.items()
                    if start_chapter <= chapter_no <= current and motif_id in motif_ids
                ]
                if has_chapter_motif_index:
                    if not indexed_chapters:
                        continue
                    display_last = max(indexed_chapters)
                elif not (start_chapter <= last <= current):
                    continue
                else:
                    display_last = last
                status_tag = "active" if display_last == current else "recall"
            else:
                # Fallback when chapter context is unknown: keep known motifs.
                status_tag = "active" if last > 0 else ""
                display_last = last
            filtered_motifs.append(
                {
                    **m,
                    "first_chapter": first,
                    "last_chapter": display_last,
                    "occurrence_count": occ,
                    "status": status_tag,
                }
            )
        filtered_motifs.sort(
            key=lambda x: (
                _STATUS_ORDER[x.get("status", "")],
                -int(x.get("last_chapter", 0) or 0),
                -int(x.get("occurrence_count", 0) or 0),
            )
        )

        visible_ids = {
            str(m.get("motif_id", "")).strip()
            for m in filtered_motifs
            if str(m.get("motif_id", "")).strip()
        }
        filtered_suggestions = []
        for suggestion in suggestions:
            if not isinstance(suggestion, dict):
                continue
            motif_id = str(suggestion.get("motif_id", "")).strip()
            if motif_id and motif_id in visible_ids:
                filtered_suggestions.append(suggestion)

        filtered_warnings = []
        for warning in warnings:
            if not isinstance(warning, dict):
                continue
            motif_id = str(warning.get("motif_id", "")).strip()
            if motif_id and motif_id in visible_ids:
                filtered_warnings.append(warning)

        if filtered_motifs or filtered_suggestions or filtered_warnings:
            self._panel.update_motifs(filtered_motifs, filtered_suggestions, filtered_warnings)
        else:
            self._panel.set_empty_motifs()

        indexed = status.get("indexed_chapters", 0)
        self._update_badge(indexed, meaningful_motifs, filtered_motifs)

        unresolved_questions = status.get("unresolved_questions", [])
        if not isinstance(unresolved_questions, list):
            unresolved_questions = []

        # NOTE: Do NOT call update_scores() or update_relations() here.
        # - Scores card is owned by _render_inspector() (studio quality scores) and
        #   update_from_audit_result() (audit scores).  Raw memory-index stats are
        #   not meaningful quality scores and must not overwrite them.
        # - Relations card is owned by render_relationship_card() which renders
        #   structured canon HTML.  Raw outline-tracker counts are not character
        #   relationship data and would overwrite the proper card on every async
        #   memory_status_changed signal.

        if unresolved_questions:
            preview_items = [
                str(question).strip()
                for question in unresolved_questions[:3]
                if str(question).strip()
            ]
            if preview_items:
                preview_text = "；".join(preview_items)
                suffix = (
                    f"（另 {max(0, len(unresolved_questions) - len(preview_items))} 条）"
                    if len(unresolved_questions) > len(preview_items)
                    else ""
                )
                self._panel.update_unresolved(
                    f"全书悬念（{len(unresolved_questions)}）：{preview_text}{suffix}"
                )
        else:
            self._panel.update_unresolved("")

    @Slot(str, int, object)
    def _on_audit_result_changed(
        self,
        project_id: str,
        chapter: int,
        result: dict[str, Any],
    ) -> None:
        """Handle audit result changes from UIStore."""
        if project_id != self._current_project:
            return
        if chapter != self._current_chapter:
            # Ignore audit results for chapters other than the one currently
            # displayed.  Without this guard a background job finishing for
            # ch20 while the user is viewing ch5 would: (a) corrupt
            # _current_chapter, causing subsequent motif filtering to use
            # ch20's window; and (b) overwrite the carry_forward /
            # suggestions / scores cards with ch20's data.
            return
        self.update_from_audit_result(result)

    @Slot(str, object)
    def _on_memory_status_changed(
        self,
        project_id: str,
        status: dict[str, Any],
    ) -> None:
        """Handle memory status changes from UIStore."""
        if project_id != self._current_project:
            return

        self.update_memory_status(status)

    def update_from_audit_result(self, result: dict[str, Any]) -> None:
        """Update the panel with audit result data.

        This method updates:
        - Scores card with overall scores
        - Continuity issues with critique issues
        - Causal issues with relevant critique issues
        - Overview cards with summary information

        Args:
            result: Audit result dictionary from quality checks
        """
        if not result:
            self._panel.set_empty_continuity()
            return

        dimension_scores = result.get("dimension_scores", {})
        if not isinstance(dimension_scores, dict):
            dimension_scores = {}

        def _dimension_score(key: str) -> float | None:
            entry = dimension_scores.get(key)
            if isinstance(entry, dict):
                return coerce_score(entry.get("score"))
            return coerce_score(entry)

        continuity_score = _dimension_score("continuity")
        if continuity_score is None:
            continuity_score = result.get("continuity_score", 0.0) or 0.0
        issue_count = result.get("issue_count", 0) or 0
        critical_count = result.get("critical_issues", 0) or 0
        high_count = result.get("high_issues", 0) or 0

        # Prefer the persisted quality-gate dimensions so the panel reflects the
        # latest normalized audit state instead of stale per-report shortcuts.
        if dimension_scores:
            ordered_dimensions = (
                ("对齐", "alignment"),
                ("章质", "chapter_quality"),
                ("连贯", "continuity"),
                ("因果", "causal"),
                ("追读", "reading_power"),
                ("护栏", "guard"),
                ("状态", "state_adjudication"),
            )
            score_parts = [
                f"{label} {score:.1f} / 10"
                for label, key in ordered_dimensions
                if (score := _dimension_score(key)) is not None
            ]
        else:
            overall_score = coerce_score(result.get("overall_score"))
            causal_score = coerce_score(result.get("causal_score"))
            alignment_score_raw = coerce_score(result.get("alignment_score"))
            reading_power_score = coerce_score(result.get("reading_power_score"))
            score_parts = format_review_score_parts(
                alignment_score=alignment_score_raw,
                continuity_score=continuity_score,
                overall_score=overall_score,
                causal_score=causal_score if causal_score and causal_score > 0 else None,
                reading_power_score=reading_power_score,
                include_suffix=True,
            )

        meta_parts: list[str] = []
        meta_parts.append(f"问题数: {issue_count}")
        if critical_count > 0 or high_count > 0:
            meta_parts.append(f"严重: {critical_count}, 高: {high_count}")

        score_text = "\n".join(score_parts)
        if meta_parts:
            score_text += "\n" + " · ".join(meta_parts)

        self._panel.update_scores(score_text)

        critique = result.get("critique")
        if critique:
            issues = critique.get("issues", [])
            warnings = critique.get("warnings", [])
            metadata = critique.get("metadata", {}) if isinstance(critique, dict) else {}
            execution_meta = metadata.get("execution", {}) if isinstance(metadata, dict) else {}

            continuity_issues = []
            causal_issues = []
            character_issues = []
            plot_issues = []
            theme_issues = []
            other_issues = []

            for issue in issues:
                dimension = issue.get("dimension", "").lower()
                category = issue.get("category", "").lower()
                issue_data = {
                    "severity": issue.get("severity", "medium"),
                    "summary": issue.get("summary", ""),
                    "evidence": issue.get("evidence", ""),
                    "suggested_fix": issue.get("suggested_fix", ""),
                    "affected_chapters": issue.get("affected_chapters", []),
                }

                if dimension == "continuity" or "continuity" in category or "coherence" in category:
                    continuity_issues.append(issue_data)
                elif dimension == "causal" or "causal" in category or "cause" in category:
                    causal_issues.append(issue_data)
                elif dimension == "chapter_quality":
                    other_issues.append(issue_data)
                elif dimension == "reading_power":
                    plot_issues.append(issue_data)
                elif "character" in category or "consistency" in category:
                    character_issues.append(issue_data)
                elif "plot" in category or "thread" in category:
                    plot_issues.append(issue_data)
                elif "theme" in category or "motif" in category:
                    theme_issues.append(issue_data)
                else:
                    other_issues.append(issue_data)

            self._panel.update_continuity_issues(
                continuity_issues + character_issues + other_issues
            )
            self._panel.update_causal_issues(causal_issues)

            suggestions_parts = []
            if warnings:
                suggestions_parts.extend(warnings)
            if plot_issues:
                suggestions_parts.append(f"情节线索: {len(plot_issues)} 个问题")
            if theme_issues:
                suggestions_parts.append(f"主题一致性: {len(theme_issues)} 个问题")
            if isinstance(execution_meta, dict):
                mode = execution_meta.get("mode", "")
                character_mode = execution_meta.get("character_check_mode", "")
                timed_out = execution_meta.get("checks_timed_out", [])
                cache_hit = bool(execution_meta.get("cache_hit", False))
                cache_size = int(execution_meta.get("cache_size", 0) or 0)
                cache_skip_reasons = execution_meta.get("cache_skip_reasons", [])
                if mode or character_mode:
                    mode_label = "并发" if "parallel" in str(mode) else str(mode)
                    if character_mode == "batched":
                        suggestions_parts.append(f"审核执行: {mode_label} + 角色批量")
                    elif mode_label:
                        suggestions_parts.append(f"审核执行: {mode_label}")
                if cache_hit:
                    suggestions_parts.append("审核缓存命中")
                elif cache_size > 0:
                    suggestions_parts.append(f"审核缓存: {cache_size}")
                if isinstance(cache_skip_reasons, list) and cache_skip_reasons:
                    reasons_text = ",".join(str(x) for x in cache_skip_reasons[:2])
                    suggestions_parts.append(f"缓存跳过: {reasons_text}")
                if isinstance(timed_out, list) and timed_out:
                    suggestions_parts.append(
                        f"审核超时: {', '.join(str(x) for x in timed_out[:2])}"
                    )
            if suggestions_parts:
                self._panel.update_suggestions("\n".join(suggestions_parts[:3]))
            else:
                self._panel.update_suggestions("")
        else:
            # Some audit payloads only carry scores/dimension state.  Treat an
            # absent critique as "no issue details in this event", not as proof
            # that the persisted continuity/causal reports are clean; otherwise
            # a low continuity score can be shown beside "暂无连贯性问题".
            causal_score = coerce_score(result.get("causal_score"))
            low_continuity = continuity_score is not None and continuity_score < 6.0
            low_causal = causal_score is not None and causal_score > 0 and causal_score < 7.0
            if issue_count <= 0 and not low_continuity and not low_causal:
                self._panel.set_empty_continuity()

        alignment = result.get("alignment")
        if alignment:
            missing = alignment.get("missing_main_points", [])
            weak_subplots = alignment.get("weak_subplot_points", [])

            checkpoint_text = ""
            if missing:
                checkpoint_text += f"缺失主线: {len(missing)} 项\n"
            if weak_subplots:
                checkpoint_text += f"薄弱支线: {len(weak_subplots)} 项\n"
            if checkpoint_text:
                self._panel.update_checkpoint(checkpoint_text.strip())

            carry_parts = []
            if missing:
                carry_parts.append(f"需补充: {', '.join(missing[:2])}")
            if weak_subplots:
                carry_parts.append(f"需加强: {', '.join(weak_subplots[:2])}")
            if carry_parts:
                self._panel.update_carry_forward(" | ".join(carry_parts))

    def _update_badge(
        self,
        indexed_chapters: int,
        motifs: list[Any],
        visible_motifs: list[Any] | None = None,
    ) -> None:
        """Update the memory status badge."""
        if indexed_chapters == 0:
            self._badge.set_memory_status("inactive")
            self._badge.setText("记忆未激活")
        elif len(motifs) > 0:
            self._badge.set_memory_status("active")
            total = len(motifs)
            visible = len(visible_motifs) if visible_motifs is not None else total
            if visible == 0 and total > 0:
                self._badge.set_memory_status("warning")
                self._badge.setText(f"母题 相关0 · 总{total}")
            elif visible != total:
                self._badge.setText(f"母题 相关{visible} · 总{total}")
            else:
                self._badge.setText(f"母题 {total}")
        else:
            self._badge.set_memory_status("warning")
            self._badge.setText(f"已索引 {indexed_chapters} 章")

    def _update_from_store(self) -> None:
        """Update panel from UIStore."""
        if not self._current_project:
            return

        status = self._store.memory_status(self._current_project)
        if status:
            self.update_memory_status(status)

        audit_result = self._store.audit_result(self._current_project, self._current_chapter)
        if audit_result:
            self.update_from_audit_result(audit_result)
        else:
            self._panel.set_empty_continuity()

    def on_chapter_changed(self, chapter_number: int) -> None:
        """Handle chapter change event."""
        self._current_chapter = chapter_number
        self._update_from_store()

    def set_motif_related_lookback_chapters(self, lookback: int) -> None:
        """Set motif related window (previous N chapters + current chapter)."""
        normalized = max(0, min(20, int(lookback)))
        if normalized == self._motif_related_lookback_chapters:
            return
        self._motif_related_lookback_chapters = normalized
        self._update_from_store()

    def motif_related_lookback_chapters(self) -> int:
        """Current motif related window size."""
        return self._motif_related_lookback_chapters

    def reset(self) -> None:
        """Reset the panel to empty state."""
        self._current_project = None
        self._current_chapter = 0
        self._badge.set_memory_status("inactive")
        self._badge.setText("记忆")
        self._panel.set_empty_overview()
        self._panel.set_empty_motifs()
        self._panel.set_empty_relations()
        self._panel.set_empty_continuity()
        self._panel.update_story_control("")

    def get_unified_panel(self) -> UnifiedMemoryPanel:
        """Get the unified panel widget."""
        return self._panel

    def get_checkpoint_widget(self) -> Any:
        """Get the checkpoint card widget."""
        return self._panel.get_checkpoint_widget()

    def get_carry_forward_widget(self) -> Any:
        """Get the carry forward card widget."""
        return self._panel.get_carry_forward_widget()

    def get_suggestions_widget(self) -> Any:
        """Get the suggestions card widget."""
        return self._panel.get_suggestions_widget()

    def get_scores_widget(self) -> Any:
        """Get the scores card widget."""
        return self._panel.get_scores_widget()

    def get_relations_widget(self) -> Any:
        """Get the relations card widget."""
        return self._panel.get_relations_widget()

    def get_relations_action_button(self) -> Any:
        """Get the relations action button."""
        return self._panel.get_relations_action_button()

    def get_relations_extract_button(self) -> Any:
        """Get the relations extract/re-extract button."""
        return self._panel.get_relations_extract_button()

    def update_relations_extract_hint(self, text: str, is_warning: bool = False) -> None:
        """Update the relations extract hint label."""
        self._panel.update_relations_extract_hint(text, is_warning)

    def get_motif_repair_button(self) -> Any:
        """Get the motif history repair button."""
        return self._panel.get_motif_repair_button()

    def get_motif_lookback_spinbox(self) -> Any:
        """Get the motif related-lookback spinbox."""
        return self._panel.get_motif_lookback_spinbox()

    def set_motif_lookback_chapters(self, value: int) -> None:
        """Set motif related-lookback value on panel control."""
        self._panel.set_motif_lookback_chapters(value)

    def update_motif_repair_hint(self, text: str, is_warning: bool = False) -> None:
        """Update the motif history repair hint label."""
        self._panel.update_motif_repair_hint(text, is_warning)

    def get_repair_button(self) -> Any:
        """Get the repair button."""
        return self._panel.get_repair_button()

    def get_reevaluate_button(self) -> Any:
        """Get the re-evaluate button."""
        return self._panel.get_reevaluate_button()

    def get_continuity_layout(self) -> Any:
        """Get the continuity issues layout."""
        return self._panel.get_continuity_layout()

    def get_causal_layout(self) -> Any:
        """Get the causal issues layout."""
        return self._panel.get_causal_layout()

    def render_relationship_card(
        self,
        workspace: Any,
        studio: Any,
    ) -> None:
        """Populate the compact relationship card in the memory panel."""
        if studio is None or workspace is None:
            return

        project_id = studio.project_id
        if not project_id:
            return

        project_dir = workspace.storage_root / project_id
        try:
            from novel_forge.desktop.pages.document_renderers import (
                render_relationship_compact,
            )
            from novel_forge.story_kernel.relationship_tracker import (
                build_relationship_overview_sync,
            )

            overview = build_relationship_overview_sync(project_dir)
        except Exception:
            self._panel.update_relations("关系数据暂不可用。")
            return

        if overview.total_relationships == 0:
            self._panel.update_relations("暂未追踪到角色关系。")
            return

        context_texts = [
            studio.current_title,
            studio.current_goal,
            studio.current_outline_summary,
            studio.previous_summary,
            studio.previous_exit_summary,
            " ".join(studio.carry_forward or []),
        ]
        focus_characters = load_chapter_focus_characters(
            project_dir,
            studio.chapter_number,
        )
        html = render_relationship_compact(
            overview,
            studio.chapter_number,
            chapter_context_texts=context_texts,
            focus_characters=focus_characters,
        )
        self._panel.update_relations(html)

    def render_continuity_checklist(
        self,
        studio: Any,
        mode: str,
        jobs: list[Any],
        auto_repair_attempts: dict[tuple[str, int], int],
        max_attempts: int,
        ctx: Any = None,
        queue_auto_submit_repair: Any = None,
        refreshing: bool = False,
    ) -> None:
        """Populate the continuity issues checklist with styled cards."""
        from PySide6.QtCore import QTimer

        from novel_forge.desktop.pages.chapter_studio.autorun import (
            build_continuity_hint,
            default_checked_issue_indices,
            should_auto_submit_repair,
        )

        if refreshing:
            self._panel.set_refreshing_continuity()
            self._panel.set_repair_section_visible(False)
            return

        previous_checked = set(self.selected_issue_indices())
        all_issues = studio.continuity_issues if studio else []

        # Filter by display min severity (UI-only, does not affect stored reports).
        # Track original indices so checkboxes store positions into the full report,
        # not positions into the filtered list — which would cause wrong issues to be repaired.
        from novel_forge.common.constants import severity_at_least

        _min_sev_str = (
            getattr(get_settings(), "repair_display_min_severity", "low") or "low"
        ).lower()
        if _min_sev_str != "low":
            _indexed = [
                (i, iss)
                for i, iss in enumerate(all_issues)
                if severity_at_least((iss.get("severity") or "low").lower(), _min_sev_str)
            ]
            issues = [iss for _, iss in _indexed]
            orig_indices: list[int] | None = [i for i, _ in _indexed]
        else:
            issues = all_issues
            orig_indices = None

        if not issues:
            self._panel.set_continuity_section_visible(False)
            self._panel.set_repair_section_visible(False)
            return

        self._panel.set_continuity_section_visible(True)
        self._panel.set_repair_section_visible(True)

        last_repair_job = next((job for job in jobs if job.kind == "repair_continuity"), None)
        chapter_key = (studio.project_id, studio.chapter_number) if studio else ("_", 0)
        attempts = auto_repair_attempts.get(chapter_key, 0)

        hint_state = build_continuity_hint(
            last_repair_job=last_repair_job,
            attempts=attempts,
            max_attempts=max_attempts,
        )
        self._panel.update_repair_hint(hint_state.text, hint_state.warning)

        checked_indices_filtered = default_checked_issue_indices(
            mode=mode,
            issues=issues,
            previous_checked=previous_checked,
            orig_indices=orig_indices,
        )
        # checked_indices_filtered is already in original-index space (see default_checked_issue_indices)
        checked_indices = checked_indices_filtered

        issue_type_map = {}
        for filtered_pos, issue in enumerate(issues):
            orig_idx = orig_indices[filtered_pos] if orig_indices is not None else filtered_pos
            issue_type_map[orig_idx] = issue.get("issue_type") or ""

        self._panel.update_continuity_issues(
            issues,
            checked_indices=checked_indices,
            issue_type_map=issue_type_map,
            orig_indices=orig_indices,
        )

        if ctx is not None and should_auto_submit_repair(ctx) and queue_auto_submit_repair:
            QTimer.singleShot(0, queue_auto_submit_repair)

    def render_causal_checklist(self, studio: Any, refreshing: bool = False) -> None:
        """Populate the causal chain issues panel with checkboxes for repair selection."""
        if refreshing:
            self._panel.set_refreshing_causal()
            return
        issues = studio.causal_issues if studio else []
        if not issues:
            self._panel.set_causal_section_visible(False)
            return

        self._panel.set_causal_section_visible(True)
        self._panel.set_repair_section_visible(True)

        previous_checked = set(self.selected_causal_issue_indices())

        checked_indices: set[int] = set()
        if previous_checked:
            checked_indices = previous_checked
        else:
            checked_indices = set(range(len(issues)))

        issue_type_map = {}
        for index, issue in enumerate(issues):
            issue_type = issue.get("issue_type") or ""
            issue_type_map[index] = issue_type

        status_hint = _resolve_causal_status_hint(studio, issues)
        self._panel.update_causal_issues(
            issues,
            issue_type_map=issue_type_map,
            checked_indices=checked_indices,
            status_hint=status_hint,
        )

    def selected_issue_indices(self) -> list[int]:
        """Return the currently checked issue indices from the rendered cards."""
        return self._panel.selected_issue_indices()

    def selected_causal_issue_indices(self) -> list[int]:
        """Return the currently checked causal issue indices from the rendered cards."""
        return self._panel.selected_causal_issue_indices()

    def selected_issue_signatures(self) -> list[str]:
        return self._panel.selected_issue_signatures()

    def selected_causal_issue_signatures(self) -> list[str]:
        return self._panel.selected_causal_issue_signatures()

    def get_exhausted_issues(self, attempt_counters: dict[str, int]) -> list[str]:
        return self._panel.get_exhausted_issues(attempt_counters)

    def has_unselected_issues(self, issues: list[dict[str, Any]]) -> bool:
        """Return True when continuity issues exist and none are selected."""
        return bool(issues) and not self.selected_issue_indices()
