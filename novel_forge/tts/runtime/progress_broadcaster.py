"""TTS progress broadcaster for real-time WebSocket push.

Provides a lightweight in-memory pub/sub channel keyed by
``project_id:chapter_number`` so WebSocket clients can subscribe to
TTS synthesis progress without polling.

Author: novel-forge
"""

from __future__ import annotations

import asyncio
from typing import Any

from novel_forge.obs.logger import get_logger

_log = get_logger("tts.runtime.progress_broadcaster")


class TTSProgressBroadcaster:
    """Fan-out broadcaster for TTS step events over asyncio.

    Usage::

        broadcaster = get_tts_progress_broadcaster()

        # Publisher side (in synthesis job):
        broadcaster.publish(project_id, chapter_number, "segment_done", {"index": 3})

        # Subscriber side (in WebSocket handler):
        queue = broadcaster.subscribe(project_id, chapter_number)
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        finally:
            broadcaster.unsubscribe(queue)

    Author: novel-forge
    """

    def __init__(self, max_queue_size: int = 128) -> None:
        self._max_queue_size = max_queue_size
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any] | None]]] = {}

    def _channel_key(self, project_id: str, chapter_number: int) -> str:
        return f"{project_id}:{chapter_number}"

    def subscribe(self, project_id: str, chapter_number: int) -> asyncio.Queue[dict[str, Any] | None]:
        """Create a subscription queue for a project/chapter channel."""
        key = self._channel_key(project_id, chapter_number)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=self._max_queue_size)
        if key not in self._subscribers:
            self._subscribers[key] = []
        self._subscribers[key].append(queue)
        _log.debug("TTS progress subscriber added for %s (total: %d)", key, len(self._subscribers[key]))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        """Remove a subscription queue from all channels."""
        for key in list(self._subscribers):
            try:
                self._subscribers[key].remove(queue)
            except ValueError:
                continue
            if not self._subscribers[key]:
                del self._subscribers[key]

    def publish(self, project_id: str, chapter_number: int, event: str, data: dict[str, Any]) -> None:
        """Broadcast a progress event to all subscribers of a channel."""
        key = self._channel_key(project_id, chapter_number)
        subscribers = self._subscribers.get(key)
        if not subscribers:
            return
        message = {"event": event, "data": data, "project_id": project_id, "chapter": chapter_number}
        for queue in subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Drop oldest event to make room.
                try:
                    queue.get_nowait()
                    queue.put_nowait(message)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def publish_job_complete(
        self,
        project_id: str,
        chapter_number: int,
        *,
        success: bool,
        error: str = "",
    ) -> None:
        """Broadcast a terminal event indicating job completion or failure."""
        self.publish(
            project_id,
            chapter_number,
            "job_complete" if success else "job_failed",
            {"success": success, "error": error},
        )

    @property
    def active_channels(self) -> int:
        """Number of channels with active subscribers."""
        return len(self._subscribers)


# Module-level singleton.
_broadcaster: TTSProgressBroadcaster | None = None


def get_tts_progress_broadcaster() -> TTSProgressBroadcaster:
    """Return the module-level shared TTS progress broadcaster.

    Author: novel-forge
    """
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = TTSProgressBroadcaster()
    return _broadcaster
