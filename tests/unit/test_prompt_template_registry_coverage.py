from __future__ import annotations

from pathlib import Path

from novel_forge.prompts.registry import _TASK_TEMPLATE_MAP

_LEGACY_TEMPLATE_ALLOWLIST: set[str] = {
    "planning/subplot_polish.j2",
    "writing/repair_literary_quality.j2",
}


def test_public_prompt_templates_are_registered_or_explicitly_legacy() -> None:
    base = Path("novel_forge/prompts/prompts")
    public_templates = {
        p.relative_to(base).as_posix()
        for p in base.rglob("*.j2")
        if not p.name.startswith("_")
    }
    registered_templates = set(_TASK_TEMPLATE_MAP.values())

    unregistered = sorted(public_templates - registered_templates - _LEGACY_TEMPLATE_ALLOWLIST)
    assert not unregistered, f"Unregistered public templates: {unregistered}"

    missing_legacy = sorted(_LEGACY_TEMPLATE_ALLOWLIST - public_templates)
    assert not missing_legacy, f"Legacy allowlist points to missing templates: {missing_legacy}"


def test_no_unregistered_webnovel_style_guide_shim_remains() -> None:
    assert not Path("novel_forge/prompts/prompts/webnovel_style_guide.j2").exists()
