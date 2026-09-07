"""Integration tests for ``ForeshadowReminder`` (memory facade over
``story_kernel.PromiseLedger``).

Covers:
1. ``get_due`` filters by status (only ``"planted"``).
2. ``get_due`` filters by chapter window (cutoff at ``current_chapter``).
3. ``record_planted`` delegates to ``store.add_promise``.
4. ``record_paid_off`` updates status by delegating to ``store.add_promise``.

The ``tests/integration/memory/`` directory is used because we exercise
the ``StoryKernelStore.add_promise`` round-trip end-to-end against an
in-memory SQLite, which crosses the memory ↔ story_kernel seam.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from novel_forge.memory.foreshadow_reminder import ForeshadowReminder
from novel_forge.story_kernel.schemas import PromiseLedger
from novel_forge.story_kernel.store import StoryKernelStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def store() -> AsyncIterator[StoryKernelStore]:
    """In-memory StoryKernelStore with a fresh kernel created."""
    s = StoryKernelStore.in_memory()
    await s.init_db()
    try:
        await s.create_kernel("foreshadow-test")
        yield s
    finally:
        await s.close()


@pytest.fixture()
async def populated_store() -> AsyncIterator[StoryKernelStore]:
    """Store with a kernel and a mix of promise statuses / chapters."""
    s = StoryKernelStore.in_memory()
    await s.init_db()
    try:
        await s.create_kernel("foreshadow-test")

        async with s._session():
            # We'll write directly through the kernel JSON so the
            # store has known fixture data. ``save_kernel`` upserts
            # the full kernel.
            kernel = await s.load_kernel("foreshadow-test")
            kernel.promise_ledger = [
                # 1. Planted at ch3, still pending — should appear in get_due(>=3)
                PromiseLedger(
                    entry_id="fs_watch",
                    description="怀表停在午夜十二点",
                    promise_type="foreshadow",
                    planted_chapter=3,
                    status="planted",
                ),
                # 2. Planted at ch5, still pending — should appear in get_due(>=5)
                PromiseLedger(
                    entry_id="fs_letter",
                    description="神秘信件暗示反派身份",
                    promise_type="foreshadow",
                    planted_chapter=5,
                    status="planted",
                    owner_entity_ids=["char_林远"],
                ),
                # 3. Already paid — should NOT appear in get_due
                PromiseLedger(
                    entry_id="fs_key",
                    description="钟楼钥匙已回收",
                    promise_type="foreshadow",
                    planted_chapter=2,
                    payoff_chapter=4,
                    status="paid",
                ),
                # 4. Planted at ch1, only hinted — should NOT appear (hinted != planted)
                PromiseLedger(
                    entry_id="fs_dream",
                    description="梦中幻象已被主角想起",
                    promise_type="foreshadow",
                    planted_chapter=1,
                    status="hinted",
                ),
                # 5. Future planted — should NOT appear if current_chapter < 7
                PromiseLedger(
                    entry_id="fs_late",
                    description="第7章才埋下的伏笔",
                    promise_type="foreshadow",
                    planted_chapter=7,
                    status="planted",
                ),
            ]
            await s.save_kernel(kernel)
        yield s
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# get_due — status filter
# ---------------------------------------------------------------------------


class TestGetDueStatusFilter:
    """Verify ``get_due`` only returns ``status="planted"`` entries."""

    async def test_get_due_filters_pending_promises(
        self, populated_store: StoryKernelStore
    ) -> None:
        """Only ``planted`` status appears; ``paid`` / ``hinted`` are hidden."""
        reminder = ForeshadowReminder("foreshadow-test", store=populated_store)
        due = await reminder.get_due(current_chapter=10)

        # Expect: fs_watch (ch3, planted), fs_letter (ch5, planted).
        # Skip: fs_key (paid), fs_dream (hinted), fs_late (ch7 > 10? no, <= 10, so it WILL be included).
        # Wait — fs_late is at ch7 which is <= 10, so it should be included.
        entry_ids = {item["entry_id"] for item in due}
        assert "fs_watch" in entry_ids
        assert "fs_letter" in entry_ids
        # Paid / hinted must never appear.
        assert "fs_key" not in entry_ids
        assert "fs_dream" not in entry_ids

        # Every returned item must have status == "planted".
        assert all(item["status"] == "planted" for item in due)

    async def test_get_due_empty_when_no_planted(self, populated_store: StoryKernelStore) -> None:
        """When cutoff is below all planted chapters, return [].

        Cutoff at ch1 excludes ch3/ch5/ch7 and the ch2 entry is already
        paid — so no planted entries qualify.
        """
        reminder = ForeshadowReminder("foreshadow-test", store=populated_store)
        due = await reminder.get_due(current_chapter=1)
        # Only fs_letter / fs_watch / fs_late are planted but all planted_chapter > 1.
        assert due == []


# ---------------------------------------------------------------------------
# get_due — chapter cutoff
# ---------------------------------------------------------------------------


class TestGetDueChapterCutoff:
    """Verify ``get_due`` respects the chapter window."""

    async def test_get_due_filters_by_chapter(self, populated_store: StoryKernelStore) -> None:
        """Cutoff at ch3 returns only promises planted on/before ch3."""
        reminder = ForeshadowReminder("foreshadow-test", store=populated_store)
        due = await reminder.get_due(current_chapter=3)

        # fs_watch (ch3) is included, fs_letter (ch5) and fs_late (ch7) are not.
        entry_ids = {item["entry_id"] for item in due}
        assert entry_ids == {"fs_watch"}

    async def test_get_due_strictly_future_excluded(
        self, populated_store: StoryKernelStore
    ) -> None:
        """``planted_chapter > current_chapter`` is excluded (strict cutoff)."""
        reminder = ForeshadowReminder("foreshadow-test", store=populated_store)
        due = await reminder.get_due(current_chapter=6)

        # fs_late is at ch7, current_chapter=6, so it must NOT appear.
        entry_ids = {item["entry_id"] for item in due}
        assert "fs_late" not in entry_ids
        # But fs_letter (ch5) and fs_watch (ch3) should be there.
        assert "fs_letter" in entry_ids
        assert "fs_watch" in entry_ids

    async def test_get_due_returns_sorted_oldest_first(
        self, populated_store: StoryKernelStore
    ) -> None:
        """Sort order: oldest planted_chapter first."""
        reminder = ForeshadowReminder("foreshadow-test", store=populated_store)
        due = await reminder.get_due(current_chapter=10)

        # Should be sorted by (planted_chapter, entry_id).
        chapters = [item["planted_chapter"] for item in due]
        assert chapters == sorted(chapters)
        # And only "planted" items remain.
        assert {item["entry_id"] for item in due} == {"fs_watch", "fs_letter", "fs_late"}


# ---------------------------------------------------------------------------
# get_due — read-only invariant
# ---------------------------------------------------------------------------


class TestGetDureReadOnly:
    """``get_due`` must not mutate state."""

    async def test_get_due_no_store_returns_empty(self) -> None:
        """Without a store, ``get_due`` returns ``[]`` (no source of truth)."""
        reminder = ForeshadowReminder("any-project")
        assert reminder.has_store is False
        assert await reminder.get_due(current_chapter=100) == []

    async def test_get_due_missing_kernel_returns_empty(self, store: StoryKernelStore) -> None:
        """Empty kernel (no promises) returns ``[]``."""
        reminder = ForeshadowReminder("foreshadow-test", store=store)
        assert await reminder.get_due(current_chapter=50) == []


# ---------------------------------------------------------------------------
# record_planted — delegation to story_kernel
# ---------------------------------------------------------------------------


class TestRecordPlantedDelegation:
    """``record_planted`` must delegate to ``store.add_promise``."""

    async def test_record_planted_delegates_to_story_kernel(self, store: StoryKernelStore) -> None:
        """``store.add_promise`` is called exactly once with a planted entry."""
        reminder = ForeshadowReminder("foreshadow-test", store=store)
        entry_id = await reminder.record_planted(
            description="怀表停在午夜十二点",
            promise_type="foreshadow",
            chapter=3,
            owner_entity_ids=["char_林远"],
        )

        # entry_id must be a non-empty string starting with "prom_".
        assert isinstance(entry_id, str)
        assert entry_id.startswith("prom_")
        assert len(entry_id) > len("prom_")

        # Round-trip: load the kernel and confirm the promise is there.
        kernel = await store.load_kernel("foreshadow-test")
        assert any(
            p.entry_id == entry_id
            and p.status == "planted"
            and p.planted_chapter == 3
            and p.promise_type == "foreshadow"
            and p.description == "怀表停在午夜十二点"
            and "char_林远" in p.owner_entity_ids
            for p in kernel.promise_ledger
        )

    async def test_record_planted_uses_mock_store(self) -> None:
        """With a mock store, ``add_promise`` is called exactly once."""
        mock_store = AsyncMock()
        mock_store.add_promise = AsyncMock(return_value=None)

        reminder = ForeshadowReminder("mock-project", store=mock_store)
        entry_id = await reminder.record_planted(
            description="测试伏笔",
            chapter=7,
        )

        assert entry_id.startswith("prom_")
        # add_promise was called exactly once.
        mock_store.add_promise.assert_awaited_once()
        # The argument is a PromiseLedger with the right fields.
        call_args = mock_store.add_promise.await_args
        entry_arg = call_args.args[0]
        assert isinstance(entry_arg, PromiseLedger)
        assert entry_arg.entry_id == entry_id
        assert entry_arg.status == "planted"
        assert entry_arg.planted_chapter == 7
        assert entry_arg.promise_type == "foreshadow"
        assert entry_arg.description == "测试伏笔"

    async def test_record_planted_without_store_raises(self) -> None:
        """Without a store, ``record_planted`` raises ``RuntimeError``."""
        reminder = ForeshadowReminder("any-project")
        assert reminder.has_store is False
        with pytest.raises(RuntimeError, match="requires a StoryKernelStore"):
            await reminder.record_planted(description="x", chapter=1)

    async def test_record_planted_validates_inputs(self, store: StoryKernelStore) -> None:
        """Empty description and negative chapter are rejected."""
        reminder = ForeshadowReminder("foreshadow-test", store=store)
        with pytest.raises(ValueError, match="description must be non-empty"):
            await reminder.record_planted(description="", chapter=1)
        with pytest.raises(ValueError, match="description must be non-empty"):
            await reminder.record_planted(description="   ", chapter=1)
        with pytest.raises(ValueError, match="chapter must be >= 0"):
            await reminder.record_planted(description="ok", chapter=-1)


# ---------------------------------------------------------------------------
# record_paid_off — status transition via delegation
# ---------------------------------------------------------------------------


class TestRecordPaidOffDelegation:
    """``record_paid_off`` updates status by delegating to ``store.add_promise``."""

    async def test_record_paid_off_updates_status(self, store: StoryKernelStore) -> None:
        """After payoff, the promise no longer appears in ``get_due``."""
        reminder = ForeshadowReminder("foreshadow-test", store=store)
        # First, plant a promise.
        entry_id = await reminder.record_planted(
            description="怀表停在午夜十二点",
            chapter=3,
        )

        # Confirm it's due at chapter 5.
        due_before = await reminder.get_due(current_chapter=5)
        assert any(item["entry_id"] == entry_id for item in due_before)

        # Pay it off at chapter 7.
        ok = await reminder.record_paid_off(entry_id, chapter=7)
        assert ok is True

        # Now get_due should NOT return it (status filter excludes "paid").
        due_after = await reminder.get_due(current_chapter=10)
        assert not any(item["entry_id"] == entry_id for item in due_after)

        # The kernel still has the entry, but the "paid" copy is the freshest.
        kernel = await store.load_kernel("foreshadow-test")
        matching = [p for p in kernel.promise_ledger if p.entry_id == entry_id]
        assert len(matching) >= 1
        # The most recent entry (highest planted_chapter tiebreaker — same here)
        # has status "paid" and payoff_chapter set.
        freshest = matching[-1]
        assert freshest.status == "paid"
        assert freshest.payoff_chapter == 7

    async def test_record_paid_off_unknown_entry_returns_false(
        self, store: StoryKernelStore
    ) -> None:
        """Missing entry_id returns ``False`` and does not crash."""
        reminder = ForeshadowReminder("foreshadow-test", store=store)
        ok = await reminder.record_paid_off("nonexistent_id", chapter=5)
        assert ok is False

    async def test_record_paid_off_idempotent(self, store: StoryKernelStore) -> None:
        """Paying off the same entry twice is a no-op the second time.

        We verify idempotency by counting ``add_promise`` calls on a
        mock store: the first payoff triggers one delegation; the
        second payoff at the same chapter is short-circuited and
        must not call ``add_promise`` at all.
        """
        # Use a mock store with an in-memory backing for load_kernel.
        real_store = StoryKernelStore.in_memory()
        await real_store.init_db()
        await real_store.create_kernel("foreshadow-test")
        try:
            mock_store = AsyncMock()
            mock_store.add_promise = AsyncMock(side_effect=real_store.add_promise)
            mock_store.load_kernel = real_store.load_kernel

            reminder = ForeshadowReminder("foreshadow-test", store=mock_store)
            entry_id = await reminder.record_planted(
                description="可重复兑现的伏笔",
                chapter=2,
            )
            # Planted once.
            assert mock_store.add_promise.await_count == 1

            # First payoff — should delegate.
            assert await reminder.record_paid_off(entry_id, chapter=4) is True
            assert mock_store.add_promise.await_count == 2

            # Second payoff at same chapter — idempotent, no extra delegation.
            assert await reminder.record_paid_off(entry_id, chapter=4) is True
            assert mock_store.add_promise.await_count == 2
        finally:
            await real_store.close()

    async def test_record_paid_off_without_store_raises(self) -> None:
        reminder = ForeshadowReminder("any-project")
        with pytest.raises(RuntimeError, match="requires a StoryKernelStore"):
            await reminder.record_paid_off("any", chapter=1)

    async def test_record_paid_off_validates_inputs(self, store: StoryKernelStore) -> None:
        reminder = ForeshadowReminder("foreshadow-test", store=store)
        with pytest.raises(ValueError, match="entry_id must be non-empty"):
            await reminder.record_paid_off("", chapter=1)
        with pytest.raises(ValueError, match="chapter must be >= 0"):
            await reminder.record_paid_off("ok", chapter=-1)


# ---------------------------------------------------------------------------
# Construction-time invariants
# ---------------------------------------------------------------------------


class TestConstruction:
    """Constructor validation and basic property surface."""

    def test_empty_project_id_raises(self) -> None:
        with pytest.raises(ValueError, match="project_id must be non-empty"):
            ForeshadowReminder("")

    def test_project_id_property(self) -> None:
        r = ForeshadowReminder("my-proj")
        assert r.project_id == "my-proj"
        assert r.has_store is False

    def test_has_store_true_when_store_provided(self) -> None:
        mock_store = AsyncMock()
        r = ForeshadowReminder("my-proj", store=mock_store)
        assert r.has_store is True
