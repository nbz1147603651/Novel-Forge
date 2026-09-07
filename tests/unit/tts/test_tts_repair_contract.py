from __future__ import annotations

from novel_forge.tts.pipeline.script_phases import ScriptCompletenessReport
from novel_forge.tts.repair_contract import verify_tts_repair_candidate
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    EmotionTag,
    SegmentType,
)
from novel_forge.tts.script_integrity import compute_source_text_hash


def _script(source_text: str) -> DubbingScript:
    return DubbingScript(
        chapter_number=2,
        source_text_hash=compute_source_text_hash(source_text),
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="夜雨落下。",
                spoken_text="夜雨落下。",
            ),
            DubbingSegment(
                segment_index=3,
                segment_type=SegmentType.DIALOGUE,
                character_id="char_a",
                text="我会回来。",
                spoken_text="我会回来。",
            ),
        ],
    )


def _passing_report() -> ScriptCompletenessReport:
    return ScriptCompletenessReport(
        passed=True,
        spoken_text_coverage=1.0,
        emotion_differentiation=0.5,
        voice_assignment_coverage=1.0,
    )


def test_tts_changes_are_anchored_by_baseline_uid_and_field_path() -> None:
    source = "夜雨落下。我会回来。"
    baseline = _script(source)
    candidate = baseline.model_copy(
        update={
            "segments": [
                baseline.segments[0].model_copy(
                    update={
                        "spoken_text": "夜雨，慢慢落下。",
                        "emotion": EmotionTag.SAD,
                    }
                ),
                baseline.segments[1],
            ]
        }
    )

    result = verify_tts_repair_candidate(
        baseline,
        candidate,
        source_text=source,
        completeness_report=_passing_report(),
    )

    assert result.passed is True
    by_field = {item.field_path: item for item in result.changed_targets}
    spoken = by_field["segments[0].spoken_text"]
    assert spoken.baseline_segment_uid == baseline.segments[0].segment_uid
    assert spoken.candidate_segment_uid != spoken.baseline_segment_uid
    assert spoken.locator.segment_uid == baseline.segments[0].segment_uid
    assert spoken.locator.segment_index == 0
    assert spoken.locator.comparator_id == "tts_derived_field_exact_v1"
    assert spoken.locator.expected_raw == "夜雨落下。"
    assert spoken.locator.actual_raw == "夜雨，慢慢落下。"
    assert spoken.policy.precedence[:2] == ["professional_review", "spoken_rewrite"]
    assert by_field["segments[0].emotion"].policy.authority == "emotion_label"


def test_tts_source_hash_and_source_segment_are_hard_gates() -> None:
    source = "夜雨落下。我会回来。"
    baseline = _script(source)
    changed_source = baseline.model_copy(
        update={
            "segments": [
                baseline.segments[0].model_copy(update={"text": "夜雪落下。"}),
                baseline.segments[1],
            ]
        }
    )

    mutation = verify_tts_repair_candidate(
        baseline,
        changed_source,
        source_text=source,
        completeness_report=_passing_report(),
    )
    stale_hash = verify_tts_repair_candidate(
        baseline,
        baseline.model_copy(update={"source_text_hash": "deadbeef"}),
        source_text=source,
        completeness_report=_passing_report(),
    )

    assert mutation.passed is False
    text_change = next(
        item for item in mutation.changed_targets if item.field_path == "segments[0].text"
    )
    assert text_change.policy.immutable is True
    assert text_change.locator.comparator_id == "tts_source_text_exact_v1"
    assert stale_hash.passed is False
    assert any(
        item.validator_id == "tts_source_hash_v1" and not item.passed
        for item in stale_hash.validators
    )


def test_tts_candidate_requires_completeness_and_resolved_speakers() -> None:
    source = "夜雨落下。我会回来。"
    baseline = _script(source)
    unresolved = baseline.model_copy(
        update={
            "segments": [
                baseline.segments[0],
                baseline.segments[1].model_copy(update={"character_id": ""}),
            ]
        }
    )
    failed_report = ScriptCompletenessReport(
        passed=False,
        spoken_text_coverage=0.5,
        emotion_differentiation=0.0,
        voice_assignment_coverage=0.0,
        failures=["spoken_text 覆盖率不足"],
    )

    result = verify_tts_repair_candidate(
        baseline,
        unresolved,
        source_text=source,
        completeness_report=failed_report,
    )

    assert result.passed is False
    failed = {item.validator_id for item in result.validators if not item.passed}
    assert failed >= {"tts_script_completeness_v1", "tts_speaker_gate_v1"}
    assert "未解析说话人" in "；".join(result.failures)
