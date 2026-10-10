"""The organiser page's controls: pause the show, remove a picture, and the winners."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from kiosk.winner import Claims, winners

log = logging.getLogger(__name__)


async def _no_claims() -> Claims:
    return {}


class StallControls:
    def __init__(self, show, store, uploader, leaderboard: Callable[[], dict | None] = lambda: None,
                 claims: Callable[[], Awaitable[Claims | None]] = _no_claims):
        """`leaderboard` returns the latest gallery leaderboard, or None if there is none yet.
        `claims` fetches the contacts left for the top 3 (None if the gallery is unreachable)."""
        self._show = show
        self._store = store
        self._uploader = uploader
        self._leaderboard = leaderboard
        self._claims = claims

    async def set_paused(self, p: bool) -> None:
        await self._show.set_paused(p)

    async def remove(self, id: str) -> bool:
        """Delete a picture ("last" or an id) from the gallery, then the store.

        If the gallery delete fails for a picture it already has, the local record
        is kept so the organiser can try again. A picture the gallery never got is
        removed locally anyway; if its upload lands after this, the uploader sees
        the record is gone and deletes it from the gallery.
        """
        rec = self._store.last() if id == "last" else self._store.get(id)
        if rec is None:
            return False
        deleted = await self._uploader.delete(rec.id)
        current = self._store.get(rec.id)
        if not deleted and current is not None and current.uploaded:
            log.warning("remove %s: gallery delete failed; kept locally to retry", rec.id)
            return False
        self._store.remove(rec.id)
        return True

    async def winners(self) -> list[dict]:
        board = self._leaderboard()
        if not winners(board, self._store, {}):
            return []  # no top 3 yet: no need to ask the gallery
        return winners(board, self._store, await self._claims())
