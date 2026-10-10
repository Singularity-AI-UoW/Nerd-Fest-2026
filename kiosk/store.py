"""Local SQLite store for generated images. Each call opens its own short-lived
connection, so a fresh process can open the same data_dir and files are never
left locked (matters on Windows)."""
from __future__ import annotations

import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_MAX_INSERT_TRIES = 100

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL,
    claim_code TEXT NOT NULL UNIQUE,
    uploaded INTEGER NOT NULL DEFAULT 0
)
"""
_COLS = "id, prompt, mode, created_at, claim_code, uploaded"


def new_id() -> str:
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(8))


@dataclass(frozen=True)
class ImageRecord:
    id: str
    prompt: str
    mode: str
    created_at: datetime
    claim_code: str
    uploaded: bool


def _to_record(row: tuple) -> ImageRecord:
    id_, prompt, mode, created_at, claim, uploaded = row
    return ImageRecord(id_, prompt, mode, datetime.fromisoformat(created_at), claim, bool(uploaded))


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.images_dir = self.data_dir / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "stall.db"
        with self._conn() as c:
            c.execute(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        # `with conn` commits/rolls back; callers wrap in closing() to release the file.
        return sqlite3.connect(self.db_path, timeout=10)

    def _query(self, sql: str, args: tuple = ()) -> list[tuple]:
        with closing(self._conn()) as c:
            return c.execute(sql, args).fetchall()

    def _execute(self, sql: str, args: tuple = ()) -> None:
        with closing(self._conn()) as c, c:
            c.execute(sql, args)

    def new_record(self, prompt: str, mode: str) -> ImageRecord:
        created = datetime.now(timezone.utc)
        for _ in range(_MAX_INSERT_TRIES):
            rec = ImageRecord(new_id(), prompt, mode, created, f"{secrets.randbelow(10000):04d}", False)
            try:
                self._execute(
                    f"INSERT INTO images ({_COLS}) VALUES (?, ?, ?, ?, ?, 0)",
                    (rec.id, prompt, mode, created.isoformat(), rec.claim_code),
                )
            except sqlite3.IntegrityError:
                continue  # claim code (or, very unlikely, id) collision: pick again
            return rec
        raise RuntimeError("could not allocate a unique claim code")

    def image_path(self, id: str) -> Path:
        return self.images_dir / f"{id}.jpg"

    def save_image(self, id: str, jpeg: bytes) -> Path:
        path = self.image_path(id)
        path.write_bytes(jpeg)
        return path

    def get(self, id: str) -> ImageRecord | None:
        rows = self._query(f"SELECT {_COLS} FROM images WHERE id = ?", (id,))
        return _to_record(rows[0]) if rows else None

    def last(self) -> ImageRecord | None:
        rows = self._query(f"SELECT {_COLS} FROM images ORDER BY created_at DESC, rowid DESC LIMIT 1")
        return _to_record(rows[0]) if rows else None

    def recent(self, limit: int) -> list[ImageRecord]:
        """The newest records first, at most `limit`."""
        rows = self._query(
            f"SELECT {_COLS} FROM images ORDER BY created_at DESC, rowid DESC LIMIT ?", (max(limit, 0),)
        )
        return [_to_record(r) for r in rows]

    def pending_uploads(self) -> list[ImageRecord]:
        rows = self._query(f"SELECT {_COLS} FROM images WHERE uploaded = 0 ORDER BY created_at, rowid")
        return [_to_record(r) for r in rows]

    def mark_uploaded(self, id: str) -> None:
        self._execute("UPDATE images SET uploaded = 1 WHERE id = ?", (id,))

    def remove(self, id: str) -> None:
        self._execute("DELETE FROM images WHERE id = ?", (id,))
        self.image_path(id).unlink(missing_ok=True)

    def claim_for(self, id: str) -> str | None:
        rec = self.get(id)
        return rec.claim_code if rec else None
