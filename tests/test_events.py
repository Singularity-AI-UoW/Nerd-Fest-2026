import pytest

from kiosk.events import Event, EventBus


async def test_bus_timeout_returns_none():
    bus = EventBus()
    assert await bus.next(0.01) is None


async def test_bus_delivers_in_order():
    bus = EventBus()
    bus.put(Event("taps", n=2))
    bus.put(Event("typed_request", text="a cat"))
    assert await bus.next(0.1) == Event("taps", n=2)
    assert await bus.next(0.1) == Event("typed_request", text="a cat")


async def test_bus_clear_drops_pending():
    bus = EventBus()
    bus.put(Event("hold_start"))
    bus.put(Event("hold_end"))
    bus.clear()
    assert await bus.next(0.01) is None


def test_event_is_frozen():
    e = Event("stt_error")
    assert e.text is None and e.n is None
    with pytest.raises(AttributeError):
        e.kind = "taps"  # type: ignore[misc]
