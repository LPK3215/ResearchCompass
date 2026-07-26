from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from yuxi.utils import image_processor as image_processor_module
from yuxi.utils.image_processor import ImageProcessor, process_uploaded_image


def test_process_uploaded_image_composites_transparent_png_pixels_on_white():
    image = Image.new("RGBA", (2, 2), (255, 255, 255, 0))
    image.putpixel((0, 0), (50, 87, 244, 0))
    image.putpixel((1, 0), (50, 87, 244, 255))

    with io.BytesIO() as buffer:
        image.save(buffer, format="PNG")
        image_data = buffer.getvalue()

    result = process_uploaded_image(image_data, "transparent.png")

    assert result["success"] is True
    assert result["format"] == "PNG"
    assert result["mime_type"] == "image/png"

    processed_data = base64.b64decode(result["image_content"])
    with Image.open(io.BytesIO(processed_data)) as processed_image:
        rgb_image = processed_image.convert("RGB")

    assert rgb_image.getpixel((0, 0)) == (255, 255, 255)
    assert rgb_image.getpixel((1, 0)) == (50, 87, 244)


def test_image_processing_failure_uses_fixed_error_and_sanitized_log(monkeypatch: pytest.MonkeyPatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    log_entries: list[tuple] = []
    processor = ImageProcessor()

    class FakeLogger:
        def error(self, *args, **kwargs):
            log_entries.append((args, kwargs))

    def fail_validation(_image_data: bytes):
        raise RuntimeError(secret)

    monkeypatch.setattr(image_processor_module, "logger", FakeLogger())
    monkeypatch.setattr(processor, "_validate_image_format", fail_validation)

    result = processor.process_image(b"image", "secret.png")

    assert result == {"success": False, "error": "图片处理失败"}
    assert secret not in repr(log_entries)


def test_thumbnail_failure_fails_the_upload_instead_of_returning_placeholder(monkeypatch):
    image = Image.new("RGB", (2, 2), "white")
    with io.BytesIO() as buffer:
        image.save(buffer, format="PNG")
        image_data = buffer.getvalue()

    processor = ImageProcessor()

    def fail_thumbnail(_image: Image.Image):
        raise RuntimeError("thumbnail encoder failed")

    monkeypatch.setattr(processor, "_generate_thumbnail", fail_thumbnail)

    result = processor.process_image(image_data, "image.png")

    assert result == {"success": False, "error": "图片处理失败"}


def test_image_pixel_limit_blocks_decompression_bomb_dimensions():
    image = Image.new("RGB", (2, 2), "white")
    with io.BytesIO() as buffer:
        image.save(buffer, format="PNG")
        image_data = buffer.getvalue()

    processor = ImageProcessor()
    processor.MAX_IMAGE_PIXELS = 3

    result = processor.process_image(image_data, "image.png")

    assert result == {"success": False, "error": "图片处理失败"}
