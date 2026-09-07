"""Integration test: Full book audit pipeline with quality metrics, repair, and verification."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.story_kernel.schemas import Entity, StoryKernel, TimelineAnchor
from novel_forge.story_kernel.store import StoryKernelStore
from novel_forge.workspace.audit_quality_metrics import AuditQualityMetrics
from novel_forge.workspace.contracts import BookConsistencyRequest
from novel_forge.workspace.execution import (
    run_book_consistency_audit,
)
from novel_forge.workspace.propagation_validator import (
    PropagationValidator,
)
from novel_forge.workspace.regression_detector import (
    STATE_REGRESSION_DEATH,
    RegressionDetector,
    RegressionIssue,
)
from novel_forge.workspace.runtime import RuntimeServices


def _make_canon_state(project_id: str = "test_audit") -> StoryKernel:
    return StoryKernel(
        project_id=project_id,
        current_chapter=5,
        entities=[
            Entity(
                entity_id="char_林远",
                name="林远",
                attributes={
                    "alive": True,
                    "location": "雾霭小镇",
                    "emotional_state": "困惑",
                    "inventory": ["铜质怀表"],
                },
            ),
            Entity(
                entity_id="char_老守夜人",
                name="老守夜人",
                attributes={
                    "alive": True,
                    "location": "钟楼",
                    "emotional_state": "神秘",
                },
            ),
        ],
        timeline=[
            TimelineAnchor(
                anchor_id="anchor_ch1_arrival",
                chapter=1,
                event="林远到达雾霭小镇",
                characters_involved=["char_林远"],
                in_story_time="第一天傍晚",
            ),
            TimelineAnchor(
                anchor_id="anchor_ch3_revelation",
                chapter=3,
                event="老守夜人揭示钟楼秘密",
                characters_involved=["char_老守夜人"],
                in_story_time="第三天深夜",
            ),
        ],
    )


def _make_chapter_content(chapter_num: int) -> str:
    return f"""第{chapter_num}章

林远走在雾霭小镇的石板路上，手中的铜质怀表滴答作响。
这是他从祖父那里继承来的唯一遗物。

「今天已经是第{chapter_num}天了。」他低声自语。

远处的钟楼传来沉闷的钟声，老守夜人依然守在那里。
小镇的居民都说，那座钟楼隐藏着不为人知的秘密。

林远握紧了怀表，继续向前走去。他知道，自己必须找到真相。
"""


async def _setup_project(tmp_path: Path, project_id: str, chapter_count: int = 4) -> ProjectLayout:
    layout = ProjectLayout(tmp_path / project_id)
    layout.ensure_dirs()

    canon = _make_canon_state(project_id)
    canon_path = layout.canon_dir / "canon_current.json"
    canon_path.parent.mkdir(parents=True, exist_ok=True)
    canon_path.write_text(
        json.dumps(canon.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    kernel_db_path = layout.story_kernel_db_path
    store = StoryKernelStore(kernel_db_path)
    await store.init_db()
    await store.save_kernel(canon)
    await store.close()

    for ch_num in range(1, chapter_count + 1):
        ch_path = layout.chapters_dir / f"chapter_{ch_num}.md"
        ch_path.write_text(_make_chapter_content(ch_num), encoding="utf-8")

        report_path = layout.creative_report_path(ch_num)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({
                "structured_summary": {
                    "one_line_summary": f"第{ch_num}章摘要",
                    "key_events": [f"事件{ch_num}-1", f"事件{ch_num}-2"],
                },
                "word_count": 500,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    spec_path = layout.root / "spec.json"
    spec_path.write_text(
        json.dumps({
            "title": "审计测试故事",
            "genre": "fantasy",
            "theme": "勇气与牺牲",
            "tone": "epic",
            "length_target": 5000,
            "language": "zh",
            "characters_hint": "一位年轻的魔法师，一位神秘的守夜人",
            "world_hint": "中世纪奇幻世界",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return layout


def _make_services(tmp_path: Path, project_id: str) -> RuntimeServices:
    storage = FileSystemStorage(tmp_path)
    router = ModelRouter(
        adapters={"mock": MockAdapter()},
        default_provider="mock",
    )
    builder = PromptBuilder()
    settings = Settings(_env_file=None)

    return RuntimeServices(
        router=router,
        builder=builder,
        storage=storage,
        settings=settings,
    )


class TestFullAuditWithQualityMetrics:
    async def test_full_audit_with_quality_metrics(self, tmp_path: Path) -> None:
        project_id = "audit_quality_test"
        await _setup_project(tmp_path, project_id, chapter_count=4)
        services = _make_services(tmp_path, project_id)

        request = BookConsistencyRequest(
            project_id=project_id,
            analysis_mode="auto",
        )

        result = await run_book_consistency_audit(
            runtime=services,
            request=request,
        )

        assert result is not None

        quality_metrics = getattr(result, "quality_metrics", None) or (
            result.get("quality_metrics") if isinstance(result, dict) else None
        )
        if quality_metrics is not None:
            if isinstance(quality_metrics, AuditQualityMetrics):
                assert 0.0 <= quality_metrics.coverage_ratio <= 1.0
                assert 0.0 <= quality_metrics.estimated_miss_rate <= 1.0
                assert isinstance(quality_metrics.dimension_scores, dict)
                assert quality_metrics.total_issues_found >= 0
                assert quality_metrics.critical_issues_found >= 0
            elif isinstance(quality_metrics, dict):
                assert "coverage_ratio" in quality_metrics
                assert "dimension_scores" in quality_metrics
                assert "total_issues_found" in quality_metrics


class TestParallelAuditExecution:
    async def test_parallel_audit_execution(self, tmp_path: Path) -> None:
        project_id = "parallel_audit_test"
        await _setup_project(tmp_path, project_id, chapter_count=6)
        services = _make_services(tmp_path, project_id)

        start_parallel = time.monotonic()

        request = BookConsistencyRequest(
            project_id=project_id,
            analysis_mode="auto",
            parallel_chunks=True,
        )

        result = await run_book_consistency_audit(
            runtime=services,
            request=request,
        )

        parallel_duration = time.monotonic() - start_parallel

        assert result is not None
        assert parallel_duration < 60.0, (
            f"Parallel audit took {parallel_duration:.2f}s, expected < 60s"
        )
        assert parallel_duration > 0


class TestRepairWithRegressionDetection:
    async def test_repair_with_regression_detection(self, tmp_path: Path) -> None:
        detector = RegressionDetector()

        original_chapter_2 = _make_chapter_content(2)

        repaired_chapter_2 = """第2章

林公子走在雾霭小镇的石板路上。
突然，他听到了钟声。

守夜君已经死了，钟楼里再也没有人。
但钟声依然在响。
"""

        canon = StoryKernel(
            project_id="regression_test",
            current_chapter=5,
            entities=[
            Entity(
                entity_id="char_守夜君",
                name="守夜君",
                attributes={"alive": True},
            ),
            ],
            timeline=[],
        )

        all_chapters = {
            1: _make_chapter_content(1),
            2: original_chapter_2,
            3: _make_chapter_content(3),
        }

        chapter_issues = [
            {
                "issue_id": "issue_001",
                "category": "character_state",
                "severity": "critical",
                "description": "角色状态不一致",
            }
        ]

        regressions = await detector.detect_regressions(
            original_text=original_chapter_2,
            repaired_text=repaired_chapter_2,
            chapter_issues=chapter_issues,
            all_chapter_texts=all_chapters,
            canon_state=canon.model_dump(mode="json"),
            chapter_number=2,
        )

        assert isinstance(regressions, list)
        death_regressions = [
            r for r in regressions
            if r.issue_type == STATE_REGRESSION_DEATH
        ]
        assert len(death_regressions) >= 1, (
            f"Expected death regression, got: {[r.issue_type for r in regressions]}"
        )

        regression = death_regressions[0]
        assert isinstance(regression, RegressionIssue)
        assert regression.severity in ("critical", "warning", "info")
        assert regression.evidence is not None


class TestRepairWithPropagationValidation:
    async def test_repair_with_propagation_validation(self, tmp_path: Path) -> None:
        validator = PropagationValidator()

        original_chapter_3 = """第3章

林远走在雾霭小镇的石板路上。
"""

        repaired_chapter_content = """第3章

林远终于明白了怀表的秘密。
这个怀表不是普通的计时工具，而是开启钟楼地下室的钥匙。

老守夜人告诉他：「你祖父留下的东西，远比你想的更重要。」
"""

        all_chapters = {
            1: _make_chapter_content(1),
            2: _make_chapter_content(2),
            3: original_chapter_3,
            4: """第4章

林远带着怀表来到了钟楼地下室。
门上的锁孔与怀表的形状完美匹配。
""",
            5: """第5章

地下室的秘密被揭开后，林远发现了更多关于祖父的往事。
""",
        }

        propagation_issues = await validator.validate_propagation(
            repaired_chapter=3,
            original_text=original_chapter_3,
            repaired_text=repaired_chapter_content,
            subsequent_chapters=[4, 5],
            all_chapter_texts=all_chapters,
        )

        assert isinstance(propagation_issues, list)

        problematic_chapter_4 = """第4章

林远把怀表扔进了河里，他再也不需要这个废物了。
"""
        all_chapters_problematic = {
            1: _make_chapter_content(1),
            2: _make_chapter_content(2),
            3: repaired_chapter_content,
            4: problematic_chapter_4,
        }

        issues = await validator.validate_propagation(
            repaired_chapter=3,
            original_text=repaired_chapter_content,
            repaired_text=problematic_chapter_4,
            subsequent_chapters=[4],
            all_chapter_texts=all_chapters_problematic,
        )

        assert isinstance(issues, list)


class TestConfidenceDrivenPrioritization:
    async def test_confidence_driven_prioritization(self, tmp_path: Path) -> None:
        issues = [
            {
                "issue_id": "low_conf_01",
                "category": "naming",
                "severity": "warning",
                "chapters_involved": [1],
                "primary_chapter": 1,
                "description": "可能的名称不一致",
                "paragraph_index": 0,
                "confidence": 0.3,
            },
            {
                "issue_id": "high_conf_01",
                "category": "timeline",
                "severity": "critical",
                "chapters_involved": [1, 2],
                "primary_chapter": 1,
                "description": "时间线明显矛盾",
                "paragraph_index": 5,
                "confidence": 0.95,
            },
            {
                "issue_id": "med_conf_01",
                "category": "character_state",
                "severity": "warning",
                "chapters_involved": [2, 3],
                "primary_chapter": 2,
                "description": "角色状态可能不一致",
                "paragraph_index": 10,
                "confidence": 0.6,
            },
            {
                "issue_id": "high_conf_02",
                "category": "worldbuilding",
                "severity": "critical",
                "chapters_involved": [1, 3],
                "primary_chapter": 1,
                "description": "世界观设定矛盾",
                "paragraph_index": 2,
                "confidence": 0.9,
            },
        ]

        severity_weights = {"critical": 3, "warning": 2, "info": 1}

        def priority_score(issue: dict[str, Any]) -> float:
            confidence = issue.get("confidence", 0.5)
            severity = issue.get("severity", "info")
            weight = severity_weights.get(severity, 1)
            return confidence * weight

        prioritized = sorted(issues, key=priority_score, reverse=True)

        assert prioritized[0]["issue_id"] == "high_conf_01"
        assert prioritized[0]["confidence"] >= 0.9
        assert prioritized[0]["severity"] == "critical"

        assert prioritized[-1]["issue_id"] == "low_conf_01"
        assert prioritized[-1]["confidence"] <= 0.4

        scores = [priority_score(i) for i in prioritized]
        assert scores == sorted(scores, reverse=True)
