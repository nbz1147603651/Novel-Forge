"""Static boundaries that keep Engine services independent of desktop clients."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _python_sources(relative_directory: str) -> list[Path]:
    return sorted((_ROOT / relative_directory).rglob("*.py"))


def test_engine_services_do_not_reverse_import_desktop_clients() -> None:
    offenders = [
        path.relative_to(_ROOT)
    for directory in ("novel_forge/api", "novel_forge/app_service", "novel_forge/workspace")
        for path in _python_sources(directory)
        if "from novel_forge.desktop" in path.read_text(encoding="utf-8")
        or "import novel_forge.desktop" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"Engine service reverse-imports desktop code: {offenders}"


def test_ollama_clients_do_not_call_native_ollama_management_endpoints() -> None:
    locations = (
        _ROOT / "clients/nimo-desktop/src",
        _ROOT / "novel_forge/desktop/pages/settings",
    )
    forbidden = ('"/api/tags"', '"/api/pull"', '"/api/delete"')
    offenders = [
        path.relative_to(_ROOT)
        for location in locations
        for path in location.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".ts", ".tsx"}
        and not path.name.startswith("test_")
        and ".test." not in path.name
        and any(token in path.read_text(encoding="utf-8") for token in forbidden)
    ]
    assert offenders == [], f"UI client still calls native Ollama API: {offenders}"
