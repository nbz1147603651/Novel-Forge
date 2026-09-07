"""Engine-owned Ollama management projections and safe mutation tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import novel_forge.app_service.ollama_management as management
from novel_forge.app_service.contracts import JobKind, JobRecord, JobScope, JobState
from novel_forge.app_service.ollama_control import OllamaModel, OllamaSidecarResult
from novel_forge.gateway.profiles import ProfilesConfig


class _FakeSidecar:
    def __init__(self, *, local: bool, owned: bool) -> None:
        self.base_url = "http://127.0.0.1:11434/v1" if local else "https://ollama.example.test/v1"
        self.enabled = True
        self.auto_start = True
        self.prefer_local_route = True
        self.owned_process = owned
        self.last_result = OllamaSidecarResult(
            status="healthy" if owned else "unavailable",
            detail="test",
            available=owned,
        )

    def resolve_binary(self) -> None:
        return None


class _FakeControl:
    def __init__(self, *, local: bool, owned: bool) -> None:
        self.sidecar = _FakeSidecar(local=local, owned=owned)
        self.base_url = self.sidecar.base_url

    def version(self) -> str:
        return "0.12.0"

    def list_models(self) -> list[OllamaModel]:
        return [OllamaModel(name="qwen3:8b", size=123, details={"family": "qwen3"})]


class _FakeJobs:
    def __init__(self) -> None:
        self.records = [
            JobRecord(
                kind=JobKind.OLLAMA_PULL_MODEL,
                label="下载模型",
                scope=JobScope.SYSTEM,
                status=JobState.RUNNING,
                current_step_payload={"model": "qwen3:8b"},
            )
        ]

    def list(self) -> list[JobRecord]:
        return self.records

    def submit(self, command):  # type: ignore[no-untyped-def]
        record = JobRecord(
            kind=command.kind,
            label=command.label,
            scope=command.scope,
            project_id=command.project_id,
        )
        self.records.append(record)
        return record

    def cancel(self, job_id: str, *, reason: str) -> JobRecord:
        for record in self.records:
            if record.job_id == job_id:
                record.status = JobState.FAILED
                record.current_step = "cancelled"
                record.error = reason
                return record
        raise KeyError(job_id)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        ollama_base_url="http://127.0.0.1:11434/v1",
        ollama_model="qwen3:8b",
        ollama_embedding_model="nomic-embed-text",
        ollama_sidecar_enabled=True,
        ollama_sidecar_auto_start=True,
        ollama_sidecar_binary_path="/private/path/never-expose",
        ollama_sidecar_models_dir="/private/models/never-expose",
        ollama_sidecar_prefer_local=True,
    )


def test_ollama_view_hides_engine_paths_and_exposes_system_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ProfilesConfig()
    monkeypatch.setattr(management, "get_ollama_control_service", lambda _settings: _FakeControl(local=True, owned=True))
    monkeypatch.setattr(management, "load_or_import_profiles", lambda: config)

    view = management.ollama_manager_view(_settings(), _FakeJobs())  # type: ignore[arg-type]

    assert view.ownership == "engine_owned"
    assert view.storage.scope == "engine_managed"
    assert "/private" not in view.model_dump_json()
    assert view.models[0].roles == ["generation"]
    assert view.active_operations[0].task_id
    assert view.active_operations[0].state == "running"


def test_ollama_delete_confirmation_is_bound_to_revision_and_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ProfilesConfig()
    monkeypatch.setattr(management, "get_ollama_control_service", lambda _settings: _FakeControl(local=False, owned=False))
    monkeypatch.setattr(management, "load_or_import_profiles", lambda: config)
    view = management.ollama_manager_view(_settings(), _FakeJobs())  # type: ignore[arg-type]
    token = view.routing_impact_by_model["qwen3:8b"].confirmation_token

    management.verify_delete_confirmation(
        model="qwen3:8b", revision=view.revision, token=token
    )
    with pytest.raises(management.OllamaConfigurationError):
        management.verify_delete_confirmation(model="qwen3:8b", revision="stale", token=token)


def test_role_and_detach_mutations_persist_config_without_desktop_dependency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = ProfilesConfig()
    persisted: list[dict[str, str]] = []
    monkeypatch.setattr(management, "load_or_import_profiles", lambda: config)
    monkeypatch.setattr(management, "get_profiles_path", lambda: tmp_path / "model_profiles.json")
    monkeypatch.setattr(
        management,
        "persist_settings_environment",
        lambda **kwargs: persisted.append(dict(kwargs["creation_parameters"] or {})),
    )
    monkeypatch.setattr(management, "clear_env_keys", lambda _keys: None)
    monkeypatch.setattr(management, "reset_settings", lambda: None)
    settings = _settings()
    revision = management.ollama_revision(settings, config)

    management.set_ollama_model_roles(
        settings,
        expected_revision=revision,
        model="qwen3:8b",
        managed=True,
        generation=True,
        embedding=False,
    )
    assert config.get_profile("ollama:qwen3:8b") is not None
    assert persisted[-1]["ollama-model"] == "qwen3:8b"

    revision = management.ollama_revision(settings, config)
    detached = management.detach_ollama_model_configuration(
        settings,
        expected_revision=revision,
        model="qwen3:8b",
    )
    assert detached.removed_profile_ids == ["ollama:qwen3:8b"]
    assert config.get_profile("ollama:qwen3:8b") is None
    assert (tmp_path / "model_profiles.json").exists()


def test_command_service_submits_only_system_jobs_and_blocks_runtime_during_model_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ProfilesConfig()
    jobs = _FakeJobs()
    monkeypatch.setattr(
        management,
        "get_ollama_control_service",
        lambda _settings: _FakeControl(local=True, owned=True),
    )
    monkeypatch.setattr(management, "load_or_import_profiles", lambda: config)
    settings = _settings()
    commands = management.OllamaEngineCommandService(jobs)  # type: ignore[arg-type]
    revision = commands.view(settings).revision

    with pytest.raises(management.OllamaConfigurationError, match="下载或删除"):
        commands.submit_runtime(
            settings,
            expected_revision=revision,
            idempotency_key="restart-ollama-123",
            operation="restart",
        )

    jobs.records.clear()
    record = commands.submit_pull(
        settings,
        expected_revision=revision,
        idempotency_key="pull-qwen3-123456",
        model="qwen3:8b",
    )
    assert record.scope == JobScope.SYSTEM
    assert record.project_id == ""
    assert record.kind == JobKind.OLLAMA_PULL_MODEL


def test_dual_clients_observe_the_same_engine_system_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nimo submit, PySide observe/cancel, and Nimo re-read share one Engine state."""

    config = ProfilesConfig()
    jobs = _FakeJobs()
    jobs.records.clear()
    monkeypatch.setattr(
        management,
        "get_ollama_control_service",
        lambda _settings: _FakeControl(local=True, owned=True),
    )
    monkeypatch.setattr(management, "load_or_import_profiles", lambda: config)
    settings = _settings()
    nimo = management.OllamaEngineCommandService(jobs)  # type: ignore[arg-type]
    pyside = management.OllamaEngineCommandService(jobs)  # type: ignore[arg-type]
    revision = nimo.view(settings).revision

    submitted = nimo.submit_pull(
        settings,
        expected_revision=revision,
        idempotency_key="nimo-pull-qwen3-123",
        model="qwen3:8b",
    )
    observed = pyside.view(settings)
    assert [operation.task_id for operation in observed.active_operations] == [submitted.job_id]
    assert observed.active_operations[0].state == "queued"

    jobs.cancel(submitted.job_id, reason="PySide cancel")
    reread = nimo.view(settings)
    assert reread.active_operations[0].state == "failed"


def test_remote_configuration_cannot_change_engine_ollama_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ProfilesConfig()
    monkeypatch.setattr(management, "load_or_import_profiles", lambda: config)
    settings = _settings()
    revision = management.ollama_revision(settings, config)

    with pytest.raises(management.OllamaConfigurationError, match="Ollama 地址"):
        management.configure_ollama_runtime(
            settings,
            expected_revision=revision,
            base_url="http://127.0.0.1:11434",
        )
