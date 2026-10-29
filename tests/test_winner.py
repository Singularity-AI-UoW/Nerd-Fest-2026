"""Winner tests: claim codes and contacts join for the organiser table; the TV shows the winner's code."""
from datetime import datetime, timedelta, timezone

import httpx

from kiosk.config import load_config
from kiosk.controls import StallControls
from kiosk.fakes import _draw_jpeg
from kiosk.slideshow import Slideshow
from kiosk.store import Store
from kiosk.winner import WinnerScreen, fetch_claims, winners
from tests.test_slideshow import BASE, Gallery, RecordingHub, top_entry

NZ = timezone(timedelta(hours=13))
CLOSE = datetime(2026, 10, 30, 18, 30, tzinfo=NZ)


def add(store, prompt):
    rec = store.new_record(prompt, "kid")
    store.save_image(rec.id, _draw_jpeg("x"))
    return rec


def leaderboard(recs, votes=(5, 3, 1), closed=True):
    top3 = [{"id": r.id, "prompt": r.prompt, "url": f"/img/{r.id}.jpg", "votes": v}
            for r, v in zip(recs, votes)]
    return {"closed": closed, "top3": top3}


def test_winners_joins_claim_codes(tmp_path):
    store = Store(tmp_path)
    a, b = add(store, "a dragon"), add(store, "a robot")
    board = leaderboard([a, b])
    board["top3"].append({"id": "zzzzzzzz", "prompt": "gone", "url": "/img/zzzzzzzz.jpg", "votes": 1})
    assert winners(board, store, {}) == [
        {"rank": 1, "id": a.id, "prompt": "a dragon", "votes": 5, "claim_code": a.claim_code,
         "contact": None, "contact_status": "none"},
        {"rank": 2, "id": b.id, "prompt": "a robot", "votes": 3, "claim_code": b.claim_code,
         "contact": None, "contact_status": "none"},
        {"rank": 3, "id": "zzzzzzzz", "prompt": "gone", "votes": 1, "claim_code": None,
         "contact": None, "contact_status": "none"},
    ]


def test_winners_check_contact_codes_against_the_store(tmp_path):
    store = Store(tmp_path)
    a, b = add(store, "a dragon"), add(store, "a robot")
    wrong = "0000" if b.claim_code != "0000" else "1111"
    claims = {a.id: {"contact": "a@example.com", "code": a.claim_code},
              b.id: {"contact": "0211234567", "code": wrong}}
    rows = winners(leaderboard([a, b]), store, claims)
    assert [(r["contact"], r["contact_status"]) for r in rows] == [
        ("a@example.com", "ok"), ("0211234567", "mismatch")]


def test_winners_without_the_gallery_say_so(tmp_path):
    store = Store(tmp_path)
    a = add(store, "a dragon")
    [row] = winners(leaderboard([a]), store, None)
    assert row["contact"] is None and row["contact_status"] == "unreachable"
    assert row["claim_code"] == a.claim_code  # the rest of the row still works


async def test_fetch_claims_uses_the_token_and_keys_by_id():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"claims": [
            {"image_id": "abcdefgh", "contact": "a@example.com", "code": "0123", "saved_at": "x"},
            {"image_id": 5}, "junk"]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        got = await fetch_claims(http, BASE + "/", "tok")
    assert seen == {"auth": "Bearer tok", "path": "/api/claims"}
    assert got == {"abcdefgh": {"contact": "a@example.com", "code": "0123"}}


async def test_fetch_claims_failure_is_none():
    def handler(request):
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await fetch_claims(http, BASE, "tok") is None


async def test_entry_without_url_is_dropped_from_table_and_tv_alike(tmp_path):
    screen, hub, recs = make_screen(tmp_path, CLOSE, "x")
    board = screen._slideshow.leaderboard
    del board["top3"][0]["url"]  # the first entry has no picture URL
    rows = winners(board, Store(tmp_path), {})
    assert [r["id"] for r in rows] == [recs[1].id]
    assert rows[0]["rank"] == 1
    await screen.tick()
    assert [t["prompt"] for t in hub.msgs[0]["top3"]] == ["a robot"]
    assert hub.msgs[0]["prompt"] == "a robot"


def test_winners_handles_missing_or_bad_leaderboard(tmp_path):
    store = Store(tmp_path)
    assert winners({}, store, {}) == []
    assert winners({"top3": "nope"}, store, {}) == []
    assert winners({"top3": [5, {"prompt": "no id"}]}, store, {}) == []


def make_screen(tmp_path, now, board, idle=True, closed_flag=None):
    store = Store(tmp_path)
    recs = [add(store, "a dragon"), add(store, "a robot")]
    hub = RecordingHub()
    show = Slideshow(httpx.AsyncClient(), BASE + "/", hub)
    show.leaderboard = None if board is None else leaderboard(recs, closed=bool(closed_flag))
    if board == "empty":
        show.leaderboard = {"closed": bool(closed_flag), "top3": []}
    screen = WinnerScreen(CLOSE, show, hub, store, clock=lambda: now, is_idle=lambda: idle)
    return screen, hub, recs


async def test_winner_message_only_after_close(tmp_path):
    before = CLOSE - timedelta(seconds=1)
    screen, hub, recs = make_screen(tmp_path, before, "x")
    assert await screen.tick() is False
    assert hub.msgs == []

    screen, hub, recs = make_screen(tmp_path, CLOSE, "x")
    assert await screen.tick() is True
    [msg] = hub.msgs
    assert msg == {
        "type": "winner",
        "image_url": f"{BASE}/img/{recs[0].id}.jpg",
        "prompt": "a dragon",
        "claim_code": recs[0].claim_code,
        "top3": [{"url": f"{BASE}/img/{recs[0].id}.jpg", "prompt": "a dragon", "votes": 5},
                 {"url": f"{BASE}/img/{recs[1].id}.jpg", "prompt": "a robot", "votes": 3}],
    }


async def test_gallery_closed_flag_is_a_second_signal(tmp_path):
    screen, hub, _ = make_screen(tmp_path, CLOSE - timedelta(hours=1), "x", closed_flag=True)
    assert await screen.tick() is True
    assert hub.msgs[0]["type"] == "winner"


async def test_no_winner_without_a_top3_or_when_busy(tmp_path):
    late = CLOSE + timedelta(minutes=5)
    screen, hub, _ = make_screen(tmp_path, late, "empty", closed_flag=True)
    assert await screen.tick() is False
    screen, hub, _ = make_screen(tmp_path, late, None)  # --fake: no leaderboard at all
    assert await screen.tick() is False
    screen, hub, _ = make_screen(tmp_path, late, "x", idle=False)
    assert await screen.tick() is False
    assert hub.msgs == []


async def test_tv_message_carries_only_the_winners_claim_code(tmp_path):
    screen, hub, recs = make_screen(tmp_path, CLOSE + timedelta(minutes=1), "x")
    await screen.tick()
    [msg] = hub.msgs
    assert msg["claim_code"] == recs[0].claim_code
    assert all(set(t) == {"url", "prompt", "votes"} for t in msg["top3"])  # no runner-up codes


async def test_tv_message_without_a_local_code_has_none(tmp_path):
    screen, hub, recs = make_screen(tmp_path, CLOSE, "x")
    screen._slideshow.leaderboard["top3"][0]["id"] = "zzzzzzzz"  # not made on this laptop
    await screen.tick()
    assert hub.msgs[0]["claim_code"] is None


async def test_slideshow_keeps_the_last_good_leaderboard():
    g = Gallery(top3=[top_entry(5, 3)])
    s = Slideshow(g.client(), BASE, RecordingHub())
    assert s.leaderboard is None
    await s.refresh()
    assert s.leaderboard == {"closed": False, "top3": [top_entry(5, 3)]}
    g.fail_leaderboard = True
    await s.refresh()
    assert s.leaderboard["top3"] == [top_entry(5, 3)]


async def test_controls_winners_uses_the_latest_leaderboard_and_claims(tmp_path):
    store = Store(tmp_path)
    rec = add(store, "a dragon")
    board = {"value": None}

    async def claims():
        return {rec.id: {"contact": "a@example.com", "code": rec.claim_code}}

    controls = StallControls(None, store, None, leaderboard=lambda: board["value"], claims=claims)
    assert await controls.winners() == []
    board["value"] = leaderboard([rec])
    assert await controls.winners() == [
        {"rank": 1, "id": rec.id, "prompt": "a dragon", "votes": 5, "claim_code": rec.claim_code,
         "contact": "a@example.com", "contact_status": "ok"}]


async def test_controls_skip_the_gallery_without_a_top3(tmp_path):
    calls = []

    async def claims():
        calls.append(1)
        return {}

    controls = StallControls(None, Store(tmp_path), None, leaderboard=lambda: None, claims=claims)
    assert await controls.winners() == []
    assert calls == []


def test_config_close_at_is_the_voting_close_time():
    from kiosk.config import REPO_ROOT
    assert load_config(REPO_ROOT / "config.toml").voting.close_at == CLOSE
