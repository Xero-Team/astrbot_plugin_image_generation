"""Dashboard Extension Protocol v1 actions for image generation."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from astrbot.api.dashboard import (
    DashboardActionContext,
    DashboardActionError,
    DashboardFile,
    DashboardFileAction,
    DashboardJsonAction,
    DashboardUploadAction,
    DashboardUploadedFile,
)

from ..shared.logging import safe_log_text
from ..shared.types import ImageCapability, has_image_capability
from ..tasks.models import GenerationTaskRecord

PAGE_PREVIEW_MAX_BYTES = 180 * 1024
PAGE_EXPORT_MAX_BYTES = 128 * 1024 * 1024
PAGE_IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}
PAGE_IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Result(BaseModel):
    payload: dict[str, Any]


class _TaskRequest(_Request):
    task_id: str = Field(min_length=1, max_length=128)


class _ImageRequest(_TaskRequest):
    image_index: int = Field(ge=1, le=100)


class _TasksRequest(_Request):
    status: str = "all"
    source: str = ""
    model: str = ""
    keyword: str = Field(default="", max_length=256)
    days: int = Field(default=0, ge=0, le=3650)
    offset: int = Field(default=0, ge=0, le=5000)
    limit: int = Field(default=30, ge=1, le=200)


class _GenerateRequest(_Request):
    prompt: str = Field(default="", max_length=16_000)
    model: str = Field(default="", max_length=512)
    image_count: int = Field(default=1, ge=1, le=32)
    aspect_ratio: str = Field(default="不指定", max_length=32)
    resolution: str = Field(default="不指定", max_length=32)
    presets: list[str] = Field(default_factory=list, max_length=20)
    personas: list[str] = Field(default_factory=list, max_length=20)
    reference_tokens: list[str] = Field(default_factory=list, max_length=8)


class _UploadFields(_Request):
    pass


class ImageGenerationPageAPI:
    """Expose plugin controls through the sandboxed Dashboard page SDK."""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._plugin_root = Path(__file__).resolve().parents[2]
        self._export_root = self._plugin_root / "dashboard_exports"

    def register(self) -> None:
        """Register the metadata-declared actions during plugin initialization."""
        registrar = self.plugin.context.dashboard_extensions.for_plugin(self.plugin)
        registrar.register_json(
            DashboardJsonAction(
                name="image.state.read",
                input_model=_Request,
                output_model=_Result,
                description="Read image-generation runtime state",
            ),
            self.read_state,
        )
        registrar.register_json(
            DashboardJsonAction(
                name="image.tasks.list",
                input_model=_TasksRequest,
                output_model=_Result,
                description="List image-generation tasks",
            ),
            self.list_tasks,
        )
        registrar.register_json(
            DashboardJsonAction(
                name="image.tasks.detail",
                input_model=_TaskRequest,
                output_model=_Result,
                description="Read one image-generation task",
            ),
            self.task_detail,
        )
        registrar.register_json(
            DashboardJsonAction(
                name="image.tasks.cancel",
                input_model=_TaskRequest,
                output_model=_Result,
                description="Cancel one image-generation task",
            ),
            self.cancel_task,
        )
        registrar.register_json(
            DashboardJsonAction(
                name="image.generate.submit",
                input_model=_GenerateRequest,
                output_model=_Result,
                timeout_seconds=60,
                description="Submit an image-generation task",
            ),
            self.submit_generation,
        )
        registrar.register_upload(
            DashboardUploadAction(
                name="image.reference.upload",
                fields_model=_UploadFields,
                output_model=_Result,
                max_file_bytes=64 * 1024 * 1024,
                allowed_content_types=frozenset(PAGE_IMAGE_EXTENSIONS),
                allowed_extensions=frozenset(PAGE_IMAGE_MIME_TYPES),
                description="Upload a reference image",
            ),
            self.upload_reference,
        )
        registrar.register_json(
            DashboardJsonAction(
                name="image.preview.read",
                input_model=_ImageRequest,
                output_model=_Result,
                description="Read a small image preview",
            ),
            self.read_preview,
        )
        registrar.register_file(
            DashboardFileAction(
                name="image.download",
                input_model=_ImageRequest,
                disposition="attachment",
                max_file_bytes=PAGE_EXPORT_MAX_BYTES,
                allowed_content_types=frozenset(PAGE_IMAGE_EXTENSIONS),
                description="Download one generated image",
            ),
            self.download_image,
        )

    def terminate(self) -> None:
        """Remove short-lived Dashboard exports left by file actions."""
        if not self._export_root.is_dir():
            return
        for export in self._export_root.iterdir():
            if export.is_file() or export.is_symlink():
                export.unlink(missing_ok=True)

    def _result_image_path(
        self,
        record: GenerationTaskRecord,
        image_index: int,
    ) -> Path | None:
        if image_index < 1 or image_index > len(record.result_paths):
            return None
        candidate = Path(record.result_paths[image_index - 1]).resolve()
        try:
            candidate.relative_to(self.plugin.image_temp_dir.resolve())
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _image_mime_type(self, path: Path) -> str:
        return PAGE_IMAGE_MIME_TYPES.get(
            path.suffix.lower(),
            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        )

    def _image_payload(
        self, record: GenerationTaskRecord, index: int
    ) -> dict[str, Any]:
        path = self._result_image_path(record, index)
        filename = Path(record.result_paths[index - 1]).name
        return {
            "index": index,
            "filename": filename,
            "available": path is not None,
            "size": path.stat().st_size if path else 0,
            "mime_type": self._image_mime_type(path) if path else "",
        }

    def _task_payload(
        self,
        record: GenerationTaskRecord,
        *,
        detail: bool = False,
    ) -> dict[str, Any]:
        request_stats = record.request_stats
        total = request_stats.get("total", record.requested_count)
        completed = request_stats.get("finished", 0)
        data: dict[str, Any] = {
            "task_id": record.task_id,
            "status": record.status.value,
            "status_label": record.status_label,
            "active": record.is_active,
            "source": record.source,
            "model": record.model or "",
            "prompt_summary": record.prompt_summary,
            "reference_image_count": record.reference_image_count,
            "requested_count": record.requested_count,
            "result_count": record.result_count or len(record.result_paths),
            "aspect_ratio": record.aspect_ratio,
            "resolution": record.resolution,
            "preset": record.preset or "",
            "preset_label": record.preset_label,
            "message": record.message,
            "error": record.error,
            "created_at": record.created_at.isoformat(timespec="seconds")
            if record.created_at
            else None,
            "started_at": record.started_at.isoformat(timespec="seconds")
            if record.started_at
            else None,
            "finished_at": record.finished_at.isoformat(timespec="seconds")
            if record.finished_at
            else None,
            "progress_percent": int((completed / total) * 100) if total else 0,
            "result_images": [
                self._image_payload(record, index)
                for index in range(1, len(record.result_paths) + 1)
            ],
        }
        if detail:
            data["prompt"] = record.prompt or record.prompt_summary
            data["items"] = [
                {
                    "index": item.index,
                    "status": item.status.value,
                    "result_count": item.result_count,
                    "error": item.error,
                }
                for item in sorted(record.items.values(), key=lambda item: item.index)
            ]
        return data

    async def read_state(
        self,
        _payload: _Request,
        _context: DashboardActionContext,
    ) -> _Result:
        """Return the current adapter and queue state."""
        config = self.plugin.config_manager.adapter_config
        capabilities = (
            self.plugin.generator.adapter.get_capabilities()
            if self.plugin.generator and self.plugin.generator.adapter
            else ImageCapability.NONE
        )
        available_models: list[str] = []
        for provider in getattr(
            self.plugin.config_manager, "_all_provider_configs", []
        ):
            available_models.extend(
                f"{provider.name}/{model}" if provider.name else model
                for model in provider.available_models
            )
        return _Result(
            payload={
                "initialized": bool(
                    self.plugin.generator and self.plugin.generator.adapter
                ),
                "has_api_key": self.plugin.has_required_api_key(),
                "current_model": f"{config.name}/{config.model}" if config else "",
                "available_models": available_models,
                "default_image_count": self.plugin.config_manager.default_image_count,
                "max_image_count": self.plugin.config_manager.max_image_count,
                "default_aspect_ratio": self.plugin.config_manager.default_aspect_ratio,
                "default_resolution": self.plugin.config_manager.default_resolution,
                "supports_image_to_image": has_image_capability(
                    capabilities,
                    ImageCapability.IMAGE_TO_IMAGE,
                ),
                "presets": [
                    {"name": name, "summary": safe_log_text(value, 120)}
                    for name, value in self.plugin.config_manager.presets.items()
                ],
                "personas": [
                    {
                        "name": name,
                        "summary": safe_log_text(persona.prompt, 120),
                        "has_image": bool(persona.image),
                    }
                    for name, persona in self.plugin.config_manager.personas.items()
                ],
                "queue": {
                    "can_accept": self.plugin.task_manager.can_accept_generation_task(),
                    "max_running": self.plugin.config_manager.max_running_generation_tasks,
                    "max_queued": self.plugin.config_manager.max_queued_generation_tasks,
                },
            }
        )

    async def list_tasks(
        self,
        payload: _TasksRequest,
        _context: DashboardActionContext,
    ) -> _Result:
        """List local task history using bounded filtering and pagination."""
        records = self.plugin.task_manager.list_generation_tasks(
            include_finished=True,
            limit=min(5000, max(200, payload.limit + payload.offset)),
        )
        since = datetime.now() - timedelta(days=payload.days) if payload.days else None
        filtered: list[GenerationTaskRecord] = []
        models: set[str] = set()
        sources: set[str] = set()
        for record in records:
            model = str(record.model or "").strip()
            source = str(record.source or "").strip()
            if model:
                models.add(model)
            if source:
                sources.add(source)
            if payload.status == "active" and not record.is_active:
                continue
            if (
                payload.status not in {"", "all", "active"}
                and record.status.value != payload.status
            ):
                continue
            if payload.source and payload.source.lower() not in source.lower():
                continue
            if payload.model and payload.model.lower() not in model.lower():
                continue
            stamp = record.finished_at or record.started_at or record.created_at
            if since and stamp and stamp < since:
                continue
            if payload.keyword:
                haystack = " ".join(
                    (
                        record.task_id,
                        record.prompt_summary or "",
                        record.prompt or "",
                        record.preset or "",
                        source,
                        model,
                        record.status.value,
                    )
                ).lower()
                if payload.keyword.lower() not in haystack:
                    continue
            filtered.append(record)
        page = filtered[payload.offset : payload.offset + payload.limit]
        return _Result(
            payload={
                "tasks": [self._task_payload(record) for record in page],
                "total": len(filtered),
                "offset": payload.offset,
                "limit": payload.limit,
                "models": sorted(models),
                "sources": sorted(sources),
            }
        )

    async def task_detail(
        self,
        payload: _TaskRequest,
        _context: DashboardActionContext,
    ) -> _Result:
        """Return one task detail."""
        record = self.plugin.task_manager.get_generation_task(payload.task_id.strip())
        if not record:
            raise DashboardActionError(
                "task_not_found", "Image-generation task was not found"
            )
        return _Result(payload={"task": self._task_payload(record, detail=True)})

    async def cancel_task(
        self,
        payload: _TaskRequest,
        _context: DashboardActionContext,
    ) -> _Result:
        """Cancel one active task."""
        task_id = payload.task_id.strip()
        ok, message = self.plugin.task_manager.cancel_generation_task(task_id)
        if not ok:
            raise DashboardActionError(
                "task_not_cancelled", "Image-generation task cannot be cancelled"
            )
        return _Result(payload={"ok": True, "message": message, "task_id": task_id})

    async def upload_reference(
        self,
        upload: DashboardUploadedFile,
        _fields: _UploadFields,
        _context: DashboardActionContext,
    ) -> _Result:
        """Validate and save one reference image in plugin-owned data storage."""
        max_bytes = (
            self.plugin.config_manager.usage_settings.max_image_size_mb * 1024 * 1024
        )
        chunks: list[bytes] = []
        size = 0
        async for chunk in upload.iter_chunks():
            size += len(chunk)
            if size > max_bytes:
                raise DashboardActionError(
                    "file_too_large",
                    "Reference image exceeds the configured size limit",
                )
            chunks.append(chunk)
        data = b"".join(chunks)
        image = self.plugin.image_processor.validate_image_data(
            data, log_source=upload.filename
        )
        if not image:
            raise DashboardActionError(
                "invalid_image", "The uploaded file is not a supported image"
            )
        suffix = PAGE_IMAGE_EXTENSIONS[image.mime_type]
        token = f"upload_{hashlib.sha256(data).hexdigest()[:20]}{suffix}"
        target = (self.plugin.page_upload_dir / token).resolve()
        target.relative_to(self.plugin.page_upload_dir.resolve())
        target.write_bytes(data)
        return _Result(
            payload={
                "token": token,
                "filename": Path(upload.filename or token).name,
                "mime_type": image.mime_type,
                "size": size,
            }
        )

    async def submit_generation(
        self,
        payload: _GenerateRequest,
        context: DashboardActionContext,
    ) -> _Result:
        """Submit a Dashboard-owned image task."""
        plugin = self.plugin
        model = payload.model.strip()
        current = (
            f"{plugin.config_manager.adapter_config.name}/{plugin.config_manager.adapter_config.model}"
            if plugin.config_manager.adapter_config
            else ""
        )
        if model and model != current:
            plugin.config_manager.save_model_setting(model)
            plugin.config_manager.reload()
            plugin.reload_runtime_settings()
            if plugin.generator and plugin.config_manager.adapter_config:
                await plugin.generator.update_adapter(
                    plugin.config_manager.adapter_config
                )
                plugin.generation_executor.update_generator(plugin.generator)
        reference_sources = [
            str(path)
            for token in payload.reference_tokens
            if (path := self._uploaded_image_path(token)) is not None
        ]
        result = await plugin.public_api.submit_generation_task(
            prompt=payload.prompt,
            source="Dashboard",
            unified_msg_origin=f"dashboard:{context.username}",
            image_count=plugin.normalize_image_count(payload.image_count),
            aspect_ratio=payload.aspect_ratio,
            resolution=payload.resolution,
            reference_image_sources=reference_sources,
            presets=payload.presets or None,
            personas=payload.personas or None,
            is_admin=True,
        )
        if not result.ok:
            raise DashboardActionError(
                "task_rejected", "Image-generation task was rejected"
            )
        record = plugin.task_manager.get_generation_task(result.task_id or "")
        return _Result(
            payload={
                "ok": True,
                "message": result.message,
                "task_id": result.task_id,
                "task": self._task_payload(record) if record else None,
            }
        )

    async def read_preview(
        self,
        payload: _ImageRequest,
        _context: DashboardActionContext,
    ) -> _Result:
        """Return a bounded inline preview suitable for the JSON bridge."""
        record = self.plugin.task_manager.get_generation_task(payload.task_id.strip())
        path = self._result_image_path(record, payload.image_index) if record else None
        if not path:
            raise DashboardActionError(
                "image_not_found", "Generated image is not available"
            )
        if path.stat().st_size > PAGE_PREVIEW_MAX_BYTES:
            raise DashboardActionError(
                "preview_too_large",
                "Image is too large for an inline preview; download it instead",
            )
        mime_type = self._image_mime_type(path)
        return _Result(
            payload={
                "filename": path.name,
                "mime_type": mime_type,
                "size": path.stat().st_size,
                "data_url": f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}",
            }
        )

    async def download_image(
        self,
        payload: _ImageRequest,
        _context: DashboardActionContext,
    ) -> DashboardFile:
        """Stage one generated image for the protocol's owner-bound file ticket."""
        record = self.plugin.task_manager.get_generation_task(payload.task_id.strip())
        path = self._result_image_path(record, payload.image_index) if record else None
        if not path:
            raise DashboardActionError(
                "image_not_found", "Generated image is not available"
            )
        if path.stat().st_size > PAGE_EXPORT_MAX_BYTES:
            raise DashboardActionError(
                "image_too_large",
                "Generated image exceeds the Dashboard download limit",
            )
        self._export_root.mkdir(exist_ok=True)
        filename = f"{payload.task_id}_{payload.image_index}_{path.name}"
        staged = self._export_root / filename
        shutil.copyfile(path, staged)
        return DashboardFile(
            relative_path=staged.relative_to(self._plugin_root),
            filename=path.name,
            content_type=self._image_mime_type(path),
        )

    def _uploaded_image_path(self, token: str) -> Path | None:
        safe_token = token.strip()
        if not safe_token or any(part in safe_token for part in ("/", "\\", "..")):
            return None
        path = (self.plugin.page_upload_dir / safe_token).resolve()
        try:
            path.relative_to(self.plugin.page_upload_dir.resolve())
        except ValueError:
            return None
        return path if path.is_file() else None
