"""Auto-run guards for ChapterStudioPage."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

import novel_forge.desktop.pages.chapter_studio.auto as chapter_studio_auto
import novel_forge.desktop.pages.chapter_studio.autorun as chapter_studio_autorun
import novel_forge.desktop.pages.chapter_studio.jobs as chapter_studio_jobs
import novel_forge.desktop.pages.chapter_studio.page as chapter_studio_page
from novel_forge.desktop.jobs import (
    CHAPTER_WRITE_KINDS,
    DesktopJobEvent,
    DesktopJobRecord,
    DesktopJobState,
)
from novel_forge.desktop.state.store import UIStore, get_ui_store
from novel_forge.workspace.contracts import (
    ChapterWorkspaceChapter,
    ChapterWorkspaceSnapshot,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def _build_snapshot(
    *,
    chapter_number: int,
    total_chapters: int = 5,
    current_status: str,
    continuity_issues: list[dict] | None = None,
    current_goal: str = "",
    current_outline_summary: str = "",
    alignment_score: float | None = None,
    overall_score: float | None = None,
    continuity_score: float | None = None,
    causal_score: float | None = None,
) -> ChapterWorkspaceSnapshot:
    chapters = [
        ChapterWorkspaceChapter(
            chapter_number=1,
            title="第1章",
            status="done",
            status_label="已完成",
        ),
        ChapterWorkspaceChapter(
            chapter_number=2,
            title="第2章",
            status="done",
            status_label="已完成",
        ),
        ChapterWorkspaceChapter(
            chapter_number=chapter_number,
            title=f"第{chapter_number}章",
            status=current_status,
            status_label="已完成" if current_status == "done" else "当前章",
        ),
        ChapterWorkspaceChapter(
            chapter_number=chapter_number + 1,
            title=f"第{chapter_number + 1}章",
            status="pending",
            status_label="待写",
        ),
    ]
    return ChapterWorkspaceSnapshot(
        project_id="遗物人生",
        project_title="遗物人生",
        chapter_number=chapter_number,
        total_chapters=total_chapters,
        chapters=chapters,
        current_title=f"第{chapter_number}章",
        current_goal=current_goal,
        current_outline_summary=current_outline_summary,
        alignment_score=alignment_score,
        overall_score=overall_score,
        continuity_score=continuity_score,
        causal_score=causal_score,
        continuity_issue_count=len(continuity_issues or []),
        continuity_issues=continuity_issues or [],
    )


def _build_success_job(*, chapter_number: int) -> DesktopJobRecord:
    return DesktopJobRecord(
        job_id="job-1",
        kind="resolve_chapter_checkpoint",
        label="归档章节",
        project_id="遗物人生",
        status=DesktopJobState.SUCCEEDED,
        result={"chapter_number": chapter_number},
    )


def _build_page(qapp: QApplication, *, chapter_number: int) -> chapter_studio_page.ChapterStudioPage:
    page = chapter_studio_page.ChapterStudioPage()
    page._project_combo.addItem("遗物人生")
    page._project_combo.setCurrentText("遗物人生")
    page._chapter_spin.setValue(chapter_number)
    page._state.current_project_id = "遗物人生"
    qapp.processEvents()
    return page


def _jobs_panel_texts(page: chapter_studio_page.ChapterStudioPage) -> list[str]:
    texts: list[str] = []
    for index in range(page._jobs_layout.count()):
        item = page._jobs_layout.itemAt(index)
        widget = item.widget()
        if widget is None:
            continue
        title = next(
            (
                label.text()
                for label in widget.findChildren(QLabel)
                if label.objectName() == "cardTitle"
            ),
            "",
        )
        if title:
            texts.append(title)
        elif isinstance(widget, QLabel):
            texts.append(widget.text())
    return [text for text in texts if text]


def test_book_auto_advances_completed_chapter_even_with_continuity_issues(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)
    page._mode = page.MODE_BOOK_AUTO
    page._auto_started = True
    page._studio = _build_snapshot(
        chapter_number=3,
        current_status="done",
        continuity_issues=[
            {"issue_type": "opening_gap", "severity": "medium", "summary": "仍有承接瑕疵"}
        ],
    )
    page._jobs = [_build_success_job(chapter_number=3)]

    emitted: list[tuple[str, int]] = []
    page.auto_advance_requested.connect(lambda project_id, ch: emitted.append((project_id, ch)))
    monkeypatch.setattr(
        chapter_studio_auto.QTimer,
        "singleShot",
        staticmethod(lambda _ms, callback: callback()),
    )

    page._try_auto_action()

    assert emitted == [("遗物人生", 4)]
    page.deleteLater()
    qapp.processEvents()


def test_auto_mode_does_not_repair_completed_chapter(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)
    page._mode = page.MODE_BOOK_AUTO
    page._auto_started = True
    page._studio = _build_snapshot(
        chapter_number=3,
        current_status="done",
        continuity_issues=[
            {"issue_type": "opening_gap", "severity": "medium", "summary": "仍有承接瑕疵"}
        ],
    )
    page._jobs = [_build_success_job(chapter_number=3)]

    emitted: list[object] = []
    page.repair_continuity_requested.connect(lambda request: emitted.append(request))
    monkeypatch.setattr(
        chapter_studio_auto.QTimer,
        "singleShot",
        staticmethod(lambda _ms, callback: callback()),
    )

    page._render_continuity_checklist()

    assert emitted == []
    page.deleteLater()
    qapp.processEvents()


def test_auto_mode_repairs_unfinished_chapter_when_issues_exist(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)
    page._mode = page.MODE_BOOK_AUTO
    page._auto_started = True
    page._studio = _build_snapshot(
        chapter_number=3,
        current_status="current",
        continuity_issues=[
            {"issue_type": "opening_gap", "severity": "medium", "summary": "仍有承接瑕疵"}
        ],
    )
    page._jobs = [_build_success_job(chapter_number=3)]

    emitted: list[object] = []
    page.repair_continuity_requested.connect(lambda request: emitted.append(request))
    monkeypatch.setattr(
        chapter_studio_auto.QTimer,
        "singleShot",
        staticmethod(lambda _ms, callback: callback()),
    )

    page._render_continuity_checklist()

    assert len(emitted) == 1
    assert emitted[0].project_id == "遗物人生"
    assert emitted[0].chapter_number == 3
    assert emitted[0].issue_indices == [0]
    page.deleteLater()
    qapp.processEvents()


def test_bind_studio_ignores_stale_snapshot_for_other_chapter(
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)
    current = _build_snapshot(chapter_number=3, current_status="current")
    stale = _build_snapshot(chapter_number=2, current_status="done")

    page.bind_studio(current)
    assert page._studio is not None
    assert page._studio.chapter_number == 3

    page.bind_studio(stale)
    assert page._studio is not None
    assert page._studio.chapter_number == 3

    page.deleteLater()
    qapp.processEvents()


def test_auto_user_chapter_navigation_rerenders_full_context(
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)
    initial = _build_snapshot(
        chapter_number=3,
        current_status="current",
        current_goal="旧章目标",
    )
    updated = _build_snapshot(
        chapter_number=3,
        current_status="done",
        current_goal="新章目标",
        current_outline_summary="新章概要",
        alignment_score=10.0,
        overall_score=7.43,
        continuity_score=9.4,
        causal_score=8.5,
    )

    page.bind_studio(initial)
    page._mode = page.MODE_BOOK_AUTO
    page._auto_started = True
    page._user_nav_ctx_pending = True

    page.bind_studio(updated)
    qapp.processEvents()

    current_card = page._compass._cards["current"]
    scores_card = page._memory_presenter.get_scores_widget()

    assert current_card._body.text() == "新章目标"
    assert current_card._foot.text() == "新章概要"
    score_text = scores_card._content_label.text()
    assert "对齐 10.0 / 10" in score_text
    assert "连贯 9.4 / 10" in score_text
    assert "质量 7.4 / 10" in score_text
    assert "因果 8.5 / 10" in score_text

    page.deleteLater()
    qapp.processEvents()


def test_jobs_panel_only_shows_current_chapter_jobs(
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)
    page._studio = _build_snapshot(chapter_number=3, current_status="current")

    jobs = [
        DesktopJobRecord(
            job_id="job-older",
            kind="prepare_chapter",
            label="章节方案 · 遗物人生 / 第 1 章",
            project_id="遗物人生",
            status=DesktopJobState.PAUSED,
            result={"chapter_number": 1},
        ),
        DesktopJobRecord(
            job_id="job-current",
            kind="resolve_chapter_checkpoint",
            label="方案执行 · 遗物人生 / 第 3 章",
            project_id="遗物人生",
            status=DesktopJobState.RUNNING,
            result={"chapter_number": 3},
        ),
    ]

    page._render_jobs_panel(jobs)

    assert _jobs_panel_texts(page) == ["方案执行（阶段 1/2） · 遗物人生 / 第 3 章"]
    page.deleteLater()
    qapp.processEvents()


def test_jobs_panel_surfaces_failed_background_chapter_job(
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=1)
    page._studio = _build_snapshot(chapter_number=1, current_status="current")

    jobs = [
        DesktopJobRecord(
            job_id="job-failed-finalize",
            kind="resolve_chapter_checkpoint_finalize",
            label="归档执行 · 遗物人生 / 第 2 章",
            project_id="遗物人生",
            status=DesktopJobState.FAILED,
            result={},
            events=[
                DesktopJobEvent(
                    at="2026-05-22T11:18:00+00:00",
                    step="contract_execution_audit",
                    payload={"chapter_number": 2},
                )
            ],
        )
    ]

    page._render_jobs_panel(jobs)

    assert any("第 2 章" in text for text in _jobs_panel_texts(page))
    page.deleteLater()
    qapp.processEvents()


def test_book_auto_surfaces_background_chapter_job_when_not_following(
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)
    page._mode = page.MODE_BOOK_AUTO
    page._auto_started = True
    page._studio = _build_snapshot(chapter_number=3, current_status="needs_decision")

    job = DesktopJobRecord(
        job_id="job-background",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · 遗物人生 / 第 4 章",
        project_id="遗物人生",
        status=DesktopJobState.RUNNING,
        current_step="draft",
        result={"chapter_number": 4},
    )

    page.bind_jobs([job])

    assert page._jobs == [job]
    assert "第 4 章" in page._action_summary.text()
    assert any("第 4 章" in text for text in _jobs_panel_texts(page))
    page.deleteLater()
    qapp.processEvents()


def test_finalize_checkpoint_jobs_are_project_writes() -> None:
    assert "resolve_chapter_checkpoint_finalize" in CHAPTER_WRITE_KINDS


def test_auto_refresh_context_keeps_autorun_alive_after_retry_limit(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)
    page._mode = page.MODE_BOOK_AUTO
    page._auto_started = True
    page._auto_refresh_count = page.MAX_AUTO_REFRESH_ATTEMPTS
    page._studio = _build_snapshot(chapter_number=3, current_status="current")

    emitted: list[bool] = []
    page.workspace_refresh_requested.connect(lambda: emitted.append(True))
    monkeypatch.setattr(
        chapter_studio_autorun,
        "decide_autopilot_action",
        lambda _ctx: chapter_studio_autorun.AutoPilotDecision("refresh_context"),
    )

    page._try_auto_action()

    assert page._auto_started is True
    assert page._mode == page.MODE_BOOK_AUTO
    assert page._auto_refresh_count == page.MAX_AUTO_REFRESH_ATTEMPTS + 1
    assert emitted == [True]
    assert page._context_request_timer.isActive()

    page._context_request_timer.stop()
    page.deleteLater()
    qapp.processEvents()


def test_jobs_panel_hides_stale_paused_job_without_live_checkpoint(
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)
    page._studio = _build_snapshot(chapter_number=3, current_status="current")

    jobs = [
        DesktopJobRecord(
            job_id="job-paused",
            kind="prepare_chapter",
            label="章节方案 · 遗物人生 / 第 3 章",
            project_id="遗物人生",
            status=DesktopJobState.PAUSED,
            result={"chapter_number": 3},
        )
    ]

    page._render_jobs_panel(jobs)

    assert _jobs_panel_texts(page) == ["当前章节暂无相关任务运行中。"]
    page.deleteLater()
    qapp.processEvents()


def test_jobs_panel_hides_failed_entry_when_newer_retry_exists(
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)
    page._studio = _build_snapshot(chapter_number=3, current_status="current")

    jobs = [
        DesktopJobRecord(
            job_id="job-new",
            kind="resolve_chapter_checkpoint",
            label="方案执行 · 遗物人生 / 第 3 章（重试中）",
            project_id="遗物人生",
            status=DesktopJobState.RUNNING,
            result={"chapter_number": 3},
        ),
        DesktopJobRecord(
            job_id="job-old-failed",
            kind="resolve_chapter_checkpoint",
            label="方案执行 · 遗物人生 / 第 3 章（上一轮）",
            project_id="遗物人生",
            status=DesktopJobState.FAILED,
            result={"chapter_number": 3},
        ),
    ]

    page._render_jobs_panel(jobs)

    texts = _jobs_panel_texts(page)
    retry_title = "方案执行（阶段 1/2） · 遗物人生 / 第 3 章（重试中）"
    previous_title = "方案执行（阶段 1/2） · 遗物人生 / 第 3 章（上一轮）"
    assert retry_title in texts
    assert previous_title in texts
    assert texts.index(retry_title) < texts.index(previous_title)
    page.deleteLater()
    qapp.processEvents()


def test_jobs_panel_keeps_history_beyond_legacy_eight_item_cap(
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)
    page._studio = _build_snapshot(chapter_number=3, current_status="current")

    jobs = [
        DesktopJobRecord(
            job_id=f"job-{idx:02d}",
            kind="resolve_chapter_checkpoint",
            label=f"方案执行 · 遗物人生 / 第 3 章（历史 {idx}）",
            project_id="遗物人生",
            status=DesktopJobState.SUCCEEDED,
            result={"chapter_number": 3},
        )
        for idx in range(1, 11)
    ]

    page._render_jobs_panel(jobs)

    texts = _jobs_panel_texts(page)
    assert len(texts) == 10
    assert texts[-1].endswith("历史 10）")
    page.deleteLater()
    qapp.processEvents()


def test_bind_jobs_uses_latest_memory_event_only(
    qapp: QApplication,
) -> None:
    UIStore.reset()
    store = get_ui_store()
    page = _build_page(qapp, chapter_number=3)
    page._studio = _build_snapshot(chapter_number=3, current_status="current")

    latest_job = DesktopJobRecord(
        job_id="job-latest",
        kind="run_chapter",
        label="生成第 5 章",
        project_id="遗物人生",
        status=DesktopJobState.SUCCEEDED,
    )
    latest_job.events = [
        DesktopJobEvent(
            at="2026-04-01T10:00:00+00:00",
            step="memory_updated",
            payload={"last_indexed_chapter": 5, "indexed_chapters": 5, "motifs": [{"motif_id": "new"}]},
        )
    ]

    old_job = DesktopJobRecord(
        job_id="job-old",
        kind="run_chapter",
        label="生成第 2 章",
        project_id="遗物人生",
        status=DesktopJobState.SUCCEEDED,
    )
    old_job.events = [
        DesktopJobEvent(
            at="2026-03-20T10:00:00+00:00",
            step="memory_updated",
            payload={"last_indexed_chapter": 2, "indexed_chapters": 2, "motifs": [{"motif_id": "old"}]},
        )
    ]

    page.bind_jobs([latest_job, old_job])
    status = store.memory_status("遗物人生")

    assert status.get("last_indexed_chapter") == 5
    assert status.get("motifs", [{}])[0].get("motif_id") == "new"

    page.deleteLater()
    qapp.processEvents()
    UIStore.reset()


def test_bind_jobs_prefers_newest_memory_event_timestamp_over_job_order(
    qapp: QApplication,
) -> None:
    UIStore.reset()
    store = get_ui_store()
    page = _build_page(qapp, chapter_number=3)
    page._studio = _build_snapshot(chapter_number=3, current_status="current")

    newer_job_with_older_event = DesktopJobRecord(
        job_id="job-created-newer",
        kind="run_chapter",
        label="生成第 4 章",
        project_id="遗物人生",
        status=DesktopJobState.SUCCEEDED,
        created_at="2026-04-02T10:00:00+00:00",
    )
    newer_job_with_older_event.events = [
        DesktopJobEvent(
            at="2026-04-01T10:00:00+00:00",
            step="memory_updated",
            payload={"last_indexed_chapter": 4, "indexed_chapters": 4, "motifs": [{"motif_id": "older"}]},
        )
    ]

    older_job_with_newer_event = DesktopJobRecord(
        job_id="job-created-older",
        kind="run_chapter",
        label="生成第 5 章",
        project_id="遗物人生",
        status=DesktopJobState.SUCCEEDED,
        created_at="2026-04-01T09:00:00+00:00",
    )
    older_job_with_newer_event.events = [
        DesktopJobEvent(
            at="2026-04-02T11:00:00+00:00",
            step="memory_updated",
            payload={"last_indexed_chapter": 5, "indexed_chapters": 5, "motifs": [{"motif_id": "newer"}]},
        )
    ]

    page.bind_jobs([newer_job_with_older_event, older_job_with_newer_event])
    status = store.memory_status("遗物人生")

    assert status.get("last_indexed_chapter") == 5
    assert status.get("motifs", [{}])[0].get("motif_id") == "newer"

    page.deleteLater()
    qapp.processEvents()
    UIStore.reset()


def test_bind_jobs_bootstrap_skips_historical_memory_replay_when_disk_status_exists(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    UIStore.reset()
    store = get_ui_store()
    page = _build_page(qapp, chapter_number=1)
    page._studio = _build_snapshot(chapter_number=1, current_status="current")

    monkeypatch.setattr(
        page,
        "_load_memory_status_from_disk",
        lambda _project_id: {
            "indexed_chapters": 0,
            "motifs": [],
            "motif_suggestions": [],
            "repetition_warnings": [],
            "last_indexed_chapter": 0,
        },
    )

    historical_job = DesktopJobRecord(
        job_id="job-historical",
        kind="run_chapter",
        label="生成第 21 章",
        project_id="遗物人生",
        status=DesktopJobState.SUCCEEDED,
    )
    historical_job.events = [
        DesktopJobEvent(
            at="2026-04-08T21:04:09.132630+00:00",
            step="memory_updated",
            payload={"last_indexed_chapter": 21, "indexed_chapters": 21, "motifs": [{"motif_id": "old"}]},
        )
    ]

    page.bind_jobs([historical_job])
    assert store.memory_status("遗物人生") == {}

    fresh_job = DesktopJobRecord(
        job_id="job-fresh",
        kind="run_chapter",
        label="生成第 1 章",
        project_id="遗物人生",
        status=DesktopJobState.SUCCEEDED,
    )
    fresh_job.events = [
        DesktopJobEvent(
            at="2026-04-09T08:00:00+00:00",
            step="memory_updated",
            payload={"last_indexed_chapter": 1, "indexed_chapters": 1, "motifs": [{"motif_id": "fresh"}]},
        )
    ]

    page.bind_jobs([fresh_job])
    status = store.memory_status("遗物人生")

    assert status.get("last_indexed_chapter") == 1
    assert status.get("motifs", [{}])[0].get("motif_id") == "fresh"

    page.deleteLater()
    qapp.processEvents()
    UIStore.reset()


def test_clear_task_flow_emits_terminal_job_ids_only(
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)

    page._jobs = [
        DesktopJobRecord(
            job_id="done",
            kind="run_chapter",
            label="方案执行 · 遗物人生 / 第 3 章",
            project_id="遗物人生",
            status=DesktopJobState.SUCCEEDED,
            result={"chapter_number": 3},
        ),
        DesktopJobRecord(
            job_id="failed",
            kind="prepare_chapter",
            label="章节方案 · 遗物人生 / 第 3 章",
            project_id="遗物人生",
            status=DesktopJobState.FAILED,
            result={"chapter_number": 3},
        ),
        DesktopJobRecord(
            job_id="running",
            kind="run_chapter",
            label="方案执行 · 遗物人生 / 第 3 章",
            project_id="遗物人生",
            status=DesktopJobState.RUNNING,
            result={"chapter_number": 3},
        ),
    ]

    captured: list[tuple[str, list[str]]] = []
    page.clear_task_flow_requested.connect(
        lambda project_id, ids: captured.append((project_id, list(ids)))
    )

    page._on_clear_task_flow()

    assert captured == [("遗物人生", ["done", "failed"])]

    page.deleteLater()
    qapp.processEvents()


def test_clear_task_flow_button_uses_visible_cards_when_filtered_jobs_empty(
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)
    page._studio = _build_snapshot(chapter_number=3, current_status="current")

    stale_label_job = DesktopJobRecord(
        job_id="job-old-label",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · 遗物人生 / 第 19 章",
        project_id="遗物人生",
        status=DesktopJobState.FAILED,
        result={},  # historical payload may miss chapter_number
    )

    captured: list[tuple[str, list[str]]] = []
    page.clear_task_flow_requested.connect(
        lambda project_id, ids: captured.append((project_id, list(ids)))
    )

    page.bind_jobs([stale_label_job])
    assert page._clear_task_flow_btn.isEnabled() is True

    page._on_clear_task_flow()
    assert captured == [("遗物人生", ["job-old-label"])]

    page.deleteLater()
    qapp.processEvents()


def test_task_flow_places_separator_before_collapsed_history(
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)
    page._studio = _build_snapshot(chapter_number=3, current_status="current")

    running_job = DesktopJobRecord(
        job_id="job-running",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · 遗物人生 / 第 3 章",
        project_id="遗物人生",
        status=DesktopJobState.RUNNING,
        result={"chapter_number": 3},
    )
    historical_job = DesktopJobRecord(
        job_id="job-historical",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · 遗物人生 / 第 3 章",
        project_id="遗物人生",
        status=DesktopJobState.SUCCEEDED,
        result={"chapter_number": 3},
    )

    page.bind_jobs([running_job, historical_job])
    qapp.processEvents()

    top_level: list[str] = []
    for index in range(page._jobs_layout.count()):
        widget = page._jobs_layout.itemAt(index).widget()
        if widget is page._history_separator_widget:
            top_level.append("历史任务")
            continue
        title = next(
            (
                label.toolTip()
                for label in (widget.findChildren(QLabel) if widget is not None else [])
                if label.objectName() == "cardTitle"
            ),
            "",
        )
        if title:
            top_level.append(title)

    assert top_level == [
        "方案执行（阶段 1/2） · 遗物人生 / 第 3 章",
        "历史任务",
        "方案执行（阶段 1/2） · 遗物人生 / 第 3 章",
    ]
    assert page._chapter_job_cards["job-running"].property("historical") is False
    assert page._chapter_job_cards["job-historical"].property("historical") is True

    page.bind_jobs([running_job])
    qapp.processEvents()

    assert page._history_separator_widget is None
    assert page._chapter_job_cards["job-running"].property("historical") is False

    page.deleteLater()
    qapp.processEvents()


def test_chapter_task_flow_job_card_signals_reach_page_handlers(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    page = _build_page(qapp, chapter_number=3)
    page._studio = _build_snapshot(chapter_number=3, current_status="current")
    monkeypatch.setattr(chapter_studio_jobs, "show_stop_confirm_dialog", lambda *args: True)

    cancelled: list[tuple[str, str]] = []
    refreshed: list[bool] = []
    page.cancel_job_requested.connect(lambda job_id, reason: cancelled.append((job_id, reason)))
    page.workspace_refresh_requested.connect(lambda: refreshed.append(True))

    running_job = DesktopJobRecord(
        job_id="job-running",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · 遗物人生 / 第 3 章",
        project_id="遗物人生",
        status=DesktopJobState.RUNNING,
        current_step="draft",
        result={"chapter_number": 3},
    )
    failed_job = DesktopJobRecord(
        job_id="job-failed",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · 遗物人生 / 第 3 章",
        project_id="遗物人生",
        status=DesktopJobState.FAILED,
        current_step="failed",
        result={"chapter_number": 3},
    )

    page.bind_jobs([running_job, failed_job])
    qapp.processEvents()

    page._chapter_job_cards["job-running"].stop_requested.emit("job-running")
    page._chapter_job_cards["job-running"].resume_requested.emit("job-running", "遗物人生", 3)
    page._chapter_job_cards["job-failed"].checkpoint_resume_requested.emit(
        "job-failed", "遗物人生", 3
    )

    assert cancelled == [("job-running", "用户已取消")]
    assert refreshed == [True, True]

    page.deleteLater()
    qapp.processEvents()


def test_memory_invalidated_rolls_back_memory_index_progress(
    qapp: QApplication,
) -> None:
    UIStore.reset()
    store = get_ui_store()
    page = _build_page(qapp, chapter_number=6)

    store.set_memory_status(
        "遗物人生",
        {
            "motifs": [
                {"motif_id": "m1", "first_chapter": 2},
                {"motif_id": "m2", "first_chapter": 5},
            ],
            "motif_suggestions": [{"motif_id": "m2"}],
            "repetition_warnings": [{"motif_id": "m2"}],
            "last_indexed_chapter": 6,
            "indexed_chapters": 6,
        },
    )

    page._handle_memory_invalidated("遗物人生", {"from_chapter": 5})
    status = store.memory_status("遗物人生")

    assert [m.get("motif_id") for m in status.get("motifs", [])] == ["m1"]
    assert status.get("last_indexed_chapter") == 4
    assert status.get("indexed_chapters") == 4
    assert status.get("motif_suggestions") == []
    assert status.get("repetition_warnings") == []

    page.deleteLater()
    qapp.processEvents()
    UIStore.reset()
