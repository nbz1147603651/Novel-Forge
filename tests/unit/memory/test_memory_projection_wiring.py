"""MemoryContext wiring for read-only story-kernel projection services."""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_forge.core.config import Settings
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.memory.audit_coordinator import AuditCoordinator
from novel_forge.memory.integration import MemoryContext
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.story_kernel.schemas import Entity, PromiseLedger, Relationship, StoryKernel
from novel_forge.story_kernel.store import StoryKernelStore


async def _seed_story_kernel(layout: ProjectLayout, project_id: str) -> None:
    store = StoryKernelStore(layout.story_kernel_db_path, wal_mode=False)
    try:
        await store.init_db()
        kernel = StoryKernel(
            project_id=project_id,
            current_chapter=3,
            entities=[
                Entity(
                    entity_id="char_linyuan",
                    name="林远",
                    aliases=["阿远"],
                    source_chapter=1,
                    last_seen_chapter=3,
                    attributes={"location": "钟楼", "emotional_state": "警觉"},
                ),
                Entity(
                    entity_id="char_shouyeren",
                    name="守夜人",
                    source_chapter=1,
                    last_seen_chapter=3,
                ),
            ],
            relationships=[
                Relationship(
                    relationship_id="rel_mentor",
                    source_entity_id="char_linyuan",
                    target_entity_id="char_shouyeren",
                    relation_type="mentor_student",
                    label="师徒",
                    established_chapter=1,
                    last_shift_chapter=3,
                    shift_summary="守夜人留下线索",
                ),
            ],
            promise_ledger=[
                PromiseLedger(
                    entry_id="prom_watch",
                    description="怀表停在午夜十二点",
                    promise_type="foreshadow",
                    planted_chapter=1,
                    status="planted",
                    owner_entity_ids=["char_linyuan"],
                ),
            ],
        )
        await store.save_kernel(kernel)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_memory_context_wires_projection_services_into_audit_context(
    tmp_path: Path,
) -> None:
    project_id = "projection_wiring"
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    await _seed_story_kernel(layout, project_id)

    router = ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock")
    builder = PromptBuilder()
    settings = Settings(_env_file=None, story_kernel_wal_mode=False)

    ctx = MemoryContext.create_from_settings(
        router=router,
        builder=builder,
        settings=settings,
        project_id=project_id,
        storage=storage,
    )
    try:
        assert ctx.entity_knowledge_service is not None
        assert ctx.relationship_query_service is not None
        assert ctx.foreshadow_reminder is not None

        status = ctx.get_status_summary()
        assert status["entity_knowledge_enabled"] is True
        assert status["relationship_query_enabled"] is True
        assert status["foreshadow_reminder_enabled"] is True

        audit_context = await AuditCoordinator(ctx).prepare_audit_context(
            chapter_number=3,
            active_characters=["林远"],
        )

        assert audit_context.entity_context["林远"]["entity_id"] == "char_linyuan"
        assert audit_context.relationship_context["林远"][0]["relationship_id"] == "rel_mentor"
        assert audit_context.foreshadow_due[0]["entry_id"] == "prom_watch"
        assert "待回收伏笔" in audit_context.get_summary_for_prompt()

        prompt_context = await ctx.aget_memory_context_for_prompt(
            current_chapter=3,
            include_summaries=False,
        )
        assert prompt_context["foreshadow_due"][0]["entry_id"] == "prom_watch"

        character_context = await ctx.get_character_context(
            "林远",
            current_chapter=3,
            include_episodic=False,
            include_motifs=False,
        )
        assert character_context["entity_projection"]["entity_id"] == "char_linyuan"
        assert character_context["relationship_projection"][0]["relationship_id"] == "rel_mentor"
    finally:
        await ctx.shutdown()
