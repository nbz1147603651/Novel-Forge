"""Enumerations and constants used across the system."""

from __future__ import annotations

from enum import Enum

from novel_forge.common.constants import ModelTier, TaskType  # noqa: F401

__all__ = [
    "BeatType",
    "CANON_CONTEXT_RECENT_CHAPTERS",
    "ChapterRunnerConstants",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
    "FEMALE_GENDER_INDICATORS",
    "ForeshadowingStatus",
    "Genre",
    "LONG_MODE_MAX_EDIT_ROUNDS",
    "MALE_GENDER_INDICATORS",
    "ModelTier",
    "PipelineConstants",
    "SHORT_MODE_MAX_EDIT_ROUNDS",
    "TaskType",
]


class Genre(str, Enum):
    """Supported story genres."""

    FANTASY = "fantasy"
    SCIFI = "scifi"
    MYSTERY = "mystery"
    ROMANCE = "romance"
    THRILLER = "thriller"
    LITERARY = "literary"
    HORROR = "horror"
    HISTORICAL = "historical"
    OTHER = "other"


class BeatType(str, Enum):
    """Structural role of a beat in the story arc."""

    OPENING = "opening"  # 开篇/引入
    RISING = "rising"  # 递进/发展
    CLIMAX = "climax"  # 高潮
    FALLING = "falling"  # 回落/转折
    RESOLUTION = "resolution"  # 收束/结局


class ForeshadowingStatus(str, Enum):
    """Lifecycle states of a foreshadowing item."""

    PLANTED = "planted"
    REINFORCED = "reinforced"
    REVEALED = "revealed"
    ABANDONED = "abandoned"


# ── Defaults ──────────────────────────────────────────

SHORT_MODE_MAX_EDIT_ROUNDS: int = 2
LONG_MODE_MAX_EDIT_ROUNDS: int = 2
DEFAULT_TEMPERATURE: float = 0.7
DEFAULT_MAX_TOKENS: int = 4096
CANON_CONTEXT_RECENT_CHAPTERS: int = 5


# ── Pipeline Constants (chapter-level) ────────────────


class PipelineConstants:
    """Centralised numeric constants for the long-form chapter pipeline."""

    # Retry / backoff
    RETRY_BACKOFF_BASE: float = 0.6
    RETRY_EXPONENTIAL_BASE: int = 2
    CONTEXT_COMPRESS_MIN_TOKEN_BUDGET: int = 512
    INITIAL_MAX_TOKENS: int = 4096
    MAX_TOKEN_CAP: int = 8192
    MIN_COMPRESS_TOKEN_BUDGET: int = 768

    # Per-task token caps — prevents lightweight tasks from escalating to draft-level budgets
    TASK_TOKEN_CAPS: dict[str, int] = {
        # Lightweight tasks — raised for reasoning-model compatibility (thinking eats into max_tokens)
        "spec_enrich": 2048,
        "evaluate": 8192,  # 综合评估：完整 JSON 报告，低预算会截断 repair_suggestions
        "check_alignment": 4096,
        "check_chapter": 4096,
        "bridge_chapter": 4096,
        "context_compress": 2048,
        "plot_guard_judge": 2048,
        "guard_constraint_check": 2048,
        "generate_config": 4096,
        "polish_config": 4096,
        "check_continuity": 4096,
        "validate_causal": 16384,  # 因果链校验：需输出详细问题报告（含证据/修复建议），长章节易超 8192
        # Medium tasks — plan_chapter raised to 8192: deepseek-reasoner thinking
        # tokens eat into the budget, and scene JSON with 4-8 nested scenes
        # (motivations, state refs, contracts) regularly exceeds 4096 tokens.
        "plan_chapter": 8192,
        # Outline tasks can be token-heavy (多章节批量生成) — raise cap to 8192
        "blueprint_element_select": 4096,
        "plan_outline": 10240,  # blueprint generation: one-shot global structure, needs more room for 80+ ch novels
        "plan_outline_batch": 8192,
        "plan_outline_continue": 8192,
        "extract_canon": 4096,
        "edit_chapter": 8192,  # Full revised text output (not diff), needs draft-level budget
        "adjust_outline": 4096,
        "enrich_character": 4096,
        "adjudicate_character_introduction": 2048,
        "introduce_character": 4096,
        "repair_continuity": 16000,  # 全文输出，需要与 draft_chapter 同等 token 空间
        "patch_chapter": 2048,  # 只输出 patches JSON，不需要大 token 预算
        "evaluate_reading_power": 2048,  # 追读力评估：轻量 JSON 输出
        "humanize_scan": 8192,  # AI 去痕扫描：pattern_hits/patches 报告容易超过 2-4K
        "volume_audit": 4096,
        "macro_guard_audit": 2048,  # 宏观护栏审计：轻量 JSON 输出
        "derive_editorial_character_voices": 4096,
        "derive_editorial_structure": 6144,
        "derive_editorial_style_constraints": 6144,
        "derive_editorial_element_directives": 4096,
        "extract_chapter_summary_exit": 4096,
        "extract_canon_delta": 4096,
        "extract_creative_report": 3072,
        "extract_character_state_deltas": 4096,
        "extract_relationship_deltas": 3072,
        "extract_plot_thread_deltas": 3072,
        "extract_expression_observations": 2048,
        "init_story_core_premise": 3072,
        "init_story_world_rules": 4096,
        "init_story_continuity_rules": 4096,
        "init_story_themes_and_symbols": 3072,
        "init_character_roster": 3072,
        "init_character_profile_batch": 8192,
        "init_character_relationship_matrix": 4096,
        "init_character_arc_plan": 4096,
        "init_coherence_ontology": 3072,
        "init_coherence_extraction_guide": 2048,
        "init_coherence_conflict_rules": 2048,
        "init_coherence_payoff_rules": 2048,
        "book_editorial_structure_audit": 4096,
        "book_editorial_voice_audit": 4096,
        "book_editorial_language_audit": 3072,
        "book_editorial_theme_symbol_audit": 4096,
        "book_editorial_element_audit": 4096,
        "book_consistency_naming": 3072,
        "book_consistency_timeline": 4096,
        "book_consistency_world_rule": 4096,
        "book_consistency_character_state": 4096,
        "book_consistency_plot_thread": 4096,
        "book_consistency_narrative_drift": 4096,
        # Heavy tasks — full-text output, raised to 16000 for 5000+ char targets (model limit 16384)
        "init_story_bible": 16384,
        "init_character_bible": 16384,
        "draft_chapter": 16000,
        "draft": 16000,
    }

    # Prompt / text
    PLACEHOLDER_NOTE: str = "系统自动补齐：原始大纲缺失该章节。"
    DEFAULT_CHAPTER_TITLE_TEMPLATE: str = "第{chapter_num}章"
    DEFAULT_CHAPTER_GOAL: str = "承接上一章并推进主线剧情。"

    # Batch processing
    MIN_OUTLINE_BATCH_SIZE: int = 2
    MAX_BATCH_REPAIR_ROUNDS: int = 2
    CONVERSATION_HISTORY_WINDOW: int = 2

    # Auto-repair
    SAMPLE_PREVIEW_LENGTH: int = 200

    # Opening echo
    OPENING_ECHO_MIN_LENGTH: int = 20
    OPENING_ECHO_OVERLAP_RATIO: float = 0.85


# Backward-compatible alias
ChapterRunnerConstants = PipelineConstants


# ── Gender-indicator noun phrases for unnamed NPCs ───────────────────────────
# Used by pronoun-correction logic (guardrails.py, pronoun_check_step.py) to
# detect when a pronoun near the POV character actually refers to an unnamed NPC
# of the opposite gender (e.g. "中年男人…他抬起头").
#
# These are intentionally broad — false negatives (missing words) cause pronoun
# mis-corrections that are hard to catch, while false positives (extra words)
# only skip one replacement opportunity that the LLM-based checker can still fix.
MALE_GENDER_INDICATORS: frozenset[str] = frozenset(
    {
        "男人",
        "男子",
        "男孩",
        "公子",
        "老爷",
        "书生",
        "老头",
        "汉子",
        "男掌柜",
        "男仆",
        "男侍",
        "男童",
        "武夫",
        "壮汉",
        "老翁",
        "老丈",
        "少爷",
        "老先生",
        "兄台",
        "兄长",
        "大人",
        "将军",
    }
)
FEMALE_GENDER_INDICATORS: frozenset[str] = frozenset(
    {
        "女人",
        "女子",
        "女孩",
        "姑娘",
        "太太",
        "娘子",
        "小姐",
        "老妇",
        "妇人",
        "侍女",
        "宫女",
        "丫鬟",
        "嬷嬷",
        "老妪",
        "女掌柜",
        "女童",
        "老妇人",
        "妇女",
        "少女",
    }
)
