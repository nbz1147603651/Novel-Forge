"""Humanize layer — AI-pattern detection and surgical patch repair for chapter text.

Wraps :class:`HumanizeScanStep` with configuration guards, change-ratio
enforcement, and semantic drift detection to ensure safe, bounded edits.
"""

from __future__ import annotations

import inspect
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from novel_forge.core.schemas.humanize import HumanizeReport
from novel_forge.core.utils.semantic_drift import DriftReport, detect_drift
from novel_forge.core.utils.text_revision_diff import build_text_revision_diff
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.services.constraints.cognitive_constraints import (
    cognitive_constraints_from_source_cards,
)
from novel_forge.pipeline.long.stages.reading_power_repair import _text_change_ratio
from novel_forge.pipeline.steps.humanize_scan_step import (
    _STRUCTURAL_REWRITE_CATEGORIES,
    HumanizeScanInput,
    HumanizeScanResult,
    HumanizeScanStep,
)

if TYPE_CHECKING:
    from novel_forge.memory.humanize_library_store import HumanizeLibrary
    from novel_forge.memory.humanize_retrieval import HumanizeLibraryRetriever

_logger = get_logger("pipeline.humanize_layer")


# ---------------------------------------------------------------------------
# Library retrieval helper (best-effort, graceful degrade)
# ---------------------------------------------------------------------------


def _safe_retrieve_for_chapter(
    library: HumanizeLibrary | None,
    retriever: HumanizeLibraryRetriever | None,
    chapter_text: str,
    chapter_number: int,
    top_k: int = 30,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Safely retrieve library hits. Returns [] on any failure."""
    if library is None or retriever is None:
        return []
    try:
        if not library.is_healthy():
            _safe_log_event_local(
                "humanize_library.degraded",
                {
                    "chapter": chapter_number,
                    "reason": "library_unhealthy",
                    "fallback": "21_hard_rules",
                },
            )
            return []
        # Retriever handles its own event emission and exception catching
        hits = retriever.retrieve(
            library,
            chapter_text,
            top_k=top_k,
            project_id=project_id,
            scan_text=HumanizeScanStep._mask_dialogue(chapter_text),
        )
        return hits
    except Exception as exc:
        _safe_log_event_local(
            "humanize_library.retrieval.failed",
            {
                "chapter": chapter_number,
                "error_type": type(exc).__name__,
                "fallback": "21_hard_rules",
            },
        )
        _logger.warning(
            "humanize_layer | chapter=%d | library retrieval failed: %s",
            chapter_number,
            exc,
        )
        return []


def _safe_log_event_local(event_name: str, data: dict[str, Any] | None = None) -> None:
    """Emit a structured event via project logger. Never raises."""
    try:
        from novel_forge.memory.humanize_library_store import _safe_log_event

        _safe_log_event(event_name, data)
    except Exception:
        pass


@dataclass(frozen=True)
class HumanizeLayerResult:
    """Result of the humanize layer pass."""

    current_text: str
    """Chapter text — either the original or the humanized version."""

    report: HumanizeReport | None
    """Humanize audit report, or ``None`` when the layer was skipped."""

    patches_applied: int
    """Number of patches actually applied to the text."""

    skipped_reason: str
    """Reason the layer was skipped.

    Empty string when the layer ran normally.  Possible values:
    - ``"disabled"``: ``settings.humanize_enabled`` is ``False``.
    - ``"text_too_short"``: text shorter than ``settings.humanize_min_text_length``.
    - ``"change_ratio_exceeded"``: humanized text diverged beyond the cap.
    - ``"semantic_drift"``: drift detection flagged high-severity changes.
    - ``"quality_regression"``: deterministic postconditions found newly introduced prose defects.
    """

    paragraph_rewrites_applied: int = 0
    """Number of paragraphs rewritten via paragraph-level LLM rewrite."""

    comparison: dict[str, Any] | None = None
    """Reusable original-vs-revised comparison artifact for UI and diagnostics."""


def _build_humanize_focus_patterns(
    expression_channel_records: list[dict[str, Any]] | None,
) -> list[str]:
    """Return a tiny dynamic focus list for the final humanize pass."""

    patterns: list[str] = []
    seen: set[str] = set()
    for item in list(expression_channel_records or [])[:6]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("text") or item.get("channel_id") or "").strip()
        if not label or label in seen:
            continue
        seen.add(label)
        patterns.append(label)
        if len(patterns) >= 3:
            break
    if len(patterns) < 3:
        for fallback in ("否定式并列", "AI 高频词汇", "模板化连接句"):
            if fallback not in seen:
                patterns.append(fallback)
                if len(patterns) >= 3:
                    break
    return patterns[:3]


def _residual_evidence_summary(report: HumanizeReport | None, text: str) -> dict[str, Any]:
    """Summarize report evidence that still appears in the accepted text."""
    if report is None or not text:
        return {
            "residual_evidence_count": 0,
            "residual_actionable_evidence_count": 0,
            "residual_evidence_samples": [],
        }

    samples: list[dict[str, Any]] = []
    actionable_count = 0
    for hit in list(getattr(report, "pattern_hits", []) or []):
        evidence = str(getattr(hit, "evidence_quote", "") or "").strip()
        if not evidence or evidence not in text:
            continue
        actionable = bool(getattr(hit, "actionable", False))
        if actionable:
            actionable_count += 1
        samples.append(
            {
                "pattern_id": str(getattr(hit, "pattern_id", "") or ""),
                "severity": str(getattr(hit, "severity", "") or ""),
                "actionable": actionable,
                "paragraph_index": getattr(hit, "paragraph_index", None),
                "evidence": evidence[:120],
            }
        )
    return {
        "residual_evidence_count": len(samples),
        "residual_actionable_evidence_count": actionable_count,
        "residual_evidence_samples": samples[:8],
    }


async def _collect_prior_rhythm_signatures(
    runner: Any,
    bundle: Any,
    chapter_number: int,
    lookback: int,
) -> list[dict[str, Any]]:
    """Collect rhythm signatures of the N prior chapters for template detection.

    Best-effort retrieval: returns [] when prior chapter text is unavailable
    (e.g. first chapter, memory disabled, or retrieval failure). Failures are
    logged at debug level so the humanize layer never blocks on a memory
    outage — cross-chapter detection simply degrades to a no-op.

    Priority order for prior-chapter text:
      1. Episodic memory ``search_by_semantic`` with chapter_range filter.
      2. Storage fallback: load archived ``chapter_NNN.md`` from the bundle
         layout (most recent runs persist chapter archives).
    """
    if lookback <= 0 or chapter_number <= 1:
        return []

    from novel_forge.core.utils.rhythm_metrics import compute_chapter_rhythm_signature

    signatures: list[dict[str, Any]] = []
    start_chapter = max(1, chapter_number - lookback)

    try:
        memory_context = None
        if runner is not None:
            has_memory = getattr(runner, "has_memory_context", None)
            if callable(has_memory) and has_memory():
                memory_context = getattr(runner, "memory_context", None)
            else:
                memory_context = getattr(runner, "_memory_context", None)

        if memory_context is not None:
            episodic = getattr(memory_context, "episodic_memory", None)
            if episodic is not None:
                for prior_ch in range(start_chapter, chapter_number):
                    try:
                        results = getattr(episodic, "search_by_semantic", None)
                        if not callable(results):
                            break
                        matches = (
                            results(
                                query="章节全文",
                                chapter_range=(prior_ch, prior_ch),
                                top_k=1,
                            )
                            or []
                        )
                        if inspect.isawaitable(matches):
                            matches = await matches
                        matches = matches or []
                        for match in matches:
                            text = None
                            if isinstance(match, dict):
                                text = match.get("text") or match.get("content")
                            else:
                                text = getattr(match, "text", None) or getattr(
                                    match, "content", None
                                )
                            if isinstance(text, str) and len(text) > 200:
                                sig = compute_chapter_rhythm_signature(text)
                                signatures.append({**sig.to_dict(), "chapter_number": prior_ch})
                                break
                    except Exception as exc:
                        _logger.debug(
                            "prior_rhythm_signature_lookup_failed | chapter=%d | error=%s",
                            prior_ch,
                            exc,
                        )
    except Exception as exc:
        _logger.debug("prior_rhythm_signature_memory_unavailable | error=%s", exc)

    # Storage fallback: load archived chapters if memory retrieval yielded few.
    if len(signatures) < lookback:
        storage = getattr(runner, "_storage", None) if runner is not None else None
        layout = getattr(bundle, "layout", None) if bundle is not None else None
        if storage is not None and layout is not None:
            existing_set = {s.get("chapter_number") for s in signatures}
            for prior_ch in range(start_chapter, chapter_number):
                if prior_ch in existing_set:
                    continue
                try:
                    chapter_path = getattr(layout, "chapter_path", None)
                    if not callable(chapter_path):
                        break
                    path = chapter_path(prior_ch)
                    if storage.exists(path):
                        text = storage.read_text(path)
                        if isinstance(text, str) and len(text) > 200:
                            sig = compute_chapter_rhythm_signature(text)
                            signatures.append({**sig.to_dict(), "chapter_number": prior_ch})
                except Exception as exc:
                    _logger.debug(
                        "prior_rhythm_signature_storage_fallback_failed | chapter=%d | error=%s",
                        prior_ch,
                        exc,
                    )

    # Stable sort by chapter_number so the prompt context is deterministic.
    signatures.sort(key=lambda s: s.get("chapter_number") or 0)
    return signatures


async def run_humanize_layer(
    runner: Any,
    bundle: Any,
    packet: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
    *,
    settings: Any | None = None,
    polish_modified: bool = False,
    expression_channel_records: list[dict[str, Any]] | None = None,
) -> HumanizeLayerResult:
    """Run the humanize scan layer on *current_text*.

    Flow:
    1. Check ``settings.humanize_enabled`` — skip if disabled.
    2. Check text length against ``settings.humanize_min_text_length``.
    3. Call :class:`HumanizeScanStep` to detect AI patterns and apply patches.
    4. Verify change ratio does not exceed ``settings.humanize_change_ratio_cap``.
    5. Run semantic drift detection — rollback on high-severity drift.

    Args:
        runner: Chapter runner with ``_router``, ``_builder``, ``_settings``.
        bundle: Prepared chapter artifacts (``chapter_outline``, etc.).
        packet: Chapter state packet with ``canon_context``.
        current_text: Chapter text to humanize.
        chapter_number: 1-based chapter number.
        trace: Pipeline trace object.
        settings: Optional settings override; defaults to ``runner._settings``.

    Returns:
        A :class:`HumanizeLayerResult` with the (possibly modified) text.
    """
    _settings = settings or runner._settings

    # ── 1. Configuration gate ─────────────────────────────────────────────────
    if not getattr(_settings, "humanize_enabled", False):
        _logger.debug("humanize_layer | chapter=%d | skipped: disabled", chapter_number)
        return HumanizeLayerResult(
            current_text=current_text,
            report=None,
            patches_applied=0,
            skipped_reason="disabled",
        )

    # ── 2. Text length gate ───────────────────────────────────────────────────
    min_length = int(getattr(_settings, "humanize_min_text_length", 500) or 500)
    if len(current_text) < min_length:
        _logger.debug(
            "humanize_layer | chapter=%d | skipped: text_too_short (%d < %d)",
            chapter_number,
            len(current_text),
            min_length,
        )
        return HumanizeLayerResult(
            current_text=current_text,
            report=None,
            patches_applied=0,
            skipped_reason="text_too_short",
        )

    # ── 2.5 Library retrieval (best-effort, graceful degrade) ─────────────
    library_enabled = bool(getattr(_settings, "humanize_library_enabled", True))
    library_hits: list[dict[str, Any]] = []
    library_enabled_pattern_ids: frozenset[str] | None = None
    if library_enabled:
        try:
            from novel_forge.memory.humanize_library_store import (
                HumanizeLibrary,
                LibraryDegradedError,
                LibraryUnavailableError,
                seed_builtin_patterns,
            )
            from novel_forge.memory.humanize_retrieval import HumanizeLibraryRetriever

            library = HumanizeLibrary.from_default_path()
            # Try to load language-appropriate library
            try:
                spec_path = getattr(bundle.layout, "spec_path", None)
                if spec_path and spec_path.exists():
                    import json
                    spec_data = json.loads(spec_path.read_text(encoding="utf-8"))
                    language = spec_data.get("language", "zh")
                    library = HumanizeLibrary.from_language(language)
            except Exception:
                pass  # Fall back to default library
            try:
                seed_builtin_patterns(library)
            except Exception:
                pass
            project_root = getattr(bundle.layout, "root", None)
            project_id = str(getattr(project_root, "name", "") or "")
            library_enabled_pattern_ids = frozenset(
                entry.pattern_id
                for entry in library.list_all()
                if entry.enabled and (entry.project_id is None or entry.project_id == project_id)
            )
            embedder: Any | None = None
            owns_embedder = False
            try:
                from novel_forge.memory.embedding_adapter import (
                    HumanizeEmbedderAdapter as _HumanizeEmbedderAdapter,
                )

                embedder = _HumanizeEmbedderAdapter(_settings)
                owns_embedder = True
            except Exception:
                embedder = None
            if (
                embedder is not None
                and getattr(_settings, "memory_vector_store_backend", "zvec") == "zvec"
            ):
                try:
                    from novel_forge.memory.integration import (
                        get_embedding_config_from_profiles,
                    )

                    embedding_config = get_embedding_config_from_profiles(
                        getattr(_settings, "memory_embedding_profile_id", None)
                    )
                    if embedding_config:
                        library.ensure_vector_index(
                            embedder,
                            provider=str(embedding_config.get("provider", "unknown")),
                            model=str(embedding_config.get("model", "unknown")),
                            index_type=str(
                                getattr(_settings, "memory_zvec_index_type", "hnsw")
                            ),
                            memory_limit_mb=int(
                                getattr(_settings, "memory_zvec_memory_limit_mb", 512)
                            ),
                        )
                except Exception:
                    _logger.debug(
                        "humanize_layer | chapter=%d | vector index unavailable",
                        chapter_number,
                        exc_info=True,
                    )
            retriever = HumanizeLibraryRetriever(
                embedder=embedder,
                sim_threshold=float(getattr(_settings, "humanize_library_sim_threshold", 0.6)),
            )
            try:
                library_hits = _safe_retrieve_for_chapter(
                    library,
                    retriever,
                    current_text,
                    chapter_number,
                    top_k=int(getattr(_settings, "humanize_library_top_k", 30)),
                    project_id=project_id,
                )
            finally:
                if owns_embedder and embedder is not None:
                    embedder.shutdown()
                library.close()
        except (LibraryUnavailableError, LibraryDegradedError, Exception) as exc:
            _safe_log_event_local(
                "humanize_library.unavailable",
                {
                    "chapter": chapter_number,
                    "error_type": type(exc).__name__,
                    "error_msg": str(exc)[:200],
                },
            )
            library_hits = []

    # ── 3. Run HumanizeScanStep (P0-3: bounded iteration loop) ──────────────
    step = HumanizeScanStep(
        runner._router,
        runner._builder,
        settings=_settings,
        trace=trace,
        on_step=getattr(runner, "on_step", None),
    )

    style_profile = None
    raw_style = getattr(bundle, "style_profile", None)
    if raw_style is not None:
        dump = getattr(raw_style, "model_dump", None)
        if callable(dump):
            style_profile = dump(mode="json")
    focus_patterns = _build_humanize_focus_patterns(expression_channel_records)
    world_rule_preservation: list[dict[str, Any]] = []
    cognitive_constraints: list[dict[str, Any]] = []
    try:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            project_stage_source_cards,
        )

        source_slice = getattr(bundle, "chapter_source_slice", None)
        if source_slice is not None:
            source_cards = project_stage_source_cards(source_slice, stage="humanize")
            rule_card = source_cards.get("world_rule_card", {})
            cognitive_constraints = cognitive_constraints_from_source_cards(source_cards)
            for item in list(rule_card.get("always_on", []) or []):
                rule = item.get("rule", {}) if isinstance(item, dict) else {}
                world_rule_preservation.append(
                    {
                        "rule_id": rule.get("rule_id", ""),
                        "content": rule.get("content", ""),
                        "forbidden_behavior": list(rule.get("forbidden_behavior", []) or []),
                    }
                )
    except Exception:
        world_rule_preservation = []
        cognitive_constraints = []
    context_capsule = {
        "chapter_title": str(getattr(bundle.chapter_outline, "title", "") or ""),
        "pov_character": str(getattr(bundle.chapter_outline, "pov_character", "") or ""),
        "post_repair_polish_modified_text": bool(polish_modified),
        "focus_patterns": focus_patterns,
        "world_rule_preservation": world_rule_preservation,
        "cognitive_constraints": cognitive_constraints,
    }

    prior_signatures = await _collect_prior_rhythm_signatures(
        runner=runner,
        bundle=bundle,
        chapter_number=chapter_number,
        lookback=int(getattr(_settings, "humanize_cross_chapter_lookback", 3) or 3),
    )

    # P0-3: Bounded iteration — run scan+patch up to max_rounds times
    max_rounds = int(getattr(_settings, "humanize_max_rounds", 2) or 2)
    iterative_text = current_text
    total_patches = 0
    total_paragraph_rewrites = 0
    last_report: HumanizeReport | None = None

    for round_idx in range(max_rounds):
        scan_result: HumanizeScanResult = await step.run(
            HumanizeScanInput(
                chapter_number=chapter_number,
                chapter_text=iterative_text,
                style_profile=style_profile,
                context_capsule=context_capsule,
                library_hits=library_hits if round_idx == 0 else [],
                library_enabled=library_enabled,
                library_enabled_pattern_ids=library_enabled_pattern_ids,
                prior_chapter_signatures=prior_signatures if round_idx == 0 else [],
            )
        )
        last_report = scan_result.report

        if scan_result.patches_applied <= 0 and scan_result.paragraph_rewrites_applied <= 0:
            break

        iterative_text = scan_result.revised_text
        total_patches += scan_result.patches_applied
        total_paragraph_rewrites += scan_result.paragraph_rewrites_applied

        _logger.info(
            "humanize_layer | chapter=%d | round=%d/%d | patches=%d | para_rewrites=%d",
            chapter_number,
            round_idx + 1,
            max_rounds,
            scan_result.patches_applied,
            scan_result.paragraph_rewrites_applied,
        )

    # Synthesize a combined scan_result for downstream checks
    scan_result = HumanizeScanResult(
        report=last_report,
        revised_text=iterative_text,
        patches_applied=total_patches,
        paragraph_rewrites_applied=total_paragraph_rewrites,
    )

    # ── 3.5 Bump library hit stats (best-effort) ──────────────────────────
    if library_enabled and scan_result.report is not None and scan_result.report.pattern_hits:
        try:
            from novel_forge.memory.humanize_library_store import HumanizeLibrary

            lib = HumanizeLibrary.from_default_path()
            lib.bump_hits_from_report(scan_result.report, chapter_number)
        except Exception as exc:
            _logger.debug(
                "humanize_layer | chapter=%d | bump_hits_from_report failed: %s",
                chapter_number,
                exc,
            )

    # If no patches or paragraph rewrites were applied, return as-is with the report.
    if scan_result.patches_applied <= 0 and scan_result.paragraph_rewrites_applied <= 0:
        _logger.info(
            "humanize_layer | chapter=%d | no modifications applied — returning original",
            chapter_number,
        )
        comparison = build_text_revision_diff(
            current_text,
            current_text,
            source="humanize_layer",
            chapter_number=chapter_number,
            label_before="原文",
            label_after="拟人化层输出",
            status="no_change",
            reason="no_patches_or_rewrites_applied",
            patches_applied=0,
        )
        return HumanizeLayerResult(
            current_text=current_text,
            report=scan_result.report,
            patches_applied=0,
            skipped_reason="",
            comparison=comparison,
        )

    # ── 4. Change ratio guard (P0-3C: adaptive cap based on hit count) ────────
    revised_text = scan_result.revised_text
    base_cap = float(getattr(_settings, "humanize_change_ratio_cap", 0.05) or 0.05)

    # P0-3C: Adaptive cap — more hits detected means more fixes needed
    hit_count = getattr(scan_result.report, "total_hits", 0) or 0
    if hit_count > 30:
        adaptive_cap = 0.12
    elif hit_count > 10:
        adaptive_cap = 0.08
    else:
        adaptive_cap = base_cap
    change_cap = max(base_cap, adaptive_cap)

    # Detect structural rewrite categories in hits — use relaxed cap when present
    has_structural_rewrites = any(
        getattr(hit, "category", "") in _STRUCTURAL_REWRITE_CATEGORIES
        for hit in scan_result.report.pattern_hits
    )
    if has_structural_rewrites:
        structural_cap = float(
            getattr(_settings, "humanize_structural_change_ratio_cap", 0.15) or 0.15
        )
        effective_cap = max(change_cap, structural_cap)
    else:
        effective_cap = change_cap

    ratio = _text_change_ratio(current_text, revised_text)

    if ratio > effective_cap:
        _logger.warning(
            "humanize_layer | chapter=%d | change_ratio=%.4f > effective_cap=%.4f "
            "(base_cap=%.4f, structural=%s) — rolling back",
            chapter_number,
            ratio,
            effective_cap,
            change_cap,
            has_structural_rewrites,
        )
        comparison = build_text_revision_diff(
            current_text,
            revised_text,
            source="humanize_layer",
            chapter_number=chapter_number,
            label_before="原文",
            label_after="拟人化层候选稿",
            status="rejected",
            reason="change_ratio_exceeded",
            patches_applied=scan_result.patches_applied,
            metadata={
                "change_ratio": ratio,
                "change_ratio_cap": change_cap,
                "effective_cap": effective_cap,
                "has_structural_rewrites": has_structural_rewrites,
            },
        )
        return HumanizeLayerResult(
            current_text=current_text,
            report=scan_result.report,
            patches_applied=0,
            skipped_reason="change_ratio_exceeded",
            comparison=comparison,
        )

    # ── 5. Semantic drift detection ───────────────────────────────────────────
    pov_character = str(getattr(bundle.chapter_outline, "pov_character", "") or "")
    known_characters = [
        name for name in (getattr(bundle.chapter_outline, "required_characters", []) or []) if name
    ]

    drift: DriftReport = detect_drift(
        current_text,
        revised_text,
        pov_character=pov_character,
        known_characters=known_characters,
        chapter_outline=bundle.chapter_outline,
    )

    if drift.has_drift and drift.high_severity_count > 0:
        _logger.warning(
            "humanize_layer | chapter=%d | semantic drift detected (%d high) — rolling back",
            chapter_number,
            drift.high_severity_count,
        )
        comparison = build_text_revision_diff(
            current_text,
            revised_text,
            source="humanize_layer",
            chapter_number=chapter_number,
            label_before="原文",
            label_after="拟人化层候选稿",
            status="rejected",
            reason="semantic_drift",
            patches_applied=scan_result.patches_applied,
            metadata={
                "change_ratio": ratio,
                "change_ratio_cap": change_cap,
                "drift": drift.to_step_payload(),
            },
        )
        return HumanizeLayerResult(
            current_text=current_text,
            report=scan_result.report,
            patches_applied=0,
            skipped_reason="semantic_drift",
            comparison=comparison,
        )

    before_rescan_hits = HumanizeScanStep.prescreen_text(
        current_text,
        enabled_pattern_ids=library_enabled_pattern_ids,
    )
    after_rescan_hits = HumanizeScanStep.prescreen_text(
        revised_text,
        enabled_pattern_ids=library_enabled_pattern_ids,
    )
    before_pattern_counts = Counter(str(hit.get("pattern_id") or "") for hit in before_rescan_hits)
    after_pattern_counts = Counter(str(hit.get("pattern_id") or "") for hit in after_rescan_hits)
    new_rescan_patterns = {
        pattern_id: count - before_pattern_counts.get(pattern_id, 0)
        for pattern_id, count in after_pattern_counts.items()
        if count > before_pattern_counts.get(pattern_id, 0)
    }
    new_rescan_hit_count = sum(new_rescan_patterns.values())
    quality_regressions = HumanizeScanStep.introduced_quality_regressions(
        current_text,
        revised_text,
    )
    residual = _residual_evidence_summary(scan_result.report, revised_text)
    if new_rescan_hit_count or quality_regressions:
        _logger.warning(
            "humanize_layer | chapter=%d | quality regression after patches: "
            "new_hits=%d deterministic=%d — rolling back",
            chapter_number,
            new_rescan_hit_count,
            len(quality_regressions),
        )
        comparison = build_text_revision_diff(
            current_text,
            revised_text,
            source="humanize_layer",
            chapter_number=chapter_number,
            label_before="原文",
            label_after="拟人化层候选稿",
            status="rejected",
            reason="quality_regression",
            patches_applied=scan_result.patches_applied,
            metadata={
                "change_ratio": ratio,
                "change_ratio_cap": change_cap,
                "drift": drift.to_step_payload(),
                "local_rescan_new_hits": new_rescan_hit_count,
                "local_rescan_new_patterns": new_rescan_patterns,
                "quality_regressions": quality_regressions,
                **residual,
            },
        )
        return HumanizeLayerResult(
            current_text=current_text,
            report=scan_result.report,
            patches_applied=0,
            skipped_reason="quality_regression",
            comparison=comparison,
        )
    if residual["residual_actionable_evidence_count"]:
        _logger.warning(
            "humanize_layer | chapter=%d | residual_actionable_evidence=%d after accepted patches",
            chapter_number,
            residual["residual_actionable_evidence_count"],
        )

    # ── All checks passed — accept humanized text ─────────────────────────────
    _logger.info(
        "humanize_layer | chapter=%d | patches=%d | ratio=%.4f | drift=%s — accepted",
        chapter_number,
        scan_result.patches_applied,
        ratio,
        "low" if drift.has_drift else "none",
    )

    comparison = build_text_revision_diff(
        current_text,
        revised_text,
        source="humanize_layer",
        chapter_number=chapter_number,
        label_before="原文",
        label_after="拟人化层输出",
        status="accepted",
        reason="",
        patches_applied=scan_result.patches_applied,
        metadata={
            "change_ratio": ratio,
            "change_ratio_cap": change_cap,
            "drift": drift.to_step_payload(),
            "local_rescan_new_hits": new_rescan_hit_count,
            "local_rescan_new_patterns": new_rescan_patterns,
            "quality_regressions": quality_regressions,
            **residual,
        },
    )
    return HumanizeLayerResult(
        current_text=revised_text,
        report=scan_result.report,
        patches_applied=scan_result.patches_applied,
        paragraph_rewrites_applied=scan_result.paragraph_rewrites_applied,
        skipped_reason="",
        comparison=comparison,
    )
