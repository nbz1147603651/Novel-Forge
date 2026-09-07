"""AddToHumanizeLibraryDialog — two-stage candidate picker for the humanize library.

User flow:
1. Stage 1 (parent, ``final_revision.py``): the user right-clicks a selection in
   the final-draft editor and chooses "加入拟人化库…". The parent builds a
   snapshot dict from ``_SelectionSnapshot`` and calls
   ``AddToHumanizeLibraryDialog(snapshot=...)``.
2. Stage 2 (this dialog): we run a *local* regex prescreen (mirroring
   ``HumanizeScanStep.prescreen_text``) over the selected text, surface each
   match as a chip-style checkbox, and let the user:
     * tick one or more AI-style candidates,
     * type a manual phrase,
     * tweak the form fields (name / category / severity / detection method /
       scope / notes / keywords),
     * check for duplicates via ``HumanizeLibrary.find_duplicates()``,
     * submit. The actual ``lib.add()`` runs in a background QRunnable to keep
       the UI responsive.

No LLM calls happen inside this dialog — semantic adjudication belongs to
``HumanizeScanStep`` (a pipeline step) per the humanize-library architecture.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.core.schemas.humanize_library import HumanizeLibraryEntry
from novel_forge.desktop.components.sizing import smart_dialog_size
from novel_forge.desktop.theme import resolve_qcolor
from novel_forge.desktop.thread_pools import desktop_thread_pools
from novel_forge.desktop.widgets import show_info_message, show_warning_message
from novel_forge.desktop.workers import BaseJobWorker, BaseJobWorkerSignals
from novel_forge.obs.logger import get_logger

_logger = get_logger("desktop.dialogs.add_to_humanize")


# ---------------------------------------------------------------------------
# Local prescreen — mirrors HumanizeScanStep._prescreen_hits style
# ---------------------------------------------------------------------------
#
# We intentionally keep the rules inline (rather than importing the private
# ``_HUMANIZE_RULES`` tuple from ``humanize_scan_step``) so the dialog is
# decoupled from pipeline internals. The patterns are a curated subset that
# yields the highest-signal chips for "add to library" workflows.
#
# Each entry maps a regex to a (pattern_name, category, severity, source_hint)
# tuple. ``source_hint`` is what we suggest as the ``pattern_name`` when the
# user ticks a single chip and hasn't filled the name field in yet.

_LOCAL_RULES: tuple[tuple[str, str, str, str, re.Pattern[str]], ...] = (
    (
        "significance_inflation",
        "显著性通胀",
        "叙事轻重",
        "high",
        re.compile(r"标志着|具有里程碑意义|划时代|开创性|关键时刻|分水岭|前所未有"),
    ),
    (
        "promotional_language",
        "宣传腔",
        "宣传式描写",
        "medium",
        re.compile(r"坐落于|令人叹为观止|迷人的|充满活力的|壮丽的|美不胜收|雄伟的|自然之美"),
    ),
    (
        "ai_vocabulary",
        "AI 高频词汇",
        "AI 词汇",
        "medium",
        re.compile(r"此外|至关重要|深入探讨|彰显|凸显|复杂性|持久的|格局|珍贵的|相互作用"),
    ),
    (
        "negative_parallelism",
        "否定式并列",
        "模板句式",
        "high",
        re.compile(r"不(?:是|仅仅|只|只不过)[^。！？\n]{0,30}(?:而是|更是|而且|恰恰是)"),
    ),
    (
        "filler_phrases",
        "填充短语",
        "元语言",
        "high",
        re.compile(r"值得注意的是|不难发现|基于以上分析|综上所述|换句话说|不可否认的是"),
    ),
    (
        "generic_conclusions",
        "万能结尾",
        "模板结尾",
        "high",
        re.compile(r"未来充满希望|新的篇章即将开启|一切才刚刚开始|前路漫漫|故事还在继续"),
    ),
    (
        "hollow_aspect_marker",
        "空洞进行态",
        "动作虚化",
        "medium",
        re.compile(r"凝望着|沉思着|注视着|思考着|感受着|回忆着|等待着"),
    ),
    (
        "persuasive_authority",
        "说教权威腔",
        "说教腔",
        "high",
        re.compile(r"从根本上说|核心问题是|归根结底|我们必须承认|事实上|真正的问题是|本质上"),
    ),
    (
        "vague_attribution",
        "模糊归因",
        "证据空泛",
        "medium",
        re.compile(r"专家认为|业内人士指出|观察者指出|一些批评者认为|行业报告显示|多个来源显示"),
    ),
    (
        "collaborative_artifact",
        "协作对话残留",
        "聊天残留",
        "critical",
        re.compile(r"希望这对[你您]有帮助|当然！|一定！|请告诉我|如果[你您]想让我"),
    ),
    (
        "sycophantic_tone",
        "谄媚语气",
        "聊天残留",
        "high",
        re.compile(r"好问题！|您说得完全正确|这是一个很好的观点|非常棒的问题"),
    ),
    (
        "diff_anchored_writing",
        "改动叙述腔",
        "元语言",
        "medium",
        re.compile(r"本次(?:更新|改动|修改)|新增了|替代了原先|相较于之前|进行了优化"),
    ),
)


@dataclass(frozen=True)
class _Candidate:
    """A single regex match surfaced in the chip list."""

    rule_id: str
    pattern_name: str
    category: str
    severity: str
    text: str
    start: int
    end: int


def _local_prescan(text: str) -> list[_Candidate]:
    """Run the local rules over ``text`` and return de-duplicated candidates.

    Candidates are de-duplicated by ``(rule_id, start)`` so a single match
    yields exactly one chip, and ordered by their position in the source
    text for predictable chip-list ordering.
    """
    if not text:
        return []
    seen: set[tuple[str, int]] = set()
    out: list[_Candidate] = []
    for rule_id, pattern_name, category, severity, regex in _LOCAL_RULES:
        for m in regex.finditer(text):
            key = (rule_id, m.start())
            if key in seen:
                continue
            seen.add(key)
            evidence = text[m.start() : m.end()].strip()
            if not evidence:
                continue
            out.append(
                _Candidate(
                    rule_id=rule_id,
                    pattern_name=pattern_name,
                    category=category,
                    severity=severity,
                    text=evidence,
                    start=m.start(),
                    end=m.end(),
                )
            )
    out.sort(key=lambda c: (c.start, c.rule_id))
    return out


# ---------------------------------------------------------------------------
# Background worker — runs lib.add() off the UI thread
# ---------------------------------------------------------------------------


class _AddEntrySignals(BaseJobWorkerSignals):
    """Signal bag for ``_AddEntryWorker``."""

    succeeded = Signal(object)  # HumanizeLibraryEntry
    failed = Signal(str)  # error message


class _AddEntryWorker(BaseJobWorker):
    """Add an entry to the humanize library off the UI thread.

    Opens (and closes) its own ``HumanizeLibrary`` instance on the worker
    thread to avoid SQLite's per-thread connection affinity violation.
    """

    pool = "aux"

    def __init__(self, entry: HumanizeLibraryEntry) -> None:
        super().__init__()
        self.signals = _AddEntrySignals()
        self._entry = entry

    async def _run_async(self) -> None:
        try:
            from novel_forge.memory.humanize_library_store import (
                HumanizeLibrary,
                LibraryDuplicateError,
            )
        except Exception as exc:  # noqa: BLE001 — surface any error to the UI
            self.signals.failed.emit(f"加载 HumanizeLibrary 失败：{exc}")
            return

        lib = HumanizeLibrary.from_default_path()
        try:
            try:
                lib.add(self._entry)
            except LibraryDuplicateError as exc:
                self.signals.failed.emit(f"已存在相同 ID 的模式：{exc}")
                return
        except Exception as exc:  # noqa: BLE001 — surface any error to the UI
            _logger.warning(
                "humanize_lib_add_failed | pattern_id=%s err=%s",
                self._entry.pattern_id,
                exc,
            )
            self.signals.failed.emit(str(exc))
            return
        finally:
            try:
                lib.close()
            except Exception:
                _logger.debug("humanize_lib_close_failed_in_worker", exc_info=True)
        self.signals.succeeded.emit(self._entry)


class _DedupSignals(BaseJobWorkerSignals):
    """Signal bag for ``_DedupWorker``."""

    succeeded = Signal(list, str, str)
    failed = Signal(str)


class _DedupWorker(BaseJobWorker):
    """Find near-duplicate patterns off the UI thread.

    Opens (and closes) its own ``HumanizeLibrary`` instance on the worker
    thread to keep the BM25 O(n²) computation off the UI loop.
    """

    pool = "aux"

    def __init__(self, name_hint: str, kw_hint: list[str]) -> None:
        super().__init__()
        self.signals = _DedupSignals()
        self._name_hint = name_hint
        self._kw_hint = kw_hint

    async def _run_async(self) -> None:
        try:
            from novel_forge.memory.humanize_library_store import HumanizeLibrary
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(f"加载 HumanizeLibrary 失败：{exc}")
            return

        lib = HumanizeLibrary.from_default_path()
        try:
            try:
                duplicates = lib.find_duplicates(threshold=0.6)
            except Exception as exc:  # noqa: BLE001
                _logger.warning("humanize_lib_find_duplicates_failed | err=%s", exc)
                self.signals.failed.emit(f"调用 find_duplicates 失败：{exc}")
                return
        finally:
            try:
                lib.close()
            except Exception:
                _logger.debug("humanize_lib_close_failed_in_dedup_worker", exc_info=True)
        self.signals.succeeded.emit(duplicates, self._name_hint, " ".join(self._kw_hint))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class AddToHumanizeLibraryDialog(QDialog):
    """Two-stage candidate picker for adding selected text to the humanize library.

    Parameters
    ----------
    parent
        Parent widget, typically the final-revision page.
    snapshot
        The selection snapshot from ``final_revision._selection_snapshot()``.
        Accepted shapes (loosely coupled, defensive):

        * A ``_SelectionSnapshot`` dataclass (real value from the call site),
          with ``start``, ``end``, ``selected``, ``before``, ``after`` attrs.
        * A dict with either ``selected`` / ``text`` and optionally
          ``chapter_id`` / ``chapter_number`` / ``paragraph_index`` /
          ``start_offset`` / ``end_offset`` (documented in the task spec).

        Only the *selected text* and the chapter context are used; offsets
        are preserved on the form for the caller's audit trail but not
        required.
    edit_entry
        If provided, the dialog opens in "edit" mode pre-populated from
        this entry. (Reserved for follow-up tasks; the lazy import in
        ``_open_add_to_humanize_library`` always passes ``None`` for now.)
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        snapshot: Any | None = None,
        edit_entry: HumanizeLibraryEntry | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("加入拟人化库")
        self.setObjectName("chapterStudioDialog")
        self.setModal(True)
        self.setMinimumSize(720, 640)
        self.resize(*smart_dialog_size(self, 820, 720))

        # State
        self._snapshot = self._coerce_snapshot(snapshot)
        self._edit_entry = edit_entry
        self._candidates: list[_Candidate] = []
        self._selected_candidate_indices: set[int] = set()
        self._threadpool = desktop_thread_pools().aux_pool
        self._in_flight_worker: _AddEntryWorker | None = None
        self._in_flight_dedup_worker: _DedupWorker | None = None

        # Build UI
        self._init_ui()
        self._populate_from_snapshot()
        self._prescan_and_refresh()

    # ------------------------------------------------------------------
    # Snapshot coercion — accept dict OR _SelectionSnapshot OR None
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_snapshot(snapshot: Any) -> dict[str, Any]:
        """Normalize the input snapshot into a dict with the fields we use."""
        if snapshot is None:
            return {"text": "", "chapter_number": 0, "paragraph_index": 0}
        # Real _SelectionSnapshot (frozen dataclass)
        if hasattr(snapshot, "selected"):
            start = getattr(snapshot, "start", 0) or 0
            end = getattr(snapshot, "end", 0) or 0
            return {
                "text": getattr(snapshot, "selected", "") or "",
                "before": getattr(snapshot, "before", "") or "",
                "after": getattr(snapshot, "after", "") or "",
                "start_offset": start,
                "end_offset": end,
                "chapter_number": 0,
                "paragraph_index": 0,
            }
        # Plain dict (per the task spec)
        if isinstance(snapshot, dict):
            text = snapshot.get("selected") or snapshot.get("text") or ""
            return {
                "text": text,
                "before": snapshot.get("before", ""),
                "after": snapshot.get("after", ""),
                "start_offset": snapshot.get("start_offset", snapshot.get("start", 0)) or 0,
                "end_offset": snapshot.get("end_offset", snapshot.get("end", 0)) or 0,
                "chapter_id": snapshot.get("chapter_id"),
                "chapter_number": snapshot.get("chapter_number", 0) or 0,
                "paragraph_index": snapshot.get("paragraph_index", 0) or 0,
            }
        # Unknown shape — treat as empty
        return {"text": "", "chapter_number": 0, "paragraph_index": 0}

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Title + sub-title
        title = QLabel("加入拟人化库")
        title.setObjectName("addHumanizeDialogTitle")
        root.addWidget(title)

        subtitle = QLabel(
            "勾选疑似 AI 句式作为检测模式；也可在下方手动加入新模式。"
            "加入后会先比对已有模式，避免重复。"
        )
        subtitle.setObjectName("addHumanizeDialogSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # Source-text preview (QTextEdit with extraSelections for chips)
        root.addWidget(self._build_source_section())

        # Candidate chips
        root.addWidget(self._build_candidates_section(), 1)

        # Form fields
        root.addWidget(self._build_form_section())

        # Status bar (success / warnings)
        self._status = QLabel("")
        self._status.setObjectName("addHumanizeStatusLabel")
        self._status.setProperty("dialogStatus", True)
        self._status.setProperty("tone", "info")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        # Action buttons
        root.addWidget(self._build_action_row())

    def _build_source_section(self) -> QWidget:
        frame = QWidget()
        frame.setObjectName("addHumanizeSourceFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QLabel("原文（可拖选子短语）")
        header.setObjectName("addHumanizeSectionHeader")
        layout.addWidget(header)

        self._source_view = QTextEdit()
        self._source_view.setObjectName("addHumanizeSourceView")
        self._source_view.setReadOnly(True)
        self._source_view.setFixedHeight(120)
        self._source_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self._source_view)

        return frame

    def _build_candidates_section(self) -> QWidget:
        frame = QWidget()
        frame.setObjectName("addHumanizeCandidatesFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QLabel("候选短语（可勾选）")
        header.setObjectName("addHumanizeSectionHeader")
        layout.addWidget(header)

        self._candidate_list = QListWidget()
        self._candidate_list.setObjectName("addHumanizeCandidateList")
        self._candidate_list.setUniformItemSizes(True)
        self._candidate_list.setAlternatingRowColors(True)
        self._candidate_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._candidate_list.itemChanged.connect(self._on_candidate_toggled)
        layout.addWidget(self._candidate_list, 1)

        # Manual-add row
        manual_row = QHBoxLayout()
        manual_row.setContentsMargins(0, 0, 0, 0)
        manual_row.setSpacing(6)

        self._manual_input = QLineEdit()
        self._manual_input.setObjectName("addHumanizeManualInput")
        self._manual_input.setPlaceholderText("手动加入一个短语或词…")
        manual_row.addWidget(self._manual_input, 1)

        self._add_manual_btn = QPushButton("+ 手动加入")
        self._add_manual_btn.setObjectName("addHumanizeManualButton")
        self._add_manual_btn.clicked.connect(self._on_add_manual)
        manual_row.addWidget(self._add_manual_btn)

        layout.addLayout(manual_row)

        return frame

    def _build_form_section(self) -> QWidget:
        frame = QWidget()
        frame.setObjectName("addHumanizeFormFrame")
        form = QFormLayout(frame)
        form.setContentsMargins(0, 4, 0, 4)
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # pattern_name
        self._name_input = QLineEdit()
        self._name_input.setObjectName("addHumanizeNameInput")
        self._name_input.setPlaceholderText("例：显著性通胀 / AI 常用转折")
        form.addRow("名称", self._name_input)

        # category
        self._category_input = QLineEdit()
        self._category_input.setObjectName("addHumanizeCategoryInput")
        self._category_input.setPlaceholderText("例：AI 词汇 / 模板句式")
        form.addRow("分类", self._category_input)

        form.addRow(self._make_divider())

        # severity (radio group)
        form.addRow(self._make_section_label("严重度"))
        self._severity_buttons: dict[str, QRadioButton] = {}
        severity_group = QButtonGroup(self)
        for sev in ("critical", "high", "medium", "low"):
            btn = QRadioButton(sev)
            btn.setObjectName(f"addHumanizeSeverity_{sev}")
            severity_group.addButton(btn)
            self._severity_buttons[sev] = btn
        self._severity_buttons["medium"].setChecked(True)
        form.addRow("", self._make_radio_row(self._severity_buttons.values()))

        # detection_method
        form.addRow(self._make_section_label("检测方式"))
        self._method_buttons: dict[str, QRadioButton] = {}
        method_group = QButtonGroup(self)
        for m in ("regex", "llm_only"):
            btn = QRadioButton(m)
            btn.setObjectName(f"addHumanizeMethod_{m}")
            method_group.addButton(btn)
            self._method_buttons[m] = btn
        self._method_buttons["regex"].setChecked(True)
        form.addRow("", self._make_radio_row(self._method_buttons.values()))

        form.addRow(self._make_divider())

        # scope (global / project)
        form.addRow(self._make_section_label("作用域"))
        self._scope_buttons: dict[str, QRadioButton] = {}
        scope_group = QButtonGroup(self)
        for s in ("global", "project"):
            btn = QRadioButton("全局" if s == "global" else "项目")
            btn.setObjectName(f"addHumanizeScope_{s}")
            scope_group.addButton(btn)
            self._scope_buttons[s] = btn
        self._scope_buttons["global"].setChecked(True)
        form.addRow("", self._make_radio_row(self._scope_buttons.values()))

        self._project_id_input = QLineEdit()
        self._project_id_input.setObjectName("addHumanizeProjectIdInput")
        self._project_id_input.setPlaceholderText("project_id（作用域=项目时必填）")
        self._project_id_input.setEnabled(False)
        self._project_id_input.hide()
        self._scope_buttons["project"].toggled.connect(self._on_project_scope_toggled)
        form.addRow("project_id", self._project_id_input)

        form.addRow(self._make_divider())

        # notes
        self._notes_input = QPlainTextEdit()
        self._notes_input.setObjectName("addHumanizeNotesInput")
        self._notes_input.setPlaceholderText("备注（可选）：为什么这是 AI 痕迹、希望怎么改…")
        self._notes_input.setFixedHeight(56)
        form.addRow("备注", self._notes_input)

        # keywords
        self._keywords_input = QLineEdit()
        self._keywords_input.setObjectName("addHumanizeKeywordsInput")
        self._keywords_input.setPlaceholderText("关键词（逗号分隔）")
        form.addRow("关键词", self._keywords_input)

        return frame

    def _make_section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("dlgSection")
        return label

    def _make_divider(self) -> QFrame:
        divider = QFrame()
        divider.setObjectName("dlgDivider")
        divider.setFixedHeight(1)
        return divider

    def _make_radio_row(self, buttons) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(16)
        for btn in buttons:
            layout.addWidget(btn)
        layout.addStretch()
        return row

    def _on_project_scope_toggled(self, checked: bool) -> None:
        self._project_id_input.setEnabled(checked)
        self._project_id_input.setVisible(checked)

    def _build_action_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)

        self._check_duplicates_btn = QPushButton("找重复")
        self._check_duplicates_btn.setObjectName("actionButton")
        self._check_duplicates_btn.setProperty("variant", "secondary")
        self._check_duplicates_btn.setProperty("compact", True)
        self._check_duplicates_btn.setToolTip("在已存在的人拟人化库中查找相似模式")
        self._check_duplicates_btn.clicked.connect(self._on_check_duplicates)
        layout.addWidget(self._check_duplicates_btn)

        layout.addStretch()

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setObjectName("actionButton")
        self._cancel_btn.setProperty("variant", "secondary")
        self._cancel_btn.setProperty("compact", True)
        self._cancel_btn.clicked.connect(self.reject)
        layout.addWidget(self._cancel_btn)

        self._submit_btn = QPushButton("加入库")
        self._submit_btn.setObjectName("actionButton")
        self._submit_btn.setProperty("variant", "primary")
        self._submit_btn.setProperty("compact", True)
        self._submit_btn.setDefault(True)
        self._submit_btn.clicked.connect(self._on_submit)
        layout.addWidget(self._submit_btn)

        return row

    # ------------------------------------------------------------------
    # Populate from snapshot
    # ------------------------------------------------------------------

    def _populate_from_snapshot(self) -> None:
        text = self._snapshot.get("text", "") or ""
        # Pre-fill source view
        self._source_view.setPlainText(text)

        # Pre-fill project_id if known
        pid = self._snapshot.get("chapter_id")
        if pid and not self._project_id_input.text():
            self._project_id_input.setText(str(pid))

        # If we are editing, overlay form values
        if self._edit_entry is not None:
            self._name_input.setText(self._edit_entry.pattern_name)
            self._category_input.setText(self._edit_entry.category)
            sev_btn = self._severity_buttons.get(self._edit_entry.severity)
            if sev_btn is not None:
                sev_btn.setChecked(True)
            method_btn = self._method_buttons.get(self._edit_entry.detection_method)
            if method_btn is not None:
                method_btn.setChecked(True)
            scope_key = "project" if self._edit_entry.project_id else "global"
            self._scope_buttons[scope_key].setChecked(True)
            if self._edit_entry.project_id:
                self._project_id_input.setText(self._edit_entry.project_id)
                self._project_id_input.setEnabled(True)
            self._notes_input.setPlainText(self._edit_entry.notes)
            self._keywords_input.setText(", ".join(self._edit_entry.keywords))

    # ------------------------------------------------------------------
    # Candidate detection + chip rendering
    # ------------------------------------------------------------------

    def _prescan_and_refresh(self) -> None:
        text = self._snapshot.get("text", "") or ""
        self._candidates = _local_prescan(text)
        self._selected_candidate_indices.clear()
        self._render_candidate_list()
        self._render_source_highlights()

    def _render_candidate_list(self) -> None:
        # Block signals while we rebuild the list to avoid cascading toggles
        self._candidate_list.blockSignals(True)
        self._candidate_list.clear()
        if not self._candidates:
            placeholder = QListWidgetItem("（未在选区中检测到 AI 句式）")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            placeholder.setForeground(QColor("#8e8275"))
            self._candidate_list.addItem(placeholder)
        else:
            for idx, cand in enumerate(self._candidates):
                label = f"{cand.pattern_name}：{cand.text}"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, idx)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                tooltip = (
                    f"规则：{cand.rule_id}\n"
                    f"分类：{cand.category}\n"
                    f"严重度：{cand.severity}\n"
                    f"位置：{cand.start}–{cand.end}\n"
                    f"原文：{cand.text}"
                )
                item.setToolTip(tooltip)
                self._candidate_list.addItem(item)
        self._candidate_list.blockSignals(False)

    def _render_source_highlights(self) -> None:
        """Apply extra-selections to highlight each detected candidate in the source view."""
        if not hasattr(self, "_source_view"):
            return
        text = self._source_view.toPlainText()
        selections: list = []
        highlight = QTextCharFormat()
        highlight.setBackground(resolve_qcolor("status.warning.bg"))
        highlight.setForeground(resolve_qcolor("text.body.warm"))
        for cand in self._candidates:
            if cand.end > len(text):
                continue
            cursor = self._source_view.textCursor()
            cursor.setPosition(cand.start)
            cursor.setPosition(cand.end, QTextCursor.MoveMode.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format = highlight
            sel.cursor.setVisualNavigation(False)
            selections.append(sel)
        self._source_view.setExtraSelections(selections)

    def _on_candidate_toggled(self, item: QListWidgetItem) -> None:
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None:
            return
        idx = int(idx)
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_candidate_indices.add(idx)
        else:
            self._selected_candidate_indices.discard(idx)
        self._sync_form_from_single_selection()

    def _on_add_manual(self) -> None:
        text = self._manual_input.text().strip()
        if not text:
            return
        # Use the first selected chip's metadata (if any) as the suggestion
        # base; otherwise fall back to sensible defaults.
        base_name = ""
        base_category = ""
        base_severity = "medium"
        if self._selected_candidate_indices:
            base = self._candidates[next(iter(self._selected_candidate_indices))]
            base_name = base.pattern_name
            base_category = base.category
            base_severity = base.severity
        if not self._name_input.text().strip():
            self._name_input.setText(base_name or "用户自定义")
        if not self._category_input.text().strip():
            self._category_input.setText(base_category or "用户自定义")
        sev_btn = self._severity_buttons.get(base_severity)
        if sev_btn is not None:
            sev_btn.setChecked(True)
        # Pre-fill keywords with the manual phrase
        existing = self._keywords_input.text().strip()
        new_kw = text
        if existing and new_kw not in existing:
            self._keywords_input.setText(f"{existing}, {new_kw}")
        elif not existing:
            self._keywords_input.setText(new_kw)
        self._manual_input.clear()
        self._set_status(
            f"已采纳手动短语：{text!r}（请在下方名称/备注处补全后提交）",
            kind="info",
        )

    def _sync_form_from_single_selection(self) -> None:
        """If exactly one chip is checked, mirror its metadata into the form.

        Only fills empty fields so user-typed values are preserved.
        """
        if len(self._selected_candidate_indices) != 1:
            return
        cand = self._candidates[next(iter(self._selected_candidate_indices))]
        if not self._name_input.text().strip():
            self._name_input.setText(cand.pattern_name)
        if not self._category_input.text().strip():
            self._category_input.setText(cand.category)
        sev_btn = self._severity_buttons.get(cand.severity)
        if sev_btn is not None:
            sev_btn.setChecked(True)

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def _set_status(self, message: str, kind: str = "info") -> None:
        self._status.setText(message)
        self._status.setProperty("tone", kind)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    # ------------------------------------------------------------------
    # Duplicates check
    # ------------------------------------------------------------------

    def _on_check_duplicates(self) -> None:
        name_hint = self._name_input.text().strip() or (
            self._candidates[next(iter(self._selected_candidate_indices))].pattern_name
            if self._selected_candidate_indices
            else ""
        )
        kw_hint = [k.strip() for k in self._keywords_input.text().split(",") if k.strip()]
        if not name_hint and not kw_hint:
            show_info_message(
                self,
                "请先填写名称或关键词",
                "为提高重复检测准确度，请先填写名称或关键词。",
            )
            return

        self._set_buttons_enabled(False)
        self._set_status("正在查重…", kind="info")

        worker = _DedupWorker(name_hint=name_hint, kw_hint=kw_hint)
        worker.signals.succeeded.connect(self._on_dedup_succeeded)
        worker.signals.failed.connect(self._on_dedup_failed)
        self._in_flight_dedup_worker = worker
        self._threadpool.start(worker)

    def _on_dedup_succeeded(
        self,
        duplicates: list,
        name_hint: str,
        joined_kw_hint: str,
    ) -> None:
        self._in_flight_dedup_worker = None
        self._set_buttons_enabled(True)

        joined_hint = (name_hint + " " + joined_kw_hint).strip().lower()
        if joined_hint:
            narrowed: list = []
            for a, b, score in duplicates:
                a_text = (a.pattern_name + " " + " ".join(a.keywords)).lower()
                b_text = (b.pattern_name + " " + " ".join(b.keywords)).lower()
                if any(tok in a_text for tok in joined_hint.split()) or any(
                    tok in b_text for tok in joined_hint.split()
                ):
                    narrowed.append((a, b, score))
            duplicates = narrowed

        if not duplicates:
            self._set_status("未发现明显重复，可以放心加入。", kind="ok")
            return

        top = duplicates[:5]
        lines = [f"找到 {len(duplicates)} 组相似条目（前 5 组）：\n"]
        for a, b, score in top:
            lines.append(
                f"• {a.pattern_name}（{a.pattern_id}）≈ {b.pattern_name}（{b.pattern_id}）"
                f" — 相似度 {score:.2f}"
            )
        show_warning_message(
            self,
            "可能存在重复",
            "\n".join(lines) + "\n\n建议先在库中查看，确认无重复后再提交。",
        )
        self._set_status(f"已发现 {len(duplicates)} 组相似条目，请确认后提交。", kind="warn")

    def _on_dedup_failed(self, error_msg: str) -> None:
        self._in_flight_dedup_worker = None
        self._set_buttons_enabled(True)
        self._set_status(f"查重失败：{error_msg}", kind="error")
        show_warning_message(self, "查重失败", error_msg)

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    def _collect_form(self) -> tuple[HumanizeLibraryEntry | None, str | None]:
        name = self._name_input.text().strip()
        if not name:
            return None, "请填写「名称」字段。"

        category = self._category_input.text().strip() or "用户自定义"
        severity = next(
            (s for s, btn in self._severity_buttons.items() if btn.isChecked()),
            "medium",
        )
        method = next(
            (m for m, btn in self._method_buttons.items() if btn.isChecked()),
            "regex",
        )
        scope = next(
            (s for s, btn in self._scope_buttons.items() if btn.isChecked()),
            "global",
        )
        project_id: str | None = None
        if scope == "project":
            project_id = self._project_id_input.text().strip() or None
            if not project_id:
                return None, "作用域=项目时必须填写 project_id。"

        notes = self._notes_input.toPlainText().strip()
        keywords = [k.strip() for k in self._keywords_input.text().split(",") if k.strip()]

        # Build example_phrases from selected chips + manual snippets
        example_phrases: list[str] = []
        for idx in sorted(self._selected_candidate_indices):
            cand = self._candidates[idx]
            if cand.text and cand.text not in example_phrases:
                example_phrases.append(cand.text)
        for phrase in [p.strip() for p in self._manual_input.text().split(",") if p.strip()]:
            if phrase and phrase not in example_phrases:
                example_phrases.append(phrase)
        if not example_phrases:
            # Last resort: use the snapshot's selected text (truncated)
            fallback = (self._snapshot.get("text", "") or "").strip()
            if fallback:
                example_phrases.append(fallback[:80])

        pattern_id = f"lib_user_{uuid.uuid4().hex[:8]}"
        entry = HumanizeLibraryEntry(
            pattern_id=pattern_id,
            pattern_name=name,
            category=category,
            severity=severity,  # type: ignore[arg-type]
            detection_method=method,  # type: ignore[arg-type]
            example_phrases=example_phrases,
            source="user",
            project_id=project_id,
            notes=notes,
            keywords=keywords,
        )
        return entry, None

    def _on_submit(self) -> None:
        entry, err = self._collect_form()
        if err is not None or entry is None:
            show_warning_message(self, "无法提交", err or "表单数据不完整。")
            return

        self._set_buttons_enabled(False)
        self._set_status("正在写入人拟人化库…", kind="info")

        worker = _AddEntryWorker(entry=entry)
        worker.signals.succeeded.connect(self._on_add_succeeded)
        worker.signals.failed.connect(self._on_add_failed)
        self._in_flight_worker = worker
        self._threadpool.start(worker)

    def _on_add_succeeded(self, entry: HumanizeLibraryEntry) -> None:
        self._in_flight_worker = None
        self._set_status(
            f"✓ 已加入人拟人化库：{entry.pattern_id}（{entry.pattern_name}）",
            kind="ok",
        )
        self.accept()

    def _on_add_failed(self, error_msg: str) -> None:
        self._in_flight_worker = None
        if "lock" in error_msg.lower():
            friendly = f"人拟人化库当前被其他进程占用，请稍后再试。\n原始错误：{error_msg}"
        else:
            friendly = f"写入人拟人化库失败：{error_msg}"
        self._set_status(friendly, kind="error")
        show_warning_message(self, "加入失败", friendly)
        self._set_buttons_enabled(True)

    def _close_owned_lib(self) -> None:
        """No-op kept for backward compatibility — the worker now owns lib lifecycle."""

    def _set_buttons_enabled(self, enabled: bool) -> None:
        self._check_duplicates_btn.setEnabled(enabled)
        self._submit_btn.setEnabled(enabled)
        self._cancel_btn.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Shutdown hygiene (per Desktop AGENTS.md)
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Disconnect worker signals and wait for any in-flight job.

        Call from the host page's ``shutdown()`` to satisfy the Desktop
        contract (per ``pages/AGENTS.md``).  Thread-pool draining is also
        handled globally by ``shutdown_desktop_thread_pools()``; the local
        ``waitForDone`` here is a best-effort safety net with a short
        timeout to avoid blocking the GUI thread.
        """
        worker = self._in_flight_worker
        if worker is not None:
            try:
                worker.signals.succeeded.disconnect()
                worker.signals.failed.disconnect()
            except (RuntimeError, TypeError):
                pass
        self._in_flight_worker = None
        dedup_worker = self._in_flight_dedup_worker
        if dedup_worker is not None:
            try:
                dedup_worker.signals.succeeded.disconnect()
                dedup_worker.signals.failed.disconnect()
            except (RuntimeError, TypeError):
                pass
        self._in_flight_dedup_worker = None
        if self._threadpool is not None and not self._threadpool.waitForDone(1000):
            _logger.debug("humanize_workers_still_running_on_shutdown")
        self._close_owned_lib()
