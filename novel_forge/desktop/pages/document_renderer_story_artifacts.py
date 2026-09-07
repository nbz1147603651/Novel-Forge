"""Backward-compat shim — re-exports the public API of
:mod:`novel_forge.desktop.pages.document_renderer.story_artifacts`.

The original 4520-line ``document_renderer_story_artifacts.py`` was
decomposed into a subpackage in the M3.3 refactor. This shim keeps
existing imports of the form
``from novel_forge.desktop.pages.document_renderer_story_artifacts import X``
working.
"""

from __future__ import annotations

from novel_forge.desktop.pages.document_renderer.story_artifacts import (  # noqa: F401
    render_blueprint_element_selection,
    render_book_consistency_repair_report,
    render_book_consistency_report,
    render_chapter_design_matrix,
    render_chapter_memory_diagnostics,
    render_editorial_contract,
    render_init_claim_contract_coverage,
    render_init_coherence_claim_ledger,
    render_init_coherence_claims,
    render_init_coherence_index,
    render_init_coherence_profile,
    render_init_conflict_adjudication,
    render_init_conflict_candidates,
    render_narrative_blueprint_fragments,
    render_outline,
    render_profile_style,
    render_reading_power_report,
    render_reading_power_window_config,
    render_short_blueprint,
    render_short_creative_summary,
    render_state_packet,
    render_version_diff,
)

__all__ = [
    "render_blueprint_element_selection",
    "render_book_consistency_repair_report",
    "render_book_consistency_report",
    "render_chapter_design_matrix",
    "render_chapter_memory_diagnostics",
    "render_editorial_contract",
    "render_init_claim_contract_coverage",
    "render_init_coherence_claim_ledger",
    "render_init_coherence_claims",
    "render_init_coherence_index",
    "render_init_coherence_profile",
    "render_init_conflict_adjudication",
    "render_init_conflict_candidates",
    "render_narrative_blueprint_fragments",
    "render_outline",
    "render_profile_style",
    "render_reading_power_report",
    "render_reading_power_window_config",
    "render_short_blueprint",
    "render_short_creative_summary",
    "render_state_packet",
    "render_version_diff",
]
