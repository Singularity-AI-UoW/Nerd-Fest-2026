"""Uploader tests. The gallery is an httpx.MockTransport; nothing touches the network."""
import asyncio
import logging
from datetime import datetime
from email.parser import BytesParser
from email.policy import HTTP

import httpx
import pytest

from kiosk.fakes import _draw_jpeg
from kiosk.store import Store
from kiosk.uploader import BACKOFF_MAX_S, BACKOFF_START_S, Uploader

BASE = "https://gallery.example"
TOKEN = "test-token-not-real"


class Gallery:
    """Records requests and answers with a fixed status (or raises a connect error)."""

    def __init__(self, status: int = 200, on_request=None):
        self.status = status
        self.fail_connect = False
        self.requests: list[httpx.Request] = []
        self.on_request = on_request

    def handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        if self.on_request:
            self.on_request(request)
        if self.fail_connect:
            raise httpx.ConnectError("no route", request=request)
        return httpx.Response(self.status, json={"ok": self.status < 400})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def make_record(store: Store, prompt: str = "a dragon riding a bike", mode: str = "kid"):
    rec = store.new_record(prompt, mode)
    store.save_image(rec.id, _draw_jpeg(prompt))
    return rec


async def wait_until(cond, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        assert loop.time() < deadline, "condition not met in time"
        await asyncio.sleep(0.01)


def parse_multipart(request: httpx.Request) -> dict:
    """Map field name -> (content_type, filename, bytes)."""
    raw = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
    msg = BytesParser(policy=HTTP).parsebytes(raw)
    out = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        out[name] = (part.get_content_type(), part.get_filename(), part.get_payload(decode=True))
    return out


async def test_upload_once_posts_multipart_with_bearer(tmp_path):
    store = Store(tmp_path)
    rec = make_record(store)
    gallery = Gallery()
    async with gallery.client() as http:
        ok = await Uploader(store, BASE, TOKEN, http).upload_once(rec)

    assert ok is True
    (req,) = gallery.requests
    assert req.method == "POST"
    assert str(req.url) == f"{BASE}/api/upload"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.headers["content-type"].startswith("multipart/form-data")
    fields = parse_multipart(req)
    assert fields["id"][2].decode() == rec.id
    assert fields["prompt"][2].decode() == rec.prompt
    assert fields["mode"][2].decode() == "kid"
    created = datetime.fromisoformat(fields["created_at"][2].decode())
    assert created.tzinfo is not None
    assert created == rec.created_at
    content_type, filename, data = fields["image"]
    assert content_type == "image/jpeg"
    assert filename == f"{rec.id}.jpg"
    assert data == store.image_path(rec.id).read_bytes()
    assert store.pending_uploads() == []


async def test_upload_trims_prompt_to_gallery_limit(tmp_path):
    store = Store(tmp_path)
    rec = make_record(store, prompt="x" * 600)
    gallery = Gallery()
    async with gallery.client() as http:
        assert await Uploader(store, BASE, TOKEN, http).upload_once(rec)
    assert len(parse_multipart(gallery.requests[0])["prompt"][2].decode()) == 500


@pytest.mark.parametrize("status", [401, 500])
async def test_failure_keeps_pending(tmp_path, status):
    store = Store(tmp_path)
    rec = make_record(store)
    async with Gallery(status=status).client() as http:
        assert await Uploader(store, BASE, TOKEN, http).upload_once(rec) is False
    assert [r.id for r in store.pending_uploads()] == [rec.id]


async def test_network_error_keeps_pending_and_never_logs_token(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    store = Store(tmp_path)
    rec = make_record(store)
    gallery = Gallery()
    gallery.fail_connect = True
    async with gallery.client() as http:
        uploader = Uploader(store, BASE, TOKEN, http)
        assert await uploader.upload_once(rec) is False
        assert await uploader.delete(rec.id) is False
    assert [r.id for r in store.pending_uploads()] == [rec.id]
    assert caplog.records  # failures are logged...
    assert TOKEN not in caplog.text  # ...but never with the token


async def test_pending_survives_restart(tmp_path):
    store = Store(tmp_path)
    rec = make_record(store)
    async with Gallery(status=503).client() as http:
        uploader = Uploader(store, BASE, TOKEN, http)
        uploader.enqueue(rec)
        assert await uploader.upload_once(rec) is False

    # A fresh process: new Store and Uploader on the same data dir.
    store2 = Store(tmp_path)
    assert [r.id for r in store2.pending_uploads()] == [rec.id]
    gallery = Gallery()
    async with gallery.client() as http:
        uploader2 = Uploader(store2, BASE, TOKEN, http)
        assert uploader2.queue_size() == 1
        task = asyncio.create_task(uploader2.run_forever())
        try:
            await wait_until(lambda: not store2.pending_uploads())
        finally:
            task.cancel()
    assert store2.pending_uploads() == []
    assert uploader2.queue_size() == 0
    assert [str(r.url) for r in gallery.requests] == [f"{BASE}/api/upload"]


async def test_enqueue_wakes_the_loop(tmp_path):
    store = Store(tmp_path)
    gallery = Gallery()
    async with gallery.client() as http:
        uploader = Uploader(store, BASE, TOKEN, http)
        task = asyncio.create_task(uploader.run_forever())
        try:
            await asyncio.sleep(0.05)  # idle: nothing pending
            assert gallery.requests == []
            rec = make_record(store)
            uploader.enqueue(rec)
            await wait_until(lambda: not store.pending_uploads())
        finally:
            task.cancel()
    assert store.pending_uploads() == []


async def test_backoff_doubles_to_max_and_resets_after_success(tmp_path):
    assert (BACKOFF_START_S, BACKOFF_MAX_S) == (5, 60)
    store = Store(tmp_path)
    make_record(store)
    gallery = Gallery(status=500)
    delays: list[float] = []

    async def fake_sleep(s: float) -> None:
        delays.append(s)
        if len(delays) == 6:
            gallery.status = 200  # the gallery comes back
        if len(delays) >= 7:
            await asyncio.Event().wait()  # park the loop so the test can look
        await asyncio.sleep(0)

    async with gallery.client() as http:
        uploader = Uploader(store, BASE, TOKEN, http)
        uploader._sleep = fake_sleep
        task = asyncio.create_task(uploader.run_forever())
        try:
            await wait_until(lambda: not store.pending_uploads())
            gallery.status = 500  # fails again: the backoff starts over
            uploader.enqueue(make_record(store))
            await wait_until(lambda: len(delays) >= 7)
        finally:
            task.cancel()

    assert delays == [5, 10, 20, 40, 60, 60, 5]


async def test_delete_calls_gallery(tmp_path):
    gallery = Gallery()
    async with gallery.client() as http:
        assert await Uploader(Store(tmp_path), BASE, TOKEN, http).delete("abcdefgh") is True
    (req,) = gallery.requests
    assert req.method == "DELETE"
    assert str(req.url) == f"{BASE}/api/images/abcdefgh"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"


async def test_delete_failure_returns_false(tmp_path):
    async with Gallery(status=500).client() as http:
        assert await Uploader(Store(tmp_path), BASE, TOKEN, http).delete("abcdefgh") is False


async def test_record_removed_during_upload_is_deleted_from_gallery(tmp_path):
    store = Store(tmp_path)
    rec = make_record(store)

    def remove_mid_flight(request: httpx.Request) -> None:
        if request.method == "POST":
            store.remove(rec.id)  # the organiser pressed Remove while it was uploading

    gallery = Gallery(on_request=remove_mid_flight)
    async with gallery.client() as http:
        await Uploader(store, BASE, TOKEN, http).upload_once(rec)
    assert [(r.method, str(r.url)) for r in gallery.requests] == [
        ("POST", f"{BASE}/api/upload"),
        ("DELETE", f"{BASE}/api/images/{rec.id}"),
    ]
