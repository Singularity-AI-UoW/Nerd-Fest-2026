import logging
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from kiosk.config import Secrets, load_config
from kiosk.store import ImageRecord, Store
from kiosk.takehome import build_takehome, image_page_url, qr_svg

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRETS = Secrets("", "", "", "", "https://gallery.example", "")


def cfg_with_signup(path: Path):
    cfg = load_config(REPO_ROOT / "config.toml")
    return replace(cfg, files=replace(cfg.files, signup_qr=str(path)))


def test_url_has_claim_fragment():
    assert image_page_url("https://gallery.example", "abcdefgh", "0123") == (
        "https://gallery.example/i/abcdefgh#c=0123"
    )


def test_url_tolerates_trailing_slash():
    assert image_page_url("https://gallery.example/", "abcdefgh", "0123") == (
        "https://gallery.example/i/abcdefgh#c=0123"
    )


def test_qr_svg_is_svg():
    svg = qr_svg("https://gallery.example/i/abcdefgh#c=0123")
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    # Sized by CSS: a viewBox about 360 px across, no fixed width/height.
    m = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    assert m and m.group(1) == m.group(2)
    assert 300 <= int(m.group(1)) <= 360
    assert "width=" not in svg.split(">", 1)[0]
    assert "#000" in svg and "#fff" in svg


def test_build_takehome_matches_tv_message(tmp_path):
    signup = tmp_path / "signup.svg"
    signup.write_text("<svg>sign up</svg>", encoding="utf-8")
    rec = Store(tmp_path / "data").new_record("a dragon", "kid")

    msg = build_takehome(rec, cfg_with_signup(signup), SECRETS)

    assert msg["type"] == "takehome"
    assert msg["image_url"] == f"/local/{rec.id}.jpg"
    assert msg["claim"] == rec.claim_code
    assert msg["qr_svg"] == qr_svg(image_page_url(SECRETS.gallery_url, rec.id, rec.claim_code))
    assert msg["signup_qr_svg"] == "<svg>sign up</svg>"


def test_missing_signup_qr_is_omitted_with_warning(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    rec = Store(tmp_path / "data").new_record("a dragon", "kid")

    msg = build_takehome(rec, cfg_with_signup(tmp_path / "missing.svg"), SECRETS)

    assert "signup_qr_svg" not in msg
    assert msg["qr_svg"].startswith("<svg")
    assert "sign-up QR" in caplog.text


def test_repo_signup_qr_is_found_from_config():
    rec = ImageRecord("abcdefgh", "p", "kid", datetime.now(timezone.utc), "0042", False)
    msg = build_takehome(rec, load_config(REPO_ROOT / "config.toml"), SECRETS)
    assert "<svg" in msg["signup_qr_svg"]
