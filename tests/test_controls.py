from kiosk.controls import StallControls
from kiosk.fakes import _draw_jpeg
from kiosk.store import Store


class RecordingShow:
    def __init__(self):
        self.paused: list[bool] = []

    async def set_paused(self, p: bool) -> None:
        self.paused.append(p)


class RecordingUploader:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.deleted: list[str] = []

    async def delete(self, id: str) -> bool:
        self.deleted.append(id)
        return self.ok


def make(tmp_path, ok=True):
    store = Store(tmp_path)
    show, uploader = RecordingShow(), RecordingUploader(ok)
    return StallControls(show, store, uploader), show, store, uploader


def add(store, uploaded=False):
    rec = store.new_record("a dragon", "kid")
    store.save_image(rec.id, _draw_jpeg("x"))
    if uploaded:
        store.mark_uploaded(rec.id)
    return rec


async def test_set_paused_goes_to_show(tmp_path):
    controls, show, _, _ = make(tmp_path)
    await controls.set_paused(True)
    await controls.set_paused(False)
    assert show.paused == [True, False]


async def test_remove_last_deletes_from_gallery_then_store(tmp_path):
    controls, _, store, uploader = make(tmp_path)
    first = add(store, uploaded=True)
    last = add(store, uploaded=True)
    assert await controls.remove("last") is True
    assert uploader.deleted == [last.id]
    assert store.get(last.id) is None
    assert not store.image_path(last.id).exists()
    assert store.get(first.id) is not None


async def test_remove_by_id(tmp_path):
    controls, _, store, uploader = make(tmp_path)
    rec = add(store, uploaded=True)
    add(store)
    assert await controls.remove(rec.id) is True
    assert uploader.deleted == [rec.id]
    assert store.get(rec.id) is None


async def test_remove_unknown_returns_false(tmp_path):
    controls, _, _, uploader = make(tmp_path)
    assert await controls.remove("abcdefgh") is False
    assert await controls.remove("last") is False
    assert uploader.deleted == []


async def test_gallery_failure_keeps_uploaded_record(tmp_path):
    controls, _, store, _ = make(tmp_path, ok=False)
    rec = add(store, uploaded=True)
    assert await controls.remove(rec.id) is False
    assert store.get(rec.id) is not None  # the organiser can try again


async def test_gallery_failure_still_removes_record_never_uploaded(tmp_path):
    controls, _, store, _ = make(tmp_path, ok=False)
    rec = add(store, uploaded=False)
    assert await controls.remove(rec.id) is True
    assert store.get(rec.id) is None
    assert store.pending_uploads() == []


async def test_winners_is_empty_without_a_leaderboard(tmp_path):
    controls, _, _, _ = make(tmp_path)
    assert await controls.winners() == []
