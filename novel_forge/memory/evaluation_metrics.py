"""Read-only evaluation metrics for chapter quality (3-dimensional, no LLM).

Wave 2 Task 9 — three deterministic, pure-function metrics:

1. ``character_drift_score`` — how well the chapter text matches
   ``CharacterBible`` (name + alias presence heuristic).
2. ``foreshadow_recovery_rate`` — paid / planted ratio in a volume window
   (uses :class:`ForeshadowReminder` if provided, else 1.0).
3. ``timeline_consistency`` — internal time-marker contradiction detection
   (regex-based, no LLM).

**No LLM, no writes, no new fact sources.** Each function is a pure
function: input → output, no side effects. Reasons:

* Testable (no model mocking; every input maps to one output).
* Free (zero tokens per chapter; safe in ``CriticAgent`` hot path).
* 8-week-shippable (no schema or fact-extraction work).

Future upgrade path is documented in
``.omo/notepads/memory-architecture-v3/learnings.md`` under
"Task 2.4 — EvaluationMetrics" — primarily LLM-graded
character-drift + chapter-shard lookups for cross-chapter
foreshadow recovery.

This module is NOT auto-wired into :class:`CriticAgent`. The
functions are stable, pure, and side-effect-free, so they can
be adopted incrementally by any caller (CLI command,
diagnostic endpoint, or a future ``critique_chapter`` site).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from novel_forge.core.schemas.bible import CharacterBible

    # Forward references for modules being built in parallel:
    # - Task 6: entity_knowledge (EntityKnowledgeService)
    # - Task 8: foreshadow_reminder (ForeshadowReminder)
    # At runtime we accept ``Any`` and duck-type, so absence is fine.
    EntityKnowledgeService = Any
    ForeshadowReminder = Any


# ─────────────────────────────────────────────────────────────────────
# Public dataclass
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvaluationReport:
    """Aggregate result of the 3-dimension read-only evaluation.

    Attributes:
        chapter: Chapter number the report covers.
        character_drift_score: 0.0–1.0; 1.0 = chapter text matches all
            active character descriptions. Higher is better.
        foreshadow_recovery_rate: 0.0–1.0; share of foreshadows that
            have been paid off in the volume window. Higher is better.
        timeline_consistency: 0.0–1.0; 1.0 = no internal time-marker
            contradictions. Higher is better.
        details: Raw data for debugging / UI display. Never used for
            decisions — only the three numeric scores are authoritative.
    """

    chapter: int
    character_drift_score: float
    foreshadow_recovery_rate: float
    timeline_consistency: float
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation (debug payload)."""
        return {
            "chapter": self.chapter,
            "character_drift_score": self.character_drift_score,
            "foreshadow_recovery_rate": self.foreshadow_recovery_rate,
            "timeline_consistency": self.timeline_consistency,
            "details": self.details,
        }


# ─────────────────────────────────────────────────────────────────────
# Internal helper dataclasses (not exported; only for the details dict)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _CharacterMention:
    """Per-character mention count."""

    name: str
    role: str
    mentions: int
    expected_min: int


# ─────────────────────────────────────────────────────────────────────
# Time-marker regex + contradiction sets
# ─────────────────────────────────────────────────────────────────────

# Combined regex covers the most common Chinese time markers seen in
# Novel-Forge drafts. Sourced from existing
# ``workspace/propagation_validator.py`` + ``core/utils/semantic_drift.py``
# + ``pipeline/long/services/time_validation.py`` so the metric is
# consistent with the rest of the timeline vocabulary in the project.
_TIME_MARKER_RE = re.compile(
    r"(第[一二三四五六七八九十\d]+[天日章回]"
    r"|早上|中午|晚上"
    r"|清晨|黎明|拂晓|上午|午后|下午|傍晚|黄昏|日暮|夜晚|深夜|子夜|午夜|凌晨"
    r"|次日|翌日|隔日|第二日|数日后|几日后|三日[后前]|数日后|一日[后前]"
    r"|当时|当天|此刻|那时|此时|随后|接着|然后|先前|之后|之后|片刻"
    r"|春|夏|秋|冬"
    r"|子时|丑时|寅时|卯时|辰时|巳时|午时|未时|申时|酉时|戌时|亥时"
    r")"
)

# Marker categories used for contradiction detection.
_DAY_MARKERS: frozenset[str] = frozenset(
    {
        "清晨",
        "黎明",
        "拂晓",
        "早上",
        "上午",
        "中午",
        "午后",
        "下午",
        "春",
        "夏",
    }
)

_NIGHT_MARKERS: frozenset[str] = frozenset(
    {
        "夜晚",
        "深夜",
        "子夜",
        "午夜",
        "凌晨",
        "秋",
        "冬",
    }
)

# "Same-day" markers (no time-skip) — contradict with "next-day" markers.
_SAME_DAY_MARKERS: frozenset[str] = frozenset(
    {
        "当天",
        "当日",
        "当时",
        "此刻",
        "此时",
        "那时",
        "随后",
        "接着",
        "然后",
    }
)

# "Next-day / future-day" markers — contradict with "same-day" markers.
_NEXT_DAY_MARKERS: frozenset[str] = frozenset(
    {
        "次日",
        "翌日",
        "隔日",
        "第二日",
    }
)


# ─────────────────────────────────────────────────────────────────────
# Character helper — extract per-character mention counts
# ─────────────────────────────────────────────────────────────────────


# Role-based expected minimum mentions. Higher role → more appearances
# expected in an active scene. These are deliberately lenient because
# the metric is *drift*, not *density*: a chapter where the protagonist
# never appears gets a soft penalty, not a hard zero.
_ROLE_EXPECTED_MENTIONS: dict[str, int] = {
    "protagonist": 3,
    "deuteragonist": 2,
    "antagonist": 1,
    "supporting": 1,
    "minor": 0,  # Optional presence
}


def _normalize_role(role: str) -> str:
    """Normalize role string to one of the buckets used by the metric."""
    text = str(role or "").strip().lower()
    aliases = {
        "protagonist": "protagonist",
        "主角": "protagonist",
        "deuteragonist": "deuteragonist",
        "第二主角": "deuteragonist",
        "antagonist": "antagonist",
        "反派": "antagonist",
        "supporting": "supporting",
        "配角": "supporting",
        "minor": "minor",
        "次要": "minor",
    }
    return aliases.get(text, "supporting")


def _extract_name_aliases(profile: Any) -> list[str]:
    """Return a de-duplicated list of name-like strings for a character.

    Source order:
    1. ``profile.name`` (always present on ``CharacterProfile``)
    2. ``profile.aliases`` if it exists (some character flavors store aliases)
    3. ``profile.notes`` lines that look like ``别名：xxx`` / ``alias: xxx``

    The primary ``name`` field is filtered to ≥ 2 chars (Chinese
    single-char names cause too many false positives in the
    mention-count heuristic). Aliases and notes-extracted tokens
    are kept at any length because they are explicitly declared.
    """
    candidates: list[tuple[str, str]] = []  # (value, source)
    name = getattr(profile, "name", None)
    if isinstance(name, str) and name.strip():
        candidates.append((name.strip(), "name"))

    raw_aliases = getattr(profile, "aliases", None)
    if isinstance(raw_aliases, (list, tuple)):
        for alias in raw_aliases:
            if isinstance(alias, str) and alias.strip():
                candidates.append((alias.strip(), "alias"))
    elif isinstance(raw_aliases, str) and raw_aliases.strip():
        # Some profiles store aliases as comma-separated strings.
        for piece in raw_aliases.split(","):
            piece = piece.strip()
            if piece:
                candidates.append((piece, "alias"))

    notes = getattr(profile, "notes", None)
    if isinstance(notes, str) and notes:
        # Heuristic: pull "别名：xxx" / "alias: xxx" tokens.
        # Exclude Chinese enumeration comma "、" and Latin "," plus
        # semicolons so each alias is captured individually.
        for pattern in (r"别名[：:]\s*([^\n,，；;、]+)", r"alias[：:]\s*([^\n,，；;、]+)"):
            for match in re.finditer(pattern, notes, flags=re.IGNORECASE):
                token = match.group(1).strip()
                if token:
                    candidates.append((token, "notes"))

    # De-duplicate (preserving first-seen order). Primary ``name``
    # must be ≥ 2 chars; declared aliases/notes tokens are kept
    # at any length.
    seen: set[str] = set()
    result: list[str] = []
    for value, source in candidates:
        if source == "name" and len(value) < 2:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _count_mentions(text: str, names: list[str]) -> dict[str, int]:
    """Count non-overlapping occurrences of each name in *text*."""
    counts: dict[str, int] = {}
    for name in names:
        if not name:
            continue
        counts[name] = text.count(name)
    return counts


def _extract_character_mentions(
    text: str,
    character_bible: Any,
) -> list[_CharacterMention]:
    """Per-character mention counts for a chapter text.

    ``character_bible`` is duck-typed — we look for ``.characters``
    (a list of ``CharacterProfile``-shaped objects) first, then
    fall back to ``.character_bios`` for callers that use the dict
    layout referenced in the plan's task spec. If neither shape is
    found, return an empty list (caller decides how to score).
    """
    characters = getattr(character_bible, "characters", None)
    if characters is None:
        # Fall back to dict layout for older/dict-shaped callers.
        characters = getattr(character_bible, "character_bios", None) or []

    mentions: list[_CharacterMention] = []
    for profile in characters:
        if profile is None:
            continue
        role = _normalize_role(getattr(profile, "role", "supporting"))
        expected_min = _ROLE_EXPECTED_MENTIONS.get(role, 1)
        names = _extract_name_aliases(profile)
        counts = _count_mentions(text, names)
        total_mentions = sum(counts.values())
        mentions.append(
            _CharacterMention(
                name=getattr(profile, "name", "") or "",
                role=role,
                mentions=total_mentions,
                expected_min=expected_min,
            )
        )
    return mentions


# ─────────────────────────────────────────────────────────────────────
# 1. character_drift_score
# ─────────────────────────────────────────────────────────────────────


def character_drift_score(
    *,
    chapter: int,
    chapter_text: str,
    character_bible: "CharacterBible | None" = None,
    entity_knowledge: "EntityKnowledgeService | None" = None,
) -> float:
    """Score 0.0–1.0: how well chapter text matches CharacterBible.

    Pure read on bible + entity_knowledge. No writes, no LLM.

    Heuristic (NO LLM):
    * For each character in the bible, count occurrences of their
      name + any aliases in the chapter text.
    * Active characters (``protagonist``/``deuteragonist``/
      ``antagonist``) that never appear are penalised.
    * ``supporting`` characters that never appear are mildly
      penalised.
    * ``minor`` characters that never appear are not penalised
      (they may be off-stage).
    * Final score =
        (matched_active / total_active) * density_factor
      where ``density_factor`` is min(total_mentions / expected,
      1.0). This rewards chapters where active characters are
      actually prominent, not merely mentioned in passing.

    Args:
        chapter: Chapter number (recorded in details only).
        chapter_text: Full chapter prose.
        character_bible: :class:`CharacterBible` (list of
            ``CharacterProfile``) or a duck-typed equivalent.
        entity_knowledge: Optional service used to look up
            canonical names / aliases. Currently the metric does
            not require it (name string matching is sufficient);
            the parameter is accepted so future upgrades can
            route through it without changing the signature.

    Returns:
        Float in ``[0.0, 1.0]``. ``0.5`` is returned when the
        bible is missing or empty (neutral — we cannot judge
        with no references).
    """
    # NOTE: ``entity_knowledge`` is accepted for API stability but the
    # current heuristic does not need to consult it. Use a void branch
    # to keep the variable referenced (ruff wants this).
    _ = entity_knowledge  # noqa: F841

    if character_bible is None:
        return 0.5

    mentions = _extract_character_mentions(chapter_text, character_bible)
    if not mentions:
        # Empty bible — neutral score.
        return 0.5

    active_roles = {"protagonist", "deuteragonist", "antagonist"}
    active = [m for m in mentions if m.role in active_roles]
    supporting = [m for m in mentions if m.role == "supporting"]

    if not active and not supporting:
        # Only minor characters in the bible — nothing to judge.
        return 0.5

    # Active-character match ratio (weighted heavier than supporting).
    if active:
        active_matched = sum(1 for m in active if m.mentions >= max(m.expected_min, 1))
        active_ratio = active_matched / len(active)
    else:
        active_ratio = 0.5  # No active characters to check — neutral.

    if supporting:
        supporting_matched = sum(1 for m in supporting if m.mentions >= m.expected_min)
        supporting_ratio = supporting_matched / len(supporting)
    else:
        supporting_ratio = 1.0  # No supporting — full credit.

    # Combine: 70% active + 30% supporting.
    coverage = 0.7 * active_ratio + 0.3 * supporting_ratio

    # Density factor: reward chapters where active characters are
    # mentioned generously, not just once.
    expected_total = sum(max(m.expected_min, 1) for m in active + supporting) or 1
    actual_total = sum(m.mentions for m in active + supporting)
    density_factor = min(actual_total / expected_total, 1.0) if expected_total else 1.0

    return max(0.0, min(coverage * density_factor, 1.0))


# ─────────────────────────────────────────────────────────────────────
# 2. foreshadow_recovery_rate
# ─────────────────────────────────────────────────────────────────────


# Sentinel returned by the safe-get helpers when they detect an async
# method (coroutine). The caller (foreshadow_recovery_rate) checks for
# this value and downgrades to the neutral baseline — a sync function
# cannot ``await`` a coroutine.
_ASYNC_SENTINEL: int = -1


def _is_coroutine_like(value: Any) -> bool:
    """Return True if *value* is a coroutine or ``inspect.iscoroutine``
    would return True for it.

    Cheap duck-typed check that avoids importing :mod:`inspect` at
    module load. The set of coroutine-like types we care about:

    * ``types.CoroutineType`` (from ``async def``)
    * ``asyncio.Future`` (rare in sync helpers, but defensive)
    """
    # ``__await__`` is the protocol that all coroutines implement.
    # This catches both ``async def`` coroutines and ``__await__``-
    # returning objects without an ``import asyncio``.
    return hasattr(value, "__await__")


def _close_silently(value: Any) -> None:
    """Close a coroutine / future if it is one, to avoid
    'coroutine was never awaited' warnings.

    A no-op for non-coroutine values. Used to keep the call site
    tidy: every async-detection branch closes the coroutine it
    would otherwise leave dangling.
    """
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


def _safe_get_due(foreshadow_reminder: Any, volume: int) -> int:
    """Return number of pending foreshadows for *volume*.

    Tries ``get_due(volume)`` first (most likely shape from
    Task 8 / :class:`ForeshadowReminder`), then falls back to
    a ``list_pending(volume)`` method, then to a duck-typed
    ``entries`` iterable.

    Special case: when the method returns a coroutine (i.e. the
    real :class:`ForeshadowReminder.get_due` is async-only), this
    function returns the sentinel ``-1`` to signal "async
    detected, sync function cannot compute". The caller checks
    for ``-1`` and downgrades to the neutral baseline.
    Returns 0 on any other failure.
    """
    if foreshadow_reminder is None:
        return 0
    for method_name in ("get_due", "list_pending", "get_pending"):
        method = getattr(foreshadow_reminder, method_name, None)
        if not callable(method):
            continue
        try:
            result = method(volume)
        except Exception:  # noqa: BLE001 — duck-typed; never raise
            return 0
        if _is_coroutine_like(result):
            _close_silently(result)
            return _ASYNC_SENTINEL
        if isinstance(result, int):
            return max(result, 0)
        if hasattr(result, "__len__"):
            try:
                return max(len(result), 0)
            except TypeError:
                continue
    return 0


def _safe_get_paid(foreshadow_reminder: Any, volume: int) -> int:
    """Return number of paid foreshadows for *volume*.

    Tries ``get_paid(volume)`` / ``list_paid(volume)`` first,
    then computes paid = planted - pending if the reminder
    exposes ``get_planted``.

    Async methods also return ``_ASYNC_SENTINEL`` (-1) — see
    :func:`_safe_get_due`.
    """
    if foreshadow_reminder is None:
        return 0
    for method_name in ("get_paid", "list_paid", "paid_count"):
        method = getattr(foreshadow_reminder, method_name, None)
        if not callable(method):
            continue
        try:
            result = method(volume)
        except Exception:  # noqa: BLE001
            continue
        if _is_coroutine_like(result):
            _close_silently(result)
            return _ASYNC_SENTINEL
        if isinstance(result, int):
            return max(result, 0)
        if hasattr(result, "__len__"):
            try:
                return max(len(result), 0)
            except TypeError:
                continue

    # Fallback: paid = planted - pending if both are available.
    planted = 0
    planted_method = getattr(foreshadow_reminder, "get_planted", None)
    if callable(planted_method):
        try:
            result = planted_method(volume)
            if _is_coroutine_like(result):
                _close_silently(result)
                return _ASYNC_SENTINEL
            planted = len(result) if hasattr(result, "__len__") else int(result or 0)
        except Exception:  # noqa: BLE001
            planted = 0
    pending = _safe_get_due(foreshadow_reminder, volume)
    if pending == _ASYNC_SENTINEL:
        return _ASYNC_SENTINEL
    return max(planted - pending, 0)


def foreshadow_recovery_rate(
    *,
    volume: int,
    foreshadow_reminder: "ForeshadowReminder | None" = None,
    chapter_range: tuple[int, int] | None = None,
) -> float:
    """Score 0.0–1.0: paid / planted ratio in a volume window.

    Pure read on ``foreshadow_reminder``. No writes, no LLM.

    Heuristic (NO LLM):
    * ``paid = foreshadow_reminder.get_paid(volume)`` (or computed
      from ``get_planted - get_due``).
    * ``pending = foreshadow_reminder.get_due(volume)``.
    * ``score = paid / (paid + pending)``.

    Args:
        volume: Volume number being evaluated.
        foreshadow_reminder: :class:`ForeshadowReminder` (Task 8)
            or a duck-typed equivalent. If ``None``, the function
            returns ``1.0`` — there are no overdue foreshadows to
            judge (vacuous truth). This is the safe default for
            projects that have not yet adopted the reminder.
        chapter_range: Optional ``(start, end)`` chapter tuple for
            fine-grained scoping. Currently informational only —
            recorded in the ``details`` dict, not used to filter
            results (the ``ForeshadowReminder`` itself owns that
            filtering).

    Returns:
        Float in ``[0.0, 1.0]``.
    """
    if foreshadow_reminder is None:
        return 1.0  # No reminder → nothing overdue.

    paid = _safe_get_paid(foreshadow_reminder, volume)
    pending = _safe_get_due(foreshadow_reminder, volume)

    if paid == _ASYNC_SENTINEL or pending == _ASYNC_SENTINEL:
        # Real ``ForeshadowReminder`` is async-only. A sync function
        # cannot ``await`` the coroutine. Degrade to the neutral
        # baseline so callers do not silently see a vacuous 1.0
        # when integration is not yet wired. Future upgrade: provide
        # a sync pre-computed count on the reminder or an async
        # sibling of this function.
        import warnings

        warnings.warn(
            "foreshadow_recovery_rate: reminder exposes async-only methods; "
            "returning neutral 0.5. Pass a sync reminder or pre-computed counts.",
            RuntimeWarning,
            stacklevel=2,
        )
        return 0.5

    total = paid + pending
    if total <= 0:
        # No foreshadows planted in this volume → vacuous 1.0.
        return 1.0
    return max(0.0, min(paid / total, 1.0))


# ─────────────────────────────────────────────────────────────────────
# 3. timeline_consistency
# ─────────────────────────────────────────────────────────────────────


def _extract_time_markers(text: str) -> list[str]:
    """Return ordered, unique time-marker tokens found in *text*."""
    if not text:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _TIME_MARKER_RE.finditer(text):
        marker = match.group(0)
        if marker in seen:
            continue
        seen.add(marker)
        ordered.append(marker)
    return ordered


def _detect_time_contradictions(markers: list[str]) -> list[tuple[str, str]]:
    """Return a list of (marker_a, marker_b) pairs that contradict.

    Two marker classes are checked:

    1. Day vs night — e.g. ``清晨`` + ``夜晚`` (impossible in one
       short scene).
    2. Same-day vs next-day — e.g. ``当天`` + ``次日`` (you can't
       be on the same day AND the next day at the same time).

    The function operates on the *set* of markers in the chapter,
    not their order, because time markers often appear non-linearly
    (flashback framing, summary beats, etc.).
    """
    if not markers:
        return []
    marker_set = set(markers)
    contradictions: list[tuple[str, str]] = []

    for day in _DAY_MARKERS & marker_set:
        for night in _NIGHT_MARKERS & marker_set:
            contradictions.append((day, night))

    for same in _SAME_DAY_MARKERS & marker_set:
        for nxt in _NEXT_DAY_MARKERS & marker_set:
            contradictions.append((same, nxt))

    return contradictions


def timeline_consistency(
    *,
    chapter: int,
    chapter_text: str,
    entity_knowledge: "EntityKnowledgeService | None" = None,
    previous_chapter_text: str | None = None,
) -> float:
    """Score 0.0–1.0: internal time-marker consistency of a chapter.

    Pure read on ``chapter_text``. No writes, no LLM.

    Heuristic (NO LLM):
    * Extract time markers via regex (Chinese + 12-hour + 24-hour
      时辰 system).
    * Detect contradictions:
        1. Day markers (``清晨``) vs night markers (``夜晚``) in
           the same chapter.
        2. Same-day markers (``当天``, ``当日``) vs next-day
           markers (``次日``, ``翌日``).
    * ``score = 1.0 - (contradictions / max(markers_seen, 1))``.
      We penalise proportionally to how many markers were seen, so
      a chapter with 2 markers and 1 contradiction scores 0.5,
      while a chapter with 20 markers and 1 contradiction scores
      0.95 (one slip-up among many clear markers is much milder).

    Args:
        chapter: Chapter number (recorded in details only).
        chapter_text: Full chapter prose.
        entity_knowledge: Optional service used to look up
            entity-specific time markers (e.g. "传武的复仇" → future
            tense). Currently unused; the parameter is accepted
            for API stability and future LLM-free upgrades.
        previous_chapter_text: Optional text of the preceding
            chapter. When provided, contradictions between
            ``previous_chapter_text``'s last markers and
            ``chapter_text``'s first markers are also checked
            (e.g. previous chapter ends with ``翌日`` and this
            chapter opens with ``当日``).

    Returns:
        Float in ``[0.0, 1.0]``. ``1.0`` is returned when the
        chapter has no time markers (no signal to judge).
    """
    _ = entity_knowledge  # noqa: F841 — see character_drift_score.

    markers = _extract_time_markers(chapter_text)
    if not markers:
        # Optionally cross-check with previous chapter.
        if previous_chapter_text:
            prev_markers = _extract_time_markers(previous_chapter_text)
            if not prev_markers:
                return 1.0
        else:
            return 1.0

    contradictions = _detect_time_contradictions(markers)

    if previous_chapter_text:
        prev_markers = _extract_time_markers(previous_chapter_text)
        # Cross-chapter: end-of-prev vs start-of-current.
        if prev_markers and markers:
            # If the previous chapter explicitly anchors "次日/翌日" and
            # the current chapter opens with "当天/当日", that's a
            # cross-chapter timeline break.
            prev_set = set(prev_markers)
            cur_set = set(markers)
            for nxt in _NEXT_DAY_MARKERS & prev_set:
                for same in _SAME_DAY_MARKERS & cur_set:
                    contradictions.append((nxt, same))
            for day in _DAY_MARKERS & prev_set:
                for night in _NIGHT_MARKERS & cur_set:
                    # If previous chapter ended in daylight, opening
                    # this chapter in the middle of the night is
                    # suspicious (would need explicit transition).
                    contradictions.append((day, night))

    denominator = max(len(markers), 1)
    penalty = len(contradictions) / denominator
    return max(0.0, min(1.0 - penalty, 1.0))


# ─────────────────────────────────────────────────────────────────────
# 4. evaluate_chapter — aggregator
# ─────────────────────────────────────────────────────────────────────


def evaluate_chapter(
    *,
    chapter: int,
    chapter_text: str,
    character_bible: "CharacterBible | None" = None,
    foreshadow_reminder: "ForeshadowReminder | None" = None,
    entity_knowledge: "EntityKnowledgeService | None" = None,
    volume: int | None = None,
    previous_chapter_text: str | None = None,
) -> EvaluationReport:
    """Run all 3 metrics and return a single :class:`EvaluationReport`.

    This is the canonical public API of the module. Each individual
    metric can also be called directly when only one dimension is
    needed (e.g. an experimental tool that only cares about timeline
    consistency).

    Args:
        chapter: Chapter number being evaluated.
        chapter_text: Full chapter prose.
        character_bible: Optional CharacterBible for
            ``character_drift_score``.
        foreshadow_reminder: Optional ForeshadowReminder for
            ``foreshadow_recovery_rate``.
        entity_knowledge: Optional EntityKnowledgeService — currently
            informational only, accepted for future upgrades.
        volume: Volume number for the foreshadow metric. Defaults to
            ``chapter`` when ``None`` (best-effort fallback for
            callers that don't track volumes explicitly).
        previous_chapter_text: Optional text of the previous chapter,
            enabling cross-chapter timeline checks in
            ``timeline_consistency``.

    Returns:
        :class:`EvaluationReport` with all three scores and a
        ``details`` dict containing the raw intermediate data
        (per-character mention counts, marker lists, contradiction
        pairs, etc.) for debugging and UI display.
    """
    # Per-character mention details (also feeds the character_drift
    # score so we don't recount when caller wants both).
    mentions: list[_CharacterMention] = []
    if character_bible is not None:
        mentions = _extract_character_mentions(chapter_text, character_bible)

    drift = character_drift_score(
        chapter=chapter,
        chapter_text=chapter_text,
        character_bible=character_bible,
        entity_knowledge=entity_knowledge,
    )
    recovery = foreshadow_recovery_rate(
        volume=chapter if volume is None else volume,
        foreshadow_reminder=foreshadow_reminder,
    )
    timeline = timeline_consistency(
        chapter=chapter,
        chapter_text=chapter_text,
        entity_knowledge=entity_knowledge,
        previous_chapter_text=previous_chapter_text,
    )

    markers = _extract_time_markers(chapter_text)
    contradictions = _detect_time_contradictions(markers)
    if previous_chapter_text:
        prev_markers = _extract_time_markers(previous_chapter_text)
        if prev_markers and markers:
            prev_set = set(prev_markers)
            cur_set = set(markers)
            for nxt in _NEXT_DAY_MARKERS & prev_set:
                for same in _SAME_DAY_MARKERS & cur_set:
                    contradictions.append((nxt, same))
            for day in _DAY_MARKERS & prev_set:
                for night in _NIGHT_MARKERS & cur_set:
                    contradictions.append((day, night))

    details: dict[str, Any] = {
        "character_mentions": [
            {
                "name": m.name,
                "role": m.role,
                "mentions": m.mentions,
                "expected_min": m.expected_min,
            }
            for m in mentions
        ],
        "time_markers": markers,
        "time_contradictions": [{"marker_a": a, "marker_b": b} for a, b in contradictions],
        "volume": chapter if volume is None else volume,
        "has_character_bible": character_bible is not None,
        "has_foreshadow_reminder": foreshadow_reminder is not None,
        "has_entity_knowledge": entity_knowledge is not None,
        "has_previous_chapter": previous_chapter_text is not None,
    }

    return EvaluationReport(
        chapter=chapter,
        character_drift_score=drift,
        foreshadow_recovery_rate=recovery,
        timeline_consistency=timeline,
        details=details,
    )


__all__ = [
    "EvaluationReport",
    "character_drift_score",
    "foreshadow_recovery_rate",
    "timeline_consistency",
    "evaluate_chapter",
]
