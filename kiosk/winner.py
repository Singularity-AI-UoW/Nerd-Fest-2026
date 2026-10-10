"""The winner screen: after voting closes, the TV shows the winning picture.

`winners` joins the gallery's top 3 with the claim codes in the local store, for the
organiser page. `WinnerScreen` sends the TV the `winner` message while the show is idle.
That message never carries a claim code: the visitor shows theirs at the stall.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

from kiosk.store import Store

log = logging.getLogger(__name__)

WINNER_EVERY_S = 30.0  # the TV shows the card for 20 s, then the slideshow, so they alternate


def _top3(leaderboard: dict | None) -> list[dict]:
    """The usable entries (an id and a picture URL). The organiser table and the TV both use this."""
    items = leaderboard.get("top3") if isinstance(leaderboard, dict) else None
    if not isinstance(items, list):
        return []
    return [
        i for i in items
        if isinstance(i, dict) and isinstance(i.get("id"), str) and i["id"]
        and isinstance(i.get("url"), str) and i["url"]
    ]


def winners(leaderboard: dict | None, store: Store) -> list[dict]:
    """The top 3 with claim codes from the local store (None if the picture is not there)."""
    rows = []
    for rank, item in enumerate(_top3(leaderboard), start=1):
        votes = item.get("votes")
        rows.append({
            "rank": rank,
            "id": item["id"],
            "prompt": str(item.get("prompt") or ""),
            "votes": votes if isinstance(votes, int) else 0,
            "claim_code": store.claim_for(item["id"]),
        })
    return rows


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WinnerScreen:
    def __init__(self, close_at: datetime, slideshow, hub, *,
                 is_idle: Callable[[], bool], clock: Callable[[], datetime] = _utc_now,
                 every_s: float = WINNER_EVERY_S):
        """`slideshow` supplies the last good leaderboard and the absolute gallery URLs."""
        self._close_at = close_at
        self._slideshow = slideshow
        self._hub = hub
        self._is_idle = is_idle
        self._clock = clock
        self._every_s = every_s

    def _closed(self, leaderboard: dict | None) -> bool:
        flag = isinstance(leaderboard, dict) and leaderboard.get("closed") is True
        return flag or self._clock() >= self._close_at

    def message(self) -> dict | None:
        """The `winner` message, or None before voting closes or with no pictures in the top 3."""
        board = self._slideshow.leaderboard
        if not self._closed(board):
            return None
        entries = [
            {"url": self._slideshow.absolute_url(i["url"]), "prompt": str(i.get("prompt") or ""),
             "votes": i["votes"] if isinstance(i.get("votes"), int) else 0}
            for i in _top3(board)
        ]
        if not entries:
            return None
        first = entries[0]
        return {"type": "winner", "image_url": first["url"], "prompt": first["prompt"], "top3": entries}

    async def tick(self) -> bool:
        """Send the winner card if voting has closed and the show is idle. True if sent."""
        if not self._is_idle():
            return False
        msg = self.message()
        if msg is None:
            return False
        await self._hub.send(msg)
        return True

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - the winner card must never stop the stall
                log.error("winner screen failed: %s", type(exc).__name__)
            await asyncio.sleep(self._every_s)
