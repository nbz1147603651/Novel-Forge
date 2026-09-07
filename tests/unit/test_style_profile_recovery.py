"""Tests for style-profile recovery from init-long model-call logs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from novel_forge.pipeline.long.services.style_profile_recovery import (
    recover_style_profile_from_project_logs,
)


def _write_model_call(
    path: Path,
    *,
    task: str,
    payload: dict | None = None,
    event: str = "api_call_done",
) -> None:
    body: dict[str, object] = {
        "event": event,
        "task": task,
    }
    if payload is not None:
        body["response"] = {"content": json.dumps(payload, ensure_ascii=False)}
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def test_recover_style_profile_from_style_and_structure_payloads(tmp_path: Path) -> None:
    run_dir = tmp_path / "logs" / "20260422-004129_desktop-init-long_80120f38" / "model_calls"
    run_dir.mkdir(parents=True)

    style_payload = {
        "modules": [
            {
                "name": "冷峻叙事",
                "rules": ["短句推进", "动作优先"],
                "positive_example": "她落笔三行，先写账目后写人心。",
                "negative_example": "她絮絮叨叨讲了很多情绪。",
            }
        ],
        "summary": "冷峻克制，信息密度高。",
        "global_style": {
            "dialogue_ratio": "high",
            "pace_mode": "fast",
            "emotional_style": "subtle",
            "environment_ratio": "high",
            "info_density": "high",
            "banned_phrases": ["穿越"],
        },
    }
    structure_payload = {
        "hook_config": {
            "preferred_types": ["mystery", "crisis"],
            "strength_baseline": "strong",
            "chapter_end_required": True,
        },
        "strand_config": {
            "quest_max_consecutive": 4,
            "fire_max_absent": 8,
            "constellation_max_absent": 12,
            "stagnation_threshold": 2,
        },
        "micro_payoff_config": {
            "preferred_types": ["information", "clue"],
            "min_per_chapter": 2,
        },
        "cool_point_config": {
            "preferred_patterns": ["真相揭露"],
            "density_per_chapter": "medium",
        },
    }

    _write_model_call(
        run_dir / "032_profile_style.json", task="profile_style", payload=style_payload
    )
    _write_model_call(
        run_dir / "033_profile_structure.json",
        task="profile_structure",
        payload=structure_payload,
    )

    profile, meta = recover_style_profile_from_project_logs(tmp_path)

    assert profile is not None
    assert profile.summary.startswith("冷峻克制")
    assert profile.hook_config.strength_baseline == "strong"
    assert profile.strand_config.quest_max_consecutive == 4
    assert meta["used_default_structure"] is False
    assert meta["style_file"] == "032_profile_style.json"
    assert meta["structure_file"] == "033_profile_structure.json"


def test_recover_style_profile_accepts_stream_call_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "logs" / "20260422-004129_desktop-init-long_80120f38" / "model_calls"
    run_dir.mkdir(parents=True)
    _write_model_call(
        run_dir / "032_profile_style.json",
        task="profile_style",
        event="api_stream_done",
        payload={
            "modules": [
                {
                    "name": "冷叙",
                    "rules": ["动作先行"],
                    "positive_example": "她先合上账簿。",
                    "negative_example": "她解释了很久。",
                }
            ],
            "summary": "冷峻克制。",
            "global_style": {},
        },
    )

    profile, meta = recover_style_profile_from_project_logs(tmp_path)

    assert profile is not None
    assert profile.summary == "冷峻克制。"
    assert meta["style_file"] == "032_profile_style.json"


def test_recover_style_profile_uses_default_structure_when_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / "logs" / "20260422-004129_desktop-init-long_80120f38" / "model_calls"
    run_dir.mkdir(parents=True)

    style_payload = {
        "modules": [
            {
                "name": "克制",
                "rules": ["少解释"],
                "positive_example": "正例",
                "negative_example": "反例",
            }
        ],
        "summary": "强调留白。",
        "global_style": {"dialogue_ratio": "medium"},
    }
    _write_model_call(
        run_dir / "037_profile_style.json", task="profile_style", payload=style_payload
    )

    profile, meta = recover_style_profile_from_project_logs(tmp_path)

    assert profile is not None
    assert profile.summary == "强调留白。"
    # defaults from schema parser when structure branch is unavailable
    assert profile.hook_config.preferred_types == ["crisis", "mystery"]
    assert profile.micro_payoff_config.min_per_chapter == 1
    assert meta["used_default_structure"] is True
    assert meta["structure_file"] is None


def test_recover_style_profile_repairs_overlong_model_fields(tmp_path: Path) -> None:
    run_dir = tmp_path / "logs" / "20260422-004129_desktop-init-long_80120f38" / "model_calls"
    run_dir.mkdir(parents=True)
    _write_model_call(
        run_dir / "037_profile_style.json",
        task="profile_style",
        payload={
            "modules": [
                {
                    "name": "克制叙事",
                    "rules": ["动作和现场证据必须先于解释性内心独白。" * 3],
                    "positive_example": "她关灯后才开口，手指仍压着账页。" * 4,
                    "negative_example": "海风吹过巷口，她回忆过去并突然感到一阵眩晕。" * 4,
                }
            ],
            "summary": "强调动作、证据和留白。",
            "global_style": {"dialogue_ratio": "medium"},
        },
    )

    profile, _meta = recover_style_profile_from_project_logs(tmp_path)

    assert profile is not None
    assert len(profile.modules[0].rules[0]) <= 50
    assert len(profile.modules[0].negative_example) <= 60


def test_recover_style_profile_ignores_logs_before_recovery_epoch(tmp_path: Path) -> None:
    run_dir = tmp_path / "logs" / "20260422-004129_desktop-init-long_80120f38" / "model_calls"
    run_dir.mkdir(parents=True)
    style_path = run_dir / "037_profile_style.json"
    _write_model_call(
        style_path,
        task="profile_style",
        payload={
            "modules": [
                {
                    "name": "旧风格",
                    "rules": ["旧规则"],
                    "positive_example": "正例",
                    "negative_example": "反例",
                }
            ],
            "summary": "旧初始化日志中的风格。",
            "global_style": {"dialogue_ratio": "medium"},
        },
    )
    old_mtime = 1_700_000_000.0
    os.utime(style_path, (old_mtime, old_mtime))
    os.utime(run_dir.parent, (old_mtime, old_mtime))

    profile, meta = recover_style_profile_from_project_logs(
        tmp_path,
        min_mtime=old_mtime + 100,
    )

    assert profile is None
    assert meta["reason"] == "no_init_long_log_dir_after_recovery_epoch"
