"""Thread-safe in-memory event stream for UI and API job consumers."""

from __future__ import annotations

from collections.abc import Iterator
from queue import Empty, Full, Queue
from threading import Lock

from novel_forge.app_service.contracts import JobEvent

_SENTINEL = object()


class EventSubscription:
    def __init__(self, stream: "JobEventStream", queue: Queue[JobEvent | object]) -> None:
        self._stream = stream
        self._queue = queue
        self._closed = False

    def __iter__(self) -> Iterator[JobEvent]:
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                return
            yield item  # type: ignore[misc]

    def get(self, timeout: float | None = None) -> JobEvent | None:
        try:
            item = self._queue.get(timeout=timeout)
        except Empty:
            return None
        if item is _SENTINEL:
            return None
        return item  # type: ignore[return-value]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stream._unsubscribe(self._queue)
        while True:
            try:
                self._queue.put_nowait(_SENTINEL)
                return
            except Full:
                try:
                    self._queue.get_nowait()
                except Empty:
                    return


class JobEventStream:
    """Fan-out event stream with optional per-job subscriptions."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._subscribers: list[tuple[str | None, Queue[JobEvent | object]]] = []

    def publish(self, event: JobEvent) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for job_id, queue in subscribers:
            if job_id is None or job_id == event.job_id:
                try:
                    queue.put_nowait(event)
                except Full:
                    try:
                        queue.get_nowait()
                    except Empty:
                        pass
                    try:
                        queue.put_nowait(event)
                    except Full:
                        pass

    def subscribe(
        self,
        job_id: str | None = None,
        *,
        max_queue_size: int = 0,
    ) -> EventSubscription:
        queue: Queue[JobEvent | object] = Queue(maxsize=max(0, int(max_queue_size or 0)))
        with self._lock:
            self._subscribers.append((job_id, queue))
        return EventSubscription(self, queue)

    def _unsubscribe(self, queue: Queue[JobEvent | object]) -> None:
        with self._lock:
            self._subscribers = [
                (job_id, existing)
                for job_id, existing in self._subscribers
                if existing is not queue
            ]
