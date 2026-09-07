"""Backward-compat shim — re-exports the public API of
:mod:`novel_forge.desktop.pages.document_renderer`.

The original 4850-line ``document_renderers.py`` was decomposed into a
package in the M3.2 refactor. Any existing import of the form
``from novel_forge.desktop.pages.document_renderers import X`` continues to
work via this shim.

M4 subdir-layout refactor: the flat ``document_renderer_relationships``
module was relocated to ``novel_forge.desktop.pages.document_renderer.relationships``
inside the existing ``document_renderer`` package.
"""

from __future__ import annotations

from novel_forge.desktop.pages.document_renderer import (  # noqa: F401
    CharacterGraphWidget,
    NarrativeBlueprintWidget,
    _BlueprintTooltipPopup,
    _build_character_card,
    _chapter_end,
    _chapter_start,
    _chapter_value,
    _character_arc_span,
    _CharacterTooltipPopup,
    _extract_mainline_nodes,
    _infer_blueprint_total_chapters,
    _normalize_tension_key,
    _role_label_for_display,
    render_chapter_prose,
    render_character_bible,
    render_character_relationship_matrix,
    render_character_system,
    render_entity_graph,
    render_entity_registry,
    render_narrative_blueprint,
    render_subplot_execution_matrix,
    smart_render_document,
)
from novel_forge.desktop.pages.document_renderer.relationships import (  # noqa: F401
    render_relationship_compact,
    render_relationship_overview,
)
from novel_forge.desktop.pages.document_renderer.story_artifacts import (  # noqa: F401
    render_blueprint_element_selection,
)
from novel_forge.desktop.pages.document_renderer_reports import (  # noqa: F401
    render_generic_report,
)
from novel_forge.desktop.pages.document_renderer_story_artifacts import (  # noqa: F401
    render_outline,
    render_version_diff,
)

__all__ = [
    "CharacterGraphWidget",
    "NarrativeBlueprintWidget",
    "_build_character_card",
    "_chapter_end",
    "_chapter_start",
    "_chapter_value",
    "_character_arc_span",
    "_extract_mainline_nodes",
    "_infer_blueprint_total_chapters",
    "_normalize_tension_key",
    "_role_label_for_display",
    "render_blueprint_element_selection",
    "render_character_bible",
    "render_character_relationship_matrix",
    "render_character_system",
    "render_chapter_prose",
    "render_entity_graph",
    "render_entity_registry",
    "render_generic_report",
    "render_narrative_blueprint",
    "render_outline",
    "render_relationship_compact",
    "render_relationship_overview",
    "render_subplot_execution_matrix",
    "render_version_diff",
    "smart_render_document",
]
