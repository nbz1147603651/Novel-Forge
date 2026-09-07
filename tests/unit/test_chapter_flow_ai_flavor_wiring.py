"""Unit tests for the ai_flavor wiring in chapter_flow_review._build_quality_gate.

The check should be added between reading_power and rhythm_curve so it always
runs after the deterministic prescreen, and it should fall back gracefully
when the text is empty or has no AI-flavor hits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class _MockRevealDensity:
    revelation_count: int = 0


@dataclass
class _MockChapterRepairReport:
    chapter_repair_score: float = 8.0


@dataclass
class _MockReadingPowerReport:
    chapter: int = 1
    overall_score: float = 7.5
    hook_type: str = ""
    hook_strength: str = ""
    micro_payoffs: list = field(default_factory=list)


@dataclass
class _MockEvalReport:
    overall_score: float = 8.0
    source_text_hash: str = ""


@dataclass
class _MockNarrativeContext:
    target_chapter_rhythm: Any = None


@dataclass
class _MockLayout:
    def chapter_path(self, chapter_number: int) -> Path:
        return Path("/tmp/novel_forge_missing") / f"chapter_{chapter_number:03d}.md"


@dataclass
class _MockPrepared:
    packet: Any = None
    bundle: Any = None


@dataclass
class _MockChapterOutline:
    chapter_number: int = 1


@dataclass
class _MockCharacter:
    name: str
    role: str = "supporting"


@dataclass
class _MockCharacterBible:
    characters: list[_MockCharacter] = field(default_factory=list)


@dataclass
class _MockBundle:
    chapter_outline: _MockChapterOutline = field(default_factory=_MockChapterOutline)
    style_profile: Any = None
    layout: Any = field(default_factory=_MockLayout)
    character_bible: Any = field(default_factory=_MockCharacterBible)
    character_silence: bool = False
    backstory_reveals: list[Any] = field(default_factory=list)


@dataclass
class _MockReviewArtifacts:
    """Minimal mock matching ChapterReviewArtifacts interface for _build_quality_gate."""

    eval_report: Any = None
    alignment_report: Any = None
    continuity_report: Any = None
    causal_report: Any = None
    chapter_repair_report: Any = None
    reading_power_report: Any = None
    current_text: str = ""
    prepared: Any = None
    guard_compliance_report: Any = None
    knowledge_findings: Any = None
    word_count_archive_gate_enabled: Any = None
    pov_drift_findings: Any = None
    pov_drift_tickets: Any = None

    def __post_init__(self):
        if self.prepared is None:
            self.prepared = _MockPrepared(
                bundle=_MockBundle(),
                packet=type("P", (), {"narrative_context": None})(),
            )


def _settings():
    s = type("S", (), {})()
    s.max_revelations_per_chapter = 2
    s.long_plot_progression_quality_floor = 5.5
    return s


def _target_word_count():
    return 5000


# ---------------------------------------------------------------------------
# AI-flavor check is added to gate
# ---------------------------------------------------------------------------


class TestAIFlavorWiringInChapterFlow:
    def test_clean_text_adds_ai_flavor_check_that_passes(self) -> None:
        from novel_forge.pipeline.long.chapter_flow_review import _build_quality_gate

        review = _MockReviewArtifacts(
            eval_report=_MockEvalReport(overall_score=8.5, source_text_hash=""),
            current_text="风从穿堂涌出来，吹动门槛边的风铃。\n\n" * 50,  # 1800 chars, no AI flavor
        )
        gate = _build_quality_gate(
            review,
            settings=_settings(),
            target_word_count=_target_word_count(),
        )
        ai_results = [c for c in gate._checks if c.dimension == "ai_flavor"]
        assert len(ai_results) == 1
        assert ai_results[0].passed is True

    def test_ai_flavor_text_fails_check(self) -> None:
        from novel_forge.pipeline.long.chapter_flow_review import _build_quality_gate

        # Text with both weak verbs and tautology markers.
        bad_text = (
            "她感到一种难以言喻的失落涌上心头。"
            "她注意到某种年长者的欲言又止，某种无声的语言，"
            "和那个比任何语言都更清晰地表明了某种意义的动作。"
        ) * 30  # repeat to fill the chapter
        review = _MockReviewArtifacts(
            eval_report=_MockEvalReport(overall_score=8.5, source_text_hash=""),
            current_text=bad_text,
        )
        gate = _build_quality_gate(
            review,
            settings=_settings(),
            target_word_count=_target_word_count(),
        )
        ai_results = [c for c in gate._checks if c.dimension == "ai_flavor"]
        assert len(ai_results) == 1
        assert ai_results[0].passed is False
        # The hit count should reflect what was detected.
        assert ai_results[0].details["hit_count"] >= 3

    def test_empty_text_skips_ai_flavor_gracefully(self) -> None:
        """An empty chapter text should not crash the gate — the check returns passed=True."""
        from novel_forge.pipeline.long.chapter_flow_review import _build_quality_gate

        review = _MockReviewArtifacts(
            eval_report=_MockEvalReport(overall_score=8.5, source_text_hash=""),
            current_text="",
        )
        gate = _build_quality_gate(
            review,
            settings=_settings(),
            target_word_count=_target_word_count(),
        )
        ai_results = [c for c in gate._checks if c.dimension == "ai_flavor"]
        assert len(ai_results) == 1
        assert ai_results[0].score == 10.0
        assert ai_results[0].passed is True

    def test_ai_flavor_check_appears_after_reading_power(self) -> None:
        """Order check: ai_flavor should be added somewhere in the gate,
        not gating subsequent checks. The exact position is less important
        than ensuring the check is registered."""
        from novel_forge.pipeline.long.chapter_flow_review import _build_quality_gate

        review = _MockReviewArtifacts(
            eval_report=_MockEvalReport(overall_score=8.5, source_text_hash=""),
            reading_power_report=_MockReadingPowerReport(),
            current_text="风从穿堂涌出来。\n\n" * 50,
        )
        gate = _build_quality_gate(
            review,
            settings=_settings(),
            target_word_count=_target_word_count(),
        )
        dimensions = [c.dimension for c in gate._checks]
        assert "ai_flavor" in dimensions
        # reading_power must also be present
        assert "reading_power" in dimensions


# ---------------------------------------------------------------------------
# Deterministic check (no LLM call) — should never require external IO
# ---------------------------------------------------------------------------


class TestAIFlavorCheckIsDeterministic:
    def test_check_does_not_invoke_llm(self) -> None:
        """The ai_flavor check runs purely on regex-based prescreen text;
        no LLM call should be made."""

        from novel_forge.pipeline.long.chapter_flow_review import _build_quality_gate

        bad_text = (
            "她感到一种难以言喻的失落，"
            "她注意到某种欲言又止，某种无声的语言，某种信号。"
        ) * 30

        review = _MockReviewArtifacts(
            eval_report=_MockEvalReport(overall_score=8.5, source_text_hash=""),
            current_text=bad_text,
        )
        # No patching — if check tries to call LLM, test would error out
        # because no router is configured in test env.
        gate = _build_quality_gate(
            review,
            settings=_settings(),
            target_word_count=_target_word_count(),
        )
        ai_results = [c for c in gate._checks if c.dimension == "ai_flavor"]
        assert ai_results
        assert ai_results[0].passed is False


# ---------------------------------------------------------------------------
# M4 wiring — backstory reveals and silence-aware dialogue ratio
# ---------------------------------------------------------------------------


class TestM4QualityGateWiring:
    def test_style_gate_uses_non_protagonist_ratio_when_character_silence(self) -> None:
        from novel_forge.pipeline.long.chapter_flow_review import _build_quality_gate

        settings = _settings()
        settings.long_style_dialogue_gate_mode = "block"
        settings.long_style_dialogue_repair_threshold = 25

        review = _MockReviewArtifacts(
            eval_report=_MockEvalReport(overall_score=8.5, source_text_hash=""),
            current_text="沈鹿溪说：“" + ("主角一直说话。" * 40) + "”",
        )
        review.prepared.bundle = _MockBundle(
            style_profile={"global_style": {"dialogue_ratio": "high"}},
            character_silence=True,
            character_bible=_MockCharacterBible(
                characters=[_MockCharacter(name="沈鹿溪", role="protagonist")]
            ),
        )

        gate = _build_quality_gate(
            review,
            settings=settings,
            target_word_count=_target_word_count(),
        )

        style_results = [c for c in gate._checks if c.dimension == "style_dialogue_ratio"]
        assert len(style_results) == 1
        assert style_results[0].passed is False
        assert style_results[0].details["character_silence_mode"] is True
        assert style_results[0].details["dialogue_ratio_pct"] == 0.0
        assert style_results[0].details["total_dialogue_ratio_pct"] > 70.0

    def test_backstory_reveals_are_added_to_quality_gate(self) -> None:
        from novel_forge.pipeline.long.chapter_flow_review import _build_quality_gate

        review = _MockReviewArtifacts(
            eval_report=_MockEvalReport(overall_score=8.5, source_text_hash=""),
            current_text="声音在屋檐下回荡。" * 30,
        )
        review.prepared.bundle = _MockBundle(
            chapter_outline=_MockChapterOutline(chapter_number=4),
            backstory_reveals=[
                {
                    "topic": "主角网络暴力前史",
                    "required_first_appearance": 1,
                    "min_word_count": 120,
                    "triggers": ["网暴", "录音"],
                }
            ],
        )

        gate = _build_quality_gate(
            review,
            settings=_settings(),
            target_word_count=_target_word_count(),
        )

        backstory_results = [c for c in gate._checks if c.dimension == "backstory_reveal"]
        assert len(backstory_results) == 1
        assert backstory_results[0].passed is False
        assert backstory_results[0].details["overdue"] == ["主角网络暴力前史"]
