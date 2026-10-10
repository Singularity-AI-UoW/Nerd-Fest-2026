"""Load config.toml and secrets for the kiosk.

Secrets live in ~/.nerdfest/.env (outside the repo). Real environment
variables override the file. Secrets never appear in repr() output.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]


def repo_path(path: str) -> Path:
    """A path from config.toml: absolute as given, relative ones from the repo root."""
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


@dataclass(frozen=True)
class Models:
    llm: str
    fallback_llm: str
    stt: str
    image: str
    image_size: str


@dataclass(frozen=True)
class Button:
    key: str
    hold_threshold_s: float
    tap_window_s: float


@dataclass(frozen=True)
class Timeouts:
    choose_mode_s: float
    listen_wait_s: float
    listen_hold_max_s: float
    confirm_s: float
    agent_cap_s: float
    tweak_s: float
    takehome_s: float
    image_call_s: float
    llm_call_s: float


@dataclass(frozen=True)
class Limits:
    max_tool_calls: int
    max_blocked_tries: int
    max_request_chars: int
    max_stt_retries: int


@dataclass(frozen=True)
class Voting:
    close_at: datetime
    claim_until: datetime


@dataclass(frozen=True)
class Files:
    blocklist: str
    signup_qr: str
    data_dir: str


@dataclass(frozen=True)
class Display:
    tv_window_x: int
    port: int


@dataclass(frozen=True)
class Config:
    models: Models
    button: Button
    timeouts: Timeouts
    limits: Limits
    voting: Voting
    files: Files
    display: Display
    preset_ideas: list[str]
    preset_tweaks: list[str]


def load_config(path: Path = Path("config.toml")) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    voting = raw["voting"]
    return Config(
        models=Models(**raw["models"]),
        button=Button(**raw["button"]),
        timeouts=Timeouts(**raw["timeouts"]),
        limits=Limits(**raw["limits"]),
        voting=Voting(
            close_at=datetime.fromisoformat(voting["close_at"]),
            claim_until=datetime.fromisoformat(voting["claim_until"]),
        ),
        files=Files(**raw["files"]),
        display=Display(**raw["display"]),
        preset_ideas=list(raw["preset_ideas"]),
        preset_tweaks=list(raw["preset_tweaks"]),
    )


@dataclass(frozen=True)
class Secrets:
    google_api_key: str
    openrouter_api_key: str
    fallback_llm_url: str
    fallback_llm_key: str
    gallery_url: str
    gallery_token: str

    def __repr__(self) -> str:
        names = (f.name for f in fields(self))
        return "Secrets(" + ", ".join(f"{n}='***'" for n in names) + ")"

    __str__ = __repr__


def load_secrets(env_path: Path | None = None) -> Secrets:
    if env_path is None:
        env_path = Path.home() / ".nerdfest" / ".env"
    values = dotenv_values(env_path)

    def get(name: str) -> str:
        return os.environ.get(name) or values.get(name) or ""

    return Secrets(
        google_api_key=get("GOOGLE_API_KEY"),
        openrouter_api_key=get("OPENROUTER_API_KEY"),
        fallback_llm_url=get("FALLBACK_LLM_URL"),
        fallback_llm_key=get("FALLBACK_LLM_KEY"),
        gallery_url=get("GALLERY_URL"),
        gallery_token=get("GALLERY_TOKEN"),
    )
