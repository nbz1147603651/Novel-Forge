from __future__ import annotations

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.domain.character_boundary import canonical_character_names
from novel_forge.core.domain.entity_references import EntityReferenceIndex
from novel_forge.core.schemas.outline import (
    ChapterCastPlan,
    ChapterDesignMatrixEntry,
    ChapterEmotionalPlan,
    ChapterOutline,
    NarrativeBlueprint,
    StoryOutline,
)
from novel_forge.narrative_state.schemas import EntityRecord, EntityRegistry
from novel_forge.pipeline.long.services.init.init_outline_batch import (
    _check_batch_quality,
    _normalize_outline_character_fields,
    _outline_entity_audit,
    _scoped_design_matrix_payload,
    build_chapter_design_matrix,
    reconcile_outline_with_chapter_design_matrix,
)
from novel_forge.prompts.builder import PromptBuilder


def _chapter(
    number: int,
    *,
    title: str,
    goal: str,
    main_plot_points: list[str],
    beats_summary: list[str],
    expected_word_count: int = 5000,
    setting: str = "魏宫偏殿",
    pov_character: str = "沈清漪",
    involved_characters: list[str] | None = None,
) -> ChapterOutline:
    return ChapterOutline(
        chapter_number=number,
        title=title,
        goal=goal,
        beats_summary=beats_summary,
        main_plot_points=main_plot_points,
        subplot_points=[],
        subplot_focus="",
        element_focus=[],
        pov_character=pov_character,
        pov_switch=False,
        setting=setting,
        expected_word_count=expected_word_count,
        involved_characters=involved_characters or [pov_character],
        notes="",
        expected_hook={
            "hook_type": "mystery",
            "hook_strength": "medium",
            "hook_description": "门外传来与青阳旧约相同的暗号。",
        },
        expected_payoffs=[
            {
                "payoff_type": "information",
                "description": "清漪确认杯沿银粉并非寻常礼制。",
            }
        ],
    )


def test_outline_batch_quality_flags_project_a_placeholder_shape() -> None:
    chapters = [
        _chapter(
            1,
            title="章名1",
            goal="第1章推进故事主线",
            main_plot_points=["主线推进1"],
            beats_summary=["节拍1-A", "节拍1-B", "节拍1-C"],
            expected_word_count=900,
            setting="小镇",
            pov_character="林远",
            involved_characters=["林远"],
        ),
        _chapter(
            2,
            title="章名2",
            goal="第2章推进故事主线",
            main_plot_points=["主线推进2"],
            beats_summary=["节拍2-A", "节拍2-B", "节拍2-C"],
            expected_word_count=900,
            setting="小镇",
            pov_character="林远",
            involved_characters=["林远"],
        ),
    ]
    chapters[0].expected_hook.hook_description = "主线推进1"
    chapters[0].expected_payoffs[0].description = "主线推进1"
    chapters[1].expected_hook.hook_description = "主线推进2"
    chapters[1].expected_payoffs[0].description = "主线推进2"

    issues = _check_batch_quality(
        chapters=chapters,
        blueprint=NarrativeBlueprint(),
        prev_chapters=[],
    )

    critical_fields = {
        str(issue["field"]) for issue in issues if issue.get("severity") == "critical"
    }
    assert {
        "title",
        "goal",
        "main_plot_points",
        "beats_summary",
        "expected_hook",
        "expected_payoffs",
        "batch",
    }.issubset(critical_fields)


def test_outline_batch_quality_allows_specific_actionable_chapters() -> None:
    chapters = [
        _chapter(
            1,
            title="杯底藏锋",
            goal="清漪在合卺礼中识别杯沿银粉，意识到玄昱借礼制试探她的旧身份。",
            main_plot_points=[
                "清漪检查合卺杯沿，发现银粉只落在最易触碰的位置。",
                "玄昱以青阳旧句试探她，双方都保留真实判断。",
            ],
            beats_summary=[
                "宫宴礼声压住偏殿内的低声交锋。",
                "清漪借转杯动作确认杯沿异样。",
                "玄昱抛出青阳旧句观察她的反应。",
                "清漪选择装作不懂，把怀疑留到离席后验证。",
            ],
        ),
        _chapter(
            2,
            title="水阁暗讯",
            goal="商漪在水阁递出婚约已成的密讯，清漪追踪失败但确认蜀线已入局。",
            main_plot_points=[
                "商漪借换香把婚约消息递给蜀国暗线。",
                "清漪追到水阁只截获半枚蜡封，确认宫中另有信源。",
            ],
            beats_summary=[
                "清漪离席后发现香灰排列异常。",
                "商漪避开宫灯，把密讯藏入水阁茶盘。",
                "清漪追踪到水阁时只剩被毁蜡封。",
                "她把蜡封残纹与青阳旧案联系起来。",
            ],
            setting="邺城水阁",
            involved_characters=["沈清漪", "商漪"],
        ),
    ]

    issues = _check_batch_quality(
        chapters=chapters,
        blueprint=NarrativeBlueprint(),
        prev_chapters=[],
    )

    assert not [issue for issue in issues if issue.get("severity") == "critical"]


def test_outline_batch_quality_rejects_conflicting_total_durations() -> None:
    chapter = _chapter(
        2,
        title="受审五日",
        goal="沈昭在停职五日受审仪轨下退出晨钟验方一线。",
        main_plot_points=["停职五日受审仪轨正式执行。"],
        beats_summary=[
            "停职首日核对签押。",
            "停职第三日复验药引。",
            "沈昭留下书面证据。",
            "审讯暂停等待复核。",
        ],
    )
    chapter.expected_hook.hook_description = "她必须在三日停职结束前决定复验顺序。"

    issues = _check_batch_quality(
        chapters=[chapter],
        blueprint=NarrativeBlueprint(),
        prev_chapters=[],
    )

    duration_issue = next(issue for issue in issues if issue["field"] == "duration_consistency")
    assert duration_issue["severity"] == "critical"
    assert "3日 / 5日" in duration_issue["message"]
    assert "第 N 日" in duration_issue["message"]


def test_canonical_character_boundary_uses_registered_profile_names_only() -> None:
    character_bible = {
        "characters": [
            {
                "name": "玄昱",
                "relationships": {"玄苍": "仅在关系说明中提及，未登记为角色档案。"},
            },
            {"name": "沈清漪"},
            {"name": "鬼蛹"},
        ]
    }

    assert list(canonical_character_names(character_bible)) == ["玄昱", "沈清漪", "鬼蛹"]

    issues = _outline_entity_audit(
        [
            _chapter(
                75,
                title="铁面寒纹",
                goal="鬼蛹递出证据，玄昱与沈清漪继续审证。",
                main_plot_points=["鬼蛹送出证据。", "玄昱与沈清漪压住清洗冲动。"],
                beats_summary=[
                    "鬼蛹留下铁面内衬拓粉。",
                    "玄昱把证据送入审册。",
                    "沈清漪继续核对舱单。",
                    "旧南廊危机被暂时压住。",
                ],
                pov_character="鬼蛹",
                involved_characters=["鬼蛹", "玄苍"],
            )
        ],
        character_bible,
    )

    assert issues["ok"] is False
    assert issues["issues"][0]["observed_name"] == "玄苍"


def test_normalize_outline_character_fields_keeps_only_whitelist_names() -> None:
    chapter = _chapter(
        75,
        title="铁面寒纹",
        goal="鬼蛹递出证据，沈清漪与玄昱继续审证。",
        main_plot_points=["鬼蛹送出证据。", "沈清漪与玄昱压住清洗冲动。"],
        beats_summary=[
            "鬼蛹留下铁面内衬拓粉。",
            "沈清漪与玄昱把证据送入审册。",
            "旧南廊危机被暂时压住。",
            "公孙烈继续催动余下影卫。",
        ],
        pov_character="沈清漪、鬼蛹",
        involved_characters=["沈清漪、鬼蛹", "玄苍", "公孙烈"],
    )

    normalized = _normalize_outline_character_fields(
        chapter,
        character_whitelist=["沈清漪", "鬼蛹", "公孙烈"],
    )

    assert normalized.pov_character == "沈清漪"
    assert normalized.involved_characters == ["沈清漪", "鬼蛹", "公孙烈"]


def test_normalize_outline_character_fields_replaces_stale_ids_even_when_names_match() -> None:
    chapter = _chapter(
        6,
        title="预览章",
        goal="林远进入学院。",
        main_plot_points=["林远察觉异常。"],
        beats_summary=["林远抵达。"],
        pov_character="林远",
        involved_characters=["林远"],
    ).model_copy(
        update={
            "pov_character_id": "char_林远",
            "involved_character_ids": ["char_林远"],
        }
    )
    catalog = [
        {
            "entity_id": "char_lin_yuan",
            "name": "林远",
            "entity_type": "character",
            "aliases": [],
        }
    ]

    normalized = _normalize_outline_character_fields(
        chapter,
        character_whitelist=["林远"],
        entity_catalog=catalog,
        design_entry=None,
    )

    assert normalized.pov_character_id == "char_lin_yuan"
    assert normalized.involved_character_ids == ["char_lin_yuan"]
    assert normalized.cast_plan.pov_entity_id == "char_lin_yuan"


def test_chapter_design_matrix_uses_registry_character_ids_not_free_names() -> None:
    registry = EntityRegistry(
        entities=[
            EntityRecord(entity_id="char_xuanyu", name="玄昱", entity_type="character"),
            EntityRecord(entity_id="char_qingyi", name="沈清漪", entity_type="character"),
            EntityRecord(entity_id="loc_weigong", name="魏宫", entity_type="location"),
        ]
    )
    blueprint = NarrativeBlueprint.model_validate(
        {
            "synopsis": "玄苍旧名被误传，玄昱与沈清漪在魏宫审证。",
            "narrative_phases": [
                {
                    "phase_name": "审证",
                    "chapter_start": 1,
                    "chapter_end": 2,
                    "description": "玄昱与沈清漪追查魏宫旧案。",
                    "key_events": ["沈清漪发现玄苍旧名只是误传。"],
                    "key_characters": ["玄昱", "沈清漪"],
                    "primary_locations": ["魏宫"],
                }
            ],
            "key_turning_points": [
                {
                    "chapter_number": 1,
                    "description": "沈清漪确认旧名误导。",
                    "characters_involved": ["沈清漪"],
                }
            ],
        }
    )

    matrix = build_chapter_design_matrix(
        blueprint=blueprint,
        total_chapters=2,
        entity_registry=registry,
        character_bible={"characters": [{"name": "玄昱"}, {"name": "沈清漪"}]},
    )

    first = matrix.by_chapter()[1]
    assert first.cast_plan.pov_entity_id == "char_qingyi"
    assert "char_qingyi" in first.cast_plan.required_character_ids
    assert first.emotional_brief.subject_entity_id == "char_qingyi"
    assert "沈清漪确认旧名误导。" in first.emotional_brief.pressure_evidence
    assert "带着上一节点的未解压力" not in str(first.model_dump(mode="json"))
    assert "emotional_plan" not in first.model_dump(mode="json")
    assert "玄苍" not in str(first.model_dump(mode="json"))

    scoped = _scoped_design_matrix_payload(matrix, batch_start=1, batch_end=1)
    assert scoped is not None
    scoped_chapter = scoped["chapters"][0]
    assert "emotional_brief" in scoped_chapter
    assert "emotional_plan" not in scoped_chapter
    assert "schema_version" not in scoped_chapter["cast_plan"]
    assert "created_at" not in scoped_chapter["emotional_brief"]


def test_reconcile_cached_outline_replaces_stale_registry_ids() -> None:
    registry = EntityRegistry(
        entities=[
            EntityRecord(entity_id="char_lead", name="林安", entity_type="character"),
            EntityRecord(entity_id="loc_current", name="新港仓库", entity_type="location"),
        ]
    )
    blueprint = NarrativeBlueprint.model_validate(
        {
            "synopsis": "林安在新港仓库查找失踪账本。",
            "narrative_phases": [
                {
                    "phase_name": "追踪",
                    "chapter_start": 1,
                    "chapter_end": 1,
                    "description": "林安进入新港仓库。",
                    "key_characters": ["林安"],
                    "primary_locations": ["新港仓库"],
                }
            ],
        }
    )
    stale_chapter = _chapter(
        1,
        title="仓门之后",
        goal="林安进入新港仓库核对账本。",
        main_plot_points=["林安发现账本缺页。"],
        beats_summary=["林安抵达新港仓库。"],
        setting="新港仓库",
        pov_character="林安",
        involved_characters=["林安"],
    ).model_copy(
        update={
            "pov_character_id": "char_lead",
            "involved_character_ids": ["char_lead", "loc_stale"],
            "cast_plan": ChapterCastPlan(
                pov_entity_id="char_lead",
                required_character_ids=["char_lead"],
                mention_only_entity_ids=["loc_stale"],
            ),
        }
    )
    outline = StoryOutline(total_chapters=1, chapters=[stale_chapter])

    reconciled, matrix, changed = reconcile_outline_with_chapter_design_matrix(
        outline=outline,
        blueprint=blueprint,
        entity_registry=registry,
        character_bible={"characters": [{"name": "林安", "character_id": "char_lead"}]},
    )

    assert changed == [1]
    assert matrix.by_chapter()[1].cast_plan.mention_only_entity_ids == ["loc_current"]
    assert reconciled.chapters[0].cast_plan.mention_only_entity_ids == ["loc_current"]
    assert "loc_stale" not in str(reconciled.model_dump(mode="json"))


def test_normalize_outline_character_fields_applies_design_matrix_ids() -> None:
    registry = EntityRegistry(
        entities=[
            EntityRecord(entity_id="char_xuanyu", name="玄昱", entity_type="character"),
            EntityRecord(entity_id="char_qingyi", name="沈清漪", entity_type="character"),
            EntityRecord(entity_id="char_guiyong", name="鬼蛹", entity_type="character"),
        ]
    )
    matrix = build_chapter_design_matrix(
        blueprint=NarrativeBlueprint.model_validate(
            {
                "narrative_phases": [
                    {
                        "phase_name": "铁面",
                        "chapter_start": 75,
                        "chapter_end": 75,
                        "description": "沈清漪与鬼蛹递交铁面证据。",
                        "key_characters": ["沈清漪", "鬼蛹"],
                    }
                ]
            }
        ),
        total_chapters=75,
        entity_registry=registry,
        character_bible={"characters": [{"name": "玄昱"}, {"name": "沈清漪"}, {"name": "鬼蛹"}]},
    )
    chapter = _chapter(
        75,
        title="铁面寒纹",
        goal="鬼蛹递出证据，沈清漪继续审证。",
        main_plot_points=["鬼蛹送出证据。", "沈清漪压住清洗冲动。"],
        beats_summary=[
            "鬼蛹留下铁面内衬拓粉。",
            "沈清漪把证据送入审册。",
            "旧南廊危机被暂时压住。",
            "公孙烈继续催动余下影卫。",
        ],
        pov_character="沈清漪、鬼蛹",
        involved_characters=["沈清漪、鬼蛹", "玄苍"],
    )

    normalized = _normalize_outline_character_fields(
        chapter,
        character_whitelist=["玄昱", "沈清漪", "鬼蛹"],
        entity_catalog=matrix.entity_catalog,
        design_entry=matrix.by_chapter()[75],
    )

    assert normalized.pov_character_id == "char_qingyi"
    assert normalized.pov_character == "沈清漪"
    assert "char_qingyi" in normalized.involved_character_ids
    assert "玄苍" not in normalized.involved_characters
    assert normalized.emotional_plan.entry_state == ""
    assert normalized.emotional_plan.pressure_source == ""


def test_normalize_outline_character_fields_prunes_unevidenced_support() -> None:
    catalog = [
        {"entity_id": "char_shen", "name": "沈岸", "entity_type": "character"},
        {"entity_id": "char_lin", "name": "林小满", "entity_type": "character"},
        {"entity_id": "char_chen", "name": "陈半仙", "entity_type": "character"},
    ]
    design = ChapterDesignMatrixEntry(
        chapter_number=2,
        cast_plan=ChapterCastPlan(
            pov_entity_id="char_shen",
            required_character_ids=["char_shen"],
            support_character_ids=["char_lin", "char_chen"],
        ),
    )
    chapter = _chapter(
        2,
        title="入梦",
        goal="沈岸进入梦境，林小满负责监测神经信号。",
        main_plot_points=["沈岸决定继续深入。"],
        beats_summary=[
            "林小满发现读数异常。",
            "沈岸触碰记忆碎片。",
            "梦境边缘开始崩塌。",
            "沈岸在倒计时结束前醒来。",
        ],
        pov_character="沈岸",
        involved_characters=["沈岸", "林小满", "陈半仙"],
    ).model_copy(
        update={
            "involved_character_ids": ["char_shen", "char_lin", "char_chen"],
            "required_character_ids": ["char_shen"],
            "support_character_ids": ["char_lin", "char_chen"],
            "cast_plan": design.cast_plan,
        }
    )

    normalized = _normalize_outline_character_fields(
        chapter,
        character_whitelist=["沈岸", "林小满", "陈半仙"],
        entity_catalog=catalog,
        design_entry=design,
    )

    assert normalized.involved_character_ids == ["char_shen", "char_lin"]
    assert normalized.involved_characters == ["沈岸", "林小满"]
    assert normalized.support_character_ids == ["char_lin"]
    assert normalized.cast_plan.support_character_ids == ["char_lin"]


def _mixed_entity_catalog() -> list[dict[str, object]]:
    return [
        {"entity_id": "char_shen", "name": "沈岸", "entity_type": "character"},
        {"entity_id": "char_worker", "name": "糖水铺帮工", "entity_type": "character"},
        {"entity_id": "char_sender", "name": "匿名信投递人", "entity_type": "character"},
        {"entity_id": "char_su", "name": "苏晚", "entity_type": "character"},
        {"entity_id": "loc_shop", "name": "糖水铺", "entity_type": "location"},
        {"entity_id": "item_letter", "name": "匿名信", "entity_type": "item"},
    ]


def test_entity_reference_index_requires_exact_unambiguous_typed_matches() -> None:
    index = EntityReferenceIndex(
        [
            *_mixed_entity_catalog(),
            {"entity_id": "char_a", "name": "甲", "entity_type": "character", "aliases": ["师父"]},
            {"entity_id": "char_b", "name": "乙", "entity_type": "character", "aliases": ["师父"]},
        ]
    )
    assert index.resolve("糖水铺帮工", entity_type="character") == "char_worker"
    assert index.resolve("匿名信投递人", entity_type="character") == "char_sender"
    assert index.resolve("糖水铺", entity_type="character") is None
    assert index.resolve("loc_shop", entity_type="character") is None
    assert index.resolve("糖水铺") == "loc_shop"
    assert index.resolve("糖水铺帮工递出匿名信", entity_type="character") is None
    assert index.resolve("师父", entity_type="character") is None


@pytest.mark.parametrize("locale", ["zh", "en"])
@pytest.mark.parametrize("task", [TaskType.PLAN_OUTLINE_BATCH, TaskType.PLAN_OUTLINE_CONTINUE])
def test_preview_prompts_render_identity_catalog_without_design_matrix(
    locale: str, task: TaskType
) -> None:
    request = PromptBuilder().build(
        task,
        {
            "batch_start": 7,
            "batch_end": 9,
            "words_per_chapter": 3000,
            "spec": {
                "genre": "mystery",
                "theme": "信任",
                "tone": "suspenseful",
                "language": locale,
                "length_target": 180000,
            },
            "blueprint": NarrativeBlueprint(),
            "beats": None,
            "style_profile": None,
            "blueprint_element_selection": None,
            "output_language": locale,
            "prompt_locale": locale,
            "chapter_design_matrix": None,
            "outline_entity_catalog": _mixed_entity_catalog(),
            "phase_guidance": None,
            "phase_rhythm_guidance": None,
            "is_final_batch": False,
            "previous_chapters": None,
            "outline_tracker_context": None,
            "creative_director_packet": None,
        },
    )
    prompt = request.messages[1]["content"]
    assert prompt.count("### Entity Catalog") == 1
    assert "char_worker" in prompt and "loc_shop" in prompt
    assert "entity_type=character" in prompt


def _preview_with_entity_names() -> ChapterOutline:
    return _chapter(
        10,
        title="旧信",
        goal="沈岸与糖水铺帮工、匿名信投递人核对账本，回忆苏晚。",
        main_plot_points=["糖水铺帮工交出账本。"],
        beats_summary=["匿名信投递人核对笔迹。"],
        pov_character="沈岸",
        involved_characters=["沈岸", "糖水铺帮工", "匿名信投递人"],
    ).model_copy(update={"emotional_plan": ChapterEmotionalPlan(subject_entity_id="沈岸")})


def test_preview_identity_resolution_never_expands_character_names_to_other_entities() -> None:
    normalized = _normalize_outline_character_fields(
        _preview_with_entity_names(),
        character_whitelist=["沈岸", "糖水铺帮工", "匿名信投递人", "苏晚"],
        entity_catalog=_mixed_entity_catalog(),
    )
    assert normalized.involved_character_ids == ["char_shen", "char_worker", "char_sender"]
    assert normalized.support_character_ids == ["char_worker", "char_sender"]
    assert normalized.emotional_plan.subject_entity_id == "char_shen"
    assert normalized.involved_character_names == ["沈岸", "糖水铺帮工", "匿名信投递人"]
    assert (
        _normalize_outline_character_fields(
            normalized, character_whitelist=[], entity_catalog=_mixed_entity_catalog()
        )
        == normalized
    )


@pytest.mark.parametrize("with_design", [False, True])
def test_outline_does_not_promote_recalled_characters_to_active_cast(with_design: bool) -> None:
    design = (
        ChapterDesignMatrixEntry(
            chapter_number=10,
            cast_plan=ChapterCastPlan(
                pov_entity_id="char_shen", required_character_ids=["char_shen"]
            ),
        )
        if with_design
        else None
    )
    normalized = _normalize_outline_character_fields(
        _preview_with_entity_names(),
        character_whitelist=["沈岸", "糖水铺帮工", "匿名信投递人", "苏晚"],
        entity_catalog=_mixed_entity_catalog(),
        design_entry=design,
    )
    assert "char_su" not in normalized.involved_character_ids


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("emotional_plan.subject_entity_id", "shen_an"),
        ("emotional_plan.subject_entity_id", "loc_shop"),
        ("cast_plan.forbidden_active_character_ids", "loc_shop"),
        ("cast_plan.mention_only_entity_ids", "missing_entity"),
    ],
)
def test_preview_audit_checks_every_reference_without_design(field: str, value: str) -> None:
    chapter = _preview_with_entity_names()
    if field.startswith("emotional_plan"):
        chapter.emotional_plan.subject_entity_id = value
    else:
        setattr(chapter.cast_plan, field.split(".")[1], [value])
    audit = _outline_entity_audit([chapter], {}, entity_catalog=_mixed_entity_catalog())
    assert any(issue["field"] == field for issue in audit["issues"])


def test_preview_normalizer_preserves_wrong_type_references_for_audit() -> None:
    chapter = _preview_with_entity_names().model_copy(
        update={
            "involved_character_ids": ["char_shen", "loc_shop"],
            "support_character_ids": ["loc_shop"],
        }
    )
    normalized = _normalize_outline_character_fields(
        chapter, character_whitelist=[], entity_catalog=_mixed_entity_catalog()
    )
    audit = _outline_entity_audit([normalized], {}, entity_catalog=_mixed_entity_catalog())
    assert any(issue.get("observed_entity_id") == "loc_shop" for issue in audit["issues"])


def test_outline_entity_audit_rejects_unknown_character_ids() -> None:
    registry = EntityRegistry(
        entities=[
            EntityRecord(entity_id="char_xuanyu", name="玄昱", entity_type="character"),
            EntityRecord(entity_id="char_qingyi", name="沈清漪", entity_type="character"),
        ]
    )
    matrix = build_chapter_design_matrix(
        blueprint=NarrativeBlueprint.model_validate(
            {
                "narrative_phases": [
                    {
                        "phase_name": "审证",
                        "chapter_start": 1,
                        "chapter_end": 1,
                        "description": "沈清漪与玄昱审证。",
                        "key_characters": ["沈清漪", "玄昱"],
                    }
                ]
            }
        ),
        total_chapters=1,
        entity_registry=registry,
        character_bible={"characters": [{"name": "玄昱"}, {"name": "沈清漪"}]},
    )
    chapter = _chapter(
        1,
        title="杯底藏锋",
        goal="沈清漪在合卺礼中识别杯沿银粉。",
        main_plot_points=["沈清漪检查合卺杯沿。", "玄昱以青阳旧句试探她。"],
        beats_summary=[
            "宫宴礼声压住偏殿内的低声交锋。",
            "清漪借转杯动作确认杯沿异样。",
            "玄昱抛出青阳旧句观察她的反应。",
            "清漪把怀疑留到离席后验证。",
        ],
    ).model_copy(
        update={
            "pov_character_id": "char_unknown",
            "involved_character_ids": ["char_unknown"],
            "required_character_ids": ["char_unknown"],
        }
    )

    audit = _outline_entity_audit(
        [chapter],
        {"characters": [{"name": "玄昱"}, {"name": "沈清漪"}]},
        entity_catalog=matrix.entity_catalog,
        design_by_chapter=matrix.by_chapter(),
    )

    assert audit["ok"] is False
    assert audit["issues"][0]["observed_entity_id"] == "char_unknown"


def test_outline_entity_audit_rejects_emotional_subject_outside_active_cast() -> None:
    registry = EntityRegistry(
        entities=[
            EntityRecord(entity_id="char_xuanyu", name="玄昱", entity_type="character"),
            EntityRecord(entity_id="char_qingyi", name="沈清漪", entity_type="character"),
            EntityRecord(entity_id="char_bystander", name="旁观者", entity_type="character"),
        ]
    )
    matrix = build_chapter_design_matrix(
        blueprint=NarrativeBlueprint.model_validate(
            {
                "narrative_phases": [
                    {
                        "phase_name": "审证",
                        "chapter_start": 1,
                        "chapter_end": 1,
                        "description": "沈清漪与玄昱审证。",
                        "key_characters": ["沈清漪", "玄昱"],
                    }
                ]
            }
        ),
        total_chapters=1,
        entity_registry=registry,
        character_bible={"characters": [{"name": "玄昱"}, {"name": "沈清漪"}, {"name": "旁观者"}]},
    )
    design = matrix.by_chapter()[1]
    chapter = _chapter(
        1,
        title="杯底藏锋",
        goal="沈清漪审证。",
        main_plot_points=["沈清漪检查杯沿。", "玄昱抛出旧句。"],
        beats_summary=["审证开始。", "证据暴露。", "两人选择。", "余韵保留。"],
    ).model_copy(
        update={
            "pov_character_id": design.cast_plan.pov_entity_id,
            "involved_character_ids": list(design.cast_plan.required_character_ids),
            "required_character_ids": list(design.cast_plan.required_character_ids),
            "cast_plan": design.cast_plan,
            "emotional_plan": ChapterEmotionalPlan(
                subject_entity_id="char_bystander",
                entry_state="克制",
                pressure_source="证据冲突",
                relationship_choice="拒绝替对方隐瞒",
                turning_emotion="从犹疑到决绝",
                exit_aftertaste="信任裂缝扩大",
                expression_channels=["action", "dialogue"],
            ),
        }
    )

    audit = _outline_entity_audit(
        [chapter],
        {"characters": [{"name": "玄昱"}, {"name": "沈清漪"}, {"name": "旁观者"}]},
        entity_catalog=matrix.entity_catalog,
        design_by_chapter=matrix.by_chapter(),
    )

    assert audit["ok"] is False
    assert any(issue["field"] == "emotional_plan.subject_entity_id" for issue in audit["issues"])
