"""Uploads finished pictures to the gallery Worker and deletes them on request.

The store is the queue: every record not yet marked uploaded is pending, so a
restart carries on where the last run stopped. enqueue() only wakes the loop.
While uploads fail, the loop waits 5 s, then 10 s, 20 s ... up to 60 s; any
success resets the wait. The token goes only in the Authorization header and is
never logged: failures are logged by exception type or HTTP status only.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from kiosk.store import ImageRecord, Store

log = logging.getLogger(__name__)

BACKOFF_START_S = 5.0
BACKOFF_MAX_S = 60.0
MAX_PROMPT_CHARS = 500  # the gallery rejects longer prompts


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class Uploader:
    def __init__(self, store: Store, base_url: str, token: str, http: httpx.AsyncClient):
        self._store = store
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = http
        self._wake = asyncio.Event()
        self._sleep = asyncio.sleep  # replaced in tests to check the backoff

    def enqueue(self, rec: ImageRecord) -> None:
        """Wake the loop. The record is already pending in the store."""
        self._wake.set()

    def queue_size(self) -> int:
        return len(self._store.pending_uploads())

    async def upload_once(self, rec: ImageRecord) -> bool:
        """POST one record to the gallery. True if it is now uploaded."""
        try:
            jpeg = self._store.image_path(rec.id).read_bytes()
        except OSError:
            log.warning("upload %s skipped: image file missing", rec.id)
            return False
        data = {
            "id": rec.id,
            "prompt": rec.prompt[:MAX_PROMPT_CHARS],
            "mode": rec.mode,
            "created_at": _iso_utc(rec.created_at),
        }
        files = {"image": (f"{rec.id}.jpg", jpeg, "image/jpeg")}
        try:
            response = await self._http.post(
                f"{self._base}/api/upload", data=data, files=files, headers=self._headers
            )
        except Exception as exc:  # noqa: BLE001 - network, timeout, bad URL
            log.warning("upload %s failed: %s", rec.id, type(exc).__name__)
            return False
        if not response.is_success:
            log.warning("upload %s failed: HTTP %d", rec.id, response.status_code)
            return False
        self._store.mark_uploaded(rec.id)
        if self._store.get(rec.id) is None:
            # The organiser removed it while the upload was in flight.
            await self.delete(rec.id)
        return True

    async def delete(self, id: str) -> bool:
        """Delete one image from the gallery. True if the gallery confirmed it."""
        try:
            response = await self._http.delete(
                f"{self._base}/api/images/{id}", headers=self._headers
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("gallery delete %s failed: %s", id, type(exc).__name__)
            return False
        if not response.is_success:
            log.warning("gallery delete %s failed: HTTP %d", id, response.status_code)
            return False
        return True

    async def run_forever(self) -> None:
        delay = BACKOFF_START_S
        while True:
            self._wake.clear()
            any_ok = any_failed = False
            # Try every pending record, so one bad record never blocks the rest.
            for rec in self._store.pending_uploads():
                if await self.upload_once(rec):
                    any_ok = True
                else:
                    any_failed = True
            if any_ok:
                delay = BACKOFF_START_S
            if any_failed:
                await self._sleep(delay)
                delay = min(delay * 2, BACKOFF_MAX_S)
            else:
                await self._wake.wait()
