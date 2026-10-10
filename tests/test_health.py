"""Health: service lights, debounced online flag, and the status message."""
from __future__ import annotations

import asyncio

from kiosk.health import Health, fake_checks


class RecordingHub:
    def __init__(self):
        self.msgs: list[dict] = []

    async def send(self, msg: dict) -> None:
        self.msgs.append(msg)


def make_health(results: dict[str, bool], *, paused=False, queue=3, llm="primary", **kw):
    """Health over checks that read `results` at call time; changes are logged."""
    changes: list[bool] = []

    def check(name):
        async def run() -> bool:
            return results[name]
        return run

    hub = RecordingHub()
    health = Health(
        {name: check(name) for name in results}, hub, changes.append,
        llm_active=lambda: llm, is_paused=lambda: paused, queue_size=lambda: queue, **kw,
    )
    return health, hub, changes


ALL_UP = {"gallery": True, "google": True, "openrouter": True, "fallback_llm": True}


async def test_status_message_shape():
    health, hub, _ = make_health({**ALL_UP, "openrouter": False}, paused=True, queue=2, llm="fallback")
    await health.check_once()
    assert hub.msgs == [{
        "type": "status",
        "services": {"gallery": True, "google": True, "openrouter": False, "fallback_llm": True},
        "llm": "fallback", "paused": True, "queue": 2,
    }]


async def test_offline_when_gallery_and_google_down():
    health, hub, changes = make_health({**ALL_UP, "gallery": False, "google": False})
    await health.check_once()
    assert changes == []  # one bad round is not enough
    await health.check_once()
    assert changes == [False]
    assert health.online is False
    await health.check_once()
    assert changes == [False]  # reported once, not every round


async def test_online_if_only_openrouter_down():
    health, _, changes = make_health({**ALL_UP, "openrouter": False})
    for _ in range(3):
        await health.check_once()
    assert changes == []
    assert health.online is True


async def test_online_if_only_one_of_gallery_and_google_down():
    for down in ("gallery", "google"):
        health, _, changes = make_health({**ALL_UP, down: False})
        for _ in range(3):
            await health.check_once()
        assert changes == [] and health.online is True


async def test_single_bad_round_does_not_flap_offline():
    results = dict(ALL_UP)
    health, _, changes = make_health(results)
    results.update(gallery=False, google=False)
    await health.check_once()
    results.update(gallery=True, google=True)
    await health.check_once()
    results.update(gallery=False, google=False)
    await health.check_once()  # the bad rounds are not consecutive
    assert changes == []


async def test_one_good_round_brings_it_back_online():
    results = {**ALL_UP, "gallery": False, "google": False}
    health, _, changes = make_health(results)
    await health.check_once()
    await health.check_once()
    results.update(gallery=True)
    await health.check_once()
    assert changes == [False, True]
    assert health.online is True


async def test_a_failing_or_slow_check_counts_as_down():
    async def boom() -> bool:
        raise RuntimeError("https://example.invalid/?key=SECRET")

    async def slow() -> bool:
        await asyncio.sleep(3600)
        return True

    async def fine() -> bool:
        return True

    hub = RecordingHub()
    health = Health({"gallery": boom, "google": slow, "openrouter": fine}, hub, lambda ok: None,
                    check_timeout_s=0.05)
    await health.check_once()
    assert hub.msgs[0]["services"] == {"gallery": False, "google": False, "openrouter": True}


async def test_checks_run_concurrently():
    async def slow() -> bool:
        await asyncio.sleep(0.2)
        return True

    health = Health({"a": slow, "b": slow, "c": slow, "d": slow}, RecordingHub(), lambda ok: None)
    loop = asyncio.get_running_loop()
    start = loop.time()
    await health.check_once()
    assert loop.time() - start < 0.5


async def test_run_forever_repeats_and_survives_a_hub_error():
    calls = {"n": 0}

    class FlakyHub:
        msgs: list[dict] = []

        async def send(self, msg):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("hub down")
            self.msgs.append(msg)

    hub = FlakyHub()
    health = Health(dict(fake_checks()), hub, lambda ok: None, interval_s=0.01)
    task = asyncio.create_task(health.run_forever())
    await asyncio.sleep(0.15)
    task.cancel()
    assert len(hub.msgs) >= 2


async def test_fake_checks_are_all_up():
    for check in fake_checks().values():
        assert await check() is True


# ----- make_checks: URLs and headers, with no network -----

def _real_checks(handler, *, fallback_url="http://llm.test/v1/"):
    import httpx
    from types import SimpleNamespace
    from kiosk.config import Secrets
    from kiosk.health import make_checks

    seen_models: list[str] = []

    class Models:
        def get(self, *, model):
            seen_models.append(model)

    secrets = Secrets("g", "o", fallback_url, "FALLBACK-KEY", "http://gallery.test/", "t")
    cfg = SimpleNamespace(models=SimpleNamespace(image="the-image-model"))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return make_checks(cfg, secrets, http, SimpleNamespace(models=Models())), seen_models


async def test_make_checks_call_the_cheap_endpoints():
    import httpx
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    checks, seen_models = _real_checks(handler)
    assert [await c() for c in checks.values()] == [True, True, True, True]
    urls = {str(r.url): r for r in requests}
    assert set(urls) == {"http://gallery.test/api/images", "https://openrouter.ai/api/v1/models",
                         "http://llm.test/v1/models"}
    assert urls["http://llm.test/v1/models"].headers["authorization"] == "Bearer FALLBACK-KEY"
    assert "authorization" not in urls["http://gallery.test/api/images"].headers
    assert seen_models == ["the-image-model"]


async def test_make_checks_http_errors_and_missing_urls_are_down():
    import httpx

    checks, _ = _real_checks(lambda r: httpx.Response(503), fallback_url="")
    assert await checks["gallery"]() is False
    assert await checks["openrouter"]() is False
    assert await checks["fallback_llm"]() is False
