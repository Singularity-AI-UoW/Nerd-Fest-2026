import asyncio
import enum
import logging
import threading

import pytest

from kiosk.events import Event, EventBus
from kiosk.inputs import KeyInput, TapDetector, resolve_key

HOLD = 0.4
WINDOW = 0.4


def make() -> TapDetector:
    return TapDetector(HOLD, WINDOW)


def press(d: TapDetector, t0: float, t1: float) -> list[Event]:
    """A press from t0 to t1, collecting everything emitted."""
    return d.down(t0) + d.up(t1)


def test_single_tap():
    d = make()
    assert press(d, 0.0, 0.1) == []
    assert d.poll(0.3) == []  # window not over yet
    assert d.poll(0.5) == [Event("taps", n=1)]
    assert d.poll(5.0) == []  # emitted once only


def test_double_tap():
    d = make()
    assert press(d, 0.0, 0.1) == []
    assert press(d, 0.3, 0.4) == []
    assert d.poll(0.6) == []  # window restarts from the last release
    assert d.poll(0.8) == [Event("taps", n=2)]


def test_four_taps():
    d = make()
    t = 0.0
    for _ in range(4):
        assert press(d, t, t + 0.1) == []
        t += 0.25
    assert d.poll(t + 0.5) == [Event("taps", n=4)]


def test_hold_emits_start_then_end_no_taps():
    d = make()
    assert d.down(0.0) == []
    assert d.poll(0.2) == []
    assert d.poll(0.45) == [Event("hold_start")]
    assert d.poll(1.0) == []  # hold_start only once
    assert d.up(2.0) == [Event("hold_end")]
    assert d.poll(5.0) == []  # a hold is never also a tap


def test_hold_detected_on_release_if_poll_missed_it():
    d = make()
    d.down(0.0)
    assert d.up(1.0) == [Event("hold_start"), Event("hold_end")]


def test_autorepeat_ignored():
    d = make()
    d.down(0.0)
    # The OS repeats key-down while held; none of these may restart the press.
    assert d.down(0.05) == []
    assert d.down(0.10) == []
    assert d.up(0.15) == []
    assert d.poll(0.7) == [Event("taps", n=1)]


def test_autorepeat_during_hold_ignored():
    d = make()
    d.down(0.0)
    assert d.poll(0.5) == [Event("hold_start")]
    assert d.down(0.55) == []
    assert d.down(0.60) == []
    assert d.up(1.0) == [Event("hold_end")]


def test_tap_then_hold():
    d = make()
    assert press(d, 0.0, 0.1) == []
    assert d.down(0.3) == []
    # The tap is reported first, then the hold, so the order matches what happened.
    assert d.poll(0.75) == [Event("taps", n=1), Event("hold_start")]
    assert d.up(1.5) == [Event("hold_end")]
    assert d.poll(3.0) == []


def test_tap_after_window_is_a_new_gesture():
    d = make()
    press(d, 0.0, 0.1)
    # The window passed before poll ran; the next press must flush it, not join it.
    assert d.down(1.0) == [Event("taps", n=1)]
    assert d.up(1.1) == []
    assert d.poll(1.6) == [Event("taps", n=1)]


def test_stray_up_ignored():
    d = make()
    assert d.up(0.0) == []
    assert d.poll(1.0) == []


# --- KeyInput -------------------------------------------------------------


class FakeKey(enum.Enum):
    f9 = 1
    space = 2


class FakeKeyCode:
    def __init__(self, char):
        self.char = char

    @classmethod
    def from_char(cls, char):
        return cls(char)


class FakeKeyboard:
    Key = FakeKey
    KeyCode = FakeKeyCode


class FakeListener:
    def __init__(self, on_press, on_release):
        self.on_press, self.on_release = on_press, on_release
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def test_resolve_named_key():
    assert resolve_key("f9", FakeKeyboard) is FakeKey.f9
    assert resolve_key(" F9 ", FakeKeyboard) is FakeKey.f9


def test_resolve_single_character():
    assert resolve_key("a", FakeKeyboard).char == "a"


@pytest.mark.parametrize("bad", ["f99", "", "banana"])
def test_unknown_key_raises_naming_it(bad):
    with pytest.raises(ValueError, match=repr(bad.strip())):
        resolve_key(bad, FakeKeyboard)


def test_real_pynput_knows_f9():
    keyboard = pytest.importorskip("pynput.keyboard")  # needs a display on Linux
    assert resolve_key("f9", keyboard) is keyboard.Key.f9


def make_input(loop, bus, key="f9", factory=None):
    holder = {}

    def default_factory(on_press, on_release):
        holder["listener"] = FakeListener(on_press, on_release)
        return holder["listener"]

    ki = KeyInput(
        key, TapDetector(0.05, 0.05), bus, loop,
        listener_factory=factory or default_factory, keyboard=FakeKeyboard,
        poll_interval_s=0.01,
    )
    return ki, holder


async def test_unknown_key_fails_at_start():
    loop = asyncio.get_running_loop()
    ki, _ = make_input(loop, EventBus(), key="f99")
    with pytest.raises(ValueError, match="'f99'"):
        ki.start()


async def test_listener_failure_warns_and_keeps_running(caplog):
    loop = asyncio.get_running_loop()

    def broken(on_press, on_release):
        raise RuntimeError("no display")

    ki, _ = make_input(loop, EventBus(), factory=broken)
    with caplog.at_level(logging.WARNING, logger="kiosk"):
        assert ki.start() is False
    assert "button" in caplog.text.lower()
    assert "no display" in caplog.text
    ki.stop()  # safe even though nothing started


async def test_listener_start_failure_warns_and_keeps_running(caplog):
    loop = asyncio.get_running_loop()

    class Failing(FakeListener):
        def start(self):
            raise PermissionError("not allowed")

    ki, _ = make_input(loop, EventBus(), factory=Failing)
    with caplog.at_level(logging.WARNING, logger="kiosk"):
        assert ki.start() is False
    assert "not allowed" in caplog.text


async def test_key_events_become_bus_events():
    loop = asyncio.get_running_loop()
    bus = EventBus()
    ki, holder = make_input(loop, bus)
    assert ki.start() is True
    listener = holder["listener"]
    assert listener.started
    try:
        listener.on_press(FakeKey.space)  # other keys are ignored
        listener.on_release(FakeKey.space)
        listener.on_press(FakeKey.f9)
        listener.on_release(FakeKey.f9)
        assert await bus.next(1.0) == Event("taps", n=1)
        assert await bus.next(0.1) is None
    finally:
        ki.stop()
    assert listener.stopped


async def test_hold_through_listener_thread():
    loop = asyncio.get_running_loop()
    bus = EventBus()
    ki, holder = make_input(loop, bus)
    ki.start()
    listener = holder["listener"]
    try:
        thread_ids = []

        def from_other_thread():
            thread_ids.append(threading.get_ident())
            listener.on_press(FakeKey.f9)

        t = threading.Thread(target=from_other_thread)
        t.start()
        t.join()
        assert await bus.next(1.0) == Event("hold_start")

        t = threading.Thread(target=lambda: listener.on_release(FakeKey.f9))
        t.start()
        t.join()
        assert await bus.next(1.0) == Event("hold_end")
    finally:
        ki.stop()


async def test_character_key_matches_either_case():
    loop = asyncio.get_running_loop()
    bus = EventBus()
    ki, holder = make_input(loop, bus, key="a")
    ki.start()
    listener = holder["listener"]
    try:
        listener.on_press(FakeKeyCode("A"))  # shift held
        listener.on_release(FakeKeyCode("A"))
        assert await bus.next(1.0) == Event("taps", n=1)
    finally:
        ki.stop()
