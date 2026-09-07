"""Tests for volume_audit.j2 template episodic_context block."""

from __future__ import annotations

from novel_forge.common.constants import TaskType
from novel_forge.prompts.builder import PromptBuilder


def _volume_audit_context(with_episodic: bool = True) -> dict:
    """Minimal volume_audit context for testing."""
    base = {
        "volume": {
            "volume_number": 1,
            "title": "觉醒",
            "start_chapter": 1,
            "end_chapter": 10,
            "arc_goal": "主角觉醒超能力",
        },
        "story_synopsis": "一个关于觉醒与成长的小说。",
        "chapter_summaries": [
            {"chapter": 1, "summary": "主角首次发现自己有异能"},
            {"chapter": 2, "summary": "主角尝试控制异能"},
        ],
        "timeline_events": [
            {"chapter": 1, "event": "异能觉醒"},
            {"chapter": 2, "event": "第一次失控"},
        ],
        "active_characters": [
            {
                "name": "林晚",
                "alive": True,
                "location": "城市",
                "inventory": ["手机", "钥匙"],
            },
        ],
        "active_foreshadowing": [
            {"id": "FS-01", "description": "神秘符号", "status": "active"},
        ],
        "world_fact_keys": ["近未来城市", "异能者存在"],
    }
    if with_episodic:
        base["episodic_context"] = {
            "similar_volumes": [
                {
                    "volume_number": 3,
                    "relevance_score": 0.85,
                    "consistency_score": 8.5,
                    "carry_over_characters": ["林晚", "周临"],
                    "carry_over_items": ["神秘符号"],
                },
                {
                    "volume_number": 7,
                    "relevance_score": 0.72,
                    "consistency_score": 7.8,
                    "carry_over_characters": ["林晚"],
                    "carry_over_items": [],
                },
            ],
        }
    return base


class TestEpisodicContextBlock:
    """Tests for episodic_context conditional block in volume_audit.j2."""

    def test_episodic_context_rendered(self) -> None:
        """When episodic_context.similar_volumes is present, block appears."""
        builder = PromptBuilder()
        rendered = builder.render(
            TaskType.VOLUME_AUDIT,
            _volume_audit_context(with_episodic=True),
        )
        assert "历史相似卷参考" in rendered
        assert "第3卷（相似度: 0.85）" in rendered
        assert "一致性评分: 8.5" in rendered
        assert "林晚、周临" in rendered
        assert "第7卷（相似度: 0.72）" in rendered

    def test_episodic_context_absent(self) -> None:
        """When episodic_context is absent, block does NOT appear."""
        builder = PromptBuilder()
        rendered = builder.render(
            TaskType.VOLUME_AUDIT,
            _volume_audit_context(with_episodic=False),
        )
        assert "历史相似卷参考" not in rendered