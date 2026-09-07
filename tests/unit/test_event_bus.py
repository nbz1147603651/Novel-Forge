"""Tests for novel_forge.core.infra.event_bus."""

from __future__ import annotations

import asyncio

import pytest

from novel_forge.core.infra.event_bus import (
    CHAPTER_COMPLETED,
    INIT_COMPLETED,
    INIT_STARTED,
    EventBus,
    ProjectEvent,
)


class TestProjectEvent:
    def test_create_basic(self) -> None:
        event = ProjectEvent(project_id="p1", event_type=CHAPTER_COMPLETED)
        assert event.project_id == "p1"
        assert event.event_type == CHAPTER_COMPLETED
        assert event.timestamp is not None

    def test_create_with_data(self) -> None:
        event = ProjectEvent(project_id="p1", event_type=CHAPTER_COMPLETED, data={"chapter": 5})
        assert event.data == {"chapter": 5}

    def test_create_without_project_id(self) -> None:
        event = ProjectEvent(project_id=None, event_type=INIT_STARTED)
        assert event.project_id is None
        assert event.event_type == INIT_STARTED


class TestEventBusSubscribe:
    def test_subscribe_global(self) -> None:
        bus = EventBus()

        def callback(event: ProjectEvent) -> None:
            pass

        bus.subscribe(INIT_STARTED, callback)
        assert len(bus.get_subscribers(INIT_STARTED)) == 1

    def test_subscribe_project_specific(self) -> None:
        bus = EventBus()

        def callback(event: ProjectEvent) -> None:
            pass

        bus.subscribe(INIT_STARTED, callback, project_id="p1")
        assert len(bus.get_subscribers(INIT_STARTED, project_id="p1")) == 1

    def test_subscribe_prevents_duplicate(self) -> None:
        bus = EventBus()

        def callback(event: ProjectEvent) -> None:
            pass

        bus.subscribe(INIT_STARTED, callback)
        bus.subscribe(INIT_STARTED, callback)
        assert len(bus.get_subscribers(INIT_STARTED)) == 1

    def test_subscribe_rejects_non_callable(self) -> None:
        bus = EventBus()
        with pytest.raises(TypeError, match="callback must be callable"):
            bus.subscribe(INIT_STARTED, "not a callback")  # type: ignore


class TestEventBusUnsubscribe:
    def test_unsubscribe_global(self) -> None:
        bus = EventBus()

        def callback(event: ProjectEvent) -> None:
            pass

        bus.subscribe(INIT_STARTED, callback)
        bus.unsubscribe(INIT_STARTED, callback)
        assert len(bus.get_subscribers(INIT_STARTED)) == 0

    def test_unsubscribe_project_specific(self) -> None:
        bus = EventBus()

        def callback(event: ProjectEvent) -> None:
            pass

        bus.subscribe(INIT_STARTED, callback, project_id="p1")
        bus.unsubscribe(INIT_STARTED, callback, project_id="p1")
        assert len(bus.get_subscribers(INIT_STARTED, project_id="p1")) == 0

    def test_unsubscribe_idempotent(self) -> None:
        bus = EventBus()

        def callback(event: ProjectEvent) -> None:
            pass

        bus.unsubscribe(INIT_STARTED, callback)


class TestEventBusPublish:
    @pytest.mark.asyncio
    async def test_publish_triggers_global_subscriber(self) -> None:
        bus = EventBus()
        received: list[ProjectEvent] = []

        async def callback(event: ProjectEvent) -> None:
            received.append(event)

        bus.subscribe(CHAPTER_COMPLETED, callback)
        await bus.publish(ProjectEvent(project_id="p1", event_type=CHAPTER_COMPLETED))
        await asyncio.sleep(0)
        assert len(received) == 1
        assert received[0].project_id == "p1"

    @pytest.mark.asyncio
    async def test_publish_triggers_project_subscriber(self) -> None:
        bus = EventBus()
        received: list[ProjectEvent] = []

        def callback(event: ProjectEvent) -> None:
            received.append(event)

        bus.subscribe(CHAPTER_COMPLETED, callback, project_id="p1")
        await bus.publish(ProjectEvent(project_id="p1", event_type=CHAPTER_COMPLETED))
        await asyncio.sleep(0)
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_publish_does_not_trigger_other_project(self) -> None:
        bus = EventBus()
        received: list[ProjectEvent] = []

        def callback(event: ProjectEvent) -> None:
            received.append(event)

        bus.subscribe(CHAPTER_COMPLETED, callback, project_id="p1")
        await bus.publish(ProjectEvent(project_id="p2", event_type=CHAPTER_COMPLETED))
        await asyncio.sleep(0)
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_global_subscriber_receives_all_events(self) -> None:
        bus = EventBus()
        received: list[ProjectEvent] = []

        def callback(event: ProjectEvent) -> None:
            received.append(event)

        bus.subscribe(CHAPTER_COMPLETED, callback)
        await bus.publish(ProjectEvent(project_id="p1", event_type=CHAPTER_COMPLETED))
        await bus.publish(ProjectEvent(project_id="p2", event_type=CHAPTER_COMPLETED))
        await asyncio.sleep(0)
        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_both_global_and_project_receive(self) -> None:
        bus = EventBus()
        global_received: list[ProjectEvent] = []
        project_received: list[ProjectEvent] = []

        def global_cb(event: ProjectEvent) -> None:
            global_received.append(event)

        def project_cb(event: ProjectEvent) -> None:
            project_received.append(event)

        bus.subscribe(CHAPTER_COMPLETED, global_cb)
        bus.subscribe(CHAPTER_COMPLETED, project_cb, project_id="p1")

        await bus.publish(ProjectEvent(project_id="p1", event_type=CHAPTER_COMPLETED))
        await asyncio.sleep(0)

        assert len(global_received) == 1
        assert len(project_received) == 1

    @pytest.mark.asyncio
    async def test_sync_callback_executed(self) -> None:
        bus = EventBus()
        received: list[ProjectEvent] = []

        def callback(event: ProjectEvent) -> None:
            received.append(event)

        bus.subscribe(CHAPTER_COMPLETED, callback)
        await bus.publish(ProjectEvent(project_id="p1", event_type=CHAPTER_COMPLETED))
        await asyncio.sleep(0)
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_async_callback_executed(self) -> None:
        bus = EventBus()
        received: list[ProjectEvent] = []

        async def callback(event: ProjectEvent) -> None:
            received.append(event)

        bus.subscribe(CHAPTER_COMPLETED, callback)
        await bus.publish(ProjectEvent(project_id="p1", event_type=CHAPTER_COMPLETED))
        await asyncio.sleep(0)
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_error_in_callback_isolated(self) -> None:
        bus = EventBus()
        received: list[ProjectEvent] = []

        def bad_callback(event: ProjectEvent) -> None:
            raise RuntimeError("test error")

        def good_callback(event: ProjectEvent) -> None:
            received.append(event)

        bus.subscribe(CHAPTER_COMPLETED, bad_callback)
        bus.subscribe(CHAPTER_COMPLETED, good_callback)
        await bus.publish(ProjectEvent(project_id="p1", event_type=CHAPTER_COMPLETED))
        await asyncio.sleep(0)
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_publish_no_subscribers(self) -> None:
        bus = EventBus()
        await bus.publish(ProjectEvent(project_id="p1", event_type=CHAPTER_COMPLETED))
        await asyncio.sleep(0)


class TestEventBusClear:
    def test_clear_removes_all(self) -> None:
        bus = EventBus()

        def cb1(event: ProjectEvent) -> None:
            pass

        def cb2(event: ProjectEvent) -> None:
            pass

        bus.subscribe(INIT_STARTED, cb1)
        bus.subscribe(CHAPTER_COMPLETED, cb2, project_id="p1")
        bus.clear()
        assert len(bus.get_subscribers(INIT_STARTED)) == 0
        assert len(bus.get_subscribers(CHAPTER_COMPLETED, project_id="p1")) == 0


class TestEventBusIntegration:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self) -> None:
        bus = EventBus()
        events: list[ProjectEvent] = []

        async def on_init_started(event: ProjectEvent) -> None:
            events.append(event)

        async def on_init_completed(event: ProjectEvent) -> None:
            events.append(event)

        async def on_chapter_completed(event: ProjectEvent) -> None:
            events.append(event)

        bus.subscribe(INIT_STARTED, on_init_started)
        bus.subscribe(INIT_COMPLETED, on_init_completed)
        bus.subscribe(CHAPTER_COMPLETED, on_chapter_completed)

        await bus.publish(ProjectEvent(project_id="p1", event_type=INIT_STARTED))
        await bus.publish(ProjectEvent(project_id="p1", event_type=INIT_COMPLETED))
        await bus.publish(ProjectEvent(project_id="p1", event_type=CHAPTER_COMPLETED))
        await asyncio.sleep(0)

        assert len(events) == 3
