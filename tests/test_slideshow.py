"""Slideshow tests. The gallery is an httpx.MockTransport; nothing touches the network."""
import asyncio
import contextlib

import httpx

from kiosk.fakes import _draw_jpeg
from kiosk.slideshow import Slideshow, local_images_source
from kiosk.store import Store

BASE = "https://gallery.example"


class RecordingHub:
    def __init__(self):
        self.msgs: list[dict] = []

    async def send(self, msg: dict) -> None:
        self.msgs.append(msg)


class Gallery:
    """Answers /api/images and /api/leaderboard; either can be switched to fail."""

    def __init__(self, images=(), top3=()):
        self.images = list(images)
        self.top3 = list(top3)
        self.fail_images = False
        self.fail_leaderboard = False
        self.paths: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path == "/api/images":
            if self.fail_images:
                raise httpx.ConnectError("no route", request=request)
            return httpx.Response(200, json={"images": self.images})
        if request.url.path == "/api/leaderboard":
            if self.fail_leaderboard:
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(200, json={"closed": False, "top3": self.top3})
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def gallery_image(n: int) -> dict:
    return {"id": f"img{n:05d}", "prompt": f"prompt {n}", "mode": "kid",
            "created_at": f"2026-10-10T10:{n % 60:02d}:00+13:00", "url": f"/img/img{n:05d}.jpg"}


def top_entry(n: int, votes: int) -> dict:
    return {"id": f"img{n:05d}", "prompt": f"prompt {n}", "url": f"/img/img{n:05d}.jpg", "votes": votes}


def make(gallery: Gallery, hub=None, **kw):
    hub = hub or RecordingHub()
    return Slideshow(gallery.client(), BASE + "/", hub, **kw), hub


async def test_slideshow_sends_latest_30():
    # The gallery lists newest first, so the first 30 are the latest.
    g = Gallery([gallery_image(n) for n in range(40, 0, -1)], [top_entry(5, 3), top_entry(6, 1)])
    s, hub = make(g)
    await s.refresh()
    [msg] = hub.msgs
    assert msg["type"] == "slideshow"
    assert len(msg["images"]) == 30
    assert msg["images"][0] == {"url": f"{BASE}/img/img00040.jpg", "prompt": "prompt 40"}
    assert msg["images"][-1]["url"] == f"{BASE}/img/img00011.jpg"
    assert msg["top3"] == [
        {"url": f"{BASE}/img/img00005.jpg", "prompt": "prompt 5", "votes": 3},
        {"url": f"{BASE}/img/img00006.jpg", "prompt": "prompt 6", "votes": 1},
    ]
    assert "winner" not in {m["type"] for m in hub.msgs}
    assert sorted(g.paths) == ["/api/images", "/api/leaderboard"]


async def test_slideshow_keeps_last_on_error():
    g = Gallery([gallery_image(2), gallery_image(1)], [top_entry(2, 4)])
    s, hub = make(g)
    await s.refresh()
    first = hub.msgs[0]
    g.fail_images = True
    g.fail_leaderboard = True
    await s.refresh()
    assert hub.msgs == [first]  # nothing new sent, so the TV keeps showing the last list
    g.fail_images = False
    g.images = [gallery_image(3)]
    await s.refresh()  # images recover; the top 3 from before is kept
    assert [i["prompt"] for i in hub.msgs[-1]["images"]] == ["prompt 3"]
    assert hub.msgs[-1]["top3"] == first["top3"]


async def test_first_failure_still_sends_empty_slideshow():
    g = Gallery()
    g.fail_images = True
    g.fail_leaderboard = True
    s, hub = make(g)
    await s.refresh()
    assert hub.msgs == [{"type": "slideshow", "images": [], "top3": []}]


async def test_bad_entries_are_skipped_and_absolute_urls_kept():
    g = Gallery(
        [{"id": "a", "prompt": "no url"}, {"prompt": "bad", "url": 5},
         {"prompt": "full", "url": "https://cdn.example/x.jpg"}, gallery_image(1)],
        [{"prompt": "no url", "votes": 2}],
    )
    s, hub = make(g)
    await s.refresh()
    assert hub.msgs[0]["images"] == [
        {"url": "https://cdn.example/x.jpg", "prompt": "full"},
        {"url": f"{BASE}/img/img00001.jpg", "prompt": "prompt 1"},
    ]
    assert hub.msgs[0]["top3"] == []


async def test_signup_qr_sent_when_available():
    s, hub = make(Gallery([gallery_image(1)]), signup_qr=lambda: "<svg/>")
    await s.refresh()
    assert hub.msgs[0]["signup_qr_svg"] == "<svg/>"
    s, hub = make(Gallery([gallery_image(1)]), signup_qr=lambda: None)
    await s.refresh()
    assert "signup_qr_svg" not in hub.msgs[0]


async def test_picture_qr_added_to_pictures_but_not_top3():
    g = Gallery([gallery_image(2), {"prompt": "no id", "url": "/img/x.jpg"}], [top_entry(2, 4)])
    s, hub = make(g, picture_qr=lambda id: f"<svg>{id}</svg>")
    await s.refresh()
    msg = hub.msgs[0]
    assert msg["images"] == [
        {"url": f"{BASE}/img/img00002.jpg", "prompt": "prompt 2", "qr_svg": "<svg>img00002</svg>"},
        {"url": f"{BASE}/img/x.jpg", "prompt": "no id"},
    ]
    assert "qr_svg" not in msg["top3"][0]


async def test_odd_ids_get_no_qr():
    g = Gallery([{**gallery_image(1), "id": "../admin"}, {**gallery_image(2), "id": 7}])
    s, hub = make(g, picture_qr=lambda id: "<svg/>")
    await s.refresh()
    assert ["qr_svg" in i for i in hub.msgs[0]["images"]] == [False, False]


async def test_injected_source_gets_picture_qr_by_id():
    async def source():
        return [{"id": "abcdefgh", "url": "/local/abcdefgh.jpg", "prompt": "local"}], []

    hub = RecordingHub()
    s = Slideshow(None, BASE, hub, fetch_images=source, picture_qr=lambda id: f"<svg>{id}</svg>")
    await s.refresh()
    assert hub.msgs[0]["images"] == [
        {"url": "/local/abcdefgh.jpg", "prompt": "local", "qr_svg": "<svg>abcdefgh</svg>"}
    ]


async def test_injected_source_replaces_the_network():
    async def source():
        return [{"url": "/local/abc.jpg", "prompt": "local"}], []

    hub = RecordingHub()
    s = Slideshow(None, BASE, hub, fetch_images=source)
    await s.refresh()
    assert hub.msgs == [{"type": "slideshow", "images": [{"url": "/local/abc.jpg", "prompt": "local"}],
                         "top3": []}]


async def test_refresh_soon_refreshes_before_the_interval():
    g = Gallery([gallery_image(1)])
    s, hub = make(g, interval_s=3600, soon_delay_s=0)
    task = asyncio.create_task(s.run_forever())
    try:
        await _until(lambda: len(hub.msgs) == 1)
        g.images = [gallery_image(2), gallery_image(1)]
        s.refresh_soon()
        await _until(lambda: len(hub.msgs) == 2)
        assert len(hub.msgs[1]["images"]) == 2
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_run_forever_refreshes_every_interval():
    g = Gallery([gallery_image(1)])
    s, hub = make(g, interval_s=0.02)
    task = asyncio.create_task(s.run_forever())
    try:
        await _until(lambda: len(hub.msgs) >= 3)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_local_images_source_uses_store_with_local_urls(tmp_path):
    store = Store(tmp_path)
    recs = []
    for n in range(35):
        rec = store.new_record(f"prompt {n}", "kid")
        store.save_image(rec.id, _draw_jpeg("x"))
        recs.append(rec)
    images, top3 = await local_images_source(store)()
    assert top3 == []
    assert len(images) == 30
    assert images[0] == {"id": recs[-1].id, "url": f"/local/{recs[-1].id}.jpg", "prompt": "prompt 34"}


async def _until(cond, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        assert loop.time() < deadline, "timed out"
        await asyncio.sleep(0.005)
