"""Tests for StoryKernel field-contract runtime enforcement (contract_guard).

Covers:
- diff_kernel_fields: change detection limited to the contract surface
- check_contract_write: immutable / undeclared-write violations, union
  semantics for multi-step writes, metadata exemption, unknown steps
- enforce_contract_write: off / warn / strict modes
- StoryKernelStateWriter integration at the single write boundary
"""

from __future__ import annotations

import pytest

from novel_forge.story_kernel.contract_guard import (
    IMMUTABLE_WRITE,
    UNDECLARED_WRITE,
    KernelContractViolationError,
    check_contract_write,
    contracts_for_steps,
    diff_kernel_fields,
    enforce_contract_write,
)
from novel_forge.story_kernel.schemas import (
    BusinessDependency,
    Entity,
    StoryKernel,
    WorldRule,
)
from novel_forge.story_kernel.state_writer import StoryKernelStateWriter
from novel_forge.story_kernel.store import StoryKernelStore


def _kernel(project_id: str = "p1") -> StoryKernel:
    return StoryKernel(project_id=project_id)


def _with_world_rule(kernel: StoryKernel) -> StoryKernel:
    rule = WorldRule(
        rule_id="wr1",
        content="魔法需要付出代价",
    )
    return kernel.model_copy(update={"world_rules": [rule]})


def _with_entity(kernel: StoryKernel, name: str = "林雪") -> StoryKernel:
    entity = Entity(entity_id="e1", name=name, entity_type="character")
    return kernel.model_copy(update={"entities": [entity]})


# ---------------------------------------------------------------------------
# diff_kernel_fields
# ---------------------------------------------------------------------------


class TestDiffKernelFields:
    def test_identical_kernels_have_no_diff(self) -> None:
        kernel = _with_entity(_kernel())
        assert diff_kernel_fields(kernel, kernel) == set()

    def test_detects_changed_field_group(self) -> None:
        before = _kernel()
        after = _with_entity(before)
        assert diff_kernel_fields(before, after) == {"entities"}

    def test_ignores_fields_outside_contract_surface(self) -> None:
        before = _kernel()
        after = before.model_copy(update={"artifact_refs": {"k": {"v": 1}}})
        assert diff_kernel_fields(before, after) == set()


# ---------------------------------------------------------------------------
# check_contract_write
# ---------------------------------------------------------------------------


class TestCheckContractWrite:
    def test_declared_write_is_allowed(self) -> None:
        before = _kernel()
        after = _with_entity(before)
        assert check_contract_write("extract", before=before, after=after) == []

    def test_immutable_field_change_is_flagged(self) -> None:
        before = _kernel()
        after = _with_world_rule(before)
        violations = check_contract_write("extract", before=before, after=after)
        assert [v.kind for v in violations] == [IMMUTABLE_WRITE]
        assert violations[0].field_name == "world_rules"

    def test_undeclared_write_is_flagged(self) -> None:
        # character_intro may only write entities/relationships.
        before = _kernel()
        after = before.model_copy(update={"chapter_summaries": {1: "摘要"}})
        violations = check_contract_write("character_intro", before=before, after=after)
        assert [v.kind for v in violations] == [UNDECLARED_WRITE]
        assert violations[0].field_name == "chapter_summaries"

    def test_business_dependencies_allowed_only_with_adjudication(self) -> None:
        before = _kernel()
        dep = BusinessDependency(
            dependency_id="d1",
            source_id="e1",
            target_id="e2",
            dependency_type="requires",
        )
        after = before.model_copy(update={"business_dependencies": [dep]})
        # extract alone: business_dependencies is immutable.
        violations = check_contract_write("extract", before=before, after=after)
        assert [v.kind for v in violations] == [IMMUTABLE_WRITE]
        # extract + state_adjudication: writable via union semantics.
        assert (
            check_contract_write(("extract", "state_adjudication"), before=before, after=after)
            == []
        )

    def test_immutable_intersection_keeps_world_rules_protected(self) -> None:
        before = _kernel()
        after = _with_world_rule(before)
        violations = check_contract_write(
            ("extract", "state_adjudication"), before=before, after=after
        )
        assert [v.kind for v in violations] == [IMMUTABLE_WRITE]

    def test_metadata_fields_are_exempt(self) -> None:
        before = _kernel()
        after = before.model_copy(update={"current_chapter": 7, "title": "新书"})
        assert check_contract_write("extract", before=before, after=after) == []

    def test_unknown_step_names_yield_no_violations(self) -> None:
        before = _kernel()
        after = _with_world_rule(before)
        assert check_contract_write("no_such_step", before=before, after=after) == []

    def test_contracts_for_steps_resolves_by_step_name(self) -> None:
        contracts = contracts_for_steps(["extract", "missing", "state_adjudication"])
        assert [c.step_name for c in contracts] == ["extract", "state_adjudication"]


# ---------------------------------------------------------------------------
# enforce_contract_write modes
# ---------------------------------------------------------------------------


class TestEnforceContractWrite:
    def _violating_pair(self) -> tuple[StoryKernel, StoryKernel]:
        before = _kernel()
        return before, _with_world_rule(before)

    def test_off_mode_skips_check(self) -> None:
        before, after = self._violating_pair()
        assert enforce_contract_write("extract", before=before, after=after, mode="off") == []

    def test_warn_mode_logs_and_returns_violations(self) -> None:
        before, after = self._violating_pair()
        violations = enforce_contract_write("extract", before=before, after=after, mode="warn")
        assert len(violations) == 1

    def test_strict_mode_raises(self) -> None:
        before, after = self._violating_pair()
        with pytest.raises(KernelContractViolationError) as exc_info:
            enforce_contract_write("extract", before=before, after=after, mode="strict")
        assert exc_info.value.violations[0].field_name == "world_rules"

    def test_clean_write_never_raises_in_strict_mode(self) -> None:
        before = _kernel()
        after = _with_entity(before)
        assert enforce_contract_write("extract", before=before, after=after, mode="strict") == []


# ---------------------------------------------------------------------------
# StoryKernelStateWriter integration
# ---------------------------------------------------------------------------


class TestStateWriterIntegration:
    async def test_attributed_clean_write_passes_in_strict_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            StoryKernelStateWriter, "_contract_mode", staticmethod(lambda: "strict")
        )
        store = StoryKernelStore.in_memory()
        writer = StoryKernelStateWriter(store)
        # state_adjudication declares "entities" in writes → allowed.
        updated = await writer.upsert_entities_from_known_names(
            project_id="p1",
            names=["林雪"],
            step_names="state_adjudication",
        )
        assert any(e.name == "林雪" for e in updated.entities)

    async def test_unattributed_write_skips_enforcement(self) -> None:
        # No step_names → guard is a no-op even for contract-surface changes.
        store = StoryKernelStore.in_memory()
        writer = StoryKernelStateWriter(store)
        updated = await writer.upsert_entities_from_known_names(
            project_id="p1",
            names=["林雪"],
        )
        assert any(e.name == "林雪" for e in updated.entities)
