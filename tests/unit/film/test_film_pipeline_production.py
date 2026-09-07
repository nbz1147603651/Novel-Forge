"""Pipeline wiring tests for materialization, QC and job governance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from novel_forge.film.providers.base import (
    FilmGenerationMode,
    FilmGenerationRequest,
    FilmProviderTask,
    FilmProviderTaskState,
)
from novel_forge.film.schemas import (
    FilmJobKind,
    FilmJobState,
    MediaArtifact,
    MediaArtifactKind,
    QcCheck,
    QcCheckStatus,
    QcReport,
)
from tests.unit.film.test_film_pipeline import _project


class _StubVault:
    def __init__(self, artifact: MediaArtifact) -> None:
        self.artifact = artifact
        self.calls: list[str] = []

    async def materialize(
        self,
        url: str,
        *,
        kind: MediaArtifactKind,
        subject_ref: str,
        provider_id: str = "",
        task_id: str = "",
    ) -> MediaArtifact:
        self.calls.append(url)
        return self.artifact


class _StubQc:
    def __init__(self, report: QcReport) -> None:
        self.report = report

    async def check_shot(self, _state: Any, shot_id: str) -> QcReport:
        return self.report.model_copy(update={"target_id": shot_id})


async def test_materialize_media_records_versioned_artifact(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    shot = state.shots[0]
    state = pipeline.update_shot(shot.shot_id, {"selected_asset_url": "mock://shot.mp4"})
    artifact = MediaArtifact(
        artifact_id="art-x",
        kind=MediaArtifactKind.VIDEO,
        subject_ref=shot.shot_id,
        local_path="/tmp/shot.mp4",
        duration_s=5.0,
    )
    pipeline.vault = _StubVault(artifact)  # type: ignore[assignment]

    saved = await pipeline.materialize_media(shot.shot_id)

    assert [item.artifact_id for item in saved.media_artifacts] == ["art-x"]
    # Re-materializing replaces the previous artifact for the same subject.
    replacement = artifact.model_copy(update={"artifact_id": "art-y"})
    pipeline.vault = _StubVault(replacement)  # type: ignore[assignment]
    saved = await pipeline.materialize_media(shot.shot_id)
    assert [item.artifact_id for item in saved.media_artifacts] == ["art-y"]


async def test_materialize_media_requires_selected_url(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    with pytest.raises(ValueError):
        await pipeline.materialize_media(state.shots[0].shot_id)


async def test_run_shot_qc_writes_report_and_status(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    shot = state.shots[0]
    pipeline.qc = _StubQc(  # type: ignore[assignment]
        QcReport(
            target_id=shot.shot_id,
            target_type="shot",
            passed=True,
            checks=[QcCheck(name="materialized", status=QcCheckStatus.PASS, detail="ok")],
        )
    )

    saved = await pipeline.run_shot_qc(shot.shot_id)

    updated = next(item for item in saved.shots if item.shot_id == shot.shot_id)
    assert updated.qc_status == "passed"
    assert [report.target_id for report in saved.qc_reports] == [shot.shot_id]
    # A second run replaces the report instead of stacking duplicates.
    saved = await pipeline.run_shot_qc(shot.shot_id)
    assert len(saved.qc_reports) == 1


def test_bootstrap_recovers_stuck_running_jobs(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    state, job, _ = pipeline.jobs.acquire(
        state, kind=FilmJobKind.GENERATE_SHOT, target_id=state.shots[0].shot_id
    )
    state = pipeline.jobs.mark_running(state, job)
    pipeline.store.save(state)

    recovered = pipeline.get_or_bootstrap()

    assert recovered.jobs[0].state == FilmJobState.QUEUED


async def test_batch_generation_records_failures_and_continues(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    shot_ids = [shot.shot_id for shot in state.shots]

    calls: list[str] = []

    async def fake_generate_shot(shot_id: str, **_kwargs: Any) -> Any:
        calls.append(shot_id)
        if shot_id == shot_ids[0]:
            raise RuntimeError("provider unavailable")
        return pipeline.get_or_bootstrap()

    pipeline.generate_shot = fake_generate_shot  # type: ignore[method-assign]

    await pipeline.generate_shots_batch(shot_ids)

    assert calls == shot_ids  # first failure did not abort the batch


# ─── Catalog-driven generation spec resolution (Batch A) ─────────────────────


def test_resolve_specs_h3_uses_768p_default_and_h3_duration_domain(tmp_path: Path) -> None:
    """H3 only supports 768P/2K and 4–15s; the resolver must stop the old
    hardcoded 1080P path that silently downgraded every H3 shot to 768P."""

    _layout, pipeline = _project(tmp_path)
    resolution, duration = pipeline._resolve_generation_specs("minimax", "MiniMax-H3", 2.4)
    assert resolution == "768P"
    assert duration == 4  # clamped into the H3 4–15s window

    resolution, duration = pipeline._resolve_generation_specs("minimax", "MiniMax-H3", 18.0)
    assert resolution == "768P"
    assert duration == 15


def test_resolve_specs_bailian_keeps_1080p_default(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    resolution, duration = pipeline._resolve_generation_specs("bailian", "wan2.7-t2v", 30.0)
    assert resolution == "1080P"
    assert duration == 15


def test_resolve_specs_unknown_model_falls_back(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    resolution, duration = pipeline._resolve_generation_specs("minimax", "ghost-model", 5.0)
    assert resolution == "1080P"
    assert duration == 5


# ─── H3 multimodal references (Batch B) ──────────────────────────────────────


async def test_shot_contract_carries_video_and_audio_references_for_minimax(
    tmp_path: Path,
) -> None:
    """H3 Ref2VA conditioning: identity images plus shot-level video/audio
    references all reach the provider request contract."""

    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    shot = state.shots[0].model_copy(
        update={
            "identity_reference_urls": ["https://x/hero.png"],
            "reference_video_urls": ["https://x/motion.mp4", "https://x/more.mp4"],
            "reference_audio_urls": ["https://x/voice.mp3"],
        }
    )
    mode, references = pipeline._shot_generation_contract(shot, state, "minimax")
    assert mode == FilmGenerationMode.SUBJECT_REFERENCE_VIDEO
    kinds = [item.kind for item in references]
    assert kinds == ["subject", "reference_video", "reference_video", "reference_audio"]


def test_shot_contract_keeps_h3_media_references_without_identity_images(tmp_path: Path) -> None:
    """H3 Ref2VA is also valid with video/audio conditioning and no subject sheet."""

    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    shot = state.shots[0].model_copy(
        update={
            "identity_reference_urls": [],
            "reference_video_urls": ["https://x/motion.mp4"],
            "reference_audio_urls": ["https://x/voice.mp3"],
        }
    )

    mode, references = pipeline._shot_generation_contract(shot, state, "minimax")

    assert mode == FilmGenerationMode.REFERENCE_TO_VIDEO
    assert [(item.kind, item.url) for item in references] == [
        ("reference_video", "https://x/motion.mp4"),
        ("reference_audio", "https://x/voice.mp3"),
    ]


def test_shot_contract_caps_multimodal_references(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    shot = state.shots[0].model_copy(
        update={
            "identity_reference_urls": [f"https://x/i-{i}.png" for i in range(12)],
            "reference_video_urls": [f"https://x/v-{i}.mp4" for i in range(5)],
            "reference_audio_urls": [f"https://x/a-{i}.mp3" for i in range(4)],
        }
    )
    _mode, references = pipeline._shot_generation_contract(shot, state, "minimax")
    assert sum(1 for item in references if item.kind == "subject") == 9
    assert sum(1 for item in references if item.kind == "reference_video") == 3
    assert sum(1 for item in references if item.kind == "reference_audio") == 3


class _FailingThenWorkingVault:
    """Primary URL fails; the backup URL succeeds."""

    def __init__(self, artifact: MediaArtifact) -> None:
        self.artifact = artifact
        self.calls: list[str] = []

    async def materialize(
        self,
        url: str,
        *,
        kind: MediaArtifactKind,
        subject_ref: str,
        provider_id: str = "",
        task_id: str = "",
    ) -> MediaArtifact:
        self.calls.append(url)
        if url.startswith("https://x/primary"):
            raise ValueError("primary URL expired")
        return self.artifact


async def test_materialize_falls_back_to_backup_url(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    shot = state.shots[0]
    task = FilmProviderTask(
        provider_id="minimax",
        model_id="MiniMax-H3",
        mode=FilmGenerationMode.SUBJECT_REFERENCE_VIDEO,
        state=FilmProviderTaskState.SUCCEEDED,
        task_id="h3-t",
        asset_urls=["https://x/primary.mp4"],
        backup_urls=["https://x/backup.mp4"],
    )
    shots = [
        shot.model_copy(
            update={"provider_task": task, "selected_asset_url": "https://x/primary.mp4"}
        )
        if item.shot_id == shot.shot_id
        else item
        for item in state.shots
    ]
    state = state.model_copy(update={"shots": shots})
    pipeline.store.save(state)
    artifact = MediaArtifact(
        artifact_id="a1",
        kind=MediaArtifactKind.VIDEO,
        subject_ref=shot.shot_id,
        local_path="/tmp/out.mp4",
    )
    vault = _FailingThenWorkingVault(artifact)
    pipeline.vault = vault  # type: ignore[method-assign]

    updated = await pipeline.materialize_media(shot.shot_id)

    assert vault.calls == ["https://x/primary.mp4", "https://x/backup.mp4"]
    assert updated.media_artifacts[0].artifact_id == "a1"


async def test_materialize_reports_when_all_urls_fail(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    shot = state.shots[0]
    task = FilmProviderTask(
        provider_id="minimax",
        model_id="MiniMax-H3",
        mode=FilmGenerationMode.SUBJECT_REFERENCE_VIDEO,
        state=FilmProviderTaskState.SUCCEEDED,
        task_id="h3-t",
        asset_urls=["https://x/primary.mp4"],
        backup_urls=["https://x/backup.mp4"],
    )
    shots = [
        shot.model_copy(
            update={"provider_task": task, "selected_asset_url": "https://x/primary.mp4"}
        )
        if item.shot_id == shot.shot_id
        else item
        for item in state.shots
    ]
    state = state.model_copy(update={"shots": shots})
    pipeline.store.save(state)
    artifact = MediaArtifact(
        artifact_id="a1",
        kind=MediaArtifactKind.VIDEO,
        subject_ref=shot.shot_id,
        local_path="/tmp/out.mp4",
    )
    vault = _FailingThenWorkingVault(artifact)
    vault.artifact = artifact

    class _AllFailVault(_FailingThenWorkingVault):
        async def materialize(self, *args: Any, **kwargs: Any) -> MediaArtifact:
            raise ValueError("network down")

    pipeline.vault = _AllFailVault(artifact)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="媒体落盘失败"):
        await pipeline.materialize_media(shot.shot_id)


# ─── Automatic task polling (Batch C) ────────────────────────────────────────


async def test_poll_shot_task_waits_for_terminal_and_updates_shot(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    shot = state.shots[0]
    task = FilmProviderTask(
        provider_id="minimax",
        model_id="MiniMax-H3",
        mode=FilmGenerationMode.SUBJECT_REFERENCE_VIDEO,
        state=FilmProviderTaskState.RUNNING,
        task_id="h3-poll",
        api_version="v2",
    )
    shots = [
        shot.model_copy(update={"provider_task": task}) if item.shot_id == shot.shot_id else item
        for item in state.shots
    ]
    state = state.model_copy(update={"shots": shots})
    pipeline.store.save(state)

    steps: list[str] = []

    class _StubProvider:
        provider_id = "minimax"

        def __init__(self) -> None:
            self.calls = 0

        async def query(self, current: FilmProviderTask) -> FilmProviderTask:
            self.calls += 1
            if self.calls == 1:
                return current.model_copy(update={"state": FilmProviderTaskState.PENDING})
            return current.model_copy(
                update={
                    "state": FilmProviderTaskState.SUCCEEDED,
                    "asset_urls": ["https://x/done.mp4"],
                }
            )

    provider = _StubProvider()
    pipeline.settings = object()  # _require_settings gate needs a settings object
    import novel_forge.film.pipeline as pipeline_module

    def _fake_factory(provider_id: str, _settings: Any) -> Any:
        assert provider_id == "minimax"
        return provider

    pipeline_module.create_film_provider = _fake_factory  # type: ignore[method-assign]
    pipeline.on_step = lambda _step, payload: steps.append(payload.get("stage", ""))  # type: ignore[method-assign]

    updated = await pipeline.poll_shot_task(shot.shot_id, max_polls=10)

    assert provider.calls == 2
    # on_step also receives the final film_task_polled event (no stage key).
    assert steps[:2] == ["queued", "processing"]
    shot_after = next(item for item in updated.shots if item.shot_id == shot.shot_id)
    assert shot_after.qc_status == "review"
    assert shot_after.selected_asset_url == "https://x/done.mp4"


async def test_generate_shot_honors_generation_params_overrides(tmp_path: Path) -> None:
    """H3 uses supported shot overrides and strips the undeclared seed."""

    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    shot = state.shots[0]
    shots = [
        shot.model_copy(
            update={
                "provider_id": "minimax",
                "model_id": "MiniMax-H3",
                "generation_params": {
                    "resolution": "2K",
                    "seed": 987654321,
                    "watermark": True,
                    "aspect_ratio": "21:9",
                },
            }
        )
        if item.shot_id == shot.shot_id
        else item
        for item in state.shots
    ]
    state = state.model_copy(update={"shots": shots})
    pipeline.store.save(state)

    captured: dict[str, Any] = {}

    class _CaptureProvider:
        provider_id = "minimax"

        async def submit(self, request: FilmGenerationRequest) -> FilmProviderTask:
            captured["resolution"] = request.resolution
            captured["seed"] = request.seed
            captured["watermark"] = request.watermark
            captured["aspect_ratio"] = request.aspect_ratio
            return FilmProviderTask(
                provider_id="minimax",
                model_id="MiniMax-H3",
                mode=request.mode,
                state=FilmProviderTaskState.PENDING,
                task_id="h3-params",
                api_version="v2",
            )

        async def query(self, task: FilmProviderTask) -> FilmProviderTask:
            return task

    pipeline.settings = object()  # _require_settings gate
    import novel_forge.film.pipeline as pipeline_module

    pipeline_module.create_film_provider = lambda _p, _s: _CaptureProvider()  # type: ignore[method-assign]

    updated = await pipeline.generate_shot(shot.shot_id)

    assert captured["resolution"] == "2K"
    assert captured["seed"] is None
    assert captured["watermark"] is True
    assert captured["aspect_ratio"] == "21:9"
    generate_job = next(item for item in updated.jobs if item.target_id == shot.shot_id)
    assert generate_job.state == FilmJobState.RUNNING


async def test_generate_shot_passes_provider_native_audio_controls(tmp_path: Path) -> None:
    """Seedance receives its trusted audio reference and native-output switches."""

    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    shot = state.shots[0]
    shots = [
        shot.model_copy(
            update={
                "provider_id": "volcengine_ark",
                "model_id": "doubao-seedance-2-0-260128",
                "generation_params": {
                    "driving_audio_url": "https://x/dialogue.mp3",
                    "generate_audio": False,
                    "return_last_frame": False,
                    "resolution": "720P",
                },
            }
        )
        if item.shot_id == shot.shot_id
        else item
        for item in state.shots
    ]
    pipeline.store.save(state.model_copy(update={"shots": shots}))
    captured: dict[str, Any] = {}

    class _CaptureProvider:
        provider_id = "volcengine_ark"

        async def submit(self, request: FilmGenerationRequest) -> FilmProviderTask:
            captured["references"] = [(item.kind, item.url) for item in request.references]
            captured["metadata"] = request.metadata
            captured["resolution"] = request.resolution
            return FilmProviderTask(
                provider_id=self.provider_id,
                model_id=request.model_id,
                mode=request.mode,
                state=FilmProviderTaskState.PENDING,
                task_id="seedance-native-params",
            )

    pipeline.settings = object()
    import novel_forge.film.pipeline as pipeline_module

    pipeline_module.create_film_provider = lambda _p, _s: _CaptureProvider()  # type: ignore[method-assign]

    await pipeline.generate_shot(shot.shot_id)

    assert ("driving_audio", "https://x/dialogue.mp3") in captured["references"]
    assert captured["metadata"]["generate_audio"] is False
    assert captured["metadata"]["return_last_frame"] is False
    assert captured["resolution"] == "720P"


async def test_poll_shot_task_respects_cancelled_job(tmp_path: Path) -> None:
    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    shot = state.shots[0]
    task = FilmProviderTask(
        provider_id="minimax",
        model_id="MiniMax-H3",
        mode=FilmGenerationMode.SUBJECT_REFERENCE_VIDEO,
        state=FilmProviderTaskState.RUNNING,
        task_id="h3-cancel",
        api_version="v2",
    )
    shots = [
        shot.model_copy(update={"provider_task": task}) if item.shot_id == shot.shot_id else item
        for item in state.shots
    ]
    state, job, _created = pipeline.jobs.acquire(
        state.model_copy(update={"shots": shots}),
        kind=FilmJobKind.GENERATE_SHOT,
        target_id=shot.shot_id,
        provider_id="minimax",
        model_id="MiniMax-H3",
    )
    state = pipeline.jobs.cancel(state, job.job_id)[0]
    pipeline.store.save(state)

    queries = 0

    class _StubProvider:
        provider_id = "minimax"

        async def query(self, current: FilmProviderTask) -> FilmProviderTask:
            nonlocal queries
            queries += 1
            return current.model_copy(update={"state": FilmProviderTaskState.RUNNING})

    pipeline.settings = object()  # _require_settings gate needs a settings object
    import novel_forge.film.pipeline as pipeline_module

    pipeline_module.create_film_provider = lambda _p, _s: _StubProvider()  # type: ignore[method-assign]

    updated = await pipeline.poll_shot_task(shot.shot_id, max_polls=50)
    assert queries == 0  # cancelled job short-circuits before any provider call
    shot_after = next(item for item in updated.shots if item.shot_id == shot.shot_id)
    assert shot_after.provider_task is not None
    assert shot_after.provider_task.state == FilmProviderTaskState.RUNNING


async def test_cancel_job_propagates_to_queued_minimax_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _layout, pipeline = _project(tmp_path)
    state = pipeline.get_or_bootstrap()
    shot = state.shots[0]
    state, job, _created = pipeline.jobs.acquire(
        state,
        kind=FilmJobKind.GENERATE_SHOT,
        target_id=shot.shot_id,
        provider_id="minimax",
        model_id="MiniMax-H3",
    )
    state = pipeline.jobs.mark_running(state, job)
    task = FilmProviderTask(
        provider_id="minimax",
        model_id="MiniMax-H3",
        mode=FilmGenerationMode.TEXT_TO_VIDEO,
        state=FilmProviderTaskState.PENDING,
        task_id="queued-cancel",
        api_version="v2",
    )
    shots = [
        item.model_copy(update={"provider_task": task}) if item.shot_id == shot.shot_id else item
        for item in state.shots
    ]
    pipeline.store.save(state.model_copy(update={"shots": shots}))

    class _CancellableProvider:
        provider_id = "minimax"

        async def cancel(self, current: FilmProviderTask) -> FilmProviderTask:
            return current.model_copy(update={"state": FilmProviderTaskState.CANCELLED})

    pipeline.settings = object()
    monkeypatch.setattr(
        "novel_forge.film.pipeline.create_film_provider",
        lambda _provider_id, _settings: _CancellableProvider(),
    )

    cancelled = await pipeline.cancel_job(job.job_id)

    cancelled_job = next(item for item in cancelled.jobs if item.job_id == job.job_id)
    cancelled_shot = next(item for item in cancelled.shots if item.shot_id == shot.shot_id)
    assert cancelled_job.state == FilmJobState.CANCELLED
    assert cancelled_shot.provider_task is not None
    assert cancelled_shot.provider_task.state == FilmProviderTaskState.CANCELLED
