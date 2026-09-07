"""Persistence-safe contracts for generated sound assets."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import Field

from novel_forge.core.schemas.base import VersionedSchema


class SoundGenerationKind(str, Enum):
    """Audio roles with distinct prompting and model selection rules."""

    BGM = "bgm"
    SOUNDSCAPE = "soundscape"
    SFX = "sfx"


class SoundGenerationRequest(VersionedSchema):
    """A deterministic, provider-neutral request to create one sound asset."""

    request_id: str = Field(min_length=1)
    chapter_number: int = Field(ge=1)
    kind: SoundGenerationKind
    cue_index: int = Field(ge=0)
    cue_label: str = Field(min_length=1)
    prompt: str = Field(min_length=3)
    negative_prompt: str = Field(default="")
    duration_ms: int = Field(default=10_000, ge=500, le=600_000)
    loop: bool = Field(default=False)
    output_format: Literal["wav", "mp3", "flac"] = Field(default="wav")
    provider: str = Field(default="auto")
    model_id: str = Field(default="")
    seed: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Stable cache key excluding the run-local request ID."""
        payload = self.model_dump(
            mode="json",
            exclude={
                "schema_version",
                "created_at",
                "request_id",
                "chapter_number",
                "cue_index",
            },
        )
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class GeneratedSoundAsset(VersionedSchema):
    """Provider response before it is persisted in the project asset library."""

    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    audio_format: Literal["wav", "mp3", "flac"] = Field(default="wav")
    duration_ms: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    audio_data: bytes = Field(default=b"", exclude=True, repr=False)
    source_url: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Provenance fields for audit/compliance. Populated by providers from the
    # model descriptor; persisted into SoundAsset so an author can audit
    # license/watermark posture years after generation.
    repository_id: str = Field(default="", description="模型权重仓库标识（HF/org 等）。")
    aigc_watermark: bool = Field(
        default=False,
        description="该资产是否携带 AIGC 水印（合规传播追溯用）。",
    )


class SoundGenerationAttempt(VersionedSchema):
    """One auditable generation outcome for a chapter cue."""

    request: SoundGenerationRequest
    status: Literal["generated", "cached", "pending_review", "failed", "skipped"]
    asset_id: str = Field(default="")
    provider: str = Field(default="")
    model_id: str = Field(default="")
    route_plugin_id: str = Field(default="")
    route_plugin_version: str = Field(default="")
    route_endpoint: str = Field(default="")
    request_hash: str = Field(default="")
    output_hash: str = Field(default="")
    latency_ms: float = Field(default=0.0, ge=0.0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    error_message: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SoundGenerationSummary(VersionedSchema):
    """Chapter-level artifact emitted by the sound-generation orchestration."""

    chapter_number: int = Field(ge=1)
    enabled: bool = Field(default=False)
    auto_generate: bool = Field(default=False)
    auto_approve: bool = Field(default=False)
    attempts: list[SoundGenerationAttempt] = Field(default_factory=list)

    @property
    def generated_count(self) -> int:
        return sum(item.status == "generated" for item in self.attempts)

    @property
    def cached_count(self) -> int:
        return sum(item.status == "cached" for item in self.attempts)

    @property
    def pending_review_count(self) -> int:
        return sum(item.status == "pending_review" for item in self.attempts)

    @property
    def failed_count(self) -> int:
        return sum(item.status == "failed" for item in self.attempts)
