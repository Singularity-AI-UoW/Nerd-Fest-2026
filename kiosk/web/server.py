"""FastAPI server: WebSocket hub plus the TV, organiser and studio pages."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Annotated, Awaitable, Callable, Literal, Protocol

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, StrictInt, StringConstraints

from kiosk.events import Event, EventBus
from kiosk.preflight import PREFLIGHT
from kiosk.store import Store

STATIC_DIR = Path(__file__).parent / "static"
_IMAGE_ID = re.compile(r"^[a-z2-7]{8}$")
_SEND_TIMEOUT_S = 2.0
_CHECK_NAMES = {name for name, _ in PREFLIGHT}

CheckRunner = Callable[[str], Awaitable[dict]]


class Controls(Protocol):
    async def set_paused(self, p: bool) -> None: ...
    async def remove(self, id: str) -> bool: ...
    def winners(self) -> list[dict]: ...


class Hub:
    """Fans messages out to every connected page. The last `state`, `status` and
    `slideshow` messages are kept and replayed to pages that connect later."""

    def __init__(self) -> None:
        self.clients: set = set()
        self.last_state: dict = {}
        self.last_status: dict = {}
        self.last_slideshow: dict = {}

    async def send(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "state":
            self.last_state = msg
        elif kind == "status":
            self.last_status = msg
        elif kind == "slideshow":
            self.last_slideshow = msg
        for ws in list(self.clients):
            await self._send_one(ws, msg)

    async def _send_one(self, ws, msg: dict) -> None:
        try:
            await asyncio.wait_for(ws.send_json(msg), _SEND_TIMEOUT_S)
        except Exception:  # dead or stalled socket: drop it, keep the others going
            self.clients.discard(ws)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        # Read each value at send time so a replay is never older than a live message.
        if self.last_state:
            await self._send_one(ws, self.last_state)
        if self.last_status:
            await self._send_one(ws, self.last_status)
        if self.last_slideshow:
            await self._send_one(ws, self.last_slideshow)


class TypedBody(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class TweakBody(BaseModel):
    n: Annotated[StrictInt, Field(ge=0, le=4)]


class StepBody(BaseModel):
    action: Literal["kid", "nerd", "go", "retry", "next"]


class PauseBody(BaseModel):
    paused: StrictBool


class RemoveBody(BaseModel):
    id: Annotated[str, StringConstraints(pattern=r"^(last|[a-z2-7]{8})$")]


def create_app(hub: Hub, bus: EventBus, store: Store, controls: Controls,
               check_runner: CheckRunner | None = None) -> FastAPI:
    app = FastAPI(title="Nerdfest stall", docs_url=None, redoc_url=None, openapi_url=None)

    def page(name: str):
        return FileResponse(STATIC_DIR / name, media_type="text/html", headers={"Cache-Control": "no-store"})

    @app.get("/tv")
    async def tv():
        return page("tv.html")

    @app.get("/organiser")
    async def organiser():
        return page("organiser.html")

    @app.get("/check")
    async def check_page():
        return page("check.html")

    @app.get("/studio")
    async def studio():
        return page("studio.html")

    @app.get("/local/{image_id}.jpg")
    async def local_image(image_id: str):
        if not _IMAGE_ID.fullmatch(image_id):
            raise HTTPException(404)
        path = store.image_path(image_id)
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await hub.connect(ws)
        try:
            while True:
                await ws.receive_text()  # pages send nothing; this notices disconnects
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    @app.post("/api/typed")
    async def typed(body: TypedBody):
        bus.put(Event("typed_request", text=body.text))
        return {"ok": True}

    @app.post("/api/tweak")
    async def tweak(body: TweakBody):
        bus.put(Event("tweak_choice", n=body.n))
        return {"ok": True}

    @app.post("/api/step")
    async def step(body: StepBody):
        bus.put(Event("step", text=body.action))
        return {"ok": True}

    @app.post("/api/pause")
    async def pause(body: PauseBody):
        await controls.set_paused(body.paused)
        return {"ok": True}

    @app.post("/api/remove")
    async def remove(body: RemoveBody):
        return {"removed": await controls.remove(body.id)}

    @app.get("/api/winners")
    async def winners():
        return controls.winners()

    @app.post("/api/check/{name}")
    async def run_one_check(name: str):
        if name not in _CHECK_NAMES:
            raise HTTPException(404, "No such check")
        if check_runner is None:
            raise HTTPException(503, "Checks are not available")
        return await check_runner(name)

    @app.get("/api/status")
    async def status():
        return hub.last_status

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
