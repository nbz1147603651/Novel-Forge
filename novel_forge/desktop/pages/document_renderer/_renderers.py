"""Internal facade — backward-compat shim for the historical
``document_renderers`` monolith. The actual implementations live in
per-renderer sub-modules; this module re-exports the public API.

New code should import directly from
``novel_forge.desktop.pages.document_renderer`` (the package).
"""

from __future__ import annotations

from novel_forge.desktop.pages.document_renderer.chapter import (  # noqa: F401
    render_chapter_prose,
)
from novel_forge.desktop.pages.document_renderer.character_bible import (  # noqa: F401
    render_character_bible,
)
from novel_forge.desktop.pages.document_renderer.character_graph import (  # noqa: F401
    CharacterGraphWidget,
)
from novel_forge.desktop.pages.document_renderer.entity import (  # noqa: F401
    render_character_system,
    render_entity_graph,
    render_entity_registry,
)
from novel_forge.desktop.pages.document_renderer.narrative_blueprint import (  # noqa: F401
    NarrativeBlueprintWidget,
    render_narrative_blueprint,
)
from novel_forge.desktop.pages.document_renderer.popups import (  # noqa: F401
    _BlueprintTooltipPopup,
    _CharacterTooltipPopup,
    _FramelessTooltipPopup,
)
from novel_forge.desktop.pages.document_renderer.relationship_matrix import (  # noqa: F401
    render_character_relationship_matrix,
)
from novel_forge.desktop.pages.document_renderer.smart_render import (  # noqa: F401
    smart_render_document,
)
from novel_forge.desktop.pages.document_renderer.subplot import (  # noqa: F401
    render_subplot_execution_matrix,
)
