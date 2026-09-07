"""Long-chapter adapter for the shared one-shot research evidence router."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from novel_forge.core.schemas.spec import StorySpec
from novel_forge.pipeline.long.services.context.source_artifacts import (
    attach_chapter_research_to_source_slice,
)
from novel_forge.research.chapter_evidence import prepare_chapter_research_evidence

if TYPE_CHECKING:
    from novel_forge.pipeline.long.execution_models import ChapterExecutionContext
    from novel_forge.pipeline.long.preflight import LongProjectBundle


def _init_generation_options(ctx: ChapterExecutionContext, bundle: LongProjectBundle) -> dict[str, Any]:
    if not ctx._storage.exists(bundle.layout.init_request_meta_path):
        return {}
    try:
        metadata = ctx._storage.load_json(bundle.layout.init_request_meta_path)
    except Exception:
        return {}
    raw_request = metadata.get("request") if isinstance(metadata, dict) else None
    request = raw_request if isinstance(raw_request, dict) else {}
    options = request.get("generation_options")
    return options if isinstance(options, dict) else {}


async def attach_long_chapter_research(
    *,
    ctx: ChapterExecutionContext,
    bundle: LongProjectBundle,
    chapter_number: int,
) -> None:
    """Attach run-local evidence to the source slice without persisting that projection."""

    source_slice = bundle.chapter_source_slice
    if source_slice is None:
        return
    runtime = source_slice.payload.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    chapter_contract = runtime.get("chapter_contract")
    chapter_contract = chapter_contract if isinstance(chapter_contract, dict) else {}
    user_intent = runtime.get("user_intent")
    user_intent = user_intent if isinstance(user_intent, dict) else {}
    options = _init_generation_options(ctx, bundle)
    spec = StorySpec.model_validate(ctx._storage.load_json(bundle.layout.spec_path))
    evidence = await prepare_chapter_research_evidence(
        storage=ctx._storage,
        layout=bundle.layout,
        settings=ctx._settings,
        spec=spec,
        chapter_number=chapter_number,
        chapter_outline=bundle.chapter_outline,
        chapter_contract=chapter_contract,
        user_intent=user_intent,
        enabled=bool(options.get("research_enabled", False)),
        provider_name=str(options.get("research_provider") or "auto"),
        query_hint=str(options.get("research_query_hint") or ""),
        mode="long",
        phase_boundary=bool(
            chapter_number == 1
            or (
                bundle.current_volume is not None
                and bundle.current_volume.start_chapter == chapter_number
            )
        ),
        on_step=ctx._on_step,
    )
    if evidence.pack is None:
        return
    bundle.chapter_source_slice = attach_chapter_research_to_source_slice(
        source_slice,
        evidence_pack=evidence.pack.model_dump(mode="json"),
        uncertainty=evidence.uncertainty,
    )


__all__ = ["attach_long_chapter_research"]
