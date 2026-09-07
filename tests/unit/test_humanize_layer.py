"""Tests for the humanize layer orchestration (run_humanize_layer).

Covers: config gate, text-length gate, scan step (clean & AI text),
change-ratio enforcement, semantic-drift rollback, and gateway error handling.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.humanize import HumanizeReport
from novel_forge.pipeline.long.stages.humanize_layer import run_humanize_layer
from novel_forge.pipeline.steps.humanize_scan_step import (
    HumanizeScanResult,
    HumanizeScanStep,
)
from novel_forge.prompts.builder import PromptBuilder

# ── Test text helpers ────────────────────────────────────────────────────────

_LONG_TEXT = (
    "林远站在钟楼的阴影里，林远手里的怀表发出微弱的磷光。这是第三个午夜了，"
    "裂缝在林远的噩梦里越变越大。林远闭上眼睛深吸一口气，推开了通往图书馆的木门。"
    "灰尘扑面而来。书架的缝隙间露出微光，那道裂缝就在深处等待着。"
    "林远对着黑暗轻声说了一句没有意义的话，林远算是给自己的勇气打气。"
    "然后林远迈开步子，走进那个连时间都会迷路的房间。"
    "远处的钟声在此刻敲响，不多不少正好十二下。林远的脚步没有停顿。"
    "走廊尽头，古旧的铜质门把手在月光下反射出暗红的光晕。林远握紧它，"
    "冰凉的触感让林远打了个冷颤，但也让混沌的头脑清明了几分。"
    "门后传来纸张翻动的声音。林远屏住呼吸，轻轻推开了门。"
    "书房里空无一人，但桌上摊着一本打开到第七十二页的笔记，"
    "墨迹未干。林远走近细看，笔记上的字迹和他自己的笔迹一模一样。"
    "那一瞬间，怀表开始逆时针转动。"
)
"""Sufficiently long Chinese text containing ``林远`` 11 times (>500 chars).

Uses a single, natural paragraph (no repetition) so that ``SequenceMatcher``
computes meaningful similarity ratios for change-ratio and drift detection.
"""


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_runner(router, builder):
    """Create a mock chapter runner with router, builder, and controllable settings."""
    runner = MagicMock()
    runner._router = router
    runner._builder = builder

    # Default settings — individual tests can override
    s = MagicMock()
    s.humanize_enabled = True
    s.humanize_min_text_length = 100  # low enough for the ~550-char test text
    s.humanize_model = ""
    s.task_routing = {}
    s.humanize_change_ratio_cap = 0.05
    runner._settings = s
    return runner


@pytest.fixture
def mock_bundle():
    """Create a mock bundle with style_profile and chapter_outline."""
    bundle = MagicMock()
    bundle.style_profile = None
    outline = MagicMock()
    outline.pov_character = "林远"
    outline.required_characters = ["林远"]
    bundle.chapter_outline = outline
    return bundle


@pytest.fixture
def mock_packet():
    """Minimal mock for the chapter state packet."""
    return MagicMock()


@pytest.fixture
def mock_trace():
    """Minimal mock for the pipeline trace."""
    return MagicMock()


# ── Test cases ───────────────────────────────────────────────────────────────


def test_humanize_scan_route_override_parses_provider_model(mock_runner: MagicMock) -> None:
    mock_runner._settings.humanize_model = "deepseek:deepseek-chat"
    step = HumanizeScanStep(
        mock_runner._router,
        mock_runner._builder,
        settings=mock_runner._settings,
        trace=MagicMock(),
    )

    assert step._humanize_route_override() == ("deepseek", "deepseek-chat")


def test_humanize_prompt_forbids_knowledge_and_fact_changes() -> None:
    prompt = PromptBuilder().render(
        TaskType.HUMANIZE_SCAN,
        {
            "chapter_number": 1,
            "chapter_text": "林远推开门，确认裂缝还在。",
            "humanize_strength": "medium",
        },
    )

    assert "不改变事实" in prompt
    assert "角色认知" in prompt
    # Note: "知识揭示顺序" was removed from the template in a refactor;
    # the constraint is now expressed as "不改变事实、因果、角色认知、POV、时间线"


async def test_humanize_config_disabled(
    mock_runner: MagicMock,
    mock_bundle: MagicMock,
    mock_packet: MagicMock,
    mock_trace: MagicMock,
) -> None:
    """humanize_enabled=False → layer is skipped entirely."""
    mock_runner._settings.humanize_enabled = False

    result = await run_humanize_layer(
        mock_runner,
        mock_bundle,
        mock_packet,
        _LONG_TEXT,
        chapter_number=1,
        trace=mock_trace,
    )

    assert result.current_text == _LONG_TEXT
    assert result.report is None
    assert result.patches_applied == 0
    assert result.skipped_reason == "disabled"


async def test_humanize_text_too_short(
    mock_runner: MagicMock,
    mock_bundle: MagicMock,
    mock_packet: MagicMock,
    mock_trace: MagicMock,
) -> None:
    """Text shorter than humanize_min_text_length → layer is skipped."""
    short_text = "太短的文本"
    mock_runner._settings.humanize_min_text_length = 500

    result = await run_humanize_layer(
        mock_runner,
        mock_bundle,
        mock_packet,
        short_text,
        chapter_number=1,
        trace=mock_trace,
    )

    assert result.current_text == short_text
    assert result.patches_applied == 0
    assert result.skipped_reason == "text_too_short"


async def test_humanize_scan_clean_text(
    mock_runner: MagicMock,
    mock_bundle: MagicMock,
    mock_packet: MagicMock,
    mock_trace: MagicMock,
) -> None:
    """Clean text (no AI pattern hits) passes through unchanged."""
    empty_report = HumanizeReport(chapter_number=1)

    with patch.object(
        HumanizeScanStep,
        "run",
        AsyncMock(
            return_value=HumanizeScanResult(
                report=empty_report,
                revised_text=_LONG_TEXT,
                patches_applied=0,
            )
        ),
    ):
        result = await run_humanize_layer(
            mock_runner,
            mock_bundle,
            mock_packet,
            _LONG_TEXT,
            chapter_number=1,
            trace=mock_trace,
        )

    assert result.current_text == _LONG_TEXT
    assert result.patches_applied == 0
    assert result.skipped_reason == ""  # ran normally, no patches needed


async def test_humanize_context_uses_expression_channel_focus(
    mock_runner: MagicMock,
    mock_bundle: MagicMock,
    mock_packet: MagicMock,
    mock_trace: MagicMock,
) -> None:
    empty_report = HumanizeReport(chapter_number=1)
    run_mock = AsyncMock(
        return_value=HumanizeScanResult(
            report=empty_report,
            revised_text=_LONG_TEXT,
            patches_applied=0,
        )
    )

    with patch.object(HumanizeScanStep, "run", run_mock):
        await run_humanize_layer(
            mock_runner,
            mock_bundle,
            mock_packet,
            _LONG_TEXT,
            chapter_number=1,
            trace=mock_trace,
            expression_channel_records=[
                {"text": "心头/胸口惊动式反应"},
                {"text": "握紧类动作标签"},
            ],
        )

    input_data = run_mock.call_args.args[0]
    assert input_data.context_capsule["focus_patterns"][:2] == [
        "心头/胸口惊动式反应",
        "握紧类动作标签",
    ]


async def test_humanize_library_similarity_threshold_reaches_retriever(
    mock_runner: MagicMock,
    mock_bundle: MagicMock,
    mock_packet: MagicMock,
    mock_trace: MagicMock,
) -> None:
    mock_runner._settings.humanize_library_enabled = True
    mock_runner._settings.humanize_library_top_k = 12
    mock_runner._settings.humanize_library_sim_threshold = 0.42
    empty_report = HumanizeReport(chapter_number=1)
    run_mock = AsyncMock(
        return_value=HumanizeScanResult(
            report=empty_report,
            revised_text=_LONG_TEXT,
            patches_applied=0,
        )
    )
    retriever_instance = MagicMock()
    retriever_instance.retrieve.return_value = []

    with (
        patch.object(HumanizeScanStep, "run", run_mock),
        patch(
            "novel_forge.memory.humanize_library_store.HumanizeLibrary.from_default_path",
            return_value=MagicMock(),
        ),
        patch("novel_forge.memory.humanize_library_store.seed_builtin_patterns"),
        patch(
            "novel_forge.memory.humanize_retrieval.HumanizeLibraryRetriever",
            return_value=retriever_instance,
        ) as retriever_ctor,
    ):
        await run_humanize_layer(
            mock_runner,
            mock_bundle,
            mock_packet,
            _LONG_TEXT,
            chapter_number=1,
            trace=mock_trace,
        )

    _, kwargs = retriever_ctor.call_args
    assert kwargs["sim_threshold"] == 0.42
    retriever_instance.retrieve.assert_called_once()


async def test_humanize_scan_ai_text(
    mock_runner: MagicMock,
    mock_bundle: MagicMock,
    mock_packet: MagicMock,
    mock_trace: MagicMock,
) -> None:
    """AI-patterned text gets patches applied and returns revised text.

    Uses a near-identical revised text (one word altered) so the
    change-ratio guard passes and the AI text is accepted.
    """
    revised = _LONG_TEXT.replace("深吸一口气", "长长地吸了一口气")
    report = HumanizeReport(
        chapter_number=1,
        critical_hits=2,
        total_hits=3,
        humanize_score=4.0,
    )

    with patch.object(
        HumanizeScanStep,
        "run",
        AsyncMock(
            return_value=HumanizeScanResult(
                report=report,
                revised_text=revised,
                patches_applied=2,
            )
        ),
    ):
        result = await run_humanize_layer(
            mock_runner,
            mock_bundle,
            mock_packet,
            _LONG_TEXT,
            chapter_number=1,
            trace=mock_trace,
        )

    assert result.patches_applied == 2
    assert result.current_text == revised
    assert result.report is not None
    assert result.report.critical_hits == 2
    assert result.skipped_reason == ""


async def test_humanize_quality_regression_rolls_back_duplicate_phrase(
    mock_runner: MagicMock,
    mock_bundle: MagicMock,
    mock_packet: MagicMock,
    mock_trace: MagicMock,
) -> None:
    revised = _LONG_TEXT.replace("林远的脚步没有停顿", "林远的脚步林远的脚步没有停顿")
    mock_runner._settings.humanize_change_ratio_cap = 0.10

    with patch.object(
        HumanizeScanStep,
        "run",
        AsyncMock(
            return_value=HumanizeScanResult(
                report=HumanizeReport(chapter_number=1),
                revised_text=revised,
                patches_applied=1,
            )
        ),
    ):
        result = await run_humanize_layer(
            mock_runner,
            mock_bundle,
            mock_packet,
            _LONG_TEXT,
            chapter_number=1,
            trace=mock_trace,
        )

    assert result.skipped_reason == "quality_regression"
    assert result.current_text == _LONG_TEXT
    assert result.patches_applied == 0
    assert result.comparison is not None
    assert result.comparison["reason"] == "quality_regression"
    assert result.comparison["metadata"]["quality_regressions"]


async def test_humanize_change_ratio_exceeded(
    mock_runner: MagicMock,
    mock_bundle: MagicMock,
    mock_packet: MagicMock,
    mock_trace: MagicMock,
) -> None:
    """When change ratio exceeds the cap, the layer rolls back to original."""
    revised = "CompletelyDifferentText@" * 30  # nothing in common → ratio ≈ 1.0
    mock_runner._settings.humanize_change_ratio_cap = 0.01

    with patch.object(
        HumanizeScanStep,
        "run",
        AsyncMock(
            return_value=HumanizeScanResult(
                report=HumanizeReport(chapter_number=1, critical_hits=2),
                revised_text=revised,
                patches_applied=3,
            )
        ),
    ):
        result = await run_humanize_layer(
            mock_runner,
            mock_bundle,
            mock_packet,
            _LONG_TEXT,
            chapter_number=1,
            trace=mock_trace,
        )

    assert result.skipped_reason == "change_ratio_exceeded"
    assert result.current_text == _LONG_TEXT  # rolled back
    assert result.patches_applied == 0


async def test_humanize_semantic_drift(
    mock_runner: MagicMock,
    mock_bundle: MagicMock,
    mock_packet: MagicMock,
    mock_trace: MagicMock,
) -> None:
    """POV character disappearance triggers semantic-drift rollback."""
    revised_text = _LONG_TEXT.replace("林远", "他")
    mock_runner._settings.humanize_change_ratio_cap = 0.10

    with patch.object(
        HumanizeScanStep,
        "run",
        AsyncMock(
            return_value=HumanizeScanResult(
                report=HumanizeReport(chapter_number=1, critical_hits=2),
                revised_text=revised_text,
                patches_applied=3,
            )
        ),
    ):
        result = await run_humanize_layer(
            mock_runner,
            mock_bundle,
            mock_packet,
            _LONG_TEXT,
            chapter_number=1,
            trace=mock_trace,
        )

    assert result.skipped_reason == "semantic_drift"
    assert result.current_text == _LONG_TEXT  # rolled back
    assert result.patches_applied == 0


async def test_humanize_gateway_error(
    mock_runner: MagicMock,
    mock_bundle: MagicMock,
    mock_packet: MagicMock,
    mock_trace: MagicMock,
    mock_adapter,
) -> None:
    """LLM gateway error is caught by HumanizeScanStep; original text survives."""
    with patch.object(
        mock_adapter,
        "complete",
        AsyncMock(side_effect=Exception("Gateway timeout")),
    ):
        result = await run_humanize_layer(
            mock_runner,
            mock_bundle,
            mock_packet,
            _LONG_TEXT,
            chapter_number=1,
            trace=mock_trace,
        )

    assert result.current_text == _LONG_TEXT
    assert result.patches_applied == 0
