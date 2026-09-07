"""Integration: humanize layer with corrupt library database.

When the library DB is corrupt, ``HumanizeLibrary.from_default_path()``
raises.  The layer must catch the exception, emit a degraded event,
and continue with empty library_hits — the 21 hard-rule scan still runs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from novel_forge.core.schemas.humanize import HumanizeReport
from novel_forge.memory.humanize_library_store import LibraryUnavailableError
from novel_forge.pipeline.long.stages.humanize_layer import run_humanize_layer
from novel_forge.pipeline.steps.humanize_scan_step import (
    HumanizeScanInput,
    HumanizeScanResult,
    HumanizeScanStep,
)

_LONG_TEXT = (
    "林远站在钟楼的阴影里，手里的怀表发出微弱的磷光。这是第三个午夜了，"
    "裂缝在他的噩梦里越变越大。他闭上眼睛深吸一口气，推开了通往图书馆的木门。"
    "灰尘扑面而来。书架的缝隙间露出微光，那道裂缝就在深处等待着。"
    "他对着黑暗轻声说了一句没有意义的话，算是给自己的勇气打气。"
    "然后他迈开步子，走进那个连时间都会迷路的房间。"
    "远处的钟声在此刻敲响，不多不少正好十二下。他的脚步没有停顿。"
    "走廊尽头，古旧的铜质门把手在月光下反射出暗红的光晕。他握紧它，"
    "冰凉的触感让他打了个冷颤，但也让混沌的头脑清明了几分。"
    "门后传来纸张翻动的声音。他屏住呼吸，轻轻推开了门。"
    "书房里空无一人，但桌上摊着一本打开到第七十二页的笔记，"
    "墨迹未干。他走近细看，笔记上的字迹和他自己的笔迹一模一样。"
    "那一瞬间，怀表开始逆时针转动。"
)


@pytest.fixture
def mock_runner():
    runner = MagicMock()
    runner._router = MagicMock()
    runner._builder = MagicMock()
    s = MagicMock()
    s.humanize_enabled = True
    s.humanize_min_text_length = 100
    s.humanize_model = ""
    s.humanize_change_ratio_cap = 0.05
    s.humanize_library_enabled = True
    s.humanize_library_top_k = 30
    runner._settings = s
    return runner


@pytest.fixture
def mock_bundle():
    bundle = MagicMock()
    bundle.style_profile = None
    outline = MagicMock()
    outline.pov_character = "林远"
    outline.required_characters = ["林远"]
    bundle.chapter_outline = outline
    return bundle


async def test_corrupt_library_degrades_gracefully(
    mock_runner: MagicMock,
    mock_bundle: MagicMock,
) -> None:
    """Corrupt library DB → degraded event, empty hits, scan still runs."""
    empty_report = HumanizeReport(chapter_number=1)
    scan_mock = AsyncMock(
        return_value=HumanizeScanResult(
            report=empty_report,
            revised_text=_LONG_TEXT,
            patches_applied=0,
        )
    )

    with (
        patch.object(HumanizeScanStep, "run", scan_mock),
        patch(
            "novel_forge.memory.humanize_library_store.HumanizeLibrary.from_default_path",
            side_effect=LibraryUnavailableError("corrupt database"),
        ),
    ):
        result = await run_humanize_layer(
            mock_runner,
            mock_bundle,
            MagicMock(),
            _LONG_TEXT,
            chapter_number=1,
            trace=MagicMock(),
        )

    assert result.current_text == _LONG_TEXT
    assert result.patches_applied == 0

    scan_input: HumanizeScanInput = scan_mock.call_args.args[0]
    assert scan_input.library_enabled is True
    assert scan_input.library_hits == []
