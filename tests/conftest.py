"""Shared test fixtures for the Novel Forge test suite."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.core.schemas.story_state import CharacterState
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.story_kernel.schemas import (
    Entity,
    PromiseLedger,
    StoryKernel,
    TimelineAnchor,
)
from tests.helpers.faux_adapter import FauxAdapter
from tests.helpers.faux_tts_adapter import FauxTTSAdapter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NOVEL_FORGE_MEMORY_USE_MOCK_EMBEDDINGS", "true")

_QT_TEST_TOKENS = (
    "PySide6",
    "pytestqt",
    "qtbot",
    "QApplication",
    "QTimer",
    "QWidget",
)
_QT_TEST_FILE_CACHE: dict[Path, bool] = {}


def pytest_configure(config: pytest.Config) -> None:
    # API dependency construction can recover durable autoruns. Never let a
    # test's unoverridden dependency discover the developer's real projects.
    # Configure before collection; each xdist process gets its own storage.
    os.environ["NOVEL_FORGE_STORAGE_ROOT"] = tempfile.mkdtemp(
        prefix="novel-forge-pytest-storage-"
    )
    try:
        import pytestqt.plugin as pytestqt_plugin
    except ImportError:
        return

    def _skip_global_qt_event_pump() -> None:
        return

    pytestqt_plugin._process_events = _skip_global_qt_event_pump


def _is_qt_test_file(path: Path) -> bool:
    cached = _QT_TEST_FILE_CACHE.get(path)
    if cached is not None:
        return cached
    if path.suffix != ".py":
        _QT_TEST_FILE_CACHE[path] = False
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        _QT_TEST_FILE_CACHE[path] = False
        return False
    result = any(token in text for token in _QT_TEST_TOKENS)
    _QT_TEST_FILE_CACHE[path] = result
    return result


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if not os.environ.get("PYTEST_XDIST_WORKER"):
        return
    skip_parallel_qt = pytest.mark.skip(
        reason="Qt/PySide tests are xdist-unsafe; run them serially."
    )
    for item in items:
        if _is_qt_test_file(Path(str(item.path)).resolve()):
            item.add_marker(skip_parallel_qt)


@pytest.fixture(autouse=True)
def isolate_qt_background_lifecycle(request: pytest.FixtureRequest):
    """Release Qt resources created by one test before the next contract."""

    test_path = Path(str(request.node.path)).resolve()
    is_qt_test = _is_qt_test_file(test_path)
    existing_widget_ids: set[int] = set()
    if is_qt_test:
        from PySide6.QtWidgets import QApplication

        existing_app = QApplication.instance()
        if existing_app is not None:
            existing_widget_ids = {id(widget) for widget in existing_app.topLevelWidgets()}
    yield
    if not is_qt_test:
        return

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    from novel_forge.control_plane.factory import reset_control_plane_store
    from novel_forge.control_plane.plane import reset_cached_control_plane
    from novel_forge.desktop.thread_pools import reset_desktop_thread_pools_for_tests

    reset_desktop_thread_pools_for_tests()
    reset_cached_control_plane()
    reset_control_plane_store()
    app = QApplication.instance()
    if app is None:
        return
    created_widgets = [
        widget for widget in app.topLevelWidgets() if id(widget) not in existing_widget_ids
    ]
    for widget in reversed(created_widgets):
        with suppress(RuntimeError, AttributeError):
            shutdown = getattr(widget, "shutdown", None)
            if callable(shutdown):
                shutdown()
        with suppress(RuntimeError, AttributeError):
            widget.close()
        with suppress(RuntimeError):
            widget.deleteLater()
            # Drain only this test-owned receiver. A global flush can delete
            # widgets belonging to module/session fixtures or dispatch queued
            # signals into unrelated, partially torn-down objects.
            QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)


@pytest.fixture(scope="module")
def mock_adapter() -> MockAdapter:
    return MockAdapter()


@pytest.fixture
def faux_adapter() -> Callable[..., FauxAdapter]:
    """Factory for a scriptable multi-turn LLM mock.

    Returns a constructor bound to a ``FauxStep`` list so each test builds its
    own deterministic response/error sequence without a shared module-scoped
    instance. See :mod:`tests.helpers.faux_adapter`.
    """
    return FauxAdapter


@pytest.fixture
def faux_tts_adapter() -> Callable[..., FauxTTSAdapter]:
    """Factory for a scriptable multi-turn TTS mock.

    Returns a constructor accepting per-channel ``synthesize_steps`` /
    ``clone_steps`` / ``design_steps`` lists. See
    :mod:`tests.helpers.faux_tts_adapter`.
    """
    return FauxTTSAdapter


@pytest.fixture(scope="module")
def router(mock_adapter: MockAdapter) -> ModelRouter:
    return ModelRouter(
        adapters={"mock": mock_adapter},
        default_provider="mock",
    )


@pytest.fixture(scope="module")
def builder() -> PromptBuilder:
    return PromptBuilder()


@pytest.fixture
def runtime_settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def tmp_storage(tmp_path: Path) -> FileSystemStorage:
    return FileSystemStorage(tmp_path)


@pytest.fixture(scope="module")
def sample_spec() -> StorySpec:
    return StorySpec(
        title="测试故事",
        genre="fantasy",
        theme="勇气与牺牲",
        tone="epic",
        length_target=3000,
        language="zh",
        characters_hint="一位年轻的魔法师",
        world_hint="中世纪奇幻世界",
    )


@pytest.fixture(scope="module")
def sample_canon_state() -> StoryKernel:
    return StoryKernel(
        project_id="test_project",
        current_chapter=1,
        entities=[
            Entity(
                entity_id="char_林远",
                name="林远",
                entity_type="character",
                status="active",
                attributes={
                    "location": "雾霭小镇",
                    "emotional_state": "困惑",
                    "inventory": ["铜质怀表"],
                },
                last_seen_chapter=1,
            ),
            Entity(
                entity_id="char_老守夜人",
                name="老守夜人",
                entity_type="character",
                status="active",
                attributes={
                    "location": "钟楼",
                    "emotional_state": "神秘",
                },
                last_seen_chapter=1,
            ),
            Entity(
                entity_id="char_已故角色",
                name="已故角色",
                entity_type="character",
                status="destroyed",
                attributes={"location": "墓地"},
                last_seen_chapter=1,
            ),
        ],
        timeline=[
            TimelineAnchor(
                anchor_id="evt_1",
                chapter=1,
                event="林远在雾霭小镇失忆醒来",
                characters_involved=["char_林远"],
                in_story_time="第一天清晨",
            ),
        ],
        promise_ledger=[
            PromiseLedger(
                entry_id="fs_watch",
                description="怀表停在午夜十二点",
                promise_type="foreshadow",
                planted_chapter=1,
                status="planted",
            ),
        ],
        world_rules=[],
        chapter_summaries={1: "林远失忆醒来，在钟楼遇到老守夜人。"},
    )


@pytest.fixture
def humanize_test_library(tmp_path: Path):
    """Seed a humanize library with builtin entries + 3 sample user entries for tests."""
    from novel_forge.core.schemas.humanize_library import HumanizeLibraryEntry
    from novel_forge.memory.humanize_library_store import (
        HumanizeLibrary,
        seed_builtin_patterns,
    )

    lib = HumanizeLibrary.from_path(tmp_path / "library")
    seed_builtin_patterns(lib)
    for i, name in enumerate(["测试模式A", "测试模式B", "测试模式C"]):
        lib.add(
            HumanizeLibraryEntry(
                pattern_id=f"lib_user_{i:08x}",
                pattern_name=name,
                category="测试分类",
                severity="high" if i < 2 else "medium",
                detection_method="regex",
                source="user",
                keywords=[name, "测试", "AI"],
            )
        )
    yield lib
    lib.close()


@pytest.fixture(scope="module")
def sample_canon_delta() -> ChapterOutcome:
    return ChapterOutcome(
        source_chapter=2,
        character_updates={
            "林远": CharacterState(
                name="林远",
                alive=True,
                location="废弃图书馆",
                emotional_state="坚定",
                inventory=["铜质怀表", "神秘信件"],
            ),
        },
        new_events=[
            TimelineAnchor(
                anchor_id="evt_2",
                chapter=2,
                event="林远进入时间裂缝",
                characters_involved=["char_林远"],
                in_story_time="第一天傍晚",
            ),
        ],
        foreshadowing_updates=[
            PromiseLedger(
                entry_id="fs_watch",
                description="怀表停在午夜十二点",
                promise_type="foreshadow",
                planted_chapter=1,
                status="hinted",
            ),
        ],
        new_world_facts={"裂缝内部": "时间倒流空间"},
        chapter_summary="林远追循信中线索来到废弃图书馆，发现并进入了时间裂缝。",
    )
