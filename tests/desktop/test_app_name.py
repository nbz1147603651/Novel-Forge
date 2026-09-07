"""Tests for application name branding (I-9)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.desktop


def test_launch_desktop_sets_application_name_to_nimo():
    """launch_desktop should set QApplication name to NIMO (not 'Novel Forge').

    Note: setOrganizationName may still use 'Novel Forge' as a fallback
    (the org name is distinct from the application display name).
    """
    import inspect

    from novel_forge.desktop import main

    source = inspect.getsource(main.launch_desktop)
    # The function should set the name to NIMO
    assert '"NIMO"' in source or "'NIMO'" in source, (
        "launch_desktop should reference 'NIMO' as the application name"
    )
    assert "apply_app_icon" in source, "launch_desktop should apply the NIMO app icon"
    # And the applicationName should NOT be "Novel Forge" anymore
    assert 'setApplicationName("Novel Forge")' not in source, (
        "launch_desktop should no longer use 'Novel Forge' as the application name"
    )


def test_spec_file_uses_nimo():
    """novel_forge_desktop.spec should use NIMO for app names and bundle metadata."""
    from pathlib import Path

    spec_path = Path(__file__).parent.parent.parent / "novel_forge_desktop.spec"
    assert spec_path.exists(), f"Spec file not found: {spec_path}"
    content = spec_path.read_text(encoding="utf-8")
    # Should reference NIMO somewhere
    assert "NIMO" in content, "novel_forge_desktop.spec should reference 'NIMO'"
    assert "nimo-logo.icns" in content
    assert "nimo-logo.ico" in content
    assert "nimo-logo.png" in content
    assert 'name="NIMO"' in content
    assert 'name="NIMO.app"' in content
    assert "NovelForge.app" not in content
