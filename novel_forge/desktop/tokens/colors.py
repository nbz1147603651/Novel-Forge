"""Color design tokens: named hex values mapped to semantic roles.

Every token is a ``(hex_string, description)`` tuple.  Hex values are
**pixel-identical** to the originals scattered across ``theme/*.py`` —
no visual changes, only named indirection.

Usage::

    from novel_forge.desktop.tokens.colors import COLORS

    hex_val, desc = COLORS["accent.primary"]   # ("#b65634", "warm brick red")
    missing = COLORS.get("no.such.token")      # None — never raises
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Dict, Tuple

# ── Frozen token container ──────────────────────────────────────────────────
#
# Each field is a (hex, description) tuple.  The frozen dataclass guarantees
# immutability at the type level.  The ``COLORS`` dict below provides
# dotted-name lookup (``COLORS["accent.primary"]``).


@dataclass(frozen=True)
class _ColorTokens:
    """Canonical color-token definitions (frozen)."""

    # ── Accent / Brand ──────────────────────────────────────────────
    accent_primary: Tuple[str, str] = ("#b65634", "warm brick red")
    accent_primary_hover: Tuple[str, str] = ("#c76442", "lightened brick hover")
    accent_primary_pressed: Tuple[str, str] = ("#a34a2a", "darkened brick pressed")
    accent_light: Tuple[str, str] = ("#c86a47", "warm coral")
    accent_dark: Tuple[str, str] = ("#a94c2d", "deep rust")
    accent_warm: Tuple[str, str] = ("#c95731", "fire orange")
    accent_muted: Tuple[str, str] = ("#983e24", "muted rust")
    accent_deep: Tuple[str, str] = ("#8e3e24", "deep ember")
    accent_badge: Tuple[str, str] = ("#9a4d32", "badge warning")
    accent_rewriting: Tuple[str, str] = ("#8a3d1a", "rewriting state")
    accent_fallback: Tuple[str, str] = ("#6e3d29", "fallback trigger")
    accent_slider_warm: Tuple[str, str] = ("#e6b99d", "slider gradient warm")
    accent_rank_top1: Tuple[str, str] = ("#8d3f20", "rank tier top-1")
    accent_rank_top2: Tuple[str, str] = ("#985338", "rank tier top-2")
    accent_rank_top3: Tuple[str, str] = ("#a1634a", "rank tier top-3")
    accent_rank_label: Tuple[str, str] = ("#8d5b3f", "rank label accent")
    brand_logo_frame: Tuple[str, str] = ("#fffaf3", "brand logo frame surface")
    brand_logo_border: Tuple[str, str] = ("#d8b688", "brand logo frame border")
    brand_logo_accent: Tuple[str, str] = ("#b65634", "brand logo frame accent")

    # ── Background ──────────────────────────────────────────────────
    bg_workspace: Tuple[str, str] = ("#f7f0e7", "cream workspace")
    bg_workspace_mid: Tuple[str, str] = ("#f2e8dc", "warm workspace mid")
    bg_workspace_end: Tuple[str, str] = ("#efe5d7", "warm workspace end")
    bg_surface: Tuple[str, str] = ("#fffaf3", "panel cream")
    bg_surface_elevated: Tuple[str, str] = ("#fffaf4", "elevated cream")
    bg_input: Tuple[str, str] = ("#ffffff", "input/control white")
    bg_input_soft: Tuple[str, str] = ("#fffcf7", "soft input surface")
    bg_panel: Tuple[str, str] = ("#f8f2ea", "subtle panel surface")
    bg_panel_muted: Tuple[str, str] = ("#f7f2ec", "muted panel surface")
    bg_control: Tuple[str, str] = ("#f1e7d8", "control surface")
    bg_outline_tab: Tuple[str, str] = ("#ede2d3", "outline editor inactive tab")
    bg_control_hover: Tuple[str, str] = ("#e0d1c0", "control hover surface")
    bg_control_pressed: Tuple[str, str] = ("#e1d3c6", "control pressed surface")
    bg_inset: Tuple[str, str] = ("#f4ebdf", "inset surface")
    bg_hero_start: Tuple[str, str] = ("#fff7ef", "hero gradient start")
    bg_hero_mid: Tuple[str, str] = ("#fff3e6", "hero gradient mid")
    bg_hero_end: Tuple[str, str] = ("#f9ecdc", "hero gradient end")
    bg_sidebar_start: Tuple[str, str] = ("#2d1e18", "dark wood start")
    bg_sidebar_mid: Tuple[str, str] = ("#271b16", "dark wood mid")
    bg_sidebar_end: Tuple[str, str] = ("#1c1310", "dark wood end")
    bg_dialog: Tuple[str, str] = ("#fef9f2", "dialog cream")
    bg_hover_secondary: Tuple[str, str] = ("#fbf5ec", "secondary hover cream")
    bg_hover_accent: Tuple[str, str] = ("#fff7ee", "accent hover wash")
    bg_blueprint_row: Tuple[str, str] = ("#fffdf9", "blueprint row cream")
    bg_memory_tag: Tuple[str, str] = ("#f5ede3", "memory tag tan")
    bg_memory_hover: Tuple[str, str] = ("#ede3d3", "memory hover tan")

    # ── Text ────────────────────────────────────────────────────────
    text_primary: Tuple[str, str] = ("#2c241e", "deep ink")
    text_secondary: Tuple[str, str] = ("#66574b", "warm grey")
    text_heading: Tuple[str, str] = ("#35281f", "heading brown")
    text_heading_warm: Tuple[str, str] = ("#33261d", "warm heading")
    text_heading_deep: Tuple[str, str] = ("#30231b", "deep heading")
    text_body: Tuple[str, str] = ("#4f4337", "body brown")
    text_body_alt: Tuple[str, str] = ("#5a4837", "alt body brown")
    text_body_warm: Tuple[str, str] = ("#49372d", "warm body")
    text_muted: Tuple[str, str] = ("#8b7460", "muted warm")
    text_muted_strong: Tuple[str, str] = ("#7a6653", "strong muted")
    text_muted_soft: Tuple[str, str] = ("#786759", "soft muted")
    text_muted_badge: Tuple[str, str] = ("#8b8074", "badge muted")
    text_muted_quiet: Tuple[str, str] = ("#9a8b7c", "quiet muted")
    text_disabled: Tuple[str, str] = ("#9a8878", "disabled text")
    text_empty: Tuple[str, str] = ("#4c3a2d", "empty state title")
    text_empty_soft: Tuple[str, str] = ("#8a7765", "soft empty message")
    text_artifact: Tuple[str, str] = ("#3a2d24", "artifact viewer text")
    text_topbar_title: Tuple[str, str] = ("#2f241c", "topbar title")
    text_topbar_subtitle: Tuple[str, str] = ("#716256", "topbar subtitle")
    text_topbar_meta: Tuple[str, str] = ("#8c7868", "topbar meta")
    text_eyebrow: Tuple[str, str] = ("#a65b3f", "eyebrow accent")
    text_brand_gold: Tuple[str, str] = ("#d8b688", "gold brand")
    text_brand_light: Tuple[str, str] = ("#f6ebdb", "light brand")
    text_rail_footer: Tuple[str, str] = ("#f3e6d4", "rail footer text")
    text_chapter_rail: Tuple[str, str] = ("#6b5847", "chapter rail text")
    text_mode_sub: Tuple[str, str] = ("#7c6b5d", "mode subtitle")
    text_workflow: Tuple[str, str] = ("#8a7560", "workflow phase")
    text_tab: Tuple[str, str] = ("#7a6655", "tab text")
    text_tab_alt: Tuple[str, str] = ("#6a5746", "alt tab text")
    text_tab_project: Tuple[str, str] = ("#705b49", "project tab text")
    text_tab_hover: Tuple[str, str] = ("#4a3828", "tab hover text")
    text_stale: Tuple[str, str] = ("#a08878", "stale text")
    text_stale_checked: Tuple[str, str] = ("#8a6a5a", "stale checked text")
    text_char_meta: Tuple[str, str] = ("#6e5a48", "character meta")
    text_char_status: Tuple[str, str] = ("#7a6a5a", "character status")
    text_char_retired: Tuple[str, str] = ("#7a5040", "retired character")
    text_memory_category: Tuple[str, str] = ("#7c6249", "memory category")
    text_memory_last: Tuple[str, str] = ("#a89580", "memory last chapter")
    text_memory_fix: Tuple[str, str] = ("#5a8a5a", "memory fix green")
    text_memory_running: Tuple[str, str] = ("#6b8e5a", "memory running green")
    text_memory_review: Tuple[str, str] = ("#5a5046", "memory review detail")
    text_memory_review_value: Tuple[str, str] = ("#6a6056", "memory review value")
    text_blueprint_badge: Tuple[str, str] = ("#8b553d", "blueprint category badge")
    text_routing_sub: Tuple[str, str] = ("#9a7b64", "routing subgroup")
    text_fallback_arrow: Tuple[str, str] = ("#7b5d43", "fallback dropdown arrow")
    text_fallback_title: Tuple[str, str] = ("#4b382b", "fallback popup title")
    text_fallback_status: Tuple[str, str] = ("#8b6b54", "fallback popup status")
    text_fallback_handle: Tuple[str, str] = ("#b39a84", "fallback popup handle")
    text_fallback_rank_inactive: Tuple[str, str] = ("#9d8b7b", "fallback rank inactive")
    text_fallback_name: Tuple[str, str] = ("#3e2f24", "fallback popup name")
    text_fallback_checkbox: Tuple[str, str] = ("#6f5848", "fallback mini toggle")
    text_fallback_disabled: Tuple[str, str] = ("#c0b0a4", "fallback disabled")
    text_guidance: Tuple[str, str] = ("#5a4a3a", "guidance title")
    text_checkbox: Tuple[str, str] = ("#544337", "checkbox label")
    text_subplot_result: Tuple[str, str] = ("#4a6b3a", "subplot result green")
    text_collapse_nested: Tuple[str, str] = ("#4b3b2e", "nested collapse toggle")
    text_quiet: Tuple[str, str] = ("#5a483d", "quiet variant text")
    text_revision_chip: Tuple[str, str] = ("#5f493b", "revision direction chip")
    text_badge_default: Tuple[str, str] = ("#735f4e", "badge default tone")

    # ── Status ──────────────────────────────────────────────────────
    status_success: Tuple[str, str] = ("#2e7d32", "green success")
    status_success_warm: Tuple[str, str] = ("#2f6d4c", "warm green")
    status_success_deep: Tuple[str, str] = ("#2d7a47", "deep green")
    status_success_light: Tuple[str, str] = ("#4caf50", "light green dot")
    status_success_bg: Tuple[str, str] = ("#e8f5e9", "success background")
    status_success_border: Tuple[str, str] = ("#a5d6a7", "success border")
    status_success_rate: Tuple[str, str] = ("#d4edda", "success rate bg")
    status_success_job: Tuple[str, str] = ("#5f8b5a", "job success indicator")
    status_ok_dialog: Tuple[str, str] = ("#2c6e3e", "dialog status ok")
    status_danger: Tuple[str, str] = ("#c0392b", "red danger")
    status_danger_alt: Tuple[str, str] = ("#a63d32", "alt danger")
    status_danger_deep: Tuple[str, str] = ("#9a352b", "deep danger")
    status_danger_dark: Tuple[str, str] = ("#7f1d1a", "dark danger error-state")
    status_danger_critical: Tuple[str, str] = ("#d32f2f", "critical severity")
    status_danger_rate: Tuple[str, str] = ("#f8d7da", "danger rate bg")
    status_danger_rate_text: Tuple[str, str] = ("#721c24", "danger rate text")
    status_error_dialog: Tuple[str, str] = ("#a23a2a", "dialog status error")
    status_warning: Tuple[str, str] = ("#e65100", "orange warning")
    status_warning_alt: Tuple[str, str] = ("#c0622b", "alt warning")
    status_warning_bg: Tuple[str, str] = ("#fff3e0", "warning background")
    status_warning_border: Tuple[str, str] = ("#ffcc80", "warning border")
    status_warning_rate: Tuple[str, str] = ("#fff3cd", "warning rate bg")
    status_warning_text: Tuple[str, str] = ("#856404", "warning text")
    status_info: Tuple[str, str] = ("#4a6d92", "blue info")
    status_info_dialog: Tuple[str, str] = ("#5b6470", "dialog status info")
    status_dot_red: Tuple[str, str] = ("#e57373", "status dot red")
    status_dot_yellow: Tuple[str, str] = ("#ffb74d", "status dot yellow")

    # ── Border ──────────────────────────────────────────────────────
    border_default: Tuple[str, str] = ("#8d6b4c", "default warm border")
    border_muted: Tuple[str, str] = ("#7b5d43", "muted warm border")
    border_soft: Tuple[str, str] = ("#b17f5b", "soft warm border")
    border_warm: Tuple[str, str] = ("#9d7a5c", "warm form border")
    border_control: Tuple[str, str] = ("#7b5d43", "control border")

    # ── Role colors (character cards) ───────────────────────────────
    role_protagonist: Tuple[str, str] = ("#b65634", "protagonist accent")
    role_deuteragonist: Tuple[str, str] = ("#c86a47", "deuteragonist accent")
    role_antagonist: Tuple[str, str] = ("#9a352b", "antagonist border")
    role_supporting: Tuple[str, str] = ("#5a7a6a", "supporting border")
    role_minor: Tuple[str, str] = ("#8b7460", "minor role muted")

    # ── Relation tone colors (character graph edges) ────────────────
    relation_bond: Tuple[str, str] = ("#be5d37", "bond / emotional connection")
    relation_ally: Tuple[str, str] = ("#4a7e5e", "ally / trust")
    relation_tension: Tuple[str, str] = ("#8e3939", "tension / conflict")
    relation_identity: Tuple[str, str] = ("#4d6084", "identity / reference")
    relation_neutral: Tuple[str, str] = ("#8d6b4c", "neutral relation")

    # ── Decorative / Misc ───────────────────────────────────────────
    motif_purple: Tuple[str, str] = ("#5f4db7", "motif tips purple")
    slate: Tuple[str, str] = ("#64748b", "slate text")
    slate_bg: Tuple[str, str] = ("#f1f5f9", "slate background")
    danger_red: Tuple[str, str] = ("#dc2626", "pure red danger")
    danger_red_bg: Tuple[str, str] = ("#fef2f2", "red danger background")
    memory_health: Tuple[str, str] = ("#475569", "memory health text")
    separator: Tuple[str, str] = ("#d6c9b8", "separator line")
    outline_ignored: Tuple[str, str] = ("#9e9e9e", "ignored outline grey")
    white: Tuple[str, str] = ("#ffffff", "pure white")
    shadow: Tuple[str, str] = ("#45291d", "surface drop-shadow")
    fade_bg: Tuple[str, str] = ("#f2e8dc", "fade overlay background")

    # ── Categorical / Chart palette ─────────────────────────────────
    chart_1: Tuple[str, str] = ("#788c64", "categorical olive green")
    chart_2: Tuple[str, str] = ("#64788c", "categorical slate blue")
    chart_3: Tuple[str, str] = ("#8c6478", "categorical mauve")
    chart_4: Tuple[str, str] = ("#c8a864", "categorical gold")
    chart_5: Tuple[str, str] = ("#c8825a", "categorical salmon")
    chart_6: Tuple[str, str] = ("#b46e50", "categorical terracotta")
    chart_7: Tuple[str, str] = ("#96785a", "categorical tan")
    chart_8: Tuple[str, str] = ("#6e8278", "categorical teal")


# ── Build the dotted-name lookup dict ───────────────────────────────────────

_TOKENS = _ColorTokens()

# Mapping: underscore field name → dotted token name
_FIELD_TO_DOTTED: Dict[str, str] = {
    # Accent / Brand
    "accent_primary": "accent.primary",
    "accent_primary_hover": "accent.primary.hover",
    "accent_primary_pressed": "accent.primary.pressed",
    "accent_light": "accent.light",
    "accent_dark": "accent.dark",
    "accent_warm": "accent.warm",
    "accent_muted": "accent.muted",
    "accent_deep": "accent.deep",
    "accent_badge": "accent.badge",
    "accent_rewriting": "accent.rewriting",
    "accent_fallback": "accent.fallback",
    "accent_slider_warm": "accent.slider.warm",
    "accent_rank_top1": "accent.rank.top1",
    "accent_rank_top2": "accent.rank.top2",
    "accent_rank_top3": "accent.rank.top3",
    "accent_rank_label": "accent.rank.label",
    "brand_logo_frame": "brand.logo.frame",
    "brand_logo_border": "brand.logo.border",
    "brand_logo_accent": "brand.logo.accent",
    # Background
    "bg_workspace": "bg.workspace",
    "bg_workspace_mid": "bg.workspace.mid",
    "bg_workspace_end": "bg.workspace.end",
    "bg_surface": "bg.surface",
    "bg_surface_elevated": "bg.surface.elevated",
    "bg_input": "bg.input",
    "bg_input_soft": "bg.input.soft",
    "bg_panel": "bg.panel",
    "bg_panel_muted": "bg.panel.muted",
    "bg_control": "bg.control",
    "bg_outline_tab": "bg.outline.tab",
    "bg_control_hover": "bg.control.hover",
    "bg_control_pressed": "bg.control.pressed",
    "bg_inset": "bg.inset",
    "bg_hero_start": "bg.hero.start",
    "bg_hero_mid": "bg.hero.mid",
    "bg_hero_end": "bg.hero.end",
    "bg_sidebar_start": "bg.sidebar.start",
    "bg_sidebar_mid": "bg.sidebar.mid",
    "bg_sidebar_end": "bg.sidebar.end",
    "bg_dialog": "bg.dialog",
    "bg_hover_secondary": "bg.hover.secondary",
    "bg_hover_accent": "bg.hover.accent",
    "bg_blueprint_row": "bg.blueprint.row",
    "bg_memory_tag": "bg.memory.tag",
    "bg_memory_hover": "bg.memory.hover",
    # Text
    "text_primary": "text.primary",
    "text_secondary": "text.secondary",
    "text_heading": "text.heading",
    "text_heading_warm": "text.heading.warm",
    "text_heading_deep": "text.heading.deep",
    "text_body": "text.body",
    "text_body_alt": "text.body.alt",
    "text_body_warm": "text.body.warm",
    "text_muted": "text.muted",
    "text_muted_strong": "text.muted.strong",
    "text_muted_soft": "text.muted.soft",
    "text_muted_badge": "text.muted.badge",
    "text_muted_quiet": "text.muted.quiet",
    "text_disabled": "text.disabled",
    "text_empty": "text.empty",
    "text_empty_soft": "text.empty.soft",
    "text_artifact": "text.artifact",
    "text_topbar_title": "text.topbar.title",
    "text_topbar_subtitle": "text.topbar.subtitle",
    "text_topbar_meta": "text.topbar.meta",
    "text_eyebrow": "text.eyebrow",
    "text_brand_gold": "text.brand.gold",
    "text_brand_light": "text.brand.light",
    "text_rail_footer": "text.rail.footer",
    "text_chapter_rail": "text.chapter.rail",
    "text_mode_sub": "text.mode.sub",
    "text_workflow": "text.workflow",
    "text_tab": "text.tab",
    "text_tab_alt": "text.tab.alt",
    "text_tab_project": "text.tab.project",
    "text_tab_hover": "text.tab.hover",
    "text_stale": "text.stale",
    "text_stale_checked": "text.stale.checked",
    "text_char_meta": "text.char.meta",
    "text_char_status": "text.char.status",
    "text_char_retired": "text.char.retired",
    "text_memory_category": "text.memory.category",
    "text_memory_last": "text.memory.last",
    "text_memory_fix": "text.memory.fix",
    "text_memory_running": "text.memory.running",
    "text_memory_review": "text.memory.review",
    "text_memory_review_value": "text.memory.review.value",
    "text_blueprint_badge": "text.blueprint.badge",
    "text_routing_sub": "text.routing.sub",
    "text_fallback_arrow": "text.fallback.arrow",
    "text_fallback_title": "text.fallback.title",
    "text_fallback_status": "text.fallback.status",
    "text_fallback_handle": "text.fallback.handle",
    "text_fallback_rank_inactive": "text.fallback.rank.inactive",
    "text_fallback_name": "text.fallback.name",
    "text_fallback_checkbox": "text.fallback.checkbox",
    "text_fallback_disabled": "text.fallback.disabled",
    "text_guidance": "text.guidance",
    "text_checkbox": "text.checkbox",
    "text_subplot_result": "text.subplot.result",
    "text_collapse_nested": "text.collapse.nested",
    "text_quiet": "text.quiet",
    "text_revision_chip": "text.revision.chip",
    "text_badge_default": "text.badge.default",
    # Status
    "status_success": "status.success",
    "status_success_warm": "status.success.warm",
    "status_success_deep": "status.success.deep",
    "status_success_light": "status.success.light",
    "status_success_bg": "status.success.bg",
    "status_success_border": "status.success.border",
    "status_success_rate": "status.success.rate",
    "status_success_job": "status.success.job",
    "status_ok_dialog": "status.ok.dialog",
    "status_danger": "status.danger",
    "status_danger_alt": "status.danger.alt",
    "status_danger_deep": "status.danger.deep",
    "status_danger_dark": "status.danger.dark",
    "status_danger_critical": "status.danger.critical",
    "status_danger_rate": "status.danger.rate",
    "status_danger_rate_text": "status.danger.rate.text",
    "status_error_dialog": "status.error.dialog",
    "status_warning": "status.warning",
    "status_warning_alt": "status.warning.alt",
    "status_warning_bg": "status.warning.bg",
    "status_warning_border": "status.warning.border",
    "status_warning_rate": "status.warning.rate",
    "status_warning_text": "status.warning.text",
    "status_info": "status.info",
    "status_info_dialog": "status.info.dialog",
    "status_dot_red": "status.dot.red",
    "status_dot_yellow": "status.dot.yellow",
    # Border
    "border_default": "border.default",
    "border_muted": "border.muted",
    "border_soft": "border.soft",
    "border_warm": "border.warm",
    "border_control": "border.control",
    # Role
    "role_protagonist": "role.protagonist",
    "role_deuteragonist": "role.deuteragonist",
    "role_antagonist": "role.antagonist",
    "role_supporting": "role.supporting",
    "role_minor": "role.minor",
    # Relation
    "relation_bond": "relation.bond",
    "relation_ally": "relation.ally",
    "relation_tension": "relation.tension",
    "relation_identity": "relation.identity",
    "relation_neutral": "relation.neutral",
    # Decorative / Misc
    "motif_purple": "motif.purple",
    "slate": "slate",
    "slate_bg": "slate.bg",
    "danger_red": "danger.red",
    "danger_red_bg": "danger.red.bg",
    "memory_health": "memory.health",
    "separator": "separator",
    "outline_ignored": "outline.ignored",
    "white": "white",
    "shadow": "shadow",
    "fade_bg": "fade.bg",
    # Categorical / Chart
    "chart_1": "chart.1",
    "chart_2": "chart.2",
    "chart_3": "chart.3",
    "chart_4": "chart.4",
    "chart_5": "chart.5",
    "chart_6": "chart.6",
    "chart_7": "chart.7",
    "chart_8": "chart.8",
}


def _build_colors_dict() -> Dict[str, Tuple[str, str]]:
    """Build the dotted-name → (hex, description) mapping from the dataclass."""
    result: Dict[str, Tuple[str, str]] = {}
    for f in fields(_TOKENS):
        dotted = _FIELD_TO_DOTTED.get(f.name)
        if dotted is not None:
            result[dotted] = getattr(_TOKENS, f.name)
    return result


COLORS: Dict[str, Tuple[str, str]] = _build_colors_dict()


# ── Reverse lookup: hex → list of token names ───────────────────────────────


def hex_to_tokens(hex_value: str) -> list[str]:
    """Return all token names that map to *hex_value* (case-insensitive)."""
    target = hex_value.lower()
    return [name for name, (h, _) in COLORS.items() if h.lower() == target]


# ── Public helpers ──────────────────────────────────────────────────────────


def all_hex_values() -> set[str]:
    """Return the set of all unique hex values (lowercase) in the token system."""
    return {h.lower() for h, _ in COLORS.values()}
