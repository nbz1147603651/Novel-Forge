"""Tests for CLI command registration after modularization."""

from __future__ import annotations

import tomllib
from pathlib import Path

from typer.main import get_command

from novel_forge.cli.main import app


def test_main_registers_expected_commands() -> None:
    command_names = [command.name for command in app.registered_commands]

    assert command_names == [
        "healthcheck",
        "show-params",
        "init-config",
        "run-short",
        "init-long",
        "run-chapter",
        "rollback-chapter",
        "restore-version",
        "ab-test",
        "sync-bible",
        "book-audit",
        "repair-audit-queue",
        "editorial-audit",
        "repair-motif-history",
        "rebuild-memory-vectors",
        "rebuild-expression-memory",
        "reextract-chapter-contract",
        "repair-outline-resume",
        "regenerate-outline",
    ]


def test_nimo_is_primary_tauri_entrypoint_with_explicit_pyside_fallback() -> None:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]

    assert scripts["novel-forge"] == "novel_forge.cli.main:app"
    assert scripts["nimo"] == "novel_forge.desktop.tauri_launcher:main"
    assert scripts["nimo-p"] == "novel_forge.desktop.main:main"
    assert scripts["nimo-t"] == "novel_forge.desktop.tauri_launcher:main"
    assert "novel-forge-desktop" not in scripts
    assert "nimo-desktop" not in scripts
    assert "nf-old" not in scripts


def test_run_chapter_no_longer_exposes_edit_rounds_option() -> None:
    """Long-form WAVE runs once; ``--edit-rounds``/``--rounds`` has been removed."""
    command = get_command(app)
    run_chapter = command.commands["run-chapter"]
    edit_rounds_param = next(
        (param for param in run_chapter.params if getattr(param, "name", "") == "edit_rounds"),
        None,
    )

    assert edit_rounds_param is None
