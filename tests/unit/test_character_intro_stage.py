from __future__ import annotations

import json
from types import SimpleNamespace

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile
from novel_forge.core.schemas.canon import CreativeReport, NewCharacterDetail
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.character_intro_policy import (
    apply_pending_retry_policy,
    load_character_alias_lookup,
    resolve_known_character_name,
    sort_auto_new_names,
)
from novel_forge.pipeline.long.services.generation.llm_helpers import route_json_object_with_retry
from novel_forge.pipeline.long.stages.character_intro import (
    _ENRICH_WRAPPER_KEYS,
    _PROFILE_WRAPPER_KEYS,
    _adjudicate_character_intro_candidates,
    _collect_chapter_character_candidates,
    _is_functional_character_label,
    _limit_auto_new_names,
    _merge_enriched,
    _parse_json_dict_response,
    _select_adjudicated_character_names,
    auto_register_from_creative_report,
)


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def test_outline_involved_characters_are_weak_intro_signals() -> None:
    outline = ChapterOutline(
        chapter_number=2,
        title="暗账",
        goal="推进供应商危机",
        pov_character="沈念卿",
        involved_characters=["沈念卿", "陈助理", "咖啡店店员"],
        expected_word_count=4000,
    )
    bundle = _ns(chapter_outline=outline)

    candidates = _collect_chapter_character_candidates(bundle, None, 2)

    assert candidates == {"沈念卿", "陈助理", "咖啡店店员"}


def test_structured_contract_character_ops_are_strong_intro_signals() -> None:
    outline = ChapterOutline(
        chapter_number=2,
        title="暗账",
        goal="推进供应商危机",
        pov_character="沈念卿",
        involved_characters=["沈念卿", "周芷若", "陈助理"],
        expected_word_count=4000,
    )
    bundle = _ns(chapter_outline=outline)
    contract = {
        "relationship_ops": [
            {
                "source_character": "周芷若",
                "target_character": "沈念卿",
                "relationship_type": "商业对手",
            }
        ],
        "required_events": ["陈助理汇报时提及智云科技"],
    }

    candidates = _collect_chapter_character_candidates(
        bundle,
        None,
        2,
        chapter_contract=contract,
    )

    assert candidates == {"沈念卿", "周芷若", "陈助理"}


def test_legacy_relationship_ops_pair_labels_are_strong_intro_signals() -> None:
    outline = ChapterOutline(
        chapter_number=39,
        title="百年棋局",
        goal="程砚秋支线情感闭环",
        pov_character="程砚秋",
        involved_characters=["程砚秋"],
        expected_word_count=4200,
    )
    bundle = _ns(chapter_outline=outline)
    contract = {
        "relationship_ops": [
            [
                "char_chengyanqiu",
                "char_sumanhua",
                "程砚秋与苏曼华偶遇完成前世追寻情感闭环",
                "程砚秋与苏曼华",
            ],
            ["char_a", "char_b", "周芷若收到密函指令", "操控者与棋子"],
        ],
    }

    candidates = _collect_chapter_character_candidates(
        bundle,
        None,
        39,
        chapter_contract=contract,
    )

    assert candidates == {"程砚秋", "苏曼华", "操控者", "棋子"}


def test_relationship_ops_subject_object_are_strong_intro_signals() -> None:
    outline = ChapterOutline(
        chapter_number=50,
        title="岁月静好的模样",
        goal="全员汇合",
        pov_character="沈念卿",
        involved_characters=["沈念卿"],
        expected_word_count=4200,
    )
    bundle = _ns(chapter_outline=outline)
    contract = {
        "relationship_ops": [
            {
                "subject": "程砚秋",
                "object": "苏曼华",
                "op_type": "pastlife_connection",
                "description": "程砚秋与苏曼华并肩而坐",
            },
            {
                "subject": "沈鹤卿",
                "object": "众人",
                "op_type": "witness_fulfillment",
            },
        ],
    }

    candidates = _collect_chapter_character_candidates(
        bundle,
        None,
        50,
        chapter_contract=contract,
    )

    assert candidates == {"沈念卿", "程砚秋", "苏曼华", "沈鹤卿", "众人"}


def test_non_character_item_ops_do_not_trigger_intro() -> None:
    outline = ChapterOutline(
        chapter_number=47,
        title="名片",
        goal="确认线索",
        pov_character="程砚秋",
        involved_characters=["程砚秋"],
        expected_word_count=4200,
    )
    bundle = _ns(chapter_outline=outline)
    contract = {
        "item_ops": [
            {
                "object": "苏曼华泛黄名片",
                "description": "苏曼华泛黄名片作为证据出现",
            }
        ],
    }

    candidates = _collect_chapter_character_candidates(
        bundle,
        None,
        47,
        chapter_contract=contract,
    )

    assert candidates == {"程砚秋"}


def test_knowledge_ops_do_not_treat_knowledge_labels_as_characters() -> None:
    outline = ChapterOutline(
        chapter_number=38,
        title="破局之棋",
        goal="技术鉴定报告击破侵权指控",
        pov_character="沈念卿",
        involved_characters=["沈念卿"],
        expected_word_count=4200,
    )
    bundle = _ns(chapter_outline=outline)
    contract = {
        "knowledge_ops": [
            ["代码原创性证明", "技术鉴定报告证明代码原创性与时间戳", "程砚秋"],
            {
                "knowledge_name": "苏曼华身份确认",
                "subject": "苏曼华身份确认",
                "description": "这是一条知识项，不是人物名",
            },
        ],
    }

    candidates = _collect_chapter_character_candidates(
        bundle,
        None,
        38,
        chapter_contract=contract,
    )

    assert candidates == {"沈念卿", "程砚秋"}


def test_blueprint_key_characters_are_not_enough_without_chapter_confirmation() -> None:
    outline = ChapterOutline(
        chapter_number=3,
        title="怀表记忆",
        goal="陆云峥找到古董店线索",
        pov_character="陆云峥",
        involved_characters=["陆云峥", "路人店员"],
        expected_word_count=4200,
    )
    bundle = _ns(chapter_outline=outline)
    blueprint = _ns(
        narrative_phases=[
            _ns(chapter_start=1, chapter_end=5, key_characters=["程砚秋"]),
        ],
        key_turning_points=[],
        character_arcs=[],
    )

    candidates = _collect_chapter_character_candidates(bundle, blueprint, 3)

    assert candidates == {"陆云峥", "路人店员", "程砚秋"}


def test_blueprint_and_outline_signals_confirm_intro_candidate() -> None:
    outline = ChapterOutline(
        chapter_number=3,
        title="怀表记忆",
        goal="陆云峥找到古董店线索",
        pov_character="陆云峥",
        involved_characters=["陆云峥", "程砚秋"],
        expected_word_count=4200,
    )
    bundle = _ns(chapter_outline=outline)
    blueprint = _ns(
        narrative_phases=[
            _ns(chapter_start=1, chapter_end=5, key_characters=["程砚秋"]),
        ],
        key_turning_points=[],
        character_arcs=[],
    )

    candidates = _collect_chapter_character_candidates(bundle, blueprint, 3)

    assert candidates == {"陆云峥", "程砚秋"}


def test_descriptive_character_labels_are_deferred_to_llm_adjudication() -> None:
    outline = ChapterOutline(
        chapter_number=24,
        title="链环",
        goal="林绾绾再次遇见神秘摄影师",
        pov_character="林绾绾",
        involved_characters=["林绾绾", "神秘摄影师"],
        expected_word_count=4200,
    )
    bundle = _ns(chapter_outline=outline)
    contract = {
        "relationship_ops": [
            {
                "source_char": "林绾绾",
                "target_char": "神秘摄影师",
                "description": "林绾绾偶遇神秘摄影师",
            }
        ],
    }

    candidates = _collect_chapter_character_candidates(
        bundle,
        None,
        24,
        chapter_contract=contract,
    )

    assert candidates == {"林绾绾", "神秘摄影师"}


def test_adjudication_selects_only_important_new_characters() -> None:
    payload = {
        "decisions": [
            {
                "candidate_name": "神秘摄影师",
                "canonical_name": "顾墨白",
                "verdict": "create_profile",
                "importance": "supporting",
                "should_create_profile": True,
            },
            {
                "candidate_name": "周芷若（狱中）",
                "canonical_name": "周芷若",
                "verdict": "merge_existing",
                "importance": "major",
                "should_create_profile": False,
                "matched_existing_name": "周芷若",
            },
            {
                "candidate_name": "咖啡店店员",
                "canonical_name": "咖啡店店员",
                "verdict": "skip",
                "importance": "incidental",
                "should_create_profile": False,
            },
        ],
        "summary": "只建顾墨白",
    }

    selected = _select_adjudicated_character_names(
        payload,
        candidate_names={"神秘摄影师", "周芷若（狱中）", "咖啡店店员"},
        existing_names={"周芷若"},
    )

    assert selected == ["顾墨白"]


def test_functional_title_is_not_selected_as_character_profile() -> None:
    payload = {
        "decisions": [
            {
                "candidate_name": "黄门侍郎",
                "canonical_name": "黄门侍郎",
                "verdict": "create_profile",
                "importance": "supporting",
                "should_create_profile": True,
            }
        ],
        "summary": "称谓不应直接建档",
    }

    selected = _select_adjudicated_character_names(
        payload,
        candidate_names={"黄门侍郎"},
        existing_names=set(),
    )

    assert selected == []
    assert _is_functional_character_label("黄门侍郎")
    assert not _is_functional_character_label("顾墨白")


class _AdjudicationBuilder:
    def __init__(self) -> None:
        self.task_types = []

    def build(self, task_type, context, **kwargs):
        self.task_types.append(task_type)
        return ModelRequest(task_type=task_type, messages=[{"role": "user", "content": ""}])


class _AdjudicationRouter:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requests: list[ModelRequest] = []

    async def route(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(content=json.dumps(self.payload, ensure_ascii=False))


async def test_adjudication_step_uses_llm_decision_payload(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    outline = ChapterOutline(
        chapter_number=24,
        title="链环",
        goal="林绾绾遇见神秘摄影师",
        pov_character="林绾绾",
        involved_characters=["林绾绾", "神秘摄影师"],
        expected_word_count=4200,
    )
    bundle = _ns(
        layout=ProjectLayout(tmp_path / "demo"),
        chapter_outline=outline,
        character_bible=_ns(
            characters=[
                _ns(name="林绾绾", role="supporting", relationships={}, notes=""),
            ]
        ),
    )
    router = _AdjudicationRouter(
        {
            "decisions": [
                {
                    "candidate_name": "神秘摄影师",
                    "canonical_name": "顾墨白",
                    "verdict": "create_profile",
                    "importance": "supporting",
                    "should_create_profile": True,
                    "confidence": 0.9,
                    "reason": "后续会承担支线关系",
                    "evidence": ["章节契约要求二人再次相遇"],
                }
            ],
            "summary": "顾墨白需要建档",
        }
    )
    builder = _AdjudicationBuilder()

    selected = await _adjudicate_character_intro_candidates(
        router=router,
        builder=builder,
        storage=storage,
        settings=_ns(temp_adjudicate_character_introduction=0.1),
        bundle=bundle,
        blueprint=None,
        chapter_number=24,
        chapter_contract={
            "new_character_candidates": [
                {
                    "name": "神秘摄影师",
                    "canonical_name": "顾墨白",
                    "importance": "supporting",
                    "should_consider_profile": True,
                }
            ]
        },
        candidate_names={"神秘摄影师"},
        candidate_signals={"神秘摄影师": {"chapter_contract", "outline"}},
        existing_names={"林绾绾"},
    )

    assert selected == ["顾墨白"]
    assert len(router.requests) == 1


class _FailingAdjudicationRouter:
    async def route(self, request: ModelRequest) -> ModelResponse:
        raise RuntimeError("model offline")


async def test_failed_adjudication_records_pending_intro(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    outline = ChapterOutline(
        chapter_number=24,
        title="链环",
        goal="林绾绾遇见神秘摄影师",
        pov_character="林绾绾",
        involved_characters=["林绾绾", "神秘摄影师"],
        expected_word_count=4200,
    )
    bundle = _ns(
        layout=ProjectLayout(tmp_path / "demo"),
        chapter_outline=outline,
        character_bible=_ns(
            characters=[
                _ns(name="林绾绾", role="supporting", relationships={}, notes=""),
            ]
        ),
    )

    selected = await _adjudicate_character_intro_candidates(
        router=_FailingAdjudicationRouter(),
        builder=_AdjudicationBuilder(),
        storage=storage,
        settings=_ns(temp_adjudicate_character_introduction=0.1),
        bundle=bundle,
        blueprint=None,
        chapter_number=24,
        chapter_contract={},
        candidate_names={"神秘摄影师"},
        candidate_signals={"神秘摄影师": {"chapter_contract", "outline"}},
        existing_names={"林绾绾"},
    )

    assert selected == []
    pending = storage.load_json(bundle.layout.states_dir / "character_intro_pending.json")
    assert pending["pending"][0]["character"] == "神秘摄影师"
    assert pending["pending"][0]["phase"] == "adjudicate"
    assert pending["pending"][0]["attempts"] == 1


def test_auto_intro_limit_caps_new_names_and_reports_skips() -> None:
    events: list[tuple[str, dict]] = []

    kept = _limit_auto_new_names(
        ["甲", "乙", "丙"],
        limit=2,
        chapter_number=4,
        on_step=lambda name, payload: events.append((name, payload)),
        event_name="introduce_character",
    )

    assert kept == ["甲", "乙"]
    assert events[0][0] == "introduce_character_limited"
    assert events[0][1]["skipped"] == ["丙"]


def test_auto_intro_limit_zero_disables_new_names() -> None:
    kept = _limit_auto_new_names(
        ["甲"],
        limit=0,
        chapter_number=4,
        on_step=None,
        event_name="introduce_character",
    )

    assert kept == []


def test_intro_response_parser_accepts_profile_wrapper_and_markdown_fence() -> None:
    payload = """```json
{"character_profile":{"name":"新角色","role":"minor","appearance":"素衣","personality":"沉稳","backstory":"来自旧巷"}}
```"""

    parsed = _parse_json_dict_response(payload, wrapper_keys=_PROFILE_WRAPPER_KEYS)

    assert parsed == {
        "name": "新角色",
        "role": "minor",
        "appearance": "素衣",
        "personality": "沉稳",
        "backstory": "来自旧巷",
    }


def test_enrich_response_parser_accepts_enrichment_wrapper() -> None:
    parsed = _parse_json_dict_response(
        '{"enriched_profile":{"appearance":"白衬衫","personality":"谨慎","backstory":"旧案相关","arc":""}}',
        wrapper_keys=_ENRICH_WRAPPER_KEYS,
    )

    assert parsed == {
        "appearance": "白衬衫",
        "personality": "谨慎",
        "backstory": "旧案相关",
        "arc": "",
    }


class _SequenceRouter:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.requests: list[ModelRequest] = []

    async def route(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.payloads:
            raise AssertionError("unexpected router call")
        return ModelResponse(content=json.dumps(self.payloads.pop(0), ensure_ascii=False))


async def test_route_helper_unwraps_adapts_validates_and_retries() -> None:
    router = _SequenceRouter(
        [
            {
                "character_profile": {
                    "name": "顾墨白",
                    "role": "supporting",
                    "social_status": "摄影师",
                    "abilities": ["暗房技术"],
                    "appearance": "黑衣高个",
                    "personality": "谨慎",
                    "backstory": "旧案相关",
                    "arc": "揭开旧案",
                    "relationships": {},
                    "voice": "短句",
                    "notes": "支线线索",
                }
            },
            {
                "character_profile": {
                    "name": "顾墨白",
                    "role": "supporting",
                    "gender": "男",
                    "social_status": "摄影师",
                    "abilities": ["暗房技术"],
                    "appearance": "黑衣高个",
                    "personality": "谨慎",
                    "backstory": "旧案相关",
                    "arc": "揭开旧案",
                    "relationships": {},
                    "voice": "短句",
                    "notes": "支线线索",
                }
            },
        ]
    )
    request = ModelRequest(
        task_type=TaskType.INTRODUCE_CHARACTER,
        messages=[{"role": "user", "content": ""}],
        temperature=0.8,
    )

    parsed = await route_json_object_with_retry(
        router,
        request,
        task_type=TaskType.INTRODUCE_CHARACTER,
        wrapper_keys=_PROFILE_WRAPPER_KEYS,
        retry_temperature=0.0,
    )

    assert parsed["gender"] == "男"
    assert isinstance(parsed["abilities"], str)
    assert "暗房技术" in parsed["abilities"]
    assert len(router.requests) == 2
    assert router.requests[1].temperature == 0.0
    assert router.requests[1].temperature_jitter_allowed is False


def test_pending_retry_policy_only_retries_current_mentions_and_caps_attempts() -> None:
    pending = {
        "pending": [
            {"character": "顾墨白", "attempts": 1},
            {"character": "旧路人", "attempts": 1},
            {"character": "失败角色", "attempts": 2},
        ]
    }
    signals, exhausted = apply_pending_retry_policy(
        {
            "顾墨白": {"chapter_contract"},
            "失败角色": {"chapter_contract"},
            "新角色": {"outline"},
        },
        pending,
        max_attempts=2,
    )

    assert signals["顾墨白"] == {"chapter_contract", "pending_retry"}
    assert "旧路人" not in signals
    assert "失败角色" not in signals
    assert exhausted == ["失败角色"]


def test_merge_enriched_rejects_placeholders_and_updates_empty_stable_fields() -> None:
    profile = CharacterProfile(
        name="顾墨白",
        role="supporting",
        gender="",
        social_status="",
        abilities="",
        appearance="黑衣高个，沉默寡言。",
        personality="谨慎，善于观察。",
        backstory="旧案相关。",
        arc="协助主角追查旧案。",
        relationships={"林绾绾": "线索提供者"},
    )

    updated = _merge_enriched(
        profile,
        {
            "appearance": "未知",
            "personality": "普通",
            "backstory": "待定",
            "arc": "无",
            "voice": "短句多，常以反问确认细节。",
            "gender": "男",
            "social_status": "自由摄影师",
            "abilities": "暗房冲洗和跟踪拍摄",
            "relationships": {"顾墨白": "自己", "陌生人": "同伙"},
        },
        known_names={"顾墨白", "林绾绾"},
    )

    assert updated.appearance == profile.appearance
    assert updated.personality == profile.personality
    assert updated.backstory == profile.backstory
    assert updated.arc == profile.arc
    assert updated.voice == "短句多，常以反问确认细节。"
    assert updated.gender == "男"
    assert updated.social_status == "自由摄影师"
    assert updated.abilities == "暗房冲洗和跟踪拍摄"
    assert "陌生人" not in updated.relationships
    assert "关系审计" in updated.notes


def test_priority_sort_feeds_limit_before_list_order() -> None:
    ordered = sort_auto_new_names(
        ["路人甲", "关键证人", "阶段角色"],
        signals={
            "路人甲": {"outline"},
            "关键证人": {"chapter_contract"},
            "阶段角色": {"phase"},
        },
    )
    kept = _limit_auto_new_names(
        ordered,
        limit=2,
        chapter_number=4,
        on_step=None,
        event_name="introduce_character",
    )

    assert kept == ["关键证人", "阶段角色"]


def test_alias_lookup_from_entity_registry_resolves_candidate(tmp_path) -> None:
    root = tmp_path / "demo"
    registry_dir = root / "narrative_state"
    registry_dir.mkdir(parents=True)
    (registry_dir / "entity_registry.json").write_text(
        json.dumps(
            {
                "entities": [
                    {
                        "entity_id": "char_zhou",
                        "name": "周芷若",
                        "entity_type": "character",
                        "aliases": ["三小姐"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    aliases = load_character_alias_lookup(root)

    assert resolve_known_character_name("三小姐", {"周芷若"}, alias_lookup=aliases) == "周芷若"


class _Trace:
    class _Step:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def step(self, _name: str):
        return self._Step()


async def test_auto_register_minor_without_carry_forward_is_not_adjudicated(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "demo")
    bundle = _ns(
        layout=layout,
        chapter_outline=ChapterOutline(
            chapter_number=3,
            title="暗房",
            goal="发现照片",
            pov_character="林绾绾",
            involved_characters=["林绾绾"],
            expected_word_count=4200,
        ),
        character_bible=CharacterBible(
            characters=[CharacterProfile(name="林绾绾", role="protagonist")]
        ),
    )
    creative_report = CreativeReport(
        new_characters=[
            NewCharacterDetail(
                name="路人店员",
                first_appearance_chapter=3,
                importance="minor",
                should_add_to_bible=True,
            )
        ]
    )
    router = _SequenceRouter([])

    registered = await auto_register_from_creative_report(
        router=router,
        builder=_AdjudicationBuilder(),
        storage=storage,
        settings=_ns(temp_adjudicate_character_introduction=0.1, temp_enrich_character=0.7),
        bundle=bundle,
        chapter_number=3,
        chapter_text="路人店员递来照片。",
        creative_report=creative_report,
        trace=_Trace(),
    )

    assert registered == []
    assert router.requests == []


async def test_auto_register_skips_title_only_candidate_without_adjudication(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "demo")
    bundle = _ns(
        layout=layout,
        chapter_outline=ChapterOutline(
            chapter_number=3,
            title="朝堂风声",
            goal="递出礼部消息",
            pov_character="沈清漪",
            involved_characters=["沈清漪"],
            expected_word_count=4200,
        ),
        character_bible=CharacterBible(
            characters=[CharacterProfile(name="沈清漪", role="protagonist")]
        ),
    )
    creative_report = CreativeReport(
        new_characters=[
            NewCharacterDetail(
                name="黄门侍郎",
                first_appearance_chapter=3,
                role_in_story="supporting",
                importance="supporting",
                should_add_to_bible=True,
            )
        ]
    )
    router = _SequenceRouter([])
    events: list[tuple[str, dict]] = []

    registered = await auto_register_from_creative_report(
        router=router,
        builder=_AdjudicationBuilder(),
        storage=storage,
        settings=_ns(temp_adjudicate_character_introduction=0.1, temp_enrich_character=0.7),
        bundle=bundle,
        chapter_number=3,
        chapter_text="黄门侍郎垂手传话，随即退到廊下。",
        creative_report=creative_report,
        trace=_Trace(),
        on_step=lambda name, payload: events.append((name, payload)),
    )

    assert registered == []
    assert router.requests == []
    assert any(name == "auto_register_character_label_deferred" for name, _ in events)


async def test_auto_register_uses_adjudication_and_syncs_relationships(
    tmp_path, monkeypatch
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "demo")
    bundle = _ns(
        layout=layout,
        project_id="",
        chapter_outline=ChapterOutline(
            chapter_number=3,
            title="暗房",
            goal="发现照片",
            pov_character="林绾绾",
            involved_characters=["林绾绾"],
            expected_word_count=4200,
        ),
        character_bible=CharacterBible(
            characters=[CharacterProfile(name="林绾绾", role="protagonist")]
        ),
    )
    creative_report = CreativeReport(
        new_characters=[
            NewCharacterDetail(
                name="顾墨白",
                first_appearance_chapter=3,
                role_in_story="supporting",
                importance="minor",
                should_add_to_bible=True,
            )
        ],
        must_carry_forward=["顾墨白将在下一章交出底片。"],
    )
    router = _SequenceRouter(
        [
            {
                "decisions": [
                    {
                        "candidate_name": "顾墨白",
                        "canonical_name": "顾墨白",
                        "verdict": "create_profile",
                        "importance": "supporting",
                        "should_create_profile": True,
                        "confidence": 0.9,
                        "reason": "后续承接底片线索",
                        "evidence": ["must_carry_forward"],
                    }
                ],
                "summary": "建档",
            },
            {
                "appearance": "黑衣高个，随身带相机。",
                "personality": "谨慎，重视证据。",
                "backstory": "曾追查旧案。",
                "arc": "提供底片推进旧案。",
                "relationships": {"林绾绾": "线索提供者"},
                "voice": "短句多。",
                "gender": "男",
                "social_status": "自由摄影师",
                "abilities": "暗房冲洗",
            },
        ]
    )
    sync_calls: list[str] = []

    async def _fake_sync(**_kwargs):
        sync_calls.append("sync")
        return 0

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.character_intro._sync_character_relationships_to_canon",
        _fake_sync,
    )

    registered = await auto_register_from_creative_report(
        router=router,
        builder=_AdjudicationBuilder(),
        storage=storage,
        settings=_ns(temp_adjudicate_character_introduction=0.1, temp_enrich_character=0.7),
        bundle=bundle,
        chapter_number=3,
        chapter_text="顾墨白把底片交给林绾绾。",
        creative_report=creative_report,
        trace=_Trace(),
    )

    assert registered == ["顾墨白"]
    assert sync_calls == ["sync"]
    saved = storage.load_json(layout.characters_path)
    saved_profile = saved["characters"][1]
    assert saved_profile["gender"] == "男"
    assert saved_profile["relationships"] == {"林绾绾": "线索提供者"}


async def test_auto_register_skips_creative_report_false_positive_after_adjudication(
    tmp_path,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "demo")
    bundle = _ns(
        layout=layout,
        chapter_outline=ChapterOutline(
            chapter_number=3,
            title="玉牌",
            goal="发现玉牌",
            pov_character="林绾绾",
            involved_characters=["林绾绾"],
            expected_word_count=4200,
        ),
        character_bible=CharacterBible(
            characters=[CharacterProfile(name="林绾绾", role="protagonist")]
        ),
    )
    creative_report = CreativeReport(
        new_characters=[
            NewCharacterDetail(
                name="青玉令",
                first_appearance_chapter=3,
                role_in_story="minor",
                importance="supporting",
                should_add_to_bible=True,
            )
        ]
    )
    router = _SequenceRouter(
        [
            {
                "decisions": [
                    {
                        "candidate_name": "青玉令",
                        "canonical_name": "青玉令",
                        "verdict": "skip",
                        "importance": "incidental",
                        "should_create_profile": False,
                        "confidence": 0.95,
                        "reason": "这是道具不是人物",
                        "evidence": ["creative_report"],
                    }
                ],
                "summary": "跳过道具",
            }
        ]
    )

    registered = await auto_register_from_creative_report(
        router=router,
        builder=_AdjudicationBuilder(),
        storage=storage,
        settings=_ns(temp_adjudicate_character_introduction=0.1, temp_enrich_character=0.7),
        bundle=bundle,
        chapter_number=3,
        chapter_text="青玉令落在桌上。",
        creative_report=creative_report,
        trace=_Trace(),
    )

    assert registered == []
    assert len(router.requests) == 1
    assert not storage.exists(layout.characters_path)
