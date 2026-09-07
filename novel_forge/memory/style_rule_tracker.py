"""Cross-chapter style-rule repetition tracker.

The :class:`MotifTracker` covers cross-chapter repetition of *imagery/motifs*
(意象/动作/感官/颜色/声音/主题/符号). It does **not** cover the executable
writing-technique rules declared in ``style_profile.modules`` (e.g. "风的三层
写法", "半句话语法"). Those rules are checked only within a single chapter by
``style_metrics.compute_style_metrics``.

This tracker closes that gap: it records which style-rule "fingerprints"
triggered in each chapter and flags rules that fire too frequently across a
lookback window — the signature of a writing technique calcifying into a
template (the "穿堂风描写模板化" failure mode). Detection is deterministic and
rule-id based (no LLM extraction, no vectorization) because the rule set is
small (≤ 5 modules × 5 rules = 25 fingerprints) and fully known up front.

The public contract mirrors ``MotifTracker`` so the tracker slots into the same
CriticAgent / AuditCoordinator integration points without new plumbing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from novel_forge.obs.logger import get_logger

_log = get_logger(__name__)

# A style-rule "category". Unlike motifs, technique rules are always eligible
# for hard repetition forbid — calcified technique templates are exactly what
# we want to catch, not soft-suggest around.
STYLE_RULE_CATEGORY = "技法"

# Default tuning (mirrors MotifTracker defaults where sensible).
DEFAULT_LOOKBACK_CHAPTERS = 5
DEFAULT_REPETITION_GAP_CHAPTERS = 2
DEFAULT_PROMPT_LOOKBACK_CHAPTERS = 2
DEFAULT_PROMPT_MAX_RULES = 5
_DEFAULT_REPEATED_PHRASE_MIN_LENGTH = 4
_DEFAULT_REPEATED_PHRASE_MIN_OCCURRENCES = 2


def _slugify(name: str) -> str:
    """Stable rule id from a module/rule name pair (module-scoped)."""
    cleaned = re.sub(r"\s+", "", str(name or ""))
    return cleaned or "rule"


@dataclass
class StyleRule:
    """A tracked writing-technique rule fingerprint.

    Fields mirror the subset of ``Motif`` needed for repetition detection:
    a stable id, the human-readable text, and per-rule usage statistics.
    """

    rule_id: str
    module_name: str
    rule_text: str
    positive_example: str = ""
    negative_example: str = ""
    # Cues extracted from rule_text / examples used to detect a trigger in
    # chapter prose. Stored as a sorted unique list.
    cues: list[str] = field(default_factory=list)
    occurrence_count: int = 0
    first_appearance_chapter: int = 0
    last_appearance_chapter: int = 0
    retired: bool = False


@dataclass
class RuleRepetitionWarning:
    """A cross-chapter style-rule repetition finding.

    Mirrors ``RepetitionWarning`` so downstream consumers (CriticAgent issue
    construction) can treat both uniformly.
    """

    rule_name: str
    module_name: str
    chapter_number: int
    previous_chapters: list[int]
    similarity_score: float
    text_snippet: str = ""
    suggestion: str = ""
    severity: str = "medium"


def _extract_cues(*texts: str, min_len: int = 3, max_len: int = 16) -> list[str]:
    """Extract distinctive cue phrases from rule text / examples.

    Reuses the n-gram idea from ``style_metrics._find_repeated_phrases`` but
    adapted to short rule strings: keep quoted spans and ≥3-char runs split on
    punctuation. Cues are the trigger vocabulary the tracker searches for in
    chapter prose to decide whether a rule "fired" this chapter.
    """
    cues: list[str] = []
    seen: set[str] = set()
    for text in texts:
        cleaned = re.sub(r"\s+", "", str(text or ""))
        if not cleaned:
            continue
        # Quoted spans first (most distinctive): 「…」 "…" 『…』 《…》
        for quoted in re.findall(r"[「\"『《<〈](.*?)[」\"』》>〉]", cleaned):
            q = quoted.strip()
            if min_len <= len(q) <= max_len:
                if q not in seen:
                    seen.add(q)
                    cues.append(q)
        # Then punctuation-split runs.
        for part in re.split(r"[，。、；：（）【】\s,;:(){}\[\]]+", cleaned):
            p = part.strip()
            if min_len <= len(p) <= max_len and not p.isdigit() and p not in seen:
                seen.add(p)
                cues.append(p)
    return cues


class StyleRuleTracker:
    """Tracks cross-chapter repetition of ``style_profile.modules`` rules.

    Lifecycle:
      1. ``load_from_style_profile(style_profile)`` builds the fingerprint table
         once from the project's ``ProjectStyleProfile`` (≤ 25 rules).
      2. ``register_chapter(chapter_number, chapter_text)`` detects which rule
         cues appear in the chapter prose and records their usage.
      3. ``check_rule_repetition(...)`` flags rules firing too often in the
         lookback window.
      4. ``get_rules_for_prompt(...)`` projects active rules + forbidden
         repetition into writing prompts.
    """

    def __init__(
        self,
        lookback_chapters: int = DEFAULT_LOOKBACK_CHAPTERS,
        repetition_gap_chapters: int = DEFAULT_REPETITION_GAP_CHAPTERS,
    ) -> None:
        self._rules: dict[str, StyleRule] = {}
        # rule_id -> ordered list of chapters where it fired.
        self._recent_usage: dict[str, list[int]] = {}
        # chapter -> set of rule_ids that fired (reverse index, mirrors
        # MotifTracker._chapter_motifs).
        self._chapter_rules: dict[int, set[str]] = {}
        self._lookback_chapters = max(0, int(lookback_chapters))
        self._repetition_gap_chapters = max(1, int(repetition_gap_chapters))
        self._warmup_complete = False

    # ------------------------------------------------------------------ props
    @property
    def rules(self) -> dict[str, StyleRule]:
        return dict(self._rules)

    @property
    def is_warmup_complete(self) -> bool:
        return self._warmup_complete

    # ----------------------------------------------------- fingerprint loading
    def load_from_style_profile(self, style_profile: Any) -> None:
        """Build the rule fingerprint table from a ``ProjectStyleProfile``.

        Accepts either the pydantic model or its dict/JSON form. Idempotent:
        re-loading replaces the table (useful after a style-profile refresh).
        """
        modules: list[Any] = []
        if style_profile is None:
            return
        if isinstance(style_profile, dict):
            modules = list(style_profile.get("modules", []) or [])
        else:
            modules = list(getattr(style_profile, "modules", []) or [])

        new_rules: dict[str, StyleRule] = {}
        for module in modules:
            module_name = str(getattr(module, "name", "") or (module.get("name", "") if isinstance(module, dict) else ""))
            rules = list(getattr(module, "rules", []) or (module.get("rules", []) if isinstance(module, dict) else []))
            pos = str(getattr(module, "positive_example", "") or (module.get("positive_example", "") if isinstance(module, dict) else ""))
            neg = str(getattr(module, "negative_example", "") or (module.get("negative_example", "") if isinstance(module, dict) else ""))
            for rule_text in rules:
                rule_text = str(rule_text or "").strip()
                if not rule_text:
                    continue
                rule_id = f"{_slugify(module_name)}::{_slugify(rule_text)}"
                cues = _extract_cues(rule_text, pos, neg)
                new_rules[rule_id] = StyleRule(
                    rule_id=rule_id,
                    module_name=module_name,
                    rule_text=rule_text,
                    positive_example=pos,
                    negative_example=neg,
                    cues=cues,
                )
        # Preserve prior usage stats for rules that survive the reload.
        for rule_id, rule in new_rules.items():
            if rule_id in self._rules:
                prior = self._rules[rule_id]
                rule.occurrence_count = prior.occurrence_count
                rule.first_appearance_chapter = prior.first_appearance_chapter
                rule.last_appearance_chapter = prior.last_appearance_chapter
        self._rules = new_rules
        # Prune usage/chapter indices for rules that no longer exist.
        self._recent_usage = {k: v for k, v in self._recent_usage.items() if k in new_rules}
        self._chapter_rules = {ch: {r for r in s if r in new_rules} for ch, s in self._chapter_rules.items()}
        self._warmup_complete = bool(new_rules)

    # ----------------------------------------------------- chapter registration
    def register_chapter(self, chapter_number: int, chapter_text: str) -> list[str]:
        """Detect fired rules in ``chapter_text`` and record their usage.

        Returns the list of rule_ids that fired this chapter. A rule "fires"
        when any of its cues appears in the prose. This is deterministic and
        cheap (substring scan over ≤25 rules' cues).
        """
        if chapter_number in self._chapter_rules:
            self.delete_chapter_rules(chapter_number)
        if not self._rules or not chapter_text:
            self._chapter_rules.setdefault(chapter_number, set())
            return []
        fired: list[str] = []
        for rule_id, rule in self._rules.items():
            if rule.retired or not rule.cues:
                continue
            if any(cue in chapter_text for cue in rule.cues):
                fired.append(rule_id)
                self._record_usage(rule_id, rule, chapter_number)
        self._chapter_rules[chapter_number] = set(fired)
        return fired

    def _record_usage(self, rule_id: str, rule: StyleRule, chapter_number: int) -> None:
        usage = self._recent_usage.setdefault(rule_id, [])
        if chapter_number in usage:
            return
        usage.append(chapter_number)
        usage.sort()
        # Keep only the lookback window to bound memory (mirrors motif pruning).
        if self._lookback_chapters > 0 and len(usage) > self._lookback_chapters + 4:
            del usage[: len(usage) - (self._lookback_chapters + 4)]
        rule.occurrence_count = len(usage)
        rule.first_appearance_chapter = usage[0] if usage else 0
        rule.last_appearance_chapter = usage[-1] if usage else 0

    def has_chapter_rules(self, chapter_number: int) -> bool:
        return chapter_number in self._chapter_rules

    # ----------------------------------------------------- repetition detection
    def check_rule_repetition(
        self,
        chapter_number: int,
        chapter_text: str,
        *,
        lookback_chapters: int | None = None,
        repetition_gap_chapters: int | None = None,
    ) -> list[RuleRepetitionWarning]:
        """Flag rules firing too frequently across the lookback window.

        Ensures the current chapter is registered first (so a freshly-written
        chapter is included). A rule triggers a warning when it fired in the
        current chapter AND in another chapter within ``repetition_gap_chapters``
        — i.e. the technique is recurring close enough to read as a template
        rather than an intentional callback.
        """
        if not self._rules:
            return []
        if chapter_number not in self._chapter_rules:
            self.register_chapter(chapter_number, chapter_text)

        lookback = self._lookback_chapters if lookback_chapters is None else max(0, int(lookback_chapters))
        gap = (
            self._repetition_gap_chapters
            if repetition_gap_chapters is None
            else max(1, int(repetition_gap_chapters))
        )
        oldest = max(1, chapter_number - lookback) if lookback > 0 else 1

        warnings: list[RuleRepetitionWarning] = []
        for rule_id, rule in self._rules.items():
            if rule.retired:
                continue
            usage = self._recent_usage.get(rule_id, [])
            recent = [ch for ch in usage if ch >= oldest and ch < chapter_number]
            if not recent:
                continue
            # Only warn if the rule also fired THIS chapter (current template use).
            if rule_id not in self._chapter_rules.get(chapter_number, set()):
                continue
            chapters_since = chapter_number - recent[-1]
            if chapters_since < gap:
                similarity = max(0.0, 1.0 - (chapters_since / max(1, float(gap + 2))))
                # Severity escalates with frequency: a rule firing 4+ times is
                # an entrenched template, not a stylistic echo.
                severity = "high" if rule.occurrence_count >= 4 else "medium"
                warnings.append(
                    RuleRepetitionWarning(
                        rule_name=rule.rule_text,
                        module_name=rule.module_name,
                        chapter_number=chapter_number,
                        previous_chapters=recent[-3:],
                        similarity_score=round(similarity, 3),
                        text_snippet=rule.positive_example or rule.rule_text,
                        suggestion=self._repetition_suggestion(rule, chapters_since),
                        severity=severity,
                    )
                )
        return warnings

    @staticmethod
    def _repetition_suggestion(rule: StyleRule, chapters_since: int) -> str:
        if chapters_since <= 1:
            return f"技法「{rule.module_name}」连续两章触发，本章换用其他表达通道或省略该技法。"
        if chapters_since <= 3:
            return f"技法「{rule.module_name}」近期已多次使用，若必须用请赋予新义或转移感官通道。"
        return f"技法「{rule.module_name}」在近窗内重复，确认是有意呼应而非模板惯性。"

    # ----------------------------------------------------- prompt projection
    def get_rules_for_prompt(
        self,
        current_chapter: int,
        *,
        related_lookback_chapters: int = DEFAULT_PROMPT_LOOKBACK_CHAPTERS,
        max_rules: int = DEFAULT_PROMPT_MAX_RULES,
    ) -> dict[str, Any]:
        """Project active rules + forbidden repetition into a prompt bundle.

        Mirrors the shape of ``MotifTracker.get_motifs_for_prompt`` so the same
        prompt-rendering code can consume both. ``forbidden_repetition`` holds
        rules that have calcified into templates and should be avoided or
        varied this chapter.
        """
        if not self._rules:
            return {
                "active_rules": [],
                "forbidden_repetition": [],
                "style_rule_guidance": "",
            }
        oldest = max(1, current_chapter - related_lookback_chapters)
        active: list[dict[str, Any]] = []
        forbidden: list[str] = []
        for rule_id, rule in self._rules.items():
            if rule.retired:
                continue
            usage = self._recent_usage.get(rule_id, [])
            recent = [ch for ch in usage if ch >= oldest]
            active.append(
                {
                    "module": rule.module_name,
                    "rule": rule.rule_text,
                    "occurrence_count": rule.occurrence_count,
                    "recent_chapters": recent[-3:],
                }
            )
            # A rule is forbidden-repetition when it fired within the gap window.
            if recent and current_chapter - recent[-1] < self._repetition_gap_chapters:
                forbidden.append(f"{rule.module_name}：{rule.rule_text}")
        active.sort(key=lambda r: r["occurrence_count"], reverse=True)
        guidance = ""
        if forbidden:
            guidance = "以下技法近期已频繁触发，本章应变换表达或暂避，避免模板化：" + "；".join(forbidden[:max_rules])
        return {
            "active_rules": active[:max_rules],
            "forbidden_repetition": forbidden[:max_rules],
            "style_rule_guidance": guidance,
        }

    # ----------------------------------------------------- maintenance
    def delete_chapter_rules(self, chapter_number: int) -> None:
        """Drop a chapter's recorded rule firings (used on rewrite/regen)."""
        rule_ids = self._chapter_rules.pop(chapter_number, set())
        if not rule_ids:
            return
        for rule_id in rule_ids:
            usage = self._recent_usage.get(rule_id)
            if not usage:
                continue
            self._recent_usage[rule_id] = [ch for ch in usage if ch != chapter_number]
            rule = self._rules.get(rule_id)
            if rule is not None:
                remaining = self._recent_usage.get(rule_id, [])
                rule.occurrence_count = len(remaining)
                rule.first_appearance_chapter = remaining[0] if remaining else 0
                rule.last_appearance_chapter = remaining[-1] if remaining else 0

    def delete_chapters_from(self, from_chapter: int) -> None:
        """Drop all chapter records at/after ``from_chapter`` (bulk invalidate)."""
        for ch in [ch for ch in self._chapter_rules if ch >= from_chapter]:
            self.delete_chapter_rules(ch)

    def get_stats(self) -> dict[str, Any]:
        return {
            "rule_count": len(self._rules),
            "chapters_tracked": len(self._chapter_rules),
            "total_firings": sum(len(s) for s in self._recent_usage.values()),
        }


__all__ = [
    "DEFAULT_LOOKBACK_CHAPTERS",
    "DEFAULT_REPETITION_GAP_CHAPTERS",
    "RuleRepetitionWarning",
    "STYLE_RULE_CATEGORY",
    "StyleRule",
    "StyleRuleTracker",
]
