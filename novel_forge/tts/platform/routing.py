"""Per-segment and per-language routing helpers."""

from __future__ import annotations

from dataclasses import dataclass

from novel_forge.tts.platform.schemas import AudioExecutionPlan, AudioLanguageRoute
from novel_forge.tts.schemas import DubbingSegment, LanguageRun


@dataclass(frozen=True)
class RoutedSpeechUnit:
    segment_index: int
    language: str
    text: str
    start_char: int
    end_char: int
    asr_plugin_id: str
    aligner_plugin_id: str
    fallback_plugin_ids: tuple[str, ...]


def _plan_route(plan: AudioExecutionPlan, language: str) -> AudioLanguageRoute | None:
    normalized = language.lower().replace("_", "-")
    return next(
        (
            route
            for route in plan.language_routes
            if route.language.lower().replace("_", "-") == normalized
        ),
        None,
    )


def route_segment(segment: DubbingSegment, plan: AudioExecutionPlan) -> list[RoutedSpeechUnit]:
    """Route each explicit language run while retaining one segment identity."""

    runs = segment.language_runs or [
        LanguageRun(
            language=segment.language_code or "auto",
            text=segment.text,
            start_char=0,
            end_char=len(segment.text),
        )
    ]
    routed: list[RoutedSpeechUnit] = []
    for run in runs:
        route = _plan_route(plan, run.language)
        routed.append(
            RoutedSpeechUnit(
                segment_index=segment.segment_index,
                language=run.language,
                text=run.text,
                start_char=run.start_char,
                end_char=run.end_char,
                asr_plugin_id=route.asr_plugin_id if route else "",
                aligner_plugin_id=route.aligner_plugin_id if route else "",
                fallback_plugin_ids=tuple(route.fallback_plugin_ids if route else ()),
            )
        )
    return routed
