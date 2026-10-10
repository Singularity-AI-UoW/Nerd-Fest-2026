import re
from datetime import timedelta
from pathlib import Path

import pytest

from kiosk.store import Store, new_id


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path)


def test_new_id_format():
    ids = [new_id() for _ in range(1000)]
    assert all(re.fullmatch(r"[a-z2-7]{8}", i) for i in ids)
    assert len(set(ids)) == 1000


def test_creates_db_and_images_dir(tmp_path: Path):
    Store(tmp_path / "nested" / "data")
    assert (tmp_path / "nested" / "data" / "stall.db").is_file()
    assert (tmp_path / "nested" / "data" / "images").is_dir()


def test_claim_codes_unique(store: Store):
    recs = [store.new_record(f"p{i}", "kid") for i in range(200)]
    claims = [r.claim_code for r in recs]
    assert all(re.fullmatch(r"\d{4}", c) for c in claims)
    assert len(set(claims)) == 200


def test_new_record_fields_and_get(store: Store):
    rec = store.new_record("a purple cow", "nerd")
    assert rec.prompt == "a purple cow" and rec.mode == "nerd"
    assert rec.uploaded is False
    assert rec.created_at.tzinfo is not None
    assert rec.created_at.utcoffset() == timedelta(0)
    assert store.get(rec.id) == rec
    assert store.get("zzzzzzzz") is None
    assert store.claim_for(rec.id) == rec.claim_code
    assert store.claim_for("zzzzzzzz") is None


def test_save_image_and_path(store: Store):
    rec = store.new_record("x", "kid")
    p = store.save_image(rec.id, b"\xff\xd8jpegdata")
    assert p == store.image_path(rec.id)
    assert p.read_bytes() == b"\xff\xd8jpegdata"
    assert p.parent == store.data_dir / "images"


def test_pending_and_mark_uploaded(store: Store):
    a = store.new_record("a", "kid")
    b = store.new_record("b", "nerd")
    assert [r.id for r in store.pending_uploads()] == [a.id, b.id]
    store.mark_uploaded(a.id)
    assert [r.id for r in store.pending_uploads()] == [b.id]
    assert store.get(a.id).uploaded is True


def test_remove_deletes_row_and_file(store: Store):
    rec = store.new_record("a", "kid")
    path = store.save_image(rec.id, b"jpeg")
    store.remove(rec.id)
    assert store.get(rec.id) is None
    assert not path.exists()
    assert store.pending_uploads() == []
    store.remove(rec.id)  # removing twice is harmless


def test_last_returns_newest(store: Store):
    assert store.last() is None
    store.new_record("first", "kid")
    second = store.new_record("second", "nerd")
    assert store.last() == second
    store.remove(second.id)
    assert store.last().prompt == "first"


def test_persists_across_fresh_store_on_same_dir(tmp_path: Path):
    s1 = Store(tmp_path)
    rec = s1.new_record("persist me", "kid")
    s1.save_image(rec.id, b"jpeg")
    s1.mark_uploaded(rec.id)
    s2 = Store(tmp_path)
    got = s2.get(rec.id)
    assert got == s1.get(rec.id)
    assert got.uploaded is True
    assert s2.last() == got
    assert s2.image_path(rec.id).read_bytes() == b"jpeg"
    assert s2.new_record("next", "kid").claim_code != rec.claim_code
