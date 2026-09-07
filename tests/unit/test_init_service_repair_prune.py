from __future__ import annotations

import json
from typing import Any

from novel_forge.pipeline.long.services.init.init_service import (
    _apply_prune_strategy,
    _merge_pruned_fields,
)


def _make_large_blueprint(
    total_chapters: int = 24,
    subplot_count: int = 5,
    hooks_per_chapter: int = 3,
    arc_count: int = 6,
) -> dict[str, Any]:
    return {
        "climax_markers": [
            {"chapter_number": total_chapters // 2, "climax_type": "main", "description": f"核心高潮在第{total_chapters // 2}章"}
        ],
        "climax_strategy": "密集高潮 + 余波收束",
        "world_rules": {"summary": "时间可被观测但不能被改变", "detail": "详细世界规则描述..." * 20},
        "theme_summary": "记忆、选择与代价",
        "narrative_hooks": [
            {
                "hook_id": f"hook_{i}",
                "chapter_number": i // hooks_per_chapter + 1,
                "hook_type": ["mystery", "crisis", "emotion"][i % 3],
                "description": f"悬念细节 {i}: " + "x" * 500,
                "payload_chapter": i // hooks_per_chapter + 2,
            }
            for i in range(total_chapters * hooks_per_chapter)
        ],
        "suspense_schedule": [
            {
                "chapter": i + 1,
                "suspense_type": ["mystery", "crisis", "choice"][i % 3],
                "intensity": (i % 10) + 1,
                "description": f"第{i+1}章悬念规划: " + "y" * 300,
            }
            for i in range(total_chapters)
        ],
        "subplot_plan": [
            {
                "name": f"支线{s}",
                "description": f"支线{s}的详细说明: " + "z" * 300,
                "chapter_events": [
                    {
                        "chapter_number": c + 1,
                        "event": f"第{c+1}章支线{s}事件: " + "w" * 200,
                    }
                    for c in range(total_chapters)
                ],
            }
            for s in range(subplot_count)
        ],
        "character_arcs": [
            {
                "character": f"角色{a}",
                "arc_summary": f"角色{a}的弧光总结",
                "milestones": [
                    {"chapter_start": m * 4 + 1, "chapter_end": m * 4 + 4, "description": f"里程碑{m}"}
                    for m in range(total_chapters // 4)
                ],
            }
            for a in range(arc_count)
        ],
    }


class TestApplyPruneStrategy:
    def test_none_strategy_no_change(self) -> None:
        payload = {"key": "value", "nested": {"a": 1}}
        result, removed = _apply_prune_strategy(payload, "none")
        assert result == payload
        assert removed == []

    def test_outline_strategy_no_change(self) -> None:
        payload = {"chapters": [{"chapter_number": 1}]}
        result, removed = _apply_prune_strategy(payload, "outline")
        assert result == payload
        assert removed == []

    def test_chapter_contracts_strategy_no_change(self) -> None:
        payload = {"contracts": [{"chapter_number": 1, "requirements": {}}]}
        result, removed = _apply_prune_strategy(payload, "chapter_contracts")
        assert result == payload
        assert removed == []

    def test_blueprint_prune_preserves_climax_markers(self) -> None:
        payload = {
            "climax_markers": [{"chapter_number": 16, "climax_type": "main"}],
            "climax_strategy": "双高潮收束",
        }
        result, removed = _apply_prune_strategy(payload, "blueprint")
        assert "climax_markers" in result
        assert result["climax_markers"] == payload["climax_markers"]
        assert "climax_strategy" in result
        assert result["climax_strategy"] == payload["climax_strategy"]

    def test_blueprint_prune_preserves_world_rules_and_theme(self) -> None:
        payload = {
            "world_rules": {"summary": "魔法需等价交换", "detail": "..."},
            "theme_summary": "牺牲与救赎",
        }
        result, removed = _apply_prune_strategy(payload, "blueprint")
        assert "world_rules" in result
        assert result["world_rules"] == payload["world_rules"]
        assert "theme_summary" in result
        assert result["theme_summary"] == payload["theme_summary"]

    def test_blueprint_prune_removes_narrative_hooks_detail(self) -> None:
        hooks = [
            {"hook_id": "h1", "description": "x" * 500},
            {"hook_id": "h2", "description": "x" * 500},
            {"hook_id": "h3", "description": "x" * 500},
        ]
        payload = {"narrative_hooks": hooks}
        result, removed = _apply_prune_strategy(payload, "blueprint")
        assert "/narrative_hooks" in removed
        assert "narrative_hooks" not in result
        assert "narrative_hooks_count" in result
        assert result["narrative_hooks_count"] == 3

    def test_blueprint_prune_removes_suspense_schedule_detail(self) -> None:
        payload = {
            "suspense_schedule": [
                {"chapter": 1, "suspense_type": "mystery"},
                {"chapter": 2, "suspense_type": "crisis"},
            ]
        }
        result, removed = _apply_prune_strategy(payload, "blueprint")
        assert "/suspense_schedule" in removed
        assert "suspense_schedule" not in result
        assert "suspense_schedule_summary" in result
        assert "2 suspense items" in str(result["suspense_schedule_summary"])

    def test_blueprint_prune_removes_subplot_plan_detail(self) -> None:
        payload = {
            "subplot_plan": [
                {"name": "复仇线", "description": "..."},
                {"name": "爱情线", "description": "..."},
            ]
        }
        result, removed = _apply_prune_strategy(payload, "blueprint")
        assert "/subplot_plan" in removed
        assert "subplot_plan" not in result
        assert "subplot_plan_summary" in result
        assert "2 subplots" in str(result["subplot_plan_summary"])
        assert "复仇线" in str(result["subplot_plan_summary"])
        assert "爱情线" in str(result["subplot_plan_summary"])

    def test_blueprint_prune_returns_removed_paths(self) -> None:
        payload = {
            "narrative_hooks": [{"h": 1}],
            "suspense_schedule": [{"s": 1}],
            "subplot_plan": [{"p": 1}],
        }
        result, removed = _apply_prune_strategy(payload, "blueprint")
        assert sorted(removed) == sorted(
            ["/narrative_hooks", "/suspense_schedule", "/subplot_plan"]
        )

    def test_blueprint_prune_handles_missing_fields(self) -> None:
        payload = {"climax_markers": [{"chapter_number": 8}]}
        result, removed = _apply_prune_strategy(payload, "blueprint")
        assert result["climax_markers"] == payload["climax_markers"]
        assert "narrative_hooks_count" not in result
        assert removed == []

    def test_blueprint_prune_dict_suspense_schedule(self) -> None:
        payload = {"suspense_schedule": {"summary": "三幕悬念结构", "detail": "..."}}
        result, removed = _apply_prune_strategy(payload, "blueprint")
        assert "/suspense_schedule" in removed
        assert "suspense_schedule_summary" in result
        assert result["suspense_schedule_summary"] == "三幕悬念结构"

    def test_blueprint_prune_dict_subplot_plan(self) -> None:
        payload = {"subplot_plan": {"summary": "两条支线交织", "count": 2}}
        result, removed = _apply_prune_strategy(payload, "blueprint")
        assert "/subplot_plan" in removed
        assert "subplot_plan_summary" in result
        assert "两条支线交织" in str(result["subplot_plan_summary"])

    def test_blueprint_prune_empty_lists(self) -> None:
        payload = {
            "narrative_hooks": [],
            "suspense_schedule": [],
            "subplot_plan": [],
        }
        result, removed = _apply_prune_strategy(payload, "blueprint")
        assert "narrative_hooks_count" in result
        assert result["narrative_hooks_count"] == 0
        assert "0 suspense items" in str(result["suspense_schedule_summary"])
        assert "0 subplots" in str(result["subplot_plan_summary"])

    def test_prune_is_non_destructive(self) -> None:
        original = {
            "narrative_hooks": [{"hook_id": "h1"}],
            "climax_markers": [{"chapter_number": 5}],
        }
        result, removed = _apply_prune_strategy(original, "blueprint")
        assert original["narrative_hooks"] == [{"hook_id": "h1"}]
        assert "narrative_hooks" in original
        assert "narrative_hooks" not in result
        assert "narrative_hooks_count" in result


class TestMergePrunedFields:
    def test_merge_when_no_pruned_fields(self) -> None:
        original = {"a": 1}
        pruned = {"a": 2}
        result = _merge_pruned_fields(original, pruned, [])
        assert result is pruned
        assert result["a"] == 2

    def test_merge_restores_pruned_fields(self) -> None:
        original = {
            "narrative_hooks": [{"hook_id": "h1"}],
            "climax_markers": [{"chapter_number": 5}],
            "suspense_schedule": [{"s": 1}],
        }
        pruned_payload, pruned_fields = _apply_prune_strategy(original, "blueprint")
        assert "narrative_hooks" not in pruned_payload

        merged = _merge_pruned_fields(original, pruned_payload, pruned_fields)
        assert "narrative_hooks" in merged
        assert merged["narrative_hooks"] == [{"hook_id": "h1"}]
        assert "suspense_schedule" in merged
        assert merged["suspense_schedule"] == [{"s": 1}]
        assert merged["climax_markers"] == [{"chapter_number": 5}]

    def test_merge_preserves_pruned_changes(self) -> None:
        original = {"climax_markers": [{"c": 1}], "narrative_hooks": [{"hook_id": "old"}]}
        pruned_payload, pruned_fields = _apply_prune_strategy(original, "blueprint")
        pruned_payload["climax_markers"][0]["c"] = 999

        merged = _merge_pruned_fields(original, pruned_payload, pruned_fields)
        assert merged["climax_markers"][0]["c"] == 999
        assert merged["narrative_hooks"] == [{"hook_id": "old"}]


class TestLargeBlueprintPrune:
    def test_24_chapter_blueprint_prune_under_200k_chars(self) -> None:
        payload = _make_large_blueprint(total_chapters=24, subplot_count=5, hooks_per_chapter=3)
        pruned, removed = _apply_prune_strategy(payload, "blueprint")

        pruned_json = json.dumps(pruned, ensure_ascii=False)
        char_count = len(pruned_json)
        approx_tokens = char_count // 4

        assert char_count < 200000, (
            f"Pruned blueprint too large: {char_count} chars (~{approx_tokens} tokens)"
        )

    def test_24_chapter_blueprint_prune_critical_fields_preserved(self) -> None:
        payload = _make_large_blueprint(total_chapters=24)
        pruned, removed = _apply_prune_strategy(payload, "blueprint")

        assert "climax_markers" in pruned
        assert pruned["climax_markers"] == payload["climax_markers"]
        assert "climax_strategy" in pruned
        assert pruned["climax_strategy"] == payload["climax_strategy"]
        assert "world_rules" in pruned
        assert "theme_summary" in pruned
        assert pruned["theme_summary"] == payload["theme_summary"]
        assert "narrative_hooks_count" in pruned
        assert pruned["narrative_hooks_count"] == 24 * 3

    def test_24_chapter_blueprint_prune_detail_removed(self) -> None:
        payload = _make_large_blueprint(total_chapters=24)
        pruned, removed = _apply_prune_strategy(payload, "blueprint")

        assert "/narrative_hooks" in removed
        assert "/suspense_schedule" in removed
        assert "/subplot_plan" in removed
        assert "narrative_hooks" not in pruned
        assert "suspense_schedule" not in pruned
        assert "subplot_plan" not in pruned
