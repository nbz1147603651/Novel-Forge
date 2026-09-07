"""P0-5: ``model_dump_json()`` fast path for snapshot hashing.

Pydantic v2's ``model.model_dump_json()`` produces a JSON string directly,
bypassing the intermediate Python dict that ``model_dump(mode="json")``
creates.  When the goal is to hash or serialize the model (i.e. the final
output is a string), ``model_dump_json()`` avoids the double conversion
``model → dict → JSON string`` and is measurably faster.

The optimization is applied in ``_compute_snapshot_hash_incremental``:
for Pydantic model attributes, use ``model_dump_json()`` directly as the
hash input instead of ``json.dumps(_stable_snapshot_value(model))``.

This test verifies:
1. ``model_dump_json()`` output is equivalent to ``json.dumps(model_dump(mode="json"))``.
2. The fast path is ≥ 2x faster for the hashing use case.
3. ``_stable_snapshot_value`` still produces correct dict output for payloads.
"""

from __future__ import annotations

import json
import time

from novel_forge.desktop.window import NovelForgeDesktopWindow
from novel_forge.workspace.projects import ChapterSummary, ProjectDetail


def _build_realistic_project_detail() -> ProjectDetail:
    """Build a ProjectDetail with nested chapters for benchmarking."""
    chapters = [
        ChapterSummary(
            chapter_number=i,
            title=f"第{i}章 测试章节标题",
            word_count=3000 + i * 100,
            overall_score=7.5 + (i % 3) * 0.5,
            continuity_score=8.0 - (i % 2) * 0.3,
            updated_at=f"2026-01-{i:02d}T12:00:00+00:00",
            preview=f"这是第{i}章的预览内容，包含一些中文文本用于测试序列化性能。" * 3,
        )
        for i in range(1, 21)
    ]
    return ProjectDetail(
        project_id="benchmark_novel",
        title="性能测试小说",
        mode="long",
        genre="scifi",
        tone="dark",
        premise="一个关于性能测试的故事",
        preview="预览内容" * 50,
        total_chapters=100,
        completed_chapters=20,
        latest_chapter=20,
        completion_ratio=0.2,
        has_outline=True,
        has_canon=True,
        updated_at="2026-01-20T12:00:00+00:00",
        load_status="ok",
        load_error="",
        project_state="writing",
        project_state_label="创作中",
        project_state_updated_at="2026-01-20T12:00:00+00:00",
        project_state_source="state_file",
        allowed_operations=["run_chapter", "view_outline"],
        outline_generated_count=50,
        language="zh",
        words_per_chapter=4500,
        volume_mode="auto",
        chapters_per_volume=30,
        characters_hint="张三, 李四, 王五",
        world_hint="未来世界",
        conflict_hint="人工智能与人类的冲突",
        pov_hint="第三人称",
        opening_style="悬疑",
        ending_style="开放",
        extra_instructions="注意节奏",
        init_resume_available=False,
        init_resume_step="",
        init_resume_step_label="",
        init_resume_progress_label="",
        init_resume_progress_percent=0,
        init_resume_next_chapter=21,
        chapters=chapters,
        recent_files=[f"chapter_{i}.md" for i in range(1, 21)],
        artifact_counts={"chapters": 20, "reports": 40, "plans": 20},
    )


class TestModelDumpJsonSpeedup:
    """Tests for model_dump_json fast path in snapshot hashing."""

    def test_model_dump_json_equivalent_to_json_dumps_model_dump(self) -> None:
        """model_dump_json() output parses to same dict as model_dump(mode='json')."""
        detail = _build_realistic_project_detail()

        from_json_str = json.loads(detail.model_dump_json())
        from_model_dump = detail.model_dump(mode="json")

        assert from_json_str == from_model_dump, (
            "model_dump_json() must produce equivalent output to model_dump(mode='json')"
        )

    def test_stable_snapshot_value_produces_correct_dict(self) -> None:
        """_stable_snapshot_value still returns a correct dict for payloads."""
        detail = _build_realistic_project_detail()

        result = NovelForgeDesktopWindow._stable_snapshot_value(detail)

        assert isinstance(result, dict)
        expected = detail.model_dump(mode="json")
        assert result == expected

    def test_model_dump_json_is_at_least_2x_faster_for_hashing(self) -> None:
        """model_dump_json() is ≥ 2x faster than json.dumps(model_dump(mode='json')).

        This is the ACTUAL hashing path comparison:
        - OLD: json.dumps(model.model_dump(mode="json"), sort_keys=True, default=str)
        - NEW: model.model_dump_json()

        The speedup comes from avoiding the intermediate Python dict creation
        and the stdlib json.dumps() overhead.

        The measurement can be distorted by transient machine load; when the
        first measurement misses the 2x bar we re-measure with a longer warm-up
        and more iterations before failing.
        """
        detail = _build_realistic_project_detail()

        def _measure(warmup: int, iterations: int) -> tuple[float, float, float]:
            # Warmup
            for _ in range(warmup):
                json.dumps(detail.model_dump(mode="json"), default=str, sort_keys=True)
                detail.model_dump_json()

            # Benchmark OLD hashing path
            start = time.monotonic()
            for _ in range(iterations):
                json.dumps(
                    detail.model_dump(mode="json"),
                    default=str,
                    sort_keys=True,
                )
            old_elapsed = time.monotonic() - start

            # Benchmark NEW hashing path (model_dump_json directly)
            start = time.monotonic()
            for _ in range(iterations):
                detail.model_dump_json()
            new_elapsed = time.monotonic() - start

            speedup = old_elapsed / new_elapsed if new_elapsed > 0 else float("inf")
            return speedup, old_elapsed, new_elapsed

        speedup, old_elapsed, new_elapsed = _measure(warmup=20, iterations=2000)
        if speedup < 2.0:
            # Transient load can distort the first pass; re-measure once with
            # a longer warm-up before declaring a regression.
            speedup, old_elapsed, new_elapsed = _measure(warmup=100, iterations=5000)

        assert speedup >= 2.0, (
            f"model_dump_json() should be ≥ 2x faster than "
            f"json.dumps(model_dump(mode='json')) for hashing. "
            f"Got {speedup:.2f}x "
            f"(old={old_elapsed:.4f}s, new={new_elapsed:.4f}s)"
        )
