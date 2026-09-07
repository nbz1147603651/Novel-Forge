"""Tests for chapter studio memory status loading fallbacks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_forge.desktop.pages.chapter_studio.memory import load_memory_status_from_disk


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_load_outline_episodic_accepts_canon_plot_threads_dict(tmp_path: Path) -> None:
    project_id = "demo-project"
    project_root = tmp_path / project_id

    outline_file = project_root / "memory" / "outline_episodic.json"
    _write_json(
        outline_file,
        {
            "chapter_outlines": {"1": ["outline_sig"]},
            "relationships": [],
            "theme_tracker": {},
            "motif_tracker": {},
            "unresolved_questions": ["胡杨信背后的密令是什么？"],
        },
    )

    _write_json(
        project_root / "canon" / "canon_current.json",
        {
            "plot_threads": {
                "thread_huyang": {
                    "thread_id": "thread_huyang",
                    "title": "胡杨信",
                    "summary": "右眼锚点里的密令",
                    "last_touched_chapter": 3,
                }
            }
        },
    )

    status = load_memory_status_from_disk(project_id, tmp_path)

    assert status is not None
    assert status["motifs"]
    assert status["motifs"][0]["motif_id"] == "thread_huyang"
    assert status["motifs"][0]["last_chapter"] == 3
    assert status["unresolved_questions"] == ["胡杨信背后的密令是什么？"]


def test_load_project_memory_prefers_motif_state_shard_over_embedded_fields(
    tmp_path: Path,
) -> None:
    """Regression test for the motif persistence split (commit b10e2cbc).

    Since 2026-06-05, ``MemoryContext.save_to_disk`` writes motif data to a
    sibling ``motif_state.json`` file instead of embedding it inside
    ``project_memory.json``. The UI disk loader MUST read the shard;
    otherwise the motif tab shows "暂无母题数据" even when extraction succeeded.
    """
    project_id = "demo-project"
    memory_dir = tmp_path / project_id / "memory"
    memory_file = memory_dir / "project_memory.json"
    motif_state_file = memory_dir / "motif_state.json"

    _write_json(
        memory_file,
        {
            "last_indexed_chapter": 3,
            "summary_cache": {},
            "summary_stats": {},
            "chapter_content_hash": {},
            "episodic_index": {"chapter_events": {"1": [], "2": [], "3": []}},
            "motif_tracker": {"motifs": {}, "chapter_motifs": {}},
            "motif_cache": {},
        },
    )

    _write_json(
        motif_state_file,
        {
            "motif_tracker": {
                "motifs": {
                    "motif_real_1": {
                        "name": "残玉",
                        "category": "意象",
                        "description": "贴身的残玉",
                        "occurrence_count": 7,
                        "first_appearance_chapter": 1,
                        "last_appearance_chapter": 3,
                    },
                    "motif_real_2": {
                        "name": "半块玉",
                        "category": "意象",
                        "description": "洞房枕下的半块玉",
                        "occurrence_count": 3,
                        "first_appearance_chapter": 2,
                        "last_appearance_chapter": 3,
                    },
                },
                "chapter_motifs": {
                    "1": ["motif_real_1"],
                    "2": ["motif_real_1", "motif_real_2"],
                    "3": ["motif_real_1", "motif_real_2"],
                },
            },
            "motif_cache": {
                "1": [{"motif_id": "motif_real_1", "chapter_number": 1}],
                "2": [{"motif_id": "motif_real_2", "chapter_number": 2}],
            },
        },
    )

    status = load_memory_status_from_disk(project_id, tmp_path)

    assert status is not None
    assert len(status["motifs"]) == 2
    motif_ids = {m["motif_id"] for m in status["motifs"]}
    assert motif_ids == {"motif_real_1", "motif_real_2"}
    real_1 = next(m for m in status["motifs"] if m["motif_id"] == "motif_real_1")
    assert real_1["occurrence_count"] == 7
    assert real_1["first_chapter"] == 1
    assert real_1["last_chapter"] == 3
    assert status["chapter_motifs"] == {
        "1": ["motif_real_1"],
        "2": ["motif_real_1", "motif_real_2"],
        "3": ["motif_real_1", "motif_real_2"],
    }


def test_load_project_memory_falls_back_when_motif_state_shard_is_empty(
    tmp_path: Path,
) -> None:
    """An empty shard must not mask legacy embedded motif data.

    If ``motif_state.json`` exists but is empty (e.g. partial migration), the
    loader falls back to the embedded ``motif_tracker``/``motif_cache`` keys
    inside ``project_memory.json``.
    """
    project_id = "demo-project"
    memory_dir = tmp_path / project_id / "memory"
    memory_file = memory_dir / "project_memory.json"
    motif_state_file = memory_dir / "motif_state.json"

    _write_json(
        memory_file,
        {
            "last_indexed_chapter": 2,
            "summary_cache": {},
            "summary_stats": {},
            "chapter_content_hash": {},
            "episodic_index": {"chapter_events": {"1": [], "2": []}},
            "motif_tracker": {
                "motifs": {
                    "legacy_x": {
                        "name": "Legacy motif",
                        "category": "意象",
                        "description": "kept in legacy embedded form",
                        "occurrence_count": 2,
                        "first_appearance_chapter": 1,
                        "last_appearance_chapter": 2,
                    }
                },
                "chapter_motifs": {"1": ["legacy_x"], "2": ["legacy_x"]},
            },
            "motif_cache": {},
        },
    )
    _write_json(
        motif_state_file,
        {"motif_tracker": {"motifs": {}, "chapter_motifs": {}}, "motif_cache": {}},
    )

    status = load_memory_status_from_disk(project_id, tmp_path)

    assert status is not None
    assert len(status["motifs"]) == 1
    assert status["motifs"][0]["motif_id"] == "legacy_x"
    assert status["chapter_motifs"] == {"1": ["legacy_x"], "2": ["legacy_x"]}


def test_load_project_memory_falls_back_when_motif_state_shard_missing(
    tmp_path: Path,
) -> None:
    """No shard on disk → legacy embedded data must still load."""
    project_id = "demo-project"
    memory_file = tmp_path / project_id / "memory" / "project_memory.json"
    _write_json(
        memory_file,
        {
            "last_indexed_chapter": 1,
            "summary_cache": {},
            "summary_stats": {},
            "chapter_content_hash": {},
            "episodic_index": {"chapter_events": {"1": []}},
            "motif_tracker": {
                "motifs": {
                    "legacy_y": {
                        "name": "Legacy Y",
                        "category": "意象",
                        "description": "old project",
                        "occurrence_count": 1,
                        "first_appearance_chapter": 1,
                        "last_appearance_chapter": 1,
                    }
                },
                "chapter_motifs": {"1": ["legacy_y"]},
            },
            "motif_cache": {},
        },
    )

    status = load_memory_status_from_disk(project_id, tmp_path)

    assert status is not None
    assert len(status["motifs"]) == 1
    assert status["motifs"][0]["motif_id"] == "legacy_y"


def test_load_project_memory_ignores_invalid_motif_cache_chapter_number(tmp_path: Path) -> None:
    project_id = "demo-project"
    memory_file = tmp_path / project_id / "memory" / "project_memory.json"
    _write_json(
        memory_file,
        {
            "last_indexed_chapter": 2,
            "summary_cache": {},
            "summary_stats": {},
            "chapter_content_hash": {},
            "episodic_index": {"chapter_events": {"1": [], "2": []}},
            "motif_tracker": {
                "motifs": {
                    "m1": {
                        "name": "怀表",
                        "category": "意象",
                        "description": "反复出现的旧怀表",
                        "occurrence_count": 0,
                        "first_appearance_chapter": 0,
                        "last_appearance_chapter": 0,
                    }
                },
                "chapter_motifs": {"1": ["m1"]},
            },
            "motif_cache": {"2": [{"motif_id": "m1", "chapter_number": "invalid"}]},
        },
    )

    status = load_memory_status_from_disk(project_id, tmp_path)

    assert status is not None
    assert status["indexed_chapters"] == 2
    assert len(status["motifs"]) == 1
    assert status["motifs"][0]["motif_id"] == "m1"
    assert status["chapter_motifs"] == {"1": ["m1"], "2": ["m1"]}


def test_load_project_memory_falls_back_to_last_indexed_when_chapter_events_empty(
    tmp_path: Path,
) -> None:
    project_id = "demo-project"
    memory_file = tmp_path / project_id / "memory" / "project_memory.json"
    _write_json(
        memory_file,
        {
            "last_indexed_chapter": 3,
            "summary_cache": {},
            "summary_stats": {},
            "chapter_content_hash": {},
            "episodic_index": {"chapter_events": {}},
            "motif_tracker": {
                "motifs": {
                    "m1": {
                        "name": "怀表",
                        "category": "意象",
                        "description": "反复出现的旧怀表",
                        "occurrence_count": 1,
                        "first_appearance_chapter": 1,
                        "last_appearance_chapter": 3,
                    }
                }
            },
        },
    )

    status = load_memory_status_from_disk(project_id, tmp_path)

    assert status is not None
    assert status["indexed_chapters"] == 3
    assert status["last_indexed_chapter"] == 3


# ═══════════════════════════════════════════════════════════════════════════
# P0-5: _select_latest_memory_event surfaces concurrent_tasks_done failures
# ═══════════════════════════════════════════════════════════════════════════


class TestSelectLatestMemoryEventConcurrentFailures:
    """P0-5 regression: finalize_chapter_memory emits
    ``concurrent_tasks_done`` with ``tasks_failed=["episodic", ...]`` when
    a memory subtask raises but the chapter itself completes. Previously
    the UI never saw these — 弈局谋心 chapter 1 lost its episodic memory
    silently with a green "completed" indicator.
    """

    def _make_record(
        self,
        project_id: str = "弈局谋心",
        events: list[tuple[str, dict]] | None = None,
    ):
        from novel_forge.desktop.jobs import DesktopJobEvent, DesktopJobRecord

        return DesktopJobRecord(
            job_id="job-1",
            kind="desktop-resolve-chapter-checkpoint",
            label="归档执行",
            project_id=project_id,
            events=[
                DesktopJobEvent(at="2026-06-03T16:00:00", step=step, payload=payload)
                for step, payload in (events or [])
            ],
        )

    def test_memory_concurrent_tasks_done_with_failures_surfaces_warning(self) -> None:
        """P0-5 (round 2): the REAL event arriving at the desktop is
        ``memory_concurrent_tasks_done`` (workspace callback wraps the
        stage with the ``memory_`` prefix in
        ``execution_runners.py:410-426`` and
        ``chapter_session_handlers.py:1598-1614``). Earlier the test
        used the unprefixed name and passed while production never
        matched — silently green. This test uses the real name.
        """
        from novel_forge.desktop.pages.chapter_studio.jobs import (
            ChapterStudioJobsMixin,
        )

        record = self._make_record(
            events=[
                (
                    "memory_concurrent_tasks_done",
                    {
                        "chapter": 1,
                        "tasks_ok": ["expression", "summary"],
                        "tasks_failed": ["episodic"],
                        "motifs_extracted": 0,
                        "summary_generated": True,
                    },
                )
            ]
        )

        result = ChapterStudioJobsMixin._select_latest_memory_event(
            [record],
            project_id="弈局谋心",
            fallback_chapter=1,
        )

        assert result is not None
        event_type, _at, payload = result
        assert event_type == "tasks_failed"
        assert payload["chapter"] == 1
        assert payload["tasks_failed"] == ["episodic"]
        assert payload["tasks_ok"] == ["expression", "summary"]

    def test_concurrent_tasks_done_without_failures_ignored(self) -> None:
        """When all tasks succeed, the event should be ignored (the UI
        already shows the success state through the normal
        ``memory_updated`` pathway)."""
        from novel_forge.desktop.pages.chapter_studio.jobs import (
            ChapterStudioJobsMixin,
        )

        record = self._make_record(
            events=[
                (
                    "memory_concurrent_tasks_done",
                    {
                        "chapter": 1,
                        "tasks_ok": ["episodic", "expression", "summary"],
                        "tasks_failed": [],
                    },
                )
            ]
        )

        result = ChapterStudioJobsMixin._select_latest_memory_event(
            [record],
            project_id="弈局谋心",
            fallback_chapter=1,
        )

        # No memory_updated in events, so no memory event selected
        assert result is None

    def test_memory_updated_still_takes_precedence(self) -> None:
        """When both memory_updated and concurrent_tasks_done with
        failures are present, the more recent (later at) wins."""
        from novel_forge.desktop.pages.chapter_studio.jobs import (
            ChapterStudioJobsMixin,
        )

        record = self._make_record(
            events=[
                (
                    "memory_updated",
                    {
                        "chapter": 1,
                        "indexed_chapters": 1,
                        "motifs": [],
                    },
                ),
                (
                    "memory_concurrent_tasks_done",
                    {
                        "chapter": 1,
                        "tasks_ok": ["summary"],
                        "tasks_failed": ["episodic"],
                    },
                ),
            ]
        )

        result = ChapterStudioJobsMixin._select_latest_memory_event(
            [record],
            project_id="弈局谋心",
            fallback_chapter=1,
        )

        assert result is not None
        event_type, _at, payload = result
        # The later event (concurrent_tasks_done) wins by at-sort
        assert event_type == "tasks_failed"
        assert payload["tasks_failed"] == ["episodic"]

    def test_unprefixed_concurrent_tasks_done_also_matched(self) -> None:
        """P0-5 (round 2): the unprefixed name is accepted for
        defense-in-depth in case a future caller forgets the prefix."""
        from novel_forge.desktop.pages.chapter_studio.jobs import (
            ChapterStudioJobsMixin,
        )

        record = self._make_record(
            events=[
                (
                    "concurrent_tasks_done",
                    {
                        "chapter": 1,
                        "tasks_ok": ["summary"],
                        "tasks_failed": ["episodic"],
                    },
                )
            ]
        )

        result = ChapterStudioJobsMixin._select_latest_memory_event(
            [record],
            project_id="弈局谋心",
            fallback_chapter=1,
        )
        assert result is not None
        event_type, _at, payload = result
        assert event_type == "tasks_failed"
        assert payload["tasks_failed"] == ["episodic"]


class TestHandleMemoryTasksFailedHandler:
    """P0-5 (round 2): the dedicated handler must surface tasks_failed
    to the UI store so a red badge / warning can be rendered. Crucially
    it must NOT be folded into ``_handle_memory_updated`` (which silently
    drops unknown payload fields).
    """

    def test_handler_writes_tasks_failed_to_ui_store(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from novel_forge.desktop.pages.chapter_studio.jobs import (
            ChapterStudioJobsMixin,
        )

        written: dict = {}

        class _FakeStore:
            def memory_status(self, project_id):
                return {"existing": "value"}

            def set_memory_status(self, project_id, status):
                written["project_id"] = project_id
                written["status"] = status

        monkeypatch.setattr(
            "novel_forge.desktop.state.store.get_ui_store",
            lambda: _FakeStore(),
        )
        mixin = ChapterStudioJobsMixin()
        mixin.current_project_id = lambda: "弈局谋心"
        mixin._handle_memory_tasks_failed(
            "弈局谋心",
            {
                "chapter": 1,
                "tasks_ok": ["expression", "summary"],
                "tasks_failed": ["episodic", "motifs"],
            },
        )

        assert written["project_id"] == "弈局谋心"
        status = written["status"]
        assert status["tasks_failed"] == ["episodic", "motifs"]
        assert status["tasks_ok"] == ["expression", "summary"]
        assert status["tasks_failed_chapter"] == 1
        assert status["existing"] == "value"  # existing fields preserved

    def test_handler_noop_when_no_tasks_failed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If for some reason the event has empty tasks_failed, do not
        clobber the store (it would be a false alarm)."""
        from novel_forge.desktop.pages.chapter_studio.jobs import (
            ChapterStudioJobsMixin,
        )

        called = {"set": 0}

        class _FakeStore:
            def memory_status(self, project_id):
                return None

            def set_memory_status(self, project_id, status):
                called["set"] += 1

        monkeypatch.setattr(
            "novel_forge.desktop.state.store.get_ui_store",
            lambda: _FakeStore(),
        )
        mixin = ChapterStudioJobsMixin()
        mixin.current_project_id = lambda: "弈局谋心"
        mixin._handle_memory_tasks_failed(
            "弈局谋心",
            {"chapter": 1, "tasks_failed": []},
        )
        assert called["set"] == 0

    def test_handler_ignores_other_projects(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from novel_forge.desktop.pages.chapter_studio.jobs import (
            ChapterStudioJobsMixin,
        )

        called = {"set": 0}

        class _FakeStore:
            def memory_status(self, project_id):
                return None

            def set_memory_status(self, project_id, status):
                called["set"] += 1

        monkeypatch.setattr(
            "novel_forge.desktop.state.store.get_ui_store",
            lambda: _FakeStore(),
        )
        mixin = ChapterStudioJobsMixin()
        mixin.current_project_id = lambda: "OTHER_PROJECT"
        mixin._handle_memory_tasks_failed(
            "弈局谋心",
            {"chapter": 1, "tasks_failed": ["episodic"]},
        )
        assert called["set"] == 0
