"""Regression tests for the desktop pipeline step indicator."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QSizePolicy  # noqa: E402

from novel_forge.desktop.widgets import (  # noqa: E402
    PipelineStep,
    StepIndicatorRow,
    StepIndicatorState,
)
from novel_forge.pipeline.progress import resolve_step_key, summary_steps  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture(autouse=True)
def _cleanup_qwidgets(qapp: QApplication) -> None:
    before = set(qapp.topLevelWidgets())
    yield
    for widget in list(qapp.topLevelWidgets()):
        if widget in before:
            continue
        try:
            widget.close()
            widget.deleteLater()
        except RuntimeError:
            continue
    qapp.processEvents()


def _states(row: StepIndicatorRow) -> list[str]:
    return [str(dot.property("state")) for dot in row._dot_labels]


def test_apply_state_renders_resolved_states_and_keeps_only_done_clickable(
    qapp: QApplication,
) -> None:
    row = StepIndicatorRow(
        [
            PipelineStep("spec", "规格确认"),
            PipelineStep("draft", "初稿"),
            PipelineStep("check", "检查"),
            PipelineStep("skip", "跳过"),
            PipelineStep("persist", "归档"),
        ]
    )
    row.apply_state(
        StepIndicatorState(("done", "active", "failed", "skipped", "pending"))
    )

    assert _states(row) == ["done", "active", "failed", "skipped", "pending"]
    assert row._dot_labels[0].toolTip() == "点击查看该步骤产出文件"
    assert row._dot_labels[1].toolTip() == ""
    assert row._dot_labels[2].toolTip() == "该步骤执行失败，未生成可查看产物"
    assert row._dot_labels[3].toolTip() == "该步骤已跳过，未生成可查看产物"
    assert row._dot_labels[4].toolTip() == ""

    clicked: list[int] = []
    row.step_clicked.connect(clicked.append)
    row.show()
    qapp.processEvents()

    QTest.mouseClick(row._dot_labels[0], Qt.MouseButton.LeftButton)
    QTest.mouseClick(row._dot_labels[1], Qt.MouseButton.LeftButton)
    QTest.mouseClick(row._dot_labels[2], Qt.MouseButton.LeftButton)
    QTest.mouseClick(row._dot_labels[3], Qt.MouseButton.LeftButton)
    QTest.mouseClick(row._dot_labels[4], Qt.MouseButton.LeftButton)

    assert clicked == [0]


def test_done_steps_without_artifacts_are_not_clickable(qapp: QApplication) -> None:
    row = StepIndicatorRow(
        [
            PipelineStep("plan", "方案"),
            PipelineStep("state", "状态"),
        ],
        clickable_indices={0},
    )
    row.apply_state(StepIndicatorState(("done", "done")))

    assert row._dot_labels[0].toolTip() == "点击查看该步骤产出文件"
    assert row._dot_labels[1].toolTip() == "该步骤暂无已落盘产物"

    clicked: list[int] = []
    row.step_clicked.connect(clicked.append)
    row.show()
    qapp.processEvents()

    QTest.mouseClick(row._dot_labels[0], Qt.MouseButton.LeftButton)
    QTest.mouseClick(row._dot_labels[1], Qt.MouseButton.LeftButton)

    assert clicked == [0]


def test_parallel_sibling_stays_done_after_next_sequential_step(qapp: QApplication) -> None:
    row = StepIndicatorRow(
        [
            PipelineStep("spec", "规格确认"),
            PipelineStep("world", "世界观"),
            PipelineStep("elements", "要素"),
            PipelineStep("characters", "角色"),
        ],
        parallel_pairs=[("world", "elements")],
    )

    row.update_step("spec")
    row.update_step("world")
    assert _states(row) == ["done", "active", "active", "pending"]

    row.update_step("characters")

    assert _states(row) == ["done", "done", "done", "active"]


def test_overlapping_parallel_pairs_are_merged_for_three_way_tasks(
    qapp: QApplication,
) -> None:
    row = StepIndicatorRow(
        [
            PipelineStep("evaluate", "质量评估"),
            PipelineStep("continuity", "连贯性"),
            PipelineStep("causal", "因果链"),
            PipelineStep("report", "报告"),
        ],
        parallel_pairs=[
            ("evaluate", "continuity"),
            ("evaluate", "causal"),
        ],
    )

    row.update_step("continuity")
    assert _states(row) == ["active", "active", "active", "pending"]

    row.update_step("report")

    assert _states(row) == ["done", "done", "done", "active"]


def test_sequential_step_backward_clears_later_running(qapp: QApplication) -> None:
    row = StepIndicatorRow(
        [
            PipelineStep("plan_checkpoint", "方案确认"),
            PipelineStep("draft", "初稿生成"),
            PipelineStep("alignment", "质量检查"),
            PipelineStep("persist", "最终归档"),
        ]
    )

    row.update_step("plan_checkpoint")
    row.update_step("draft")
    row.update_step("alignment")
    row.update_step("persist")
    assert _states(row) == ["done", "done", "done", "active"]

    row.update_step("draft")
    assert _states(row) == ["done", "active", "pending", "pending"]


def test_pre_alignment_retry_clears_stale_alignment_done(qapp: QApplication) -> None:
    row = StepIndicatorRow(
        [
            PipelineStep("plan_checkpoint", "方案确认"),
            PipelineStep("draft", "初稿生成"),
            PipelineStep("pre_alignment", "审前检查"),
            PipelineStep("alignment", "对齐检查"),
            PipelineStep("post_alignment", "文本精修"),
        ]
    )

    row.update_step("plan_checkpoint")
    row.update_step("draft")
    row.update_step("alignment")
    assert _states(row) == ["done", "done", "pending", "active", "pending"]

    row.update_step("pre_alignment")

    assert _states(row) == ["done", "done", "active", "pending", "pending"]


def test_mark_reached_at_implies_prior_milestones_after_truncated_history(
    qapp: QApplication,
) -> None:
    row = StepIndicatorRow(
        [
            PipelineStep("spec", "规格确认"),
            PipelineStep("world", "世界观"),
            PipelineStep("blueprint", "蓝图"),
            PipelineStep("outline", "大纲"),
        ]
    )

    row.update_step("blueprint")
    assert _states(row) == ["pending", "pending", "active", "pending"]

    row.mark_reached_at("outline")

    assert _states(row) == ["done", "done", "done", "active"]


@pytest.mark.parametrize(
    ("kind", "events"),
    [
        (
            "repair_continuity",
            (
                "repair_continuity_start",
                "continuity_eval_after_repair_start",
                "continuity_eval_after_repair",
                "repair_continuity",
            ),
        ),
        (
            "repair_causal",
            (
                "repair_causal_start",
                "causal_eval_after_repair_start",
                "causal_eval_after_repair",
                "repair_causal",
            ),
        ),
    ],
)
def test_targeted_repair_event_order_keeps_recheck_done_at_final_result(
    qapp: QApplication,
    kind: str,
    events: tuple[str, ...],
) -> None:
    steps = [
        PipelineStep(step.key, step.label, step.is_prefix)
        for step in summary_steps(kind)
    ]
    row = StepIndicatorRow(steps)

    for raw_step in events:
        row.update_step(resolve_step_key(kind, raw_step))

    assert _states(row) == ["done", "done", "active"]


def test_combined_repair_postprocess_does_not_rewind_causal_stage(qapp: QApplication) -> None:
    steps = [
        PipelineStep(step.key, step.label, step.is_prefix)
        for step in summary_steps("repair_issues")
    ]
    row = StepIndicatorRow(steps)

    for raw_step in (
        "repair_continuity_start",
        "repair_continuity",
        "repair_causal_start",
        "chapter_postprocess",
    ):
        row.update_step(resolve_step_key("repair_issues", raw_step))

    assert _states(row) == ["done", "active", "pending"]


def test_step_indicator_uses_single_line_elided_labels_and_round_dots(
    qapp: QApplication,
) -> None:
    row = StepIndicatorRow(
        [
            PipelineStep("plan_checkpoint", "这是一个非常长的步骤名称"),
            PipelineStep("draft", "另一个很长的步骤名称"),
        ]
    )

    assert all(not label.wordWrap() for label in row._step_labels)
    assert all(dot.text() == "" for dot in row._dot_labels)
    assert row._step_labels[0].toolTip() == "这是一个非常长的步骤名称"


def test_step_indicator_can_shrink_in_narrow_task_flow(qapp: QApplication) -> None:
    steps = [
        PipelineStep(f"step_{index}", "非常长的初始化阶段名称")
        for index in range(12)
    ]
    row = StepIndicatorRow(steps)
    row.resize(320, 64)
    row.show()
    qapp.processEvents()

    assert row.minimumSizeHint().width() <= 320
    assert all(label.minimumWidth() == 0 for label in row._step_labels)
    assert all(label.minimumSizeHint().width() == 0 for label in row._step_labels)
    assert all(
        label.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Preferred
        for label in row._step_labels
    )


def test_compact_step_indicator_keeps_labels_readable_on_wide_cards(
    qapp: QApplication,
) -> None:
    steps = [
        PipelineStep(f"step_{index}", label)
        for index, label in enumerate(
            [
                "规格确认",
                "世界观设定",
                "要素选择",
                "角色设定",
                "角色审计",
                "风格规范",
                "实体注册表",
                "实体图谱",
                "创作导演",
                "叙事蓝图",
                "蓝图验证",
                "章节设计矩阵",
                "章节大纲",
                "契约裁判",
            ]
        )
    ]
    row = StepIndicatorRow(steps)
    row.resize(1300, 64)
    row.show()
    qapp.processEvents()

    rendered = [label.text() for label in row._step_labels]
    assert all(text and text not in {"...", "…"} for text in rendered)
    assert min(label.width() for label in row._step_labels) >= 48
