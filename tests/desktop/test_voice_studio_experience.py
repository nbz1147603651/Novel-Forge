"""UI regression tests for the production Voice Studio experience."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QScrollArea, QSplitter, QWidget
from pytestqt.qtbot import QtBot

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings
from novel_forge.desktop.components.containers import CollapsibleSection
from novel_forge.desktop.components.primitives import SectionHeading
from novel_forge.desktop.pages.voice_studio.page import (
    _HTML_BODY_FONT_FAMILY,
    RebuildConfirmDialog,
    ScriptSegmentEditorDialog,
    VoiceStudioPage,
    _ProviderDropdown,
    _TTSCleanupDialog,
)
from novel_forge.desktop.pages.voice_studio.sound_design_dialog import SoundDesignEditorDialog
from novel_forge.desktop.pages.voice_studio.workers import (
    BuildVoiceTeamWorker,
    FullTTSPipelineWorker,
)
from novel_forge.desktop.task_observation import TaskFocusScope, TaskObservationStore
from novel_forge.desktop.workers.base import BaseJobWorkerSignals
from novel_forge.gateway.profiles import ModelProfile, ProfilesConfig, TaskRouteEntry
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.assets.sound_library import load_sound_library, save_sound_library
from novel_forge.tts.model_center.manager import ApplicationAudioModelManager
from novel_forge.tts.model_center.runtimes import audio_runtime_catalog
from novel_forge.tts.model_center.schemas import AudioRuntimeState, RuntimeInstallState
from novel_forge.tts.runtime.cleanup import CAT_ORPHAN_CANDIDATES, CAT_STALE_PREVIEWS
from novel_forge.tts.runtime.performance_policy import (
    derive_voice_performance_profile,
    voice_performance_profile,
)
from novel_forge.tts.schemas import (
    BGMTiming,
    ChapterAudioResult,
    DubbingScript,
    DubbingSegment,
    NarratorVoiceProfile,
    SegmentType,
    SFXCue,
    SoundAsset,
    SoundLibraryManifest,
    SoundscapeCue,
    SynthesisResult,
    SynthesisStatus,
    TTSProvider,
    VoiceCastEntry,
    VoiceCloneStatus,
    VoiceTeamContract,
)
from novel_forge.tts.script_integrity import compute_source_text_hash
from novel_forge.tts.services.automation import AudioAutomationMode
from novel_forge.tts.services.studio_service import VoiceStudioProjectService


class _ObservedVoiceSignals(BaseJobWorkerSignals):
    step_progress = Signal(str, dict)


class _ObservedVoiceWorker:
    worker_id = "voice-observation-test"

    def __init__(self) -> None:
        self.signals = _ObservedVoiceSignals()


class _VoiceCatalogSignals(BaseJobWorkerSignals):
    voices_listed = Signal(list)
    provider_status = Signal(dict)


class _QueuedVoiceCatalogWorker:
    """No-thread catalog worker used to expose operation/catalog races."""

    instances: list[_QueuedVoiceCatalogWorker] = []

    def __init__(self, **kwargs) -> None:
        self.provider = kwargs.get("provider", "")
        self.worker_id = f"voice-catalog-{len(self.instances)}"
        self.signals = _VoiceCatalogSignals()
        self.submitted = False
        self.cancel_requests = 0
        self.__class__.instances.append(self)

    def submit(self) -> None:
        self.submitted = True

    def request_cancel(self) -> None:
        self.cancel_requests += 1


class _AudioModelCenterSignals(BaseJobWorkerSignals):
    models_listed = Signal(list)
    runtimes_listed = Signal(list)
    repository_status = Signal(dict)
    operation_progress = Signal(str, int)
    runtime_progress = Signal(str, str, int)
    operation_finished = Signal(bool, str, dict)


class _QueuedAudioModelWorker:
    """No-thread model worker used to exercise model-card event ordering."""

    instances: list[_QueuedAudioModelWorker] = []

    def __init__(self, **kwargs) -> None:
        self.operation = kwargs["operation"]
        self.plugin_id = kwargs.get("plugin_id", "")
        self.signals = _AudioModelCenterSignals()
        self.submitted = False
        self.cancel_requests = 0
        self.__class__.instances.append(self)

    def submit(self) -> None:
        self.submitted = True

    def request_cancel(self) -> None:
        self.cancel_requests += 1


def _page(qtbot: QtBot, *, provider: str = "local") -> VoiceStudioPage:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider=provider),
        defer_tabs=True,
    )
    qtbot.addWidget(page)
    page._deferred_tab_timer.stop()
    return page


def test_script_html_uses_the_platform_body_font_token(qtbot: QtBot) -> None:
    page = _page(qtbot, provider="mock")
    page._build_deferred_tab(1)
    page._current_script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="雨声在窗外渐密。",
            )
        ],
    )
    rendered: list[str] = []
    page._set_browser_html = lambda _browser, html, **_kwargs: rendered.append(html)  # type: ignore[method-assign]

    page._render_script_html()

    assert rendered
    assert f"font-family:{_HTML_BODY_FONT_FAMILY}" in rendered[0]
    assert "font-family:sans-serif" not in rendered[0].replace(" ", "").lower()


def test_model_download_starts_after_click_event_and_is_not_immediately_cancelled(
    qtbot: QtBot, tmp_path, monkeypatch
) -> None:
    """The download button must not be replaced by a live cancel button mid-click."""
    _QueuedAudioModelWorker.instances.clear()
    monkeypatch.setattr(VoiceStudioPage, "_AudioModelCenterWorker", _QueuedAudioModelWorker)
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path / "projects",
        audio_models_root=str(tmp_path / "models" / "audio"),
    )
    page = VoiceStudioPage(settings=settings, defer_tabs=False)
    qtbot.addWidget(page)
    status = ApplicationAudioModelManager(settings).model_status("silero-vad-onnx")
    page._on_audio_models_listed([status.model_dump(mode="json")])

    download = next(button for button in page.findChildren(QPushButton) if button.text() == "下载")
    qtbot.mouseClick(download, Qt.MouseButton.LeftButton)

    # ``singleShot(0)`` deliberately keeps the cancel button out of the
    # original mouse-release event.  The install worker appears on the next
    # event-loop turn and has not received a cancellation request.
    assert not [item for item in _QueuedAudioModelWorker.instances if item.operation == "install"]
    qtbot.waitUntil(
        lambda: any(item.operation == "install" for item in _QueuedAudioModelWorker.instances)
    )
    install = next(
        item for item in _QueuedAudioModelWorker.instances if item.operation == "install"
    )
    assert install.submitted is True
    assert install.cancel_requests == 0
    page._cancel_audio_model_download("silero-vad-onnx")
    assert install.cancel_requests == 0
    qtbot.wait(page._DOWNLOAD_CANCEL_GUARD_MS + 50)
    page._cancel_audio_model_download("silero-vad-onnx")
    assert install.cancel_requests == 1
    cancelling = next(
        button for button in page.findChildren(QPushButton) if button.text() == "正在取消…"
    )
    assert cancelling.isEnabled() is False
    page._cancel_audio_model_download("silero-vad-onnx")
    assert install.cancel_requests == 1
    page.shutdown()


def test_model_center_disables_repository_actions_while_busy(
    qtbot: QtBot, tmp_path, monkeypatch
) -> None:
    _QueuedAudioModelWorker.instances.clear()
    monkeypatch.setattr(VoiceStudioPage, "_AudioModelCenterWorker", _QueuedAudioModelWorker)
    page = VoiceStudioPage(
        settings=Settings(
            _env_file=None,
            storage_root=tmp_path / "projects",
            audio_models_root=str(tmp_path / "models" / "audio"),
        ),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page._on_audio_repository_status(
        {
            "root": str(tmp_path / "models" / "audio"),
            "size_bytes": 1024,
            "free_bytes": 4096,
            "rollback_available": True,
        }
    )

    assert page._audio_model_migrate_btn.isEnabled()
    assert page._audio_model_rollback_btn.isEnabled()
    assert "可回滚" in page._audio_model_repository_summary.text()

    page._audio_model_download_progress["silero-vad-onnx"] = ("下载中", 10)
    page._sync_audio_model_center_action_state()

    assert not page._audio_model_migrate_btn.isEnabled()
    assert not page._audio_model_rollback_btn.isEnabled()
    page.shutdown()


def test_runtime_card_shows_one_line_status_and_keeps_probe_logs_out_of_ui(
    qtbot: QtBot,
    tmp_path,
) -> None:
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path / "projects",
        audio_models_root=str(tmp_path / "models" / "audio"),
    )
    page = VoiceStudioPage(settings=settings, defer_tabs=True)
    qtbot.addWidget(page)
    page._deferred_tab_timer.stop()
    page._audio_runtime_operation_progress = {}
    descriptor = next(item for item in audio_runtime_catalog() if item.runtime_id == "whisperx")
    state = AudioRuntimeState(
        runtime_id="whisperx",
        state=RuntimeInstallState.FAILED,
        environment_path=str(tmp_path / "runtime"),
        last_error="\n".join(
            [
                "sidecar 连续崩溃，已停止自动重启。",
                'INFO: 127.0.0.1:50001 - "GET /v1/health HTTP/1.1" 200 OK',
            ]
        ),
    )

    card = page._build_audio_runtime_card(descriptor, state)
    labels = "\n".join(label.text() for label in card.findChildren(QLabel))

    assert "运行异常，当前不可执行" in labels
    assert "自动恢复多次失败" in labels
    assert "127.0.0.1" not in labels
    assert "Python >=" not in labels
    page.shutdown()


def test_voice_worker_is_bridged_to_shared_task_observation(qtbot: QtBot) -> None:
    page = _page(qtbot)
    page._project_id = "demo"
    store = TaskObservationStore()
    page.bind_task_observation_store(store)
    worker = _ObservedVoiceWorker()

    page._observe_voice_worker(worker, label="声腔 · 生成配音脚本", chapter_number=4)
    worker.signals.step_progress.emit(
        "llm_stream_start",
        {"stream_id": "voice-stream", "task": "TTS_GENERATE_DUBBING_SCRIPT"},
    )
    worker.signals.step_progress.emit(
        "llm_stream_delta",
        {
            "stream_id": "voice-stream",
            "segments": [{"kind": "content", "text": '{"segments": []}'}],
        },
    )

    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)
    assert focus is not None
    assert focus.job.kind == "voice_studio"
    assert focus.stream is not None
    assert focus.stream.text == '{"segments": []}'

    worker.signals.worker_finished.emit(worker.worker_id)
    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)
    assert focus is not None
    assert focus.status_label == "已完成"
    assert worker.worker_id not in page._observed_voice_task_ids


def test_auto_build_shows_incremental_cast_progress(qtbot: QtBot) -> None:
    page = _page(qtbot, provider="mock")

    assert page._voice_team_progress.isHidden()
    assert page._voice_team_task_card.isHidden()

    page._on_step_progress("build_voice_team_start", {"total": 11})
    assert not page._voice_team_progress.isHidden()
    assert not page._voice_team_task_card.isHidden()
    assert page._voice_team_progress.maximum() == 100
    assert page._voice_team_progress.value() == 20
    assert page._voice_team_progress.format() == "2/5 目录与匹配 · 准备 11 个角色"
    assert page._voice_team_task_state.text() == "进行中 · 阶段 2/5"
    assert page._voice_team_task_title.text() == "阶段 2/5 · 目录与匹配"

    page._on_step_progress(
        "build_voice_team_progress",
        {
            "completed": 4,
            "total": 11,
            "character_name": "林小满",
            "status": "assigned",
        },
    )
    assert page._voice_team_progress.value() == 47
    assert page._voice_team_progress.format() == "3/5 角色分配 · 4/11 · 林小满"
    assert "林小满" in page._status_badge.text()
    assert "4/11" in page._status_badge.text()
    assert "4/11 · 林小满" in page._voice_team_task_detail.text()

    page._current_worker = object()
    page._on_voice_team_updated(
        VoiceTeamContract(default_provider=TTSProvider.MOCK).model_dump(mode="json")
    )
    assert page._voice_team_progress.isHidden()


def test_auto_build_keeps_successful_outcome_visible_in_team_tab(qtbot: QtBot, tmp_path) -> None:
    page = _page(qtbot, provider="mock")
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    worker = BuildVoiceTeamWorker(
        project_id="demo",
        characters=[],
        settings=page._settings,
        layout=layout,
        provider="mock",
        mock=True,
    )
    page._current_worker = worker

    page._on_voice_team_worker_started(worker)
    page._on_voice_team_updated(VoiceTeamContract(default_provider=TTSProvider.MOCK).model_dump())

    assert page._voice_team_progress.isHidden()
    assert not page._voice_team_task_card.isHidden()
    assert page._voice_team_task_state.text() == "已完成"
    assert page._voice_team_task_title.text() == "配音团队已更新"
    assert "已更新 0 个角色" in page._voice_team_task_detail.text()
    assert page._voice_team_task_progress.value() == 1
    assert page._voice_team_task_progress.maximum() == 1


def test_auto_build_keeps_failure_detail_visible_in_team_tab(
    qtbot: QtBot,
    tmp_path,
    monkeypatch,
) -> None:
    page = _page(qtbot, provider="mock")
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    worker = BuildVoiceTeamWorker(
        project_id="demo",
        characters=[],
        settings=page._settings,
        layout=layout,
        provider="mock",
        mock=True,
    )
    page._current_worker = worker
    page._on_voice_team_worker_started(worker)
    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.show_warning_message",
        lambda *_args, **_kwargs: None,
    )

    page._on_worker_failed(
        worker.worker_id,
        {"summary": "平台连接失败", "detail": "认证被拒绝，请检查 API Key。"},
    )

    assert page._voice_team_progress.isHidden()
    assert not page._voice_team_task_card.isHidden()
    assert page._voice_team_task_state.text() == "构建失败"
    assert page._voice_team_task_title.text() == "配音团队构建未完成"
    assert "认证被拒绝" in page._voice_team_task_detail.text()


def test_auto_build_cancellation_keeps_recovery_hint_visible_in_team_tab(
    qtbot: QtBot,
    tmp_path,
) -> None:
    page = _page(qtbot, provider="mock")
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    worker = BuildVoiceTeamWorker(
        project_id="demo",
        characters=[],
        settings=page._settings,
        layout=layout,
        provider="mock",
        mock=True,
    )
    page._current_worker = worker
    page._on_voice_team_worker_started(worker)

    page._on_cancel_clicked()

    assert page._voice_team_progress.isHidden()
    assert not page._voice_team_task_card.isHidden()
    assert page._voice_team_task_state.text() == "已取消"
    assert "已保留" in page._voice_team_task_detail.text()


def test_auto_build_progress_does_not_reset_between_cast_and_previews(qtbot: QtBot) -> None:
    page = _page(qtbot, provider="minimax")

    page._on_step_progress(
        "build_voice_team_progress",
        {"completed": 11, "total": 11, "character_name": "林小满", "status": "assigned"},
    )
    cast_value = page._voice_team_progress.value()

    page._on_step_progress(
        "minimax_voice_activation_progress",
        {"completed": 2, "total": 4, "label": "林小满", "success": True},
    )
    activation_value = page._voice_team_progress.value()

    page._on_step_progress("voice_preview_batch_start", {"total": 11})
    preview_value = page._voice_team_progress.value()

    assert cast_value == 60
    assert activation_value == 70
    assert preview_value == 80
    assert page._voice_team_progress.format() == "5/5 试听准备 · 0/11"
    assert "阶段 5/5" in page._status_badge.text()


def test_auto_build_explains_low_confidence_llm_review(qtbot: QtBot) -> None:
    page = _page(qtbot, provider="minimax")

    page._on_step_progress(
        "tts_voice_llm_adjudication_start",
        {"candidate_count": 4, "voice_choice_count": 13},
    )

    assert page._voice_team_progress.value() == 60
    assert page._voice_team_progress.format() == "3/5 角色分配 · 4 个角色 · 13 个音色"
    assert "4 个角色比较 13 个硬约束合格音色" in page._status_badge.text()


def test_partial_rebuild_reports_only_selected_matching_work(qtbot: QtBot) -> None:
    page = _page(qtbot, provider="minimax")

    page._on_step_progress(
        "voice_semantic_retrieval_start",
        {"characters": 3, "total_characters": 12, "catalog_voices": 300},
    )

    assert "仅需重新匹配 3/12 个角色" in page._voice_team_progress.format()
    assert "仅需重新匹配 3/12 个角色" in page._status_badge.text()


def test_platform_settings_expose_voice_match_llm_review_and_route(qtbot: QtBot) -> None:
    page = _page(qtbot, provider="mock")
    page._build_deferred_tab(4)

    assert page._voice_llm_adjudication_check.isChecked() is True
    assert page._voice_llm_min_score_spin.value() == 0.72
    assert page._voice_llm_max_candidates_spin.value() == 4
    assert page._voice_llm_choices_spin.value() == 4
    assert page._voice_llm_auto_select_spin.value() == 0.85
    assert page._voice_llm_max_tokens_spin.value() == 1200
    assert page._voice_llm_min_score_spin.isEnabled() is True
    assert page._voice_llm_max_candidates_spin.isEnabled() is True
    assert page._voice_llm_choices_spin.isEnabled() is True
    assert page._voice_llm_auto_select_spin.isEnabled() is True
    assert page._voice_llm_max_tokens_spin.isEnabled() is True
    assert TaskType.TTS_ADJUDICATE_VOICE_MATCH.value in page._tts_route_rows
    assert page._script_llm_review_check.isChecked() is True
    assert page._script_llm_review_tokens_spin.value() == 8192
    assert TaskType.TTS_REVIEW_DUBBING_SCRIPT.value in page._tts_route_rows

    page._voice_llm_adjudication_check.setChecked(False)

    assert page._voice_llm_min_score_spin.isEnabled() is False
    assert page._voice_llm_max_candidates_spin.isEnabled() is False
    assert page._voice_llm_choices_spin.isEnabled() is False
    assert page._voice_llm_auto_select_spin.isEnabled() is False
    assert page._voice_llm_max_tokens_spin.isEnabled() is False


def test_partial_voice_team_rebuild_updates_only_selected_row_and_count(qtbot: QtBot) -> None:
    page = _page(qtbot, provider="mock")
    page._bible_characters = [
        {"character_id": "c1", "name": "林小满", "role": "protagonist"},
        {"character_id": "c2", "name": "陈半仙", "role": "supporting"},
    ]
    page._voice_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林小满",
                voice_id="mock-c1-old",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                voice_source="system",
            ),
            VoiceCastEntry(
                character_id="c2",
                character_name="陈半仙",
                voice_id="mock-c2",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                voice_source="system",
            ),
        ],
        default_provider=TTSProvider.MOCK,
    )
    page._update_character_list()
    page._character_list.setCurrentRow(1)
    selected_item = page._character_list.item(1)
    untouched_item = page._character_list.item(2)

    updated_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林小满",
                voice_id="mock-c1-new",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                voice_source="designed",
            ),
            page._voice_team.entries[1],
        ],
        default_provider=TTSProvider.MOCK,
    )
    page._current_worker = object()

    page._on_voice_team_updated(
        updated_team.model_dump(mode="json"),
        rebuild_character_ids=["c1"],
    )

    assert page._status_badge.text() == "配音团队已更新 · 1 个角色 · 0 个试听可播放"
    assert page._character_list.item(1) is selected_item
    assert page._character_list.item(2) is untouched_item
    assert page._character_list.currentItem() is selected_item
    assert "AI设计" in selected_item.text()


def test_cast_row_surfaces_match_reason_and_ready_audition(qtbot: QtBot, tmp_path) -> None:
    page = _page(qtbot, provider="mock")
    preview = tmp_path / "c1-preview.mp3"
    preview.write_bytes(b"audition")
    page._bible_characters = [
        {
            "character_id": "c1",
            "name": "沈岸",
            "role": "protagonist",
            "gender": "男",
            "age": "28",
            "personality": "沉稳克制",
        }
    ]
    page._voice_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="沈岸",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                voice_source="library",
                match_score=0.9,
                match_reasons=["性别硬约束一致", "角色特质吻合：沉稳克制"],
                preview_audio_path=str(preview),
                preview_text="事情还没有结束。",
            )
        ],
        default_provider=TTSProvider.MOCK,
    )
    page._update_character_list()
    page._character_list.setCurrentRow(1)

    row_text = page._character_list.item(1).text()
    assert "沈岸\n" in row_text
    assert "音色库" in row_text
    assert "画像 90%" in row_text
    assert "可试听" in row_text
    assert page._match_badge.text() == "画像匹配 90%"
    assert page._preview_btn.text() == "播放试听"
    assert "为什么匹配" in page._voice_info_text.toPlainText()


def test_auto_build_locks_new_voice_model_work_but_keeps_current_worker(
    qtbot: QtBot,
    tmp_path,
) -> None:
    page = _page(qtbot, provider="mock")
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    page._layout = layout
    entry = VoiceCastEntry(
        character_id="c1",
        character_name="林远",
        voice_id="mock-c1",
        provider=TTSProvider.MOCK,
        clone_status=VoiceCloneStatus.READY,
    )
    page._voice_team = VoiceTeamContract(
        entries=[entry],
        default_provider=TTSProvider.MOCK,
    )
    page._bible_characters = [{"character_id": "c1", "name": "林远"}]
    page._update_character_list()
    page._character_list.setCurrentRow(1)
    assert page._preview_btn.isEnabled()

    build_worker = BuildVoiceTeamWorker(
        project_id="demo",
        characters=page._bible_characters,
        settings=page._settings,
        layout=layout,
        provider="mock",
        mock=True,
    )
    page._current_worker = build_worker
    page._refresh_selected_voice_action_state()

    assert not page._preview_btn.isEnabled()
    assert not page._design_voice_btn.isEnabled()
    assert not page._clone_voice_btn.isEnabled()
    assert not page._voice_combo.isEnabled()
    assert not page._speed_slider.isEnabled()

    page._on_preview_voice()

    assert page._current_worker is build_worker
    assert "自动组建正在使用本地音频模型" in page._status_badge.text()


def test_voice_catalog_load_keeps_auto_build_cancellable(
    qtbot: QtBot,
    tmp_path,
    monkeypatch,
) -> None:
    _QueuedVoiceCatalogWorker.instances.clear()
    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.ListVoicesWorker",
        _QueuedVoiceCatalogWorker,
    )
    page = _page(qtbot, provider="mock")
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    build_worker = BuildVoiceTeamWorker(
        project_id="demo",
        characters=[{"character_id": "c1", "name": "林远"}],
        settings=page._settings,
        layout=layout,
        provider="mock",
        mock=True,
    )
    page._current_worker = build_worker
    page._cancel_btn.setEnabled(True)
    page._status_badge.setText("正在构建配音团队...")

    page._load_system_voices()

    catalog_worker = _QueuedVoiceCatalogWorker.instances[-1]
    assert catalog_worker.submitted is True
    assert page._current_worker is build_worker
    assert page._voice_catalog_worker is catalog_worker
    assert page._cancel_btn.isEnabled()

    catalog_worker.signals.voices_listed.emit(
        [{"voice_id": "mock-voice", "name": "测试音色", "tags": []}]
    )

    assert page._voice_catalog_worker is None
    assert page._current_worker is build_worker
    assert page._cancel_btn.isEnabled()
    assert page._status_badge.text() == "正在构建配音团队..."

    page._on_cancel_clicked()

    assert build_worker._cancel_event.is_set()
    assert page._current_worker is None


def test_voice_catalog_failure_does_not_swallow_auto_build_completion(
    qtbot: QtBot,
    tmp_path,
    monkeypatch,
) -> None:
    _QueuedVoiceCatalogWorker.instances.clear()
    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.ListVoicesWorker",
        _QueuedVoiceCatalogWorker,
    )
    page = _page(qtbot, provider="mock")
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    build_worker = BuildVoiceTeamWorker(
        project_id="demo",
        characters=[{"character_id": "c1", "name": "林远"}],
        settings=page._settings,
        layout=layout,
        provider="mock",
        mock=True,
    )
    page._current_worker = build_worker
    page._cancel_btn.setEnabled(True)

    page._load_system_voices()
    catalog_worker = _QueuedVoiceCatalogWorker.instances[-1]
    catalog_worker.signals.worker_failed.emit(
        catalog_worker.worker_id,
        {"summary": "catalog unavailable"},
    )

    assert page._voice_catalog_worker is None
    assert page._current_worker is build_worker
    assert page._cancel_btn.isEnabled()

    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        default_provider=TTSProvider.MOCK,
    )
    page._on_voice_team_updated(team.model_dump(mode="json"), worker=build_worker)

    assert page._current_worker is None
    assert page._voice_team is not None
    assert len(page._voice_team.get_ready_entries()) == 1
    assert not page._cancel_btn.isEnabled()
    assert "配音团队已更新" in page._status_badge.text()


def test_top_context_owns_metrics_provider_switch_and_project_selector(qtbot: QtBot) -> None:
    page = _page(qtbot)

    top_context = page.get_top_bar_widget()
    assert top_context is not None
    assert top_context.objectName() == "voiceStudioTopContext"
    header_metrics = page.findChild(QWidget, "voiceStudioHeaderMetrics")
    assert header_metrics is not None
    assert page._metric_total.parentWidget() is header_metrics
    assert page._metric_ready.parentWidget() is header_metrics
    assert page._metric_expired.parentWidget() is header_metrics
    assert isinstance(page._provider_dropdown, _ProviderDropdown)
    assert page._provider_dropdown.parentWidget() is top_context
    assert page._project_selector is not None
    assert page._project_selector.parentWidget() is top_context
    assert page.findChild(QWidget, "voiceStudioHeader") is None


def test_voice_studio_removes_generic_novel_task_flow_from_audio_workspace(
    qtbot: QtBot,
    tmp_path,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    page.set_project("demo", layout)
    page._load_chapter_artifacts(1)
    assert not hasattr(page, "_task_focus_panel")
    assert page._tabs.tabText(2) == "3 配音室"
    assert page._tabs.tabText(3) == "4 后处理"


def test_script_version_banner_blocks_stale_outputs_and_accepts_current_source(
    qtbot: QtBot,
    tmp_path,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    chapter_text = "苏晚打开档案。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    stale = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="苏晚打开了旧档案。",
            )
        ],
        source_text_hash=compute_source_text_hash("旧版正文"),
    )
    script_path = layout.tts_dubbing_script_path(1)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(stale.model_dump_json(), encoding="utf-8")
    layout.tts_audio_result_path(1).parent.mkdir(parents=True, exist_ok=True)
    layout.tts_audio_result_path(1).write_text(
        ChapterAudioResult(chapter_number=1, script=stale).model_dump_json(),
        encoding="utf-8",
    )

    page._layout = layout
    page._load_chapter_artifacts(1)

    assert page._script_freshness_badge.text() == "旧版脚本"
    assert page._replace_script_btn.isVisibleTo(page._script_freshness_bar)
    assert not page._synthesize_from_script_btn.isEnabled()
    assert not page._room_generate_btn.isEnabled()
    assert not page._reassemble_btn.isEnabled()

    current = stale.model_copy(
        update={
            "segments": [
                stale.segments[0].model_copy(update={"text": chapter_text})
            ],
            "source_text_hash": compute_source_text_hash(chapter_text),
        }
    )
    script_path.write_text(current.model_dump_json(), encoding="utf-8")
    page._load_chapter_artifacts(1)

    assert page._script_freshness_badge.text() == "当前版本"
    assert not page._replace_script_btn.isVisibleTo(page._script_freshness_bar)
    assert page._synthesize_from_script_btn.isEnabled()
    assert not page._reassemble_btn.isEnabled(), "old segment audio cannot mix with the new script"


def test_one_click_tts_allows_a_narrator_only_final_chapter(
    qtbot: QtBot,
    tmp_path,
    monkeypatch,
) -> None:
    """The UI must dispatch a valid final chapter even before a cast exists."""

    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    layout.chapter_path(1).write_text("只有旁白的终稿。", encoding="utf-8")
    page.set_project("demo", layout)
    page._audio_chapter_combo.clear()
    page._audio_chapter_combo.addItem("第 1 章", 1)

    dispatched: list[dict] = []

    class _Signal:
        def connect(self, _callback) -> None:
            return

    class _Worker:
        worker_id = "narrator-only-full-pipeline"

        def __init__(self, **kwargs) -> None:
            dispatched.append(kwargs)
            self.signals = type(
                "Signals",
                (),
                {
                    "step_progress": _Signal(),
                    "script_updated": _Signal(),
                    "segment_progress": _Signal(),
                    "audio_completed": _Signal(),
                    "worker_failed": _Signal(),
                },
            )()

        def submit(self) -> None:
            return

    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.FullTTSPipelineWorker",
        _Worker,
    )

    page._on_full_pipeline()

    assert dispatched == [
        {
            "project_id": "demo",
            "chapter_number": 1,
            "chapter_text": "只有旁白的终稿。",
            "characters": [],
            "settings": page._settings,
            "layout": layout,
            "provider": "mock",
            "automation_mode": AudioAutomationMode.ASSISTED,
            "mock": True,
        }
    ]


def test_tts_llm_routes_save_without_touching_other_task_routes(
    qtbot: QtBot,
    tmp_path,
    monkeypatch,
) -> None:
    profiles = ProfilesConfig(
        profiles=[
            ModelProfile(
                profile_id="primary",
                display_name="主模型",
                provider="openai",
                model_id="gpt-4o-mini",
                api_key="sk-primary",
            ),
            ModelProfile(
                profile_id="fallback",
                display_name="备用模型",
                provider="openai",
                model_id="gpt-4o",
                api_key="sk-fallback",
            ),
        ],
        routes={"draft_chapter": TaskRouteEntry(profile_id="primary")},
    )
    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.load_or_import_profiles",
        lambda _settings: profiles,
    )
    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.get_profiles_path",
        lambda: tmp_path / "model_profiles.json",
    )
    page = _page(qtbot)
    settings_tab = page._create_settings_tab()
    qtbot.addWidget(settings_tab)

    for _task_key, row in page._tts_route_rows.items():
        row.model_combo.setCurrentIndex(row.model_combo.findData("primary"))
    page._save_tts_llm_routes()

    assert profiles.routes["draft_chapter"].profile_id == "primary"
    assert profiles.routes["tts_generate_dubbing_script"].profile_id == "primary"
    assert profiles.routes["tts_adjudicate_script_segments"].profile_id == "primary"
    assert profiles.routes["tts_review_dubbing_script"].profile_id == "primary"
    assert profiles.routes["tts_adjudicate_voice_match"].profile_id == "primary"
    assert profiles.routes["tts_build_narrator_profile"].profile_id == "primary"
    # "fallback" is routeable (has API key) and is not the primary, so it becomes
    # an active fallback route automatically in the _TaskRouteRow popup.
    assert [
        entry.profile_id for entry in profiles.fallback_routes["tts_generate_dubbing_script"]
    ] == ["fallback"]


def test_tts_settings_separate_platform_controls_from_shared_sound_asset_controls(
    qtbot: QtBot,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="local"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)

    assert "当前平台" in page._settings_context_hint.text()
    assert page._model_combo.currentText() == page._settings.tts_local_model
    assert page._sound_generation_widgets["stable_audio_sfx_model"].text() == "small-sfx"
    assert hasattr(page, "_audio_model_center_badge")
    assert hasattr(page, "_audio_model_cards_layout")

    page._apply_complete_mix_preset()
    assert page._sound_generation_widgets["enabled"].currentText() == "true"
    assert page._sound_generation_widgets["auto_generate"].currentText() == "true"
    assert page._sound_generation_widgets["auto_approve"].currentText() == "true"
    assert page._sound_generation_widgets["default_provider"].currentData() == "auto"
    assert "完整成片推荐" in page._status_badge.text()

    page._sound_generation_widgets["enabled"].setCurrentText("true")
    updates = page._sound_generation_env_updates()

    assert updates["NOVEL_FORGE_SOUND_GENERATION_ENABLED"] == "true"
    assert updates["NOVEL_FORGE_SOUND_GENERATION_STABLE_AUDIO_SFX_MODEL"] == "small-sfx"
    assert (
        updates["NOVEL_FORGE_SOUND_GENERATION_STABLE_AUDIO_MODELS_DIR"]
        == page._settings.sound_generation_stable_audio_models_dir
    )
    page.shutdown()


def test_tts_settings_expose_independent_pipeline_and_script_batch_limits(
    qtbot: QtBot,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(
            _env_file=None,
            tts_default_provider="mock",
            tts_background_pipeline_concurrency=2,
            tts_script_max_concurrent_batches=3,
            tts_max_concurrent_synthesis=5,
        ),
        defer_tabs=False,
    )
    qtbot.addWidget(page)

    assert page._background_pipeline_spin.value() == 2
    assert page._background_pipeline_spin.maximum() == 3
    assert page._script_batch_concurrency_spin.value() == 3
    assert page._concurrent_spin.value() == 5

    page.shutdown()


def test_provider_dropdown_is_keyboard_accessible_and_closes_after_selection(qtbot: QtBot) -> None:
    dropdown = _ProviderDropdown()
    dropdown.add_item("mock", "模拟平台", "mock-neutral-1")
    dropdown.add_item("local", "本地引擎", "cosyvoice")
    dropdown.set_selected("mock")
    qtbot.addWidget(dropdown)
    dropdown.show()
    dropdown.setFocus()

    qtbot.keyClick(dropdown, Qt.Key.Key_Space)
    assert dropdown._popup is not None
    assert dropdown._popup.isVisible()
    assert dropdown._popup._rows[0]._checked

    qtbot.mouseClick(dropdown._popup._rows[0], Qt.MouseButton.LeftButton)
    assert dropdown._popup is None

    with qtbot.waitSignal(dropdown.item_selected) as signal:
        qtbot.keyClick(dropdown, Qt.Key.Key_Return)
        assert dropdown._popup is not None
        qtbot.mouseClick(dropdown._popup._rows[1], Qt.MouseButton.LeftButton)

    assert signal.args == ["local"]
    assert dropdown._selected_value == "local"
    assert dropdown._popup is None


def test_apply_action_shares_detail_heading_row(qtbot: QtBot) -> None:
    page = _page(qtbot)

    detail_layout = page._apply_offset_btn.parentWidget().layout()
    assert detail_layout is not None
    assert detail_layout.indexOf(page._apply_offset_btn) >= 0
    assert any(
        isinstance(detail_layout.itemAt(index).widget(), SectionHeading)
        for index in range(detail_layout.count())
    )


def test_character_parameters_are_explicit_and_preview_persists_them(
    qtbot: QtBot,
    monkeypatch,
) -> None:
    page = _page(qtbot, provider="mock")
    page._layout = object()
    page._voice_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        default_provider=TTSProvider.MOCK,
    )
    page._system_voices = [{"voice_id": "mock-c1", "name": "模拟音色"}]
    page._update_character_list()
    page._character_list.setCurrentRow(1)
    page._speed_slider.setValue(15)
    page._pitch_slider.setValue(-3)
    page._vol_slider.setValue(-20)

    assert page._speed_label.text() == "1.15×"
    assert page._pitch_label.text() == "-3 st"
    assert page._vol_label.text() == "0.80×"
    assert page._apply_offset_btn.text() == "应用参数 *"

    persisted: list[VoiceTeamContract] = []
    monkeypatch.setattr(page, "_persist_voice_team", lambda: persisted.append(page._voice_team))

    class _Signals:
        class _Signal:
            def connect(self, _slot) -> None:
                return None

        step_progress = _Signal()
        worker_failed = _Signal()
        audio_completed = _Signal()

    class _Worker:
        signals = _Signals()

        def __init__(self, **_kwargs) -> None:
            return None

        def submit(self) -> None:
            return None

    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.PreviewVoiceWorker",
        _Worker,
    )

    page._on_preview_voice()

    assert persisted
    entry = page._voice_team.get_entry("c1")
    assert entry is not None
    assert entry.speed_offset == 0.15
    assert entry.pitch_offset == -3
    assert entry.vol_offset == -0.2
    assert page._apply_offset_btn.text() == "应用参数"


def test_character_parameter_override_is_per_field_and_reset_restores_auto(
    qtbot: QtBot,
    monkeypatch,
) -> None:
    page = _page(qtbot, provider="mock")
    page._layout = object()
    profile = derive_voice_performance_profile(
        {"tts_voice_hints": {"preferred_speed": "fast", "preferred_pitch": "low"}}
    )
    page._voice_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                speed_offset=0.05,
                pitch_offset=-1,
                performance_profile=profile,
            )
        ],
        default_provider=TTSProvider.MOCK,
    )
    monkeypatch.setattr(page, "_persist_voice_team", lambda: None)
    page._update_character_list()
    page._character_list.setCurrentRow(1)

    page._vol_slider.setValue(20)
    assert "人工覆盖 音量" in page._performance_policy_label.text()
    assert page._persist_current_offsets()
    entry = page._voice_team.get_entry("c1")
    assert entry is not None
    assert voice_performance_profile(entry).manual_overrides.active_fields == {"volume"}
    assert entry.speed_offset == 0.05
    assert entry.pitch_offset == -1

    page._on_reset_offsets()
    assert page._persist_current_offsets()
    reset_entry = page._voice_team.get_entry("c1")
    assert reset_entry is not None
    assert voice_performance_profile(reset_entry).manual_overrides.active_fields == set()


def test_narrator_speed_pitch_and_volume_are_persisted(qtbot: QtBot, tmp_path) -> None:
    page = _page(qtbot, provider="mock")
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    page._layout = layout
    page._narrator_profile = NarratorVoiceProfile(
        voice_id="mock-narrator",
        provider=TTSProvider.MOCK,
        voice_source="manual",
    )
    page._voice_team = VoiceTeamContract(
        narrator_voice_id="mock-narrator",
        narrator_provider=TTSProvider.MOCK,
        default_provider=TTSProvider.MOCK,
    )
    page._update_character_list()
    page._character_list.setCurrentRow(0)
    page._speed_slider.setValue(-10)
    page._pitch_slider.setValue(2)
    page._vol_slider.setValue(-15)

    assert page._persist_current_offsets()

    saved = NarratorVoiceProfile.model_validate_json(
        layout.tts_narrator_profile_path.read_text(encoding="utf-8")
    )
    assert saved.base_speed == 0.9
    assert saved.pitch_offset == 2
    assert saved.vol_offset == -0.15
    assert "语速 0.90×" in page._status_badge.text()


def test_character_details_are_escaped_searchable_and_auditable(qtbot: QtBot) -> None:
    page = _page(qtbot)
    page._bible_characters = [
        {
            "character_id": "c1",
            "name": "林远",
            "role": "protagonist",
            "age": "青年",
            "personality": "沉稳克制",
        },
        {
            "character_id": "c2",
            "name": "顾衡",
            "role": "反派",
            "age": "中年",
            "personality": "冷酷",
        },
    ]
    page._voice_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="<b>林远</b>",
                voice_id="local-c1",
                provider=TTSProvider.LOCAL,
                clone_status=VoiceCloneStatus.READY,
                voice_source="designed",
                voice_design_prompt="<script>bad()</script> 沉稳克制",
            ),
            VoiceCastEntry(
                character_id="c2",
                character_name="顾衡",
                voice_id="local-c2",
                provider=TTSProvider.LOCAL,
                clone_status=VoiceCloneStatus.READY,
                voice_source="system",
            ),
        ],
        default_provider=TTSProvider.LOCAL,
    )
    page._system_voices = [{"voice_id": "local-c1", "name": "角色音色"}]

    page._update_character_list()
    page._character_list.setCurrentRow(1)

    plain_text = page._voice_info_text.toPlainText()
    rendered_html = page._voice_info_text.toHtml()
    assert "<b>林远</b>" in plain_text
    assert "<script>bad()</script>" in plain_text
    assert "<script>bad()</script>" not in rendered_html
    assert "角色特征生成" in plain_text
    assert "沉稳克制" in plain_text

    page._character_search.setText("反派")
    assert page._character_list.item(0).isHidden()
    assert page._character_list.item(1).isHidden()
    assert not page._character_list.item(2).isHidden()


def test_capability_negotiation_controls_voice_actions(qtbot: QtBot) -> None:
    page = _page(qtbot)
    entry = VoiceCastEntry(
        character_id="c1",
        character_name="林远",
        voice_id="local-c1",
        provider=TTSProvider.LOCAL,
        clone_status=VoiceCloneStatus.READY,
    )
    page._voice_team = VoiceTeamContract(
        entries=[entry],
        default_provider=TTSProvider.LOCAL,
    )
    page._system_voices = [{"voice_id": "local-c1", "name": "角色音色"}]
    page._update_character_list()
    page._character_list.setCurrentRow(1)

    page._on_provider_status(
        {
            "provider": "local",
            "capabilities": {
                "synthesis": True,
                "voice_clone": False,
                "voice_design": False,
                "system_voice_catalog": False,
                "local_reference_audio": False,
            },
        }
    )
    assert not page._clone_voice_btn.isEnabled()
    assert not page._design_voice_btn.isEnabled()
    assert page._preview_btn.isEnabled()

    page._on_provider_status(
        {
            "provider": "local",
            "capabilities": {
                "synthesis": True,
                "voice_clone": True,
                "voice_design": True,
                "system_voice_catalog": True,
                "local_reference_audio": True,
            },
        }
    )
    assert page._clone_voice_btn.isEnabled()
    assert page._design_voice_btn.isEnabled()
    assert "特征设计" in page._capability_hint.text()


def test_provider_switch_drives_relevant_settings(qtbot: QtBot, monkeypatch) -> None:
    page = _page(qtbot)
    page._build_deferred_tab(4)
    monkeypatch.setattr(page, "_load_system_voices", lambda: None)

    assert not page._local_url_row.isHidden()
    assert page._minimax_key_row.isHidden()
    assert page._minimax_group_row.isHidden()
    assert page._dashscope_model_row.isHidden()
    assert page._local_model_row.isHidden()
    assert page._model_combo.findText("Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign") >= 0

    page._switch_provider("minimax")
    assert not page._minimax_key_row.isHidden()
    assert not page._minimax_group_row.isHidden()
    assert page._local_url_row.isHidden()
    assert page._provider_dropdown._selected_value == "minimax"
    assert "MiniMax" in page._settings_context_hint.text()
    assert page._dashscope_model_row.isHidden()
    assert page._model_combo.currentText() == "speech-2.8-hd"
    assert page._model_combo.findText("Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign") == -1

    page._switch_provider("bailian")
    assert not page._dashscope_key_row.isHidden()
    assert not page._dashscope_preview_row.isHidden()
    assert not page._dashscope_clone_row.isHidden()
    assert not page._dashscope_design_row.isHidden()
    assert not page._dashscope_url_row.isHidden()
    assert page._model_combo.findText("qwen-audio-3.0-tts-plus") >= 0
    assert page._model_combo.findText("qwen3-tts-instruct-flash") >= 0

    page._switch_provider("cosyvoice")
    assert not page._cosyvoice_url_row.isHidden()
    assert not page._cosyvoice_mode_row.isHidden()
    assert page._local_url_row.isHidden()
    assert page._openvoice_checkpoint_row.isHidden()
    assert page._model_combo.currentText() == "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
    assert page._model_combo.findText("speech-2.8-hd") == -1

    page._switch_provider("openvoice")
    assert not page._openvoice_checkpoint_row.isHidden()
    assert not page._openvoice_device_row.isHidden()
    assert page._cosyvoice_url_row.isHidden()
    assert "OpenVoice V2" in page._provider_dropdown._label.text()
    assert page._provider_dropdown._selected_value == "openvoice"
    assert "OpenVoice V2" in page._settings_context_hint.text()

    page._switch_provider("volcengine_ark")
    assert not page._volcengine_key_row.isHidden()
    assert not page._volcengine_url_row.isHidden()
    assert page._mimo_key_row.isHidden()
    assert page._model_combo.currentText() == "doubao-seed-tts-2.0"
    assert "火山方舟" in page._provider_dropdown._label.text()

    page._switch_provider("mimo")
    assert not page._mimo_key_row.isHidden()
    assert not page._mimo_url_row.isHidden()
    assert page._volcengine_key_row.isHidden()
    assert page._model_combo.currentText() == "mimo-v2.5-tts"
    assert "Xiaomi MiMo" in page._settings_context_hint.text()


def test_provider_switch_filters_stale_cross_platform_model_and_updates_runtime(
    qtbot: QtBot,
    monkeypatch,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(
            _env_file=None,
            tts_default_provider="local",
            tts_default_model="FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        ),
        defer_tabs=True,
    )
    qtbot.addWidget(page)
    page._deferred_tab_timer.stop()
    page._build_deferred_tab(4)
    monkeypatch.setattr(page, "_load_system_voices", lambda: None)

    page._switch_provider("minimax")

    assert page._model_combo.currentText() == "speech-2.8-hd"
    assert page._model_combo.findText("FunAudioLLM/Fun-CosyVoice3-0.5B-2512") == -1
    assert page._settings.tts_default_model == "speech-2.8-hd"

    page._switch_provider("tencent")

    assert page._model_combo.currentText() == "0"
    assert page._model_combo.findText("speech-2.8-hd") == -1
    assert page._settings.tts_tencent_voice_type == "0"
    page.shutdown()


def test_voice_design_editor_preserves_upstream_role_context(qtbot: QtBot, monkeypatch) -> None:
    page = _page(qtbot, provider="mock")
    page._layout = object()
    page._bible_characters = [
        {
            "character_id": "c1",
            "name": "沈岸",
            "role": "主角",
            "personality": "沉稳克制",
        }
    ]
    page._voice_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="沈岸",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                voice_design_prompt="保留低沉、克制与短句表达。",
            )
        ],
        default_provider=TTSProvider.MOCK,
    )
    page._system_voices = [{"voice_id": "mock-c1", "name": "模拟音色"}]
    page._update_character_list()
    page._character_list.setCurrentRow(1)
    captured: dict[str, object] = {}

    def _capture_dialog(parent, title, label, **kwargs):
        captured.update({"parent": parent, "title": title, "label": label, **kwargs})
        return "", False

    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.show_multiline_input_dialog",
        _capture_dialog,
    )

    page._on_design_voice()

    assert captured["parent"] is page
    assert captured["heading"] == "确认角色音色简报"
    assert captured["initial_text"] == "保留低沉、克制与短句表达。"
    assert "沈岸" in str(captured["context_text"])
    assert "模拟平台" in str(captured["context_text"])
    assert captured["confirm_text"] == "生成并试听"


def test_narrator_appears_as_a_first_class_constructed_cast_member(qtbot: QtBot) -> None:
    page = _page(qtbot, provider="mock")
    page._narrator_profile = NarratorVoiceProfile(
        voice_id="mock-neutral-1",
        provider=TTSProvider.MOCK,
        voice_type="克制的中性旁白",
        base_speed=0.92,
        emotional_range="restrained",
        narration_distance="medium",
        style_keywords=["冷峻", "清晰", "留白"],
        notes="依据悬疑大纲与压抑氛围设计。",
        voice_source="designed",
        voice_design_prompt="为悬疑小说构建克制、清晰的作品级旁白。",
    )
    page._voice_team = VoiceTeamContract(default_provider=TTSProvider.MOCK)
    page._system_voices = [{"voice_id": "mock-neutral-1", "name": "模拟中性旁白"}]

    page._update_character_list()
    page._character_list.setCurrentRow(0)

    assert "旁白" in page._character_list.item(0).text()
    assert "作品级旁白" in page._voice_info_text.toPlainText()
    assert "作品特征设计" in page._voice_info_text.toPlainText()
    assert "克制的中性旁白" in page._voice_info_text.toPlainText()
    assert page._design_voice_btn.text() == "重新构建旁白音色"


def test_rebuild_dialog_filters_by_structured_voice_source(qtbot: QtBot) -> None:
    dialog = RebuildConfirmDialog(
        characters=[
            {"character_id": "c1", "name": "林远", "role": "protagonist"},
            {"character_id": "c2", "name": "系统匹配使者", "role": "supporting"},
            {"character_id": "c3", "name": "顾衡", "role": "antagonist"},
        ],
        voice_team_entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-male-1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                voice_source="system",
            ),
            VoiceCastEntry(
                character_id="c2",
                character_name="系统匹配使者",
                voice_id="mock-male-2",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                voice_source="designed",
            ),
            VoiceCastEntry(
                character_id="c3",
                character_name="顾衡",
                voice_id="mock-male-3",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                voice_source="library",
            ),
        ],
    )
    qtbot.addWidget(dialog)

    assert dialog.objectName() == "appDialog"
    assert dialog._list.objectName() == "voiceRebuildList"
    assert not dialog._confirm_btn.isEnabled()
    dialog._select_by_source("system")

    assert dialog.get_selected_ids() == ["c1"]
    assert dialog._confirm_btn.isEnabled()
    assert dialog._confirm_btn.text() == "重建 1 位角色"


def test_audio_result_surfaces_unresolved_environmental_cues(qtbot: QtBot) -> None:
    page = _page(qtbot, provider="mock")
    page._build_deferred_tab(3)

    # Simulate an active worker so the cancel-guard does not short-circuit
    page._current_worker = object()
    page._on_audio_completed(
        {
            "chapter_number": 1,
            "script": {"chapter_number": 1, "segments": []},
            "total_duration_ms": 20_000,
            "is_complete": True,
            "delivery_ready": False,
            "delivery_blocking_reasons": ["unresolved_sound_cues"],
            "metadata": {"sound_resolution": {"matched": 1, "unresolved": 2}},
        }
    )

    assert page._sound_resolution_badge.text() == "场景声音: 已匹配 1 · 待补充 2"
    assert "人声已合成，成片待完善" in page._status_badge.text()


def _script(chapter_number: int = 1) -> DubbingScript:
    return DubbingScript(
        chapter_number=chapter_number,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="雨声在窗外渐密。",
            )
        ],
    )


def test_clearing_source_script_hides_embedded_snapshot_but_keeps_audio(
    qtbot: QtBot,
    tmp_path,
    monkeypatch,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    script = _script()
    script_path = layout.tts_dubbing_script_path(1)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script.model_dump_json(), encoding="utf-8")
    result_path = layout.tts_audio_result_path(1)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        ChapterAudioResult(chapter_number=1, script=script).model_dump_json(),
        encoding="utf-8",
    )
    page._layout = layout
    page._load_chapter_artifacts(1)
    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.ask_confirmation",
        lambda *_args, **_kwargs: True,
    )

    page._on_clear_script_chapter()

    assert not script_path.exists()
    assert result_path.exists()
    assert page._source_script_available is False
    assert page._current_script is None
    assert page._playback_script is not None
    assert "仅保留已合成音频" in page._script_source_hint.text()
    assert "雨声在窗外渐密" not in page._script_browser.toPlainText()
    assert not page._synthesize_from_script_btn.isEnabled()
    assert page._clear_audio_chapter_btn.isEnabled()


def test_script_json_stream_renders_complete_segments_incrementally(qtbot: QtBot) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    task = "tts_generate_dubbing_script"
    # Simulate chapter 1 being generated while the user views it, mirroring
    # what _on_generate_script sets up before the first stream event arrives.
    page._generating_chapter = 1
    page._active_chapter_number = 1
    page._current_worker = object()  # non-None so stream events are not dropped
    page._on_step_progress(
        "llm_stream_start",
        {"task": task, "chapter": 1, "stream_id": "stream-1"},
    )
    page._on_step_progress(
        "llm_stream_delta",
        {
            "task": task,
            "chapter": 1,
            "stream_id": "stream-1",
            "segments": [
                {
                    "kind": "content",
                    "text": (
                        '{"segments":[{"segment_type":"narration",'
                        '"text":"雨声在窗外渐密。","emotion":"anxious",'
                        '"tone_hint":"压低声音","speed_override":0.9},'
                    ),
                }
            ],
        },
    )

    assert "雨声在窗外渐密" in page._script_browser.toPlainText()
    assert "压低声音" in page._script_browser.toPlainText()
    assert "已完成 1 段结构" in page._script_browser.toPlainText()
    assert "真实流式生成" in page._script_source_hint.text()
    assert page._script_stream_follow is not None


def test_script_stream_rerender_keeps_manual_reader_at_absolute_offset(qtbot: QtBot) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page.resize(1080, 720)
    page.show()
    page._tabs.setCurrentIndex(1)
    original = "".join(f"<p>旧输出 {index}</p>" for index in range(500))
    extended = "".join(f"<p>新输出 {index}</p>" for index in range(1500))
    page._set_browser_html(page._script_browser, original, preserve_scroll=False)
    qtbot.waitUntil(lambda: page._script_browser.verticalScrollBar().maximum() > 0)
    bar = page._script_browser.verticalScrollBar()
    bar.setValue(bar.maximum() // 3)
    qtbot.waitUntil(lambda: page._script_stream_follow is not None)
    previous_value = bar.value()

    page._set_browser_html(page._script_browser, extended, preserve_scroll=True)

    assert page._script_stream_follow is not None
    assert page._script_stream_follow.following_latest is False
    assert bar.value() == min(previous_value, bar.maximum())


def test_tts_clear_actions_are_locked_while_a_chapter_workflow_runs(
    qtbot: QtBot,
    tmp_path,
    monkeypatch,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    script_path = layout.tts_dubbing_script_path(1)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(_script().model_dump_json(), encoding="utf-8")
    page._layout = layout
    page._load_chapter_artifacts(1)
    page._tts_operation = ("synthesis", 1)
    page._refresh_workflow_controls()
    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.show_warning_message",
        lambda *_args, **_kwargs: None,
    )

    page._on_clear_script_chapter()

    assert script_path.exists()
    assert not page._clear_script_chapter_btn.isEnabled()
    assert not page._clear_audio_chapter_btn.isEnabled()


def test_old_asset_cleanup_dialog_previews_and_preselects_auditions(
    qtbot: QtBot,
    tmp_path,
) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    candidate = layout.tts_take_dir(1) / "candidates" / "take-old" / "segment.mp3"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(b"old-take")
    stale_preview = layout.tts_audio_dir(0) / "preview_old.mp3"
    stale_preview.parent.mkdir(parents=True, exist_ok=True)
    stale_preview.write_bytes(b"old-preview")

    dialog = _TTSCleanupDialog(
        layout,
        default_categories={CAT_ORPHAN_CANDIDATES},
    )
    qtbot.addWidget(dialog)

    assert dialog.selected_categories() == {CAT_ORPHAN_CANDIDATES}
    assert dialog._checkboxes[CAT_ORPHAN_CANDIDATES].isEnabled()
    assert dialog._checkboxes[CAT_STALE_PREVIEWS].isEnabled()
    assert "1 项旧资产" in dialog._selection_summary.text()
    assert candidate.exists()
    assert stale_preview.exists()


async def test_full_pipeline_publishes_script_before_audio_completion(
    qtbot: QtBot,
    tmp_path,
    monkeypatch,
) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_name="林小满",
                text="我已经准备好了。",
            )
        ],
    )
    script_path = layout.tts_dubbing_script_path(1)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script.model_dump_json(), encoding="utf-8")

    async def _fake_full_pipeline(**kwargs):
        kwargs["on_step_progress"](
            "tts_script_persisted",
            {"chapter": 1, "segment_count": 1, "script_path": str(script_path)},
        )
        return SimpleNamespace(result={"is_complete": True})

    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.workers.execute_full_tts_pipeline",
        _fake_full_pipeline,
    )
    worker = FullTTSPipelineWorker(
        project_id="demo",
        chapter_number=1,
        chapter_text="我已经准备好了。",
        characters=[],
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        layout=layout,
        provider="mock",
    )
    qtbot.addWidget(QWidget())
    published: list[dict] = []
    worker.signals.script_updated.connect(published.append)

    await worker._run_async()

    assert published
    assert published[0]["segments"][0]["character_name"] == "林小满"


def test_audio_workspace_uses_resizable_script_and_subtitle_pane(qtbot: QtBot) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)

    splitter = page.findChild(QSplitter, "voiceStudioAudioSplitter")

    assert splitter is not None
    assert splitter.count() == 2
    assert splitter.childrenCollapsible() is False
    assert page._audio_right_tabs.count() == 3
    assert page._audio_right_tabs.tabText(0) == "实时脚本"
    assert page._audio_right_tabs.tabText(1) == "字幕"
    assert page._audio_right_tabs.tabText(2) == "混音清单"


def test_mix_manifest_explains_anchors_ducking_and_asset_readiness(qtbot: QtBot) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page._current_script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="雨声落下。",
            )
        ],
        soundscapes=[
            SoundscapeCue(
                name="雨夜街道",
                start_segment_index=0,
                volume=0.18,
                ducking_db=6,
            )
        ],
        bgm_suggestions=[
            BGMTiming(
                track_name="低频铺底",
                start_segment_index=0,
                volume=0.2,
                ducking_db=10,
            )
        ],
        sfx_cues=[
            SFXCue(
                effect_name="玻璃轻响",
                trigger_segment_index=0,
                offset_ms=300,
            )
        ],
    )

    page._render_mix_manifest()
    text = page._mix_manifest_browser.toPlainText()

    assert "环境 1 轨 · BGM 1 轨 · 音效 1 点" in text
    assert "对白下压 10dB" in text
    assert "第 1 段 +300ms" in text
    assert "待合成后匹配素材" in text


def test_post_production_dedicates_the_full_right_column_to_script_and_subtitles(
    qtbot: QtBot,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)

    right_panel = page._audio_right_tabs.parentWidget()
    assert right_panel is not None
    assert right_panel.layout() is not None
    assert right_panel.layout().count() == 1
    assert not any(
        button.text() == "管理 / 试听声音资源" for button in page.findChildren(QPushButton)
    )
    assert isinstance(page._export_mp3_btn, QAction)
    assert isinstance(page._clear_audio_chapter_btn, QAction)


def test_script_context_prioritizes_character_navigation_over_metrics(
    qtbot: QtBot,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)

    navigation_row = page.findChild(QWidget, "voiceScriptCharacterNavRow")
    metrics_row = page.findChild(QWidget, "voiceScriptMetricsRow")

    assert navigation_row is not None
    assert metrics_row is not None
    assert page._avatar_scroll.parentWidget() is navigation_row
    assert page._seg_metric_total.parentWidget() is metrics_row
    assert page._seg_metric_narration.parentWidget() is metrics_row
    assert page._seg_metric_dialogue.parentWidget() is metrics_row
    assert page._seg_metric_sfx.parentWidget() is metrics_row
    assert page._avatar_scroll.height() >= 64


def test_script_character_navigation_preserves_chip_viewport_when_it_overflows(
    qtbot: QtBot,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page.resize(1080, 720)
    page.show()
    page._tabs.setCurrentIndex(1)
    page._voice_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id=f"character-{index}",
                character_name=f"角色{index}",
                voice_id="mock-neutral-1",
                provider=TTSProvider.MOCK,
            )
            for index in range(30)
        ]
    )
    page._update_avatars()
    qtbot.waitUntil(lambda: page._avatar_scroll.horizontalScrollBar().maximum() > 0)

    assert not page._avatar_scroll.verticalScrollBar().isVisible()
    assert (
        page._avatar_scroll.viewport().height() >= page._avatar_container.minimumSizeHint().height()
    )


def test_settings_use_a_vertical_fire_style_category_flow(qtbot: QtBot, monkeypatch) -> None:
    monkeypatch.setattr(
        CollapsibleSection,
        "_load_expanded_from_settings",
        classmethod(lambda _cls, _key: None),
    )
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)

    settings_scroll = page.findChild(QScrollArea, "voiceStudioSettingsScroll")
    settings_content = page.findChild(QWidget, "voiceStudioSettingsContent")
    assert settings_scroll is not None
    assert settings_content is not None
    assert page.findChild(QSplitter, "voiceSettingsPrimarySplitter") is None

    category_titles = [
        category.findChild(QLabel, "sectionTitle").text()
        for category in settings_content.findChildren(QWidget, "voiceSettingsCategory")
        if category.findChild(QLabel, "sectionTitle") is not None
    ]
    assert category_titles == [
        "本机模型中心",
        "质量与路由",
        "当前平台",
        "项目配音策略",
        "声音设计",
        "输出与运行",
        "文本智能",
    ]

    section_toggles = {
        toggle.text(): toggle
        for toggle in settings_content.findChildren(QPushButton, "collapseToggle")
    }
    assert "连接与模型" in page._platform_settings_section._toggle.text()
    assert page._platform_settings_section._toggle.isChecked()
    assert not section_toggles["通用合成策略"].isChecked()
    assert page._platform_settings_section.parentWidget() is settings_content


def test_settings_tab_restores_its_last_scroll_offset(qtbot: QtBot, monkeypatch) -> None:
    monkeypatch.setattr(
        CollapsibleSection,
        "_load_expanded_from_settings",
        classmethod(lambda _cls, _key: None),
    )
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page.resize(1080, 720)
    page.show()
    page._custom_tab_bar.setCurrentIndex(4)
    qtbot.waitUntil(lambda: page._settings_scroll.verticalScrollBar().maximum() > 0)
    first_bar = page._settings_scroll.verticalScrollBar()
    first_bar.setValue(min(240, first_bar.maximum()))
    state = page.export_ui_state()

    restored = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(restored)
    restored.restore_ui_state(state)
    restored.resize(1080, 720)
    restored.show()
    qtbot.waitUntil(lambda: restored._custom_tab_bar.currentIndex() == 4)
    qtbot.waitUntil(lambda: restored._settings_scroll.verticalScrollBar().maximum() > 0)
    expected = min(
        int(state["settings_scroll_value"]),
        restored._settings_scroll.verticalScrollBar().maximum(),
    )
    qtbot.waitUntil(lambda: restored._settings_scroll.verticalScrollBar().value() == expected)


def test_audio_automation_mode_is_project_scoped_and_restart_safe(
    qtbot: QtBot,
    tmp_path,
) -> None:
    settings = Settings(
        _env_file=None,
        tts_default_provider="mock",
        tts_automation_mode="autonomous",
    )
    page = VoiceStudioPage(settings=settings, defer_tabs=False)
    qtbot.addWidget(page)
    first = ProjectLayout(tmp_path / "first")
    second = ProjectLayout(tmp_path / "second")
    first.ensure_dirs()
    second.ensure_dirs()

    page.set_project("first", first)
    assert page._audio_automation_selector.current_mode() == AudioAutomationMode.AUTONOMOUS
    page._audio_automation_selector.set_mode(AudioAutomationMode.MANUAL)
    assert page._audio_automation_mode == "manual"
    assert page._full_pipeline_btn.text() == "按现状成片"

    page.set_project("second", second)
    assert page._audio_automation_selector.current_mode() == AudioAutomationMode.AUTONOMOUS
    page.set_project("first", first)
    assert page._audio_automation_selector.current_mode() == AudioAutomationMode.MANUAL

    state = page.export_ui_state()
    restored = VoiceStudioPage(settings=settings, defer_tabs=False)
    qtbot.addWidget(restored)
    restored.restore_ui_state(state)
    restored.set_project("first", first)
    assert restored._audio_automation_selector.current_mode() == AudioAutomationMode.MANUAL


def test_platform_mode_derives_legacy_sound_switches(qtbot: QtBot) -> None:
    page = VoiceStudioPage(
        settings=Settings(
            _env_file=None,
            tts_default_provider="mock",
            tts_automation_mode="assisted",
        ),
        defer_tabs=False,
    )
    qtbot.addWidget(page)

    assert page._sound_widget("enabled").currentText() == "true"
    assert page._sound_widget("auto_generate").currentText() == "true"
    assert page._sound_widget("auto_approve").currentText() == "false"
    assert not page._sound_widget("auto_approve").isEnabled()

    mode = page._sound_widget("automation_mode")
    mode.setCurrentIndex(mode.findData("autonomous"))
    assert page._sound_widget("auto_approve").currentText() == "true"
    updates = page._sound_generation_env_updates()
    assert updates["NOVEL_FORGE_TTS_AUTOMATION_MODE"] == "autonomous"
    assert updates["NOVEL_FORGE_SOUND_GENERATION_AUTO_APPROVE"] == "true"


def test_audio_progress_shows_phase_counts_without_runtime_logs(qtbot: QtBot) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)

    page._on_step_progress("tts_sound_generation_start", {"missing": 3})
    assert page._automation_progress_badge.text() == "AI 伴随 · 4/6 声场素材"
    assert page._status_badge.text() == "正在补齐 3 个缺失的 BGM / 环境声 / 音效候选…"

    page._on_step_progress(
        "tts_sound_generation_complete",
        {"generated": 2, "pending_review": 2, "failed": 1},
    )
    assert "已生成 2" in page._status_badge.text()
    assert "待试听 2" in page._status_badge.text()
    assert "127.0.0.1" not in page._status_badge.text()

    page._on_step_progress("tts_quality_complete", {"passed": False})
    assert page._automation_progress_badge.text() == "AI 伴随 · 6/6 交付质检"
    assert page._automation_progress_badge.property("tone") == "danger"
    assert page._status_badge.text() == "成片质检未通过，正在保留阻塞原因"


def test_audio_quality_plan_combines_platform_native_and_capability_plugins(
    qtbot: QtBot,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(
            _env_file=None,
            tts_default_provider="qwen3",
            audio_quality_preset="master",
            audio_memory_budget="high",
        ),
        defer_tabs=False,
    )
    qtbot.addWidget(page)

    report = page._audio_plan_report.toPlainText()
    assert "Qwen3-TTS 1.7B CustomVoice" in report
    assert "Qwen3 ForcedAligner 0.6B" in report
    assert "WhisperX" in report
    assert "多轨渲染" in report
    assert "原生能力" in page._platform_capability_summary.text()
    assert "全局本机调度" in report
    assert page._local_resource_budget_combo.currentData() == "medium"
    assert page._audio_benchmark_btn.text() == "用当前章节校准"

    page._sync_provider_settings_ui("minimax")

    assert "MiniMax" in page._platform_settings_section._toggle.text()
    assert "空间效果" in page._platform_capability_summary.text()


def test_post_production_separates_mastering_from_review_first_library(
    qtbot: QtBot,
    tmp_path,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    asset_path = layout.tts_sound_assets_dir / "theme.wav"
    asset_path.write_bytes(b"audio" * 100)
    save_sound_library(
        layout,
        SoundLibraryManifest(
            assets=[
                SoundAsset(
                    asset_id="theme",
                    kind="bgm",
                    display_name="作品主题候选",
                    relative_path="theme.wav",
                    tags=["作品主题", "悬疑"],
                    source="generated",
                    approval_status="pending",
                )
            ]
        ),
    )

    page.set_project("demo", layout)
    page._sound_library_panel.refresh()

    assert page._post_workspace_tabs.tabText(0) == "成品装配"
    assert page._post_workspace_tabs.tabText(1) == "声音资源库（项目 / 应用）"
    assert page._sound_library_panel._asset_list.count() == 1
    page._sound_library_panel._approve_btn.click()
    assert load_sound_library(layout).assets[0].approval_status == "approved"


def test_voice_room_uses_fixed_inspector_with_only_internal_text_scroll(qtbot: QtBot) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page._current_script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="雨声在窗外渐密。",
                scene_context="深夜书房",
            )
        ],
        bgm_suggestions=[
            BGMTiming(track_name="暗线低鸣", start_segment_index=0, end_segment_index=0)
        ],
        soundscapes=[SoundscapeCue(name="夜雨", start_segment_index=0, end_segment_index=0)],
        sfx_cues=[SFXCue(effect_name="木门轻响", trigger_segment_index=0)],
    )
    page._source_script_available = True
    page._populate_voice_room_segments()
    page.resize(1080, 720)
    page.show()
    page._tabs.setCurrentIndex(2)
    qtbot.wait(1)

    splitter = page.findChild(QSplitter, "voiceStudioRoomSplitter")
    assert splitter is not None
    assert splitter.count() == 2
    assert splitter.widget(1).objectName() == "voiceRoomEditorPanel"
    assert page.findChild(QScrollArea, "voiceRoomEditorScroll") is None
    assert page._room_segment_text.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert splitter.widget(1).height() <= splitter.contentsRect().height()
    context = page._room_sound_context.text()
    assert "深夜书房" in context
    assert "夜雨" in context
    assert "暗线低鸣" in context
    assert "木门轻响" in context
    assert "后处理" in context


def test_voice_studio_narrow_window_keeps_caption_actions_and_transport_separate(
    qtbot: QtBot,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page._current_script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="苏晚翻开档案。",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                character_id="lin",
                character_name="林小满",
                text="这是我的实习证明。",
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="屋里安静下来。",
            ),
        ],
    )
    page._source_script_available = True
    page._populate_voice_room_segments()
    page.resize(900, 650)
    page.show()
    page._tabs.setCurrentIndex(2)
    page._room_segment_list.setCurrentRow(1)
    qtbot.wait(20)

    caption = page.findChild(QFrame, "voiceRoomCaptionStage")
    actions = page.findChild(QWidget, "voiceRoomActionBar")
    transport = page.findChild(QFrame, "voiceRoomTransportDock")
    assert caption is not None
    assert actions is not None
    assert transport is not None
    assert not caption.geometry().intersects(actions.geometry())
    assert not actions.geometry().intersects(transport.geometry())
    assert page._room_caption_position.text() == "字幕 02 / 03"
    assert page._room_caption_speaker.text() == "林小满"
    rendered = page._room_segment_text.toPlainText()
    assert "苏晚翻开档案" in rendered
    assert "这是我的实习证明" in rendered
    assert "屋里安静下来" in rendered

    page._tabs.setCurrentIndex(3)
    qtbot.wait(20)
    command_bar = page.findChild(QWidget, "voicePostCommandBar")
    assert command_bar is not None
    assert not page._audio_automation_selector.geometry().intersects(
        page._synthesize_btn.geometry()
    )
    assert abs(
        page._audio_automation_selector.geometry().center().y()
        - page._synthesize_btn.geometry().center().y()
    ) <= 2
    assert page._cleanup_takes_btn in page._manage_menu_btn.menu().actions()
    assert not page._progress_badge.geometry().intersects(page._inspect_mix_btn.geometry())


def test_voice_room_directly_loads_the_formal_baseline_after_script_synthesis(
    qtbot: QtBot,
    tmp_path,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_name="林远",
                text="坐。",
            )
        ],
        script_hash="script-v1",
    )
    layout.tts_dubbing_script_path(1).write_text(script.model_dump_json(), encoding="utf-8")
    segment_audio = layout.tts_segment_audio_path(1, 0)
    segment_audio.parent.mkdir(parents=True, exist_ok=True)
    segment_audio.write_bytes(b"formal-audio")
    result = ChapterAudioResult(
        chapter_number=1,
        script=script,
        segment_results=[
            SynthesisResult(
                segment_index=0,
                status=SynthesisStatus.COMPLETED,
                audio_path=str(segment_audio),
            )
        ],
    )
    layout.tts_audio_result_path(1).write_text(result.model_dump_json(), encoding="utf-8")
    page._layout = layout
    page._studio_service = VoiceStudioProjectService(layout, project_id="demo")

    page._load_chapter_artifacts(1)

    assert page._room_loaded_audio_path == str(segment_audio)
    assert "可直接试听" in page._room_status_badge.text()
    assert not page._room_accept_btn.isEnabled()


def test_script_editor_applies_safe_suggestions_and_preserves_segment_controls(
    qtbot: QtBot,
) -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_name="林远",
                text="你会回来吗？",
            )
        ],
    )
    dialog = ScriptSegmentEditorDialog(script)
    qtbot.addWidget(dialog)
    dialog._text_input.setPlainText("别回头")
    dialog._tone_input.setText("压低声音")
    dialog._speed_combo.setCurrentIndex(dialog._speed_combo.findData(0.8))
    dialog._volume_combo.setCurrentIndex(dialog._volume_combo.findData(0.9))
    dialog._pitch_combo.setCurrentIndex(dialog._pitch_combo.findData(-2))
    dialog._apply_safe_suggestion()

    edited = dialog.edited_script().segments[0]

    assert edited.text == "别回头。"
    assert edited.tone_hint == "压低声音"
    assert edited.speed_override == 0.8
    assert edited.vol_override == 0.9
    assert edited.pitch_override == -2


def test_script_editor_resolves_a_source_audit_speaker_with_cast_id(qtbot: QtBot) -> None:
    page = _page(qtbot)
    page._voice_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(character_id="lin", character_name="林远", voice_id="voice-lin"),
            VoiceCastEntry(character_id="shen", character_name="沈岸", voice_id="voice-shen"),
        ]
    )
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="lin",
                character_name="林远",
                text="现在。",
            )
        ],
        metadata={
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [0],
            },
            "professional_script_review": {
                "llm_review": {
                    "status": "needs_review",
                    "coverage_complete": True,
                    "manual_review_segment_indices": [0],
                    "manual_review_items": [
                        {
                            "segment_index": 0,
                            "issue_types": ["speaker_ownership"],
                        }
                    ],
                }
            },
        },
    )
    dialog = ScriptSegmentEditorDialog(script, parent=page)
    qtbot.addWidget(dialog)

    dialog._speaker_combo.setCurrentIndex(dialog._speaker_combo.findData("shen"))
    edited = dialog.edited_script()

    assert edited.segments[0].character_id == "shen"
    assert edited.segments[0].character_name == "沈岸"
    assert edited.metadata["speaker_adjudication"]["status"] == "passed"
    assert edited.metadata["speaker_adjudication"]["unresolved_segment_indices"] == []
    llm_review = edited.metadata["professional_script_review"]["llm_review"]
    assert llm_review["status"] == "passed"
    assert llm_review["manual_review_segment_indices"] == []


def test_script_editor_can_confirm_an_existing_speaker_candidate(qtbot: QtBot) -> None:
    page = _page(qtbot)
    page._voice_team = VoiceTeamContract(
        entries=[VoiceCastEntry(character_id="lin", character_name="林远", voice_id="voice-lin")]
    )
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="lin",
                character_name="林远",
                text="现在。",
            )
        ],
        metadata={
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [0],
            }
        },
    )
    dialog = ScriptSegmentEditorDialog(script, parent=page)
    qtbot.addWidget(dialog)

    assert dialog._confirm_speaker_btn.isVisibleTo(dialog)
    assert dialog._segment_list.item(0).text().startswith("待复核")

    dialog._confirm_current_speaker_review()
    edited = dialog.edited_script()

    assert edited.metadata["speaker_adjudication"]["status"] == "passed"
    assert edited.metadata["speaker_adjudication"]["unresolved_segment_indices"] == []
    assert dialog._review_badge.text() == "复核完成 1/1"


def test_script_editor_confirming_narration_merges_contiguous_narration(qtbot: QtBot) -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="他放下杯子，在签名旁边补充了一行小字：",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="建议关注同源纹路的触发场景及递送渠道信息，可另行建档。",
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="写完后，他把审批页放回文件夹。",
            ),
            DubbingSegment(
                segment_index=3,
                segment_type=SegmentType.DIALOGUE,
                character_id="lin",
                character_name="林远",
                text="我明白了。",
            ),
        ],
        bgm_suggestions=[BGMTiming(start_segment_index=0, end_segment_index=2)],
        sfx_cues=[SFXCue(effect_name="笔尖划纸", trigger_segment_index=1)],
        soundscapes=[SoundscapeCue(name="办公室底噪", start_segment_index=1, end_segment_index=2)],
        metadata={
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [1],
            }
        },
    )
    dialog = ScriptSegmentEditorDialog(script, initial_segment_index=1)
    qtbot.addWidget(dialog)

    assert dialog._confirm_speaker_btn.text() == "确认本段为旁白"
    assert dialog._confirm_speaker_btn.isVisibleTo(dialog)

    dialog._confirm_current_speaker_review()
    edited = dialog.edited_script()

    assert [segment.segment_type for segment in edited.segments] == [
        SegmentType.NARRATION,
        SegmentType.DIALOGUE,
    ]
    assert edited.segments[0].text == (
        "他放下杯子，在签名旁边补充了一行小字："
        "建议关注同源纹路的触发场景及递送渠道信息，可另行建档。"
        "写完后，他把审批页放回文件夹。"
    )
    assert edited.bgm_suggestions[0].start_segment_index == 0
    assert edited.bgm_suggestions[0].end_segment_index == 0
    assert edited.sfx_cues[0].trigger_segment_index == 0
    assert edited.soundscapes[0].start_segment_index == 0
    assert edited.soundscapes[0].end_segment_index == 0
    assert edited.metadata["speaker_adjudication"]["status"] == "passed"
    assert edited.metadata["speaker_adjudication"]["unresolved_segment_indices"] == []


def test_gapped_segment_ids_keep_human_facing_review_numbers_contiguous(
    qtbot: QtBot,
) -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=10,
                segment_type=SegmentType.NARRATION,
                text="第一段。",
            ),
            DubbingSegment(
                segment_index=30,
                segment_type=SegmentType.DIALOGUE,
                text="第二段。",
            ),
        ],
        metadata={
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [30],
            }
        },
    )
    dialog = ScriptSegmentEditorDialog(script, initial_segment_index=30)
    qtbot.addWidget(dialog)

    assert dialog._segment_list.currentRow() == 1
    assert "待复核 · 02" in dialog._segment_list.item(1).text()
    assert dialog._segment_heading.text().startswith("第 2 段")

    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page._current_script = script
    page._render_script_html()

    assert page._speaker_review_btn.text() == "复核第 2 段"


def test_script_page_highlights_and_links_pending_speaker_review(qtbot: QtBot) -> None:
    page = _page(qtbot, provider="mock")
    page._build_deferred_tab(1)
    page._current_script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_name="林远",
                text="你听见了吗？",
            )
        ],
        metadata={
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [0],
            }
        },
    )
    rendered: list[str] = []
    page._set_browser_html = lambda _browser, html, **_kwargs: rendered.append(html)  # type: ignore[method-assign]

    page._render_script_html()

    assert "说话人待复核" in rendered[0]
    assert "去复核" in rendered[0]
    assert page._speaker_review_badge.text() == "待复核 1 段"
    assert page._speaker_review_btn.text() == "复核第 1 段"


def test_sound_design_editor_adds_segment_anchored_bgm(qtbot: QtBot) -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="雨落下来。",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="灯光熄灭。",
            ),
        ],
    )
    dialog = SoundDesignEditorDialog(script)
    qtbot.addWidget(dialog)

    dialog._add_cue("bgm")
    dialog._name_input.setText("低频悬疑铺底")
    dialog._start_combo.setCurrentIndex(dialog._start_combo.findData(1))
    dialog._end_combo.setCurrentIndex(dialog._end_combo.findData(1))
    dialog._volume_spin.setValue(20)
    dialog._duck_spin.setValue(10)
    edited = dialog.edited_script()

    assert len(edited.bgm_suggestions) == 1
    cue = edited.bgm_suggestions[0]
    assert cue.track_name == "低频悬疑铺底"
    assert cue.start_segment_index == 1
    assert cue.end_segment_index == 1
    assert cue.volume == 0.2
    assert cue.ducking_db == 10


def test_script_editor_reclassifies_and_deletes_segments_with_reference_remap(
    qtbot: QtBot,
    monkeypatch,
) -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="lin",
                character_name="林远",
                text="童年夏夜蝉鸣",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="这是一段需要删除的重复旁白。",
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="记忆被取走后，罐子就空了。",
            ),
        ],
        bgm_suggestions=[BGMTiming(start_segment_index=0, end_segment_index=2)],
        sfx_cues=[SFXCue(effect_name="玻璃轻响", trigger_segment_index=1)],
        soundscapes=[SoundscapeCue(name="室内底噪", start_segment_index=0, end_segment_index=2)],
        metadata={
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [0, 1],
            }
        },
    )
    dialog = ScriptSegmentEditorDialog(script)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.dialogs.ask_confirmation",
        lambda *_args, **_kwargs: True,
    )

    dialog._segment_type_combo.setCurrentIndex(
        dialog._segment_type_combo.findData(SegmentType.NARRATION.value)
    )
    dialog._segment_list.setCurrentRow(1)
    dialog._delete_current_segment()
    edited = dialog.edited_script()

    assert [segment.segment_index for segment in edited.segments] == [0, 1]
    assert [segment.segment_type for segment in edited.segments] == [
        SegmentType.NARRATION,
        SegmentType.NARRATION,
    ]
    assert edited.segments[0].character_id == ""
    assert edited.bgm_suggestions[0].end_segment_index == 1
    assert edited.sfx_cues[0].trigger_segment_index == 1
    assert edited.soundscapes[0].end_segment_index == 1
    assert edited.metadata["speaker_adjudication"]["status"] == "passed"
    assert edited.metadata["speaker_adjudication"]["unresolved_segment_indices"] == []
    assert dialog._segment_list.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert dialog.findChild(QScrollArea, "voiceScriptEditorScroll") is not None


def test_voice_room_editor_keeps_source_text_and_saves_performance_wording(
    qtbot: QtBot,
) -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                text="坐。",
            )
        ],
    )
    dialog = ScriptSegmentEditorDialog(script, performance_only=True)
    qtbot.addWidget(dialog)

    dialog._text_input.setPlainText("坐下说吧。")
    edited = dialog.edited_script().segments[0]

    assert edited.text == "坐。"
    assert edited.spoken_text == "坐下说吧。"
    assert dialog.has_changes
    assert "预计时长" in dialog._estimate_label.text()
    assert "合成指令" in dialog._synthesis_direction_label.text()


def test_script_editor_persists_neutral_vocal_direction_and_platform_extension(
    qtbot: QtBot,
) -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_name="林远",
                text="雨停了。",
            )
        ],
    )
    dialog = ScriptSegmentEditorDialog(script, provider="qwen3")
    qtbot.addWidget(dialog)
    dialog._language_combo.setCurrentIndex(dialog._language_combo.findData("ja"))
    dialog._delivery_style_combo.setCurrentIndex(dialog._delivery_style_combo.findData("dramatic"))
    dialog._energy_combo.setCurrentIndex(dialog._energy_combo.findData(0.8))
    dialog._articulation_combo.setCurrentIndex(dialog._articulation_combo.findData(0.85))
    dialog._platform_instruction_input.setText("尾句压低，保持危险的克制感")

    edited = dialog.edited_script().segments[0]

    assert edited.language_code == "ja"
    assert edited.language_runs[0].language == "ja"
    assert edited.vocal_direction.delivery_style == "dramatic"
    assert edited.vocal_direction.energy == 0.8
    assert edited.vocal_direction.articulation == 0.85
    assert edited.platform_extensions["qwen3"]["instruct"] == "尾句压低，保持危险的克制感"
    assert "原生" in dialog._platform_mapping_hint.text()


def test_script_edit_actions_open_the_editor_for_button_and_inline_links(
    qtbot: QtBot,
    tmp_path,
    monkeypatch,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    chapter_text = "雨声在窗外渐密。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    script = _script().model_copy(
        update={"source_text_hash": compute_source_text_hash(chapter_text)}
    )
    script_path = layout.tts_dubbing_script_path(1)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script.model_dump_json(), encoding="utf-8")
    page._layout = layout
    page._load_chapter_artifacts(1)

    opened: list[int | None] = []

    class _FakeEditor:
        def __init__(self, _script, *, initial_segment_index=None, parent=None) -> None:
            opened.append(initial_segment_index)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.ScriptSegmentEditorDialog",
        _FakeEditor,
    )

    assert page._edit_script_btn.isEnabled()
    page._edit_script_btn.click()
    page._on_script_anchor_clicked(QUrl("edit-segment:0"))
    page._on_script_anchor_clicked(QUrl("edit-segment://0"))

    assert opened == [None, 0, 0]


def test_live_script_rerender_preserves_reader_scroll_position(qtbot: QtBot) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page._tabs.setCurrentIndex(1)
    page._current_script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=index,
                segment_type=SegmentType.NARRATION,
                text=f"第 {index + 1} 段：" + "这是用于验证实时刷新不跳回顶部的旁白。" * 3,
            )
            for index in range(80)
        ],
    )
    page.show()
    page._render_script_html(preserve_scroll=False)
    qtbot.wait(20)
    scrollbar = page._script_browser.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum() // 2)
    before = scrollbar.value() / max(scrollbar.maximum(), 1)

    page._segment_status[42] = "synthesizing"
    page._render_script_html()

    after = scrollbar.value() / max(scrollbar.maximum(), 1)
    assert after > 0.2
    assert abs(after - before) < 0.08


def test_voice_room_keeps_local_model_storage_at_application_scope(qtbot: QtBot, tmp_path) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()

    page.set_project("demo", layout)

    expected = page._settings.sound_generation_stable_audio_models_dir
    assert page._stable_audio_models_dir_value() == expected
    assert page._sound_generation_widgets["stable_audio_models_dir"].text() == expected


def test_saving_edited_script_retires_stale_audio_derivatives(
    qtbot: QtBot,
    tmp_path,
    monkeypatch,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    chapter_text = "雨声在窗外渐密。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    script = _script().model_copy(
        update={"source_text_hash": compute_source_text_hash(chapter_text)}
    )
    script_path = layout.tts_dubbing_script_path(1)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script.model_dump_json(), encoding="utf-8")
    result_path = layout.tts_audio_result_path(1)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        ChapterAudioResult(chapter_number=1, script=script).model_dump_json(),
        encoding="utf-8",
    )
    audio_path = layout.tts_assembled_audio_path(1)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"old generated audio")
    page._layout = layout
    page._load_chapter_artifacts(1)
    edited_segment = script.segments[0].model_copy(update={"text": "雨声在窗外渐密，别回头。"})
    edited_script = script.model_copy(update={"segments": [edited_segment]})
    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.ask_confirmation",
        lambda *_args, **_kwargs: True,
    )

    page._persist_edited_script(edited_script)

    saved_script = DubbingScript.model_validate_json(script_path.read_text(encoding="utf-8"))
    assert saved_script.segments[0].text == "雨声在窗外渐密，别回头。"
    assert not result_path.exists()
    assert not audio_path.exists()
    assert page._current_audio_result is None
    assert page._source_script_available is True
    assert page._synthesize_btn.isEnabled()
    assert "待重建" in page._status_badge.text()


def test_failed_edited_script_write_preserves_previous_audio_derivatives(
    qtbot: QtBot,
    tmp_path,
    monkeypatch,
) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    chapter_text = "雨声在窗外渐密。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    script = _script().model_copy(
        update={"source_text_hash": compute_source_text_hash(chapter_text)}
    )
    script_path = layout.tts_dubbing_script_path(1)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    previous_bytes = script.model_dump_json().encode("utf-8")
    script_path.write_bytes(previous_bytes)
    audio_path = layout.tts_assembled_audio_path(1)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"playable-audio")
    page._layout = layout
    page._load_chapter_artifacts(1)
    edited_script = script.model_copy(
        update={
            "segments": [
                script.segments[0].model_copy(update={"text": "写入会失败的新版本。"})
            ]
        }
    )
    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.ask_confirmation",
        lambda *_args, **_kwargs: True,
    )

    def _fail_write(*_args, **_kwargs) -> None:
        raise OSError("模拟磁盘写入失败")

    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.atomic_write_json",
        _fail_write,
    )
    monkeypatch.setattr(
        "novel_forge.desktop.pages.voice_studio.page.show_warning_message",
        lambda *_args, **_kwargs: None,
    )

    page._persist_edited_script(edited_script)

    assert script_path.read_bytes() == previous_bytes
    assert audio_path.read_bytes() == b"playable-audio"
    assert "保存失败" in page._status_badge.text()


def test_audition_plays_via_native_player_and_stops_previous(qtbot: QtBot, tmp_path) -> None:
    """Auditions play through the native OS player, stopping any prior clip.

    Auditions are short single voice clips; they must not jump to the heavy
    DubbingPlayerWidget (whose Qt FFmpeg backend cannot probe MiniMax
    AIGC-watermarked MP3s).  A second audition must terminate the first so
    clips do not overlap, and ``shutdown`` must clean up the process.
    """
    page = _page(qtbot)

    spawned: list[list[str]] = []

    class _FakeProc:
        def __init__(self) -> None:
            self._terminated = False

        def poll(self):
            return None  # still running

        def terminate(self) -> None:
            self._terminated = True

    procs: list[_FakeProc] = []

    def fake_popen(command, **kwargs):  # noqa: ANN001
        spawned.append(list(command))
        proc = _FakeProc()
        procs.append(proc)
        return proc

    clip = tmp_path / "preview.mp3"
    clip.write_bytes(b"\x00" * 256)

    import novel_forge.desktop.pages.voice_studio.page as vs_page

    original_popen = vs_page.subprocess.Popen
    original_platform = vs_page.sys.platform
    vs_page.subprocess.Popen = fake_popen  # type: ignore[assignment]
    vs_page.sys.platform = "darwin"  # type: ignore[assignment]
    try:
        # First audition launches afplay.
        assert page._play_audition(str(clip)) is True
        assert spawned[-1][0] == "/usr/bin/afplay"
        assert page._audition_proc is procs[-1]

        # A second audition terminates the first and starts a new process.
        first_proc = procs[-1]
        assert page._play_audition(str(clip)) is True
        assert first_proc._terminated is True
        assert page._audition_proc is procs[-1]
        assert page._audition_proc is not first_proc

        # shutdown stops the in-flight audition.
        page.shutdown()
        assert procs[-1]._terminated is True
        assert page._audition_proc is None
    finally:
        vs_page.subprocess.Popen = original_popen  # type: ignore[assignment]
        vs_page.sys.platform = original_platform  # type: ignore[assignment]


def test_audition_returns_false_for_missing_file(qtbot: QtBot, tmp_path) -> None:
    """A missing audition clip must not launch a player."""
    page = _page(qtbot)
    assert page._play_audition(str(tmp_path / "absent.mp3")) is False


def test_fast_reassemble_resumes_from_accepted_segment_with_timeline(
    qtbot: QtBot, tmp_path
) -> None:
    """After a fast reassemble, playback resumes from the accepted segment.

    ``_on_segment_take_accepted`` records the accepted segment and triggers a
    fast reassemble; on completion ``_maybe_resume_after_reassemble`` must seek
    the chapter player to that segment and resume playback, then clear the
    pending flag so a later manual reassemble does not spuriously resume.
    """
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)

    # Stand in for the chapter player: record seek/play instead of using Qt media.
    seek_calls: list[int] = []
    play_calls: list[None] = []

    class _FakePlayer:
        def seek_to_segment(self, idx: int) -> None:
            seek_calls.append(idx)

        def play(self) -> None:
            play_calls.append(None)

        def shutdown(self) -> None:
            pass

    page._room_chapter_player = _FakePlayer()  # type: ignore[assignment]
    page._set_room_player_scope = lambda *args, **kwargs: None  # type: ignore[method-assign]

    # Build a minimal timeline whose only entry is segment 2.
    from novel_forge.tts.pipeline.timeline_builder import (
        PlaybackTimeline,
        TimelineEntry,
    )

    entry = TimelineEntry(
        segment_index=2,
        start_ms=5000,
        end_ms=9000,
        segment_type=SegmentType.NARRATION,
        character_id="",
        character_name="旁白",
        text="测试。",
    )
    timeline = PlaybackTimeline(entries=[entry], total_duration_ms=9000)

    # No pending resume -> no-op (must not call seek/play).
    page._maybe_resume_after_reassemble(timeline)
    assert seek_calls == []
    assert play_calls == []

    # Pending resume for a segment present in the timeline -> seek + play.
    page._resume_after_reassemble_segment = 2
    page._maybe_resume_after_reassemble(timeline)
    assert seek_calls == [2]
    assert play_calls == [None]
    assert page._resume_after_reassemble_segment is None
    assert page._voice_room_playback is True

    # Pending resume for a segment NOT in the timeline -> cleared, no seek/play.
    page._resume_after_reassemble_segment = 99
    page._maybe_resume_after_reassemble(timeline)
    assert page._resume_after_reassemble_segment is None
    assert seek_calls == [2]

    page.shutdown()


def test_room_segment_double_click_seeks_chapter_player(qtbot: QtBot, tmp_path) -> None:
    """Double-clicking a voice-room segment jumps the chapter player there.

    Single-click still selects for editing; double-click launches chapter
    playback from that segment via ``seek_to_segment`` + ``play``.
    """
    from PySide6.QtWidgets import QListWidgetItem

    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    # Ensure the voice room tab (and its segment list) is built.
    page._build_deferred_tab(2)
    assert hasattr(page, "_room_segment_list")

    seek_calls: list[int] = []
    play_calls: list[None] = []

    class _FakePlayer:
        def has_timeline(self) -> bool:
            return True

        def seek_to_segment(self, idx: int) -> None:
            seek_calls.append(idx)

        def play(self) -> None:
            play_calls.append(None)

        def shutdown(self) -> None:
            pass

    page._room_chapter_player = _FakePlayer()  # type: ignore[assignment]
    page._prepare_room_chapter_player = lambda: True  # type: ignore[method-assign]
    page._set_room_player_scope = lambda *args, **kwargs: None  # type: ignore[method-assign]

    item = QListWidgetItem("segment")
    item.setData(Qt.ItemDataRole.UserRole, 3)
    page._on_room_segment_double_clicked(item)
    assert seek_calls == [3]
    assert play_calls == [None]
    assert page._voice_room_playback is True

    page.shutdown()


def test_room_segment_double_click_warns_when_no_timeline(qtbot: QtBot) -> None:
    """Without a loaded chapter timeline, double-click warns instead of seeking."""
    from PySide6.QtWidgets import QListWidgetItem

    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page._build_deferred_tab(2)

    page._prepare_room_chapter_player = lambda: False  # type: ignore[method-assign]

    item = QListWidgetItem("segment")
    item.setData(Qt.ItemDataRole.UserRole, 0)
    page._on_room_segment_double_clicked(item)
    assert "未就绪" in page._room_status_badge.text()

    page.shutdown()
    assert page._audition_proc is None


def test_full_chapter_playback_updates_voice_room_inspector_and_subtitle_state(
    qtbot: QtBot,
) -> None:
    """Chapter playback must drive both the left queue and right inspector."""
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page._current_script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="前一段。",
            ),
            DubbingSegment(
                segment_index=7,
                segment_type=SegmentType.DIALOGUE,
                character_name="林小满",
                text="右侧详情应跟随到这一段。",
            ),
        ],
    )
    page._populate_voice_room_segments()
    page._voice_room_playback = True

    page._on_playback_segment_changed(7, "")

    assert page._selected_voice_room_segment_index() == 7
    assert "第 8 段" in page._room_segment_heading.text()
    assert "右侧详情应跟随到这一段" in page._room_segment_text.toPlainText()
    assert "第 8 段" in page._room_playback_state.text()
    assert "字幕跟随" in page._room_playback_state.text()

    page.shutdown()


def test_full_chapter_button_toggles_pause_and_resume_in_voice_room(qtbot: QtBot) -> None:
    """The visible full-chapter CTA remains a pause/resume control during playback."""
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)

    class _FakePlayer:
        def __init__(self) -> None:
            self.playing = True
            self.pause_calls = 0
            self.play_calls = 0

        def is_playing(self) -> bool:
            return self.playing

        def pause(self) -> None:
            self.playing = False
            self.pause_calls += 1

        def play(self) -> None:
            self.playing = True
            self.play_calls += 1

        def shutdown(self) -> None:
            pass

    player = _FakePlayer()
    page._room_chapter_player = player  # type: ignore[assignment]
    page._set_room_player_scope = lambda *args, **kwargs: None  # type: ignore[method-assign]
    page._voice_room_playback = True
    page._playback_active = True

    page._on_play_full_chapter()
    assert player.pause_calls == 1
    assert page._room_play_full_btn.text() == "▶ 继续全章"
    assert "已暂停" in page._room_playback_state.text()

    page._on_play_full_chapter()
    assert player.play_calls == 1
    assert page._room_play_full_btn.text() == "⏸ 暂停全章"

    page.shutdown()


def test_voice_room_switches_visible_progress_with_playback_scope(qtbot: QtBot) -> None:
    """The red-box transport shows the timeline that owns the active audio."""
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page._build_deferred_tab(2)

    assert page._segment_player is not None
    assert page._room_chapter_player is not None
    assert page._room_player_stack is not None

    page._segment_player._on_duration_changed(36_000)
    page._segment_player._on_position_changed(8_000)
    page._room_chapter_player._on_duration_changed(180_000)
    page._room_chapter_player._on_position_changed(45_000)

    page._set_room_player_scope("segment")
    assert page._room_player_stack.currentWidget() is page._segment_player
    assert page._segment_player._time_cur.text() == "0:08"
    assert page._segment_player._time_total.text() == "0:36"
    assert page._segment_player._pct_label.text() == "22%"
    assert "片段" in page._room_player_scope_label.text()

    page._active_chapter_number = 1
    page._set_room_player_scope("chapter", segment_index=1)
    assert page._room_player_stack.currentWidget() is page._room_chapter_player
    assert page._room_chapter_player._time_cur.text() == "0:45"
    assert page._room_chapter_player._time_total.text() == "3:00"
    assert page._room_chapter_player._pct_label.text() == "25%"
    assert page._room_player_scope_label.text() == "当前播放：整章 · 第 1 章 · 当前第 2 段"

    page.shutdown()


def test_single_segment_preview_builds_timeline_and_highlights_right_subtitle(
    qtbot: QtBot,
    tmp_path,
) -> None:
    """An isolated take uses its own timeline to drive the right subtitle stage."""
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    audio_path = tmp_path / "segment.mp3"
    audio_path.write_bytes(b"\x00" * 256)
    page._current_script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=4,
                segment_type=SegmentType.NARRATION,
                text="字幕跟随单节试听。",
            )
        ],
    )
    page._current_results = [
        SynthesisResult(
            segment_index=4,
            status=SynthesisStatus.COMPLETED,
            audio_path=str(audio_path),
            duration_ms=2_000,
        )
    ]

    page._populate_voice_room_segments()

    assert page._segment_player is not None
    assert page._segment_player.has_timeline()
    page._on_segment_preview_playback_state_changed(True)
    page._on_room_word_highlight(4, 0, 4)
    page._flush_room_word_highlight()

    assert "正在试听" in page._room_playback_state.text()
    assert "字幕跟随" in page._room_playback_state.text()
    assert "字幕跟随单节试听" in page._room_segment_text.toPlainText()
    assert "font-weight:800" in page._room_segment_text.toHtml().replace(" ", "")

    page.shutdown()


def test_voice_room_uses_compact_karaoke_focus_for_long_subtitles(qtbot: QtBot) -> None:
    """Playback fills the stage with adjacent cues while keeping a compact current phrase."""
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    text = (
        "第一句有一段很长的远处上下文用来验证不会整段展示。"
        "当前朗读短语需要居中聚焦。"
        "下一句只作为弱化预览。"
    )
    segment = DubbingSegment(
        segment_index=2,
        segment_type=SegmentType.NARRATION,
        text=text,
    )
    page._current_script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=index,
                segment_type=SegmentType.NARRATION,
                text=label,
            )
            for index, label in enumerate(
                ["上上段字幕。", "上一段字幕。", text, "下一段字幕。", "下下段字幕。"]
            )
        ],
    )
    start = text.index("朗读短语")

    page._set_voice_room_segment_text(segment, highlight=(start, start + 4))

    rendered = page._room_segment_text.toPlainText()
    assert "当前朗读短语" in rendered
    assert "第一句有一段很长的远处" not in rendered
    assert "上一段字幕" in rendered
    assert "下一段字幕" in rendered
    page.shutdown()


def test_voice_room_explains_effective_short_dialogue_stability(qtbot: QtBot) -> None:
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="minimax"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page._voice_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林小满",
                voice_id="voice-c1",
                provider=TTSProvider.MINIMAX,
                clone_status=VoiceCloneStatus.READY,
                speed_offset=0.2,
                pitch_offset=4,
            )
        ]
    )
    segment = DubbingSegment(
        segment_index=0,
        segment_type=SegmentType.DIALOGUE,
        character_id="c1",
        character_name="林小满",
        text="沈先生？",
    )

    speed, _volume, pitch = page._voice_room_effective_parameter_labels(segment)

    assert speed == "1.00× · 短句稳定"
    assert pitch == "+0 st · 短句稳定"
    page.shutdown()


def test_resume_after_reassemble_seeks_and_plays_from_accepted_segment(
    qtbot: QtBot,
) -> None:
    """Accept → fast-reassemble → resume: must seek to the accepted segment and play.

    ``_on_segment_take_accepted`` records the segment index and launches a fast
    reassemble; ``_maybe_resume_after_reassemble`` (called at the end of
    ``_on_audio_completed``) is responsible for seeking the rebuilt chapter
    player to that segment and resuming playback.
    """
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page._build_deferred_tab(2)

    class _FakePlayer:
        def __init__(self) -> None:
            self.seek_calls: list[int] = []
            self.play_calls = 0

        def seek_to_segment(self, idx: int) -> None:
            self.seek_calls.append(idx)

        def play(self) -> None:
            self.play_calls += 1

        def shutdown(self) -> None:
            pass

    player = _FakePlayer()
    page._room_chapter_player = player  # type: ignore[assignment]
    page._set_room_player_scope = lambda *args, **kwargs: None  # type: ignore[method-assign]

    # Simulate that a fast reassemble just completed after accepting segment 3.
    page._resume_after_reassemble_segment = 3

    class _TimelineEntry:
        def __init__(self, segment_index: int) -> None:
            self.segment_index = segment_index

    class _Timeline:
        entries = [_TimelineEntry(1), _TimelineEntry(3), _TimelineEntry(5)]

    timeline = _Timeline()

    page._maybe_resume_after_reassemble(timeline)

    assert player.seek_calls == [3], "Must seek to the accepted segment"
    assert player.play_calls == 1, "Must resume playback after seeking"
    assert page._voice_room_playback is True
    assert "第 4 段" in page._room_status_badge.text()
    # The pending resume marker must be cleared so a subsequent call is a no-op.
    assert page._resume_after_reassemble_segment is None

    page.shutdown()


def test_resume_after_reassemble_is_noop_without_pending_segment(
    qtbot: QtBot,
) -> None:
    """``_maybe_resume_after_reassemble`` is a no-op when no resume is pending."""
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page._build_deferred_tab(2)

    class _FakePlayer:
        def __init__(self) -> None:
            self.seek_calls: list[int] = []
            self.play_calls = 0

        def seek_to_segment(self, idx: int) -> None:
            self.seek_calls.append(idx)

        def play(self) -> None:
            self.play_calls += 1

        def shutdown(self) -> None:
            pass

    player = _FakePlayer()
    page._audio_player = player  # type: ignore[assignment]

    # No pending resume.
    page._resume_after_reassemble_segment = None

    class _Timeline:
        entries: list[object] = []

    page._maybe_resume_after_reassemble(_Timeline())

    assert player.seek_calls == []
    assert player.play_calls == 0

    page.shutdown()


def test_resume_after_reassemble_skips_when_segment_not_in_timeline(
    qtbot: QtBot,
) -> None:
    """If the rebuilt timeline no longer contains the accepted segment, do not seek."""
    page = VoiceStudioPage(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        defer_tabs=False,
    )
    qtbot.addWidget(page)
    page._build_deferred_tab(2)

    class _FakePlayer:
        def __init__(self) -> None:
            self.seek_calls: list[int] = []
            self.play_calls = 0

        def seek_to_segment(self, idx: int) -> None:
            self.seek_calls.append(idx)

        def play(self) -> None:
            self.play_calls += 1

        def shutdown(self) -> None:
            pass

    player = _FakePlayer()
    page._audio_player = player  # type: ignore[assignment]

    # Pending resume for segment 7, but timeline only has 1, 3, 5.
    page._resume_after_reassemble_segment = 7

    class _TimelineEntry:
        def __init__(self, segment_index: int) -> None:
            self.segment_index = segment_index

    class _Timeline:
        entries = [_TimelineEntry(1), _TimelineEntry(3), _TimelineEntry(5)]

    page._maybe_resume_after_reassemble(_Timeline())

    assert player.seek_calls == []
    assert player.play_calls == 0
    # Marker is always cleared, even when the segment is missing.
    assert page._resume_after_reassemble_segment is None

    page.shutdown()


# ── Speaker recommendation and batch accept ─────────────────────────────────


def test_script_editor_shows_candidate_recommendation_in_combo(qtbot: QtBot) -> None:
    """Unresolved segments display confidence-annotated candidates in the speaker combo."""
    page = _page(qtbot)
    page._voice_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(character_id="lin", character_name="林远", voice_id="v-lin"),
            VoiceCastEntry(character_id="su", character_name="苏晚", voice_id="v-su"),
        ]
    )
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="",
                character_name="",
                text="别碰它。",
            )
        ],
        metadata={
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [0],
                "decisions": [
                    {
                        "segment_index": 0,
                        "character_id": "lin",
                        "confidence": 0.85,
                        "rationale": "上下文主语为林远",
                    },
                    {
                        "segment_index": 0,
                        "character_id": "su",
                        "confidence": 0.12,
                        "rationale": "",
                    },
                ],
            }
        },
    )
    dialog = ScriptSegmentEditorDialog(script, parent=page)
    qtbot.addWidget(dialog)

    # The highest-confidence candidate should be auto-selected.
    assert dialog._speaker_combo.currentData() == "lin"
    # Combo text should contain confidence annotation.
    lin_idx = dialog._speaker_combo.findData("lin")
    assert "推荐 85%" in dialog._speaker_combo.itemText(lin_idx)
    # Advice label shows rationale.
    assert "上下文主语为林远" in dialog._advice_label.text()


def test_script_editor_batch_accept_recommendations(qtbot: QtBot) -> None:
    """Batch accept fills high-confidence candidates and leaves low ones unresolved."""
    page = _page(qtbot)
    page._voice_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(character_id="lin", character_name="林远", voice_id="v-lin"),
            VoiceCastEntry(character_id="su", character_name="苏晚", voice_id="v-su"),
        ]
    )
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="",
                character_name="",
                text="第一句。",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                character_id="",
                character_name="",
                text="第二句。",
            ),
        ],
        metadata={
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [0, 1],
                "decisions": [
                    {
                        "segment_index": 0,
                        "character_id": "lin",
                        "confidence": 0.90,
                        "rationale": "高置信度",
                    },
                    {
                        "segment_index": 1,
                        "character_id": "su",
                        "confidence": 0.40,
                        "rationale": "低置信度",
                    },
                ],
            }
        },
    )
    dialog = ScriptSegmentEditorDialog(script, parent=page)
    qtbot.addWidget(dialog)

    # Initially 2 unresolved.
    assert len(dialog._remaining_unresolved_rows()) == 2

    dialog._batch_accept_recommendations()

    # Only segment 0 (confidence 0.90 >= 0.75) should be resolved.
    assert 0 in dialog._speaker_changed_indices
    assert 1 not in dialog._speaker_changed_indices
    assert dialog._segments[0].character_id == "lin"
    # Segment 1 remains unresolved.
    assert len(dialog._remaining_unresolved_rows()) == 1
    assert "已自动采纳 1 段" in dialog._advice_label.text()
