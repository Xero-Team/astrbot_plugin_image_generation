"""Shared constants, types, and logging helpers."""

from .logging import (
    format_cn_log_fields,
    format_optional,
    format_seconds,
    log_prefix,
    mask_sensitive,
    safe_log_error_body,
    safe_log_mapping,
    safe_log_text,
    safe_log_url,
)
from .types import (
    AdapterConfig,
    AdapterMetadata,
    AdapterType,
    GenerationRequest,
    GenerationResult,
    ImageCapability,
    ImageData,
    has_image_capability,
)

__all__ = (
    "AdapterConfig",
    "AdapterMetadata",
    "AdapterType",
    "GenerationRequest",
    "GenerationResult",
    "ImageCapability",
    "ImageData",
    "format_cn_log_fields",
    "format_optional",
    "format_seconds",
    "has_image_capability",
    "log_prefix",
    "mask_sensitive",
    "safe_log_error_body",
    "safe_log_mapping",
    "safe_log_text",
    "safe_log_url",
)
