"""Tests for novel_forge.core.infra.resource_locks."""

from __future__ import annotations

import asyncio

import pytest

from novel_forge.core.infra.resource_locks import (
    LockKey,
    ResourceLockManager,
    ResourceLockType,
    ResourceName,
    get_resource_lock_manager,
    reset_resource_lock_manager,
)


class TestResourceName:
    def test_all_values_present(self) -> None:
        assert ResourceName.STORY_BIBLE.value == "story_bible"
        assert ResourceName.CHARACTER_BIBLE.value == "character_bible"
        assert ResourceName.OUTLINE.value == "outline"
        assert ResourceName.CANON.value == "canon"
        assert ResourceName.STATE.value == "state"

    def test_is_string_enum(self) -> None:
        assert isinstance(ResourceName.OUTLINE, str)
        assert ResourceName.OUTLINE == "outline"


class TestResourceLockType:
    def test_all_values_present(self) -> None:
        assert ResourceLockType.EXCLUSIVE.value == "exclusive"
        assert ResourceLockType.SHARED.value == "shared"

    def test_is_string_enum(self) -> None:
        assert isinstance(ResourceLockType.EXCLUSIVE, str)


class TestLockKey:
    def test_global_lock_key(self) -> None:
        key = LockKey(resource=ResourceName.OUTLINE)
        assert key.resource == ResourceName.OUTLINE
        assert key.chapter is None
        assert str(key) == "outline"

    def test_chapter_lock_key(self) -> None:
        key = LockKey(resource=ResourceName.CANON, chapter=5)
        assert key.resource == ResourceName.CANON
        assert key.chapter == 5
        assert str(key) == "canon_chapter_5"

    def test_project_scoped_lock_key(self) -> None:
        key = LockKey(resource=ResourceName.STATE, chapter=5, project_id=" 与君共赴 ")
        assert key.project_id == "与君共赴"
        assert str(key) == "与君共赴:state_chapter_5"

    def test_negative_chapter_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            LockKey(resource=ResourceName.OUTLINE, chapter=-1)

    def test_is_outline_segment(self) -> None:
        outline_key = LockKey(resource=ResourceName.OUTLINE, chapter=49)
        assert outline_key.is_outline_segment is True

        non_outline_key = LockKey(resource=ResourceName.CANON, chapter=49)
        assert non_outline_key.is_outline_segment is False

        global_outline_key = LockKey(resource=ResourceName.OUTLINE)
        assert global_outline_key.is_outline_segment is False

    def test_as_outline_segment(self) -> None:
        key = LockKey(resource=ResourceName.OUTLINE, chapter=49)
        assert key.as_outline_segment() == "outline_chapter_49"

    def test_as_project_outline_segment(self) -> None:
        key = LockKey(resource=ResourceName.OUTLINE, chapter=49, project_id="朱批录")
        assert key.as_outline_segment() == "朱批录:outline_chapter_49"

    def test_as_outline_segment_raises_for_non_outline(self) -> None:
        key = LockKey(resource=ResourceName.CANON, chapter=49)
        with pytest.raises(ValueError, match="not an outline segment"):
            key.as_outline_segment()

    def test_frozen_dataclass(self) -> None:
        key1 = LockKey(resource=ResourceName.OUTLINE, chapter=1)
        key2 = LockKey(resource=ResourceName.OUTLINE, chapter=1)
        assert key1 == key2
        assert hash(key1) == hash(key2)


class TestResourceLockManager:
    @pytest.fixture(autouse=True)
    def reset_manager(self) -> None:
        reset_resource_lock_manager()
        yield
        reset_resource_lock_manager()

    @pytest.fixture
    def mgr(self) -> ResourceLockManager:
        return ResourceLockManager()

    @pytest.mark.asyncio
    async def test_global_exclusive_lock(self, mgr: ResourceLockManager) -> None:
        async with mgr.lock(ResourceName.OUTLINE, ResourceLockType.EXCLUSIVE):
            assert mgr.is_locked(ResourceName.OUTLINE) is True
        assert mgr.is_locked(ResourceName.OUTLINE) is False

    @pytest.mark.asyncio
    async def test_global_shared_lock(self, mgr: ResourceLockManager) -> None:
        async with mgr.lock(ResourceName.OUTLINE, ResourceLockType.SHARED):
            assert mgr.get_shared_count(ResourceName.OUTLINE) == 1
            assert mgr.is_locked(ResourceName.OUTLINE) is True
        assert mgr.get_shared_count(ResourceName.OUTLINE) == 0

    @pytest.mark.asyncio
    async def test_multiple_shared_holders(self, mgr: ResourceLockManager) -> None:
        counts: list[int] = []
        release = asyncio.Event()

        async def acquire_shared() -> None:
            async with mgr.lock(ResourceName.CANON, ResourceLockType.SHARED):
                counts.append(mgr.get_shared_count(ResourceName.CANON))
                await release.wait()

        # Start 3 tasks - they should all be able to acquire SHARED lock
        t1 = asyncio.create_task(acquire_shared())
        t2 = asyncio.create_task(acquire_shared())
        t3 = asyncio.create_task(acquire_shared())

        # Give them time to all acquire
        await asyncio.sleep(0.1)

        # All should be holding the lock simultaneously (counts should be 1, 2, 3)
        assert len(counts) == 3, f"Expected 3 holders, got {len(counts)}"
        # The fact that we got [1, 2, 3] proves SHARED mode works - multiple tasks held simultaneously
        assert counts == [1, 2, 3], f"Expected [1, 2, 3], got {counts}"

        release.set()
        await asyncio.gather(t1, t2, t3)

    @pytest.mark.asyncio
    async def test_exclusive_holders_are_serialized(self, mgr: ResourceLockManager) -> None:
        active = 0
        max_active = 0

        async def acquire_exclusive() -> None:
            nonlocal active, max_active
            async with mgr.lock(ResourceName.STATE, ResourceLockType.EXCLUSIVE):
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)
                active -= 1

        await asyncio.gather(acquire_exclusive(), acquire_exclusive())

        assert max_active == 1

    @pytest.mark.asyncio
    async def test_shared_waits_for_active_exclusive(self, mgr: ResourceLockManager) -> None:
        exclusive_entered = asyncio.Event()
        release_exclusive = asyncio.Event()
        shared_entered = asyncio.Event()

        async def hold_exclusive() -> None:
            async with mgr.lock(ResourceName.STATE, ResourceLockType.EXCLUSIVE):
                exclusive_entered.set()
                await release_exclusive.wait()

        async def acquire_shared() -> None:
            await exclusive_entered.wait()
            async with mgr.lock(ResourceName.STATE, ResourceLockType.SHARED):
                shared_entered.set()

        exclusive_task = asyncio.create_task(hold_exclusive())
        shared_task = asyncio.create_task(acquire_shared())
        await exclusive_entered.wait()
        await asyncio.sleep(0)
        assert not shared_entered.is_set()
        release_exclusive.set()
        await asyncio.gather(exclusive_task, shared_task)
        assert shared_entered.is_set()

    @pytest.mark.asyncio
    async def test_same_task_exclusive_lock_is_reentrant(self, mgr: ResourceLockManager) -> None:
        async with mgr.lock(ResourceName.STATE, ResourceLockType.EXCLUSIVE):
            async with mgr.lock(ResourceName.STATE, ResourceLockType.EXCLUSIVE):
                assert mgr.current_task_holds(ResourceName.STATE)

    @pytest.mark.asyncio
    async def test_child_task_does_not_inherit_reentrant_access(
        self,
        mgr: ResourceLockManager,
    ) -> None:
        child_entered = asyncio.Event()

        async def child() -> None:
            async with mgr.lock(ResourceName.STATE, ResourceLockType.EXCLUSIVE):
                child_entered.set()

        async with mgr.lock(ResourceName.STATE, ResourceLockType.EXCLUSIVE):
            task = asyncio.create_task(child())
            await asyncio.sleep(0)
            assert not child_entered.is_set()
        await task
        assert child_entered.is_set()

    @pytest.mark.asyncio
    async def test_shared_to_exclusive_upgrade_is_rejected(
        self,
        mgr: ResourceLockManager,
    ) -> None:
        async with mgr.lock(ResourceName.STATE, ResourceLockType.SHARED):
            with pytest.raises(RuntimeError, match="Cannot upgrade shared lock"):
                async with mgr.lock(ResourceName.STATE, ResourceLockType.EXCLUSIVE):
                    pass

    @pytest.mark.asyncio
    async def test_chapter_lock(self, mgr: ResourceLockManager) -> None:
        async with mgr.lock(ResourceName.STATE, ResourceLockType.EXCLUSIVE, chapter=5):
            assert mgr.is_locked(ResourceName.STATE, chapter=5) is True
            assert mgr.is_locked(ResourceName.STATE, chapter=3) is False
        assert mgr.is_locked(ResourceName.STATE, chapter=5) is False

    @pytest.mark.asyncio
    async def test_project_scoped_chapter_locks_are_independent(
        self,
        mgr: ResourceLockManager,
    ) -> None:
        async with mgr.lock(
            ResourceName.STATE,
            ResourceLockType.EXCLUSIVE,
            chapter=2,
            project_id="与君共赴",
        ):
            assert mgr.is_locked(
                ResourceName.STATE,
                chapter=2,
                project_id="与君共赴",
            )
            assert not mgr.is_locked(
                ResourceName.STATE,
                chapter=2,
                project_id="朱批录",
            )
            async with mgr.lock(
                ResourceName.STATE,
                ResourceLockType.EXCLUSIVE,
                chapter=2,
                project_id="朱批录",
            ):
                assert mgr.is_locked(
                    ResourceName.STATE,
                    chapter=2,
                    project_id="朱批录",
                )

    @pytest.mark.asyncio
    async def test_project_scoped_outline_segments_are_independent(
        self,
        mgr: ResourceLockManager,
    ) -> None:
        async with mgr.acquire_outline_chapter(
            7,
            ResourceLockType.EXCLUSIVE,
            project_id="与君共赴",
        ):
            assert mgr.is_locked(
                ResourceName.OUTLINE,
                chapter=7,
                project_id="与君共赴",
            )
            assert not mgr.is_locked(
                ResourceName.OUTLINE,
                chapter=7,
                project_id="朱批录",
            )
            async with mgr.acquire_outline_chapter(
                7,
                ResourceLockType.EXCLUSIVE,
                project_id="朱批录",
            ):
                assert mgr.is_locked(
                    ResourceName.OUTLINE,
                    chapter=7,
                    project_id="朱批录",
                )

    @pytest.mark.asyncio
    async def test_outline_segment_lock(self, mgr: ResourceLockManager) -> None:
        async with mgr.acquire_outline_chapter(49, ResourceLockType.EXCLUSIVE):
            assert mgr.is_locked(ResourceName.OUTLINE, chapter=49) is True
        assert mgr.is_locked(ResourceName.OUTLINE, chapter=49) is False

    @pytest.mark.asyncio
    async def test_outline_segment_shared_lock(self, mgr: ResourceLockManager) -> None:
        async with mgr.acquire_outline_chapter(49, ResourceLockType.SHARED):
            assert mgr.get_shared_count(ResourceName.OUTLINE, chapter=49) == 1
        assert mgr.get_shared_count(ResourceName.OUTLINE, chapter=49) == 0

    @pytest.mark.asyncio
    async def test_outline_segment_shared_allows_multiple(self, mgr: ResourceLockManager) -> None:
        counts_49: list[int] = []
        release = asyncio.Event()

        async def acquire_shared_49() -> None:
            async with mgr.acquire_outline_chapter(49, ResourceLockType.SHARED):
                counts_49.append(mgr.get_shared_count(ResourceName.OUTLINE, chapter=49))
                await release.wait()

        t1 = asyncio.create_task(acquire_shared_49())
        t2 = asyncio.create_task(acquire_shared_49())

        await asyncio.sleep(0.1)

        assert len(counts_49) == 2, f"Expected 2 holders, got {len(counts_49)}"
        assert counts_49 == [1, 2], f"Expected [1, 2], got {counts_49}"

        release.set()
        await asyncio.gather(t1, t2)

    @pytest.mark.asyncio
    async def test_different_resources_no_conflict(self, mgr: ResourceLockManager) -> None:
        async with mgr.lock(ResourceName.OUTLINE, ResourceLockType.EXCLUSIVE):
            async with mgr.lock(ResourceName.CANON, ResourceLockType.EXCLUSIVE):
                async with mgr.lock(ResourceName.STATE, ResourceLockType.EXCLUSIVE):
                    pass

    @pytest.mark.asyncio
    async def test_negative_chapter_raises(self, mgr: ResourceLockManager) -> None:
        with pytest.raises(ValueError, match=r"non-negative"):
            async with mgr.acquire_outline_chapter(-1, ResourceLockType.SHARED):
                pass

    def test_unlocked_resource_returns_false(self, mgr: ResourceLockManager) -> None:
        assert mgr.is_locked(ResourceName.OUTLINE) is False
        assert mgr.is_locked(ResourceName.CANON, chapter=99) is False

    def test_shared_count_zero_for_unlocked(self, mgr: ResourceLockManager) -> None:
        assert mgr.get_shared_count(ResourceName.OUTLINE) == 0
        assert mgr.get_shared_count(ResourceName.CANON, chapter=1) == 0


class TestSingleton:
    @pytest.fixture(autouse=True)
    def reset_singleton(self) -> None:
        reset_resource_lock_manager()
        yield
        reset_resource_lock_manager()

    def test_get_resource_lock_manager_returns_same_instance(self) -> None:
        mgr1 = get_resource_lock_manager()
        mgr2 = get_resource_lock_manager()
        assert mgr1 is mgr2

    def test_reset_clears_singleton(self) -> None:
        mgr1 = get_resource_lock_manager()
        reset_resource_lock_manager()
        mgr2 = get_resource_lock_manager()
        assert mgr1 is not mgr2


class TestAsyncIntegration:
    @pytest.fixture(autouse=True)
    def reset_manager(self) -> None:
        reset_resource_lock_manager()
        yield
        reset_resource_lock_manager()

    @pytest.mark.asyncio
    async def test_concurrent_lock_acquisition(self) -> None:
        mgr = ResourceLockManager()
        results: list[bool] = []

        async def try_lock(chapter: int, lock_type: ResourceLockType) -> None:
            async with mgr.acquire_outline_chapter(chapter, lock_type):
                results.append(True)

        await asyncio.gather(
            try_lock(1, ResourceLockType.SHARED),
            try_lock(2, ResourceLockType.SHARED),
            try_lock(3, ResourceLockType.SHARED),
        )
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_exclusive_blocks_shared(self) -> None:
        mgr = ResourceLockManager()
        exclusive_held = asyncio.Event()
        shared_acquired = asyncio.Event()
        shared_got_lock = False

        async def exclusive_worker() -> None:
            async with mgr.acquire_outline_chapter(10, ResourceLockType.EXCLUSIVE):
                exclusive_held.set()
                await asyncio.sleep(0.1)
                exclusive_held.set()

        async def shared_worker() -> None:
            await exclusive_held.wait()
            async with mgr.acquire_outline_chapter(10, ResourceLockType.SHARED):
                nonlocal shared_got_lock
                shared_got_lock = True
                shared_acquired.set()

        await asyncio.gather(exclusive_worker(), shared_worker())
        assert shared_got_lock is True
