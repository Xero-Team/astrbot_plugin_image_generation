"""Public-SDK LLM tools for image generation."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from astrbot.api import FunctionTool, logger
from astrbot.api.event import AstrMessageEvent

from ..config.templates import (
    build_generation_prompt,
    find_named_entry,
    format_template_summary,
    parse_preset_prompt,
)
from ..config.templates import (
    normalize_name_items as _normalize_name_items,
)
from ..generation.reference_collector import (
    collect_tool_reference_images,
)
from ..generation.reference_collector import (
    normalize_string_items as _normalize_string_items,
)
from ..shared.constants import SUPPORTED_ASPECT_RATIOS, SUPPORTED_RESOLUTIONS
from ..shared.logging import (
    log_prefix,
    mask_sensitive,
    safe_log_error_body,
)
from ..shared.types import ImageCapability, has_image_capability
from ..tasks.ids import new_task_id
from ..tasks.models import GenerationTaskCreationError

ASPECT_RATIO_OPTIONS = list(SUPPORTED_ASPECT_RATIOS)
RESOLUTION_OPTIONS = list(SUPPORTED_RESOLUTIONS)
LOG = log_prefix("LLMTool")


def _own_tool(tool: FunctionTool) -> FunctionTool:
    """Mark an explicit SDK tool as belonging to this plugin package.

    The public ``context.tools.add()`` capability resolves ownership from the
    tool instance's module.  ``FunctionTool`` itself is defined in the SDK, so
    explicit instances need this module marker before publication.
    """
    tool.__module__ = __name__
    return tool


def normalize_string_items(raw: Any) -> list[str]:
    """Normalize one or many string-like tool values."""
    return _normalize_string_items(raw)


def normalize_name_items(raw: Any) -> list[str]:
    """Normalize one or many preset/persona names."""
    return _normalize_name_items(raw)


def normalize_preset_query_category(category: str) -> str:
    """Normalize preset query category aliases."""
    normalized = category.strip().lower()
    if normalized in {
        "人设",
        "persona",
        "personas",
        "list_persona",
        "list_personas",
        "get_persona",
        "persona_list",
    }:
        return "persona"
    return "preset"


def normalize_preset_edit_action(action: str) -> str:
    """Normalize preset edit action aliases."""
    normalized = action.strip().lower()
    if normalized in {"添加", "新增", "保存", "save", "create", "add", "add_preset"}:
        return "create_preset"
    if normalized in {"删除", "移除", "remove", "del", "delete", "delete_preset"}:
        return "delete_preset"
    return normalized


def normalize_task_action(action: str) -> str:
    """Normalize image task management action aliases."""
    normalized = action.strip().lower()
    if normalized in {"", "列表", "查看列表", "list", "list_tasks", "tasks"}:
        return "list"
    if normalized in {"详情", "查看", "查询", "detail", "get", "show"}:
        return "detail"
    if normalized in {"取消", "cancel", "cancel_task"}:
        return "cancel"
    return normalized


def _parse_preset(
    plugin: Any,
    preset_names: Any,
    aspect_ratio: Any,
    resolution: Any,
) -> tuple[list[str], str, str, list[str], str | None]:
    """Resolve selected presets and their optional generation overrides."""
    names = normalize_name_items(preset_names)
    if not names:
        return [], str(aspect_ratio), str(resolution), [], None

    prompt_parts: list[str] = []
    matched_presets: list[str] = []
    for preset_name in names:
        matched_preset = find_named_entry(plugin.config_manager.presets, preset_name)
        if not matched_preset:
            return (
                [],
                str(aspect_ratio),
                str(resolution),
                [],
                f"❌ 预设不存在: {preset_name}",
            )
        preset_prompt, aspect_ratio, resolution = parse_preset_prompt(
            plugin.config_manager.presets[matched_preset],
            str(aspect_ratio),
            str(resolution),
        )
        if preset_prompt:
            prompt_parts.append(preset_prompt)
        matched_presets.append(matched_preset)
    return prompt_parts, str(aspect_ratio), str(resolution), matched_presets, None


def _parse_persona(
    plugin: Any,
    persona_names: Any,
) -> tuple[list[str], list[tuple[str, str]], list[str], str | None]:
    """Resolve selected personas and their optional reference images."""
    names = normalize_name_items(persona_names)
    if not names:
        return [], [], [], None

    prompt_parts: list[str] = []
    persona_images: list[tuple[str, str]] = []
    matched_personas: list[str] = []
    for persona_name in names:
        matched_persona = find_named_entry(plugin.config_manager.personas, persona_name)
        if not matched_persona:
            return [], [], [], f"❌ 人设不存在: {persona_name}"
        persona = plugin.config_manager.personas[matched_persona]
        if persona.prompt.strip():
            prompt_parts.append(persona.prompt.strip())
        if persona.image:
            persona_images.append((matched_persona, persona.image))
        matched_personas.append(matched_persona)
    return prompt_parts, persona_images, matched_personas, None


async def _start_generation_task(
    plugin: Any,
    event: AstrMessageEvent,
    *,
    prompt: str,
    aspect_ratio: str,
    resolution: str,
    reference_images: Any = None,
    avatar_references: Any = None,
    persona_images: list[tuple[str, str]] | None = None,
    preset_or_persona: str | None = None,
    preset_label: str = "预设",
    presets: list[str] | None = None,
    personas: list[str] | None = None,
    image_count: int = 1,
) -> str:
    """Validate a tool request, then enqueue an image task."""
    if not plugin.generator or not plugin.generator.adapter:
        return "❌ 生图生成器未初始化"

    image_count = plugin.normalize_image_count(image_count)
    is_usage_limit_admin = plugin.is_usage_limit_admin(event)
    if not plugin.has_required_api_key():
        logger.debug(
            "%s 工具调用失败: 未配置 API Key (用户: %s)",
            LOG,
            mask_sensitive(event.unified_msg_origin),
        )
        return "❌ 未配置 API Key，无法生成图片"

    check_result = plugin.usage_manager.check_rate_limit(
        event.unified_msg_origin,
        is_admin=is_usage_limit_admin,
        requested_count=image_count,
        update_timestamp=False,
    )
    if isinstance(check_result, str):
        return check_result

    prompt_allowed, prompt_reason = await plugin.safety_auditor.audit_prompt(
        prompt,
        event.unified_msg_origin,
    )
    if not prompt_allowed:
        return f"❌ 提示词审核未通过: {prompt_reason}"
    if rejection := plugin.task_manager.get_generation_queue_rejection():
        code, message = rejection
        return f"❌ 生图任务提交失败: {message} ({code})"

    check_result = plugin.usage_manager.check_rate_limit(
        event.unified_msg_origin,
        is_admin=is_usage_limit_admin,
        requested_count=image_count,
    )
    if isinstance(check_result, str):
        return check_result

    usage_reserved = True
    task_created = False
    task_id = new_task_id()
    try:
        try:
            images_data = await collect_tool_reference_images(
                plugin.image_processor,
                event,
                capabilities=plugin.generator.adapter.get_capabilities(),
                reference_images=reference_images,
                avatar_references=avatar_references,
                persona_images=persona_images,
                task_id=task_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "%s 处理参考图失败: %s",
                log_prefix("LLMTool", task_id),
                safe_log_error_body(exc, 200),
                exc_info=True,
            )
            images_data = []

        plugin.create_generation_task(
            task_id=task_id,
            source="LLM工具",
            prompt=prompt,
            images_data=images_data,
            unified_msg_origin=event.unified_msg_origin,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            image_count=image_count,
            is_usage_limit_admin=is_usage_limit_admin,
            preset=preset_or_persona,
            preset_label=preset_label,
            presets=presets,
            personas=personas,
        )
        task_created = True
    except asyncio.CancelledError:
        if usage_reserved and not task_created:
            plugin.usage_manager.release_reserved_usage(
                event.unified_msg_origin,
                is_admin=is_usage_limit_admin,
                count=image_count,
            )
        raise
    except GenerationTaskCreationError as exc:
        usage_reserved = False
        return f"❌ 生图任务提交失败: {exc.message} ({exc.code})"
    except Exception as exc:
        if usage_reserved and not task_created:
            plugin.usage_manager.release_reserved_usage(
                event.unified_msg_origin,
                is_admin=is_usage_limit_admin,
                count=image_count,
            )
        logger.error(
            "%s 生图任务提交失败: %s",
            log_prefix("LLMTool", task_id),
            safe_log_error_body(exc, 200),
            exc_info=True,
        )
        return f"❌ 生图任务提交失败: {safe_log_error_body(exc, 160)}"

    return plugin.llm_result_handler.format_tool_start_result(
        prompt=prompt,
        reference_image_count=len(images_data),
        preset=preset_or_persona,
        preset_label=preset_label,
        presets=presets,
        personas=personas,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        image_count=image_count,
        task_id=task_id,
    )


def _format_preset_detail(name: str, content: Any) -> str:
    """Format one preset's content for a model-facing answer."""
    content_text = str(content or "").strip()
    lines = [f"📋 预设详情: {name}"]
    if content_text.startswith("{"):
        try:
            preset_data = json.loads(content_text)
        except json.JSONDecodeError:
            lines.extend(
                ("格式: 高级 JSON（解析失败，将按原文展示）", f"内容: {content_text}")
            )
            return "\n".join(lines)
        if isinstance(preset_data, dict):
            lines.append("格式: 高级 JSON")
            for key, label in (
                ("prompt", "提示词"),
                ("aspect_ratio", "宽高比"),
                ("resolution", "分辨率"),
                ("description", "描述"),
            ):
                if value := str(preset_data.get(key, "") or "").strip():
                    lines.append(f"{label}: {value}")
            lines.append(f"原始内容: {content_text}")
            return "\n".join(lines)
    return "\n".join(
        (f"📋 预设详情: {name}", "格式: 简单提示词", f"内容: {content_text}")
    )


def _format_persona_detail(name: str, persona: Any) -> str:
    """Format one persona's content for a model-facing answer."""
    return "\n".join(
        (
            f"👤 人设详情: {name}",
            f"提示词: {persona.prompt}",
            f"参考图: {persona.image or '无'}",
        )
    )


def _validate_preset_content(content: str) -> str | None:
    """Validate optional structured preset content."""
    if not content.startswith("{"):
        return None
    try:
        preset_data = json.loads(content)
    except json.JSONDecodeError as exc:
        return f"高级 JSON 预设格式错误: {exc}"
    if not isinstance(preset_data, dict):
        return "高级 JSON 预设必须是对象"
    if not str(preset_data.get("prompt", "") or "").strip():
        return "高级 JSON 预设必须包含非空 prompt 字段"
    aspect_ratio = str(preset_data.get("aspect_ratio", "") or "").strip()
    if aspect_ratio and aspect_ratio not in ASPECT_RATIO_OPTIONS:
        return f"高级 JSON 预设的 aspect_ratio 不支持: {aspect_ratio}"
    resolution = str(preset_data.get("resolution", "") or "").strip()
    if resolution and resolution not in RESOLUTION_OPTIONS:
        return f"高级 JSON 预设的 resolution 不支持: {resolution}"
    return None


def make_image_generation_tool(plugin: Any) -> FunctionTool:
    """Create the configured image-generation FunctionTool."""

    async def handler(event: AstrMessageEvent, **kwargs: Any) -> str:
        prompt = str(kwargs.get("prompt", "") or "").strip()
        aspect_ratio = (
            kwargs.get("aspect_ratio") or plugin.config_manager.default_aspect_ratio
        )
        resolution = (
            kwargs.get("resolution") or plugin.config_manager.default_resolution
        )
        preset_prompts, aspect_ratio, resolution, presets, error = _parse_preset(
            plugin, kwargs.get("preset"), aspect_ratio, resolution
        )
        if error:
            return error
        persona_prompts, persona_images, personas, error = _parse_persona(
            plugin, kwargs.get("persona")
        )
        if error:
            return error
        prompt = build_generation_prompt(
            preset_prompts=preset_prompts,
            persona_prompts=persona_prompts,
            extra_prompt=prompt,
        )
        if not prompt:
            return "❌ 请提供图片生成的提示词、预设或人设"
        preset_or_persona, preset_label = format_template_summary(presets, personas)
        return await _start_generation_task(
            plugin,
            event,
            prompt=prompt,
            aspect_ratio=str(aspect_ratio),
            resolution=str(resolution),
            reference_images=kwargs.get("reference_images"),
            avatar_references=kwargs.get("avatar_references"),
            persona_images=persona_images,
            preset_or_persona=preset_or_persona,
            preset_label=preset_label,
            presets=presets,
            personas=personas,
            image_count=kwargs.get("image_count")
            or plugin.config_manager.default_image_count,
        )

    tool = FunctionTool(
        name="generate_image",
        description="使用生图模型生成或修改图片。需要人物、自拍、头像或角色出镜时填写 persona。",
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "额外图片提示词。"},
                "preset": {
                    "type": "string",
                    "description": "一个或多个已配置预设名称，空格分隔。",
                },
                "persona": {
                    "type": "string",
                    "description": "一个或多个已配置人设名称，空格分隔。",
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ASPECT_RATIO_OPTIONS,
                    "default": "不指定",
                },
                "resolution": {
                    "type": "string",
                    "enum": RESOLUTION_OPTIONS,
                    "default": "不指定",
                },
                "image_count": {"type": "integer", "minimum": 1, "default": 1},
                "avatar_references": {"type": "array", "items": {"type": "string"}},
                "reference_images": {"type": "array", "items": {"type": "string"}},
            },
        },
        handler=handler,
        parallel_policy="serial",
    )
    adjust_tool_parameters(tool, plugin.generator.adapter.get_capabilities())
    if not plugin.config_manager.personas:
        tool.parameters["properties"].pop("persona", None)
    return _own_tool(tool)


def make_preset_query_tool(plugin: Any) -> FunctionTool:
    """Create the configured preset/persona query tool."""

    async def handler(event: AstrMessageEvent, **kwargs: Any) -> str:
        if not plugin.usage_manager.is_limit_exempt(
            event.unified_msg_origin,
            is_admin=plugin.is_usage_limit_admin(event),
        ) and plugin.usage_manager.is_session_blocked(event.unified_msg_origin):
            return plugin.config_manager.usage_settings.blacklist_block_message
        category = normalize_preset_query_category(
            str(kwargs.get("category", "preset") or "preset")
        )
        name = str(kwargs.get("name", "") or "").strip()
        if category == "persona":
            if name:
                matched = find_named_entry(plugin.config_manager.personas, name)
                return (
                    _format_persona_detail(
                        matched, plugin.config_manager.personas[matched]
                    )
                    if matched
                    else f"❌ 人设不存在: {name}"
                )
            if not plugin.config_manager.personas:
                return "👤 当前没有人设"
            return "\n".join(
                [
                    "👤 人设列表:",
                    *[
                        f"{index}. {persona_name} [{'有参考图' if persona.image else '无参考图'}]"
                        for index, (persona_name, persona) in enumerate(
                            plugin.config_manager.personas.items(), 1
                        )
                    ],
                ]
            )
        if name:
            matched = find_named_entry(plugin.config_manager.presets, name)
            return (
                _format_preset_detail(matched, plugin.config_manager.presets[matched])
                if matched
                else f"❌ 预设不存在: {name}"
            )
        if not plugin.config_manager.presets:
            return "📋 当前没有预设"
        return "\n".join(
            [
                "📋 预设列表:",
                *[
                    f"{index}. {preset_name}"
                    for index, preset_name in enumerate(
                        plugin.config_manager.presets, 1
                    )
                ],
            ]
        )

    return _own_tool(
        FunctionTool(
            name="query_image_presets",
            description="查询生图预设或人设。填写 name 可以查看完整内容。",
            parameters={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["preset", "persona"],
                        "default": "preset",
                    },
                    "name": {"type": "string"},
                },
            },
            handler=handler,
        )
    )


def make_preset_edit_tool(plugin: Any) -> FunctionTool:
    """Create the configured preset editing tool."""

    async def handler(event: AstrMessageEvent, **kwargs: Any) -> str:
        if not plugin.usage_manager.is_limit_exempt(
            event.unified_msg_origin, is_admin=plugin.is_usage_limit_admin(event)
        ) and plugin.usage_manager.is_session_blocked(event.unified_msg_origin):
            return plugin.config_manager.usage_settings.blacklist_block_message
        action = normalize_preset_edit_action(str(kwargs.get("action", "") or ""))
        name = str(kwargs.get("name", "") or "").strip()
        if not name:
            return "❌ 请提供预设名称"
        if action == "create_preset":
            prompt = str(kwargs.get("prompt", "") or "").strip()
            if not prompt:
                return "❌ 请提供预设内容"
            if error := _validate_preset_content(prompt):
                return f"❌ {error}"
            plugin.config_manager.save_preset(name, prompt)
            return f"✅ 预设已保存: {name}"
        if action == "delete_preset":
            matched = find_named_entry(plugin.config_manager.presets, name)
            if not matched:
                return f"❌ 预设不存在: {name}"
            plugin.config_manager.delete_preset(matched)
            return f"✅ 预设已删除: {matched}"
        return "❌ 不支持的操作，请使用 create_preset 或 delete_preset"

    return _own_tool(
        FunctionTool(
            name="edit_image_presets",
            description="创建或删除生图预设；不修改人设。",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create_preset", "delete_preset"],
                    },
                    "name": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["action", "name"],
            },
            handler=handler,
            parallel_policy="serial",
        )
    )


def make_image_task_tool(plugin: Any) -> FunctionTool:
    """Create the configured task-management tool."""

    async def handler(event: AstrMessageEvent, **kwargs: Any) -> str:
        origin = event.unified_msg_origin
        action = normalize_task_action(str(kwargs.get("action", "list") or "list"))
        task_ref = str(kwargs.get("task", "") or "").strip()
        if action == "list":
            return plugin.format_task_list(
                plugin.task_manager.list_generation_tasks(
                    unified_msg_origin=origin, include_finished=False, limit=10
                )
            )
        if action == "detail":
            if not task_ref:
                return "❌ 请提供要查看的任务编号或任务ID"
            record = plugin.resolve_task_reference(
                origin, task_ref, include_finished=True
            )
            if not record:
                return (
                    "❌ 未找到编号对应的进行中任务；已结束任务请使用完整任务ID查看"
                    if task_ref.isdigit()
                    else f"❌ 任务不存在或已被清理: {task_ref}"
                )
            return plugin.format_task_detail(record)
        if action == "cancel":
            if not task_ref:
                active = plugin.task_manager.list_generation_tasks(
                    unified_msg_origin=origin, include_finished=False, limit=5
                )
                return (
                    "❌ 请提供要取消的编号或任务ID\n" + plugin.format_task_list(active)
                    if active
                    else "📭 当前没有可取消的生图任务"
                )
            record = plugin.resolve_active_task_reference(origin, task_ref)
            if not record:
                return f"❌ 未找到对应的进行中任务，请检查编号或任务ID: {task_ref}"
            _, message = plugin.task_manager.cancel_generation_task(
                record.task_id, unified_msg_origin=origin
            )
            return message
        return "❌ 不支持的操作，请使用 list、detail 或 cancel"

    return _own_tool(
        FunctionTool(
            name="manage_image_tasks",
            description="查看、查看详情或取消当前会话的生图任务。",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "detail", "cancel"],
                        "default": "list",
                    },
                    "task": {"type": "string"},
                },
            },
            handler=handler,
            parallel_policy="serial",
        )
    )


def adjust_tool_parameters(tool: FunctionTool, capabilities: ImageCapability) -> None:
    """Remove schema fields unsupported by the active image adapter."""
    props = tool.parameters.get("properties", {})
    for capability, key in (
        (ImageCapability.ASPECT_RATIO, "aspect_ratio"),
        (ImageCapability.RESOLUTION, "resolution"),
    ):
        if not has_image_capability(capabilities, capability):
            props.pop(key, None)
    if not has_image_capability(capabilities, ImageCapability.IMAGE_TO_IMAGE):
        props.pop("avatar_references", None)
        props.pop("reference_images", None)


__all__ = (
    "adjust_tool_parameters",
    "make_image_generation_tool",
    "make_image_task_tool",
    "make_preset_edit_tool",
    "make_preset_query_tool",
)
