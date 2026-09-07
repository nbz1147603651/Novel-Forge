"""Cross-platform application-managed Python toolchain powered by uv."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

EnvironmentCommandRunner = Callable[[Sequence[str], Path, Mapping[str, str]], None]
RUNTIME_PYTHON_VERSION = "3.12"
_CREATOR_METADATA_FILE = ".novel_forge_python.json"


def python_version_satisfies(version: str, constraint: str) -> bool:
    """Return whether a dotted Python version satisfies our small constraint grammar."""

    try:
        value = tuple(int(part) for part in version.strip().split(".")[:3])
    except (TypeError, ValueError):
        return False
    value += (0,) * (3 - len(value))
    for raw_clause in constraint.split(","):
        clause = raw_clause.strip()
        if not clause:
            continue
        if clause.startswith("==") and clause.endswith(".*"):
            expected = tuple(int(part) for part in clause[2:-2].split("."))
            if value[: len(expected)] != expected:
                return False
            continue
        operator = next(
            (
                candidate
                for candidate in (">=", "<=", "==", ">", "<")
                if clause.startswith(candidate)
            ),
            "",
        )
        if not operator:
            return False
        try:
            expected = tuple(int(part) for part in clause[len(operator) :].split("."))
        except ValueError:
            return False
        expected += (0,) * (3 - len(expected))
        if operator == ">=" and not value >= expected:
            return False
        if operator == "<=" and not value <= expected:
            return False
        if operator == "==" and not value == expected:
            return False
        if operator == ">" and not value > expected:
            return False
        if operator == "<" and not value < expected:
            return False
    return True


class ManagedPythonRuntimeError(RuntimeError):
    """The bundled Python toolchain could not provision a runtime."""


class ManagedPythonRuntime:
    """Own one uv-managed CPython and create isolated sidecar environments."""

    def __init__(
        self,
        root: Path,
        *,
        uv_executable: Path | None = None,
        command_runner: EnvironmentCommandRunner | None = None,
        preferred_python: Path | None = None,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.python_root = self.root / "python"
        self.cache_root = self.root / "cache" / "uv"
        self.uv_executable = self._find_uv(uv_executable)
        self._command_runner = command_runner or self._run_command
        self.preferred_python = self._find_preferred_python(preferred_python)

    def create_environment(
        self,
        destination: Path,
        *,
        python_constraint: str = ">=3.11,<3.13",
    ) -> None:
        uv = self._require_uv()
        preferred_version = (
            self.python_version(self.preferred_python) if self.preferred_python is not None else ""
        )
        use_preferred = bool(
            self.preferred_python is not None
            and preferred_version
            and python_version_satisfies(preferred_version, python_constraint)
        )
        selected_python = str(self.preferred_python) if use_preferred else RUNTIME_PYTHON_VERSION
        managed_flag = [] if use_preferred else ["--managed-python"]
        self._command_runner(
            [
                str(uv),
                "venv",
                "--python",
                selected_python,
                *managed_flag,
                "--clear",
                "--no-project",
                "--no-config",
                str(destination),
            ],
            self.root,
            self.environment(),
        )
        destination.mkdir(parents=True, exist_ok=True)
        (destination / _CREATOR_METADATA_FILE).write_text(
            json.dumps(
                {
                    "source": "project_venv" if use_preferred else "uv_managed",
                    "base_python": selected_python,
                    "base_python_version": (
                        preferred_version if use_preferred else RUNTIME_PYTHON_VERSION
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def install_packages(
        self,
        environment: Path,
        packages: Sequence[str],
        *,
        exclude_newer: str = "",
    ) -> None:
        uv = self._require_uv()
        resolution_guard = ["--exclude-newer", exclude_newer] if exclude_newer else []
        self._command_runner(
            [
                str(uv),
                "pip",
                "install",
                "--python",
                str(self.python_path(environment)),
                "--upgrade",
                *resolution_guard,
                *packages,
            ],
            environment,
            self.environment(),
        )

    def prune_cache(self) -> None:
        """Remove unreachable uv objects while retaining reusable package artifacts."""

        uv = self._require_uv()
        self._command_runner(
            [str(uv), "cache", "prune", "--no-config"],
            self.root,
            self.environment(),
        )

    def environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "UV_PYTHON_INSTALL_DIR": str(self.python_root),
            "UV_CACHE_DIR": str(self.cache_root),
        }

    @staticmethod
    def python_path(environment: Path) -> Path:
        return environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    @staticmethod
    def python_version(candidate: Path) -> str:
        try:
            completed = subprocess.run(
                [
                    str(candidate),
                    "-c",
                    "import sys; print('.'.join(map(str, sys.version_info[:3])))",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            return completed.stdout.strip() if completed.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    @staticmethod
    def creator_metadata(environment: Path) -> dict[str, str]:
        path = environment / _CREATOR_METADATA_FILE
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {str(key): str(item) for key, item in value.items() if item is not None}

    def _require_uv(self) -> Path:
        if self.uv_executable is None:
            raise ManagedPythonRuntimeError("应用缺少 uv 运行时管理器，请重新安装完整版 NIMO。")
        return self.uv_executable

    @staticmethod
    def _find_uv(requested: Path | None) -> Path | None:
        source_root = Path(__file__).resolve().parents[3]
        executable = "uv.exe" if sys.platform == "win32" else "uv"
        bundled_value = str(getattr(sys, "_MEIPASS", "") or "").strip()
        bundled_root = Path(bundled_value) if bundled_value else None
        located = shutil.which("uv")
        candidates = [
            requested,
            bundled_root / "vendor" / "uv" / executable if bundled_root is not None else None,
            source_root / "vendor" / "uv" / executable,
            Path(sys.executable).parent / executable,
            Path(located) if located else None,
        ]
        for candidate in candidates:
            if candidate is not None and candidate.expanduser().is_file():
                return candidate.expanduser().absolute()
        return None

    @staticmethod
    def _find_preferred_python(requested: Path | None) -> Path | None:
        if requested is not None:
            candidate = requested.expanduser()
            return candidate.absolute() if candidate.is_file() else None
        executable = "python.exe" if sys.platform == "win32" else "python"
        virtual_env = str(os.environ.get("VIRTUAL_ENV") or "").strip()
        virtual_env_python = (
            Path(virtual_env) / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            if virtual_env
            else None
        )
        source_root = Path(__file__).resolve().parents[3]
        candidates = [
            virtual_env_python,
            Path.cwd()
            / ".venv"
            / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"),
            source_root
            / ".venv"
            / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"),
            Path(sys.executable) if sys.prefix != sys.base_prefix else None,
        ]
        for candidate in candidates:
            if candidate is not None and candidate.expanduser().is_file():
                return candidate.expanduser().absolute()
        located = shutil.which(executable)
        return Path(located).absolute() if located else None

    @staticmethod
    def _run_command(
        command: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
    ) -> None:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[-4000:]
            raise ManagedPythonRuntimeError(
                detail or f"运行时命令失败，exit={completed.returncode}"
            )
