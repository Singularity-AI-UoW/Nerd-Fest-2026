"""Image generation and editing via the Gemini Interactions API.

The google-genai call is synchronous, so it runs in a worker thread under
asyncio.wait_for. Each call makes at most two attempts (one retry) for
transient errors and timeouts. A safety block is never retried.

Safety-block signal (checked against the SDK types, docs and one live call on 10/10/2026):
  - The image-generation docs page does not describe a block signal, and the
    SDK has no dedicated block field on the Interaction (it has `status`,
    `errors` and `steps`, plus `output_image`, which is None when no image
    step came back).
  - So a blocked prompt is detected two ways: (1) a successful response with
    no `output_image`, or (2) an exception whose message mentions
    safety/blocked/prohibited. Both map to ImageBlocked.
  - Live success shape: status "completed", steps [ThoughtStep, ModelOutputStep],
    output_image.mime_type "image/jpeg", 1024x1024. A real block was not
    triggered on purpose; if one looks different,
    adjust BLOCK_WORDS / _extract_image_b64 here.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import io
import logging
from typing import Any

from google import genai
from PIL import Image, UnidentifiedImageError

from kiosk.config import Config, Secrets

log = logging.getLogger(__name__)

JPEG_QUALITY = 85
MAX_ATTEMPTS = 2  # first try plus one retry
BLOCK_WORDS = ("safety", "blocked", "prohibited")


class ImageBlocked(Exception):
    """The model declined to make the image (safety block). Never retried."""


class ImageFailed(Exception):
    """The image call failed or timed out, even after one retry."""


def _is_block_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(word in message for word in BLOCK_WORDS)


def _extract_image_b64(interaction: Any) -> str:
    image = getattr(interaction, "output_image", None)
    data = getattr(image, "data", None) if image is not None else None
    if not data:
        raise ImageBlocked("response held no image")
    return data


def _to_jpeg(b64_data: str) -> bytes:
    try:
        with Image.open(io.BytesIO(base64.b64decode(b64_data))) as img:
            out = io.BytesIO()
            img.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY)
            return out.getvalue()
    except (binascii.Error, UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageFailed("could not decode image data") from exc


class ImageService:
    def __init__(self, client: genai.Client, model: str, size: str, timeout_s: float):
        self._client = client
        self.model = model
        self._size = size
        self._timeout_s = timeout_s

    async def generate(self, prompt: str) -> bytes:
        return await self._run(prompt)

    async def edit(self, jpeg: bytes, instruction: str) -> bytes:
        parts = [
            {"type": "text", "text": instruction},
            {
                "type": "image",
                "data": base64.b64encode(jpeg).decode("ascii"),
                "mime_type": "image/jpeg",
            },
        ]
        return await self._run(parts)

    def _create(self, model_input: str | list[dict]) -> Any:
        return self._client.interactions.create(
            model=self.model,
            input=model_input,
            response_format={"type": "image", "aspect_ratio": "1:1", "image_size": self._size},
            timeout=self._timeout_s,
        )

    async def _run(self, model_input: str | list[dict]) -> bytes:
        last: BaseException | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                interaction = await asyncio.wait_for(
                    asyncio.to_thread(self._create, model_input), self._timeout_s
                )
                return _to_jpeg(_extract_image_b64(interaction))
            except (ImageBlocked, ImageFailed):
                raise
            except Exception as exc:  # noqa: BLE001 - SDK raises many types
                if _is_block_error(exc):
                    raise ImageBlocked("blocked by the image model") from exc
                last = exc
                # Type only: messages could carry request details.
                log.warning("image call attempt %d failed: %s", attempt, type(exc).__name__)
        raise ImageFailed(f"image call failed: {type(last).__name__}") from last


def make_image_service(cfg: Config, secrets: Secrets) -> ImageService:
    client = genai.Client(api_key=secrets.google_api_key)
    return ImageService(
        client, cfg.models.image, cfg.models.image_size, cfg.timeouts.image_call_s
    )
