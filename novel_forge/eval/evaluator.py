"""DraftEvaluator — scores a draft via model + rule-based metrics."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.core.parsing.parse_utils import safe_parse_json
from novel_forge.core.schemas.eval_schema import EvalReport, EvalScore, RepairSuggestion
from novel_forge.eval.metrics import DEFAULT_PASS_THRESHOLD
from novel_forge.eval.rule_based_precheck import (
    run_rule_based_precheck,
)
from novel_forge.gateway.router import ModelRouter
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.prompts.builder import PromptBuilder

_log = get_logger("eval.evaluator")
_TENCENT_THINKING_MODELS = {
    "hunyuan-2.0-think",
    "hunyuan-2.0-thinking",
    "hunyuan-2.0-thinking-20251109",
}
_TENCENT_STRUCTURED_RETRY_MODEL = "hunyuan-2.0-instruct-20251111"

# Rubric version history:
#   v2 — original 7-dimension rubric (consistency / continuity / plot_progression
#        / character / style / engagement / pacing) + optional rewrite_compliance.
#   v3 — adds ai_flavor advisory block on EvalReport (M5). The advisory is
#        non-blocking: it never modifies scores, overall_score, or passed.
#        Future v4+ may promote it to a hard gate once distribution is
#        calibrated. See scripts/calibrate_ai_flavor_distribution.py.
_EVAL_RUBRIC_VERSION = "2026-06-30.evaluate_draft.v3"

# ── M5 AI-flavor advisory constants (mirrors QualityGate._AI_FLAVOR_SEVERITY_WEIGHTS) ──
_AI_FLAVOR_SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 4.0,
    "high": 2.0,
    "medium": 1.0,
    "low": 0.5,
}
_AI_FLAVOR_CRITICAL_CAP: float = 5.0
_AI_FLAVOR_EVIDENCE_QUOTE_LIMIT: int = 60
_EVAL_PROMPT_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "prompts"
    / "prompts"
    / "writing"
    / "evaluate_draft.j2"
)
_GENERIC_SCORE_COMMENT_MARKERS = (
    "基本满足",
    "整体较好",
    "较好",
    "良好",
    "无明显问题",
    "符合要求",
    "完成度",
    "比较稳定",
)
_AI_FLAVOR_SOURCE_MISSING = object()


def _safe_ai_flavor_confidence(value: Any, *, default: float = 0.8) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    return max(0.0, min(1.0, result))


class DraftEvaluator:
    """Evaluates a draft by calling the model for scoring."""

    #: Current rubric version, exposed as a class attribute for downstream tools.
    RUBRIC_VERSION: str = _EVAL_RUBRIC_VERSION

    def __init__(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
        *,
        threshold: float = DEFAULT_PASS_THRESHOLD,
        settings: Settings,
    ) -> None:
        self._router = router
        self._builder = builder
        self._threshold = threshold
        self._settings = settings

    # ── M5: AI-flavor advisory block ─────────────────────────────────

    @staticmethod
    def _build_ai_flavor_advisory(
        humanize_report: Any,
        *,
        evidence_quote_limit: int = _AI_FLAVOR_EVIDENCE_QUOTE_LIMIT,
    ) -> dict[str, Any]:
        """Build an advisory block from a HumanizeReport (or hits list).

        Mirrors QualityGate.check_ai_flavor's severity-weighted formula but
        exposes the result as a non-blocking advisory. The block has shape::

            {
              "hit_count": int,
              "critical_count": int,
              "by_pattern_id": dict[str, int],
              "by_severity": dict[str, int],
              "deterministic_score": float,    # 0..10; critical caps at 5.0
              "evidence": [                   # top-N per pattern, quote truncated
                {"pattern_id": str, "severity": str,
                 "confidence": float, "evidence_quote": str}
              ],
              "rubric_version": str,
            }

        Returns an empty advisory (``hit_count=0``) for None or empty input,
        so callers can stamp unconditionally.

        Added in M5 — see docs/ai_flavor_quality.md.
        """
        empty: dict[str, Any] = {
            "hit_count": 0,
            "critical_count": 0,
            "by_pattern_id": {},
            "by_severity": {},
            "deterministic_score": 10.0,
            "evidence": [],
            "rubric_version": _EVAL_RUBRIC_VERSION,
        }
        if humanize_report is None:
            return empty

        # Normalize input shape: HumanizeReport / list[Hit] / list[dict] / dict-wrapped.
        if isinstance(humanize_report, list):
            hits_raw: list[Any] = list(humanize_report)
        elif isinstance(humanize_report, dict):
            hits_raw = list(humanize_report.get("pattern_hits") or [])
        else:
            hits_raw = list(getattr(humanize_report, "pattern_hits", []) or [])

        if not hits_raw:
            return empty

        by_pattern: dict[str, int] = {}
        # by_severity starts with all severity buckets at 0 so downstream
        # consumers can rely on the keys existing regardless of which severities
        # actually fired.
        by_severity: dict[str, int] = {sev: 0 for sev in _AI_FLAVOR_SEVERITY_WEIGHTS}
        critical_count = 0
        penalty = 0.0
        evidence: list[dict[str, Any]] = []

        for hit in hits_raw:
            if isinstance(hit, dict):
                pattern_id = str(hit.get("pattern_id", "unknown") or "unknown")
                severity = str(hit.get("severity", "medium") or "medium").lower()
                confidence = _safe_ai_flavor_confidence(hit.get("confidence", 0.8))
                quote = str(hit.get("evidence_quote", "") or "").strip()
            else:
                pattern_id = str(getattr(hit, "pattern_id", "unknown") or "unknown")
                severity = str(getattr(hit, "severity", "medium") or "medium").lower()
                confidence = _safe_ai_flavor_confidence(getattr(hit, "confidence", 0.8))
                quote = str(getattr(hit, "evidence_quote", "") or "").strip()

            weight = _AI_FLAVOR_SEVERITY_WEIGHTS.get(severity, 1.0)
            penalty += weight * confidence
            by_pattern[pattern_id] = by_pattern.get(pattern_id, 0) + 1
            by_severity[severity] = by_severity.get(severity, 0) + 1
            if severity == "critical":
                critical_count += 1

            if len(evidence) < 8 and quote:
                evidence.append({
                    "pattern_id": pattern_id,
                    "severity": severity,
                    "confidence": round(confidence, 2),
                    "evidence_quote": (
                        quote[:evidence_quote_limit]
                        if len(quote) > evidence_quote_limit
                        else quote
                    ),
                })

        deterministic_score = max(0.0, 10.0 - penalty)
        if critical_count:
            deterministic_score = min(deterministic_score, _AI_FLAVOR_CRITICAL_CAP)
        deterministic_score = round(deterministic_score, 2)

        return {
            "hit_count": len(hits_raw),
            "critical_count": critical_count,
            "by_pattern_id": by_pattern,
            "by_severity": by_severity,
            "deterministic_score": deterministic_score,
            "evidence": evidence,
            "rubric_version": _EVAL_RUBRIC_VERSION,
        }

    @staticmethod
    def _ai_flavor_source_from_context(extra_ctx: dict[str, Any]) -> Any:
        if "humanize_report" in extra_ctx:
            return extra_ctx.get("humanize_report")
        if "ai_flavor_hits" in extra_ctx:
            return extra_ctx.get("ai_flavor_hits")
        return _AI_FLAVOR_SOURCE_MISSING

    def _attach_ai_flavor_advisory(
        self,
        report: EvalReport,
        ai_flavor_source: Any,
    ) -> EvalReport:
        if ai_flavor_source is _AI_FLAVOR_SOURCE_MISSING:
            return report
        object.__setattr__(
            report,
            "ai_flavor_advisory",
            self._build_ai_flavor_advisory(ai_flavor_source),
        )
        return report

    @staticmethod
    def _content_preview(text: str, *, limit: int = 180) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

    @staticmethod
    def _prompt_template_hash() -> str:
        try:
            return hashlib.sha256(_EVAL_PROMPT_TEMPLATE_PATH.read_bytes()).hexdigest()[:16]
        except OSError:
            return ""

    @classmethod
    def _score_diagnostics(cls, report: EvalReport) -> tuple[str, dict[str, Any]]:
        scores = list(report.scores or [])
        rounded_scores = [round(float(item.score), 1) for item in scores]
        comments = [str(item.comment or "").strip() for item in scores]
        normalized_comments = ["".join(comment.split()) for comment in comments if comment]
        repeated_comment_count = len(normalized_comments) - len(set(normalized_comments))
        generic_comment_count = sum(
            1
            for comment in comments
            if not comment
            or len(comment) < 8
            or any(marker in comment for marker in _GENERIC_SCORE_COMMENT_MARKERS)
        )
        score_counts = {
            score: rounded_scores.count(score)
            for score in sorted(set(rounded_scores))
        }
        dominant_score_count = max(score_counts.values(), default=0)
        all_dimensions_same = len(rounded_scores) >= 4 and len(score_counts) == 1
        dominant_uniform = (
            len(rounded_scores) >= 6
            and dominant_score_count >= len(rounded_scores) - 1
            and (repeated_comment_count > 0 or generic_comment_count >= len(rounded_scores) // 2)
        )
        suspicious = all_dimensions_same or dominant_uniform
        diagnostics = {
            "rubric_version": _EVAL_RUBRIC_VERSION,
            "dimension_count": len(scores),
            "unique_score_count": len(score_counts),
            "dominant_score_count": dominant_score_count,
            "all_dimensions_same_score": all_dimensions_same,
            "repeated_comment_count": repeated_comment_count,
            "generic_comment_count": generic_comment_count,
            "ai_flavor_advisory": dict(getattr(report, "ai_flavor_advisory", {}) or {}),
            "score_scope": "清单式综合写作质量分；结构完成度高不等于文学表达满分。",
            "diagnostic_only": True,
        }
        return ("suspect" if suspicious else "normal"), diagnostics

    def _apply_precheck_ceilings(self, report: EvalReport, draft_text: str) -> EvalReport:
        """P1-2: Apply rule-based precheck ceilings to prevent score inflation."""
        try:
            precheck = run_rule_based_precheck(draft_text)
            if not precheck.has_violations:
                return report
            # Clamp overall score
            clamped_overall = report.overall_score
            if "overall" in precheck.ceilings:
                clamped_overall = min(report.overall_score, precheck.ceilings["overall"])
            # Clamp dimension scores
            clamped_scores = dict(report.scores) if report.scores else {}
            for dim, ceiling in precheck.ceilings.items():
                if dim == "overall" or dim not in clamped_scores:
                    continue
                score_obj = clamped_scores[dim]
                current_val = float(getattr(score_obj, "score", score_obj) if not isinstance(score_obj, (int, float)) else score_obj)
                if current_val > ceiling:
                    clamped_scores[dim] = ceiling
            # Build updated report
            report = report.model_copy(
                update={
                    "overall_score": clamped_overall,
                    "scores": clamped_scores,
                    "summary": (
                        f"{report.summary}｜规则预检: {'; '.join(precheck.violations)}"
                        if report.summary
                        else f"规则预检: {'; '.join(precheck.violations)}"
                    ),
                }
            )
            _log.info(
                "eval_precheck_clamped | violations=%d | overall=%.1f→%.1f",
                len(precheck.violations),
                report.overall_score,
                clamped_overall,
            )
        except Exception as exc:
            _log.debug("eval_precheck_failed | error=%s", exc)
        return report

    def _stamp_report_metadata(
        self,
        report: EvalReport,
        *,
        confidence_override: str | None = None,
        fallback_reason: str = "evaluation_response_parse_failed",
    ) -> EvalReport:
        confidence, diagnostics = self._score_diagnostics(report)
        if confidence_override:
            confidence = confidence_override
            diagnostics["fallback_reason"] = fallback_reason
        object.__setattr__(report, "rubric_version", _EVAL_RUBRIC_VERSION)
        object.__setattr__(report, "prompt_template_hash", self._prompt_template_hash())
        object.__setattr__(report, "score_confidence", confidence)
        object.__setattr__(report, "score_diagnostics", diagnostics)
        if confidence == "suspect":
            _log.warning(
                "eval_score_pattern_suspect | overall=%.1f | diagnostics=%s",
                float(report.overall_score or 0.0),
                diagnostics,
            )
        return report

    def _default_report(
        self,
        *,
        ai_flavor_source: Any = _AI_FLAVOR_SOURCE_MISSING,
        fallback_reason: str = "evaluation_response_parse_failed",
    ) -> EvalReport:
        report = EvalReport(
            scores=[
                EvalScore(dimension="consistency", score=5.0, comment="解析失败，默认分"),
                EvalScore(dimension="continuity", score=5.0, comment="解析失败，默认分"),
                EvalScore(dimension="plot_progression", score=5.0, comment="解析失败，默认分"),
                EvalScore(dimension="character", score=5.0, comment="解析失败，默认分"),
                EvalScore(dimension="style", score=5.0, comment="解析失败，默认分"),
                EvalScore(dimension="engagement", score=5.0, comment="解析失败，默认分"),
                EvalScore(dimension="pacing", score=5.0, comment="解析失败，默认分"),
            ],
            threshold=self._threshold,
            summary=(
                "模型网关暂不可用，使用不可参与质量门的兜底报告。"
                if fallback_reason == "model_gateway_unavailable"
                else "评估响应解析失败，使用默认分数。"
            ),
            repair_suggestions=[],
            evaluation_status="fallback",
            is_fallback=True,
            fallback_reason=fallback_reason,
        )
        report.compute_overall()
        self._attach_ai_flavor_advisory(report, ai_flavor_source)
        return self._stamp_report_metadata(
            report,
            confidence_override="fallback",
            fallback_reason=fallback_reason,
        )

    def _parse_report(
        self,
        raw_content: str,
        *,
        ai_flavor_source: Any = _AI_FLAVOR_SOURCE_MISSING,
    ) -> EvalReport:
        data = safe_parse_json(raw_content)
        if not isinstance(data, dict):
            raise TypeError(f"expected dict payload, got {type(data).__name__}")

        raw_scores = data.get("scores", [])
        scores: list[EvalScore] = []
        for s in raw_scores:
            if not isinstance(s, dict):
                continue
            try:
                scores.append(
                    EvalScore(
                        dimension=str(s.get("dimension", "unknown")),
                        score=float(s.get("score", 5.0)),
                        comment=str(s.get("comment", "")),
                    )
                )
            except (ValueError, TypeError, KeyError):
                continue
        if not scores:
            raise ValueError("evaluation payload does not contain any valid scores")

        raw_suggestions = data.get("repair_suggestions", [])
        repair_suggestions: list[RepairSuggestion] = []
        for sug in raw_suggestions:
            if not isinstance(sug, dict):
                continue
            try:
                repair_suggestions.append(
                    RepairSuggestion(
                        issue=str(sug.get("issue", "")),
                        location=str(sug.get("location", "")),
                        suggestion=str(sug.get("suggestion", "")),
                        priority=str(sug.get("priority", "medium")),
                        dimension=str(sug.get("dimension", "")),
                    )
                )
            except (ValueError, TypeError, KeyError):
                continue

        overall_score = data.get("overall_score", 0.0)
        threshold = data.get("threshold", self._threshold)
        report = EvalReport(
            scores=scores,
            overall_score=float(overall_score) if isinstance(overall_score, (int, float)) else 0.0,
            passed=bool(data.get("passed", False)),
            threshold=float(threshold) if isinstance(threshold, (int, float)) else self._threshold,
            summary=str(data.get("summary", "")),
            repair_suggestions=repair_suggestions,
        )
        report.compute_overall()
        self._attach_ai_flavor_advisory(report, ai_flavor_source)
        return self._stamp_report_metadata(report)

    def _should_retry_with_tencent_instruct(
        self, request: ModelRequest, response: ModelResponse
    ) -> bool:
        model_id = str(response.model_id or request.model_id or "").strip().lower()
        return model_id in _TENCENT_THINKING_MODELS

    async def _retry_with_json_only(
        self,
        request: ModelRequest,
        response: ModelResponse,
    ) -> ModelResponse | None:
        """Generic retry for any provider: re-send with temperature=0.0 and JSON-only instruction."""
        json_only_instruction = (
            "\n\n上文响应解析失败。请只输出纯JSON文本，不要任何解释、前言、后语或Markdown代码块。"
        )

        new_messages = list(request.messages)
        if new_messages:
            last_msg = new_messages[-1]
            new_messages[-1] = {
                **last_msg,
                "content": last_msg.get("content", "") + json_only_instruction,
            }
        else:
            new_messages = [{"role": "user", "content": json_only_instruction}]

        retry_request = request.model_copy(
            update={
                "messages": new_messages,
                "temperature": 0.0,
            }
        )
        _log.warning(
            "eval_parse_retry_json_only | model=%s | preview=%s",
            response.model_id or request.model_id or "",
            self._content_preview(response.content),
        )
        try:
            return await self._router.route(retry_request)
        except (ModelGatewayError, asyncio.TimeoutError, TimeoutError) as exc:
            _log.warning(
                "eval_parse_retry_json_only_failed | model=%s | error=%s",
                response.model_id or request.model_id or "",
                exc,
            )
            return None

    async def _retry_with_tencent_instruct(
        self,
        request: ModelRequest,
        response: ModelResponse,
    ) -> ModelResponse | None:
        if not self._should_retry_with_tencent_instruct(request, response):
            return None

        retry_request = request.model_copy(
            update={
                "model_id": _TENCENT_STRUCTURED_RETRY_MODEL,
                "thinking": False,
                "temperature": 0.0,
            }
        )
        _log.warning(
            "eval_parse_retry | from_model=%s | to_model=%s | preview=%s",
            response.model_id or request.model_id or "",
            _TENCENT_STRUCTURED_RETRY_MODEL,
            self._content_preview(response.content),
        )
        try:
            return await self._router.route(retry_request, provider="tencent")
        except Exception as exc:
            _log.warning(
                "eval_parse_retry_failed | from_model=%s | to_model=%s | error=%s",
                response.model_id or request.model_id or "",
                _TENCENT_STRUCTURED_RETRY_MODEL,
                exc,
            )
            return None

    async def evaluate(self, draft_text: str, **extra_ctx: Any) -> EvalReport:
        """Score a draft and return an EvalReport.

        Args:
            draft_text: The chapter text to evaluate.
            **extra_ctx: Optional context passed to the prompt template.
                Supported keys: tone, genre, chapter_goal, rewrite_notes.
        """
        ai_flavor_source = self._ai_flavor_source_from_context(extra_ctx)
        prompt_extra_ctx = dict(extra_ctx)
        prompt_extra_ctx.pop("humanize_report", None)
        prompt_extra_ctx.pop("ai_flavor_hits", None)
        ctx: dict[str, Any] = {"draft_text": draft_text, **prompt_extra_ctx}
        request = self._builder.build(
            TaskType.EVALUATE,
            ctx,
            max_tokens=calculate_route_aware_max_tokens(
                self._router,
                TaskType.EVALUATE,
                max(1800, len(draft_text) // 4),
                prompt_overhead=2400,
                min_tokens=2048,
            ),
            temperature=self._settings.temp_evaluate,
        )
        try:
            response = await self._router.route(request)
        except ModelGatewayError as exc:
            if not exc.is_transient_error:
                raise
            _log.warning("eval_route_failed_using_fallback | error=%s", exc)
            return self._default_report(
                ai_flavor_source=ai_flavor_source,
                fallback_reason="model_gateway_unavailable",
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            _log.warning("eval_route_failed_using_fallback | error=%s", exc)
            return self._default_report(
                ai_flavor_source=ai_flavor_source,
                fallback_reason="model_gateway_unavailable",
            )

        try:
            report = self._parse_report(
                response.content,
                ai_flavor_source=ai_flavor_source,
            )
            # P1-2: Apply rule-based precheck ceilings to prevent score inflation
            report = self._apply_precheck_ceilings(report, draft_text)
            return report
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            _log.warning(
                "eval_parse_failed | model=%s | thinking_chars=%d | error=%s | preview=%s",
                response.model_id or request.model_id or "",
                len(response.thinking_content or ""),
                exc,
                self._content_preview(response.content),
            )
            retry_response = await self._retry_with_json_only(request, response)
            if retry_response is not None:
                try:
                    return self._parse_report(
                        retry_response.content,
                        ai_flavor_source=ai_flavor_source,
                    )
                except (json.JSONDecodeError, TypeError, ValueError) as retry_exc:
                    _log.warning(
                        "eval_parse_failed_after_json_only_retry | model=%s | error=%s | preview=%s",
                        retry_response.model_id or request.model_id or "",
                        retry_exc,
                        self._content_preview(retry_response.content),
                    )

            tencent_response = await self._retry_with_tencent_instruct(request, response)
            if tencent_response is not None:
                try:
                    return self._parse_report(
                        tencent_response.content,
                        ai_flavor_source=ai_flavor_source,
                    )
                except (json.JSONDecodeError, TypeError, ValueError) as tencent_exc:
                    _log.warning(
                        "eval_parse_failed_after_tencent_retry | model=%s | error=%s | preview=%s",
                        tencent_response.model_id or _TENCENT_STRUCTURED_RETRY_MODEL,
                        tencent_exc,
                        self._content_preview(tencent_response.content),
                    )

        return self._default_report(ai_flavor_source=ai_flavor_source)
