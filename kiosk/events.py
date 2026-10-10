"""Events passed from input sources (button, STT, organiser box) to the state machine."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

# kinds: taps (n), hold_start, hold_end, typed_request (text),
# tweak_choice (n 1-4, 0 = keep), caption (text, partial),
# transcript (text, final), stt_error


@dataclass(frozen=True)
class Event:
    kind: str
    text: str | None = None
    n: int | None = None


class EventBus:
    def __init__(self) -> None:
        self._q: asyncio.Queue[Event] = asyncio.Queue()

    def put(self, e: Event) -> None:
        self._q.put_nowait(e)

    async def next(self, timeout: float | None) -> Event | None:
        try:
            return await asyncio.wait_for(self._q.get(), timeout)
        except asyncio.TimeoutError:
            return None

    def clear(self) -> None:
        while not self._q.empty():
            self._q.get_nowait()
