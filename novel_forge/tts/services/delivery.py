"""Canonical final-delivery gate for chapter audio artifacts."""

from __future__ import annotations

from pathlib import Path

from novel_forge.tts.schemas import ChapterAudioResult


class TTSDeliveryNotReadyError(RuntimeError):
    """Raised when a consumer attempts to export or publish blocked audio."""

    def __init__(self, chapter_number: int, reasons: tuple[str, ...]) -> None:
        self.chapter_number = chapter_number
        self.reasons = reasons
        joined = ", ".join(reasons) if reasons else "delivery_not_ready"
        super().__init__(f"Chapter {chapter_number} audio is not delivery-ready: {joined}")


def delivery_blocking_reasons(
    audio_result: ChapterAudioResult,
    *,
    require_artifact: bool = True,
) -> tuple[str, ...]:
    """Return the one canonical set of reasons blocking external consumption."""

    reasons = list(audio_result.delivery_blocking_reasons)
    if not audio_result.is_complete:
        reasons.append("incomplete_synthesis")
    if bool(audio_result.metadata.get("assembly_stale")):
        reasons.append("assembly_stale")
    if require_artifact:
        path = (
            Path(audio_result.assembled_audio_path) if audio_result.assembled_audio_path else None
        )
        try:
            artifact_exists = bool(path and path.is_file() and path.stat().st_size > 0)
        except OSError:
            artifact_exists = False
        if not artifact_exists:
            reasons.append("missing_assembled_audio")
    if not audio_result.delivery_ready and not reasons:
        reasons.append("delivery_not_ready")
    return tuple(dict.fromkeys(reasons))


def require_delivery_ready(
    audio_result: ChapterAudioResult,
    *,
    require_artifact: bool = True,
) -> ChapterAudioResult:
    """Return the result only when every final-delivery invariant holds."""

    reasons = delivery_blocking_reasons(audio_result, require_artifact=require_artifact)
    if not audio_result.delivery_ready or reasons:
        raise TTSDeliveryNotReadyError(audio_result.chapter_number, reasons)
    return audio_result
