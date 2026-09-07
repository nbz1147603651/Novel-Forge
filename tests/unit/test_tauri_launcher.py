"""Tests for the dual-UI Tauri launcher boundary."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, call

import pytest

from novel_forge.desktop import tauri_launcher
from novel_forge.desktop.tauri_launcher import (
    LocalBackendOwnership,
    _backend_target,
    _ownership_matches_runtime,
    _prepare_client_command,
    _resolve_local_backend,
    _select_available_local_backend_target,
    _tauri_command,
)


def test_backend_target_accepts_loopback_http() -> None:
    target = _backend_target("http://127.0.0.1:8123/")

    assert target.base_url == "http://127.0.0.1:8123"
    assert target.host == "127.0.0.1"
    assert target.port == 8123
    assert target.is_loopback is True


def test_backend_target_requires_https_for_remote_hosts() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        _backend_target("http://engine.example.test")


def test_tauri_command_uses_explicit_packaged_binary(tmp_path: Path) -> None:
    binary = tmp_path / "nimo-ui"
    binary.write_bytes(b"binary")

    command, cwd = _tauri_command(
        repo_root=tmp_path,
        explicit_binary=str(binary),
        force_dev=False,
    )

    assert command == [str(binary)]
    assert cwd == tmp_path


def test_prepare_client_command_reuses_existing_nimo_frontend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = ["pnpm", "--filter", "@nimo/desktop", "tauri", "dev"]
    monkeypatch.setattr(tauri_launcher, "_nimo_frontend_ready", lambda: True)

    prepared, reused = _prepare_client_command(command)

    assert reused is True
    assert prepared[:-2] == command
    assert prepared[-2] == "--config"
    assert prepared[-1] == '{"build":{"beforeDevCommand":null}}'


def test_prepare_client_command_rejects_foreign_port_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = ["pnpm", "--filter", "@nimo/desktop", "tauri", "dev"]
    monkeypatch.setattr(tauri_launcher, "_nimo_frontend_ready", lambda: False)
    monkeypatch.setattr(tauri_launcher, "_tcp_port_in_use", lambda _host, _port: True)

    with pytest.raises(RuntimeError, match="1420"):
        _prepare_client_command(command, frontend_port_release_grace_s=0)


def test_prepare_client_command_waits_for_just_closing_frontend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = ["pnpm", "--filter", "@nimo/desktop", "tauri", "dev"]
    port_states = iter([True, False])
    sleep = Mock()
    monkeypatch.setattr(tauri_launcher, "_nimo_frontend_ready", lambda: False)
    monkeypatch.setattr(tauri_launcher, "_tcp_port_in_use", lambda _host, _port: next(port_states))
    monkeypatch.setattr(tauri_launcher.time, "sleep", sleep)

    prepared, reused = _prepare_client_command(command, frontend_port_release_grace_s=1)

    assert prepared == command
    assert reused is False
    sleep.assert_called_once()


def test_prepare_client_command_reuses_frontend_that_finishes_starting_during_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = ["pnpm", "--filter", "@nimo/desktop", "tauri", "dev"]
    ready_states = iter([False, True])
    monkeypatch.setattr(tauri_launcher, "_nimo_frontend_ready", lambda: next(ready_states))
    monkeypatch.setattr(tauri_launcher, "_tcp_port_in_use", lambda _host, _port: True)
    monkeypatch.setattr(tauri_launcher.time, "sleep", Mock())

    prepared, reused = _prepare_client_command(command, frontend_port_release_grace_s=1)

    assert reused is True
    assert prepared[:-2] == command


def test_prepare_client_command_keeps_normal_dev_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = ["pnpm", "--filter", "@nimo/desktop", "tauri", "dev"]
    monkeypatch.setattr(tauri_launcher, "_nimo_frontend_ready", lambda: False)
    monkeypatch.setattr(tauri_launcher, "_tcp_port_in_use", lambda _host, _port: False)

    prepared, reused = _prepare_client_command(command)

    assert prepared == command
    assert reused is False


def test_prepare_client_command_ignores_packaged_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tauri_launcher,
        "_nimo_frontend_ready",
        lambda: pytest.fail("packaged launch must not probe the dev server"),
    )

    prepared, reused = _prepare_client_command(["/Applications/NIMO.app/Contents/MacOS/NIMO"])

    assert prepared == ["/Applications/NIMO.app/Contents/MacOS/NIMO"]
    assert reused is False


def test_select_available_local_backend_target_uses_next_free_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _backend_target("http://127.0.0.1:8000")
    monkeypatch.setattr(tauri_launcher, "_health_ready", lambda _url: False)
    monkeypatch.setattr(
        tauri_launcher,
        "_tcp_port_in_use",
        lambda _host, port: port in {8000, 8001},
    )

    fallback = _select_available_local_backend_target(target)

    assert fallback.base_url == "http://127.0.0.1:8002"
    assert fallback.port == 8002


def test_select_available_local_backend_target_reuses_healthy_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _backend_target("http://127.0.0.1:8000")
    monkeypatch.setattr(tauri_launcher, "_health_ready", lambda _url: True)
    monkeypatch.setattr(
        tauri_launcher,
        "_tcp_port_in_use",
        lambda _host, _port: pytest.fail("healthy Engine must not probe fallback ports"),
    )

    assert _select_available_local_backend_target(target) == target


def test_main_uses_fallback_port_for_the_default_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    default_target = _backend_target("http://127.0.0.1:8000")
    fallback_target = _backend_target("http://127.0.0.1:8001")
    captured: dict[str, object] = {}
    preparation_order: list[str] = []

    class _ClientProcess:
        def wait(self) -> int:
            return 0

    def _fake_popen(command: list[str], *, cwd: str, env: dict[str, str]) -> _ClientProcess:
        captured["command"] = command
        captured["cwd"] = cwd
        captured["env"] = env
        return _ClientProcess()

    monkeypatch.delenv("NIMO_ENGINE_BASE_URL", raising=False)
    monkeypatch.setattr(
        tauri_launcher,
        "_tauri_command",
        lambda **_kwargs: (["nimo-client"], tmp_path),
    )
    monkeypatch.setattr(
        tauri_launcher,
        "_select_available_local_backend_target",
        lambda target: (
            preparation_order.append("backend")
            or (fallback_target if target == default_target else target)
        ),
    )
    monkeypatch.setattr(
        tauri_launcher,
        "_prepare_client_command",
        lambda command: preparation_order.append("frontend") or (command, False),
    )
    monkeypatch.setattr(
        tauri_launcher,
        "_resolve_local_backend",
        lambda **_kwargs: (tauri_launcher.BackendStartupState("external", False), None),
    )
    monkeypatch.setattr(tauri_launcher.subprocess, "Popen", _fake_popen)

    assert tauri_launcher.main([]) == 0
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["NIMO_ENGINE_BASE_URL"] == "http://127.0.0.1:8001"
    assert environment["NIMO_ENGINE_MODE"] == "legacy"
    assert preparation_order[:2] == ["frontend", "backend"]
    assert "已自动改用本地 Engine 端口 8001" in capsys.readouterr().err


def test_main_stops_before_backend_selection_when_frontend_is_busy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _frontend_busy(_command: list[str]) -> tuple[list[str], bool]:
        raise RuntimeError("前端端口 1420 仍被占用")

    monkeypatch.delenv("NIMO_ENGINE_BASE_URL", raising=False)
    monkeypatch.setattr(
        tauri_launcher, "_tauri_command", lambda **_kwargs: (["nimo-client"], tmp_path)
    )
    monkeypatch.setattr(tauri_launcher, "_prepare_client_command", _frontend_busy)
    monkeypatch.setattr(
        tauri_launcher,
        "_select_available_local_backend_target",
        lambda _target: pytest.fail("frontend conflict must prevent backend selection"),
    )

    assert tauri_launcher.main([]) == 1


def test_main_supervises_stable_source_revision_and_cleans_up_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _backend_target("http://127.0.0.1:8000")
    original, replacement, client = Mock(pid=1234), Mock(pid=2345), Mock()
    client.wait.side_effect = [
        tauri_launcher.subprocess.TimeoutExpired("nimo-client", 3),
        tauri_launcher.subprocess.TimeoutExpired("nimo-client", 3),
        0,
    ]
    updated = LocalBackendOwnership(target.base_url, 2345, "replacement", "new", False)
    restart = Mock(return_value=(replacement, updated))
    stop, remove = Mock(), Mock()
    monkeypatch.setattr(tauri_launcher, "_tauri_command", lambda **_kwargs: (["client"], tmp_path))
    monkeypatch.setattr(tauri_launcher, "_prepare_client_command", lambda command: (command, False))
    monkeypatch.setattr(
        tauri_launcher, "current_engine_revision", Mock(side_effect=["old", "new", "new"])
    )
    monkeypatch.setattr(
        tauri_launcher,
        "_resolve_local_backend",
        lambda **_kwargs: (tauri_launcher.BackendStartupState("managed-local", False), None),
    )
    monkeypatch.setattr(tauri_launcher, "_health_ready", lambda _url: False)
    monkeypatch.setattr(tauri_launcher, "_spawn_backend", Mock(return_value=original))
    monkeypatch.setattr(tauri_launcher, "_wait_for_backend", Mock())
    monkeypatch.setattr(tauri_launcher, "_write_ownership", Mock())
    monkeypatch.setattr(tauri_launcher, "_restart_idle_owned_backend", restart)
    monkeypatch.setattr(tauri_launcher, "_stop_process", stop)
    monkeypatch.setattr(tauri_launcher, "_remove_ownership", remove)
    monkeypatch.setattr(tauri_launcher.subprocess, "Popen", Mock(return_value=client))

    assert tauri_launcher.main(["--backend-url", target.base_url]) == 0
    restart.assert_called_once()
    assert restart.call_args.kwargs["process"] is original
    assert restart.call_args.kwargs["expected_revision"] == "new"
    assert client.wait.call_args_list == [call(timeout=3)] * 3
    stop.assert_called_once_with(replacement)
    remove.assert_called_once_with(target)


def test_owned_backend_requires_matching_runtime_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ownership = LocalBackendOwnership(
        base_url="http://127.0.0.1:8000",
        backend_pid=1234,
        instance_id="owned-instance",
        revision="src-current",
        reload_enabled=False,
    )
    monkeypatch.setattr(tauri_launcher, "_pid_alive", lambda pid: pid == 1234)

    assert _ownership_matches_runtime(
        _backend_target("http://127.0.0.1:8000"),
        ownership,
        {"instanceId": "owned-instance", "managedBy": "nimo"},
    )
    assert not _ownership_matches_runtime(
        _backend_target("http://127.0.0.1:8000"),
        ownership,
        {"instanceId": "someone-else", "managedBy": "nimo"},
    )
    # Ownership files created by the former nimo-t command remain safe to
    # adopt because PID and the opaque runtime instance token still match.
    assert _ownership_matches_runtime(
        _backend_target("http://127.0.0.1:8000"),
        ownership,
        {"instanceId": "owned-instance", "managedBy": "nimo-t"},
    )


def test_spawned_backend_records_primary_nimo_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen = Mock(return_value=Mock())
    monkeypatch.setattr(tauri_launcher.subprocess, "Popen", popen)

    tauri_launcher._spawn_backend(
        target=_backend_target("http://127.0.0.1:8000"),
        environment={},
        instance_id="instance-token",
        backend_reload=False,
    )

    assert popen.call_args.kwargs["env"]["NOVEL_FORGE_LOCAL_ENGINE_MANAGER"] == "nimo"


def test_external_stale_backend_is_never_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _backend_target("http://127.0.0.1:8000")
    monkeypatch.setattr(tauri_launcher, "_health_ready", lambda _url: True)
    monkeypatch.setattr(
        tauri_launcher,
        "_runtime_probe",
        lambda _url, **_kwargs: {
            "status": "ready",
            "bootRevision": "src-old",
            "currentRevision": "src-old",
            "instanceId": "external",
            "managedBy": "external",
        },
    )
    monkeypatch.setattr(tauri_launcher, "_load_ownership", lambda _target: None)
    monkeypatch.setattr(
        tauri_launcher,
        "_stop_owned_backend",
        lambda _ownership: pytest.fail("external process must never be stopped"),
    )

    state, ownership = _resolve_local_backend(
        target=target,
        access_token="",
        expected_revision="src-current",
        backend_reload=False,
        restart_owned_backend=False,
    )

    assert state.mode == "external"
    assert state.read_only is True
    assert "无法安全接管" in state.diagnostic
    assert ownership is None


def test_unhealthy_listener_is_reported_before_spawning_a_second_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _backend_target("http://127.0.0.1:8000")
    monkeypatch.setattr(tauri_launcher, "_health_ready", lambda _url: False)
    monkeypatch.setattr(tauri_launcher, "_tcp_port_in_use", lambda _host, _port: True)

    with pytest.raises(RuntimeError, match="端口 8000 已被其他服务占用"):
        _resolve_local_backend(
            target=target,
            access_token="",
            expected_revision="src-current",
            backend_reload=False,
            restart_owned_backend=False,
        )


def test_owned_stale_backend_with_active_jobs_is_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _backend_target("http://127.0.0.1:8000")
    ownership = LocalBackendOwnership(
        base_url=target.base_url,
        backend_pid=1234,
        instance_id="owned-instance",
        revision="src-old",
        reload_enabled=False,
    )
    monkeypatch.setattr(tauri_launcher, "_health_ready", lambda _url: True)
    monkeypatch.setattr(tauri_launcher, "_load_ownership", lambda _target: ownership)
    monkeypatch.setattr(tauri_launcher, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        tauri_launcher,
        "_runtime_probe",
        lambda _url, **_kwargs: {
            "status": "restartRequired",
            "bootRevision": "src-old",
            "currentRevision": "src-current",
            "instanceId": "owned-instance",
            "managedBy": "nimo",
            "activeJobCount": 1,
            "queuedJobCount": 0,
        },
    )
    monkeypatch.setattr(
        tauri_launcher,
        "_stop_owned_backend",
        lambda _ownership: pytest.fail("active jobs must be protected"),
    )

    state, returned_ownership = _resolve_local_backend(
        target=target,
        access_token="",
        expected_revision="src-current",
        backend_reload=False,
        restart_owned_backend=False,
    )

    assert state.mode == "managed-protected"
    assert state.read_only is True
    assert returned_ownership == ownership


@pytest.mark.parametrize(
    "overrides",
    [
        {"activeJobCount": 1},
        {"queuedJobCount": 1},
        {"activeJobCount": None},
        {"queuedJobCount": -1},
        {"instanceId": "another-process"},
        {"managedBy": "external"},
        {"currentRevision": "source-still-changing"},
        {"status": "ready", "bootRevision": "src-current"},
    ],
)
def test_live_restart_never_interrupts_work_or_an_external_process(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object]
) -> None:
    target = _backend_target("http://127.0.0.1:8000")
    ownership = LocalBackendOwnership(target.base_url, 1234, "owned", "src-old", False)
    process = Mock(pid=1234)
    process.poll.return_value = None
    probe = {
        "status": "restartRequired",
        "bootRevision": "src-old",
        "currentRevision": "src-current",
        "instanceId": "owned",
        "managedBy": "nimo",
        "activeJobCount": 0,
        "queuedJobCount": 0,
        **overrides,
    }
    monkeypatch.setattr(tauri_launcher, "_runtime_probe", lambda *_args, **_kwargs: probe)
    monkeypatch.setattr(tauri_launcher, "_pid_alive", lambda _pid: True)
    stop = Mock()
    monkeypatch.setattr(tauri_launcher, "_stop_process", stop)
    result = tauri_launcher._restart_idle_owned_backend(
        target=target,
        process=process,
        ownership=ownership,
        environment={},
        access_token="",
        expected_revision="src-current",
        timeout_s=1,
    )
    assert result == (process, ownership)
    stop.assert_not_called()


def test_live_restart_replaces_only_idle_owned_backend_and_records_new_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _backend_target("http://127.0.0.1:8000")
    ownership = LocalBackendOwnership(target.base_url, 1234, "owned", "src-old", False)
    process = Mock(pid=1234)
    process.poll.return_value = None
    replacement = Mock(pid=2345)
    probe = {
        "status": "restartRequired",
        "bootRevision": "src-old",
        "currentRevision": "src-current",
        "instanceId": "owned",
        "managedBy": "nimo",
        "activeJobCount": 0,
        "queuedJobCount": 0,
    }
    monkeypatch.setattr(tauri_launcher, "_runtime_probe", lambda *_args, **_kwargs: probe)
    monkeypatch.setattr(tauri_launcher, "_pid_alive", lambda _pid: True)
    stop, remove, spawn, wait, write = (
        Mock(),
        Mock(),
        Mock(return_value=replacement),
        Mock(),
        Mock(),
    )
    monkeypatch.setattr(tauri_launcher, "_stop_process", stop)
    monkeypatch.setattr(tauri_launcher, "_remove_ownership", remove)
    monkeypatch.setattr(tauri_launcher, "_spawn_backend", spawn)
    monkeypatch.setattr(tauri_launcher, "_wait_for_backend", wait)
    monkeypatch.setattr(tauri_launcher, "_write_ownership", write)
    result, updated = tauri_launcher._restart_idle_owned_backend(
        target=target,
        process=process,
        ownership=ownership,
        environment={},
        access_token="",
        expected_revision="src-current",
        timeout_s=1,
    )
    stop.assert_called_once_with(process)
    remove.assert_called_once_with(target)
    assert result is replacement
    assert updated.backend_pid == 2345
    assert updated.instance_id != ownership.instance_id
    assert updated.revision == "src-current"
    assert spawn.call_args.kwargs["instance_id"] == updated.instance_id
    assert wait.call_args.kwargs["instance_id"] == updated.instance_id
    write.assert_called_once_with(target, updated)
