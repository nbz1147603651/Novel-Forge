"""ForeshadowReminder — read-only access to ``story_kernel.PromiseLedger``.

A thin facade for the memory layer to query and record foreshadowing /
promise lifecycle events. The actual state lives in story_kernel;
``ForeshadowReminder`` is a **read-only consumer** of that data — every
mutation goes through ``store.add_promise()``, the documented public
API. There is no SQLite access, no new tables, no schema changes.

Public API:
    * ``get_due(current_chapter)`` — pure read, no state mutation.
    * ``record_planted(...)`` — delegates to ``store.add_promise``.
    * ``record_paid_off(entry_id, chapter=...)`` — delegates to
      ``store.add_promise`` after loading the kernel to locate the
      matching entry.

Out of scope (deliberately):
    * Direct SQLite writes, new tables, schema edits.
    * LLM-based promise detection (see ``EXTRACT_CANDIDATE_STATE_DELTAS``).
    * Injection into ``get_layered_context`` (follow-up plan task).
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from novel_forge.story_kernel.schemas import PromiseLedger

if TYPE_CHECKING:
    from novel_forge.story_kernel.store import StoryKernelStore

_log = logging.getLogger("novel_forge.memory.foreshadow_reminder")

_STATUS_PLANTED = "planted"
_STATUS_PAID = "paid"


def _generate_entry_id() -> str:
    """Unique promise id: ``prom_<12-hex>`` (48 bits of entropy)."""
    return f"prom_{uuid.uuid4().hex[:12]}"


class ForeshadowReminder:
    """Read-only facade over ``PromiseLedger`` with delegated writes.

    Args:
        project_id: Project namespace this reminder operates on.
        store: Optional ``StoryKernelStore``. Required for
            ``record_planted`` and ``record_paid_off``; ``get_due``
            returns ``[]`` when absent.
    """

    def __init__(
        self,
        project_id: str,
        store: "StoryKernelStore | None" = None,
    ) -> None:
        if not project_id:
            raise ValueError("project_id must be non-empty")
        self._project_id = project_id
        self._store = store

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    async def get_due(self, current_chapter: int) -> list[dict[str, Any]]:
        """Return pending promises planted in ``(0, current_chapter]``.

        Filters by ``status == "planted"`` and
        ``0 < planted_chapter <= current_chapter``. The status filter
        naturally handles the append-only history of
        ``promise_ledger`` — once ``record_paid_off`` adds a new entry
        with ``status="paid"``, the older ``"planted"`` copy stays
        for history but is no longer surfaced.

        Returns:
            Dicts with ``entry_id``, ``description``, ``planted_chapter``,
            ``status``, ``promise_type``, ``owner_entity_ids``. Sorted
            oldest-first so older debts surface first.

        Notes:
            Pure read — no kernel mutation, no DB writes.
        """
        if self._store is None:
            _log.debug(
                "get_due | project_id=%s no store attached, returning []",
                self._project_id,
            )
            return []

        try:
            kernel = await self._store.load_kernel(self._project_id)
        except ValueError:
            _log.debug(
                "get_due | project_id=%s kernel not found, returning []",
                self._project_id,
            )
            return []

        results: list[dict[str, Any]] = []
        for promise in kernel.promise_ledger:
            if promise.status != _STATUS_PLANTED:
                continue
            if promise.planted_chapter <= 0 or promise.planted_chapter > current_chapter:
                continue
            results.append(
                {
                    "entry_id": promise.entry_id,
                    "description": promise.description,
                    "planted_chapter": promise.planted_chapter,
                    "status": promise.status,
                    "promise_type": promise.promise_type,
                    "owner_entity_ids": list(promise.owner_entity_ids),
                }
            )

        # Stable order: oldest planted first.
        results.sort(key=lambda item: (item["planted_chapter"], item["entry_id"]))
        return results

    # ------------------------------------------------------------------
    # Public write API (delegated to story_kernel)
    # ------------------------------------------------------------------

    async def record_planted(
        self,
        description: str,
        *,
        promise_type: str = "foreshadow",
        chapter: int,
        owner_entity_ids: list[str] | None = None,
    ) -> str:
        """Record a planted promise by delegating to ``store.add_promise``.

        Builds a ``PromiseLedger`` and hands it to story_kernel — the
        only writer. Does NOT touch SQLite directly.

        Returns:
            The ``entry_id`` assigned to the new promise.

        Raises:
            RuntimeError: If no ``StoryKernelStore`` was provided.
            ValueError: On empty ``description`` or negative ``chapter``.
        """
        if self._store is None:
            raise RuntimeError(
                "ForeshadowReminder.record_planted requires a StoryKernelStore; "
                "construct with store=... to enable writes."
            )
        if not description or not description.strip():
            raise ValueError("description must be non-empty")
        if chapter < 0:
            raise ValueError("chapter must be >= 0")

        entry_id = _generate_entry_id()
        entry = PromiseLedger(
            entry_id=entry_id,
            description=description.strip(),
            promise_type=promise_type,
            planted_chapter=chapter,
            status=_STATUS_PLANTED,
            owner_entity_ids=list(owner_entity_ids or []),
        )
        # DELEGATION: story_kernel owns the SQLite transaction, the
        # kernel JSON upsert, and the promise_ledger ORM row.
        await self._store.add_promise(entry)
        _log.info(
            "record_planted | project_id=%s entry_id=%s chapter=%d type=%s",
            self._project_id,
            entry_id,
            chapter,
            promise_type,
        )
        return entry_id

    async def record_paid_off(
        self,
        entry_id: str,
        *,
        chapter: int,
    ) -> bool:
        """Mark a promise as paid off by delegating to ``store.add_promise``.

        Loads the kernel, locates the freshest entry for ``entry_id``,
        builds a new ``PromiseLedger`` with ``status="paid"`` and
        ``payoff_chapter=chapter``, then delegates to
        ``store.add_promise``. The store appends a new ORM row and
        upserts the kernel JSON; the older ``planted`` entry remains
        for history.

        Idempotency: if the latest entry is already ``paid`` at the
        requested chapter, returns ``True`` without re-writing.

        Returns:
            ``True`` if updated (or already paid); ``False`` if no
            such ``entry_id`` exists in the kernel.

        Raises:
            RuntimeError: If no ``StoryKernelStore`` was provided.
            ValueError: On empty ``entry_id`` or negative ``chapter``.
        """
        if self._store is None:
            raise RuntimeError(
                "ForeshadowReminder.record_paid_off requires a StoryKernelStore; "
                "construct with store=... to enable writes."
            )
        if not entry_id or not entry_id.strip():
            raise ValueError("entry_id must be non-empty")
        if chapter < 0:
            raise ValueError("chapter must be >= 0")

        try:
            kernel = await self._store.load_kernel(self._project_id)
        except ValueError:
            return False

        # Freshest match — the kernel list is append-only, so the
        # last entry with this entry_id is the current state.
        latest = None
        for promise in kernel.promise_ledger:
            if promise.entry_id != entry_id:
                continue
            if latest is None or promise.planted_chapter >= latest.planted_chapter:
                latest = promise

        if latest is None:
            _log.debug(
                "record_paid_off | project_id=%s entry_id=%s not found",
                self._project_id,
                entry_id,
            )
            return False

        if latest.status == _STATUS_PAID and latest.payoff_chapter == chapter:
            return True

        paid_entry = latest.model_copy(update={"status": _STATUS_PAID, "payoff_chapter": chapter})
        # DELEGATION: story_kernel is the single source of truth.
        await self._store.add_promise(paid_entry)
        _log.info(
            "record_paid_off | project_id=%s entry_id=%s payoff_chapter=%d",
            self._project_id,
            entry_id,
            chapter,
        )
        return True

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def project_id(self) -> str:
        """Project namespace this reminder is bound to."""
        return self._project_id

    @property
    def has_store(self) -> bool:
        """``True`` if a ``StoryKernelStore`` is attached for writes."""
        return self._store is not None
