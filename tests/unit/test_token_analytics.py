"""Tests for project token analytics aggregation and preferences."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QApplication

from novel_forge.desktop.pages.standalone.token_analytics import (
    TokenAnalyticsTab,
    _extract_outline_range,
    _step_tokens_for_filter,
    collect_project_token_analytics,
    estimate_token_cost_cny,
    load_token_dashboard_prefs,
    save_token_dashboard_prefs,
)


def _write_summary(run_dir: Path, payload: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_model_call(run_dir: Path, filename: str, payload: dict) -> None:
    model_calls_dir = run_dir / "model_calls"
    model_calls_dir.mkdir(parents=True, exist_ok=True)
    (model_calls_dir / filename).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def test_collect_project_token_analytics_aggregates_runs_steps_and_models(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo_project"
    logs_dir = project_dir / "logs"

    _write_summary(
        logs_dir / "20260101-run-a",
        {
            "run_id": "run-a",
            "started_at": "2026-01-01T08:00:00Z",
            "status": "success",
            "metadata": {"kind": "init_long"},
            "trace_summary": {
                "total_tokens": 3000,
                "total_prompt_tokens": 1800,
                "total_completion_tokens": 1200,
                "total_cost_usd": 0.03,
                "steps": [
                    {
                        "name": "plan_blueprint",
                        "tokens": 2000,
                        "prompt_tokens": 1200,
                        "completion_tokens": 800,
                        "model_call_count": 1,
                        "cost": 0.02,
                        "model_calls": [
                            {
                                "provider": "openai",
                                "model": "gpt-4o",
                                "prompt_tokens": 1200,
                                "completion_tokens": 800,
                                "total_tokens": 2000,
                                "cost_usd": 0.02,
                            }
                        ],
                    },
                    {
                        "name": "plan_outline_batch_001",
                        "tokens": 1000,
                        "prompt_tokens": 600,
                        "completion_tokens": 400,
                        "model_call_count": 1,
                        "cost": 0.01,
                        "model_calls": [
                            {
                                "provider": "openai",
                                "model": "gpt-4o-mini",
                                "prompt_tokens": 600,
                                "completion_tokens": 400,
                                "total_tokens": 1000,
                                "cost_usd": 0.01,
                            }
                        ],
                    },
                ],
            },
        },
    )

    _write_summary(
        logs_dir / "20260102-run-b",
        {
            "run_id": "run-b",
            "started_at": "2026-01-02T08:00:00Z",
            "status": "success",
            "metadata": {"kind": "run_chapter", "chapter_number": 1},
            "trace_summary": {
                "total_tokens": 5000,
                "total_prompt_tokens": 3000,
                "total_completion_tokens": 2000,
                "total_cost_usd": 0.05,
                "steps": [
                    {
                        "name": "draft",
                        "tokens": 3200,
                        "prompt_tokens": 1900,
                        "completion_tokens": 1300,
                        "model_call_count": 1,
                        "cost": 0.032,
                        "model_calls": [
                            {
                                "provider": "openai",
                                "model": "gpt-4o",
                                "prompt_tokens": 1900,
                                "completion_tokens": 1300,
                                "total_tokens": 3200,
                                "cost_usd": 0.032,
                            }
                        ],
                    },
                    {
                        "name": "evaluate",
                        "tokens": 1800,
                        "prompt_tokens": 1100,
                        "completion_tokens": 700,
                        "model_call_count": 1,
                        "cost": 0.018,
                        "model_calls": [
                            {
                                "provider": "deepseek",
                                "model": "deepseek-chat",
                                "prompt_tokens": 1100,
                                "completion_tokens": 700,
                                "total_tokens": 1800,
                                "cost_usd": 0.018,
                            }
                        ],
                    },
                ],
            },
        },
    )

    stats = collect_project_token_analytics(project_dir)

    assert stats["run_count"] == 2
    assert stats["step_count"] == 4
    assert stats["total_tokens"] == 8000
    assert stats["total_prompt_tokens"] == 4800
    assert stats["total_completion_tokens"] == 3200
    assert stats["logged_cost_usd"] == 0.08

    assert stats["runs"][0]["run_id"] == "run-a"
    assert stats["runs"][1]["chapter"] == 1

    steps = {entry["step"]: entry for entry in stats["steps"]}
    assert steps["draft"]["tokens"] == 3200
    assert steps["plan_blueprint"]["tokens"] == 2000
    assert steps["plan_blueprint"]["kind_tokens"]["init"] == 2000
    assert steps["draft"]["kind_tokens"]["chapter"] == 3200

    models = {entry["label"]: entry for entry in stats["models"]}
    assert models["openai/gpt-4o"]["tokens"] == 5200
    assert models["deepseek/deepseek-chat"]["tokens"] == 1800


def test_token_dashboard_preferences_roundtrip(tmp_path: Path) -> None:
    project_dir = tmp_path / "project_x"
    prefs = load_token_dashboard_prefs(project_dir)
    assert prefs["currency"] == "CNY"
    assert prefs["price_per_million"] == 8.0
    assert isinstance(prefs["model_price_per_million"], dict)

    prefs["currency"] = "USD"
    prefs["price_per_million"] = 9.5
    prefs["model_price_per_million"]["openai/gpt-4o"] = 30.0
    prefs["step_waterfall_filter"] = "repair"
    prefs["exchange_rates"]["USD"] = 0.2
    save_token_dashboard_prefs(project_dir, prefs)

    loaded = load_token_dashboard_prefs(project_dir)
    assert loaded["currency"] == "USD"
    assert loaded["price_per_million"] == 9.5
    assert loaded["model_price_per_million"]["openai/gpt-4o"] == 30.0
    assert loaded["step_waterfall_filter"] == "repair"
    assert loaded["exchange_rates"]["USD"] == 0.2
    assert loaded["exchange_rates"]["CNY"] == 1.0


def test_estimate_token_cost_cny() -> None:
    assert estimate_token_cost_cny(2_500_000, price_per_million=8.0) == 20.0
    assert estimate_token_cost_cny(0, price_per_million=8.0) == 0.0
    assert estimate_token_cost_cny(2000, price_per_million=0.0) == 0.0


def test_collect_project_token_analytics_reads_nested_trace_summary_when_top_level_is_empty(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "nested_trace_project"
    logs_dir = project_dir / "logs"

    _write_summary(
        logs_dir / "20260103-run-c",
        {
            "run_id": "run-c",
            "started_at": "2026-01-03T08:00:00Z",
            "status": "success",
            "metadata": {"kind": "init_long"},
            "trace_summary": {},
            "result": {
                "project_id": "nested_trace_project",
                "result": {
                    "trace_summary": {
                        "total_tokens": 4321,
                        "total_prompt_tokens": 3000,
                        "total_completion_tokens": 1321,
                        "total_cost_usd": 0.04321,
                        "steps": [
                            {
                                "name": "plan_outline_continue_31_35",
                                "tokens": 2500,
                                "prompt_tokens": 1700,
                                "completion_tokens": 800,
                                "model_call_count": 2,
                                "cost": 0.025,
                                "model_calls": [
                                    {
                                        "provider": "deepseek",
                                        "model": "deepseek-reasoner",
                                        "prompt_tokens": 1700,
                                        "completion_tokens": 800,
                                        "total_tokens": 2500,
                                        "cost_usd": 0.025,
                                    }
                                ],
                            },
                            {
                                "name": "plan_outline_continue_36_36",
                                "tokens": 1821,
                                "prompt_tokens": 1300,
                                "completion_tokens": 521,
                                "model_call_count": 1,
                                "cost": 0.01821,
                                "model_calls": [
                                    {
                                        "provider": "deepseek",
                                        "model": "deepseek-reasoner",
                                        "prompt_tokens": 1300,
                                        "completion_tokens": 521,
                                        "total_tokens": 1821,
                                        "cost_usd": 0.01821,
                                    }
                                ],
                            },
                        ],
                    }
                },
            },
        },
    )

    stats = collect_project_token_analytics(project_dir)

    assert stats["run_count"] == 1
    assert stats["step_count"] == 2
    assert stats["total_tokens"] == 4321
    assert stats["total_prompt_tokens"] == 3000
    assert stats["total_completion_tokens"] == 1321
    assert stats["logged_cost_usd"] == 0.04321
    assert stats["runs"][0]["tokens"] == 4321
    assert stats["runs"][0]["step_count"] == 2
    assert stats["models"][0]["label"] == "deepseek/deepseek-reasoner"


def test_collect_project_token_analytics_backfills_error_runs_from_model_calls(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "error_with_model_calls"
    logs_dir = project_dir / "logs"
    run_dir = logs_dir / "20260104-run-error"

    _write_summary(
        run_dir,
        {
            "run_id": "run-error",
            "started_at": "2026-01-04T08:00:00Z",
            "status": "error",
            "metadata": {"kind": "init_long"},
            "trace_summary": {"completed_steps": ["spec", "plan_outline_batch_1_5"]},
        },
    )
    _write_model_call(
        run_dir,
        "001_spec_enrich.json",
        {
            "task": "spec_enrich",
            "provider": "deepseek",
            "model": "deepseek-reasoner",
            "prompt_tokens": 1000,
            "completion_tokens": 600,
            "total_tokens": 1600,
            "cost_usd": 0.0016,
        },
    )
    _write_model_call(
        run_dir,
        "002_plan_outline_batch.json",
        {
            "task": "plan_outline_batch",
            "provider": "deepseek",
            "model": "deepseek-reasoner",
            "prompt_tokens": 1200,
            "completion_tokens": 800,
            "total_tokens": 2000,
            "cost_usd": 0.0020,
        },
    )

    stats = collect_project_token_analytics(project_dir)

    assert stats["run_count"] == 1
    assert stats["total_tokens"] == 3600
    assert stats["step_count"] == 2
    assert stats["recovered_run_count"] == 1
    assert stats["recovered_tokens"] == 3600
    assert stats["runs"][0]["tokens"] == 3600
    assert stats["runs"][0]["step_count"] == 2
    assert stats["runs"][0]["token_source"] == "model_calls"
    assert stats["models"][0]["label"] == "deepseek/deepseek-reasoner"


def test_collect_project_token_analytics_reconciles_and_deduplicates_call_logs(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "reconciled_project"
    run_dir = project_dir / "logs" / "run-1"
    _write_summary(
        run_dir,
        {
            "run_id": "run-1",
            "started_at": "2026-01-04T08:00:00Z",
            "status": "success",
            "metadata": {"kind": "run_chapter", "chapter_number": 2},
            "trace_summary": {
                "total_tokens": 1000,
                "total_prompt_tokens": 700,
                "total_completion_tokens": 300,
                "steps": [{"name": "draft", "tokens": 1000}],
            },
        },
    )
    call = {
        "call_id": "same-call",
        "task": "draft",
        "provider": "openai",
        "model": "gpt-test",
        "prompt_tokens": 900,
        "completion_tokens": 600,
        "total_tokens": 1500,
        "cost_usd": 0.015,
    }
    _write_model_call(run_dir, "001_draft.json", call)
    _write_model_call(run_dir, "001_draft_done.json", call)

    stats = collect_project_token_analytics(project_dir)

    assert stats["total_tokens"] == 1500
    assert stats["total_prompt_tokens"] == 900
    assert stats["total_completion_tokens"] == 600
    assert stats["total_call_count"] == 1
    assert stats["reconciled_tokens"] == 500
    assert stats["reconciled_run_count"] == 1
    assert stats["runs"][0]["token_source"] == "reconciled"
    assert stats["models"][0]["tokens"] == 1500


def test_collect_project_token_analytics_uses_model_display_name_from_workspace_profiles(
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "workspace"
    project_dir = workspace_dir / "data" / "demo_project"
    logs_dir = project_dir / "logs"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    (workspace_dir / "model_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "deepseek:deepseek-reasoner",
                        "display_name": "DeepSeek-reasoner",
                        "provider": "deepseek",
                        "model_id": "deepseek-reasoner",
                    }
                ],
                "routes": {},
                "default_profile_id": "",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _write_summary(
        logs_dir / "20260105-run-d",
        {
            "run_id": "run-d",
            "started_at": "2026-01-05T08:00:00Z",
            "status": "success",
            "metadata": {"kind": "init_long"},
            "trace_summary": {
                "total_tokens": 1234,
                "steps": [
                    {
                        "name": "plan_outline_continue_31_35",
                        "tokens": 1234,
                        "model_calls": [
                            {
                                "provider": "deepseek",
                                "model": "deepseek-reasoner",
                                "total_tokens": 1234,
                            }
                        ],
                    }
                ],
            },
        },
    )

    stats = collect_project_token_analytics(project_dir)
    assert stats["models"][0]["key"] == "deepseek/deepseek-reasoner"
    assert stats["models"][0]["label"] == "deepseek/deepseek-reasoner"
    assert stats["models"][0]["display_name"] == "DeepSeek-reasoner"


def test_extract_outline_range_handles_batch_continue_and_repair_keys() -> None:
    assert _extract_outline_range("plan_outline_batch_1_5") == (1, 5)
    assert _extract_outline_range("plan_outline_continue_31_35") == (31, 35)
    assert _extract_outline_range("plan_outline_repair_36_36") == (36, 36)
    assert _extract_outline_range("plan_outline_continue_bad") is None


def test_step_tokens_for_filter_reads_kind_token_buckets() -> None:
    step_item = {"tokens": 1000, "kind_tokens": {"init": 700, "chapter": 200, "repair": 100}}
    assert _step_tokens_for_filter(step_item, "all") == 1000
    assert _step_tokens_for_filter(step_item, "init") == 700
    assert _step_tokens_for_filter(step_item, "chapter") == 200
    assert _step_tokens_for_filter(step_item, "repair") == 100


def test_model_cost_ring_uses_png_image_and_hides_raw_model_key(tmp_path: Path) -> None:
    _app = QApplication.instance() or QApplication([])
    project_dir = tmp_path / "ring_project"
    tab = TokenAnalyticsTab(project_dir)

    html = tab._render_model_cost_ring(  # noqa: SLF001
        [
            {
                "key": "deepseek/deepseek-reasoner",
                "display_name": "DeepSeek-reasoner",
                "provider": "deepseek",
                "model": "deepseek-reasoner",
                "tokens": 300000,
                "calls": 2,
            }
        ]
    )

    assert "data:image/png;base64," in html
    assert "DeepSeek-reasoner" in html
    assert "deepseek/deepseek-reasoner" not in html


def test_model_display_name_falls_back_to_model_id_not_provider_slash_model(tmp_path: Path) -> None:
    _app = QApplication.instance() or QApplication([])
    project_dir = tmp_path / "name_project"
    tab = TokenAnalyticsTab(project_dir)

    item = {
        "key": "deepseek/deepseek-reasoner",
        "display_name": "deepseek/deepseek-reasoner",
        "provider": "deepseek",
        "model": "deepseek-reasoner",
    }
    assert tab._model_display_name_for_item(item) == "deepseek-reasoner"  # noqa: SLF001
    assert tab._model_provider_for_item(item) == "deepseek"  # noqa: SLF001
