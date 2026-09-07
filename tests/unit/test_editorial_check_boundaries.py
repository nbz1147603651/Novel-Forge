from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.schemas.artifacts import ArtifactScope, ChapterSourceSliceArtifact
from novel_forge.pipeline.long.stages.quality_checks_runner import _editorial_boundary_context


def test_editorial_boundary_context_reads_chapter_source_slice() -> None:
    source_slice = ChapterSourceSliceArtifact(
        project_id="test",
        artifact_id="chapter_source_slice:023",
        scope=ArtifactScope(kind="chapter", ids=["23"]),
        payload={
            "chapter_contract": {
                "chapter_number": 23,
                "completion_criteria": ["完成最终剪辑，发布尚未发生"],
                "forbidden_changes": ["不得让沈鹿溪在本章完成最终发布，最终发布留给第24章"],
            },
            "forbidden_reveal_boundaries": [{"rule": "不得让风能路灯在本章提前亮起，属第24章"}],
            "runtime": {
                "chapter_contract": {
                    "chapter_number": 23,
                    "p0_forbidden_boundaries": [{"rule": "不得让风能路灯在本章提前亮起，属第24章"}],
                },
                "world": {},
                "style": {},
                "entities": [],
            },
        },
    )

    context = _editorial_boundary_context(SimpleNamespace(chapter_source_slice=source_slice))

    assert context["chapter_contract"]["chapter_number"] == 23
    assert context["forbidden_reveal_boundaries"] == [
        {"rule": "不得让风能路灯在本章提前亮起，属第24章"}
    ]
