"""Candidate-only audit contract for dubbing-script self-repair.

The TTS pipeline owns only the derived :class:`DubbingScript`. Chapter prose
is immutable input: review, spoken rewrite, emotion, pause and sound-design
phases may enrich the script but may never rewrite a source segment. This
module records exact field changes against the pre-review segment UID and runs
the original completeness and speaker gates before persistence.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import ConfigDict, Field

from novel_forge.core.schemas.audit import AuditLocator
from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.repair import RepairValidatorResult
from novel_forge.tts.pipeline.script_phases import ScriptCompletenessReport
from novel_forge.tts.schemas import DubbingScript, DubbingSegment
from novel_forge.tts.script_integrity import (
    compute_dubbing_script_hash,
    compute_segment_uid,
    source_text_hash_matches,
    unresolved_speaker_indices,
)

TTSFieldAuthority = Literal[
    "source_locked",
    "professional_review",
    "spoken_rewrite",
    "deterministic_fallback",
    "emotion_label",
    "pause_projection",
    "derived_script_phase",
]


class TTSFieldPolicy(VersionedSchema):
    """Precedence and mutability for one TTS candidate field."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    authority: TTSFieldAuthority
    priority: int = Field(ge=0)
    immutable: bool = False
    precedence: list[str] = Field(default_factory=list)


class TTSRepairFieldChange(VersionedSchema):
    """One exact candidate delta anchored to the baseline segment UID."""

    model_config = ConfigDict(extra="forbid")

    baseline_segment_uid: str
    candidate_segment_uid: str
    display_segment_index: int = Field(ge=0)
    field_path: str
    before_hash: str
    after_hash: str
    policy: TTSFieldPolicy
    locator: AuditLocator


class TTSRepairVerification(VersionedSchema):
    """Independent pre-persistence result for one derived script candidate."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    source_text_hash: str
    baseline_script_hash: str
    candidate_script_hash: str
    changed_targets: list[TTSRepairFieldChange] = Field(default_factory=list)
    validators: list[RepairValidatorResult] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    field_precedence: dict[str, list[str]] = Field(default_factory=dict)


_SOURCE_FIELDS = frozenset({"text", "source_paragraph"})
_REVIEW_FIELDS = frozenset(
    {
        "character_id",
        "character_name",
        "segment_type",
        "tone_hint",
        "speed_override",
        "vol_override",
        "pitch_override",
        "dml_tags",
        "stress_words",
        "paralinguistic_tags",
        "pronunciation_overrides",
        "language_boost",
        "language_code",
        "language_runs",
        "vocal_direction",
        "vocal_tags",
        "platform_extensions",
        "voice_effect",
    }
)
_EMOTION_FIELDS = frozenset({"emotion", "sub_emotion", "emotion_intensity"})
_IGNORED_POSITION_FIELDS = frozenset({"segment_uid", "start_ms", "end_ms"})

TTS_FIELD_PRECEDENCE: dict[str, list[str]] = {
    "text": ["source_locked"],
    "spoken_text": [
        "professional_review",
        "spoken_rewrite",
        "deterministic_fallback",
        "pause_projection",
    ],
    "emotion": ["professional_review", "emotion_label", "deterministic_fallback"],
    "performance": ["professional_review", "emotion_label", "derived_script_phase"],
}


def tts_field_policy(field_name: str) -> TTSFieldPolicy:
    """Return the single documented authority order for a segment field."""

    if field_name in _SOURCE_FIELDS:
        return TTSFieldPolicy(
            field_path=field_name,
            authority="source_locked",
            priority=1000,
            immutable=True,
            precedence=TTS_FIELD_PRECEDENCE["text"],
        )
    if field_name == "spoken_text":
        return TTSFieldPolicy(
            field_path=field_name,
            authority="professional_review",
            priority=800,
            precedence=TTS_FIELD_PRECEDENCE["spoken_text"],
        )
    if field_name in _EMOTION_FIELDS:
        return TTSFieldPolicy(
            field_path=field_name,
            authority="emotion_label",
            priority=600,
            precedence=TTS_FIELD_PRECEDENCE["emotion"],
        )
    if field_name in _REVIEW_FIELDS:
        return TTSFieldPolicy(
            field_path=field_name,
            authority="professional_review",
            priority=700,
            precedence=TTS_FIELD_PRECEDENCE["performance"],
        )
    return TTSFieldPolicy(
        field_path=field_name,
        authority="derived_script_phase",
        priority=500,
        precedence=["derived_script_phase"],
    )


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _normalized_value(value: Any) -> Any:
    raw = _json_value(value)
    if isinstance(raw, str):
        return " ".join(raw.split())
    return raw


def _content_hash(value: Any) -> str:
    payload = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _segment_payload(segment: DubbingSegment) -> dict[str, Any]:
    return segment.model_dump(mode="json", exclude_none=False)


def _changed_fields(
    baseline: DubbingSegment,
    candidate: DubbingSegment,
    *,
    chapter_number: int,
    source_version: str,
) -> list[TTSRepairFieldChange]:
    baseline_payload = _segment_payload(baseline)
    candidate_payload = _segment_payload(candidate)
    baseline_uid = compute_segment_uid(baseline)
    candidate_uid = compute_segment_uid(candidate)
    container_hash = _content_hash(baseline_payload)
    changes: list[TTSRepairFieldChange] = []
    for field_name in sorted(set(baseline_payload) | set(candidate_payload)):
        if field_name in _IGNORED_POSITION_FIELDS:
            continue
        before = baseline_payload.get(field_name)
        after = candidate_payload.get(field_name)
        if before == after:
            continue
        policy = tts_field_policy(field_name)
        field_path = f"segments[{baseline.segment_index}].{field_name}"
        comparator_id = (
            "tts_source_text_exact_v1" if policy.immutable else "tts_derived_field_exact_v1"
        )
        locator = AuditLocator(
            target_format="tts_script",
            role="repair",
            surface="dubbing_segment",
            confidence=1.0,
            artifact=f"tts/chapter_{chapter_number}/dubbing_script",
            field=field_name,
            field_path=field_path,
            stable_node_id=baseline_uid,
            segment_uid=baseline_uid,
            segment_index=baseline.segment_index,
            container_hash=container_hash,
            chapter_number=chapter_number,
            comparator_id=comparator_id,
            expected_raw=before,
            actual_raw=after,
            expected_normalized=_normalized_value(before),
            actual_normalized=_normalized_value(after),
            source_version=source_version,
            target_version=candidate_uid,
            text_hash=_content_hash(baseline.text),
        )
        changes.append(
            TTSRepairFieldChange(
                baseline_segment_uid=baseline_uid,
                candidate_segment_uid=candidate_uid,
                display_segment_index=baseline.segment_index,
                field_path=field_path,
                before_hash=_content_hash(before),
                after_hash=_content_hash(after),
                policy=policy,
                locator=locator,
            )
        )
    return changes


def verify_tts_repair_candidate(
    baseline: DubbingScript,
    candidate: DubbingScript,
    *,
    source_text: str,
    completeness_report: ScriptCompletenessReport,
) -> TTSRepairVerification:
    """Verify a derived script without granting permission to touch prose."""

    baseline_by_index = {item.segment_index: item for item in baseline.segments}
    candidate_by_index = {item.segment_index: item for item in candidate.segments}
    unique_indices = (
        len(baseline_by_index) == len(baseline.segments)
        and len(candidate_by_index) == len(candidate.segments)
    )
    same_indices = unique_indices and set(baseline_by_index) == set(candidate_by_index)
    changes: list[TTSRepairFieldChange] = []
    if same_indices:
        for index in sorted(baseline_by_index):
            changes.extend(
                _changed_fields(
                    baseline_by_index[index],
                    candidate_by_index[index],
                    chapter_number=candidate.chapter_number,
                    source_version=candidate.source_text_hash,
                )
            )

    source_hash_ok = source_text_hash_matches(candidate.source_text_hash, source_text)
    immutable_changes = [item for item in changes if item.policy.immutable]
    source_segments_unchanged = same_indices and not immutable_changes
    unresolved = unresolved_speaker_indices(candidate)
    speaker_gate_passed = not unresolved
    validators = [
        RepairValidatorResult(
            validator_id="tts_source_hash_v1",
            passed=source_hash_ok,
            details=[f"source_text_hash={candidate.source_text_hash or '<missing>'}"],
        ),
        RepairValidatorResult(
            validator_id="tts_segment_identity_v1",
            passed=same_indices,
            details=[
                f"baseline={sorted(baseline_by_index)}",
                f"candidate={sorted(candidate_by_index)}",
            ],
        ),
        RepairValidatorResult(
            validator_id="tts_source_segment_read_only_v1",
            passed=source_segments_unchanged,
            details=[item.field_path for item in immutable_changes],
        ),
        RepairValidatorResult(
            validator_id="tts_script_completeness_v1",
            passed=completeness_report.passed,
            details=list(completeness_report.failures),
            evidence={
                "spoken_text_coverage": completeness_report.spoken_text_coverage,
                "emotion_differentiation": completeness_report.emotion_differentiation,
                "voice_assignment_coverage": completeness_report.voice_assignment_coverage,
            },
        ),
        RepairValidatorResult(
            validator_id="tts_speaker_gate_v1",
            passed=speaker_gate_passed,
            details=[f"segment_index={index}" for index in unresolved],
        ),
    ]
    failures: list[str] = []
    if not source_hash_ok:
        failures.append("配音候选的源文本哈希与当前章节不一致")
    if not same_indices:
        failures.append("配音候选的段落身份集合已变化，无法精确定位")
    if immutable_changes:
        failures.append("配音候选尝试改写正文源文本")
    failures.extend(completeness_report.failures)
    if unresolved:
        failures.append("配音候选仍有未解析说话人：" + ",".join(map(str, unresolved)))
    failures = list(dict.fromkeys(failures))
    return TTSRepairVerification(
        passed=all(item.passed for item in validators if item.required),
        source_text_hash=candidate.source_text_hash,
        baseline_script_hash=compute_dubbing_script_hash(baseline),
        candidate_script_hash=compute_dubbing_script_hash(candidate),
        changed_targets=changes,
        validators=validators,
        failures=failures,
        field_precedence=TTS_FIELD_PRECEDENCE,
    )


__all__ = [
    "TTS_FIELD_PRECEDENCE",
    "TTSFieldPolicy",
    "TTSRepairFieldChange",
    "TTSRepairVerification",
    "tts_field_policy",
    "verify_tts_repair_candidate",
]
