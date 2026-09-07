"""HumanizeScanStep — AI-generated pattern detection and surgical patch repair."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.domain.humanize_rule_catalog import (
    HUMANIZE_RULES as _HUMANIZE_RULES,
)
from novel_forge.core.domain.humanize_rule_catalog import (
    HumanizeRule as _HumanizeRule,
)
from novel_forge.core.patch_engine import PatchExecutorV2, PatchOperation
from novel_forge.core.schemas.humanize import HumanizePatternHit, HumanizeReport
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.base import PipelineStep

_logger = get_logger("pipeline.humanize_scan")

_PATCHABLE_SEVERITIES = {"critical", "high", "medium"}
_PATCH_CONFIDENCE_FLOOR = 0.80
_MAX_PRESCREEN_HITS_PER_PATTERN = 4
_MAX_HIGH_SEVERITY_PRESCREEN_HITS_PER_PATTERN = 12
_MAX_LLM_REPORT_HITS = 24
_MAX_LLM_CANDIDATES_PER_BATCH = _MAX_LLM_REPORT_HITS
_MAX_LLM_PRESCREEN_HITS = _MAX_LLM_CANDIDATES_PER_BATCH
_MAX_LIBRARY_PROMPT_HITS = 12
_PRESCREEN_EVIDENCE_CHAR_LIMIT = 140
_LIBRARY_EVIDENCE_CHAR_LIMIT = 200
_SEVERITY_RANK: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1}
# Deterministic humanize_score weights — mirrors QualityGate._AI_FLAVOR_SEVERITY_WEIGHTS
_DETERMINISTIC_SCORE_WEIGHTS: dict[str, float] = {
    "critical": 4.0,
    "high": 2.0,
    "medium": 1.0,
    "low": 0.5,
}
_DETERMINISTIC_SCORE_CRITICAL_CAP: float = 5.0
_STRUCTURAL_REWRITE_CATEGORIES: frozenset[str] = frozenset(
    {"模板句式", "结构模板", "叙事轻重", "模板结尾"}
)
_REPORT_ONLY_PATTERN_IDS: frozenset[str] = frozenset({"monotone_rhythm", "cross_chapter_template"})
_RECURRENT_STRUCTURAL_MIN_OCCURRENCES = 3
_STRUCTURAL_PATTERN_FAMILIES: dict[str, frozenset[str]] = {
    "contrastive_template": frozenset(
        {
            "negative_parallelism",
            "binary_judgment_closing",
        }
    ),
}
_PROMOTABLE_PATTERN_IDS: frozenset[str] = frozenset(
    {
        "ai_vocabulary",
        "diff_anchored_writing",
        "em_dash_overuse",
        "hollow_aspect_marker",
        "persuasive_authority",
        "promotional_language",
    }
)
_CONTEXT_ADJUDICATION_REQUIRED_PATTERN_IDS: frozenset[str] = frozenset(
    {
        "filler_phrases",
        "significance_inflation",
    }
)
_HARD_EVIDENCE_PATTERN_IDS: frozenset[str] = frozenset(
    {
        "collaborative_artifact",
        "knowledge_cutoff_disclaimer",
        "markdown_formatting_residue",
        "sycophantic_tone",
    }
)
_EXACT_PHRASE_REPEAT_RE = re.compile(
    r"(?P<phrase>[\u4e00-\u9fff]{3,12})(?:[，,、；;：:\s]*)(?P=phrase)"
)
_REDUNDANT_ACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "retract_limb",
        re.compile(
            r"(?:收回|缩回|抽回|撤回|退回)(?:了|着)?"
            r"(?P<object>手指|手腕|手臂|胳膊|手|脚|腿)"
        ),
    ),
    ("grip_hand", re.compile(r"(?:握紧|攥紧|抓紧)(?:了|着)?(?P<object>拳头|手指|手)")),
    ("release_hand", re.compile(r"(?:松开|放开|撒开)(?:了|着)?(?P<object>手指|手)")),
    ("turn_body", re.compile(r"(?:转过身|转身|回身|扭身)(?P<object>)")),
    ("nod", re.compile(r"(?:点了点头|点头|颔首)(?P<object>)")),
    ("shake_head", re.compile(r"(?:摇了摇头|摇头)(?P<object>)")),
)
_SYNONYM_CYCLING_ALIAS_RE = re.compile(
    r"少年|少女|青年|女孩|男孩|男人|女人|男子|女子|老人|老者|妇人|姑娘|小伙|"
    r"对方|那人|这人|[\u4e00-\u9fff]{1,3}(?:哥|姐|叔|姨|婶|伯|爷|奶)"
)
_PATTERN_GUIDANCE: dict[str, str] = {
    "ai_vocabulary": "替换或删除说明文/AI 高频词，只做局部措辞调整。",
    "collaborative_artifact": "聊天助手残留；能安全删除整句时给空替换外的真实正文，否则 actionable=false。",
    "diff_anchored_writing": "版本变更腔；改成当下叙事动作或状态。",
    "em_dash_overuse": "非对话破折号改成逗号、句号或自然分句；不要保留重复插注。",
    "excessive_hedging": "多重不确定词只保留必要的一层。",
    "false_ranges": "压缩“从A到B/大到小”假范围，保留真实范围或动作顺序。",
    "filler_phrases": "删除元话语引导词，让叙事直接开始。",
    "generic_conclusions": "万能结尾改为具体动作、物象或情绪落点。",
    "hollow_aspect_marker": "空洞“着/了”改为具体动作或更精确动词。",
    "markdown_formatting_residue": "移除 Markdown、emoji、列表符号，只保留正文含义。",
    "negative_parallelism": "完整覆盖“不是A/没有A……而是B”片段；无法只替换证据就自然改写时 actionable=false。",
    "binary_judgment_closing": "二元判断句即使无法安全局部替换也必须保留为真实命中，并交给段落级定向改写。",
    "outline_heading_voice": "提纲/标题腔改成正文叙述，不保留编号标题格式。",
    "passive_subjectless": "被动/无主语句改为有主语的主动句。",
    "persuasive_authority": "说教判断改为可见动作、场景证据或角色感知。",
    "promotional_language": "宣传腔改为具体感官/场景细节。",
    "quotation_mark_misuse": "旁白中非必要引号去掉或改成自然表述。",
    "rule_of_three": "机械三项列举拆成两项、分句或具体动作链；不要扩大事实。",
    "significance_inflation": "里程碑式拔高改成普通叙事描述。",
    "synonym_cycling": "同一角色称谓机械轮换；只在能确认同一指代时保留，通常 actionable=false。",
    "sycophantic_tone": "客服/讨好语气直接改成正文语气。",
    "tautology_marker": "抽象虚指重复改成具体对象或动作。",
    "vague_attribution": "模糊权威归因改为具体观察或删去。",
    "weak_verb_stacking": "弱动词/感受词堆叠改为直接动作、感官或判断。",
}


@dataclass(frozen=True)
class _PatchCandidate:
    idx: int
    hit: Any
    pattern_id: str
    category: str
    original: str
    replacement: str
    paragraph_index: int | None
    abs_start: int
    abs_end: int
    promoted: bool = False

    @property
    def span_length(self) -> int:
        return max(0, self.abs_end - self.abs_start)


_HEDGE_TERMS = ("可能", "或许", "似乎", "有些", "某种程度上", "往往", "不难想象", "某种意义")


@dataclass(frozen=True)
class HumanizeScanInput:
    """Input payload for humanize pattern scan."""

    chapter_number: int
    chapter_text: str
    humanize_strength: str = "medium"
    """Scan sensitivity: 'light', 'medium', or 'aggressive'."""
    style_profile: dict[str, Any] | None = None
    """Project style profile for exemption checks."""
    filter_dialogue: bool = True
    """Whether to exclude dialogue content from scanning."""
    context_capsule: dict[str, Any] | None = None
    """Small chapter context for final AI-pattern cleanup. Not an audit schema."""
    library_hits: list[dict[str, Any]] = field(default_factory=list)
    """Library pattern hits from HumanizeLibraryRetriever, used in prompt context."""
    library_enabled: bool = True
    """Whether library-based pattern retrieval is enabled."""
    library_enabled_pattern_ids: frozenset[str] | None = None
    """Enabled library IDs; ``None`` means local fallback with all builtin rules."""
    prior_chapter_signatures: list[dict[str, Any]] = field(default_factory=list)
    """Rhythm signatures of preceding N chapters for cross-chapter template detection."""


@dataclass(frozen=True)
class HumanizeScanResult:
    """Output from the humanize pattern scan step."""

    report: HumanizeReport
    revised_text: str
    patches_applied: int
    paragraph_rewrites_applied: int = 0


class HumanizeScanStep(PipelineStep[HumanizeScanInput, HumanizeScanResult]):
    """Scan chapter text for AI-generated patterns and apply surgical fixes.

    Flow:
    1. Render prompt via ``TaskType.HUMANIZE_SCAN`` template.
    2. Call LLM to produce a ``HumanizeReport`` (JSON).
    3. If critical hits found, build ``PatchOperation`` list from evidence/suggestion pairs.
    4. Apply patches via ``PatchExecutorV2``; fall back to original text on failure.
    """

    @property
    def step_name(self) -> str:
        return "humanize_scan"

    # ── Local prescreen ───────────────────────────────────────────────────────

    @classmethod
    def prescreen_text(
        cls,
        chapter_text: str,
        *,
        filter_dialogue: bool = True,
        enabled_pattern_ids: frozenset[str] | set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return deterministic AI-style candidates for LLM adjudication.

        The prescreen is intentionally conservative: it only surfaces likely
        candidates and never mutates text by itself.
        """
        if not chapter_text:
            return []

        masked = cls._mask_dialogue(chapter_text) if filter_dialogue else chapter_text
        hits: list[HumanizePatternHit] = []
        for rule in _HUMANIZE_RULES:
            if enabled_pattern_ids is not None and rule.pattern_id not in enabled_pattern_ids:
                continue
            per_pattern = 0
            per_pattern_limit = (
                _MAX_HIGH_SEVERITY_PRESCREEN_HITS_PER_PATTERN
                if rule.severity in {"critical", "high"}
                else _MAX_PRESCREEN_HITS_PER_PATTERN
            )
            for match in rule.regex.finditer(masked):
                evidence = chapter_text[match.start() : match.end()].strip()
                if not evidence:
                    continue
                hits.append(
                    cls._prescreen_hit(rule, evidence, chapter_text, match.start(), match.end())
                )
                per_pattern += 1
                if per_pattern >= per_pattern_limit:
                    break

        if enabled_pattern_ids is None or "excessive_hedging" in enabled_pattern_ids:
            hits.extend(cls._prescreen_hedging(chapter_text, masked))
        if enabled_pattern_ids is None or "synonym_cycling" in enabled_pattern_ids:
            hits.extend(cls._prescreen_synonym_cycling(chapter_text, masked))
        if enabled_pattern_ids is None or "monotone_rhythm" in enabled_pattern_ids:
            hits.extend(cls._prescreen_monotone_rhythm(chapter_text, masked))
        hits = cls._rank_prescreen_hits(hits)
        return [cls._hit_prompt_payload(hit) for hit in hits]

    @staticmethod
    def _pattern_family(pattern_id: str) -> str:
        """Return the comparison family used for overlap and recurrence checks."""

        normalized = str(pattern_id or "").strip()
        for family, members in _STRUCTURAL_PATTERN_FAMILIES.items():
            if normalized in members:
                return family
        return normalized

    @classmethod
    def _canonicalize_prescreen_candidates(
        cls,
        hits: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Remove same-family overlap without hiding distinct source locations.

        ``negative_parallelism`` and ``binary_judgment_closing`` often describe
        the same sentence.  Sending both consumes adjudication slots and makes
        the model mistake taxonomy overlap for uncertainty.  Keep the cleaner
        explicit contrast anchor, while retaining non-overlapping binary forms.
        """

        canonical: list[dict[str, Any]] = []
        for raw_hit in hits:
            hit = dict(raw_hit)
            family = cls._pattern_family(str(hit.get("pattern_id") or ""))
            start = cls._coerce_non_negative_int(hit.get("span_start"))
            end = cls._coerce_non_negative_int(hit.get("span_end"))
            if not family or start is None or end is None or end <= start:
                canonical.append(hit)
                continue

            overlap_index: int | None = None
            for index, existing in enumerate(canonical):
                if cls._pattern_family(str(existing.get("pattern_id") or "")) != family:
                    continue
                existing_start = cls._coerce_non_negative_int(existing.get("span_start"))
                existing_end = cls._coerce_non_negative_int(existing.get("span_end"))
                if existing_start is None or existing_end is None:
                    continue
                if max(start, existing_start) < min(end, existing_end):
                    overlap_index = index
                    break
            if overlap_index is None:
                canonical.append(hit)
                continue

            existing = canonical[overlap_index]
            existing_id = str(existing.get("pattern_id") or "")
            hit_id = str(hit.get("pattern_id") or "")
            existing_evidence = str(existing.get("evidence_quote") or "")
            hit_evidence = str(hit.get("evidence_quote") or "")

            def preference(pattern_id: str, evidence: str) -> tuple[int, int, int]:
                explicit_contrast = int(
                    pattern_id == "negative_parallelism"
                    and any(token in evidence for token in ("而是", "更是", "恰恰是"))
                )
                complete_sentence = int(evidence.rstrip().endswith(("。", "！", "？", "!", "?")))
                return explicit_contrast, complete_sentence, len(evidence)

            if preference(hit_id, hit_evidence) > preference(existing_id, existing_evidence):
                canonical[overlap_index] = hit
        return canonical

    @classmethod
    def _candidate_family_counts(cls, hits: list[dict[str, Any]]) -> dict[str, int]:
        counts = Counter(
            cls._pattern_family(str(hit.get("pattern_id") or ""))
            for hit in hits
            if str(hit.get("pattern_id") or "").strip()
        )
        return dict(counts)

    @staticmethod
    def _hit_prompt_payload(hit: HumanizePatternHit) -> dict[str, Any]:
        return hit.model_dump(mode="json", exclude={"schema_version", "created_at"})

    @staticmethod
    def _rank_prescreen_hits(hits: list[HumanizePatternHit]) -> list[HumanizePatternHit]:
        """Rank deterministic hits before prompt batching.

        Earlier versions stopped as soon as one noisy pattern filled the cap
        (notably ``rule_of_three``), starving later high-severity patterns such
        as markdown residue, chat residue, and em-dash overuse.  Ranking after
        per-pattern collection keeps earlier batches focused on the most
        repairable signals without globally dropping later candidates.
        """

        def sort_key(item: tuple[int, HumanizePatternHit]) -> tuple[int, bool, float, int, int]:
            index, hit = item
            severity_rank = _SEVERITY_RANK.get(str(hit.severity), 0)
            span_start = hit.span_start if isinstance(hit.span_start, int) else index
            return (-severity_rank, not hit.actionable, -float(hit.confidence), span_start, index)

        return [hit for _index, hit in sorted(enumerate(hits), key=sort_key)]

    @classmethod
    def _prescreen_hit(
        cls,
        rule: _HumanizeRule,
        evidence: str,
        chapter_text: str,
        start: int,
        end: int,
    ) -> HumanizePatternHit:
        suggestion = cls._local_suggestion(rule.pattern_id, evidence)
        return HumanizePatternHit(
            pattern_id=rule.pattern_id,
            pattern_name=rule.pattern_name,
            category=rule.category,
            severity=rule.severity,
            evidence_quote=evidence,
            paragraph_index=cls._paragraph_index_for_offset(chapter_text, start),
            suggestion=suggestion,
            confidence=rule.confidence,
            actionable=rule.actionable and bool(suggestion),
            source="local",
            span_start=start,
            span_end=end,
        )

    @classmethod
    def _prescreen_hedging(cls, chapter_text: str, masked_text: str) -> list[HumanizePatternHit]:
        hits: list[HumanizePatternHit] = []
        for sentence, start, end in cls._iter_sentences(masked_text):
            term_count = sum(1 for term in _HEDGE_TERMS if term in sentence)
            if term_count < 2:
                continue
            evidence = chapter_text[start:end].strip()
            if not evidence:
                continue
            hits.append(
                HumanizePatternHit(
                    pattern_id="excessive_hedging",
                    pattern_name="过度缓冲",
                    category="语气虚化",
                    severity="medium",
                    evidence_quote=evidence,
                    paragraph_index=cls._paragraph_index_for_offset(chapter_text, start),
                    suggestion=cls._local_suggestion("excessive_hedging", evidence),
                    confidence=0.86,
                    actionable=False,
                    source="local",
                    span_start=start,
                    span_end=end,
                )
            )
        return hits

    @classmethod
    def _prescreen_monotone_rhythm(
        cls,
        chapter_text: str,
        masked_text: str,
    ) -> list[HumanizePatternHit]:
        hits: list[HumanizePatternHit] = []
        for paragraph_index, paragraph, start, _end in cls._iter_paragraphs(masked_text):
            sentences = [s.strip() for s in re.split(r"[。！？]", paragraph) if s.strip()]
            if len(sentences) < 4:
                continue
            lengths = [len(s) for s in sentences[:6] if len(s) >= 12]
            if len(lengths) < 4:
                continue
            avg = sum(lengths) / len(lengths)
            if avg <= 0:
                continue
            if max(abs(length - avg) for length in lengths) / avg > 0.18:
                continue
            evidence = chapter_text[start : start + len(paragraph)].strip()[:120]
            if not evidence:
                continue
            hits.append(
                HumanizePatternHit(
                    pattern_id="monotone_rhythm",
                    pattern_name="节奏单调",
                    category="节奏问题",
                    severity="medium",
                    evidence_quote=evidence,
                    paragraph_index=paragraph_index,
                    suggestion="调整相邻句长，穿插短句、动作句或感官锚点。",
                    confidence=0.78,
                    actionable=False,
                    source="local",
                    span_start=start,
                    span_end=start + len(paragraph),
                )
            )
        return hits

    @classmethod
    def _prescreen_synonym_cycling(
        cls,
        chapter_text: str,
        masked_text: str,
    ) -> list[HumanizePatternHit]:
        hits: list[HumanizePatternHit] = []
        for paragraph_index, paragraph, start, _end in cls._iter_paragraphs(masked_text):
            if len(paragraph.strip()) < 20:
                continue
            alias_matches = list(_SYNONYM_CYCLING_ALIAS_RE.finditer(paragraph))
            if len(alias_matches) < 2:
                continue
            aliases = {match.group(0) for match in alias_matches}
            pronoun_count = len(re.findall(r"[他她]", paragraph))
            if len(aliases) < 3 and not (len(aliases) >= 2 and pronoun_count >= 2):
                continue
            evidence = chapter_text[start : start + len(paragraph)].strip()[:160]
            if not evidence:
                continue
            hits.append(
                HumanizePatternHit(
                    pattern_id="synonym_cycling",
                    pattern_name="同义替换/称谓轮换",
                    category="称谓漂移",
                    severity="medium",
                    evidence_quote=evidence,
                    paragraph_index=paragraph_index,
                    suggestion="",
                    confidence=0.76,
                    actionable=False,
                    source="local",
                    span_start=start,
                    span_end=start + min(len(paragraph), len(evidence)),
                )
            )
        return hits

    @classmethod
    def _prescreen_cross_chapter_structure(
        cls,
        chapter_text: str,
        prior_signatures: list[dict[str, Any]],
    ) -> list[HumanizePatternHit]:
        """Detect when the current chapter's rhythm is near-identical to recent chapters.

        Cross-chapter structural templates are the "same pacing pattern every
        chapter" failure mode that single-chapter humanize cannot catch. A
        chapter whose rhythm signature scores >0.85 against any of its
        predecessors is flagged as a structural template. The hit is marked
        ``actionable=False`` (report only, no automatic rewrite) because
        cross-chapter rhythm changes require human editorial judgment.
        """
        if not prior_signatures or not chapter_text.strip():
            return []

        from novel_forge.core.utils.rhythm_metrics import (
            compute_chapter_rhythm_signature,
            compute_structure_similarity,
        )

        current = compute_chapter_rhythm_signature(chapter_text)
        threshold = 0.85
        hits: list[HumanizePatternHit] = []

        for prior in prior_signatures[:3]:
            if not isinstance(prior, dict):
                continue
            similarity = compute_structure_similarity(current, prior)
            if similarity >= threshold:
                prior_chapter = prior.get("chapter_number", "?")
                evidence = (
                    f"当前章节奏签名与前章 {prior_chapter} 高度相似"
                    f"（相似度 {similarity:.2f}），可能存在结构模板化"
                )
                hits.append(
                    HumanizePatternHit(
                        pattern_id="cross_chapter_template",
                        pattern_name="跨章结构模板",
                        category="结构模板",
                        severity="medium",
                        evidence_quote=evidence,
                        paragraph_index=0,
                        suggestion=(
                            "本章节奏与前章过于接近。请考虑：增加一段短句密集段制造窒息感，"
                            "或将意象出现位置从章首移到章中/章尾，打破可预测的结构模板。"
                        ),
                        confidence=similarity,
                        actionable=False,
                        source="local",
                    )
                )
                break  # One hit is enough; avoid spamming the prompt.
        return hits

    # ── Public entry ──────────────────────────────────────────────────────────

    async def _execute(self, input_data: HumanizeScanInput) -> HumanizeScanResult:
        max_tokens = self._dynamic_max_tokens(
            TaskType.HUMANIZE_SCAN,
            max(1200, len(input_data.chapter_text) // 3),
            prompt_overhead=2000,
            min_tokens=2048,
        )

        prescreen_kwargs: dict[str, Any] = {"filter_dialogue": input_data.filter_dialogue}
        if input_data.library_enabled_pattern_ids is not None:
            prescreen_kwargs["enabled_pattern_ids"] = input_data.library_enabled_pattern_ids
        prescreen_hits = self._canonicalize_prescreen_candidates(
            self.prescreen_text(input_data.chapter_text, **prescreen_kwargs)
        )
        library_hits = self._remove_duplicate_library_candidates(
            prescreen_hits,
            input_data.library_hits,
        )
        # Cross-chapter structural-template check sits alongside prescreen hits
        # so the LLM sees them in the same context block.
        if input_data.prior_chapter_signatures and (
            input_data.library_enabled_pattern_ids is None
            or "cross_chapter_template" in input_data.library_enabled_pattern_ids
        ):
            cross_hits = self._prescreen_cross_chapter_structure(
                input_data.chapter_text, input_data.prior_chapter_signatures
            )
            if cross_hits:
                prescreen_hits = list(prescreen_hits) + [
                    self._hit_prompt_payload(h) for h in cross_hits
                ]
        if not prescreen_hits and not library_hits:
            _logger.info(
                "humanize_scan | chapter=%d | no local/library candidates — skipping LLM",
                input_data.chapter_number,
            )
            return self._empty_result(input_data.chapter_text, input_data.chapter_number)

        # ── P0-2: Punctuation-only fast-path (no LLM needed) ───────────────
        working_text = input_data.chapter_text
        deterministic_applied = 0
        deterministic_patterns: list[str] = []
        working_text, deterministic_applied, deterministic_patterns = (
            self._deterministic_patch_pass(working_text, prescreen_hits, input_data.chapter_number)
        )
        # Remove deterministic-safe hits from prescreen so LLM doesn't re-process
        if deterministic_patterns:
            deterministic_set = set(deterministic_patterns)
            prescreen_hits = [
                h for h in prescreen_hits if str(h.get("pattern_id") or "") not in deterministic_set
            ]
            library_hits = [
                h for h in library_hits if str(h.get("pattern_id") or "") not in deterministic_set
            ]

        # If no remaining hits for LLM, return deterministic result directly
        if not prescreen_hits and not library_hits:
            _logger.info(
                "humanize_scan | chapter=%d | all hits resolved deterministically (%d patches)",
                input_data.chapter_number,
                deterministic_applied,
            )
            report = self._fallback_report_from_candidates(
                working_text,
                input_data.chapter_number,
                [],
                [],
                reason="deterministic_only",
            )
            return HumanizeScanResult(
                report=report,
                revised_text=working_text,
                patches_applied=deterministic_applied,
                paragraph_rewrites_applied=0,
            )

        prescreen_family_counts = self._candidate_family_counts(prescreen_hits)
        provider_hint, model_hint = self._humanize_route_override()

        # ── LLM call: bounded batches, no global candidate loss ───────────────
        batch_reports: list[HumanizeReport] = []
        batches = self._candidate_batches(
            prescreen_hits,
            library_hits,
            batch_size=_MAX_LLM_CANDIDATES_PER_BATCH,
        )
        for batch_index, (batch_prescreen_hits, batch_library_hits) in enumerate(
            batches,
            start=1,
        ):
            llm_context = self._build_llm_context(
                input_data,
                batch_prescreen_hits,
                library_hits=batch_library_hits,
                prescreen_hit_count=len(prescreen_hits),
                prescreen_family_counts=prescreen_family_counts,
                batch_index=batch_index,
                batch_count=len(batches),
            )
            report: HumanizeReport | None = None
            try:
                data = await self._call_with_retry(
                    TaskType.HUMANIZE_SCAN,
                    llm_context,
                    max_tokens=max_tokens,
                    temperature=0.2,
                    provider=provider_hint,
                    model_id=model_hint,
                )
            except Exception:
                _logger.warning(
                    "humanize_scan_failed | chapter=%d batch=%d/%d — falling back to local candidates",
                    input_data.chapter_number,
                    batch_index,
                    len(batches),
                )
                report = self._fallback_report_from_candidates(
                    input_data.chapter_text,
                    input_data.chapter_number,
                    batch_prescreen_hits,
                    batch_library_hits,
                    reason="llm_failed",
                )

            if report is None:
                report = self._parse_report(
                    data, input_data.chapter_number, input_data.chapter_text
                )
            if report is None:
                report = self._fallback_report_from_candidates(
                    input_data.chapter_text,
                    input_data.chapter_number,
                    batch_prescreen_hits,
                    batch_library_hits,
                    reason="llm_invalid_report",
                )
            if report is not None:
                batch_reports.append(report)

        report = self._merge_reports(
            batch_reports,
            input_data.chapter_number,
            working_text,
        )
        if report is None:
            return self._empty_result(working_text, input_data.chapter_number)
        report = self._reconcile_report_with_prescreen(
            report,
            prescreen_hits,
            chapter_number=input_data.chapter_number,
            chapter_text=working_text,
            recurrent_min_occurrences=int(
                getattr(
                    self.settings,
                    "humanize_recurrent_structural_min_occurrences",
                    _RECURRENT_STRUCTURAL_MIN_OCCURRENCES,
                )
            ),
        )

        # ── No safe local patches → report only ──────────────────────────────
        patch_confidence_floor = float(
            getattr(self.settings, "humanize_patch_confidence_floor", _PATCH_CONFIDENCE_FLOOR)
        )
        patches, unpatchable_hits = self._build_patches(
            report.pattern_hits,
            working_text,
            chapter_number=input_data.chapter_number,
            patch_confidence_floor=patch_confidence_floor,
        )
        report = report.model_copy(
            update={
                "patchable_hits": len(patches),
                "unpatchable_hits": unpatchable_hits,
            }
        )
        # ── Apply patches and paragraph-level rewrite ───────────────────────────
        revised_text = working_text
        patches_applied = 0
        paragraph_rewrites = 0

        if patches:
            revised_text, patches_applied = self._apply_patches(
                working_text,
                patches,
                input_data.chapter_number,
            )

        rewrite_threshold = int(getattr(self.settings, "humanize_paragraph_rewrite_threshold", 3))
        paragraph_confidence_floor = float(
            getattr(self.settings, "humanize_paragraph_confidence_floor", 0.70)
        )
        rewrite_candidates = self._collect_affected_paragraphs(
            report.pattern_hits,
            min_confidence=paragraph_confidence_floor,
            current_text=revised_text,
        )
        rewrite_candidate_count = sum(len(items) for items in rewrite_candidates.values())
        if rewrite_candidate_count >= rewrite_threshold:
            revised_text, paragraph_rewrites = await self._paragraph_level_rewrite(
                revised_text,
                report,
                input_data.chapter_number,
                style_profile=input_data.style_profile,
                context_capsule=input_data.context_capsule,
                min_confidence=paragraph_confidence_floor,
            )

        # P0-2: Accumulate deterministic patches into total
        total_patches_applied = patches_applied + deterministic_applied

        _logger.info(
            "humanize_scan | chapter=%d | score=%.1f | hits=%d | patches=%d/%d | deterministic=%d | unpatchable=%d | para_rewrites=%d",
            input_data.chapter_number,
            report.humanize_score,
            report.total_hits,
            patches_applied,
            len(patches) if patches else 0,
            deterministic_applied,
            unpatchable_hits,
            paragraph_rewrites,
        )

        return HumanizeScanResult(
            report=report,
            revised_text=revised_text,
            patches_applied=total_patches_applied,
            paragraph_rewrites_applied=paragraph_rewrites,
        )

    @classmethod
    def _remove_duplicate_library_candidates(
        cls,
        prescreen_hits: list[dict[str, Any]],
        library_hits: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Prefer richer local candidates for identical library-backed spans."""

        local_spans = {
            (
                str(hit.get("pattern_id") or ""),
                cls._coerce_non_negative_int(hit.get("span_start")),
                cls._coerce_non_negative_int(hit.get("span_end")),
            )
            for hit in prescreen_hits
        }
        filtered: list[dict[str, Any]] = []
        seen: set[tuple[str, int | None, int | None]] = set()
        accepted = list(prescreen_hits)
        for hit in library_hits:
            key = (
                str(hit.get("pattern_id") or ""),
                cls._coerce_non_negative_int(hit.get("span_start")),
                cls._coerce_non_negative_int(hit.get("span_end")),
            )
            if key in local_spans or key in seen:
                continue
            family = cls._pattern_family(key[0])
            if key[1] is not None and key[2] is not None:
                overlaps_same_family = any(
                    cls._pattern_family(str(existing.get("pattern_id") or "")) == family
                    and (existing_start := cls._coerce_non_negative_int(existing.get("span_start")))
                    is not None
                    and (existing_end := cls._coerce_non_negative_int(existing.get("span_end")))
                    is not None
                    and max(key[1], existing_start) < min(key[2], existing_end)
                    for existing in accepted
                )
                if overlaps_same_family:
                    continue
            seen.add(key)
            filtered.append(hit)
            accepted.append(hit)
        return filtered

    # ── Context building ──────────────────────────────────────────────────────

    @classmethod
    def _build_llm_context(
        cls,
        input_data: HumanizeScanInput,
        prescreen_hits: list[dict[str, Any]] | None = None,
        *,
        library_hits: list[dict[str, Any]] | None = None,
        prescreen_hit_count: int | None = None,
        prescreen_family_counts: dict[str, int] | None = None,
        batch_index: int | None = None,
        batch_count: int | None = None,
    ) -> dict[str, Any]:
        """Build the context dict passed to the prompt template."""
        prompt_prescreen_hits = cls._compact_prescreen_hits_for_prompt(
            prescreen_hits or [],
            limit=_MAX_LLM_PRESCREEN_HITS,
        )
        raw_library_hits = library_hits if library_hits is not None else input_data.library_hits
        prompt_library_hits = cls._compact_library_hits_for_prompt(
            raw_library_hits or [],
            input_data.chapter_text,
            limit=_MAX_LIBRARY_PROMPT_HITS,
        )
        prompt_candidates = [*prompt_prescreen_hits, *prompt_library_hits]
        chapter_text_for_prompt = (
            cls._chapter_context_for_prescreen_prompt(
                input_data.chapter_text,
                prompt_candidates,
            )
            if prompt_candidates
            else input_data.chapter_text
        )
        context: dict[str, Any] = {
            "chapter_number": input_data.chapter_number,
            "chapter_text": chapter_text_for_prompt,
            "humanize_strength": input_data.humanize_strength,
            "filter_dialogue": input_data.filter_dialogue,
            "prescreen_hits": prompt_prescreen_hits,
            "prescreen_hit_count": (
                prescreen_hit_count
                if prescreen_hit_count is not None
                else len(prescreen_hits or [])
            ),
            "prescreen_adjudication_only": bool(prompt_candidates),
            "max_report_hits": _MAX_LLM_REPORT_HITS,
            "pattern_guidance": cls._pattern_guidance_for_prompt(prompt_candidates),
        }
        if prescreen_family_counts:
            context["prescreen_family_counts"] = dict(prescreen_family_counts)
        if batch_index is not None and batch_count is not None:
            context["candidate_batch"] = {
                "index": batch_index,
                "count": batch_count,
                "candidate_count": len(prompt_candidates),
            }
        if input_data.style_profile is not None:
            context["style_profile"] = cls._compact_style_profile_for_prompt(
                input_data.style_profile
            )
        if input_data.context_capsule is not None:
            context["humanize_context"] = input_data.context_capsule

        if prompt_library_hits:
            context["library_hits"] = prompt_library_hits
            if len(raw_library_hits or []) > _MAX_LIBRARY_PROMPT_HITS:
                _logger.warning(
                    "humanize_library_hits_truncated | original=%d truncated=%d",
                    len(raw_library_hits or []),
                    _MAX_LIBRARY_PROMPT_HITS,
                )
        context["library_enabled"] = input_data.library_enabled

        return context

    @staticmethod
    def _candidate_batches(
        prescreen_hits: list[dict[str, Any]],
        library_hits: list[dict[str, Any]],
        *,
        batch_size: int,
    ) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
        """Split all candidates into bounded model batches without dropping any."""
        size = max(1, batch_size)
        tagged: list[tuple[str, dict[str, Any]]] = [
            *[("prescreen", hit) for hit in prescreen_hits],
            *[("library", hit) for hit in library_hits],
        ]
        batches: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
        for index in range(0, len(tagged), size):
            prescreen_batch: list[dict[str, Any]] = []
            library_batch: list[dict[str, Any]] = []
            for kind, hit in tagged[index : index + size]:
                if kind == "library":
                    library_batch.append(hit)
                else:
                    prescreen_batch.append(hit)
            batches.append((prescreen_batch, library_batch))
        return batches

    @classmethod
    def _compact_prescreen_hits_for_prompt(
        cls,
        prescreen_hits: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return a compact prompt payload for candidate adjudication."""
        compact: list[dict[str, Any]] = []
        keep_keys = (
            "pattern_id",
            "pattern_name",
            "category",
            "severity",
            "evidence_quote",
            "paragraph_index",
            "suggestion",
            "confidence",
            "source",
            "span_start",
            "span_end",
        )
        for hit in prescreen_hits[: max(0, limit)]:
            item = {
                key: hit.get(key)
                for key in keep_keys
                if key in hit and not (key == "suggestion" and not str(hit.get(key) or ""))
            }
            evidence = str(item.get("evidence_quote") or "")
            if len(evidence) > _PRESCREEN_EVIDENCE_CHAR_LIMIT:
                item["evidence_quote"] = evidence[:_PRESCREEN_EVIDENCE_CHAR_LIMIT].rstrip()
            compact.append(item)
        return compact

    @classmethod
    def _compact_library_hits_for_prompt(
        cls,
        library_hits: list[dict[str, Any]],
        chapter_text: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return compact library retrieval hits as adjudication candidates."""
        compact: list[dict[str, Any]] = []
        keep_keys = (
            "pattern_id",
            "pattern_name",
            "category",
            "severity",
            "evidence_quote",
            "paragraph_index",
            "span_start",
            "span_end",
            "match_method",
            "source",
        )
        for hit in library_hits[: max(0, limit)]:
            item = {key: hit.get(key) for key in keep_keys if key in hit}
            item["source"] = cls._library_prompt_source(hit.get("source"))
            evidence = str(item.get("evidence_quote") or "")
            if len(evidence) > _LIBRARY_EVIDENCE_CHAR_LIMIT:
                item["evidence_quote"] = evidence[:_LIBRARY_EVIDENCE_CHAR_LIMIT].rstrip()
            if "paragraph_index" not in item:
                paragraph_index = cls._resolve_hit_paragraph_index(item, chapter_text)
                if paragraph_index is not None:
                    item["paragraph_index"] = paragraph_index
            compact.append(item)
        return compact

    @staticmethod
    def _library_prompt_source(source: Any) -> str:
        raw = str(source or "").strip().lower()
        if raw == "user":
            return "library_user"
        return "library"

    @staticmethod
    def _compact_style_profile_for_prompt(
        style_profile: dict[str, Any],
        *,
        max_banned_phrases: int = 20,
    ) -> dict[str, Any]:
        """Keep only style fields needed to avoid false positives."""
        if not isinstance(style_profile, dict):
            return {}

        compact: dict[str, Any] = {}
        summary = str(style_profile.get("summary") or "").strip()
        if summary:
            compact["summary"] = summary[:240]

        global_style = style_profile.get("global_style")
        if isinstance(global_style, dict):
            compact_global: dict[str, Any] = {}
            for key in (
                "dialogue_ratio",
                "pace_mode",
                "emotional_style",
                "environment_ratio",
                "info_density",
            ):
                if global_style.get(key) not in (None, "", [], {}):
                    compact_global[key] = global_style.get(key)
            banned_phrases = [
                str(item).strip()
                for item in global_style.get("banned_phrases", [])
                if str(item).strip()
            ]
            if banned_phrases:
                compact_global["banned_phrases"] = banned_phrases[:max_banned_phrases]
            if compact_global:
                compact["global_style"] = compact_global

        return compact

    @staticmethod
    def _pattern_guidance_for_prompt(
        prompt_prescreen_hits: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Return concise guidance only for patterns present in prompt candidates."""
        seen: set[str] = set()
        guidance: list[dict[str, str]] = []
        for hit in prompt_prescreen_hits:
            pattern_id = str(hit.get("pattern_id") or "").strip()
            if not pattern_id or pattern_id in seen:
                continue
            seen.add(pattern_id)
            note = _PATTERN_GUIDANCE.get(pattern_id)
            if note:
                guidance.append({"pattern_id": pattern_id, "rule": note})
        return guidance

    @staticmethod
    def _chapter_context_for_prescreen_prompt(
        chapter_text: str,
        prompt_prescreen_hits: list[dict[str, Any]],
        *,
        max_chars: int = 2800,
        max_paragraph_chars: int = 520,
    ) -> str:
        """Return bounded source context for candidate-only adjudication."""
        if not chapter_text:
            return ""
        paragraphs = chapter_text.split("\n\n")
        selected: list[tuple[int, str]] = []
        seen: set[int] = set()
        for hit in prompt_prescreen_hits:
            paragraph_index = HumanizeScanStep._coerce_non_negative_int(hit.get("paragraph_index"))
            if paragraph_index is None:
                paragraph_index = HumanizeScanStep._resolve_hit_paragraph_index(
                    hit,
                    chapter_text,
                )
            if paragraph_index is None or paragraph_index in seen:
                continue
            if 0 <= paragraph_index < len(paragraphs):
                paragraph = paragraphs[paragraph_index].strip()
                if paragraph:
                    selected.append((paragraph_index, paragraph[:max_paragraph_chars]))
                    seen.add(paragraph_index)

        if not selected:
            return chapter_text[:max_chars].rstrip()

        parts: list[str] = []
        total = 0
        for paragraph_index, paragraph in selected:
            entry = f"[段落 {paragraph_index}]\n{paragraph}"
            projected = total + len(entry) + (2 if parts else 0)
            if projected > max_chars and parts:
                break
            parts.append(entry)
            total = projected
        return "\n\n".join(parts)[:max_chars].rstrip()

    def _humanize_route_override(self) -> tuple[str | None, str | None]:
        """Return the optional provider/model override for humanize scan."""
        override = str(getattr(self.settings, "humanize_model", "") or "").strip()
        if not override:
            return None, None
        if ":" not in override:
            return None, override
        provider, model_id = override.split(":", 1)
        return provider.strip() or None, model_id.strip() or None

    # ── Report parsing ────────────────────────────────────────────────────────

    @classmethod
    def _fallback_report_from_prescreen(
        cls,
        chapter_text: str,
        chapter_number: int,
        prescreen_hits: list[dict[str, Any]],
        *,
        reason: str,
    ) -> HumanizeReport | None:
        """Build a valid local report when the LLM scan cannot produce JSON."""
        if not prescreen_hits:
            return None

        payload: dict[str, Any] = {
            "total_hits": len(prescreen_hits),
            "hits_by_category": {},
            "critical_hits": 0,
            "pattern_hits": prescreen_hits,
            "humanize_score": cls._estimate_humanize_score(prescreen_hits),
            "summary": (
                "LLM 拟人化扫描未返回可用结构化报告，"
                f"已使用本地确定性预扫描结果继续流程（reason={reason}）。"
            ),
        }
        report = cls._parse_report(payload, chapter_number, chapter_text)
        if report is None:
            _logger.warning("humanize_scan: local prescreen fallback report failed to validate")
        return report

    @classmethod
    def _fallback_report_from_candidates(
        cls,
        chapter_text: str,
        chapter_number: int,
        prescreen_hits: list[dict[str, Any]],
        library_hits: list[dict[str, Any]],
        *,
        reason: str,
    ) -> HumanizeReport | None:
        """Build a local report from one failed adjudication batch."""
        report_hits = [
            *prescreen_hits,
            *cls._compact_library_hits_for_prompt(
                library_hits,
                chapter_text,
                limit=max(len(library_hits), 1),
            ),
        ]
        return cls._fallback_report_from_prescreen(
            chapter_text,
            chapter_number,
            report_hits,
            reason=reason,
        )

    @classmethod
    def _merge_reports(
        cls,
        reports: list[HumanizeReport],
        chapter_number: int,
        chapter_text: str,
    ) -> HumanizeReport | None:
        """Merge per-batch adjudication reports into a single chapter report."""
        if not reports:
            return None

        merged_hits: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int]] = set()
        for report in reports:
            for hit in report.pattern_hits:
                evidence = str(hit.evidence_quote or "").strip()
                key = (str(hit.pattern_id), evidence, int(hit.paragraph_index or 0))
                if not evidence or key in seen:
                    continue
                seen.add(key)
                merged_hits.append(
                    hit.model_dump(mode="json", exclude={"schema_version", "created_at"})
                )

        payload: dict[str, Any] = {
            "total_hits": len(merged_hits),
            "hits_by_category": {},
            "critical_hits": sum(
                1 for hit in merged_hits if str(hit.get("severity", "")).lower() == "critical"
            ),
            "pattern_hits": merged_hits,
            "humanize_score": cls._compute_deterministic_score(merged_hits),
            "summary": cls._merge_report_summaries(reports, len(merged_hits)),
        }
        return cls._parse_report(payload, chapter_number, chapter_text)

    @classmethod
    def _reconcile_report_with_prescreen(
        cls,
        report: HumanizeReport,
        prescreen_hits: list[dict[str, Any]],
        *,
        chapter_number: int,
        chapter_text: str,
        recurrent_min_occurrences: int = _RECURRENT_STRUCTURAL_MIN_OCCURRENCES,
    ) -> HumanizeReport:
        """Rebind model verdicts and preserve deterministic recurrent structure.

        The model decides whether a candidate is a semantic false positive, but
        ``actionable`` is an independent repair-routing decision.  A repeated,
        high-confidence structural family must remain visible even when the
        model cannot produce a safe one-span replacement; otherwise the
        paragraph-rewrite path never receives the issue.
        """

        candidates = cls._canonicalize_prescreen_candidates(prescreen_hits)
        candidate_rows = [dict(hit) for hit in candidates]
        used_candidates: set[int] = set()
        rebound_hits: list[dict[str, Any]] = []

        for report_hit in report.pattern_hits:
            raw = report_hit.model_dump(mode="json", exclude={"schema_version", "created_at"})
            pattern_id = str(raw.get("pattern_id") or "")
            evidence = str(raw.get("evidence_quote") or "").strip()
            paragraph_index = cls._coerce_non_negative_int(raw.get("paragraph_index")) or 0
            eligible: list[int] = []
            for index, candidate in enumerate(candidate_rows):
                if index in used_candidates:
                    continue
                candidate_id = str(candidate.get("pattern_id") or "")
                candidate_evidence = str(candidate.get("evidence_quote") or "").strip()
                if candidate_evidence != evidence:
                    continue
                if candidate_id == pattern_id or cls._pattern_family(
                    candidate_id
                ) == cls._pattern_family(pattern_id):
                    eligible.append(index)
            if eligible:
                selected = min(
                    eligible,
                    key=lambda index: abs(
                        (
                            cls._coerce_non_negative_int(
                                candidate_rows[index].get("paragraph_index")
                            )
                            or 0
                        )
                        - paragraph_index
                    ),
                )
                used_candidates.add(selected)
                anchor = candidate_rows[selected]
                # P0-1: Preserve span from prescreen candidate (never allow LLM to clear)
                anchor_span_start = cls._coerce_non_negative_int(anchor.get("span_start"))
                anchor_span_end = cls._coerce_non_negative_int(anchor.get("span_end"))
                # P0-1: Preserve local suggestions only for context-free repairs.
                llm_suggestion = str(raw.get("suggestion") or "").strip()
                anchor_suggestion = str(anchor.get("suggestion") or "").strip()
                requires_context_adjudication = (
                    pattern_id in _CONTEXT_ADJUDICATION_REQUIRED_PATTERN_IDS
                )
                adjudicated_source = str(raw.get("source") or "") in {"llm", "merged"}
                llm_actionable = bool(raw.get("actionable", False))
                if requires_context_adjudication:
                    # Word deletion can change cadence, emphasis, or grammar.  A
                    # local regex hit is evidence, not repair authorization: only
                    # an affirmative contextual verdict with a concrete replacement
                    # may reach the patch gate.
                    effective_suggestion = (
                        llm_suggestion if adjudicated_source and llm_actionable else ""
                    )
                else:
                    effective_suggestion = llm_suggestion or anchor_suggestion
                raw.update(
                    {
                        "evidence_quote": str(anchor.get("evidence_quote") or evidence),
                        "paragraph_index": cls._coerce_non_negative_int(
                            anchor.get("paragraph_index")
                        )
                        or 0,
                        "span_start": anchor_span_start,
                        "span_end": anchor_span_end,
                        "suggestion": effective_suggestion,
                        "source": (
                            "merged"
                            if str(raw.get("source") or "") in {"local", "llm", "merged"}
                            else raw.get("source")
                        ),
                    }
                )
                # P0-1: The local validator owns context-free repair routing.  For
                # semantic word deletion, respect the model's contextual verdict.
                is_false_positive = bool(raw.get("is_false_positive", False))
                if requires_context_adjudication:
                    raw["actionable"] = bool(
                        adjudicated_source and llm_actionable and effective_suggestion
                    )
                elif anchor.get("actionable") and not is_false_positive:
                    raw["actionable"] = True
            rebound_hits.append(raw)

        family_counts = cls._candidate_family_counts(candidate_rows)
        recurrent_families = {
            family
            for family in _STRUCTURAL_PATTERN_FAMILIES
            if family_counts.get(family, 0) >= max(2, recurrent_min_occurrences)
        }
        restored = 0
        schema_keys = {
            "pattern_id",
            "pattern_name",
            "category",
            "severity",
            "evidence_quote",
            "paragraph_index",
            "suggestion",
            "confidence",
            "actionable",
            "source",
            "span_start",
            "span_end",
        }
        for candidate in candidate_rows:
            family = cls._pattern_family(str(candidate.get("pattern_id") or ""))
            if family not in recurrent_families:
                continue
            candidate_start = cls._coerce_non_negative_int(candidate.get("span_start"))
            candidate_end = cls._coerce_non_negative_int(candidate.get("span_end"))
            if candidate_start is None or candidate_end is None or candidate_end <= candidate_start:
                continue
            already_present = False
            for existing in rebound_hits:
                if cls._pattern_family(str(existing.get("pattern_id") or "")) != family:
                    continue
                existing_start = cls._coerce_non_negative_int(existing.get("span_start"))
                existing_end = cls._coerce_non_negative_int(existing.get("span_end"))
                if existing_start is None or existing_end is None:
                    continue
                if max(candidate_start, existing_start) < min(candidate_end, existing_end):
                    already_present = True
                    break
            if already_present:
                continue

            restored_hit = {key: candidate.get(key) for key in schema_keys if key in candidate}
            # P0-1: Generate local suggestion for restored structural hits so
            # deterministic patterns (em_dash, filler) can still be patched.
            restored_pattern_id = str(candidate.get("pattern_id") or "")
            restored_evidence = str(candidate.get("evidence_quote") or "")
            local_sugg = cls._local_suggestion(restored_pattern_id, restored_evidence)
            restored_hit.update(
                {
                    "suggestion": local_sugg,
                    "actionable": bool(local_sugg),
                    "source": "local",
                    "confidence": max(0.86, float(candidate.get("confidence") or 0.0)),
                }
            )
            rebound_hits.append(restored_hit)
            restored += 1

        summary = str(report.summary or "").strip()
        if restored:
            summary = (
                f"{summary}；本地重复密度复核补回 {restored} 条不可单句补丁的结构命中。"
                if summary
                else f"本地重复密度复核补回 {restored} 条不可单句补丁的结构命中。"
            )
        payload: dict[str, Any] = {
            "total_hits": len(rebound_hits),
            "hits_by_category": {},
            "critical_hits": 0,
            "pattern_hits": rebound_hits,
            "humanize_score": cls._compute_deterministic_score(rebound_hits),
            "summary": summary,
        }
        reconciled = cls._parse_report(payload, chapter_number, chapter_text)
        return reconciled or report

    @staticmethod
    def _merge_report_summaries(reports: list[HumanizeReport], total_hits: int) -> str:
        if total_hits <= 0:
            return "分批候选裁判完成，未保留真实 AI 痕迹命中。"
        fragments = [
            str(report.summary or "").strip()
            for report in reports
            if str(report.summary or "").strip()
        ]
        if not fragments:
            return f"分批候选裁判完成，保留 {total_hits} 条 AI 痕迹命中。"
        joined = "；".join(fragments[:3])
        return f"分批候选裁判完成，保留 {total_hits} 条 AI 痕迹命中：{joined}"

    @staticmethod
    def _compute_deterministic_score(hits: list[dict[str, Any]]) -> float:
        """Compute humanize_score deterministically from confirmed pattern hits.

        Uses the same severity-weighted penalty formula as QualityGate.check_ai_flavor:
            score = 10 - sum(severity_weight * confidence for hit in hits)
            if any critical hit: score = min(score, 5.0)
        """
        if not hits:
            return 10.0
        penalty = 0.0
        has_critical = False
        for hit in hits:
            severity = str(hit.get("severity", "medium") or "medium").lower()
            try:
                confidence = max(0.0, min(1.0, float(hit.get("confidence", 0.8))))
            except (TypeError, ValueError):
                confidence = 0.8
            penalty += _DETERMINISTIC_SCORE_WEIGHTS.get(severity, 1.0) * confidence
            if severity == "critical":
                has_critical = True
        score = max(0.0, 10.0 - penalty)
        if has_critical:
            score = min(score, _DETERMINISTIC_SCORE_CRITICAL_CAP)
        return round(score, 1)

    @staticmethod
    def _estimate_humanize_score(prescreen_hits: list[dict[str, Any]]) -> float:
        """Estimate humanize_score from deterministic prescreen hits.

        Delegates to _compute_deterministic_score for consistency.
        """
        return HumanizeScanStep._compute_deterministic_score(prescreen_hits)

    @classmethod
    def _parse_report(
        cls,
        raw: Any,
        chapter_number: int,
        chapter_text: str,
    ) -> HumanizeReport | None:
        """Parse LLM JSON output into a ``HumanizeReport``.

        Returns ``None`` when parsing fails or the payload is invalid.
        """
        if not isinstance(raw, dict):
            _logger.warning("humanize_scan: LLM returned non-dict payload — skipping")
            return None

        try:
            # Inject source_text_hash if the LLM omitted or mismatched it.
            text_hash = hashlib.sha256(chapter_text.encode()).hexdigest()
            payload = cls._normalize_raw_report_payload(raw, chapter_text=chapter_text)
            payload["source_text_hash"] = text_hash
            payload["chapter_number"] = chapter_number
            report = HumanizeReport.model_validate(payload)
            report = cls._normalize_report_counts(report)
        except Exception:
            _logger.warning(
                "humanize_scan: failed to parse HumanizeReport — skipping", exc_info=True
            )
            return None

        return report

    @classmethod
    def _normalize_raw_report_payload(
        cls,
        raw: dict[str, Any],
        *,
        chapter_text: str = "",
    ) -> dict[str, Any]:
        """Coerce known provider/mock drift before Pydantic validation."""
        payload = dict(raw)
        raw_hits = payload.get("pattern_hits")
        if not isinstance(raw_hits, list):
            payload["pattern_hits"] = []
        else:
            normalized_hits = [
                cls._normalize_raw_pattern_hit(hit, chapter_text=chapter_text)
                for hit in raw_hits
                if isinstance(hit, dict)
            ]
            payload["pattern_hits"] = [
                hit for hit in normalized_hits if cls._hard_evidence_is_consistent(hit)
            ]

        if not isinstance(payload.get("hits_by_category"), dict):
            payload["hits_by_category"] = {}
        if not isinstance(payload.get("total_hits"), int):
            payload["total_hits"] = len(payload["pattern_hits"])
        if not isinstance(payload.get("critical_hits"), int):
            payload["critical_hits"] = sum(
                1
                for hit in payload["pattern_hits"]
                if isinstance(hit, dict) and str(hit.get("severity", "")).lower() == "critical"
            )
        # Override LLM-provided humanize_score with deterministic computation
        # based on confirmed pattern hits (severity-weighted penalty formula).
        payload["humanize_score"] = cls._compute_deterministic_score(payload["pattern_hits"])
        return payload

    @classmethod
    def _normalize_raw_pattern_hit(
        cls,
        hit: dict[str, Any],
        *,
        chapter_text: str = "",
    ) -> dict[str, Any]:
        """Normalize one raw hit before ``HumanizeReport`` validation."""
        normalized = dict(hit)
        paragraph_index = cls._coerce_non_negative_int(normalized.get("paragraph_index"))
        evidence = str(normalized.get("evidence_quote") or "").strip()
        original, span_start, span_end = cls._find_unique_original_span(
            chapter_text,
            evidence,
            paragraph_index=paragraph_index,
        )
        if original and span_start >= 0:
            # Model offsets refer to the compact prompt excerpt, not necessarily
            # the canonical chapter. Rebind every hit to the source of truth.
            normalized["evidence_quote"] = original
            normalized["span_start"] = span_start
            normalized["span_end"] = span_end
            paragraph_index = cls._paragraph_index_for_offset(chapter_text, span_start)
        else:
            normalized["span_start"] = None
            normalized["span_end"] = None
            if paragraph_index is None:
                paragraph_index = cls._resolve_hit_paragraph_index(normalized, chapter_text)
        normalized["paragraph_index"] = paragraph_index if paragraph_index is not None else 0
        return normalized

    @staticmethod
    def _hard_evidence_is_consistent(hit: dict[str, Any]) -> bool:
        """Reject impossible critical/chat hits produced by retrieval or the model."""
        pattern_id = str(hit.get("pattern_id") or "").strip()
        if pattern_id not in _HARD_EVIDENCE_PATTERN_IDS:
            return True
        evidence = str(hit.get("evidence_quote") or "").strip()
        rule = next((item for item in _HUMANIZE_RULES if item.pattern_id == pattern_id), None)
        return bool(rule is not None and evidence and rule.regex.search(evidence))

    @staticmethod
    def _coerce_non_negative_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float) and value.is_integer():
            return max(0, int(value))
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                return int(stripped)
        return None

    @classmethod
    def _resolve_hit_paragraph_index(
        cls,
        hit: dict[str, Any],
        chapter_text: str,
    ) -> int | None:
        if not chapter_text:
            return None
        span_start = cls._coerce_non_negative_int(hit.get("span_start"))
        if span_start is not None and span_start < len(chapter_text):
            return cls._paragraph_index_for_offset(chapter_text, span_start)
        evidence = str(hit.get("evidence_quote") or "").strip()
        if not evidence:
            return None
        _original, start, _end = cls._find_unique_original_span(chapter_text, evidence)
        if start >= 0:
            return cls._paragraph_index_for_offset(chapter_text, start)
        return None

    @staticmethod
    def _normalize_report_counts(report: HumanizeReport) -> HumanizeReport:
        """Trust parsed hits over LLM-provided aggregate counters."""
        from collections import Counter

        hits = list(report.pattern_hits)
        hits_by_category = Counter(hit.category for hit in hits if hit.category)
        return report.model_copy(
            update={
                "total_hits": len(hits),
                "hits_by_category": dict(hits_by_category),
                "critical_hits": sum(1 for hit in hits if hit.severity == "critical"),
            }
        )

    @staticmethod
    def _mask_dialogue(text: str) -> str:
        """Replace dialogue spans with spaces so offsets stay stable."""
        chars = list(text)
        quote_pairs = (("「", "」"), ("“", "”"), ('"', '"'))
        for left, right in quote_pairs:
            start: int | None = None
            index = 0
            while index < len(chars):
                char = chars[index]
                if start is None and char == left:
                    start = index
                elif start is not None and char == right:
                    for mask_index in range(start, index + 1):
                        if chars[mask_index] != "\n":
                            chars[mask_index] = " "
                    start = None
                index += 1
        return "".join(chars)

    @staticmethod
    def _iter_paragraphs(text: str) -> list[tuple[int, str, int, int]]:
        paragraphs: list[tuple[int, str, int, int]] = []
        offset = 0
        for index, paragraph in enumerate(text.split("\n\n")):
            end = offset + len(paragraph)
            paragraphs.append((index, paragraph, offset, end))
            offset = end + 2
        return paragraphs

    @classmethod
    def _paragraph_index_for_offset(cls, text: str, offset: int) -> int:
        for index, _paragraph, start, end in cls._iter_paragraphs(text):
            if start <= offset <= end:
                return index
        return 0

    @staticmethod
    def _iter_sentences(text: str) -> list[tuple[str, int, int]]:
        results: list[tuple[str, int, int]] = []
        for match in re.finditer(r"[^。！？\n]+[。！？]?", text):
            sentence = match.group(0)
            if sentence.strip():
                results.append((sentence, match.start(), match.end()))
        return results

    @staticmethod
    def _local_suggestion(pattern_id: str, evidence: str) -> str:
        """Produce a tiny deterministic suggestion for obvious local hits.

        Returns the locally rewritten text when the pattern has a safe
        in-place transformation (delete a phrase, swap a token).  Returns
        an empty string when the pattern requires a semantic rewrite
        (e.g. ``negative_parallelism``) so the LLM is forced to supply a
        real suggestion.  An empty suggestion here prevents the
        ``_build_patches`` gate from accepting a no-op rewrite where
        ``suggestion == evidence`` and keeps the local prescreen from
        poisoning the LLM's judgment with identical evidence/suggestion
        pairs.
        """
        replacements = {
            "ai_vocabulary": (
                "此外",
                "至关重要",
                "深入探讨",
                "彰显",
                "凸显",
                "复杂性",
                "持久的",
                "格局",
                "珍贵的",
                "相互作用",
            ),
            "promotional_language": (
                "令人叹为观止",
                "迷人的",
                "充满活力的",
                "壮丽的",
                "美不胜收",
                "雄伟的",
                "自然之美",
            ),
            "significance_inflation": (
                "标志着",
                "具有里程碑意义",
                "划时代的",
                "开创性的",
                "关键时刻",
                "分水岭",
                "前所未有的",
            ),
            "filler_phrases": (
                "值得注意的是",
                "不难发现",
                "基于以上分析",
                "综上所述",
                "换句话说",
                "不可否认的是",
            ),
            "persuasive_authority": (
                "从根本上说，",
                "核心问题是",
                "归根结底，",
                "我们必须承认，",
                "事实上，",
                "本质上，",
            ),
            "diff_anchored_writing": (
                "本次更新",
                "本次改动",
                "本次修改",
                "新增了",
                "替代了原先",
                "相较于之前",
                "进行了优化",
            ),
        }
        result = evidence
        for phrase in replacements.get(pattern_id, ()):
            result = result.replace(phrase, "")
        if pattern_id == "em_dash_overuse":
            result = result.replace("——", "，")
        if pattern_id == "hollow_aspect_marker":
            result = (
                result.replace("凝望着", "望着")
                .replace("沉思着", "想着")
                .replace("注视着", "看着")
                .replace("思考着", "想着")
            )
        cleaned = result.strip(" ，,")
        if not cleaned:
            return ""
        if cleaned == evidence.strip():
            return ""
        return cleaned

    # ── P0-2: Deterministic punctuation patch (no LLM required) ─────────────

    # Only a bounded punctuation-density correction is safe before contextual
    # adjudication. Word-deletion patterns remain candidates until the model
    # confirms both the hit and a concrete local replacement.
    _DETERMINISTIC_SAFE_PATTERNS: frozenset[str] = frozenset({"em_dash_overuse"})
    # Maximum em_dash occurrences to preserve per chapter (density control)
    _EM_DASH_PRESERVE_COUNT: int = 3

    @classmethod
    def _deterministic_patch_pass(
        cls,
        text: str,
        prescreen_hits: list[dict[str, Any]],
        chapter_number: int,
    ) -> tuple[str, int, list[str]]:
        """Apply deterministic-safe fixes directly without LLM involvement.

        Handles the one pattern whose replacement is bounded and context-free:
        - em_dash_overuse: context-aware "——" replacement (preserves first N,
          skips dialogue, chooses punctuation by surrounding context)

        Returns:
            (revised_text, patches_applied, patched_pattern_ids)
        """
        deterministic_hits = [
            h
            for h in prescreen_hits
            if str(h.get("pattern_id") or "") in cls._DETERMINISTIC_SAFE_PATTERNS
        ]
        if not deterministic_hits:
            return text, 0, []

        patched_patterns: list[str] = []
        total_applied = 0
        revised = text

        # ── em_dash_overuse: context-aware replacement ──────────────────────
        em_dash_hits = [h for h in deterministic_hits if h.get("pattern_id") == "em_dash_overuse"]
        if em_dash_hits:
            revised, em_applied = cls._deterministic_em_dash_fix(
                revised, cls._EM_DASH_PRESERVE_COUNT
            )
            if em_applied > 0:
                total_applied += em_applied
                patched_patterns.append("em_dash_overuse")

        if total_applied > 0:
            _logger.info(
                "humanize_deterministic_pass | chapter=%d | applied=%d | patterns=%s",
                chapter_number,
                total_applied,
                patched_patterns,
            )
        return revised, total_applied, patched_patterns

    @classmethod
    def _deterministic_em_dash_fix(cls, text: str, preserve_count: int) -> tuple[str, int]:
        """Context-aware em-dash replacement outside dialogue.

        Preserves the first *preserve_count* em-dashes and replaces the rest
        with contextually appropriate punctuation:
        - Both sides are complete clauses → "。"
        - Otherwise → "，"
        - Inside dialogue quotes → never replace
        """
        em_dash = "——"
        masked = cls._mask_dialogue(text)
        # Find all em_dash positions outside dialogue
        positions: list[int] = []
        search_start = 0
        while True:
            idx = masked.find(em_dash, search_start)
            if idx == -1:
                break
            positions.append(idx)
            search_start = idx + len(em_dash)

        if len(positions) <= preserve_count:
            return text, 0

        # Replace from end to start to preserve offsets
        to_replace = positions[preserve_count:]
        result = text
        applied = 0
        for pos in reversed(to_replace):
            before = result[:pos]
            after = result[pos + len(em_dash) :]
            replacement = cls._choose_em_dash_replacement(before, after)
            result = before + replacement + after
            applied += 1
        return result, applied

    @staticmethod
    def _choose_em_dash_replacement(before: str, after: str) -> str:
        """Choose contextually appropriate punctuation to replace em-dash."""
        # Sentence-ending punctuation before → use period
        before_stripped = before.rstrip()
        if before_stripped and before_stripped[-1] in "。！？；…":
            return ""
        # If after starts with a complete sentence (ends with period later)
        # and before is a complete clause, use period
        after_stripped = after.lstrip()
        if (
            before_stripped
            and before_stripped[-1] in "，、；："
            and after_stripped
            and len(after_stripped) > 4
        ):
            return "，"
        # Default: comma
        return "，"

    # ── Patch construction ────────────────────────────────────────────────────

    @classmethod
    def _build_patches(
        cls,
        pattern_hits: list[Any],
        chapter_text: str,
        *,
        chapter_number: int = 0,
        patch_confidence_floor: float = _PATCH_CONFIDENCE_FLOOR,
    ) -> tuple[list[PatchOperation], int]:
        """Convert pattern hits into ``PatchOperation`` list and count rejections.

        Returns:
            ``(patches, unpatchable_count)`` where *unpatchable_count* is the
            number of hits the LLM marked ``actionable=True`` but the local
            validator refused to patch (unusable suggestion, no unique
            anchor, etc.).  Defensive gating: a hit is only turned into a
            patch when either the LLM marks it actionable or the local
            validator can safely promote a high-confidence report-only hit.
            When a hit declares ``actionable=True`` but its suggestion is
            unusable, the local validator downgrades ``actionable`` to
            ``False`` so the report reflects the true reason the hit was not
            patched.
        """
        candidates: list[_PatchCandidate] = []
        unpatchable = 0
        for idx, hit in enumerate(pattern_hits):
            pattern_id = str(getattr(hit, "pattern_id", "unknown"))
            severity = str(getattr(hit, "severity", "")).lower()
            confidence = float(getattr(hit, "confidence", 0.0))
            actionable = bool(getattr(hit, "actionable", False))
            patchable = cls._is_patchable_hit(
                hit,
                confidence_floor=patch_confidence_floor,
            )
            promoted = False
            if not patchable:
                promoted = cls._is_promotable_report_only_hit(
                    hit,
                    confidence_floor=patch_confidence_floor,
                )

            if not patchable and not promoted:
                _logger.debug(
                    "humanize_patch_gate | chapter=%d | pattern=%s | "
                    "actionable=%s | severity=%s | confidence=%.2f — not patchable",
                    chapter_number,
                    pattern_id,
                    actionable,
                    severity,
                    confidence,
                )
                continue
            evidence = str(getattr(hit, "evidence_quote", "") or "").strip()
            suggestion = str(getattr(hit, "suggestion", "") or "").strip()
            if not evidence:
                unpatchable += 1
                continue
            if cls._has_incomplete_structural_anchor(pattern_id, evidence):
                _logger.debug(
                    "humanize_patch_gate | chapter=%d | pattern=%s | "
                    "reason=incomplete_structural_anchor | evidence=%r",
                    chapter_number,
                    pattern_id,
                    evidence[:50] if evidence else "",
                )
                cls._downgrade_actionable(hit)
                unpatchable += 1
                continue
            if not cls._validate_suggestion_usable(evidence, suggestion):
                # The LLM claimed this was actionable but the suggestion is
                # not actually usable.  Downgrade so the report and any
                # downstream consumers see the real state instead of a
                # silent skip.
                _logger.debug(
                    "humanize_patch_gate | chapter=%d | pattern=%s | "
                    "reason=unusable_suggestion — suggestion=%r",
                    chapter_number,
                    pattern_id,
                    suggestion[:50] if suggestion else "",
                )
                cls._downgrade_actionable(hit)
                unpatchable += 1
                continue
            category = str(getattr(hit, "category", "") or "").strip()
            if not cls._is_small_replacement(evidence, suggestion, category=category):
                _logger.debug(
                    "humanize_patch_gate | chapter=%d | pattern=%s | category=%s | "
                    "reason=not_small_replacement | evidence_len=%d | suggestion_len=%d",
                    chapter_number,
                    pattern_id,
                    category,
                    len(evidence),
                    len(suggestion),
                )
                cls._downgrade_actionable(hit)
                unpatchable += 1
                continue

            # Exact match check
            paragraph_index = getattr(hit, "paragraph_index", None)
            original, abs_start, abs_end = cls._find_unique_original_span(
                chapter_text,
                evidence,
                paragraph_index=paragraph_index if isinstance(paragraph_index, int) else None,
            )
            if not original:
                _logger.debug(
                    "humanize_patch_gate | chapter=%d | pattern=%s | "
                    "reason=no_unique_anchor | evidence=%r",
                    chapter_number,
                    pattern_id,
                    evidence[:50] if evidence else "",
                )
                cls._downgrade_actionable(hit)
                unpatchable += 1
                continue

            if cls._suggestion_repeats_adjacent_context(
                chapter_text,
                original,
                suggestion,
                paragraph_index=paragraph_index if isinstance(paragraph_index, int) else None,
                category=category,
            ):
                _logger.debug(
                    "humanize_patch_gate | chapter=%d | pattern=%s | category=%s | "
                    "reason=suggestion_repeats_adjacent_context",
                    chapter_number,
                    pattern_id,
                    category,
                )
                cls._downgrade_actionable(hit)
                unpatchable += 1
                continue

            if cls._candidate_introduces_quality_regression(
                chapter_text,
                abs_start,
                abs_end,
                suggestion,
            ):
                _logger.debug(
                    "humanize_patch_gate | chapter=%d | pattern=%s | category=%s | "
                    "reason=local_quality_regression",
                    chapter_number,
                    pattern_id,
                    category,
                )
                cls._downgrade_actionable(hit)
                unpatchable += 1
                continue

            if promoted:
                cls._set_actionable(hit, True)
                _logger.debug(
                    "humanize_patch_gate | chapter=%d | pattern=%s | "
                    "reason=promoted_report_only_hit | confidence=%.2f",
                    chapter_number,
                    pattern_id,
                    confidence,
                )

            resolved_paragraph_index = cls._paragraph_index_for_offset(chapter_text, abs_start)
            candidates.append(
                _PatchCandidate(
                    idx=idx,
                    hit=hit,
                    pattern_id=pattern_id,
                    category=category,
                    original=original,
                    replacement=suggestion,
                    paragraph_index=resolved_paragraph_index,
                    abs_start=abs_start,
                    abs_end=abs_end,
                    promoted=promoted,
                )
            )

        candidates = cls._coalesce_patch_candidates(candidates, chapter_number=chapter_number)
        safe_candidates: list[_PatchCandidate] = []
        for candidate in candidates:
            if cls._candidate_introduces_quality_regression(
                chapter_text,
                candidate.abs_start,
                candidate.abs_end,
                candidate.replacement,
            ):
                cls._downgrade_actionable(candidate.hit)
                unpatchable += 1
                continue
            safe_candidates.append(candidate)
        patches = [
            PatchOperation(
                patch_id=f"humanize_{candidate.idx}",
                original=candidate.original,
                replacement=candidate.replacement,
                para_start=candidate.paragraph_index,
                para_end=candidate.paragraph_index,
            )
            for candidate in safe_candidates
        ]
        return patches, unpatchable

    @classmethod
    def _validate_suggestion_usable(cls, evidence: str, suggestion: str) -> bool:
        """Return True when *suggestion* is a real, non-trivial rewrite of *evidence*.

        A suggestion is unusable when any of the following is true:
        - It is empty (the local prescreen has no transformation for the
          pattern and the LLM did not supply one).
        - It is byte-identical to the evidence (a no-op rewrite that the
          ``PatchExecutorV2`` would either reject or, worse, accept as a
          zero-effect edit).
        - It is a known placeholder (e.g. "改为" / "建议改写为") that
          describes an action instead of performing it.
        """
        if not suggestion:
            return False
        if suggestion == evidence:
            return False
        if cls._is_placeholder_suggestion(suggestion):
            return False
        return True

    @staticmethod
    def _has_incomplete_structural_anchor(pattern_id: str, evidence: str) -> bool:
        """Reject structure rewrites that only anchor the connector prefix.

        ``不是A，而是`` without the B side is not a replaceable text span.  If
        patched surgically, the replacement is inserted before the original B
        side, producing duplicated prose.
        """
        if pattern_id != "negative_parallelism":
            return False
        stripped = evidence.rstrip(" ，,；;：:")
        return stripped.endswith(("而是", "更是", "而且", "恰恰是"))

    @staticmethod
    def _downgrade_actionable(hit: Any) -> None:
        """Flip ``actionable`` to ``False`` in-place on Pydantic hits.

        The LLM is the source of truth for "is this safe to patch", but the
        local validator has the final say: a hit that is claimed actionable
        but carries an unusable suggestion is silently downgraded so the
        report surfaces the real reason no patch was produced.
        """
        HumanizeScanStep._set_actionable(hit, False)

    @staticmethod
    def _set_actionable(hit: Any, value: bool) -> None:
        """Best-effort update for schema or dict hits used in reports."""
        try:
            if isinstance(hit, dict):
                hit["actionable"] = value
            else:
                object.__setattr__(hit, "actionable", value)
        except Exception:
            pass

    @staticmethod
    def _is_patchable_hit(
        hit: Any,
        *,
        confidence_floor: float = _PATCH_CONFIDENCE_FLOOR,
    ) -> bool:
        severity = str(getattr(hit, "severity", "") or "").strip().lower()
        confidence = float(getattr(hit, "confidence", 0.0) or 0.0)
        actionable = bool(getattr(hit, "actionable", False))
        return actionable and severity in _PATCHABLE_SEVERITIES and confidence >= confidence_floor

    @staticmethod
    def _is_promotable_report_only_hit(
        hit: Any,
        *,
        confidence_floor: float = _PATCH_CONFIDENCE_FLOOR,
    ) -> bool:
        """Allow locally safe report-only hits to become surgical patches.

        Some providers correctly locate a sentence and provide a concrete
        replacement, but still set ``actionable=false``. The local validator is
        stricter than the model: only high-confidence, non-report-only patterns
        with a real suggestion are promoted, and all normal anchor/similarity
        gates still run before any patch is emitted.
        """
        if bool(getattr(hit, "actionable", False)):
            return False
        pattern_id = str(getattr(hit, "pattern_id", "") or "").strip()
        if pattern_id in _CONTEXT_ADJUDICATION_REQUIRED_PATTERN_IDS:
            return False
        if pattern_id not in _PROMOTABLE_PATTERN_IDS:
            return False
        severity = str(getattr(hit, "severity", "") or "").strip().lower()
        confidence = float(getattr(hit, "confidence", 0.0) or 0.0)
        evidence = str(getattr(hit, "evidence_quote", "") or "").strip()
        suggestion = str(getattr(hit, "suggestion", "") or "").strip()
        return bool(
            severity in _PATCHABLE_SEVERITIES
            and confidence >= confidence_floor
            and evidence
            and HumanizeScanStep._validate_suggestion_usable(evidence, suggestion)
        )

    @staticmethod
    def _is_placeholder_suggestion(suggestion: str) -> bool:
        """Detect LLM placeholder suggestions that don't carry real edits."""
        lower = suggestion.lower()
        placeholders = (
            "此处可改为",
            "建议改写为",
            "可替换为",
            "改为",
            "请改写",
            "删除该句",
            "删除此句",
            "直接删除",
            "无需替换",
        )
        return any(lower.startswith(p) for p in placeholders) and len(suggestion) < 30

    @classmethod
    def _suggestion_repeats_adjacent_context(
        cls,
        text: str,
        original: str,
        suggestion: str,
        *,
        paragraph_index: int | None = None,
        category: str = "",
    ) -> bool:
        """Return True when a suggestion duplicates nearby prose."""
        if not text or not original or not suggestion:
            return False

        target = text
        if paragraph_index is not None:
            paragraphs = text.split("\n\n")
            if 0 <= paragraph_index < len(paragraphs):
                target = paragraphs[paragraph_index]
        if target.count(original) != 1:
            return False

        start = target.find(original)
        left_context = target[max(0, start - 50) : start]
        right_context = target[start + len(original) : start + len(original) + 50]
        suggestion_compact = cls._compact_for_overlap(suggestion)
        if len(suggestion_compact) < 6:
            return False
        left_compact = cls._compact_for_overlap(left_context)
        right_compact = cls._compact_for_overlap(right_context)

        # Exact boundary duplication is unsafe for every category. It catches
        # suggestions such as prefixing ``沈岸的目光`` when those same characters
        # already sit immediately to the left of the evidence span.
        max_boundary = min(16, len(suggestion_compact), len(left_compact))
        for overlap in range(max_boundary, 3, -1):
            if left_compact.endswith(suggestion_compact[:overlap]):
                return True
        max_boundary = min(16, len(suggestion_compact), len(right_compact))
        for overlap in range(max_boundary, 3, -1):
            if suggestion_compact.endswith(right_compact[:overlap]):
                return True

        if category not in _STRUCTURAL_REWRITE_CATEGORIES:
            return False
        head_window = suggestion_compact[: min(24, len(suggestion_compact))]
        tail_window = suggestion_compact[max(0, len(suggestion_compact) - 24) :]
        max_probe = min(14, len(suggestion_compact))
        for probe_len in range(max_probe, 5, -1):
            for start in range(0, len(head_window) - probe_len + 1):
                if head_window[start : start + probe_len] in left_compact:
                    return True
            for start in range(0, len(tail_window) - probe_len + 1):
                if tail_window[start : start + probe_len] in right_compact:
                    return True
        return False

    @classmethod
    def _candidate_introduces_quality_regression(
        cls,
        text: str,
        start: int,
        end: int,
        replacement: str,
    ) -> bool:
        left = max(text.rfind(mark, 0, start) for mark in ("。", "！", "？", "\n")) + 1
        right_candidates = [
            found + 1 for mark in ("。", "！", "？", "\n") if (found := text.find(mark, end)) >= 0
        ]
        right = min(right_candidates) if right_candidates else len(text)
        original_window = text[left:right]
        relative_start = start - left
        relative_end = end - left
        revised_window = (
            original_window[:relative_start] + replacement + original_window[relative_end:]
        )
        return bool(cls.introduced_quality_regressions(original_window, revised_window))

    @classmethod
    def introduced_quality_regressions(cls, original_text: str, revised_text: str) -> list[str]:
        """Return deterministic prose regressions newly introduced by a rewrite."""
        before = Counter(cls._quality_regression_signals(original_text))
        after = Counter(cls._quality_regression_signals(revised_text))
        introduced: list[str] = []
        for signal, count in after.items():
            introduced.extend([signal] * max(0, count - before.get(signal, 0)))
        return introduced

    @classmethod
    def _quality_regression_signals(cls, text: str) -> list[str]:
        masked = cls._mask_dialogue(text)
        signals = [
            f"exact_phrase_repeat:{match.group('phrase')}"
            for match in _EXACT_PHRASE_REPEAT_RE.finditer(masked)
        ]
        for sentence in re.split(r"[。！？\n]+", masked):
            clauses = [part.strip() for part in re.split(r"[，,；;]+", sentence) if part.strip()]
            clause_signatures: list[set[tuple[str, str]]] = []
            for clause in clauses:
                signatures: set[tuple[str, str]] = set()
                for family, pattern in _REDUNDANT_ACTION_PATTERNS:
                    for match in pattern.finditer(clause):
                        signatures.add((family, str(match.groupdict().get("object") or "")))
                clause_signatures.append(signatures)
            for previous, current in zip(clause_signatures, clause_signatures[1:], strict=False):
                for family, obj in sorted(previous & current):
                    signals.append(f"redundant_action:{family}:{obj}")
        return signals

    @staticmethod
    def _compact_for_overlap(text: str) -> str:
        return re.sub(r"[\s，,。！？；;：:、—…“”\"'「」（）()《》]+", "", text)

    @classmethod
    def _find_unique_original(
        cls,
        text: str,
        needle: str,
        *,
        paragraph_index: int | None = None,
    ) -> str:
        original, _start, _end = cls._find_unique_original_span(
            text,
            needle,
            paragraph_index=paragraph_index,
        )
        return original

    @classmethod
    def _find_unique_original_span(
        cls,
        text: str,
        needle: str,
        *,
        paragraph_index: int | None = None,
    ) -> tuple[str, int, int]:
        masked_text = cls._mask_dialogue(text)
        if masked_text.count(needle) == 1:
            start = masked_text.find(needle)
            if paragraph_index is None:
                return needle, start, start + len(needle)
            paragraphs = text.split("\n\n")
            masked_paragraphs = masked_text.split("\n\n")
            if 0 <= paragraph_index < len(masked_paragraphs):
                para_start = sum(len(p) + 2 for p in paragraphs[:paragraph_index])
                para_offset = masked_paragraphs[paragraph_index].find(needle)
                if para_offset >= 0:
                    return needle, para_start + para_offset, para_start + para_offset + len(needle)
            # The model can mis-number paragraphs while still quoting a unique,
            # non-dialogue evidence span. Trust the unique quote; paragraph
            # anchors are an optimization, not the source of truth.
            return needle, start, start + len(needle)
        if masked_text.count(needle) > 1:
            if paragraph_index is not None:
                paragraphs = text.split("\n\n")
                masked_paragraphs = masked_text.split("\n\n")
                if 0 <= paragraph_index < len(masked_paragraphs):
                    para_start = sum(len(p) + 2 for p in paragraphs[:paragraph_index])
                    if masked_paragraphs[paragraph_index].count(needle) == 1:
                        para_offset = masked_paragraphs[paragraph_index].find(needle)
                        return (
                            needle,
                            para_start + para_offset,
                            para_start + para_offset + len(needle),
                        )
                    if masked_paragraphs[paragraph_index].count(needle) == 0:
                        return cls._fuzzy_find_span(
                            paragraphs[paragraph_index],
                            masked_paragraphs[paragraph_index],
                            needle,
                            base_offset=para_start,
                        )
            return "", -1, -1

        target_text = text
        target_masked = masked_text
        base_offset = 0
        if paragraph_index is not None:
            paragraphs = text.split("\n\n")
            masked_paragraphs = masked_text.split("\n\n")
            if 0 <= paragraph_index < len(paragraphs) and paragraph_index < len(masked_paragraphs):
                target_text = paragraphs[paragraph_index]
                target_masked = masked_paragraphs[paragraph_index]
                base_offset = sum(len(p) + 2 for p in paragraphs[:paragraph_index])

        return cls._fuzzy_find_span(target_text, target_masked, needle, base_offset=base_offset)

    @staticmethod
    def _is_small_replacement(evidence: str, suggestion: str, *, category: str = "") -> bool:
        if category in _STRUCTURAL_REWRITE_CATEGORIES:
            # Structural rewrites (e.g. rhetorical pattern rephrasing) naturally
            # produce low-similarity output.  Use a relaxed gate but still cap
            # runaway suggestions that are wildly longer than the evidence.
            if len(suggestion) > max(200, len(evidence) * 3 + 30):
                return False
            ratio = SequenceMatcher(None, evidence, suggestion).ratio()
            return ratio >= 0.15 or abs(len(evidence) - len(suggestion)) <= 80
        if len(suggestion) > max(120, len(evidence) * 2 + 20):
            return False
        ratio = SequenceMatcher(None, evidence, suggestion).ratio()
        return ratio >= 0.35 or abs(len(evidence) - len(suggestion)) <= 16

    @staticmethod
    def _fuzzy_find(text: str, masked_text: str, needle: str) -> str:
        """Find the best fuzzy match for *needle* in *text*.

        Returns a unique matched substring if similarity >= 0.78, otherwise empty string.
        """
        match, _start, _end = HumanizeScanStep._fuzzy_find_span(
            text,
            masked_text,
            needle,
            base_offset=0,
        )
        return match

    @staticmethod
    def _fuzzy_find_span(
        text: str,
        masked_text: str,
        needle: str,
        *,
        base_offset: int = 0,
    ) -> tuple[str, int, int]:
        """Find the best fuzzy match and return its absolute span."""
        if not text or not masked_text or not needle:
            return "", -1, -1
        best_ratio = 0.0
        best_match = ""
        best_start = -1
        best_end = -1
        strong_matches = 0
        for match in re.finditer(r"[^。！？\n]+[。！？]?", masked_text):
            masked_sentence = match.group(0)
            cleaned = masked_sentence.strip()
            if not cleaned:
                continue
            ratio = SequenceMatcher(None, needle, cleaned).ratio()
            if ratio >= 0.78:
                strong_matches += 1
            if ratio > best_ratio:
                best_ratio = ratio
                raw_original = text[match.start() : match.end()]
                leading = len(raw_original) - len(raw_original.lstrip())
                trailing = len(raw_original.rstrip())
                best_match = raw_original[leading:trailing]
                best_start = base_offset + match.start() + leading
                best_end = base_offset + match.start() + trailing
        if best_ratio >= 0.78 and strong_matches == 1:
            return best_match, best_start, best_end
        return "", -1, -1

    @classmethod
    def _coalesce_patch_candidates(
        cls,
        candidates: list[_PatchCandidate],
        *,
        chapter_number: int = 0,
    ) -> list[_PatchCandidate]:
        """Merge overlapping humanize candidates into one replacement.

        ``PatchExecutorV2`` rejects overlapping operations. Humanize reports
        often mark both a whole sentence and a nested punctuation/template issue,
        so composing them here prevents the "located correctly, but neither
        patch landed" failure mode.
        """
        if len(candidates) <= 1:
            return candidates

        ordered = sorted(candidates, key=lambda c: (c.abs_start, -c.abs_end, c.idx))
        groups: list[list[_PatchCandidate]] = []
        current: list[_PatchCandidate] = []
        current_end = -1
        for candidate in ordered:
            if not current or candidate.abs_start >= current_end:
                if current:
                    groups.append(current)
                current = [candidate]
                current_end = candidate.abs_end
                continue
            current.append(candidate)
            current_end = max(current_end, candidate.abs_end)
        if current:
            groups.append(current)

        merged: list[_PatchCandidate] = []
        for group in groups:
            if len(group) == 1:
                merged.append(group[0])
                continue
            merged_candidate = cls._merge_patch_candidate_group(group)
            _logger.debug(
                "humanize_patch_coalesce | chapter=%d | merged=%d | base=%s | span=%d:%d",
                chapter_number,
                len(group),
                merged_candidate.pattern_id,
                merged_candidate.abs_start,
                merged_candidate.abs_end,
            )
            merged.append(merged_candidate)
        return merged

    @classmethod
    def _merge_patch_candidate_group(
        cls,
        group: list[_PatchCandidate],
    ) -> _PatchCandidate:
        severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}

        def key(candidate: _PatchCandidate) -> tuple[int, int, float, int]:
            severity = str(getattr(candidate.hit, "severity", "") or "").lower()
            confidence = float(getattr(candidate.hit, "confidence", 0.0) or 0.0)
            return (
                candidate.span_length,
                severity_rank.get(severity, 0),
                confidence,
                -candidate.idx,
            )

        base = max(group, key=key)
        replacement = base.replacement
        for candidate in sorted(group, key=lambda c: c.span_length, reverse=True):
            if candidate is base:
                continue
            rel_start = candidate.abs_start - base.abs_start
            rel_end = candidate.abs_end - base.abs_start
            inner_original = ""
            if 0 <= rel_start < rel_end <= len(base.original):
                inner_original = base.original[rel_start:rel_end]
            if inner_original and inner_original in replacement:
                replacement = replacement.replace(inner_original, candidate.replacement, 1)
            elif candidate.original and candidate.original in replacement:
                replacement = replacement.replace(candidate.original, candidate.replacement, 1)
            elif candidate.pattern_id == "em_dash_overuse" and "——" in replacement:
                replacement = replacement.replace("——", "，")

        return _PatchCandidate(
            idx=base.idx,
            hit=base.hit,
            pattern_id=base.pattern_id,
            category=base.category,
            original=base.original,
            replacement=replacement,
            paragraph_index=base.paragraph_index,
            abs_start=base.abs_start,
            abs_end=base.abs_end,
            promoted=base.promoted,
        )

    # ── Patch application ─────────────────────────────────────────────────────

    def _apply_patches(
        self,
        text: str,
        patches: list[PatchOperation],
        chapter_number: int,
    ) -> tuple[str, int]:
        """Apply patches via ``PatchExecutorV2``; return (revised_text, applied_count).

        Falls back to *text* when all patches fail or an exception occurs.
        """
        if not patches:
            return text, 0

        try:
            executor = PatchExecutorV2(strategy="best_effort")
            result = executor.apply_batch(text, patches)

            if result.applied_count < len(patches):
                _logger.info(
                    "humanize_scan_patch | chapter=%d | applied=%d/%d — partial success",
                    chapter_number,
                    result.applied_count,
                    len(patches),
                )

            # If nothing was applied, fall back to original
            if result.applied_count == 0:
                _logger.warning(
                    "humanize_scan_patch | chapter=%d | no patches applied — falling back",
                    chapter_number,
                )
                return text, 0

            return result.revised_text, result.applied_count

        except Exception:
            _logger.warning(
                "humanize_scan_patch_failed | chapter=%d — falling back to original",
                chapter_number,
                exc_info=True,
            )
            return text, 0

    # ── Paragraph-level rewrite (for unpatchable structural patterns) ──────────

    @classmethod
    def _collect_affected_paragraphs(
        cls,
        pattern_hits: list[Any],
        min_confidence: float = 0.70,
        current_text: str = "",
    ) -> dict[int, list[dict[str, Any]]]:
        affected: dict[int, list[dict[str, Any]]] = {}
        for hit in pattern_hits:
            severity = str(getattr(hit, "severity", "")).lower()
            confidence = float(getattr(hit, "confidence", 0.0))
            if severity not in ("medium", "high", "critical"):
                continue
            if confidence < min_confidence:
                continue
            pattern_id = str(getattr(hit, "pattern_id", "") or "")
            category = str(getattr(hit, "category", "") or "")
            evidence = str(getattr(hit, "evidence_quote", "") or "").strip()
            suggestion = str(getattr(hit, "suggestion", "") or "").strip()
            if pattern_id in _REPORT_ONLY_PATTERN_IDS:
                continue
            if category not in _STRUCTURAL_REWRITE_CATEGORIES:
                continue
            if bool(getattr(hit, "actionable", False)) or suggestion:
                continue
            if current_text and (not evidence or evidence not in current_text):
                continue
            para_idx = getattr(hit, "paragraph_index", 0)
            if para_idx not in affected:
                affected[para_idx] = []
            affected[para_idx].append(
                {
                    "pattern_id": pattern_id,
                    "pattern_name": str(getattr(hit, "pattern_name", "")),
                    "category": category,
                    "severity": severity,
                    "evidence_quote": str(getattr(hit, "evidence_quote", "")),
                    "paragraph_index": para_idx,
                    "span_start": getattr(hit, "span_start", None),
                    "span_end": getattr(hit, "span_end", None),
                }
            )
        return affected

    @classmethod
    def _target_family_counts(
        cls,
        text: str,
        target_pattern_ids: set[str],
    ) -> Counter[str]:
        """Count only target families so equivalent patterns cannot evade recheck."""

        target_families = {cls._pattern_family(pattern_id) for pattern_id in target_pattern_ids}
        candidates = cls._canonicalize_prescreen_candidates(
            cls.prescreen_text(text, filter_dialogue=True)
        )
        return Counter(
            cls._pattern_family(str(hit.get("pattern_id") or ""))
            for hit in candidates
            if cls._pattern_family(str(hit.get("pattern_id") or "")) in target_families
        )

    async def _paragraph_level_rewrite(
        self,
        current_text: str,
        report: HumanizeReport,
        chapter_number: int,
        style_profile: dict[str, Any] | None = None,
        context_capsule: dict[str, Any] | None = None,
        min_confidence: float = 0.70,
    ) -> tuple[str, int]:
        affected = self._collect_affected_paragraphs(
            report.pattern_hits,
            min_confidence=min_confidence,
            current_text=current_text,
        )
        if not affected:
            return current_text, 0

        paragraphs = current_text.split("\n\n")
        para_entries: list[dict[str, Any]] = []
        all_hits: list[dict[str, Any]] = []
        for para_idx in sorted(affected.keys()):
            if para_idx >= len(paragraphs):
                continue
            para_entries.append({"index": para_idx, "text": paragraphs[para_idx]})
            all_hits.extend(affected[para_idx])

        if not para_entries:
            return current_text, 0

        llm_context: dict[str, Any] = {
            "chapter_number": chapter_number,
            "paragraphs": para_entries,
            "pattern_hits": all_hits,
        }
        if style_profile:
            llm_context["style_profile"] = style_profile
        if context_capsule:
            llm_context["humanize_context"] = {
                "world_rule_preservation": list(
                    context_capsule.get("world_rule_preservation", []) or []
                )[:4],
                "cognitive_constraints": list(
                    context_capsule.get("cognitive_constraints", []) or []
                ),
            }

        try:
            raw = await self._call_with_retry(
                TaskType.HUMANIZE_PARAGRAPH_REWRITE,
                llm_context,
                max_tokens=max(2048, sum(len(p["text"]) for p in para_entries)),
                temperature=float(getattr(self.settings, "temp_humanize_paragraph_rewrite", 0.3)),
            )
        except Exception:
            _logger.warning(
                "humanize_paragraph_rewrite_failed | chapter=%d",
                chapter_number,
            )
            return current_text, 0

        response_text = (
            raw
            if isinstance(raw, str)
            else (raw.get("content", "") if isinstance(raw, dict) else str(raw))
        )
        if not response_text or not isinstance(response_text, str):
            return current_text, 0

        rewritten_paras = [p.strip() for p in response_text.split("\n\n") if p.strip()]
        if not rewritten_paras:
            return current_text, 0

        result_paras = list(paragraphs)
        applied = 0
        for i, para_entry in enumerate(para_entries):
            if i >= len(rewritten_paras):
                break
            original = paragraphs[para_entry["index"]]
            rewritten = rewritten_paras[i]
            if not rewritten or rewritten == original:
                continue
            if abs(len(rewritten) - len(original)) > len(original) * 0.30:
                _logger.debug(
                    "humanize_paragraph_rewrite | chapter=%d | para=%d | "
                    "size_deviation: orig=%d rewrite=%d — keeping original",
                    chapter_number,
                    para_entry["index"],
                    len(original),
                    len(rewritten),
                )
                continue
            target_pattern_ids = {
                str(hit.get("pattern_id") or "")
                for hit in affected.get(para_entry["index"], [])
                if str(hit.get("pattern_id") or "")
            }
            before_target_counts = self._target_family_counts(original, target_pattern_ids)
            after_target_counts = self._target_family_counts(rewritten, target_pattern_ids)
            unresolved_families = {
                family
                for family, before_count in before_target_counts.items()
                if before_count > 0 and after_target_counts.get(family, 0) >= before_count
            }
            if unresolved_families:
                _logger.debug(
                    "humanize_paragraph_rewrite | chapter=%d | para=%d | "
                    "target_not_reduced=%s — keeping original",
                    chapter_number,
                    para_entry["index"],
                    sorted(unresolved_families),
                )
                continue
            quality_regressions = self.introduced_quality_regressions(original, rewritten)
            if quality_regressions:
                _logger.debug(
                    "humanize_paragraph_rewrite | chapter=%d | para=%d | "
                    "quality_regression=%s — keeping original",
                    chapter_number,
                    para_entry["index"],
                    quality_regressions[:3],
                )
                continue
            result_paras[para_entry["index"]] = rewritten
            applied += 1

        if applied == 0:
            return current_text, 0

        return "\n\n".join(result_paras), applied

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _empty_result(chapter_text: str, chapter_number: int) -> HumanizeScanResult:
        """Return a no-op result with an empty report."""
        empty_report = HumanizeReport(chapter_number=chapter_number)
        return HumanizeScanResult(
            report=empty_report,
            revised_text=chapter_text,
            patches_applied=0,
            paragraph_rewrites_applied=0,
        )
