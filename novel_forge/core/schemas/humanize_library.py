"""Humanize library entry schema — 26 regex + 4 llm-only builtin pattern definitions.

The humanize library catalogs every detectable AI-writing pattern the system knows
about. There are two categories of builtin entries:

* 26 ``regex`` entries — one per compiled rule in ``_HUMANIZE_RULES``
  (``pipeline/steps/humanize_scan_step.py``). Each maps 1:1 to a regex-based
  scanner that runs locally without an LLM call.

*  4 ``llm_only`` entries — patterns that require an LLM adjudicator because
  they depend on semantic context: ``synonym_cycling``, ``excessive_hedging``,
  ``monotone_rhythm``, ``cross_chapter_template``. These ship ``enabled=False``
  by default.

Users and imported libraries extend this with ``lib_user_<8hex>`` /
``lib_imported_<8hex>`` IDs.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from novel_forge.core.domain.humanize_rule_catalog import HUMANIZE_RULE_MAP
from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.humanize import HUMANIZE_PATTERN_IDS

LIBRARY_SCHEMA_VERSION: str = "2.0"

# ---------------------------------------------------------------------------
# Pattern ID validation — accept builtin IDs + lib_user_ / lib_imported_
# ---------------------------------------------------------------------------

_PATTERN_ID_RE: re.Pattern[str] = re.compile(
    r"^(?:"
    + "|".join(re.escape(pid) for pid in HUMANIZE_PATTERN_IDS)
    + r"|lib_user_[0-9a-f]{8}"
    + r"|lib_imported_[0-9a-f]{8}"
    + r")$"
)


class HumanizeLibraryEntry(VersionedSchema):
    """A single entry in the humanize pattern library.

    Each entry describes one detectable AI-writing pattern with its detection
    method, severity, usage statistics, and optional embedding signature.

    Builtin entries are defined as ``LIBRARY_BUILTIN_ENTRIES`` — 26 regex-backed
    entries (enabled by default) and 4 LLM-only entries (``enabled=False``).
    """

    pattern_id: str = Field(
        description=(
            "Unique identifier — one of the builtin IDs, "
            "``lib_user_<8hex>`` (user-defined), or ``lib_imported_<8hex>`` (imported library)."
        )
    )
    pattern_name: str = Field(description="Human-readable pattern name, e.g. '显著性通胀'.")
    category: str = Field(description="Pattern category, e.g. '模板句式', '聊天残留'.")
    severity: Literal["critical", "high", "medium", "low"] = Field(
        description="Severity level of this pattern."
    )
    example_phrases: list[str] = Field(
        default_factory=list,
        description="Example phrases that exhibit this pattern.",
    )
    example_template: str = Field(
        default="",
        description="A template string illustrating the pattern structure.",
    )
    detection_method: Literal["regex", "llm_only", "vector_only", "mixed"] = Field(
        description="How this pattern is detected."
    )
    detection_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Configuration parameters for the detection method.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Keywords associated with this pattern for search / tag matching.",
    )
    source: Literal["builtin", "user", "imported"] = Field(
        description="Origin of this pattern entry."
    )
    project_id: str | None = Field(
        default=None,
        description="If scoped to a specific project; None means global scope.",
    )
    notes: str = Field(default="", description="Free-text notes about this pattern.")
    hit_count: int = Field(default=0, ge=0, description="Cumulative detection count.")
    first_seen_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when this pattern was first seen in user content.",
    )
    last_seen_at: datetime | None = Field(
        default=None,
        description="UTC timestamp of the most recent detection.",
    )
    last_hit_chapter: int | None = Field(
        default=None,
        ge=1,
        description="Chapter number where the last hit occurred.",
    )
    enabled: bool = Field(default=True, description="Whether this pattern is active for detection.")
    embedding_signature: str | None = Field(
        default=None,
        description="Hash / fingerprint of the embedding vector for staleness checks.",
    )
    vector_stale: bool = Field(
        default=False,
        description="Whether the cached embedding vector needs regeneration.",
    )

    @field_validator("pattern_id")
    @classmethod
    def _validate_pattern_id(cls, v: str) -> str:
        if not _PATTERN_ID_RE.match(v):
            raise ValueError(
                f"pattern_id must match builtin ID or lib_user/lib_imported with 8-hex suffix, "
                f"got {v!r}"
            )
        return v


# ---------------------------------------------------------------------------
#  LIBRARY_BUILTIN_ENTRIES — 26 regex entries + 4 llm_only entries
# ---------------------------------------------------------------------------
# The 26 regex entries map 1:1 to _HUMANIZE_RULES in humanize_scan_step.py:36-58.
# The 4 llm_only entries are pattern_ids that exist in HUMANIZE_PATTERN_IDS but
# have no local regex scanner — they require an LLM adjudicator.
#
# Note: 5 entries were added in response to the 山风与归人2 audit:
# weak_verb_stacking, tautology_marker, binary_judgment_closing,
# pronoun_disappearance_run, precise_timestamp_overuse.

LIBRARY_BUILTIN_ENTRIES: list[dict[str, Any]] = [
    # ── 26 regex entries (detection_method="regex", enabled=True) ──────────
    {
        "pattern_id": "significance_inflation",
        "pattern_name": "显著性通胀",
        "category": "叙事轻重",
        "severity": "high",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "promotional_language",
        "pattern_name": "宣传腔",
        "category": "宣传式描写",
        "severity": "medium",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "ai_vocabulary",
        "pattern_name": "AI 高频词汇",
        "category": "AI 词汇",
        "severity": "medium",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "negative_parallelism",
        "pattern_name": "否定式并列",
        "category": "模板句式",
        "severity": "high",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "rule_of_three",
        "pattern_name": "三项列举",
        "category": "模板句式",
        "severity": "medium",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "false_ranges",
        "pattern_name": "假范围",
        "category": "模板句式",
        "severity": "medium",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "filler_phrases",
        "pattern_name": "填充短语",
        "category": "元语言",
        "severity": "high",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "generic_conclusions",
        "pattern_name": "万能结尾",
        "category": "模板结尾",
        "severity": "high",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "hollow_aspect_marker",
        "pattern_name": "空洞进行态",
        "category": "动作虚化",
        "severity": "medium",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "em_dash_overuse",
        "pattern_name": "破折号滥用",
        "category": "标点习惯",
        "severity": "high",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "quotation_mark_misuse",
        "pattern_name": "引号强调",
        "category": "标点习惯",
        "severity": "medium",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "passive_subjectless",
        "pattern_name": "被动/无主语",
        "category": "句法虚化",
        "severity": "high",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "persuasive_authority",
        "pattern_name": "说教权威腔",
        "category": "说教腔",
        "severity": "high",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "vague_attribution",
        "pattern_name": "模糊归因",
        "category": "证据空泛",
        "severity": "medium",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "challenge_future_template",
        "pattern_name": "挑战/未来模板",
        "category": "结构模板",
        "severity": "medium",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "collaborative_artifact",
        "pattern_name": "协作对话残留",
        "category": "聊天残留",
        "severity": "critical",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "knowledge_cutoff_disclaimer",
        "pattern_name": "知识截止声明",
        "category": "聊天残留",
        "severity": "critical",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "sycophantic_tone",
        "pattern_name": "谄媚语气",
        "category": "聊天残留",
        "severity": "high",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "markdown_formatting_residue",
        "pattern_name": "Markdown/表情残留",
        "category": "格式残留",
        "severity": "high",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "outline_heading_voice",
        "pattern_name": "结构性标题/提纲腔",
        "category": "格式残留",
        "severity": "medium",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "diff_anchored_writing",
        "pattern_name": "改动叙述腔",
        "category": "元语言",
        "severity": "medium",
        "detection_method": "regex",
        "source": "builtin",
    },
    # ── Form-level AI-flavor detectors (added in response to 山风与归人2 audit) ──
    {
        "pattern_id": "weak_verb_stacking",
        "pattern_name": "弱动词堆叠",
        "category": "叙事轻重",
        "severity": "high",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "tautology_marker",
        "pattern_name": "抽象虚指三连",
        "category": "模板句式",
        "severity": "high",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "binary_judgment_closing",
        "pattern_name": "二元判断收束",
        "category": "模板结尾",
        "severity": "high",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "pronoun_disappearance_run",
        "pattern_name": "主语弱化句式",
        "category": "叙事轻重",
        "severity": "medium",
        "detection_method": "regex",
        "source": "builtin",
    },
    {
        "pattern_id": "precise_timestamp_overuse",
        "pattern_name": "精确时长堆叠",
        "category": "AI 习惯",
        "severity": "low",
        "detection_method": "regex",
        "source": "builtin",
    },
    # ── 4 llm_only entries (detection_method="llm_only", enabled=False) ───
    {
        "pattern_id": "synonym_cycling",
        "pattern_name": "同义词循环",
        "category": "重复用词",
        "severity": "medium",
        "detection_method": "llm_only",
        "enabled": False,
        "source": "builtin",
        "example_phrases": ["少年低声说。年轻人随后回答。那人又沉默下来。"],
        "keywords": ["称谓轮换", "同一角色", "机械换词"],
        "notes": "同一角色在短距离内被多个近义称谓轮换指代，需结合实体语境判断。",
    },
    {
        "pattern_id": "excessive_hedging",
        "pattern_name": "过度模糊化",
        "category": "元语言",
        "severity": "medium",
        "detection_method": "llm_only",
        "enabled": False,
        "source": "builtin",
        "example_phrases": ["或许在某种程度上，他似乎可能已经明白。"],
        "keywords": ["可能", "或许", "似乎", "模糊化"],
        "notes": "同一句或相邻句叠加多层不确定表达，削弱叙事判断。",
    },
    {
        "pattern_id": "monotone_rhythm",
        "pattern_name": "节奏单调",
        "category": "句法模式",
        "severity": "low",
        "detection_method": "llm_only",
        "enabled": False,
        "source": "builtin",
        "example_phrases": ["连续多句长度和句法骨架近似，缺少长短句变化。"],
        "keywords": ["句长近似", "句法重复", "节奏单调"],
        "notes": "需要按段落比较句长与句法骨架，不能由单个词语直接定性。",
    },
    {
        "pattern_id": "cross_chapter_template",
        "pattern_name": "跨章节结构模板",
        "category": "结构模板",
        "severity": "medium",
        "detection_method": "llm_only",
        "enabled": False,
        "source": "builtin",
        "example_phrases": ["连续章节使用相同开场、转折位置与结尾悬念骨架。"],
        "keywords": ["跨章重复", "结构模板", "节奏签名"],
        "notes": "比较相邻章节的开场、段落节奏、意象位置和收束方式。",
    },
]

# Attach the executable definition from the shared catalog.  The persisted
# library therefore contains the same expression the local prescreen executes,
# instead of being a disconnected label-only index.
for _builtin_entry in LIBRARY_BUILTIN_ENTRIES:
    _builtin_rule = HUMANIZE_RULE_MAP.get(str(_builtin_entry["pattern_id"]))
    if _builtin_rule is not None:
        _builtin_entry["detection_config"] = _builtin_rule.detection_config()
