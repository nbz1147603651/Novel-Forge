"""Keep the PySide → Engine → Nimo convergence matrix machine-checkable."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path


def test_engine_feature_matrix_has_contract_evidence_for_every_surface() -> None:
    root = Path(__file__).resolve().parents[2]
    matrix_path = root / "docs" / "ui-parity" / "engine-feature-matrix.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))

    assert matrix["schemaVersion"] == 1
    default_gate = matrix["defaultUiGate"]
    assert default_gate["target"] == "nimo"
    assert default_gate["status"] == "ready"
    assert isinstance(default_gate["reason"], str) and default_gate["reason"]
    required = {"area", "engineReadModel", "command", "longTask", "nimoEntry", "evidence", "status", "limitation"}
    rows = matrix["rows"]
    assert {row["area"] for row in rows} >= {
        "projects",
        "initialization",
        "chapters_and_book_tools",
        "ollama_models_and_routing",
        "storage",
        "task_center_and_recovery",
        "error_recovery",
        "memory",
        "tts_audio",
        "platform_capabilities",
    }
    for row in rows:
        assert required <= row.keys(), row["area"]
        assert row["status"] in {"in_progress", "verified", "limited"}
        assert all(isinstance(item, str) and item for item in row["evidence"])
        if row["status"] == "verified":
            assert row["evidence"], f"verified row without automation evidence: {row['area']}"


def test_primary_desktop_and_fallback_policy_stay_aligned() -> None:
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    assert scripts["nimo"] == "novel_forge.desktop.tauri_launcher:main"
    assert scripts["nimo-t"] == scripts["nimo"]
    assert scripts["nimo-p"] == "novel_forge.desktop.main:main"

    tauri = json.loads(
        (root / "clients" / "nimo-desktop" / "src-tauri" / "tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )
    assert tauri["productName"] == "NIMO"
    assert tauri["identifier"] == "com.novelforge.nimo"

    primary_build = (root / "scripts" / "build_desktop.py").read_text(encoding="utf-8")
    fallback_build = (root / "scripts" / "build_pyside_desktop.py").read_text(
        encoding="utf-8"
    )
    assert '"tauri", "build"' in primary_build
    assert "PyInstaller" not in primary_build
    assert "PyInstaller" in fallback_build

    desktop_rules = (root / "novel_forge" / "desktop" / "AGENTS.md").read_text(
        encoding="utf-8"
    )
    assert "Frozen fallback policy" in desktop_rules
    assert "only target for new product UI" in desktop_rules
