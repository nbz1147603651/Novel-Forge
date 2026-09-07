"""Configurable event sound rule engine with priority and project overrides.

Extends the basic JSON rule loading with:
- Priority-based rule ordering (higher priority wins)
- Mutual exclusion groups (only one rule per group fires)
- Project-level rule overrides (data/<project>/tts_rules.json)
- User-defined custom rules

Author: novel-forge
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novel_forge.obs.logger import get_logger
from novel_forge.tts.rules import EventSoundRule, load_event_sound_rules

_log = get_logger("tts.rules.engine")


@dataclass(frozen=True)
class RuleMatch:
    """Result of a rule matching against text."""

    rule: EventSoundRule
    match_start: int
    match_end: int
    matched_text: str


@dataclass
class RuleEngineConfig:
    """Configuration for the rule engine."""

    enable_mutual_exclusion: bool = True
    """When True, only the highest-priority rule in each exclusion group fires."""

    max_matches_per_segment: int = 3
    """Maximum number of SFX cues per segment to prevent overload."""

    project_override_path: Path | None = None
    """Path to project-level tts_rules.json for custom overrides."""


@dataclass
class SoundRuleEngine:
    """Configurable rule engine for event-to-sound-effect mapping.

    Supports:
    - Priority-based ordering (higher priority rules evaluated first)
    - Mutual exclusion groups (only one rule per group fires per segment)
    - Project-level overrides via tts_rules.json
    - Runtime rule addition/removal

    Usage::

        engine = SoundRuleEngine()
        engine.load_default_rules()
        engine.load_project_overrides(project_path)

        matches = engine.evaluate("门砰的一声关上了")
        # Returns [RuleMatch(rule=EventSoundRule(effect_name="door_slam", ...), ...)]

    Author: novel-forge
    """

    rules: list[EventSoundRule] = field(default_factory=list)
    exclusion_groups: dict[str, list[str]] = field(default_factory=dict)
    """Maps group_name -> list of effect_names that are mutually exclusive."""

    config: RuleEngineConfig = field(default_factory=RuleEngineConfig)

    def load_default_rules(self) -> None:
        """Load the built-in rules from event_sound_rules.json."""
        self.rules = list(load_event_sound_rules())
        # Sort by priority descending (higher priority first).
        self.rules.sort(key=lambda r: r.priority, reverse=True)
        _log.debug("Loaded %d default event sound rules", len(self.rules))

    def load_project_overrides(self, project_path: Path) -> int:
        """Load project-level rule overrides from tts_rules.json.

        The override file format::

            {
                "rules": [
                    {"effect_name": "custom_sound", "pattern": "regex", ...}
                ],
                "disabled_rules": ["effect_name_to_disable"],
                "exclusion_groups": {
                    "group_name": ["effect_a", "effect_b"]
                }
            }

        Returns the number of rules added/modified.
        """
        override_path = project_path / "tts_rules.json"
        if not override_path.exists():
            return 0

        try:
            data: dict[str, Any] = json.loads(override_path.read_text(encoding="utf-8"))
        except Exception as exc:
            _log.warning("Failed to load project rule overrides: %s", exc)
            return 0

        count = 0

        # Disable specified rules.
        disabled = set(data.get("disabled_rules", []))
        if disabled:
            self.rules = [r for r in self.rules if r.effect_name not in disabled]
            count += len(disabled)

        # Add custom rules.
        for item in data.get("rules", []):
            try:
                rule = EventSoundRule(
                    effect_name=str(item["effect_name"]),
                    pattern=re.compile(str(item["pattern"])),
                    description=str(item.get("description", "")),
                    duration_ms=int(item.get("duration_ms", 1000)),
                    volume=float(item.get("volume", 0.4)),
                    priority=int(item.get("priority", 100)),  # Custom rules default higher
                )
                # Replace existing rule with same name or add new.
                self.rules = [r for r in self.rules if r.effect_name != rule.effect_name]
                self.rules.append(rule)
                count += 1
            except Exception as exc:
                _log.warning("Invalid custom rule %r: %s", item.get("effect_name"), exc)

        # Load exclusion groups.
        self.exclusion_groups.update(data.get("exclusion_groups", {}))

        # Re-sort by priority.
        self.rules.sort(key=lambda r: r.priority, reverse=True)
        _log.info("Loaded %d project rule overrides from %s", count, override_path)
        return count

    def add_rule(self, rule: EventSoundRule) -> None:
        """Add a rule at runtime."""
        self.rules = [r for r in self.rules if r.effect_name != rule.effect_name]
        self.rules.append(rule)
        self.rules.sort(key=lambda r: r.priority, reverse=True)

    def remove_rule(self, effect_name: str) -> bool:
        """Remove a rule by effect name. Returns True if found."""
        original_len = len(self.rules)
        self.rules = [r for r in self.rules if r.effect_name != effect_name]
        return len(self.rules) < original_len

    def evaluate(self, text: str, *, segment_index: int = 0) -> list[RuleMatch]:
        """Evaluate all rules against text and return matches.

        Applies priority ordering, mutual exclusion, and max match limits.
        """
        matches: list[RuleMatch] = []
        fired_groups: set[str] = set()

        for rule in self.rules:
            if len(matches) >= self.config.max_matches_per_segment:
                break

            # Check mutual exclusion.
            if self.config.enable_mutual_exclusion:
                group = self._get_exclusion_group(rule.effect_name)
                if group and group in fired_groups:
                    continue

            # Try to match.
            match = rule.pattern.search(text)
            if match:
                matches.append(
                    RuleMatch(
                        rule=rule,
                        match_start=match.start(),
                        match_end=match.end(),
                        matched_text=match.group(),
                    )
                )
                # Mark exclusion group as fired.
                if self.config.enable_mutual_exclusion:
                    group = self._get_exclusion_group(rule.effect_name)
                    if group:
                        fired_groups.add(group)

        return matches

    def _get_exclusion_group(self, effect_name: str) -> str | None:
        """Find the exclusion group for an effect name."""
        for group_name, members in self.exclusion_groups.items():
            if effect_name in members:
                return group_name
        return None


# Module-level singleton for shared use.
_engine: SoundRuleEngine | None = None


def get_sound_rule_engine() -> SoundRuleEngine:
    """Return the module-level shared sound rule engine singleton.

    Author: novel-forge
    """
    global _engine
    if _engine is None:
        _engine = SoundRuleEngine()
        _engine.load_default_rules()
    return _engine
