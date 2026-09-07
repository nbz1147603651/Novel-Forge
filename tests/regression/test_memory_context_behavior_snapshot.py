# behavior-snapshot — MemoryContext behavior snapshot tests.
#
# These tests freeze the observable behavior of MemoryContext's key public
# entry points so that the upcoming integration.py refactor (Sprint 2) can be
# verified to preserve behavior.  Each test exercises one real entry point
# and asserts:
#   1. Structural shape (key set + value types) of the return dict
#   2. Canonical hash of the noise-stripped output (deterministic with MockAdapter)
#   3. On-disk artifact fingerprints after persistence operations
#
# If a refactoring legitimately changes behavior, update the expected hash
# after review and note the reason in the commit message.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.memory.integration import MemoryContext
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.prompts.builder import PromptBuilder
from tests.helpers.artifact_diff import (
    canonical_json_hash,
    fingerprint_dir,
)

_SAMPLE_CHAPTER_TEXT = (
    "林远在雾霭小镇的石板路上醒来，怀中的铜质怀表停在午夜十二点。\n"
    "钟楼的方向传来沉闷的响声，他循声而去，遇到了一位神秘的老守夜人。\n"
    "老守夜人告诉他，这座小镇被时间裂缝所困，每隔七天便会重置一次。\n"
    "林远决定找出时间裂缝的入口，打破这个循环。他握紧怀表，踏上了通往废弃图书馆的小径。\n"
    "沿途的景物似曾相识，仿佛他在某个遗忘的过去曾经走过这条路。"
    "信件从怀表中滑落，上面写着一行模糊的字迹：「找到第七根钟摆，时间便会倒流。」"
    * 3
)

_SAMPLE_CREATIVE_REPORT = json.dumps(
    {
        "key_moments": [
            "林远在雾霭小镇醒来",
            "遇到老守夜人",
            "得知时间裂缝的秘密",
        ],
        "emotional_arc": "困惑→好奇→决心",
    },
    ensure_ascii=False,
)


def _make_settings() -> Settings:
    return Settings(_env_file=None)


def _make_router() -> ModelRouter:
    return ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock")


def _make_context(
    tmp_path: Path,
    project_id: str = "snap_proj",
) -> MemoryContext:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    storage.save_json(
        layout.outline_path,
        {
            "project_id": project_id,
            "total_chapters": 3,
            "chapters": [
                {"chapter": 1, "title": "雾霭小镇", "summary": "林远醒来"},
                {"chapter": 2, "title": "时间裂缝", "summary": "进入裂缝"},
                {"chapter": 3, "title": "第七根钟摆", "summary": "打破循环"},
            ],
        },
    )
    storage.save_json(
        layout.bible_path,
        {
            "project_id": project_id,
            "premise": "一个关于时间旅行的故事",
            "themes": ["时间", "记忆", "牺牲"],
            "world_rules": [],
        },
    )
    settings = _make_settings()
    return MemoryContext.create_from_settings(
        router=_make_router(),
        builder=PromptBuilder(),
        settings=settings,
        project_id=project_id,
        storage=storage,
    )


def _sorted_keys(d: dict[str, Any]) -> list[str]:
    return sorted(d.keys())


class TestMemoryContextCreateFromSettings:
    # behavior-snapshot: create_from_settings entry point

    def test_create_returns_memory_context_with_expected_services(
        self, tmp_path: Path
    ) -> None:
        ctx = _make_context(tmp_path)
        try:
            assert isinstance(ctx, MemoryContext)
            assert ctx.project_id == "snap_proj"
            assert ctx.settings is not None
            assert ctx._router is not None
            assert ctx._builder is not None
            assert ctx._storage is not None
            assert ctx._episodic_memory is not None
            assert ctx._motif_tracker is not None
            assert ctx._summary_service is not None
            assert ctx._critic_agent is not None
            assert ctx._compression_service is not None
        finally:
            import asyncio

            asyncio.run(ctx.shutdown())


class TestMemoryContextGetStatusForUI:
    # behavior-snapshot: get_memory_status_for_ui entry point

    def test_status_shape_before_indexing(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        try:
            status = ctx.get_memory_status_for_ui()
            expected_keys = {
                "indexed_chapters",
                "motifs",
                "motif_suggestions",
                "repetition_warnings",
                "memory_module_status",
            }
            assert expected_keys.issubset(set(status.keys())), (
                f"Missing keys: {expected_keys - set(status.keys())}"
            )
            assert isinstance(status["indexed_chapters"], (list, int))
            assert isinstance(status["motifs"], list)
            assert isinstance(status["motif_suggestions"], list)
            assert isinstance(status["repetition_warnings"], list)
            assert status["motifs"] == []
            assert status["motif_suggestions"] == []
            status_hash = canonical_json_hash(status)
            assert status_hash == _EXPECTED_STATUS_HASH_BEFORE, (
                f"Status shape changed: {status_hash}"
            )
        finally:
            import asyncio

            asyncio.run(ctx.shutdown())

    async def test_status_shape_after_indexing(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        try:
            ctx.index_chapter(
                chapter_number=1,
                text=_SAMPLE_CHAPTER_TEXT,
                creative_report_text=_SAMPLE_CREATIVE_REPORT,
                async_summarize=False,
                async_motifs=False,
            )
            await ctx.flush_pending_tasks(timeout_s=5.0)
            status = ctx.get_memory_status_for_ui()
            assert isinstance(status["indexed_chapters"], (list, int))
            indexed = status["indexed_chapters"]
            if isinstance(indexed, list):
                assert len(indexed) >= 1
                assert indexed[0] == 1
            else:
                assert indexed >= 1
            status_hash = canonical_json_hash(status)
            assert status_hash == _EXPECTED_STATUS_HASH_AFTER, (
                f"Status shape changed after indexing: {status_hash}"
            )
        finally:
            await ctx.shutdown()


class TestMemoryContextGetMemoryContextForPrompt:
    # behavior-snapshot: get_memory_context_for_prompt entry point

    def test_prompt_context_shape(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        try:
            ctx.index_chapter(
                chapter_number=1,
                text=_SAMPLE_CHAPTER_TEXT,
                creative_report_text=_SAMPLE_CREATIVE_REPORT,
                async_summarize=False,
                async_motifs=False,
            )
            import asyncio

            asyncio.run(ctx.flush_pending_tasks(timeout_s=5.0))
            context = ctx.get_memory_context_for_prompt(current_chapter=2)
            assert isinstance(context, dict)
            context_hash = canonical_json_hash(context)
            assert context_hash == _EXPECTED_PROMPT_CONTEXT_HASH, (
                f"Prompt context shape changed: {context_hash}"
            )
        finally:
            import asyncio

            asyncio.run(ctx.shutdown())

    async def test_async_prompt_context_shape(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        try:
            ctx.index_chapter(
                chapter_number=1,
                text=_SAMPLE_CHAPTER_TEXT,
                creative_report_text=_SAMPLE_CREATIVE_REPORT,
                async_summarize=False,
                async_motifs=False,
            )
            await ctx.flush_pending_tasks(timeout_s=5.0)
            context = await ctx.aget_memory_context_for_prompt(current_chapter=2)
            assert isinstance(context, dict)
            context_hash = canonical_json_hash(context)
            assert context_hash == _EXPECTED_ASYNC_PROMPT_CONTEXT_HASH, (
                f"Async prompt context shape changed: {context_hash}"
            )
        finally:
            await ctx.shutdown()


class TestMemoryContextIndexAndFinalize:
    # behavior-snapshot: index_chapter + finalize_chapter_memory entry points

    async def test_finalize_chapter_memory_stats_shape(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        try:
            stats = await ctx.finalize_chapter_memory(
                chapter_number=1,
                text=_SAMPLE_CHAPTER_TEXT,
                creative_report_text=_SAMPLE_CREATIVE_REPORT,
            )
            assert isinstance(stats, dict)
            expected_keys = {
                "chapter",
                "tasks_run",
                "tasks_ok",
                "tasks_failed",
                "motifs_extracted",
                "summary_generated",
                "expression_observations",
                "saved",
            }
            assert expected_keys.issubset(set(stats.keys())), (
                f"Missing keys: {expected_keys - set(stats.keys())}"
            )
            assert stats["chapter"] == 1
            assert isinstance(stats["tasks_run"], list)
            assert isinstance(stats["tasks_ok"], list)
            assert isinstance(stats["tasks_failed"], list)
            assert isinstance(stats["saved"], bool)
            stats_hash = canonical_json_hash(stats)
            assert stats_hash == _EXPECTED_FINALIZE_STATS_HASH, (
                f"Finalize stats shape changed: {stats_hash}"
            )
        finally:
            await ctx.shutdown()

    async def test_artifact_fingerprint_after_finalize(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        project_dir = tmp_path / "snap_proj"
        try:
            await ctx.finalize_chapter_memory(
                chapter_number=1,
                text=_SAMPLE_CHAPTER_TEXT,
                creative_report_text=_SAMPLE_CREATIVE_REPORT,
            )
            memory_dir = project_dir / "memory"
            if memory_dir.exists():
                fp = fingerprint_dir(memory_dir)
                assert fp, "Memory directory should have fingerprintable files"
                fp_hash = canonical_json_hash(fp)
                assert fp_hash == _EXPECTED_MEMORY_FP_HASH, (
                    f"Memory artifact fingerprint changed: {fp_hash}"
                )
        finally:
            await ctx.shutdown()


class TestMemoryContextSearchRelevantHistory:
    # behavior-snapshot: search_relevant_history entry point

    async def test_search_returns_list(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        try:
            await ctx.finalize_chapter_memory(
                chapter_number=1,
                text=_SAMPLE_CHAPTER_TEXT,
                creative_report_text=_SAMPLE_CREATIVE_REPORT,
            )
            results = await ctx.search_relevant_history(
                query="林远 怀表 时间裂缝",
                current_chapter=2,
                top_k=5,
            )
            assert isinstance(results, list)
        finally:
            await ctx.shutdown()


class TestMemoryContextWarmStartAndShutdown:
    # behavior-snapshot: warm_start_from_init_artifacts + shutdown entry points

    async def test_warm_start_returns_stats_dict(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        try:
            stats = await ctx.warm_start_from_init_artifacts()
            assert isinstance(stats, dict)
            expected_keys = {
                "outline_episodic_loaded",
                "motif_warmup",
                "saved",
                "errors",
            }
            assert expected_keys.issubset(set(stats.keys())), (
                f"Missing keys: {expected_keys - set(stats.keys())}"
            )
            assert isinstance(stats["errors"], list)
        finally:
            await ctx.shutdown()

    async def test_shutdown_is_clean(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        await ctx.finalize_chapter_memory(
            chapter_number=1,
            text=_SAMPLE_CHAPTER_TEXT,
            creative_report_text=_SAMPLE_CREATIVE_REPORT,
        )
        await ctx.shutdown()
        await ctx.aclose()


class TestMemoryContextSaveLoadRoundtrip:
    # behavior-snapshot: save_to_disk + load_from_disk persistence entry points

    def test_save_load_preserves_indexed_chapter(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        try:
            ctx.index_chapter(
                chapter_number=1,
                text=_SAMPLE_CHAPTER_TEXT,
                creative_report_text=_SAMPLE_CREATIVE_REPORT,
                async_summarize=False,
                async_motifs=False,
            )
            import asyncio

            asyncio.run(ctx.flush_pending_tasks(timeout_s=5.0))
            saved = ctx.save_to_disk()
            assert saved is True

            ctx2 = _make_context(tmp_path)
            loaded = ctx2.load_from_disk()
            assert loaded is True
            assert ctx2._last_indexed_chapter == ctx._last_indexed_chapter
            assert ctx2._chapter_content_hash == ctx._chapter_content_hash
        finally:
            import asyncio

            asyncio.run(ctx.shutdown())


# ── Expected canonical hashes (filled after first baseline run) ──────────
# Run with NOVEL_FORGE_SNAPSHOT_UPDATE=1 to regenerate, then paste here.

_EXPECTED_STATUS_HASH_BEFORE = "f3086d077481c9e9be72e8d28692ff04fc45af57cc4026d00705765e955178d5"
_EXPECTED_STATUS_HASH_AFTER = "e1f3e88f9aa412a38c7a046f13c51974f165c2931d2209b7a4e54a30f1771d7a"
_EXPECTED_PROMPT_CONTEXT_HASH = "2f0a13755fe7fbb6544967907bca25f74b5ae1235e67ed695962e6f92dbaa492"
_EXPECTED_ASYNC_PROMPT_CONTEXT_HASH = "2f0a13755fe7fbb6544967907bca25f74b5ae1235e67ed695962e6f92dbaa492"
_EXPECTED_FINALIZE_STATS_HASH = "2961fb661026ace9bc939734c2d7bef65be3700cc2def73d6158f6438e7efbd5"
_EXPECTED_MEMORY_FP_HASH = "306507fe3418d96d6922abb042ed533bd817d3f199879ad02a883bfd3eb67170"
