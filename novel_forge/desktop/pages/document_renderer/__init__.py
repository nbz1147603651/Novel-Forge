"""Rich document renderers for the project document viewer.

This package was created as part of the M3 refactor (giant file splits).
The original 4850-line ``document_renderers.py`` was decomposed into
per-renderer submodules. The public ``render_*`` functions are re-exported
here from the per-domain submodules so existing imports keep working.

M4 added the ``incremental`` and ``relationships`` modules here (previously
flat ``document_renderer_incremental.py`` / ``document_renderer_relationships.py``).
"""

from __future__ import annotations

from novel_forge.desktop.pages.document_renderer.chapter import render_chapter_prose
from novel_forge.desktop.pages.document_renderer.character_bible import (
    _build_character_card,
    render_character_bible,
)
from novel_forge.desktop.pages.document_renderer.character_graph import (
    CharacterGraphWidget,
    _role_label_for_display,
)
from novel_forge.desktop.pages.document_renderer.entity import (
    render_character_system,
    render_entity_graph,
    render_entity_registry,
)
from novel_forge.desktop.pages.document_renderer.narrative_blueprint import (
    NarrativeBlueprintWidget,
    _chapter_end,
    _chapter_start,
    _chapter_value,
    _character_arc_span,
    _extract_mainline_nodes,
    _infer_blueprint_total_chapters,
    _normalize_tension_key,
    render_narrative_blueprint,
)
from novel_forge.desktop.pages.document_renderer.popups import (
    _BlueprintTooltipPopup,  # noqa: F401
    _CharacterTooltipPopup,  # noqa: F401
)
from novel_forge.desktop.pages.document_renderer.relationship_matrix import (
    render_character_relationship_matrix,
)
from novel_forge.desktop.pages.document_renderer.smart_render import (
    smart_render_document,
)
from novel_forge.desktop.pages.document_renderer.subplot import (
    render_subplot_execution_matrix,
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
    "render_character_bible",
    "render_character_relationship_matrix",
    "render_character_system",
    "render_chapter_prose",
    "render_entity_graph",
    "render_entity_registry",
    "render_narrative_blueprint",
    "render_subplot_execution_matrix",
    "smart_render_document",
]
