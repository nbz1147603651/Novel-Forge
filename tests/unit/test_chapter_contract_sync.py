"""Unit tests for the chapter-contract sync pure helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _outline_chapter(
    n: int,
    *,
    title: str = "X",
    goal: str = "g",
    points=None,
    beats=None,
    hook=None,
):
    return SimpleNamespace(
        chapter_number=n,
        title=title,
        goal=goal,
        main_plot_points=list(points or []),
        beats_summary=list(beats or []),
        subplot_focus=[],
        expected_hook=hook,
    )


def _outline(*, total: int = 3, chapters=None):
    chapters = chapters or [
        _outline_chapter(1, title="楔子", goal="引入", points=["事件A"], beats=["节拍1"]),
        _outline_chapter(2, title="冲突", goal="升级", points=["事件B"], beats=["节拍2"]),
        _outline_chapter(3, title="解决", goal="收束", points=["事件C"], beats=["节拍3"]),
    ]
    return SimpleNamespace(
        synopsis="s",
        total_chapters=total,
        volume_mode="",
        chapters=chapters,
    )


def _outline_dict(title_2: str = "冲突") -> dict:
    return {
        "total_chapters": 3,
        "volume_mode": False,
        "synopsis": "s",
        "chapters": [
            {
                "chapter_number": 1,
                "title": "楔子",
                "goal": "引入",
                "main_plot_points": ["事件A"],
                "beats_summary": ["节拍1"],
            },
            {
                "chapter_number": 2,
                "title": title_2,
                "goal": "升级",
                "main_plot_points": ["事件B"],
                "beats_summary": ["节拍2"],
            },
            {
                "chapter_number": 3,
                "title": "解决",
                "goal": "收束",
                "main_plot_points": ["事件C"],
                "beats_summary": ["节拍3"],
            },
        ],
    }


# ── compute_outline_fingerprint ──────────────────────────────────────────────


def test_fingerprint_stable_for_same_outline():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        compute_outline_fingerprint,
    )

    fp1 = compute_outline_fingerprint(_outline())
    fp2 = compute_outline_fingerprint(_outline())
    assert fp1 == fp2
    assert len(fp1) == 64  # SHA-256 hex


def test_fingerprint_changes_when_chapter_modified():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        compute_outline_fingerprint,
    )

    fp1 = compute_outline_fingerprint(_outline())
    chapters = list(_outline().chapters)
    chapters[1] = _outline_chapter(
        2, title="冲突改写", goal="升级", points=["事件B-新"], beats=["节拍2"],
    )
    fp2 = compute_outline_fingerprint(
        SimpleNamespace(synopsis="s", total_chapters=3, volume_mode="", chapters=chapters),
    )
    assert fp1 != fp2


# ── compute_affected_chapters_from_outline_fingerprint ──────────────────────


def test_affected_returns_empty_when_fingerprint_matches():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        compute_affected_chapters_from_outline_fingerprint,
        compute_outline_chapter_fingerprints,
        compute_outline_fingerprint,
    )

    outline = _outline()
    fp = compute_outline_fingerprint(outline)
    chapter_fps = compute_outline_chapter_fingerprints(outline)
    affected = compute_affected_chapters_from_outline_fingerprint(outline, fp, chapter_fps)
    assert affected == []


def test_affected_returns_empty_when_cache_missing_to_avoid_global_sync():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        compute_affected_chapters_from_outline_fingerprint,
    )

    outline = _outline()
    affected = compute_affected_chapters_from_outline_fingerprint(outline, "")
    assert affected == []


def test_affected_uses_per_chapter_fingerprints_for_local_scope():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        compute_affected_chapters_from_outline_fingerprint,
        compute_outline_chapter_fingerprints,
        compute_outline_fingerprint,
    )

    baseline = _outline()
    chapter_fps = compute_outline_chapter_fingerprints(baseline)
    chapters = list(_outline().chapters)
    chapters[1] = _outline_chapter(
        2,
        title="冲突改写",
        goal="升级",
        points=["事件B-新"],
        beats=["节拍2"],
    )
    current = _outline(chapters=chapters)
    affected = compute_affected_chapters_from_outline_fingerprint(
        current,
        compute_outline_fingerprint(baseline),
        chapter_fps,
    )
    assert affected == [2]


def test_scope_analysis_requires_manual_for_missing_baseline():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        analyze_outline_change_scope,
    )

    analysis = analyze_outline_change_scope(_outline(), {})
    assert analysis["requires_manual_scope"] is True
    assert analysis["affected"] == []
    assert analysis["reason"] == "missing_chapter_fingerprints"


# ── compute_cascade ─────────────────────────────────────────────────────────


def test_cascade_finds_direct_downstream_forward_reference():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        compute_cascade,
    )

    contracts = {
        "chapter_contracts": [
            {
                "chapter_number": 1,
                "entry_state_requirements": ["开篇状态"],
                "required_events": [],
                "required_progressions": [],
                "forbidden_changes": [],
                "forbidden_progressions": [],
            },
            {
                "chapter_number": 2,
                "entry_state_requirements": ["承接第1章的世界", "伏笔第3章"],
                "required_events": [],
                "required_progressions": [],
                "forbidden_changes": [],
                "forbidden_progressions": [],
            },
            {
                "chapter_number": 3,
                "entry_state_requirements": ["独立"],
                "required_events": [],
                "required_progressions": [],
                "forbidden_changes": [],
                "forbidden_progressions": [],
            },
        ]
    }
    cascade = compute_cascade({2}, contracts, max_depth=3)
    assert 3 in cascade
    assert 1 not in cascade  # upstream, not downstream


def test_cascade_is_not_recursive():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        compute_cascade,
    )

    contracts = {
        "chapter_contracts": [
            {
                "chapter_number": 1,
                "entry_state_requirements": ["继承第5章伏笔"],
                "required_events": [],
                "required_progressions": [],
                "forbidden_changes": [],
                "forbidden_progressions": [],
            },
            {
                "chapter_number": 5,
                "entry_state_requirements": ["继承第10章伏笔"],
                "required_events": [],
                "required_progressions": [],
                "forbidden_changes": [],
                "forbidden_progressions": [],
            },
        ]
    }
    cascade = compute_cascade({1}, contracts, max_depth=3)
    assert 5 in cascade
    assert 10 not in cascade


def test_cascade_finds_later_contract_referencing_affected_chapter():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        compute_cascade,
    )

    contracts = {
        "chapter_contracts": [
            {
                "chapter_number": 2,
                "entry_state_requirements": ["本章改变主线方向"],
                "required_events": [],
                "required_progressions": [],
                "forbidden_changes": [],
                "forbidden_progressions": [],
            },
            {
                "chapter_number": 5,
                "entry_state_requirements": ["承接第2章的选择"],
                "required_events": [],
                "required_progressions": [],
                "forbidden_changes": [],
                "forbidden_progressions": [],
            },
            {
                "chapter_number": 6,
                "entry_state_requirements": ["承接第5章的后果"],
                "required_events": [],
                "required_progressions": [],
                "forbidden_changes": [],
                "forbidden_progressions": [],
            },
        ]
    }
    cascade = compute_cascade({2}, contracts, max_depth=3)
    assert cascade == {5}


def test_cascade_handles_chinese_numerals():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        compute_cascade,
    )

    contracts = {
        "chapter_contracts": [
            {
                "chapter_number": 2,
                "entry_state_requirements": ["伏笔第五章", "承接第一章"],
                "required_events": [],
                "required_progressions": [],
                "forbidden_changes": [],
                "forbidden_progressions": [],
            }
        ]
    }
    cascade = compute_cascade({2}, contracts, max_depth=3)
    # Downstream: chapter 5 (forward ref via Chinese numeral).
    assert 5 in cascade
    # Upstream: chapter 1 (backward ref) must NOT propagate.
    assert 1 not in cascade


# ── merge_reextracted_contracts ─────────────────────────────────────────────


def test_merge_preserves_untouched_replaces_focus():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        merge_reextracted_contracts,
    )

    original = {
        "chapter_contracts": [
            {"chapter_number": 1, "title": "A", "required_events": ["旧1"]},
            {"chapter_number": 2, "title": "B", "required_events": ["旧2"]},
            {"chapter_number": 3, "title": "C", "required_events": ["旧3"]},
        ]
    }
    reextracted = {
        "chapter_contracts": [
            {"chapter_number": 2, "title": "B", "required_events": ["新2"]},
        ]
    }
    merged = merge_reextracted_contracts(original, reextracted, {2})
    by_num = {item["chapter_number"]: item for item in merged["chapter_contracts"]}
    assert by_num[1]["required_events"] == ["旧1"]
    assert by_num[2]["required_events"] == ["新2"]
    assert by_num[3]["required_events"] == ["旧3"]
    assert merged["sync_metadata"]["synced_chapter_numbers"] == [2]


def test_merge_preserves_original_when_focus_missing_in_reextracted():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        merge_reextracted_contracts,
    )

    original = {
        "chapter_contracts": [
            {"chapter_number": 1, "title": "A", "required_events": ["旧1"]},
        ]
    }
    reextracted: dict = {"chapter_contracts": []}
    merged = merge_reextracted_contracts(original, reextracted, {1})
    # No replacement came back, so original is preserved.
    assert merged["chapter_contracts"][0]["required_events"] == ["旧1"]


def test_restore_nonfocus_contract_items_keeps_original_dicts():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        restore_nonfocus_contract_items,
    )

    original = {
        "chapter_contracts": [
            {"chapter_number": 1, "title": "A", "custom": {"keep": True}},
            {"chapter_number": 2, "title": "B", "required_events": ["旧2"]},
        ]
    }
    normalized = {
        "chapter_contracts": [
            {"chapter_number": 1, "title": "A", "custom": {"keep": True}, "source": "x"},
            {"chapter_number": 2, "title": "B", "required_events": ["新2"], "source": "y"},
        ]
    }
    restored = restore_nonfocus_contract_items(normalized, original, {2})
    by_num = {item["chapter_number"]: item for item in restored["chapter_contracts"]}
    assert by_num[1] == original["chapter_contracts"][0]
    assert by_num[2] == normalized["chapter_contracts"][1]


# ── mark_artifact_stale ─────────────────────────────────────────────────────


def test_mark_artifact_stale_adds_flag(tmp_path: Path):
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        mark_artifact_stale,
    )

    target = tmp_path / "plan.json"
    target.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")

    class _Storage:
        def load_json(self, path):
            return json.loads(path.read_text(encoding="utf-8"))

        def save_json(self, path, data):
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
            )

    result = mark_artifact_stale(_Storage(), target, "test reason")
    assert result is True
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["stale"] is True
    assert payload["soft_stale"] is True
    assert payload["stale_kind"] == "chapter_contract_sync"
    assert payload["stale_reason"] == "test reason"
    assert "stale_marked_at" in payload
    assert payload["soft_stale_metadata"]["requires_regeneration"] is False


def test_mark_artifact_stale_missing_file_returns_false(tmp_path: Path):
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        mark_artifact_stale,
    )

    class _Storage:
        def load_json(self, path):
            return {}

        def save_json(self, path, data):
            pass

    assert mark_artifact_stale(_Storage(), tmp_path / "missing.json", "x") is False


# ── collect_downstream_stale_paths ──────────────────────────────────────────


def test_collect_downstream_stale_paths_dedup(tmp_path: Path):
    from novel_forge.persistence.models import ProjectLayout
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        collect_downstream_stale_paths,
    )

    layout = ProjectLayout(tmp_path)
    (layout.plans_dir).mkdir(parents=True, exist_ok=True)
    (layout.plans_dir / "chapter_003_plan.json").write_text("{}", encoding="utf-8")
    (layout.plans_dir / "chapter_003_bridge.json").write_text("{}", encoding="utf-8")
    (layout.plans_dir / "chapter_003_scene_plan.json").write_text("{}", encoding="utf-8")
    (layout.reports_dir).mkdir(parents=True, exist_ok=True)
    (layout.reports_dir / "contract_coherence.json").write_text("{}", encoding="utf-8")
    (layout.reports_dir / "chapter_003_guidance_plan.json").write_text("{}", encoding="utf-8")

    paths = collect_downstream_stale_paths(layout, {3}, {4, 5})
    names = sorted(p.name for p in paths)
    assert "chapter_003_plan.json" in names
    assert "chapter_003_bridge.json" in names
    assert "chapter_003_scene_plan.json" in names
    assert "chapter_003_guidance_plan.json" in names
    assert "contract_coherence.json" in names


# ── build_sync_preview ─────────────────────────────────────────────────────


def test_build_sync_preview_combines_affected_and_cascade():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        build_sync_preview,
        compute_outline_chapter_fingerprints,
    )

    baseline = _outline()
    current_chapters = list(_outline().chapters)
    current_chapters[1] = _outline_chapter(2, title="冲突新", goal="升级", points=["事件B2"])
    outline = _outline(chapters=current_chapters)
    contracts = {
        "chapter_contracts": [
            {
                "chapter_number": 2,
                "entry_state_requirements": ["承接第1章"],
                "required_events": [],
                "required_progressions": [],
                "forbidden_changes": [],
                "forbidden_progressions": [],
            },
            {
                "chapter_number": 3,
                "entry_state_requirements": ["承接第2章的伏笔"],
                "required_events": [],
                "required_progressions": [],
                "forbidden_changes": [],
                "forbidden_progressions": [],
            }
        ]
    }
    preview = build_sync_preview(
        outline,
        contracts,
        "",
        cascade_downstream=True,
        max_cascade_depth=3,
        cached_chapter_fingerprints=compute_outline_chapter_fingerprints(baseline),
    )
    assert preview["affected"] == [2]
    assert preview["cascade"] == [3]
    assert set(preview["focus"]) == {2, 3}
    assert preview["requires_manual_scope"] is False


def test_build_sync_preview_no_baseline_requires_manual_scope():
    from novel_forge.pipeline.long.services.chapter_contract_sync import build_sync_preview

    preview = build_sync_preview(
        _outline(),
        {"chapter_contracts": []},
        "",
        cascade_downstream=True,
    )
    assert preview["focus"] == []
    assert preview["requires_manual_scope"] is True


def test_build_sync_preview_legacy_matching_fingerprint_is_noop():
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        build_sync_preview,
        compute_outline_fingerprint,
    )

    outline = _outline()
    preview = build_sync_preview(
        outline,
        {"chapter_contracts": []},
        compute_outline_fingerprint(outline),
        cascade_downstream=True,
    )
    assert preview["focus"] == []
    assert preview["requires_manual_scope"] is False


def test_build_sync_preview_explicit_scope_works_without_baseline():
    from novel_forge.pipeline.long.services.chapter_contract_sync import build_sync_preview

    preview = build_sync_preview(
        _outline(),
        {"chapter_contracts": []},
        "",
        cascade_downstream=True,
        explicit_affected=[2],
    )
    assert preview["affected"] == [2]
    assert preview["focus"] == [2]
    assert preview["requires_manual_scope"] is False


def test_presenter_diff_cancel_returns_none(tmp_path: Path):
    from novel_forge.core.schemas.outline import StoryOutline
    from novel_forge.desktop.pages.standalone.outline_sync_presenter import (
        ChapterContractSyncPresenter,
    )
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        compute_outline_chapter_fingerprints,
        compute_outline_chapter_snapshots,
        compute_outline_fingerprint,
    )

    old_outline = StoryOutline.model_validate(_outline_dict())
    current_outline = _outline_dict(title_2="冲突改写")
    (tmp_path / "plans").mkdir(parents=True)
    (tmp_path / "outline.json").write_text(
        json.dumps(current_outline, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "plans" / "chapter_contracts.json").write_text(
        json.dumps(
            {
                "chapter_contracts": [
                    {"chapter_number": 2, "entry_state_requirements": ["旧"]},
                ],
                "sync_metadata": {
                    "outline_fingerprint": compute_outline_fingerprint(old_outline),
                    "outline_chapter_fingerprints": compute_outline_chapter_fingerprints(
                        old_outline
                    ),
                    "outline_chapter_snapshots": compute_outline_chapter_snapshots(
                        old_outline
                    ),
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class _Storage:
        def load_json(self, path: Path):
            return json.loads(path.read_text(encoding="utf-8"))

    class _Presenter(ChapterContractSyncPresenter):
        confirm_called = False
        diff_called = False

        def _show_confirm_dialog(self, preview):
            self.confirm_called = True
            return "view_diff"

        def _show_diff_dialog(self, preview, chapter_contracts, outline_data):
            self.diff_called = True
            return "cancel"

        def _show_info(self, title, text):
            raise AssertionError(f"unexpected info: {title} {text}")

        def _show_critical(self, title, text):
            raise AssertionError(f"unexpected critical: {title} {text}")

    presenter = _Presenter(
        parent=None,
        storage=_Storage(),
        layout_root=tmp_path,
        project_id="p",
    )
    assert presenter.run() is None
    assert presenter.confirm_called is True
    assert presenter.diff_called is True


async def test_execute_sync_only_sends_focus_chapters_and_leaves_state_dirs(tmp_path: Path):
    from novel_forge.core.schemas.outline import StoryOutline
    from novel_forge.persistence.filesystem import FileSystemStorage
    from novel_forge.persistence.models import ProjectLayout
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        compute_outline_chapter_fingerprints,
        compute_outline_chapter_snapshots,
        compute_outline_fingerprint,
    )
    from novel_forge.workspace.contracts import SyncChapterContractsRequest
    from novel_forge.workspace.execution import execute_sync_chapter_contracts

    storage = FileSystemStorage(tmp_path)
    project_id = "sync_exec"
    layout = ProjectLayout(storage.project_dir(project_id))
    layout.ensure_dirs()

    old_outline = StoryOutline.model_validate(_outline_dict())
    current_outline_payload = _outline_dict(title_2="冲突改写")
    storage.save_json(layout.outline_path, current_outline_payload)
    storage.save_json(layout.narrative_contract_path, {"rules": []})
    original_ch1 = {"chapter_number": 1, "title": "A", "custom": {"must_stay": True}}
    storage.save_json(
        layout.plans_dir / "chapter_contracts.json",
        {
            "chapter_contracts": [
                original_ch1,
                {"chapter_number": 2, "title": "B", "entry_state_requirements": []},
                {
                    "chapter_number": 3,
                    "title": "C",
                    "entry_state_requirements": ["承接第2章的选择"],
                },
            ],
            "sync_metadata": {
                "outline_fingerprint": compute_outline_fingerprint(old_outline),
                "outline_chapter_fingerprints": compute_outline_chapter_fingerprints(
                    old_outline
                ),
                "outline_chapter_snapshots": compute_outline_chapter_snapshots(old_outline),
            },
        },
    )
    storage.save_json(layout.chapter_plan_path(2), {"chapter_number": 2})
    storage.save_json(layout.chapter_bridge_path(3), {"chapter_number": 3})

    layout.chapter_path(1).write_text("chapter text", encoding="utf-8")
    layout.story_kernel_db_path.write_text("kernel", encoding="utf-8")
    (layout.memory_dir / "sentinel.json").write_text("memory", encoding="utf-8")
    (layout.narrative_state_dir / "sentinel.json").write_text("state", encoding="utf-8")

    class _Router:
        pass

    class _ServiceCtx:
        def __init__(self) -> None:
            self.router = _Router()
            self.settings = SimpleNamespace(temp_plan_chapter_contracts=0.1)
            self.sent_chapters: list[int] = []

        async def call_with_retry(self, task_type, payload, **kwargs):
            del task_type, kwargs
            chapters = payload["outline"]["chapters"]
            self.sent_chapters = [int(ch["chapter_number"]) for ch in chapters]
            return {
                "chapter_contracts": [
                    {
                        "chapter_number": number,
                        "title": f"new-{number}",
                        "entry_state_requirements": [],
                        "required_events": [f"event-{number}"],
                    }
                    for number in self.sent_chapters
                ]
            }

    service_ctx = _ServiceCtx()
    runtime = SimpleNamespace(storage=storage, service_ctx=service_ctx)
    result = await execute_sync_chapter_contracts(
        runtime,
        SyncChapterContractsRequest(
            project_id=project_id,
            rebuild_milestones=False,
            mark_stale=True,
        ),
    )

    assert result.result["status"] == "completed"
    assert result.result["affected"] == [2]
    assert result.result["cascade"] == [3]
    assert service_ctx.sent_chapters == [2, 3]

    synced = storage.load_json(layout.plans_dir / "chapter_contracts.json")
    by_num = {item["chapter_number"]: item for item in synced["chapter_contracts"]}
    assert by_num[1] == original_ch1
    assert by_num[2]["required_events"] == ["event-2"]
    assert by_num[3]["required_events"] == ["event-3"]
    assert "outline_chapter_fingerprints" in synced["sync_metadata"]

    assert layout.chapter_path(1).read_text(encoding="utf-8") == "chapter text"
    assert layout.story_kernel_db_path.read_text(encoding="utf-8") == "kernel"
    assert (layout.memory_dir / "sentinel.json").read_text(encoding="utf-8") == "memory"
    assert (layout.narrative_state_dir / "sentinel.json").read_text(encoding="utf-8") == "state"
    assert storage.load_json(layout.chapter_plan_path(2))["soft_stale"] is True
    assert storage.load_json(layout.chapter_bridge_path(3))["soft_stale"] is True


# ── compute_outline_fingerprint resilience ─────────────────────────────────


def test_fingerprint_ignores_chapter_order_changes():
    """Order of chapters is sorted by chapter_number, so the fingerprint is stable."""
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        compute_outline_fingerprint,
    )

    chapters_normal = [
        _outline_chapter(1, title="A"),
        _outline_chapter(2, title="B"),
        _outline_chapter(3, title="C"),
    ]
    chapters_reversed = list(reversed(chapters_normal))
    outline_a = SimpleNamespace(synopsis="s", total_chapters=3, volume_mode="", chapters=chapters_normal)
    outline_b = SimpleNamespace(synopsis="s", total_chapters=3, volume_mode="", chapters=chapters_reversed)
    assert compute_outline_fingerprint(outline_a) == compute_outline_fingerprint(outline_b)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
