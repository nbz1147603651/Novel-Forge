"""Tests for StateAdjudicationStep writing to StoryKernel.

Verifies that the adjudication pipeline can write accepted state deltas
to a StoryKernelStore in addition to (or instead of) NarrativeStateStore.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.narrative_state.schemas import (
    AdjudicationDecision,
    CandidateStateDelta,
    EvidenceSpan,
    FinalStateAdjudication,
    NarrativeAdjudicationReport,
    StateLedgerEntry,
)
from novel_forge.narrative_state.store import NarrativeStateStore
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.pipeline.long.stages.finalize import (
    _write_chapter_outcome_to_story_kernel,
)
from novel_forge.pipeline.steps.state_adjudication_step import (
    apply_adjudication_to_kernel,
    persist_adjudicated_state_ledger,
    run_narrative_state_adjudication,
)
from novel_forge.story_kernel.schemas import (
    Entity,
    TimelineAnchor,
)
from novel_forge.story_kernel.store import StoryKernelStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
async def kernel_store() -> AsyncIterator[StoryKernelStore]:
    """In-memory StoryKernelStore with initialized DB."""
    store = StoryKernelStore.in_memory()
    await store.init_db()
    await store.create_kernel("test-project")
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture()
def sample_report() -> NarrativeAdjudicationReport:
    """A minimal adjudication report with one accepted event candidate."""
    candidate = CandidateStateDelta(
        candidate_id="cand_001",
        chapter_number=1,
        delta_type="event",
        summary="林远发现时间裂缝入口",
        entity_ids=["lin_yuan"],
        proposed_delta={
            "source": "chapter_contract",
            "delta_type": "event",
            "scope": "contract_progression",
            "state_path": "contract.required_events.1.01",
            "summary": "林远发现时间裂缝入口",
            "value": "追查时间裂缝",
        },
        evidence=[
            EvidenceSpan(
                quote="林远继续追查时间裂缝",
                paragraph_index=0,
                start_offset=0,
                end_offset=10,
                found=True,
                chapter_number=1,
            ),
        ],
    )
    decision = AdjudicationDecision(
        candidate_id="cand_001",
        verdict="accept",
        severity="low",
        confidence=0.9,
        rationale="证据确凿，事件已在正文出现。",
    )
    final = FinalStateAdjudication(
        chapter_number=1,
        verdict="accept",
        severity="low",
        confidence=0.9,
        accepted_candidate_ids=["cand_001"],
        rejected_candidate_ids=[],
        pending_candidate_ids=[],
        repair_candidate_ids=[],
        should_block_archive=False,
        summary="所有候选均通过裁决。",
        state_updates=[
            {
                "candidate_id": "cand_001",
                "scope": "contract_progression",
                "state_path": "contract.required_events.1.01",
                "value": "追查时间裂缝",
                "summary": "林远发现时间裂缝入口",
            },
        ],
    )
    return NarrativeAdjudicationReport(
        chapter_number=1,
        candidates=[candidate],
        omitted_candidates=[],
        decisions=[decision],
        final_adjudication=final,
        ledger_entries=[
            StateLedgerEntry(
                entry_id="state_1_cand_001_accept",
                chapter_number=1,
                candidate_id="cand_001",
                delta_type="event",
                summary="林远发现时间裂缝入口",
                state_update={
                    "candidate_id": "cand_001",
                    "scope": "contract_progression",
                    "state_path": "contract.required_events.1.01",
                    "value": "追查时间裂缝",
                    "summary": "林远发现时间裂缝入口",
                },
                decision=decision,
                evidence=[candidate.evidence[0]],
            ),
        ],
        source_text_hash="abc123",
    )


@pytest.fixture()
def knowledge_report() -> NarrativeAdjudicationReport:
    """Report with a knowledge-type accepted delta."""
    candidate = CandidateStateDelta(
        candidate_id="cand_know_001",
        chapter_number=2,
        delta_type="knowledge",
        summary="林远得知裂缝不是自然现象",
        entity_ids=["lin_yuan"],
        proposed_delta={
            "delta_type": "knowledge",
            "summary": "林远得知裂缝不是自然现象",
            "entity_ids": ["lin_yuan"],
            "value": "裂缝不是自然现象",
        },
        evidence=[
            EvidenceSpan(
                quote="裂缝不是自然现象",
                paragraph_index=0,
                start_offset=0,
                end_offset=8,
                found=True,
                chapter_number=2,
            ),
        ],
    )
    decision = AdjudicationDecision(
        candidate_id="cand_know_001",
        verdict="accept",
        severity="medium",
        confidence=0.85,
        rationale="知识增量已在正文确认。",
    )
    final = FinalStateAdjudication(
        chapter_number=2,
        verdict="accept",
        severity="medium",
        confidence=0.85,
        accepted_candidate_ids=["cand_know_001"],
        rejected_candidate_ids=[],
        pending_candidate_ids=[],
        repair_candidate_ids=[],
        should_block_archive=False,
        summary="知识增量已裁决通过。",
        state_updates=[
            {
                "candidate_id": "cand_know_001",
                "scope": "main_plot",
                "state_path": "knowledge.lin_yuan.2.01",
                "value": "裂缝不是自然现象",
                "summary": "林远得知裂缝不是自然现象",
                "entity_ids": ["lin_yuan"],
            },
        ],
    )
    return NarrativeAdjudicationReport(
        chapter_number=2,
        candidates=[candidate],
        omitted_candidates=[],
        decisions=[decision],
        final_adjudication=final,
        ledger_entries=[
            StateLedgerEntry(
                entry_id="state_2_cand_know_001_accept",
                chapter_number=2,
                candidate_id="cand_know_001",
                delta_type="knowledge",
                summary="林远得知裂缝不是自然现象",
                state_update={
                    "candidate_id": "cand_know_001",
                    "scope": "main_plot",
                    "state_path": "knowledge.lin_yuan.2.01",
                    "value": "裂缝不是自然现象",
                    "summary": "林远得知裂缝不是自然现象",
                    "entity_ids": ["lin_yuan"],
                },
                decision=decision,
                evidence=[candidate.evidence[0]],
            ),
        ],
        source_text_hash="def456",
    )


@pytest.fixture()
def relationship_report() -> NarrativeAdjudicationReport:
    """Report with a relationship-type accepted delta."""
    candidate = CandidateStateDelta(
        candidate_id="cand_rel_001",
        chapter_number=3,
        delta_type="relationship",
        summary="林远与老守夜人建立师徒关系",
        entity_ids=["lin_yuan", "old_watchman"],
        proposed_delta={
            "delta_type": "relationship",
            "summary": "林远与老守夜人建立师徒关系",
            "entity_ids": ["lin_yuan", "old_watchman"],
            "relationship_pair": ["lin_yuan", "old_watchman"],
            "value": "mentor_student",
        },
        evidence=[
            EvidenceSpan(
                quote="老守夜人收林远为徒",
                paragraph_index=0,
                start_offset=0,
                end_offset=9,
                found=True,
                chapter_number=3,
            ),
        ],
    )
    decision = AdjudicationDecision(
        candidate_id="cand_rel_001",
        verdict="accept",
        severity="low",
        confidence=0.95,
        rationale="关系变化已在正文确认。",
    )
    final = FinalStateAdjudication(
        chapter_number=3,
        verdict="accept",
        severity="low",
        confidence=0.95,
        accepted_candidate_ids=["cand_rel_001"],
        rejected_candidate_ids=[],
        pending_candidate_ids=[],
        repair_candidate_ids=[],
        should_block_archive=False,
        summary="关系变化已裁决通过。",
        state_updates=[
            {
                "candidate_id": "cand_rel_001",
                "scope": "relationship_carry_forward",
                "state_path": "relationship.lin_yuan_old_watchman.3.01",
                "value": "mentor_student",
                "summary": "林远与老守夜人建立师徒关系",
                "relationship_pair": ["lin_yuan", "old_watchman"],
            },
        ],
    )
    return NarrativeAdjudicationReport(
        chapter_number=3,
        candidates=[candidate],
        omitted_candidates=[],
        decisions=[decision],
        final_adjudication=final,
        ledger_entries=[
            StateLedgerEntry(
                entry_id="state_3_cand_rel_001_accept",
                chapter_number=3,
                candidate_id="cand_rel_001",
                delta_type="relationship",
                summary="林远与老守夜人建立师徒关系",
                state_update={
                    "candidate_id": "cand_rel_001",
                    "scope": "relationship_carry_forward",
                    "state_path": "relationship.lin_yuan_old_watchman.3.01",
                    "value": "mentor_student",
                    "summary": "林远与老守夜人建立师徒关系",
                    "relationship_pair": ["lin_yuan", "old_watchman"],
                },
                decision=decision,
                evidence=[candidate.evidence[0]],
            ),
        ],
        source_text_hash="ghi789",
    )


# ---------------------------------------------------------------------------
# Tests: apply_adjudication_to_kernel
# ---------------------------------------------------------------------------


class TestApplyAdjudicationToKernel:
    async def test_event_delta_adds_timeline_anchor(
        self,
        kernel_store: StoryKernelStore,
        sample_report: NarrativeAdjudicationReport,
    ) -> None:
        kernel = await kernel_store.load_kernel("test-project")
        updated = apply_adjudication_to_kernel(kernel, sample_report)
        assert len(updated.timeline) == 1
        assert updated.timeline[0].chapter == 1
        assert "林远发现时间裂缝入口" in updated.timeline[0].event

    async def test_event_delta_updates_chapter_summary(
        self,
        kernel_store: StoryKernelStore,
        sample_report: NarrativeAdjudicationReport,
    ) -> None:
        """An accepted event candidate should update chapter_summaries."""
        kernel = await kernel_store.load_kernel("test-project")
        updated = apply_adjudication_to_kernel(kernel, sample_report)
        assert 1 in updated.chapter_summaries
        assert "林远发现时间裂缝入口" in updated.chapter_summaries[1]

    async def test_knowledge_delta_adds_knowledge_ledger_entry(
        self,
        kernel_store: StoryKernelStore,
        knowledge_report: NarrativeAdjudicationReport,
    ) -> None:
        """An accepted knowledge candidate should add a KnowledgeLedger entry."""
        kernel = await kernel_store.load_kernel("test-project")
        # Ensure the entity exists
        kernel.entities.append(
            Entity(entity_id="lin_yuan", name="林远", entity_type="character")
        )
        updated = apply_adjudication_to_kernel(kernel, knowledge_report)
        assert len(updated.knowledge_ledger) >= 1
        entry = updated.knowledge_ledger[-1]
        assert "裂缝不是自然现象" in entry.fact
        assert entry.entity_id == "lin_yuan"
        assert entry.source_chapter == 2

    async def test_knowledge_delta_preserves_adjudicated_knowledge_type(
        self,
        kernel_store: StoryKernelStore,
        knowledge_report: NarrativeAdjudicationReport,
    ) -> None:
        """Accepted knowledge deltas should preserve known/suspected/misbelief semantics."""
        kernel = await kernel_store.load_kernel("test-project")
        kernel.entities.append(
            Entity(entity_id="lin_yuan", name="林远", entity_type="character")
        )
        report = knowledge_report.model_copy(deep=True)
        report.ledger_entries[0].state_update["knowledge_type"] = "misbelief"

        updated = apply_adjudication_to_kernel(kernel, report)

        assert updated.knowledge_ledger[-1].knowledge_type == "misbelief"

    async def test_relationship_delta_adds_relationship_entry(
        self,
        kernel_store: StoryKernelStore,
        relationship_report: NarrativeAdjudicationReport,
    ) -> None:
        """An accepted relationship candidate should add a Relationship entry."""
        kernel = await kernel_store.load_kernel("test-project")
        # Ensure entities exist
        kernel.entities.extend([
            Entity(entity_id="lin_yuan", name="林远", entity_type="character"),
            Entity(entity_id="old_watchman", name="老守夜人", entity_type="character"),
        ])
        updated = apply_adjudication_to_kernel(kernel, relationship_report)
        assert len(updated.relationships) >= 1
        rel = updated.relationships[-1]
        assert rel.source_entity_id == "lin_yuan"
        assert rel.target_entity_id == "old_watchman"
        assert rel.relation_type == "mentor_student"

    async def test_no_writes_when_final_blocks_archive(
        self,
        kernel_store: StoryKernelStore,
    ) -> None:
        """No kernel writes should happen when should_block_archive=True."""
        report = NarrativeAdjudicationReport(
            chapter_number=1,
            candidates=[],
            omitted_candidates=[],
            decisions=[],
            final_adjudication=FinalStateAdjudication(
                chapter_number=1,
                verdict="needs_repair",
                severity="critical",
                confidence=0.8,
                accepted_candidate_ids=[],
                rejected_candidate_ids=[],
                pending_candidate_ids=[],
                repair_candidate_ids=["cand_001"],
                should_block_archive=True,
                summary="需要修复。",
            ),
            ledger_entries=[],
            source_text_hash="blocked",
        )
        kernel = await kernel_store.load_kernel("test-project")
        updated = apply_adjudication_to_kernel(kernel, report)
        # Should return kernel unchanged
        assert updated.timeline == kernel.timeline
        assert updated.knowledge_ledger == kernel.knowledge_ledger
        assert updated.relationships == kernel.relationships

    async def test_no_writes_when_repair_requested(
        self,
        kernel_store: StoryKernelStore,
    ) -> None:
        """No kernel writes should happen when verdict=needs_repair."""
        report = NarrativeAdjudicationReport(
            chapter_number=1,
            candidates=[],
            omitted_candidates=[],
            decisions=[],
            final_adjudication=FinalStateAdjudication(
                chapter_number=1,
                verdict="needs_repair",
                severity="high",
                confidence=0.7,
                accepted_candidate_ids=[],
                rejected_candidate_ids=[],
                pending_candidate_ids=[],
                repair_candidate_ids=[],
                repair_issues=[{"candidate_id": "cand_001", "issue": "证据不足"}],
                should_block_archive=False,
                summary="需要修复。",
            ),
            ledger_entries=[],
            source_text_hash="repair",
        )
        kernel = await kernel_store.load_kernel("test-project")
        updated = apply_adjudication_to_kernel(kernel, report)
        assert updated.timeline == kernel.timeline

    async def test_promise_delta_adds_promise_ledger_entry(
        self,
        kernel_store: StoryKernelStore,
    ) -> None:
        """An accepted promise candidate should add a PromiseLedger entry."""
        candidate = CandidateStateDelta(
            candidate_id="cand_prom_001",
            chapter_number=4,
            delta_type="promise",
            summary="怀表停在午夜十二点暗示时间异常",
            entity_ids=[],
            proposed_delta={
                "delta_type": "promise",
                "summary": "怀表停在午夜十二点暗示时间异常",
                "value": "foreshadow",
            },
            evidence=[
                EvidenceSpan(
                    quote="怀表停在午夜十二点",
                    paragraph_index=0,
                    start_offset=0,
                    end_offset=9,
                    found=True,
                    chapter_number=4,
                ),
            ],
        )
        report = NarrativeAdjudicationReport(
            chapter_number=4,
            candidates=[candidate],
            omitted_candidates=[],
            decisions=[
                AdjudicationDecision(
                    candidate_id="cand_prom_001",
                    verdict="accept",
                    severity="low",
                    confidence=0.9,
                    rationale="伏笔已确认。",
                ),
            ],
            final_adjudication=FinalStateAdjudication(
                chapter_number=4,
                verdict="accept",
                severity="low",
                confidence=0.9,
                accepted_candidate_ids=["cand_prom_001"],
                rejected_candidate_ids=[],
                pending_candidate_ids=[],
                repair_candidate_ids=[],
                should_block_archive=False,
                summary="伏笔通过。",
                state_updates=[
                    {
                        "candidate_id": "cand_prom_001",
                        "scope": "contract_progression",
                        "state_path": "promise.foreshadow.4.01",
                        "value": "foreshadow",
                        "summary": "怀表停在午夜十二点暗示时间异常",
                    },
                ],
            ),
            ledger_entries=[
                StateLedgerEntry(
                    entry_id="state_4_cand_prom_001_accept",
                    chapter_number=4,
                    candidate_id="cand_prom_001",
                    delta_type="promise",
                    summary="怀表停在午夜十二点暗示时间异常",
                    state_update={
                        "candidate_id": "cand_prom_001",
                        "scope": "contract_progression",
                        "state_path": "promise.foreshadow.4.01",
                        "value": "foreshadow",
                        "summary": "怀表停在午夜十二点暗示时间异常",
                    },
                    decision=AdjudicationDecision(
                        candidate_id="cand_prom_001",
                        verdict="accept",
                        severity="low",
                        confidence=0.9,
                        rationale="伏笔已确认。",
                    ),
                    evidence=[candidate.evidence[0]],
                ),
            ],
            source_text_hash="prom_hash",
        )
        kernel = await kernel_store.load_kernel("test-project")
        updated = apply_adjudication_to_kernel(kernel, report)
        assert len(updated.promise_ledger) >= 1
        entry = updated.promise_ledger[-1]
        assert "怀表" in entry.description
        assert entry.planted_chapter == 4


# ---------------------------------------------------------------------------
# Tests: persist_adjudicated_state_ledger with StoryKernelStore
# ---------------------------------------------------------------------------


class TestPersistAdjudicatedStateLedgerWithKernel:
    """Test persist_adjudicated_state_ledger with StoryKernelStore."""

    async def test_finalize_archive_write_persists_to_story_kernel(
        self,
        tmp_path: Path,
        sample_report: NarrativeAdjudicationReport,
    ) -> None:
        db_path = tmp_path / "story_kernel.db"
        seed_store = StoryKernelStore(db_path, wal_mode=False)
        try:
            await seed_store.init_db()
            await seed_store.create_kernel("test-project")
        finally:
            await seed_store.close()

        events: list[tuple[str, dict[str, object]]] = []
        runner = SimpleNamespace(
            _settings=SimpleNamespace(
                story_kernel_db_path=str(db_path),
                story_kernel_wal_mode=False,
            ),
            _on_step=lambda step, data: events.append((step, data)),
        )
        bundle = SimpleNamespace(
            project_id="test-project",
            layout=SimpleNamespace(root=tmp_path),
        )
        outcome = ChapterOutcome(
            source_chapter=1,
            new_events=[
                TimelineAnchor(
                    anchor_id="evt_extracted",
                    chapter=1,
                    event="章节抽取事件",
                    characters_involved=[],
                )
            ],
            chapter_exit_state=ChapterExitState(chapter_number=1),
        )

        await _write_chapter_outcome_to_story_kernel(
            runner,
            bundle,
            outcome,
            sample_report,
        )

        verify_store = StoryKernelStore(db_path, wal_mode=False)
        try:
            loaded = await verify_store.load_kernel("test-project")
        finally:
            await verify_store.close()
        assert len(loaded.timeline) == 2
        assert {entry.event for entry in loaded.timeline} == {
            "章节抽取事件",
            "林远发现时间裂缝入口",
        }
        assert events and events[0][0] == "story_kernel_updated"

    async def test_persist_writes_to_both_stores(
        self,
        tmp_path: Path,
        kernel_store: StoryKernelStore,
        sample_report: NarrativeAdjudicationReport,
    ) -> None:
        """When kernel_store is provided, results should be written to both."""
        await persist_adjudicated_state_ledger(
            project_root=tmp_path,
            report=sample_report,
            kernel_store=kernel_store,
            project_id="test-project",
        )
        # NarrativeStateStore should still have entries
        ns_store = NarrativeStateStore(tmp_path)
        assert len(ns_store.load_ledger_entries()) == 1
        # StoryKernel should have timeline entry
        kernel = await kernel_store.load_kernel("test-project")
        assert len(kernel.timeline) >= 1

    async def test_persist_without_kernel_store_falls_back(
        self,
        tmp_path: Path,
        sample_report: NarrativeAdjudicationReport,
    ) -> None:
        """Without kernel_store, should fall back to NarrativeStateStore only."""
        entries = await persist_adjudicated_state_ledger(
            project_root=tmp_path,
            report=sample_report,
        )
        assert len(entries) == 1
        ns_store = NarrativeStateStore(tmp_path)
        assert len(ns_store.load_ledger_entries()) == 1


# ---------------------------------------------------------------------------
# Tests: run_narrative_state_adjudication with StoryKernelStore
# ---------------------------------------------------------------------------


class TestRunNarrativeStateAdjudicationWithKernel:
    """Test the full adjudication flow with StoryKernel integration."""

    async def test_adjudication_writes_to_kernel_when_provided(
        self,
        tmp_path: Path,
        router,
        builder,
        runtime_settings,
        kernel_store: StoryKernelStore,
    ) -> None:
        """Full flow should write to StoryKernel when kernel_store is passed."""
        report_path = tmp_path / "reports" / "chapter_001_state_adjudication.json"

        report = await run_narrative_state_adjudication(
            router=router,
            builder=builder,
            settings=runtime_settings,
            trace=PipelineTrace(),
            project_root=tmp_path,
            chapter_number=1,
            chapter_text="林远继续追查时间裂缝。",
            chapter_contract={"chapter_number": 1, "required_events": ["追查时间裂缝"]},
            current_state={},
            known_characters=["林远"],
            on_step=lambda _step, _payload: None,
            report_path=report_path,
            kernel_store=kernel_store,
            project_id="test-project",
        )

        # Report should still be generated
        assert report.final_adjudication.verdict == "accept"
        assert report_path.exists()

        # StoryKernel should have been updated
        kernel = await kernel_store.load_kernel("test-project")
        # Should have at least a timeline entry or chapter summary
        has_kernel_update = (
            len(kernel.timeline) > 0
            or len(kernel.chapter_summaries) > 0
            or len(kernel.entities) > 0
        )
        assert has_kernel_update, "StoryKernel should have been updated with adjudication results"

    async def test_adjudication_without_kernel_store_falls_back(
        self,
        tmp_path: Path,
        router,
        builder,
        runtime_settings,
    ) -> None:
        """Without kernel_store, should work as before (NarrativeStateStore only)."""
        report_path = tmp_path / "reports" / "chapter_001_state_adjudication.json"

        report = await run_narrative_state_adjudication(
            router=router,
            builder=builder,
            settings=runtime_settings,
            trace=PipelineTrace(),
            project_root=tmp_path,
            chapter_number=1,
            chapter_text="林远继续追查时间裂缝。",
            chapter_contract={"chapter_number": 1, "required_events": ["追查时间裂缝"]},
            current_state={},
            known_characters=["林远"],
            on_step=lambda _step, _payload: None,
            report_path=report_path,
        )

        assert report.final_adjudication.verdict == "accept"
        # NarrativeStateStore should have entries
        store = NarrativeStateStore(tmp_path)
        # Candidate extraction may legitimately produce more than one accepted
        # delta as the schema grows; this test verifies the fallback sink, not a
        # provider-specific candidate count.
        assert store.load_ledger_entries()
