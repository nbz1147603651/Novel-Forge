"""Application-managed Python toolchain tests."""

from __future__ import annotations

import sys
from pathlib import Path

from novel_forge.tts.model_center.python_runtime import (
    ManagedPythonRuntime,
    python_version_satisfies,
)


def _uv_path(root: Path) -> Path:
    path = root / ("uv.exe" if sys.platform == "win32" else "uv")
    path.write_text("", encoding="utf-8")
    return path


def test_managed_python_creates_fixed_uv_environment(tmp_path) -> None:
    uv = _uv_path(tmp_path)
    calls: list[tuple[list[str], Path, dict[str, str]]] = []
    runtime = ManagedPythonRuntime(
        tmp_path,
        uv_executable=uv,
        command_runner=lambda command, cwd, env: calls.append(
            (list(command), cwd, dict(env))
        ),
        preferred_python=tmp_path / "missing-python",
    )
    destination = tmp_path / "runtime"

    runtime.create_environment(destination)

    command, cwd, env = calls[0]
    assert command[:5] == [str(uv), "venv", "--python", "3.12", "--managed-python"]
    assert command[-1] == str(destination)
    assert cwd == tmp_path.resolve()
    assert env["UV_PYTHON_INSTALL_DIR"] == str(tmp_path.resolve() / "python")
    assert env["UV_CACHE_DIR"] == str(tmp_path.resolve() / "cache" / "uv")


def test_managed_python_prefers_compatible_project_virtualenv(tmp_path) -> None:
    uv = _uv_path(tmp_path)
    calls: list[list[str]] = []
    runtime = ManagedPythonRuntime(
        tmp_path,
        uv_executable=uv,
        command_runner=lambda command, _cwd, _env: calls.append(list(command)),
        preferred_python=Path(sys.executable),
    )
    destination = tmp_path / "runtime"

    runtime.create_environment(destination, python_constraint=">=3.11,<3.13")

    assert calls[0][:4] == [str(uv), "venv", "--python", str(Path(sys.executable))]
    assert "--managed-python" not in calls[0]
    metadata = runtime.creator_metadata(destination)
    assert metadata["source"] == "project_venv"
    assert metadata["base_python"] == str(Path(sys.executable))


def test_python_version_constraint_supports_project_runtime_range() -> None:
    assert python_version_satisfies("3.11.15", ">=3.11,<3.13") is True
    assert python_version_satisfies("3.12.13", ">=3.11,<3.13") is True
    assert python_version_satisfies("3.13.0", ">=3.11,<3.13") is False
    assert python_version_satisfies("3.12.7", "==3.12.*") is True


def test_managed_python_installs_packages_with_uv(tmp_path) -> None:
    uv = _uv_path(tmp_path)
    calls: list[tuple[list[str], Path, dict[str, str]]] = []
    runtime = ManagedPythonRuntime(
        tmp_path,
        uv_executable=uv,
        command_runner=lambda command, cwd, env: calls.append(
            (list(command), cwd, dict(env))
        ),
    )
    environment = tmp_path / "runtime"

    runtime.install_packages(environment, ["fastapi", "uvicorn"])

    command, cwd, _env = calls[0]
    assert command == [
        str(uv),
        "pip",
        "install",
        "--python",
        str(runtime.python_path(environment)),
        "--upgrade",
        "fastapi",
        "uvicorn",
    ]
    assert cwd == environment


def test_managed_python_can_freeze_dependency_resolution_by_date(tmp_path) -> None:
    uv = _uv_path(tmp_path)
    calls: list[list[str]] = []
    runtime = ManagedPythonRuntime(
        tmp_path,
        uv_executable=uv,
        command_runner=lambda command, _cwd, _env: calls.append(list(command)),
    )

    runtime.install_packages(
        tmp_path / "runtime",
        ["qwen-tts"],
        exclude_newer="2026-07-14",
    )

    assert calls[0][-3:] == ["--exclude-newer", "2026-07-14", "qwen-tts"]
