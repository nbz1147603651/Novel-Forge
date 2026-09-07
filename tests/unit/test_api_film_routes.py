from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from novel_forge.api.app import create_app
from novel_forge.api.deps import get_runtime_services
from novel_forge.film.providers.base import (
    FilmGenerationMode,
    FilmGenerationRequest,
    FilmProviderTask,
    FilmProviderTaskState,
)
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout


class StubFilmProvider:
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id

    async def submit(self, request: FilmGenerationRequest) -> FilmProviderTask:
        is_image = request.mode in {
            FilmGenerationMode.TEXT_TO_IMAGE,
            FilmGenerationMode.IMAGE_EDIT,
            FilmGenerationMode.IMAGE_SET,
        }
        return FilmProviderTask(
            provider_id=self.provider_id,
            model_id=request.model_id or f"{self.provider_id}-test-model",
            mode=request.mode,
            state=(FilmProviderTaskState.SUCCEEDED if is_image else FilmProviderTaskState.RUNNING),
            task_id=f"task-{self.provider_id}",
            asset_urls=(
                [f"https://assets.invalid/{self.provider_id}/identity-01.png"] if is_image else []
            ),
        )

    async def query(self, task: FilmProviderTask) -> FilmProviderTask:
        return task.model_copy(
            update={
                "state": FilmProviderTaskState.SUCCEEDED,
                "asset_urls": [f"https://assets.invalid/{self.provider_id}/shot-01.mp4"],
            }
        )


def _seed_project(storage: FileSystemStorage, project_id: str) -> ProjectLayout:
    layout = ProjectLayout(storage.project_dir(project_id))
    layout.ensure_dirs()
    storage.save_json(
        layout.spec_path,
        {
            "title": "长夜电台",
            "theme": "失声主持人重返旧电台追查被隐藏的事故录音",
            "genre": "悬疑剧情",
            "tone": "克制、潮湿",
        },
    )
    storage.save_json(
        layout.characters_path,
        {
            "characters": [
                {
                    "character_id": "char-shen",
                    "name": "沈鹿溪",
                    "role": "protagonist",
                    "appearance": "短黑发，左眉浅疤，深绿旧风衣",
                    "voice": "低声短句，情绪激烈时反而放慢",
                    "visual_identity": {
                        "facial_anchors": ["左眉浅疤", "短黑发"],
                        "silhouette": "瘦削高挑，窄长风衣轮廓",
                        "body_language": "收肩，观察时下颌微抬",
                        "costume_palette": ["深绿", "炭黑"],
                        "signature_props": ["旧录音笔"],
                        "continuity_rules": ["左眉浅疤始终可辨"],
                        "forbidden_drift": ["禁止随机改变发长"],
                    },
                    "tts_voice_hints": {
                        "timbre": "低沉清晰",
                        "register": "中低音",
                        "cadence": "偏慢",
                    },
                }
            ]
        },
    )
    storage.save_json(
        layout.bible_path,
        {
            "locations": [
                {
                    "location_id": "loc-radio",
                    "name": "旧电台直播间",
                    "spatial_layout": "控制台朝东，南墙是 ON AIR 灯",
                    "ambient_sound": ["雨打窗", "设备电流"],
                }
            ]
        },
    )
    storage.save_json(
        layout.outline_path,
        {
            "chapters": [
                {
                    "chapter_number": 1,
                    "scenes": [
                        {
                            "title": "停播后的第一通电话",
                            "location": "旧电台直播间",
                            "time": "雨夜",
                            "objective": "确认神秘来电者身份",
                            "conflict": "对方播放未公开录音",
                            "turn": "录音中出现她自己的声音",
                            "visual_hook": "ON AIR 红灯自行亮起",
                            "sound_hook": "雨声中混入磁带倒转声",
                        }
                    ],
                }
            ]
        },
    )
    return layout


def _client(tmp_path: Path, project_id: str) -> tuple[TestClient, ProjectLayout]:
    storage = FileSystemStorage(tmp_path)
    layout = _seed_project(storage, project_id)
    runtime = SimpleNamespace(
        storage=storage,
        settings=SimpleNamespace(),
        router=None,
    )
    app = create_app()
    app.dependency_overrides[get_runtime_services] = lambda: runtime
    return TestClient(app), layout


def test_film_api_supports_reviewable_media_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "novel_forge.film.pipeline.create_film_provider",
        lambda provider_id, _settings: StubFilmProvider(provider_id),
    )
    client, layout = _client(tmp_path, "film-api")

    catalog = client.get("/api/v1/film/catalog")
    bootstrapped = client.post(
        "/api/v1/film/projects/film-api/bootstrap",
        json={"mode": "collaborative", "refresh_sources": True},
    )

    assert catalog.status_code == 200
    assert {"bailian", "minimax"}.issubset(catalog.json())
    assert bootstrapped.status_code == 200
    initial = bootstrapped.json()
    assert initial["current_stage"] == "planning"
    assert initial["production_bible"]["characters"][0]["screen_identity"]["facial_anchors"] == [
        "左眉浅疤",
        "短黑发",
    ]

    advanced = client.post(
        "/api/v1/film/projects/film-api/advance",
        json={"use_ai": False, "run_until": "screenplay"},
    )
    assert advanced.status_code == 200
    assert advanced.json()["current_stage"] == "screenplay"

    asset_id = advanced.json()["visual_assets"][0]["asset_id"]
    generated_asset = client.post(
        f"/api/v1/film/projects/film-api/assets/{asset_id}/generate",
        json={"provider_id": "bailian", "model_id": "wan2.6-image", "image_count": 4},
    )
    assert generated_asset.status_code == 200
    asset = generated_asset.json()["visual_assets"][0]
    candidate_url = asset["candidates"][0]
    assert asset["provider_task"]["state"] == "succeeded"

    selected = client.post(
        f"/api/v1/film/projects/film-api/assets/{asset_id}/select",
        json={"url": candidate_url, "lock": True},
    )
    assert selected.status_code == 200
    assert selected.json()["visual_assets"][0]["qc_status"] == "approved"

    shot_id = selected.json()["shots"][0]["shot_id"]
    patched = client.patch(
        f"/api/v1/film/projects/film-api/shots/{shot_id}",
        json={"patch": {"duration_s": 5.5, "prompt": "雨夜电台近景，人物身份严格一致"}},
    )
    assert patched.status_code == 200
    assert patched.json()["shots"][0]["duration_s"] == 5.5

    submitted_shot = client.post(
        f"/api/v1/film/projects/film-api/shots/{shot_id}/generate",
        json={"provider_id": "minimax", "model_id": "MiniMax-Hailuo-2.3"},
    )
    assert submitted_shot.status_code == 200
    assert submitted_shot.json()["shots"][0]["provider_task"]["state"] == "running"
    submitted_job = next(
        item
        for item in submitted_shot.json()["jobs"]
        if item["target_id"] == shot_id and item["kind"] == "generate_shot"
    )
    assert submitted_job["state"] == "running"

    handshake = client.post(
        "/api/v1/film/providers/minimax/callback",
        json={"challenge": "minimax-challenge"},
    )
    assert handshake.status_code == 200
    assert handshake.json() == {"challenge": "minimax-challenge"}

    callback = client.post(
        "/api/v1/film/providers/minimax/callback?project_id=film-api",
        json={
            "task": {
                "id": "task-minimax",
                "status": "succeeded",
                "content": {"url": "https://assets.invalid/minimax/callback.mp4"},
            }
        },
    )
    assert callback.status_code == 200
    assert callback.json() == {"received": True, "applied": True}
    callback_state = client.get("/api/v1/film/projects/film-api").json()
    callback_job = next(
        item
        for item in callback_state["jobs"]
        if item["target_id"] == shot_id and item["kind"] == "generate_shot"
    )
    assert callback_job["state"] == "succeeded"

    queried = client.post(f"/api/v1/film/projects/film-api/media/{shot_id}/query")
    assert queried.status_code == 200
    assert queried.json()["shots"][0]["selected_asset_url"].endswith("callback.mp4")

    exported = client.post("/api/v1/film/projects/film-api/export-otio")
    assert exported.status_code == 200
    assert exported.json()["format"] == "OpenTimelineIO"
    assert Path(exported.json()["path"]).exists()
    assert (layout.root / "production_bible.json").exists()


def test_film_api_autonomous_mode_stops_at_paid_generation_gate(tmp_path: Path) -> None:
    client, _layout = _client(tmp_path, "film-auto")

    response = client.post(
        "/api/v1/film/projects/film-auto/advance",
        json={"mode": "autonomous", "use_ai": False, "run_until": "delivery"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_stage"] == "shot_production"
    shot_stage = next(item for item in payload["stages"] if item["stage"] == "shot_production")
    assert shot_stage["status"] == "blocked"
    assert "付费镜头生成" in shot_stage["warnings"][0]


def test_film_graph_v2_api_supports_revision_estimate_run_and_cancel(tmp_path: Path) -> None:
    client, _layout = _client(tmp_path, "film-graph-api")

    catalog = client.get("/api/v1/film/node-catalog")
    graph_response = client.get("/api/v1/film/projects/film-graph-api/graph")

    assert catalog.status_code == 200
    assert any(item["type_id"] == "minimax_h3_video" for item in catalog.json())
    assert all(item["prompt_template"]["sections"] for item in catalog.json())
    assert graph_response.status_code == 200
    graph_view = graph_response.json()
    assert graph_view["definition"]["schema_version"] == "2.0"
    assert graph_view["validation_issues"] == []

    graph = graph_view["definition"]
    h3_node = next(item for item in graph["nodes"] if item["node_id"] == "minimax-h3")
    h3_node["prompt"]["sections"] = h3_node["prompt"]["sections"][1:]
    optimized = client.post(
        "/api/v1/film/projects/film-graph-api/graph/prompts/optimize",
        json={"node": h3_node},
    )
    assert optimized.status_code == 200
    assert optimized.json()["prompt"]["sections"][0]["section_id"] == "reference_materials"
    assert "【核心创意】" in optimized.json()["rendered_prompt"]
    h3_node["prompt"] = optimized.json()["prompt"]
    graph["viewport"] = {"x": 140, "y": 90, "zoom": 0.74}
    saved = client.put(
        "/api/v1/film/projects/film-graph-api/graph",
        json={"expected_revision": graph["revision"], "graph": graph},
    )
    assert saved.status_code == 200
    assert saved.json()["definition"]["revision"] == graph["revision"] + 1
    assert saved.json()["definition"]["viewport"]["zoom"] == 0.74

    conflict = client.put(
        "/api/v1/film/projects/film-graph-api/graph",
        json={"expected_revision": graph["revision"], "graph": graph},
    )
    assert conflict.status_code == 409

    estimate = client.post(
        "/api/v1/film/projects/film-graph-api/runs/estimate",
        json={"scope": "to_node", "target_node_ids": ["minimax-h3"]},
    )
    assert estimate.status_code == 200
    assert estimate.json()["requires_confirmation"] is True
    assert "delivery" not in estimate.json()["execution_node_ids"]

    created = client.post(
        "/api/v1/film/projects/film-graph-api/runs",
        json={
            "scope": "to_node",
            "target_node_ids": ["minimax-h3"],
            "confirmed_cost": False,
            "high_priority": False,
        },
    )
    assert created.status_code == 200
    assert created.json()["status"] == "waiting_confirmation"
    run_id = created.json()["run_id"]

    events = client.get(f"/api/v1/film/projects/film-graph-api/runs/{run_id}/events")
    assert events.status_code == 200
    assert events.json()[0]["kind"] == "run_created"

    cancelled = client.post(f"/api/v1/film/projects/film-graph-api/runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_film_api_returns_404_for_missing_project(tmp_path: Path) -> None:
    storage = FileSystemStorage(tmp_path)
    runtime = SimpleNamespace(storage=storage, settings=SimpleNamespace(), router=None)
    app = create_app()
    app.dependency_overrides[get_runtime_services] = lambda: runtime

    response = TestClient(app).get("/api/v1/film/projects/not-found")

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"
