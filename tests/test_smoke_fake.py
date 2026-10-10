"""End-to-end smoke test: the whole stall in --fake --headless mode, in-process.

Types a request through the organiser API, picks tweak 2, and watches the TV's
WebSocket feed go agent -> tweak -> takehome -> idle. Uses the real server, Show,
agents, Safety and headless Chromium studio; only the LLM, images, voice and
uploader are fakes, so nothing touches the network.
"""
import asyncio
import contextlib
import json
import re
import socket
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from websockets.asyncio.client import connect

from kiosk.__main__ import fake_secrets, run_app
from kiosk.config import load_config
from kiosk.store import Store

pytestmark = pytest.mark.browser

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUEST = "a dragon riding a bike"
TURN_TIMEOUT_S = 30.0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def smoke_cfg(data_dir: Path):
    cfg = load_config(REPO_ROOT / "config.toml")
    timeouts = replace(cfg.timeouts, confirm_s=0.3, tweak_s=10, takehome_s=1, agent_cap_s=60)
    return replace(cfg, timeouts=timeouts, files=replace(cfg.files, data_dir=str(data_dir)))


async def wait_for_server(base: str, app_task: asyncio.Task) -> None:
    async with httpx.AsyncClient() as http:
        for _ in range(300):
            if app_task.done():
                exc = app_task.exception()
                if exc is not None and "Executable doesn't exist" in str(exc):
                    pytest.skip("Chromium not installed")
                raise RuntimeError("app stopped during start-up") from exc
            with contextlib.suppress(httpx.TransportError):
                if (await http.get(f"{base}/tv")).status_code == 200:
                    return
            await asyncio.sleep(0.1)
    raise RuntimeError("server did not start")


async def test_fake_turn_end_to_end(tmp_path):
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    app_task = asyncio.create_task(
        run_app(smoke_cfg(tmp_path), fake_secrets(), fake=True, headless=True, port=port)
    )
    try:
        await wait_for_server(base, app_task)
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws, httpx.AsyncClient() as http:

            async def next_msg() -> dict:
                return json.loads(await ws.recv())

            # The show is running once it has announced idle (replayed on connect).
            async with asyncio.timeout(20):
                while (m := await next_msg()) != {"type": "state", "state": "idle", "mode": ""}:
                    pass

            assert (await http.post(f"{base}/api/typed", json={"text": REQUEST})).json() == {"ok": True}

            states: list[str] = []
            tweaks: list[str] = []
            takehome: dict | None = None
            async with asyncio.timeout(TURN_TIMEOUT_S):
                while True:
                    m = await next_msg()
                    if m["type"] == "tweaks":
                        tweaks = m["options"]
                    elif m["type"] == "takehome":
                        takehome = m
                    elif m["type"] == "state":
                        states.append(m["state"])
                        if m["state"] == "tweak":
                            r = await http.post(f"{base}/api/tweak", json={"n": 2})
                            assert r.json() == {"ok": True}
                        if m["state"] == "idle":
                            break

            # agent -> tweak -> (agent) -> takehome -> idle, in that order.
            order = [s for s in states if s in ("agent", "tweak", "takehome", "idle")]
            assert order == ["agent", "tweak", "agent", "takehome", "idle"], states
            assert len(tweaks) == 4

            assert takehome is not None
            assert re.fullmatch(r"\d{4}", takehome["claim"])
            assert takehome["qr_svg"].startswith(("<svg", "<?xml"))
            m = re.fullmatch(r"/local/([a-z2-7]{8})\.jpg", takehome["image_url"])
            assert m, takehome["image_url"]
            image = await http.get(base + takehome["image_url"])
            assert image.status_code == 200
            assert image.headers["content-type"] == "image/jpeg"

            # Tweak 2 was applied, the record was stored, and the fake uploader took it.
            rec = Store(tmp_path / "fake").get(m.group(1))
            assert rec is not None
            assert rec.claim_code == takehome["claim"]
            assert rec.prompt.endswith(f"Tweak: {tweaks[1]}")
            assert rec.uploaded
    finally:
        app_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(app_task, 20)
