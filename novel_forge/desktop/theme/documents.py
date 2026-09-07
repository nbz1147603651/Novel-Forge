"""Token-first styling helpers for ``QTextBrowser`` documents.

Application QSS does not cascade into a QTextBrowser's HTML document.  Keep
document styling declarative by writing ``{{semantic.token}}`` placeholders in
application-owned CSS, then resolving them at the rendering boundary.
"""

from __future__ import annotations

import re

from . import resolve_theme_tokens

_HEX_COLOR_RE = re.compile(r"#([0-9a-fA-F]{6})\b")
_DOCUMENT_MARKUP_TOKEN_RE = re.compile(r"\[\[nf:([a-z0-9.]+)\]\]")
_DOCUMENT_MARKUP_RGBA_TOKEN_RE = re.compile(
    r"rgba\(\s*\[\[nf:([a-z0-9.]+)\]\]\s*,\s*([0-9]*\.?[0-9]+)\s*\)"
)


def render_document_css(css_template: str) -> str:
    """Resolve semantic token placeholders in an application-owned CSS template."""
    return resolve_theme_tokens(css_template)


def render_document_html(html_template: str) -> str:
    """Resolve semantic token placeholders in controlled HTML markup.

    Only pass markup assembled by the application.  User-authored document
    text should be escaped and interpolated after this step.
    """
    return resolve_theme_tokens(html_template)


def render_document_markup(markup: str) -> str:
    """Resolve application-owned document color markers for the active theme.

    Document renderers use ``[[nf:semantic.token]]`` inside generated HTML.
    Unlike the general ``{{token}}`` template syntax, this deliberately
    reserved form can be resolved after dynamic, user-authored content has
    been escaped and interpolated without treating a user's braces as theme
    syntax.
    """

    from . import qcolor_hex, qcolor_rgba

    def _rgba_replacer(match: re.Match[str]) -> str:
        return qcolor_rgba(match.group(1), float(match.group(2)))

    def _color_replacer(match: re.Match[str]) -> str:
        return qcolor_hex(match.group(1))

    markup = _DOCUMENT_MARKUP_RGBA_TOKEN_RE.sub(_rgba_replacer, markup)
    return _DOCUMENT_MARKUP_TOKEN_RE.sub(_color_replacer, markup)


def hex_to_rgb(color: str) -> str:
    """Convert a validated ``#RRGGBB`` color to CSS RGB components.

    Dynamic visualizations should usually resolve a token first with
    ``qcolor_hex`` and then pass the result here when CSS requires an RGB
    triplet for an alpha channel.
    """
    match = _HEX_COLOR_RE.fullmatch(color.strip())
    if match is None:
        raise ValueError(f"Expected #RRGGBB color, got {color!r}")
    value = match.group(1)
    return f"{int(value[0:2], 16)},{int(value[2:4], 16)},{int(value[4:6], 16)}"


__all__ = [
    "hex_to_rgb",
    "render_document_css",
    "render_document_html",
    "render_document_markup",
]
