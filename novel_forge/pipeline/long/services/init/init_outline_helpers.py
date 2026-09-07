"""Outline generation helpers for init service."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from novel_forge.core.constants import PipelineConstants, TaskType
from novel_forge.core.schemas.outline import ChapterOutline, NarrativeBlueprint
from novel_forge.memory.episodic import EpisodicMemory, RelationshipChange
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.artifact_manifest import (
    STATUS_NEEDS_REPAIR,
    STATUS_SUCCEEDED,
    ArtifactManifest,
)
from novel_forge.story_kernel.outline_tracker import HybridOutlineTracker, OutlineTracker

_log = logging.getLogger(__name__)

_ACCEPTED_OUTLINE_BATCH_STATUS = "accepted"
_OUTLINE_BATCH_CHECKPOINT_SCHEMA_VERSION = "1.1"


def _stable_json_hash(payload: Any) -> str:
    """Return a deterministic hash for checkpoint/audit payloads."""
    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        raw = json.dumps(str(payload), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _outline_batch_manifest_artifact(batch_start: int, batch_end: int) -> str:
    return f"outline_batch:{batch_start}:{batch_end}"


def _outline_batch_manifest_inputs(payload: Mapping[str, Any]) -> dict[str, str]:
    inputs = {
        "total_chapters": str(payload.get("total_chapters") or 0),
        "batch_range": f"{payload.get('batch_start')}:{payload.get('batch_end')}",
        "session": str(payload.get("session_id") or ""),
    }
    source_hashes = payload.get("source_hashes")
    if isinstance(source_hashes, Mapping):
        inputs.update({f"source:{key}": str(value) for key, value in source_hashes.items()})
    return inputs


def _save_outline_tracker_state(
    storage: Any,
    layout: ProjectLayout,
    tracker: HybridOutlineTracker,
) -> None:
    """Persist HybridOutlineTracker state so batched generation can resume cleanly."""
    try:
        storage.save_json(layout.outline_tracker_path, tracker.to_dict())
    except Exception as exc:
        _log.warning("Failed to save outline tracker state: %s", exc)


def _load_outline_tracker_state(
    storage: Any,
    layout: ProjectLayout,
) -> HybridOutlineTracker | None:
    """Load persisted HybridOutlineTracker state; returns None if none exists."""
    if not storage.exists(layout.outline_tracker_path):
        return None
    try:
        data = storage.load_json(layout.outline_tracker_path)
        return HybridOutlineTracker.from_dict(data)
    except Exception as exc:
        _log.warning("Failed to load outline tracker state, starting fresh: %s", exc)
        return None


def _save_episodic_memory_outline_data(
    storage: Any,
    layout: ProjectLayout,
    episodic_memory: EpisodicMemory,
) -> None:
    """Save episodic memory outline data to disk for sharing with chapter generation."""
    try:
        layout.ensure_dirs()
        outline_data = episodic_memory.serialize_outline_data()
        if outline_data:
            memory_path = layout.memory_dir / "outline_episodic.json"
            storage.save_json(memory_path, outline_data)
            _log.info(
                "Saved episodic memory outline data | path=%s | outlines=%d | relationships=%d",
                memory_path,
                len(outline_data.get("outline_index", {})),
                len(outline_data.get("relationships", {})),
            )
    except Exception as exc:
        _log.warning(
            "Failed to save episodic memory outline data: %s",
            exc,
        )


async def _index_chapter_to_episodic_memory(
    episodic_memory: EpisodicMemory,
    chapter: ChapterOutline,
    blueprint: NarrativeBlueprint,
) -> None:
    """Index a chapter's outline to episodic memory for semantic retrieval."""
    plot_points = chapter.main_plot_points or []
    characters = [chapter.pov_character] if chapter.pov_character else []
    themes: list[str] = []

    blueprint_themes = getattr(blueprint, "themes", None) or []
    if blueprint_themes:
        for point in plot_points:
            point_lower = point.lower()
            for theme in blueprint_themes:
                if isinstance(theme, str) and theme.lower() in point_lower:
                    themes.append(theme)

    unresolved_questions: list[str] = []
    for point in plot_points:
        if any(kw in point for kw in ["？", "?", "悬念", "疑问", "为什么", "如何"]):
            unresolved_questions.append(f"第{chapter.chapter_number}章悬念: {point[:50]}...")

    relationship_changes: list[RelationshipChange] = []
    rel_notes = getattr(chapter, "relationship_notes", None) or []
    if rel_notes:
        for rel_note in rel_notes:
            if isinstance(rel_note, dict):
                change = RelationshipChange(
                    character_a=rel_note.get("character_a", "") or "",
                    character_b=rel_note.get("character_b", "") or "",
                    chapter=chapter.chapter_number,
                    change_type=rel_note.get("change_type", "observed") or "observed",
                    description=rel_note.get("description", "") or "",
                )
                relationship_changes.append(change)

    await episodic_memory.index_outline_phase(
        chapter_number=chapter.chapter_number,
        plot_points=plot_points,
        characters=characters,
        pov_character=chapter.pov_character or "",
        chapter_goal=chapter.goal or chapter.title or "",
        themes=themes,
        relationship_changes=relationship_changes if relationship_changes else None,
        unresolved_questions=unresolved_questions if unresolved_questions else None,
    )


async def _build_outline_tracker_context(
    tracker: OutlineTracker | HybridOutlineTracker | None,
    episodic_memory: EpisodicMemory | None,
    current_chapter: int,
    existing_chapters: list[ChapterOutline],
) -> dict[str, Any]:
    """Build memory context from outline tracker and episodic memory for prompt injection."""
    recent_chapters = sorted(existing_chapters, key=lambda c: c.chapter_number)[-5:]
    recent_summary = []
    for ch in recent_chapters:
        summary_parts = [
            f"第{ch.chapter_number}章: {ch.title}" if ch.title else f"第{ch.chapter_number}章"
        ]
        if ch.pov_character:
            summary_parts.append(f"视角: {ch.pov_character}")
        if ch.setting:
            summary_parts.append(f"场景: {ch.setting}")
        if ch.main_plot_points:
            summary_parts.append(f"主线: {ch.main_plot_points[0][:50]}...")
        recent_summary.append(" | ".join(summary_parts))

    recent_summary_text = "\n".join(recent_summary) if recent_summary else "暂无已生成章节。"

    if episodic_memory is not None:
        try:
            outline_context = await episodic_memory.get_outline_context(
                current_chapter=current_chapter,
                recent_chapters=5,
                similar_top_k=3,
            )
            enhanced_summary = outline_context.to_prompt_text()

            return {
                "relationship_summary": outline_context.chapter_summary,
                "themes_summary": enhanced_summary,
                "key_events_summary": enhanced_summary,
                "unresolved_summary": "\n".join(outline_context.unresolved_questions[-5:])
                if outline_context.unresolved_questions
                else "暂无待解决悬念。",
                "recent_chapters_summary": recent_summary_text,
                "unified_memory_context": enhanced_summary,
            }
        except Exception as exc:
            _log.warning(
                "outline_episodic_context_unavailable | current_chapter=%s | "
                "error=%s | fallback=%s",
                current_chapter,
                exc,
                "outline_tracker" if tracker is not None else "recent_summary",
            )

    if tracker is not None:
        context = tracker.get_context_for_prompt(current_chapter)
        tracker_unified = f"{context['relationship_summary']}\n{context['themes_summary']}".strip()

        return {
            "relationship_summary": context["relationship_summary"],
            "themes_summary": context["themes_summary"],
            "key_events_summary": context["key_events_summary"],
            "unresolved_summary": context["unresolved_summary"],
            "recent_chapters_summary": recent_summary_text,
            "unified_memory_context": tracker_unified,
        }

    return {
        "relationship_summary": "暂无关系追踪数据。",
        "themes_summary": "暂无主题追踪数据。",
        "key_events_summary": "暂无事件追踪数据。",
        "unresolved_summary": "暂无待解决悬念。",
        "recent_chapters_summary": recent_summary_text,
        "unified_memory_context": "",
    }


def _initialize_outline_tracker(
    outline_ctx: dict[str, Any],
    blueprint: NarrativeBlueprint,
    existing_chapters: list[ChapterOutline],
    batch_size: int = 5,
) -> HybridOutlineTracker:
    """Initialize outline relationship tracker with initial data."""
    tracker = HybridOutlineTracker(
        batch_size=batch_size,
        llm_extraction_enabled=True,
    )

    character_bible = outline_ctx.get("character_bible")
    if character_bible:
        try:
            from novel_forge.core.schemas.bible import CharacterBible

            if isinstance(character_bible, dict):
                bible = CharacterBible.model_validate(character_bible)
            elif hasattr(character_bible, "characters"):
                bible = character_bible
            else:
                bible = None

            if bible:
                tracker.initialize_from_bible(bible)
        except Exception as exc:
            _log.debug("Failed to initialize relationship tracker from bible: %s", exc)

    tracker.initialize_from_blueprint(blueprint)

    if existing_chapters:
        tracker.initialize_from_existing_chapters(existing_chapters)

    return tracker


def _sanitize_outline_conversation_history(
    raw_messages: Any,
) -> list[dict[str, str]]:
    """Keep only valid user/assistant turns for resumable outline history."""

    def _compact_text(content: Any, *, limit: int) -> str:
        text = " ".join(str(content or "").split()).strip()
        if len(text) <= limit:
            return text
        return f"{text[:limit].rstrip()}…"

    def _summarize_outline_response(content: str) -> str:
        text = str(content or "").strip()
        if not text:
            return "[模型返回为空]"
        try:
            payload = json.loads(text)
        except Exception:
            return _compact_text(text, limit=700)

        chapters = payload.get("chapters")
        if not isinstance(chapters, list) or not chapters:
            return _compact_text(text, limit=700)

        snippets: list[str] = []
        for item in chapters[:4]:
            if not isinstance(item, dict):
                continue
            chapter_no = item.get("chapter_number", "?")
            title = _compact_text(item.get("title", ""), limit=16)
            goal = _compact_text(item.get("goal", ""), limit=28)
            points = item.get("main_plot_points") or item.get("beats_summary") or []
            hook = ""
            if isinstance(points, list) and points:
                hook = _compact_text(points[0], limit=22)
            parts = [f"第{chapter_no}章"]
            if title:
                parts.append(f"《{title}》")
            if goal:
                parts.append(f"目标:{goal}")
            if hook:
                parts.append(f"推进:{hook}")
            snippets.append(" ".join(parts))

        if not snippets:
            return _compact_text(text, limit=700)

        suffix = f"（共{len(chapters)}章）" if len(chapters) > 4 else ""
        return f"[章节输出摘要]{suffix} " + "；".join(snippets)

    def _rebalance_keep_latest_full_assistant(
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Keep latest assistant reply in full, summarize older assistant turns."""
        latest_assistant_idx = None
        for idx in range(len(messages) - 1, -1, -1):
            if messages[idx].get("role") == "assistant":
                latest_assistant_idx = idx
                break

        balanced: list[dict[str, str]] = []
        for idx, message in enumerate(messages):
            role = message.get("role")
            content = str(message.get("content", "")).strip()
            if not content:
                continue
            if role == "user":
                balanced.append({"role": "user", "content": _compact_text(content, limit=320)})
                continue
            if role == "assistant" and idx == latest_assistant_idx:
                balanced.append({"role": "assistant", "content": content})
                continue
            if role == "assistant":
                balanced.append(
                    {"role": "assistant", "content": _summarize_outline_response(content)}
                )
        return balanced

    if not isinstance(raw_messages, list):
        return []
    raw_cleaned: list[dict[str, str]] = []
    for item in raw_messages:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        cleaned = content.strip()
        if not cleaned:
            continue
        raw_cleaned.append({"role": role, "content": cleaned})
    return _rebalance_keep_latest_full_assistant(raw_cleaned)


def _load_outline_conversation_history(
    storage: Any,
    layout: ProjectLayout,
    *,
    total_chapters: int,
    history_window_rounds: int,
) -> list[dict[str, str]]:
    """Load persisted outline multi-turn history for breakpoint continuation."""
    if not storage.exists(layout.outline_session_path):
        return []
    try:
        payload = storage.load_json(layout.outline_session_path)
    except Exception:
        return []
    if payload.get("total_chapters") != total_chapters:
        return []
    try:
        chapters_done = int(payload.get("chapters_done") or 0)
        latest_chapter_number = int(payload.get("latest_chapter_number") or 0)
    except (TypeError, ValueError):
        return []
    if chapters_done <= 0 or latest_chapter_number <= 0:
        return []
    history = _sanitize_outline_conversation_history(payload.get("conversation_history"))
    max_msgs = max(2, history_window_rounds * 2)
    return history[-max_msgs:]


def _load_partial_outline_chapters_from_session(
    storage: Any,
    layout: ProjectLayout,
    *,
    total_chapters: int,
) -> list[ChapterOutline]:
    """Recover validated chapter outlines from accepted outline checkpoints.

    Conversation history is intentionally not parsed as source data here. It is
    useful as LLM continuation context, but it is not a transactional commit log:
    a user cancellation can leave a full-looking assistant response in history
    before the batch passes validation.
    """
    if not storage.exists(layout.outline_session_path):
        return []
    try:
        session_payload = storage.load_json(layout.outline_session_path)
    except Exception:
        return []
    if not isinstance(session_payload, dict) or session_payload.get("total_chapters") != total_chapters:
        return []

    records = _load_outline_session_accepted_batches(
        storage,
        layout,
        total_chapters=total_chapters,
        session_payload=session_payload,
    )
    if not records:
        return []
    last_safe_chapter = _coerce_outline_int(session_payload.get("last_safe_chapter"), default=0)
    checkpoint_chapters = _load_outline_batch_checkpoint_chapters(
        storage,
        layout,
        total_chapters=total_chapters,
        accepted_batches=records,
        session_id=str(session_payload.get("session_id") or ""),
    )
    return _continuous_outline_chapter_prefix(
        checkpoint_chapters,
        total_chapters=total_chapters,
        last_safe_chapter=last_safe_chapter,
    )


def _normalize_outline_accepted_batch_records(
    accepted_batches: Sequence[Mapping[str, Any]],
    *,
    total_chapters: int,
) -> list[dict[str, Any]]:
    """Return valid session-ledger batch records in stable order."""
    records: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for item in accepted_batches:
        if not isinstance(item, Mapping):
            continue
        try:
            batch_start = int(item.get("batch_start"))
            batch_end = int(item.get("batch_end"))
        except (TypeError, ValueError):
            continue
        if not 1 <= batch_start <= batch_end <= total_chapters:
            continue
        raw_chapters = item.get("accepted_chapters")
        if isinstance(raw_chapters, list):
            accepted_chapters = sorted(
                {
                    int(chapter)
                    for chapter in raw_chapters
                    if isinstance(chapter, int | str) and str(chapter).isdigit()
                }
            )
        else:
            accepted_chapters = list(range(batch_start, batch_end + 1))
        accepted_chapters = [
            chapter
            for chapter in accepted_chapters
            if batch_start <= chapter <= batch_end <= total_chapters
        ]
        if not accepted_chapters:
            continue
        key = (batch_start, batch_end)
        if key in seen:
            continue
        seen.add(key)
        records.append(
            {
                "batch_start": batch_start,
                "batch_end": batch_end,
                "accepted_chapters": accepted_chapters,
                "checkpoint_file": str(
                    item.get("checkpoint_file")
                    or f"batch_{batch_start:03d}_{batch_end:03d}.json"
                ),
                "content_hash": str(item.get("content_hash") or ""),
                "source_hash": str(item.get("source_hash") or ""),
                "session_id": str(item.get("session_id") or ""),
                "entity_audit": item.get("entity_audit") if isinstance(item, dict) else None,
            }
        )
    return sorted(records, key=lambda record: (record["batch_start"], record["batch_end"]))


def _coerce_outline_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _continuous_outline_batch_prefix(
    records: Sequence[Mapping[str, Any]],
    *,
    total_chapters: int,
    last_safe_chapter: int = 0,
) -> list[dict[str, Any]]:
    """Return the continuous accepted-batch prefix starting at chapter 1."""
    expected_start = 1
    prefix: list[dict[str, Any]] = []
    target_last = last_safe_chapter if 1 <= last_safe_chapter <= total_chapters else total_chapters
    for record in _normalize_outline_accepted_batch_records(
        records,
        total_chapters=total_chapters,
    ):
        batch_start = int(record["batch_start"])
        batch_end = int(record["batch_end"])
        if batch_start != expected_start:
            break
        accepted_chapters = list(record.get("accepted_chapters") or [])
        expected_chapters = list(range(batch_start, batch_end + 1))
        if accepted_chapters != expected_chapters:
            break
        prefix.append(record)
        expected_start = batch_end + 1
        if batch_end >= target_last:
            break
    return prefix


def _continuous_outline_chapter_prefix(
    chapters: Sequence[ChapterOutline],
    *,
    total_chapters: int,
    last_safe_chapter: int = 0,
) -> list[ChapterOutline]:
    """Return the continuous validated chapter prefix starting at chapter 1."""
    by_number = {
        chapter.chapter_number: chapter
        for chapter in chapters
        if 1 <= chapter.chapter_number <= total_chapters
        and chapter.notes != PipelineConstants.PLACEHOLDER_NOTE
        and chapter.beats_summary
    }
    target_last = last_safe_chapter if 1 <= last_safe_chapter <= total_chapters else total_chapters
    prefix: list[ChapterOutline] = []
    for chapter_number in range(1, target_last + 1):
        chapter = by_number.get(chapter_number)
        if chapter is None:
            break
        prefix.append(chapter)
    return prefix


def _load_outline_session_accepted_batches(
    storage: Any,
    layout: ProjectLayout,
    *,
    total_chapters: int,
    session_payload: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Load accepted batch records from the current outline session ledger."""
    if session_payload is None:
        if not storage.exists(layout.outline_session_path):
            return []
        try:
            session_payload = storage.load_json(layout.outline_session_path)
        except Exception:
            return []
    if not isinstance(session_payload, dict) or session_payload.get("total_chapters") != total_chapters:
        return []
    accepted_batches = session_payload.get("accepted_batches")
    if not isinstance(accepted_batches, list):
        return []
    last_safe_chapter = _coerce_outline_int(session_payload.get("last_safe_chapter"), default=0)
    return _continuous_outline_batch_prefix(
        accepted_batches,
        total_chapters=total_chapters,
        last_safe_chapter=last_safe_chapter,
    )


def _accepted_batch_checkpoint_names(
    accepted_batches: Sequence[Mapping[str, Any]],
) -> set[str]:
    """Return checkpoint filenames referenced by accepted session batch records."""
    names: set[str] = set()
    for item in accepted_batches:
        if not isinstance(item, Mapping):
            continue
        try:
            batch_start = int(item.get("batch_start"))
            batch_end = int(item.get("batch_end"))
        except (TypeError, ValueError):
            continue
        names.add(f"batch_{batch_start:03d}_{batch_end:03d}.json")
    return names


def _load_outline_batch_checkpoint_chapters(
    storage: Any,
    layout: ProjectLayout,
    *,
    total_chapters: int,
    accepted_batches: Sequence[Mapping[str, Any]] | None = None,
    session_id: str = "",
) -> list[ChapterOutline]:
    """Load validated chapter outlines from accepted per-batch checkpoints."""
    checkpoint_dir = _outline_batch_checkpoint_dir(layout)
    if not checkpoint_dir.exists():
        return []

    allowed_checkpoint_names: set[str] | None = None
    accepted_record_by_name: dict[str, Mapping[str, Any]] = {}
    allowed_chapters_by_checkpoint: dict[str, set[int]] = {}
    if accepted_batches is not None:
        normalized = _normalize_outline_accepted_batch_records(
            accepted_batches,
            total_chapters=total_chapters,
        )
        allowed_checkpoint_names = _accepted_batch_checkpoint_names(normalized)
        for item in normalized:
            filename = f"batch_{item['batch_start']:03d}_{item['batch_end']:03d}.json"
            accepted_record_by_name[filename] = item
            allowed_chapters_by_checkpoint[filename] = set(item["accepted_chapters"])
        if not allowed_checkpoint_names:
            return []

    chapters_by_number: dict[int, ChapterOutline] = {}
    for path in sorted(checkpoint_dir.glob("batch_*.json")):
        if allowed_checkpoint_names is not None and path.name not in allowed_checkpoint_names:
            continue
        try:
            payload = storage.load_json(path)
        except Exception:
            continue
        if payload.get("total_chapters") != total_chapters:
            continue
        if str(payload.get("status") or "").strip() != _ACCEPTED_OUTLINE_BATCH_STATUS:
            continue
        raw_chapters = payload.get("chapters")
        if not isinstance(raw_chapters, list):
            continue
        actual_content_hash = _stable_json_hash(raw_chapters)
        payload_content_hash = str(payload.get("content_hash") or "")
        if not payload_content_hash or payload_content_hash != actual_content_hash:
            continue
        if accepted_batches is not None:
            record = accepted_record_by_name.get(path.name, {})
            record_content_hash = str(record.get("content_hash") or "")
            payload_session_id = str(payload.get("session_id") or "")
            record_session_id = str(record.get("session_id") or "")
            if record_content_hash and record_content_hash != payload_content_hash:
                continue
            expected_session_id = session_id or record_session_id
            if expected_session_id and payload_session_id != expected_session_id:
                continue
        artifact = _outline_batch_manifest_artifact(
            int(payload.get("batch_start") or 0),
            int(payload.get("batch_end") or 0),
        )
        inputs = _outline_batch_manifest_inputs(payload)
        manifest = ArtifactManifest(storage, layout)
        record = manifest.matching_record(
            artifact,
            input_hashes=inputs,
            output_hashes={"chapters": payload_content_hash},
            statuses={STATUS_SUCCEEDED},
            allow_reusable_failure=False,
        )
        if record is None and manifest.get(artifact) is not None:
            continue
        if record is None:
            manifest.record_success(
                artifact=artifact,
                workflow="init_long",
                step="plan_outline_batch",
                input_hashes=inputs,
                output_hashes={"chapters": payload_content_hash},
                paths={"checkpoint": str(path)},
                metadata={"migrated_legacy_checkpoint": True},
                input_signature=_stable_json_hash(inputs),
                schema_version=2,
                workflow_version="init.outline.batch.v2",
            )
        allowed_chapter_numbers = allowed_chapters_by_checkpoint.get(path.name)
        for raw_chapter in raw_chapters:
            if not isinstance(raw_chapter, dict):
                continue
            try:
                chapter = ChapterOutline.model_validate(raw_chapter)
            except Exception as exc:
                _log.debug(
                    "outline_batch_checkpoint_chapter_skipped | path=%s | error=%s",
                    path,
                    exc,
                )
                continue
            if not 1 <= chapter.chapter_number <= total_chapters:
                continue
            if (
                allowed_chapter_numbers is not None
                and chapter.chapter_number not in allowed_chapter_numbers
            ):
                continue
            if chapter.notes == PipelineConstants.PLACEHOLDER_NOTE or not chapter.beats_summary:
                continue
            chapters_by_number[chapter.chapter_number] = chapter

    return [chapters_by_number[number] for number in sorted(chapters_by_number)]


def _load_all_outline_batch_checkpoint_chapters(
    storage: Any,
    layout: ProjectLayout,
    *,
    total_chapters: int,
) -> list[ChapterOutline]:
    """Compatibility wrapper for legacy audits that intentionally scan all accepted batches."""
    return _load_outline_batch_checkpoint_chapters(
        storage,
        layout,
        total_chapters=total_chapters,
    )


def _outline_batch_checkpoint_dir(layout: ProjectLayout) -> Any:
    return layout.states_dir / "outline_batches"


def _outline_batch_checkpoint_path(layout: ProjectLayout, batch_start: int, batch_end: int) -> Any:
    return _outline_batch_checkpoint_dir(layout) / f"batch_{batch_start:03d}_{batch_end:03d}.json"


def _save_outline_batch_checkpoint(
    storage: Any,
    layout: ProjectLayout,
    *,
    total_chapters: int,
    batch_start: int,
    batch_end: int,
    chapters: list[ChapterOutline],
    missing_chapters: list[int],
    status: str,
    raw_response: str | None = None,
    source_hashes: dict[str, str] | None = None,
    entity_audit: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Persist one outline batch checkpoint independently of session history."""
    chapter_payload = [chapter.model_dump(mode="json") for chapter in chapters]
    payload: dict[str, Any] = {
        "schema_version": _OUTLINE_BATCH_CHECKPOINT_SCHEMA_VERSION,
        "status": status,
        "total_chapters": total_chapters,
        "batch_start": batch_start,
        "batch_end": batch_end,
        "accepted_chapters": [chapter.chapter_number for chapter in chapters],
        "missing_chapters": list(missing_chapters),
        "chapters": chapter_payload,
        "content_hash": _stable_json_hash(chapter_payload),
    }
    if session_id:
        payload["session_id"] = session_id
    if raw_response:
        payload["raw_response"] = raw_response
        payload["source_hash"] = hashlib.sha256(raw_response.encode("utf-8")).hexdigest()
    if source_hashes:
        payload["source_hashes"] = {str(key): str(value) for key, value in source_hashes.items()}
    if entity_audit:
        payload["entity_audit"] = entity_audit
    if metadata:
        payload["metadata"] = metadata
    checkpoint_path = _outline_batch_checkpoint_path(layout, batch_start, batch_end)
    storage.save_json(checkpoint_path, payload)
    inputs = _outline_batch_manifest_inputs(payload)
    manifest = ArtifactManifest(storage, layout)
    record_kwargs = {
        "artifact": _outline_batch_manifest_artifact(batch_start, batch_end),
        "workflow": "init_long",
        "step": "plan_outline_batch",
        "input_hashes": inputs,
        "output_hashes": {"chapters": payload["content_hash"]},
        "paths": {"checkpoint": str(checkpoint_path)},
        "metadata": {"status": status, "accepted_chapters": payload["accepted_chapters"]},
        "input_signature": _stable_json_hash(inputs),
        "schema_version": 2,
        "workflow_version": "init.outline.batch.v2",
    }
    if status == _ACCEPTED_OUTLINE_BATCH_STATUS:
        manifest.record_success(**record_kwargs)
    else:
        manifest.record_failure(
            **record_kwargs,
            status=STATUS_NEEDS_REPAIR,
            reusable_failure=False,
        )
    return payload


def _save_outline_resume_state(
    storage: Any,
    layout: ProjectLayout,
    *,
    total_chapters: int,
    chapter_map: dict[int, ChapterOutline],
    conversation_history: list[dict[str, str]],
    history_window_rounds: int,
    session_metadata: dict[str, Any] | None = None,
) -> None:
    """Persist resumable outline state so the next run can continue cleanly."""
    max_msgs = max(2, history_window_rounds * 2)
    trimmed_history = conversation_history[-max_msgs:] if chapter_map else []
    payload = {
        "schema_version": "1.1",
        "status": "running",
        "total_chapters": total_chapters,
        "chapters_done": len(chapter_map),
        "latest_chapter_number": max(chapter_map) if chapter_map else 0,
        "last_safe_chapter": max(chapter_map) if chapter_map else 0,
        "conversation_history": trimmed_history,
    }
    if session_metadata:
        payload.update(session_metadata)
    storage.save_json(
        layout.outline_session_path,
        payload,
    )


def _persist_partial_outline_state(
    storage: Any,
    layout: ProjectLayout,
    *,
    total_chapters: int,
    synopsis: str,
    volume_mode_flag: bool,
    volumes: list[Any],
    chapter_map: dict[int, ChapterOutline],
    conversation_history: list[dict[str, str]],
    history_window_rounds: int,
    session_metadata: dict[str, Any] | None = None,
) -> None:
    """Save resumable outline session metadata without touching canonical outline."""
    _ = synopsis, volume_mode_flag, volumes
    _save_outline_resume_state(
        storage,
        layout,
        total_chapters=total_chapters,
        chapter_map=chapter_map,
        conversation_history=conversation_history,
        history_window_rounds=history_window_rounds,
        session_metadata=session_metadata,
    )


def _record_outline_exchange(
    _builder: Any,
    conversation_history: list[dict[str, str]],
    task_type: TaskType,
    ctx: dict[str, Any],
    raw_response: str,
    is_first_batch: bool = False,
) -> None:
    """Record a compact outline exchange to conversation history."""

    def _compact_text(content: Any, *, limit: int) -> str:
        text = " ".join(str(content or "").split()).strip()
        if len(text) <= limit:
            return text
        return f"{text[:limit].rstrip()}…"

    def _batch_range_label() -> str:
        batch_start = ctx.get("batch_start", "?")
        batch_end = ctx.get("batch_end", "?")
        return f"第{batch_start}-{batch_end}章"

    def _build_user_summary() -> str:
        phase_guidance = _compact_text(ctx.get("phase_guidance", ""), limit=180)
        is_final_batch = bool(ctx.get("is_final_batch"))
        if task_type == TaskType.PLAN_OUTLINE_BATCH:
            if phase_guidance:
                return f"[首轮批生成任务] {_batch_range_label()}；阶段导航：{phase_guidance}"
            return (
                f"[首轮批生成任务] {_batch_range_label()}；按既定蓝图继续输出同格式 chapters JSON。"
            )
        if phase_guidance:
            return f"[续写任务] {_batch_range_label()}；阶段导航：{phase_guidance}"
        if is_final_batch:
            return f"[续写任务] {_batch_range_label()}；终批，必须完成收束并保持章节号连续。"
        return f"[续写任务] {_batch_range_label()}；保持章节号连续、同格式输出。"

    def _rebalance_keep_latest_full_assistant() -> None:
        """Keep latest assistant reply full; summarize older assistant turns."""
        latest_assistant_idx = None
        for idx in range(len(conversation_history) - 1, -1, -1):
            if conversation_history[idx].get("role") == "assistant":
                latest_assistant_idx = idx
                break
        if latest_assistant_idx is None:
            return
        for idx, message in enumerate(conversation_history):
            if message.get("role") != "assistant" or idx == latest_assistant_idx:
                continue
            message["content"] = _build_assistant_summary_from_text(message.get("content", ""))

    def _build_assistant_summary_from_text(content: Any) -> str:
        text = str(content or "").strip()
        if not text:
            return "[模型返回为空]"
        try:
            payload = json.loads(text)
        except Exception:
            return _compact_text(text, limit=700)
        chapters = payload.get("chapters")
        if not isinstance(chapters, list) or not chapters:
            return _compact_text(text, limit=700)

        snippets: list[str] = []
        for item in chapters[:4]:
            if not isinstance(item, dict):
                continue
            chapter_no = item.get("chapter_number", "?")
            title = _compact_text(item.get("title", ""), limit=16)
            goal = _compact_text(item.get("goal", ""), limit=28)
            points = item.get("main_plot_points") or item.get("beats_summary") or []
            hook = ""
            if isinstance(points, list) and points:
                hook = _compact_text(points[0], limit=22)
            parts = [f"第{chapter_no}章"]
            if title:
                parts.append(f"《{title}》")
            if goal:
                parts.append(f"目标:{goal}")
            if hook:
                parts.append(f"推进:{hook}")
            snippets.append(" ".join(parts))

        if not snippets:
            return _compact_text(text, limit=700)
        suffix = f"（共{len(chapters)}章）" if len(chapters) > 4 else ""
        return f"[章节输出摘要]{suffix} " + "；".join(snippets)

    conversation_history.extend(
        [
            {"role": "user", "content": _build_user_summary()},
            {"role": "assistant", "content": str(raw_response or "").strip() or "[模型返回为空]"},
        ]
    )
    _rebalance_keep_latest_full_assistant()
