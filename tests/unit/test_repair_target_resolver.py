from __future__ import annotations

from typing import Any

from novel_forge.core.schemas.audit import AuditLocator
from novel_forge.core.utils.repair_target_resolver import (
    RepairResolverContext,
    RepairTargetResolver,
    _fragment,
    _locator_key,
    stable_repair_value_hash,
)


def _issue(**overrides: Any) -> dict[str, Any]:
    payload = {
        "issue_id": "issue_1",
        "issue_type": "unit",
        "severity": "medium",
        "blocking": False,
        "summary": "定位测试",
        "description": "定位测试",
        "evidence": [{"quote": "证据", "source": "unit"}],
        "repair_targets": [],
        "reference_targets": [],
        "repair_intent": {
            "operation": "replace",
            "target_policy": "single_target",
            "rationale": "unit",
        },
    }
    payload.update(overrides)
    return payload


def test_json_artifact_resolves_exact_pointer() -> None:
    resolver = RepairTargetResolver(
        RepairResolverContext(artifacts={"outline": {"chapters": [{"goal": "旧目标"}]}})
    )

    targets = resolver.resolve_issue(
        _issue(
            repair_targets=[
                {
                    "target_format": "json_artifact",
                    "role": "repair",
                    "artifact": "outline",
                    "json_pointer": "/chapters/0/goal",
                    "field": "goal",
                }
            ]
        )
    )

    assert targets[0].resolution_status == "resolved"
    assert targets[0].path == "/chapters/0/goal"
    assert targets[0].current_value == "旧目标"


def test_json_artifact_resolves_stable_node_and_field_path() -> None:
    payload = {"chapters": [{"node_id": "chapter-1", "goal": "旧目标"}]}
    resolver = RepairTargetResolver(
        RepairResolverContext(
            artifacts={"outline": payload},
            stable_node_paths={"outline:chapter-1": "/chapters/0"},
        )
    )

    targets = resolver.resolve_issue(
        _issue(
            repair_targets=[
                {
                    "target_format": "json_artifact",
                    "role": "repair",
                    "artifact": "outline",
                    "stable_node_id": "chapter-1",
                    "field_path": "goal",
                    "container_hash": stable_repair_value_hash("旧目标"),
                }
            ]
        )
    )

    assert targets[0].resolution_status == "resolved"
    assert targets[0].path == "/chapters/0/goal"
    assert targets[0].current_value == "旧目标"


def test_json_artifact_normalizes_dotted_and_bracketed_field_path() -> None:
    resolver = RepairTargetResolver(
        RepairResolverContext(artifacts={"outline": {"chapters": [{"goal": "旧目标"}]}})
    )

    targets = resolver.resolve_issue(
        _issue(
            repair_targets=[
                {
                    "target_format": "json_artifact",
                    "role": "repair",
                    "artifact": "outline",
                    "field_path": "chapters[0].goal",
                }
            ]
        )
    )

    assert targets[0].resolution_status == "resolved"
    assert targets[0].path == "/chapters/0/goal"


def test_json_artifact_resolves_exact_character_span() -> None:
    text = "她必须在三日停职结束前复验甘松"
    resolver = RepairTargetResolver(
        RepairResolverContext(artifacts={"outline": {"goal": text}})
    )

    targets = resolver.resolve_issue(
        _issue(
            repair_targets=[
                {
                    "target_format": "json_artifact",
                    "role": "repair",
                    "artifact": "outline",
                    "json_pointer": "/goal",
                    "quote": "三日停职结束",
                    "text_hash": stable_repair_value_hash(text),
                    "char_start": 4,
                    "char_end": 10,
                }
            ]
        )
    )

    assert targets[0].resolution_status == "resolved"
    assert targets[0].current_value == "三日停职结束"
    assert targets[0].window["char_start"] == 4
    assert targets[0].window["char_end"] == 10
    assert targets[0].reason == "exact_text_span"


def test_json_artifact_stale_span_does_not_widen_to_full_field() -> None:
    resolver = RepairTargetResolver(
        RepairResolverContext(artifacts={"outline": {"goal": "停职五日受审"}})
    )

    targets = resolver.resolve_issue(
        _issue(
            repair_targets=[
                {
                    "target_format": "json_artifact",
                    "role": "repair",
                    "artifact": "outline",
                    "json_pointer": "/goal",
                    "quote": "三日停职",
                    "text_hash": "stale",
                    "char_start": 0,
                    "char_end": 4,
                }
            ]
        )
    )

    assert targets[0].resolution_status == "manual_required"
    assert targets[0].reason == "json_text_span_stale_or_unresolved"


def test_json_artifact_does_not_repair_reference_target() -> None:
    resolver = RepairTargetResolver(
        RepairResolverContext(artifacts={"blueprint": {"character_arcs": ["保留"]}})
    )

    targets = resolver.resolve_issue(
        _issue(
            severity="high",
            repair_targets=[
                {
                    "target_format": "json_artifact",
                    "role": "repair",
                    "artifact": "blueprint",
                    "json_pointer": "/character_arcs/0",
                    "field": "character_arcs",
                }
            ],
            reference_targets=[
                {
                    "target_format": "json_artifact",
                    "role": "reference",
                    "artifact": "blueprint",
                    "json_pointer": "/character_arcs/0",
                    "field": "character_arcs",
                }
            ],
        )
    )

    assert targets[0].target_format == "manual_only"
    assert targets[0].resolution_status == "manual_required"
    assert targets[0].reason == "repair_target_matches_reference_target"


def test_prose_text_ambiguous_quote_returns_manual_required() -> None:
    resolver = RepairTargetResolver(
        RepairResolverContext(
            texts={
                "chapter_text": "同一句需要定位。\n\n另一段。\n\n同一句需要定位。",
            }
        )
    )

    targets = resolver.resolve_issue(
        _issue(
            repair_targets=[
                {
                    "target_format": "prose_text",
                    "role": "repair",
                    "quote": "同一句需要定位",
                }
            ]
        )
    )

    assert targets[0].target_format == "manual_only"
    assert targets[0].reason == "quote_ambiguous"


def test_markdown_document_resolves_heading_subtree() -> None:
    resolver = RepairTargetResolver(
        RepairResolverContext(
            markdown_documents={
                "document": "# 第一章\n正文一\n\n## 场景A\n目标段\n\n# 第二章\n正文二",
            }
        )
    )

    targets = resolver.resolve_issue(
        _issue(
            repair_targets=[
                {
                    "target_format": "markdown_document",
                    "role": "repair",
                    "heading_path": ["第一章", "场景A"],
                }
            ]
        )
    )

    assert targets[0].resolution_status == "resolved"
    assert targets[0].window == {"heading_path": ["第一章", "场景A"]}
    assert targets[0].current_value == "## 场景A\n目标段"


def test_prompt_response_missing_key_stays_structured_manual_repair() -> None:
    resolver = RepairTargetResolver()

    targets = resolver.resolve_issue(
        _issue(
            repair_targets=[
                {
                    "target_format": "prompt_response",
                    "role": "repair",
                    "task_type": "DRAFT_CHAPTER",
                    "missing_key": "scene_intents",
                }
            ]
        )
    )

    assert targets[0].target_format == "manual_only"
    assert targets[0].reason == "prompt_response_schema_repair_required"


def test_multi_chapter_set_requires_split_targets() -> None:
    resolver = RepairTargetResolver()

    targets = resolver.resolve_issue(
        _issue(
            repair_targets=[
                {
                    "target_format": "multi_chapter_set",
                    "role": "repair",
                    "issue_thread_id": "thread_1",
                    "chapter_set": [4, 8],
                }
            ]
        )
    )

    assert targets[0].target_format == "manual_only"
    assert targets[0].reason == "multi_chapter_set_requires_split_targets"


def test_locator_key_does_not_collide_with_colon_in_quote() -> None:
    """Quote containing ':' must not produce the same key as a different locator."""
    locator_a = AuditLocator(
        target_format="prose_text",
        role="repair",
        surface="chapter",
        quote="场景A：冲突",
        paragraph_start=1,
        paragraph_end=2,
    )
    locator_b = AuditLocator(
        target_format="prose_text",
        role="repair",
        surface="chapter场景A",
        quote="冲突",
        paragraph_start=1,
        paragraph_end=2,
    )
    assert _locator_key(locator_a) != _locator_key(locator_b)


def test_locator_key_json_artifact_uses_stable_separator() -> None:
    locator = AuditLocator(
        target_format="json_artifact",
        role="repair",
        artifact="blueprint",
        json_pointer="/key_turning_points/0/description",
    )
    key = _locator_key(locator)
    assert "|||" in key
    assert "blueprint" in key


def test_fragment_short_text_requires_containment() -> None:
    """Short quote/evidence must match only when it is contained in target text."""
    assert _fragment("具象动作", "这段文本提到了具象动作") is True
    assert _fragment("这段文本提到了具象动作", "具象动作") is False
    assert _fragment("具象动作", "另一段完全不同的文本") is False
    assert _fragment("动作", "这段文本提到了动作") is False


def test_fragment_long_text_allows_reverse_match() -> None:
    """Long fragment (>=8 chars) still allows bidirectional matching."""
    long_fragment = "这是一个足够长的片段用于测试双向匹配功能"
    current_text = "前文铺垫。这是一个足够长的片段用于测试双向匹配功能。后文承接。"
    assert _fragment(long_fragment, current_text) is True
    assert _fragment(current_text, long_fragment) is True
