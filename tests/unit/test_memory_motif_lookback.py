"""Tests for motif lookback propagation across UI/backend contexts."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.config import Settings
from novel_forge.memory.audit_coordinator import AuditCoordinator
from novel_forge.memory.integration import MemoryContext


class _MotifTrackerStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, int]] = []

    def get_motifs_for_prompt(self, *, current_chapter: int, related_lookback_chapters: int = 2, **_kwargs):  # noqa: ANN003
        self.calls.append(
            {
                "current_chapter": int(current_chapter),
                "related_lookback_chapters": int(related_lookback_chapters),
            }
        )
        return {"active_motifs": [], "forbidden_repetition": [], "suggested_callbacks": []}

    async def check_unintentional_repetition(self, **_kwargs):  # noqa: ANN003
        return []


def test_memory_context_prompt_uses_configured_motif_lookback() -> None:
    settings = Settings(
        _env_file=None,
        memory_motif_related_lookback_chapters=6,
    )
    ctx = MemoryContext(settings=settings)
    tracker = _MotifTrackerStub()
    ctx._motif_tracker = tracker  # noqa: SLF001

    context = ctx.get_memory_context_for_prompt(
        current_chapter=12,
        include_motifs=True,
        include_summaries=False,
    )

    assert "motif_context" in context
    assert tracker.calls == [
        {
            "current_chapter": 12,
            "related_lookback_chapters": 6,
        }
    ]


def test_memory_context_prompt_preserves_zero_motif_lookback() -> None:
    settings = Settings(
        _env_file=None,
        memory_motif_related_lookback_chapters=0,
    )
    ctx = MemoryContext(settings=settings)
    tracker = _MotifTrackerStub()
    ctx._motif_tracker = tracker  # noqa: SLF001

    context = ctx.get_memory_context_for_prompt(
        current_chapter=12,
        include_motifs=True,
        include_summaries=False,
    )

    assert "motif_context" in context
    assert tracker.calls == [
        {
            "current_chapter": 12,
            "related_lookback_chapters": 0,
        }
    ]


def test_memory_context_prompt_excludes_current_chapter_cached_summary() -> None:
    ctx = MemoryContext(settings=Settings(_env_file=None))
    ctx._summary_cache = {  # noqa: SLF001
        2: {"text": "第二章既有摘要"},
        3: {"text": "第三章旧摘要不应注入"},
    }

    context = ctx.get_memory_context_for_prompt(
        current_chapter=3,
        include_motifs=False,
        include_summaries=True,
        include_critiques=False,
    )

    assert "第二章既有摘要" in context["summary_context"]
    assert "第三章旧摘要不应注入" not in context["summary_context"]


class _MemoryContextStub:
    def __init__(self, lookback: int) -> None:
        self.project_id = "demo-project"
        self.settings = SimpleNamespace(
            memory_motif_related_lookback_chapters=lookback,
        )
        self.motif_tracker = _MotifTrackerStub()

    def get_cached_summary(self, _chapter: int):
        return None

    def get_chapter_exit_state(self, _chapter: int):
        return None

    async def search_relevant_history(self, **_kwargs):  # noqa: ANN003
        return []

    def get_character_history(self, **_kwargs):  # noqa: ANN003
        return []

    def get_status_summary(self) -> dict[str, object]:
        return {}


async def test_audit_coordinator_uses_configured_motif_lookback() -> None:
    memory = _MemoryContextStub(lookback=4)
    coordinator = AuditCoordinator(memory)  # type: ignore[arg-type]

    await coordinator.prepare_audit_context(
        chapter_number=9,
        chapter_text="x" * 800,
    )

    assert memory.motif_tracker.calls == [
        {
            "current_chapter": 9,
            "related_lookback_chapters": 4,
        }
    ]
