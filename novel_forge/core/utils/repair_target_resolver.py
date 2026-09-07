"""Resolve v2 audit locators into concrete repair targets.

The resolver owns code-side target selection.  Audit models may describe what
should be fixed, but repair prompts only receive targets that this module has
resolved against the current payload/text.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from novel_forge.core.schemas.audit import (
    AuditIssueV2,
    AuditLocator,
    AuditRepairOperation,
    ResolvedRepairTarget,
)


@dataclass(frozen=True)
class RepairResolverContext:
    """Inputs available to format-specific resolvers."""

    artifacts: dict[str, Any] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)
    markdown_documents: dict[str, str] = field(default_factory=dict)
    prompt_responses: dict[str, Any] = field(default_factory=dict)
    claim_sources: dict[str, AuditLocator] = field(default_factory=dict)
    stable_node_paths: dict[str, str] = field(default_factory=dict)
    min_confidence: float = 0.62
    ambiguity_margin: float = 0.08


class RepairTargetResolver:
    """Resolve ``AuditIssueV2.repair_targets`` by target format."""

    def __init__(self, context: RepairResolverContext | None = None) -> None:
        self.context = context or RepairResolverContext()

    def resolve_issue(self, issue: AuditIssueV2 | dict[str, Any]) -> list[ResolvedRepairTarget]:
        parsed_issue = _coerce_issue(issue)
        if parsed_issue is None:
            return [
                _manual_target(
                    issue_id=_issue_id(issue),
                    reason="invalid_audit_issue_v2",
                    summary=_issue_summary(issue),
                )
            ]
        reference_keys = _reference_keys(parsed_issue.reference_targets)
        resolved: list[ResolvedRepairTarget] = []
        for locator in parsed_issue.repair_targets:
            if _locator_key(locator) in reference_keys:
                resolved.append(
                    _manual_target(
                        issue_id=parsed_issue.issue_id,
                        reason="repair_target_matches_reference_target",
                        summary=parsed_issue.summary,
                        locator=locator,
                    )
                )
                continue
            target = self.resolve_locator(
                locator,
                issue_id=parsed_issue.issue_id,
                operation=parsed_issue.repair_intent.operation,
                reference_keys=reference_keys,
            )
            resolved.append(target)
        return resolved or [
            _manual_target(
                issue_id=parsed_issue.issue_id,
                reason="missing_repair_targets",
                summary=parsed_issue.summary,
            )
        ]

    def resolve_locator(
        self,
        locator: AuditLocator,
        *,
        issue_id: str,
        operation: AuditRepairOperation = "replace",
        reference_keys: set[str] | None = None,
    ) -> ResolvedRepairTarget:
        reference_keys = reference_keys or set()
        if locator.target_format == "json_artifact":
            return self._resolve_json_artifact(
                locator,
                issue_id=issue_id,
                operation=operation,
                reference_keys=reference_keys,
            )
        if locator.target_format == "prose_text":
            return self._resolve_prose_text(locator, issue_id=issue_id, operation=operation)
        if locator.target_format == "markdown_document":
            return self._resolve_markdown_document(locator, issue_id=issue_id, operation=operation)
        if locator.target_format == "prompt_response":
            return self._resolve_prompt_response(locator, issue_id=issue_id, operation=operation)
        if locator.target_format == "multi_chapter_set":
            return _manual_target(
                issue_id=issue_id,
                reason="multi_chapter_set_requires_split_targets",
                locator=locator,
            )
        return _manual_target(
            issue_id=issue_id,
            reason=locator.manual_review_reason or "manual_only",
            locator=locator,
        )

    def _resolve_json_artifact(
        self,
        locator: AuditLocator,
        *,
        issue_id: str,
        operation: AuditRepairOperation,
        reference_keys: set[str],
    ) -> ResolvedRepairTarget:
        payload = self.context.artifacts.get(locator.artifact)
        if payload is None:
            return _manual_target(issue_id=issue_id, reason="artifact_payload_missing", locator=locator)

        candidates: list[tuple[float, str, Any, str]] = []
        exact_path = _locator_json_path(locator, self.context)
        if exact_path:
            value = _get_json_pointer(payload, exact_path)
            if value is not _MISSING and _json_path_not_reference(locator.artifact, exact_path, reference_keys):
                if (
                    locator.container_hash
                    and stable_repair_value_hash(value) != locator.container_hash
                ):
                    return _manual_target(
                        issue_id=issue_id,
                        reason="json_container_hash_mismatch",
                        locator=locator,
                    )
                span = _exact_text_span(value, locator)
                if span is not None:
                    start, end, current_value = span
                    target_id = _target_id(
                        locator.target_format,
                        locator.artifact,
                        f"{exact_path}#{start}:{end}",
                        issue_id,
                    )
                    return ResolvedRepairTarget(
                        target_id=target_id,
                        target_format=locator.target_format,
                        surface=locator.surface or locator.artifact,
                        locator=locator,
                        path=exact_path,
                        window={
                            "char_start": start,
                            "char_end": end,
                            "container_hash": stable_repair_value_hash(value),
                        },
                        current_value=current_value,
                        current_hash=stable_repair_value_hash(current_value),
                        issue_ids=[issue_id],
                        allowed_operation=operation,
                        confidence=1.0,
                        reason="exact_text_span",
                    )
                if locator.char_end > locator.char_start:
                    return _manual_target(
                        issue_id=issue_id,
                        reason="json_text_span_stale_or_unresolved",
                        locator=locator,
                    )
                leaf_path, leaf_value = _prefer_text_leaf(payload, exact_path, value, locator)
                candidates.append((1.0, leaf_path, leaf_value, "exact_path"))

        if locator.field:
            for path, value, score, reason in _json_field_candidates(payload, locator):
                if not _json_path_not_reference(locator.artifact, path, reference_keys):
                    continue
                candidates.append((score, path, value, reason))

        winner = _select_best_candidate(candidates, self.context)
        if winner is None:
            return _manual_target(issue_id=issue_id, reason="json_target_unresolved", locator=locator)
        if winner[0] != "resolved":
            return _manual_target(issue_id=issue_id, reason=winner[0], locator=locator)
        _, score, path, current_value, reason = winner
        target_id = _target_id(locator.target_format, locator.artifact, path, issue_id)
        return ResolvedRepairTarget(
            target_id=target_id,
            target_format=locator.target_format,
            surface=locator.surface or locator.artifact,
            locator=locator,
            path=path,
            current_value=current_value,
            current_hash=stable_repair_value_hash(current_value),
            issue_ids=[issue_id],
            allowed_operation=operation,
            confidence=score,
            reason=reason,
        )

    def _resolve_prose_text(
        self,
        locator: AuditLocator,
        *,
        issue_id: str,
        operation: AuditRepairOperation,
    ) -> ResolvedRepairTarget:
        key = str(locator.chapter_number or locator.surface or "chapter_text")
        text = self.context.texts.get(key) or self.context.texts.get("chapter_text") or ""
        if not text:
            return _manual_target(issue_id=issue_id, reason="prose_text_missing", locator=locator)
        paragraphs = _paragraphs(text)
        start = max(1, locator.paragraph_start) if locator.paragraph_start else 0
        end = max(start, locator.paragraph_end or start) if start else 0
        if start and end and end <= len(paragraphs):
            current = "\n\n".join(paragraphs[start - 1 : end])
            score = 0.92 if _locator_quote_matches(locator, current) else 0.75
            return _resolved_window_target(
                locator,
                issue_id=issue_id,
                operation=operation,
                current_value=current,
                score=score,
                window={"paragraph_start": start, "paragraph_end": end},
                reason="paragraph_window",
            )
        if locator.quote:
            matches = _quote_matches(paragraphs, locator.quote)
            if len(matches) == 1:
                index, current = matches[0]
                return _resolved_window_target(
                    locator,
                    issue_id=issue_id,
                    operation=operation,
                    current_value=current,
                    score=0.68,
                    window={"paragraph_start": index, "paragraph_end": index},
                    reason="quote_only_low_confidence",
                )
            if len(matches) > 1:
                return _manual_target(issue_id=issue_id, reason="quote_ambiguous", locator=locator)
        return _manual_target(issue_id=issue_id, reason="prose_target_unresolved", locator=locator)

    def _resolve_markdown_document(
        self,
        locator: AuditLocator,
        *,
        issue_id: str,
        operation: AuditRepairOperation,
    ) -> ResolvedRepairTarget:
        key = locator.surface or "markdown_document"
        text = self.context.markdown_documents.get(key) or self.context.markdown_documents.get("document") or ""
        if not text:
            return _manual_target(issue_id=issue_id, reason="markdown_document_missing", locator=locator)
        section = _markdown_section(text, locator.heading_path)
        if section:
            return _resolved_window_target(
                locator,
                issue_id=issue_id,
                operation=operation,
                current_value=section,
                score=0.86,
                window={"heading_path": locator.heading_path},
                reason="heading_subtree",
            )
        return _manual_target(issue_id=issue_id, reason="markdown_target_unresolved", locator=locator)

    def _resolve_prompt_response(
        self,
        locator: AuditLocator,
        *,
        issue_id: str,
        operation: AuditRepairOperation,
    ) -> ResolvedRepairTarget:
        payload = self.context.prompt_responses.get(locator.task_type) or self.context.prompt_responses.get(
            "response"
        )
        path = locator.json_pointer or locator.contract_path
        if payload is not None and path:
            value = _get_json_pointer(payload, path)
            if value is not _MISSING:
                return ResolvedRepairTarget(
                    target_id=_target_id(locator.target_format, locator.task_type or "response", path, issue_id),
                    target_format=locator.target_format,
                    surface=locator.surface or "prompt_response",
                    locator=locator,
                    path=path,
                    current_value=value,
                    current_hash=stable_repair_value_hash(value),
                    issue_ids=[issue_id],
                    allowed_operation=operation,
                    confidence=0.9,
                    reason="schema_path",
                )
        if locator.missing_key or locator.schema_error:
            return _manual_target(
                issue_id=issue_id,
                reason="prompt_response_schema_repair_required",
                locator=locator,
            )
        return _manual_target(issue_id=issue_id, reason="prompt_response_target_unresolved", locator=locator)


_MISSING = object()


def _coerce_issue(issue: AuditIssueV2 | dict[str, Any]) -> AuditIssueV2 | None:
    if isinstance(issue, AuditIssueV2):
        return issue
    if not isinstance(issue, dict):
        return None
    try:
        return AuditIssueV2.model_validate(issue)
    except ValidationError:
        return None


def _issue_id(issue: Any) -> str:
    if isinstance(issue, dict):
        return str(issue.get("issue_id") or issue.get("id") or "issue").strip() or "issue"
    return str(getattr(issue, "issue_id", "") or "issue")


def _issue_summary(issue: Any) -> str:
    if isinstance(issue, dict):
        return str(issue.get("summary") or issue.get("description") or "").strip()
    return str(getattr(issue, "summary", "") or getattr(issue, "description", "") or "").strip()


def _manual_target(
    *,
    issue_id: str,
    reason: str,
    summary: str = "",
    locator: AuditLocator | None = None,
) -> ResolvedRepairTarget:
    locator = locator or AuditLocator(
        target_format="manual_only",
        role="repair",
        manual_review_reason=reason,
    )
    return ResolvedRepairTarget(
        target_id=_target_id(locator.target_format, locator.surface or "manual", reason, issue_id),
        target_format="manual_only",
        surface=locator.surface or "manual",
        locator=locator,
        current_value=summary,
        current_hash=stable_repair_value_hash(summary),
        issue_ids=[issue_id],
        allowed_operation="manual_review",
        confidence=0.0,
        resolution_status="manual_required",
        reason=reason,
    )


def _resolved_window_target(
    locator: AuditLocator,
    *,
    issue_id: str,
    operation: AuditRepairOperation,
    current_value: str,
    score: float,
    window: dict[str, Any],
    reason: str,
) -> ResolvedRepairTarget:
    return ResolvedRepairTarget(
        target_id=_target_id(locator.target_format, locator.surface or "text", json.dumps(window), issue_id),
        target_format=locator.target_format,
        surface=locator.surface or locator.target_format,
        locator=locator,
        window=window,
        current_value=current_value,
        current_hash=stable_repair_value_hash(current_value),
        issue_ids=[issue_id],
        allowed_operation=operation,
        confidence=score,
        reason=reason,
    )


def _reference_keys(reference_targets: list[AuditLocator]) -> set[str]:
    return {_locator_key(locator) for locator in reference_targets if _locator_key(locator)}


_LOCATOR_KEY_SEP = "|||"


def _locator_key(locator: AuditLocator) -> str:
    if locator.target_format == "json_artifact":
        location = (
            locator.json_pointer
            or locator.field_path
            or locator.field
            or locator.stable_node_id
            or locator.claim_id
        )
        return f"{locator.artifact}{_LOCATOR_KEY_SEP}{location}"
    parts = [
        locator.target_format,
        locator.surface,
        locator.quote,
        str(locator.paragraph_start),
        str(locator.paragraph_end),
    ]
    return _LOCATOR_KEY_SEP.join(parts)


def _json_path_not_reference(artifact: str, path: str, reference_keys: set[str]) -> bool:
    if f"{artifact}{_LOCATOR_KEY_SEP}{path}" in reference_keys:
        return False
    root = path.strip("/").split("/", 1)[0]
    return f"{artifact}{_LOCATOR_KEY_SEP}{root}" not in reference_keys


def _claim_source_path(locator: AuditLocator, claim_sources: dict[str, AuditLocator]) -> str:
    if not locator.claim_id:
        return ""
    source = claim_sources.get(locator.claim_id)
    return source.json_pointer if source is not None else ""


def _locator_json_path(locator: AuditLocator, context: RepairResolverContext) -> str:
    """Resolve an absolute pointer from direct, stable-node, or claim anchors."""

    if locator.json_pointer:
        return locator.json_pointer
    if locator.stable_node_id:
        base = context.stable_node_paths.get(
            f"{locator.artifact}:{locator.stable_node_id}",
            context.stable_node_paths.get(locator.stable_node_id, ""),
        )
        if base:
            suffix = _field_path_pointer(locator.field_path)
            if suffix and suffix != "/":
                return f"{base.rstrip('/')}/{suffix.strip('/')}"
            return base
    if locator.field_path:
        return _field_path_pointer(locator.field_path)
    return _claim_source_path(locator, context.claim_sources)


def _field_path_pointer(field_path: str) -> str:
    """Normalize a dotted/bracketed field path to an escaped JSON Pointer."""

    raw = field_path.strip()
    if not raw:
        return ""
    if raw.startswith("/"):
        return raw
    normalized = re.sub(r"\[([^\]]+)\]", r".\1", raw)
    parts = [part for part in normalized.split(".") if part]
    escaped = [part.replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(escaped)


def field_path_to_json_pointer(field_path: str) -> str:
    """Public canonical field-path to JSON-Pointer conversion."""

    return _field_path_pointer(field_path)


def split_json_pointer(pointer: str) -> list[str]:
    """Decode an RFC 6901 pointer into path tokens.

    ``"/"`` intentionally resolves to the empty-string object key for
    compatibility with the historical initialization repair helper.
    """

    raw_parts = str(pointer).lstrip("/").split("/")
    return [part.replace("~1", "/").replace("~0", "~") for part in raw_parts]


def navigate_json_pointer_parent(payload: Any, pointer: str) -> tuple[Any, str]:
    """Return the parent container and final token for an existing pointer.

    The function deliberately preserves normal ``KeyError``/``IndexError``/
    ``ValueError`` failures so callers can distinguish stale paths without a
    second, subtly different JSON-Pointer implementation.
    """

    parts = split_json_pointer(pointer)
    if not parts:
        raise ValueError(f"Empty JSON Pointer path: {pointer}")
    current = payload
    for part in parts[:-1]:
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(f"Cannot descend into {type(current).__name__} at '{part}'")
    return current, parts[-1]


def resolve_json_pointer(payload: Any, pointer: str) -> tuple[bool, Any]:
    """Resolve a pointer without leaking the module's missing sentinel."""

    value = _get_json_pointer(payload, pointer)
    return (value is not _MISSING, None if value is _MISSING else value)


def get_json_pointer(payload: Any, pointer: str) -> Any:
    """Return a JSON-Pointer value, or the module's private missing sentinel."""

    return _get_json_pointer(payload, pointer)


def _get_json_pointer(payload: Any, pointer: str) -> Any:
    if not pointer:
        return payload
    if not pointer.startswith("/"):
        return _MISSING
    current = payload
    for part in split_json_pointer(pointer):
        if isinstance(current, list):
            if not part.isdigit():
                return _MISSING
            index = int(part)
            if index < 0 or index >= len(current):
                return _MISSING
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current[part]
        else:
            return _MISSING
    return current


def _exact_text_span(value: Any, locator: AuditLocator) -> tuple[int, int, str] | None:
    """Validate an exact character span declared by a code-side locator."""

    if not isinstance(value, str) or locator.char_end <= locator.char_start:
        return None
    start = locator.char_start
    end = locator.char_end
    if end > len(value):
        return None
    current = value[start:end]
    if locator.quote and current != locator.quote:
        return None
    if locator.text_hash and stable_repair_value_hash(value) != locator.text_hash:
        return None
    return start, end, current


def _prefer_text_leaf(
    payload: Any,
    path: str,
    value: Any,
    locator: AuditLocator,
) -> tuple[str, Any]:
    if isinstance(value, str):
        return path, value
    if isinstance(value, dict):
        preferred_keys = ("description", "summary", "text", "goal", "event")
        for key in preferred_keys:
            item = value.get(key)
            if isinstance(item, str):
                return f"{path.rstrip('/')}/{_escape_json_pointer(key)}", item
    if isinstance(value, list) and locator.quote:
        for index, item in enumerate(value):
            item_path = f"{path.rstrip('/')}/{index}"
            leaf_path, leaf_value = _prefer_text_leaf(payload, item_path, item, locator)
            if isinstance(leaf_value, str) and _fragment(locator.quote, leaf_value):
                return leaf_path, leaf_value
    return path, value


def _json_field_candidates(payload: Any, locator: AuditLocator) -> list[tuple[str, Any, float, str]]:
    if not isinstance(payload, dict) or locator.field not in payload:
        return []
    root = payload[locator.field]
    candidates: list[tuple[str, Any, float, str]] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, str):
            score = 0.45
            reason = "field_only"
            if locator.quote and _fragment(locator.quote, value):
                score = 0.74
                reason = "field_quote"
            if _path_or_value_mentions_chapter(path, value, locator):
                score += 0.16
                reason = "field_chapter_containment"
            candidates.append((path, value, min(score, 0.94), reason))
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}/{index}")
            return
        if isinstance(value, dict):
            if _dict_mentions_chapter(value, locator):
                preferred_path, preferred_value = _prefer_text_leaf(payload, path, value, locator)
                if isinstance(preferred_value, str):
                    score = 0.82 if locator.quote and _fragment(locator.quote, preferred_value) else 0.72
                    candidates.append((preferred_path, preferred_value, score, "object_chapter_containment"))
                    return
            for key, item in value.items():
                if isinstance(item, (str, list, dict)):
                    walk(item, f"{path}/{_escape_json_pointer(str(key))}")

    walk(root, f"/{_escape_json_pointer(locator.field)}")
    return candidates


def _path_or_value_mentions_chapter(path: str, value: Any, locator: AuditLocator) -> bool:
    del value
    numbers = set(locator.chapter_range)
    if locator.chapter_number:
        numbers.add(locator.chapter_number)
    return any(f"/{number}/" in f"{path}/" for number in numbers)


def _dict_mentions_chapter(value: dict[str, Any], locator: AuditLocator) -> bool:
    numbers = set(locator.chapter_range)
    if locator.chapter_number:
        numbers.add(locator.chapter_number)
    if not numbers:
        return bool(locator.quote)
    for key in ("chapter_number", "chapter", "trigger_chapter", "resolve_chapter"):
        try:
            if int(value.get(key) or 0) in numbers:
                return True
        except (TypeError, ValueError):
            pass
    start = _coerce_int(value.get("chapter_start"))
    end = _coerce_int(value.get("chapter_end"))
    if start and end and any(start <= number <= end for number in numbers):
        return True
    for key in ("chapters", "chapter_numbers", "foreshadow_chapters"):
        raw = value.get(key)
        items = raw if isinstance(raw, list) else []
        if any(_coerce_int(item) in numbers for item in items):
            return True
    return False


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _select_best_candidate(
    candidates: list[tuple[float, str, Any, str]],
    context: RepairResolverContext,
) -> tuple[str, float, str, Any, str] | None:
    if not candidates:
        return None
    ordered = sorted(candidates, key=lambda item: item[0], reverse=True)
    best = ordered[0]
    if best[0] < context.min_confidence:
        return ("target_unresolved_low_confidence", best[0], best[1], best[2], best[3])
    if len(ordered) > 1 and best[0] - ordered[1][0] < context.ambiguity_margin:
        return ("target_ambiguous", best[0], best[1], best[2], best[3])
    return ("resolved", best[0], best[1], best[2], best[3])


def _paragraphs(text: str) -> list[str]:
    chunks = [chunk.strip() for chunk in str(text or "").split("\n\n")]
    return [chunk for chunk in chunks if chunk]


def _quote_matches(paragraphs: list[str], quote: str) -> list[tuple[int, str]]:
    return [(idx, text) for idx, text in enumerate(paragraphs, start=1) if _fragment(quote, text)]


def _locator_quote_matches(locator: AuditLocator, text: str) -> bool:
    return not locator.quote or _fragment(locator.quote, text)


def _fragment(fragment: str, text: str) -> bool:
    normalized_fragment = "".join(str(fragment or "").split()).lower()
    normalized_text = "".join(str(text or "").split()).lower()
    if len(normalized_fragment) < 4:
        return False
    # 短证据只允许 evidence/quote 命中目标文本；不要用长证据反向命中很短字段。
    if len(normalized_fragment) < 8 or len(normalized_text) < 8:
        return normalized_fragment in normalized_text
    return normalized_fragment in normalized_text or normalized_text in normalized_fragment


def _markdown_section(text: str, heading_path: list[str]) -> str:
    if not heading_path:
        return ""
    lines = str(text or "").splitlines()
    target = heading_path[-1].strip()
    start = -1
    level = 0
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            continue
        hashes = len(stripped) - len(stripped.lstrip("#"))
        title = stripped[hashes:].strip()
        if title == target:
            start = index
            level = hashes
            break
    if start < 0:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].lstrip()
        if stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            if hashes <= level:
                end = index
                break
    return "\n".join(lines[start:end]).strip()


def _target_id(target_format: str, surface: str, path: str, issue_id: str) -> str:
    raw = f"{target_format}:{surface}:{path}:{issue_id}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{target_format}:{surface}:{digest}"


def stable_repair_value_hash(value: Any) -> str:
    """Return the canonical value hash used by repair preconditions."""

    data = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _stable_value_hash(value: Any) -> str:
    """Backward-compatible private alias for older local callers/tests."""

    return stable_repair_value_hash(value)


def _escape_json_pointer(value: str) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def escape_json_pointer(value: str) -> str:
    """Public RFC 6901 token escaping used by repair plugins."""

    return _escape_json_pointer(value)
