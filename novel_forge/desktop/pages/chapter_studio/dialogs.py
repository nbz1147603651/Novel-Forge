"""章台页面对话框 — 导出、版本对比、全书审计的增强交互。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.primitives import ActionButton

if TYPE_CHECKING:
    from novel_forge.core.utils.version_diff import DraftVersionInfo


def _divider() -> QFrame:
    div = QFrame()
    div.setObjectName("dlgDivider")
    div.setFixedHeight(1)
    return div


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("dlgSection")
    return lbl


def _hint_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("dlgHint")
    lbl.setWordWrap(True)
    return lbl


# ═══════════════════════════════════════════════════════════════════
# 导出对话框
# ═══════════════════════════════════════════════════════════════════


class ExportDialog(QDialog):
    """选择导出格式、章节范围和输出路径。"""

    def __init__(
        self,
        completed_chapters: list[int],
        default_output_dir: Path,
        book_title: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("chapterStudioDialog")
        self.setWindowTitle("导出设置")
        self.setModal(True)
        self.setFixedWidth(520)

        self._completed = completed_chapters
        self._output_dir = default_output_dir
        self._default_book_title = book_title
        self._selected_format = "markdown"
        self._selected_chapters: list[int] = []  # empty = all
        self._accepted = False

        self._build_ui()
        self.adjustSize()

    # ── Public getters ────────────────────────────────────────────

    def get_format(self) -> str:
        return self._selected_format

    def get_chapter_range(self) -> list[int]:
        return self._selected_chapters

    def get_output_dir(self) -> Path:
        return self._output_dir

    def get_book_title(self) -> str:
        """Return the user-specified book title (stripped), or the default."""
        if hasattr(self, "_book_title_edit"):
            val = self._book_title_edit.text().strip()
            return val if val else self._default_book_title
        return self._default_book_title

    def was_accepted(self) -> bool:
        return self._accepted

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("导出设置")
        title.setObjectName("dlgTitle")
        root.addWidget(title)

        subtitle = QLabel(f"当前共 {len(self._completed)} 个已完成章节可供导出。")
        subtitle.setObjectName("dlgSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)
        root.addWidget(_divider())

        # ── Format ────────────────────────────────────────────────
        root.addWidget(_section_label("导出格式"))

        fmt_group = QButtonGroup(self)
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(16)
        for i, (key, label, hint) in enumerate(
            [
                ("markdown", "Markdown (.md)", "保留标题标记，适合再编辑"),
                ("txt", "纯文本 (.txt)", "去除格式标记，纯净阅读"),
                ("epub", "EPUB (.epub)", "电子书格式，可导入阅读器"),
            ]
        ):
            radio = QRadioButton(label)
            radio.setToolTip(hint)
            fmt_group.addButton(radio, i)
            if key == "markdown":
                radio.setChecked(True)
            fmt_row.addWidget(radio)

        self._fmt_group = fmt_group
        self._fmt_keys = ["markdown", "txt", "epub"]
        fmt_group.idToggled.connect(self._on_fmt_changed)
        root.addLayout(fmt_row)

        fmt_hint = _hint_label(
            "Markdown 保留章节标题标记；纯文本去除所有格式；EPUB 可直接导入 Kindle / Apple Books 等阅读器。"
        )
        root.addWidget(fmt_hint)

        root.addWidget(_divider())

        # ── Chapter range ─────────────────────────────────────────
        root.addWidget(_section_label("导出范围"))

        range_group = QButtonGroup(self)
        if len(self._completed) >= 2:
            _range_label = f"全部已完成章节（第 {self._completed[0]}–{self._completed[-1]} 章）"
        elif len(self._completed) == 1:
            _range_label = f"全部已完成章节（第 {self._completed[0]} 章）"
        else:
            _range_label = "全部已完成章节"
        self._range_all = QRadioButton(_range_label)
        self._range_all.setChecked(True)
        range_group.addButton(self._range_all, 0)
        root.addWidget(self._range_all)

        self._range_custom = QRadioButton("选择特定章节")
        range_group.addButton(self._range_custom, 1)
        root.addWidget(self._range_custom)
        self._range_group = range_group

        # Chapter checkbox grid (scrollable)
        self._ch_scroll = QScrollArea()
        self._ch_scroll.setWidgetResizable(True)
        self._ch_scroll.setMaximumHeight(130)
        self._ch_scroll.setVisible(False)

        ch_container = QWidget()
        ch_layout = QHBoxLayout(ch_container)
        ch_layout.setContentsMargins(8, 6, 8, 6)
        ch_layout.setSpacing(4)

        self._ch_boxes: dict[int, QCheckBox] = {}
        # Wrap into rows of 10
        col_widget = QWidget()
        col_layout = QVBoxLayout(col_widget)
        col_layout.setContentsMargins(0, 0, 0, 0)
        col_layout.setSpacing(2)

        row_layout: QHBoxLayout | None = None
        for idx, ch in enumerate(self._completed):
            if idx % 10 == 0:
                row_layout = QHBoxLayout()
                row_layout.setSpacing(4)
                col_layout.addLayout(row_layout)
            cb = QCheckBox(f"第{ch}章")
            cb.setChecked(True)
            self._ch_boxes[ch] = cb
            if row_layout is not None:
                row_layout.addWidget(cb)

        if row_layout is not None:
            row_layout.addStretch()
        col_layout.addStretch()

        self._ch_scroll.setWidget(col_widget)
        root.addWidget(self._ch_scroll)

        range_group.idToggled.connect(self._on_range_changed)

        root.addWidget(_divider())

        # ── Output path ───────────────────────────────────────────
        root.addWidget(_section_label("输出目录"))

        path_row = QHBoxLayout()
        self._path_label = QLabel(str(self._output_dir))
        self._path_label.setObjectName("pathLabel")
        self._path_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        path_row.addWidget(self._path_label, 1)

        browse_btn = ActionButton("选择路径…", variant="secondary")
        browse_btn.setFixedHeight(32)
        browse_btn.clicked.connect(self._browse_output)
        path_row.addWidget(browse_btn)
        root.addLayout(path_row)

        root.addWidget(_hint_label("导出文件将保存至该目录下，文件名为「书名.格式后缀」。"))

        root.addWidget(_divider())

        # ── Book title ───────────────────────────────────────────
        root.addWidget(_section_label("书名"))
        self._book_title_edit = QLineEdit()
        self._book_title_edit.setObjectName("exportBookTitleEdit")
        placeholder = self._default_book_title if self._default_book_title else "未命名书籍"
        self._book_title_edit.setPlaceholderText(placeholder)
        if self._default_book_title:
            self._book_title_edit.setText(self._default_book_title)
        root.addWidget(self._book_title_edit)
        root.addWidget(_hint_label("此处书名用于导出文件名。不填则自动采用项目初始化时的书名。"))
        root.addSpacing(8)

        # ── Buttons ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = ActionButton("取消", variant="quiet")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = ActionButton("开始导出", variant="primary")
        ok_btn.setFixedHeight(36)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(ok_btn)

        root.addLayout(btn_row)

    def _on_fmt_changed(self, _id: int, checked: bool) -> None:
        if checked:
            self._selected_format = self._fmt_keys[_id]

    def _on_range_changed(self, _id: int, checked: bool) -> None:
        if checked:
            self._ch_scroll.setVisible(_id == 1)
            self.adjustSize()

    def _browse_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择导出目录", str(self._output_dir))
        if directory:
            self._output_dir = Path(directory)
            self._path_label.setText(str(self._output_dir))

    def _on_accept(self) -> None:
        self._selected_format = self._fmt_keys[self._fmt_group.checkedId()]
        if self._range_group.checkedId() == 1:
            self._selected_chapters = [ch for ch, cb in self._ch_boxes.items() if cb.isChecked()]
        else:
            self._selected_chapters = []
        self._accepted = True
        self.accept()


# ═══════════════════════════════════════════════════════════════════
# 版本对比对话框
# ═══════════════════════════════════════════════════════════════════


class VersionDiffDialog(QDialog):
    """选择要对比的两个版本。"""

    def __init__(
        self,
        versions: list[DraftVersionInfo],
        chapter_num: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("chapterStudioDialog")
        self.setWindowTitle("版本对比")
        self.setModal(True)
        self.setFixedWidth(480)

        self._versions = versions
        self._chapter_num = chapter_num
        self._accepted = False
        self._version_a: int = versions[-2].version if len(versions) >= 2 else 0
        self._version_b: int = versions[-1].version if len(versions) >= 1 else 0

        self._build_ui()
        self.adjustSize()

    def get_versions(self) -> tuple[int, int]:
        return self._version_a, self._version_b

    def was_accepted(self) -> bool:
        return self._accepted

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel(f"第 {self._chapter_num} 章 · 版本对比")
        title.setObjectName("dlgTitle")
        root.addWidget(title)

        subtitle = QLabel(f"共有 {len(self._versions)} 个版本可供对比，请选择两个版本进行比较。")
        subtitle.setObjectName("dlgSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)
        root.addWidget(_divider())

        # ── Version A (left) ──────────────────────────────────────
        root.addWidget(_section_label("对比基准（旧版本）"))
        group_a = QButtonGroup(self)
        self._group_a = group_a
        for i, v in enumerate(self._versions):
            wc = f"  ·  {v.word_count:,} 字" if v.word_count else ""
            radio = QRadioButton(f"{v.label}{wc}")
            group_a.addButton(radio, i)
            if v.version == self._version_a:
                radio.setChecked(True)
            root.addWidget(radio)

        root.addWidget(_divider())

        # ── Version B (right) ─────────────────────────────────────
        root.addWidget(_section_label("对比目标（新版本）"))
        group_b = QButtonGroup(self)
        self._group_b = group_b
        for i, v in enumerate(self._versions):
            wc = f"  ·  {v.word_count:,} 字" if v.word_count else ""
            radio = QRadioButton(f"{v.label}{wc}")
            group_b.addButton(radio, i)
            if v.version == self._version_b:
                radio.setChecked(True)
            root.addWidget(radio)

        root.addWidget(
            _hint_label(
                "选择两个不同版本，系统将以行级差异高亮显示修改前后的变化——绿色为新增、红色为删除。"
            )
        )

        root.addSpacing(8)

        # ── Buttons ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = ActionButton("取消", variant="quiet")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = ActionButton("开始对比", variant="primary")
        ok_btn.setFixedHeight(36)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(ok_btn)

        root.addLayout(btn_row)

    def _on_accept(self) -> None:
        a_idx = self._group_a.checkedId()
        b_idx = self._group_b.checkedId()
        if a_idx < 0 or b_idx < 0:
            return
        va = self._versions[a_idx]
        vb = self._versions[b_idx]
        if va.version == vb.version:
            # 不允许相同版本
            return
        self._version_a = va.version
        self._version_b = vb.version
        self._accepted = True
        self.accept()


# ═══════════════════════════════════════════════════════════════════
# 全书审计对话框
# ═══════════════════════════════════════════════════════════════════


class BookAuditDialog(QDialog):
    """选择审计范围 — 全部已完成章节或特定章节。"""

    def __init__(
        self,
        completed_chapters: list[int],
        *,
        default_analysis_mode: str = "full_text",
        default_prompt_hint: str = "",
        default_location_strictness: str = "balanced",
        default_max_tokens: int = 8192,
        model_max_output_tokens: int | None = None,
        default_temperature: float = 0.2,
        default_audit_max_chapters_per_batch: int = 12,
        default_audit_max_issues_per_chunk: int = 12,
        default_audit_issue_pool_max_items: int = 160,
        default_chapter_max_chars: int = 12000,
        default_two_phase_enabled: bool = True,
        default_two_phase_threshold: float = 0.7,
        default_two_phase_max_target_chapters: int = 24,
        default_repair_mode: str = "off",
        default_repair_min_severity: str = "warning",
        default_repair_max_chapters: int = 12,
        default_allow_exhausted_retry: bool = False,
        default_use_issue_panel_pool: bool = True,
        default_repair_concurrency: int = 1,
        default_generate_repair_report: bool = True,
        default_panel_first_expansion: bool = True,
        default_verify_before_repair: bool = True,
        default_post_repair_targeted_audit: bool = False,
        default_repair_guard_enabled: bool = True,
        default_repair_guard_max_delta_ratio: float = 0.12,
        default_repair_guard_max_added_chars: int = 600,
        default_parallel_chunks: bool = False,
        default_parallel_dimensions: bool = True,
        has_prior_audit: bool = False,
        prior_audit_status: str = "",
        has_audit_checkpoint: bool = False,
        audit_checkpoint_status: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("chapterStudioDialog")
        self.setWindowTitle("全书审计设置")
        self.setModal(True)
        self.setFixedWidth(640)
        self.setStyleSheet(
            """
            QDialog#chapterStudioDialog QLabel#dlgSubtitle { font-size: 11px; }
            QDialog#chapterStudioDialog QLabel#dlgBody,
            QDialog#chapterStudioDialog QCheckBox#dlgBody,
            QDialog#chapterStudioDialog QRadioButton,
            QDialog#chapterStudioDialog QCheckBox { font-size: 12px; }
            """
        )

        self._completed = completed_chapters
        self._selected_chapters: list[int] = []
        self._accepted = False
        self._default_analysis_mode = str(default_analysis_mode or "full_text")
        self._default_prompt_hint = str(default_prompt_hint or "")
        self._default_location_strictness = str(default_location_strictness or "balanced")
        self._model_max_output_tokens: int | None = (
            int(max(512, min(model_max_output_tokens, 65536)))
            if model_max_output_tokens is not None
            else None
        )
        _token_cap = (
            self._model_max_output_tokens if self._model_max_output_tokens is not None else 65536
        )
        self._default_max_tokens = int(max(512, min(default_max_tokens, _token_cap)))
        self._default_temperature = float(max(0.0, min(default_temperature, 2.0)))
        self._default_audit_max_chapters_per_batch = int(
            max(1, min(default_audit_max_chapters_per_batch, 500))
        )
        self._default_audit_max_issues_per_chunk = int(
            max(1, min(default_audit_max_issues_per_chunk, 50))
        )
        self._default_audit_issue_pool_max_items = int(
            max(0, min(default_audit_issue_pool_max_items, 1000))
        )
        self._default_chapter_max_chars = int(max(1000, min(default_chapter_max_chars, 100000)))
        self._default_two_phase_enabled = bool(default_two_phase_enabled)
        self._default_two_phase_threshold = float(max(0.0, min(default_two_phase_threshold, 1.0)))
        self._default_two_phase_max_target_chapters = int(
            max(1, min(default_two_phase_max_target_chapters, max(1, len(completed_chapters))))
        )
        self._default_repair_mode = "off"
        self._default_repair_min_severity = str(default_repair_min_severity or "warning")
        self._default_repair_max_chapters = int(max(1, min(default_repair_max_chapters, 500)))
        self._default_allow_exhausted_retry = bool(default_allow_exhausted_retry)
        self._default_use_issue_panel_pool = bool(default_use_issue_panel_pool)
        self._default_repair_concurrency = int(max(1, min(default_repair_concurrency, 8)))
        self._default_generate_repair_report = bool(default_generate_repair_report)
        self._default_panel_first_expansion = bool(default_panel_first_expansion)
        self._default_verify_before_repair = bool(default_verify_before_repair)
        self._default_post_repair_targeted_audit = bool(default_post_repair_targeted_audit)
        self._default_repair_guard_enabled = bool(default_repair_guard_enabled)
        self._default_repair_guard_max_delta_ratio = float(
            max(0.01, min(default_repair_guard_max_delta_ratio, 1.0))
        )
        self._default_repair_guard_max_added_chars = int(
            max(100, min(default_repair_guard_max_added_chars, 10000))
        )
        self._default_parallel_chunks = bool(default_parallel_chunks)
        self._default_parallel_dimensions = bool(default_parallel_dimensions)
        self._has_prior_audit = bool(has_prior_audit)
        self._prior_audit_status = str(prior_audit_status or "").strip()
        self._has_audit_checkpoint = bool(has_audit_checkpoint)
        self._audit_checkpoint_status = str(audit_checkpoint_status or "").strip()

        self._build_ui()
        scr = self.screen()
        max_h = int(scr.availableGeometry().height() * 0.88) if scr else 780
        self.setMaximumHeight(max_h)
        self.adjustSize()

    def get_chapter_range(self) -> list[int]:
        return self._selected_chapters

    def get_analysis_mode(self) -> str:
        return self._analysis_mode.currentData() or self._analysis_mode.currentText()

    def get_prompt_hint(self) -> str:
        return self._prompt_hint.toPlainText().strip()

    def get_location_strictness(self) -> str:
        return self._location_strictness.currentData() or self._location_strictness.currentText()

    def get_max_tokens(self) -> int:
        return int(self._max_tokens.value())

    def get_temperature(self) -> float:
        return float(self._temperature.value())

    def get_audit_max_chapters_per_batch(self) -> int:
        return int(self._audit_max_chapters_per_batch.value())

    def get_audit_max_issues_per_chunk(self) -> int:
        return int(self._audit_max_issues_per_chunk.value())

    def get_audit_issue_pool_max_items(self) -> int:
        return int(self._audit_issue_pool_max_items.value())

    def get_chapter_max_chars(self) -> int:
        return int(self._chapter_max_chars.value())

    def get_two_phase_enabled(self) -> bool:
        return self._two_phase_enabled.isChecked()

    def get_two_phase_threshold(self) -> float:
        return float(self._two_phase_threshold.value())

    def get_two_phase_max_target_chapters(self) -> int:
        return int(self._two_phase_max_target_chapters.value())

    def get_repair_mode(self) -> str:
        return "off"

    def get_repair_min_severity(self) -> str:
        return self._repair_min_severity.currentData() or self._repair_min_severity.currentText()

    def get_repair_max_chapters(self) -> int:
        return int(self._repair_max_chapters.value())

    def get_allow_exhausted_retry(self) -> bool:
        return self._allow_exhausted_retry.isChecked()

    def get_use_issue_panel_pool(self) -> bool:
        return self._use_issue_panel_pool.isChecked()

    def get_repair_concurrency(self) -> int:
        return int(self._repair_concurrency.value())

    def get_generate_repair_report(self) -> bool:
        return self._generate_repair_report.isChecked()

    def get_panel_first_expansion(self) -> bool:
        return self._panel_first_expansion.isChecked()

    def get_verify_before_repair(self) -> bool:
        return self._verify_before_repair.isChecked()

    def get_post_repair_targeted_audit(self) -> bool:
        return self._post_repair_targeted_audit.isChecked()

    def get_repair_guard_enabled(self) -> bool:
        return self._repair_guard_enabled.isChecked()

    def get_repair_guard_max_delta_ratio(self) -> float:
        return self._default_repair_guard_max_delta_ratio

    def get_repair_guard_max_added_chars(self) -> int:
        return self._default_repair_guard_max_added_chars

    def get_auto_continue(self) -> bool:
        return False

    def get_continue_from_audit(self) -> bool:
        return False

    def get_continue_audit_from_checkpoint(self) -> bool:
        return (
            hasattr(self, "_strategy_continue_audit") and self._strategy_continue_audit.isChecked()
        )

    def get_reset_audit_checkpoint(self) -> bool:
        return self._has_audit_checkpoint and not self.get_continue_audit_from_checkpoint()

    def get_parallel_chunks(self) -> bool:
        return self._parallel_chunks.isChecked()

    def get_parallel_dimensions(self) -> bool:
        return self._parallel_dimensions.isChecked()

    def was_accepted(self) -> bool:
        return self._accepted

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 10)
        outer.setSpacing(6)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        _content = QWidget()
        root = QVBoxLayout(_content)
        root.setContentsMargins(18, 14, 18, 4)
        root.setSpacing(8)

        title = QLabel("全书一致性审计")
        title.setObjectName("dlgTitle")
        root.addWidget(title)

        subtitle = QLabel(
            f"当前共 {len(self._completed)} 个已完成章节。\n"
            "AI 将检查章节间的命名一致性、时间线连贯性、世界观设定、角色状态和叙事走向，"
            "并给出整体评分与具体问题建议。"
        )
        subtitle.setObjectName("dlgSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)
        root.addWidget(_divider())

        # ── Audit scope ───────────────────────────────────────────
        root.addWidget(_section_label("审计范围"))

        range_group = QButtonGroup(self)
        self._range_all = QRadioButton(
            f"全部已完成章节（第 {self._completed[0]}–{self._completed[-1]} 章）"
            if self._completed
            else "全部"
        )
        self._range_all.setChecked(True)
        range_group.addButton(self._range_all, 0)
        root.addWidget(self._range_all)

        self._range_custom = QRadioButton("选择特定章节（至少 2 章）")
        range_group.addButton(self._range_custom, 1)
        root.addWidget(self._range_custom)
        self._range_group = range_group

        # Chapter checkbox grid
        self._ch_scroll = QScrollArea()
        self._ch_scroll.setWidgetResizable(True)
        self._ch_scroll.setMaximumHeight(84)
        self._ch_scroll.setVisible(False)

        col_widget = QWidget()
        col_layout = QVBoxLayout(col_widget)
        col_layout.setContentsMargins(8, 4, 8, 4)
        col_layout.setSpacing(2)

        self._ch_boxes: dict[int, QCheckBox] = {}
        row_layout: QHBoxLayout | None = None
        for idx, ch in enumerate(self._completed):
            if idx % 10 == 0:
                row_layout = QHBoxLayout()
                row_layout.setSpacing(4)
                col_layout.addLayout(row_layout)
            cb = QCheckBox(f"第{ch}章")
            cb.setChecked(True)
            self._ch_boxes[ch] = cb
            if row_layout is not None:
                row_layout.addWidget(cb)

        if row_layout is not None:
            row_layout.addStretch()
        col_layout.addStretch()

        self._ch_scroll.setWidget(col_widget)
        root.addWidget(self._ch_scroll)

        range_group.idToggled.connect(self._on_range_changed)

        root.addWidget(_divider())

        # ── Audit strategy (when prior audit exists) ──────────────
        if self._has_prior_audit or self._has_audit_checkpoint:
            root.addWidget(_section_label("审计策略"))
            strategy_group = QButtonGroup(self)
            self._strategy_reaudit = QRadioButton(
                "重新审计：从头执行完整全书审计（将覆盖上次审计结果）"
            )
            strategy_group.addButton(self._strategy_reaudit, 0)
            root.addWidget(self._strategy_reaudit)

            if self._has_audit_checkpoint:
                self._strategy_continue_audit = QRadioButton("继续审计：从上次中断的分批审计继续")
                self._strategy_continue_audit.setChecked(True)
                self._strategy_continue_audit.setToolTip(
                    "适用于模型审计阶段中断：复用已完成的审计批次，\n只继续尚未完成的章节批次。"
                )
                strategy_group.addButton(self._strategy_continue_audit, 1)
                root.addWidget(self._strategy_continue_audit)
            else:
                self._strategy_reaudit.setChecked(True)

            self._strategy_group = strategy_group

            status_lines = []
            if self._audit_checkpoint_status:
                status_lines.append(self._audit_checkpoint_status)
            if self._prior_audit_status:
                status_lines.append(self._prior_audit_status)
            if not status_lines:
                status_lines.append("检测到上次审计状态。")
            status_lines.append(
                "选择「重新审计」会清除分批检查点并从头扫描；"
                "选择「继续审计」会复用已完成批次；"
                "正文修复已迁移到独立修复队列入口。"
            )
            root.addWidget(_hint_label("".join(status_lines)))
            root.addWidget(_divider())

        # ── Deep audit options ───────────────────────────────────
        root.addWidget(_section_label("分析模式与参数"))

        # ── Simple/Advanced mode toggle ──────────────────────────
        mode_toggle_row = QHBoxLayout()
        mode_toggle_row.setSpacing(8)
        self._mode_toggle_btn = ActionButton("高级模式", variant="quiet")
        self._mode_toggle_btn.setFixedHeight(24)
        self._mode_toggle_btn.setFixedWidth(72)
        self._mode_toggle_btn.setStyleSheet("font-size: 11px; padding: 2px 6px;")
        self._mode_toggle_btn.clicked.connect(self._on_mode_toggle)
        mode_toggle_row.addWidget(self._mode_toggle_btn)
        mode_toggle_row.addStretch()
        root.addLayout(mode_toggle_row)

        self._is_advanced_mode = False
        settings = QSettings("NovelForge", "BookAuditDialog")
        saved_mode = settings.value("advanced_mode", False, type=bool)
        self._is_advanced_mode = bool(saved_mode)

        # Core params (always visible)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_lbl = QLabel("分析模式")
        mode_lbl.setObjectName("dlgHint")
        mode_lbl.setFixedWidth(64)
        mode_row.addWidget(mode_lbl)
        self._analysis_mode = QComboBox()
        self._analysis_mode.addItem("智能深审（摘要筛查→定向全文，推荐）", "full_text")
        self._analysis_mode.addItem("摘要审计（更快）", "summary")
        _idx = self._analysis_mode.findData(self._default_analysis_mode)
        self._analysis_mode.setCurrentIndex(_idx if _idx >= 0 else 0)
        self._analysis_mode.setToolTip(
            "全文深审：将每章正文全部注入模型上下文，可精确到段落级别定位问题。\n"
            "摘要审计：只使用章节摘要，速度更快、成本更低，但定位粒度为章节级别。"
        )
        mode_row.addWidget(self._analysis_mode, 1)
        root.addLayout(mode_row)

        self._two_phase_enabled = QCheckBox("智能漏斗：先摘要筛查，再定向全文深审")
        self._two_phase_enabled.setChecked(self._default_two_phase_enabled)
        self._two_phase_enabled.setToolTip(
            "开启后，系统先用章节摘要快速筛出高风险章节，\n"
            "再只对目标章节注入全文做段落级审计，避免全书 82 章被拆成数百个模型块。"
        )
        root.addWidget(self._two_phase_enabled)

        target_row = QHBoxLayout()
        target_row.setSpacing(8)
        self._target_chapters_lbl = QLabel("定向深审章节")
        self._target_chapters_lbl.setObjectName("dlgHint")
        self._target_chapters_lbl.setFixedWidth(88)
        target_row.addWidget(self._target_chapters_lbl)
        self._two_phase_max_target_chapters = QSpinBox()
        self._two_phase_max_target_chapters.setRange(1, max(1, len(self._completed)))
        self._two_phase_max_target_chapters.setValue(
            min(self._default_two_phase_max_target_chapters, max(1, len(self._completed)))
        )
        self._two_phase_max_target_chapters.setFixedWidth(86)
        self._two_phase_max_target_chapters.setToolTip(
            "摘要筛查后最多进入全文深审的章节数。\n"
            "章节很多时建议 12–24；问题密集时系统会按严重度与置信度截断。"
        )
        target_row.addWidget(self._two_phase_max_target_chapters)
        target_row.addWidget(_hint_label("超过上限会优先审最严重、最可定位的章节。"), 1)
        root.addLayout(target_row)

        strict_row = QHBoxLayout()
        strict_row.setSpacing(8)
        self._strict_lbl = QLabel("定位严格度")
        self._strict_lbl.setObjectName("dlgHint")
        self._strict_lbl.setFixedWidth(64)
        strict_row.addWidget(self._strict_lbl)
        self._location_strictness = QComboBox()
        self._location_strictness.addItem("严格（优先精确到段）", "strict")
        self._location_strictness.addItem("平衡（推荐）", "balanced")
        self._location_strictness.addItem("宽松（覆盖更多）", "loose")
        _strict_idx = self._location_strictness.findData(self._default_location_strictness)
        self._location_strictness.setCurrentIndex(_strict_idx if _strict_idx >= 0 else 1)
        self._location_strictness.setToolTip(
            "控制问题定位的严格程度：\n"
            "· 严格 — 要求模型给出章节+段落索引，漏填则不纳入报告\n"
            "· 平衡 — 优先段落定位，章节级定位也接受（推荐）\n"
            "· 宽松 — 接受所有定位格式，覆盖最广但部分锚点可能不精确"
        )
        strict_row.addWidget(self._location_strictness, 1)
        root.addLayout(strict_row)

        token_temp_row = QHBoxLayout()
        token_temp_row.setSpacing(10)
        self._token_lbl = QLabel("max_tokens")
        self._token_lbl.setObjectName("dlgHint")
        token_temp_row.addWidget(self._token_lbl)
        self._max_tokens = QSpinBox()
        _token_range_max = (
            self._model_max_output_tokens if self._model_max_output_tokens is not None else 65536
        )
        self._max_tokens.setRange(512, _token_range_max)
        self._max_tokens.setSingleStep(512)
        self._max_tokens.setValue(self._default_max_tokens)
        self._max_tokens.setFixedWidth(110)
        self._max_tokens.setToolTip(
            "模型单次输出的最大 token 数。\n"
            "全文深审 52 章时，报告约占用 4,000–12,000 tokens。\n"
            "建议设置为 8,192 或以上；过低会导致输出截断，漏报问题。"
        )
        token_temp_row.addWidget(self._max_tokens)
        self._token_cap_lbl: QLabel | None = None
        if self._model_max_output_tokens is not None:
            self._token_cap_lbl = QLabel(f"≤ {self._model_max_output_tokens:,}")
            self._token_cap_lbl.setObjectName("dlgHint")
            self._token_cap_lbl.setToolTip(
                f"当前审计模型的最大输出 token 上限为 {self._model_max_output_tokens:,}。\n"
                "建议设置为 8,192–16,384，52 章全书审计报告大约占用 4,000–12,000 tokens。"
            )
            token_temp_row.addWidget(self._token_cap_lbl)
        self._temp_lbl = QLabel("temperature")
        self._temp_lbl.setObjectName("dlgHint")
        token_temp_row.addWidget(self._temp_lbl)
        self._temperature = QDoubleSpinBox()
        self._temperature.setRange(0.0, 2.0)
        self._temperature.setDecimals(2)
        self._temperature.setSingleStep(0.05)
        self._temperature.setValue(self._default_temperature)
        self._temperature.setFixedWidth(100)
        self._temperature.setToolTip(
            "模型采样温度，控制输出的随机性。\n"
            "· 0.0–0.3 — 更确定、保守，适合一致性审计（推荐）\n"
            "· 0.5–1.0 — 适度创意，问题描述更多样\n"
            "· 高于 1.0 — 输出更随机，不建议用于审计任务"
        )
        token_temp_row.addWidget(self._temperature)
        token_temp_row.addStretch()
        root.addLayout(token_temp_row)

        batch_row = QHBoxLayout()
        batch_row.setSpacing(8)
        batch_lbl = QLabel("每批审查章节")
        batch_lbl.setObjectName("dlgHint")
        batch_lbl.setFixedWidth(88)
        batch_row.addWidget(batch_lbl)
        self._audit_max_chapters_per_batch = QSpinBox()
        self._audit_max_chapters_per_batch.setRange(1, max(1, len(self._completed)))
        self._audit_max_chapters_per_batch.setValue(
            min(self._default_audit_max_chapters_per_batch, max(1, len(self._completed)))
        )
        self._audit_max_chapters_per_batch.setFixedWidth(86)
        self._audit_max_chapters_per_batch.setToolTip(
            "full_text 模式下，单次模型请求最多注入多少章全文。\n"
            "数值越小越不容易超过模型上下文窗口，也更便于分批定位；\n"
            "系统仍会额外按输入字符预算自动拆分特别长的章节批次。"
        )
        batch_row.addWidget(self._audit_max_chapters_per_batch)
        batch_row.addWidget(_hint_label("仅 full_text 模式生效；摘要模式仍一次审查全部摘要。"), 1)
        root.addLayout(batch_row)

        limit_row = QHBoxLayout()
        limit_row.setSpacing(8)
        self._issues_limit_lbl = QLabel("每块问题数")
        self._issues_limit_lbl.setObjectName("dlgHint")
        self._issues_limit_lbl.setFixedWidth(88)
        limit_row.addWidget(self._issues_limit_lbl)
        self._audit_max_issues_per_chunk = QSpinBox()
        self._audit_max_issues_per_chunk.setRange(1, 50)
        self._audit_max_issues_per_chunk.setValue(self._default_audit_max_issues_per_chunk)
        self._audit_max_issues_per_chunk.setFixedWidth(86)
        self._audit_max_issues_per_chunk.setToolTip(
            "单次模型调用最多返回多少条问题。\n建议 8–12；过高容易导致 JSON 输出截断。"
        )
        limit_row.addWidget(self._audit_max_issues_per_chunk)
        self._issue_pool_limit_lbl = QLabel("问题池条目")
        self._issue_pool_limit_lbl.setObjectName("dlgHint")
        limit_row.addWidget(self._issue_pool_limit_lbl)
        self._audit_issue_pool_max_items = QSpinBox()
        self._audit_issue_pool_max_items.setRange(0, 1000)
        self._audit_issue_pool_max_items.setSingleStep(20)
        self._audit_issue_pool_max_items.setValue(self._default_audit_issue_pool_max_items)
        self._audit_issue_pool_max_items.setFixedWidth(90)
        self._audit_issue_pool_max_items.setToolTip(
            "注入审计提示词的问题面板条目上限，按严重度优先。\n0 表示不注入问题池；建议 80–200。"
        )
        limit_row.addWidget(self._audit_issue_pool_max_items)
        limit_row.addStretch()
        root.addLayout(limit_row)

        phase_row = QHBoxLayout()
        phase_row.setSpacing(8)
        self._phase_threshold_lbl = QLabel("漏斗阈值")
        self._phase_threshold_lbl.setObjectName("dlgHint")
        self._phase_threshold_lbl.setFixedWidth(88)
        phase_row.addWidget(self._phase_threshold_lbl)
        self._two_phase_threshold = QDoubleSpinBox()
        self._two_phase_threshold.setRange(0.0, 1.0)
        self._two_phase_threshold.setDecimals(2)
        self._two_phase_threshold.setSingleStep(0.05)
        self._two_phase_threshold.setValue(self._default_two_phase_threshold)
        self._two_phase_threshold.setFixedWidth(86)
        self._two_phase_threshold.setToolTip(
            "摘要筛查标记章节占比超过该值时，系统仍不会退回全量全文，\n"
            "而是按优先级截断到「定向深审章节」上限。"
        )
        phase_row.addWidget(self._two_phase_threshold)
        phase_row.addWidget(_hint_label("高级参数；默认 0.70。"), 1)
        root.addLayout(phase_row)

        chars_row = QHBoxLayout()
        chars_row.setSpacing(8)
        chars_lbl = QLabel("单章最大字符")
        chars_lbl.setObjectName("dlgHint")
        chars_lbl.setFixedWidth(88)
        chars_row.addWidget(chars_lbl)
        self._chapter_max_chars = QSpinBox()
        self._chapter_max_chars.setRange(1000, 100000)
        self._chapter_max_chars.setSingleStep(1000)
        self._chapter_max_chars.setValue(self._default_chapter_max_chars)
        self._chapter_max_chars.setFixedWidth(90)
        self._chapter_max_chars.setToolTip("单章注入的最大字符数（仅 full_text 模式生效）")
        chars_row.addWidget(self._chapter_max_chars)
        chars_row.addWidget(_hint_label("过长章节会截断并在报告中标记。"), 1)
        root.addLayout(chars_row)

        self._audit_hint_lbl = _hint_label(
            "全文深审会向模型注入章节全文并输出\u201c章节+段落锚点\u201d；摘要模式更省成本，但定位粒度较粗。"
        )
        root.addWidget(self._audit_hint_lbl)

        # ── 并行化设置 ─────────────────────────────────────────────
        parallel_note = QLabel("并行加速")
        parallel_note.setObjectName("settingGroupSubtitle")
        root.addWidget(parallel_note)

        hint = QLabel(
            "注意：并行加速会消耗更多 Token（并发调用），但大幅缩短等待时间。多维度和分块并行可叠加。"
        )
        hint.setObjectName("dlgHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._parallel_chunks = QCheckBox("分块并行：多块 LLM 请求同时发出（2-4x 加速）")
        self._parallel_chunks.setObjectName("dlgBody")
        self._parallel_chunks.setChecked(self._default_parallel_chunks)
        self._parallel_chunks.setToolTip("将审计拆分为多个上下文块并发调用，纯加速不降质量。")
        root.addWidget(self._parallel_chunks)

        self._parallel_dimensions = QCheckBox(
            "维度并行：命名/时间线/世界观/角色/漂移分别审查（3-5x 加速）"
        )
        self._parallel_dimensions.setObjectName("dlgBody")
        self._parallel_dimensions.setChecked(self._default_parallel_dimensions)
        self._parallel_dimensions.setToolTip(
            "5 个审计维度独立并发调用，合并时智能去重。可与分块并行叠加。"
        )
        root.addWidget(self._parallel_dimensions)

        self._prompt_section_lbl = _section_label("审计提示词（可选）")
        root.addWidget(self._prompt_section_lbl)
        self._prompt_hint = QTextEdit()
        self._prompt_hint.setAcceptRichText(False)
        self._prompt_hint.setPlaceholderText(
            "例如：优先检查人物称谓变体、法术规则边界、关键道具归属与时间线跳跃。"
        )
        self._prompt_hint.setFixedHeight(68)
        self._prompt_hint.setToolTip(
            "可选的额外审计指令。填写后 AI 将在审计时优先关注你指定的一致性方向。\n"
            "留空则使用默认全项审计策略。"
        )
        self._prompt_hint.setPlainText(self._default_prompt_hint)
        root.addWidget(self._prompt_hint)

        # Audit-param widgets to disable in "续修" mode
        self._audit_param_widgets: list[QWidget] = [
            self._analysis_mode,
            self._location_strictness,
            self._max_tokens,
            self._temperature,
            self._audit_max_chapters_per_batch,
            self._audit_max_issues_per_chunk,
            self._audit_issue_pool_max_items,
            self._chapter_max_chars,
            self._two_phase_enabled,
            self._two_phase_threshold,
            self._two_phase_max_target_chapters,
            self._prompt_hint,
        ]

        root.addSpacing(4)
        root.addWidget(_divider())
        root.addSpacing(2)

        # ── Repair queue boundary ──────────────────────────────────
        root.addWidget(_section_label("修复队列"))

        repair_mode_row = QHBoxLayout()
        repair_mode_row.setSpacing(8)
        repair_mode_lbl = QLabel("模式")
        repair_mode_lbl.setObjectName("dlgHint")
        repair_mode_lbl.setFixedWidth(64)
        repair_mode_row.addWidget(repair_mode_lbl)
        self._repair_mode = QComboBox()
        self._repair_mode.addItem("全局审计（生成报告与修复队列）", "off")
        self._repair_mode.setCurrentIndex(0)
        self._repair_mode.setEnabled(False)
        self._repair_mode.setToolTip(
            "全书审计只执行全局诊断、证据定位与修复队列生成，不直接修改正文。\n"
            "需要改正文时，请在审计完成后执行独立的修复队列入口。"
        )
        repair_mode_row.addWidget(self._repair_mode, 1)
        root.addLayout(repair_mode_row)

        repair_policy_row = QHBoxLayout()
        repair_policy_row.setSpacing(10)
        self._severity_lbl = QLabel("最低级别")
        self._severity_lbl.setObjectName("dlgHint")
        self._severity_lbl.setToolTip(
            "只修复达到此严重程度及以上的问题。\n"
            "· critical — 仅修复最严重的矛盾\n"
            "· warning  — 修复严重 + 一般警告（推荐）\n"
            "· info     — 修复所有问题（可能噪音较多）"
        )
        repair_policy_row.addWidget(self._severity_lbl)
        self._repair_min_severity = QComboBox()
        self._repair_min_severity.addItem("critical", "critical")
        self._repair_min_severity.addItem("warning", "warning")
        self._repair_min_severity.addItem("info", "info")
        _sev_idx = self._repair_min_severity.findData(self._default_repair_min_severity)
        self._repair_min_severity.setCurrentIndex(_sev_idx if _sev_idx >= 0 else 1)
        self._repair_min_severity.setFixedWidth(120)
        self._repair_min_severity.setToolTip(
            "只修复达到此严重程度及以上的问题。\n"
            "· critical — 仅修复最严重的矛盾\n"
            "· warning  — 修复严重 + 一般警告（推荐）\n"
            "· info     — 修复所有问题（可能噪音较多）"
        )
        repair_policy_row.addWidget(self._repair_min_severity)
        self._max_ch_lbl = QLabel("最多章节（单次）")
        self._max_ch_lbl.setObjectName("dlgHint")
        _max_ch_tip = (
            "单次审计后自动修复的章节数上限。\n"
            "超出部分按严重度 + 置信度排序截断，剩余章节不会被本次修复，\n"
            "但问题会保存在报告中。\n\n"
            "· 设为 52（或更大）= 修复全部含问题章节\n"
            "· 设为 5–12 = 每次只处理最严重的几章，成本可控\n\n"
            "开启「自动续修」后，系统将自动分批处理剩余章节。"
        )
        self._max_ch_lbl.setToolTip(_max_ch_tip)
        repair_policy_row.addWidget(self._max_ch_lbl)
        self._repair_max_chapters = QSpinBox()
        self._repair_max_chapters.setRange(1, 500)
        self._repair_max_chapters.setValue(self._default_repair_max_chapters)
        self._repair_max_chapters.setFixedWidth(90)
        self._repair_max_chapters.setToolTip(_max_ch_tip)
        repair_policy_row.addWidget(self._repair_max_chapters)
        self._concurrency_lbl = QLabel("并发上限")
        self._concurrency_lbl.setObjectName("dlgHint")
        self._concurrency_lbl.setToolTip(
            "同时修复的章节并发数。\n"
            "· 1（推荐）— 顺序修复，确保章节间上下文不冲突\n"
            "· 2–3      — 适度并发，适合章节独立性较强的场景\n"
            "· 过高并发可能导致修复质量下降或接口限流"
        )
        repair_policy_row.addWidget(self._concurrency_lbl)
        self._repair_concurrency = QSpinBox()
        self._repair_concurrency.setRange(1, 8)
        self._repair_concurrency.setValue(self._default_repair_concurrency)
        self._repair_concurrency.setFixedWidth(72)
        self._repair_concurrency.setToolTip(
            "同时修复的章节并发数。\n"
            "· 1（推荐）— 顺序修复，确保章节间上下文不冲突\n"
            "· 2–3      — 适度并发，适合章节独立性较强的场景\n"
            "· 过高并发可能导致修复质量下降或接口限流"
        )
        repair_policy_row.addWidget(self._repair_concurrency)
        repair_policy_row.addStretch()
        root.addLayout(repair_policy_row)

        self._use_issue_panel_pool = QCheckBox("优先使用问题面板问题池做精准锚定")
        self._use_issue_panel_pool.setChecked(self._default_use_issue_panel_pool)
        self._use_issue_panel_pool.setToolTip(
            "开启后，修复时会额外加载该章节问题面板中已记录的问题作为修复上下文，\n"
            "帮助 AI 更精准地定位和修复对应位置。"
        )
        root.addWidget(self._use_issue_panel_pool)
        self._panel_first_expansion = QCheckBox(
            "面板优先：修复时额外带入面板中已有的 high/critical 问题（每通道最多 3 条）"
        )
        self._panel_first_expansion.setChecked(self._default_panel_first_expansion)
        self._panel_first_expansion.setToolTip(
            "开启后，将利用进入该章的机会，顺带清理当前面板里遗留的高/关键级别问题。\n"
            "关闭后，自动修复仅针对全书审计命中的问题，范围更精确。"
        )
        root.addWidget(self._panel_first_expansion)
        self._verify_before_repair = QCheckBox("修复前逐章验证：精确定位并过滤幻觉问题（推荐）")
        self._verify_before_repair.setChecked(self._default_verify_before_repair)
        self._verify_before_repair.setToolTip(
            "开启后，在修复前会对每个含问题的章节单独发一次 LLM 验证调用，\n"
            "逐条确认/否定问题是否真实存在，并精确重定位段落索引。\n"
            "可有效过滤全书审计阶段的幻觉问题，提升修复精度。\n"
            "额外成本：每章约 15K tokens 输入 + 2K 输出。"
        )
        root.addWidget(self._verify_before_repair)
        self._post_repair_targeted_audit = QCheckBox(
            "修复后二次小审计：复查已修/受影响章节（终章验收推荐）"
        )
        self._post_repair_targeted_audit.setChecked(self._default_post_repair_targeted_audit)
        self._post_repair_targeted_audit.setToolTip(
            "开启后，自动修复完成后会只对已修章节与传播影响章节再跑一次全文审计，\n"
            "用于确认修复没有留下新矛盾。会额外产生一轮模型调用成本。"
        )
        root.addWidget(self._post_repair_targeted_audit)
        self._repair_guard_enabled = QCheckBox(
            "修复后防污染闸门：疑似重复/提示词/异常大改动自动回滚（推荐）"
        )
        self._repair_guard_enabled.setChecked(self._default_repair_guard_enabled)
        self._repair_guard_enabled.setToolTip(
            "开启后，自动修复写入后会立即比较修复前后正文。\n"
            "如果发现提示词或 JSON 字段混入正文、段落/句子异常重复、Markdown 标记污染，\n"
            "或单章改动超过安全预算，会恢复该章修复前文本并转人工复核。"
        )
        root.addWidget(self._repair_guard_enabled)
        self._auto_continue = QCheckBox("自动续修：修完第一批后自动继续修剩余问题章节")
        self._auto_continue.setChecked(True)
        self._auto_continue.setToolTip(
            "开启后，当第一批修复完成后仍有未处理的问题章节（excluded_chapters），\n"
            "系统将自动调用续修模式继续处理，直到所有问题章节处理完毕或达到最大批次数。\n"
            "关闭后，修完第一批即停止，剩余章节需手动选择「续修模式」再次运行。"
        )
        root.addWidget(self._auto_continue)
        self._allow_exhausted_retry = QCheckBox("允许重试已达上限的问题（仅建议人工确认后开启）")
        self._allow_exhausted_retry.setChecked(self._default_allow_exhausted_retry)
        self._allow_exhausted_retry.setToolTip(
            "问题面板中「已达重试上限」的问题默认不再自动处理。\n"
            "开启此选项后，这些问题也会被纳入本次修复范围。\n"
            "建议先人工确认这些问题仍然有效，再开启此选项。"
        )
        root.addWidget(self._allow_exhausted_retry)

        self._generate_repair_report = QCheckBox("在卷帙生成全书修复报告")
        self._generate_repair_report.setChecked(self._default_generate_repair_report)
        self._generate_repair_report.setToolTip(
            "修复完成后，将本次自动修复的章节、应用的补丁数、跳过的问题等\n"
            "汇总为一份 JSON 报告，保存至 reports/book_consistency_repair_report.json。"
        )
        root.addWidget(self._generate_repair_report)

        # Collect all repair-mode-dependent widgets for enable/disable toggling
        self._repair_dependent_widgets: list[QWidget] = [
            self._severity_lbl,
            self._repair_min_severity,
            self._max_ch_lbl,
            self._repair_max_chapters,
            self._concurrency_lbl,
            self._repair_concurrency,
            self._use_issue_panel_pool,
            self._panel_first_expansion,
            self._verify_before_repair,
            self._post_repair_targeted_audit,
            self._repair_guard_enabled,
            self._auto_continue,
            self._allow_exhausted_retry,
            self._generate_repair_report,
        ]
        self._repair_mode.currentIndexChanged.connect(self._on_repair_mode_changed)
        # Apply initial state
        self._on_repair_mode_changed()

        self._audit_scope_hint = _hint_label(
            "审计项目包括：全局时间弧、承诺兑现、角色弧、情节线生命力、母题分布、"
            "张力曲线与世界规则完整性。\n"
            "审计结果将写入 reports/book_consistency_audit.json，并在 states/global_audit.db 中生成修复队列。"
        )
        root.addWidget(self._audit_scope_hint)

        # Advanced mode widgets (hidden in simple mode)
        self._advanced_widgets: list[QWidget] = [
            self._strict_lbl,
            self._location_strictness,
            self._token_lbl,
            self._max_tokens,
            self._temp_lbl,
            self._temperature,
            self._issues_limit_lbl,
            self._audit_max_issues_per_chunk,
            self._issue_pool_limit_lbl,
            self._audit_issue_pool_max_items,
            self._phase_threshold_lbl,
            self._two_phase_threshold,
            chars_lbl,
            self._chapter_max_chars,
            self._prompt_section_lbl,
            self._prompt_hint,
            self._audit_hint_lbl,
            self._audit_scope_hint,
        ]
        if self._token_cap_lbl is not None:
            self._advanced_widgets.append(self._token_cap_lbl)

        root.addSpacing(4)

        self._scroll.setWidget(_content)
        outer.addWidget(self._scroll, 1)

        # ── Buttons ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(18, 0, 18, 0)
        btn_row.addStretch()

        cancel_btn = ActionButton("取消", variant="quiet")
        cancel_btn.setFixedHeight(32)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._ok_btn = ActionButton("开始审计", variant="primary")
        self._ok_btn.setFixedHeight(32)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(self._ok_btn)

        outer.addLayout(btn_row)

        # Wire up strategy toggle and apply initial state
        if self._has_prior_audit or self._has_audit_checkpoint:
            self._strategy_group.idToggled.connect(self._on_strategy_changed)
            self._apply_strategy_state()

        self._apply_mode_state()

    def _on_range_changed(self, _id: int, checked: bool) -> None:
        if checked:
            self._ch_scroll.setVisible(_id == 1)
            self.adjustSize()

    def _on_repair_mode_changed(self, _index: int = 0) -> None:
        """Enable/disable repair-dependent controls based on selected mode."""
        for widget in self._repair_dependent_widgets:
            widget.setEnabled(False)
            widget.setVisible(False)

    def _on_mode_toggle(self) -> None:
        self._is_advanced_mode = not self._is_advanced_mode
        self._apply_mode_state()
        settings = QSettings("NovelForge", "BookAuditDialog")
        settings.setValue("advanced_mode", self._is_advanced_mode)

    def _apply_mode_state(self) -> None:
        visible = bool(self._is_advanced_mode)
        for widget in self._advanced_widgets:
            widget.setVisible(visible)
        if self._is_advanced_mode:
            self._mode_toggle_btn.setText("简单模式")
        else:
            self._mode_toggle_btn.setText("高级模式")
        self._on_repair_mode_changed()

    def _on_strategy_changed(self, _id: int, checked: bool) -> None:
        """Switch between re-audit and continue-repair modes."""
        if checked:
            self._apply_strategy_state()

    def _apply_strategy_state(self) -> None:
        """Enable/disable sections based on the selected audit strategy."""
        is_continue = hasattr(self, "_strategy_continue") and self._strategy_continue.isChecked()
        is_continue_audit = (
            hasattr(self, "_strategy_continue_audit") and self._strategy_continue_audit.isChecked()
        )

        # Disable audit parameters only in "续修" mode (no new audit will run).
        for w in self._audit_param_widgets:
            w.setEnabled(not is_continue)

        if is_continue:
            self._repair_mode.setEnabled(False)
            self._on_repair_mode_changed()
            self._ok_btn.setText("生成修复队列")
        elif is_continue_audit:
            self._repair_mode.setEnabled(True)
            self._on_repair_mode_changed()
            self._ok_btn.setText("继续审计")
        else:
            # Restore repair mode selector
            self._repair_mode.setEnabled(True)
            self._on_repair_mode_changed()
            self._ok_btn.setText("开始审计")

    def _on_accept(self) -> None:
        if self._range_group.checkedId() == 1:
            selected = [ch for ch, cb in self._ch_boxes.items() if cb.isChecked()]
            if len(selected) < 2:
                from novel_forge.desktop.components.dialogs import show_warning_message

                show_warning_message(self, "章节不足", "至少需要选择 2 个章节才能进行一致性审计。")
                return
            self._selected_chapters = selected
        else:
            self._selected_chapters = []
        self._accepted = True
        self.accept()

    def shutdown(self) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        from novel_forge.desktop.shutdown_utils import safe_disconnect

        safe_disconnect(self._repair_mode.currentIndexChanged)
        safe_disconnect(self._strategy_group.idToggled)
        safe_disconnect(self._mode_toggle_btn.clicked)


# ═══════════════════════════════════════════════════════════════════
# 清理失效章节对话框
# ═══════════════════════════════════════════════════════════════════


class CleanChaptersDialog(QDialog):
    """选择从第几章起清理失效文件，支持手动调整起始章。"""

    CONFIRM = "ok"
    CANCEL = "cancel"

    def __init__(
        self,
        default_cutoff: int,
        max_chapter: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("chapterStudioDialog")
        self.setWindowTitle("清理失效章节")
        self.setModal(True)
        self.setFixedWidth(480)
        self._result = self.CANCEL
        self._build_ui(default_cutoff, max_chapter)
        self.adjustSize()

    def _build_ui(self, default_cutoff: int, max_chapter: int) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)

        title_lbl = QLabel("清理失效章节")
        title_lbl.setObjectName("dlgTitle")
        root.addWidget(title_lbl)

        desc_lbl = QLabel(
            f"将从第 <b>{default_cutoff}</b> 章起删除已生成文件（正文、草稿、报告等），从该章节重新开始。\n"
            "你可以手动调整起始章；Canon 水位将同步回滚至清理起始章的前一章。"
        )
        desc_lbl.setObjectName("dlgBody")
        desc_lbl.setWordWrap(True)
        root.addWidget(desc_lbl)

        div = QFrame()
        div.setObjectName("dlgDivider")
        div.setFixedHeight(1)
        root.addWidget(div)

        spin_row = QHBoxLayout()
        spin_row.setSpacing(8)
        spin_lbl = QLabel("从第")
        spin_lbl.setObjectName("dlgSection")
        spin_row.addWidget(spin_lbl)
        self._spin = QSpinBox()
        self._spin.setRange(1, max_chapter)
        self._spin.setValue(default_cutoff)
        self._spin.setFixedWidth(72)
        spin_row.addWidget(self._spin)
        spin_row.addWidget(QLabel("章起清理"))
        spin_row.addStretch()
        root.addLayout(spin_row)

        warn_lbl = QLabel("⚠️ 此操作不可撤销，清理后只能重新生成。")
        warn_lbl.setObjectName("dlgHint")
        warn_lbl.setWordWrap(True)
        root.addWidget(warn_lbl)

        root.addSpacing(8)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        cancel_btn = ActionButton("取消", variant="quiet")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(lambda: self._done(self.CANCEL))
        btn_row.addWidget(cancel_btn)

        btn_row.addStretch()

        confirm_btn = ActionButton("确认清理", variant="danger")
        confirm_btn.setFixedHeight(36)
        confirm_btn.setDefault(True)
        confirm_btn.clicked.connect(lambda: self._done(self.CONFIRM))
        btn_row.addWidget(confirm_btn)

        root.addLayout(btn_row)

    def _done(self, key: str) -> None:
        self._result = key
        self.accept() if key == self.CONFIRM else self.reject()

    def result_key(self) -> str:
        return self._result

    def selected_cutoff(self) -> int:
        return self._spin.value()


# ═══════════════════════════════════════════════════════════════════
# 章节连跑跳过策略对话框
# ═══════════════════════════════════════════════════════════════════


class SkipStrategyDialog(QDialog):
    """选择章节连跑时对已完成章节的处理策略。"""

    SKIP = "skip"
    REGENERATE = "regenerate"
    CANCEL = "cancel"

    def __init__(self, done_count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chapterStudioDialog")
        self.setWindowTitle("章节连跑\u2014已有完成章节")
        self.setModal(True)
        self.setFixedWidth(480)
        self._result = self.CANCEL
        self._delete_old_chapters: bool = True
        self._build_ui(done_count)
        self.adjustSize()

    def _build_ui(self, done_count: int) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)

        title_lbl = QLabel("已有完成章节")
        title_lbl.setObjectName("dlgTitle")
        root.addWidget(title_lbl)

        count_lbl = QLabel(f"当前项目已有 {done_count} 章完成归档，请选择处理方式：")
        count_lbl.setObjectName("dlgCount")
        count_lbl.setWordWrap(True)
        root.addWidget(count_lbl)

        div = QFrame()
        div.setObjectName("dlgDivider")
        div.setFixedHeight(1)
        root.addWidget(div)

        body_lbl = QLabel(
            "「跳过已完成」（推荐）：从第一个未完成章节继续推进，已完成章节保持不变。\n\n"
            "「全部重新生成」：从当前章节开始，所有章节（含已完成）重新走完整流程。"
        )
        body_lbl.setObjectName("dlgBody")
        body_lbl.setWordWrap(True)
        root.addWidget(body_lbl)

        self._delete_checkbox = QCheckBox(
            "同时删除已完成章节的正文文件（推荐，避免新旧世界观冲突）"
        )
        self._delete_checkbox.setChecked(True)
        self._delete_checkbox.setObjectName("dlgBody")
        root.addWidget(self._delete_checkbox)

        root.addSpacing(8)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        regen_btn = ActionButton("全部重新生成", variant="danger")
        regen_btn.setFixedHeight(36)
        regen_btn.clicked.connect(lambda: self._done(self.REGENERATE))
        btn_row.addWidget(regen_btn)

        btn_row.addStretch()

        cancel_btn = ActionButton("取消", variant="quiet")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(lambda: self._done(self.CANCEL))
        btn_row.addWidget(cancel_btn)

        skip_btn = ActionButton("跳过已完成（推荐）", variant="primary")
        skip_btn.setFixedHeight(36)
        skip_btn.setDefault(True)
        skip_btn.clicked.connect(lambda: self._done(self.SKIP))
        btn_row.addWidget(skip_btn)

        root.addLayout(btn_row)

    def _done(self, key: str) -> None:
        self._result = key
        self._delete_old_chapters = self._delete_checkbox.isChecked()
        self.accept()

    def get_result(self) -> str:
        return self._result

    def should_delete_old_chapters(self) -> bool:
        return self._delete_old_chapters


class ProjectSwitchConfirmDialog(QDialog):
    """确认在项目有运行中任务时切换项目。"""

    def __init__(
        self,
        project_name: str,
        chapter_number: int,
        job_status: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("chapterStudioDialog")
        self.setWindowTitle("切换项目")
        self.setModal(True)
        self.setFixedWidth(480)
        self._build_ui(project_name, chapter_number, job_status)
        self.adjustSize()

    def _build_ui(self, project_name: str, chapter_number: int, job_status: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)

        title_lbl = QLabel("切换项目")
        title_lbl.setObjectName("dlgTitle")
        root.addWidget(title_lbl)

        status_lbl = QLabel(f"当前项目「{project_name}」{job_status}")
        status_lbl.setObjectName("dlgBody")
        status_lbl.setWordWrap(True)
        root.addWidget(status_lbl)

        div = QFrame()
        div.setObjectName("dlgDivider")
        div.setFixedHeight(1)
        root.addWidget(div)

        hint_lbl = QLabel("切换后任务将继续在后台运行")
        hint_lbl.setObjectName("dlgHint")
        hint_lbl.setWordWrap(True)
        root.addWidget(hint_lbl)

        root.addSpacing(8)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        confirm_btn = ActionButton("确定", variant="primary")
        confirm_btn.setFixedHeight(36)
        confirm_btn.setDefault(True)
        confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(confirm_btn)

        cancel_btn = ActionButton("取消", variant="quiet")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        root.addLayout(btn_row)
