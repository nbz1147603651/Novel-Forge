"""Tests for StoryKernel configuration defaults in Settings."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.config import Settings
from novel_forge.pipeline.long.services.context.story_kernel_context import (
    load_story_kernel_composer,
    story_kernel_db_path,
)
from novel_forge.story_kernel.store import StoryKernelStore


class TestStoryKernelConfig:
    """Verify StoryKernel-related Settings fields have correct defaults."""

    def test_story_kernel_db_path_default_empty(self) -> None:
        s = Settings()
        assert s.story_kernel_db_path == ""

    def test_story_kernel_wal_mode_default_true(self) -> None:
        s = Settings()
        assert s.story_kernel_wal_mode is True

    def test_story_kernel_zvec_enabled_default_false(self) -> None:
        s = Settings()
        # ZVec is optional and must be explicitly enabled after its runtime is
        # installed; the safe default is deterministic SQLite-only operation.
        assert s.story_kernel_zvec_enabled is False

    async def test_empty_db_path_falls_back_to_project_root_and_initializes(
        self,
        tmp_path,
    ) -> None:
        settings = SimpleNamespace(story_kernel_db_path="", story_kernel_wal_mode=False)
        layout = SimpleNamespace(root=tmp_path)
        runner = SimpleNamespace(_settings=settings)
        bundle = SimpleNamespace(project_id="demo-project", layout=layout)

        composer = await load_story_kernel_composer(runner, bundle)

        db_path = tmp_path / "story_kernel.db"
        assert story_kernel_db_path(settings, layout) == str(db_path)
        assert db_path.exists()
        assert "entities" in composer.compose_bridge_input(1)
        store = StoryKernelStore(db_path, wal_mode=False)
        try:
            loaded = await store.load_kernel("demo-project")
        finally:
            await store.close()
        assert loaded.project_id == "demo-project"

    async def test_existing_empty_db_file_is_migrated_before_load(self, tmp_path) -> None:
        db_path = tmp_path / "story_kernel.db"
        db_path.touch()
        settings = SimpleNamespace(
            story_kernel_db_path=str(db_path),
            story_kernel_wal_mode=False,
        )
        runner = SimpleNamespace(_settings=settings)
        bundle = SimpleNamespace(project_id="demo-project", layout=SimpleNamespace(root=tmp_path))

        await load_story_kernel_composer(runner, bundle)

        store = StoryKernelStore(db_path, wal_mode=False)
        try:
            loaded = await store.load_kernel("demo-project")
        finally:
            await store.close()
        assert loaded.project_id == "demo-project"
