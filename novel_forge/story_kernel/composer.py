"""ContextComposer — assembles minimal field slices from StoryKernel.

Each pipeline step declares what fields it reads via a FieldContract.
ContextComposer queries only those fields from the kernel store and returns
a minimal dict slice, keeping prompt payloads small and focused.

The 10 field groups of StoryKernel:
    world_rules, entities, relationships, timeline, object_ledger,
    knowledge_ledger, access_ledger, promise_ledger, motif_protocols,
    business_dependencies

Supplementary fields:
    chapter_summaries, banned_phrases, notes

Metadata fields:
    project_id, current_chapter, active_volume, title, premise
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from novel_forge.story_kernel.contracts import (
    ALL_CONTRACTS,
    FieldContract,
)
from novel_forge.story_kernel.schemas import StoryKernel

_logger = logging.getLogger(__name__)
COMPOSER_RECENCY_WINDOW = 10


# ---------------------------------------------------------------------------
# Store Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class StoryKernelLoader(Protocol):
    """Synchronous kernel loader used by :class:`ContextComposer`.

    The async SQLite store is intentionally not used directly here. Chapter
    generation should load the kernel once, then compose local slices from a
    snapshot so step preparation stays cheap and cannot accidentally block on
    storage.
    """

    def load_kernel(self, project_id: str) -> StoryKernel:
        """Load the StoryKernel for a given project.

        Parameters
        ----------
        project_id:
            Unique project identifier.

        Returns
        -------
        StoryKernel
            The loaded kernel instance.

        Raises
        ------
        KeyError
            If no kernel exists for the given project_id.
        """
        ...  # pragma: no cover


class StoryKernelSnapshotStore:
    """Tiny in-memory loader for a preloaded StoryKernel snapshot."""

    def __init__(self, kernel: StoryKernel) -> None:
        self._kernel = kernel

    def load_kernel(self, project_id: str) -> StoryKernel:
        if project_id and project_id != self._kernel.project_id:
            raise KeyError(
                f"StoryKernel snapshot is for project '{self._kernel.project_id}', "
                f"not '{project_id}'."
            )
        return self._kernel


# ---------------------------------------------------------------------------
# Step-name → Contract lookup
# ---------------------------------------------------------------------------

# Build a step_name → FieldContract mapping from ALL_CONTRACTS.
_CONTRACT_BY_STEP: dict[str, FieldContract] = {
    contract.step_name: contract for contract in ALL_CONTRACTS.values()
}


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_field(value: Any) -> Any:
    """Serialize a kernel field value for inclusion in a context dict.

    - Pydantic models → model_dump(mode="json")
    - list of Pydantic models → [item.model_dump(mode="json") for item]
    - dict, list[str], str, int, float → pass through
    """
    if isinstance(value, list):
        if value and hasattr(value[0], "model_dump"):
            return [item.model_dump(mode="json") for item in value]
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _extract_field(kernel: StoryKernel, field_name: str) -> Any:
    """Extract a single field from the kernel and serialize it."""
    value = getattr(kernel, field_name, None)
    if value is None:
        return None
    return _serialize_field(value)


# ---------------------------------------------------------------------------
# ContextComposer
# ---------------------------------------------------------------------------


class ContextComposer:
    """Assembles minimal field slices from StoryKernel for each pipeline step.

    Usage::

        store = StoryKernelStore(db_path="...")
        composer = ContextComposer(store, project_id="my-novel")

        # Generic: any registered step
        ctx = composer.compose_for_step("bridge", chapter_number=3)

        # Convenience: specific step
        ctx = composer.compose_bridge_input(chapter_number=3)

        # With extra context (merged, does NOT override kernel fields)
        ctx = composer.compose_for_step("draft", 3, extra_context={"hint": "..."})
    """

    def __init__(
        self,
        kernel_store: StoryKernelLoader,
        *,
        project_id: str = "",
    ) -> None:
        self._store = kernel_store
        self._project_id = project_id

    @classmethod
    def from_kernel(cls, kernel: StoryKernel) -> "ContextComposer":
        """Build a composer from an already-loaded kernel snapshot."""
        return cls(StoryKernelSnapshotStore(kernel), project_id=kernel.project_id)

    # ── Generic composition ──────────────────────────────────────────────

    def compose_for_step(
        self,
        step_name: str,
        chapter_number: int,
        extra_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a minimal field slice for the given pipeline step.

        Looks up the step's FieldContract, queries only the ``reads`` fields
        from the kernel, and merges any ``extra_context`` entries (without
        overriding kernel-derived fields).

        Parameters
        ----------
        step_name:
            Contract step name (e.g. ``"bridge"``, ``"plan"``, ``"draft"``).
        chapter_number:
            Current chapter number (available for future filtering logic).
        extra_context:
            Additional key-value pairs to merge into the result.  Keys that
            collide with kernel fields are silently ignored.

        Returns
        -------
        dict[str, Any]
            Minimal field slice.  Keys are StoryKernel field names (from the
            contract's ``reads`` set) plus any non-colliding extra entries.
        """
        contract = _CONTRACT_BY_STEP.get(step_name)
        if contract is None:
            _logger.warning("No contract found for step '%s'; returning empty slice.", step_name)
            result: dict[str, Any] = {}
        else:
            kernel = self._store.load_kernel(self._project_id)
            result = self._extract_fields(
                kernel,
                contract.reads,
                chapter_number=chapter_number,
                include_future=step_name in {"book_consistency", "macro_guard", "volume"},
            )

        # Merge extra context (kernel fields take precedence)
        if extra_context:
            for key, value in extra_context.items():
                if key not in result:
                    result[key] = value

        return result

    # ── Convenience composers ────────────────────────────────────────────

    def compose_bridge_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Bridge step.

        Reads: entities, relationships, timeline, world_rules,
        knowledge_ledger, promise_ledger, motif_protocols, chapter_summaries.
        """
        return self.compose_for_step("bridge", chapter_number)

    def compose_plan_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Plan step.

        Reads: entities, relationships, timeline, world_rules, object_ledger,
        knowledge_ledger, promise_ledger, motif_protocols,
        business_dependencies, chapter_summaries.
        """
        return self.compose_for_step("plan", chapter_number)

    def compose_draft_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Draft step.

        Reads: entities, relationships, timeline, world_rules, object_ledger,
        knowledge_ledger, access_ledger, promise_ledger, motif_protocols.
        """
        return self.compose_for_step("draft", chapter_number)

    def compose_edit_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Edit step.

        Reads: entities, relationships, timeline, world_rules,
        knowledge_ledger, access_ledger, banned_phrases.
        """
        return self.compose_for_step("edit", chapter_number)

    def compose_continuity_eval_input(
        self,
        chapter_number: int,
        chapter_text: str,
    ) -> dict[str, Any]:
        """Compose field slice for the Continuity Eval step.

        Reads: entities, relationships, timeline, world_rules,
        knowledge_ledger, object_ledger, chapter_summaries.

        Parameters
        ----------
        chapter_number:
            Current chapter number.
        chapter_text:
            The chapter text to evaluate (passed as extra context).
        """
        return self.compose_for_step(
            "continuity_eval",
            chapter_number,
            extra_context={"chapter_text": chapter_text},
        )

    def compose_extract_input(
        self,
        chapter_number: int,
        chapter_text: str,
    ) -> dict[str, Any]:
        """Compose field slice for the Extract step.

        Reads: entities, relationships, timeline, world_rules, object_ledger,
        knowledge_ledger, access_ledger, promise_ledger, motif_protocols,
        business_dependencies, chapter_summaries.

        Parameters
        ----------
        chapter_number:
            Current chapter number.
        chapter_text:
            The chapter text to extract from (passed as extra context).
        """
        return self.compose_for_step(
            "extract",
            chapter_number,
            extra_context={"chapter_text": chapter_text},
        )

    def compose_causal_validate_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Causal Validation step.

        Reads: entities, relationships, timeline, knowledge_ledger,
        business_dependencies, promise_ledger.
        """
        return self.compose_for_step("causal_validate", chapter_number)

    def compose_causal_repair_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Causal Repair step.

        Reads: entities, relationships, timeline, knowledge_ledger,
        business_dependencies, promise_ledger.
        """
        return self.compose_for_step("causal_repair", chapter_number)

    def compose_alignment_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Alignment step.

        Reads: entities, relationships, timeline, world_rules,
        promise_ledger, motif_protocols, chapter_summaries.
        """
        return self.compose_for_step("alignment", chapter_number)

    def compose_check_chapter_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Check Chapter step.

        Reads: entities, relationships, timeline, world_rules,
        knowledge_ledger, object_ledger, promise_ledger, banned_phrases.
        """
        return self.compose_for_step("check_chapter", chapter_number)

    def compose_patch_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Patch step.

        Reads: entities, world_rules.
        """
        return self.compose_for_step("patch", chapter_number)

    def compose_book_consistency_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Book Consistency step.

        Reads: all StoryKernel fields (full audit).
        """
        return self.compose_for_step("book_consistency", chapter_number)

    def compose_macro_guard_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Macro Guard step.

        Reads: all StoryKernel fields (full audit).
        """
        return self.compose_for_step("macro_guard", chapter_number)

    def compose_reading_power_eval_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Reading Power Eval step.

        Reads: entities, relationships, timeline, world_rules.
        """
        return self.compose_for_step("reading_power_eval", chapter_number)

    def compose_reading_power_repair_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Reading Power Repair step.

        Reads: entities, relationships, timeline, world_rules.
        """
        return self.compose_for_step("reading_power_repair", chapter_number)

    def compose_polish_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Polish step.

        Reads: entities, relationships, timeline, world_rules,
        banned_phrases, motif_protocols.
        """
        return self.compose_for_step("polish", chapter_number)

    def compose_evaluate_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Evaluate step.

        Reads: entities, relationships, timeline, world_rules,
        chapter_summaries.
        """
        return self.compose_for_step("evaluate", chapter_number)

    def compose_volume_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Volume Audit step.

        Reads: entities, relationships, timeline, world_rules,
        promise_ledger, motif_protocols, chapter_summaries.
        """
        return self.compose_for_step("volume", chapter_number)

    def compose_pronoun_check_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Pronoun Check step.

        Reads: entities.
        """
        return self.compose_for_step("pronoun_check", chapter_number)

    def compose_forbidden_sources_input(
        self,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Compose field slice for the Forbidden Sources step.

        Reads: banned_phrases.
        """
        return self.compose_for_step("forbidden_sources", chapter_number)

    def compose_generic(
        self,
        step_name: str,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Generic convenience wrapper — delegates to compose_for_step.

        Parameters
        ----------
        step_name:
            Contract step name.
        chapter_number:
            Current chapter number.
        """
        return self.compose_for_step(step_name, chapter_number)

    # ── Internal helpers ─────────────────────────────────────────────────

    @staticmethod
    def _extract_fields(
        kernel: StoryKernel,
        field_names: frozenset[str],
        *,
        chapter_number: int,
        include_future: bool = False,
    ) -> dict[str, Any]:
        """Extract and serialize the requested fields from the kernel."""
        result: dict[str, Any] = {}
        for field_name in sorted(field_names):
            value = _extract_field(kernel, field_name)
            if value is not None:
                if not include_future:
                    value = _filter_to_chapter(field_name, value, chapter_number)
                result[field_name] = value
        return result


def _filter_to_chapter(field_name: str, value: Any, chapter_number: int) -> Any:
    """Trim future-only facts out of a field slice.

    Init may know the whole outline, but draft/repair/eval steps should mostly
    receive established state plus the current chapter anchor. Full-book audit
    paths opt out through ``include_future=True``.
    """
    window_start = max(1, int(chapter_number or 0) - COMPOSER_RECENCY_WINDOW)
    if field_name in {"chapter_summaries", "chapter_exit_states"} and isinstance(value, dict):
        filtered_mapping: dict[Any, Any] = {}
        for key, item in value.items():
            try:
                chapter = int(key)
            except (TypeError, ValueError):
                continue
            if window_start <= chapter <= chapter_number:
                filtered_mapping[key] = item
        return filtered_mapping
    if not isinstance(value, list):
        return value
    if field_name == "entities":
        filtered_entities: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            source = int(item.get("source_chapter", 0) or 0)
            last_seen = int(item.get("last_seen_chapter", 0) or 0)
            if source > chapter_number:
                continue
            is_init_core = source == 0 or last_seen == 0
            is_recent = last_seen >= window_start or source >= window_start
            if is_init_core or (last_seen <= chapter_number and is_recent):
                filtered_entities.append(item)
        return filtered_entities
    if field_name == "relationships":
        return [
            item
            for item in value
            if int(item.get("last_shift_chapter", 0) or 0) <= chapter_number
        ]
    if field_name == "timeline":
        return [
            item
            for item in value
            if window_start <= int(item.get("chapter", 0) or 0) <= chapter_number
        ]
    if field_name == "knowledge_ledger":
        return [
            item
            for item in value
            if int(item.get("source_chapter", 0) or 0) <= chapter_number
        ]
    if field_name == "object_ledger":
        return [
            item
            for item in value
            if int(item.get("introduced_chapter", 0) or 0) <= chapter_number
        ]
    if field_name == "access_ledger":
        filtered_access: list[dict[str, Any]] = []
        for item in value:
            granted = int(item.get("granted_chapter", 0) or 0)
            revoked = int(item.get("revoked_chapter", 0) or 0)
            if granted <= chapter_number and (revoked == 0 or revoked > chapter_number):
                filtered_access.append(item)
        return filtered_access
    if field_name == "promise_ledger":
        return [
            item
            for item in value
            if int(item.get("planted_chapter", 0) or 0) <= chapter_number
        ]
    if field_name == "motif_protocols":
        filtered_motifs: list[dict[str, Any]] = []
        for item in value:
            copy = dict(item)
            copy["occurrences"] = [
                ch for ch in copy.get("occurrences", []) if int(ch or 0) <= chapter_number
            ]
            filtered_motifs.append(copy)
        return filtered_motifs
    return value


__all__ = [
    "ContextComposer",
    "StoryKernelLoader",
    "StoryKernelSnapshotStore",
]
