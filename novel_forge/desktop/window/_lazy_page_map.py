"""Shared ``_LazyPageMap`` mapping for the desktop window module.

This class was previously duplicated across 19+ mixin files.  It is now
defined once here and re-imported wherever needed.

``get()`` returns only already-loaded pages so background refresh paths do
not accidentally instantiate heavy pages.  ``__getitem__`` is reserved for
user/navigation paths that genuinely need the page now.
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from typing import TYPE_CHECKING, Any

from novel_forge.desktop.registry import page_registry

if TYPE_CHECKING:
    from novel_forge.desktop.window import NovelForgeDesktopWindow


class _LazyPageMap(MutableMapping[str, Any]):
    """Mapping of loaded pages with explicit lazy creation on item access.

    ``get()`` returns only already-loaded pages so background refresh paths do
    not accidentally instantiate heavy pages. ``__getitem__`` is reserved for
    user/navigation paths that genuinely need the page now.
    """

    def __init__(self, owner: "NovelForgeDesktopWindow") -> None:
        self._owner = owner
        self._loaded: dict[str, Any] = {}

    def __getitem__(self, key: str) -> Any:
        page = self._owner._ensure_page(key)
        if page is None:
            raise KeyError(key)
        return page

    def __setitem__(self, key: str, value: Any) -> None:
        self._loaded[key] = value

    def __delitem__(self, key: str) -> None:
        del self._loaded[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._loaded)

    def __len__(self) -> int:
        return len(page_registry.list_pages())

    def keys(self) -> list[str]:  # type: ignore[override]
        return page_registry.list_pages()

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and (key in self._loaded or page_registry.has(key))

    def get(self, key: str, default: Any = None) -> Any:
        return self._loaded.get(key, default)

    def loaded_items(self) -> list[tuple[str, Any]]:
        return list(self._loaded.items())

    def loaded_values(self) -> list[Any]:
        return list(self._loaded.values())
