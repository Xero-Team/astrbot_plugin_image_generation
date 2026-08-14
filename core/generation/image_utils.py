from __future__ import annotations

import asyncio
from collections.abc import Iterable
from io import BytesIO

from astrbot.api import logger
from PIL import Image

from ..shared.constants import SUPPORTED_ASPECT_RATIOS, SUPPORTED_RESOLUTIONS
from ..shared.logging import (
    log_prefix,
)
from ..shared.logging import (
    mask_sensitive as mask_sensitive,
)
from ..shared.logging import (
    safe_log_error_body as safe_log_error_body,
)
from ..shared.logging import (
    safe_log_mapping as safe_log_mapping,
)
from ..shared.logging import (
    safe_log_text as safe_log_text,
)
from ..shared.logging import (
    safe_log_url as safe_log_url,
)
from ..shared.types import ImageData

ALLOWED_ASPECT_RATIOS = set(SUPPORTED_ASPECT_RATIOS)
ALLOWED_RESOLUTIONS = set(SUPPORTED_RESOLUTIONS)
SUPPORTED_IMAGE_FORMATS = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/heic",
    "image/heif",
}

LOG = log_prefix("Utils")


def validate_aspect_ratio(value: str | None) -> str | None:
    """Validate that the aspect ratio is supported."""
    if value is None:
        return None
    return value if value in ALLOWED_ASPECT_RATIOS else None


def validate_resolution(value: str | None) -> str | None:
    """Validate that the resolution is supported."""
    if value is None:
        return None
    return value if value in ALLOWED_RESOLUTIONS else None


def detect_mime_type(data: bytes) -> str:
    """Detect the MIME type from magic bytes when possible."""

    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if len(data) > 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"heic", b"heix", b"heim", b"heis"):
            return "image/heic"
        if brand in (b"mif1", b"msf1", b"heif"):
            return "image/heif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _sync_convert_image_format(
    image_data: bytes, mime_type: str, source_url: str | None = None
) -> ImageData:
    """Synchronously convert unsupported image formats to JPEG."""

    try:
        img = Image.open(BytesIO(image_data))

        if img.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            elif img.mode == "LA":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[3])
            img = background

        output = BytesIO()
        img.save(output, format="JPEG", quality=95)
        return ImageData(
            data=output.getvalue(), mime_type="image/jpeg", source_url=source_url
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"{LOG} 图像转换失败: {safe_log_error_body(exc)}")
        return ImageData(data=image_data, mime_type=mime_type, source_url=source_url)


async def convert_image_format(
    image_data: bytes, mime_type: str, source_url: str | None = None
) -> ImageData:
    """Convert image bytes when the MIME type is unsupported."""

    real_mime = detect_mime_type(image_data)
    if real_mime in SUPPORTED_IMAGE_FORMATS:
        return ImageData(data=image_data, mime_type=real_mime, source_url=source_url)
    logger.debug(f"{LOG} 转换图像格式: {mime_type} -> image/jpeg")
    return await asyncio.to_thread(
        _sync_convert_image_format, image_data, mime_type, source_url
    )


async def convert_images_batch(images: Iterable[ImageData]) -> list[ImageData]:
    """Convert a batch of images concurrently."""

    tasks = [
        convert_image_format(img.data, img.mime_type, img.source_url) for img in images
    ]
    return await asyncio.gather(*tasks)


__all__ = (
    "ALLOWED_ASPECT_RATIOS",
    "ALLOWED_RESOLUTIONS",
    "convert_image_format",
    "convert_images_batch",
    "detect_mime_type",
    "mask_sensitive",
    "safe_log_error_body",
    "safe_log_mapping",
    "safe_log_text",
    "safe_log_url",
    "validate_aspect_ratio",
    "validate_resolution",
)
