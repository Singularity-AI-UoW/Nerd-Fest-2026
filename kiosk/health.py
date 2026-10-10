"""Service health: lights for the organiser page and the online flag for the show.

Every `interval_s` all checks run at once, each with its own timeout. The result goes
to the organiser page as a `status` message. The stall counts as offline when both the
gallery and Google are down (OpenRouter being down is fine: the fallback LLM covers it).
To stop one slow check flapping the TV between "Back soon" and the show, going offline
takes two bad rounds in a row and coming back takes one good round.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import httpx

log = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 5.0
OFFLINE_AFTER_BAD_ROUNDS = 2
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

Check = Callable[[], Awaitable[bool]]


class Health:
    def __init__(self, checks: dict[str, Check], hub, on_change: Callable[[bool], None],
                 interval_s: float = 10, *,
                 llm_active: Callable[[], str | None] = lambda: None,
                 is_paused: Callable[[], bool] = lambda: False,
                 queue_size: Callable[[], int] = lambda: 0,
                 check_timeout_s: float = CHECK_TIMEOUT_S):
        self._checks = checks
        self._hub = hub
        self._on_change = on_change
        self.interval_s = interval_s
        self._llm_active, self._is_paused, self._queue_size = llm_active, is_paused, queue_size
        self._timeout_s = check_timeout_s
        self.online = True
        self._bad_rounds = 0

    async def run_forever(self) -> None:
        while True:
            try:
                await self.check_once()
            except Exception as exc:  # noqa: BLE001 - health must never stop the stall
                log.error("health round failed: %s", type(exc).__name__)
            await asyncio.sleep(self.interval_s)

    async def check_once(self) -> None:
        names = list(self._checks)
        results = await asyncio.gather(*(self._run(n, self._checks[n]) for n in names))
        services = dict(zip(names, results))
        self._update_online(services)
        await self._hub.send({
            "type": "status", "services": services, "llm": self._llm_active(),
            "paused": self._is_paused(), "queue": self._queue_size(),
        })

    async def _run(self, name: str, check: Check) -> bool:
        try:
            return bool(await asyncio.wait_for(check(), self._timeout_s))
        except Exception as exc:  # noqa: BLE001
            # Type only: messages can carry URLs, and URLs can carry keys.
            log.warning("health check %s failed: %s", name, type(exc).__name__)
            return False

    def _update_online(self, services: dict[str, bool]) -> None:
        down = not services.get("gallery", True) and not services.get("google", True)
        if not down:
            self._bad_rounds = 0
            if not self.online:
                self._set_online(True)
            return
        self._bad_rounds += 1
        if self.online and self._bad_rounds >= OFFLINE_AFTER_BAD_ROUNDS:
            self._set_online(False)

    def _set_online(self, ok: bool) -> None:
        self.online = ok
        log.info("stall is %s", "online" if ok else "offline")
        self._on_change(ok)


async def _always_true() -> bool:
    return True


def fake_checks() -> dict[str, Check]:
    """For --fake: every service is up and nothing touches the network."""
    return {n: _always_true for n in ("gallery", "google", "openrouter", "fallback_llm")}


def _http_ok(http: httpx.AsyncClient, url: str, headers: dict | None = None) -> Check:
    async def check() -> bool:
        return (await http.get(url, headers=headers)).is_success
    return check


def make_checks(cfg, secrets, http: httpx.AsyncClient, genai_client) -> dict[str, Check]:
    """Cheap checks only: list calls and a model lookup, never a generation."""
    async def google() -> bool:
        await asyncio.to_thread(genai_client.models.get, model=cfg.models.image)
        return True

    fallback_url = secrets.fallback_llm_url.rstrip("/")

    async def fallback_llm() -> bool:
        if not fallback_url:
            return False
        return await _http_ok(http, f"{fallback_url}/models",
                              {"Authorization": f"Bearer {secrets.fallback_llm_key}"})()

    async def gallery() -> bool:
        if not secrets.gallery_url:
            return False
        return await _http_ok(http, f"{secrets.gallery_url.rstrip('/')}/api/images")()

    return {
        "gallery": gallery,
        "google": google,
        "openrouter": _http_ok(http, OPENROUTER_MODELS_URL),
        "fallback_llm": fallback_llm,
    }
