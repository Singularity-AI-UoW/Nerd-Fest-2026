"""The take-home card: claim code, a QR code for the picture's gallery page, and
the sign-up QR. The claim code travels only in the URL fragment (#c=NNNN), which
browsers never send to the gallery server."""
from __future__ import annotations

import functools
import logging
from pathlib import Path

import segno

from kiosk.config import Config, Secrets, repo_path
from kiosk.store import ImageRecord

log = logging.getLogger(__name__)

QR_DISPLAY_PX = 360


def picture_page_url(base: str, id: str) -> str:
    """The picture's public gallery page, with no claim code."""
    return f"{base.rstrip('/')}/i/{id}"


def image_page_url(base: str, id: str, claim: str) -> str:
    return f"{picture_page_url(base, id)}#c={claim}"


@functools.lru_cache(maxsize=256)
def picture_qr_svg(base: str, id: str) -> str:
    """The idle slideshow's QR: everyone can see it, so no claim code."""
    return qr_svg(picture_page_url(base, id))


def qr_svg(url: str) -> str:
    """An inline SVG QR code (error level M, black on white). It has a viewBox
    about 360 px across and no fixed size, so CSS sizes it."""
    qr = segno.make(url, error="m", boost_error=False)
    width, _ = qr.symbol_size()  # modules, including the quiet zone
    scale = max(1, QR_DISPLAY_PX // width)
    return qr.svg_inline(scale=scale, dark="#000", light="#fff", omitsize=True)


@functools.lru_cache(maxsize=8)
def _signup_qr(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        log.warning("sign-up QR file not found: %s (take-home card shows without it)", path)
        return None


def signup_qr_svg(cfg: Config) -> str | None:
    """The club sign-up QR as inline SVG, or None if its file is missing."""
    return _signup_qr(repo_path(cfg.files.signup_qr))


def build_takehome(rec: ImageRecord, cfg: Config, secrets: Secrets) -> dict:
    msg = {
        "type": "takehome",
        "image_url": f"/local/{rec.id}.jpg",  # served locally, so it works offline
        "claim": rec.claim_code,
        "qr_svg": qr_svg(image_page_url(secrets.gallery_url, rec.id, rec.claim_code)),
    }
    signup = signup_qr_svg(cfg)
    if signup is not None:
        msg["signup_qr_svg"] = signup
    return msg
