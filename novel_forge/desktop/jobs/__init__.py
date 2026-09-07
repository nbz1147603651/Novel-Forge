"""Background job execution for the PySide6 desktop client.

Thin re-export layer — core logic lives in sub-modules:
- ``manager.py``  — DesktopJobManager + _WorkspaceJobWorker
- ``records.py``  — DesktopJobRecord, DesktopJobState, serialization helpers
- ``events.py``   — stream event coalescing, event bus accessor
- ``history.py``  — persisted history loader
- ``serialize.py`` — compact payload builders
- ``worker.py``   — _WorkerSignals
"""

from __future__ import annotations

from novel_forge.desktop.jobs.events import (
    _coalesce_stream_delta_events,
    _event_type_value,
    _get_event_bus,
    _merge_stream_delta_events,
    _stream_event_key,
)
from novel_forge.desktop.jobs.history import _HistoryWorker, _HistoryWorkerSignals
from novel_forge.desktop.jobs.manager import (
    CHAPTER_WRITE_KINDS,
    WORKFLOW_JOB_KINDS,
    DesktopJobManager,
    _WorkspaceJobWorker,
)
from novel_forge.desktop.jobs.records import (
    DesktopJobEvent,
    DesktopJobRecord,
    DesktopJobState,
    WaitingJob,
    _infer_failed_step,
    _job_chapter_num,
    _job_event_to_payload,
    _job_from_payload,
    _job_kind_to_section,
    _job_to_payload,
    _now_iso,
    app_job_record_to_desktop_record,
    desktop_job_record_to_app_record,
)
from novel_forge.desktop.jobs.serialize import (
    _compact_memory_stage_payload,
    _compact_memory_status_payload,
    _compact_payload,
    _compact_stage_memory_context_payload,
    _extract_trace_summary,
    _serialize_chapter_result,
    _serialize_consistency_result,
    _serialize_init_result,
    _serialize_polish_result,
    _serialize_prepare_result,
    _serialize_repair_result,
    _serialize_session_result,
    _serialize_short_result,
)
from novel_forge.desktop.jobs.worker import _WorkerSignals

__all__ = [
    "CHAPTER_WRITE_KINDS",
    "DesktopJobEvent",
    "DesktopJobManager",
    "DesktopJobRecord",
    "DesktopJobState",
    "WaitingJob",
    "WORKFLOW_JOB_KINDS",
    "_HistoryWorker",
    "_HistoryWorkerSignals",
    "_WorkerSignals",
    "_WorkspaceJobWorker",
    "_coalesce_stream_delta_events",
    "_compact_memory_stage_payload",
    "_compact_memory_status_payload",
    "_compact_payload",
    "_compact_stage_memory_context_payload",
    "_event_type_value",
    "_extract_trace_summary",
    "_get_event_bus",
    "_infer_failed_step",
    "_job_chapter_num",
    "_job_event_to_payload",
    "_job_from_payload",
    "_job_kind_to_section",
    "_job_to_payload",
    "_merge_stream_delta_events",
    "_now_iso",
    "_serialize_chapter_result",
    "_serialize_consistency_result",
    "_serialize_init_result",
    "_serialize_polish_result",
    "_serialize_prepare_result",
    "_serialize_repair_result",
    "_serialize_session_result",
    "_serialize_short_result",
    "_stream_event_key",
    "app_job_record_to_desktop_record",
    "desktop_job_record_to_app_record",
]
