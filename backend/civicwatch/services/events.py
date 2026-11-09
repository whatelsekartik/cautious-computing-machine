"""Tiny in-process pub/sub used by the SSE endpoint for live UI updates."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any

log = logging.getLogger("civicwatch.events")

_MAX_QUEUE = 200


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._sequence = 0

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=_MAX_QUEUE)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._sequence += 1
            message = {
                "type": event_type,
                "seq": self._sequence,
                "ts": time.time(),
                "data": payload,
            }
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(message)
            except queue.Full:  # drop the oldest so the newest state always wins
                try:
                    q.get_nowait()
                    q.put_nowait(message)
                except queue.Empty:  # pragma: no cover
                    pass

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


bus = EventBus()


def publish(event_type: str, payload: dict[str, Any]) -> None:
    bus.publish(event_type, payload)


def sse_format(message: dict[str, Any]) -> str:
    return f"event: {message['type']}\ndata: {json.dumps(message)}\n\n"
