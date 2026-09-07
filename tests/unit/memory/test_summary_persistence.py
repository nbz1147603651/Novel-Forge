"""Tests for MultiGranularitySummaryService persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from novel_forge.memory.summary import MultiGranularitySummaryService

if TYPE_CHECKING:
    from novel_forge.gateway.router import ModelRouter
    from novel_forge.persistence.filesystem import FileSystemStorage
    from novel_forge.prompts.builder import PromptBuilder


@pytest.fixture
def service(
    router: "ModelRouter",
    builder: "PromptBuilder",
) -> MultiGranularitySummaryService:
    return MultiGranularitySummaryService(router=router, builder=builder)


class TestSaveAndLoadSummaries:
    async def test_save_and_load_summaries(
        self,
        service: MultiGranularitySummaryService,
        tmp_storage: "FileSystemStorage",
    ) -> None:
        service._scene_summaries[(1, 0)] = "Scene 1-0 summary"
        service._scene_summaries[(2, 1)] = "Scene 2-1 summary"
        service._chapter_summaries[1] = "Chapter 1 summary"
        service._chapter_summaries[3] = "Chapter 3 summary"
        service._volume_summaries[1] = "Volume 1 summary"
        service._arc_summaries["arc_one"] = "Arc One summary"

        project_id = "test_project"
        service.set_storage(tmp_storage, project_id)

        result = await service.save_summaries()
        assert result is True

        new_service = MultiGranularitySummaryService(
            router=service._router,
            builder=service._builder,
        )
        new_service.set_storage(tmp_storage, project_id)

        load_result = await new_service.load_summaries()
        assert load_result is True

        assert new_service._scene_summaries[(1, 0)] == "Scene 1-0 summary"
        assert new_service._scene_summaries[(2, 1)] == "Scene 2-1 summary"
        assert new_service._chapter_summaries[1] == "Chapter 1 summary"
        assert new_service._chapter_summaries[3] == "Chapter 3 summary"
        assert new_service._volume_summaries[1] == "Volume 1 summary"
        assert new_service._arc_summaries["arc_one"] == "Arc One summary"


class TestLoadSummariesFileNotFound:
    async def test_load_summaries_file_not_found(
        self,
        service: MultiGranularitySummaryService,
        tmp_storage: "FileSystemStorage",
    ) -> None:
        project_id = "nonexistent_project"
        service.set_storage(tmp_storage, project_id)

        result = await service.load_summaries()
        assert result is False


class TestSaveSummariesNoStorage:
    async def test_save_summaries_no_storage(
        self,
        service: MultiGranularitySummaryService,
    ) -> None:
        service._chapter_summaries[1] = "Should not save"
        result = await service.save_summaries()
        assert result is False