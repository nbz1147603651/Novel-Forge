"""Provider-neutral contracts for commercial image and video generation.

The contracts deliberately mirror the stable intersection of Alibaba Model
Studio and MiniMax while preserving provider-specific options in ``metadata``.
This lets the film pipeline keep one shot model without hiding advanced modes
such as Wan reference-to-video or MiniMax subject-reference video.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FilmMediaKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class FilmGenerationMode(str, Enum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_EDIT = "image_edit"
    IMAGE_SET = "image_set"
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    FIRST_LAST_FRAME = "first_last_frame"
    VIDEO_CONTINUATION = "video_continuation"
    REFERENCE_TO_VIDEO = "reference_to_video"
    SUBJECT_REFERENCE_VIDEO = "subject_reference_video"


class FilmProviderTaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FilmReferenceMedia(BaseModel):
    """One ordered reference used by a provider generation request."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    url: str = Field(min_length=1)
    label: str = ""


class FilmGenerationRequest(BaseModel):
    """A provider-neutral request for one image set or video clip."""

    model_config = ConfigDict(extra="forbid")

    mode: FilmGenerationMode
    prompt: str = Field(min_length=1, max_length=5000)
    model_id: str = ""
    negative_prompt: str = ""
    aspect_ratio: str = "16:9"
    resolution: str = "1080P"
    duration_s: int = Field(default=6, ge=2, le=30)
    image_count: int = Field(default=1, ge=1, le=12)
    # Provider-neutral seed.  MiniMax H3 v2 does not currently declare it;
    # adapters only send this field when their own capability contract does.
    seed: int | None = Field(default=None, ge=0, le=4_294_967_295)
    prompt_optimizer: bool = True
    watermark: bool = False
    references: list[FilmReferenceMedia] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_reference_contract(self) -> FilmGenerationRequest:
        kinds = [item.kind for item in self.references]
        if self.mode == FilmGenerationMode.FIRST_LAST_FRAME:
            if "first_frame" not in kinds or "last_frame" not in kinds:
                raise ValueError("first_last_frame requires first_frame and last_frame references")
        if self.mode == FilmGenerationMode.VIDEO_CONTINUATION and "first_clip" not in kinds:
            raise ValueError("video_continuation requires a first_clip reference")
        if (
            self.mode
            in {
                FilmGenerationMode.IMAGE_TO_VIDEO,
                FilmGenerationMode.IMAGE_EDIT,
            }
            and not self.references
        ):
            raise ValueError(f"{self.mode.value} requires at least one reference")
        if self.mode == FilmGenerationMode.SUBJECT_REFERENCE_VIDEO and not any(
            kind in {"subject", "reference_image"} for kind in kinds
        ):
            raise ValueError("subject_reference_video requires a subject reference")
        return self


class FilmProviderTask(BaseModel):
    """Normalized submission/query result persisted by the film pipeline."""

    provider_id: str
    model_id: str
    mode: FilmGenerationMode
    state: FilmProviderTaskState
    task_id: str = ""
    file_id: str = ""
    # Provider API generation: "v2" selects the H3 Open Platform v2
    # endpoints (``/v2/video_generation`` + content[] payloads); anything
    # else falls back to the legacy polling contract.
    api_version: str = ""
    asset_urls: list[str] = Field(default_factory=list)
    # Alternate download URLs (e.g. MiniMax ``backup_download_url``): the
    # pipeline tries them in order when the primary URL fails to materialize.
    backup_urls: list[str] = Field(default_factory=list)
    error_message: str = ""
    trace_id: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class FilmProviderError(RuntimeError):
    """Provider error safe to surface through the Engine API."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        self.retryable = retryable
        super().__init__(message)


class FilmGenerationProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    async def submit(self, request: FilmGenerationRequest) -> FilmProviderTask: ...

    async def query(self, task: FilmProviderTask) -> FilmProviderTask: ...
