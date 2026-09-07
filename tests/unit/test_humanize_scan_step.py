"""Unit tests for HumanizeScanStep schema, prescreen, and patch gating."""

from __future__ import annotations

from typing import Literal

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import (
    get_task_format_contract,
    validate_json_output_contract,
)
from novel_forge.core.schemas.humanize import (
    HUMANIZE_PATTERN_IDS,
    HumanizePatternHit,
    HumanizeReport,
)
from novel_forge.core.utils.rhythm_metrics import compute_chapter_rhythm_signature
from novel_forge.pipeline.long.stages.humanize_layer import _collect_prior_rhythm_signatures
from novel_forge.pipeline.steps import humanize_scan_step as humanize_scan_module
from novel_forge.pipeline.steps.humanize_scan_step import HumanizeScanInput, HumanizeScanStep
from novel_forge.prompts.builder import PromptBuilder


def _hit(
    *,
    pattern_id: str = "filler_phrases",
    evidence: str = "值得注意的是",
    suggestion: str = "",
    severity: Literal["critical", "high", "medium", "low"] = "high",
    confidence: float = 0.95,
    actionable: bool = True,
    paragraph_index: int = 0,
) -> HumanizePatternHit:
    return HumanizePatternHit(
        pattern_id=pattern_id,
        pattern_name="填充短语",
        category="元语言",
        severity=severity,
        evidence_quote=evidence,
        paragraph_index=paragraph_index,
        suggestion=suggestion or evidence.replace("值得注意的是", "").strip() or "她没有回来",
        confidence=confidence,
        actionable=actionable,
        source="llm",
    )


def _valid_report_payload() -> dict[str, object]:
    return {
        "source_text_hash": "abc",
        "chapter_number": 1,
        "total_hits": 1,
        "hits_by_category": {"元语言": 1},
        "critical_hits": 0,
        "pattern_hits": [
            {
                "pattern_id": "filler_phrases",
                "pattern_name": "填充短语",
                "category": "元语言",
                "severity": "high",
                "evidence_quote": "值得注意的是",
                "paragraph_index": 0,
                "suggestion": "她没有回来",
                "confidence": 0.95,
                "actionable": True,
                "source": "llm",
                "span_start": None,
                "span_end": None,
            }
        ],
        "humanize_score": 7.5,
        "summary": "检测到一处填充短语。",
    }


def test_humanize_pattern_id_accepts_string_and_legacy_int() -> None:
    modern = _hit(pattern_id="filler_phrases")
    legacy = HumanizePatternHit(
        pattern_id=8,
        pattern_name="填充短语",
        category="元语言",
        severity="high",
        evidence_quote="值得注意的是",
    )

    assert modern.pattern_id == "filler_phrases"
    assert legacy.pattern_id == "filler_phrases"

    provider_drift = HumanizePatternHit(
        pattern_id="filler_phrases",
        pattern_name="填充短语",
        category="元语言",
        severity="high",
        evidence_quote="值得注意的是",
        paragraph_index=None,
    )
    assert provider_drift.paragraph_index == 0


def test_humanize_scan_contract_has_typed_schema_and_rejects_type_drift() -> None:
    contract = get_task_format_contract(TaskType.HUMANIZE_SCAN)

    assert contract is not None
    assert contract.schema_model == "humanize_report"
    assert contract.json_schema is not None
    assert "source_text_hash" not in contract.required_top_level_keys
    assert "chapter_number" not in contract.required_top_level_keys
    assert contract.json_schema["properties"]["critical_hits"]["type"] == "integer"

    payload = _valid_report_payload()
    validate_json_output_contract(TaskType.HUMANIZE_SCAN, payload)

    payload_without_local_fields = _valid_report_payload()
    payload_without_local_fields.pop("source_text_hash")
    payload_without_local_fields.pop("chapter_number")
    validate_json_output_contract(TaskType.HUMANIZE_SCAN, payload_without_local_fields)

    payload["critical_hits"] = []
    with pytest.raises(ValueError, match=r"critical_hits.*expected integer"):
        validate_json_output_contract(TaskType.HUMANIZE_SCAN, payload)


def test_humanize_scan_contract_allows_nullable_paragraph_index_for_local_recovery() -> None:
    payload = _valid_report_payload()
    pattern_hit = payload["pattern_hits"][0]
    assert isinstance(pattern_hit, dict)
    pattern_hit["paragraph_index"] = None

    validate_json_output_contract(TaskType.HUMANIZE_SCAN, payload)


def test_humanize_parse_report_recovers_null_paragraph_index_from_unique_evidence() -> None:
    text = "第一段只是风声。\n\n第二段里，风经过榕树，经过屋檐，经过我的窗户。"
    payload = _valid_report_payload()
    pattern_hit = payload["pattern_hits"][0]
    assert isinstance(pattern_hit, dict)
    pattern_hit.update(
        {
            "pattern_id": "rule_of_three",
            "pattern_name": "三项列举",
            "category": "模板句式",
            "severity": "medium",
            "evidence_quote": "风经过榕树，经过屋檐，经过我的窗户。",
            "paragraph_index": None,
            "suggestion": "风经过榕树和屋檐，从我的窗户穿过来。",
            "confidence": 0.85,
            "actionable": True,
        }
    )
    payload["total_hits"] = 99
    payload["hits_by_category"] = {"错误计数": 99}

    report = HumanizeScanStep._parse_report(payload, 4, text)

    assert report is not None
    assert report.pattern_hits[0].paragraph_index == 1
    assert report.total_hits == 1
    assert report.hits_by_category == {"模板句式": 1}


@pytest.mark.parametrize("source", ["local", "llm", "merged", "library", "library_user"])
def test_humanize_scan_contract_source_enum_matches_domain_schema(source: str) -> None:
    payload = _valid_report_payload()
    pattern_hit = payload["pattern_hits"][0]
    assert isinstance(pattern_hit, dict)
    pattern_hit["source"] = source

    validate_json_output_contract(TaskType.HUMANIZE_SCAN, payload)
    HumanizePatternHit(**pattern_hit)


def test_parse_report_rebinds_model_location_to_canonical_source_span() -> None:
    text = "第一段只是风声。\n\n他迅速收回手，仿佛被烫到。"
    payload = _valid_report_payload()
    payload["pattern_hits"] = [
        {
            "pattern_id": "weak_verb_stacking",
            "pattern_name": "弱动词堆叠",
            "category": "叙事轻重",
            "severity": "high",
            "evidence_quote": "仿佛被烫到",
            "paragraph_index": 99,
            "suggestion": "",
            "confidence": 0.8,
            "actionable": False,
            "source": "llm",
            "span_start": 1,
            "span_end": 2,
        }
    ]

    report = HumanizeScanStep._parse_report(payload, 1, text)

    assert report is not None
    hit = report.pattern_hits[0]
    assert hit.paragraph_index == 1
    assert hit.span_start == text.index("仿佛被烫到")
    assert hit.span_end == hit.span_start + len("仿佛被烫到")


def test_parse_report_drops_impossible_critical_library_hit() -> None:
    text = "沈岸把最后一个旧玻璃罐推上架子。"
    payload = _valid_report_payload()
    payload["pattern_hits"] = [
        {
            "pattern_id": "collaborative_artifact",
            "pattern_name": "协作对话残留",
            "category": "聊天残留",
            "severity": "critical",
            "evidence_quote": text,
            "paragraph_index": 0,
            "suggestion": "",
            "confidence": 0.9,
            "actionable": False,
            "source": "library",
            "span_start": None,
            "span_end": None,
        }
    ]

    report = HumanizeScanStep._parse_report(payload, 1, text)

    assert report is not None
    assert report.pattern_hits == []
    assert report.total_hits == 0


def test_prescreen_does_not_treat_natural_this_is_a_phrase_as_chat_residue() -> None:
    hits = HumanizeScanStep.prescreen_text("这是一个安静的清晨。")

    assert "collaborative_artifact" not in {hit["pattern_id"] for hit in hits}


def test_humanize_scan_contract_rejects_unknown_source() -> None:
    payload = _valid_report_payload()
    pattern_hit = payload["pattern_hits"][0]
    assert isinstance(pattern_hit, dict)
    pattern_hit["source"] = "external"

    with pytest.raises(ValueError, match=r"source.*expected one of"):
        validate_json_output_contract(TaskType.HUMANIZE_SCAN, payload)


def test_humanize_prescreen_detects_ai_tells_but_ignores_dialogue() -> None:
    text = (
        "「值得注意的是，这不是他的错，而是命运的问题。」\n\n"
        "值得注意的是，他推开门，这标志着故事的关键时刻。"
    )

    hits = HumanizeScanStep.prescreen_text(text, filter_dialogue=True)
    evidence = [str(hit["evidence_quote"]) for hit in hits]

    assert any("值得注意的是" in item for item in evidence)
    assert any(hit["pattern_id"] == "significance_inflation" for hit in hits)
    assert all("这不是他的错" not in item for item in evidence)


def test_cross_chapter_structure_prescreen_accepts_signature_with_chapter_number() -> None:
    text = ("她停下。风穿过门缝，灯影晃了一下。\n\n" * 8).strip()
    prior = compute_chapter_rhythm_signature(text).to_dict()
    prior["chapter_number"] = 3

    hits = HumanizeScanStep._prescreen_cross_chapter_structure(text, [prior])

    assert len(hits) == 1
    assert hits[0].pattern_id == "cross_chapter_template"
    assert hits[0].source == "local"
    assert hits[0].actionable is False


async def test_collect_prior_rhythm_signatures_awaits_episodic_memory() -> None:
    text = ("她停下。风穿过门缝，灯影晃了一下。\n\n" * 30).strip()

    class Episodic:
        async def search_by_semantic(self, **_kwargs):
            return [{"text": text}]

    class MemoryContext:
        episodic_memory = Episodic()

    class Runner:
        memory_context = MemoryContext()

        @staticmethod
        def has_memory_context() -> bool:
            return True

    signatures = await _collect_prior_rhythm_signatures(Runner(), None, 2, 1)

    assert len(signatures) == 1
    assert signatures[0]["chapter_number"] == 1
    assert signatures[0]["sentence_count"] > 0


def test_humanize_patch_builder_promotes_safe_report_only_hit_and_requires_confidence() -> None:
    text = "此外她没有回来。"

    # A provider may locate the issue and give a concrete safe suggestion while
    # still marking actionable=False. The local validator can promote a
    # context-free vocabulary hit, but not a semantic word-deletion pattern.
    report_only_hit = _hit(
        pattern_id="ai_vocabulary",
        evidence="此外",
        suggestion="当时",
        actionable=False,
    )
    patches, unpatchable = HumanizeScanStep._build_patches([report_only_hit], text)
    assert len(patches) == 1
    assert unpatchable == 0
    assert report_only_hit.actionable is True

    report_only_structural = HumanizePatternHit(
        pattern_id="monotone_rhythm",
        pattern_name="节奏单调",
        category="节奏问题",
        severity="medium",
        evidence_quote=text,
        paragraph_index=0,
        suggestion="调整相邻句长，穿插短句。",
        confidence=0.9,
        actionable=False,
        source="llm",
    )
    assert HumanizeScanStep._build_patches([report_only_structural], text) == ([], 0)

    semantic_word_deletion = _hit(
        pattern_id="filler_phrases",
        evidence="值得注意的是",
        suggestion="",
        actionable=False,
    )
    assert HumanizeScanStep._build_patches(
        [semantic_word_deletion],
        "值得注意的是。",
    ) == ([], 0)
    assert semantic_word_deletion.actionable is False

    text = "值得注意的是。"
    # confidence=0.79 should be rejected (floor is 0.80)
    assert HumanizeScanStep._build_patches(
        [_hit(confidence=0.79)],
        text,
    ) == ([], 0)
    patches, unpatchable = HumanizeScanStep._build_patches(
        [_hit(confidence=0.79)],
        text,
        patch_confidence_floor=0.75,
    )
    assert len(patches) == 1
    assert unpatchable == 0

    # severity="low" should still be rejected (only critical/high/medium are patchable)
    assert HumanizeScanStep._build_patches(
        [_hit(severity="low")],
        text,
    ) == ([], 0)

    # confidence=0.80 should now be accepted (floor lowered from 0.85 to 0.80)
    patches, unpatchable = HumanizeScanStep._build_patches(
        [_hit(confidence=0.80)],
        text,
    )
    assert len(patches) == 1
    assert unpatchable == 0

    # severity="medium" should now be accepted (medium added to patchable severities)
    patches, unpatchable = HumanizeScanStep._build_patches(
        [_hit(severity="medium")],
        text,
    )
    assert len(patches) == 1
    assert unpatchable == 0

    # Default hit (high severity, 0.95 confidence, actionable=True) should work
    patches, unpatchable = HumanizeScanStep._build_patches([_hit()], text)
    assert len(patches) == 1
    assert unpatchable == 0
    assert patches[0].original == "值得注意的是"


def test_humanize_patch_builder_rejects_placeholder_delete_suggestion() -> None:
    text = "当然！下面是这一章的内容。\n\n她推开门。"

    assert HumanizeScanStep._build_patches(
        [
            _hit(
                pattern_id="collaborative_artifact",
                evidence="当然！下面是这一章的内容。",
                suggestion="删除该句",
                severity="critical",
            )
        ],
        text,
    ) == ([], 1)


def test_humanize_patch_builder_uses_paragraph_anchor_and_rejects_duplicate_evidence() -> None:
    duplicate_text = "值得注意的是，她没有回来。\n\n值得注意的是，他也没有回来。"
    patches, unpatchable = HumanizeScanStep._build_patches(
        [_hit(paragraph_index=1)],
        duplicate_text,
    )
    assert len(patches) == 1, (
        "evidence unique within paragraph 1 should be patchable even if it appears in paragraph 0"
    )
    assert patches[0].para_start == 1
    assert patches[0].para_end == 1

    # Evidence that appears multiple times WITHIN the same paragraph should still reject
    same_para_duplicate = "值得注意的是，她没有回来。值得注意的是，门也关了。"
    patches2, unpatchable2 = HumanizeScanStep._build_patches(
        [_hit(evidence="值得注意的是", paragraph_index=0)],
        same_para_duplicate,
    )
    assert len(patches2) == 0, "evidence appearing >1 in same paragraph should be rejected"
    assert unpatchable2 == 1


def test_humanize_patch_builder_coalesces_overlapping_same_sentence_hits() -> None:
    text = (
        "不是想要说更多的松动，而是发现自己并没有像预想中那样抗拒这些人的出现"
        "——尤其是苏皖，那种沉默的气质让沈鹿溪隐约觉得，这个人的到来不只是为了住店。"
    )
    negative = HumanizePatternHit(
        pattern_id="negative_parallelism",
        pattern_name="否定式并列",
        category="模板句式",
        severity="high",
        evidence_quote=text,
        paragraph_index=0,
        suggestion=(
            "她的内心在松动，但不是想要说更多——尤其是苏皖，"
            "那种沉默的气质让沈鹿溪隐约觉得，这个人的到来不只是为了住店。"
        ),
        confidence=0.9,
        actionable=True,
        source="llm",
    )
    em_dash = HumanizePatternHit(
        pattern_id="em_dash_overuse",
        pattern_name="破折号滥用",
        category="模板句式",
        severity="high",
        evidence_quote=(
            "而是发现自己并没有像预想中那样抗拒这些人的出现——尤其是苏皖，"
            "那种沉默的气质让沈鹿溪隐约觉得"
        ),
        paragraph_index=0,
        suggestion=(
            "而是发现自己并没有像预想中那样抗拒这些人的出现，尤其是苏皖，"
            "那种沉默的气质让沈鹿溪隐约觉得"
        ),
        confidence=0.85,
        actionable=True,
        source="llm",
    )

    patches, unpatchable = HumanizeScanStep._build_patches([negative, em_dash], text)

    assert len(patches) == 1
    assert unpatchable == 0
    assert patches[0].original == text
    assert "——" not in patches[0].replacement


def test_humanize_patch_builder_trusts_unique_evidence_over_wrong_paragraph_index() -> None:
    text = "第一段只是风声。\n\n第二段里，她捕捉到了——没有好奇，只有防御。"
    hit = HumanizePatternHit(
        pattern_id="negative_parallelism",
        pattern_name="否定式并列",
        category="模板句式",
        severity="high",
        evidence_quote="第二段里，她捕捉到了——没有好奇，只有防御。",
        paragraph_index=0,
        suggestion="第二段里，她捕捉到了那点防御。",
        confidence=0.9,
        actionable=True,
        source="llm",
    )

    patches, unpatchable = HumanizeScanStep._build_patches([hit], text)

    assert len(patches) == 1
    assert patches[0].para_start == 1
    assert patches[0].para_end == 1
    assert unpatchable == 0


def test_humanize_patch_builder_rejects_synonymous_adjacent_action_regression() -> None:
    text = "他迅速收回手，仿佛被烫到。"
    hit = HumanizePatternHit(
        pattern_id="weak_verb_stacking",
        pattern_name="弱动词堆叠",
        category="叙事轻重",
        severity="high",
        evidence_quote="仿佛被烫到",
        paragraph_index=0,
        suggestion="猛地缩回手",
        confidence=0.8,
        actionable=True,
        source="llm",
    )

    patches, unpatchable = HumanizeScanStep._build_patches([hit], text)

    assert patches == []
    assert unpatchable == 1
    assert hit.actionable is False


def test_humanize_patch_builder_rejects_repeated_subject_at_patch_boundary() -> None:
    text = "沈岸的目光从她湿透的裙摆，移到她指缝的灰渍上。"
    hit = HumanizePatternHit(
        pattern_id="rule_of_three",
        pattern_name="三项列举",
        category="模板句式",
        severity="medium",
        evidence_quote="从她湿透的裙摆，移到她指缝的灰渍上",
        paragraph_index=0,
        suggestion="沈岸的目光从她湿透的裙摆移到她指缝的灰渍上",
        confidence=0.8,
        actionable=True,
        source="llm",
    )

    patches, unpatchable = HumanizeScanStep._build_patches([hit], text)

    assert patches == []
    assert unpatchable == 1
    assert hit.actionable is False


def test_local_suggestion_returns_empty_for_structural_patterns_without_safe_transform() -> None:
    """Structure-level rewrites stay empty unless a local transform is safe.

    Echoing the evidence back as the suggestion caused ``_build_patches`` to
    silently skip every hit (``suggestion == evidence``), producing the
    "5 hits / 0 patches" bug.  The local layer may now fix vocabulary residue,
    but it still refuses to invent semantic rewrites for structural patterns.
    """
    cases = [
        ("negative_parallelism", "不是酒沫，不是光斑"),
        ("rule_of_three", "古籍、字画和瓷器"),
    ]
    for pattern_id, evidence in cases:
        result = HumanizeScanStep._local_suggestion(pattern_id, evidence)
        assert result == "", (
            f"_local_suggestion({pattern_id!r}, {evidence!r}) returned {result!r} "
            f"but should return empty string to force LLM to provide a real rewrite"
        )
        # Critically, the result must not equal the evidence
        assert result != evidence, (
            f"_local_suggestion({pattern_id!r}) echoed evidence as suggestion — "
            f"this is the root cause of the 0-patches bug"
        )


def test_local_suggestion_still_transforms_patterns_with_local_rules() -> None:
    """Patterns with safe in-place transformations keep working."""
    # "标志着" and "关键时刻" are both in the replacements tuple, so both are stripped
    assert HumanizeScanStep._local_suggestion("significance_inflation", "这标志着关键时刻") == "这"
    assert HumanizeScanStep._local_suggestion("ai_vocabulary", "此外这是关键") == "这是关键"
    assert HumanizeScanStep._local_suggestion("filler_phrases", "值得注意的是,他沉默") == "他沉默"
    assert (
        HumanizeScanStep._local_suggestion("em_dash_overuse", "他走进来——环顾四周")
        == "他走进来，环顾四周"
    )


def test_prescreen_ranking_does_not_let_rule_of_three_starve_high_severity_hits() -> None:
    noisy = "，".join(f"物件{i}" for i in range(40))
    text = f"{noisy}\n\n他走进来——环顾四周。"

    hits = HumanizeScanStep.prescreen_text(text, filter_dialogue=True)
    pattern_ids = [str(hit["pattern_id"]) for hit in hits]

    assert "em_dash_overuse" in pattern_ids
    assert pattern_ids.count("rule_of_three") <= 4


def test_prescreen_keeps_high_severity_contrast_locations_beyond_legacy_cap() -> None:
    text = "\n\n".join(f"这不是第{index}个误差，而是第{index}条线索。" for index in range(1, 7))

    hits = HumanizeScanStep.prescreen_text(text, filter_dialogue=True)
    contrasts = [hit for hit in hits if hit["pattern_id"] == "negative_parallelism"]

    assert len(contrasts) == 6
    assert len({(hit["span_start"], hit["span_end"]) for hit in contrasts}) == 6


def test_prescreen_respects_library_enabled_pattern_ids() -> None:
    text = "不是风停了，而是门关了。值得注意的是，灯也灭了。"

    hits = HumanizeScanStep.prescreen_text(
        text,
        filter_dialogue=False,
        enabled_pattern_ids=frozenset({"negative_parallelism"}),
    )

    assert {hit["pattern_id"] for hit in hits} == {"negative_parallelism"}


def test_canonicalize_prescreen_removes_overlapping_contrast_taxonomy_duplicates() -> None:
    text = "他需要答案。不是从别人那里，而是从数据里。\n\n不是卡住，是彻底静止。"

    hits = HumanizeScanStep.prescreen_text(text, filter_dialogue=True)
    canonical = HumanizeScanStep._canonicalize_prescreen_candidates(hits)
    contrast_hits = [
        hit
        for hit in canonical
        if HumanizeScanStep._pattern_family(str(hit["pattern_id"])) == "contrastive_template"
    ]

    assert len(contrast_hits) == 2
    assert {hit["pattern_id"] for hit in contrast_hits} == {
        "negative_parallelism",
        "binary_judgment_closing",
    }


def test_reconcile_restores_recurrent_structural_hits_rejected_by_llm() -> None:
    text = "\n\n".join(
        (
            "他要的不是解释，而是原始记录。",
            "那不是偶然，而是有人刻意留下的缺口。",
            "这不是终点，而是下一段追查的入口。",
        )
    )
    prescreen = HumanizeScanStep._canonicalize_prescreen_candidates(
        HumanizeScanStep.prescreen_text(text, filter_dialogue=True)
    )
    llm_report = HumanizeReport(
        chapter_number=8,
        total_hits=0,
        hits_by_category={},
        critical_hits=0,
        pattern_hits=[],
        summary="模型将本批候选判为误伤。",
    )

    reconciled = HumanizeScanStep._reconcile_report_with_prescreen(
        llm_report,
        prescreen,
        chapter_number=8,
        chapter_text=text,
    )
    contrast_hits = [
        hit
        for hit in reconciled.pattern_hits
        if HumanizeScanStep._pattern_family(hit.pattern_id) == "contrastive_template"
    ]

    assert len(contrast_hits) == 3
    assert all(hit.source == "local" for hit in contrast_hits)
    assert all(hit.actionable is False and hit.suggestion == "" for hit in contrast_hits)
    assert all(hit.span_start is not None and hit.span_end is not None for hit in contrast_hits)
    assert "重复密度复核补回 3 条" in reconciled.summary


async def test_paragraph_rewrite_accepts_only_when_target_family_is_reduced() -> None:
    original = "他要的不是解释，而是原始记录。"
    candidate = next(
        hit
        for hit in HumanizeScanStep.prescreen_text(original, filter_dialogue=True)
        if hit["pattern_id"] == "negative_parallelism"
    )
    candidate.update({"suggestion": "", "actionable": False, "source": "local"})
    report = HumanizeReport(
        chapter_number=8,
        total_hits=1,
        hits_by_category={"模板句式": 1},
        critical_hits=0,
        pattern_hits=[HumanizePatternHit.model_validate(candidate)],
        summary="检测到重复对照结构。",
    )

    class _Settings:
        temp_humanize_paragraph_rewrite = 0.2

    class _RewriteStep(HumanizeScanStep):
        def __init__(self, response: str) -> None:
            super().__init__(router=None, builder=None, settings=_Settings())  # type: ignore[arg-type]
            self.response = response

        async def _call_with_retry(self, *args: object, **kwargs: object) -> str:
            return self.response

    ineffective = _RewriteStep("他真正要的不是解释，而是未经处理的原始记录。")
    ineffective_text, ineffective_count = await ineffective._paragraph_level_rewrite(
        original,
        report,
        8,
    )

    effective = _RewriteStep("他只接受原始记录，解释留给别人。")
    effective_text, effective_count = await effective._paragraph_level_rewrite(
        original,
        report,
        8,
    )

    assert ineffective_text == original
    assert ineffective_count == 0
    assert effective_text != original
    assert effective_count == 1


def test_prescreen_hit_with_empty_local_suggestion_is_not_actionable() -> None:
    text = "古籍、字画和瓷器都放在架上。"

    hits = HumanizeScanStep.prescreen_text(text, filter_dialogue=True)
    rule_hit = next(hit for hit in hits if hit["pattern_id"] == "rule_of_three")

    assert rule_hit["suggestion"] == ""
    assert rule_hit["actionable"] is False


def test_prescreen_detects_synonym_cycling_candidate_without_auto_patch() -> None:
    text = "林远推开窗户，少年深吸一口气，远哥望着远山，男人没有说话。"

    hits = HumanizeScanStep.prescreen_text(text, filter_dialogue=True)
    hit = next(item for item in hits if item["pattern_id"] == "synonym_cycling")

    assert hit["actionable"] is False
    assert hit["suggestion"] == ""
    assert "少年" in hit["evidence_quote"]


def test_all_registered_humanize_patterns_have_candidate_source() -> None:
    rule_ids = {rule.pattern_id for rule in humanize_scan_module._HUMANIZE_RULES}
    special_prescreen_ids = {
        "excessive_hedging",
        "monotone_rhythm",
        "cross_chapter_template",
        "synonym_cycling",
    }

    missing = set(HUMANIZE_PATTERN_IDS) - (rule_ids | special_prescreen_ids)

    assert not missing


def test_build_patches_regression_five_hits_zero_suggestions_scenario() -> None:
    """Regression: the 弈局谋心 chapter had 5 ``negative_parallelism`` hits
    with empty/identical-to-evidence suggestions, and ``_build_patches``
    silently skipped them all.  The defensive ``_validate_suggestion_usable``
    must downgrade ``actionable`` to ``False`` and report the unpatchable count.
    """
    # Evidence is in paragraph 2 of the test text, matching the actual
    # chapter layout from the bug report.
    text = (
        "广袖垂落时，薄刃的锋沿硌着腕骨。\n\n"
        "合卺酒送至面前。\n\n"
        "金杯在烛光下泛着暖色，酒液表面平静无波。她的目光掠过杯沿，"
        "捕捉到那层极细的银粉——均匀涂抹在杯口内侧的金属微尘，不是酒沫，不是光斑。"
    )
    hit_with_real_suggestion = HumanizePatternHit(
        pattern_id="negative_parallelism",
        pattern_name="否定式并列",
        category="模板句式",
        severity="high",
        evidence_quote="不是酒沫，不是光斑",
        paragraph_index=2,
        suggestion="而是极细的银粉",
        confidence=0.9,
        actionable=True,
        source="llm",
    )
    hit_with_empty_suggestion = HumanizePatternHit(
        pattern_id="negative_parallelism",
        pattern_name="否定式并列",
        category="模板句式",
        severity="high",
        evidence_quote="不是酒沫，不是光斑",
        paragraph_index=2,
        suggestion="",
        confidence=0.9,
        actionable=True,
        source="llm",
    )
    hit_with_same_suggestion = HumanizePatternHit(
        pattern_id="negative_parallelism",
        pattern_name="否定式并列",
        category="模板句式",
        severity="high",
        evidence_quote="不是酒沫，不是光斑",
        paragraph_index=2,
        suggestion="不是酒沫，不是光斑",
        confidence=0.9,
        actionable=True,
        source="llm",
    )

    patches, unpatchable = HumanizeScanStep._build_patches(
        [hit_with_real_suggestion, hit_with_empty_suggestion, hit_with_same_suggestion],
        text,
    )
    # Only the first hit should produce a patch
    assert len(patches) == 1
    assert patches[0].original == "不是酒沫，不是光斑"
    assert patches[0].replacement == "而是极细的银粉"
    assert unpatchable == 2
    # Downgrade: empty / no-op suggestions flip actionable back to False
    assert hit_with_empty_suggestion.actionable is False
    assert hit_with_same_suggestion.actionable is False
    # Real suggestion stays actionable
    assert hit_with_real_suggestion.actionable is True


def test_build_patches_rejects_incomplete_negative_parallelism_anchor() -> None:
    text = (
        "他走出去两步，又停下来，回头看她的方向——不是看她的脸，而是看她身后东偏房那扇半开的窗户。"
    )
    hit = HumanizePatternHit(
        pattern_id="negative_parallelism",
        pattern_name="否定式并列",
        category="模板句式",
        severity="high",
        evidence_quote="不是看她的脸，而是",
        paragraph_index=0,
        suggestion="他的视线落在她身后东偏房那扇半开的窗户上",
        confidence=0.9,
        actionable=True,
        source="llm",
    )

    patches, unpatchable = HumanizeScanStep._build_patches([hit], text)

    assert patches == []
    assert unpatchable == 1
    assert hit.actionable is False


def test_build_patches_rejects_structural_suggestion_repeating_left_context() -> None:
    text = (
        "林正点了点头，没有追问录了什么。"
        "他只是抬手指了一下天空的方向——"
        "不是指具体的哪一片云，而是指整个山谷上空那些被风推着往东走的云群。"
    )
    hit = HumanizePatternHit(
        pattern_id="negative_parallelism",
        pattern_name="否定式并列",
        category="模板句式",
        severity="high",
        evidence_quote="不是指具体的哪一片云，而是指整个山谷上空那些被风推着往东走的云群",
        paragraph_index=0,
        suggestion="他抬手指了一下天空——整片山谷上空那些被风推着往东走的云群",
        confidence=0.9,
        actionable=True,
        source="llm",
    )

    patches, unpatchable = HumanizeScanStep._build_patches([hit], text)

    assert patches == []
    assert unpatchable == 1
    assert hit.actionable is False


def test_humanize_report_carries_patchable_and_unpatchable_counts() -> None:
    """The report schema must surface patchable/unpatchable counts so the
    UI can distinguish "no hits" from "hits detected but not patchable"."""
    from novel_forge.core.schemas.humanize import HumanizePatternHit, HumanizeReport

    report = HumanizeReport(
        chapter_number=1,
        total_hits=5,
        hits_by_category={"模板句式": 5},
        critical_hits=0,
        pattern_hits=[
            HumanizePatternHit(
                pattern_id="negative_parallelism",
                pattern_name="否定式并列",
                category="模板句式",
                severity="high",
                evidence_quote="不是酒沫，不是光斑",
                paragraph_index=0,
                suggestion="",
                confidence=0.9,
                actionable=True,
                source="local",
            )
        ],
        humanize_score=7.0,
        summary="",
        patchable_hits=0,
        unpatchable_hits=5,
    )

    assert report.patchable_hits == 0
    assert report.unpatchable_hits == 5
    # Total hits still includes the 5 detected (even though only 1 is in pattern_hits for the test)
    assert report.total_hits == 5


def test_humanize_fallback_report_from_prescreen_is_valid_and_counted() -> None:
    text = "值得注意的是，她没有回来。\n\n他走进来——环顾四周。"
    prescreen_hits = HumanizeScanStep.prescreen_text(text, filter_dialogue=True)

    report = HumanizeScanStep._fallback_report_from_prescreen(
        text,
        7,
        prescreen_hits,
        reason="unit_test",
    )

    assert report is not None
    assert report.chapter_number == 7
    assert report.source_text_hash
    assert report.total_hits == len(report.pattern_hits)
    assert report.hits_by_category
    assert "本地确定性预扫描" in report.summary


def test_prescreen_prompt_context_uses_bounded_relevant_paragraphs() -> None:
    text = "第一段只是风声。\n\n第二段里，她走进来——环顾四周。\n\n第三段没有问题。"
    hits = HumanizeScanStep.prescreen_text(text, filter_dialogue=True)
    compact = HumanizeScanStep._compact_prescreen_hits_for_prompt(hits, limit=16)

    excerpt = HumanizeScanStep._chapter_context_for_prescreen_prompt(text, compact)

    assert "[段落 1]" in excerpt
    assert "她走进来——环顾四周" in excerpt
    assert "第一段只是风声" not in excerpt
    assert len(excerpt) < len(text)


def test_prompt_context_compacts_candidates_style_and_library_hits() -> None:
    text = "第一段。\n\n值得注意的是，她推开门。"
    local_hits = [
        {
            "pattern_id": "filler_phrases",
            "pattern_name": "填充短语",
            "category": "元语言",
            "severity": "high",
            "evidence_quote": "值得注意的是",
            "paragraph_index": 1,
            "suggestion": "",
            "confidence": 0.95,
            "actionable": True,
            "source": "local",
        }
    ]
    scan_input = HumanizeScanInput(
        chapter_number=1,
        chapter_text=text,
        style_profile={
            "summary": "冷峻克制" * 80,
            "global_style": {
                "emotional_style": "subtle",
                "banned_phrases": [f"禁用词{i}" for i in range(30)],
            },
            "modules": [{"name": "冗余模块", "rules": ["不应进入 humanize_scan prompt"]}],
        },
        library_hits=[
            {
                "pattern_id": "lib_user_00000001",
                "pattern_name": "自定义模式",
                "category": "AI写作痕迹",
                "severity": "high",
                "evidence_quote": "她推开门",
                "source": "user",
                "similarity": 0.91,
            }
        ],
    )

    context = HumanizeScanStep._build_llm_context(scan_input, local_hits)

    compact_hit = context["prescreen_hits"][0]
    assert compact_hit["confidence"] == 0.95
    assert "actionable" not in compact_hit
    assert compact_hit["source"] == "local"
    assert "suggestion" not in compact_hit
    assert context["style_profile"]["summary"] == ("冷峻克制" * 80)[:240]
    assert len(context["style_profile"]["global_style"]["banned_phrases"]) == 20
    assert "modules" not in context["style_profile"]
    assert context["library_hits"][0]["source"] == "library_user"
    assert "similarity" not in context["library_hits"][0]
    assert context["library_hits"][0]["paragraph_index"] == 1
    assert context["prescreen_adjudication_only"] is True


def test_library_fallback_drops_retrieval_metadata_before_report_validation() -> None:
    text = "第一段。\n\n她推开门，终于看见那束光。"
    report = HumanizeScanStep._fallback_report_from_candidates(
        text,
        3,
        prescreen_hits=[],
        library_hits=[
            {
                "pattern_id": "lib_user_00000001",
                "pattern_name": "自定义模式",
                "category": "AI写作痕迹",
                "severity": "medium",
                "evidence_quote": "她推开门",
                "source": "user",
                "similarity": 0.91,
            }
        ],
        reason="unit_test",
    )

    assert report is not None
    assert report.pattern_hits[0].source == "library_user"
    assert not hasattr(report.pattern_hits[0], "similarity")


async def test_humanize_scan_skips_llm_when_no_candidates() -> None:
    class _NoCallStep(HumanizeScanStep):
        def _dynamic_max_tokens(self, *args: object, **kwargs: object) -> int:
            return 2048

        async def _call_with_retry(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise AssertionError("LLM should not be called without candidates")

    step = _NoCallStep(router=None, builder=None, settings=object())  # type: ignore[arg-type]

    result = await step._execute(HumanizeScanInput(chapter_number=1, chapter_text="风声停了。"))

    assert result.report.total_hits == 0
    assert result.revised_text == "风声停了。"
    assert result.patches_applied == 0


def test_deterministic_fast_path_only_changes_bounded_punctuation() -> None:
    text = "值得注意的是，这标志着关键时刻。甲——乙。丙——丁。戊——己。庚——辛。"
    candidates = [
        {"pattern_id": "filler_phrases"},
        {"pattern_id": "significance_inflation"},
        {"pattern_id": "em_dash_overuse"},
    ]

    revised, applied, patterns = HumanizeScanStep._deterministic_patch_pass(
        text,
        candidates,
        chapter_number=1,
    )

    assert "值得注意的是" in revised
    assert "标志着关键时刻" in revised
    assert revised.count("——") == 3
    assert applied == 1
    assert patterns == ["em_dash_overuse"]


@pytest.mark.parametrize(
    ("verdict", "expected_text", "expected_actionable", "expected_patches"),
    [
        ("rejected", "这标志着关键时刻。", False, 0),
        ("approved", "这意味着关键时刻。", True, 1),
        ("failed", "这标志着关键时刻。", False, 0),
    ],
)
async def test_semantic_word_repair_requires_affirmative_contextual_adjudication(
    verdict: str,
    expected_text: str,
    expected_actionable: bool,
    expected_patches: int,
) -> None:
    text = "这标志着关键时刻。"
    candidate = {
        "pattern_id": "significance_inflation",
        "pattern_name": "显著性通胀",
        "category": "叙事轻重",
        "severity": "high",
        "evidence_quote": "标志着",
        "paragraph_index": 0,
        "suggestion": "意味着",
        "confidence": 0.9,
        "actionable": True,
        "source": "local",
        "span_start": 1,
        "span_end": 4,
    }

    class _Settings:
        humanize_model = ""
        humanize_patch_confidence_floor = 0.8
        humanize_paragraph_rewrite_threshold = 999

    class _SemanticVerdictStep(HumanizeScanStep):
        @classmethod
        def prescreen_text(
            cls,
            chapter_text: str,
            *,
            filter_dialogue: bool = True,
        ) -> list[dict[str, object]]:
            return [dict(candidate)]

        def _dynamic_max_tokens(self, *args: object, **kwargs: object) -> int:
            return 2048

        async def _call_with_retry(self, *args: object, **kwargs: object) -> dict[str, object]:
            if verdict == "failed":
                raise RuntimeError("provider unavailable")
            actionable = verdict == "approved"
            return {
                "total_hits": 1,
                "hits_by_category": {"叙事轻重": 1},
                "critical_hits": 0,
                "pattern_hits": [
                    {
                        **candidate,
                        "suggestion": "意味着" if actionable else "",
                        "actionable": actionable,
                        "source": "llm",
                    }
                ],
                "humanize_score": 7.0,
                "summary": "语境裁判完成。",
            }

    step = _SemanticVerdictStep(
        router=None,
        builder=None,
        settings=_Settings(),
    )  # type: ignore[arg-type]

    result = await step._execute(HumanizeScanInput(chapter_number=1, chapter_text=text))

    assert result.revised_text == expected_text
    assert result.patches_applied == expected_patches
    assert result.report.pattern_hits[0].actionable is expected_actionable


async def test_humanize_scan_batches_candidates_without_dropping_items() -> None:
    candidates = [
        {
            "pattern_id": "filler_phrases",
            "pattern_name": "填充短语",
            "category": "元语言",
            "severity": "high",
            "evidence_quote": f"值得注意的是{i}",
            "paragraph_index": 0,
            "suggestion": "",
            "confidence": 0.95,
            "actionable": False,
            "source": "local",
            "span_start": None,
            "span_end": None,
        }
        for i in range(25)
    ]

    class _Settings:
        humanize_model = ""
        humanize_patch_confidence_floor = 0.8
        humanize_paragraph_rewrite_threshold = 999

    class _BatchStep(HumanizeScanStep):
        def __init__(self) -> None:
            super().__init__(router=None, builder=None, settings=_Settings())  # type: ignore[arg-type]
            self.batch_sizes: list[int] = []

        @classmethod
        def prescreen_text(
            cls,
            chapter_text: str,
            *,
            filter_dialogue: bool = True,
        ) -> list[dict[str, object]]:
            return candidates

        def _dynamic_max_tokens(self, *args: object, **kwargs: object) -> int:
            return 2048

        async def _call_with_retry(self, *args: object, **kwargs: object) -> dict[str, object]:
            context = kwargs.get("context")
            if context is None and len(args) >= 2:
                context = args[1]
            assert isinstance(context, dict)
            hits = list(context["prescreen_hits"])
            self.batch_sizes.append(len(hits))
            return {
                "total_hits": len(hits),
                "hits_by_category": {"元语言": len(hits)},
                "critical_hits": 0,
                "pattern_hits": hits,
                "humanize_score": 7.0,
                "summary": "批次裁判完成。",
            }

    step = _BatchStep()

    result = await step._execute(HumanizeScanInput(chapter_number=1, chapter_text="正文。"))

    assert step.batch_sizes == [24, 1]
    assert result.report.total_hits == 25
    assert len(result.report.pattern_hits) == 25


def test_humanize_prompt_consumes_prescreen_and_style_context() -> None:
    prompt = PromptBuilder().render(
        TaskType.HUMANIZE_SCAN,
        {
            "chapter_number": 1,
            "chapter_text": "值得注意的是，他推开门。",
            "humanize_strength": "medium",
            "prescreen_hits": [
                {
                    "pattern_id": "filler_phrases",
                    "pattern_name": "填充短语",
                    "evidence_quote": "值得注意的是",
                }
            ],
            "prescreen_hit_count": 1,
            "prescreen_adjudication_only": True,
            "max_report_hits": 12,
            "style_profile": {
                "global_style": {"banned_phrases": ["值得注意的是"]},
                "summary": "冷峻克制",
            },
            "humanize_context": {
                "cognitive_constraints": [
                    {
                        "claim_id": "claim_humanize",
                        "cognitive_subjects": ["他"],
                        "cognitive_object": "门后的真相",
                        "cognitive_level": "suspicion",
                        "action_level": "internal",
                        "reader_awareness": "partial",
                        "character_knowledge_coverage": {"他": "partial"},
                    }
                ]
            },
        },
    )

    assert "本地预扫描候选" in prompt
    assert "不做全章穷举" in prompt
    assert "最多输出 12 条" in prompt
    assert "检测模式清单" not in prompt
    assert "## 输出格式" not in prompt
    assert "source_text_hash" in prompt
    assert "压缩风格参考" in prompt
    assert "不改变事实" in prompt
    assert "角色认知" in prompt
    assert "命中真实性与可修复性是两个独立判断" in prompt
    assert "禁止减一" in prompt
    assert "claim_id=claim_humanize" in prompt
    assert "character_knowledge_coverage=他=partial" in prompt


def test_humanize_paragraph_rewrite_preserves_cognitive_contract() -> None:
    prompt = PromptBuilder().render(
        TaskType.HUMANIZE_PARAGRAPH_REWRITE,
        {
            "chapter_number": 1,
            "paragraphs": [{"index": 0, "text": "他隐约觉得门后有人。"}],
            "pattern_hits": [
                {
                    "pattern_name": "AI 高频词",
                    "category": "措辞",
                    "severity": "medium",
                    "evidence_quote": "隐约觉得",
                }
            ],
            "style_profile": {},
            "humanize_context": {
                "cognitive_constraints": [
                    {
                        "claim_id": "claim_rewrite",
                        "cognitive_subjects": ["他"],
                        "cognitive_object": "门后是谁",
                        "cognitive_level": "suspicion",
                        "action_level": "internal",
                        "reader_awareness": "partial",
                        "character_knowledge_coverage": {"他": "partial"},
                    }
                ]
            },
        },
    )

    assert "claim_id=claim_rewrite" in prompt
    assert "character_knowledge_coverage=他=partial" in prompt


def test_compute_deterministic_score_empty_hits() -> None:
    """Empty hits should return perfect score 10.0."""
    from novel_forge.pipeline.steps.humanize_scan_step import HumanizeScanStep

    assert HumanizeScanStep._compute_deterministic_score([]) == 10.0


def test_compute_deterministic_score_high_severity_hits() -> None:
    """High severity hits should reduce score based on confidence-weighted penalty."""
    from novel_forge.pipeline.steps.humanize_scan_step import HumanizeScanStep

    hits = [
        {"severity": "high", "confidence": 0.9},
        {"severity": "high", "confidence": 0.8},
    ]
    # penalty = 2.0 * 0.9 + 2.0 * 0.8 = 1.8 + 1.6 = 3.4
    # score = 10 - 3.4 = 6.6
    assert HumanizeScanStep._compute_deterministic_score(hits) == 6.6


def test_compute_deterministic_score_critical_cap() -> None:
    """Critical hits should cap score at 5.0."""
    from novel_forge.pipeline.steps.humanize_scan_step import HumanizeScanStep

    hits = [
        {"severity": "critical", "confidence": 0.9},
        {"severity": "medium", "confidence": 0.8},
    ]
    # penalty = 4.0 * 0.9 + 1.0 * 0.8 = 3.6 + 0.8 = 4.4
    # raw score = 10 - 4.4 = 5.6
    # but critical cap = 5.0
    assert HumanizeScanStep._compute_deterministic_score(hits) == 5.0


def test_compute_deterministic_score_mixed_severities() -> None:
    """Mixed severities should compute correctly with their weights."""
    from novel_forge.pipeline.steps.humanize_scan_step import HumanizeScanStep

    hits = [
        {"severity": "critical", "confidence": 1.0},  # 4.0 * 1.0 = 4.0
        {"severity": "high", "confidence": 0.5},  # 2.0 * 0.5 = 1.0
        {"severity": "medium", "confidence": 0.6},  # 1.0 * 0.6 = 0.6
        {"severity": "low", "confidence": 0.4},  # 0.5 * 0.4 = 0.2
    ]
    # penalty = 4.0 + 1.0 + 0.6 + 0.2 = 5.8
    # raw score = 10 - 5.8 = 4.2
    # critical cap = 5.0, but 4.2 < 5.0 so score = 4.2
    assert HumanizeScanStep._compute_deterministic_score(hits) == 4.2


def test_compute_deterministic_score_low_only() -> None:
    """Low severity hits should have minimal impact."""
    from novel_forge.pipeline.steps.humanize_scan_step import HumanizeScanStep

    hits = [
        {"severity": "low", "confidence": 0.5},
        {"severity": "low", "confidence": 0.3},
    ]
    # penalty = 0.5 * 0.5 + 0.5 * 0.3 = 0.25 + 0.15 = 0.4
    # score = 10 - 0.4 = 9.6
    assert HumanizeScanStep._compute_deterministic_score(hits) == 9.6


def test_estimate_humanize_score_delegates_to_deterministic() -> None:
    """_estimate_humanize_score should use the same formula as _compute_deterministic_score."""
    from novel_forge.pipeline.steps.humanize_scan_step import HumanizeScanStep

    hits = [
        {"severity": "high", "confidence": 0.9},
        {"severity": "medium", "confidence": 0.7},
    ]
    assert HumanizeScanStep._estimate_humanize_score(
        hits
    ) == HumanizeScanStep._compute_deterministic_score(hits)
