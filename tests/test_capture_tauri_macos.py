from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

CAPTURE_TOOL = Path(__file__).parents[1] / "tools/ui-parity/capture_tauri_macos.py"
SPEC = importlib.util.spec_from_file_location("capture_tauri_macos", CAPTURE_TOOL)
assert SPEC is not None and SPEC.loader is not None
capture_tauri_macos = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture_tauri_macos)


def write_file(path: Path, contents: str, *, mtime_ns: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    os.utime(path, ns=(mtime_ns, mtime_ns))


def test_frontend_bundle_freshness_records_current_embedded_fixture(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    binary = tmp_path / "nimo_ui_parity"
    write_file(dist / "assets/index.js", "bundle", mtime_ns=100)
    write_file(binary, "binary", mtime_ns=101)

    result = capture_tauri_macos.frontend_bundle_freshness(binary, frontend_dist=dist)

    assert result == {
        "binary_mtime_ns": 101,
        "dist_asset_count": 1,
        "newest_dist_asset": "assets/index.js",
        "newest_dist_asset_mtime_ns": 100,
    }


def test_frontend_bundle_freshness_rejects_stale_native_executable(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    binary = tmp_path / "nimo_ui_parity"
    write_file(binary, "binary", mtime_ns=100)
    write_file(dist / "assets/index.js", "new bundle", mtime_ns=101)

    with pytest.raises(RuntimeError, match="stale embedded assets can render a white WebView"):
        capture_tauri_macos.frontend_bundle_freshness(binary, frontend_dist=dist)


def test_frontend_bundle_freshness_requires_a_generated_vite_dist(tmp_path: Path) -> None:
    binary = tmp_path / "nimo_ui_parity"
    write_file(binary, "binary", mtime_ns=100)

    with pytest.raises(RuntimeError, match="Vite dist directory is missing"):
        capture_tauri_macos.frontend_bundle_freshness(binary, frontend_dist=tmp_path / "missing")


def test_voice_studio_configured_fixture_query_is_explicit() -> None:
    query = capture_tauri_macos.fixture_query(
        SimpleNamespace(
            page="voice_studio",
            theme="narrative_ember",
            reader=None,
            settings_section=None,
            chapter_studio_state=None,
            chapter_dialog=None,
            workflow_dialog=None,
            voice_dialog=None,
            voice_studio_state="configured",
            voice_studio_tab=None,
            rail="expanded",
        )
    )

    assert "page=voice_studio" in query
    assert "voice_state=configured" in query


def test_model_routing_fixture_query_is_explicit() -> None:
    query = capture_tauri_macos.fixture_query(
        SimpleNamespace(
            page="settings",
            theme="narrative_ember",
            reader=None,
            settings_section="model-routing",
            chapter_studio_state=None,
            chapter_dialog=None,
            workflow_dialog=None,
            voice_dialog=None,
            voice_studio_state=None,
            voice_studio_tab=None,
            rail="expanded",
        )
    )

    assert "page=settings" in query
    assert "settings_section=model-routing" in query
