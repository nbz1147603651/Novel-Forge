"""Structured document renderers extracted from the main UI facade."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTextBrowser

JsonDict = dict[str, Any]

__all__ = [
    "render_blueprint_element_selection",
    "render_narrative_blueprint_fragments",
    "render_book_consistency_report",
    "render_book_consistency_repair_report",
    "render_chapter_design_matrix",
    "render_chapter_memory_diagnostics",
    "render_init_coherence_claim_ledger",
    "render_init_coherence_claims",
    "render_init_claim_contract_coverage",
    "render_init_coherence_index",
    "render_init_coherence_profile",
    "render_init_conflict_adjudication",
    "render_init_conflict_candidates",
    "render_editorial_contract",
    "render_outline",
    "render_profile_style",
    "render_reading_power_report",
    "render_reading_power_window_config",
    "render_short_blueprint",
    "render_short_creative_summary",
    "render_state_packet",
    "render_version_diff",
]

def render_blueprint_element_selection(data: JsonDict) -> QTextBrowser:
    ...
def render_profile_style(data: JsonDict) -> QTextBrowser:
    ...

def render_editorial_contract(data: JsonDict) -> QTextBrowser:
    ...

def render_reading_power_window_config(data: JsonDict) -> QTextBrowser:
    ...

def render_reading_power_report(data: JsonDict) -> QTextBrowser:
    ...

def render_chapter_memory_diagnostics(data: JsonDict) -> QTextBrowser:
    ...

def render_outline(data: JsonDict) -> QTextBrowser:
    ...

def render_version_diff(
    diff_result: JsonDict,
    label_a: str = "版本 A",
    label_b: str = "版本 B",
) -> QTextBrowser:
    ...

# M3.3: re-export extracted render_* functions from sub-modules.
from novel_forge.desktop.pages.document_renderer.story_artifacts.blueprint_element import (  # noqa: E402
    render_blueprint_element_selection,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.story_artifacts.book_consistency import (  # noqa: E402
    render_book_consistency_repair_report,  # noqa: E402, F401, F811
    render_book_consistency_report,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.story_artifacts.chapter_matrix import (  # noqa: E402
    render_chapter_design_matrix,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.story_artifacts.chapter_memory_diagnostics import (  # noqa: E402
    render_chapter_memory_diagnostics,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.story_artifacts.coherence import (  # noqa: E402
    render_init_claim_contract_coverage,  # noqa: E402, F401, F811
    render_init_coherence_claim_ledger,  # noqa: E402, F401, F811
    render_init_coherence_claims,  # noqa: E402, F401, F811
    render_init_coherence_index,  # noqa: E402, F401, F811
    render_init_coherence_profile,  # noqa: E402, F401, F811
    render_init_conflict_adjudication,  # noqa: E402, F401, F811
    render_init_conflict_candidates,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.story_artifacts.editorial import (  # noqa: E402
    render_editorial_contract,  # noqa: E402, F401, F811
    render_profile_style,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.story_artifacts.narrative_blueprint import (  # noqa: E402
    render_narrative_blueprint_fragments,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.story_artifacts.outline import (  # noqa: E402
    render_outline,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.story_artifacts.reading_power_report import (  # noqa: E402
    render_reading_power_report,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.story_artifacts.reading_power_window import (  # noqa: E402
    render_reading_power_window_config,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.story_artifacts.short_forms import (  # noqa: E402
    render_short_blueprint,  # noqa: E402, F401, F811
    render_short_creative_summary,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.story_artifacts.state_packet import (  # noqa: E402
    render_state_packet,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.story_artifacts.version_diff import (  # noqa: E402
    render_version_diff,  # noqa: E402, F401, F811
)
