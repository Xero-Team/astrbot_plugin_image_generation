from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

import aiohttp
from astrbot.api import logger

from ..core.adapters.base import BaseImageAdapter
from ..core.shared.constants import OPENAI_DEFAULT_BASE_URL
from ..core.shared.logging import safe_log_error_body, safe_log_url
from ..core.shared.types import GenerationRequest, ImageCapability


class OpenAIChatAdapter(BaseImageAdapter):
    """Generic OpenAI Chat Completions-compatible image generation adapter."""

    DEFAULT_BASE_URL = OPENAI_DEFAULT_BASE_URL

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

        images = await self._extract_images(response, request.task_id)
        if images:
            return images, None

        # Try to extract a text error from chat completion responses.
        if "choices" in response and response["choices"]:
            content = response["choices"][0].get("message", {}).get("content")
            if isinstance(content, str) and content.strip():
                return None, f"未生成图片，API 返回文本: {content[:100]}"
        return None, "响应中未找到图片 data"

    def _build_payload(self, request: GenerationRequest) -> dict:
        """Build the request payload."""
        message_content: list[dict] = [
            {"type": "text", "text": self._build_prompt_text(request.prompt)}
        ]

        for image in request.images:
            b64_data = base64.b64encode(image.data).decode("utf-8")
            message_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image.mime_type};base64,{b64_data}"},
                }
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": message_content}],
            "stream": False,
        }

        if modalities := self._get_modalities():
            payload["modalities"] = modalities

        payload.update(self._get_extra_body())

        return payload

    def _build_prompt_text(self, prompt: str) -> str:
        """Build final text prompt sent to the chat endpoint."""
        prompt_prefix = str(
            self.config.extra.get("prompt_prefix", "Generate an image: ") or ""
        )
        return f"{prompt_prefix}{prompt}" if prompt_prefix else prompt

    def _get_modalities(self) -> list[str] | None:
        """Read optional modalities from adapter extra config."""
        raw_modalities = self.config.extra.get("modalities", ["image", "text"])
        if isinstance(raw_modalities, str):
            raw_modalities = raw_modalities.strip()
            if not raw_modalities:
                return None
            if raw_modalities.startswith("["):
                try:
                    raw_modalities = json.loads(raw_modalities)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        f"{self._get_log_prefix()} modalities JSON 解析失败: {exc}"
                    )
                    return None
            else:
                raw_modalities = re.split(r"[,，\s]+", raw_modalities)

        if not isinstance(raw_modalities, list):
            return None

        modalities: list[str] = []
        for item in raw_modalities:
            value = str(item or "").strip()
            if value and value not in modalities:
                modalities.append(value)
        return modalities or None

    def _get_extra_body(self) -> dict[str, Any]:
        """Read optional JSON body fields from adapter extra config."""
        raw_extra_body = self.config.extra.get("extra_body") or self.config.extra.get(
            "extra_body_json"
        )
        if not raw_extra_body:
            return {}
        if isinstance(raw_extra_body, dict):
            return dict(raw_extra_body)
        if not isinstance(raw_extra_body, str):
            return {}

        try:
            parsed = json.loads(raw_extra_body)
        except json.JSONDecodeError as exc:
            logger.warning(f"{self._get_log_prefix()} extra_body JSON 解析失败: {exc}")
            return {}
        if not isinstance(parsed, dict):
            logger.warning(f"{self._get_log_prefix()} extra_body 必须是 JSON 对象")
            return {}
        return parsed

    async def _make_request(
        self,
        session: aiohttp.ClientSession,
        payload: dict,
        request: GenerationRequest,
    ) -> dict | None:
        """Send the API request."""
        start_time = time.time()
        url = self._build_chat_completions_url()
        api_key = self._get_current_api_key()
        self._log_request_overview(
            request,
            url,
            payload=payload,
            extra={"API Key": "已配置" if api_key else "未配置"},
        )
        self._log_debug_json("请求", payload, request.task_id)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
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

    def _build_chat_completions_url(self) -> str:
        """Build a chat completions URL from flexible OpenAI-compatible base URLs."""
        base = (self.base_url or self.DEFAULT_BASE_URL).rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    async def _download_image_from_url(
        self, url: str, task_id: str | None = None
    ) -> bytes | None:
        """Download image bytes from a URL."""
        prefix = self._get_log_prefix(task_id)
        try:
            session = self._get_session()
            async with session.get(
                url, timeout=self._get_download_timeout()
            ) as response:
                if response.status == 200:
                    return await response.read()
                logger.error(
                    f"{prefix} 下载图像失败: {response.status} - {safe_log_url(url)}"
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"{prefix} 下载图像出错: {safe_log_error_body(exc)}")
        return None

    async def _extract_images(
        self, response_data: dict[str, Any], task_id: str | None = None
    ) -> list[bytes] | None:
        """Extract image bytes from response data."""
        images: list[bytes] = []
        prefix = self._get_log_prefix(task_id)

        # DALL-E-style response data.
        if isinstance(response_data.get("data"), list):
            for item in response_data["data"]:
                if not isinstance(item, dict):
                    continue
                if b64 := item.get("b64_json"):
                    try:
                        images.append(base64.b64decode(b64))
                    except Exception as e:
                        logger.warning(
                            f"{prefix} Base64 解码失败 (b64_json): {safe_log_error_body(e)}"
                        )
                elif url := item.get("url"):
                    if url.startswith("http"):
                        if content := await self._download_image_from_url(url, task_id):
                            images.append(content)
                    else:
                        decoded = self._decode_image_url(url, task_id)
                        if decoded:
                            images.append(decoded)

        # Chat-completions-style response data.
        if choices := response_data.get("choices"):
            message = (
                choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            )
            content = message.get("content")

            if isinstance(content, str):
                markdown_matches = re.findall(r"!\[.*?\]\((.*?)\)", content)
                for url in markdown_matches:
                    if url.startswith("http"):
                        if data := await self._download_image_from_url(url, task_id):
                            images.append(data)
                    else:
                        decoded = self._decode_image_url(url, task_id)
                        if decoded:
                            images.append(decoded)

                content_without_md = re.sub(r"!\[.*?\]\(.*?\)", "", content)
                pattern = re.compile(
                    r"data\s*:\s*image/([a-zA-Z0-9.+-]+)\s*;\s*base64\s*,\s*([-A-Za-z0-9+/=_\s]+)",
                    flags=re.IGNORECASE,
                )
                for _, b64_str in pattern.findall(content_without_md):
                    try:
                        images.append(base64.b64decode(b64_str))
                    except Exception as e:
                        logger.warning(
                            f"{prefix} Base64 解码失败 (inline): {safe_log_error_body(e)}"
                        )

            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        image_url = part.get("image_url", {}).get("url")
                        if not image_url:
                            continue
                        if image_url.startswith("http"):
                            if data := await self._download_image_from_url(
                                image_url, task_id
                            ):
                                images.append(data)
                        else:
                            decoded = self._decode_image_url(image_url, task_id)
                            if decoded:
                                images.append(decoded)

            if message.get("images"):
                for img_item in message["images"]:
                    url = None
                    if isinstance(img_item, dict):
                        url = img_item.get("url") or img_item.get("image_url", {}).get(
                            "url"
                        )
                    elif isinstance(img_item, str):
                        url = img_item
                    if not url:
                        continue
                    if url.startswith("http"):
                        if data := await self._download_image_from_url(url, task_id):
                            images.append(data)
                    else:
                        decoded = self._decode_image_url(url, task_id)
                        if decoded:
                            images.append(decoded)

        return images or None

    def _decode_image_url(self, url: str, task_id: str | None = None) -> bytes | None:
        """Decode an image represented as a data URL."""
        if url.startswith("data:image/") and ";base64," in url:
            try:
                _, _, data_part = url.partition(";base64,")
                return base64.b64decode(data_part)
            except Exception as exc:  # noqa: BLE001
                prefix = self._get_log_prefix(task_id)
                logger.warning(f"{prefix} Base64 解码失败: {safe_log_error_body(exc)}")
        return None
