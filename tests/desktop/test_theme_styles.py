"""Tests for new QSS styles (Task 11) and theme-token enforcement.

The theme module exposes a unified ``get_stylesheet()`` that returns the full
resolved QSS string. New selectors must be present so the corresponding
widgets (rich doc viewer toolbar, project card quick actions, quick nav row)
get their themed look.

The ``resolve_qcolor`` API provides call-time token → QColor resolution so
paint-time code automatically tracks theme switches.  The audit tests below
guard against regressions where raw RGB tuples are reintroduced.
"""

from __future__ import annotations

import inspect
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from novel_forge.desktop.theme import get_stylesheet, resolve_qcolor  # noqa: E402
from novel_forge.desktop.theme.palettes import (  # noqa: E402
    DEFAULT_DESKTOP_THEME_ID,
    desktop_theme_choices,
    list_desktop_themes,
)
from novel_forge.desktop.theme.runtime import apply_desktop_theme  # noqa: E402
from novel_forge.desktop.tokens.colors import COLORS  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def test_theme_contains_rich_doc_toolbar_styles() -> None:
    qss = get_stylesheet()
    assert "#richDocToolbar" in qss
    assert "#richDocTitle" in qss
    assert "#richDocToolBtn" in qss
    assert "#richDocStatus" in qss
    assert "#richDocStatusItem" in qss


def test_theme_contains_project_card_action_styles() -> None:
    qss = get_stylesheet()
    assert "#projectCardAction" in qss


def test_theme_contains_character_profile_scroll_background_styles() -> None:
    qss = get_stylesheet(theme_id="ink_jade")
    assert "#characterProfileReadScroll" in qss
    assert "#characterProfileReadContent" in qss
    assert "#characterProfileFormScroll" in qss
    assert "#characterProfileFormContent" in qss
    assert "background: #f4faf7" in qss


def test_theme_paints_voice_cast_viewport_background() -> None:
    qss = get_stylesheet(theme_id="ink_jade")
    assert "QListWidget#voiceCastList > QWidget#qt_scrollarea_viewport" in qss
    assert "background: #fbfefd" in qss


def test_theme_contains_task_observation_and_chapter_task_flow_styles() -> None:
    qss = get_stylesheet(theme_id="ink_jade")
    assert "#taskFocusPanel" in qss
    assert "#taskSwitcherBar" in qss
    assert "#chapterTaskFlowContent" in qss
    assert "#2f7667" in qss


def test_stylesheet_is_valid_string() -> None:
    qss = get_stylesheet()
    assert isinstance(qss, str)
    assert len(qss) > 0
    # It must contain selectors introduced earlier (sanity check)
    assert "#navButton" in qss
    assert "#topBar" in qss


def test_theme_catalog_exposes_default_and_alternate_styles() -> None:
    choices = dict(desktop_theme_choices())
    assert DEFAULT_DESKTOP_THEME_ID in choices
    assert "ink_jade" in choices
    assert choices[DEFAULT_DESKTOP_THEME_ID] == "玄炉"
    assert all("NIMO" not in label for label in choices.values())

    default_qss = get_stylesheet(theme_id=DEFAULT_DESKTOP_THEME_ID)
    jade_qss = get_stylesheet(theme_id="ink_jade")

    assert "#sideRailToggle" in default_qss
    assert "#brandLogoFrame" in default_qss
    assert "#brandLogo" in default_qss
    assert "#b7d7c7" in jade_qss
    assert "#2f7667" in jade_qss
    assert "#b65634" not in jade_qss
    assert jade_qss != default_qss


def test_theme_catalog_exposes_all_desktop_themes() -> None:
    choices = desktop_theme_choices()
    assert choices == (
        ("narrative_ember", "玄炉"),
        ("cinnabar_seal", "朱砂"),
        ("autumn_apricot", "秋杏"),
        ("bamboo_mist", "竹青"),
        ("ink_jade", "青墨"),
        ("stillwater", "素蓝"),
        ("ink_amethyst", "墨紫"),
        ("snow_inkstone", "雪砚"),
        ("moonlit_paper", "月白"),
        ("twilight_ink", "暮夜"),
        ("indigo_night", "黛蓝"),
        ("pine_soot", "松烟"),
    )


def test_all_desktop_theme_stylesheets_resolve_tokens() -> None:
    for theme in list_desktop_themes():
        qss = get_stylesheet(theme_id=theme.theme_id)
        assert "{{" not in qss
        assert "}}" not in qss
        assert "rgba(#" not in qss
        assert "__ARROW_" not in qss
        assert "#brandLogoFrame" in qss
        assert "#brandOrnament" in qss


def test_brand_logo_frame_has_no_visible_shell() -> None:
    qss = get_stylesheet(theme_id=DEFAULT_DESKTOP_THEME_ID)
    start = qss.index("QFrame#brandLogoFrame {")
    end = qss.index("}", start)
    block = qss[start:end]
    hover_start = qss.index("QFrame#brandLogoFrame:hover {")
    hover_end = qss.index("}", hover_start)
    hover_block = qss[hover_start:hover_end]

    assert "background: transparent" in block
    assert "border: none" in block
    assert "qradialgradient" not in block
    assert "background: transparent" in hover_block
    assert "border: none" in hover_block


def test_alternate_theme_overrides_cover_brand_shell_tokens() -> None:
    themes = [
        theme for theme in list_desktop_themes() if theme.theme_id != DEFAULT_DESKTOP_THEME_ID
    ]
    override_key_sets = {theme.theme_id: set(theme.token_overrides) for theme in themes}
    expected_keys = next(iter(override_key_sets.values()))
    required = {
        "accent.primary",
        "bg.workspace",
        "bg.sidebar.start",
        "brand.logo.frame",
        "brand.logo.border",
        "brand.logo.accent",
        "text.brand.gold",
        "text.brand.light",
        "border.default",
        "role.protagonist",
        "role.antagonist",
        "relation.bond",
        "relation.tension",
        "shadow",
        "fade.bg",
    }

    assert required <= expected_keys
    assert all(keys == expected_keys for keys in override_key_sets.values())


def test_apply_theme_recovers_root_updates_when_brand_logo_refresh_fails(
    qapp: QApplication,
) -> None:
    class BrokenLogoWindow(QWidget):
        def _refresh_brand_logo(self, theme_id: str | None = None) -> None:
            raise RuntimeError(f"logo refresh failed for {theme_id}")

    root = QWidget()
    window = BrokenLogoWindow()
    old_window = qapp.property("_novel_forge_window")
    old_theme = qapp.property("_novel_forge_desktop_theme")
    old_stylesheet = qapp.styleSheet()

    try:
        qapp.setProperty("_novel_forge_window", window)
        qapp.setProperty("_novel_forge_desktop_theme", DEFAULT_DESKTOP_THEME_ID)
        root.setUpdatesEnabled(True)

        applied = apply_desktop_theme("ink_jade", app=qapp, root=root)

        assert applied == "ink_jade"
        assert root.updatesEnabled()
        assert qapp.property("_novel_forge_desktop_theme") == "ink_jade"
    finally:
        qapp.setProperty("_novel_forge_window", old_window)
        qapp.setProperty("_novel_forge_desktop_theme", old_theme)
        qapp.setStyleSheet(old_stylesheet)
        root.deleteLater()
        window.deleteLater()


# ═══ resolve_qcolor tests ═══════════════════════════════════════════════════


def test_resolve_qcolor_returns_valid_color_for_known_token(qapp: QApplication) -> None:
    color = resolve_qcolor("accent.primary")
    assert isinstance(color, QColor)
    assert color.isValid()
    # Default theme accent.primary = #b65634 → RGB(182, 86, 52)
    assert color.red() == 182
    assert color.green() == 86
    assert color.blue() == 52
    assert color.alpha() == 255


def test_resolve_qcolor_respects_alpha(qapp: QApplication) -> None:
    color = resolve_qcolor("accent.primary", alpha=128)
    assert color.alpha() == 128
    assert color.red() == 182  # same RGB


def test_resolve_qcolor_clamps_alpha_to_qcolor_range(qapp: QApplication) -> None:
    assert resolve_qcolor("accent.primary", alpha=-1).alpha() == 0
    assert resolve_qcolor("accent.primary", alpha=999).alpha() == 255


def test_resolve_qcolor_returns_magenta_for_unknown_token(qapp: QApplication) -> None:
    color = resolve_qcolor("no.such.token.exists")
    # Magenta fallback: RGB(255, 0, 255)
    assert color.red() == 255
    assert color.green() == 0
    assert color.blue() == 255


def test_resolve_qcolor_tracks_theme_switch(qapp: QApplication) -> None:
    old_theme = qapp.property("_novel_forge_desktop_theme")
    try:
        qapp.setProperty("_novel_forge_desktop_theme", DEFAULT_DESKTOP_THEME_ID)
        default_accent = resolve_qcolor("accent.primary")

        qapp.setProperty("_novel_forge_desktop_theme", "ink_jade")
        jade_accent = resolve_qcolor("accent.primary")

        # Jade accent.primary = #2f7667 → RGB(47, 118, 103), different from default
        assert default_accent.red() != jade_accent.red()
        assert jade_accent.red() == 47
        assert jade_accent.green() == 118
    finally:
        qapp.setProperty("_novel_forge_desktop_theme", old_theme)


def test_semantic_paint_tokens_track_theme_switch(qapp: QApplication) -> None:
    """Custom-painted roles, links, shadows, and fades must leave the warm palette."""
    old_theme = qapp.property("_novel_forge_desktop_theme")
    try:
        qapp.setProperty("_novel_forge_desktop_theme", DEFAULT_DESKTOP_THEME_ID)
        default_colors = {
            name: resolve_qcolor(name)
            for name in ("role.protagonist", "relation.bond", "shadow", "fade.bg")
        }

        qapp.setProperty("_novel_forge_desktop_theme", "ink_jade")
        for name, default_color in default_colors.items():
            assert resolve_qcolor(name) != default_color, name
    finally:
        qapp.setProperty("_novel_forge_desktop_theme", old_theme)


def test_apply_theme_refreshes_opted_in_custom_widgets(qapp: QApplication) -> None:
    class ThemeAwareWidget(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.refresh_count = 0

        def refresh_theme_colors(self) -> None:
            self.refresh_count += 1

    widget = ThemeAwareWidget()
    old_theme = qapp.property("_novel_forge_desktop_theme")
    old_stylesheet = qapp.styleSheet()
    target_theme = "ink_jade" if old_theme != "ink_jade" else "stillwater"
    try:
        apply_desktop_theme(target_theme, app=qapp)
        assert widget.refresh_count == 1
    finally:
        qapp.setProperty("_novel_forge_desktop_theme", old_theme)
        qapp.setStyleSheet(old_stylesheet)
        widget.deleteLater()


def test_surface_shadow_refreshes_with_the_active_theme(qapp: QApplication) -> None:
    from novel_forge.desktop.components.primitives import Surface

    surface = Surface("hero")
    old_theme = qapp.property("_novel_forge_desktop_theme")
    old_stylesheet = qapp.styleSheet()
    try:
        apply_desktop_theme(DEFAULT_DESKTOP_THEME_ID, app=qapp)
        effect = surface.graphicsEffect()
        assert effect is not None
        default_shadow = effect.color()

        apply_desktop_theme("ink_jade", app=qapp)
        assert effect.color() == resolve_qcolor("shadow", 28)
        assert effect.color() != default_shadow
    finally:
        qapp.setProperty("_novel_forge_desktop_theme", old_theme)
        qapp.setStyleSheet(old_stylesheet)
        surface.deleteLater()


# ═══ Token system integrity ═══════════════════════════════════════════════════


def test_shadow_and_fade_bg_tokens_exist() -> None:
    assert "shadow" in COLORS
    assert "fade.bg" in COLORS
    assert COLORS["shadow"][0] == "#45291d"
    assert COLORS["fade.bg"][0] == "#f2e8dc"


# ═══ Hardcoded-color regression audit ═══════════════════════════════════════

# Regex that matches raw QColor(r, g, b) / QColor(r, g, b, a) constructor calls
# with integer literals.  Excludes QColor(0, 0, 0, 0) (transparent fill).
_RAW_QCOLOR_RE = re.compile(
    r"QColor\(\s*\d+\s*,\s*\d+\s*,\s*\d+(?:\s*,\s*\d+)?\s*\)"
)
_TRANSPARENT_BLACK_RE = re.compile(
    r"QColor\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0\s*\)"
)


def _get_source_without_transparent_fills(module: object) -> str:
    """Return module source with transparent-black fills stripped out."""
    source = inspect.getsource(module)
    return _TRANSPARENT_BLACK_RE.sub("", source)


def test_toast_has_no_hardcoded_variant_colors() -> None:
    """``_VARIANT_COLORS`` RGB-tuple dict was removed in favour of token lookup."""
    from novel_forge.desktop.components import toast as toast_mod

    assert not hasattr(toast_mod, "_VARIANT_COLORS"), (
        "toast._VARIANT_COLORS must be replaced by _VARIANT_TOKENS + resolve_qcolor"
    )
    # The token-based replacement must exist.
    assert hasattr(toast_mod, "_VARIANT_TOKENS")


def test_companion_glow_uses_token_map() -> None:
    """``_STATUS_GLOW_COLORS`` QColor dict replaced by ``_STATUS_GLOW_TOKENS``."""
    from novel_forge.desktop.components.task_focus import companion as comp_mod

    assert not hasattr(comp_mod, "_STATUS_GLOW_COLORS"), (
        "companion._STATUS_GLOW_COLORS must use _STATUS_GLOW_TOKENS + _status_glow_color"
    )
    assert hasattr(comp_mod, "_STATUS_GLOW_TOKENS")
    assert hasattr(comp_mod, "_status_glow_color")


def test_workflow_jobs_status_colors_use_tokens() -> None:
    """``_JOB_STATUS_COLORS`` QColor dict replaced by ``_JOB_STATUS_TOKEN_MAP``."""
    from novel_forge.desktop.pages.workflow import jobs as jobs_mod

    assert not hasattr(jobs_mod, "_JOB_STATUS_COLORS"), (
        "jobs._JOB_STATUS_COLORS must use _JOB_STATUS_TOKEN_MAP + _job_status_color"
    )
    assert hasattr(jobs_mod, "_JOB_STATUS_TOKEN_MAP")
    assert hasattr(jobs_mod, "_job_status_color")


def _audit_paint_methods_for_hardcoded_colors(
    module: object,
    *,
    module_label: str,
    allowed_raw_patterns: set[str] = frozenset(),
) -> None:
    """Fail if any paint-related method contains raw QColor(r,g,b) literals.

    ``allowed_raw_patterns`` may contain exact QColor(...) strings that are
    known-safe (e.g. truly neutral decorative colors that have no token
    equivalent).
    """
    source = _get_source_without_transparent_fills(module)
    matches = _RAW_QCOLOR_RE.findall(source)
    # Subtract known-safe patterns.
    offending = [m for m in matches if m not in allowed_raw_patterns]
    assert not offending, (
        f"{module_label} contains raw QColor(...) literals in paint-time code. "
        f"Use resolve_qcolor(token, alpha) instead.\n"
        f"Offending: {offending}"
    )


def test_toast_paint_uses_no_hardcoded_qcolor() -> None:
    from novel_forge.desktop.components import toast as toast_mod

    _audit_paint_methods_for_hardcoded_colors(toast_mod, module_label="toast.py")


def test_skeleton_paint_uses_no_hardcoded_qcolor() -> None:
    from novel_forge.desktop.components import skeleton as skel_mod

    _audit_paint_methods_for_hardcoded_colors(skel_mod, module_label="skeleton.py")


def test_floating_surface_paint_uses_no_hardcoded_qcolor() -> None:
    from novel_forge.desktop.components import floating_surface as fs_mod

    # The class-level DEFAULT_BACKGROUND / DEFAULT_BORDER constants are
    # kept for backward compatibility but are NOT used in paint methods.
    _audit_paint_methods_for_hardcoded_colors(
        fs_mod,
        module_label="floating_surface.py",
        allowed_raw_patterns={
            "QColor(255, 250, 243)",       # class-level DEFAULT_BACKGROUND
            "QColor(141, 107, 76, 140)",   # class-level DEFAULT_BORDER
        },
    )


def test_companion_glow_function_keeps_sprite_surface_transparent(qapp: QApplication) -> None:
    from novel_forge.desktop.components.task_focus.companion import (
        _STATUS_GLOW_TOKENS,
        _status_glow_color,
    )

    # Idle returns a fully transparent color (no glow).
    idle_color = _status_glow_color("idle")
    assert idle_color.alpha() == 0

    # Active states also stay transparent: the compact glow previously read
    # as an opaque card behind the pet.
    running_color = _status_glow_color("running")
    assert running_color.alpha() == 0

    # Every configured state preserves the transparent sprite surface.
    for state, entry in _STATUS_GLOW_TOKENS.items():
        c = _status_glow_color(state)
        assert entry is None
        assert isinstance(c, QColor)
        assert c.alpha() == 0


# ═══ QSS template hardcoded-color audit ═══════════════════════════════════

# Regex matching raw hex colors (#rgb or #rrggbb) in QSS template strings.
# Excludes tokens inside {{...}} and Python comments.
_QSS_RAW_HEX_RE = re.compile(r"(?<!\{)(?<!\{)\b#[0-9a-fA-F]{3,6}\b(?!\})")
# Regex matching raw rgba(n, n, n, ...) with integer literals (not tokens).
_QSS_RAW_RGBA_RE = re.compile(r"rgba\(\s*\d+\s*,")


def test_qss_templates_use_token_variables_not_hardcoded_colors() -> None:
    """QSS template files must use ``{{token}}`` placeholders, not raw hex/rgba.

    This audit scans all QSS fragment modules under ``theme/`` (excluding
    ``palettes.py`` and ``core.py`` which legitimately define color values)
    and fails if any ``CONTENT`` / ``_CONTENT`` / ``_CONTENT_TEMPLATE``
    string contains raw color literals.
    """
    from novel_forge.desktop.theme import (
        _globals,
        components,
        dialogs,
        forms,
        memory,
        navigation,
        toast,
    )

    # Also check the qss/ fragment modules via the combined CONTENT.
    from novel_forge.desktop.theme.qss import CONTENT as _qss_content

    # Map of module label → template string to audit.
    modules_to_audit: dict[str, str] = {
        "_globals": _globals.CONTENT,
        "navigation": navigation.CONTENT,
        "components": components.CONTENT,
        "forms": forms.CONTENT,
        "dialogs": dialogs.CONTENT,
        "toast": toast.CONTENT,
        "memory": memory.CONTENT,
        "qss_combined": _qss_content,
    }

    violations: list[str] = []
    for label, content in modules_to_audit.items():
        # Strip comments (/* ... */) and Python docstrings to avoid false
        # positives on documented color examples.
        # Check for raw hex.
        hex_matches = _QSS_RAW_HEX_RE.findall(content)
        # Filter out known-safe hex values that appear in token comments.
        real_hex = [h for h in hex_matches if not h.startswith("#{")]
        if real_hex:
            violations.append(f"{label}: raw hex {real_hex[:5]}")

        # Check for raw rgba(n, n, n, ...).
        rgba_matches = _QSS_RAW_RGBA_RE.findall(content)
        if rgba_matches:
            violations.append(f"{label}: raw rgba() {rgba_matches[:5]}")

    assert not violations, (
        "QSS templates contain hardcoded color literals. "
        "Use {{token.name}} placeholders instead.\n"
        + "\n".join(violations)
    )


def test_document_renderers_use_resolve_qcolor_not_hardcoded() -> None:
    """Document renderer paint code must use ``resolve_qcolor``, not raw QColor.

    The character_graph and narrative_blueprint renderers have many
    painter.setPen / setBrush calls.  All theme-sensitive colors must go
    through ``resolve_qcolor``.  Only universally neutral overlays
    (pure black shadows, pure white highlights) are allowed as raw QColor.
    """
    from novel_forge.desktop.pages.document_renderer import (
        character_graph,
        narrative_blueprint,
    )

    # Regex for QColor(int, int, int[, ...]) with integer RGB literals.
    _raw_re = re.compile(r"QColor\(\s*\d+\s*,\s*\d+\s*,\s*\d+")
    # Allowed: pure black (shadows), pure white (highlights), fully transparent.
    _allowed_re = re.compile(
        r"QColor\(\s*(?:0\s*,\s*0\s*,\s*0|255\s*,\s*255\s*,\s*255)\b"
    )

    for mod_label, mod in [
        ("character_graph", character_graph),
        ("narrative_blueprint", narrative_blueprint),
    ]:
        source = inspect.getsource(mod)
        raw_matches = _raw_re.findall(source)
        offending = [m for m in raw_matches if not _allowed_re.match(m)]
        assert not offending, (
            f"{mod_label} contains hardcoded QColor(r,g,b) in paint code. "
            f"Use resolve_qcolor(token, alpha) instead.\n"
            f"Offending: {offending[:8]}"
        )
