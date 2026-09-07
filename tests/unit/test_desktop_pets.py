from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QAbstractAnimation, QEvent, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from novel_forge.desktop.components.task_focus import FloatingTaskCompanion
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pets import (
    CODEX_ATLAS_CELL_SIZE,
    CODEX_ATLAS_COLUMNS,
    CODEX_ATLAS_ROWS,
    available_pet_ids,
    load_pet_atlas,
    load_pet_definition,
    load_pet_pixmap,
)
from novel_forge.desktop.task_observation import TaskObservationStore
from novel_forge.desktop.window import task_companion as task_companion_module
from novel_forge.desktop.window.task_companion import TaskCompanionMixin


def _qapp() -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def _mouse_event(
    event_type: QEvent.Type,
    *,
    local_x: int,
    local_y: int,
    global_x: int,
    global_y: int,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> QMouseEvent:
    local = QPointF(local_x, local_y)
    global_pos = QPointF(global_x, global_y)
    return QMouseEvent(
        event_type,
        local,
        local,
        global_pos,
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def test_default_desktop_pet_manifest_loads_asset() -> None:
    pet = load_pet_definition()

    assert pet.pet_id == "nimo"
    assert pet.display_name == "Nimo"
    assert pet.standard == "codex-atlas-v1"
    assert pet.sprite_path.exists()
    assert pet.atlas_columns == CODEX_ATLAS_COLUMNS
    assert pet.atlas_rows == CODEX_ATLAS_ROWS
    assert pet.cell_size == CODEX_ATLAS_CELL_SIZE
    assert "running" in pet.states
    assert pet.state("running").row_index == 7
    assert pet.state("decision").row_index == 8
    assert pet.state("paused").label == "等待继续"
    assert "nimo" in available_pet_ids()


def test_desktop_pet_codex_frame_rects_are_cell_aligned() -> None:
    pet = load_pet_definition()

    assert pet.frame_rect("idle", 0) == (0, 0, 192, 208)
    assert pet.frame_rect("idle", 7) == (192, 0, 192, 208)
    assert pet.frame_rect("running", 5) == (960, 1456, 192, 208)
    assert pet.frame_rect("decision", 0) == (0, 1664, 192, 208)


def test_default_desktop_pet_uses_codex_atlas_when_available() -> None:
    pet = load_pet_definition()

    assert pet.atlas_path is not None
    assert pet.has_atlas
    assert pet.sprite_path.exists()


def test_default_desktop_pet_pixmap_loaders_return_assets() -> None:
    _qapp()
    pet = load_pet_definition()

    assert not load_pet_atlas(pet).isNull()
    assert not load_pet_pixmap(pet).isNull()


def test_default_desktop_pet_frames_have_transparent_backgrounds() -> None:
    _qapp()
    pet = load_pet_definition()
    image = load_pet_atlas(pet).toImage()
    cell_width, cell_height = pet.cell_size

    for state in pet.states.values():
        for frame in range(state.frame_count):
            origin_x = frame * cell_width
            origin_y = state.row_index * cell_height
            transparent = sum(
                1
                for y in range(origin_y, origin_y + cell_height)
                for x in range(origin_x, origin_x + cell_width)
                if image.pixelColor(x, y).alpha() == 0
            )
            assert transparent > cell_width * cell_height * 0.25


def test_desktop_pet_loader_falls_back_for_unknown_env(monkeypatch) -> None:
    monkeypatch.setenv("NOVEL_FORGE_DESKTOP_PET_ID", "missing-pet")

    pet = load_pet_definition()

    assert pet.pet_id == "nimo"


def test_nimo_shortcut_toggle_persists_hide_and_summon(monkeypatch) -> None:
    class _Companion:
        def __init__(self) -> None:
            self.visibility_changes: list[bool] = []

        def set_user_visible(self, visible: bool) -> None:
            self.visibility_changes.append(visible)

    class _Window(TaskCompanionMixin):
        def __init__(self) -> None:
            self._floating_task_companion = _Companion()

    state = {"visible": True}
    persisted: list[dict[str, str]] = []
    monkeypatch.setattr(
        task_companion_module,
        "get_settings",
        lambda: SimpleNamespace(desktop_pet_visible=state["visible"]),
    )
    monkeypatch.setattr(task_companion_module, "get_writable_env_path", lambda: None)
    monkeypatch.setattr(task_companion_module, "reset_settings", lambda: None)

    def _merge_env(_path: object, pairs: dict[str, str]) -> None:
        persisted.append(pairs)
        state["visible"] = pairs["NOVEL_FORGE_DESKTOP_PET_VISIBLE"] == "true"

    monkeypatch.setattr(
        task_companion_module.DesktopSettingsStore,
        "merge_env",
        staticmethod(_merge_env),
    )
    window = _Window()

    window._toggle_task_companion_visibility()
    window._toggle_task_companion_visibility()

    assert window._floating_task_companion.visibility_changes == [False, True]
    assert persisted == [
        {"NOVEL_FORGE_DESKTOP_PET_VISIBLE": "false"},
        {"NOVEL_FORGE_DESKTOP_PET_VISIBLE": "true"},
    ]


def test_floating_task_companion_compact_mode_resizes_pet() -> None:
    _qapp()
    companion = FloatingTaskCompanion()
    try:
        normal_size = companion.size()
        pet = load_pet_definition()

        companion.set_compact_mode(True)
        compact_size = companion.size()

        assert normal_size.width() == pet.normal_size[0]
        assert normal_size.height() == pet.normal_size[1]
        assert compact_size.width() < normal_size.width()
        assert compact_size.height() < normal_size.height()
        assert companion._sprite.width() == pet.compact_sprite_size
        assert pet.compact_sprite_size >= 72
    finally:
        companion.shutdown()


def test_floating_task_companion_shows_live_usage_and_honors_visibility_preference() -> None:
    _qapp()
    store = TaskObservationStore()
    observed_id = store.begin_external_task(
        "voice-worker",
        kind="voice_studio",
        label="声腔 · 生成配音脚本",
        project_id="demo",
    )
    call_payload = {
        "status": "success",
        "call_id": "voice-call-1",
        "task": "TTS_GENERATE_DUBBING_SCRIPT",
        "prompt_tokens": 1200,
        "completion_tokens": 800,
        "total_tokens": 2000,
        "cost_usd": 0.02,
    }
    store.ingest_external_step(observed_id, "model_call_update", call_payload)
    store.ingest_external_step(observed_id, "model_call_update", call_payload)
    assert store.active_usage().total_tokens == 2000
    companion = FloatingTaskCompanion()
    try:
        companion.bind_store(store)

        assert companion.isVisible()
        assert companion._usage.isVisible()
        assert "1,200" in companion._usage.text()
        assert "800" in companion._usage.text()
        assert "声腔" in companion._title.text()

        companion.set_user_visible(False)
        assert not companion.isVisible()
        companion.set_user_visible(True)
        assert companion.isVisible()
    finally:
        companion.shutdown()


def test_floating_task_companion_is_not_a_themed_surface() -> None:
    _qapp()
    companion = FloatingTaskCompanion()
    try:
        assert companion.objectName() == "floatingTaskCompanion"
        assert companion.property("tone") is None
        assert companion.graphicsEffect() is None
    finally:
        companion.shutdown()


def test_floating_task_companion_surface_is_transparent() -> None:
    _qapp()
    companion = FloatingTaskCompanion()
    try:
        companion.show()
        QApplication.processEvents()
        assert companion.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert companion.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        assert not companion.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        assert not companion.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        assert not companion.autoFillBackground()
        image = companion.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        assert [
            image.pixelColor(x, y).alpha()
            for x, y in (
                (0, 0),
                (image.width() - 1, 0),
                (0, image.height() - 1),
                (image.width() - 1, image.height() - 1),
                (1, image.height() // 2),
                (image.width() - 2, image.height() // 2),
            )
        ] == [0, 0, 0, 0, 0, 0]
    finally:
        companion.shutdown()


def test_floating_task_companion_drag_threshold_separates_click_from_move() -> None:
    _qapp()
    companion = FloatingTaskCompanion()
    activated: list[bool] = []
    moved: list[tuple[int, int]] = []
    companion.activated.connect(lambda: activated.append(True))
    companion.position_changed.connect(lambda point: moved.append((point.x(), point.y())))
    try:
        companion.mousePressEvent(
            _mouse_event(
                QEvent.Type.MouseButtonPress,
                local_x=10,
                local_y=10,
                global_x=100,
                global_y=100,
                button=Qt.MouseButton.LeftButton,
                buttons=Qt.MouseButton.LeftButton,
            )
        )
        companion.mouseReleaseEvent(
            _mouse_event(
                QEvent.Type.MouseButtonRelease,
                local_x=10,
                local_y=10,
                global_x=100,
                global_y=100,
                button=Qt.MouseButton.LeftButton,
                buttons=Qt.MouseButton.NoButton,
            )
        )

        assert activated == [True]
        assert moved == []

        companion.mousePressEvent(
            _mouse_event(
                QEvent.Type.MouseButtonPress,
                local_x=10,
                local_y=10,
                global_x=100,
                global_y=100,
                button=Qt.MouseButton.LeftButton,
                buttons=Qt.MouseButton.LeftButton,
            )
        )
        companion.mouseMoveEvent(
            _mouse_event(
                QEvent.Type.MouseMove,
                local_x=40,
                local_y=40,
                global_x=140,
                global_y=140,
                button=Qt.MouseButton.NoButton,
                buttons=Qt.MouseButton.LeftButton,
            )
        )
        companion.mouseReleaseEvent(
            _mouse_event(
                QEvent.Type.MouseButtonRelease,
                local_x=40,
                local_y=40,
                global_x=140,
                global_y=140,
                button=Qt.MouseButton.LeftButton,
                buttons=Qt.MouseButton.NoButton,
            )
        )

        assert activated == [True]
        assert moved
    finally:
        companion.shutdown()


def test_floating_task_companion_double_click_requests_reset() -> None:
    _qapp()
    companion = FloatingTaskCompanion()
    resets: list[bool] = []
    companion.reset_position_requested.connect(lambda: resets.append(True))
    try:
        companion.mouseDoubleClickEvent(
            _mouse_event(
                QEvent.Type.MouseButtonDblClick,
                local_x=10,
                local_y=10,
                global_x=100,
                global_y=100,
                button=Qt.MouseButton.LeftButton,
                buttons=Qt.MouseButton.LeftButton,
            )
        )

        assert resets == [True]
    finally:
        companion.shutdown()


def test_floating_task_companion_idle_cycles_light_animations() -> None:
    _qapp()
    companion = FloatingTaskCompanion()
    try:
        companion.bind_store(TaskObservationStore())

        assert companion.isVisible()
        assert companion._meta.text() == "待命"
        assert companion._pet_state == "idle"

        for _ in range(24):
            companion._tick()

        assert companion._pet_state == "waving"
        assert companion._meta.text() == "待命"
    finally:
        companion.shutdown()


def test_floating_task_companion_status_does_not_paint_a_sprite_background() -> None:
    _qapp()
    companion = FloatingTaskCompanion()
    try:
        companion._set_pet_state("running")

        assert companion._sprite._glow_color.alpha() == 0
        assert companion._glow_anim is not None
        assert companion._glow_anim.state() != QAbstractAnimation.State.Running

        companion._set_pet_state("paused")

        assert companion._sprite._glow_color.alpha() == 0
        assert companion._glow_anim is not None
        assert companion._glow_anim.state() != QAbstractAnimation.State.Running
    finally:
        companion.shutdown()


def test_floating_task_companion_preserves_transparent_frame_pixels() -> None:
    _qapp()
    companion = FloatingTaskCompanion()
    try:
        companion.set_compact_mode(True)
        companion._set_pet_state("paused")
        companion.show()
        QApplication.processEvents()

        rendered = companion._sprite.grab().toImage().convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        frame = (
            companion._current_pet_frame()
            .scaled(
                companion._sprite.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            .toImage()
            .convertToFormat(QImage.Format.Format_RGBA8888)
        )
        offset_x = (rendered.width() - frame.width()) // 2
        offset_y = (rendered.height() - frame.height()) // 2

        for y in range(rendered.height()):
            for x in range(rendered.width()):
                source_x = x - offset_x
                source_y = y - offset_y
                source_alpha = (
                    frame.pixelColor(source_x, source_y).alpha()
                    if 0 <= source_x < frame.width() and 0 <= source_y < frame.height()
                    else 0
                )
                if source_alpha == 0:
                    assert rendered.pixelColor(x, y).alpha() == 0
    finally:
        companion.shutdown()


def test_floating_task_companion_non_looping_state_returns_to_idle() -> None:
    _qapp()
    companion = FloatingTaskCompanion()
    try:
        companion._has_active_attention = True
        companion._set_pet_state("waving")

        assert companion._non_loop_active
        for _ in range(companion._pet.state("waving").frame_count):
            companion._tick()

        assert companion._pet_state == "idle"
        assert not companion._non_loop_active
    finally:
        companion.shutdown()


def test_floating_task_companion_fallback_animation_uses_smooth_cycle() -> None:
    _qapp()
    companion = FloatingTaskCompanion()
    try:
        companion._atlas_pixmap = QPixmap()
        companion._set_pet_state("running")
        frame_count = companion._frame_count_for_current_state()

        deltas: list[int] = []
        for _ in range(frame_count):
            deltas.append(companion._fallback_animation_delta())
            companion._pulse = (companion._pulse + 1) % frame_count

        assert min(deltas) == 0
        assert max(deltas) >= 2
        assert len(set(deltas)) >= 3
    finally:
        companion.shutdown()


def test_floating_task_companion_shows_recent_task_progress_bubbles() -> None:
    _qapp()
    parent = QWidget()
    parent.resize(900, 640)
    parent.show()
    QApplication.processEvents()
    store = TaskObservationStore()
    now = datetime.now(timezone.utc).isoformat()
    store.ingest_jobs(
        [
            DesktopJobRecord(
                job_id="done",
                kind="run_short",
                label="分析格式错误成因",
                status=DesktopJobState.SUCCEEDED,
                updated_at=now,
                current_step="completed",
            ),
            DesktopJobRecord(
                job_id="running",
                kind="run_chapter",
                label="优化目前的宠物模块",
                status=DesktopJobState.RUNNING,
                updated_at=now,
                current_step="draft",
            ),
        ]
    )
    companion = FloatingTaskCompanion(parent)
    try:
        companion.move(720, 500)
        companion.bind_store(store)

        cards = [card for card in companion._bubble_panel._cards if not card.isHidden()]
        assert companion._bubble_panel.isVisible()
        assert len(cards) == 2
        assert cards[0]._title.text() == "分析格式错误成因"
        assert cards[0]._progress.value() == 100
        assert cards[0]._status_icon._status == "success"
        assert cards[1]._title.text() == "优化目前的宠物模块"
        assert cards[1]._status_icon._status == "running"
        assert companion._bubble_panel.geometry().right() <= parent.width() - 8
        assert companion._bubble_panel.geometry().bottom() < companion.geometry().top()
    finally:
        companion.shutdown()


def test_floating_task_companion_task_bubbles_can_collapse() -> None:
    _qapp()
    store = TaskObservationStore()
    store.begin_external_task(
        "bubble-toggle",
        kind="run_short",
        label="生成短篇",
    )
    companion = FloatingTaskCompanion()
    try:
        companion.bind_store(store)
        QApplication.processEvents()
        assert companion._bubble_panel.isVisible()
        assert companion._bubble_toggle.isVisible()
        assert companion._bubble_toggle.arrowType() == Qt.ArrowType.UpArrow
        assert not companion._bubble_toggle.geometry().intersects(companion._sprite.geometry())

        companion._bubble_toggle.click()

        assert not companion._bubble_panel.isVisible()
        assert companion._bubble_toggle.arrowType() == Qt.ArrowType.DownArrow
    finally:
        companion.shutdown()
