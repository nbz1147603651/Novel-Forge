from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.config import Settings
from novel_forge.memory.base import MemoryBudgetConfig, MemoryLayerBudget
from novel_forge.memory.integration import MemoryContext
from novel_forge.memory.layered_context_builder import build_layered_memory_context


class _SummaryService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get_summary_for_context(self, **kwargs: object) -> str:
        self.calls.append(dict(kwargs))
        granularity = kwargs.get("granularity")
        if granularity == "volume":
            return "第1卷宏观摘要"
        return "第4章：重复摘要应被 L2 去重\n第2章：保留摘要"


class _EpisodicMemory:
    def __init__(self) -> None:
        self.calls: list[dict[str, int]] = []
        self.events = [
            SimpleNamespace(
                chapter_number=4,
                scene_index=2,
                event_summary="林远发现异常信号。",
                characters_involved=["林远", "周岚"],
            ),
            SimpleNamespace(
                chapter_number=2,
                scene_index=1,
                event_summary="信号首次出现。",
                characters_involved=[],
            ),
        ]

    def search_by_temporal(self, *, start_chapter: int, end_chapter: int):
        self.calls.append({"start_chapter": start_chapter, "end_chapter": end_chapter})
        return list(self.events)


def test_layered_context_builds_layers_and_dedups_l1_against_l2() -> None:
    ctx = MemoryContext(settings=Settings(_env_file=None, memory_volume_summary_inject_threshold=3))
    ctx._summary_service = _SummaryService()
    ctx._episodic_memory = _EpisodicMemory()
    ctx.search_relevant_history_sync = lambda **_: [
        {
            "chapter_number": 2,
            "event_summary": "信号首次出现。",
            "relevance_score": 0.78,
            "characters_involved": ["林远"],
        }
    ]
    budget = MemoryBudgetConfig(
        l0_identity=MemoryLayerBudget("L0_identity", soft_limit_chars=5, hard_limit_chars=10)
    )

    result = ctx.get_layered_context(
        current_chapter=6,
        budget=budget,
        project_identity="abcdefg",
        query="异常信号",
    )

    assert result["L0_identity"] == "abcdefg"
    assert "## 卷级宏观\n第1卷宏观摘要" in result["L1_core_memory"]
    assert "第4章：重复摘要应被 L2 去重" not in result["L1_core_memory"]
    assert "第2章：保留摘要" not in result["L1_core_memory"]
    assert result["L2_on_demand"].splitlines() == [
        "## 近期事件",
        "- 第4章（林远、周岚）: 林远发现异常信号。",
        "- 第2章: 信号首次出现。",
    ]
    assert result["L3_deep_search"] == "## 相关历史\n- 第2章（林远） [相关度0.78]: 信号首次出现。"


def test_layered_context_includes_previous_volume_carry_forward() -> None:
    summary_service = _SummaryService()
    ctx = MemoryContext(settings=Settings(_env_file=None))
    ctx._summary_service = summary_service
    ctx.set_outline(
        SimpleNamespace(
            volume_mode=True,
            volumes=[
                SimpleNamespace(
                    volume_number=1,
                    title="旧钟楼",
                    start_chapter=1,
                    end_chapter=9,
                    resolution_hint="上一卷怀表线已交接。",
                ),
                SimpleNamespace(
                    volume_number=2,
                    title="新渡口",
                    start_chapter=10,
                    end_chapter=20,
                    arc_goal="追查渡口信号",
                ),
            ],
        )
    )

    result = ctx.get_layered_context(current_chapter=11)

    assert "## 上卷收束（卷1「旧钟楼」）\n上一卷怀表线已交接。" in result["L1_core_memory"]
    assert "## 上卷详要（卷1「旧钟楼」）\n第1卷宏观摘要" in result["L1_core_memory"]
    assert "第4章：重复摘要应被 L2 去重" in result["L1_core_memory"]
    assert summary_service.calls[0] == {
        "current_chapter": 11,
        "granularity": "volume",
        "lookback_volumes": 1,
        "current_volume_number": 1,
    }


def test_layered_context_builder_can_run_without_memory_services() -> None:
    result = build_layered_memory_context(
        current_chapter=3,
        settings=SimpleNamespace(memory_volume_summary_inject_threshold=10),
        project_identity="项目身份",
        query="不会触发深搜",
    )

    assert result == {
        "L0_identity": "项目身份",
        "L1_core_memory": "",
        "L2_on_demand": "",
        "L3_deep_search": "",
    }


def test_layered_context_preserves_complete_selected_evidence() -> None:
    long_summary = "事件起点" + ("甲" * 300) + "关键因果收束"
    many_characters = [f"角色{i}" for i in range(8)]
    episodic = _EpisodicMemory()
    episodic.events = [
        SimpleNamespace(
            chapter_number=4,
            scene_index=1,
            event_summary=long_summary,
            characters_involved=many_characters,
        )
    ]
    result = build_layered_memory_context(
        current_chapter=6,
        budget=MemoryBudgetConfig(
            l0_identity=MemoryLayerBudget("L0_identity", soft_limit_chars=4, hard_limit_chars=8),
            l2_on_demand=MemoryLayerBudget(
                "L2_on_demand", soft_limit_chars=20, hard_limit_chars=40
            ),
        ),
        settings=SimpleNamespace(memory_volume_summary_inject_threshold=10),
        project_identity="完整项目身份",
        episodic_memory=episodic,
    )

    assert result["L0_identity"] == "完整项目身份"
    assert long_summary in result["L2_on_demand"]
    assert "关键因果收束" in result["L2_on_demand"]
    assert "、".join(many_characters) in result["L2_on_demand"]
