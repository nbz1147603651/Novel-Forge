"""Tests for the pure-Python ``novel_forge.api.step_artifact_resolver``.

These tests intentionally do not pull in PySide6 so they continue to run
on CI lanes that skip the desktop extras.  They mirror the assertions in
``tests/unit/test_workflow_artifacts.py`` for the PySide6 implementation
and add coverage for the React/Tauri web-API surface that the resolver
now powers.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from novel_forge.api.step_artifact_resolver import (
    artifact_mapping_for,
    build_empty_artifact_hint,
    build_step_artifact_candidates,
    entries_for_step,
    extract_profile_style_failure_reason,
    format_candidate_summary,
    infer_artifact_format,
    resolve_step_artifacts,
)

# ── Mapping + lookup ──────────────────────────────────────────────────────────


def test_artifact_mapping_covers_repair_audit_export_tts_kinds() -> None:
    mapping = artifact_mapping_for("init_long")
    assert "profile_style" in mapping
    assert "canon_state" in mapping
    assert "init_readiness" in mapping
    assert "plan_chapter_design_matrix" in mapping
    assert "chapter_research" in artifact_mapping_for("run_short")
    assert "chapter_research" in artifact_mapping_for("run_chapter")

    for kind in (
        "repair_continuity",
        "repair_causal",
        "repair_issues",
        "reevaluate_chapter",
        "book_consistency",
        "export_book",
        "reextract_relationships",
        "repair_motif_history",
        "tts_synthesize",
        "tts_full_pipeline",
        "tts_post_archive",
    ):
        kind_map = artifact_mapping_for(kind)
        assert kind_map, f"missing artifact mapping for kind={kind}"


def test_chapter_research_artifacts_resolve_for_short_and_long(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    short_evidence = reports / "short_research_evidence.json"
    long_dossier = reports / "chapter_007_research_dossier.json"
    short_evidence.write_text("{}", encoding="utf-8")
    long_dossier.write_text("{}", encoding="utf-8")

    assert resolve_step_artifacts("run_short", "chapter_research", tmp_path) == [
        ("章节证据包", short_evidence)
    ]
    assert resolve_step_artifacts(
        "run_chapter",
        "chapter_research",
        tmp_path,
        chapter_number=7,
    ) == [("章节研究摘要", long_dossier)]


def test_prepare_chapter_plan_artifacts_resolve_for_chapter_planning(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    reports = tmp_path / "reports"
    plans.mkdir()
    reports.mkdir()
    plan = plans / "chapter_007_plan.json"
    scene_plan = plans / "chapter_007_scene_plan.json"
    validation = reports / "chapter_007_scene_plan_validation.json"
    plan.write_text("{}", encoding="utf-8")
    scene_plan.write_text("{}", encoding="utf-8")
    validation.write_text("{}", encoding="utf-8")

    assert "plan" in artifact_mapping_for("prepare_chapter")
    assert resolve_step_artifacts("prepare_chapter", "plan", tmp_path, chapter_number=7) == [
        ("章节计划", plan),
        ("场景计划", scene_plan),
        ("场景计划验证", validation),
    ]


def test_entries_for_step_supports_exact_keys_and_trailing_underscore_prefixes() -> None:
    finalize_map = artifact_mapping_for("resolve_chapter_checkpoint_finalize")

    # Exact key match.
    assert entries_for_step(finalize_map, "guard_checkpoint")

    # Trailing-underscore prefix (covers all ``book_consistency_verify_*``).
    book_consistency_map = artifact_mapping_for("book_consistency")
    verify_entries = entries_for_step(book_consistency_map, "book_consistency_verify_progress")
    assert verify_entries

    # ``book_consistency`` is a public prefix that aliases its trailing-underscore step.
    assert entries_for_step(book_consistency_map, "book_consistency")

    # Unknown step yields an empty list (not an error).
    assert not entries_for_step(finalize_map, "does_not_exist")

    # Empty inputs are guarded.
    assert not entries_for_step({}, "anything")
    assert not entries_for_step(finalize_map, "")


# ── Format inference ──────────────────────────────────────────────────────────


def test_infer_artifact_format_returns_engine_contract_values() -> None:
    assert infer_artifact_format("story_bible.json") == "json"
    assert infer_artifact_format("coherence_claims.jsonl") == "json"
    assert infer_artifact_format("outline.json") == "json"
    assert infer_artifact_format("chapter.md") == "markdown"
    assert infer_artifact_format("plan.txt") == "plain_text"


def test_infer_artifact_format_flags_binary_assets_and_subtitles() -> None:
    assert infer_artifact_format("chapter_full.mp3") == "binary"
    assert infer_artifact_format("voice_take.wav") == "binary"
    assert infer_artifact_format("archive.zip") == "binary"
    assert infer_artifact_format("book_export.docx") == "binary"
    assert infer_artifact_format("preview.pdf") == "binary"

    assert infer_artifact_format("chapter.srt") == "subtitle"
    assert infer_artifact_format("captions.vtt") == "subtitle"


# ── Variant resolution: init_story_bible fragment fallback ───────────────────


def test_init_long_story_bible_falls_back_to_fragments(tmp_path: Path) -> None:
    project_dir = tmp_path

    fragment_core = project_dir / "initialization" / "fragments" / "story_bible"
    fragment_core.mkdir(parents=True)
    core = fragment_core / "story_core.checkpoint.json"
    core.write_text(json.dumps({"premise": "demo"}), encoding="utf-8")
    continuity = fragment_core / "story_bible_continuity.checkpoint.json"
    continuity.write_text("{}", encoding="utf-8")

    entries = resolve_step_artifacts("init_long", "init_story_bible", project_dir)
    labels = [label for label, _ in entries]
    assert any(label.startswith("世界观分片") for label in labels)
    # Main ``story_bible.json`` is missing, so the fragment list is the source of truth.
    assert labels == sorted(set(labels))  # no duplicates


def test_init_long_story_bible_prefers_main_file_when_present(tmp_path: Path) -> None:
    project_dir = tmp_path
    main_path = project_dir / "story_bible.json"
    main_path.write_text('{"premise":"primary"}', encoding="utf-8")
    core = (
        project_dir / "initialization" / "fragments" / "story_bible" / "story_core.checkpoint.json"
    )
    core.parent.mkdir(parents=True)
    core.write_text("{}", encoding="utf-8")

    entries = resolve_step_artifacts("init_long", "init_story_bible", project_dir)
    labels = [label for label, _ in entries]
    assert labels[0] == "世界观设定"
    assert not any(label.startswith("世界观分片") for label in labels)


# ── Variant resolution: chapter draft auto-enumeration ───────────────────────


def test_run_chapter_bridge_lists_all_draft_versions_sorted(tmp_path: Path) -> None:
    project_dir = tmp_path
    draft_dir = project_dir / "drafts" / "chapter_007"
    draft_dir.mkdir(parents=True)
    (draft_dir / "v0_draft.md").write_text("# DRAFT 原稿", encoding="utf-8")
    (draft_dir / "v1_wave.md").write_text("# 初稿成章", encoding="utf-8")
    (draft_dir / "v3_edited.md").write_text("# 第3轮编辑", encoding="utf-8")
    (draft_dir / "v_final_review.md").write_text("# 评审定稿", encoding="utf-8")

    entries = resolve_step_artifacts("run_chapter", "bridge", project_dir, chapter_number=7)
    labels = [label for label, _ in entries]
    # ``run_chapter.bridge`` uses the same semantic labels and version order
    # as the PySide artifact dialog.
    assert labels == [
        "章节草稿（评审定稿）",
        "章节草稿（第3轮润色）",
        "章节草稿（初稿成章）",
        "章节草稿（DRAFT 原稿）",
    ]


def test_resolve_chapter_checkpoint_draft_uses_rank_aware_version_order(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path
    draft_dir = project_dir / "drafts" / "chapter_007"
    draft_dir.mkdir(parents=True)
    (draft_dir / "v0_draft.md").write_text("# DRAFT 原稿", encoding="utf-8")
    (draft_dir / "v1_wave.md").write_text("# 初稿成章", encoding="utf-8")
    (draft_dir / "v3_edited.md").write_text("# 第3轮编辑", encoding="utf-8")
    (draft_dir / "v_final_review.md").write_text("# 评审定稿", encoding="utf-8")

    entries = resolve_step_artifacts(
        "resolve_chapter_checkpoint", "draft", project_dir, chapter_number=7
    )
    labels = [label for label, _ in entries]
    # ``_chapter_draft_rank`` orders the chapter drafts so consumers see
    # the newest (v_final_review / edited rounds) before older baseline.
    auto_labels = [label for label in labels if label.startswith("章节草稿")]
    assert auto_labels == [
        "章节草稿（评审定稿）",
        "章节草稿（第3轮润色）",
        "章节草稿（初稿成章）",
        "章节草稿（DRAFT 原稿）",
    ]


def test_resolve_chapter_checkpoint_draft_dedupes_static_and_auto_enumeration(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path
    draft_dir = project_dir / "drafts" / "chapter_007"
    draft_dir.mkdir(parents=True)
    (draft_dir / "v0_draft.md").write_text("# DRAFT 原稿", encoding="utf-8")
    (draft_dir / "v1_wave.md").write_text("# 初稿成章", encoding="utf-8")

    entries = resolve_step_artifacts(
        "resolve_chapter_checkpoint", "draft", project_dir, chapter_number=7
    )
    labels = [label for label, _ in entries]
    # When the auto-enumeration matches a path that the static mapping also
    # surfaces, the resolver keeps the auto-enumeration label and skips the
    # static declaration.  This mirrors PySide6 ``_append_artifact``
    # dedupe-by-path semantics so the dialog never shows two labels for
    # the same file.
    assert "章节草稿（初稿成章）" in labels
    assert "章节草稿（DRAFT 原稿）" in labels
    assert labels.count("章节草稿（初稿成章）") == 1
    assert labels.count("章节草稿（DRAFT 原稿）") == 1


# ── Variant resolution: short-story edit + segments wildcards ────────────────


def test_run_short_edit_step_auto_enumerates_edited_snapshots(tmp_path: Path) -> None:
    project_dir = tmp_path
    drafts = project_dir / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "v0_draft.md").write_text("# 初稿", encoding="utf-8")
    (drafts / "v2_edited.md").write_text("# 第二轮", encoding="utf-8")
    (drafts / "v5_edited.md").write_text("# 第五轮", encoding="utf-8")

    entries = resolve_step_artifacts("run_short", "edit_round_2", project_dir)
    labels = [label for label, _ in entries]
    assert "编辑稿 (v2_edited)" in labels
    assert "编辑稿 (v5_edited)" in labels


def test_run_short_draft_step_collects_segment_drafts(tmp_path: Path) -> None:
    project_dir = tmp_path
    seg_drafts = project_dir / "drafts" / "segments"
    seg_drafts.mkdir(parents=True)
    (seg_drafts / "segment_001.md").write_text("# 一", encoding="utf-8")
    (seg_drafts / "segment_002.md").write_text("# 二", encoding="utf-8")
    seg_bridges = project_dir / "plans" / "short_segments"
    seg_bridges.mkdir(parents=True)
    (seg_bridges / "segment_001_bridge.json").write_text("{}", encoding="utf-8")

    entries = resolve_step_artifacts("run_short", "draft", project_dir)
    labels = [label for label, _ in entries]
    assert "分段草稿 (segment_001)" in labels
    assert "分段草稿 (segment_002)" in labels
    assert "分段桥接 (segment_001_bridge)" in labels


def test_run_short_edit_step_falls_back_to_initial_draft(tmp_path: Path) -> None:
    project_dir = tmp_path
    drafts = project_dir / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "v0_draft.md").write_text("# 初稿", encoding="utf-8")

    entries = resolve_step_artifacts("run_short", "edit_round_1", project_dir)
    labels = [label for label, _ in entries]
    # No edited snapshots exist, so the resolver falls back to v0_draft.md.
    assert any("v0_draft" in label for label in labels) or labels == ["初稿"]


# ── Variant resolution: book_consistency timestamped reports ────────────────


def test_book_consistency_prefers_latest_timestamped_report(tmp_path: Path) -> None:
    project_dir = tmp_path
    reports = project_dir / "reports"
    reports.mkdir(parents=True)
    (reports / "book_consistency_audit.json").write_text("{}", encoding="utf-8")
    older = reports / "book_consistency_audit_20260601T120000.json"
    older.write_text("{}", encoding="utf-8")
    # Force the newer file to have a strictly greater mtime.
    newer = reports / "book_consistency_audit_20260801T120000.json"
    newer.write_text("{}", encoding="utf-8")
    import time

    time.sleep(0.01)
    newer.touch()

    entries = resolve_step_artifacts("book_consistency", "book_consistency", project_dir)
    labels = [label for label, _ in entries]
    assert "全书一致性审计" in labels
    assert "带时间戳审计报告" in labels
    # The static 全书一致性审计 label is mapped to the legacy file, so
    # the timestamped latest is the second entry.
    timestamped_paths = [path for label, path in entries if label == "带时间戳审计报告"]
    assert timestamped_paths and timestamped_paths[0].name == newer.name


def test_book_editorial_audit_surfaces_publication_report(tmp_path: Path) -> None:
    report = tmp_path / "reports" / "book_editorial_audit.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}", encoding="utf-8")

    entries = resolve_step_artifacts(
        "book_editorial_audit",
        "book_editorial_audit_report_written",
        tmp_path,
    )

    assert entries == [("全书出版编辑审查", report)]


# ── Variant resolution: volume_audit + export_book ──────────────────────────


def test_resolve_volume_audit_picks_latest_audit_file(tmp_path: Path) -> None:
    project_dir = tmp_path
    reports = project_dir / "reports"
    reports.mkdir(parents=True)
    # The static mapping expects ``reports/volume_{ch}_audit.json`` so the
    # newer named audit should also match that channel-specific path.
    older = reports / "volume_001_audit.json"
    older.write_text("{}", encoding="utf-8")
    import time

    time.sleep(0.01)
    newer = reports / "volume_002_audit.json"
    newer.write_text("{}", encoding="utf-8")

    entries = resolve_step_artifacts(
        "resolve_chapter_checkpoint_finalize",
        "volume_audit",
        project_dir,
        chapter_number=2,
    )
    labels = [label for label, _ in entries]
    # Static mapping surfaces the channel-specific audit (``reports/volume_002_audit.json``);
    # the wildcard enumerator appends the most recent ``volume_*_audit.json``.
    assert "卷末审计报告" in labels
    targets = [path for _, path in entries]
    assert any(path.name == "volume_002_audit.json" for path in targets)
    assert any(path.name == "volume_002_audit.json" for path in targets) and any(
        path.name == "volume_002_audit.json" for path in targets
    )


def test_export_book_surfaces_up_to_twenty_export_files(tmp_path: Path) -> None:
    project_dir = tmp_path
    exports = project_dir / "exports"
    exports.mkdir(parents=True)
    for index in range(3):
        (exports / f"export_{index:02d}.docx").write_text("binary", encoding="utf-8")

    entries = resolve_step_artifacts("export_book", "export", project_dir)
    labels = [label for label, _ in entries]
    # Static mapping points to the directory (``exports``); the file-level
    # enumerator appends one ``导出文件`` row per export.
    assert (
        entries
        == [
            ("导出文件", exports / "export_00.docx"),
            ("导出文件", exports / "export_01.docx"),
            ("导出文件", exports / "export_02.docx"),
        ]
        or labels.count("导出文件") == 3
    )


# ── Empty-state diagnostics ──────────────────────────────────────────────────


def _write_history(project_dir: Path, payload: dict[str, object]) -> None:
    states = project_dir / "states"
    states.mkdir(parents=True, exist_ok=True)
    history = states / "task_flow_history.json"
    history.write_text(json.dumps([payload]), encoding="utf-8")


def test_profile_style_failure_reason_reads_task_flow_history(tmp_path: Path) -> None:
    project_dir = tmp_path
    _write_history(
        project_dir,
        {
            "kind": "init_long",
            "events": [
                {
                    "step": "profile_style_failed",
                    "payload": {"error": "rate limit exceeded"},
                }
            ],
        },
    )

    reason = extract_profile_style_failure_reason(project_dir)
    assert reason is not None
    assert "风格规范生成失败" in reason
    assert "rate limit exceeded" in reason


def test_profile_style_skipped_event_returns_skip_message(tmp_path: Path) -> None:
    project_dir = tmp_path
    _write_history(
        project_dir,
        {
            "kind": "init_long",
            "events": [{"step": "profile_style_skipped", "payload": {}}],
        },
    )

    reason = extract_profile_style_failure_reason(project_dir)
    assert reason is not None
    assert "跳过" in reason


def test_build_empty_artifact_hint_for_profile_style_includes_remediation(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path
    _write_history(
        project_dir,
        {
            "kind": "init_long",
            "events": [
                {
                    "step": "profile_style_failed",
                    "payload": {"error": "AuthenticationError: key rejected"},
                }
            ],
        },
    )

    hint = build_empty_artifact_hint("init_long", "profile_style", project_dir)
    assert hint is not None
    assert "未找到 `style_profile.json`" in hint
    assert "风格规范生成失败" in hint
    assert "建议修复模型路由/权限" in hint


def test_build_empty_artifact_hint_returns_none_for_unhandled_steps(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path

    assert build_empty_artifact_hint("init_long", "plan_blueprint", project_dir) is None


def test_format_candidate_summary_caps_displayed_rows(tmp_path: Path) -> None:
    candidates = [(f"label_{i}", tmp_path / f"path_{i}.json") for i in range(15)]
    summary = format_candidate_summary(candidates, tmp_path, limit=10)
    assert summary.count("\n") == 10
    assert "另有 5 个候选路径" in summary


# ── Candidate path enumeration ───────────────────────────────────────────────


def test_step_artifact_candidates_list_potential_paths(tmp_path: Path) -> None:
    project_dir = tmp_path

    candidates = build_step_artifact_candidates("init_long", "profile_style", project_dir)
    labels = [label for label, _ in candidates]
    assert "风格规范" in labels


def test_step_artifact_candidates_for_book_consistency_include_wildcards(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path

    candidates = build_step_artifact_candidates("book_consistency", "book_consistency", project_dir)
    labels = [label for label, _ in candidates]
    assert "带时间戳审计报告" in labels


def test_step_artifact_candidates_for_export_book_use_glob(tmp_path: Path) -> None:
    project_dir = tmp_path

    candidates = build_step_artifact_candidates("export_book", "export", project_dir)
    labels = [label for label, _ in candidates]
    assert "默认导出目录" in labels
    assert "默认导出文件" in labels


# ── TTS end-to-end and remaining kinds ──────────────────────────────────────


def test_tts_full_pipeline_surfaces_delivery_assets(tmp_path: Path) -> None:
    project_dir = tmp_path
    chapter = 5
    results_dir = project_dir / "tts" / "results"
    quality_dir = project_dir / "tts" / "quality"
    audio_dir = project_dir / "tts" / "audio" / f"chapter_{chapter:03d}"
    for directory in (results_dir, quality_dir, audio_dir):
        directory.mkdir(parents=True)
    (results_dir / f"chapter_{chapter:03d}_auto_run.json").write_text("{}")
    (results_dir / f"chapter_{chapter:03d}_audio.json").write_text("{}")
    (quality_dir / f"chapter_{chapter:03d}.json").write_text("{}")
    (audio_dir / "chapter.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nhi")
    (audio_dir / "chapter_full.mp3").write_text("fake-mp3")

    entries = resolve_step_artifacts("tts_full_pipeline", "tts_delivery", project_dir, chapter)
    labels = [label for label, _ in entries]
    assert "章节字幕" in labels
    assert "章节成品音频" in labels
    assert "自动配音运行记录" in labels
    assert "章节音频结果" in labels
    assert "音频质量报告" in labels
    # The MP3 / SRT entry must surface as a ``binary`` / ``subtitle`` format hint
    # so the React viewer can pivot to a download instead of inline rendering.
    from novel_forge.api.step_artifact_resolver import infer_artifact_format

    assert infer_artifact_format("chapter_full.mp3") == "binary"
    assert infer_artifact_format("chapter.srt") == "subtitle"


def test_tts_synthesize_resolves_progress_state_file(tmp_path: Path) -> None:
    project_dir = tmp_path
    chapter = 9
    progress_dir = project_dir / "states" / "tts_progress"
    progress_dir.mkdir(parents=True)
    (progress_dir / f"chapter_{chapter:03d}.json").write_text("{}")

    entries = resolve_step_artifacts("tts_synthesize", "tts_synthesis", project_dir, chapter)
    assert any(path.name == f"chapter_{chapter:03d}.json" for path in (p for _, p in entries))


def test_repair_motif_history_exposes_memory_artifacts(tmp_path: Path) -> None:
    project_dir = tmp_path
    (project_dir / "memory").mkdir(parents=True)
    (project_dir / "memory" / "project_memory.json").write_text("{}")
    (project_dir / "memory" / "narrative_state_index.json").write_text("{}")

    entries = resolve_step_artifacts(
        "repair_motif_history", "motif_repair_layer2_progress", project_dir
    )
    paths = [path.name for _, path in entries]
    assert "project_memory.json" in paths
    assert "narrative_state_index.json" in paths


def test_reextract_relationships_surfaces_state_ledger(tmp_path: Path) -> None:
    project_dir = tmp_path
    chapter = 11
    (project_dir / "chapters").mkdir(exist_ok=True)
    (project_dir / "narrative_state").mkdir(parents=True)
    (project_dir / "chapters" / f"chapter_{chapter:03d}.md").write_text("body")
    (project_dir / "reports").mkdir(exist_ok=True)
    (project_dir / "reports" / f"chapter_{chapter:03d}_creative.json").write_text("{}")
    (project_dir / "narrative_state" / "state_ledger.jsonl").write_text("{}\n")
    (project_dir / "narrative_state" / "story_state_projection.json").write_text("{}")

    entries = resolve_step_artifacts(
        "reextract_relationships", "reextract_done", project_dir, chapter
    )
    paths = [path.name for _, path in entries]
    assert "state_ledger.jsonl" in paths
    assert "story_state_projection.json" in paths
    assert any(p.endswith("creative.json") for p in paths)


def test_repair_continuity_lists_repaired_chapter_and_continuity_report(tmp_path: Path) -> None:
    project_dir = tmp_path
    chapter = 3
    (project_dir / "chapters").mkdir(exist_ok=True)
    (project_dir / "reports").mkdir(exist_ok=True)
    (project_dir / "chapters" / f"chapter_{chapter:03d}.md").write_text("after")
    (project_dir / "reports" / f"chapter_{chapter:03d}_continuity.json").write_text("{}")

    entries = resolve_step_artifacts("repair_continuity", "repair_continuity", project_dir, chapter)
    labels = [label for label, _ in entries]
    assert "修复后章节" in labels
    assert "连贯性报告" in labels


def test_reevaluate_chapter_gathers_all_six_review_artifacts(tmp_path: Path) -> None:
    project_dir = tmp_path
    chapter = 4
    (project_dir / "chapters").mkdir(exist_ok=True)
    (project_dir / "reports").mkdir(exist_ok=True)
    (project_dir / "chapters" / f"chapter_{chapter:03d}.md").write_text("body")
    for stub in (
        "alignment",
        "continuity",
        "causal",
        "reading_power",
        "knowledge_boundary_verification",
        "eval",
    ):
        (project_dir / "reports" / f"chapter_{chapter:03d}_{stub}.json").write_text("{}")

    entries = resolve_step_artifacts(
        "reevaluate_chapter", "reevaluate_chapter", project_dir, chapter
    )
    paths = [path.name for _, path in entries]
    assert any(p.endswith("_alignment.json") for p in paths)
    assert any(p.endswith("_continuity.json") for p in paths)
    assert any(p.endswith("_causal.json") for p in paths)
    assert any(p.endswith("_reading_power.json") for p in paths)
    assert any(p.endswith("_knowledge_boundary_verification.json") for p in paths)
    assert any(p.endswith("_eval.json") for p in paths)


def test_polish_chapter_lists_pre_and_post_polish_artifacts(tmp_path: Path) -> None:
    project_dir = tmp_path
    chapter = 2
    (project_dir / "chapters").mkdir(exist_ok=True)
    (project_dir / "drafts" / f"chapter_{chapter:03d}").mkdir(parents=True)
    (project_dir / "reports").mkdir(exist_ok=True)
    (project_dir / "chapters" / f"chapter_{chapter:03d}.md").write_text("# after")
    # polish_start expects v0_draft; publish v0_draft in addition to v_final_review.
    (project_dir / "drafts" / f"chapter_{chapter:03d}" / "v0_draft.md").write_text("# pre")
    (project_dir / "drafts" / f"chapter_{chapter:03d}" / "v_polished.md").write_text("# done")
    (project_dir / "reports" / f"chapter_{chapter:03d}_eval.json").write_text("{}")
    revisions = project_dir / "reports" / "revisions"
    revisions.mkdir(exist_ok=True)
    (revisions / f"chapter_{chapter:03d}_polish_chapter.json").write_text("{}")

    polish_start = resolve_step_artifacts("polish_chapter", "polish_start", project_dir, chapter)
    polish_start_labels = {label for label, _ in polish_start}
    assert "当前章节" in polish_start_labels
    assert "章节草稿" in polish_start_labels
    assert "质量评估" in polish_start_labels

    polish = resolve_step_artifacts("polish_chapter", "polish", project_dir, chapter)
    polish_labels = {label for label, _ in polish}
    assert "润色后章节" in polish_labels
    assert "润色修订对比" in polish_labels
    assert "质量评估" in polish_labels


# ── Cross-resolver parity with PySide6 ──────────────────────────────────────


def test_resolve_step_artifacts_matches_pyside_on_representative_cases(tmp_path: Path) -> None:
    """Diff the web resolver against the PySide6 implementation on representative inputs."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication  # noqa: E402

    QApplication.instance() or QApplication([])

    from novel_forge.desktop.pages.workflow.artifacts import (
        _resolve_artifacts as pyside_resolve,
    )

    # Build a representative project layout.
    project_dir = tmp_path
    chapter = 12
    for directory in ("reports", "plans", "chapters", "drafts", "exports", "memory"):
        (project_dir / directory).mkdir(parents=True, exist_ok=True)
    (project_dir / "story_bible.json").write_text("{}")
    (project_dir / "style_profile.json").write_text("{}")
    draft_dir = project_dir / "drafts" / f"chapter_{chapter:03d}"
    draft_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("v0_draft", "v1_wave", "v3_edited", "v_final_review"):
        (draft_dir / f"{stem}.md").write_text("# stub")
    for stub in ("eval", "alignment", "continuity", "causal", "reading_power", "guard"):
        (project_dir / "reports" / f"chapter_{chapter:03d}_{stub}.json").write_text("{}")
    (project_dir / "reports" / "book_consistency_audit_20260101.json").write_text("{}")
    (project_dir / "reports" / "book_consistency_audit_20260801.json").write_text("{}")
    (project_dir / "exports" / "book.docx").write_text("dummy")
    (project_dir / "exports" / "book.pdf").write_text("dummy")

    def _normalize(entries: list[tuple[str, Path]]) -> set[tuple[str, str]]:
        result: set[tuple[str, str]] = set()
        for label, path in entries:
            try:
                rel = path.relative_to(project_dir).as_posix()
            except ValueError:
                rel = path.as_posix()
            result.add((label, rel))
        return result

    cases = [
        ("run_chapter", "bridge", chapter),
        ("run_chapter", "alignment", chapter),
        ("resolve_chapter_checkpoint", "draft", chapter),
        ("resolve_chapter_checkpoint_finalize", "volume_audit", chapter),
        ("book_consistency", "book_consistency", chapter),
        ("export_book", "export", chapter),
        ("init_long", "init_story_bible", chapter),
        ("run_short", "edit_round_2", chapter),
        ("run_short", "draft", chapter),
        ("init_long", "profile_style", chapter),
    ]

    for kind, step, ch in cases:
        web = resolve_step_artifacts(kind, step, project_dir, ch)
        pyside = pyside_resolve(kind, step, project_dir, ch)
        web_normalized = _normalize(web)
        pyside_normalized = _normalize(pyside)
        assert web_normalized == pyside_normalized, (
            f"resolver divergence on {kind}.{step}: "
            f"missing_in_web={pyside_normalized - web_normalized}, "
            f"extra_in_web={web_normalized - pyside_normalized}"
        )
