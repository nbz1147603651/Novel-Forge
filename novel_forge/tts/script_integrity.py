"""Stable integrity helpers for persisted dubbing scripts."""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any

from novel_forge.tts.schemas import DubbingScript, SegmentType


class DubbingScriptFreshness(str, Enum):
    """Relationship between a persisted dubbing script and the final chapter."""

    CURRENT = "current"
    STALE = "stale"
    LEGACY = "legacy"
    SOURCE_MISSING = "source_missing"


def compute_source_text_hash(text: str) -> str:
    """Return the canonical compact identity of one authoritative chapter text."""

    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]


def source_text_hash_matches(stored_hash: str, source_text: str) -> bool:
    """Accept both compact TTS hashes and full publication SHA-256 hashes.

    TTS artifacts intentionally persist a 16-character digest while the
    publication ledger stores the full digest.  Comparing the strings
    directly makes a current TTS artifact look stale at the cross-media
    boundary.  Matching either canonical representation keeps historical
    artifacts compatible without weakening the content check.
    """

    normalized = str(stored_hash or "").strip().lower()
    if not normalized:
        return False
    digest = hashlib.sha256(str(source_text or "").encode("utf-8")).hexdigest()
    return normalized in {digest, digest[:16]}


def assess_dubbing_script_freshness(
    script: DubbingScript,
    source_text: str,
) -> DubbingScriptFreshness:
    """Classify a script without trusting timestamps or file ordering.

    The source hash is the authority.  Timestamps are useful to people but do
    not prove that an artifact belongs to the current chapter; legacy scripts
    without a source identity therefore remain visible but cannot synthesize.
    """

    if not str(source_text or ""):
        return DubbingScriptFreshness.SOURCE_MISSING
    stored_hash = str(script.source_text_hash or "").strip()
    if not stored_hash:
        return DubbingScriptFreshness.LEGACY
    if source_text_hash_matches(stored_hash, source_text):
        return DubbingScriptFreshness.CURRENT
    return DubbingScriptFreshness.STALE


def dubbing_script_content_payload(script: DubbingScript) -> dict[str, Any]:
    """Return only fields whose change invalidates script-derived artifacts."""
    payload = script.model_dump(
        mode="json",
        exclude={"script_hash", "source_text_hash", "created_at"},
    )
    # Exclude internal identity fields from content-sensitivity hash, so
    # old scripts (without segment_uid) and regenerated scripts (with
    # auto-filled segment_uid) produce the same hash for identical content.
    for seg in payload.get("segments", []):
        if isinstance(seg, dict):
            seg.pop("segment_uid", None)
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        # These records explain how a decision was reached; their wording does
        # not change the approved segment/cue data consumed by synthesis.
        metadata.pop("source_reconciliation", None)
        metadata.pop("speaker_adjudication", None)
        metadata.pop("professional_script_review", None)
        metadata.pop("repair_verification", None)
    return payload


def compute_dubbing_script_hash(script: DubbingScript) -> str:
    """Compute the canonical, regeneration-stable dubbing script hash."""
    encoded = json.dumps(
        dubbing_script_content_payload(script),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def unresolved_speaker_indices(script: DubbingScript) -> tuple[int, ...]:
    """Return every spoken segment that cannot safely select a cast voice.

    Persisted audit metadata remains useful, but it is not trusted as the only
    gate.  Legacy or manually edited dialogue with an empty ``character_id``
    must never fall through to the narrator during synthesis.
    """
    audit = script.metadata.get("speaker_adjudication")
    segment_ids = {segment.segment_index for segment in script.segments}
    values: set[int] = {
        segment.segment_index
        for segment in script.segments
        if segment.segment_type in {SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}
        and not segment.character_id.strip()
    }
    if isinstance(audit, dict):
        for value in audit.get("unresolved_segment_indices", []):
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if index in segment_ids:
                values.add(index)
    professional_review = script.metadata.get("professional_script_review")
    if isinstance(professional_review, dict):
        llm_review = professional_review.get("llm_review")
        if isinstance(llm_review, dict):
            for value in llm_review.get("manual_review_segment_indices", []):
                try:
                    index = int(value)
                except (TypeError, ValueError):
                    continue
                if index in segment_ids:
                    values.add(index)
    return tuple(sorted(values))


def script_source_audit_complete(script: DubbingScript) -> bool:
    """Return whether quote roles and ambiguous speakers used the new audit path."""
    reconciliation = script.metadata.get("source_reconciliation")
    adjudication = script.metadata.get("speaker_adjudication")
    return bool(
        isinstance(reconciliation, dict)
        and reconciliation.get("status") == "passed"
        and isinstance(adjudication, dict)
        and adjudication.get("status") in {"passed", "not_needed", "needs_review"}
    )


def requires_script_source_audit(script: DubbingScript, source_text: str) -> bool:
    """Detect legacy quote-bearing scripts that must not bypass reconciliation."""
    if script_source_audit_complete(script):
        return False
    return bool(re.search(r'[\u201c\u201d\u300c\u300d\u300e\u300f"]', source_text or ""))


def compute_segment_uid(segment: Any) -> str:
    """Return a content-addressed stable identity for a dubbing segment.

    The identity is derived from the fields that materially affect TTS output
    (text, character, emotion, overrides, paralinguistic tags), excluding
    positional metadata like ``segment_index`` and ``source_paragraph``.

    Two segments with the same identity will produce the same audio and can
    share a cached take.  The identity uses the project-wide ``stable_id``
    helper so the digest is consistent with entity/canon identifiers.
    """
    from novel_forge.narrative_state.schemas import stable_id

    emotion_raw = getattr(segment, "emotion", None)
    emotion_str = emotion_raw.value if hasattr(emotion_raw, "value") else str(emotion_raw or "")

    sub_emotion_raw = getattr(segment, "sub_emotion", None)
    sub_emotion_str = (
        sub_emotion_raw.value
        if sub_emotion_raw is not None and hasattr(sub_emotion_raw, "value")
        else str(sub_emotion_raw or "")
    )

    ptags = getattr(segment, "paralinguistic_tags", None) or []
    ptags_payload = "|".join(
        json.dumps(
            t.model_dump(mode="json", exclude_none=True) if hasattr(t, "model_dump") else t,
            ensure_ascii=False,
            sort_keys=True,
        )
        for t in ptags
    )

    stype = getattr(segment, "segment_type", None)
    stype_str = stype.value if hasattr(stype, "value") else str(stype or "")

    identity = "|".join(
        [
            str(getattr(segment, "text", "") or ""),
            str(getattr(segment, "spoken_text", "") or ""),
            str(getattr(segment, "character_id", "") or ""),
            stype_str,
            emotion_str,
            sub_emotion_str,
            str(getattr(segment, "speed_override", None) or ""),
            str(getattr(segment, "vol_override", None) or ""),
            str(getattr(segment, "pitch_override", None) or ""),
            str(getattr(segment, "tone_hint", "") or ""),
            str(getattr(segment, "narrator_distance", "") or ""),
            str(getattr(segment, "language_code", "auto") or "auto"),
            str(getattr(segment, "language_boost", "auto") or "auto"),
            ptags_payload,
            "|".join(str(w or "") for w in (getattr(segment, "stress_words", None) or [])),
        ]
    )
    return stable_id("seg", identity)


def refresh_segment_uid(segment: Any) -> Any:
    """Return ``segment`` with a freshly computed content-addressed uid.

    ``model_copy(update=...)`` bypasses validators, so after copying a segment
    with uid-relevant field updates (text/character/emotion/overrides/tags)
    the uid would go stale.  Call this on the copy to restore the invariant
    that ``segment_uid`` always matches the current content.  Returns the
    original object when the uid is already fresh.
    """
    uid = compute_segment_uid(segment)
    if str(getattr(segment, "segment_uid", "") or "") == uid:
        return segment
    return segment.model_copy(update={"segment_uid": uid})


__all__ = [
    "DubbingScriptFreshness",
    "assess_dubbing_script_freshness",
    "compute_dubbing_script_hash",
    "compute_segment_uid",
    "compute_source_text_hash",
    "dubbing_script_content_payload",
    "refresh_segment_uid",
    "requires_script_source_audit",
    "script_source_audit_complete",
    "source_text_hash_matches",
    "unresolved_speaker_indices",
]
