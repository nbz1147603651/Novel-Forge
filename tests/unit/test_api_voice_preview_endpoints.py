"""API tests for multi-candidate voice A/B preview command endpoints.

Verifies the three engine commands (build-voice-preview-plan /
generate-voice-previews / confirm-voice-preview) expose the same
``tts/services/voice_preview.py`` service the PySide6 studio uses
in-process — the React studio consumes them over HTTP.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from novel_forge.api import deps
from novel_forge.api.app import create_app
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.gateway.factory import TTSAdapterRegistry
from novel_forge.tts.schemas import (
    TTSProvider,
    TTSRequest,
    TTSResponse,
    VoiceCastEntry,
    VoiceCloneStatus,
    VoiceTeamContract,
)

_CATALOG: list[dict[str, Any]] = [
    {
        "voice_id": "f-warm",
        "name": "温暖女声",
        "gender": "female",
        "age_hint": "青年",
        "personality": "warm",
        "voice_description": "温暖温柔",
    },
    {
        "voice_id": "f-calm",
        "name": "沉稳女声",
        "gender": "female",
        "age_hint": "中年",
        "personality": "calm",
        "voice_description": "沉稳冷静",
    },
    {
        "voice_id": "m-calm",
        "name": "沉稳男声",
        "gender": "male",
        "age_hint": "中年",
        "personality": "calm",
        "voice_description": "沉稳",
    },
]


class _FakeAdapter:
    def __init__(self) -> None:
        self.synthesize_calls = 0

    async def list_system_voices(
        self,
        *,
        gender: str | None = None,
        language: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        voices = _CATALOG
        if gender:
            voices = [v for v in voices if v.get("gender") == gender]
        return voices[:limit]

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        self.synthesize_calls += 1
        return TTSResponse(
            audio_data=b"\xff\xfb" * 64,
            model_id=request.model_id,
            voice_id=request.voice_id,
            audio_format="mp3",
        )


class _FakeRegistry:
    def __init__(self, adapter: _FakeAdapter) -> None:
        self._adapter = adapter

    def get_adapter(self, provider: str | TTSProvider) -> _FakeAdapter:
        return self._adapter


@pytest.fixture(autouse=True)
def _runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    deps.reload_runtime_dependencies()
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("NOVEL_FORGE_TTS_DEFAULT_PROVIDER", "mock")
    adapter = _FakeAdapter()
    monkeypatch.setattr(
        TTSAdapterRegistry, "get_instance", lambda settings: _FakeRegistry(adapter)
    )
    yield
    deps.reload_runtime_dependencies()


def _make_project(tmp_path: Path, project_id: str) -> ProjectLayout:
    project_dir = tmp_path / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()
    return layout


def _write_team(layout: ProjectLayout) -> None:
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="lin-yuan",
                character_name="林远",
                voice_id="old-v",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        narrator_voice_id="mock-narrator",
        narrator_provider=TTSProvider.MOCK,
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")


def test_build_voice_preview_plan_returns_three_candidates(
    tmp_path: Path,
) -> None:
    project_id = "preview-plan"
    _make_project(tmp_path, project_id)
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/engine/commands/build-voice-preview-plan",
        json={
            "kind": "build_voice_preview_plan",
            "project_id": project_id,
            "characters": [
                {
                    "character_id": "lin-yuan",
                    "name": "林远",
                    "gender": "male",
                    "personality": "calm",
                }
            ],
            "candidate_count": 3,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "accepted"
    plans = payload["data"]["plans"]
    assert len(plans) == 1
    plan = plans[0]
    assert plan["character_id"] == "lin-yuan"
    assert len(plan["candidates"]) == 1  # 目录中仅 1 个男性候选
    assert plan["candidates"][0]["voice_id"] == "m-calm"
    assert plan["sample_text"]
    assert "audio_url" not in plan["candidates"][0]


def test_generate_voice_previews_maps_sample_path_to_audio_url(
    tmp_path: Path,
) -> None:
    project_id = "preview-generate"
    _make_project(tmp_path, project_id)
    client = TestClient(create_app())

    build = client.post(
        "/api/v1/engine/commands/build-voice-preview-plan",
        json={
            "kind": "build_voice_preview_plan",
            "project_id": project_id,
            "characters": [
                {
                    "character_id": "lin-yuan",
                    "name": "林远",
                    "gender": "female",
                }
            ],
        },
    ).json()
    plan = build["data"]["plans"][0]
    assert len(plan["candidates"]) == 2

    generated = client.post(
        "/api/v1/engine/commands/generate-voice-previews",
        json={
            "kind": "generate_voice_previews",
            "project_id": project_id,
            "plan": plan,
        },
    )

    assert generated.status_code == 200
    payload = generated.json()
    assert payload["status"] == "accepted"
    out_plan = payload["data"]["plan"]
    for candidate in out_plan["candidates"]:
        assert candidate["sample_path"]
        assert candidate["audio_url"].startswith(
            f"/api/v1/engine/voice/projects/{project_id}/audio/"
        )
    assert "（2/2 成功）" in payload["message"]

    # 试听文件可以通过静态音频端点访问。
    audio_url = out_plan["candidates"][0]["audio_url"]
    audio = client.get(audio_url)
    assert audio.status_code == 200
    assert audio.content


def test_confirm_voice_preview_writes_selected_voice_into_team(
    tmp_path: Path,
) -> None:
    project_id = "preview-confirm"
    layout = _make_project(tmp_path, project_id)
    _write_team(layout)
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/engine/commands/confirm-voice-preview",
        json={
            "kind": "confirm_voice_preview",
            "project_id": project_id,
            "character_id": "lin-yuan",
            "voice_id": "f-warm",
            "speed": 1.1,
            "volume": 0.9,
            "sample_text": "试听台词。",
            "sample_path": str(layout.tts_audio_dir(0) / "preview_x.mp3"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "accepted"
    team = payload["data"]["team"]
    entry = next(item for item in team["entries"] if item["character_id"] == "lin-yuan")
    assert entry["voice_id"] == "f-warm"
    assert entry["voice_source"] == "system"
    assert entry["approval_status"] == "approved"
    assert team["confirmed"] is False


def test_confirm_voice_preview_rejects_missing_team(tmp_path: Path) -> None:
    project_id = "preview-no-team"
    _make_project(tmp_path, project_id)
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/engine/commands/confirm-voice-preview",
        json={
            "kind": "confirm_voice_preview",
            "project_id": project_id,
            "character_id": "lin-yuan",
            "voice_id": "f-warm",
            "speed": 1.0,
            "volume": 1.0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "rejected"
    assert "配音团队" in payload["message"]


def test_build_voice_preview_plan_rejects_unknown_project(tmp_path: Path) -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/engine/commands/build-voice-preview-plan",
        json={
            "kind": "build_voice_preview_plan",
            "project_id": "does-not-exist",
            "characters": [{"character_id": "c1", "name": "角色"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_generate_voice_previews_rejects_invalid_plan(tmp_path: Path) -> None:
    project_id = "preview-bad-plan"
    _make_project(tmp_path, project_id)
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/engine/commands/generate-voice-previews",
        json={
            "kind": "generate_voice_previews",
            "project_id": project_id,
            "plan": {"not": "a plan"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
