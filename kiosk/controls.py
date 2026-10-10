"""The organiser page's controls: pause the show and remove a picture."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class StallControls:
    def __init__(self, show, store, uploader):
        self._show = show
        self._store = store
        self._uploader = uploader

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

    def winners(self) -> list[dict]:
        return []  # filled in once voting results are wired up
