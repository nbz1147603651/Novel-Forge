"""Pages subdirectory — auto-discovery of <group>/page.py modules.

Each subdirectory should have:
- AGENTS.md (group-specific conventions)
- page.py (the *Page class)

Subdirectories discovered via pkgutil.iter_modules on import. Each
subdirectory's __init__.py must register its page class via page_registry.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from typing import Any

_PAGE_CLASS_ALIASES: dict[str, tuple[str, str]] = {
    "ChapterStudioPage": ("novel_forge.desktop.pages.chapter_studio.page", "ChapterStudioPage"),
    "DashboardPage": ("novel_forge.desktop.pages.standalone.dashboard_page", "DashboardPage"),
    "ProjectsPage": ("novel_forge.desktop.pages.standalone.projects_page", "ProjectsPage"),
    "SettingsPage": ("novel_forge.desktop.pages.settings.page", "SettingsPage"),
    "WorkflowPage": ("novel_forge.desktop.pages.workflow.page", "WorkflowPage"),
}

_MODULE_ALIASES: dict[str, str] = {
    "chapter_studio_action_panel": "novel_forge.desktop.pages.chapter_studio.action_panel",
    "chapter_studio_actions": "novel_forge.desktop.pages.chapter_studio.actions",
    "chapter_studio_artifacts": "novel_forge.desktop.pages.chapter_studio.artifacts",
    "chapter_studio_auto": "novel_forge.desktop.pages.chapter_studio.auto",
    "chapter_studio_autorun": "novel_forge.desktop.pages.chapter_studio.autorun",
    "chapter_studio_contract": "novel_forge.desktop.pages.chapter_studio.contract",
    "chapter_studio_coord": "novel_forge.desktop.pages.chapter_studio.coord",
    "chapter_studio_coordinator": "novel_forge.desktop.pages.chapter_studio.coordinator",
    "chapter_studio_data": "novel_forge.desktop.pages.chapter_studio.data",
    "chapter_studio_dialogs": "novel_forge.desktop.pages.chapter_studio.dialogs",
    "chapter_studio_inspector": "novel_forge.desktop.pages.chapter_studio.inspector",
    "chapter_studio_jobs": "novel_forge.desktop.pages.chapter_studio.jobs",
    "chapter_studio_memory": "novel_forge.desktop.pages.chapter_studio.memory",
    "chapter_studio_page": "novel_forge.desktop.pages.chapter_studio.page",
    "chapter_studio_renderers": "novel_forge.desktop.pages.chapter_studio.renderers",
    "chapter_studio_state": "novel_forge.desktop.pages.chapter_studio.state",
    "chapter_studio_widgets": "novel_forge.desktop.pages.chapter_studio.widgets",
    "chapter_studio_workers": "novel_forge.desktop.pages.chapter_studio.workers",
    "character_artifact_writer": "novel_forge.desktop.pages.standalone.character_artifact_writer",
    "character_bible_editor": "novel_forge.desktop.pages.standalone.character_bible_editor",
    "character_bible_store": "novel_forge.desktop.pages.standalone.character_bible_store",
    "character_profile_page": "novel_forge.desktop.pages.standalone.character_profile_page",
    "character_shared_widgets": "novel_forge.desktop.pages.standalone.character_shared_widgets",
    "dashboard_page": "novel_forge.desktop.pages.standalone.dashboard_page",
    "dialogs_add_to_humanize": "novel_forge.desktop.pages.standalone.dialogs_add_to_humanize",
    "document_renderer_incremental": "novel_forge.desktop.pages.document_renderer.incremental",
    "document_renderer_relationships": "novel_forge.desktop.pages.document_renderer.relationships",
    "final_revision": "novel_forge.desktop.pages.standalone.final_revision",
    "humanize_library_dashboard": "novel_forge.desktop.pages.standalone.humanize_library_dashboard",
    "init_artifact_catalog": "novel_forge.desktop.pages.standalone.init_artifact_catalog",
    "init_manual_repair_dialog": "novel_forge.desktop.pages.standalone.init_manual_repair_dialog",
    "memory_panel": "novel_forge.desktop.pages.standalone.memory_panel",
    "outline_editor": "novel_forge.desktop.pages.standalone.outline_editor",
    "outline_sync_presenter": "novel_forge.desktop.pages.standalone.outline_sync_presenter",
    "projects_page": "novel_forge.desktop.pages.standalone.projects_page",
    "relationship_network_page": "novel_forge.desktop.pages.standalone.relationship_network_page",
    "renderer_html": "novel_forge.desktop.pages.standalone.renderer_html",
    "settings_components": "novel_forge.desktop.pages.settings.components",
    "settings_contract": "novel_forge.desktop.pages.settings.contract",
    "settings_model_mgmt": "novel_forge.desktop.pages.settings.model_mgmt",
    "settings_ollama": "novel_forge.desktop.pages.settings.ollama",
    "settings_page": "novel_forge.desktop.pages.settings.page",
    "settings_page_save": "novel_forge.desktop.pages.settings.save",
    "settings_page_specs": "novel_forge.desktop.pages.settings.specs",
    "settings_sections": "novel_forge.desktop.pages.settings.sections",
    "subplot_manager": "novel_forge.desktop.pages.standalone.subplot_manager",
    "token_analytics": "novel_forge.desktop.pages.standalone.token_analytics",
    "workflow_artifacts": "novel_forge.desktop.pages.workflow.artifacts",
    "workflow_chapter_launcher": "novel_forge.desktop.pages.workflow.chapter_launcher",
    "workflow_components": "novel_forge.desktop.pages.workflow.components",
    "workflow_forms": "novel_forge.desktop.pages.workflow.forms",
    "workflow_jobs": "novel_forge.desktop.pages.workflow.jobs",
    "workflow_page": "novel_forge.desktop.pages.workflow.page",
    "workflow_presets": "novel_forge.desktop.pages.workflow.presets",
    "workflow_widgets": "novel_forge.desktop.pages.workflow.widgets",
    "workflow_workers": "novel_forge.desktop.pages.workflow.workers",
}

for _finder, _name, _ispkg in pkgutil.iter_modules(__path__):
    if _name.startswith("_"):
        continue
    if not _ispkg:
        continue
    importlib.import_module(f"{__name__}.{_name}")


def __getattr__(name: str) -> Any:
    if name in _PAGE_CLASS_ALIASES:
        module_name, attr_name = _PAGE_CLASS_ALIASES[name]
        value = getattr(importlib.import_module(module_name), attr_name)
        globals()[name] = value
        return value
    if name in _MODULE_ALIASES:
        module = importlib.import_module(_MODULE_ALIASES[name])
        globals()[name] = module
        sys.modules[f"{__name__}.{name}"] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted([*_PAGE_CLASS_ALIASES, *_MODULE_ALIASES])
