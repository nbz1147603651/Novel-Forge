"""Tests for unified memory panel presenter behavior."""

from __future__ import annotations

from PySide6.QtCore import QObject

from novel_forge.desktop.pages.standalone.memory_panel import (
    UnifiedMemoryPanelPresenter,
    _resolve_causal_status_hint,
)


class _SignalStub:
    def connect(self, _callback) -> None:  # noqa: ANN001
        return None


class _StoreStub:
    def __init__(self) -> None:
        self.audit_result_changed = _SignalStub()
        self.memory_status_changed = _SignalStub()

    def memory_status(self, _project_id: str):  # noqa: ANN001
        return None

    def audit_result(self, _project_id: str, _chapter: int):  # noqa: ANN001
        return None


class _BadgeStub:
    def __init__(self) -> None:
        self.memory_status = ""
        self.text = ""

    def set_memory_status(self, value: str) -> None:
        self.memory_status = value

    def setText(self, value: str) -> None:
        self.text = value


class _PanelStub(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.suggestions_text = ""
        self.unresolved_text = ""
        self.relations_text = ""
        self.motif_updates: list[tuple[list[dict], list[dict], list[dict]]] = []
        self.empty_motifs_count = 0
        self.continuity_updates = 0
        self.continuity_clears = 0

    def update_motifs(self, motifs, suggestions, warnings) -> None:  # noqa: ANN001
        self.motif_updates.append((list(motifs), list(suggestions), list(warnings)))

    def set_empty_motifs(self) -> None:
        self.empty_motifs_count += 1

    def update_relations(self, text: str) -> None:
        self.relations_text = text

    def update_suggestions(self, text: str) -> None:
        self.suggestions_text = text

    def update_unresolved(self, text: str) -> None:
        self.unresolved_text = text

    def update_continuity_issues(self, _issues, **_kwargs) -> None:  # noqa: ANN001
        self.continuity_updates += 1

    def update_causal_issues(self, _issues, **_kwargs) -> None:  # noqa: ANN001
        return None

    def set_empty_continuity(self) -> None:
        self.continuity_clears += 1

    def update_scores(self, _text: str) -> None:
        return None

    def update_checkpoint(self, _text: str) -> None:
        return None

    def update_carry_forward(self, _text: str) -> None:
        return None


def test_unresolved_questions_do_not_override_continuity_checklist() -> None:
    panel = _PanelStub()
    presenter = UnifiedMemoryPanelPresenter(
        unified_panel=panel,
        memory_badge=_BadgeStub(),
        store=_StoreStub(),
    )
    presenter.bind_project("demo-project", 1)

    presenter.update_memory_status(
        {
            "indexed_chapters": 6,
            "motifs": [],
            "motif_suggestions": [],
            "repetition_warnings": [],
            "unresolved_questions": [
                "第36章悬念: 最终战役（黑石堡之战）",
            ],
            "outline_stats": {
                "total_relationships_tracked": 12,
                "total_themes_tracked": 4,
                "unresolved_questions": 1,
            },
        }
    )

    assert panel.continuity_updates == 0
    assert panel.continuity_clears == 1
    assert "全书悬念" in panel.unresolved_text
    assert "第36章悬念" in panel.unresolved_text


def test_missing_audit_result_shows_empty_continuity() -> None:
    panel = _PanelStub()
    presenter = UnifiedMemoryPanelPresenter(
        unified_panel=panel,
        memory_badge=_BadgeStub(),
        store=_StoreStub(),
    )

    presenter.bind_project("demo-project", 1)

    assert panel.continuity_clears == 1
    assert panel.continuity_updates == 0


def test_audit_result_without_critique_shows_empty_continuity() -> None:
    panel = _PanelStub()
    presenter = UnifiedMemoryPanelPresenter(
        unified_panel=panel,
        memory_badge=_BadgeStub(),
        store=_StoreStub(),
    )
    presenter.bind_project("demo-project", 1)

    presenter.update_from_audit_result(
        {
            "continuity_score": 10.0,
            "alignment_score": 9.0,
            "issue_count": 0,
        }
    )

    assert panel.continuity_clears == 2
    assert panel.continuity_updates == 0


def test_low_score_audit_without_critique_preserves_report_issue_list() -> None:
    panel = _PanelStub()
    presenter = UnifiedMemoryPanelPresenter(
        unified_panel=panel,
        memory_badge=_BadgeStub(),
        store=_StoreStub(),
    )
    presenter.bind_project("demo-project", 1)

    presenter.update_from_audit_result(
        {
            "continuity_score": 5.5,
            "alignment_score": 9.0,
            "issue_count": 3,
        }
    )

    assert panel.continuity_clears == 1
    assert panel.continuity_updates == 0


def test_character_issues_do_not_override_relations_card() -> None:
    panel = _PanelStub()
    presenter = UnifiedMemoryPanelPresenter(
        unified_panel=panel,
        memory_badge=_BadgeStub(),
        store=_StoreStub(),
    )
    presenter.bind_project("demo-project", 1)
    presenter.update_relations("关系图谱")

    presenter.update_from_audit_result(
        {
            "continuity_score": 8.0,
            "issue_count": 1,
            "critique": {
                "issues": [
                    {
                        "category": "character_consistency",
                        "severity": "high",
                        "summary": "人物语气前后不一致",
                    }
                ],
                "warnings": [],
                "metadata": {},
            },
        }
    )

    assert panel.relations_text == "关系图谱"


def test_empty_audit_suggestions_clear_previous_text() -> None:
    panel = _PanelStub()
    presenter = UnifiedMemoryPanelPresenter(
        unified_panel=panel,
        memory_badge=_BadgeStub(),
        store=_StoreStub(),
    )
    presenter.bind_project("demo-project", 1)
    presenter.update_suggestions("旧提示")

    presenter.update_from_audit_result(
        {
            "continuity_score": 9.0,
            "issue_count": 1,
            "critique": {
                "issues": [
                    {
                        "category": "continuity",
                        "severity": "low",
                        "summary": "轻微衔接问题",
                    }
                ],
                "warnings": [],
                "metadata": {},
            },
        }
    )

    assert panel.suggestions_text == ""


def test_motifs_show_recent_window_by_default() -> None:
    panel = _PanelStub()
    badge = _BadgeStub()
    presenter = UnifiedMemoryPanelPresenter(
        unified_panel=panel,
        memory_badge=badge,
        store=_StoreStub(),
    )
    presenter.bind_project("demo-project", 5)

    presenter.update_memory_status(
        {
            "indexed_chapters": 5,
            "motifs": [
                # Placeholder motif (never appeared): should be hidden.
                {
                    "motif_id": "placeholder",
                    "category": "意象",
                    "occurrence_count": 0,
                    "first_chapter": 0,
                    "last_chapter": 0,
                },
                # Active motif near current chapter.
                {
                    "motif_id": "active_motif",
                    "category": "主题",
                    "occurrence_count": 1,
                    "first_chapter": 4,
                    "last_chapter": 5,
                },
                # Recall motif in lookback window (chapter 4 for current chapter 5).
                {
                    "motif_id": "recall_motif",
                    "category": "动作",
                    "occurrence_count": 3,
                    "first_chapter": 1,
                    "last_chapter": 4,
                },
                # Old low-frequency motif: outside default related window.
                {
                    "motif_id": "stale_motif",
                    "category": "意象",
                    "occurrence_count": 1,
                    "first_chapter": 1,
                    "last_chapter": 1,
                },
            ],
            "motif_suggestions": [
                {"motif_id": "active_motif", "suggestion": "继续强化"},
                {"motif_id": "recall_motif", "suggestion": "可做回收呼应"},
                {"motif_id": "stale_motif", "suggestion": "不应显示"},
            ],
            "repetition_warnings": [
                {"motif_id": "recall_motif", "message": "可回收"},
                {"motif_id": "stale_motif", "message": "不应显示"},
            ],
        }
    )

    assert panel.empty_motifs_count == 0
    assert len(panel.motif_updates) == 1
    motifs, suggestions, warnings = panel.motif_updates[0]
    assert [m.get("motif_id") for m in motifs] == ["active_motif", "recall_motif"]
    assert [s.get("motif_id") for s in suggestions] == ["active_motif", "recall_motif"]
    assert [w.get("motif_id") for w in warnings] == ["recall_motif"]
    assert badge.text == "母题 相关2 · 总3"


def test_motif_filter_uses_chapter_motif_index_for_past_chapters() -> None:
    panel = _PanelStub()
    badge = _BadgeStub()
    presenter = UnifiedMemoryPanelPresenter(
        unified_panel=panel,
        memory_badge=badge,
        store=_StoreStub(),
    )
    presenter.bind_project("demo-project", 22)

    presenter.update_memory_status(
        {
            "indexed_chapters": 26,
            "motifs": [
                {
                    "motif_id": "appears_in_22",
                    "category": "意象",
                    "occurrence_count": 5,
                    "first_chapter": 1,
                    "last_chapter": 26,
                },
                {
                    "motif_id": "future_only",
                    "category": "符号",
                    "occurrence_count": 1,
                    "first_chapter": 24,
                    "last_chapter": 26,
                },
            ],
            "chapter_motifs": {
                "22": ["appears_in_22"],
                "26": ["appears_in_22", "future_only"],
            },
            "motif_suggestions": [
                {"motif_id": "appears_in_22", "suggestion": "可呼应"},
                {"motif_id": "future_only", "suggestion": "不应显示"},
            ],
            "repetition_warnings": [],
        }
    )

    assert len(panel.motif_updates) == 1
    motifs, suggestions, _warnings = panel.motif_updates[0]
    assert [m.get("motif_id") for m in motifs] == ["appears_in_22"]
    assert motifs[0]["last_chapter"] == 22
    assert [s.get("motif_id") for s in suggestions] == ["appears_in_22"]
    assert badge.text == "母题 1"


def test_resolve_causal_status_hint_prioritizes_stale_warning() -> None:
    class _Studio:
        warnings = ["因果校验报告未绑定正文版本，当前问题可能来自旧版文本，建议重新评估。"]

    hint = _resolve_causal_status_hint(
        _Studio(),
        [{"severity": "low", "summary": "开头承接偏弱"}],
    )

    assert "未绑定正文版本" in hint


def test_resolve_causal_status_hint_explains_low_priority_residuals() -> None:
    class _Studio:
        warnings: list[str] = []

    hint = _resolve_causal_status_hint(
        _Studio(),
        [
            {"severity": "low", "summary": "开头承接偏弱"},
            {"severity": "medium", "summary": "动作衔接可加强"},
        ],
    )

    assert "中低优先级" in hint
