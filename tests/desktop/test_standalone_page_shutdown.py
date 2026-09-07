"""Tests for standalone page shutdown() (I-5).

Locks down the shutdown() contract for the four standalone pages that were
missing one before this fix:

- RelationshipNetworkPage
- CharacterProfilePage
- CharacterBibleEditor
- InteractiveOutlineWidget (outline editor)

Each page must expose a callable ``shutdown()`` method so the host can stop
timers and disconnect signals when switching away from the tab. The bodies
are not exercised here because constructing the full pages requires
non-trivial fixtures (CharacterBibleStore, project directories, etc.) —
these tests are a contract-only guard.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

PAGE_MODULES: tuple[str, ...] = (
    "novel_forge.desktop.pages.standalone.relationship_network_page",
    "novel_forge.desktop.pages.standalone.character_profile_page",
    "novel_forge.desktop.pages.standalone.character_bible_editor",
    "novel_forge.desktop.pages.standalone.outline_editor",
)

# Per-module, the concrete page class the host instantiates. We resolve the
# class by exact name rather than by suffix because some modules also import
# helpers (e.g. ``CharacterGraphWidget``) that would otherwise be picked up
# wrongly.
EXPECTED_PAGE_CLASS: dict[str, str] = {
    "novel_forge.desktop.pages.standalone.relationship_network_page": "RelationshipNetworkPage",
    "novel_forge.desktop.pages.standalone.character_profile_page": "CharacterProfilePage",
    "novel_forge.desktop.pages.standalone.character_bible_editor": "CharacterBibleEditor",
    "novel_forge.desktop.pages.standalone.outline_editor": "InteractiveOutlineWidget",
}


def _find_page_class(module_name: str) -> type | None:
    """Resolve the concrete page class for *module_name*."""
    class_name = EXPECTED_PAGE_CLASS.get(module_name)
    if class_name is None:
        return None
    mod = importlib.import_module(module_name)
    cls = getattr(mod, class_name, None)
    if cls is None or not inspect.isclass(cls):
        return None
    # The class must be defined in this module — guards against re-exports
    # pointing at a helper class from another package.
    if getattr(cls, "__module__", None) != module_name:
        return None
    return cls


@pytest.mark.parametrize("page_module_name", PAGE_MODULES)
def test_page_has_shutdown_method(page_module_name: str) -> None:
    """Each standalone page must implement shutdown()."""
    page_cls = _find_page_class(page_module_name)
    assert page_cls is not None, (
        f"Expected class {EXPECTED_PAGE_CLASS[page_module_name]} not found in {page_module_name}"
    )
    assert hasattr(page_cls, "shutdown"), (
        f"{page_module_name}.{page_cls.__name__} missing shutdown() method"
    )
    assert callable(page_cls.shutdown), (
        f"{page_module_name}.{page_cls.__name__}.shutdown must be callable"
    )


@pytest.mark.parametrize("page_module_name", PAGE_MODULES)
def test_shutdown_signature_returns_none(page_module_name: str) -> None:
    """shutdown() must be a no-arg callable returning None.

    The host invokes shutdown() without arguments and expects no return value.
    """
    page_cls = _find_page_class(page_module_name)
    assert page_cls is not None
    shutdown = getattr(page_cls, "shutdown", None)
    assert shutdown is not None
    sig = inspect.signature(shutdown)
    # Allow ``self`` only — no positional business args.
    params = [
        p for p in sig.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        and p.name != "self"
    ]
    assert params == [], (
        f"{page_module_name}.shutdown() should take no positional args; got {[p.name for p in params]}"
    )
    if sig.return_annotation is not inspect.Signature.empty:
        # ``-> None`` is encoded as the string "None" (forward reference) under
        # ``from __future__ import annotations``. Accept either.
        ann = sig.return_annotation
        assert ann is None or ann is type(None) or ann == "None", (
            f"{page_module_name}.shutdown() should return None; got {ann!r}"
        )
