"""Stable contracts for the application-level audio model center."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from novel_forge.core.schemas.base import VersionedSchema


class AudioModelSource(str, Enum):
    HUGGINGFACE = "huggingface"
    RELEASE_ARCHIVE = "release_archive"
    DIRECT_FILE = "direct_file"
    SIDECAR = "sidecar"
    STABLE_AUDIO = "stable_audio"


class AudioModelInstallState(str, Enum):
    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"
    INCOMPLETE = "incomplete"
    EXTERNAL = "external"


class AudioModelDescriptor(VersionedSchema):
    plugin_id: str
    model_id: str
    display_name: str
    family: str
    roles: list[str] = Field(default_factory=list)
    source: AudioModelSource
    repository_id: str = ""
    download_url: str = ""
    revision: str = "main"
    required_files: list[str] = Field(default_factory=list)
    estimated_download_bytes: int = Field(default=0, ge=0)
    license_name: str = ""
    license_url: str = ""
    requires_license_acceptance: bool = False
    endpoint_setting: str = ""
    runtime_package: str = ""
    recommended_for: str = ""
    sha256: str = Field(default="", pattern=r"^[0-9a-fA-F]{64}$|^$")
    signature: str = ""
    signature_key_id: str = ""
    runtime_id: str = ""
    minimum_runtime_version: str = ""
    maximum_runtime_version: str = ""


class AudioModelInventoryRecord(VersionedSchema):
    plugin_id: str
    model_id: str
    revision: str = "main"
    local_path: str = ""
    installed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    license_accepted_at: datetime | None = None


class AudioModelInventory(VersionedSchema):
    records: list[AudioModelInventoryRecord] = Field(default_factory=list)


class AudioModelStatus(BaseModel):
    descriptor: AudioModelDescriptor
    state: AudioModelInstallState
    local_path: str = ""
    installed_size_bytes: int = Field(default=0, ge=0)
    runtime_healthy: bool | None = None
    runtime_version: str = ""
    installed_revision: str = ""
    self_test_passed: bool | None = None
    project_references: list[str] = Field(default_factory=list)
    license_accepted: bool = False
    detail: str = ""
    compatible: bool = True
    compatibility_reason: str = ""


class RuntimeInstallState(str, Enum):
    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    INCOMPATIBLE = "incompatible"


class AudioRuntimeDescriptor(VersionedSchema):
    runtime_id: str
    display_name: str
    version: str
    python_constraint: str = ">=3.11,<3.13"
    packages: list[str] = Field(default_factory=list)
    resolution_exclude_newer: str = ""
    self_test_imports: list[str] = Field(default_factory=list)
    entry_module: str = ""
    endpoint: str = ""
    port: int = Field(default=0, ge=0, le=65535)
    health_path: str = "/health"
    managed_process: bool = True
    supported_plugins: list[str] = Field(default_factory=list)


class AudioRuntimeState(VersionedSchema):
    runtime_id: str
    version: str = ""
    state: RuntimeInstallState = RuntimeInstallState.NOT_INSTALLED
    environment_path: str = ""
    pid: int | None = None
    port: int = 0
    should_run: bool = False
    restart_count: int = 0
    last_error: str = ""
    creator_python: str = ""
    creator_python_version: str = ""
    creator_python_source: str = ""
    rollback_available: bool = False
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DownloadTaskState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class ModelDownloadTask(VersionedSchema):
    task_id: str
    plugin_id: str
    url: str
    target_path: str
    expected_sha256: str = ""
    signature: str = ""
    signature_key_id: str = ""
    state: DownloadTaskState = DownloadTaskState.QUEUED
    bytes_downloaded: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    error: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ModelRepositoryMigration(VersionedSchema):
    migration_id: str
    source_root: str
    target_root: str
    backup_root: str = ""
    state: str = "planned"
    manifest_sha256: str = ""
    source_size_bytes: int = Field(default=0, ge=0)
    required_free_bytes: int = Field(default=0, ge=0)
    available_free_bytes: int = Field(default=0, ge=0)
    resume_runtime_ids: list[str] = Field(default_factory=list)
    error: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
