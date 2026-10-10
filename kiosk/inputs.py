"""Button input: a USB arcade button that types one key, turned into tap and hold events.

TapDetector is pure (times are passed in). KeyInput feeds it from a pynput
global keyboard listener and puts the resulting events on the EventBus.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from kiosk.events import Event, EventBus

log = logging.getLogger("kiosk")

POLL_INTERVAL_S = 0.05


class TapDetector:
    """Turns key down/up times into taps(n), hold_start and hold_end events.

    - A press shorter than hold_threshold_s is a tap. taps(n) is emitted once
      tap_window_s has passed since the last tap was released.
    - A press held for hold_threshold_s emits hold_start, and hold_end on release.
    - If taps are still waiting when a hold starts, they are emitted first.
    - Key auto-repeat (down again with no up in between) is ignored.
    """

    def __init__(self, hold_threshold_s: float, tap_window_s: float) -> None:
        self._hold_s = hold_threshold_s
        self._window_s = tap_window_s
        self._down_at: float | None = None
        self._holding = False
        self._taps = 0
        self._last_up: float | None = None

    def down(self, t: float) -> list[Event]:
        if self._down_at is not None:
            return []  # auto-repeat
        events = self._flush_taps_if_window_over(t)
        self._down_at = t
        self._holding = False
        return events

    def up(self, t: float) -> list[Event]:
        if self._down_at is None:
            return []
        events = self._start_hold_if_due(t)
        if self._holding:
            events.append(Event("hold_end"))
        else:
            self._taps += 1
            self._last_up = t
        self._down_at = None
        self._holding = False
        return events

    def poll(self, t: float) -> list[Event]:
        if self._down_at is not None:
            return self._start_hold_if_due(t)
        return self._flush_taps_if_window_over(t)

    def _start_hold_if_due(self, t: float) -> list[Event]:
        if self._down_at is None or self._holding or t - self._down_at < self._hold_s:
            return []
        self._holding = True
        return [*self._take_taps(), Event("hold_start")]

    def _flush_taps_if_window_over(self, t: float) -> list[Event]:
        if self._taps and self._last_up is not None and t - self._last_up >= self._window_s:
            return self._take_taps()
        return []

    def _take_taps(self) -> list[Event]:
        if not self._taps:
            return []
        n, self._taps = self._taps, 0
        return [Event("taps", n=n)]


def resolve_key(name: str, keyboard: Any) -> Any:
    """Config key name -> pynput key: Key[name] for named keys, KeyCode for one character."""
    name = name.strip()
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name.lower())
    try:
        return keyboard.Key[name.lower()]
    except KeyError:
        raise ValueError(
            f"Unknown button key {name!r} in config.toml [button] key "
            "(use a pynput key name such as 'f9', or a single character)"
        ) from None


def _import_keyboard() -> Any:
    import pynput.keyboard  # lazy: on Linux this fails without a display

    return pynput.keyboard


class KeyInput:
    """Global key listener for the button key, posting events to the bus.

    pynput calls back on its own thread and EventBus is not thread-safe, so the
    callbacks only hand the key time to the event loop; the detector, the poll
    and every bus.put then run on the loop thread.
    """

    def __init__(
        self,
        key: str,
        detector: TapDetector,
        bus: EventBus,
        loop: asyncio.AbstractEventLoop,
        *,
        listener_factory: Callable[..., Any] | None = None,
        keyboard: Any = None,
        poll_interval_s: float = POLL_INTERVAL_S,
    ) -> None:
        self._key_name = key
        self._detector = detector
        self._bus = bus
        self._loop = loop
        self._listener_factory = listener_factory
        self._keyboard = keyboard
        self._poll_interval_s = poll_interval_s
        self._target: Any = None
        self._listener: Any = None
        self._poll_handle: asyncio.Handle | None = None
        self._running = False

    def start(self) -> bool:
        """Start listening. Returns False (after a warning) if the listener cannot run.

        Raises ValueError for an unknown key name. A listener failure never stops
        the kiosk: the organiser's typed fallback still works without the button.
        """
        try:
            keyboard = self._keyboard or _import_keyboard()
        except Exception as exc:  # noqa: BLE001 - e.g. no display on Linux
            self._warn(exc)
            return False
        self._target = resolve_key(self._key_name, keyboard)
        factory = self._listener_factory or keyboard.Listener
        try:
            listener = factory(on_press=self._on_press, on_release=self._on_release)
            listener.start()
        except Exception as exc:  # noqa: BLE001 - no display, permissions, ...
            self._warn(exc)
            return False
        self._listener = listener
        self._running = True
        self._poll_handle = self._loop.call_soon_threadsafe(self._poll)
        log.info("Button input listening for key %r", self._key_name)
        return True

    def stop(self) -> None:
        self._running = False
        if self._poll_handle is not None:
            self._poll_handle.cancel()
            self._poll_handle = None
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception as exc:  # noqa: BLE001
                log.debug("button listener stop: %s", type(exc).__name__)
            self._listener = None

    def _warn(self, exc: Exception) -> None:
        log.warning(
            "Button input is off (%s: %s). The kiosk keeps running; "
            "use the organiser's typed request box instead.",
            type(exc).__name__, exc,
        )

    def _matches(self, key: Any) -> bool:
        char = getattr(self._target, "char", None)
        if char is not None:
            other = getattr(key, "char", None)
            return other is not None and other.lower() == char.lower()
        return key == self._target

    # pynput thread: only timestamp and hand over to the loop thread.
    def _on_press(self, key: Any) -> None:
        if self._matches(key):
            self._hand_over(self._handle_down, time.monotonic())

    def _on_release(self, key: Any) -> None:
        if self._matches(key):
            self._hand_over(self._handle_up, time.monotonic())

    def _hand_over(self, fn: Callable[[float], None], t: float) -> None:
        try:
            self._loop.call_soon_threadsafe(fn, t)
        except RuntimeError:
            pass  # loop already closed: the kiosk is shutting down

    # Loop thread from here on.
    def _handle_down(self, t: float) -> None:
        if self._running:
            self._emit(self._detector.down(t))

    def _handle_up(self, t: float) -> None:
        if self._running:
            self._emit(self._detector.up(t))

    def _poll(self) -> None:
        if not self._running:
            return
        self._emit(self._detector.poll(time.monotonic()))
        self._poll_handle = self._loop.call_later(self._poll_interval_s, self._poll)

    def _emit(self, events: list[Event]) -> None:
        for e in events:
            self._bus.put(e)
