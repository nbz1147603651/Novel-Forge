from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from novel_forge.core.constants import TaskType
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.init.init_coherence_v2 import refine_init_coherence_profile


class _ProfileCtx:
    def __init__(
        self,
        tmp_path: Path,
        *,
        split_tasks_enabled: bool = False,
        responses: dict[TaskType, dict] | None = None,
    ) -> None:
        self.calls: list[TaskType] = []
        self.events: list[tuple[str, dict]] = []
        self.settings = SimpleNamespace(
            split_tasks_enabled=split_tasks_enabled,
            temp_refine_init_coherence_profile=0.1,
        )
        self.router = SimpleNamespace()
        self.layout = ProjectLayout(tmp_path / "project")
        self.storage = SimpleNamespace(save_json=lambda *_args, **_kwargs: None)
        self.responses = responses or {
            TaskType.REFINE_INIT_COHERENCE_PROFILE: {
                "genre_tags": ["现代言情"],
                "project_ontology": {"state_axes": ["trust"]},
                "conflict_lens": ["信任状态不可回滚"],
                "extraction_guidance": ["抽取信任状态"],
                "summary": "单任务画像。",
            }
        }

    def on_step(self, step: str, payload: dict) -> None:
        self.events.append((step, payload))

    async def call_with_retry(self, task_type: TaskType, *_args, **_kwargs):
        self.calls.append(task_type)
        return self.responses[task_type]


async def test_refine_init_coherence_profile_uses_single_refine_task_when_split_disabled(
    tmp_path: Path,
) -> None:
    ctx = _ProfileCtx(tmp_path, split_tasks_enabled=False)

    profile = await refine_init_coherence_profile(
        ctx,
        current_profile={"summary": "seed"},
        spec={},
        story_bible={},
        character_bible={},
        creative_director_packet={},
        blueprint={},
    )

    assert ctx.calls == [TaskType.REFINE_INIT_COHERENCE_PROFILE]
    assert profile["refined_from_blueprint"] is True
    assert ctx._init_efficiency_metrics["profile_call_count"] == 1


async def test_refine_init_coherence_profile_uses_split_fragments_by_default(
    tmp_path: Path,
) -> None:
    ctx = _ProfileCtx(
        tmp_path,
        split_tasks_enabled=True,
        responses={
            TaskType.INIT_COHERENCE_ONTOLOGY: {
                "genre_tags": ["现代言情"],
                "narrative_modes": ["双线推进"],
                "project_ontology": {
                    "domains": ["中医"],
                    "state_axes": [{"axis": "trust"}],
                    "terminology": {"银杏叶": "信物"},
                },
            },
            TaskType.INIT_COHERENCE_PAYOFF_RULES: {
                "payoff_types": [{"type": "relationship"}],
                "irreversible_event_markers": [{"marker": "公开"}],
                "temporal_markers": [{"marker": "回忆"}],
                "summary": "按蓝图精炼。",
            },
            TaskType.INIT_COHERENCE_CONFLICT_RULES: {
                "conflict_lens": [{"label": "信任状态不可回滚"}],
            },
            TaskType.INIT_COHERENCE_EXTRACTION_GUIDE: {
                "extraction_guidance": [{"value": "抽取状态变化"}],
            },
        },
    )

    profile = await refine_init_coherence_profile(
        ctx,
        current_profile={"summary": "seed"},
        spec={},
        story_bible={},
        character_bible={},
        creative_director_packet={},
        blueprint={},
    )

    assert ctx.calls == [
        TaskType.INIT_COHERENCE_ONTOLOGY,
        TaskType.INIT_COHERENCE_PAYOFF_RULES,
        TaskType.INIT_COHERENCE_CONFLICT_RULES,
        TaskType.INIT_COHERENCE_EXTRACTION_GUIDE,
    ]
    assert profile["project_ontology"]["state_axes"] == ["trust"]
    assert profile["conflict_lens"] == ["信任状态不可回滚"]
    assert profile["extraction_guidance"] == ["抽取状态变化"]
    assert profile["refined_from_blueprint"] is True
    assert ctx.events[-1][1]["mode"] == "split"
