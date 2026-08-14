from __future__ import annotations

import base64
import time

import aiohttp
from astrbot.api import logger

from ..core.adapters.base import BaseImageAdapter
from ..core.shared.constants import (
    GEMINI_DEFAULT_BASE_URL,
    GEMINI_SAFETY_CATEGORIES,
    UNSPECIFIED_OPTION,
)
from ..core.shared.logging import safe_log_error_body
from ..core.shared.types import GenerationRequest, ImageCapability


class GeminiAdapter(BaseImageAdapter):
    """Native Gemini image generation adapter."""

    DEFAULT_BASE_URL = GEMINI_DEFAULT_BASE_URL

    def get_capabilities(self) -> ImageCapability:
        """Return adapter capabilities."""
        return self._get_configured_capabilities()

    # generate() is provided by the base class via the template method pattern.

    async def _generate_once(
        self, request: GenerationRequest
    ) -> tuple[list[bytes] | None, str | None]:
        """Execute one image generation request."""
        payload = self._build_payload(request)
        session = self._get_session()
        response = await self._make_request(session, payload, request)
        if response is None:
            return None, "API 请求失败"
        if response_error := response.get("error"):
            if isinstance(response_error, dict):
                message = response_error.get("message") or response_error.get("code")
                return None, str(message or response_error)
            return None, str(response_error)

        images = self._extract_images(response, request.task_id)
        if images:
            return images, None
        return None, "响应中未找到图片数据"

    def _build_payload(self, request: GenerationRequest) -> dict:
        """Build the request payload."""
        generation_config: dict = {"responseModalities": ["IMAGE"]}
        image_config: dict = {}

        if (
            request.aspect_ratio
            and request.aspect_ratio != UNSPECIFIED_OPTION
            and not request.images
        ):
            image_config["aspectRatio"] = request.aspect_ratio

        if (
            request.resolution
            and request.resolution != UNSPECIFIED_OPTION
            and "gemini-3" in self.model.lower()
        ):
            image_config["imageSize"] = request.resolution

        if image_config:
            generation_config["imageConfig"] = image_config

        safety_settings = []
        if self.safety_settings:
            for category in GEMINI_SAFETY_CATEGORIES:
                safety_settings.append(
                    {"category": category, "threshold": self.safety_settings}
                )

        parts = [{"text": request.prompt}]
        for image in request.images:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": image.mime_type,
                        "data": base64.b64encode(image.data).decode("utf-8"),
                    }
                }
            )

        payload: dict = {
            "contents": [{"parts": parts}],
            "generationConfig": generation_config,
        }

        if safety_settings:
            payload["safetySettings"] = safety_settings

        return payload

    async def _make_request(
        self,
        session: aiohttp.ClientSession,
        payload: dict,
        request: GenerationRequest,
    ) -> dict | None:
        """Send the API request."""
        start_time = time.time()
        url = f"{self.base_url or self.DEFAULT_BASE_URL}/v1beta/models/{self.model}:generateContent"
        api_key = self._get_current_api_key()
        self._log_request_overview(
            request,
            url,
            payload=payload,
            extra={"API Key": "已配置" if api_key else "未配置"},
        )
        self._log_debug_json("请求", payload, request.task_id)

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }

        try:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=self._get_timeout(),
                proxy=self.proxy,
            ) as response:
                duration = time.time() - start_time
                self._log_response_status(request, response.status, duration)
                if response.status != 200:
                    error_text = await response.text()
                    self._log_debug_json_text("响应", error_text, request.task_id)
                    self._log_api_error(request, response.status, duration, error_text)
                    return {
                        "error": {
                            "message": self._format_api_error_message(
                                response.status,
                                error_text,
                            )
                        }
                    }
                return await self._read_response_json(response, request.task_id)
        except Exception as e:
            duration = time.time() - start_time
            self._log_request_exception(request, duration, e)
            return None

    def _extract_images(
        self, response: dict, task_id: str | None
    ) -> list[bytes] | None:
        """Extract image bytes from the response payload."""
        prefix = self._get_log_prefix(task_id)
        try:
            candidates = response.get("candidates", [])
            if self.debug_request_logging:
                logger.debug(f"{prefix} 候选结果: {len(candidates)}")
            if not candidates:
                return None

            parts = candidates[0].get("content", {}).get("parts", [])
            images: list[bytes] = []
            for part in parts:
                inline_data = part.get("inline_data") or part.get("inlineData")
                if inline_data and inline_data.get("data"):
                    images.append(base64.b64decode(inline_data["data"]))

            return images if images else None
        except Exception as exc:  # noqa: BLE001
            logger.error(f"{prefix} 解析失败: {safe_log_error_body(exc)}")
            return None
