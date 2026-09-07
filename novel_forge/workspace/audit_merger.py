"""Audit result merger for normalized chapter review findings.

The quality-gate report is the authoritative cross-module audit contract.  It
stores all review findings and repair tickets in a single shape so desktop
panels do not need to know which checker produced which domain report.  Older
continuity/causal report merging remains only as a fallback for runs that have
not produced the unified quality-gate payload yet.
"""

from __future__ import annotations

from typing import Any

from novel_forge.obs.logger import get_logger

_log = get_logger("workspace.audit_merger")


class AuditResultMerger:
    """Merge normalized findings into a UI-ready audit result."""

    @classmethod
    def merge_reports(
        cls,
        layout: Any,  # ProjectLayout
        chapter_number: int,
        storage: Any,  # StorageService
        *,
        continuity_report_new: Any | None = None,
        causal_report_new: Any | None = None,
    ) -> dict[str, Any]:
        """Merge all available review findings into a complete audit result.

        Args:
            layout: ProjectLayout instance for path resolution
            chapter_number: Chapter number
            storage: StorageService instance for file I/O
            continuity_report_new: Fresh ContinuityReport, if a repair just rechecked it
            causal_report_new: Fresh CausalValidationReport, if a repair just rechecked it

        Returns:
            Complete audit result dict suitable for UIStore.set_audit_result()
        """
        from novel_forge.core.schemas.chapter import CausalValidationReport
        from novel_forge.core.schemas.continuity import ContinuityReport

        quality_payload = cls._load_quality_gate_payload(layout, chapter_number, storage)
        dimension_scores = cls._extract_dimension_scores(quality_payload)
        normalized_issues = cls._extract_findings_from_quality_payload(quality_payload)

        # Load reports: prefer new reports from parameters, otherwise load from disk.
        # These domain reports are still useful when the unified report has not been
        # produced yet, and when a targeted repair just produced a fresher recheck.
        continuity_report: ContinuityReport | None = continuity_report_new
        causal_report: CausalValidationReport | None = causal_report_new

        if continuity_report is None:
            cont_path = layout.continuity_report_path(chapter_number)
            if storage.exists(cont_path):
                try:
                    cont_raw = storage.load_json(cont_path) or {}
                    continuity_report = ContinuityReport.model_validate(cont_raw)
                except Exception as exc:
                    _log.warning("Failed to load continuity report: %s", exc)

        if causal_report is None:
            causal_path = layout.chapter_causal_report_path(chapter_number)
            if storage.exists(causal_path):
                try:
                    causal_raw = storage.load_json(causal_path) or {}
                    causal_report = CausalValidationReport.model_validate(causal_raw)
                except Exception as exc:
                    _log.warning("Failed to load causal report: %s", exc)

        continuity_issues = cls._extract_issues(continuity_report, category_hint="continuity")
        causal_issues = cls._extract_issues(causal_report, category_hint="causal")
        if continuity_report_new is not None:
            normalized_issues = [
                issue for issue in normalized_issues if issue.get("dimension") != "continuity"
            ]
        if causal_report_new is not None:
            normalized_issues = [
                issue for issue in normalized_issues if issue.get("dimension") != "causal"
            ]
        merged_issues = cls._merge_and_dedup(
            normalized_issues,
            continuity_issues if continuity_report_new is not None or not normalized_issues else [],
            causal_issues if causal_report_new is not None or not normalized_issues else [],
        )

        # Calculate scores
        continuity_score = (
            cls._extract_score(continuity_report_new, score_key="continuity_score")
            if continuity_report_new is not None
            else cls._dimension_score(dimension_scores, "continuity")
        )
        if continuity_score is None:
            continuity_score = cls._extract_score(continuity_report, score_key="continuity_score")
        causal_score = (
            cls._extract_score(causal_report_new, score_key="causal_score")
            if causal_report_new is not None
            else cls._dimension_score(dimension_scores, "causal")
        )
        if causal_score is None:
            causal_score = cls._extract_score(causal_report, score_key="causal_score")
        cls._override_dimension_score(
            dimension_scores,
            "continuity",
            continuity_score,
            threshold=6.0,
            passed=(continuity_score is None or continuity_score >= 6.0),
        )
        cls._override_dimension_score(
            dimension_scores,
            "causal",
            causal_score,
            threshold=7.0,
            passed=(causal_score is None or causal_score >= 7.0),
        )
        alignment_score = cls._dimension_score(dimension_scores, "alignment")
        chapter_quality_score = cls._dimension_score(dimension_scores, "chapter_quality")
        reading_power_score = cls._dimension_score(dimension_scores, "reading_power")

        overall_score: float | None = None
        try:
            eval_path = layout.eval_report_path(chapter_number)
            if storage.exists(eval_path):
                eval_raw = storage.load_json(eval_path) or {}
                overall_score = eval_raw.get("overall_score")
        except Exception:
            pass

        final_score = continuity_score
        if final_score is None:
            scored_values = [
                score
                for score in (
                    alignment_score,
                    chapter_quality_score,
                    causal_score,
                    reading_power_score,
                )
                if score is not None
            ]
            final_score = sum(scored_values) / len(scored_values) if scored_values else 0.0

        # Build complete audit result
        return cls._build_audit_result(
            chapter_number=chapter_number,
            merged_issues=merged_issues,
            continuity_score=final_score,
            causal_score=causal_score,
            alignment_score=alignment_score,
            chapter_quality_score=chapter_quality_score,
            reading_power_score=reading_power_score,
            dimension_scores=dimension_scores,
            overall_score=overall_score,
        )

    @classmethod
    def _load_quality_gate_payload(
        cls,
        layout: Any,
        chapter_number: int,
        storage: Any,
    ) -> dict[str, Any]:
        path_getter = getattr(layout, "quality_gate_report_path", None)
        if not callable(path_getter):
            return {}
        path = path_getter(chapter_number)
        if not storage.exists(path):
            return {}
        try:
            payload = storage.load_json(path) or {}
        except Exception as exc:
            _log.warning("Failed to load quality gate report: %s", exc)
            return {}
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _extract_dimension_scores(cls, quality_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw = quality_payload.get("dimension_scores", {}) if quality_payload else {}
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for dimension, value in raw.items():
            key = str(dimension or "").strip()
            if not key:
                continue
            if isinstance(value, dict):
                score = cls._coerce_optional_float(value.get("score"))
                result[key] = {
                    "score": score,
                    "threshold": cls._coerce_optional_float(value.get("threshold")),
                    "passed": bool(value.get("passed", score is not None and score >= 0.0)),
                }
            else:
                result[key] = {
                    "score": cls._coerce_optional_float(value),
                    "threshold": None,
                    "passed": True,
                }
        return result

    @classmethod
    def _extract_findings_from_quality_payload(
        cls,
        quality_payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        raw_findings = quality_payload.get("review_findings", []) if quality_payload else []
        if not isinstance(raw_findings, list):
            return []
        issues: list[dict[str, Any]] = []
        for finding in raw_findings:
            if isinstance(finding, dict):
                issues.append(cls._finding_to_issue(finding))
        return issues

    @classmethod
    def _finding_to_issue(cls, finding: dict[str, Any]) -> dict[str, Any]:
        metadata = finding.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        original = metadata.get("original_issue", {})
        original_location = original.get("location", "") if isinstance(original, dict) else ""
        return {
            "severity": finding.get("severity", "medium"),
            "dimension": finding.get("dimension", ""),
            "category": finding.get("issue_type", finding.get("dimension", "")),
            "issue_type": finding.get("issue_type", finding.get("dimension", "")),
            "summary": finding.get("summary", ""),
            "evidence": finding.get("evidence_quote", ""),
            "suggested_fix": finding.get("repair_goal", ""),
            "affected_chapters": [finding.get("chapter_number")]
            if finding.get("chapter_number")
            else [],
            "location": original_location,
            "finding_id": finding.get("finding_id", ""),
            "source_module": finding.get("source_module", ""),
        }

    @classmethod
    def _extract_issues(
        cls, report: Any | None, *, category_hint: str = "continuity"
    ) -> list[dict[str, Any]]:
        """Extract issues from a report, converting to dict format."""
        if report is None:
            return []

        issues = getattr(report, "issues", []) or []
        result = []

        for issue in issues:
            issue_dict = cls._issue_to_dict(issue, category_hint=category_hint)
            result.append(issue_dict)

        return result

    @classmethod
    def _issue_to_dict(
        cls, issue: Any, *, category_hint: str = "continuity"
    ) -> dict[str, Any]:
        """Convert issue object or dict to unified dict format."""
        if isinstance(issue, dict):
            # Already a dict, ensure required fields
            return {
                "severity": issue.get("severity", "medium"),
                "category": issue.get("issue_type", issue.get("category", category_hint)),
                "issue_type": issue.get("issue_type", issue.get("category", category_hint)),
                "summary": issue.get("summary", ""),
                "evidence": issue.get("evidence", ""),
                "suggested_fix": issue.get("suggested_fix", issue.get("fix_suggestion", "")),
                "affected_chapters": issue.get("affected_chapters", []),
                "location": issue.get("location", ""),
            }

        # Issue is an object (Pydantic model or dataclass)
        return {
            "severity": getattr(issue, "severity", "medium") or "medium",
            "category": getattr(issue, "issue_type", category_hint) or category_hint,
            "issue_type": getattr(issue, "issue_type", category_hint) or category_hint,
            "summary": getattr(issue, "summary", "") or "",
            "evidence": getattr(issue, "evidence", "") or "",
            "suggested_fix": (
                getattr(issue, "suggested_fix", None)
                or getattr(issue, "fix_suggestion", "")
                or ""
            ),
            "affected_chapters": getattr(issue, "affected_chapters", []) or [],
            "location": getattr(issue, "location", "") or "",
        }

    @classmethod
    def _merge_and_dedup(
        cls,
        *issue_groups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge and deduplicate issues from all available review sources.

        Uses (issue_type, location, summary[:50]) as dedup key.
        """
        seen: set[tuple[str, str, str]] = set()
        merged: list[dict[str, Any]] = []

        for issues in issue_groups:
            for issue in issues:
                key = cls._dedup_key(issue)
                if key not in seen:
                    seen.add(key)
                    merged.append(issue)

        return merged

    @classmethod
    def _dedup_key(cls, issue: dict[str, Any]) -> tuple[str, str, str]:
        """Generate dedup key for an issue."""
        issue_type = issue.get("issue_type", issue.get("category", ""))
        location = issue.get("location", "")
        summary = issue.get("summary", "")[:50]  # First 50 chars for dedup
        return (issue_type, location, summary)

    @classmethod
    def _coerce_optional_float(cls, value: Any) -> float | None:
        """Coerce to float or None (preserves original None semantics)."""
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _dimension_score(
        cls,
        dimension_scores: dict[str, dict[str, Any]],
        dimension: str,
    ) -> float | None:
        entry = dimension_scores.get(dimension)
        if not isinstance(entry, dict):
            return None
        return cls._coerce_optional_float(entry.get("score"))

    @classmethod
    def _override_dimension_score(
        cls,
        dimension_scores: dict[str, dict[str, Any]],
        dimension: str,
        score: float | None,
        *,
        threshold: float,
        passed: bool,
    ) -> None:
        if score is None:
            return
        existing = dimension_scores.get(dimension, {})
        existing_threshold = existing.get("threshold") if isinstance(existing, dict) else None
        dimension_scores[dimension] = {
            "score": score,
            "threshold": existing_threshold if existing_threshold is not None else threshold,
            "passed": passed,
        }

    @classmethod
    def _extract_score(cls, report: Any | None, *, score_key: str = "continuity_score") -> float | None:
        """Extract score from a report."""
        if report is None:
            return None

        # Try attribute access first
        score = getattr(report, score_key, None)
        if score is not None:
            try:
                return float(score)
            except (TypeError, ValueError):
                pass

        # Try dict access
        if isinstance(report, dict):
            score = report.get(score_key)
            if score is not None:
                return cls._coerce_optional_float(score)

        return None

    @classmethod
    def _issues_by_dimension(cls, issues: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for issue in issues:
            dimension = str(issue.get("dimension") or issue.get("category") or "other").strip()
            grouped.setdefault(dimension or "other", []).append(issue)
        return grouped

    @classmethod
    def _build_audit_result(
        cls,
        *,
        chapter_number: int,
        merged_issues: list[dict[str, Any]],
        continuity_score: float,
        causal_score: float | None = None,
        alignment_score: float | None = None,
        chapter_quality_score: float | None = None,
        reading_power_score: float | None = None,
        dimension_scores: dict[str, dict[str, Any]] | None = None,
        overall_score: float | None = None,
    ) -> dict[str, Any]:
        """Build complete audit result dict."""
        # Count by severity
        critical_count = sum(
            1 for iss in merged_issues if iss.get("severity", "").lower() == "critical"
        )
        high_count = sum(
            1 for iss in merged_issues if iss.get("severity", "").lower() == "high"
        )

        result = {
            "chapter_number": chapter_number,
            "continuity_score": continuity_score,
            "causal_score": causal_score if causal_score is not None else 0.0,
            "alignment_score": alignment_score,
            "chapter_quality_score": chapter_quality_score,
            "reading_power_score": reading_power_score,
            "dimension_scores": dimension_scores or {},
            "issues_by_dimension": cls._issues_by_dimension(merged_issues),
            "issue_count": len(merged_issues),
            "critical_issues": critical_count,
            "high_issues": high_count,
            "overall_score": overall_score,
            "critique": {
                "issues": [
                    {
                        "severity": iss.get("severity", "medium"),
                        "dimension": iss.get("dimension", ""),
                        "category": iss.get("issue_type", iss.get("category", "")),
                        "summary": iss.get("summary", ""),
                        "evidence": iss.get("evidence", ""),
                        "suggested_fix": iss.get("suggested_fix", ""),
                        "affected_chapters": iss.get("affected_chapters", []),
                    }
                    for iss in merged_issues
                ],
            },
        }
        return result
