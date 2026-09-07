"""Tests for the dedicated structured-output repair orchestrator."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.format_contracts import validate_json_output_contract
from novel_forge.core.parsing.response_schemas import validate_response_schema
from novel_forge.core.response_repair.orchestrator import (
    _TASK_SPECIFIC_REPAIR_STRATEGIES,
    FormatRepairContext,
    RepairRisk,
    RepairSource,
    _classify_local_repair_risk,
    repair_json_object_response,
)
from novel_forge.pipeline.long.services.task_output_adapters import apply_task_output_adapter


def _init_claim_response_with_duplicate_field(
    *,
    field: str,
    first_value: Any,
    second_value: Any,
    claim_id: str = "outline_1_2_001",
) -> str:
    claim_prefix = (
        '{"claims":[{'
        f'"claim_id":{json.dumps(claim_id, ensure_ascii=False)},'
        '"artifact":"outline",'
        '"source_path":"/chapters/0:5#part1_1_2/chapter_1",'
        '"subject_ids":["char_e48458705aa0"],'
        '"axis":"玄慧人格裂隙状态",'
        '"claim_type":"state",'
        '"claim_text":"玄慧在婚礼上维持沉默",'
        '"state_before":"物品出现引发追问风险",'
        '"state_after":"双方均选择不追问",'
    )
    claim_suffix = (
        '"payoff_id":"",'
        '"payoff_kind":"",'
        '"irreversible":false,'
        '"temporality":"actual",'
        '"cognitive_subjects":[],'
        '"cognitive_object":"",'
        '"cognitive_level":"unaware",'
        '"action_level":"none",'
        '"reader_awareness":"full",'
        '"character_knowledge_coverage":{},'
        '"cognitive_chapter":null,'
        '"public_reveal_chapter":null,'
        '"foreshadow_chapters":[],'
        '"confidence":0.9'
        '}],"coverage_status":"complete","unprocessed_source_refs":[],"summary":"ok"}'
    )
    return (
        claim_prefix
        + f'"{field}":{json.dumps(first_value, ensure_ascii=False)},'
        + f'"{field}":{json.dumps(second_value, ensure_ascii=False)},'
        + claim_suffix
    )


def _validate_init_claim_response(data: dict[str, Any]) -> None:
    validate_response_schema(data, TaskType.EXTRACT_INIT_COHERENCE_CLAIMS)


def test_orchestrator_accepts_safe_local_json_repair() -> None:
    raw = (
        '{"chapters":[{"chapter_number":20,"title":"来不及说出口的三个字",'
        '"goal":"推进关系临界点","beats_summary":["她强撑没有后退"],'
        '"main_plot_points":["陆云峥首次展示怀表"],'
        '"expected_hook":{"hook_type":"emotion","hook_strength":"strong",'
        '"hook_description":"沈鹤卿说这次可别再错过了"}]},'
        '"expected_payoffs":[{"payoff_type":"relationship","description":"她选择靠近"}]}]}'
    )

    def _validate(data: dict[str, Any]) -> None:
        if "chapters" not in data:
            raise KeyError("chapters")

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.PLAN_OUTLINE_BATCH,
                raw_content=raw,
                error=None,
                required_keys=("chapters",),
            ),
            validator=_validate,
        )
    )

    assert result.success is True
    assert result.source == RepairSource.LOCAL
    assert result.data is not None
    assert [item["chapter_number"] for item in result.data["chapters"]] == [20]
    assert result.data["chapters"][0]["expected_payoffs"][0]["description"] == "她选择靠近"


def test_orchestrator_repairs_outline_batch_scalar_beats_summary() -> None:
    raw = (
        '{"chapters":[{"chapter_number":73,"title":"水道危局",'
        '"goal":"清漪主持水道收网并撞破商漪暗线铁证。",'
        '"beats_summary":"清漪以探亲为掩护再赴建业。",'
        '"影卫在三处暗闸截获蠲毒运输船。",'
        '"清漪在水阁暗渠撞见商漪交接暗记本。",'
        '"商漪被迫吐露蜀国以家族商路相胁。",'
        '"main_plot_points":["清漪主导水道收网并截断蠲毒运输暗线",'
        '"清漪撞破商漪双面身份铁证"],'
        '"subplot_points":["鬼蛹密信使身世线倒计时加速"],'
        '"subplot_focus":"商漪背叛线走向临界",'
        '"element_focus":["spy_cover_integrity"],'
        '"pov_character":"沈清漪","pov_switch":false,'
        '"setting":"建业水道与水阁暗渠","expected_word_count":5000,'
        '"pov_character_id":"char_qingyi","pov_character_name":"沈清漪",'
        '"involved_character_ids":["char_qingyi","char_shangyi"],'
        '"required_character_ids":["char_qingyi"],'
        '"support_character_ids":["char_shangyi"],'
        '"involved_character_names":["沈清漪","商漪"],'
        '"cast_plan":{"pov_entity_id":"char_qingyi",'
        '"required_character_ids":["char_qingyi"],'
        '"support_character_ids":["char_shangyi"],'
        '"mention_only_entity_ids":[],'
        '"forbidden_active_character_ids":[]},'
        '"emotional_plan":{"subject_entity_id":"char_qingyi",'
        '"entry_state":"冷静","pressure_source":"水道危机",'
        '"relationship_choice":"逼问商漪",'
        '"turning_emotion":"警觉","exit_aftertaste":"紧绷",'
        '"expression_channels":["动作"]},'
        '"scene_design_goals":["水道收网"],'
        '"involved_characters":["沈清漪","商漪"],'
        '"notes":"友情与国事的冲突必须具体呈现。",'
        '"expected_hook":{"hook_type":"crisis","hook_strength":"strong",'
        '"hook_description":"蜀国影卫以断药机制威胁商漪。"},'
        '"expected_payoffs":[{"payoff_type":"information",'
        '"description":"水阁暗道节点暴露。"}]}]}'
    )

    def _validate(data: dict[str, Any]) -> None:
        validate_json_output_contract(
            TaskType.PLAN_OUTLINE_BATCH,
            data,
            validate_json_schema=True,
        )

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.PLAN_OUTLINE_BATCH,
                raw_content=raw,
                error=None,
                required_keys=("chapters",),
            ),
            validator=_validate,
        )
    )

    assert result.success is True
    assert result.source == RepairSource.LOCAL
    assert result.strategy == "outline_batch_scalar_beats_summary"
    assert result.data is not None
    chapter = result.data["chapters"][0]
    assert chapter["beats_summary"] == [
        "清漪以探亲为掩护再赴建业。",
        "影卫在三处暗闸截获蠲毒运输船。",
        "清漪在水阁暗渠撞见商漪交接暗记本。",
        "商漪被迫吐露蜀国以家族商路相胁。",
    ]


def test_orchestrator_repairs_plan_outline_fragment_sibling_inside_array() -> None:
    raw = (
        '{"character_arcs":[{"character":"沈鹿溪","arc_summary":"从封闭到发声",'
        '"milestones":[{"chapter_start":1,"chapter_end":6,'
        '"description":"初到小镇，封闭自我，只敢记录风声。"}]},'
        '"emotional_arcs":[{"arc_name":"沈鹿溪创伤疗愈弧线",'
        '"emotion_type":"温馨","peak_chapters":[23,24],'
        '"valley_chapters":[1,13],"description":"从压抑到舒展。",'
        '"related_characters":["沈鹿溪"],"related_subplots":["主线"]}]}'
    )
    context = {
        "total_chapters": 24,
        "blueprint_fragment_request": {
            "block_key": "character_arcs",
            "required_keys": ["character_arcs", "emotional_arcs"],
        },
    }

    def _validate(data: dict[str, Any]) -> None:
        apply_task_output_adapter(data, TaskType.PLAN_OUTLINE, context=context)
        validate_json_output_contract(
            TaskType.PLAN_OUTLINE,
            data,
            context=context,
            explicit_required_keys=("character_arcs", "emotional_arcs"),
            include_contract_required_keys=False,
            validate_allowed_keys=True,
            validate_json_schema=True,
        )

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.PLAN_OUTLINE,
                raw_content=raw,
                error=None,
                required_keys=("character_arcs", "emotional_arcs"),
                contract_context=context,
                include_contract_required_keys=False,
            ),
            validator=_validate,
        )
    )

    assert result.success is True
    assert result.source == RepairSource.LOCAL
    assert result.strategy == "plan_outline_fragment_array_sibling_repair"
    assert result.data is not None
    assert set(result.data) == {"character_arcs", "emotional_arcs"}
    assert "emotional_arcs" not in result.data["character_arcs"][0]
    assert result.data["emotional_arcs"][0]["arc_name"] == "沈鹿溪创伤疗愈弧线"


def test_format_error_replay_plan_outline_character_arcs_fixture() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "format_errors"
        / "plan_outline_character_arcs_emotional_arcs.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    context = fixture["contract_context"]

    def _validate(data: dict[str, Any]) -> None:
        apply_task_output_adapter(data, TaskType.PLAN_OUTLINE, context=context)
        validate_json_output_contract(
            TaskType.PLAN_OUTLINE,
            data,
            context=context,
            explicit_required_keys=("character_arcs", "emotional_arcs"),
            include_contract_required_keys=False,
            validate_allowed_keys=True,
            validate_json_schema=True,
        )

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType[fixture["task"]],
                raw_content=fixture["raw_content"],
                error=None,
                required_keys=("character_arcs", "emotional_arcs"),
                contract_context=context,
                include_contract_required_keys=False,
            ),
            validator=_validate,
        )
    )

    assert result.success is fixture["expected"]["success"]
    assert result.strategy == fixture["expected"]["strategy"]
    assert result.data is not None
    assert sorted(result.data) == sorted(fixture["expected"]["top_level_keys"])


def test_task_specific_repair_registry_declares_plan_outline_safe_strategy() -> None:
    strategy = next(
        item
        for item in _TASK_SPECIFIC_REPAIR_STRATEGIES
        if item.name == "plan_outline_fragment_array_sibling_repair"
    )

    assert TaskType.PLAN_OUTLINE in strategy.supported_task_types
    assert strategy.risk == RepairRisk.SAFE
    assert "fragment mode" in strategy.preconditions


def test_orchestrator_preserves_non_empty_init_claim_duplicate_key() -> None:
    raw = _init_claim_response_with_duplicate_field(
        field="evidence",
        first_value="玄慧在朱阙前迎辇时重复摩挲腰间铁符。",
        second_value="",
    )

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
                raw_content=raw,
                error=None,
                required_keys=("claims",),
            ),
            validator=_validate_init_claim_response,
        )
    )

    assert result.success is True
    assert result.source == RepairSource.LOCAL
    assert result.strategy == "init_claim_duplicate_key_preserve_non_empty"
    assert result.data is not None
    assert result.data["claims"][0]["evidence"] == "玄慧在朱阙前迎辇时重复摩挲腰间铁符。"
    assert result.diagnostics["duplicate_key_locations"] == ["claims[0].evidence"]
    assert result.diagnostics["duplicate_key_claim_ids"] == ["outline_1_2_001"]
    assert result.diagnostics["duplicate_key_actions"][0]["action"] == "preserved_non_empty"


def test_orchestrator_rejects_conflicting_init_claim_duplicate_key() -> None:
    raw = _init_claim_response_with_duplicate_field(
        field="evidence",
        first_value="玄慧在朱阙前摩挲铁符。",
        second_value="沈清漪按住袖中残玉。",
    )

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
                raw_content=raw,
                error=None,
                required_keys=("claims",),
            ),
            validator=_validate_init_claim_response,
        )
    )

    assert result.success is False
    assert result.error is not None
    assert "Duplicate key conflict at claims[0].evidence" in str(result.error)


def test_orchestrator_dedupes_identical_init_claim_duplicate_key() -> None:
    raw = _init_claim_response_with_duplicate_field(
        field="evidence",
        first_value="玄慧在朱阙前摩挲铁符。",
        second_value="玄慧在朱阙前摩挲铁符。",
    )

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
                raw_content=raw,
                error=None,
                required_keys=("claims",),
            ),
            validator=lambda data: validate_response_schema(
                data,
                TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
            ),
        )
    )

    assert result.success is True
    assert result.source == RepairSource.LOCAL
    assert result.strategy == "init_claim_duplicate_key_preserve_non_empty"
    assert result.data is not None
    assert result.data["claims"][0]["evidence"] == "玄慧在朱阙前摩挲铁符。"
    assert result.diagnostics["duplicate_key_actions"][0]["action"] == "deduped_identical"


def test_orchestrator_dedupes_identical_character_knowledge_coverage_key() -> None:
    raw = (
        '{"claims":[{'
        '"claim_id":"blueprint_ktp_18_001",'
        '"artifact":"blueprint",'
        '"source_path":"/key_turning_points/3:6",'
        '"subject_ids":["char_e48458705aa0"],'
        '"axis":"identity",'
        '"claim_type":"state",'
        '"claim_text":"身份确认完成",'
        '"state_before":"身份待验证",'
        '"state_after":"身份完成确认",'
        '"evidence":"清漪确认玄玊身份。",'
        '"payoff_id":"",'
        '"payoff_kind":"",'
        '"irreversible":true,'
        '"temporality":"actual",'
        '"cognitive_subjects":["玄玊"],'
        '"cognitive_object":"玄玊身份",'
        '"cognitive_level":"confirmed",'
        '"action_level":"internal",'
        '"reader_awareness":"full",'
        '"character_knowledge_coverage":{"玄玊":"full","玄玊":"full","澔澄":"partial"},'
        '"cognitive_chapter":18,'
        '"public_reveal_chapter":null,'
        '"foreshadow_chapters":[],"confidence":0.9'
        '}],"coverage_status":"complete","unprocessed_source_refs":[],"summary":"ok"}'
    )

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
                raw_content=raw,
                error=None,
                required_keys=("claims",),
            ),
            validator=_validate_init_claim_response,
        )
    )

    assert result.success is True
    assert result.source == RepairSource.LOCAL
    assert result.strategy == "init_claim_duplicate_key_preserve_non_empty"
    assert result.data is not None
    assert result.data["claims"][0]["character_knowledge_coverage"] == {
        "玄玊": "full",
        "澔澄": "partial",
    }
    assert result.diagnostics["duplicate_key_locations"] == [
        "claims[0].character_knowledge_coverage.玄玊"
    ]
    assert result.diagnostics["duplicate_key_actions"][0]["action"] == "deduped_identical"


def test_orchestrator_rejects_conflicting_character_knowledge_coverage_duplicate_key() -> None:
    raw = (
        '{"claims":[{'
        '"claim_id":"blueprint_ktp_18_001",'
        '"artifact":"blueprint",'
        '"source_path":"/key_turning_points/3:6",'
        '"subject_ids":["char_e48458705aa0"],'
        '"axis":"identity",'
        '"claim_type":"state",'
        '"claim_text":"身份确认完成",'
        '"state_before":"身份待验证",'
        '"state_after":"身份完成确认",'
        '"evidence":"清漪确认玄玊身份。",'
        '"payoff_id":"",'
        '"payoff_kind":"",'
        '"irreversible":true,'
        '"temporality":"actual",'
        '"cognitive_subjects":["玄玊"],'
        '"cognitive_object":"玄玊身份",'
        '"cognitive_level":"confirmed",'
        '"action_level":"internal",'
        '"reader_awareness":"full",'
        '"character_knowledge_coverage":{"玄玊":"full","玄玊":"partial"},'
        '"cognitive_chapter":18,'
        '"public_reveal_chapter":null,'
        '"foreshadow_chapters":[],"confidence":0.9'
        '}],"coverage_status":"complete","unprocessed_source_refs":[],"summary":"ok"}'
    )

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
                raw_content=raw,
                error=None,
                required_keys=("claims",),
            ),
            validator=_validate_init_claim_response,
        )
    )

    assert result.success is False
    assert result.error is not None
    assert "Duplicate key conflict at claims[0].character_knowledge_coverage.玄玊" in str(
        result.error
    )


def test_orchestrator_rejects_duplicate_claims_top_level_key() -> None:
    raw = (
        '{"claims":[],'
        '"claims":[{"claim_id":"outline_1_2_001","artifact":"outline",'
        '"source_path":"/chapters/1","claim_text":"玄慧沉默",'
        '"evidence":"玄慧在朱阙前摩挲铁符。",'
        '"claim_type":"state","cognitive_subjects":[],'
        '"cognitive_object":"","cognitive_level":"unaware",'
        '"action_level":"none","reader_awareness":"full",'
        '"character_knowledge_coverage":{},"cognitive_chapter":null,'
        '"public_reveal_chapter":null,"foreshadow_chapters":[]}],'
        '"coverage_status":"complete","unprocessed_source_refs":[],"summary":"ok"}'
    )

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
                raw_content=raw,
                error=None,
                required_keys=("claims",),
            ),
            validator=_validate_init_claim_response,
        )
    )

    assert result.success is False
    assert result.error is not None
    assert "Duplicate key outside supported init claim object at claims" in str(result.error)


def test_orchestrator_rejects_scalar_beats_repair_when_chapter_numbers_are_lost() -> None:
    prompts: list[str] = []
    raw = (
        '{"chapters":[{"chapter_number":73,"title":"水道危局",'
        '"goal":"清漪主持水道收网。",'
        '"beats_summary":"清漪以探亲为掩护再赴建业。",'
        '"影卫截获蠲毒运输船。",'
        '"main_plot_points":["清漪主导水道收网"],'
        '"subplot_points":[],"subplot_focus":"蜀谍线收缩",'
        '"element_focus":[],"pov_character":"沈清漪","pov_switch":false,'
        '"setting":"建业水道","expected_word_count":5000,'
        '"involved_characters":["沈清漪"],"notes":"",'
        '"expected_hook":{"hook_type":"crisis","hook_strength":"strong",'
        '"hook_description":"商漪被威胁。"},'
        '"expected_payoffs":[{"payoff_type":"information","description":"暗线暴露。"}]}]}'
        '{"chapter_number":74,"title":"谍影初收"}'
    )

    def _validate(data: dict[str, Any]) -> None:
        if "chapters" not in data:
            raise KeyError("chapters")

    async def _llm_repair(prompt: str) -> str:
        prompts.append(prompt)
        return '{"chapters":[]}'

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.PLAN_OUTLINE_BATCH,
                raw_content=raw,
                error=None,
                required_keys=("chapters",),
            ),
            validator=_validate,
            llm_repair=_llm_repair,
        )
    )

    assert result.success is True
    assert result.source == RepairSource.LLM
    assert prompts


def test_orchestrator_calls_llm_repair_when_local_strategy_cannot_satisfy_contract() -> None:
    prompts: list[str] = []

    def _validate(data: dict[str, Any]) -> None:
        if "beats" not in data:
            raise KeyError("beats")

    async def _llm_repair(prompt: str) -> str:
        prompts.append(prompt)
        return '{"beats":["开场","转折"]}'

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.BEATS,
                raw_content='{"beatzz":["开场","转折"]}',
                error=KeyError("beats"),
                required_keys=("beats",),
            ),
            validator=_validate,
            llm_repair=_llm_repair,
        )
    )

    assert result.success is True
    assert result.source == RepairSource.LLM
    assert result.data == {"beats": ["开场", "转折"]}
    assert prompts and '"beatzz"' in prompts[0]
    assert "只修复机器可解析格式" in prompts[0]


def test_orchestrator_accepts_local_repair_for_json5_style_init_world_rules() -> None:
    raw = (
        '{world_rules:{era:"当代中国东部沿海二线城市",'
        'geography:"大学城理工楼负一层与老街区知微堂由银杏道相连",'
        'culture:"中西医并行构成信任结构"}}'
    )

    def _validate(data: dict[str, Any]) -> None:
        if "world_rules" not in data:
            raise KeyError("world_rules")

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.INIT_STORY_WORLD_RULES,
                raw_content=raw,
                error=None,
                required_keys=("world_rules",),
            ),
            validator=_validate,
        )
    )

    assert result.success is True
    assert result.source == RepairSource.LOCAL
    assert result.data is not None
    assert result.data["world_rules"]["culture"] == "中西医并行构成信任结构"


def test_orchestrator_repairs_extract_canon_object_key_only_member() -> None:
    raw = (
        '{"canon_delta":{"item_updates":{"暗卫密报":{'
        '"内容":"三行字，极短",'
        '"处置":"放入暗档木匣最底层",'
        '"未向沈清漪透露"}}},'
        '"creative_report":{},'
        '"chapter_exit_state":{},'
        '"character_state_deltas":[],'
        '"relationship_deltas":[],'
        '"plot_thread_deltas":[],'
        '"structured_summary":"ok"}'
    )

    def _validate(data: dict[str, Any]) -> None:
        validate_json_output_contract(
            TaskType.EXTRACT_CANON,
            data,
            validate_json_schema=True,
        )

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.EXTRACT_CANON,
                raw_content=raw,
                error=None,
            ),
            validator=_validate,
        )
    )

    assert result.success is True
    assert result.source == RepairSource.LOCAL
    assert result.strategy == "safe_parse_json"
    assert result.data is not None
    secret_report = result.data["canon_delta"]["item_updates"]["暗卫密报"]
    assert secret_report["未向沈清漪透露"] is True


def test_orchestrator_turns_degenerate_claim_loop_into_empty_claims() -> None:
    raw = '{"claims": [{"claim_text": "' + "60章的" * 900

    def _validate(data: dict[str, Any]) -> None:
        if "claims" not in data:
            raise KeyError("claims")

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
                raw_content=raw,
                error=None,
                required_keys=("claims",),
                finish_reason="length",
            ),
            validator=_validate,
        )
    )

    assert result.success is True
    assert result.source == RepairSource.LOCAL
    assert result.strategy == "degenerate_claims_empty_fallback"
    assert result.data == {
        "claims": [],
        "coverage_status": "uncertain",
        "unprocessed_source_refs": ["model_output_truncated"],
        "summary": "模型 claims 输出重复并截断，按无可用 claims 处理。",
    }


def test_orchestrator_turns_repeated_unicode_claim_loop_into_empty_claims() -> None:
    raw = '{"claims": [{"claim_text": "' + (r"\u5496\u5561\u9986\u5328\u57e0\u4e00" * 80)

    def _validate(data: dict[str, Any]) -> None:
        if "claims" not in data:
            raise KeyError("claims")

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
                raw_content=raw,
                error=None,
                required_keys=("claims",),
                finish_reason="length",
            ),
            validator=_validate,
        )
    )

    assert result.success is True
    assert result.source == RepairSource.LOCAL
    assert result.strategy == "degenerate_claims_empty_fallback"
    assert result.data == {
        "claims": [],
        "coverage_status": "uncertain",
        "unprocessed_source_refs": ["model_output_truncated"],
        "summary": "模型 claims 输出重复并截断，按无可用 claims 处理。",
    }


def test_orchestrator_salvages_candidate_state_deltas_after_misnested_evidence() -> None:
    raw = (
        '{"candidates":['
        '{"candidate_id":"c10_e001","chapter_number":10,"delta_type":"event",'
        '"summary":"证据公开","proposed_delta":{"source":"chapter_text",'
        '"delta_type":"event","summary":"证据公开"},'
        '"evidence":[{"quote":"证据公开。","paragraph_index":1,"context":"很长上下文"}]},'
        '{"candidate_id":"c10_e002","chapter_number":10,"delta_type":"character_state",'
        '"summary":"沈鹤卿短暂清醒","proposed_delta":{"source":"chapter_text",'
        '"delta_type":"character_state","summary":"沈鹤卿短暂清醒",'
        '"evidence":[{"quote":"清醒片刻。","paragraph_index":2}]},'
        '{"candidate_id":"c10_e003","chapter_number":10,"delta_type":"item",'
        '"summary":"金镯被放到桌上","proposed_delta":{"source":"chapter_text",'
        '"delta_type":"item","summary":"金镯被放到桌上"},'
        '"evidence":[{"quote":"金镯被放到桌上。","paragraph_index":3,"context":"冗余"}]}]}'
    )

    def _validate(data: dict[str, Any]) -> None:
        if "candidates" not in data:
            raise KeyError("candidates")
        for candidate in data["candidates"]:
            if not {"delta_type", "summary", "proposed_delta", "evidence"} <= set(candidate):
                raise KeyError("candidate fields")

    result = asyncio.run(
        repair_json_object_response(
            FormatRepairContext(
                task_type=TaskType.EXTRACT_CANDIDATE_STATE_DELTAS,
                raw_content=raw,
                error=None,
                required_keys=("candidates",),
            ),
            validator=_validate,
        )
    )

    assert result.success is True
    assert result.source == RepairSource.LOCAL
    assert result.strategy == "candidate_state_delta_salvage"
    assert result.risk == RepairRisk.LOSSY
    assert result.data is not None
    assert [item["candidate_id"] for item in result.data["candidates"]] == [
        "c10_e001",
        "c10_e003",
    ]
    assert "context" not in result.data["candidates"][0]["evidence"][0]


def test_chapter_contract_structural_loss_is_lossy_when_batch_can_backfill() -> None:
    context = FormatRepairContext(
        task_type=TaskType.PLAN_CHAPTER_CONTRACTS,
        raw_content=(
            '{"chapter_contracts":['
            '{"chapter_number":5,"title":"指尖记忆"},'
            '{"chapter_number":6,"title":"热搜风云"},'
            '{"chapter_number":7,"title":"并肩之约"},'
            '{"chapter_number":8,"title":"前世今生","completion_criteria":["坏段落"'
        ),
        error=None,
        required_keys=("chapter_contracts",),
        finish_reason="stop",
        contract_context={
            "outline": {
                "contract_batch": {"chapter_numbers": [5, 6, 7, 8]},
                "chapters": [
                    {"chapter_number": 5},
                    {"chapter_number": 6},
                    {"chapter_number": 7},
                    {"chapter_number": 8},
                ],
                "contract_scaffold": [
                    {"chapter_number": 5},
                    {"chapter_number": 6},
                    {"chapter_number": 7},
                    {"chapter_number": 8},
                ],
            }
        },
    )
    repaired_prefix = {
        "chapter_contracts": [
            {"chapter_number": 5, "title": "指尖记忆"},
            {"chapter_number": 6, "title": "热搜风云"},
            {"chapter_number": 7, "title": "并肩之约"},
        ]
    }

    assert _classify_local_repair_risk(context=context, data=repaired_prefix) == RepairRisk.LOSSY


def test_chapter_contract_overgeneration_outside_batch_does_not_make_repair_unsafe() -> None:
    context = FormatRepairContext(
        task_type=TaskType.PLAN_CHAPTER_CONTRACTS,
        raw_content=(
            '{"chapter_contracts":['
            '{"chapter_number":34,"title":"暗流涌动"},'
            '{"chapter_number":35,"title":"证据交锋"},'
            '{"chapter_number":36,"title":"钟楼钥匙"},'
            '{"chapter_number":37,"title":"信任危机"},'
            '{"chapter_number":38,"title":"局中反击"}'
        ),
        error=None,
        required_keys=("chapter_contracts",),
        finish_reason="length",
        contract_context={
            "outline": {
                "contract_batch": {"chapter_numbers": [34, 35, 36, 37]},
                "chapters": [
                    {"chapter_number": 34},
                    {"chapter_number": 35},
                    {"chapter_number": 36},
                    {"chapter_number": 37},
                ],
                "contract_scaffold": [
                    {"chapter_number": 34},
                    {"chapter_number": 35},
                    {"chapter_number": 36},
                    {"chapter_number": 37},
                ],
            }
        },
    )
    repaired_batch = {
        "chapter_contracts": [
            {"chapter_number": 34, "title": "暗流涌动"},
            {"chapter_number": 35, "title": "证据交锋"},
            {"chapter_number": 36, "title": "钟楼钥匙"},
            {"chapter_number": 37, "title": "信任危机"},
        ]
    }

    assert _classify_local_repair_risk(context=context, data=repaired_batch) == RepairRisk.LOSSY


def test_chapter_contract_missing_in_batch_stays_unsafe_on_length_repair() -> None:
    context = FormatRepairContext(
        task_type=TaskType.PLAN_CHAPTER_CONTRACTS,
        raw_content=(
            '{"chapter_contracts":['
            '{"chapter_number":34,"title":"暗流涌动"},'
            '{"chapter_number":35,"title":"证据交锋"},'
            '{"chapter_number":36,"title":"钟楼钥匙"},'
            '{"chapter_number":37,"title":"信任危机"}'
        ),
        error=None,
        required_keys=("chapter_contracts",),
        finish_reason="length",
        contract_context={
            "outline": {
                "contract_batch": {"chapter_numbers": [34, 35, 36, 37]},
                "chapters": [
                    {"chapter_number": 34},
                    {"chapter_number": 35},
                    {"chapter_number": 36},
                    {"chapter_number": 37},
                ],
                "contract_scaffold": [
                    {"chapter_number": 34},
                    {"chapter_number": 35},
                    {"chapter_number": 36},
                    {"chapter_number": 37},
                ],
            }
        },
    )
    repaired_prefix = {
        "chapter_contracts": [
            {"chapter_number": 34, "title": "暗流涌动"},
            {"chapter_number": 35, "title": "证据交锋"},
            {"chapter_number": 36, "title": "钟楼钥匙"},
        ]
    }

    assert _classify_local_repair_risk(context=context, data=repaired_prefix) == RepairRisk.UNSAFE
