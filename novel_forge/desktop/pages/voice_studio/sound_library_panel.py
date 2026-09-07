"""Review-first project sound library for Voice Studio post-production."""

from __future__ import annotations

import html
import re
from typing import Literal, cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.audio_player import AudioPlayerWidget
from novel_forge.desktop.pages.document_renderer.incremental import update_browser_html
from novel_forge.desktop.widgets import ActionButton, Badge, EmptyState, SectionHeading, Surface
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.schemas import SoundAsset
from novel_forge.tts.services.studio_service import (
    SoundLibraryQuery,
    VoiceStudioProjectService,
)

_KIND_LABELS = {"bgm": "背景音乐", "soundscape": "环境声", "sfx": "短音效"}
_STATUS_LABELS = {"approved": "已批准", "pending": "待试听", "rejected": "已停用"}
_STATUS_ICONS = {"approved": "✓", "pending": "◌", "rejected": "—"}
_COMMERCIAL_STATUS_LABELS = {
    "cleared": "已确认可商用",
    "review_required": "待核对授权",
    "restricted": "限制商业使用",
}


class SoundLibraryPanel(QWidget):
    """Browse project assets and explicitly published application-wide assets."""

    generate_palette_requested = Signal()
    import_requested = Signal(str)
    library_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("soundLibraryPanel")
        self._service: VoiceStudioProjectService | None = None
        self._assets: list[SoundAsset] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        intro = Surface("panel")
        intro_layout = QHBoxLayout(intro)
        intro_layout.setContentsMargins(8, 4, 8, 4)
        intro_layout.setSpacing(6)
        intro_text = QVBoxLayout()
        intro_text.setSpacing(2)
        intro_text.addWidget(SectionHeading("作品与应用声音资源库"))
        description = QLabel(
            "项目资产服务当前作品；已批准的候选可发布到应用资源库，供其他作品按标签复用。"
            "短音效仍按具体剧情线索补充。"
        )
        description.setObjectName("panelDescription")
        description.setWordWrap(True)
        intro_text.addWidget(description)
        intro_layout.addLayout(intro_text, 1)
        self._library_badge = Badge("0 项资产", tone="muted")
        intro_layout.addWidget(self._library_badge)
        self._generate_btn = ActionButton("生成作品声音方案", variant="primary")
        self._generate_btn.setToolTip("生成 3 首纯音乐与 2 组环境声候选，默认进入待试听状态")
        self._generate_btn.clicked.connect(self.generate_palette_requested.emit)
        intro_layout.addWidget(self._generate_btn)
        layout.addWidget(intro)

        controls = QHBoxLayout()
        controls.setSpacing(4)
        controls.addWidget(QLabel("筛选"))
        self._kind_filter = QComboBox()
        self._kind_filter.addItem("全部类型", "")
        self._kind_filter.addItem("背景音乐", "bgm")
        self._kind_filter.addItem("环境声", "soundscape")
        self._kind_filter.addItem("短音效", "sfx")
        self._kind_filter.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self._kind_filter)
        self._status_filter = QComboBox()
        self._status_filter.addItem("全部状态", "")
        self._status_filter.addItem("待试听", "pending")
        self._status_filter.addItem("已批准", "approved")
        self._status_filter.addItem("已停用", "rejected")
        self._status_filter.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self._status_filter)
        self._scope_filter = QComboBox()
        self._scope_filter.setAccessibleName("声音资源范围")
        self._scope_filter.addItem("全部范围", "")
        self._scope_filter.addItem("当前项目", "project")
        self._scope_filter.addItem("应用共享", "application")
        self._scope_filter.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self._scope_filter)
        controls.addStretch()
        self._import_kind = QComboBox()
        self._import_kind.addItem("导入环境声", "soundscape")
        self._import_kind.addItem("导入背景音乐", "bgm")
        self._import_kind.addItem("导入短音效", "sfx")
        controls.addWidget(self._import_kind)
        import_btn = ActionButton("导入并打标签", variant="secondary")
        import_btn.clicked.connect(
            lambda: self.import_requested.emit(str(self._import_kind.currentData() or "soundscape"))
        )
        controls.addWidget(import_btn)
        layout.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("soundLibrarySplitter")
        splitter.setChildrenCollapsible(False)

        list_card = Surface("card")
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(6, 5, 6, 6)
        list_layout.setSpacing(4)
        list_layout.addWidget(SectionHeading("项目候选与应用复用资产"))
        self._asset_list = QListWidget()
        self._asset_list.setObjectName("soundLibraryList")
        self._asset_list.viewport().setObjectName("soundLibraryListViewport")
        self._asset_list.setMinimumWidth(280)
        self._asset_list.currentItemChanged.connect(self._render_selected)
        list_layout.addWidget(self._asset_list, 1)
        splitter.addWidget(list_card)

        detail_card = Surface("card")
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(7, 5, 7, 7)
        detail_layout.setSpacing(4)
        self._detail_title = SectionHeading("选择声音资产")
        detail_layout.addWidget(self._detail_title)
        self._empty = EmptyState(
            "尚未选择声音资产",
            "从左侧选择候选进行试听、编辑标签，并决定是否进入正式混音。",
        )
        detail_layout.addWidget(self._empty)
        self._detail = QTextBrowser()
        self._detail.setObjectName("soundLibraryDetail")
        self._detail.viewport().setObjectName("soundLibraryDetailViewport")
        self._detail.setOpenExternalLinks(False)
        self._detail.setMinimumHeight(100)
        detail_layout.addWidget(self._detail)
        self._player = AudioPlayerWidget(compact=True)
        self._player.setObjectName("soundLibraryPlayer")
        detail_layout.addWidget(self._player)
        tag_row = QHBoxLayout()
        tag_row.addWidget(QLabel("检索标签"))
        self._tags = QLineEdit()
        self._tags.setObjectName("soundLibraryTags")
        self._tags.setPlaceholderText("如：悬疑、夜雨、城市、人物余韵")
        tag_row.addWidget(self._tags, 1)
        save_tags = ActionButton("保存标签", variant="quiet")
        save_tags.clicked.connect(self._save_tags)
        tag_row.addWidget(save_tags)
        detail_layout.addLayout(tag_row)
        rights_row = QHBoxLayout()
        rights_row.addWidget(QLabel("商用复核"))
        self._commercial_status = QComboBox()
        self._commercial_status.setObjectName("soundLibraryCommercialStatus")
        self._commercial_status.addItem("待核对授权", "review_required")
        self._commercial_status.addItem("已确认可商用", "cleared")
        self._commercial_status.addItem("限制商业使用", "restricted")
        rights_row.addWidget(self._commercial_status)
        self._license_note = QLineEdit()
        self._license_note.setObjectName("soundLibraryLicenseNote")
        self._license_note.setPlaceholderText("授权依据：订单、API 条款或素材来源")
        rights_row.addWidget(self._license_note, 1)
        self._save_rights_btn = ActionButton("保存授权结论", variant="quiet")
        self._save_rights_btn.setToolTip(
            "听感批准不代表获得发行权；商业/Master 交付仅接受已确认可商用资产"
        )
        self._save_rights_btn.clicked.connect(self._save_commercial_rights)
        rights_row.addWidget(self._save_rights_btn)
        detail_layout.addLayout(rights_row)
        decision_row = QHBoxLayout()
        self._approve_btn = ActionButton("批准并用于混音", variant="primary")
        self._approve_btn.clicked.connect(lambda: self._set_status("approved"))
        decision_row.addWidget(self._approve_btn)
        self._pending_btn = ActionButton("保留待试听", variant="secondary")
        self._pending_btn.clicked.connect(lambda: self._set_status("pending"))
        decision_row.addWidget(self._pending_btn)
        self._reject_btn = ActionButton("停用", variant="quiet")
        self._reject_btn.clicked.connect(lambda: self._set_status("rejected"))
        decision_row.addWidget(self._reject_btn)
        self._publish_btn = ActionButton("发布到应用库", variant="secondary")
        self._publish_btn.setToolTip("复制已批准的项目资产到本机应用资源库，供其他项目复用")
        self._publish_btn.clicked.connect(self._publish_selected_asset)
        decision_row.addWidget(self._publish_btn)
        decision_row.addStretch()
        detail_layout.addLayout(decision_row)
        splitter.addWidget(detail_card)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([380, 620])
        layout.addWidget(splitter, 1)
        self._set_detail_enabled(False)

    def set_project_layout(self, layout: ProjectLayout | None) -> None:
        """Compatibility adapter for callers that have not adopted the service facade."""
        self.set_project_service(VoiceStudioProjectService(layout) if layout else None)

    def set_project_service(self, service: VoiceStudioProjectService | None) -> None:
        self._service = service
        self.refresh()

    def set_generation_running(self, running: bool) -> None:
        self._generate_btn.setEnabled(not running and self._service is not None)
        self._generate_btn.setText("正在生成候选…" if running else "生成作品声音方案")

    def refresh(self, *_args: object) -> None:
        selected_id = self._selected_asset_id()
        kind_filter = str(self._kind_filter.currentData() or "")
        status_filter = str(self._status_filter.currentData() or "")
        scope_filter = str(self._scope_filter.currentData() or "")
        self._assets = self._service.all_sound_assets() if self._service is not None else []
        visible = (
            self._service.list_sound_assets(
                SoundLibraryQuery(kind=kind_filter, status=status_filter, scope=scope_filter)
            )
            if self._service is not None
            else []
        )
        self._asset_list.blockSignals(True)
        self._asset_list.clear()
        target_row = -1
        for row, asset in enumerate(visible):
            icon = _STATUS_ICONS.get(asset.approval_status, "○")
            kind = _KIND_LABELS.get(asset.kind, asset.kind)
            scope = "应用库" if asset.scope == "application" else "本项目"
            item = QListWidgetItem(
                f"{icon}  {asset.display_name}\n    {kind} · {scope} · {_STATUS_LABELS.get(asset.approval_status, asset.approval_status)}"
            )
            item.setData(Qt.ItemDataRole.UserRole, asset.asset_id)
            item.setToolTip("、".join(asset.tags))
            self._asset_list.addItem(item)
            if asset.asset_id == selected_id:
                target_row = row
        self._asset_list.blockSignals(False)
        if target_row >= 0:
            self._asset_list.setCurrentRow(target_row)
        elif self._asset_list.count():
            self._asset_list.setCurrentRow(0)
        else:
            self._render_selected(None, None)
        pending = sum(asset.approval_status == "pending" for asset in self._assets)
        approved = sum(asset.approval_status == "approved" for asset in self._assets)
        project_count = sum(asset.scope == "project" for asset in self._assets)
        application_count = sum(asset.scope == "application" for asset in self._assets)
        self._library_badge.setText(
            f"{len(self._assets)} 项 · 项目 {project_count} · 应用 {application_count} · 已批准 {approved}"
        )
        self._library_badge.set_tone("warning" if pending else ("success" if approved else "muted"))
        self._generate_btn.setEnabled(self._service is not None)

    def _selected_asset_id(self) -> str:
        item = self._asset_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""

    def _selected_asset(self) -> SoundAsset | None:
        asset_id = self._selected_asset_id()
        return next((asset for asset in self._assets if asset.asset_id == asset_id), None)

    def _render_selected(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        asset = self._selected_asset() if current is not None else None
        self._set_detail_enabled(asset is not None)
        if asset is None:
            self._detail_title.set_content("选择声音资产")
            self._detail.clear()
            self._tags.clear()
            self._player.stop()
            return
        self._detail_title.set_content(asset.display_name)
        source = {"generated": "AI 生成", "imported": "用户导入", "manual": "人工登记"}.get(
            asset.source, asset.source
        )
        prompt = html.escape(asset.generation_prompt or "—")
        update_browser_html(
            self._detail,
            "<table cellspacing='4'>"
            f"<tr><td><b>用途</b></td><td>{html.escape(_KIND_LABELS.get(asset.kind, asset.kind))}</td></tr>"
            f"<tr><td><b>状态</b></td><td>{html.escape(_STATUS_LABELS.get(asset.approval_status, asset.approval_status))}</td></tr>"
            f"<tr><td><b>来源</b></td><td>{html.escape(source)}</td></tr>"
            f"<tr><td><b>归属</b></td><td>{'应用共享资源库' if asset.scope == 'application' else '当前项目'}</td></tr>"
            f"<tr><td><b>模型</b></td><td>{html.escape(asset.generation_provider or '—')} · {html.escape(asset.generation_model or '—')}</td></tr>"
            f"<tr><td><b>授权</b></td><td>{html.escape(asset.license_note or '未填写')}</td></tr>"
            f"<tr><td><b>商用结论</b></td><td>"
            f"{html.escape(_COMMERCIAL_STATUS_LABELS.get(asset.commercial_use_status, asset.commercial_use_status))}"
            "</td></tr>"
            f"<tr><td><b>生成方向</b></td><td>{prompt}</td></tr>"
            "</table>"
        )
        self._tags.setText("、".join(asset.tags))
        status_index = self._commercial_status.findData(asset.commercial_use_status)
        self._commercial_status.setCurrentIndex(max(0, status_index))
        self._license_note.setText(asset.license_note)
        self._publish_btn.setEnabled(
            self._service is not None
            and asset.scope == "project"
            and asset.approval_status == "approved"
            and asset.commercial_use_status == "cleared"
        )
        if self._service is not None:
            self._player.load_audio(self._service.sound_asset_path(asset))

    def _set_detail_enabled(self, enabled: bool) -> None:
        self._empty.setVisible(not enabled)
        for widget in (
            self._detail,
            self._player,
            self._tags,
            self._commercial_status,
            self._license_note,
            self._save_rights_btn,
            self._approve_btn,
            self._pending_btn,
            self._reject_btn,
            self._publish_btn,
        ):
            widget.setVisible(enabled)

    def _set_status(
        self,
        status: Literal["approved", "pending", "rejected"],
    ) -> None:
        asset = self._selected_asset()
        if asset is None or self._service is None:
            return
        self._service.set_sound_asset_status(asset.asset_id, status)
        self.refresh()
        self.library_changed.emit()

    def _save_tags(self) -> None:
        asset = self._selected_asset()
        if asset is None or self._service is None:
            return
        tags = [item.strip() for item in re.split(r"[,，、]", self._tags.text()) if item.strip()]
        self._service.update_sound_asset_tags(asset.asset_id, tags)
        self.refresh()
        self.library_changed.emit()

    def _save_commercial_rights(self) -> None:
        asset = self._selected_asset()
        if asset is None or self._service is None:
            return
        raw_status = str(self._commercial_status.currentData() or "review_required")
        if raw_status not in {"cleared", "review_required", "restricted"}:
            raw_status = "review_required"
        status = cast(Literal["cleared", "review_required", "restricted"], raw_status)
        note = self._license_note.text().strip()
        if status == "cleared" and not note:
            self._license_note.setPlaceholderText("确认可商用时必须填写授权依据")
            self._license_note.setFocus()
            return
        self._service.set_sound_asset_commercial_rights(
            asset.asset_id,
            status=status,
            license_note=note,
        )
        self.refresh()
        self.library_changed.emit()

    def _publish_selected_asset(self) -> None:
        asset = self._selected_asset()
        if asset is None or self._service is None or asset.scope != "project":
            return
        self._service.publish_sound_asset(asset.asset_id)
        self.refresh()
        self.library_changed.emit()

    def shutdown(self) -> None:
        self._player.shutdown()
