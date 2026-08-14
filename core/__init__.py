"""Core module for the image generation plugin."""

from .adapters.base import BaseImageAdapter
from .adapters.generator import ImageGenerator
from .audit.safety import SafetyAuditor
from .config.manager import (
    ConfigManager,
    GenerationSettings,
    ImageAuditSettings,
    PluginConfig,
    PromptAuditSettings,
    SafetyAuditSettings,
    UsageSettings,
)
from .generation.executor import GenerationExecutor
from .generation.image_processor import ImageProcessor
from .generation.image_utils import (
    convert_image_format,
    convert_images_batch,
    detect_mime_type,
    validate_aspect_ratio,
    validate_resolution,
)
from .llm.tools import (
    adjust_tool_parameters,
    make_image_generation_tool,
    make_image_task_tool,
    make_preset_edit_tool,
    make_preset_query_tool,
)
from .shared.constants import (
    DEFAULT_ASPECT_RATIO,
    DEFAULT_MAX_RETRY_ATTEMPTS,
    DEFAULT_RESOLUTION,
    DEFAULT_TIMEOUT,
    GEMINI_DEFAULT_BASE_URL,
    GEMINI_SAFETY_CATEGORIES,
    GITEE_AI_DEFAULT_BASE_URL,
    JIMENG_DEFAULT_BASE_URL,
    LOG_PREFIX,
    OPENAI_DEFAULT_BASE_URL,
    RESOLUTION_1K_MAP,
    RESOLUTION_2K_MAP,
    SUPPORTED_ASPECT_RATIOS,
    SUPPORTED_RESOLUTIONS,
)
from .shared.logging import (
    log_prefix,
    mask_sensitive,
    safe_log_error_body,
    safe_log_mapping,
    safe_log_text,
    safe_log_url,
)
from .shared.types import (
    AdapterConfig,
    AdapterMetadata,
    AdapterType,
    GenerationRequest,
    GenerationResult,
    ImageCapability,
    ImageData,
)
from .tasks.manager import TaskManager
from .tasks.models import (
    GenerationTaskCreationError,
    GenerationTaskItem,
    GenerationTaskItemStatus,
    GenerationTaskRecord,
    GenerationTaskStatus,
)
from .tasks.store import GenerationTaskStore
from .tasks.usage import UsageManager

__all__ = [
    # Base classes and core components.
    "BaseImageAdapter",
    "ImageGenerator",
    "GenerationExecutor",
    "TaskManager",
    # Managers.
    "ConfigManager",
    "UsageManager",
    "ImageProcessor",
    # Configuration data classes.
    "PluginConfig",
    "UsageSettings",
    "GenerationSettings",
    "PromptAuditSettings",
    "ImageAuditSettings",
    "SafetyAuditSettings",
    # LLM tools.
    "adjust_tool_parameters",
    "make_image_generation_tool",
    "make_image_task_tool",
    "make_preset_edit_tool",
    "make_preset_query_tool",
    "SafetyAuditor",
    # Data types.
    "AdapterConfig",
    "AdapterMetadata",
    "AdapterType",
    "GenerationRequest",
    "GenerationResult",
    "GenerationTaskCreationError",
    "GenerationTaskItem",
    "GenerationTaskItemStatus",
    "GenerationTaskRecord",
    "GenerationTaskStatus",
    "GenerationTaskStore",
    "ImageCapability",
    "ImageData",
    # Utility functions.
    "convert_image_format",
    "convert_images_batch",
    "detect_mime_type",
    "log_prefix",
    "mask_sensitive",
    "safe_log_error_body",
    "safe_log_mapping",
    "safe_log_text",
    "safe_log_url",
    "validate_aspect_ratio",
    "validate_resolution",
    # Constants.
    "LOG_PREFIX",
    "DEFAULT_TIMEOUT",
    "DEFAULT_MAX_RETRY_ATTEMPTS",
    "DEFAULT_ASPECT_RATIO",
    "DEFAULT_RESOLUTION",
    "GEMINI_DEFAULT_BASE_URL",
    "GEMINI_SAFETY_CATEGORIES",
    "OPENAI_DEFAULT_BASE_URL",
    "GITEE_AI_DEFAULT_BASE_URL",
    "JIMENG_DEFAULT_BASE_URL",
    "RESOLUTION_1K_MAP",
    "RESOLUTION_2K_MAP",
    "SUPPORTED_ASPECT_RATIOS",
    "SUPPORTED_RESOLUTIONS",
]
