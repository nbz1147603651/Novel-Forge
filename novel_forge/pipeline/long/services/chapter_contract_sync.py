"""Chapter-contract sync helpers for outline-driven local regeneration.

This module provides **pure, side-effect-free** helpers used by the workspace
``execute_sync_chapter_contracts`` runner.  The intent is to keep every
fingerprint, cascade, merge, and stale-marking decision in small, unit
testable functions that can be exercised without spinning up an LLM, a
project lock, or any storage backend.

Scope (intentionally narrow):

* ``compute_affected_chapters_from_outline_fingerprint`` — diff the current
  ``StoryOutline`` against a previously-cached fingerprint and return the
  chapter numbers that changed.
* ``compute_cascade`` — propagate downstream chapter numbers from explicit
  ``entry_state_requirements`` references (e.g. ``"承接第3章…"``).
* ``merge_reextracted_contracts`` — keep untouched chapters verbatim and
  splice in newly-reextracted contracts for the focus subset.
* ``mark_artifact_stale`` — annotate a JSON artifact with ``stale=true`` and
  a reason string, atomically.
* ``collect_downstream_stale_paths`` — enumerate the artifact paths that
  would be marked stale given a set of affected/cascade chapters.

These helpers intentionally do **not** call any LLM.  The runner that
consumes them owns retry, prompt assembly, and disk writes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from novel_forge.persistence.filesystem import atomic_write_text
from novel_forge.persistence.models import ProjectLayout

_log = logging.getLogger(__name__)

# Chinese numeral chapter reference patterns used by ``compute_cascade``.
# Matches: "第3章", "第03章", "第 3 章", "第十二章", "第两百章".
# ``re.UNICODE`` is implicit in str patterns in Python 3.
_CN_DIGIT = "〇一二三四五六七八九十百千零两壹贰叁肆伍陆柒捌玖拾佰仟"
_CN_DIGIT_MAP: dict[str, int] = {
    "零": 0, "〇": 0,
    "一": 1, "壹": 1,
    "二": 2, "贰": 2, "两": 2,
    "三": 3, "叁": 3,
    "四": 4, "肆": 4,
    "五": 5, "伍": 5,
    "六": 6, "陆": 6,
    "七": 7, "柒": 7,
    "八": 8, "捌": 8,
    "九": 9, "玖": 9,
    "十": 10, "拾": 10,
    "百": 100, "佰": 100,
    "千": 1000, "仟": 1000,
}

_ARABIC_CHAPTER_RE = re.compile(r"第\s*(\d{1,4})\s*章")
_CN_CHAPTER_RE = re.compile(rf"第\s*([{_CN_DIGIT}]{{1,8}})\s*章")
_CONTRACT_REFERENCE_FIELDS: tuple[str, ...] = (
    "entry_state_requirements",
    "required_events",
    "required_progressions",
    "allowed_progressions",
    "forbidden_changes",
    "forbidden_progressions",
    "exit_state_targets",
    "completion_criteria",
    "future_leak_risks",
)
_STRUCTURAL_CHANGE_RATIO = 0.2
_STRUCTURAL_CHANGE_MIN_COUNT = 4


def _cn_to_int(text: str) -> int | None:
    """Convert a small Chinese numeral (e.g. ``十二``) to int.  Returns None on failure."""
    if not text:
        return None
    # Pure digits already handled by the arabic regex; here we only accept CN digits.
    if not all(ch in _CN_DIGIT_MAP for ch in text):
        return None
    if text in _CN_DIGIT_MAP and _CN_DIGIT_MAP[text] < 10:
        return _CN_DIGIT_MAP[text]
    total = 0
    current = 0
    for ch in text:
        v = _CN_DIGIT_MAP[ch]
        if v >= 10:
            if current == 0:
                current = 1
            total += current * v
            current = 0
        else:
            current = v
    total += current
    return total or None


def _scan_chapter_references(text: str) -> set[int]:
    """Return all chapter numbers mentioned in free-text ``text``.

    Handles both arabic (``第3章``) and simple Chinese-numeral (``第十二章``)
    forms.  Pure-number references like ``第3节`` are intentionally ignored
    because they are not chapters.
    """
    if not text:
        return set()
    found: set[int] = set()
    for match in _ARABIC_CHAPTER_RE.finditer(text):
        try:
            n = int(match.group(1))
        except ValueError:
            continue
        if 1 <= n <= 9999:
            found.add(n)
    for match in _CN_CHAPTER_RE.finditer(text):
        cn_n: int | None = _cn_to_int(match.group(1))
        if cn_n is not None and 1 <= cn_n <= 9999:
            found.add(cn_n)
    return found


# ── Outline fingerprint ──────────────────────────────────────────────────────


def _chapter_signature(chapter: Any) -> tuple[Any, ...]:
    """Return a JSON-serializable signature for a single chapter.

    We hash on the fields that most directly drive chapter-contract content:
    title, goal, plot beats, subplot/element focus, POV/setting/time anchors,
    expected hook, and payoff hints.  These are the outline inputs used to
    derive hard chapter-contract obligations.
    """
    hook = getattr(chapter, "expected_hook", None)
    hook_payload: Any = ""
    if hook is not None:
        if hasattr(hook, "model_dump"):
            hook_payload = hook.model_dump(mode="json")
        elif isinstance(hook, dict):
            hook_payload = hook
        else:
            hook_payload = str(hook)
    expected_payoffs = getattr(chapter, "expected_payoffs", None)
    payoffs_payload: Any
    if isinstance(expected_payoffs, list):
        payoffs_payload = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in expected_payoffs
        ]
    else:
        payoffs_payload = _normalize_str_list(expected_payoffs)
    return (
        int(getattr(chapter, "chapter_number", 0) or 0),
        str(getattr(chapter, "title", "") or ""),
        str(getattr(chapter, "goal", "") or ""),
        _normalize_str_list(getattr(chapter, "main_plot_points", None)),
        _normalize_str_list(getattr(chapter, "beats_summary", None)),
        _normalize_str_list(getattr(chapter, "subplot_points", None)),
        str(getattr(chapter, "subplot_focus", "") or ""),
        _normalize_str_list(getattr(chapter, "element_focus", None)),
        str(getattr(chapter, "pov_character", "") or ""),
        bool(getattr(chapter, "pov_switch", False)),
        str(getattr(chapter, "setting", "") or ""),
        int(getattr(chapter, "expected_word_count", 0) or 0),
        _normalize_str_list(getattr(chapter, "involved_characters", None)),
        str(getattr(chapter, "time_anchor", "") or ""),
        str(getattr(chapter, "time_span", "") or ""),
        str(getattr(chapter, "time_gap_from_prev", "") or ""),
        str(getattr(chapter, "countdown_state", "") or ""),
        bool(getattr(chapter, "is_flashback", False)),
        hook_payload,
        payoffs_payload,
    )


def _normalize_str_list(value: Any) -> list[str]:
    """Best-effort normalize an iterable to a flat list of stripped strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        return [str(v).strip() for v in value.values() if str(v or "").strip()]
    if isinstance(value, Iterable):
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                out.append(text)
        return out
    return [str(value).strip()] if str(value or "").strip() else []


def compute_outline_fingerprint(outline: Any) -> str:
    """Compute a stable SHA-256 fingerprint of an outline's contract-relevant content.

    The fingerprint intentionally ignores non-deterministic fields (timestamps,
    internal ids) so the same outline content always produces the same hash.
    """
    chapters = list(getattr(outline, "chapters", []) or [])
    payload = {
        "synopsis": str(getattr(outline, "synopsis", "") or ""),
        "total_chapters": int(getattr(outline, "total_chapters", 0) or 0),
        "volume_mode": str(getattr(outline, "volume_mode", "") or ""),
        "chapters": [_chapter_signature(ch) for ch in sorted(
            chapters,
            key=lambda c: int(getattr(c, "chapter_number", 0) or 0),
        )],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_outline_chapter_fingerprints(outline: Any) -> dict[str, str]:
    """Return stable per-chapter fingerprints keyed by chapter number string."""
    out: dict[str, str] = {}
    for chapter in list(getattr(outline, "chapters", []) or []):
        try:
            chapter_number = int(getattr(chapter, "chapter_number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if chapter_number <= 0:
            continue
        blob = json.dumps(
            _chapter_signature(chapter),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        out[str(chapter_number)] = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return out


def compute_outline_chapter_snapshots(outline: Any) -> dict[str, dict[str, Any]]:
    """Return lightweight chapter outline snapshots used for UI diffs."""
    snapshots: dict[str, dict[str, Any]] = {}
    for chapter in list(getattr(outline, "chapters", []) or []):
        try:
            chapter_number = int(getattr(chapter, "chapter_number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if chapter_number <= 0:
            continue
        hook = getattr(chapter, "expected_hook", None)
        hook_payload: Any = None
        if hook is not None:
            hook_payload = hook.model_dump(mode="json") if hasattr(hook, "model_dump") else hook
        payoffs = getattr(chapter, "expected_payoffs", None)
        if isinstance(payoffs, list):
            payoffs_payload = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in payoffs
            ]
        else:
            payoffs_payload = []
        snapshots[str(chapter_number)] = {
            "chapter_number": chapter_number,
            "title": str(getattr(chapter, "title", "") or ""),
            "goal": str(getattr(chapter, "goal", "") or ""),
            "beats_summary": _normalize_str_list(getattr(chapter, "beats_summary", None)),
            "main_plot_points": _normalize_str_list(getattr(chapter, "main_plot_points", None)),
            "subplot_points": _normalize_str_list(getattr(chapter, "subplot_points", None)),
            "subplot_focus": str(getattr(chapter, "subplot_focus", "") or ""),
            "element_focus": _normalize_str_list(getattr(chapter, "element_focus", None)),
            "pov_character": str(getattr(chapter, "pov_character", "") or ""),
            "setting": str(getattr(chapter, "setting", "") or ""),
            "expected_word_count": int(getattr(chapter, "expected_word_count", 0) or 0),
            "involved_characters": _normalize_str_list(
                getattr(chapter, "involved_characters", None)
            ),
            "time_anchor": str(getattr(chapter, "time_anchor", "") or ""),
            "time_span": str(getattr(chapter, "time_span", "") or ""),
            "time_gap_from_prev": str(getattr(chapter, "time_gap_from_prev", "") or ""),
            "countdown_state": str(getattr(chapter, "countdown_state", "") or ""),
            "expected_hook": hook_payload,
            "expected_payoffs": payoffs_payload,
        }
    return snapshots


def analyze_outline_change_scope(
    current_outline: Any,
    cached_chapter_fingerprints: dict[str, Any] | None,
) -> dict[str, Any]:
    """Analyze per-chapter outline changes without falling back to full-book sync."""
    current = compute_outline_chapter_fingerprints(current_outline)
    cached = {
        str(key): str(value)
        for key, value in (cached_chapter_fingerprints or {}).items()
        if str(key).strip() and str(value).strip()
    }
    current_numbers = {int(key) for key in current}
    cached_numbers = {int(key) for key in cached}
    if not cached:
        return {
            "affected": [],
            "added": sorted(current_numbers),
            "deleted": [],
            "changed": [],
            "requires_manual_scope": True,
            "reason": "missing_chapter_fingerprints",
            "current_chapter_fingerprints": current,
        }

    added = sorted(current_numbers - cached_numbers)
    deleted = sorted(cached_numbers - current_numbers)
    changed = sorted(
        number
        for number in (current_numbers & cached_numbers)
        if current.get(str(number)) != cached.get(str(number))
    )
    affected = sorted(set(added) | set(changed))
    structure_delta = len(added) + len(deleted)
    ratio_floor = max(_STRUCTURAL_CHANGE_MIN_COUNT, int(len(current_numbers) * _STRUCTURAL_CHANGE_RATIO))
    requires_manual_scope = bool(deleted) or structure_delta >= ratio_floor
    reason = ""
    if deleted:
        reason = "chapters_deleted_or_renumbered"
    elif requires_manual_scope:
        reason = "large_outline_structure_change"
    return {
        "affected": affected,
        "added": added,
        "deleted": deleted,
        "changed": changed,
        "requires_manual_scope": requires_manual_scope,
        "reason": reason,
        "current_chapter_fingerprints": current,
    }


def compute_affected_chapters_from_outline_fingerprint(
    current_outline: Any,
    cached_fingerprint: str,
    cached_chapter_fingerprints: dict[str, Any] | None = None,
) -> list[int]:
    """Return chapter numbers whose contract-relevant content changed.

    ``cached_fingerprint`` is the SHA-256 from
    :func:`compute_outline_fingerprint` recorded at the last successful
    contract sync.  When the current fingerprint matches, an empty list is
    returned.  Otherwise, the algorithm diffs the per-chapter signatures to
    identify *which* chapter numbers actually changed.

    The function tolerates a fresh outline (no cache) by returning all
    non-zero chapter numbers.
    """
    if cached_chapter_fingerprints:
        analysis = analyze_outline_change_scope(current_outline, cached_chapter_fingerprints)
        if analysis.get("requires_manual_scope"):
            return []
        return list(analysis.get("affected") or [])
    if not cached_fingerprint:
        return []
    current = compute_outline_fingerprint(current_outline)
    if current == cached_fingerprint:
        return []
    # Legacy aggregate-only cache cannot identify a safe local scope.
    return []


def _all_chapter_numbers(outline: Any) -> list[int]:
    return sorted({
        int(getattr(ch, "chapter_number", 0) or 0)
        for ch in list(getattr(outline, "chapters", []) or [])
        if int(getattr(ch, "chapter_number", 0) or 0) > 0
    })


# ── Cascade detection ────────────────────────────────────────────────────────


def compute_cascade(
    affected: set[int] | list[int],
    chapter_contracts: dict[str, Any],
    *,
    max_depth: int = 3,
) -> set[int]:
    """Return direct downstream chapters referenced by or referencing affected chapters.

    The scope is intentionally non-recursive: chapter N can pull in a later
    chapter M when either N explicitly points forward to M, or M explicitly
    references N in its contract text.  This keeps outline edits local while
    still refreshing direct dependency contracts.

    A chapter already in ``affected`` is never re-added.  Chapters not
    present in the contracts map are still included if referenced — the
    caller decides whether to backfill.
    """
    if not affected or max_depth <= 0:
        return set()
    affected_set = {int(n) for n in affected if int(n or 0) > 0}
    if not affected_set:
        return set()
    contracts_by_number: dict[int, dict[str, Any]] = {}
    raw_items = chapter_contracts.get("chapter_contracts") if isinstance(chapter_contracts, dict) else None
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                n = int(item.get("chapter_number", 0) or 0)
            except (TypeError, ValueError):
                continue
            if n > 0:
                contracts_by_number[n] = item

    cascade: set[int] = set()
    for chapter_number, contract in sorted(contracts_by_number.items()):
        references = _scan_chapter_references(_contract_reference_text(contract))
        if chapter_number in affected_set:
            for referenced in references:
                if referenced > chapter_number and referenced not in affected_set:
                    cascade.add(referenced)
            continue
        if chapter_number <= min(affected_set):
            continue
        if references.intersection(affected_set):
            cascade.add(chapter_number)
    return cascade


def _contract_reference_text(contract: dict[str, Any]) -> str:
    text_chunks: list[str] = []
    for field in _CONTRACT_REFERENCE_FIELDS:
        values = contract.get(field)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str):
                    text_chunks.append(value)
                elif isinstance(value, dict):
                    text_chunks.extend(str(v) for v in value.values() if str(v or "").strip())
        elif isinstance(values, str):
            text_chunks.append(values)
        elif isinstance(values, dict):
            text_chunks.extend(str(v) for v in values.values() if str(v or "").strip())
    return "\n".join(text_chunks)


# ── Merge ────────────────────────────────────────────────────────────────────


def merge_reextracted_contracts(
    original: dict[str, Any],
    reextracted: dict[str, Any],
    focus_numbers: set[int] | Iterable[int],
) -> dict[str, Any]:
    """Splice reextracted contracts for ``focus_numbers`` into ``original``.

    Chapters outside the focus set are preserved verbatim from ``original``.
    Chapters inside the focus set are replaced by the reextracted entries
    (when present).  This mirrors the algorithm of
    ``_merge_repaired_chapter_contracts`` in init_repair.
    """
    focus = {int(n) for n in focus_numbers if int(n or 0) > 0}
    focus_list = sorted(focus)

    def _collect(src: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if not isinstance(src, dict):
            return items
        raw = src.get("chapter_contracts")
        if not isinstance(raw, list):
            return items
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                n = int(item.get("chapter_number", 0) or 0)
            except (TypeError, ValueError):
                continue
            if n <= 0:
                continue
            items.append(item)
        return items

    original_items = _collect(original)
    reextracted_items = _collect(reextracted)

    reextracted_by_number: dict[int, dict[str, Any]] = {}
    for item in reextracted_items:
        n = int(item.get("chapter_number", 0) or 0)
        reextracted_by_number[n] = item

    kept: list[dict[str, Any]] = []
    for item in original_items:
        n = int(item.get("chapter_number", 0) or 0)
        if n in focus:
            continue
        kept.append(item)
    replaced: list[dict[str, Any]] = []
    for n in focus_list:
        if n in reextracted_by_number:
            replaced.append(reextracted_by_number[n])
        else:
            # No reextraction returned for this chapter: preserve the
            # original to avoid silently losing content.
            for item in original_items:
                if int(item.get("chapter_number", 0) or 0) == n:
                    replaced.append(item)
                    break

    merged_items = kept + replaced
    payload: dict[str, Any] = dict(original) if isinstance(original, dict) else {}
    payload["chapter_contracts"] = merged_items
    payload["sync_metadata"] = {
        "synced_chapter_numbers": focus_list,
        "kept_chapter_numbers": sorted({
            int(item.get("chapter_number", 0) or 0)
            for item in kept
        }),
        "replaced_chapter_numbers": [n for n in focus_list if n in reextracted_by_number],
    }
    return payload


def restore_nonfocus_contract_items(
    payload: dict[str, Any],
    original: dict[str, Any],
    focus_numbers: set[int] | Iterable[int],
) -> dict[str, Any]:
    """Restore original contract dicts for non-focus chapters after normalization."""
    focus = {int(n) for n in focus_numbers if int(n or 0) > 0}
    raw_original = original.get("chapter_contracts") if isinstance(original, dict) else None
    raw_items = payload.get("chapter_contracts") if isinstance(payload, dict) else None
    if not isinstance(raw_original, list) or not isinstance(raw_items, list):
        return payload
    originals_by_number: dict[int, dict[str, Any]] = {}
    for item in raw_original:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("chapter_number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in focus:
            originals_by_number[number] = item

    restored: list[Any] = []
    for item in raw_items:
        if not isinstance(item, dict):
            restored.append(item)
            continue
        try:
            number = int(item.get("chapter_number", 0) or 0)
        except (TypeError, ValueError):
            restored.append(item)
            continue
        restored.append(originals_by_number.get(number, item))
    out = dict(payload)
    out["chapter_contracts"] = restored
    return out


# ── Stale marking ────────────────────────────────────────────────────────────


def mark_artifact_stale(
    storage: Any,
    path: Path,
    reason: str,
) -> bool:
    """Annotate a JSON artifact with soft-stale metadata and a reason.

    Returns ``True`` if the file was successfully marked, ``False`` if the
    file did not exist or could not be parsed.  Non-JSON files are skipped
    without raising so the runner can apply this to a heterogeneous set of
    paths.
    """
    path = Path(path)
    if not path.exists():
        return False
    try:
        data = storage.load_json(path)
    except Exception as exc:  # noqa: BLE001 — preserve on-disk content
        _log.debug("mark_artifact_stale: cannot load %s as JSON: %s", path, exc)
        return False
    if not isinstance(data, dict):
        return False
    marked_at = _utc_now_iso()
    data["stale"] = True
    data["soft_stale"] = True
    data["stale_kind"] = "chapter_contract_sync"
    data["stale_reason"] = reason
    data["stale_marked_at"] = marked_at
    data["soft_stale_metadata"] = {
        "kind": "chapter_contract_sync",
        "reason": reason,
        "marked_at": marked_at,
        "requires_regeneration": False,
    }
    try:
        storage.save_json(path, data)
        return True
    except Exception as exc:  # noqa: BLE001
        _log.warning("mark_artifact_stale: failed to write %s: %s", path, exc)
        return False


def _utc_now_iso() -> str:
    """Return an ISO 8601 timestamp in UTC (no microseconds) for stamping."""
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


# ── Stale path collection ────────────────────────────────────────────────────


def collect_downstream_stale_paths(
    layout: ProjectLayout,
    affected: set[int] | Iterable[int],
    cascade: set[int] | Iterable[int],
) -> list[Path]:
    """Return the artifact paths that should be marked stale after a sync.

    The list is intentionally deterministic (sorted, deduped) so both the
    preview UI and the post-run log show the same set in the same order.
    """
    chapter_numbers = sorted({
        int(n) for n in (set(affected) | set(cascade)) if int(n or 0) > 0
    })
    paths: set[Path] = set()
    if not chapter_numbers:
        return []
    for n in chapter_numbers:
        for path in (
            layout.chapter_plan_path(n),
            layout.chapter_bridge_path(n),
            layout.plans_dir / f"chapter_{n:03d}_scene_plan.json",
            layout.reports_dir / f"chapter_{n:03d}_scene_plan_validation.json",
            layout.reports_dir / f"chapter_{n:03d}_guidance_plan.json",
            layout.reports_dir / f"chapter_{n:03d}_guidance_contract_audit.json",
            layout.contract_execution_report_path(n),
            layout.alignment_report_path(n),
            layout.continuity_report_path(n),
            layout.chapter_causal_report_path(n),
            layout.quality_gate_report_path(n),
            layout.chapter_review_progress_path(n),
        ):
            if path.exists():
                paths.add(path)
    # Reports that are project-wide and always considered stale when any
    # chapter was re-extracted.
    coherence_report = layout.reports_dir / "contract_coherence.json"
    if coherence_report.exists():
        paths.add(coherence_report)
    completion_report = layout.reports_dir / "contract_completion.json"
    if completion_report.exists():
        paths.add(completion_report)
    return sorted(paths)


# ── Sync preview ─────────────────────────────────────────────────────────────


def build_sync_preview(
    current_outline: Any,
    chapter_contracts: dict[str, Any],
    cached_fingerprint: str,
    *,
    cascade_downstream: bool = True,
    max_cascade_depth: int = 3,
    cached_chapter_fingerprints: dict[str, Any] | None = None,
    explicit_affected: Iterable[int] | None = None,
) -> dict[str, Any]:
    """Assemble a preview payload for the UI confirmation dialog.

    The payload is JSON-serializable and contains everything the widget
    needs to show a confirmation dialog and (optionally) a diff preview:

    * ``affected`` — chapter numbers whose outline content changed
    * ``cascade`` — additional downstream chapter numbers discovered
    * ``focus`` — union of affected + cascade (chapters that will be sent
      to the LLM for re-extraction)
    * ``affected_contract_items`` — old contract entries for the focus set
    * ``existing_prose_chapters`` — chapters that already have prose; the
      sync leaves their text untouched but flags them in the UI
    """
    explicit_set = {
        int(number)
        for number in (explicit_affected or [])
        if int(number or 0) > 0
    }
    analysis = analyze_outline_change_scope(current_outline, cached_chapter_fingerprints)
    legacy_fingerprint_matches = (
        not cached_chapter_fingerprints
        and bool(cached_fingerprint)
        and compute_outline_fingerprint(current_outline) == cached_fingerprint
    )
    if explicit_set:
        affected_list = sorted(explicit_set)
        requires_manual_scope = False
        manual_reason = ""
    elif legacy_fingerprint_matches:
        affected_list = []
        requires_manual_scope = False
        manual_reason = ""
    else:
        affected_list = compute_affected_chapters_from_outline_fingerprint(
            current_outline,
            cached_fingerprint,
            cached_chapter_fingerprints,
        )
        requires_manual_scope = bool(analysis.get("requires_manual_scope"))
        manual_reason = str(analysis.get("reason") or "")
    affected_set = set(affected_list)
    cascade_set: set[int] = set()
    if cascade_downstream and affected_set and not requires_manual_scope:
        cascade_set = compute_cascade(
            affected_set,
            chapter_contracts,
            max_depth=max_cascade_depth,
        )
    focus_set = affected_set | cascade_set
    focus_list = sorted(focus_set)
    affected_contract_items = _lookup_contract_items(chapter_contracts, focus_set)
    return {
        "affected": sorted(affected_set),
        "cascade": sorted(cascade_set),
        "focus": focus_list,
        "affected_contract_items": affected_contract_items,
        "requires_manual_scope": requires_manual_scope,
        "manual_scope_reason": manual_reason,
        "scope_analysis": {
            "added": analysis.get("added") or [],
            "deleted": analysis.get("deleted") or [],
            "changed": analysis.get("changed") or [],
        },
        "fingerprint": {
            "current": compute_outline_fingerprint(current_outline),
            "cached": cached_fingerprint,
            "chapters": analysis.get("current_chapter_fingerprints") or {},
        },
    }


def _lookup_contract_items(
    chapter_contracts: dict[str, Any],
    chapter_numbers: Iterable[int],
) -> list[dict[str, Any]]:
    wanted = {int(n) for n in chapter_numbers if int(n or 0) > 0}
    if not wanted:
        return []
    out: list[dict[str, Any]] = []
    raw_items = chapter_contracts.get("chapter_contracts") if isinstance(chapter_contracts, dict) else None
    if not isinstance(raw_items, list):
        return out
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("chapter_number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if n in wanted:
            out.append(item)
    return sorted(out, key=lambda item: int(item.get("chapter_number", 0) or 0))


# ── Backup helpers (lightweight; for resume/undo) ────────────────────────────


def backup_artifact(storage: Any, path: Path, *, backup_root: Path) -> Path | None:
    """Copy ``path`` to ``backup_root`` if it exists.  Returns the backup path or None.

    The backup is a plain ``cp``-style copy (not atomic) because the caller
    must already hold the project lock.  JSON content is preserved
    byte-for-byte so the rollback is bit-exact.
    """
    path = Path(path)
    if not path.exists():
        return None
    backup_root = Path(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = backup_root / path.name
    try:
        data = path.read_bytes()
        atomic_write_text(backup_path, data.decode("utf-8"), encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _log.warning("backup_artifact: failed to copy %s -> %s: %s", path, backup_path, exc)
        return None
    return backup_path


__all__ = [
    "analyze_outline_change_scope",
    "backup_artifact",
    "build_sync_preview",
    "collect_downstream_stale_paths",
    "compute_affected_chapters_from_outline_fingerprint",
    "compute_cascade",
    "compute_outline_chapter_fingerprints",
    "compute_outline_chapter_snapshots",
    "compute_outline_fingerprint",
    "mark_artifact_stale",
    "merge_reextracted_contracts",
    "restore_nonfocus_contract_items",
]
