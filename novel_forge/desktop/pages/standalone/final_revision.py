"""Final-draft revision widgets for the project reader."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.app_service.selection_revision import (
    SelectionRevisionInput,
    generate_selection_revision_candidate,
)
from novel_forge.common.constants import TaskType
from novel_forge.core.domain.guardrails import detect_prompt_leaks
from novel_forge.core.parsing.text_utils import extract_text_content
from novel_forge.core.utils.text_validation import (
    assess_word_count,
    count_chapter_words,
    display_word_count,
)
from novel_forge.desktop.components.sizing import smart_dialog_size
from novel_forge.desktop.pages.document_renderers import render_chapter_prose
from novel_forge.desktop.thread_pools import desktop_thread_pools
from novel_forge.desktop.widgets import (
    ActionButton,
    MessageBoxAction,
    show_info_message,
    show_message_box,
    show_warning_message,
)
from novel_forge.desktop.workers import BaseJobWorker, BaseJobWorkerSignals
from novel_forge.gateway.text_retry import call_text_with_retry
from novel_forge.gateway.types import ModelRequest
from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_text
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    InvalidationScope,
    invalidate_downstream_generated_artifacts,
    resolve_manual_invalidation_range,
)
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.workspace.contracts import ReevaluateChapterRequest
from novel_forge.workspace.runtime import create_runtime_services

_CONTEXT_CHARS = 900
_STYLE_CONTEXT_CHARS = 3600
_MAX_SELECTION_CHARS = 12000
_DEFAULT_REVISION_DIRECTIONS = (
    "更凝练",
    "更有画面",
    "增强张力",
    "更顺畅",
    "保留事实微调",
)
_REVISION_DIFF_MAX_LINES = 260


def _text_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _count_display_words(text: str) -> int:
    return display_word_count(text)


@dataclass(frozen=True)
class _RevisionGuardReport:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    word_count: int
    expected_word_count: int
    min_acceptable: int
    text_hash: str
    prompt_leaks: tuple[str, ...]


@dataclass(frozen=True)
class _RevisionVersionRecord:
    timestamp: str
    version_dir: Path
    before_path: Path
    after_path: Path
    meta_path: Path
    previous_hash: str
    current_hash: str


def _build_revision_guard_report(
    text: str,
    *,
    previous_text: str = "",
    expected_word_count: int = 0,
) -> _RevisionGuardReport:
    prompt_leaks = tuple(detect_prompt_leaks(text, max_hits=8))
    word_count = count_chapter_words(text)
    previous_count = count_chapter_words(previous_text) if previous_text else 0
    errors: list[str] = []
    warnings: list[str] = []

    if prompt_leaks:
        errors.append("检测到疑似提示词/规划语句泄露，请清理后再保存。")

    min_acceptable = 0
    if expected_word_count > 0:
        min_acceptable = max(500, int(expected_word_count * 0.3))
    elif previous_count >= 500:
        min_acceptable = max(120, int(previous_count * 0.3))

    if min_acceptable and word_count < min_acceptable:
        errors.append(f"正文过短：当前约 {word_count} 字，最低需要 {min_acceptable} 字。")

    if expected_word_count > 0:
        assessment = assess_word_count(text, expected_word_count)
        if assessment.band in {"structural", "hard_reject"}:
            warnings.append(
                "字数与章节目标偏离较大："
                f"当前约 {assessment.actual} 字，目标 {assessment.target} 字。"
            )

    if previous_count and word_count:
        delta_ratio = abs(word_count - previous_count) / max(previous_count, 1)
        if delta_ratio >= 0.25:
            warnings.append(
                f"字数变化较大：保存前约 {previous_count} 字，保存后约 {word_count} 字。"
            )

    return _RevisionGuardReport(
        ok=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        word_count=word_count,
        expected_word_count=max(0, int(expected_word_count or 0)),
        min_acceptable=min_acceptable,
        text_hash=_text_hash(text),
        prompt_leaks=prompt_leaks,
    )


def _revision_unified_diff(before: str, after: str) -> str:
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="保存前",
            tofile="保存后",
            lineterm="",
        )
    )
    if len(lines) > _REVISION_DIFF_MAX_LINES:
        hidden = len(lines) - _REVISION_DIFF_MAX_LINES
        lines = lines[:_REVISION_DIFF_MAX_LINES]
        lines.append(f"... 省略 {hidden} 行 diff ...")
    return "\n".join(lines) or "文本内容无可显示差异。"


def _compact_json_payload(data: Any, *, limit: int) -> str:
    if not data:
        return ""
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    except TypeError:
        text = str(data)
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…"


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _chapter_outline_entry(outline: Any, chapter_number: int) -> Any:
    if chapter_number <= 0 or not isinstance(outline, dict):
        return None
    chapters = outline.get("chapters")
    if not isinstance(chapters, list):
        return None
    for item in chapters:
        if not isinstance(item, dict):
            continue
        try:
            if int(item.get("chapter_number", 0) or 0) == chapter_number:
                return item
        except (TypeError, ValueError):
            continue
    return None


def _project_revision_context(project_dir: Path, chapter_number: int) -> str:
    """Load a compact style/theme packet for final-draft local revision."""
    packets: list[str] = []
    for label, filename in (
        ("创作规格", "spec.json"),
        ("世界与主题", "story_bible.json"),
        ("写作风格", "style_profile.json"),
        ("角色语气", "character_bible.json"),
    ):
        payload = _load_json_file(project_dir / filename)
        compact = _compact_json_payload(payload, limit=900)
        if compact:
            packets.append(f"【{label}】\n{compact}")

    outline = _load_json_file(project_dir / "outline.json")
    chapter = _chapter_outline_entry(outline, chapter_number)
    compact_chapter = _compact_json_payload(chapter, limit=900)
    if compact_chapter:
        packets.append(f"【当前章节大纲】\n{compact_chapter}")

    joined = "\n\n".join(packets).strip()
    if len(joined) <= _STYLE_CONTEXT_CHARS:
        return joined
    return joined[:_STYLE_CONTEXT_CHARS].rstrip() + "\n…"


def _parse_direction_response(text: str) -> tuple[list[str], str]:
    cleaned = extract_text_content(text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    def _loads_direction_payload(payload: str) -> Any:
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            start = payload.find("{")
            end = payload.rfind("}")
            if 0 <= start < end:
                try:
                    return json.loads(payload[start : end + 1])
                except json.JSONDecodeError:
                    return None
            return None

    def _normalize_direction(value: Any) -> str:
        direction = str(value or "").strip()
        direction = re.sub(r"^[\-\d.、•\s]+", "", direction).strip()
        direction = direction.strip(" \t\r\n\"'“”‘’「」[]{}")
        direction = re.sub(
            r"^(?:directions?|note|关键词|方向)\s*[:：]\s*", "", direction, flags=re.I
        )
        direction = direction.strip(" \t,，;；")
        if not direction:
            return ""
        if re.fullmatch(r"[\{\}\[\]:,，\"'“”\s]+", direction):
            return ""
        if re.search(r"\b(?:directions?|note)\b", direction, flags=re.I):
            return ""
        if len(direction) > 18:
            return ""
        return direction

    try:
        data = _loads_direction_payload(cleaned)
    except TypeError:
        data = None
    if isinstance(data, dict):
        raw_directions = data.get("directions", [])
        note = str(data.get("note", "") or "").strip()
    else:
        note_match = re.search(r'["“]note["”]\s*:\s*["“]([^"”]+)["”]', cleaned)
        note = note_match.group(1).strip() if note_match else ""
        directions_match = re.search(
            r'["“]directions["”]\s*:\s*\[(?P<body>.*?)\]',
            cleaned,
            flags=re.S | re.I,
        )
        if directions_match:
            body = directions_match.group("body")
            raw_directions = re.findall(r'"([^"]+)"|“([^”]+)”|「([^」]+)」', body)
            raw_directions = [next(part for part in item if part) for item in raw_directions]
        else:
            raw_directions = re.split(r"[\n、,，；;]+", cleaned)

    directions: list[str] = []
    for item in raw_directions if isinstance(raw_directions, list) else []:
        value = _normalize_direction(item)
        if not value or value in directions:
            continue
        directions.append(value)
        if len(directions) >= 5:
            break
    return directions, note


class _SelectionRevisionSignals(BaseJobWorkerSignals):
    result = Signal(str)
    error = Signal(str)


class _DirectionSuggestionSignals(BaseJobWorkerSignals):
    result = Signal(list, str)
    error = Signal(str)


class _DirectionSuggestionWorker(BaseJobWorker):
    """Suggest local revision directions for a selected final-draft passage."""

    signals_cls = _DirectionSuggestionSignals
    pool = "aux"

    def __init__(
        self,
        *,
        project_dir: Path,
        project_id: str,
        chapter_number: int,
        chapter_title: str,
        selected_text: str,
        before_context: str,
        after_context: str,
    ) -> None:
        super().__init__()
        self._project_dir = project_dir
        self._project_id = project_id
        self._chapter_number = chapter_number
        self._chapter_title = chapter_title
        self._selected_text = selected_text
        self._before_context = before_context
        self._after_context = after_context

    async def _run_async(self) -> None:
        runtime = create_runtime_services(mock=False)
        try:
            style_context = _project_revision_context(self._project_dir, self._chapter_number)
            chapter_label = (
                f"第 {self._chapter_number} 章" if self._chapter_number > 0 else "短篇正文"
            )
            system = (
                "你是 Novel Forge 的终稿选段火候助手。"
                "根据选中片段、前后文与项目风格，给出适合本段的局部精修方向。"
                "只返回 JSON，不改写正文。"
            )
            user = f"""请分析选中片段适合怎样精修。

## 项目
- project_id: {self._project_id}
- 章节: {chapter_label}
- 标题: {self._chapter_title or "未命名"}

## 风格与主题参考
{style_context or "暂无结构化风格资料。"}

## 选区前文
{self._before_context or "无"}

## 选中文本
{self._selected_text}

## 选区后文
{self._after_context or "无"}

## 输出
返回 JSON：
{{"directions":["2-5 个短关键词，例如 更凝练"],"note":"一句话说明本段最该守住的风格"}}
关键词要短，贴合本段内容；可以包含“更凝练”“更有画面”“增强张力”等，但不要机械固定。
"""
            request = ModelRequest(
                task_type=TaskType.POLISH_CHAPTER,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=calculate_route_aware_max_tokens(
                    runtime.router,
                    TaskType.POLISH_CHAPTER,
                    1200,
                    prompt_overhead=3000,
                    min_tokens=700,
                ),
                temperature=min(
                    float(getattr(runtime.settings, "temp_polish_chapter", 0.35)),
                    0.45,
                ),
            )
            response = await call_text_with_retry(runtime.router, request)
            directions, note = _parse_direction_response(response.content)
            if not directions:
                directions = list(_DEFAULT_REVISION_DIRECTIONS[:3])
            self.signals.result.emit(directions, note)
        except Exception as exc:  # noqa: BLE001 - non-blocking helper, surfaced as hint
            self.signals.error.emit(str(exc))
        finally:
            await runtime.shutdown()


class _SelectionRevisionWorker(BaseJobWorker):
    """Generate an AI rewrite candidate for a selected final-draft passage."""

    signals_cls = _SelectionRevisionSignals
    pool = "aux"

    def __init__(
        self,
        *,
        project_dir: Path,
        project_id: str,
        chapter_number: int,
        chapter_title: str,
        selected_text: str,
        before_context: str,
        after_context: str,
        instruction: str,
    ) -> None:
        super().__init__()
        self._project_dir = project_dir
        self._project_id = project_id
        self._chapter_number = chapter_number
        self._chapter_title = chapter_title
        self._selected_text = selected_text
        self._before_context = before_context
        self._after_context = after_context
        self._instruction = instruction

    async def _run_async(self) -> None:
        runtime = create_runtime_services(mock=False)
        try:
            revised = await generate_selection_revision_candidate(
                runtime.router,
                project_dir=self._project_dir,
                request=SelectionRevisionInput(
                    project_id=self._project_id,
                    chapter_number=self._chapter_number,
                    chapter_title=self._chapter_title,
                    selected_text=self._selected_text,
                    before_context=self._before_context,
                    after_context=self._after_context,
                    instruction=self._instruction,
                ),
                temperature=float(getattr(runtime.settings, "temp_polish_chapter", 0.35)),
            )
            self.signals.result.emit(revised)
        except Exception as exc:  # noqa: BLE001 - surfaced to UI
            self.signals.error.emit(str(exc))
        finally:
            await runtime.shutdown()


@dataclass
class _PendingSelectionRevision:
    start: int
    end: int
    original: str
    replacement: str


@dataclass(frozen=True)
class _SelectionSnapshot:
    start: int
    end: int
    selected: str
    before: str
    after: str


class _SelectionRevisionDialog(QDialog):
    """Dialog for choosing local final-draft revision direction."""

    def __init__(self, *, selection_words: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("选段精修")
        # renamed from selectionRevisionDialog → finalRevisionDialog (unify radii)
        self.setObjectName("finalRevisionDialog")
        self._direction_buttons: list[QPushButton] = []
        self._spinner_frames = ("◐", "◓", "◑", "◒")
        self._spinner_index = 0
        self.setFixedSize(560, 300)
        self.setSizeGripEnabled(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel(f"选中约 {selection_words:,} 字")
        title.setObjectName("revisionDialogTitle")
        layout.addWidget(title)

        hint_row = QWidget()
        hint_layout = QHBoxLayout(hint_row)
        hint_layout.setContentsMargins(0, 0, 0, 0)
        hint_layout.setSpacing(6)

        self._spinner = QLabel(self._spinner_frames[0])
        self._spinner.setObjectName("revisionDialogSpinner")
        self._spinner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spinner.setFixedWidth(18)
        hint_layout.addWidget(self._spinner)

        self._hint = QLabel("正在析取本段火候…")
        self._hint.setObjectName("revisionDialogHint")
        self._hint.setWordWrap(True)
        self._hint.setFixedHeight(36)
        hint_layout.addWidget(self._hint, 1)
        layout.addWidget(hint_row)

        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(140)
        self._spinner_timer.timeout.connect(self._advance_spinner)
        self._spinner_timer.start()
        self.finished.connect(lambda _code: self._stop_analysis_animation())

        self._chip_host = QWidget()
        self._chip_host.setFixedHeight(34)
        self._chip_row = QHBoxLayout(self._chip_host)
        self._chip_row.setContentsMargins(0, 0, 0, 0)
        self._chip_row.setSpacing(8)
        layout.addWidget(self._chip_host)
        self.set_directions([])

        self._instruction = QPlainTextEdit()
        self._instruction.setObjectName("revisionDirectionInput")
        self._instruction.setPlaceholderText(
            "写下你想要的修订方向，例如：保留含蓄感，把动作写得更克制。"
        )
        self._instruction.setFixedHeight(90)
        layout.addWidget(self._instruction)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_btn is not None:
            ok_btn.setText("生成候选")
        if cancel_btn is not None:
            cancel_btn.setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_directions(self, directions: list[str]) -> None:
        while self._chip_row.count():
            item = self._chip_row.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._direction_buttons = []
        if not directions:
            btn = QPushButton("候选火候生成中")
            btn.setObjectName("revisionDirectionChip")
            btn.setEnabled(False)
            btn.setMaximumWidth(132)
            self._chip_row.addWidget(btn)
            self._chip_row.addStretch()
            return
        for direction in directions[:5]:
            label = direction if len(direction) <= 10 else f"{direction[:10]}…"
            btn = QPushButton(label)
            btn.setObjectName("revisionDirectionChip")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(direction)
            btn.setMaximumWidth(132)
            btn.clicked.connect(
                lambda checked=False, value=direction: self._append_direction(value)
            )
            self._direction_buttons.append(btn)
            self._chip_row.addWidget(btn)
        self._chip_row.addStretch()

    def _advance_spinner(self) -> None:
        self._spinner_index = (self._spinner_index + 1) % len(self._spinner_frames)
        self._spinner.setText(self._spinner_frames[self._spinner_index])

    def _stop_analysis_animation(self) -> None:
        if self._spinner_timer.isActive():
            self._spinner_timer.stop()
        self._spinner.hide()

    def set_analysis_note(self, note: str) -> None:
        self._stop_analysis_animation()
        text = note.strip() or "可直接输入修订方向，也可点选一个火候关键词。"
        self._hint.setText(text)

    def _append_direction(self, direction: str) -> None:
        current = self._instruction.toPlainText().strip()
        if current:
            if direction in current:
                return
            text = f"{current}；{direction}"
        else:
            text = direction
        self._instruction.setPlainText(text)
        cursor = self._instruction.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._instruction.setTextCursor(cursor)

    def instruction(self) -> str:
        text = self._instruction.toPlainText().strip()
        return text or "在不改变事实与剧情的前提下，让文字更自然、更贴合本书文风。"


class _RevisionSavePreviewDialog(QDialog):
    """Preview final-draft diff and guard warnings before writing to disk."""

    def __init__(
        self,
        *,
        report: _RevisionGuardReport,
        diff_text: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("finalRevisionDialog")
        self.setWindowTitle("保存终稿修订")
        self.resize(*smart_dialog_size(self, 840, 560))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        summary = QLabel(
            f"保存后约 {report.word_count:,} 字"
            + (
                f" · 目标 {report.expected_word_count:,} 字"
                if report.expected_word_count > 0
                else ""
            )
        )
        summary.setObjectName("revisionDialogTitle")
        layout.addWidget(summary)

        notes = [*report.warnings]
        notes.append("保存后将标记本章评估/叙事状态需要刷新，并在下一步选择下游影响范围。")
        note = QLabel("\n".join(f"• {item}" for item in notes))
        note.setObjectName("revisionDialogHint")
        note.setWordWrap(True)
        layout.addWidget(note)

        diff_view = QPlainTextEdit()
        diff_view.setReadOnly(True)
        diff_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        diff_view.setPlainText(diff_text)
        layout.addWidget(diff_view, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if save_btn is not None:
            save_btn.setText("确认保存")
        if cancel_btn is not None:
            cancel_btn.setText("继续修订")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class _InvalidationScopeDialog(QDialog):
    """Choose downstream invalidation scope and optional background refresh."""

    def __init__(
        self,
        *,
        chapter_number: int,
        affected_preview: list[int],
        has_volume_scope: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # renamed from invalidationScopeDialog → finalRevisionDialog (unify radii)
        self.setObjectName("finalRevisionDialog")
        self.setWindowTitle("保存影响范围")
        self.resize(*smart_dialog_size(self, 560, 360))
        self._scope_keys = [
            InvalidationScope.NONE,
            InvalidationScope.NEXT,
            InvalidationScope.VOLUME,
            InvalidationScope.DOWNSTREAM,
        ]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel(f"第 {chapter_number} 章终稿已改动")
        title.setObjectName("revisionDialogTitle")
        layout.addWidget(title)

        if affected_preview:
            preview_text = "、".join(f"第 {num} 章" for num in affected_preview[:5])
            if len(affected_preview) > 5:
                preview_text += f" 等 {len(affected_preview)} 章"
        else:
            preview_text = "暂无已完成下游章节"
        hint = QLabel(f"请选择保存后下游链路的处理范围。当前可影响：{preview_text}。")
        hint.setObjectName("revisionDialogHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._scope_group = QButtonGroup(self)
        scope_options = [
            (InvalidationScope.NONE, "不级联", "只记录本章修订痕迹，不标记下游章节。"),
            (InvalidationScope.NEXT, "仅下一章", "仅标记并清理下一章的可恢复生成链路。"),
            (InvalidationScope.VOLUME, "仅本卷", "标记并清理本卷内下游章节的可恢复链路。"),
            (InvalidationScope.DOWNSTREAM, "全下游", "标记并清理所有下游章节，保持旧行为。"),
        ]
        for idx, (scope, label, tooltip) in enumerate(scope_options):
            radio = QRadioButton(label)
            radio.setToolTip(tooltip)
            radio.setProperty("scope", scope.value)
            self._scope_group.addButton(radio, idx)
            if scope is InvalidationScope.DOWNSTREAM:
                radio.setChecked(True)
            if scope is InvalidationScope.VOLUME and not has_volume_scope:
                radio.setEnabled(False)
                radio.setToolTip("当前项目未启用分卷模式，无法选择本卷范围。")
            layout.addWidget(radio)

        self._background_reevaluate = QCheckBox("保存后后台重评本章")
        self._background_reevaluate.setToolTip(
            "后台刷新本章评估报告和检查点摘要；不会直接推进全局叙事状态。"
        )
        layout.addWidget(self._background_reevaluate)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if save_btn is not None:
            save_btn.setText("按此范围保存")
        if cancel_btn is not None:
            cancel_btn.setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_scope(self) -> InvalidationScope:
        checked_id = self._scope_group.checkedId()
        if checked_id < 0:
            return InvalidationScope.DOWNSTREAM
        return self._scope_keys[checked_id]

    def background_reevaluate_requested(self) -> bool:
        return self._background_reevaluate.isChecked()


class FinalRevisionWidget(QWidget):
    """Read/revise surface for a final chapter file."""

    saved = Signal()
    workspace_refresh_requested = Signal()

    def __init__(
        self,
        *,
        project_id: str,
        project_dir: Path,
        chapter_path: Path,
        chapter_number: int,
        title: str,
        initial_text: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("finalRevisionWidget")
        self._project_id = project_id
        self._project_dir = project_dir
        self._chapter_path = chapter_path
        self._chapter_number = chapter_number
        self._title = title
        self._saved_text = initial_text
        self._pending: _PendingSelectionRevision | None = None
        self._worker: _SelectionRevisionWorker | None = None
        self._suggestion_workers: list[_DirectionSuggestionWorker] = []
        self._revision_attention_note = self._revision_status_note(self._saved_text)

        self._build_ui()
        self._load_read_view()
        self._refresh_state()

    def has_unsaved_changes(self) -> bool:
        return self._stack.currentWidget() == self._revision_page and (
            self._editor.toPlainText() != self._saved_text
        )

    def view_state_key(self) -> tuple[str, int, str]:
        """Stable key used by the project reader to restore this view after rebuilds."""
        return (self._project_id, self._chapter_number, str(self._chapter_path))

    def view_state(self) -> dict[str, Any]:
        """Capture non-content UI state that should survive a timer-driven rebuild."""
        read_browser = self._read_host.findChild(QTextEdit, "chapterProseBrowser")
        read_bar = read_browser.verticalScrollBar() if read_browser is not None else None
        editor_bar = self._editor.verticalScrollBar()
        cursor = self._editor.textCursor()
        return {
            "mode": "revision" if self._stack.currentWidget() == self._revision_page else "read",
            "read_scroll": read_bar.value() if read_bar is not None else 0,
            "read_scroll_max": read_bar.maximum() if read_bar is not None else 0,
            "editor_scroll": editor_bar.value(),
            "editor_scroll_max": editor_bar.maximum(),
            "cursor_position": cursor.position(),
            "selection_start": cursor.selectionStart(),
            "selection_end": cursor.selectionEnd(),
        }

    def restore_view_state(self, state: dict[str, Any]) -> None:
        """Restore a view state captured from :meth:`view_state`."""
        if not state:
            return

        def _scaled_scroll(value: Any, old_max: Any, new_max: int) -> int:
            try:
                raw_value = max(0, int(value or 0))
                raw_old_max = max(0, int(old_max or 0))
            except (TypeError, ValueError):
                return 0
            if raw_old_max > 0 and new_max > 0:
                return max(0, min(new_max, round(raw_value / raw_old_max * new_max)))
            return max(0, min(new_max, raw_value))

        def _apply() -> None:
            try:
                if state.get("mode") == "revision":
                    self._editor.blockSignals(True)
                    try:
                        self._editor.setPlainText(self._saved_text)
                    finally:
                        self._editor.blockSignals(False)
                    self._stack.setCurrentWidget(self._revision_page)
                    cursor = self._editor.textCursor()
                    text_len = len(self._editor.toPlainText())
                    start = max(0, min(text_len, int(state.get("selection_start") or 0)))
                    end = max(0, min(text_len, int(state.get("selection_end") or start)))
                    cursor.setPosition(start)
                    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                    if start == end:
                        cursor.setPosition(
                            max(0, min(text_len, int(state.get("cursor_position") or start)))
                        )
                    self._editor.setTextCursor(cursor)
                    bar = self._editor.verticalScrollBar()
                    bar.setValue(
                        _scaled_scroll(
                            state.get("editor_scroll"),
                            state.get("editor_scroll_max"),
                            bar.maximum(),
                        )
                    )
                else:
                    self._stack.setCurrentWidget(self._read_host)
                    read_browser = self._read_host.findChild(QTextEdit, "chapterProseBrowser")
                    if read_browser is not None:
                        bar = read_browser.verticalScrollBar()
                        bar.setValue(
                            _scaled_scroll(
                                state.get("read_scroll"),
                                state.get("read_scroll_max"),
                                bar.maximum(),
                            )
                        )
                self._refresh_state()
            except RuntimeError:
                return

        _apply()
        QTimer.singleShot(0, _apply)

    def save_pending_changes(self) -> bool:
        if not self.has_unsaved_changes():
            return True
        return self._save_revision(silent=True)

    def confirm_close(self) -> bool:
        if not self.has_unsaved_changes():
            return True
        choice = show_message_box(
            self.window(),
            "终稿修订尚未保存",
            "当前终稿有未保存修改。",
            informative_text="可以先保存定稿，也可以放弃本次修订。",
            actions=(
                MessageBoxAction(
                    "save",
                    "保存定稿",
                    role=QMessageBox.ButtonRole.AcceptRole,
                    variant="primary",
                    default=True,
                ),
                MessageBoxAction(
                    "discard",
                    "放弃修改",
                    role=QMessageBox.ButtonRole.DestructiveRole,
                    variant="danger",
                ),
                MessageBoxAction(
                    "cancel",
                    "继续修订",
                    role=QMessageBox.ButtonRole.RejectRole,
                    variant="secondary",
                ),
            ),
            escape_key="cancel",
        )
        if choice == "save":
            return self._save_revision(silent=False)
        if choice == "discard":
            self._discard_revision()
            return True
        return False

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        toolbar = QWidget()
        toolbar.setObjectName("finalRevisionToolbar")
        row = QHBoxLayout(toolbar)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(8)

        self._mode_label = QLabel()
        self._mode_label.setObjectName("revisionModeLabel")
        row.addWidget(self._mode_label)

        self._status = QLabel()
        self._status.setObjectName("revisionStatus")
        row.addWidget(self._status, 1)

        self._enter_btn = ActionButton("入砚修", variant="secondary")
        self._enter_btn.clicked.connect(self._enter_revision)
        row.addWidget(self._enter_btn)

        self._save_btn = ActionButton("保存定稿", variant="primary")
        self._save_btn.clicked.connect(lambda: self._save_revision(silent=False))
        row.addWidget(self._save_btn)

        self._discard_btn = ActionButton("还原本稿", variant="secondary")
        self._discard_btn.clicked.connect(self._discard_revision)
        row.addWidget(self._discard_btn)

        self._ai_btn = ActionButton("选段精修", variant="secondary")
        self._ai_btn.clicked.connect(self._open_selection_revision_dialog)
        row.addWidget(self._ai_btn)

        self._read_btn = ActionButton("回到阅稿", variant="quiet")
        self._read_btn.clicked.connect(self._return_to_reading)
        row.addWidget(self._read_btn)
        root.addWidget(toolbar)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._read_host = QWidget()
        self._read_layout = QVBoxLayout(self._read_host)
        self._read_layout.setContentsMargins(0, 0, 0, 0)
        self._read_layout.setSpacing(0)
        self._stack.addWidget(self._read_host)

        self._revision_page = QWidget()
        revision_layout = QVBoxLayout(self._revision_page)
        revision_layout.setContentsMargins(0, 0, 0, 0)
        revision_layout.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("revisionSplitter")
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)

        self._editor = QTextEdit()
        self._editor.setObjectName("finalRevisionEditor")
        self._editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._editor.customContextMenuRequested.connect(self._show_editor_context_menu)
        self._editor.textChanged.connect(self._refresh_state)
        splitter.addWidget(self._editor)

        proposal_host = QWidget()
        proposal_layout = QVBoxLayout(proposal_host)
        proposal_layout.setContentsMargins(6, 0, 0, 0)
        proposal_layout.setSpacing(6)

        proposal_title = QLabel("精修候选")
        proposal_title.setObjectName("revisionProposalTitle")
        proposal_layout.addWidget(proposal_title)

        self._proposal = QTextEdit()
        self._proposal.setObjectName("revisionProposal")
        self._proposal.setReadOnly(True)
        self._proposal.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        proposal_layout.addWidget(self._proposal, 1)

        proposal_actions = QHBoxLayout()
        proposal_actions.setContentsMargins(0, 0, 0, 0)
        proposal_actions.setSpacing(8)
        self._apply_ai_btn = ActionButton("纳入正文", variant="primary")
        self._apply_ai_btn.clicked.connect(self._apply_ai_revision)
        proposal_actions.addWidget(self._apply_ai_btn)
        clear_btn = ActionButton("收起候选", variant="quiet")
        clear_btn.clicked.connect(self._clear_ai_revision)
        proposal_actions.addWidget(clear_btn)
        proposal_actions.addStretch()
        proposal_layout.addLayout(proposal_actions)
        splitter.addWidget(proposal_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([780, 360])
        revision_layout.addWidget(splitter, 1)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.NoFrame)
        line.setObjectName("revisionBottomRule")
        revision_layout.addWidget(line)
        self._stack.addWidget(self._revision_page)

    def _load_read_view(self) -> None:
        while self._read_layout.count():
            item = self._read_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._read_layout.addWidget(
            render_chapter_prose(
                self._saved_text,
                chapter_num=self._chapter_number,
                title=self._title,
            ),
            1,
        )

    def _refresh_state(self) -> None:
        editing = self._stack.currentWidget() == self._revision_page
        dirty = editing and self._editor.toPlainText() != self._saved_text
        self._mode_label.setText("砚修" if editing else "阅稿")
        self._enter_btn.setVisible(not editing)
        self._save_btn.setVisible(editing)
        self._discard_btn.setVisible(editing)
        self._ai_btn.setVisible(editing)
        self._read_btn.setVisible(editing)
        self._save_btn.setEnabled(dirty)
        self._discard_btn.setEnabled(dirty)
        self._ai_btn.setEnabled(editing and self._worker is None)
        self._apply_ai_btn.setEnabled(self._pending is not None)
        if editing:
            count = _count_display_words(self._editor.toPlainText())
            suffix = " · 亲笔修订未落盘" if dirty else " · 已同步"
            self._status.setText(f"约 {count:,} 字{suffix}")
        else:
            count = _count_display_words(self._saved_text)
            suffix = f" · {self._revision_attention_note}" if self._revision_attention_note else ""
            self._status.setText(f"终稿约 {count:,} 字{suffix}")

    def _enter_revision(self) -> None:
        self._editor.setPlainText(self._saved_text)
        self._clear_ai_revision()
        self._stack.setCurrentWidget(self._revision_page)
        self._refresh_state()
        self._editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def _return_to_reading(self) -> None:
        if not self.confirm_close():
            return
        self._stack.setCurrentWidget(self._read_host)
        self._refresh_state()

    def _discard_revision(self) -> None:
        self._editor.setPlainText(self._saved_text)
        self._clear_ai_revision()
        self._refresh_state()

    def _save_revision(self, *, silent: bool) -> bool:
        text = self._editor.toPlainText()
        if text == self._saved_text:
            self._refresh_state()
            return True
        expected_word_count = self._expected_word_count()
        guard = _build_revision_guard_report(
            text,
            previous_text=self._saved_text,
            expected_word_count=expected_word_count,
        )
        if not guard.ok:
            details = "\n".join(f"• {item}" for item in guard.errors)
            if guard.prompt_leaks:
                samples = "\n".join(f"  - {item}" for item in guard.prompt_leaks[:5])
                details = f"{details}\n\n命中片段：\n{samples}"
            show_warning_message(self.window(), "终稿修订未保存", details)
            return False

        conflict_action = self._handle_active_write_conflict(text, guard, silent=silent)
        if conflict_action == "staged":
            return True
        if conflict_action == "cancel":
            return False

        scope = InvalidationScope.DOWNSTREAM
        background_reevaluate = False
        if not silent:
            if not self._confirm_revision_save(text, guard):
                return False
            options = self._ask_invalidation_options()
            if options is None:
                return False
            scope, background_reevaluate = options
        try:
            self._ensure_pre_save_snapshot()
            version_record = self._write_revision_version(self._saved_text, text, guard)
            atomic_write_text(self._chapter_path, text)
            self._record_manual_edit()
            self._refresh_chain_snapshot()
            self._mark_revision_requires_refresh(version_record, guard, scope=scope)
            self._saved_text = text
            self._revision_attention_note = self._revision_status_note(self._saved_text)
            self._load_read_view()
            self._clear_ai_revision()
            self._refresh_state()
            self.saved.emit()
            self.workspace_refresh_requested.emit()
            if background_reevaluate:
                self._submit_background_reevaluation()
        except Exception as exc:  # noqa: BLE001 - user-facing save failure
            show_warning_message(self.window(), "保存失败", str(exc))
            return False
        if not silent:
            show_info_message(
                self.window(),
                "已保存",
                "砚修后的定稿已写入章节文件，并已记录版本历史。",
                informative_text=self._scope_success_message(scope),
            )
        return True

    def _confirm_revision_save(self, text: str, guard: _RevisionGuardReport) -> bool:
        dialog = _RevisionSavePreviewDialog(
            report=guard,
            diff_text=_revision_unified_diff(self._saved_text, text),
            parent=self.window(),
        )
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _ask_invalidation_options(self) -> tuple[InvalidationScope, bool] | None:
        layout = ProjectLayout(self._project_dir)
        finalized = [
            number
            for path in layout.chapters_dir.glob("chapter_*.md")
            if (number := self._parse_revision_chapter_number(path)) is not None
        ]
        downstream = sorted(number for number in finalized if number > self._chapter_number)
        outline = _load_json_file(self._project_dir / "outline.json")
        has_volume_scope = bool(
            isinstance(outline, dict)
            and outline.get("volume_mode")
            and isinstance(outline.get("volumes"), list)
            and outline.get("volumes")
        )
        dialog = _InvalidationScopeDialog(
            chapter_number=self._chapter_number,
            affected_preview=downstream,
            has_volume_scope=has_volume_scope,
            parent=self.window(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selected_scope(), dialog.background_reevaluate_requested()

    @staticmethod
    def _parse_revision_chapter_number(path: Path) -> int | None:
        match = re.search(r"chapter_(\d+)", path.name)
        if match is None:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _active_write_jobs(self) -> list[Any]:
        window = self.window()
        manager = getattr(window, "_job_manager", None)
        if manager is None or not hasattr(manager, "jobs"):
            return []
        try:
            from novel_forge.desktop.jobs import CHAPTER_WRITE_KINDS
        except Exception:
            return []

        active = {"running", "queued"}
        jobs = []
        for job in manager.jobs():
            status = str(getattr(getattr(job, "status", ""), "value", getattr(job, "status", "")))
            if (
                getattr(job, "project_id", "") == self._project_id
                and getattr(job, "kind", "") in CHAPTER_WRITE_KINDS
                and status in active
            ):
                jobs.append(job)
        return jobs

    def _handle_active_write_conflict(
        self,
        text: str,
        guard: _RevisionGuardReport,
        *,
        silent: bool,
    ) -> str:
        active_jobs = self._active_write_jobs()
        if not active_jobs:
            return "continue"

        if silent:
            self._write_staged_revision(text, guard, status="staged_silent_conflict")
            return "staged"

        labels = "、".join(
            str(getattr(job, "label", "") or getattr(job, "kind", "")) for job in active_jobs[:3]
        )
        choice = show_message_box(
            self.window(),
            "后台写作任务正在运行",
            "当前项目仍有后台任务会写入章节或叙事状态。",
            informative_text=(
                f"活跃任务：{labels}。为避免旧上下文污染下文，建议先保存为待应用修订。"
            ),
            actions=(
                MessageBoxAction(
                    "stage",
                    "保存为待应用修订",
                    role=QMessageBox.ButtonRole.AcceptRole,
                    variant="primary",
                    default=True,
                ),
                MessageBoxAction(
                    "wait",
                    "等待任务完成后应用",
                    role=QMessageBox.ButtonRole.AcceptRole,
                    variant="secondary",
                ),
                MessageBoxAction(
                    "stop_save",
                    "停止连跑并保存",
                    role=QMessageBox.ButtonRole.DestructiveRole,
                    variant="danger",
                ),
                MessageBoxAction(
                    "cancel",
                    "取消",
                    role=QMessageBox.ButtonRole.RejectRole,
                    variant="secondary",
                ),
            ),
            escape_key="cancel",
        )
        if choice == "stage":
            self._write_staged_revision(text, guard, status="staged")
            show_info_message(
                self.window(),
                "已暂存",
                "终稿修订已保存为待应用版本，未改动正式章节或叙事状态链。",
            )
            return "staged"
        if choice == "wait":
            self._write_staged_revision(text, guard, status="pending_apply")
            show_info_message(
                self.window(),
                "已登记",
                "终稿修订已登记为待应用版本；后台任务完成后可再应用到正式章节。",
            )
            return "staged"
        if choice == "stop_save":
            self._cancel_active_write_jobs(active_jobs)
            return "continue"
        return "cancel"

    def _cancel_active_write_jobs(self, jobs: list[Any]) -> None:
        manager = getattr(self.window(), "_job_manager", None)
        if manager is None or not hasattr(manager, "cancel_job"):
            return
        for job in jobs:
            job_id = str(getattr(job, "job_id", "") or "")
            if job_id:
                manager.cancel_job(job_id, reason="终稿修订保存需要停止后台写入")

    def _write_staged_revision(
        self,
        text: str,
        guard: _RevisionGuardReport,
        *,
        status: str,
    ) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        chapter_label = f"chapter_{self._chapter_number:03d}"
        root = self._project_dir / "states" / "final_revision_staging" / chapter_label / timestamp
        root.mkdir(parents=True, exist_ok=True)
        before_path = root / "before.md"
        after_path = root / "after.md"
        meta_path = root / "meta.json"
        atomic_write_text(before_path, self._saved_text)
        atomic_write_text(after_path, text)
        atomic_write_text(
            meta_path,
            json.dumps(
                {
                    "schema_version": "1.0",
                    "timestamp": timestamp,
                    "project_id": self._project_id,
                    "chapter_number": self._chapter_number,
                    "title": self._title,
                    "status": status,
                    "previous_hash": _text_hash(self._saved_text),
                    "current_hash": _text_hash(text),
                    "word_count": guard.word_count,
                    "expected_word_count": guard.expected_word_count,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        return meta_path

    def _submit_background_reevaluation(self) -> None:
        manager = getattr(self.window(), "_job_manager", None)
        if manager is None or not hasattr(manager, "submit_reevaluate_chapter"):
            return
        request = ReevaluateChapterRequest(
            project_id=self._project_id,
            chapter_number=self._chapter_number,
        )
        mock = bool(getattr(self.window(), "_mock_enabled", False))
        manager.submit_reevaluate_chapter(request, mock=mock)

    def _scope_success_message(self, scope: InvalidationScope) -> str:
        if scope is InvalidationScope.NONE:
            return "本章评估报告与叙事状态已标记需要刷新；下游章节未被标记失效。"
        if scope is InvalidationScope.NEXT:
            return "本章评估报告与叙事状态已标记需要刷新；仅下一章的旧链路被标记失效。"
        if scope is InvalidationScope.VOLUME:
            return "本章评估报告与叙事状态已标记需要刷新；本卷下游旧链路被标记失效。"
        return "本章评估报告与叙事状态已标记需要刷新；所有下游旧链路按兼容行为处理。"

    def _expected_word_count(self) -> int:
        chapter = _chapter_outline_entry(
            _load_json_file(self._project_dir / "outline.json"),
            self._chapter_number,
        )
        if not isinstance(chapter, dict):
            return 0
        try:
            return max(0, int(chapter.get("expected_word_count", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _write_revision_version(
        self,
        previous_text: str,
        current_text: str,
        guard: _RevisionGuardReport,
    ) -> _RevisionVersionRecord:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        chapter_label = (
            f"chapter_{self._chapter_number:03d}" if self._chapter_number > 0 else "short_text"
        )
        version_dir = self._project_dir / "states" / "final_revision_versions" / chapter_label
        before_path = version_dir / f"{timestamp}_before.md"
        after_path = version_dir / f"{timestamp}_after.md"
        meta_path = version_dir / f"{timestamp}.json"
        previous_hash = _text_hash(previous_text)
        current_hash = _text_hash(current_text)
        version_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(before_path, previous_text)
        atomic_write_text(after_path, current_text)
        meta = {
            "schema_version": "1.0",
            "timestamp": timestamp,
            "project_id": self._project_id,
            "chapter_number": self._chapter_number,
            "title": self._title,
            "chapter_path": str(self._chapter_path),
            "previous_hash": previous_hash,
            "current_hash": current_hash,
            "word_count": guard.word_count,
            "expected_word_count": guard.expected_word_count,
            "warnings": list(guard.warnings),
        }
        atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
        return _RevisionVersionRecord(
            timestamp=timestamp,
            version_dir=version_dir,
            before_path=before_path,
            after_path=after_path,
            meta_path=meta_path,
            previous_hash=previous_hash,
            current_hash=current_hash,
        )

    def _mark_revision_requires_refresh(
        self,
        version_record: _RevisionVersionRecord,
        guard: _RevisionGuardReport,
        *,
        scope: InvalidationScope = InvalidationScope.DOWNSTREAM,
    ) -> None:
        if self._chapter_number <= 0:
            return
        layout = ProjectLayout(self._project_dir)
        storage = FileSystemStorage(self._project_dir.parent)
        invalidated: list[int] = []
        range_start, range_end, affected = resolve_manual_invalidation_range(
            scope=scope,
            chapter_number=self._chapter_number,
            layout=layout,
        )
        try:
            if scope is not InvalidationScope.NONE and affected:
                invalidated = invalidate_downstream_generated_artifacts(
                    storage,
                    layout,
                    completed_chapter=self._chapter_number,
                    delete_chapter_files=False,
                    max_chapter=range_end,
                )
        except Exception:
            invalidated = []

        status_dir = self._project_dir / "states" / "final_revision_status"
        status_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1.0",
            "timestamp": version_record.timestamp,
            "revision_id": version_record.timestamp,
            "project_id": self._project_id,
            "chapter_number": self._chapter_number,
            "title": self._title,
            "previous_hash": version_record.previous_hash,
            "current_hash": version_record.current_hash,
            "word_count": guard.word_count,
            "expected_word_count": guard.expected_word_count,
            "requires_reevaluation": True,
            "requires_state_reextract": True,
            "requires_canon_reextract": True,
            "scope": scope.value,
            "range_start": range_start,
            "range_end": range_end,
            "affected_chapters": list(affected),
            "reason": "manual_final_revision",
            "version_meta_path": str(version_record.meta_path),
            "invalidated_downstream_chapters": invalidated,
        }
        status_path = status_dir / f"chapter_{self._chapter_number:03d}.json"
        atomic_write_text(status_path, json.dumps(payload, ensure_ascii=False, indent=2))

    def _revision_status_note(self, text: str) -> str:
        if self._chapter_number <= 0:
            return ""
        current_hash = _text_hash(text)
        status_path = (
            self._project_dir
            / "states"
            / "final_revision_status"
            / f"chapter_{self._chapter_number:03d}.json"
        )
        marker_needs_refresh = False
        if status_path.exists():
            payload = _load_json_file(status_path)
            marker_needs_refresh = (
                isinstance(payload, dict)
                and payload.get("current_hash") == current_hash
                and bool(
                    payload.get("requires_reevaluation")
                    or payload.get("requires_state_reextract")
                    or payload.get("requires_canon_reextract")
                )
            )
        stale_reports = self._stale_report_names(current_hash)
        if marker_needs_refresh:
            return "需重评 / 叙事状态刷新"
        if stale_reports:
            return "评估报告基于旧版正文"
        return ""

    def _stale_report_names(self, current_hash: str) -> list[str]:
        if self._chapter_number <= 0:
            return []
        layout = ProjectLayout(self._project_dir)
        report_paths = [
            ("对齐", layout.alignment_report_path(self._chapter_number)),
            ("质量", layout.eval_report_path(self._chapter_number)),
            ("连贯", layout.continuity_report_path(self._chapter_number)),
            ("因果", layout.chapter_causal_report_path(self._chapter_number)),
            ("追读力", layout.reading_power_report_path(self._chapter_number)),
            ("守门员", layout.guard_report_path(self._chapter_number)),
        ]
        stale: list[str] = []
        for label, path in report_paths:
            payload = _load_json_file(path)
            if not isinstance(payload, dict):
                continue
            report_hash = str(payload.get("source_text_hash", "") or "").strip()
            if report_hash and report_hash != current_hash:
                stale.append(label)
        return stale

    def _ensure_pre_save_snapshot(self) -> None:
        if self._chapter_number <= 0 or not self._chapter_path.exists():
            return
        snapshot = (
            self._project_dir
            / "states"
            / "edit_snapshots"
            / f"chapter_{self._chapter_number:03d}_snapshot.txt"
        )
        if snapshot.exists():
            return
        from novel_forge.core.utils.edit_tracker import save_snapshot

        save_snapshot(
            self._chapter_path,
            self._project_dir / "states" / "edit_snapshots",
            self._chapter_number,
        )

    def _record_manual_edit(self) -> None:
        if self._chapter_number <= 0:
            return
        from novel_forge.core.utils.edit_tracker import record_manual_edit, save_snapshot

        record_manual_edit(self._project_dir, self._chapter_number)
        save_snapshot(
            self._chapter_path,
            self._project_dir / "states" / "edit_snapshots",
            self._chapter_number,
        )

    def _refresh_chain_snapshot(self) -> None:
        if self._chapter_number <= 0:
            return
        from novel_forge.core.utils.edit_tracker import save_snapshot

        save_snapshot(
            self._chapter_path,
            self._project_dir / "states",
            self._chapter_number,
        )

    def _selection_snapshot(self) -> _SelectionSnapshot | None:
        cursor = self._editor.textCursor()
        if not cursor.hasSelection():
            return None

        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        full_text = self._editor.toPlainText()
        selected = full_text[start:end]
        before = full_text[max(0, start - _CONTEXT_CHARS) : start]
        after = full_text[end : end + _CONTEXT_CHARS]
        return _SelectionSnapshot(
            start=start,
            end=end,
            selected=selected,
            before=before,
            after=after,
        )

    def _show_editor_context_menu(self, pos: QPoint) -> None:
        menu: QMenu = self._editor.createStandardContextMenu()
        snapshot = self._selection_snapshot()
        menu.addSeparator()
        action = menu.addAction("选段精修…")
        action.setEnabled(snapshot is not None and bool(snapshot.selected.strip()))
        action.triggered.connect(self._open_selection_revision_dialog)
        menu.addSeparator()
        action_add_lib = menu.addAction("加入拟人化库…")
        action_add_lib.triggered.connect(
            lambda: self._open_add_to_humanize_library(self._selection_snapshot())
        )
        menu.exec(self._editor.mapToGlobal(pos))

    def _open_selection_revision_dialog(self) -> None:
        snapshot = self._selection_snapshot()
        if snapshot is None:
            show_warning_message(self.window(), "未选中文字", "请先在定稿中选中要精修的片段。")
            return
        self._request_ai_revision(snapshot=snapshot)

    def _open_add_to_humanize_library(self, snapshot: _SelectionSnapshot | None) -> None:
        """Open the dialog to add selected text to the humanize library.

        Currently a thin stub that delegates to the dialog implemented in Task 12.
        The import is lazy so that this page keeps loading during parallel
        development of ``dialogs_add_to_humanize.py``.
        """
        from novel_forge.desktop.pages.standalone.dialogs_add_to_humanize import (
            AddToHumanizeLibraryDialog,
        )

        dialog = AddToHumanizeLibraryDialog(self, snapshot=snapshot)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._status.setText("已加入拟人化库候选")

    def _request_ai_revision(
        self,
        *,
        snapshot: _SelectionSnapshot,
        instruction: str | None = None,
    ) -> None:
        selected = snapshot.selected
        if not selected.strip():
            show_warning_message(self.window(), "选区为空", "请选择包含正文的片段。")
            return
        if len(selected) > _MAX_SELECTION_CHARS:
            show_warning_message(
                self.window(),
                "选区过长",
                "请缩小选区后再精修，以便保持局部修改可控。",
            )
            return

        if self._worker is not None:
            show_warning_message(self.window(), "正在精修", "上一段候选尚未返回，请稍候。")
            return

        if instruction is None:
            dialog = _SelectionRevisionDialog(
                selection_words=_count_display_words(selected),
                parent=self.window(),
            )
            self._start_direction_suggestions(dialog, snapshot)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            instruction = dialog.instruction()

        self._worker = _SelectionRevisionWorker(
            project_dir=self._project_dir,
            project_id=self._project_id,
            chapter_number=self._chapter_number,
            chapter_title=self._title,
            selected_text=selected,
            before_context=snapshot.before,
            after_context=snapshot.after,
            instruction=instruction,
        )
        self._worker.signals.result.connect(
            lambda result, s=snapshot.start, e=snapshot.end, src=selected: (
                self._on_ai_revision_ready(s, e, src, result)
            )
        )
        self._worker.signals.error.connect(self._on_ai_revision_error)
        self._status.setText("正在为选段炼字…")
        self._ai_btn.setEnabled(False)
        desktop_thread_pools().aux_pool.start(self._worker)

    def _start_direction_suggestions(
        self,
        dialog: _SelectionRevisionDialog,
        snapshot: _SelectionSnapshot,
    ) -> None:
        worker = _DirectionSuggestionWorker(
            project_dir=self._project_dir,
            project_id=self._project_id,
            chapter_number=self._chapter_number,
            chapter_title=self._title,
            selected_text=snapshot.selected,
            before_context=snapshot.before,
            after_context=snapshot.after,
        )
        self._suggestion_workers.append(worker)

        def cleanup() -> None:
            try:
                self._suggestion_workers.remove(worker)
            except ValueError:
                pass

        def on_result(directions: list[str], note: str) -> None:
            cleanup()
            if dialog.isVisible():
                dialog.set_directions(directions or list(_DEFAULT_REVISION_DIRECTIONS[:4]))
                dialog.set_analysis_note(note)

        def on_error(_message: str) -> None:
            cleanup()
            if dialog.isVisible():
                dialog.set_directions(list(_DEFAULT_REVISION_DIRECTIONS[:4]))
                dialog.set_analysis_note("可直接输入修订方向，也可点选一个火候关键词。")

        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        desktop_thread_pools().aux_pool.start(worker)

    def _on_ai_revision_ready(
        self,
        start: int,
        end: int,
        original: str,
        replacement: str,
    ) -> None:
        self._worker = None
        self._pending = _PendingSelectionRevision(
            start=start,
            end=end,
            original=original,
            replacement=replacement,
        )
        self._proposal.setPlainText(replacement)
        self._refresh_state()

    def _on_ai_revision_error(self, message: str) -> None:
        self._worker = None
        self._refresh_state()
        show_warning_message(self.window(), "选段精修失败", message or "模型没有返回可用结果。")

    def _apply_ai_revision(self) -> None:
        if self._pending is None:
            return
        current = self._editor.toPlainText()
        start = self._pending.start
        end = self._pending.end
        if 0 <= start <= end <= len(current) and current[start:end] == self._pending.original:
            replace_start, replace_end = start, end
        else:
            first = current.find(self._pending.original)
            if first < 0 or current.find(self._pending.original, first + 1) >= 0:
                show_warning_message(
                    self.window(),
                    "选区已变化",
                    "原选区已被修改或出现多处匹配，请重新选择文本后再让 AI 改写。",
                )
                return
            replace_start = first
            replace_end = first + len(self._pending.original)

        cursor = self._editor.textCursor()
        cursor.setPosition(replace_start)
        cursor.setPosition(replace_end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(self._pending.replacement)
        self._editor.setTextCursor(cursor)
        self._clear_ai_revision()
        self._refresh_state()

    def _clear_ai_revision(self) -> None:
        self._pending = None
        if hasattr(self, "_proposal"):
            self._proposal.clear()
        if hasattr(self, "_apply_ai_btn"):
            self._apply_ai_btn.setEnabled(False)
