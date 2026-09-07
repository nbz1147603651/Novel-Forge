"""Sub-module of novel_forge.desktop.pages.document_renderer.

Auto-generated in the M3.2 split. Contains smart_render.py renderers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from PySide6.QtWidgets import (
    QTabWidget,
    QWidget,
)

# Late imports to break circular dependencies between sub-modules.
from novel_forge.desktop.pages.document_renderer.character_bible import (
    render_character_bible,
)
from novel_forge.desktop.pages.document_renderer.entity import (
    render_character_system,
    render_entity_graph,
    render_entity_registry,
)
from novel_forge.desktop.pages.document_renderer.narrative_blueprint import (
    render_narrative_blueprint,
)
from novel_forge.desktop.pages.document_renderer.popups import (
    _configure_artifact_tabs,
)
from novel_forge.desktop.pages.document_renderer.relationship_matrix import (
    render_character_relationship_matrix,
)
from novel_forge.desktop.pages.document_renderer.subplot import (
    render_subplot_execution_matrix,
)
from novel_forge.desktop.pages.document_renderer_reports import (
    render_alignment_report,
    render_bridge,
    render_canon_state,
    render_causal_report,
    render_chapter_contracts,
    render_chapter_plan,
    render_continuity_report,
    render_creative_report,
    render_eval_report,
    render_expression_repetition_report,
    render_generic_report,
    render_guard_report,
    render_humanize_report,
    render_init_editorial_readiness_report,
    render_init_readiness_report,
    render_knowledge_boundary_report,
    render_llm_narrative_contract,
    render_narrative_contract,
    render_repair_plan_report,
    render_research_dossier_report,
    render_spec,
    render_stage_visibility_report,
    render_state_adjudication_index,
    render_state_adjudication_report,
    render_state_evidence_snapshot,
    render_state_ledger,
    render_state_pending_queue,
    render_story_bible,
    render_story_state_projection,
    render_web_research_report,
)
from novel_forge.desktop.pages.document_renderer_story_artifacts import (
    render_blueprint_element_selection,
    render_book_consistency_repair_report,
    render_book_consistency_report,
    render_chapter_design_matrix,
    render_chapter_memory_diagnostics,
    render_editorial_contract,
    render_init_claim_contract_coverage,
    render_init_coherence_claim_ledger,
    render_init_coherence_claims,
    render_init_coherence_index,
    render_init_coherence_profile,
    render_init_conflict_adjudication,
    render_init_conflict_candidates,
    render_narrative_blueprint_fragments,
    render_outline,
    render_short_blueprint,
    render_short_creative_summary,
    render_state_packet,
    render_version_diff,
)
from novel_forge.desktop.pages.standalone.init_artifact_catalog import (
    BLUEPRINT_THEME_ARTIFACTS,
    CHARACTER_THEME_ARTIFACTS,
    INIT_ARTIFACT_TITLES,
    INIT_COHERENCE_THEME_ARTIFACTS,
)

JsonDict = dict[str, Any]
FeedbackMarker = tuple[float, float, float, JsonDict, JsonDict]
WeaveLine = tuple[float, float, float, JsonDict, JsonDict]
DocumentRenderer = Callable[[dict[str, Any]], QWidget]


def _load_json_dict(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _load_jsonl_dicts(path: Path) -> list[dict[str, Any]] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    records: list[dict[str, Any]] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        if isinstance(item, dict):
            records.append(item)
    return records


def _add_generic_json_tab(tabs: QTabWidget, path: Path, label: str) -> bool:
    if path.name == "init_coherence_claims.jsonl":
        widget = smart_render_document(path)
        if widget is None:
            return False
        tabs.addTab(widget, label)
        return True
    data = _load_json_dict(path)
    if data is None:
        return False
    widget = smart_render_document(path)
    if widget is None:
        widget = render_generic_report(data, title=label)
    tabs.addTab(widget, label)
    return True


def _add_artifact_group_tab(
    tabs: QTabWidget,
    label: str,
    artifacts: list[tuple[str, Path]],
    object_name: str,
) -> bool:
    group_tabs = _configure_artifact_tabs(QTabWidget(), object_name)
    added = False
    for artifact_label, artifact_path in artifacts:
        added = _add_generic_json_tab(group_tabs, artifact_path, artifact_label) or added
    if not added:
        group_tabs.deleteLater()
        return False
    tabs.addTab(group_tabs, label)
    return True


def _render_character_bible_bundle(path: Path, data: dict[str, Any]) -> QWidget:
    entity_graph = _load_json_dict(path.parent / "narrative_state" / "entity_graph.json")
    primary = render_character_bible(data, entity_graph=entity_graph)
    extras = [(label, path.parent / rel_path) for label, rel_path in CHARACTER_THEME_ARTIFACTS]
    if not any(extra_path.exists() for _, extra_path in extras):
        return primary

    tabs = _configure_artifact_tabs(QTabWidget(), "characterBibleBundleTabs")
    tabs.addTab(primary, "总览")
    for label, extra_path in extras:
        _add_generic_json_tab(tabs, extra_path, label)
    return tabs


def _render_element_selection_bundle(path: Path, data: dict[str, Any]) -> QWidget:
    project_dir = path.parent.parent if path.parent.name == "plans" else path.parent
    style_path = project_dir / "style_profile.json"
    style_data = _load_json_dict(style_path)
    if style_data is None:
        return render_blueprint_element_selection(data)

    from novel_forge.desktop.pages.document_renderer_story_artifacts import (
        render_profile_style,
        render_reading_power_window_config,
    )

    tabs = _configure_artifact_tabs(QTabWidget(), "elementSelectionBundleTabs")
    tabs.addTab(render_blueprint_element_selection(data), "总览")
    tabs.addTab(render_profile_style(style_data), "风格规范")
    if style_data.get("reading_power_window_config"):
        tabs.addTab(render_reading_power_window_config(style_data), "追读力窗口")
    return tabs


def _render_style_profile_bundle(path: Path, data: dict[str, Any]) -> QWidget:
    from novel_forge.desktop.pages.document_renderer_story_artifacts import (
        render_profile_style,
        render_reading_power_window_config,
    )

    tabs = _configure_artifact_tabs(QTabWidget(), "styleProfileTabs")
    tabs.addTab(render_profile_style(data), "总览")

    element_paths = [
        path.parent / "blueprint_elements_selection.json",
        path.parent / "plans" / "blueprint_elements_selection.json",
    ]
    element_path = next((candidate for candidate in element_paths if candidate.exists()), None)
    if element_path is not None:
        element_data = _load_json_dict(element_path)
        if element_data is not None:
            tabs.addTab(render_blueprint_element_selection(element_data), "要素选择")

    if data.get("reading_power_window_config"):
        tabs.addTab(render_reading_power_window_config(data), "追读力窗口")
    return tabs


def _render_narrative_blueprint_bundle(path: Path, data: dict[str, Any]) -> QWidget:
    project_dir = path.parent.parent if path.parent.name == "plans" else path.parent
    primary = render_narrative_blueprint(data, project_path=project_dir)
    blueprint_artifacts = [
        (label, project_dir / rel_path) for label, rel_path in BLUEPRINT_THEME_ARTIFACTS
    ]
    coherence_artifacts = [
        (label, project_dir / rel_path) for label, rel_path in INIT_COHERENCE_THEME_ARTIFACTS
    ]
    if not any(
        extra_path.exists() for _, extra_path in (*blueprint_artifacts, *coherence_artifacts)
    ):
        return primary

    tabs = _configure_artifact_tabs(QTabWidget(), "narrativeBlueprintBundleTabs")
    tabs.addTab(primary, "总览")

    direct_labels = {"叙事契约", "章节契约", "创作导演包"}
    appendix_artifacts: list[tuple[str, Path]] = []
    for label, extra_path in blueprint_artifacts:
        if label in direct_labels:
            _add_generic_json_tab(tabs, extra_path, label)
        else:
            appendix_artifacts.append((label, extra_path))

    _add_artifact_group_tab(
        tabs,
        "附录",
        appendix_artifacts,
        "narrativeBlueprintAppendixTabs",
    )
    _add_artifact_group_tab(
        tabs,
        "一致性校验",
        coherence_artifacts,
        "narrativeBlueprintCoherenceTabs",
    )
    return tabs


def _looks_like_chapter_repair_plan(filename: str, data: dict[str, Any]) -> bool:
    return filename.endswith("_repair_plan.json") or (
        isinstance(data.get("issues"), list)
        and "expected_outcome" in data
        and any(key in data for key in ("target_sections", "must_keep", "must_change"))
    )


def _looks_like_humanize_report(filename: str, data: dict[str, Any]) -> bool:
    if re.fullmatch(r"chapter_\d{3}_humanize\.json", filename):
        return True
    return (
        "humanize_score" in data
        and isinstance(data.get("pattern_hits"), list)
        and isinstance(data.get("hits_by_category"), dict)
    )


_REPORT_TYPE_RENDERERS: dict[str, DocumentRenderer] = {
    "chapter_memory_diagnostics": render_chapter_memory_diagnostics,
    "expression_repetition": render_expression_repetition_report,
    "stage_visibility_diagnostics": render_stage_visibility_report,
}

_GENERIC_REPORT_TYPE_TITLES: dict[str, str] = {
    "contract_execution": "契约执行报告",
    "arc_liveness": "角色弧光活跃度",
    "milestone_window": "剧情里程碑窗口",
}

_STORY_BIBLE_CHECKPOINT_TITLES: dict[str, str] = {
    "story_bible_core": "世界观分片：核心前提",
    "story_bible_independent_fragments": "世界观分片：世界规则与主题",
    "story_bible_continuity": "世界观分片：连续性规则",
}

_STORY_BIBLE_FRAGMENT_KEYS: tuple[str, ...] = (
    "story_core",
    "world_rules",
    "themes_and_symbols",
    "continuity_rules",
)


def _render_registered_report(filename: str, data: dict[str, Any]) -> QWidget | None:
    if _looks_like_humanize_report(filename, data):
        return render_humanize_report(data)
    if _looks_like_chapter_repair_plan(filename, data):
        return render_repair_plan_report(data)
    report_type = str(data.get("report_type") or "").strip()
    renderer = _REPORT_TYPE_RENDERERS.get(report_type)
    if renderer is not None:
        return renderer(data)
    if "stage_visibility_diagnostics" in data:
        return render_stage_visibility_report(data)
    title = _GENERIC_REPORT_TYPE_TITLES.get(report_type)
    if title is not None:
        return render_generic_report(data, title=title)
    return None


def _dict_value(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_of_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _story_bible_checkpoint_payload(data: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    artifact_name = str(data.get("artifact_name") or "").strip()
    if artifact_name not in _STORY_BIBLE_CHECKPOINT_TITLES:
        return None
    fragments = data.get("fragments")
    if not isinstance(fragments, dict):
        return None

    payload: dict[str, Any] = {"title": _STORY_BIBLE_CHECKPOINT_TITLES[artifact_name]}
    remainder: dict[str, Any] = {}
    for fragment_name, raw_fragment in fragments.items():
        if not isinstance(raw_fragment, dict):
            continue
        fragment_payload: dict[str, Any] = {}
        for key in _STORY_BIBLE_FRAGMENT_KEYS:
            fragment_payload.update(_dict_value(raw_fragment.get(key)))
        if not fragment_payload:
            fragment_payload = dict(raw_fragment)
        for key, value in fragment_payload.items():
            if key in {"title", "premise", "era", "geography", "culture", "magic_or_tech", "tone"}:
                payload[key] = value
            elif key in {"rules", "themes"}:
                values = _list_of_strings(value)
                if values:
                    payload.setdefault(key, [])
                    payload[key].extend(values)
            elif key == "banned_intent_rules":
                values = _list_of_strings(value)
                if values:
                    payload.setdefault("rules", [])
                    payload["rules"].extend(values)
            elif key == "world_rule_book" and isinstance(value, dict):
                payload["world_rule_book"] = value
            else:
                remainder[f"{fragment_name}.{key}"] = value

    if len(payload) > 1:
        if remainder:
            payload["notes"] = "\n".join(
                f"{key}: {json.dumps(value, ensure_ascii=False, default=str)}"
                for key, value in remainder.items()
            )
        return artifact_name, payload
    if remainder:
        return artifact_name, {"title": payload["title"], **remainder}
    return None


def _render_split_checkpoint(filename: str, data: dict[str, Any]) -> QWidget | None:
    checkpoint = _story_bible_checkpoint_payload(data)
    if checkpoint is None:
        return None
    _artifact_name, payload = checkpoint
    if any(
        key in payload
        for key in ("premise", "era", "geography", "culture", "magic_or_tech", "tone")
    ) or any(payload.get(key) for key in ("rules", "themes")):
        return render_story_bible(payload)
    return render_generic_report(payload, title=str(payload.get("title") or filename))


def smart_render_document(path: Path) -> QWidget | None:
    """Try to render a document using the best available rich renderer.

    Returns a QWidget if a rich renderer is applicable, or None to fall back
    to the default plain-text display.
    """
    if path.name == "init_coherence_claims.jsonl":
        records = _load_jsonl_dicts(path)
        if records is None:
            return None
        return render_init_coherence_claims(records)

    if path.name == "state_ledger.jsonl":
        records = _load_jsonl_dicts(path)
        if records is None:
            return None
        return render_state_ledger(records)

    if path.suffix != ".json":
        return None

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    filename = path.name

    split_checkpoint = _render_split_checkpoint(filename, data)
    if split_checkpoint is not None:
        return split_checkpoint

    if data.get("artifact_type") == "text_revision_diff":
        return render_version_diff(
            data,
            label_a=str(data.get("label_before") or "原文"),
            label_b=str(data.get("label_after") or "修订后"),
        )

    if filename == "init_coherence_profile.json":
        return render_init_coherence_profile(data)

    if filename == "init_coherence_claim_ledger.json":
        return render_init_coherence_claim_ledger(data)

    if filename == "init_coherence_index.json":
        return render_init_coherence_index(data)

    if filename == "init_claim_contract_coverage.json":
        return render_init_claim_contract_coverage(data)

    if filename == "init_conflict_candidates.json":
        return render_init_conflict_candidates(data)

    if filename == "init_conflict_adjudication.json":
        return render_init_conflict_adjudication(data)

    if filename == "init_readiness.json":
        return render_init_readiness_report(data)

    if filename == "init_editorial_readiness.json":
        return render_init_editorial_readiness_report(data)

    if filename == "init_web_research.json":
        return render_web_research_report(data)

    if filename == "init_research_dossier.json":
        return render_research_dossier_report(data)

    if filename == "outline_research_grounding.json":
        return render_generic_report(data, title="大纲资料校准")

    if filename == "chapter_contracts.json":
        return render_chapter_contracts(data)

    if filename == "story_state_projection.json":
        return render_story_state_projection(data)

    if filename == "adjudication_report_index.json":
        return render_state_adjudication_index(data)

    if filename == "pending_queue.json" and path.parent.name == "narrative_state":
        return render_state_pending_queue(data)

    if filename.endswith("_evidence.json") and path.parent.name == "evidence":
        return render_state_evidence_snapshot(data)

    if filename.endswith("_state_adjudication.json"):
        return render_state_adjudication_report(data)

    if filename == "canon_current.json":
        return render_canon_state(data)

    if filename == "subplot_execution_matrix.json":
        return render_subplot_execution_matrix(data)

    if filename == "character_system.json":
        return render_character_system(data)

    if filename == "character_relationship_matrix.json":
        system_data = _load_json_dict(path.parent / "character_system.json")
        return render_character_relationship_matrix(data, character_system=system_data)

    if filename == "entity_graph.json":
        return render_entity_graph(data)

    if filename == "entity_registry.json":
        return render_entity_registry(data)

    if filename == "narrative_contract.json" and path.parent.name == "plans":
        return render_narrative_contract(data)

    if filename == "narrative_contract.json" and path.parent.name == "narrative_state":
        return render_llm_narrative_contract(data)

    if filename == "narrative_blueprint_fragments.json":
        return render_narrative_blueprint_fragments(data)

    if filename == "editorial_contract.json":
        return render_editorial_contract(data)

    if filename == "chapter_design_matrix.json":
        return render_chapter_design_matrix(data)

    if filename in INIT_ARTIFACT_TITLES:
        return render_generic_report(data, title=INIT_ARTIFACT_TITLES[filename])

    # story_bible.json
    if filename == "story_bible.json" or (
        "premise" in data and "era" in data and "geography" in data
    ):
        # NOTE:
        # Incremental rich-text insertion can collapse paragraph boundaries in
        # some Qt environments, causing section titles/body text to concatenate.
        # Use full-document renderer for stable layout.
        return render_story_bible(data)

    # character_bible.json
    if filename == "character_bible.json" or "characters" in data:
        chars = data.get("characters", [])
        if chars and isinstance(chars[0], dict) and "name" in chars[0]:
            return _render_character_bible_bundle(path, data)

    # blueprint_elements_selection.json
    if filename == "blueprint_elements_selection.json" or (
        "required_elements" in data and "extension_elements" in data and "library_version" in data
    ):
        return _render_element_selection_bundle(path, data)

    # style_profile.json — combined with element selection if available
    if filename == "style_profile.json" or ("modules" in data and "summary" in data):
        return _render_style_profile_bundle(path, data)

    # short blueprint (短篇叙事蓝图) — must precede generic narrative_blueprint check
    if filename == "short_blueprint.json" or (
        "narrative_phases" in data
        and "emotional_arc" in data
        and "turning_points" in data
        and "total_chapters" not in data
        and "subplot_plan" not in data
    ):
        return render_short_blueprint(data)

    # narrative_blueprint.json
    if "narrative_phases" in data:
        return _render_narrative_blueprint_bundle(path, data)

    # chapter_design_matrix.json — must precede outline.json because both have
    # total_chapters + chapters. outline.json 同样包含 cast_plan / emotional_plan
    # 字段，需要通过文件名精确排除以避免误匹配。
    if (
        filename != "outline.json"
        and "total_chapters" in data
        and "chapters" in data
        and isinstance(data.get("chapters"), list)
        and any(
            isinstance(chapter, dict)
            and ("plot_duties" in chapter or "cast_plan" in chapter or "emotional_plan" in chapter)
            for chapter in data.get("chapters", [])[:5]
        )
    ):
        return render_chapter_design_matrix(data)

    # outline.json
    if "total_chapters" in data and "chapters" in data:
        return render_outline(data)

    # spec.json
    if filename == "spec.json" or ("genre" in data and "length_target" in data):
        return render_spec(data)

    # eval reports
    if "overall_score" in data and "scores" in data:
        # NOTE:
        # Keep eval report on full-document renderer to avoid block-boundary
        # collapse (same class of issue as bridge rendering artifacts).
        return render_eval_report(data)

    # chapter state packet
    if "chapter_outline" in data and "canon_context" in data:
        return render_state_packet(data)

    # chapter bridge
    if "bridge_summary" in data or (
        "from_chapter" in data and "to_chapter" in data and "transition_mode" in data
    ):
        # NOTE:
        # Bridge incremental rendering via QTextCursor.insertHtml() can collapse
        # block boundaries in Qt rich-text mode, causing headings/rows to run
        # together (e.g. "章节桥接第 0 章 → 第 1 章"). Use full-document
        # HTML rendering for stable layout.
        return render_bridge(data)

    # chapter plan
    if "scene_intents" in data:
        return render_chapter_plan(data)

    # alignment report
    if "alignment_score" in data:
        return render_alignment_report(data)

    # continuity report
    if "continuity_score" in data:
        return render_continuity_report(data)

    # reading power report
    if filename.startswith("reading_power_report") or (
        "hook_type" in data and "hook_strength" in data and "micro_payoffs" in data
    ):
        from novel_forge.desktop.pages.document_renderer_story_artifacts import (
            render_reading_power_report,
        )

        return render_reading_power_report(data)

    # causal validation report
    if "causal_score" in data:
        return render_causal_report(data)

    # guard report (AI 护栏)
    if "decision" in data and "chapter_number" in data:
        decision = data.get("decision")
        if isinstance(decision, dict) and "risk_level" in decision:
            return render_guard_report(data)

    # knowledge boundary verification (知识边界审计)
    if filename.endswith("_knowledge_boundary_verification.json"):
        return render_knowledge_boundary_report(data)

    # short creative summary (短篇创作分析)
    if filename == "short_creative_summary.json" or (
        "narrative_analysis" in data and "thematic_analysis" in data and "beat_fulfillment" in data
    ):
        return render_short_creative_summary(data)

    # creative report (创作总结)
    if "structured_summary" in data and "creative_highlights" in data:
        return render_creative_report(data)

    # book consistency audit (全书一致性审计)
    if "consistency_score" in data and "issues" in data:
        return render_book_consistency_report(data)

    # whole-book targeted repair report
    if data.get("report_type") == "book_consistency_repair_report" and "task_flow" in data:
        return render_book_consistency_repair_report(data)

    registered_report = _render_registered_report(filename, data)
    if registered_report is not None:
        return registered_report

    # volume audit report
    if filename.startswith("volume_") and filename.endswith("_audit.json"):
        stem = path.stem.replace("_", " ").title()
        return render_generic_report(data, title=stem)

    # Generic reports with known fields
    report_keywords = {
        "alignment",
        "continuity",
        "issues",
        "summary",
        "recommendations",
        "violations",
        "analysis",
    }
    if report_keywords & data.keys():
        stem = path.stem.replace("_", " ").title()
        return render_generic_report(data, title=stem)

    return None
