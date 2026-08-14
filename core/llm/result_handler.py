"""Formatting for asynchronous image-generation tool submissions."""

from __future__ import annotations

from ..config.manager import ConfigManager
from ..shared.logging import safe_log_text


class LLMResultHandler:
    """Create concise, truthful responses for LLM tool submissions.

    AstrBot's current public tool contract invokes a plugin-owned handler with
    the active event.  Completed work is therefore delivered through the
    public message capability instead of constructing an internal follow-up
    Agent turn.
    """

    def __init__(self, config_manager: ConfigManager) -> None:
        self.config_manager = config_manager

    def format_tool_start_result(
        self,
        *,
        prompt: str,
        reference_image_count: int,
        preset: str | None,
        preset_label: str = "预设",
        presets: list[str] | None = None,
        personas: list[str] | None = None,
        aspect_ratio: str,
        resolution: str,
        image_count: int,
        task_id: str,
    ) -> str:
        """Return the task receipt supplied to the calling model."""
        model = ""
        if self.config_manager.adapter_config:
            model = (
                f"{self.config_manager.adapter_config.name}/"
                f"{self.config_manager.adapter_config.model}"
            )

        template_parts: list[str] = []
        if presets:
            template_parts.append(f"预设={'、'.join(presets)}")
        if personas:
            template_parts.append(f"人设={'、'.join(personas)}")
        if not template_parts and preset:
            template_parts.append(f"{preset_label}={preset}")

        lines = [
            "✅ 生图任务已提交，正在后台执行。",
            f"任务ID: {task_id}",
            "状态: 排队/运行中（尚未完成）",
            f"请求数量: {image_count}；结果图片数以任务完成为准",
            f"模式: {'图生图' if reference_image_count else '文生图'}",
            f"参考图: {reference_image_count}张",
            f"宽高比: {aspect_ratio}，分辨率: {resolution}",
        ]
        if model:
            lines.append(f"模型: {model}")
        if template_parts:
            lines.append("模板: " + "，".join(template_parts))
        if prompt:
            lines.append(f"提示词摘要: {safe_log_text(prompt, 120)}")
        lines.append("完成后插件会直接向当前会话发送图片或失败原因。")
        return "\n".join(lines)
