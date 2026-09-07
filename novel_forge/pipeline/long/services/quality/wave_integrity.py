"""Classify WAVE post-condition warnings for Review-stage routing."""

from __future__ import annotations

import dataclasses
import hashlib
import re
from typing import Any

from novel_forge.core.guidance import required_cross_scene_ref
from novel_forge.core.review.review_contracts import normalize_issue_to_finding
from novel_forge.core.schemas.review import ReviewFinding
from novel_forge.core.utils.field_extractor import field
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.pipeline.long.services.plan_obligations import assess_plan_literal_coverage
from novel_forge.pipeline.steps.wave_step import _anchor_words, _semantic_anchor_present


@dataclasses.dataclass(frozen=True)
class WaveIntegrityIssue:
    issue_id: str
    issue_type: str
    severity: str
    summary: str
    evidence: str
    fix_suggestion: str
    blocking: bool
    location: str = "全章"
    fix_mode: str = "window"
    postconditions: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class WaveIntegrityResult:
    policy: str
    blocking: bool
    issues: list[WaveIntegrityIssue]
    warnings: list[str]
    metrics: dict[str, Any]

    @property
    def archive_blocking(self) -> bool:
        """Whether this local WAVE diagnostic should directly block archive."""

        return bool(self.blocking and self.policy == "block")

    def model_dump(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "blocking": self.blocking,
            "diagnostic_blocking": self.blocking,
            "archive_blocking": self.archive_blocking,
            "warnings": list(self.warnings),
            "issues": [dataclasses.asdict(issue) for issue in self.issues],
            "metrics": dict(self.metrics),
        }


_CROSS_REF_RE = re.compile(r"wave_post_cond_cross_ref:\s*hit\s+(\d+)/(\d+)")
_ANCHOR_RE = re.compile(r"wave_post_cond_anchor:\s*scene\s+([^\s]+)")
_OUTCOME_RE = re.compile(r"wave_post_cond_outcome:\s*scene\s+([^\s]+)")


def _stable_wave_issue_id(issue_type: str, *parts: Any) -> str:
    raw = "|".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{issue_type}_{digest}"


def _expected_cross_refs(meta: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for idx, ref in enumerate(list(meta.get("cross_ref_expected", []) or []), start=1):
        if not required_cross_scene_ref(ref):
            continue
        desc = str(ref.get("description") or "").strip()
        if not desc:
            continue
        refs.append(
            {
                "index": int(ref.get("index") or idx),
                "from_scene": str(ref.get("from_scene") or "").strip(),
                "to_scene": str(ref.get("to_scene") or "").strip(),
                "ref_type": str(ref.get("ref_type") or "callback").strip(),
                "requirement": ref.get("requirement", {}),
                "description": desc,
            }
        )
    return refs


def _expected_scene_anchors(meta: dict[str, Any]) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    for idx, scene in enumerate(list(meta.get("scene_anchor_expected", []) or []), start=1):
        if not isinstance(scene, dict):
            continue
        summary = str(scene.get("summary") or "").strip()
        scene_id = str(scene.get("scene_id") or f"scene_{idx:02d}").strip()
        if not summary and not scene_id:
            continue
        anchors.append(
            {
                "index": int(scene.get("index") or idx),
                "scene_id": scene_id,
                "summary": summary,
                "anchor_words": list(scene.get("anchor_words", []) or []),
            }
        )
    return anchors


def _anchor_warning_scene_ids(warnings: list[str]) -> list[str]:
    scene_ids: list[str] = []
    for warning in warnings:
        match = _ANCHOR_RE.search(warning)
        if match:
            scene_ids.append(match.group(1).strip())
    return scene_ids


def normalize_wave_word_count_policy(value: Any) -> str:
    """Normalize WAVE word-count handling into inherit/enforce/warn."""

    normalized = str(value or "inherit").strip().lower()
    aliases = {
        "archive_gate": "inherit",
        "inherit_archive_gate": "inherit",
        "block": "enforce",
        "repair": "enforce",
        "enabled": "enforce",
        "enable": "enforce",
        "on": "enforce",
        "true": "enforce",
        "disabled": "warn",
        "disable": "warn",
        "off": "warn",
        "false": "warn",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"inherit", "enforce", "warn"}:
        return "inherit"
    return normalized


def wave_word_count_blocking_enabled(
    *,
    word_count_policy: Any = "inherit",
    archive_gate_enabled: bool = True,
) -> bool:
    """Return whether WAVE word-count warnings should become blocking issues."""

    normalized = normalize_wave_word_count_policy(word_count_policy)
    if normalized == "warn":
        return False
    if normalized == "enforce":
        return True
    return bool(archive_gate_enabled)


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
        return dict(payload) if isinstance(payload, dict) else {}
    return {}


def assess_wave_integrity(
    wave_meta: dict[str, Any] | None,
    *,
    policy: str = "repair",
    word_count_policy: Any = "inherit",
    archive_gate_enabled: bool = True,
) -> WaveIntegrityResult:
    """Classify WAVE warnings into diagnostics vs blocking repair targets."""

    meta = wave_meta or {}
    normalized_policy = str(policy or "repair").strip().lower()
    if normalized_policy not in {"warn", "repair", "block"}:
        normalized_policy = "repair"
    normalized_word_count_policy = normalize_wave_word_count_policy(word_count_policy)
    word_count_blocks = wave_word_count_blocking_enabled(
        word_count_policy=normalized_word_count_policy,
        archive_gate_enabled=archive_gate_enabled,
    )

    warnings = [str(item) for item in list(meta.get("warnings", []) or []) if str(item)]
    issues: list[WaveIntegrityIssue] = []
    expected_refs = _expected_cross_refs(meta)
    expected_anchors = _expected_scene_anchors(meta)

    # Detect WAVE skip: if WAVE was skipped (empty output or regression),
    # downstream anchor/cross-ref issues are expected, not blocking.
    wave_skipped = bool(meta.get("wave_skipped")) or any(
        str(w).startswith("wave skipped:") for w in warnings
    )

    obligation_coverage = _as_mapping(meta.get("plan_obligation_coverage"))
    missing_required_literals = [
        item
        for item in list(obligation_coverage.get("missing_required_literals", []) or [])
        if isinstance(item, dict) and str(item.get("literal") or "").strip()
    ]
    for item in missing_required_literals:
        literal = str(item.get("literal") or "").strip()
        scene_id = str(item.get("scene_id") or "").strip()
        source_text = str(item.get("source_text") or "").strip()
        issues.append(
            WaveIntegrityIssue(
                issue_id=_stable_wave_issue_id(
                    "wave_required_literal_missing",
                    scene_id,
                    literal,
                ),
                issue_type="wave_required_literal_missing",
                severity="high",
                summary=f"计划要求的字面锚点缺失：「{literal}」",
                evidence=(f"{scene_id}: {source_text}" if scene_id else source_text),
                fix_suggestion=(
                    "在计划指定的场景中补回该原文，不得改写、同义替换或移动到无关场景。"
                ),
                blocking=True,
                location=scene_id or "全章",
                postconditions=[
                    {
                        "validator_id": "wave_required_literal_present",
                        "description": f"修复后正文必须逐字包含：{literal}",
                        "evidence_hint": literal,
                        "required": True,
                    }
                ],
                metadata={
                    "scene_id": scene_id,
                    "literal": literal,
                    "source_field": item.get("source_field"),
                    "objective_check": True,
                },
            )
        )

    cross_ref_hit_count = len(list(meta.get("cross_ref_hits", []) or []))
    cross_ref_total = int(meta.get("cross_ref_total") or 0)
    cross_ref_hits = [
        str(item or "").strip() for item in list(meta.get("cross_ref_hits", []) or [])
    ]
    for warning in warnings:
        match = _CROSS_REF_RE.search(warning)
        if not match:
            continue
        cross_ref_hit_count = int(match.group(1))
        cross_ref_total = int(match.group(2))
        break
    cross_ref_hit_ratio = cross_ref_hit_count / cross_ref_total if cross_ref_total > 0 else None
    if expected_refs and cross_ref_hit_count == 0 and not wave_skipped:
        missing_refs = [
            ref
            for ref in expected_refs
            if str(ref.get("description") or "").strip() not in cross_ref_hits
        ]
        if missing_refs:
            for ref in missing_refs:
                desc = str(ref.get("description") or "").strip()
                from_scene = str(ref.get("from_scene") or "").strip()
                to_scene = str(ref.get("to_scene") or "").strip()
                location = " -> ".join(item for item in (from_scene, to_scene) if item) or "全章"
                evidence = f"{location}: {desc}" if location != "全章" else desc
                issues.append(
                    WaveIntegrityIssue(
                        issue_id=_stable_wave_issue_id(
                            "wave_cross_ref_missing",
                            ref.get("index"),
                            from_scene,
                            to_scene,
                            desc,
                        ),
                        issue_type="wave_cross_ref_missing",
                        severity="high",
                        summary=f"WAVE 未命中跨场景引用：{desc}",
                        evidence=evidence,
                        fix_suggestion=(
                            "在对应场景中兑现必要的行动或因果承接，不要求复述意象或指导原文。"
                        ),
                        blocking=True,
                        location=location,
                        postconditions=[
                            {
                                "validator_id": "wave_cross_ref_present",
                                "description": f"正文应语义兑现跨场景引用：{desc}",
                                "evidence_hint": desc,
                                "required": True,
                            }
                        ],
                        metadata={
                            "cross_ref_hit_count": cross_ref_hit_count,
                            "cross_ref_total": cross_ref_total,
                            "cross_ref_index": ref.get("index"),
                            "from_scene": from_scene,
                            "to_scene": to_scene,
                            "ref_type": ref.get("ref_type"),
                            "description": desc,
                        },
                    )
                )
        else:
            issues.append(
                WaveIntegrityIssue(
                    issue_id=_stable_wave_issue_id(
                        "wave_cross_ref_missing",
                        cross_ref_hit_count,
                        cross_ref_total,
                    ),
                    issue_type="wave_cross_ref_missing",
                    severity="high",
                    summary="WAVE 未命中任何跨场景引用",
                    evidence=f"cross_ref_hits={cross_ref_hit_count}/{cross_ref_total}",
                    fix_suggestion="在不重写主线结果的前提下补回跨场景引用、回声或承接句。",
                    blocking=True,
                    postconditions=[
                        {
                            "validator_id": "wave_cross_ref_hit_ratio",
                            "description": "修复后 WAVE 跨场景引用命中率应达到 80% 以上。",
                            "evidence_hint": f"cross_ref_hits={cross_ref_hit_count}/{cross_ref_total}",
                            "required": True,
                        }
                    ],
                    metadata={
                        "cross_ref_hit_count": cross_ref_hit_count,
                        "cross_ref_total": cross_ref_total,
                    },
                )
            )

    anchor_missing_count = sum(
        1 for warning in warnings if warning.startswith("wave_post_cond_anchor:")
    )
    # Degraded-mode anchors (WAVE skipped) are tracked separately and do
    # not count toward the blocking threshold.
    degraded_anchor_count = sum(
        1 for warning in warnings if warning.startswith("wave_degraded_anchor:")
    )
    anchor_total = int(meta.get("scene_anchor_total") or meta.get("scenes_woven") or 0)
    anchor_missing_ratio = anchor_missing_count / anchor_total if anchor_total > 0 else 0.0
    if anchor_total > 0 and anchor_missing_ratio >= 0.8 and not wave_skipped:
        missing_scene_ids = _anchor_warning_scene_ids(warnings)
        expected_by_id = {
            str(anchor.get("scene_id") or "").strip(): anchor for anchor in expected_anchors
        }
        missing_anchors: list[dict[str, Any]] = []
        for scene_id in missing_scene_ids:
            if scene_id in expected_by_id:
                missing_anchors.append(expected_by_id[scene_id])
            else:
                missing_anchors.append({"scene_id": scene_id, "summary": "", "anchor_words": []})

        if missing_anchors:
            for anchor in missing_anchors:
                scene_id = str(anchor.get("scene_id") or "").strip()
                summary = str(anchor.get("summary") or "").strip()
                location = scene_id or "全章"
                evidence = (
                    f"{scene_id}: {summary}" if summary else f"{scene_id} anchor words missing"
                )
                issues.append(
                    WaveIntegrityIssue(
                        issue_id=_stable_wave_issue_id(
                            "wave_scene_anchors_missing",
                            scene_id,
                            summary,
                        ),
                        issue_type="wave_scene_anchors_missing",
                        severity="high",
                        summary=(
                            f"WAVE 丢失场景锚点：{scene_id}" if scene_id else "WAVE 丢失场景锚点"
                        ),
                        evidence=evidence,
                        fix_suggestion="按该 scene 的 summary 补回关键锚点，保留已生成正文中的有效细节。",
                        blocking=True,
                        location=location,
                        postconditions=[
                            {
                                "validator_id": "wave_scene_anchor_present",
                                "description": (
                                    f"正文应保留 {scene_id} 的场景锚点：{summary}"
                                    if summary
                                    else f"正文应保留 {scene_id} 的场景锚点"
                                ),
                                "evidence_hint": summary or scene_id,
                                "required": True,
                            }
                        ],
                        metadata={
                            "anchor_missing_count": anchor_missing_count,
                            "anchor_total": anchor_total,
                            "anchor_missing_ratio": round(anchor_missing_ratio, 4),
                            "scene_id": scene_id,
                            "scene_summary": summary,
                            "anchor_words": list(anchor.get("anchor_words", []) or []),
                        },
                    )
                )
        else:
            issues.append(
                WaveIntegrityIssue(
                    issue_id=_stable_wave_issue_id(
                        "wave_scene_anchors_missing",
                        anchor_missing_count,
                        anchor_total,
                    ),
                    issue_type="wave_scene_anchors_missing",
                    severity="high",
                    summary="WAVE 大比例丢失场景锚点",
                    evidence=f"anchor_missing={anchor_missing_count}/{anchor_total}",
                    fix_suggestion="按场景计划补回关键场景锚点，保留已生成正文中的有效细节。",
                    blocking=True,
                    postconditions=[
                        {
                            "validator_id": "wave_scene_anchor_coverage",
                            "description": "修复后丢失的场景锚点比例应低于 80%。",
                            "evidence_hint": f"anchor_missing={anchor_missing_count}/{anchor_total}",
                            "required": True,
                        }
                    ],
                    metadata={
                        "anchor_missing_count": anchor_missing_count,
                        "anchor_total": anchor_total,
                        "anchor_missing_ratio": round(anchor_missing_ratio, 4),
                    },
                )
            )

    word_count_warnings = [
        warning for warning in warnings if warning.startswith("wave_post_cond_word_count:")
    ]
    if word_count_warnings:
        issues.append(
            WaveIntegrityIssue(
                issue_id=_stable_wave_issue_id(
                    "wave_word_count_out_of_range",
                    word_count_warnings[0],
                ),
                issue_type="wave_word_count_out_of_range",
                severity="high",
                summary="WAVE 后字数超出目标区间 ±30%",
                evidence=word_count_warnings[0],
                fix_suggestion="压缩或扩写正文到目标区间，同时保留计划场景和关键因果。",
                blocking=word_count_blocks,
                postconditions=[
                    {
                        "validator_id": "wave_word_count_range",
                        "description": "修复后正文字数应回到 WAVE 目标区间 +/-30%。",
                        "evidence_hint": word_count_warnings[0],
                        "required": bool(word_count_blocks),
                    }
                ],
                metadata={
                    "warning": word_count_warnings[0],
                    "word_count_policy": normalized_word_count_policy,
                    "archive_gate_enabled": bool(archive_gate_enabled),
                    "word_count_blocking": word_count_blocks,
                },
            )
        )

    # ── required_outcome coverage warnings ────────────────────────────────
    outcome_warnings = [
        warning for warning in warnings if warning.startswith("wave_post_cond_outcome:")
    ]
    outcome_missing_count = len(outcome_warnings)
    if outcome_warnings:
        outcome_scene_ids = [m.group(1) for w in outcome_warnings if (m := _OUTCOME_RE.search(w))]
        issues.append(
            WaveIntegrityIssue(
                issue_id=_stable_wave_issue_id(
                    "wave_outcome_coverage_gap",
                    outcome_missing_count,
                    *outcome_scene_ids[:3],
                ),
                issue_type="wave_outcome_coverage_gap",
                severity="medium",
                summary=f"WAVE 后 {outcome_missing_count} 个场景的 required_outcome 未语义覆盖",
                evidence="；".join(outcome_warnings[:5]),
                fix_suggestion="对齐修复阶段应根据 missing_main_points 补回缺失的必须结果。",
                blocking=False,
                postconditions=[],
                metadata={
                    "outcome_missing_count": outcome_missing_count,
                    "scene_ids": outcome_scene_ids,
                },
            )
        )

    blocking = any(issue.blocking for issue in issues)
    metrics = {
        "policy": normalized_policy,
        "blocking": blocking,
        "diagnostic_blocking": blocking,
        "archive_blocking": bool(blocking and normalized_policy == "block"),
        "warning_count": len(warnings),
        "blocking_issue_count": sum(1 for issue in issues if issue.blocking),
        "cross_ref_hit_count": cross_ref_hit_count,
        "cross_ref_total": cross_ref_total,
        "cross_ref_hit_ratio": cross_ref_hit_ratio,
        "anchor_missing_count": anchor_missing_count,
        "anchor_total": anchor_total,
        "anchor_missing_ratio": round(anchor_missing_ratio, 4),
        "missing_cross_ref_count": max(0, cross_ref_total - cross_ref_hit_count),
        "missing_anchor_issue_count": anchor_missing_count,
        "wave_skipped": wave_skipped,
        "degraded_anchor_count": degraded_anchor_count,
        "word_count_warning_count": len(word_count_warnings),
        "word_count_policy": normalized_word_count_policy,
        "archive_gate_enabled": bool(archive_gate_enabled),
        "word_count_blocking": bool(word_count_warnings and word_count_blocks),
        "outcome_missing_count": outcome_missing_count,
        "required_literal_count": int(obligation_coverage.get("required_literal_count") or 0),
        "missing_required_literal_count": len(missing_required_literals),
    }
    return WaveIntegrityResult(
        policy=normalized_policy,
        blocking=blocking,
        issues=issues,
        warnings=warnings,
        metrics=metrics,
    )


def wave_integrity_to_findings(
    result: WaveIntegrityResult | None,
    *,
    chapter_number: int,
    current_text_hash: str,
) -> list[ReviewFinding]:
    """Project blocking WAVE integrity issues into normalized repair findings."""

    if result is None:
        return []
    findings: list[ReviewFinding] = []
    for issue in result.issues:
        if not issue.blocking:
            continue
        findings.append(
            normalize_issue_to_finding(
                {
                    "issue_id": issue.issue_id,
                    "issue_type": issue.issue_type,
                    "severity": issue.severity,
                    "summary": issue.summary,
                    "evidence": issue.evidence,
                    "fix_suggestion": issue.fix_suggestion,
                    "location": issue.location,
                    "fix_mode": issue.fix_mode,
                    "blocking": issue.blocking,
                    "postconditions": list(issue.postconditions),
                },
                source_module="wave_integrity",
                chapter_number=chapter_number,
                dimension="chapter_quality",
                current_text_hash=current_text_hash,
                metadata={
                    "wave_integrity": True,
                    "wave_policy": result.policy,
                    **dict(issue.metadata),
                },
            )
        )
    return findings


def assess_wave_integrity_against_plan(
    *,
    current_text: str,
    plan: Any,
    target_word_count: int,
    policy: str = "repair",
    word_count_policy: Any = "inherit",
    archive_gate_enabled: bool = True,
    wave_skipped: bool = False,
) -> WaveIntegrityResult:
    """Re-run WAVE post-condition checks against the current post-repair text.

    When *wave_skipped* is True (WAVE was skipped due to empty output or
    regression), scene-anchor gaps are expected and recorded as
    ``wave_degraded_anchor:`` warnings instead of the blocking
    ``wave_post_cond_anchor:`` prefix.  This prevents a known-degraded
    WAVE pass from triggering a ConsistencyViolationError that would
    otherwise lead to an ineffective plan replan.
    """

    raw_csi = field(plan, "cross_scene_intent", {}) or {}
    cross_scene_intent = _as_mapping(raw_csi)
    raw_scenes = list(field(plan, "scene_intents", []) or [])
    scene_intents = [_as_mapping(item) for item in raw_scenes]
    scene_intents = [item for item in scene_intents if item]

    warnings: list[str] = []
    cross_refs = [
        ref
        for ref in list(cross_scene_intent.get("cross_scene_references", []) or [])
        if required_cross_scene_ref(ref)
    ]
    expected_cross_refs = [
        {
            "index": idx,
            "from_scene": str(ref.get("from_scene") or "").strip(),
            "to_scene": str(ref.get("to_scene") or "").strip(),
            "ref_type": str(ref.get("ref_type") or "callback").strip(),
            "requirement": ref.get("requirement", {}),
            "description": str(ref.get("description") or "").strip(),
        }
        for idx, ref in enumerate(cross_refs, start=1)
    ]
    hits: list[str] = []
    for ref in cross_refs:
        desc = str(ref.get("description") or "").strip()
        if _semantic_anchor_present(desc, current_text):
            hits.append(desc)
            continue
    if cross_refs:
        hit_ratio = len(hits) / max(1, len(cross_refs))
        if hit_ratio < 0.8:
            warnings.append(
                f"wave_post_cond_cross_ref: hit {len(hits)}/{len(cross_refs)} "
                f"({hit_ratio:.0%}) < 80%"
            )

    scene_anchor_total = 0
    expected_scene_anchors: list[dict[str, Any]] = []
    anchor_prefix = "wave_degraded_anchor:" if wave_skipped else "wave_post_cond_anchor:"
    for scene in scene_intents:
        anchor = str(scene.get("summary") or "").strip()
        if anchor and not _anchor_words(anchor, min_words=2):
            continue
        anchor_words = _anchor_words(anchor)
        if not anchor_words:
            continue
        scene_anchor_total += 1
        expected_scene_anchors.append(
            {
                "index": scene_anchor_total,
                "scene_id": str(scene.get("scene_id") or f"scene_{scene_anchor_total:02d}"),
                "summary": anchor,
                "anchor_words": anchor_words,
            }
        )
        if not _semantic_anchor_present(anchor, current_text):
            warnings.append(
                f"{anchor_prefix} scene {scene.get('scene_id', '?')} anchor words missing"
            )

    if target_word_count > 0:
        low = int(target_word_count * 0.7)
        high = int(target_word_count * 1.3)
        wc = count_chapter_words(current_text)
        if wc < low or wc > high:
            warnings.append(
                f"wave_post_cond_word_count: {wc} words not in [{low}, {high}] "
                f"(target {target_word_count} +/-30%)"
            )

    plan_obligation_coverage = assess_plan_literal_coverage(plan, current_text)

    return assess_wave_integrity(
        {
            "warnings": warnings,
            "cross_ref_hits": hits,
            "cross_ref_total": len(cross_refs),
            "cross_ref_expected": expected_cross_refs,
            "scene_anchor_total": scene_anchor_total,
            "scene_anchor_expected": expected_scene_anchors,
            "scenes_woven": len(scene_intents),
            "final_word_count": count_chapter_words(current_text),
            "wave_skipped": bool(wave_skipped),
            "plan_obligation_coverage": plan_obligation_coverage,
        },
        policy=policy,
        word_count_policy=word_count_policy,
        archive_gate_enabled=archive_gate_enabled,
    )
