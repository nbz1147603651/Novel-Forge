"""Tests for desktop-managed Ollama sidecar lifecycle."""

from __future__ import annotations

from pathlib import Path

import novel_forge.app_service.ollama_control as sidecar_module
from novel_forge.app_service.ollama_control import (
    OllamaSidecarService,
)
from novel_forge.app_service.ollama_control import (
    host_value as _host_value,
)
from novel_forge.app_service.ollama_control import (
    native_base_url as _native_base_url,
)


class _FakeProcess:
    def __init__(self, *, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        return self.returncode or 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_native_base_url_normalizes_openai_and_native_paths() -> None:
    assert _native_base_url("http://localhost:11434/v1") == "http://localhost:11434"
    assert _native_base_url("http://localhost:11434/api") == "http://localhost:11434"
    assert _host_value("http://127.0.0.1:11434/v1") == "127.0.0.1:11434"


def test_sidecar_resolves_explicit_binary_path(tmp_path: Path) -> None:
    binary = tmp_path / "ollama"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")

    service = OllamaSidecarService(
        base_url="http://127.0.0.1:11434/v1",
        binary_path=binary,
    )

    assert service.resolve_binary() == binary.resolve()


def test_sidecar_resolves_binary_from_explicit_directory(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    binary = bundle_dir / ("ollama.exe" if sidecar_module.sys.platform == "win32" else "ollama")
    binary.write_text("#!/bin/sh\n", encoding="utf-8")

    service = OllamaSidecarService(
        base_url="http://127.0.0.1:11434/v1",
        binary_path=bundle_dir,
    )

    assert service.resolve_binary() == binary.resolve()


def test_managed_ollama_uses_conservative_model_residency_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    binary = tmp_path / "ollama"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    captured: dict[str, str] = {}
    for key in ("OLLAMA_MAX_LOADED_MODELS", "OLLAMA_NUM_PARALLEL", "OLLAMA_KEEP_ALIVE"):
        monkeypatch.delenv(key, raising=False)

    def fake_popen(*_args, **kwargs):
        captured.update(kwargs["env"])
        return _FakeProcess()

    monkeypatch.setattr(sidecar_module.subprocess, "Popen", fake_popen)
    service = OllamaSidecarService(
        base_url="http://127.0.0.1:11434/v1",
        binary_path=binary,
        resource_budget="light",
    )

    service._launch_process(binary)

    assert captured["OLLAMA_MAX_LOADED_MODELS"] == "1"
    assert captured["OLLAMA_NUM_PARALLEL"] == "1"
    assert captured["OLLAMA_KEEP_ALIVE"] == "15s"


def test_sidecar_autostarts_when_unhealthy_and_binary_exists(
    monkeypatch,
    tmp_path: Path,
) -> None:
    binary = tmp_path / "ollama"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    service = OllamaSidecarService(
        base_url="http://127.0.0.1:11434/v1",
        binary_path=binary,
        poll_interval_s=0.0,
        startup_timeout_s=1.0,
    )

    checks = iter([False, False, True])
    launches: list[tuple[Path, str, str | None]] = []

    def fake_check_health(*, timeout_s: float) -> bool:
        _ = timeout_s
        return next(checks)

    def fake_launch(binary_path: Path) -> _FakeProcess:
        launches.append(
            (
                binary_path,
                _host_value(service.base_url),
                str(service.resolve_models_dir()) if service.resolve_models_dir() else None,
            )
        )
        return _FakeProcess()

    monkeypatch.setattr(service, "_check_health", fake_check_health)
    monkeypatch.setattr(service, "_launch_process", fake_launch)
    monkeypatch.setattr(sidecar_module.time, "sleep", lambda _: None)

    result = service.ensure_available()

    assert result.status == "started"
    assert result.available is True
    assert result.auto_started is True
    assert launches == [(binary.resolve(), "127.0.0.1:11434", None)]
    assert service.owned_process is True


def test_sidecar_reports_missing_binary_when_unhealthy(monkeypatch, tmp_path: Path) -> None:
    service = OllamaSidecarService(
        base_url="http://127.0.0.1:11434/v1",
        runtime_root=tmp_path,
    )

    monkeypatch.setattr(service, "_check_health", lambda *, timeout_s: False)

    result = service.ensure_available()

    assert result.status == "missing-binary"
    assert result.available is False


def test_sidecar_shutdown_only_terminates_owned_process(tmp_path: Path) -> None:
    binary = tmp_path / "ollama"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    service = OllamaSidecarService(
        base_url="http://127.0.0.1:11434/v1",
        binary_path=binary,
    )
    process = _FakeProcess()
    service._process = process
    service._owned_process = True

    service.shutdown(timeout_s=0.1)

    assert process.terminated is True
    assert service.owned_process is False
