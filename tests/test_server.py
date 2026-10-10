import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kiosk.events import Event, EventBus
from kiosk.store import Store
from kiosk.web.server import Hub, create_app


class FakeControls:
    def __init__(self):
        self.paused: list[bool] = []
        self.removed: list[str] = []
        self.remove_result = True
        self.winner_rows: list[dict] = []

    async def set_paused(self, p: bool) -> None:
        self.paused.append(p)

    async def remove(self, id: str) -> bool:
        self.removed.append(id)
        return self.remove_result

    def winners(self) -> list[dict]:
        return self.winner_rows


def drain(bus: EventBus) -> list[Event]:
    out = []
    while not bus._q.empty():
        out.append(bus._q.get_nowait())
    return out


@pytest.fixture
def parts(tmp_path):
    return Hub(), EventBus(), Store(tmp_path), FakeControls()


@pytest.fixture
def client(parts):
    hub, bus, store, controls = parts
    with TestClient(create_app(hub, bus, store, controls)) as c:
        yield c


def test_typed_posts_event(client, parts):
    r = client.post("/api/typed", json={"text": "  a purple cow  "})
    assert r.status_code == 200
    assert drain(parts[1]) == [Event("typed_request", text="a purple cow")]


@pytest.mark.parametrize("body", [
    {"text": ""},
    {"text": "   "},
    {"text": "x" * 201},
    {"text": 5},
    {},
])
def test_typed_rejects_bad_text(client, parts, body):
    assert client.post("/api/typed", json=body).status_code == 422
    assert drain(parts[1]) == []


def test_typed_accepts_200_chars(client, parts):
    assert client.post("/api/typed", json={"text": "x" * 200}).status_code == 200
    assert len(drain(parts[1])) == 1


def test_tweak_posts_event(client, parts):
    assert client.post("/api/tweak", json={"n": 3}).status_code == 200
    assert drain(parts[1]) == [Event("tweak_choice", n=3)]


@pytest.mark.parametrize("n", [0, 4])
def test_tweak_accepts_keep_and_four(client, parts, n):
    assert client.post("/api/tweak", json={"n": n}).status_code == 200
    assert drain(parts[1]) == [Event("tweak_choice", n=n)]


@pytest.mark.parametrize("n", [-1, 5, "2", 1.5, True, None])
def test_tweak_rejects_bad_n(client, parts, n):
    assert client.post("/api/tweak", json={"n": n}).status_code == 422
    assert drain(parts[1]) == []


@pytest.mark.parametrize("action", ["kid", "nerd", "go", "retry", "next"])
def test_step_posts_event(client, parts, action):
    assert client.post("/api/step", json={"action": action}).status_code == 200
    assert drain(parts[1]) == [Event("step", text=action)]


@pytest.mark.parametrize("action", ["", "KID", "stop", 1, None])
def test_step_rejects_unknown_action(client, parts, action):
    assert client.post("/api/step", json={"action": action}).status_code == 422
    assert drain(parts[1]) == []


def test_pause_calls_controls(client, parts):
    assert client.post("/api/pause", json={"paused": True}).status_code == 200
    assert client.post("/api/pause", json={"paused": False}).status_code == 200
    assert parts[3].paused == [True, False]
    assert client.post("/api/pause", json={"paused": "maybe"}).status_code == 422
    assert parts[3].paused == [True, False]


def test_remove_last_and_by_id(client, parts):
    r = client.post("/api/remove", json={"id": "last"})
    assert r.status_code == 200 and r.json() == {"removed": True}
    assert client.post("/api/remove", json={"id": "abcdefg2"}).status_code == 200
    assert parts[3].removed == ["last", "abcdefg2"]


def test_remove_reports_nothing_removed(client, parts):
    parts[3].remove_result = False
    assert client.post("/api/remove", json={"id": "last"}).json() == {"removed": False}


@pytest.mark.parametrize("id_", ["", "LAST", "abc", "abcdefg1", "abcdefgh9", "../etc/x", "abcdefgh "])
def test_remove_rejects_bad_id(client, parts, id_):
    assert client.post("/api/remove", json={"id": id_}).status_code == 422
    assert parts[3].removed == []


def test_winners_from_controls(client, parts):
    assert client.get("/api/winners").json() == []
    parts[3].winner_rows = [{"rank": 1, "id": "abcdefgh", "votes": 7, "claim": "1234"}]
    assert client.get("/api/winners").json() == parts[3].winner_rows


def test_status_is_empty_until_hub_sends_status(client, parts):
    assert client.get("/api/status").json() == {}
    msg = {"type": "status", "services": {"llm": True}, "llm": "primary", "paused": False, "queue": 2}
    client.portal.call(parts[0].send, msg)
    assert client.get("/api/status").json() == msg


def test_ws_receives_hub_message(client, parts):
    with client.websocket_connect("/ws") as ws:
        msg = {"type": "caption", "text": "hello", "final": False}
        client.portal.call(parts[0].send, msg)
        assert ws.receive_json() == msg


def test_new_ws_gets_last_state(client, parts):
    state = {"type": "state", "state": "listen", "mode": "kid"}
    client.portal.call(parts[0].send, {"type": "state", "state": "idle", "mode": "kid"})
    client.portal.call(parts[0].send, state)
    client.portal.call(parts[0].send, {"type": "caption", "text": "x", "final": True})
    assert parts[0].last_state == state
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json() == state


def test_new_ws_gets_last_slideshow(client, parts):
    show = {"type": "slideshow", "images": [{"url": "/a.jpg", "prompt": "a"}], "top3": []}
    client.portal.call(parts[0].send, show)
    client.portal.call(parts[0].send, {"type": "slideshow", "images": [], "top3": []})
    client.portal.call(parts[0].send, show)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json() == show


def test_new_ws_gets_last_status_too(client, parts):
    status = {"type": "status", "services": {}, "llm": "primary", "paused": False, "queue": 0}
    client.portal.call(parts[0].send, status)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json() == status


def test_dead_socket_is_dropped_and_others_still_get_messages(client, parts):
    hub = parts[0]

    class Dead:
        async def send_json(self, _):
            raise RuntimeError("gone")

    hub.clients.add(Dead())
    with client.websocket_connect("/ws") as ws:
        msg = {"type": "narration", "text": "hi"}
        client.portal.call(hub.send, msg)
        assert ws.receive_json() == msg
    assert not any(isinstance(c, Dead) for c in hub.clients)


def test_local_image_served(client, parts):
    store = parts[2]
    path = store.save_image("abcdefgh", b"\xff\xd8jpegbytes")
    assert path == store.image_path("abcdefgh")
    r = client.get("/local/abcdefgh.jpg")
    assert r.status_code == 200
    assert r.content == b"\xff\xd8jpegbytes"
    assert r.headers["content-type"] == "image/jpeg"


def test_local_image_missing_is_404(client):
    assert client.get("/local/abcdefgh.jpg").status_code == 404


@pytest.mark.parametrize("path", [
    "/local/..%2fx.jpg",
    "/local/..%2f..%2fstall.db.jpg",
    "/local/ABCDEFGH.jpg",
    "/local/abcdefg.jpg",
    "/local/abcdefgh1.jpg",
    "/local/abcdefg1.jpg",
])
def test_local_image_rejects_bad_id(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("route", ["/tv", "/organiser", "/studio"])
def test_pages_served(client, route):
    r = client.get(route)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_static_assets_served(client):
    for name in ["style.css", "tv.js", "organiser.js"]:
        assert client.get(f"/static/{name}").status_code == 200


def test_studio_dom_contract(client):
    html = client.get("/studio").text
    for frag in ['id="prompt"', 'id="generate"', 'id="busy"', 'id="result"',
                 "window.showImage", "window.resetStudio"]:
        assert frag in html
    assert "<textarea" in html


def test_pages_use_no_external_assets(client):
    for route in ["/tv", "/organiser", "/studio", "/static/style.css", "/static/tv.js", "/static/organiser.js"]:
        assert not re.search(r"https?://", client.get(route).text), route


def test_tv_handles_every_message_type(client):
    js = client.get("/static/tv.js").text
    for t in ["state", "caption", "narration", "tool", "prompt", "image", "tweaks",
              "takehome", "message", "status", "log", "winner", "slideshow"]:
        assert re.search(rf"\b{t}\s*[:(]|['\"]{t}['\"]", js), t
    html = client.get("/tv").text
    assert 'id="studio"' in html and 'src="/studio"' in html


def test_tv_uses_exact_visitor_strings(client):
    text = client.get("/tv").text + client.get("/static/tv.js").text
    for s in ["Hold for kid mode, press twice for nerd mode", "Hold the button and talk",
              "Press to go, hold to try again", "Back soon",
              "Winner: show your claim code at the stall", "Press the big button!"]:
        assert s in text, s


def test_tv_idle_screen_contract(client):
    text = client.get("/tv").text + client.get("/static/tv.js").text
    for s in ["Top 3 so far", "Be the first!", 'id="idle-signup"', "signup_qr_svg"]:
        assert s in text, s


def test_static_dir_ships_with_package():
    static = Path(__file__).parent.parent / "kiosk" / "web" / "static"
    for n in ["tv.html", "tv.js", "organiser.html", "organiser.js", "studio.html", "style.css"]:
        assert (static / n).is_file(), n
