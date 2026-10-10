import base64
import io
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from kiosk.images import ImageBlocked, ImageFailed, ImageService


def png_b64(colour="red") -> str:
    buf = io.BytesIO()
    Image.new("RGBA", (64, 64), colour).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def image_result():
    return SimpleNamespace(
        status="completed",
        output_image=SimpleNamespace(data=png_b64(), mime_type="image/png"),
    )


class StubInteractions:
    """Scripted interactions.create. Each item is a result, an Exception
    (raised) or a float (seconds to sleep before returning an image)."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, float):
            time.sleep(item)
            return image_result()
        return item


def service(script, timeout_s=5.0):
    interactions = StubInteractions(script)
    client = SimpleNamespace(interactions=interactions)
    return ImageService(client, "test-model", "1K", timeout_s), interactions


async def test_generate_returns_jpeg():
    svc, interactions = service([image_result()])
    out = await svc.generate("a tuatara")
    assert out.startswith(b"\xff\xd8")
    call = interactions.calls[0]
    assert call["model"] == "test-model"
    assert call["input"] == "a tuatara"
    assert call["response_format"] == {"type": "image", "aspect_ratio": "1:1", "image_size": "1K"}
    assert call["timeout"] == 5.0
    assert svc.model == "test-model"


async def test_edit_sends_image_part():
    svc, interactions = service([image_result()])
    source = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(source, format="JPEG")
    out = await svc.edit(source.getvalue(), "add a hat")
    assert out.startswith(b"\xff\xd8")
    text_part, image_part = interactions.calls[0]["input"]
    assert text_part == {"type": "text", "text": "add a hat"}
    assert image_part["type"] == "image"
    assert image_part["mime_type"] == "image/jpeg"
    assert base64.b64decode(image_part["data"]) == source.getvalue()


async def test_retry_once_then_success():
    svc, interactions = service([RuntimeError("503 unavailable"), image_result()])
    assert (await svc.generate("x")).startswith(b"\xff\xd8")
    assert len(interactions.calls) == 2


async def test_retry_once_then_failed():
    svc, interactions = service([RuntimeError("boom"), RuntimeError("boom again")])
    with pytest.raises(ImageFailed):
        await svc.generate("x")
    assert len(interactions.calls) == 2


async def test_timeout_counts_as_failure():
    svc, interactions = service([0.3, 0.3], timeout_s=0.05)
    with pytest.raises(ImageFailed):
        await svc.generate("x")
    assert len(interactions.calls) == 2


async def test_safety_block_raises_blocked_without_retry():
    svc, interactions = service([RuntimeError("Request blocked for SAFETY reasons")])
    with pytest.raises(ImageBlocked):
        await svc.generate("x")
    assert len(interactions.calls) == 1


async def test_no_output_image_is_blocked_without_retry():
    svc, interactions = service([SimpleNamespace(status="completed", output_image=None)])
    with pytest.raises(ImageBlocked):
        await svc.generate("x")
    assert len(interactions.calls) == 1


async def test_undecodable_image_is_failed():
    bad = SimpleNamespace(status="completed", output_image=SimpleNamespace(data="bm90IGFuIGltYWdl"))
    svc, _ = service([bad])
    with pytest.raises(ImageFailed):
        await svc.generate("x")


async def test_fake_images_returns_jpeg_records_and_fails_once():
    from kiosk.fakes import FakeImages

    fake = FakeImages()
    out = await fake.generate("a kiwi")
    assert out.startswith(b"\xff\xd8")
    assert Image.open(io.BytesIO(out)).size == (1024, 1024)
    fake.fail_next(ImageBlocked("no"))
    with pytest.raises(ImageBlocked):
        await fake.edit(out, "add a hat")
    assert (await fake.edit(out, "add a hat")).startswith(b"\xff\xd8")
    assert [c["kind"] for c in fake.calls] == ["generate", "edit", "edit"]
