"""PySide chapter policy controls reuse Engine preset semantics."""

from __future__ import annotations

from novel_forge.core.config import Settings
from novel_forge.desktop.pages.settings.parameters.research import _build_research_params


def test_research_settings_expose_balanced_chapter_policy(qapp) -> None:
    _section, widgets = _build_research_params(Settings(_env_file=None))
    preset = widgets["_chapter_runtime_policy_preset"]

    assert preset.currentData() == "compat"
    preset.setCurrentIndex(preset.findData("balanced"))

    assert widgets["_chapter_intent_guard_mode"].currentData() == "block"
    assert widgets["_chapter_research_refresh_enabled"].isChecked() is True
    assert widgets["_chapter_research_inspiration_enabled"].isChecked() is True
    assert widgets["_chapter_research_inspiration_cooldown"].value() == 3
    assert widgets["_short_adaptive_revision_enabled"].isChecked() is True
    assert widgets["_long_single_final_verify_enabled"].isChecked() is True
