"""End-to-end integration tests for narrative-enhanced motif behaviors."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from novel_forge.common.constants import TaskType
from novel_forge.core.domain.forbidden_element_registry import ForbiddenElementRegistry
from novel_forge.memory.motif import Motif, MotifTracker


class _CapturingBuilder:
    """Prompt builder double that captures motif extraction context."""

    def __init__(self) -> None:
        self.captured_task_type: TaskType | None = None
        self.captured_context: dict | None = None

    def build(self, task_type: TaskType, context: dict, **kwargs):
        self.captured_task_type = task_type
        self.captured_context = dict(context)
        return SimpleNamespace(task_type=task_type, context=context, kwargs=kwargs)


class _FakeRouter:
    """Router double that avoids real LLM calls."""

    def __init__(self, content: str = '{"motifs": []}') -> None:
        self.content = content
        self.last_request = None

    async def route(self, request):
        self.last_request = request
        return SimpleNamespace(content=self.content)


def _make_tracker() -> MotifTracker:
    return MotifTracker(router=AsyncMock(), builder=AsyncMock())


def _add_motif(
    tracker: MotifTracker,
    *,
    motif_id: str,
    name: str,
    category: str = "意象",
    occurrence_count: int = 5,
    first_appearance_chapter: int = 1,
    last_appearance_chapter: int = 1,
    associated_characters: list[str] | None = None,
    thematic_meaning: str = "",
    retired: bool = False,
) -> None:
    tracker._motifs[motif_id] = Motif(
        motif_id=motif_id,
        name=name,
        category=category,
        occurrence_count=occurrence_count,
        first_appearance_chapter=first_appearance_chapter,
        last_appearance_chapter=last_appearance_chapter,
        associated_characters=associated_characters or [],
        thematic_meaning=thematic_meaning,
        is_intentional=True,
        retired=retired,
    )
    tracker._recent_usage[motif_id] = [last_appearance_chapter]
    tracker._chapter_motifs[last_appearance_chapter].add(motif_id)


@pytest.mark.asyncio
async def test_outline_context_in_extraction() -> None:
    """Extraction prompt should include outline goal, POV, and element focus."""
    builder = _CapturingBuilder()
    router = _FakeRouter()
    tracker = MotifTracker(router=router, builder=builder)

    outline = {
        "goal": "揭示旧钟楼的秘密",
        "pov_character": "林远",
        "element_focus": ["悬念", "伏笔回收"],
    }

    await tracker.extract_from_chapter(
        chapter_number=6,
        chapter_text="第六章正文",
        chapter_outline=outline,
    )

    assert builder.captured_task_type == TaskType.EXTRACT_MOTIFS
    assert builder.captured_context is not None
    assert builder.captured_context["chapter_goal"] == "揭示旧钟楼的秘密"
    assert builder.captured_context["pov_character"] == "林远"
    assert builder.captured_context["element_focus"] == ["悬念", "伏笔回收"]


def test_outline_aware_suggestions() -> None:
    """Outline goal should boost matching motif suggestions."""
    tracker = _make_tracker()
    _add_motif(
        tracker,
        motif_id="motif_reunion",
        name="重逢",
        category="主题",
        last_appearance_chapter=1,
        thematic_meaning="重逢后的救赎",
    )
    _add_motif(
        tracker,
        motif_id="motif_mirror",
        name="镜子",
        category="符号",
        last_appearance_chapter=1,
        thematic_meaning="真相映照",
    )

    suggestions = tracker.get_suggestions_for_chapter(
        current_chapter=10,
        chapter_outline={"goal": "重逢", "pov_character": "", "element_focus": []},
    )
    priorities = {item.motif_name: item.priority for item in suggestions}

    assert priorities["重逢"] == "high"
    assert priorities["镜子"] == "medium"


async def test_forward_guidance_dormant_detection() -> None:
    """Motifs dormant for 25 chapters should surface as callbacks."""
    tracker = _make_tracker()
    _add_motif(
        tracker,
        motif_id="motif_bell",
        name="铜铃",
        category="声音",
        occurrence_count=3,
        last_appearance_chapter=5,
    )

    guidance = await tracker.get_forward_looking_guidance(current_chapter=30)
    dormant_ids = [item["motif_id"] for item in guidance["dormant_callbacks"]]

    assert "motif_bell" in dormant_ids


async def test_forward_guidance_plot_matched() -> None:
    """Chapter goals should surface motifs with matching thematic meaning."""
    tracker = _make_tracker()
    _add_motif(
        tracker,
        motif_id="motif_lantern",
        name="灯塔",
        category="意象",
        occurrence_count=4,
        last_appearance_chapter=3,
        thematic_meaning="重逢",
    )

    guidance = await tracker.get_forward_looking_guidance(
        current_chapter=12,
        chapter_outline={"goal": "重逢", "pov_character": "", "element_focus": []},
    )
    matched_ids = [item["motif_id"] for item in guidance["plot_matched_motifs"]]

    assert "motif_lantern" in matched_ids


@pytest.mark.asyncio
async def test_warmup_with_themes() -> None:
    """Warmup should create theme-category motifs from story themes."""
    tracker = _make_tracker()

    await tracker.warmup(story_themes=["重生", "救赎"])

    theme_motifs = {motif.name: motif.category for motif in tracker._motifs.values()}
    assert theme_motifs["重生"] == "主题"
    assert theme_motifs["救赎"] == "主题"


def test_genre_reinit(tmp_path: Path) -> None:
    """Genre re-init should replace project seeds with the selected genre template."""
    seeds_path = ForbiddenElementRegistry.reinit_project_seeds(tmp_path, "scifi")

    assert seeds_path == tmp_path / "config" / "forbidden_element_seeds.yaml"
    payload = yaml.safe_load(seeds_path.read_text(encoding="utf-8"))

    assert payload["metadata"]["genre"] == "scifi"
    assert "星光" in payload["rhetorical_imagery_hints"]
    assert "舰长" in payload["kinship_and_address_terms"]
