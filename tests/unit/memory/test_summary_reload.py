"""Tests for summary reload in MemoryContext.load_from_disk.

Verifies that load_from_disk calls asyncio.run(self._summary_service.load_summaries())
when invoked from a sync context, and skips when already in an async context.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.memory.integration import MemoryContext
from novel_forge.memory.summary import MultiGranularitySummaryService
from novel_forge.persistence.filesystem import FileSystemStorage


def _make_mock_builder() -> MagicMock:
    builder = MagicMock()
    builder.build = MagicMock(
        return_value=ModelRequest(
            task_type=TaskType.CONTEXT_COMPRESS,
            messages=[{"role": "user", "content": "test"}],
        )
    )
    return builder


def _make_mock_router() -> MagicMock:
    router = MagicMock()
    router.route = AsyncMock(return_value=ModelResponse(content="ok"))
    return router


def _prepare_project_disk(tmp_path: Path, project_id: str) -> FileSystemStorage:
    """Create storage with summaries.json and project_memory.json."""
    storage = FileSystemStorage(tmp_path)
    memory_dir = tmp_path / project_id / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    summaries_data = {
        "chapter_summaries": {
            "1": "第一章概要：主角觉醒。",
            "2": "第二章概要：遇到同伴。",
        },
        "volume_summaries": {
            "1": "第一卷概要：觉醒篇。",
        },
        "arc_summaries": {
            "arc_intro": "序章弧概要。",
        },
        "scene_summaries": {
            "1_1": "第一章第一场概要。",
        },
    }
    storage.save_json(memory_dir / "summaries.json", summaries_data)
    storage.save_json(
        memory_dir / "project_memory.json",
        {"last_indexed_chapter": 2, "summary_cache": {}, "chapter_content_hash": {}},
    )
    return storage


class TestSummaryReloadOnLoadFromDisk:
    """Verify load_from_disk reloads summaries from summaries.json."""

    def test_sync_context_loads_summaries_into_service(
        self,
        tmp_path: Path,
    ) -> None:
        """In sync context (no running loop), summaries are loaded from summaries.json."""
        project_id = "test_summary_reload"
        storage = _prepare_project_disk(tmp_path, project_id)

        router = _make_mock_router()
        builder = _make_mock_builder()
        summary_service = MultiGranularitySummaryService(router=router, builder=builder)
        summary_service.set_storage(storage, project_id)

        ctx = MemoryContext(
            _project_id=project_id,
            _storage=storage,
            _router=router,
            _builder=builder,
            _summary_service=summary_service,
        )

        assert summary_service._chapter_summaries == {}
        assert summary_service._volume_summaries == {}

        result = ctx.load_from_disk()

        assert result is True

        assert summary_service._chapter_summaries == {
            1: "第一章概要：主角觉醒。",
            2: "第二章概要：遇到同伴。",
        }
        assert summary_service._volume_summaries == {1: "第一卷概要：觉醒篇。"}
        assert summary_service._arc_summaries == {"arc_intro": "序章弧概要。"}
        assert summary_service._scene_summaries == {(1, 1): "第一章第一场概要。"}
        assert ctx._summary_service_disk_load_attempted is True

    def test_no_summary_service_skips_reload(
        self,
        tmp_path: Path,
    ) -> None:
        """When _summary_service is None, load_from_disk still succeeds."""
        project_id = "test_no_summary_service"
        storage = _prepare_project_disk(tmp_path, project_id)

        ctx = MemoryContext(
            _project_id=project_id,
            _storage=storage,
            _router=None,
            _builder=None,
            _summary_service=None,
        )

        result = ctx.load_from_disk()
        assert result is True

    def test_load_summaries_failure_logged_not_raised(
        self,
        tmp_path: Path,
    ) -> None:
        """If load_summaries raises, the exception is logged and load_from_disk still returns True."""
        project_id = "test_summary_failure"
        storage = _prepare_project_disk(tmp_path, project_id)

        mock_summary_service = MagicMock()
        mock_summary_service.load_summaries = AsyncMock(
            side_effect=OSError("disk read error")
        )

        ctx = MemoryContext(
            _project_id=project_id,
            _storage=storage,
            _router=None,
            _builder=None,
            _summary_service=mock_summary_service,
        )

        result = ctx.load_from_disk()
        assert result is True
        mock_summary_service.load_summaries.assert_awaited_once()
        assert ctx._summary_service_disk_load_attempted is False

    def test_load_summaries_failure_can_be_retried_by_async_loader(
        self,
        tmp_path: Path,
    ) -> None:
        """A failed sync reload must not suppress the async retry path."""
        project_id = "test_summary_failure_retry"
        storage = _prepare_project_disk(tmp_path, project_id)

        mock_summary_service = MagicMock()
        mock_summary_service.load_summaries = AsyncMock(
            side_effect=[OSError("disk read error"), True]
        )

        ctx = MemoryContext(
            _project_id=project_id,
            _storage=storage,
            _router=None,
            _builder=None,
            _summary_service=mock_summary_service,
        )

        result = ctx.load_from_disk()
        assert result is True
        assert ctx._summary_service_disk_load_attempted is False

        import asyncio

        retry_stats = asyncio.run(ctx.ensure_async_disk_state_loaded())

        assert retry_stats == {
            "summary_attempted": True,
            "summary_loaded": True,
            "narrative_evidence_bootstrapped": False,
        }
        assert mock_summary_service.load_summaries.await_count == 2
        assert ctx._summary_service_disk_load_attempted is True

    @pytest.mark.asyncio
    async def test_async_context_skips_summary_reload(
        self,
        tmp_path: Path,
    ) -> None:
        """When called from async context, summary reload is skipped (caller handles it)."""
        project_id = "test_async_skip"
        storage = _prepare_project_disk(tmp_path, project_id)

        router = _make_mock_router()
        builder = _make_mock_builder()
        summary_service = MultiGranularitySummaryService(router=router, builder=builder)
        summary_service.set_storage(storage, project_id)

        ctx = MemoryContext(
            _project_id=project_id,
            _storage=storage,
            _router=router,
            _builder=builder,
            _summary_service=summary_service,
        )

        result = ctx.load_from_disk()

        assert result is True
        assert summary_service._chapter_summaries == {}
        assert ctx._summary_service_disk_load_attempted is False

        await summary_service.load_summaries()
        assert summary_service._chapter_summaries == {
            1: "第一章概要：主角觉醒。",
            2: "第二章概要：遇到同伴。",
        }


class TestCreateFromSettingsWiring:
    """Verify create_from_settings wires storage to summary_service."""

    def test_create_from_settings_wires_storage_to_summary_service(
        self,
        tmp_path: Path,
    ) -> None:
        """create_from_settings calls set_storage on summary_service before load_from_disk."""
        from novel_forge.core.config import Settings

        project_id = "test_wiring_project"
        storage = FileSystemStorage(tmp_path)
        memory_dir = tmp_path / project_id / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        storage.save_json(
            memory_dir / "project_memory.json",
            {"last_indexed_chapter": 0, "summary_cache": {}, "chapter_content_hash": {}},
        )

        router = _make_mock_router()
        builder = _make_mock_builder()
        settings = Settings(_env_file=None)

        ctx = MemoryContext.create_from_settings(
            router=router,
            builder=builder,
            settings=settings,
            project_id=project_id,
            storage=storage,
        )

        assert ctx.summary_service is not None
        assert ctx.summary_service._storage is storage
        assert ctx.summary_service._project_id == project_id

    def test_create_from_settings_no_storage_skips_wiring(
        self,
        tmp_path: Path,
    ) -> None:
        """Without storage, summary_service._storage remains None."""
        from novel_forge.core.config import Settings

        router = _make_mock_router()
        builder = _make_mock_builder()
        settings = Settings(_env_file=None)

        ctx = MemoryContext.create_from_settings(
            router=router,
            builder=builder,
            settings=settings,
            project_id="no_storage_project",
            storage=None,
        )

        if ctx.summary_service is not None:
            assert ctx.summary_service._storage is None

    def test_wiring_enables_save_and_load_roundtrip(
        self,
        tmp_path: Path,
    ) -> None:
        """After create_from_settings, save_summaries and load_summaries work end-to-end."""
        import asyncio

        from novel_forge.core.config import Settings

        project_id = "test_roundtrip_project"
        storage = FileSystemStorage(tmp_path)
        memory_dir = tmp_path / project_id / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        storage.save_json(
            memory_dir / "project_memory.json",
            {"last_indexed_chapter": 0, "summary_cache": {}, "chapter_content_hash": {}},
        )

        router = _make_mock_router()
        builder = _make_mock_builder()
        settings = Settings(_env_file=None)

        ctx = MemoryContext.create_from_settings(
            router=router,
            builder=builder,
            settings=settings,
            project_id=project_id,
            storage=storage,
        )

        ctx.summary_service._chapter_summaries[1] = "第一章：觉醒"
        ctx.summary_service._chapter_summaries[2] = "第二章：相遇"

        asyncio.run(ctx.summary_service.save_summaries())

        summaries_path = memory_dir / "summaries.json"
        assert summaries_path.exists()

        new_service = MultiGranularitySummaryService(router=router, builder=builder)
        new_service.set_storage(storage, project_id)
        assert new_service._chapter_summaries == {}

        asyncio.run(new_service.load_summaries())
        assert new_service._chapter_summaries == {1: "第一章：觉醒", 2: "第二章：相遇"}
