"""ChapterPatchStep — surgical patch-based chapter repair.

Instead of sending the full chapter text and expecting the full chapter text back,
this step:

1. Splits the chapter into paragraphs.
2. For each issue, extracts a narrow context window (target paragraphs + neighbours).
3. Calls the LLM with ONLY those windows, asking for ``{original, replacement}`` patches.
4. Applies the patches back into the full chapter text via exact-substring replacement.

Token cost comparison (3000-char chapter, 3 issues):
- EditStep (full text): ~4500 in + ~4500 out ≈ 9000 tokens
- ChapterPatchStep:     ~1800 in +  ~400 out ≈ 2200 tokens  (~75 % reduction)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from novel_forge.core.constants import TaskType
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens

_logger = get_logger("pipeline.chapter_patch")


class PatchInput(BaseModel):
    """Input for the chapter patch step."""

    model_config = {"arbitrary_types_allowed": True}

    chapter_number: int
    chapter_text: str
    issues: list[Any]
    """CausalIssue / ContinuityIssue objects — need .location, .summary, .fix_suggestion, .severity.
    May optionally have .evidence (exact text excerpt) for more precise targeting."""
    causal_link: dict[str, Any] | None = None
    """Optional causal chain context dict (previous_event, causal_mechanism, unresolved_question)."""
    context_size: int = 2
    """Number of paragraphs to include on each side of the target paragraphs."""
    must_fix_summaries: list[str] = Field(default_factory=list)
    """Critical/high-severity issue summaries that must be resolved — rendered as a
    priority block in the patch prompt so the model does not skip them."""
    style: str = "literary"
    style_profile: dict[str, Any] | None = None
    continuity_context: dict[str, Any] | None = None
    """Optional continuity constraints for continuity-style patch fixes."""
    boundary_context: dict[str, Any] | None = None
    """Optional cross-chapter boundary context (prev ending + opening/closing contracts).
    Used for opening/closing repair without exposing the full chapter."""
    kernel_context: dict[str, Any] | None = None
    """Optional StoryKernel field slices from ContextComposer.
    Merged into the prompt context (entities, world_rules, etc.)."""
    cognitive_constraints: list[dict[str, Any]] = Field(default_factory=list)
    """Complete chapter cognitive boundaries; fixed structured facts, never vector recall."""
    propagate_failure: bool = False
    """Let an orchestration-owned safety policy handle failures instead of returning fallback."""


class PatchResult(BaseModel):
    """Result of a patch run."""

    revised_text: str
    patches_applied: int
    patches_attempted: int
    fallback: bool = False
    """True when the call or executor explicitly fell back to the original text."""
    # ── V2 diagnostics (populated only when patch_executor_version="v2") ──
    failure_codes: list[str] = Field(default_factory=list)
    """Per-patch failure reason strings from PatchExecutorV2 (e.g. 'not_found', 'ambiguous_match')."""
    unique_match_count: int = 0
    normalized_match_count: int = 0
    ambiguous_match_count: int = 0
    failure_kind: str = ""
    """Stable top-level failure category: gateway or internal."""
    failure_reason: str = ""
    """Typed failure message retained when the compatibility fallback is used."""
    error_type: str = ""
    """Original exception class name for logs and Desktop diagnostics."""


class ChapterPatchStep:
    """Surgical chapter patch step — no inheritance from PipelineStep to keep it lightweight."""

    def __init__(self, router: Any, builder: Any, *, settings: Any, trace: Any = None) -> None:
        from novel_forge.obs.tracer import PipelineTrace

        self._router = router
        self._builder = builder
        self._settings = settings
        self._trace = trace or PipelineTrace()

    @staticmethod
    def _estimate_output_chars(issues_ctx: list[dict[str, Any]]) -> int:
        if not issues_ctx:
            return 800
        total = 512
        for item in issues_ctx:
            window_text = str(item.get("window_text") or "")
            evidence = str(item.get("evidence_quote") or "")
            summary = str(item.get("summary") or "")
            directive = item.get("repair_directive") if isinstance(item, dict) else None
            fix_mode = str(item.get("fix_mode") or "").lower()
            window_budget = min(len(window_text), 1800)
            evidence_budget = min(len(evidence), 800)
            summary_budget = min(len(summary), 300)
            directive_budget = (
                min(len(str(directive or "")), 900) if isinstance(directive, dict) else 0
            )
            per_issue_floor = 900 if fix_mode in {"insert", "replace"} else 650
            total += max(
                per_issue_floor,
                window_budget + evidence_budget + summary_budget + directive_budget,
            )
        return total

    def _compute_max_tokens(self, issues_ctx: list[dict[str, Any]]) -> int:
        token_cap = int(getattr(self._settings, "patch_chapter_max_tokens", 8192) or 8192)
        token_cap = max(1024, token_cap)
        return calculate_route_aware_max_tokens(
            self._router,
            TaskType.PATCH_CHAPTER,
            self._estimate_output_chars(issues_ctx),
            prompt_overhead=0,
            safety_margin=0.92,
            min_tokens=min(2048, token_cap),
            max_cap=token_cap,
        )

    async def run(self, input_data: PatchInput) -> PatchResult:
        from novel_forge.core.format_contracts import validate_json_output_contract
        from novel_forge.core.parsing.parse_utils import safe_parse_json, strip_markdown_fences
        from novel_forge.core.parsing.response_schemas import validate_response_schema
        from novel_forge.core.utils.patch_utils import (
            apply_patches,
            build_issue_windows,
            patches_from_dicts,
            split_paragraphs,
        )

        paragraphs = split_paragraphs(input_data.chapter_text)
        issue_windows = build_issue_windows(
            paragraphs, input_data.issues, context_size=input_data.context_size
        )

        # Build serialisable context for the prompt template
        issues_ctx: list[dict[str, Any]] = []
        for iw in issue_windows:
            issue = iw["issue"]
            repair_directive: Any = getattr(issue, "repair_directive", None)
            if hasattr(repair_directive, "model_dump"):
                repair_directive_payload = repair_directive.model_dump(
                    mode="json",
                    exclude={"schema_version", "created_at"},
                )
            elif isinstance(repair_directive, dict):
                repair_directive_payload = dict(repair_directive)
            else:
                repair_directive_payload = {}
            issues_ctx.append(
                {
                    "severity": (getattr(issue, "severity", "") or "medium"),
                    "location": (getattr(issue, "location", "") or ""),
                    "summary": (getattr(issue, "summary", "") or ""),
                    "fix_suggestion": (getattr(issue, "fix_suggestion", "") or ""),
                    "issue_type": (getattr(issue, "issue_type", "") or ""),
                    "evidence_quote": (getattr(issue, "evidence_quote", "") or ""),
                    "fix_mode": (getattr(issue, "fix_mode", "") or ""),
                    "missing_anchors": [
                        anchor.model_dump(mode="json") if hasattr(anchor, "model_dump") else anchor
                        for anchor in (getattr(issue, "missing_anchors", []) or [])
                    ],
                    "postconditions": [
                        condition.model_dump(mode="json")
                        if hasattr(condition, "model_dump")
                        else condition
                        for condition in (getattr(issue, "postconditions", []) or [])
                    ],
                    "repair_directive": repair_directive_payload,
                    "window_text": iw["window_text"],
                    "para_start": iw["para_start"],
                    "para_end": iw["para_end"],
                    "editable_para_start": iw["editable_para_start"],
                    "editable_para_end": iw["editable_para_end"],
                }
            )

        context: dict[str, Any] = {}
        if input_data.kernel_context is not None:
            context.update(input_data.kernel_context)
        context.update(
            {
                "chapter_number": input_data.chapter_number,
                "issues_with_windows": issues_ctx,
                "causal_link": input_data.causal_link,
                "continuity_context": input_data.continuity_context,
                "boundary_context": input_data.boundary_context,
                "must_fix_summaries": input_data.must_fix_summaries,
                "style": input_data.style,
                "style_profile": input_data.style_profile,
                "cognitive_constraints": input_data.cognitive_constraints,
            }
        )

        try:
            request = self._builder.build(
                TaskType.PATCH_CHAPTER,
                context,
                max_tokens=self._compute_max_tokens(issues_ctx),
                temperature=getattr(self._settings, "temp_patch_chapter", 0.15),
            )
            response = await self._router.route(request)
            raw_text = strip_markdown_fences(response.content)
            try:
                data = safe_parse_json(raw_text) if raw_text.strip() else {}
            except Exception:
                data = {}
            if not isinstance(data, dict):
                raise ValueError("PATCH_CHAPTER response must be a JSON object")
            validate_response_schema(data, TaskType.PATCH_CHAPTER)
            validate_json_output_contract(TaskType.PATCH_CHAPTER, data)
            patches: list[dict[str, Any]] = data.get("patches") or []
            if not isinstance(patches, list):
                patches = []

            # Enrich patches with paragraph anchors from issue windows
            # so apply_patches can restrict matching to the correct window.
            for patch in patches:
                _missing_window_anchor = "para_start" not in patch or "para_end" not in patch
                _missing_editable_anchor = (
                    "editable_para_start" not in patch or "editable_para_end" not in patch
                )
                if _missing_window_anchor or _missing_editable_anchor:
                    # Try to find the matching issue window for this patch
                    patch_original = (patch.get("original") or "").strip()
                    if patch_original:
                        for iw in issue_windows:
                            if patch_original[:40] in iw["window_text"]:
                                patch.setdefault("para_start", iw["para_start"])
                                patch.setdefault("para_end", iw["para_end"])
                                patch.setdefault("editable_para_start", iw["editable_para_start"])
                                patch.setdefault("editable_para_end", iw["editable_para_end"])
                                break

            # ── Executor dispatch: v1 (legacy) or v2 (transactional) ──
            _use_v2 = getattr(self._settings, "patch_executor_version", "v1") == "v2"

            if _use_v2:
                from novel_forge.core.patch_engine import PatchExecutorV2

                patch_ops = patches_from_dicts(patches, issue_windows)
                executor = PatchExecutorV2(strategy="best_effort")
                v2_result = executor.apply_batch(input_data.chapter_text, patch_ops)

                _logger.info(
                    "chapter_patch_v2 | chapter=%d | attempted=%d | applied=%d | "
                    "unique=%d normalized=%d ambiguous=%d failures=%d",
                    input_data.chapter_number,
                    v2_result.attempted_count,
                    v2_result.applied_count,
                    v2_result.unique_match_count,
                    v2_result.normalized_match_count,
                    v2_result.ambiguous_match_count,
                    len(v2_result.failed_items),
                )
                return PatchResult(
                    revised_text=v2_result.revised_text,
                    patches_applied=v2_result.applied_count,
                    patches_attempted=v2_result.attempted_count,
                    fallback=v2_result.fallback,
                    failure_codes=[f.failure_code.value for f in v2_result.failed_items],
                    unique_match_count=v2_result.unique_match_count,
                    normalized_match_count=v2_result.normalized_match_count,
                    ambiguous_match_count=v2_result.ambiguous_match_count,
                )

            # ── Legacy v1 path ──
            revised, applied = apply_patches(
                input_data.chapter_text, patches, paragraphs=paragraphs
            )

            _logger.info(
                "chapter_patch | chapter=%d | attempted=%d | applied=%d",
                input_data.chapter_number,
                len(patches),
                applied,
            )
            return PatchResult(
                revised_text=revised,
                patches_applied=applied,
                patches_attempted=len(patches),
            )

        except Exception as exc:
            failure_action = (
                "propagating to orchestration safety policy"
                if input_data.propagate_failure
                else "returning original text"
            )
            _logger.warning(
                "chapter_patch_failed | chapter=%d | error=%s | action=%s",
                input_data.chapter_number,
                exc,
                failure_action,
            )
            if input_data.propagate_failure:
                raise
            from novel_forge.core.exceptions import ModelGatewayError

            return PatchResult(
                revised_text=input_data.chapter_text,
                patches_applied=0,
                patches_attempted=0,
                fallback=True,
                failure_kind=("gateway" if isinstance(exc, ModelGatewayError) else "internal"),
                failure_reason=f"{type(exc).__name__}: {exc}",
                error_type=type(exc).__name__,
            )
