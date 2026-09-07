"""PatchExecutorV2 — transactional, verifiable chapter patch execution.

Three-phase execution:
1. **Precheck** — validate source hash, anchor bounds, editable window validity.
2. **Dry-run** — apply all patches to an in-memory copy, detect conflicts/ambiguities.
3. **Commit** — only if dry-run passes strategy gate, write result.

Compared to v1 (``patch_utils.apply_patches``):
- Unique-match constraint: ``replace_all=False`` + match_count > 1 → AMBIGUOUS_MATCH.
- Source-text-hash guard: optional stale-text rejection.
- Span overlap detection: overlapping patches are flagged as OVERLAP_CONFLICT.
- Failure codes on every patch: callers can make informed escalation decisions.
- Backwards compatible: PatchApplyResult exposes ``revised_text``, ``applied_count``,
  ``attempted_count``, ``fallback`` so existing consumers work without change.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from novel_forge.core.patch_engine.matchers import run_matcher_chain
from novel_forge.core.patch_engine.models import (
    FailureCode,
    MatchPolicy,
    PatchApplyResult,
    PatchFailure,
    PatchOperation,
)
from novel_forge.core.utils.patch_utils import normalize_post_patch_punctuation, split_paragraphs
from novel_forge.obs.logger import get_logger

_logger = get_logger("patch_engine.executor")


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class PatchExecutorV2:
    """Batch executor with dry-run, conflict detection, and failure codes."""

    def __init__(
        self,
        *,
        strategy: Literal["strict", "best_effort"] = "strict",
    ) -> None:
        self._strategy = strategy

    # ── Public API ────────────────────────────────────────────────────────────

    def apply_batch(
        self,
        text: str,
        patches: list[PatchOperation],
        *,
        source_text_hash: str = "",
    ) -> PatchApplyResult:
        """Apply a batch of patches transactionally.

        Parameters
        ----------
        text
            Full chapter text.
        patches
            Ordered list of patch operations.
        source_text_hash
            Expected SHA-256 of *text*.  If non-empty and mismatched, all
            patches are rejected with STALE_SOURCE_HASH.

        Returns
        -------
        PatchApplyResult
            Contains revised_text, counts, and per-patch failure diagnostics.
        """
        attempted = len(patches)
        if not patches:
            return PatchApplyResult(revised_text=text, attempted_count=0)

        # ── Phase 1: Precheck ──
        if source_text_hash and _text_hash(text) != source_text_hash:
            failures = [
                PatchFailure(
                    patch_id=p.patch_id or f"patch_{i}",
                    failure_code=FailureCode.STALE_SOURCE_HASH,
                    detail="Source text hash mismatch — text has been modified since report.",
                )
                for i, p in enumerate(patches)
            ]
            _logger.warning(
                "patch_executor_v2: source_text_hash mismatch, rejecting all %d patches",
                attempted,
            )
            return PatchApplyResult(
                revised_text=text,
                attempted_count=attempted,
                failed_items=failures,
                fallback=True,
                stale_reject_count=attempted,
            )

        # ── Phase 2: Dry-run ──
        paragraphs = split_paragraphs(text)
        dry_run_results = self._dry_run(text, paragraphs, patches)

        # Collect failures
        failures_list: list[PatchFailure] = []
        successful: list[_DryRunHit] = []
        stats = _Stats()

        for dr in dry_run_results:
            if dr.failure is not None:
                failures_list.append(dr.failure)
                if dr.failure.failure_code == FailureCode.AMBIGUOUS_MATCH:
                    stats.ambiguous += 1
            else:
                successful.append(dr)
                if dr.matcher_name == "exact":
                    stats.unique_match += 1
                elif dr.matcher_name == "normalized":
                    stats.normalized_match += 1

        # Overlap conflict detection among successful patches
        successful.sort(key=lambda h: h.abs_start)
        overlap_indices: set[int] = set()
        for i in range(len(successful) - 1):
            if successful[i].abs_end > successful[i + 1].abs_start:
                overlap_indices.add(i)
                overlap_indices.add(i + 1)

        if overlap_indices:
            for idx in sorted(overlap_indices, reverse=True):
                hit = successful[idx]
                failures_list.append(
                    PatchFailure(
                        patch_id=hit.patch_id,
                        failure_code=FailureCode.OVERLAP_CONFLICT,
                        detail=f"Overlaps with adjacent patch at [{hit.abs_start}:{hit.abs_end}].",
                    )
                )
                successful.pop(idx)

        # ── Strategy gate ──
        critical_failures = [
            f for f in failures_list
            if f.failure_code in (FailureCode.AMBIGUOUS_MATCH, FailureCode.OVERLAP_CONFLICT)
        ]

        if self._strategy == "strict" and critical_failures:
            # Check if any critical/high severity patches failed
            # In strict mode, any ambiguous/overlap → full batch reject
            _logger.info(
                "patch_executor_v2: strict strategy — %d critical failures, rejecting batch",
                len(critical_failures),
            )
            all_failures = failures_list + [
                PatchFailure(
                    patch_id=s.patch_id,
                    failure_code=FailureCode.GUARD_REJECTED,
                    detail="Batch rejected due to strict strategy gate (other patches had critical failures).",
                )
                for s in successful
            ]
            return PatchApplyResult(
                revised_text=text,
                attempted_count=attempted,
                failed_items=all_failures,
                fallback=True,
                ambiguous_match_count=stats.ambiguous,
            )

        # ── Phase 3: Commit — apply successful patches in reverse offset order ──
        if not successful:
            return PatchApplyResult(
                revised_text=text,
                attempted_count=attempted,
                failed_items=failures_list,
                fallback=bool(failures_list),
                ambiguous_match_count=stats.ambiguous,
                unique_match_count=stats.unique_match,
                normalized_match_count=stats.normalized_match,
            )

        result = self._commit(text, successful)
        result = normalize_post_patch_punctuation(result)

        _logger.info(
            "patch_executor_v2: applied=%d attempted=%d failed=%d strategy=%s",
            len(successful),
            attempted,
            len(failures_list),
            self._strategy,
        )

        return PatchApplyResult(
            revised_text=result,
            applied_count=len(successful),
            attempted_count=attempted,
            failed_items=failures_list,
            unique_match_count=stats.unique_match,
            normalized_match_count=stats.normalized_match,
            ambiguous_match_count=stats.ambiguous,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _dry_run(
        self,
        text: str,
        paragraphs: list[str],
        patches: list[PatchOperation],
    ) -> list[_DryRunHit]:
        """Run all patches against text without modifying it."""
        results: list[_DryRunHit] = []

        for i, patch in enumerate(patches):
            pid = patch.patch_id or f"patch_{i}"
            original = patch.original.strip()
            if not original:
                results.append(_DryRunHit.fail(pid, FailureCode.NOT_FOUND, "Empty original."))
                continue

            # Determine search window
            window, window_offset, in_editable = self._resolve_window(
                text, paragraphs, patch,
            )

            if not in_editable:
                results.append(_DryRunHit.fail(pid, FailureCode.OUT_OF_WINDOW, "Could not resolve editable window."))
                continue

            # Run matcher chain
            match = run_matcher_chain(window, original)
            # If not found in editable window, try the larger para window once
            if match is None and (
                patch.para_start is not None and patch.para_end is not None
                and (patch.para_start != patch.editable_para_start or patch.para_end != patch.editable_para_end)
            ):
                _larger_window, _larger_offset, _ = self._resolve_window(
                    text, paragraphs,
                    PatchOperation(
                        patch_id=patch.patch_id,
                        original=patch.original,
                        replacement=patch.replacement,
                        para_start=patch.para_start,
                        para_end=patch.para_end,
                        editable_para_start=patch.para_start,
                        editable_para_end=patch.para_end,
                    ),
                )
                match = run_matcher_chain(_larger_window, original)
                if match is not None:
                    window = _larger_window
                    window_offset = _larger_offset
            if match is None:
                results.append(_DryRunHit.fail(pid, FailureCode.NOT_FOUND, "No matcher could locate original in window."))
                continue

            # Ambiguity check
            if not patch.replace_all and match.match_count > 1:
                if patch.match_policy == MatchPolicy.UNIQUE_REQUIRED:
                    results.append(_DryRunHit.fail(
                        pid,
                        FailureCode.AMBIGUOUS_MATCH,
                        f"Found {match.match_count} occurrences but replace_all=False and match_policy=unique_required.",
                    ))
                    continue

            abs_start = window_offset + match.start
            abs_end = window_offset + match.end

            results.append(_DryRunHit(
                patch_id=pid,
                patch=patch,
                abs_start=abs_start,
                abs_end=abs_end,
                matcher_name=match.matcher_name,
            ))

        return results

    def _resolve_window(
        self,
        text: str,
        paragraphs: list[str],
        patch: PatchOperation,
    ) -> tuple[str, int, bool]:
        """Return (window_text, offset_in_full_text, is_valid).

        If paragraph anchors are available, restrict to the editable subwindow.
        Otherwise, use the full text.
        """
        if patch.para_start is not None and patch.para_end is not None:
            p_start = max(0, patch.para_start)
            p_end = min(len(paragraphs) - 1, patch.para_end)

            e_start = patch.editable_para_start
            e_end = patch.editable_para_end
            if e_start is not None and e_end is not None:
                e_start = max(p_start, e_start)
                e_end = min(p_end, e_end)
            else:
                e_start, e_end = p_start, p_end

            if e_start > e_end:
                e_start, e_end = p_start, p_end

            # Calculate offset of editable window in full text
            offset = 0
            for idx in range(e_start):
                offset += len(paragraphs[idx]) + 2  # +2 for "\n\n"

            window = "\n\n".join(paragraphs[e_start: e_end + 1])
            return window, offset, True

        # Full text fallback
        return text, 0, True

    def _commit(self, text: str, hits: list[_DryRunHit]) -> str:
        """Apply hits using fragment-list join (single O(n) pass).

        Sorts hits by ascending offset and builds the result from slices,
        avoiding the O(n * patches) cost of repeated string slicing that
        the previous reverse-order approach incurred.
        """
        sorted_hits = sorted(hits, key=lambda h: h.abs_start)
        parts: list[str] = []
        cursor = 0
        for hit in sorted_hits:
            patch = hit.patch
            assert patch is not None, "patch should not be None for successful hits"
            parts.append(text[cursor:hit.abs_start])
            parts.append(patch.replacement)
            cursor = hit.abs_end
        parts.append(text[cursor:])
        return "".join(parts)


# ── Supporting dataclass ──────────────────────────────────────────────────────

class _Stats:
    __slots__ = ("unique_match", "normalized_match", "ambiguous")

    def __init__(self) -> None:
        self.unique_match = 0
        self.normalized_match = 0
        self.ambiguous = 0


class _DryRunHit:
    """Result of evaluating a single patch in dry-run phase."""

    __slots__ = ("patch_id", "patch", "abs_start", "abs_end", "matcher_name", "failure")

    def __init__(
        self,
        *,
        patch_id: str,
        patch: PatchOperation | None = None,
        abs_start: int = 0,
        abs_end: int = 0,
        matcher_name: str = "",
        failure: PatchFailure | None = None,
    ) -> None:
        self.patch_id = patch_id
        self.patch = patch
        self.abs_start = abs_start
        self.abs_end = abs_end
        self.matcher_name = matcher_name
        self.failure = failure

    @classmethod
    def fail(cls, patch_id: str, code: FailureCode, detail: str = "") -> _DryRunHit:
        return cls(
            patch_id=patch_id,
            failure=PatchFailure(patch_id=patch_id, failure_code=code, detail=detail),
        )
