from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from novel_forge.desktop.pages.settings_page_parameters import _add_init_coherence_controls
from novel_forge.desktop.widgets import CollapsibleSection


def test_init_coherence_repair_rounds_ui_fallback_matches_core_default(
    qapp: QApplication,  # noqa: ARG001 - ensures QApplication exists
) -> None:
    section = CollapsibleSection("初始化一致性裁判", expanded=True)
    widgets: dict[str, object] = {}

    _add_init_coherence_controls(section, widgets, SimpleNamespace())

    assert widgets["_init_coh_repair_rounds"].value() == 2
    assert widgets["_init_source_artifact_auto_repair"].currentText() == "true"
    assert widgets["_init_source_artifact_repair_rounds"].value() == 2
    assert widgets["_init_claim_coverage_block_degraded"].currentText() == "true"
