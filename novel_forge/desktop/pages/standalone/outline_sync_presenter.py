"""Presenter for the "同步契约" (sync chapter contracts) flow.

Encapsulates the user-facing interaction logic for the 卷帙 → 章节大纲
→ 润色 mode "同步契约" button.  The presenter is intentionally kept free
of Qt-aware decision making where possible so it can be unit-tested
with a minimal in-memory ``storage`` stub.

Flow:

1. Load the latest ``outline.json`` and ``chapter_contracts.json`` from
   disk.
2. Build a sync preview via
   :func:`chapter_contract_sync.build_sync_preview`.
3. Show a confirmation dialog (affected + cascade + stale files) with
   two actions: "查看差异" and "立即同步".
4. If the user asks for a diff, show a field-level diff dialog and
   confirm again.
5. Emit / return a fully-formed
   :class:`SyncChapterContractsRequest` for the widget to dispatch.
"""

from __future__ import annotations

import difflib
import html
import json
import logging
from pathlib import Path
from typing import Any

from novel_forge.desktop.components.sizing import smart_dialog_size
from novel_forge.pipeline.long.services.chapter_contract_sync import (
    build_sync_preview,
    collect_downstream_stale_paths,
    compute_outline_chapter_snapshots,
)
from novel_forge.workspace.contracts import SyncChapterContractsRequest

_log = logging.getLogger(__name__)


# Fields rendered in the diff preview.  Order matches the schema's
# declaration order for visual stability.
_DIFF_FIELDS: tuple[str, ...] = (
    "entry_state_requirements",
    "required_events",
    "required_progressions",
    "allowed_progressions",
    "forbidden_progressions",
    "exit_state_targets",
    "completion_criteria",
    "future_leak_risks",
)
_OUTLINE_DIFF_FIELDS: tuple[str, ...] = (
    "title",
    "goal",
    "main_plot_points",
    "beats_summary",
    "subplot_points",
    "subplot_focus",
    "element_focus",
    "pov_character",
    "setting",
    "expected_word_count",
    "involved_characters",
    "time_anchor",
    "time_span",
    "time_gap_from_prev",
    "countdown_state",
    "expected_hook",
    "expected_payoffs",
)


class ChapterContractSyncPresenter:
    """Drives the 同步契约 confirmation + diff flow.

    Parameters
    ----------
    parent:
        QWidget used as the parent for any modal dialogs.
    storage:
        ``FileSystemStorage``-compatible object exposing
        ``load_json`` / ``save_json`` / ``exists``.  May be a stub during
        tests; the presenter only calls ``load_json``.
    layout_root:
        Project root directory (typically the parent of ``outline.json``).
    project_id:
        Project identifier; stored on the returned request.
    cascade_downstream, max_cascade_depth:
        Defaults used to populate the returned request.
    """

    def __init__(
        self,
        *,
        parent: Any,
        storage: Any,
        layout_root: Path,
        project_id: str,
        cascade_downstream: bool = True,
        max_cascade_depth: int = 3,
        explicit_affected_numbers: list[int] | None = None,
    ) -> None:
        self._parent = parent
        self._storage = storage
        self._layout_root = Path(layout_root)
        self._project_id = project_id
        self._cascade_downstream = cascade_downstream
        self._max_cascade_depth = max_cascade_depth
        self._explicit_affected_numbers = [
            int(number) for number in (explicit_affected_numbers or []) if int(number or 0) > 0
        ]

    # ── Public API ───────────────────────────────────────────────────────

    def run(self) -> SyncChapterContractsRequest | None:
        """Execute the full confirmation flow.

        Returns the request the caller should dispatch, or ``None`` if
        the user cancelled at any step.
        """
        outline_data = self._load_outline()
        if outline_data is None:
            self._show_critical(
                "无法加载 outline.json",
                "未找到项目大纲文件,请先在 卷帙 页面编辑或保存大纲。",
            )
            return None

        chapter_contracts = self._load_chapter_contracts()
        cached_fingerprint = self._read_cached_fingerprint(chapter_contracts)
        cached_chapter_fingerprints = self._read_cached_chapter_fingerprints(chapter_contracts)

        # Build preview (this never touches the LLM).
        try:
            from novel_forge.core.schemas.outline import StoryOutline

            outline = StoryOutline.model_validate(outline_data)
        except Exception as exc:  # noqa: BLE001
            self._show_critical(
                "outline 解析失败",
                f"无法解析当前大纲:{exc}",
            )
            return None

        preview = build_sync_preview(
            outline,
            chapter_contracts,
            cached_fingerprint,
            cascade_downstream=self._cascade_downstream,
            max_cascade_depth=self._max_cascade_depth,
            cached_chapter_fingerprints=cached_chapter_fingerprints,
            explicit_affected=self._explicit_affected_numbers,
        )

        if preview.get("requires_manual_scope") and not preview.get("focus"):
            self._show_info(
                "需要选择同步范围",
                "当前项目缺少章节级同步基线，无法安全自动判断局部范围。"
                "请在章节大纲中勾选需要同步的章节后再点击同步契约。",
            )
            return None

        if not preview["focus"]:
            self._show_info(
                "无需同步",
                "当前 outline 与最近一次同步时的指纹一致,没有需要刷新的章节。",
            )
            return None

        # 1. Confirmation dialog
        choice = self._show_confirm_dialog(preview)
        if choice == "cancel":
            return None
        if choice == "view_diff":
            diff_choice = self._show_diff_dialog(preview, chapter_contracts, outline_data)
            if diff_choice != "confirm":
                return None

        # 2. Build request
        return SyncChapterContractsRequest(
            project_id=self._project_id,
            affected_chapter_numbers=preview["affected"],
            cascade_downstream=self._cascade_downstream,
            rebuild_milestones=True,
            mark_stale=True,
            prose_untouched=True,
            max_cascade_depth=self._max_cascade_depth,
            cached_outline_fingerprint=cached_fingerprint or None,
        )

    # ── Load helpers ─────────────────────────────────────────────────────

    def _load_outline(self) -> dict[str, Any] | None:
        path = self._layout_root / "outline.json"
        return self._safe_load_json(path)

    def _load_chapter_contracts(self) -> dict[str, Any]:
        path = self._layout_root / "plans" / "chapter_contracts.json"
        data = self._safe_load_json(path)
        if data is None:
            return {"chapter_contracts": []}
        return data

    def _read_cached_fingerprint(self, chapter_contracts: dict[str, Any]) -> str:
        metadata = chapter_contracts.get("sync_metadata")
        if not isinstance(metadata, dict):
            return ""
        return str(metadata.get("outline_fingerprint") or "")

    def _read_cached_chapter_fingerprints(
        self,
        chapter_contracts: dict[str, Any],
    ) -> dict[str, str]:
        metadata = chapter_contracts.get("sync_metadata")
        if not isinstance(metadata, dict):
            return {}
        raw = metadata.get("outline_chapter_fingerprints")
        if not isinstance(raw, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in raw.items()
            if str(key).strip() and str(value).strip()
        }

    def _read_cached_outline_snapshots(
        self,
        chapter_contracts: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        metadata = chapter_contracts.get("sync_metadata")
        if not isinstance(metadata, dict):
            return {}
        raw = metadata.get("outline_chapter_snapshots")
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}

    def _safe_load_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            data = self._storage.load_json(path)
            return data if isinstance(data, dict) else None
        except Exception as exc:  # noqa: BLE001
            _log.debug("Presenter: failed to load %s: %s", path, exc)
            return None

    # ── Dialogs ─────────────────────────────────────────────────────────

    def _show_confirm_dialog(self, preview: dict[str, Any]) -> str:
        from PySide6.QtWidgets import QMessageBox

        from novel_forge.desktop.components import (
            MessageBoxAction,
            show_message_box,
        )

        affected = preview.get("affected") or []
        cascade = preview.get("cascade") or []
        focus = preview.get("focus") or []
        # Approximate stale-file count for the confirmation copy.
        try:
            from novel_forge.persistence.models import ProjectLayout

            layout = ProjectLayout(self._layout_root)
            stale_paths = collect_downstream_stale_paths(
                layout,
                set(affected),
                set(cascade),
            )
        except Exception:  # noqa: BLE001
            stale_paths = []
        stale_count = len(stale_paths)

        text = f"本次同步会刷新 {len(focus)} 个章节契约({len(affected)} 直接 + {len(cascade)} 级联)"
        informative = (
            f"受影响章节: {affected or '自动检测'}\n"
            f"级联章节: {cascade or '无'}\n"
            f"软过期提示产物: {stale_count} 个\n"
            "不会修改正文、canon、memory、narrative_state。\n"
            "确认后会提交后台同步任务；完成前同项目章台生成会等待写任务冲突解除。"
        )
        return show_message_box(
            self._parent,
            title="确认同步章节契约",
            text=text,
            informative_text=informative,
            icon=QMessageBox.Icon.Question,
            actions=(
                MessageBoxAction(
                    "view_diff",
                    "查看差异",
                    QMessageBox.ButtonRole.ActionRole,
                    "secondary",
                ),
                MessageBoxAction(
                    "confirm",
                    "立即同步",
                    QMessageBox.ButtonRole.AcceptRole,
                    "primary",
                    True,
                ),
                MessageBoxAction(
                    "cancel",
                    "取消",
                    QMessageBox.ButtonRole.RejectRole,
                    "secondary",
                ),
            ),
            escape_key="cancel",
        )

    def _show_diff_dialog(
        self,
        preview: dict[str, Any],
        chapter_contracts: dict[str, Any],
        outline_data: dict[str, Any],
    ) -> str:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QLabel,
            QTextBrowser,
            QVBoxLayout,
        )

        from novel_forge.desktop.pages.document_renderer.incremental import (
            IncrementalDocumentRenderer,
        )

        old_snapshots = self._read_cached_outline_snapshots(chapter_contracts)
        try:
            from novel_forge.core.schemas.outline import StoryOutline

            outline = StoryOutline.model_validate(outline_data)
            new_snapshots = compute_outline_chapter_snapshots(outline)
        except Exception:  # noqa: BLE001
            new_snapshots = {}

        html_body = self._render_outline_diff_html(
            preview,
            old_snapshots,
            new_snapshots,
            outline_data,
        )

        dialog = QDialog(self._parent)
        dialog.setObjectName("appDialog")
        dialog.setWindowTitle("大纲差异预览")
        dialog.setMinimumSize(780, 520)
        dialog.resize(*smart_dialog_size(dialog, 980, 660))
        dialog.setSizeGripEnabled(True)
        dialog.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)

        root = QVBoxLayout(dialog)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        title = QLabel("大纲字段差异")
        title.setObjectName("dialogTitle")
        root.addWidget(title)

        headline = QLabel(
            "绿色为新增或调整，红色删除线为移除；同步只会刷新下列章节契约。确认后提交后台任务。"
        )
        headline.setObjectName("dialogText")
        headline.setWordWrap(True)
        root.addWidget(headline)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        IncrementalDocumentRenderer(browser).update_content(html_body)
        root.addWidget(browser, 1)

        buttons = QDialogButtonBox()
        confirm = buttons.addButton("提交后台同步", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        confirm.setObjectName("actionButton")
        confirm.setProperty("variant", "primary")
        cancel.setObjectName("actionButton")
        cancel.setProperty("variant", "secondary")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        root.addWidget(buttons)

        return "confirm" if dialog.exec() == QDialog.DialogCode.Accepted else "cancel"

    def _render_chapter_diff_lines(
        self,
        chapter_number: int,
        old_contract: dict[str, Any],
        outline_data: dict[str, Any],
    ) -> list[str]:
        """Produce human-readable diff HTML lines for a single chapter.

        We diff the *current outline* (treated as the new source of
        truth) against the *current contract* (treated as old).  The
        LLM has not yet been called.
        """
        new_chapter = _find_outline_chapter(outline_data, chapter_number)
        if new_chapter is None:
            return [f"<i>未在 outline 中找到第 {chapter_number} 章。</i>"]
        title = str(new_chapter.get("title") or "")
        goal = str(new_chapter.get("goal") or "")
        new_plot_points = _normalize_list(new_chapter.get("main_plot_points"))
        new_beats = _normalize_list(new_chapter.get("beats_summary"))

        lines: list[str] = []
        if title:
            lines.append(f"<b>标题:</b> {html.escape(title)}")
        if goal:
            lines.append(f"<b>目标:</b> {html.escape(goal)}")
        for field in _DIFF_FIELDS:
            old_value = old_contract.get(field)
            old_str = _stringify_field(old_value)
            if field in ("required_events", "required_progressions"):
                new_str = "；".join(new_plot_points + new_beats)
            elif field == "exit_state_targets":
                hook = new_chapter.get("expected_hook") or {}
                new_str = _stringify_field(hook.get("hook_description"))
            else:
                new_str = old_str
            if old_str == new_str:
                continue
            lines.append(
                f"<div style='margin:6px 0;'>"
                f"<b>{_field_label(field)}:</b><br/>"
                f"<span style='background:rgba(192,57,43,0.10);"
                f"text-decoration:line-through;'>旧: {html.escape(old_str) or '<i>(空)</i>'}</span><br/>"
                f"<span style='background:rgba(39,174,96,0.12);color:#1e8449;'>新: {html.escape(new_str) or '<i>(空)</i>'}</span>"
                f"</div>"
            )
        return lines or ["<i>未检测到差异。</i>"]

    def _render_outline_diff_html(
        self,
        preview: dict[str, Any],
        old_snapshots: dict[str, dict[str, Any]],
        new_snapshots: dict[str, dict[str, Any]],
        outline_data: dict[str, Any],
    ) -> str:
        focus = [int(n) for n in preview.get("focus", []) or [] if int(n or 0) > 0]
        affected = set(int(n) for n in preview.get("affected", []) or [] if int(n or 0) > 0)
        cascade = set(int(n) for n in preview.get("cascade", []) or [] if int(n or 0) > 0)
        if not focus:
            return "<p>没有需要同步的章节。</p>"

        sections: list[str] = []
        has_baseline = bool(old_snapshots)
        for chapter_number in focus:
            new_snapshot = (
                new_snapshots.get(str(chapter_number))
                or _find_outline_chapter(
                    outline_data,
                    chapter_number,
                )
                or {}
            )
            old_snapshot = old_snapshots.get(str(chapter_number), {})
            title = html.escape(str(new_snapshot.get("title") or "未命名"), quote=True)
            badge = "直接变更" if chapter_number in affected else "直接下游"
            if chapter_number in cascade:
                badge = "直接下游"
            fields_html: list[str] = []
            for field in _OUTLINE_DIFF_FIELDS:
                new_text = _stringify_field(new_snapshot.get(field))
                old_text = _stringify_field(old_snapshot.get(field)) if has_baseline else ""
                if has_baseline and old_text == new_text:
                    continue
                value_html = (
                    compute_diff_html(old_text, new_text)
                    if has_baseline
                    else html.escape(new_text or "(空)", quote=True)
                )
                fields_html.append(
                    "<div class='field'>"
                    f"<div class='field-title'>{_field_label(field)}</div>"
                    f"<div class='field-body'>{value_html or '<i>(空)</i>'}</div>"
                    "</div>"
                )
            if not fields_html:
                fields_html.append("<div class='empty'>未检测到大纲字段差异。</div>")
            sections.append(
                "<section class='chapter'>"
                f"<h3>第 {chapter_number} 章 · {title} <span>{badge}</span></h3>"
                f"{''.join(fields_html)}"
                "</section>"
            )

        baseline_note = (
            "已基于上次同步快照显示旧/新差异。"
            if has_baseline
            else "当前项目缺少旧快照，仅显示本次将用于同步的大纲字段。"
        )
        return (
            "<style>"
            "body { margin:0; font-family:system-ui,-apple-system,sans-serif;"
            " color:#2f261f; background:#fffdf9; }"
            ".note { color:#806b59; font-size:12px; margin:0 0 12px; line-height:1.55; }"
            ".chapter { margin:0 0 12px; padding:12px;"
            " background:#fffaf3; border:1px solid rgba(141,107,76,0.18);"
            " border-radius:8px; }"
            "h3 { margin:0 0 8px; font-size:14px; color:#2c241e; }"
            "h3 span { margin-left:8px; color:#8e3e24; font-size:11px; }"
            ".field { margin-top:8px; padding-top:8px;"
            " border-top:1px solid rgba(141,107,76,0.12); }"
            ".field-title { margin-bottom:4px; font-size:11px; color:#8b7460; font-weight:700; }"
            ".field-body { font-size:12px; line-height:1.72; white-space:normal; }"
            ".empty { color:#8b7460; font-size:12px; }"
            "</style>"
            f"<p class='note'>{baseline_note} 同步不会修改正文、canon、memory、narrative_state。</p>"
            f"{''.join(sections)}"
        )

    def _show_info(self, title: str, text: str) -> None:
        from novel_forge.desktop.components import show_info_message

        show_info_message(self._parent, title, text)

    def _show_critical(self, title: str, text: str) -> None:
        from novel_forge.desktop.components import show_critical_message

        show_critical_message(self._parent, title, text)


# ── Pure helpers (importable for tests) ─────────────────────────────────────


def _find_outline_chapter(
    outline_data: dict[str, Any], chapter_number: int
) -> dict[str, Any] | None:
    for ch in outline_data.get("chapters", []) or []:
        if not isinstance(ch, dict):
            continue
        try:
            n = int(ch.get("chapter_number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if n == chapter_number:
            return ch
    return None


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                out.append(text)
        return out
    text = str(value or "").strip()
    return [text] if text else []


def _stringify_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "；".join(str(v) for v in value if str(v or "").strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _field_label(field: str) -> str:
    return {
        "title": "标题",
        "goal": "章节目标",
        "main_plot_points": "主线要点",
        "beats_summary": "节拍摘要",
        "subplot_points": "支线要点",
        "subplot_focus": "支线焦点",
        "element_focus": "元素焦点",
        "pov_character": "POV 角色",
        "setting": "场景/地点",
        "expected_word_count": "目标字数",
        "involved_characters": "涉及角色",
        "time_anchor": "时间锚点",
        "time_span": "章内跨度",
        "time_gap_from_prev": "与上章时间差",
        "countdown_state": "倒计时状态",
        "expected_hook": "章尾钩子",
        "expected_payoffs": "微兑现",
        "entry_state_requirements": "入场状态",
        "required_events": "必须事件",
        "required_progressions": "必须推进",
        "allowed_progressions": "允许推进",
        "forbidden_progressions": "禁止推进",
        "exit_state_targets": "出场状态",
        "completion_criteria": "完成判据",
        "future_leak_risks": "未来泄露风险",
    }.get(field, field)


__all__ = [
    "ChapterContractSyncPresenter",
    "compute_diff_html",
    "compute_unified_diff",
]


# Lightweight pure helpers exposed for unit tests.
def compute_unified_diff(old: str, new: str) -> str:
    """Compute a unified diff between two strings (test helper)."""
    return "\n".join(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            lineterm="",
            n=2,
        )
    )


def compute_diff_html(old: str, new: str) -> str:
    """Compute inline HTML diff using difflib (test helper)."""
    sm = difflib.SequenceMatcher(None, old or "", new or "")
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            parts.append(html.escape((old or "")[i1:i2], quote=True))
        elif tag == "delete":
            parts.append(
                f"<span style='background:rgba(192,57,43,0.10);"
                f"text-decoration:line-through;'>"
                f"{html.escape((old or '')[i1:i2], quote=True)}</span>"
            )
        elif tag == "insert":
            parts.append(
                f"<span style='background:rgba(39,174,96,0.12);"
                f"color:#1e8449;font-weight:500;'>"
                f"{html.escape((new or '')[j1:j2], quote=True)}</span>"
            )
        elif tag == "replace":
            parts.append(
                f"<span style='background:rgba(192,57,43,0.10);"
                f"text-decoration:line-through;'>"
                f"{html.escape((old or '')[i1:i2], quote=True)}</span>"
            )
            parts.append(
                f"<span style='background:rgba(39,174,96,0.12);"
                f"color:#1e8449;font-weight:500;'>"
                f"{html.escape((new or '')[j1:j2], quote=True)}</span>"
            )
    return "".join(parts)
