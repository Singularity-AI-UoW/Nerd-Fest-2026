"""The TV's idle slideshow feed: the latest gallery pictures plus the vote leaders.

Every interval it fetches /api/images and /api/leaderboard from the gallery and
sends one `slideshow` message to the TV. If the gallery cannot be reached, the TV
keeps the last list it was sent. The last good leaderboard is kept in `leaderboard`
for the winner screen (kiosk/winner.py), which sends the `winner` message after voting closes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import httpx

from kiosk.store import Store

log = logging.getLogger(__name__)

LATEST_IMAGES = 30
SOON_DELAY_S = 5.0  # let a new picture finish uploading before refreshing for it

Entries = list[dict]
FetchImages = Callable[[], Awaitable[tuple[Entries, Entries]]]


class Slideshow:
    def __init__(self, http: httpx.AsyncClient | None, gallery_url: str, hub, interval_s: float = 60,
                 *, fetch_images: FetchImages | None = None,
                 signup_qr: Callable[[], str | None] | None = None,
                 soon_delay_s: float = SOON_DELAY_S):
        """`fetch_images` replaces the gallery calls (--fake uses the local store).
        It returns (images, top3) and raises on failure."""
        self._http = http
        self._base = gallery_url.rstrip("/")
        self._hub = hub
        self.interval_s = interval_s
        self._fetch = fetch_images
        self._signup_qr = signup_qr
        self._soon_delay_s = soon_delay_s
        self._soon = asyncio.Event()
        self._images: Entries = []
        self._top3: Entries = []
        self._sent = False
        self.leaderboard: dict | None = None  # last good /api/leaderboard: {closed, top3}; None if never

    def refresh_soon(self) -> None:
        """Ask for a refresh in a few seconds (after a new picture is made)."""
        self._soon.set()

    async def run_forever(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception as exc:  # noqa: BLE001 - the idle screen must never stop the stall
                log.error("slideshow refresh failed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(self._soon.wait(), self.interval_s)
            except TimeoutError:
                continue
            await asyncio.sleep(self._soon_delay_s)
            self._soon.clear()

    async def refresh(self) -> None:
        """Fetch and send. Each list that fails to load keeps its last value."""
        changed = False
        if self._fetch is not None:
            try:
                images, top3 = await self._fetch()
            except Exception as exc:  # noqa: BLE001 - a bad source must not stop the show
                log.warning("slideshow source failed: %s", type(exc).__name__)
            else:
                self._images, self._top3, changed = images[:LATEST_IMAGES], top3, True
        else:
            images = await self._get("/api/images", "images")
            board = await self._get_leaderboard()
            top3 = board["top3"] if board is not None else None
            if board is not None:
                self.leaderboard = board
            if images is not None:
                self._images, changed = self._entries(images, with_votes=False)[:LATEST_IMAGES], True
            if top3 is not None:
                self._top3, changed = self._entries(top3, with_votes=True), True
        if changed or not self._sent:  # on a first failure, still send the (empty) card
            await self._send()

    async def _send(self) -> None:
        msg = {"type": "slideshow", "images": self._images, "top3": self._top3}
        qr = self._signup_qr() if self._signup_qr else None
        if qr:
            msg["signup_qr_svg"] = qr
        self._sent = True
        await self._hub.send(msg)

    async def _get(self, path: str, key: str) -> list | None:
        try:
            response = await self._http.get(self._base + path)
            response.raise_for_status()
            items = response.json()[key]
            if not isinstance(items, list):
                raise ValueError("not a list")
            return items
        except Exception as exc:  # noqa: BLE001 - network or shape errors: keep the last list
            log.warning("slideshow fetch %s failed: %s", path, type(exc).__name__)
            return None

    async def _get_leaderboard(self) -> dict | None:
        try:
            response = await self._http.get(self._base + "/api/leaderboard")
            response.raise_for_status()
            body = response.json()
            if not isinstance(body["top3"], list):
                raise ValueError("not a list")
            return {"closed": body.get("closed") is True, "top3": body["top3"]}
        except Exception as exc:  # noqa: BLE001 - network or shape errors: keep the last board
            log.warning("slideshow fetch /api/leaderboard failed: %s", type(exc).__name__)
            return None

    def absolute_url(self, url: str) -> str:
        return url if url.startswith(("http://", "https://")) else self._base + url

    def _entries(self, items: list, *, with_votes: bool) -> Entries:
        out = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not item["url"]:
                continue
            entry = {"url": self.absolute_url(item["url"]), "prompt": str(item.get("prompt") or "")}
            if with_votes:
                votes = item.get("votes")
                entry["votes"] = votes if isinstance(votes, int) else 0
            out.append(entry)
        return out


def local_images_source(store: Store, limit: int = LATEST_IMAGES) -> FetchImages:
    """For --fake: the latest local pictures, no leaderboard."""
    async def fetch() -> tuple[Entries, Entries]:
        recs = await asyncio.to_thread(store.recent, limit)
        return [{"url": f"/local/{r.id}.jpg", "prompt": r.prompt} for r in recs], []
    return fetch
