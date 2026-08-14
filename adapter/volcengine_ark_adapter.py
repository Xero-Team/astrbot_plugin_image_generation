from __future__ import annotations

import base64
import time
from typing import Any

from astrbot.api import logger

from ..core.adapters.base import BaseImageAdapter
from ..core.shared.constants import UNSPECIFIED_OPTION, VOLCENGINE_ARK_DEFAULT_BASE_URL
from ..core.shared.logging import (
    safe_log_error_body,
    safe_log_mapping,
    safe_log_url,
)
from ..core.shared.types import GenerationRequest, ImageCapability, ImageData


class VolcengineArkAdapter(BaseImageAdapter):
    """Volcengine Ark Seedream image generation adapter."""

    DEFAULT_BASE_URL = VOLCENGINE_ARK_DEFAULT_BASE_URL

    # Generic Seedream pixel maps used by 4.x / 5.0 lite.
    SIZE_MAPS: dict[str, dict[str, str]] = {
        "1K": {
            "1:1": "1024x1024",
            "4:3": "1152x864",
            "3:4": "864x1152",
            "16:9": "1280x720",
            "9:16": "720x1280",
            "3:2": "1248x832",
            "2:3": "832x1248",
            "21:9": "1512x648",
            "4:5": "864x1152",
            "5:4": "1152x864",
        },
        "2K": {
            "1:1": "2048x2048",
            "4:3": "2304x1728",
            "3:4": "1728x2304",
            "16:9": "2848x1600",
            "9:16": "1600x2848",
            "3:2": "2496x1664",
            "2:3": "1664x2496",
            "21:9": "3136x1344",
            "4:5": "1728x2304",
            "5:4": "2304x1728",
        },
        "3K": {
            "1:1": "3072x3072",
            "4:3": "3456x2592",
            "3:4": "2592x3456",
            "16:9": "4096x2304",
            "9:16": "2304x4096",
            "3:2": "3744x2496",
            "2:3": "2496x3744",
            "21:9": "4704x2016",
            "4:5": "2592x3456",
            "5:4": "3456x2592",
        },
        "4K": {
            "1:1": "4096x4096",
            "4:3": "4704x3520",
            "3:4": "3520x4704",
            "16:9": "5504x3040",
            "9:16": "3040x5504",
            "3:2": "4992x3328",
            "2:3": "3328x4992",
            "21:9": "6240x2656",
            "4:5": "3520x4704",
            "5:4": "4704x3520",
        },
    }

    # Seedream 5.0 Pro official sample pixel maps (1K / 2K only).
    PRO_SIZE_MAPS: dict[str, dict[str, str]] = {
        "1K": {
            "1:1": "1024x1024",
            "4:3": "1152x864",
            "3:4": "864x1152",
            "16:9": "1424x800",
            "9:16": "800x1424",
            "3:2": "1248x832",
            "2:3": "832x1248",
            "21:9": "1568x672",
            "4:5": "864x1152",
            "5:4": "1152x864",
        },
        "2K": {
            "1:1": "2048x2048",
            "4:3": "2368x1776",
            "3:4": "1776x2368",
            "16:9": "2816x1584",
            "9:16": "1584x2816",
            "3:2": "2496x1664",
            "2:3": "1664x2496",
            "21:9": "3136x1344",
            "4:5": "1776x2368",
            "5:4": "2368x1776",
        },
    }

    def get_capabilities(self) -> ImageCapability:
        """Return adapter capabilities."""
        return self._get_configured_capabilities()

    async def _generate_once(
        self, request: GenerationRequest
    ) -> tuple[list[bytes] | None, str | None]:
        """Execute one Volcengine Ark image generation request."""
        start_time = time.time()
        payload = self._build_payload(request)
        session = self._get_session()

        headers = {
            "Authorization": f"Bearer {self._get_current_api_key()}",
            "Content-Type": "application/json",
        }
        url = self._endpoint_url()

        self._log_request_overview(request, url, payload=payload)
        self._log_debug_json("请求", payload, request.task_id)

        try:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                proxy=self.proxy,
                timeout=self._get_timeout(),
            ) as resp:
                duration = time.time() - start_time
                self._log_response_status(request, resp.status, duration)
                if resp.status != 200:
                    error_text = await resp.text()
                    self._log_debug_json_text("响应", error_text, request.task_id)
                    self._log_api_error(request, resp.status, duration, error_text)
                    return None, self._format_api_error_message(
                        resp.status,
                        error_text,
                    )

                data = await self._read_response_json(resp, request.task_id)
                return await self._extract_images(data, request.task_id)
        except Exception as exc:  # noqa: BLE001
            duration = time.time() - start_time
            self._log_request_exception(request, duration, exc)
            return None, safe_log_error_body(exc)

    def _build_payload(self, request: GenerationRequest) -> dict[str, Any]:
        """Build the Volcengine Ark image generation request payload."""
        payload: dict[str, Any] = {
            "model": self._model_name(),
            "prompt": request.prompt,
            "response_format": "b64_json",
        }

        size = self._resolve_size(request)
        if size:
            payload["size"] = size

        if request.images:
            self._add_images(payload, request.images, request.task_id)

        self._add_extra_options(payload)
        return payload

    def _endpoint_url(self) -> str:
        """Return a usable Ark image generation endpoint URL."""
        base = (self.base_url or self.DEFAULT_BASE_URL).rstrip("/")
        if base.endswith("/api/v3/images/generations"):
            return base
        if base.endswith("/api/v3"):
            return f"{base}/images/generations"
        return f"{base}/api/v3/images/generations"

    def _model_name(self) -> str:
        """Return the active model name."""
        return self.model or "doubao-seedream-5.0-lite"

    def _model_name_lower(self) -> str:
        """Return the lower-cased active model name."""
        return self._model_name().lower()

    def _is_seedream_pro(self) -> bool:
        """Return whether the active model is Seedream 5.0 Pro."""
        model = self._model_name_lower()
        return "seedream-5-0-pro" in model or "seedream-5.0-pro" in model

    def _is_seedream_5_lite(self) -> bool:
        """Return whether the active model is Seedream 5.0 lite (not Pro)."""
        if self._is_seedream_pro():
            return False
        model = self._model_name_lower()
        return "seedream-5.0" in model or "seedream-5-0" in model

    def _supports_sequential_image_generation(self) -> bool:
        """Return whether sequential image generation is supported."""
        # Seedream 5.0 Pro does not support image-set generation.
        return not self._is_seedream_pro()

    def _supports_web_search(self) -> bool:
        """Return whether web search tools are supported."""
        # Official docs: only Seedream 5.0 lite supports web_search.
        return self._is_seedream_5_lite()

    def _max_reference_images_limit(self) -> int:
        """Return the model-specific max reference image count."""
        # Pro supports up to 10; other Seedream models commonly allow up to 14.
        return 10 if self._is_seedream_pro() else 14

    def _size_maps_for_model(self) -> dict[str, dict[str, str]]:
        """Return pixel size maps for the active model family."""
        return self.PRO_SIZE_MAPS if self._is_seedream_pro() else self.SIZE_MAPS

    def _resolve_size(self, request: GenerationRequest) -> str | None:
        """Resolve the Ark size parameter from resolution and aspect ratio.

        Seedream accepts either a resolution tier (e.g. ``2K``) or exact
        ``widthxheight`` pixels. When only resolution is set, send the tier.
        When both resolution and aspect ratio are set, send mapped pixels.
        """
        resolution_unspecified = (
            not request.resolution or request.resolution == UNSPECIFIED_OPTION
        )
        aspect_unspecified = (
            not request.aspect_ratio or request.aspect_ratio == UNSPECIFIED_OPTION
        )

        if resolution_unspecified and aspect_unspecified:
            return None

        if not resolution_unspecified and aspect_unspecified:
            return self._normalize_resolution(request.resolution)

        if resolution_unspecified:
            return None

        resolution = self._normalize_resolution(request.resolution)
        aspect_ratio = request.aspect_ratio
        size_maps = self._size_maps_for_model()
        resolution_map = size_maps.get(resolution) or size_maps["2K"]
        return resolution_map.get(aspect_ratio, resolution_map["1:1"])

    def _normalize_resolution(self, resolution: str | None) -> str:
        """Map plugin resolution values to sizes supported by the selected model."""
        value = resolution or "2K"
        model = self._model_name_lower()

        # Seedream 5.0 Pro: 1K / 2K only.
        if self._is_seedream_pro():
            return value if value in {"1K", "2K"} else "2K"
        if "seedream-4.0" in model or "seedream-4-0" in model:
            return value if value in {"1K", "2K", "4K"} else "2K"
        # Seedream 5.0 lite: 2K / 3K / 4K.
        if "seedream-5.0" in model or "seedream-5-0" in model:
            return value if value in {"2K", "3K", "4K"} else "2K"
        return value if value in {"2K", "4K"} else "2K"

    def _add_images(
        self, payload: dict[str, Any], images: list[ImageData], task_id: str | None
    ) -> None:
        """Add reference images as Ark-supported data URLs."""
        limit = self._max_reference_images_limit()
        max_images = self._coerce_int(
            self.config.extra.get("max_reference_images"),
            default=limit,
            min_value=1,
            max_value=limit,
        )
        selected_images = images[:max_images]
        if len(images) > max_images:
            logger.debug(
                f"{self._get_log_prefix(task_id)} 当前配置最多使用 {max_images} 张参考图，已忽略多余图片"
            )

        image_values = [self._to_data_url(image) for image in selected_images]
        payload["image"] = image_values[0] if len(image_values) == 1 else image_values

    def _to_data_url(self, image: ImageData) -> str:
        """Convert image data to a Volcengine Ark-compatible data URL."""
        mime_type = (image.mime_type or "image/png").lower()
        b64_data = base64.b64encode(image.data).decode("ascii")
        return f"data:{mime_type};base64,{b64_data}"

    def _add_extra_options(self, payload: dict[str, Any]) -> None:
        """Add optional Volcengine Ark generation parameters.

        Model-specific unsupported fields are omitted to avoid 400 errors
        (e.g. Seedream 5.0 Pro rejects sequential_image_generation).
        """
        extra = self.config.extra

        payload["watermark"] = self._coerce_bool(extra.get("watermark"), default=True)

        if self._supports_sequential_image_generation():
            sequential = str(
                extra.get("sequential_image_generation") or "disabled"
            ).strip()
            if sequential in {"auto", "disabled"}:
                payload["sequential_image_generation"] = sequential
                if sequential == "auto":
                    max_images = self._coerce_int(
                        extra.get("sequential_max_images"),
                        default=15,
                        min_value=1,
                        max_value=15,
                    )
                    payload["sequential_image_generation_options"] = {
                        "max_images": max_images
                    }

        optimize_mode = str(extra.get("optimize_prompt_mode") or "").strip()
        if optimize_mode in {"standard", "fast"}:
            payload["optimize_prompt_options"] = {"mode": optimize_mode}

        if self._supports_web_search() and self._coerce_bool(
            extra.get("enable_web_search"), default=False
        ):
            payload["tools"] = [{"type": "web_search"}]

    async def _extract_images(
        self, response: dict[str, Any], task_id: str | None = None
    ) -> tuple[list[bytes] | None, str | None]:
        """Extract image bytes from a Volcengine Ark response."""
        if response_error := response.get("error"):
            if isinstance(response_error, dict):
                message = response_error.get("message") or response_error.get("code")
                return None, f"API 错误: {message}"
            return None, f"API 错误: {response_error}"

        data_items = response.get("data")
        if not isinstance(data_items, list):
            return None, f"响应中未找到 data 字段: {safe_log_mapping(response)}"

        images: list[bytes] = []
        errors: list[str] = []
        for item in data_items:
            if not isinstance(item, dict):
                continue

            if item_error := item.get("error"):
                errors.append(self._format_item_error(item_error))
                continue

            if b64_json := item.get("b64_json"):
                try:
                    images.append(base64.b64decode(str(b64_json)))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        f"{self._get_log_prefix(task_id)} Base64 解码失败: {safe_log_error_body(exc)}"
                    )
                continue

            url = item.get("url")
            if isinstance(url, str) and url:
                if data := await self._download_image(url, task_id):
                    images.append(data)

        if images:
            return images, None
        if errors:
            return None, "; ".join(errors)
        return None, "未找到有效的图片数据"

    def _format_item_error(self, error: Any) -> str:
        """Format an item-level image generation error."""
        if not isinstance(error, dict):
            return str(error)
        code = str(error.get("code") or "").strip()
        message = str(error.get("message") or "").strip()
        if code and message:
            return f"{code}: {message}"
        return message or code or str(error)

    async def _download_image(
        self, url: str, task_id: str | None = None
    ) -> bytes | None:
        """Download a temporary image URL returned by Volcengine Ark."""
        prefix = self._get_log_prefix(task_id)
        try:
            async with self._get_session().get(
                url, proxy=self.proxy, timeout=self._get_download_timeout()
            ) as resp:
                if resp.status == 200:
                    return await resp.read()
                logger.error(
                    f"{prefix} 下载图像失败 ({resp.status}): {safe_log_url(url)}"
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"{prefix} 下载图像异常: {safe_log_error_body(exc)}")
        return None

    def _coerce_int(
        self,
        value: Any,
        *,
        default: int,
        min_value: int,
        max_value: int,
    ) -> int:
        """Safely coerce an integer setting."""
        if value in (None, "") or isinstance(value, bool):
            return default
        try:
            parsed = int(value)
        except TypeError, ValueError:
            return default
        return max(min_value, min(max_value, parsed))

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        """Safely coerce a boolean setting."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on", "开启", "启用"}:
                return True
            if lowered in {"false", "0", "no", "off", "关闭", "禁用"}:
                return False
        return default
