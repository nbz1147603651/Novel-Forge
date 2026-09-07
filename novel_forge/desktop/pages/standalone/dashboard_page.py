"""Dashboard page — the project management hub for the Novel Forge desktop workspace.

Merges the former project browser and the old dashboard into a single
management hub.  Users browse, filter, inspect and manage all projects here,
then navigate to the full-page document reader (卷帙) for deep reading.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Callable, cast

from PySide6.QtCore import Property, QPropertyAnimation, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from novel_forge.core.config import get_settings
from novel_forge.desktop.components.signal_coalescing import TrailingDebounce
from novel_forge.desktop.components.task_focus import TaskFocusPanel
from novel_forge.desktop.constants import SEARCH_DEBOUNCE_MS
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.workflow.artifacts import StepArtifactDialog
from novel_forge.desktop.pages.workflow.jobs import _CARD_FIXED_HEIGHT, JobCard
from novel_forge.desktop.task_observation import TaskFocusScope, TaskObservationStore
from novel_forge.desktop.theme import resolve_qcolor
from novel_forge.desktop.widgets import (
    ActionButton,
    Badge,
    EmptyState,
    FilterChip,
    MetricCard,
    ScrollPage,
    SectionHeading,
    Surface,
    add_card_grid,
    ask_confirmation,
    clear_layout,
)
from novel_forge.desktop.workspace import (
    DesktopProjectItem,
    DesktopWorkspaceSnapshot,
    ProviderStatus,
)
from novel_forge.gateway.profiles import (
    ModelProfile,
    get_model_capabilities,
    load_or_import_profiles,
)

# ── Helper widgets ───────────────────────────────────────────────


def _fast_signature(data: Any) -> str:
    """Build a stable hash fingerprint using normalized pickle + blake2s."""
    import hashlib
    import pickle

    def _normalize(value: Any) -> Any:
        if is_dataclass(value) and not isinstance(value, type):
            value = asdict(value)
        if isinstance(value, dict):
            return tuple(
                (
                    type(key).__name__,
                    repr(key),
                    _normalize(item_value),
                )
                for key, item_value in sorted(value.items(), key=lambda item: repr(item[0]))
            )
        if isinstance(value, (list, tuple)):
            return tuple(_normalize(item) for item in value)
        if isinstance(value, (set, frozenset)):
            return tuple(sorted((_normalize(item) for item in value), key=repr))
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        return repr(value)

    try:
        raw = pickle.dumps(_normalize(data), protocol=pickle.HIGHEST_PROTOCOL)
    except (TypeError, ValueError, pickle.PicklingError):
        import json

        try:
            raw = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode()
        except (TypeError, ValueError):
            raw = repr(data).encode()
    return hashlib.blake2s(raw, digest_size=16).hexdigest()


def _describe_artifact(path: str) -> str:
    """将项目内的相对文件路径翻译为用户友好的中文描述。"""
    import re

    p = path.replace("\\", "/")

    # ── 正文章节 ────────────────────────────────────────────────
    m = re.match(r"chapters/chapter_(\d+)\.md$", p)
    if m:
        return f"第 {int(m.group(1))} 章正文"
    if p == "chapters/short_story.md":
        return "短篇正文"

    # ── 草稿 ────────────────────────────────────────────────────
    m = re.match(r"drafts/chapter_(\d+)/draft_v(\d+)\.md$", p)
    if m:
        return f"第 {int(m.group(1))} 章草稿 v{m.group(2)}"
    m = re.match(r"drafts/chapter_(\d+)/", p)
    if m:
        return f"第 {int(m.group(1))} 章草稿"

    # ── 写作计划 ────────────────────────────────────────────────
    m = re.match(r"plans/plan_chapter_(\d+)\.json$", p)
    if m:
        return f"第 {int(m.group(1))} 章写作计划"
    if p.startswith("plans/"):
        return "章节写作计划"

    # ── 故事状态快照 ────────────────────────────────────────────
    m = re.match(r"states/state_chapter_(\d+)\.json$", p)
    if m:
        return f"第 {int(m.group(1))} 章故事状态"
    m = re.match(r"states/compact_state_chapter_(\d+)\.json$", p)
    if m:
        return f"第 {int(m.group(1))} 章压缩状态"
    if p.startswith("states/"):
        return "故事记忆快照"

    # ── 章节报告 ────────────────────────────────────────────────
    m = re.match(r"reports/chapter_(\d+)_knowledge_boundary_verification\.json$", p)
    if m:
        return f"第 {int(m.group(1))} 章知识边界审计"
    m = re.match(r"reports/report_chapter_(\d+)\.json$", p)
    if m:
        return f"第 {int(m.group(1))} 章质检报告"
    if p.startswith("reports/"):
        return "质检报告"

    # ── Canon / 故事记忆 ────────────────────────────────────────
    if p == "canon/canon_current.json":
        return "当前故事记忆 (Canon)"
    m = re.match(r"canon/canon_chapter_(\d+)\.json$", p)
    if m:
        return f"第 {int(m.group(1))} 章故事记忆存档"
    if p.startswith("canon/"):
        return "故事记忆档案"

    # ── 核心设定文件 ────────────────────────────────────────────
    _core_map = {
        "outline.json": "故事大纲",
        "spec.json": "故事规格设定",
        "story_bible.json": "世界观设定",
        "character_bible.json": "角色档案",
    }
    if p in _core_map:
        return _core_map[p]

    # ── 运行日志 ────────────────────────────────────────────────
    # logs/{run_id}/summary.json → 运行摘要
    m = re.match(r"logs/[^/]+/summary\.json$", p)
    if m:
        return "运行摘要"
    m = re.match(r"logs/[^/]+/events\.jsonl?$", p)
    if m:
        return "运行事件日志"
    # logs/{run_id}/model_calls/{step}.json → 模型调用记录
    m = re.match(r"logs/[^/]+/model_calls/(\d+_\w+)\.json$", p)
    if m:
        step_raw = m.group(1)
        # 去掉序号前缀，转可读
        step_name = re.sub(r"^\d+_", "", step_raw).replace("_", " ")
        return f"模型调用记录（{step_name}）"
    if re.match(r"logs/[^/]+/model_calls/", p):
        return "模型调用记录"
    if p.startswith("logs/"):
        return "运行日志"

    # ── 评估 / 短篇专属 ─────────────────────────────────────────
    if p.endswith("creative_summary.json"):
        return "创意分析报告"
    if p.endswith("alignment_report.json"):
        return "对齐度报告"
    if p.endswith("continuity_report.json"):
        return "连贯性报告"
    if p.endswith("bridge.json"):
        return "章节桥接"
    if p.endswith("edit_tracker.json"):
        return "编辑追踪记录"

    # ── 兜底：保留文件名，去掉目录部分 ─────────────────────────
    return p.rsplit("/", 1)[-1]


def _result_warnings(payload: dict[str, object] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    warnings = payload.get("warnings")
    if not isinstance(warnings, list):
        metadata = payload.get("metadata")
        warnings = metadata.get("warnings") if isinstance(metadata, dict) else []
    return [str(item).strip() for item in (warnings or []) if str(item).strip()]


class _DashModelCard(Surface):
    """Dashboard mini-card: shows a model's static status within a provider group.

    Unlike the settings-page ``_ModelStatusCard`` this card does NOT run live
    connection tests.  Status is derived purely from the configuration state:
    - Green dot  → provider is loaded into the runtime AND the API key is set.
    - Yellow dot → API key is configured but provider is not yet loaded.
    - Red dot    → API key is not configured.
    """

    def __init__(
        self,
        profile: ModelProfile,
        provider: ProviderStatus,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("card", parent)
        self.setMinimumWidth(200)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        # ── Top row: status dot + model display name ──────────────
        top = QHBoxLayout()
        top.setSpacing(6)

        dot = QLabel()
        dot.setFixedSize(10, 10)
        if provider.ready and profile.is_key_configured:
            dot.setObjectName("statusDotGreen")
        elif profile.is_key_configured:
            dot.setObjectName("statusDotYellow")
        else:
            dot.setObjectName("statusDotRed")
        top.addWidget(dot)

        name_label = QLabel(profile.display_name)
        name_label.setObjectName("modelNameLabel")
        name_label.setToolTip(f"{profile.provider}:{profile.model_id}")
        top.addWidget(name_label, 1)
        layout.addLayout(top)

        # ── Status subtitle ───────────────────────────────────────
        if provider.ready and profile.is_key_configured:
            status_text = "已载入"
        elif profile.is_key_configured:
            status_text = "密钥就绪 · 未载入"
        else:
            status_text = "密钥未配置"
        status_label = QLabel(f"{profile.model_id} · {status_text}")
        status_label.setObjectName("statusMeta")
        status_label.setWordWrap(True)
        layout.addWidget(status_label)

        # ── Capability dots (static, from profile registry) ───────
        can_thinking, can_multi_turn = get_model_capabilities(profile.provider, profile.model_id)
        cap_row = QHBoxLayout()
        cap_row.setSpacing(6)
        cap_row.setContentsMargins(0, 2, 0, 0)

        think_dot = QLabel()
        think_dot.setFixedSize(8, 8)
        think_dot.setObjectName("capDotGreen" if can_thinking else "capDotRed")
        cap_row.addWidget(think_dot)
        think_label = QLabel("思考")
        think_label.setObjectName("capLabel")
        cap_row.addWidget(think_label)

        cap_row.addSpacing(8)

        multi_dot = QLabel()
        multi_dot.setFixedSize(8, 8)
        multi_dot.setObjectName("capDotGreen" if can_multi_turn else "capDotRed")
        cap_row.addWidget(multi_dot)
        multi_label = QLabel("多轮")
        multi_label.setObjectName("capLabel")
        cap_row.addWidget(multi_label)

        cap_row.addStretch()
        layout.addLayout(cap_row)


class _AnimatedHero(Surface):
    """Hero panel with a ``scale`` Property for the entrance micro-animation.

    Task 18 polish: the dashboard hero fades in (200ms) and scales from
    0.98 → 1.0 (300ms) on initial render. The scale property is exposed
    via ``Property`` so ``Motion.scale`` can drive the value, and
    ``paintEvent`` applies the scale to the entire widget including its
    shadow. macOS-safe (D1): scale is on the safe list (only geometry
    animations are blocked).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("hero", parent)
        self._scale: float = 1.0

    def _get_scale(self) -> float:
        return self._scale

    def _set_scale(self, value: float) -> None:
        self._scale = value
        self.update()

    scale = Property(float, _get_scale, _set_scale)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        if self._scale == 1.0:
            super().paintEvent(event)
            return
        from PySide6.QtWidgets import QStyleOptionFrame

        option = QStyleOptionFrame()
        self.initStyleOption(option)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        center = self.rect().center()
        painter.translate(center)
        painter.scale(self._scale, self._scale)
        painter.translate(-center.x(), -center.y())
        super().paintEvent(event)
        painter.end()


class _FadeBodyLabel(QLabel):
    """QLabel 子类，在底部叠加一个柔和的垂直渐变遮罩。

    设计意图：避免“在库卷册”窄卡片中的描述文本被硬截断成“...”造成阅读割裂感。
    当卡片在同列等高约束下只露出文本前面 2-3 行时，本控件会在文字下方渲染
    一段由透明过渡到卡片背景色的渐变，让文本看起来像是优雅地淡出。

    实现要点（Spec 要求）：
    - 仅重写 ``paintEvent``，先调用 ``super().paintEvent(event)`` 正常渲染文字，
      再用 ``QLinearGradient`` 在底部 1/3 区域填充背景色。
    - 背景色通过 ``resolve_qcolor("bg.input.soft")`` 读取，与
      ``QFrame#surface[tone="card"]`` 主题色保持一致。
    - 渐变高度固定为 ``max(fontMetrics.height() * 1.2, 24px)``，避免过短看不出
      渐隐效果。
    - 启用 ``setWordWrap(True)`` 后调用方不再需要额外的样式适配。
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setObjectName("cardBody")

    def _text_is_clipped(self) -> bool:
        """判断当前文字在换行后是否超出控件高度（即是否被裁掉）。

        只有文字真的被裁掉时才需要渐变遮罩；短文本完整可见时不画遮罩，
        避免末行被误淡出。
        """
        rect = self.rect()
        if rect.height() <= 0 or rect.width() <= 0:
            return False
        metrics = self.fontMetrics()
        full_rect = metrics.boundingRect(
            QRect(0, 0, rect.width(), 10_000),
            Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )
        return full_rect.height() > rect.height()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)

        # 文字完整可见时不画遮罩，避免短文本末行被误淡出。
        if not self._text_is_clipped():
            return

        # 渐变终点色使用主题 token ``bg.input.soft``，与卡片的
        # ``QFrame#surface[tone="card"]`` 背景色保持一致，避免主题切换后
        # 出现可见的接缝。
        bg_color = resolve_qcolor("bg.input.soft")
        if bg_color.alpha() == 0:
            bg_color = self.window().palette().window().color() if self.window() else bg_color
        if bg_color.alpha() == 0:
            return  # 无背景可参照，跳过遮罩

        rect = self.rect()
        if rect.height() <= 0:
            return

        # Spec：渐变高度 = max(fontMetrics.height() * 1.2, 24px)，
        # 过矮看不出淡出效果，过高又会吃掉正常文字。
        fade_height = max(int(self.fontMetrics().height() * 1.2), 24)
        fade_height = min(fade_height, rect.height() // 2)
        fade_rect = QRect(0, rect.height() - fade_height, rect.width(), fade_height)

        gradient = QLinearGradient(0, fade_rect.top(), 0, fade_rect.bottom())
        gradient.setColorAt(0.0, QColor(bg_color.red(), bg_color.green(), bg_color.blue(), 0))
        gradient.setColorAt(1.0, QColor(bg_color.red(), bg_color.green(), bg_color.blue(), 255))

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(fade_rect, gradient)
        painter.end()


class ProjectCard(Surface):
    """Selectable project card.

    Task 18 polish: hover triggers a 200ms scale micro-animation (1.0 → 1.02)
    via the Motion library, plus a border color change driven by QSS. Scale
    is D1-safe on macOS (only geometry animations are blocked); the
    :hover QSS selector handles the border accent (no animation needed).
    """

    HOVER_SCALE: float = 1.02
    HOVER_DURATION_MS: int = 200

    clicked = Signal(str)

    def __init__(self, project: DesktopProjectItem, parent: QWidget | None = None) -> None:
        super().__init__("card", parent)
        self._project = project
        self._project_id: str = project.project_id
        self.setProperty("project_id", project.project_id)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scale: float = 1.0
        self._hover_anim: QPropertyAnimation | None = None
        self._action_callbacks: dict[str, Callable[[str], None]] = {}
        self._quick_action_buttons: dict[str, QToolButton] = {}
        # Per-button slot handle so set_action_callbacks can rebind cleanly.
        self._action_slots: dict[str, Callable[[], None]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)

        self._title = QLabel(project.title)
        self._title.setObjectName("cardTitle")
        top.addWidget(self._title, 1)

        self._badge = Badge(project.status_label, tone=self._badge_tone(project.status))
        top.addWidget(self._badge)
        layout.addLayout(top)

        self._meta = QLabel(
            f"{project.mode_label} \u00b7 {project.progress_label} \u00b7 {project.last_updated_label}"
        )
        self._meta.setObjectName("cardMeta")
        self._meta.setWordWrap(True)
        layout.addWidget(self._meta)

        self._body = _FadeBodyLabel(project.headline)
        layout.addWidget(self._body)

        # 悬停 tooltip：展示完整描述（不截断），避免 fade 遮罩后内容看不完整。
        self._apply_tooltip(project)

        self._progress = QProgressBar()
        self._progress.setObjectName("projectProgress")
        self._progress.setRange(0, 100)
        self._progress.setValue(project.progress_percent)
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress)

        self._footer = QLabel(f"下一步：{project.next_action}")
        self._footer.setObjectName("cardHint")
        self._footer.setWordWrap(True)
        layout.addWidget(self._footer)

        # ── Quick action bar (Task 6) ──────────────────────────────
        # 阅卷 / 续写 / 蓝图 / 图谱 / 档案 — 五个快速入口，callback 由
        # DashboardPage 注入；未注入的按钮默认隐藏，避免视觉噪声。
        action_bar = QHBoxLayout()
        action_bar.setSpacing(6)
        for label, action in [
            ("阅卷", "view"),
            ("续写", "compose"),
            ("蓝图", "blueprint"),
            ("图谱", "graph"),
            ("档案", "profile"),
        ]:
            btn = QToolButton()
            btn.setText(label)
            btn.setToolTip(label)
            btn.setObjectName("projectCardAction")
            btn.setProperty("action", action)
            btn.setVisible(False)  # 等 set_action_callbacks 注入后再显示
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setAutoRaise(True)
            action_bar.addWidget(btn)
            self._quick_action_buttons[action] = btn
        action_bar.addStretch()
        layout.addLayout(action_bar)

    def _get_scale(self) -> float:
        return self._scale

    def _set_scale(self, value: float) -> None:
        self._scale = value
        self.update()

    scale = Property(float, _get_scale, _set_scale)

    def enterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.isEnabled():
            self._start_hover_animation(self.HOVER_SCALE)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.isEnabled():
            self._start_hover_animation(1.0)
        super().leaveEvent(event)

    def _start_hover_animation(self, target: float) -> None:
        """D1-safe hover scale via Motion (D11 stops prior anim first)."""
        from novel_forge.desktop.motion import Motion

        if self._hover_anim is not None:
            try:
                self._hover_anim.stop()
            except RuntimeError:
                pass
            self._hover_anim = None
        anim = Motion.scale(
            self,
            start_value=self._scale,
            end_value=target,
            duration=self.HOVER_DURATION_MS,
            easing="standard",
        )
        self._hover_anim = anim

        def _clear_hover_anim() -> None:
            if self._hover_anim is anim:
                self._hover_anim = None

        anim.finished.connect(_clear_hover_anim)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        if self._scale == 1.0:
            super().paintEvent(event)
            return
        from PySide6.QtWidgets import QStyleOptionFrame

        option = QStyleOptionFrame()
        self.initStyleOption(option)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        center = self.rect().center()
        painter.translate(center)
        painter.scale(self._scale, self._scale)
        painter.translate(-center.x(), -center.y())
        super().paintEvent(event)
        painter.end()

    def update_project(self, project: DesktopProjectItem) -> None:
        """Refresh card content with updated project data."""
        self._project = project
        self._project_id = project.project_id
        self.setProperty("project_id", project.project_id)
        self._title.setText(project.title)
        self._badge.setText(project.status_label)
        self._badge.set_tone(self._badge_tone(project.status))
        self._meta.setText(
            f"{project.mode_label} \u00b7 {project.progress_label} \u00b7 {project.last_updated_label}"
        )
        self._body.setText(project.headline)
        self._apply_tooltip(project)
        self._progress.setValue(project.progress_percent)
        self._footer.setText(f"下一步：{project.next_action}")

    def _apply_tooltip(self, project: DesktopProjectItem) -> None:
        """Set/clear the hover tooltip showing the full (untruncated) description.

        Only set when the full text differs from the truncated headline so short
        cards don't carry a redundant tooltip. Called from both ``__init__`` and
        ``update_project`` so a refreshed snapshot keeps the tooltip in sync.
        """
        tooltip_text = project.headline_full or project.headline
        if tooltip_text and tooltip_text != project.headline:
            self.setToolTip(tooltip_text)
        else:
            self.setToolTip("")

    def set_project_id(self, project_id: str) -> None:
        """Update the project_id cached on the card (used by quick action callbacks)."""
        self._project_id = project_id
        self.setProperty("project_id", project_id)

    def set_action_callbacks(
        self,
        *,
        view: Callable[[str], None] | None = None,
        compose: Callable[[str], None] | None = None,
        blueprint: Callable[[str], None] | None = None,
        graph: Callable[[str], None] | None = None,
        profile: Callable[[str], None] | None = None,
    ) -> None:
        """Wire quick action buttons to caller-provided callbacks.

        Buttons without a callback are hidden so the bar only shows entries
        the surrounding page can actually route. Idempotent — calling again
        with a different mapping rebinds cleanly.
        """
        new_callbacks: dict[str, Callable[[str], None] | None] = {
            "view": view,
            "compose": compose,
            "blueprint": blueprint,
            "graph": graph,
            "profile": profile,
        }

        def _make_action_slot(action_name: str) -> Callable[[bool], None]:
            def _slot(checked: bool = False) -> None:
                self._emit_action(action_name)

            return _slot

        # Disconnect previously bound handler (if any) to keep this idempotent.
        for action, btn in self._quick_action_buttons.items():
            prior_slot = self._action_slots.pop(action, None)
            if prior_slot is not None:
                try:
                    btn.clicked.disconnect(prior_slot)
                except (RuntimeError, TypeError):
                    pass
            cb = new_callbacks.get(action)
            if cb is not None:
                # Bind the current action name without closing over the loop variable.
                slot = _make_action_slot(action)
                self._action_slots[action] = slot
                btn.clicked.connect(slot)
            btn.setVisible(cb is not None)
        # Store the concrete (non-None) callbacks for emission.
        self._action_callbacks = {
            action: cb for action, cb in new_callbacks.items() if cb is not None
        }

    def _emit_action(self, action: str) -> None:
        cb = self._action_callbacks.get(action)
        if cb is None:
            return
        project_id = self._project_id
        if not project_id:
            return
        cb(str(project_id))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._project.project_id)
        super().mouseReleaseEvent(event)

    @staticmethod
    def _badge_tone(status: str) -> str:
        return {
            "completed": "success",
            "writing": "warning",
        }.get(status, "default")


class DashboardPage(ScrollPage):
    """Project management hub — browse, inspect and manage all projects."""

    navigate_requested = Signal(str)
    compose_requested = Signal(str, int)
    view_project_requested = Signal(str)
    open_project_requested = Signal(str)
    open_blueprint_requested = Signal(str)
    open_graph_requested = Signal(str)
    open_profile_requested = Signal(str)
    open_book_consistency_requested = Signal(str)
    delete_requested = Signal(str)
    rebuild_memory_vectors_requested = Signal(str)
    context_changed = Signal()
    clear_task_flow_requested = Signal(object)  # job_ids
    task_focus_decision_selected = Signal(str, str, str, str)
    task_focus_expand_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._snapshot: DesktopWorkspaceSnapshot | None = None
        self._all_projects: list[DesktopProjectItem] = []
        self._selected_project_id: str | None = None
        self._project_card_widgets: dict[str, ProjectCard] = {}
        self._workspace_fingerprint: object | None = None
        self._jobs_fingerprint: object | None = None
        self._pending_workspace_full: DesktopWorkspaceSnapshot | None = None
        self._pending_workspace_sections: tuple[
            DesktopWorkspaceSnapshot, frozenset[str]
        ] | None = None
        self._pending_jobs: list[DesktopJobRecord] | None = None
        self._pending_search_refresh = False
        self._job_cards: dict[str, JobCard] = {}
        self._jobs_clearable_ids: list[str] = []
        self._task_observation_store: TaskObservationStore | None = None
        self._last_card_columns = 0
        self._active_filter = "all"
        self._filter_predicates: dict[str, Callable[[DesktopProjectItem], bool]] = {
            "all": lambda item: True,
            "writing": lambda item: item.status == "writing",
            "planning": lambda item: item.status == "planning",
            "completed": lambda item: item.status == "completed",
            "long": lambda item: item.mode == "long",
            "short": lambda item: item.mode == "short",
        }
        self._search_refresh_debounce = TrailingDebounce(
            self,
            SEARCH_DEBOUNCE_MS,
            self._apply_search_refresh,
        )
        self._build_ui()

    def shutdown(self) -> None:
        """Stop timers, disconnect signals, and tear down sub-widgets on app exit.

        Thread-pool draining is handled globally by
        ``shutdown_desktop_thread_pools()`` in ``_pre_close_cleanup()``.
        """
        self._search_refresh_debounce.cancel()
        if hasattr(self, "_task_focus_panel"):
            self._task_focus_panel.shutdown()
        self._clear_job_cards()

    def export_ui_state(self) -> dict[str, Any]:
        """Return restart-safe project-library choices for the shell session."""
        return {
            "version": 1,
            "selected_project_id": self._selected_project_id or "",
            "filter": self._active_filter,
            "search": self._search.text(),
        }

    def restore_ui_state(self, payload: object) -> None:
        """Restore library filtering before the next workspace bind renders it."""
        if not isinstance(payload, dict):
            return
        selected_project_id = str(payload.get("selected_project_id") or "").strip()
        filter_key = str(payload.get("filter") or "all")
        search = payload.get("search")
        self._selected_project_id = selected_project_id or None
        self._active_filter = filter_key if filter_key in self._filter_predicates else "all"
        if isinstance(search, str):
            self._search.setText(search)
        if self._snapshot is not None:
            self._render_cards()
            self._render_detail()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        """Run the hero entrance animation on first show."""
        super().showEvent(event)
        if not getattr(self, "_hero_intro_done", False):
            self._animate_hero()
            self._hero_intro_done = True
        self._replay_pending_renders()
        if self._pending_search_refresh:
            self._pending_search_refresh = False
            self._apply_search_refresh()

    def hideEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        if self._search_refresh_debounce.is_active():
            self._search_refresh_debounce.cancel()
            self._pending_search_refresh = True
        super().hideEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not getattr(self, "_project_card_widgets", None):
            return
        columns = self._project_grid_columns()
        if columns != self._last_card_columns:
            self._reflow_project_cards(self._filtered_projects(), columns=columns)

    # ── UI construction ──────────────────────────────────────────

    def _build_ui(self) -> None:
        # ── Hero panel ─────────────────────────────────────────────
        hero = _AnimatedHero()
        self._hero_panel = hero
        hero.setMinimumHeight(280)
        hero.setMaximumHeight(340)
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(24, 18, 24, 18)
        hero_layout.setSpacing(20)

        hero_text = QVBoxLayout()
        hero_text.setSpacing(10)

        hero_eyebrow = QLabel("案头一览")
        hero_eyebrow.setObjectName("eyebrowLabel")
        hero_text.addWidget(hero_eyebrow)

        self._hero_title = QLabel("诸卷总领，尽归案头。")
        self._hero_title.setObjectName("heroTitle")
        self._hero_title.setWordWrap(True)
        hero_text.addWidget(self._hero_title)

        self._hero_body = QLabel("在此管理全部卷册——查检进度、阅卷细览、续写下笔，皆可一步落定。")
        self._hero_body.setObjectName("heroBody")
        self._hero_body.setWordWrap(True)
        hero_text.addWidget(self._hero_body)

        button_row = QHBoxLayout()
        button_row.setSpacing(12)
        self._hero_compose_button = ActionButton("起笔")
        self._hero_compose_button.clicked.connect(self._emit_featured_compose)
        button_row.addWidget(self._hero_compose_button)

        workflow_button = ActionButton("去机杼", variant="secondary")
        workflow_button.clicked.connect(lambda: self.navigate_requested.emit("workflow"))
        button_row.addWidget(workflow_button)
        button_row.addStretch()
        hero_text.addLayout(button_row)
        hero_text.addStretch()
        hero_layout.addLayout(hero_text, 1)

        metrics_panel = QWidget()
        metrics_panel.setMinimumWidth(420)
        metrics_panel.setMaximumWidth(560)
        metrics_layout_outer = QVBoxLayout(metrics_panel)
        metrics_layout_outer.setContentsMargins(0, 0, 0, 0)
        metrics_layout_outer.setSpacing(8)

        metrics_title = QLabel("工坊脉息")
        metrics_title.setObjectName("cardTitle")
        metrics_title.setProperty("compact", True)
        metrics_layout_outer.addWidget(metrics_title)

        metrics_hint = QLabel("总揽卷册规模、字数流转与通路起伏。")
        metrics_hint.setObjectName("cardHint")
        metrics_hint.setProperty("compact", True)
        metrics_hint.setWordWrap(True)
        metrics_layout_outer.addWidget(metrics_hint)

        metrics_layout = QGridLayout()
        metrics_layout.setHorizontalSpacing(10)
        metrics_layout.setVerticalSpacing(10)
        self._metric_cards = [
            MetricCard("项目卷轴", compact=True),
            MetricCard("创作章节", compact=True),
            MetricCard("累计字数", compact=True),
            MetricCard("可用 Provider", compact=True),
        ]
        for index, card in enumerate(self._metric_cards):
            card.setMinimumHeight(88)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            metrics_layout.addWidget(card, index // 2, index % 2)
        metrics_layout_outer.addLayout(metrics_layout)
        hero_layout.addWidget(metrics_panel, 2)
        self.body_layout.addWidget(hero)

        self._task_focus_panel = TaskFocusPanel(
            TaskFocusScope.GLOBAL, title="案头关注", compact=True
        )
        self._task_focus_panel.decision_selected.connect(self.task_focus_decision_selected)
        self._task_focus_panel.expand_requested.connect(self.task_focus_expand_requested)
        self._task_focus_panel.artifact_requested.connect(self._on_task_focus_artifact_requested)
        self.body_layout.addWidget(self._task_focus_panel)

        # ── Library controls (search + filters) ───────────────────
        self.body_layout.addWidget(
            SectionHeading("卷库总览", "按题材、进度与卷势查检卷帙，只理在库诸卷。")
        )

        controls = Surface("panel")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(20, 18, 20, 18)
        controls_layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(14)

        self._search = QLineEdit()
        self._search.setObjectName("dashboardSearch")
        self._search.setPlaceholderText("检卷名、项目 ID、题材或气口")
        self._search.textChanged.connect(self._schedule_search_refresh)
        top_row.addWidget(self._search, 1)

        summary_column = QVBoxLayout()
        summary_column.setSpacing(4)
        self._library_summary = QLabel("卷库未整，书气未成。")
        self._library_summary.setObjectName("cardMeta")
        self._library_summary.setAlignment(Qt.AlignmentFlag.AlignRight)
        summary_column.addWidget(self._library_summary)
        self._result_caption = QLabel("静候卷帙归架…")
        self._result_caption.setObjectName("fieldHint")
        self._result_caption.setAlignment(Qt.AlignmentFlag.AlignRight)
        summary_column.addWidget(self._result_caption)
        top_row.addLayout(summary_column)
        controls_layout.addLayout(top_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        self._filter_buttons: dict[str, FilterChip] = {}
        labels = {
            "all": "全部",
            "writing": "连载中",
            "planning": "筹备中",
            "completed": "已完成",
            "long": "长篇",
            "short": "短篇",
        }
        for key, label in labels.items():
            chip = FilterChip(label, active=key == "all")
            chip.clicked.connect(self._make_filter_handler(key))
            self._filter_buttons[key] = chip
            filter_row.addWidget(chip)
        filter_row.addStretch()
        controls_layout.addLayout(filter_row)
        self.body_layout.addWidget(controls)

        # ── Project detail panel ───────────────────────────────────
        self._detail_panel = Surface("hero")
        detail_layout = QVBoxLayout(self._detail_panel)
        detail_layout.setContentsMargins(24, 22, 24, 22)
        detail_layout.setSpacing(10)

        title_row = QHBoxLayout()
        self._detail_title = QLabel("卷页细览")
        self._detail_title.setObjectName("heroTitle")
        title_row.addWidget(self._detail_title, 1)
        self._detail_badge = Badge("待选择", tone="default")
        title_row.addWidget(self._detail_badge)
        detail_layout.addLayout(title_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)

        self._compose_button = ActionButton("续此卷")
        self._compose_button.clicked.connect(self._emit_compose_for_selected)
        action_row.addWidget(self._compose_button)

        self._browse_button = ActionButton("阅卷", variant="secondary")
        self._browse_button.clicked.connect(self._open_viewer_for_selected)
        action_row.addWidget(self._browse_button)
        self._open_button = self._browse_button

        self._folder_button = ActionButton("打开目录", variant="secondary")
        self._folder_button.clicked.connect(self._open_folder_for_selected)
        action_row.addWidget(self._folder_button)

        self._rebuild_vectors_button = ActionButton("重建向量", variant="secondary")
        self._rebuild_vectors_button.setToolTip(
            "为当前选中项目重建记忆向量索引和表达通道语义索引。\n"
            "不会改动正文、章节大纲或初始化产物。"
        )
        self._rebuild_vectors_button.clicked.connect(self._request_rebuild_vectors_for_selected)
        action_row.addWidget(self._rebuild_vectors_button)

        action_row.addStretch()

        self._delete_button = ActionButton("删除项目", variant="danger")
        self._delete_button.clicked.connect(self._confirm_delete)
        self._delete_button.setEnabled(False)
        action_row.addWidget(self._delete_button)
        detail_layout.addLayout(action_row)

        self._detail_meta = QLabel("点选任意卷帙，即可展开细览与近时产物。")
        self._detail_meta.setObjectName("heroBody")
        self._detail_meta.setWordWrap(True)
        detail_layout.addWidget(self._detail_meta)

        self._detail_synopsis = QLabel()
        self._detail_synopsis.setObjectName("cardBody")
        self._detail_synopsis.setWordWrap(True)
        detail_layout.addWidget(self._detail_synopsis)

        self._detail_stats = QLabel()
        self._detail_stats.setObjectName("cardHint")
        self._detail_stats.setWordWrap(True)
        detail_layout.addWidget(self._detail_stats)

        self._detail_chapters = QLabel()
        self._detail_chapters.setObjectName("cardMeta")
        self._detail_chapters.setWordWrap(True)
        detail_layout.addWidget(self._detail_chapters)

        self._detail_recent_files = QLabel()
        self._detail_recent_files.setObjectName("cardMeta")
        self._detail_recent_files.setWordWrap(True)
        detail_layout.addWidget(self._detail_recent_files)
        self.body_layout.addWidget(self._detail_panel)

        # ── Card grid ──────────────────────────────────────────────
        self.body_layout.addWidget(SectionHeading("在库卷册", "筛选一动，下面诸卷随之更迭。"))
        self._cards_grid = QGridLayout()
        self._cards_grid.setHorizontalSpacing(12)
        self._cards_grid.setVerticalSpacing(12)
        self.body_layout.addLayout(self._cards_grid)

        self._empty = EmptyState(
            "卷架暂寂，尘光未动",
            "当前筛选下暂无相合卷帙。可更易筛法，或去机杼页起笔一篇。",
        )
        self._empty.setVisible(False)
        self.body_layout.addWidget(self._empty)

        # ── Jobs ───────────────────────────────────────────────────
        jobs_heading_row = QHBoxLayout()
        jobs_heading_row.setSpacing(10)
        jobs_heading_row.addWidget(
            SectionHeading("后台任务", "全部任务实时进度，滚动查看更多。"),
            1,
        )
        self._clear_jobs_btn = ActionButton("🧹 清理", variant="secondary")
        self._clear_jobs_btn.setToolTip(
            "清空案头任务列表里的历史条目（已完成 / 失败 / 待决策）。\n运行中任务不会被中断。"
        )
        self._clear_jobs_btn.setFixedHeight(30)
        self._clear_jobs_btn.setEnabled(False)
        self._clear_jobs_btn.clicked.connect(self._on_clear_jobs)
        jobs_heading_row.addWidget(self._clear_jobs_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body_layout.addLayout(jobs_heading_row)

        self._jobs_scroll = QScrollArea()
        self._jobs_scroll.setWidgetResizable(True)
        self._jobs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._jobs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._jobs_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._jobs_scroll.setMaximumHeight(640)
        self._jobs_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._jobs_scroll.setObjectName("dashboardJobsScroll")

        jobs_container = QWidget()
        jobs_container.setObjectName("dashboardTaskFlowContent")
        self._jobs_layout = QVBoxLayout(jobs_container)
        self._jobs_layout.setSpacing(12)
        self._jobs_layout.setContentsMargins(12, 12, 12, 12)
        self._jobs_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._jobs_scroll.setWidget(jobs_container)
        self.body_layout.addWidget(self._jobs_scroll)

        # ── Workspace status ───────────────────────────────────────
        self.body_layout.addWidget(SectionHeading("案头近况", "工坊所在与各供应商、模型就绪状态。"))
        workspace_panel = Surface("panel")
        workspace_layout = QVBoxLayout(workspace_panel)
        workspace_layout.setContentsMargins(22, 20, 22, 20)
        workspace_layout.setSpacing(10)
        workspace_layout.addWidget(SectionHeading("工坊所在", "此处记路径、默认通路与卷库规模。"))
        self._workspace_path = QLabel("工坊路径尚未照见。")
        self._workspace_path.setObjectName("cardBody")
        self._workspace_path.setWordWrap(True)
        workspace_layout.addWidget(self._workspace_path)
        self._workspace_summary = QLabel("")
        self._workspace_summary.setObjectName("cardMeta")
        self._workspace_summary.setWordWrap(True)
        workspace_layout.addWidget(self._workspace_summary)
        self._workspace_mix = QLabel("")
        self._workspace_mix.setObjectName("cardHint")
        self._workspace_mix.setWordWrap(True)
        workspace_layout.addWidget(self._workspace_mix)
        self.body_layout.addWidget(workspace_panel)

        # 通路点检：供应商分组 + 模型状态卡片（全宽，自适应）
        self.body_layout.addWidget(
            SectionHeading(
                "通路点检",
                "供应商下各模型就绪状态一览。绿灯 = 已载入，黄灯 = 密钥就绪未启，红灯 = 密钥未配置。",
            )
        )
        self._provider_groups_layout = QVBoxLayout()
        self._provider_groups_layout.setSpacing(16)
        self.body_layout.addLayout(self._provider_groups_layout)

        self.body_layout.addStretch()

    # ── Data binding ─────────────────────────────────────────────

    def _is_background_hidden(self) -> bool:
        host_window = self.window()
        return host_window is not self and host_window.isVisible() and not self.isVisible()

    def _adopt_workspace_state(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        self._snapshot = snapshot
        self._all_projects = list(snapshot.projects)
        available_ids = {item.project_id for item in self._all_projects}
        if self._selected_project_id not in available_ids:
            self._selected_project_id = (
                self._all_projects[0].project_id if self._all_projects else None
            )

    def _replay_pending_renders(self) -> None:
        full_snapshot = self._pending_workspace_full
        section_binding = self._pending_workspace_sections
        pending_jobs = self._pending_jobs
        self._pending_workspace_full = None
        self._pending_workspace_sections = None
        self._pending_jobs = None
        if full_snapshot is not None:
            self.bind_workspace(full_snapshot)
        elif section_binding is not None:
            self.bind_workspace_sections(*section_binding)
        if pending_jobs is not None:
            self.bind_jobs(pending_jobs)

    def bind_workspace(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        fingerprint = self._workspace_signature(snapshot)
        if fingerprint == self._workspace_fingerprint:
            self._adopt_workspace_state(snapshot)
            return
        if self._is_background_hidden():
            self._adopt_workspace_state(snapshot)
            self._pending_workspace_full = snapshot
            self._pending_workspace_sections = None
            return
        self._workspace_fingerprint = fingerprint

        self._adopt_workspace_state(snapshot)

        self._metric_cards[0].set_content(
            "项目卷轴",
            str(snapshot.metrics.total_projects),
            f"长篇 {snapshot.overview.long_projects} \u00b7 短篇 {snapshot.overview.short_projects}",
        )
        self._metric_cards[1].set_content(
            "创作章节",
            str(snapshot.metrics.total_chapters),
            f"默认通路：{snapshot.default_provider}",
        )
        self._metric_cards[2].set_content(
            "累计字数",
            f"{snapshot.metrics.total_words:,}",
            f"数据目录：{snapshot.storage_root}",
        )
        self._metric_cards[3].set_content(
            "可用 Provider",
            str(snapshot.metrics.configured_providers),
            "、".join(snapshot.overview.providers)
            if snapshot.overview.providers
            else "当前仅 Mock",
        )

        self._render_hero(snapshot.featured_project)
        self._render_status(snapshot)
        self._render_cards()
        self._render_detail()

        self.context_changed.emit()

    def _on_task_focus_artifact_requested(
        self,
        project_id: str,
        job_kind: str,
        step_key: str,
        step_label: str,
        chapter_number: int,
    ) -> None:
        project_dir = (
            self._snapshot.storage_root / project_id
            if self._snapshot is not None and project_id
            else None
        )
        nav_target = StepArtifactDialog.show_for_step(
            kind=job_kind,
            step_key=step_key,
            step_label=step_label or step_key,
            project_dir=project_dir,
            chapter_number=chapter_number,
            parent=self.window(),
        )
        if nav_target:
            win = self.window()
            if hasattr(win, "switch_page"):
                win.switch_page(nav_target)

    def bind_workspace_sections(
        self, snapshot: DesktopWorkspaceSnapshot, sections: frozenset[str]
    ) -> None:
        """Incrementally re-render only the changed *sections* of the dashboard.

        Section names and their render targets:

        * ``"metrics"``          → metric cards (项目卷轴 / 创作章节 / 累计字数 / Provider)
        * ``"projects"``         → project card grid + detail panel
        * ``"overview"``         → workspace status + provider model cards
        * ``"featured_project"`` → hero section

        Base state (``_snapshot``, ``_all_projects``, project selection) is
        always refreshed so that render methods see consistent data.
        """
        # Always update base state — render methods and top-bar actions depend on these.
        self._adopt_workspace_state(snapshot)
        if self._is_background_hidden():
            if self._pending_workspace_full is not None:
                self._pending_workspace_full = snapshot
            else:
                pending_sections = (
                    self._pending_workspace_sections[1]
                    if self._pending_workspace_sections is not None
                    else frozenset()
                )
                self._pending_workspace_sections = (snapshot, pending_sections | sections)
            return

        if not sections:
            return

        # ── metrics → metric card content ────────────────────────────
        if "metrics" in sections:
            self._metric_cards[0].set_content(
                "项目卷轴",
                str(snapshot.metrics.total_projects),
                f"长篇 {snapshot.overview.long_projects} \u00b7 "
                f"短篇 {snapshot.overview.short_projects}",
            )
            self._metric_cards[1].set_content(
                "创作章节",
                str(snapshot.metrics.total_chapters),
                f"默认通路：{snapshot.default_provider}",
            )
            self._metric_cards[2].set_content(
                "累计字数",
                f"{snapshot.metrics.total_words:,}",
                f"数据目录：{snapshot.storage_root}",
            )
            self._metric_cards[3].set_content(
                "可用 Provider",
                str(snapshot.metrics.configured_providers),
                "、".join(snapshot.overview.providers)
                if snapshot.overview.providers
                else "当前仅 Mock",
            )

        # ── overview → workspace status + provider cards ─────────────
        if "overview" in sections:
            self._render_status(snapshot)

        # ── featured_project → hero section ──────────────────────────
        if "featured_project" in sections:
            self._render_hero(snapshot.featured_project)

        # ── projects → project card grid + detail panel ──────────────
        if "projects" in sections:
            self._render_cards()
            self._render_detail()

        self.context_changed.emit()

    def supports_job_binding(self) -> bool:
        return True

    def on_jobs_changed(self, jobs: list[DesktopJobRecord]) -> None:
        self.bind_jobs(jobs)

    def bind_task_observation_store(self, store: TaskObservationStore) -> None:
        self._task_observation_store = store
        self._task_focus_panel.bind_store(store)
        self._task_focus_panel.set_scope(TaskFocusScope.GLOBAL)

    def bind_jobs(self, jobs: list[DesktopJobRecord]) -> None:
        fingerprint = self._jobs_signature(jobs)
        if fingerprint == self._jobs_fingerprint:
            return
        if self._is_background_hidden():
            self._pending_jobs = list(jobs)
            return
        self._jobs_fingerprint = fingerprint

        top_jobs = jobs[:24]
        self._jobs_clearable_ids = [
            job.job_id
            for job in top_jobs
            if job.status
            in {DesktopJobState.SUCCEEDED, DesktopJobState.FAILED, DesktopJobState.PAUSED}
        ]
        self._clear_jobs_btn.setEnabled(bool(self._jobs_clearable_ids))

        if not top_jobs:
            self._clear_job_cards()
            clear_layout(self._jobs_layout)
            empty_surface = Surface("inset")
            el = QVBoxLayout(empty_surface)
            el.setContentsMargins(24, 20, 24, 20)
            label = QLabel("案头暂静，正宜拈笔开卷。")
            label.setObjectName("emptyMessage")
            label.setWordWrap(True)
            el.addWidget(label)
            self._jobs_layout.addWidget(empty_surface)
            self._update_jobs_scroll_min_height()
            return

        visible_ids = {job.job_id for job in top_jobs}
        for job_id in tuple(self._job_cards.keys()):
            if job_id in visible_ids:
                continue
            card = self._job_cards.pop(job_id)
            self._jobs_layout.removeWidget(card)
            card.shutdown()
            card.deleteLater()

        if not self._job_cards and self._jobs_layout.count():
            clear_layout(self._jobs_layout)

        storage_root = self._snapshot.storage_root if self._snapshot is not None else None
        for index, job in enumerate(top_jobs):
            card = self._job_cards.get(job.job_id)
            if card is None:
                card = JobCard(job, storage_root=storage_root)
                card.stop_requested.connect(self._on_stop_job)
                card.resume_requested.connect(self._on_resume_job)
                card.checkpoint_resume_requested.connect(self._on_checkpoint_resume_job)
                self._job_cards[job.job_id] = card
            else:
                card.set_storage_root(storage_root)
                card.set_job(job)
            self._jobs_layout.insertWidget(index, card)
        self._update_jobs_scroll_min_height()

    def _update_jobs_scroll_min_height(self) -> None:
        visible_cards = 2.5
        row_height = _CARD_FIXED_HEIGHT

        margins = self._jobs_layout.contentsMargins()
        spacing = self._jobs_layout.spacing()
        min_height = int(
            margins.top()
            + margins.bottom()
            + row_height * visible_cards
            + spacing * max(0.0, visible_cards - 1.0)
        )
        self._jobs_scroll.setMinimumHeight(min(min_height, 520))

    def _on_clear_jobs(self) -> None:
        ids = list(self._jobs_clearable_ids)
        if not ids:
            return
        self.clear_task_flow_requested.emit(ids)

    def _clear_job_cards(self) -> None:
        for card in self._job_cards.values():
            self._jobs_layout.removeWidget(card)
            card.shutdown()
            card.deleteLater()
        self._job_cards.clear()

    def _on_stop_job(self, job_id: str) -> None:
        win = self.window()
        if hasattr(win, "_job_manager"):
            win._job_manager.cancel_job(job_id, reason="用户已取消")

    def _on_resume_job(self, job_id: str, project_id: str, chapter_number: int) -> None:
        if project_id:
            self.compose_requested.emit(project_id, chapter_number or 1)

    def _on_checkpoint_resume_job(
        self,
        job_id: str,
        project_id: str,
        chapter_number: int,
    ) -> None:
        if project_id:
            self.compose_requested.emit(project_id, chapter_number or 1)

    # ── Hero rendering ───────────────────────────────────────────

    def _render_hero(self, project: DesktopProjectItem | None) -> None:
        if project is None:
            self._hero_title.setText("案头未陈一卷，且先起今日第一笔。")
            self._hero_body.setText("可先往机杼启篇，再回案头候其行止。")
            self._hero_compose_button.setText("起笔")
            return

        if project.mode == "long":
            self._hero_title.setText(f"当前主卷：{project.title}")
            self._hero_compose_button.setText("续主卷")
        else:
            self._hero_title.setText(f"案头当看：{project.title}")
            self._hero_compose_button.setText("赴机杼")
        self._hero_body.setText(
            f"{project.mode_label} \u00b7 {project.progress_label} \u00b7 "
            f"最近更新 {project.last_updated_label}"
        )

    # ── Status rendering ─────────────────────────────────────────

    def _render_status(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        self._workspace_path.setText(f"工作区：{snapshot.storage_root}")
        self._workspace_summary.setText(
            f"默认通路：{snapshot.default_provider}  \u00b7  库中凡 {snapshot.metrics.total_projects} 卷"
        )
        self._workspace_mix.setText(
            f"长篇 {snapshot.overview.long_projects} \u00b7 短篇 {snapshot.overview.short_projects} \u00b7 "
            f"已成章节 {snapshot.metrics.total_chapters}"
        )

        # ── Provider-grouped model status cards ───────────────────
        clear_layout(self._provider_groups_layout)
        config = load_or_import_profiles(get_settings())

        # Group configured model profiles by provider
        provider_profiles: dict[str, list[ModelProfile]] = {}
        for profile in config.profiles:
            provider_profiles.setdefault(profile.provider, []).append(profile)

        first = True
        for provider in snapshot.providers:
            profiles = provider_profiles.get(provider.provider_id, [])
            # Skip fully unconfigured providers that have no registered models
            if not profiles and not provider.configured and not provider.ready:
                continue

            if not first:
                spacer = QWidget()
                spacer.setFixedHeight(2)
                self._provider_groups_layout.addWidget(spacer)
            first = False

            # ── Provider header row ───────────────────────────────
            header = QWidget()
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(0, 0, 0, 4)
            header_layout.setSpacing(8)

            prov_label = QLabel(provider.label)
            prov_label.setObjectName("providerGroupTitle")
            header_layout.addWidget(prov_label)

            if provider.is_default:
                badge_text, tone = "默认", "warning" if provider.ready else "default"
            elif provider.ready:
                badge_text, tone = "就绪", "success"
            elif provider.configured:
                badge_text, tone = "待启", "warning"
            else:
                badge_text, tone = "未备", "default"
            header_layout.addWidget(Badge(badge_text, tone=tone))

            detail_label = QLabel(provider.detail)
            detail_label.setObjectName("cardMeta")
            header_layout.addWidget(detail_label)
            header_layout.addStretch()
            self._provider_groups_layout.addWidget(header)

            # ── Model cards (3-column responsive grid) ────────────
            if profiles:
                model_grid = QGridLayout()
                model_grid.setHorizontalSpacing(12)
                model_grid.setVerticalSpacing(12)
                cards: list[QWidget] = [_DashModelCard(p, provider) for p in profiles]
                add_card_grid(model_grid, cards, columns=3)
                self._provider_groups_layout.addLayout(model_grid)

    # ── Project filtering & card rendering ───────────────────────

    def _make_filter_handler(self, filter_key: str) -> Callable[[], None]:
        def _handler() -> None:
            self._active_filter = filter_key
            self._render_cards()
            self._render_detail()
            self.context_changed.emit()

        return _handler

    def _schedule_search_refresh(self) -> None:
        self._search_refresh_debounce.trigger()

    def _apply_search_refresh(self) -> None:
        if self._is_background_hidden():
            self._pending_search_refresh = True
            return
        self._render_cards()
        self._render_detail()
        self.context_changed.emit()

    def _filtered_projects(self) -> list[DesktopProjectItem]:
        predicate = self._filter_predicates[self._active_filter]
        query = self._search.text().strip().casefold()
        filtered = [item for item in self._all_projects if predicate(item)]
        if not query:
            return filtered
        return [
            item
            for item in filtered
            if query
            in " ".join(
                [
                    item.project_id,
                    item.title,
                    item.genre,
                    item.tone,
                    item.headline_full or item.headline,
                ]
            ).casefold()
        ]

    def _render_cards(self) -> None:
        filtered = self._filtered_projects()
        for key, chip in self._filter_buttons.items():
            chip.setChecked(key == self._active_filter)

        self._render_library_summary(filtered)
        filtered_ids = {item.project_id for item in filtered}
        current_ids = {pid for pid in self._project_card_widgets}

        to_remove = current_ids - filtered_ids
        for i in range(self._cards_grid.count() - 1, -1, -1):
            grid_item = self._cards_grid.itemAt(i)
            widget = grid_item.widget() if grid_item is not None else None
            if isinstance(widget, ProjectCard) and widget._project.project_id in to_remove:
                self._cards_grid.takeAt(i)
                widget.deleteLater()
                del self._project_card_widgets[widget._project.project_id]

        new_cards: list[ProjectCard] = []
        for project in filtered:
            pid = project.project_id
            if pid in self._project_card_widgets:
                self._project_card_widgets[pid].update_project(project)
            else:
                card = self._build_card(project)
                self._project_card_widgets[pid] = card
                new_cards.append(card)
        self._reflow_project_cards(filtered)

        self._empty.setVisible(not filtered)

        if new_cards:
            self._animate_project_cards(new_cards)

        if filtered and self._selected_project_id not in filtered_ids:
            self._selected_project_id = filtered[0].project_id
        elif not filtered:
            self._selected_project_id = None

    def _render_detail(self) -> None:
        if self._snapshot is None or self._selected_project_id is None:
            self._detail_title.setText("卷页细览")
            self._detail_badge.setText("待选择")
            self._detail_badge.set_tone("default")
            self._detail_meta.setText("点选任意卷帙，即可展开细览与近时产物。")
            self._detail_synopsis.setText("尚未择卷，请先于下方择定一册。")
            self._detail_stats.setText("")
            self._detail_chapters.setText("")
            self._detail_recent_files.setText("")
            self._compose_button.setEnabled(False)
            self._compose_button.setText("续此卷")
            self._browse_button.setEnabled(False)
            self._folder_button.setEnabled(False)
            self._rebuild_vectors_button.setEnabled(False)
            self._delete_button.setEnabled(False)
            return

        self._delete_button.setEnabled(False)
        detail = self._snapshot.details.get(self._selected_project_id)
        project = next(
            (item for item in self._all_projects if item.project_id == self._selected_project_id),
            None,
        )
        if detail is None or project is None:
            self._selected_project_id = None
            self._render_detail()
            return
        self._detail_title.setText(project.title)
        self._detail_badge.setText(project.status_label)
        self._detail_badge.set_tone(
            {"completed": "success", "writing": "warning"}.get(project.status, "default")
        )
        self._detail_meta.setText(
            f"{project.mode_label} \u00b7 {project.genre or '未设题材'} \u00b7 "
            f"{project.tone or '未设语气'} \u00b7 近次修订 {project.last_updated_label}"
        )
        self._detail_synopsis.setText(
            detail.preview or detail.premise or "此卷尚无序言，待笔墨落定后方可细读。"
        )
        artifacts = [
            f"{key}:{value}" for key, value in detail.artifact_counts.items() if value
        ] or ["尚无簿录"]
        self._detail_stats.setText(
            " \u00b7 ".join(
                [
                    f"项目 ID：{detail.project_id}",
                    f"完成度：{project.progress_label}",
                    "卷中簿录：" + ", ".join(artifacts),
                ]
            )
        )
        if detail.chapters:
            chapter_lines = [
                f"第 {item.chapter_number} 章 \u00b7 {item.title or '未命名'} \u00b7 {item.word_count:,} 字"
                for item in detail.chapters[-4:]
            ]
            self._detail_chapters.setText("近时章节：\n" + "\n".join(chapter_lines))
        elif detail.mode == "short":
            self._detail_chapters.setText(
                "短篇正文既成，可点击「阅卷」查看 chapters/short_story.md。"
            )
        else:
            self._detail_chapters.setText("章回尚未落笔，可续此卷，先补首章。")
        if detail.recent_files:
            lines = [f"- {_describe_artifact(path)}" for path in detail.recent_files[:5]]
            self._detail_recent_files.setText("近时产物：\n" + "\n".join(lines))
        else:
            self._detail_recent_files.setText("近时产物：\n- 暂无归档。")
        self._compose_button.setText("续此卷" if project.mode == "long" else "去机杼")
        self._compose_button.setEnabled(True)
        self._browse_button.setEnabled(True)
        self._folder_button.setEnabled(True)
        self._rebuild_vectors_button.setEnabled(True)
        self._delete_button.setEnabled(True)

    def _render_library_summary(self, filtered: list[DesktopProjectItem]) -> None:
        total = len(self._all_projects)
        long_count = sum(1 for item in self._all_projects if item.mode == "long")
        short_count = total - long_count
        writing_count = sum(1 for item in filtered if item.status == "writing")
        planning_count = sum(1 for item in filtered if item.status == "planning")
        completed_count = sum(1 for item in filtered if item.status == "completed")

        self._library_summary.setText(
            f"在库 {total} 卷 \u00b7 长篇 {long_count} \u00b7 短篇 {short_count}"
        )
        self._result_caption.setText(
            f"今筛得 {len(filtered)} 卷 \u00b7 连载 {writing_count} \u00b7 "
            f"筹备 {planning_count} \u00b7 完稿 {completed_count}"
        )

    @staticmethod
    def _workspace_signature(snapshot: DesktopWorkspaceSnapshot) -> object:
        def _safe_asdict(obj: object) -> dict[str, object] | object:
            if not is_dataclass(obj) or isinstance(obj, type):
                return str(obj)
            return cast(dict[str, object], asdict(cast(Any, obj)))

        return _fast_signature(
            {
                "storage_root": str(snapshot.storage_root),
                "default_provider": snapshot.default_provider,
                "overview": snapshot.overview.model_dump(mode="python"),
                "metrics": _safe_asdict(snapshot.metrics),
                "providers": [_safe_asdict(p) for p in snapshot.providers],
                "projects": [_safe_asdict(p) for p in snapshot.projects],
                "featured_project": snapshot.featured_project.project_id
                if snapshot.featured_project
                else None,
                "details": {
                    project_id: detail.model_dump(mode="python")
                    for project_id, detail in sorted(snapshot.details.items())
                },
            }
        )

    @staticmethod
    def _jobs_signature(jobs: list[DesktopJobRecord]) -> object:
        return _fast_signature(
            [
                {
                    "job_id": job.job_id,
                    "kind": job.kind,
                    "label": job.label,
                    "project_id": job.project_id,
                    # Reuse the card's visible signature.  In particular,
                    # do not include updated_at or raw event count: streaming
                    # callbacks update both values without changing the
                    # dashboard card's visible content.
                    "card": JobCard._signature_for(job),
                    "resolved_error_entry_ids": tuple(
                        sorted(
                            str(entry_id)
                            for entry_id in getattr(job, "resolved_error_entry_ids", set())
                        )
                    ),
                }
                for job in jobs[:24]
            ]
        )

    def _build_card(self, project: DesktopProjectItem) -> ProjectCard:
        card = ProjectCard(project)
        card.clicked.connect(self._select_project)
        card.set_action_callbacks(
            view=self.view_project_requested.emit,
            compose=lambda pid: self._emit_compose_for(pid),
            blueprint=self.open_blueprint_requested.emit,
            graph=self.open_graph_requested.emit,
            profile=self.open_profile_requested.emit,
        )
        return card

    def _emit_compose_for(self, project_id: str) -> None:
        """Compose request that resolves the next chapter for *project_id*.

        Falls back to chapter 1 when the project isn't in the current snapshot
        (e.g. cards rendered for projects no longer in the workspace). Mirrors
        the behaviour of ``_emit_compose_for_selected``.
        """
        if not project_id:
            return
        project = next(
            (item for item in self._all_projects if item.project_id == project_id),
            None,
        )
        next_chapter = project.next_chapter if project is not None else 1
        self.compose_requested.emit(project_id, next_chapter or 1)

    # ── Entrance animations (Task 18) ────────────────────────────

    HERO_FADE_DURATION_MS: int = 200
    HERO_SCALE_DURATION_MS: int = 300
    CARD_STAGGER_DELAY_MS: int = 50
    CARD_STAGGER_FADE_MS: int = 220

    def _animate_hero(self) -> None:
        """Run the hero fade-in + scale entrance (D1/D10 safe).

        The hero carries a ``QGraphicsDropShadowEffect`` for elevation; per
        D10, we cannot stack ``QGraphicsOpacityEffect`` on top without a
        segfault. Instead we install the opacity effect for the duration of
        the fade and restore the shadow effect on completion.

        - Fade: 200ms (opacity 0 → 1)
        - Scale: 300ms (0.98 → 1.0, via ``Motion.scale`` on the custom
          ``scale`` Property of ``_AnimatedHero``)
        """
        hero = self._hero_panel
        if hero is None:
            return

        from PySide6.QtWidgets import QGraphicsOpacityEffect

        from novel_forge.desktop.motion import Motion

        # D10: opacity fade temporarily replaces the hero shadow effect.
        prior_effect = hero.graphicsEffect()
        is_opacity = isinstance(prior_effect, QGraphicsOpacityEffect)
        if not is_opacity:
            opacity_effect = QGraphicsOpacityEffect(hero)
            opacity_effect.setOpacity(0.0)
            hero.setGraphicsEffect(opacity_effect)

        fade_anim = Motion.fade_in(
            hero,
            duration=self.HERO_FADE_DURATION_MS,
            easing="standard",
        )

        if not is_opacity and prior_effect is not None:

            def _restore_shadow_effect() -> None:
                try:
                    from PySide6.QtWidgets import QGraphicsDropShadowEffect

                    from novel_forge.desktop.theme import resolve_qcolor

                    shadow = QGraphicsDropShadowEffect(hero)
                    shadow.setBlurRadius(8)
                    shadow.setOffset(0, 4)
                    shadow.setColor(resolve_qcolor("shadow", 28))
                    hero.setGraphicsEffect(shadow)
                except RuntimeError:
                    return

            fade_anim.finished.connect(_restore_shadow_effect)

        # Scale entrance (D1-safe — scale is on the safe list, geometry is not).
        hero._scale = 0.98
        Motion.scale(
            hero,
            start_value=0.98,
            end_value=1.0,
            duration=self.HERO_SCALE_DURATION_MS,
            easing="decelerate",
        )

    def _animate_project_cards(self, cards: list[ProjectCard]) -> None:
        """Stagger the card entrance: 50ms delay between each card's fade-in.

        Each card receives a delayed ``Motion.fade_in`` via
        ``QTimer.singleShot``. Only cards whose ``project_id`` is not already
        in ``_animated_card_ids`` are scheduled — re-renders (e.g. after a
        filter change) reuse existing card widgets and skip the animation.
        """

        if not hasattr(self, "_animated_card_ids"):
            self._animated_card_ids: set[str] = set()
        if not hasattr(self, "_stagger_pending"):
            self._stagger_pending: set[str] = set()

        for index, card in enumerate(cards):
            pid = card._project.project_id
            if pid in self._animated_card_ids or pid in self._stagger_pending:
                continue
            self._stagger_pending.add(pid)
            delay = index * self.CARD_STAGGER_DELAY_MS
            QTimer.singleShot(
                delay,
                lambda c=card, p=pid: self._run_card_stagger(c, p),
            )

    def _run_card_stagger(self, card: ProjectCard, pid: str) -> None:
        """Run a single card's stagger fade-in (called via QTimer)."""
        from novel_forge.desktop.motion import Motion

        try:
            from shiboken6 import isValid
        except ImportError:  # pragma: no cover - PySide always ships shiboken6 in CI.
            isValid = None

        pending = getattr(self, "_stagger_pending", set())
        current_widgets = getattr(self, "_project_card_widgets", {})
        if (
            card is None
            or current_widgets.get(pid) is not card
            or (isValid is not None and not isValid(card))
        ):
            pending.discard(pid)
            return
        try:
            Motion.fade_in(
                card,
                duration=self.CARD_STAGGER_FADE_MS,
                easing="standard",
            )
        except RuntimeError:
            pending.discard(pid)
            return
        pending.discard(pid)
        getattr(self, "_animated_card_ids", set()).add(pid)

    def _project_grid_columns(self) -> int:
        width = self.viewport().width() if self.viewport() is not None else self.width()
        if width >= 1040:
            return 3
        if width >= 700:
            return 2
        return 1

    def _reflow_project_cards(
        self,
        projects: list[DesktopProjectItem],
        *,
        columns: int | None = None,
    ) -> None:
        columns = max(1, columns or self._project_grid_columns())
        while self._cards_grid.count():
            self._cards_grid.takeAt(0)
        for column in range(4):
            self._cards_grid.setColumnStretch(column, 1 if column < columns else 0)
        for index, project in enumerate(projects):
            card = self._project_card_widgets.get(project.project_id)
            if card is None:
                continue
            self._cards_grid.addWidget(card, index // columns, index % columns)
        self._last_card_columns = columns

    def _select_project(self, project_id: str) -> None:
        self._selected_project_id = project_id
        self._render_detail()
        self.context_changed.emit()

    # ── Public accessors ─────────────────────────────────────────

    def selected_project(self) -> DesktopProjectItem | None:
        if self._selected_project_id is None:
            return None
        return next(
            (item for item in self._all_projects if item.project_id == self._selected_project_id),
            None,
        )

    def open_selected_project_folder(self) -> None:
        project = self.selected_project()
        if project is not None:
            self.open_project_requested.emit(project.project_id)

    # ── Action handlers ──────────────────────────────────────────

    def _emit_featured_compose(self) -> None:
        if self._snapshot is None or self._snapshot.featured_project is None:
            self.navigate_requested.emit("workflow")
            return
        project = self._snapshot.featured_project
        if project.mode == "long":
            self.compose_requested.emit(project.project_id, project.next_chapter or 1)
            return
        self.navigate_requested.emit("workflow")

    def _emit_compose_for_selected(self) -> None:
        if self._snapshot is None or self._selected_project_id is None:
            self.compose_requested.emit("", 1)
            return
        project = next(
            item for item in self._all_projects if item.project_id == self._selected_project_id
        )
        self.compose_requested.emit(project.project_id, project.next_chapter or 1)

    def _open_viewer_for_selected(self) -> None:
        if self._selected_project_id:
            self.view_project_requested.emit(self._selected_project_id)

    def _open_folder_for_selected(self) -> None:
        self.open_selected_project_folder()

    def _request_rebuild_vectors_for_selected(self) -> None:
        if self._selected_project_id is None:
            return
        pid = self._selected_project_id
        confirmed = ask_confirmation(
            self,
            "重建向量索引？",
            f"将为项目「{pid}」重建记忆向量索引。",
            informative_text=(
                "该操作只重建 zvec 记忆索引和表达通道语义索引，不会改动正文、"
                "章节大纲或初始化产物。建议在没有章节生成/审计任务运行时执行。"
            ),
            confirm_text="开始重建",
        )
        if confirmed:
            self.rebuild_memory_vectors_requested.emit(pid)

    def _confirm_delete(self) -> None:
        if self._selected_project_id is None:
            return
        pid = self._selected_project_id
        confirmed = ask_confirmation(
            self,
            "确认删除",
            f"确定要永久删除项目「{pid}」？",
            informative_text="此操作不可撤销，项目目录及所有生成内容将被清除。",
            confirm_text="删除项目",
            cancel_text="取消",
            confirm_variant="danger",
        )
        if confirmed:
            self._selected_project_id = None
            self.delete_requested.emit(pid)
