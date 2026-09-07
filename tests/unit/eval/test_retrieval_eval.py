from __future__ import annotations

import math

from novel_forge.core.schemas.continuity import ChapterPlan, SceneIntent
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.retrieval_eval import RetrievalEvalReportPayload
from novel_forge.eval.retrieval_eval import (
    derive_gold_set,
    evaluate_retrieval_against_plan,
    extract_retrieved_set,
)
from novel_forge.eval.retrieval_tokenizer import tokenize_for_recall


def _outline() -> ChapterOutline:
    return ChapterOutline(
        chapter_number=1,
        goal="让主角发现秘密线索",
        pov_character="玄昱",
        involved_characters=["沈清漪"],
        involved_character_names=["宇文铎"],
        expected_word_count=1000,
    )


def _plan() -> ChapterPlan:
    return ChapterPlan(
        scene_intents=[
            SceneIntent(
                scene_id="scene_01",
                summary="玄昱在棋局旁发现秘密线索",
                pov_character="玄昱",
                required_characters=["玄昱", "沈清漪"],
                owned_events=["薄刃挑开残玉"],
                owned_revelations=["影卫留下暗号"],
                required_outcome="沈清漪确认棋局暗号",
                dramatic_question="玄昱能否识破影卫的局？",
            ),
            SceneIntent(
                scene_id="scene_02",
                summary="宇文铎追查采莲曲",
                pov_character="宇文铎",
                required_characters=["宇文铎"],
                owned_events=["采莲曲暴露秘密"],
            ),
        ],
        key_revelations=["残玉不是信物"],
        foreshadowing_plan=["采莲曲暗藏薄刃线索"],
    )


def test_tokenize_for_recall_uses_jieba_filters_stopwords_and_dedupes() -> None:
    tokens = tokenize_for_recall("秘密 秘密 的 了 线索 A")

    assert "秘密" in tokens
    assert "线索" in tokens
    assert "的" not in tokens
    assert "了" not in tokens
    assert len(tokens) == len(set(tokens))


def test_derive_gold_set_merges_characters_and_limits_scene_corpus() -> None:
    gold = derive_gold_set(plan=_plan(), chapter_outline=_outline(), max_scenes=1)

    assert gold.scene_count == 1
    assert gold.characters == frozenset({"玄昱", "沈清漪", "宇文铎"})
    assert "chapter_plan.event_fields" in gold.source_fields
    assert gold.event_tokens


def test_extract_retrieved_set_filters_non_character_entities_and_counts_sources() -> None:
    retrieved = extract_retrieved_set(
        plan_canon_context={
            "characters": {
                "玄昱": {"entity_type": "character"},
                "黑灰沾领": {"entity_type": "item"},
                "旧人": {},
            },
            "recent_events": [{"event": "玄昱发现秘密线索"}],
            "active_foreshadowing": [{"description": "薄刃暗号仍未解开"}],
        },
        plan_memory_hints={
            "relevant_history": [{"event_summary": "棋局留下线索"}],
            "previous_chapter_events": [{"event_summary": "采莲曲暴露秘密"}],
        },
    )

    assert retrieved.character_names == frozenset({"玄昱", "旧人"})
    assert retrieved.noisy_entity_names == frozenset({"黑灰沾领"})
    assert retrieved.source_counts["canon.characters"] == 3
    assert retrieved.source_counts["memory.relevant_history"] == 1
    assert retrieved.event_tokens


def test_evaluate_retrieval_metrics_and_payload_round_trip() -> None:
    report = evaluate_retrieval_against_plan(
        plan=_plan(),
        chapter_outline=_outline(),
        plan_canon_context={
            "characters": {
                "玄昱": {"entity_type": "character"},
                "沈清漪": {"entity_type": "character"},
                "黑灰沾领": {"entity_type": "item"},
            },
            "recent_events": [{"event": "玄昱发现秘密线索"}],
            "active_foreshadowing": [{"description": "采莲曲暗藏薄刃线索"}],
        },
        plan_memory_hints={
            "relevant_history": [{"event_summary": "沈清漪确认棋局暗号"}],
            "previous_chapter_events": [{"event_summary": "影卫留下暗号"}],
        },
        chapter_number=1,
        config_snapshot={"tokenizer": "jieba"},
    )

    assert report.aggregate["character_recall"] == 2 / 3
    assert report.aggregate["entity_noise_rate"] == 1 / 3
    assert report.aggregate["event_token_recall"] is not None
    assert report.aggregate["event_token_recall"] >= 0
    assert report.scene_metrics

    payload = report.to_payload()
    parsed = RetrievalEvalReportPayload.model_validate(payload.model_dump(mode="json"))
    assert parsed.report_type == "retrieval_eval_point_a"
    assert parsed.created_at


def test_empty_gold_recall_is_null() -> None:
    report = evaluate_retrieval_against_plan(
        plan=ChapterPlan(scene_intents=[]),
        chapter_outline=ChapterOutline(chapter_number=1, goal="过渡"),
        plan_canon_context={"characters": {}},
        plan_memory_hints={},
        chapter_number=1,
    )

    assert report.aggregate["character_recall"] is None
    assert report.aggregate["event_token_recall"] is None
    assert report.aggregate["entity_noise_rate"] is None
    assert not math.isnan(report.duration_ms)
