"""Application-service boundary shared by desktop and API clients."""

from __future__ import annotations

from novel_forge.app_service.contracts import (
    DecisionRequest,
    JobCommand,
    JobEvent,
    JobEventType,
    JobKind,
    JobRecord,
    JobScope,
    JobState,
)
from novel_forge.app_service.engine_novel import (
    EngineNovelStudioView,
    project_novel_studio_view,
)
from novel_forge.app_service.engine_queries import EngineQueryService
from novel_forge.app_service.engine_views import (
    ENGINE_API_VERSION,
    ENGINE_CONTRACT_VERSION,
    EngineCapabilitiesView,
    EngineJobsView,
    EngineJobView,
    EngineModuleCapabilityView,
    EngineTaskModelCallView,
    EngineTaskStreamEventView,
    EngineTaskStreamRuntimeSummaryView,
    EngineTaskStreamView,
    OllamaCapabilitiesView,
    OllamaConfiguredRolesView,
    OllamaManagerView,
    OllamaModelView,
    OllamaOperationView,
    OllamaRoutingImpactView,
    OllamaRuntimeStatusView,
    OllamaSidecarView,
    OllamaStorageView,
    engine_capabilities,
    project_job_view,
    project_jobs_view,
    project_task_stream_view,
)
from novel_forge.app_service.engine_voice import (
    EngineVoiceStudioView,
    project_voice_studio_view,
)
from novel_forge.app_service.job_service import JobService

__all__ = (
    "DecisionRequest",
    "ENGINE_API_VERSION",
    "ENGINE_CONTRACT_VERSION",
    "EngineCapabilitiesView",
    "EngineJobsView",
    "EngineJobView",
    "EngineModuleCapabilityView",
    "EngineNovelStudioView",
    "EngineQueryService",
    "EngineTaskModelCallView",
    "EngineTaskStreamEventView",
    "EngineTaskStreamRuntimeSummaryView",
    "EngineTaskStreamView",
    "EngineVoiceStudioView",
    "OllamaCapabilitiesView",
    "OllamaConfiguredRolesView",
    "OllamaManagerView",
    "OllamaModelView",
    "OllamaOperationView",
    "OllamaRoutingImpactView",
    "OllamaRuntimeStatusView",
    "OllamaSidecarView",
    "OllamaStorageView",
    "JobCommand",
    "JobEvent",
    "JobEventType",
    "JobKind",
    "JobRecord",
    "JobScope",
    "JobService",
    "JobState",
    "engine_capabilities",
    "project_job_view",
    "project_jobs_view",
    "project_novel_studio_view",
    "project_task_stream_view",
    "project_voice_studio_view",
)
