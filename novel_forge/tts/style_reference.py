"""Reference dubbing-script analysis for safe, abstract style imitation.

The reference text is treated as untrusted, transient input.  Only aggregate
metrics and abstract performance rules are persisted; verbatim lines never
cross the style-profile boundary.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from collections.abc import Callable
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.logger import get_logger
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.tts.schemas import DubbingStyleProfile

_log = get_logger("tts.style_reference")

_TIMECODE_RE = re.compile(
    r"^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}[,.]\d{3}\s*--?>\s*"
    r"(?:\d{1,2}:)?\d{1,2}:\d{2}[,.]\d{3}.*$"
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;…])")
_CUE_RE = re.compile(r"(?:\[|【|\()\s*(?:BGM|音乐|音效|环境|声场|转场|SFX)[^\]】)]*(?:\]|】|\))", re.I)
_DIALOGUE_RE = re.compile(r"(?:^[^\n：:]{1,16}[：:]|[“\u300c\u300e\"])", re.M)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def normalize_reference_script(text: str, *, max_chars: int) -> str:
    """Normalize common TXT/Markdown/SRT/VTT input without retaining metadata noise."""

    lines: list[str] = []
    for raw_line in str(text or "").replace("\ufeff", "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.upper() == "WEBVTT" or _TIMECODE_RE.match(line):
            continue
        if line.isdecimal() and len(line) <= 8:
            continue
        lines.append(line)
    normalized = "\n".join(lines)
    return normalized[: max(1, max_chars)].strip()


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _deterministic_metrics(text: str) -> dict[str, float | int | str]:
    sentences = [item.strip() for item in _SENTENCE_SPLIT_RE.split(text) if item.strip()]
    lengths = [len(re.sub(r"\s+", "", item)) for item in sentences]
    char_count = len(re.sub(r"\s+", "", text))
    return {
        "character_count": char_count,
        "sentence_count": len(sentences),
        "mean_sentence_chars": round(statistics.fmean(lengths), 2) if lengths else 0.0,
        "median_sentence_chars": round(statistics.median(lengths), 2) if lengths else 0.0,
        "short_sentence_ratio": round(
            sum(length <= 18 for length in lengths) / max(1, len(lengths)), 3
        ),
        "dialogue_line_ratio": round(
            sum(bool(_DIALOGUE_RE.search(line)) for line in text.splitlines())
            / max(1, len(text.splitlines())),
            3,
        ),
        "ellipsis_per_k_chars": round(text.count("…") * 1000 / max(1, char_count), 2),
        "dash_per_k_chars": round(
            (text.count("—") + text.count("——")) * 1000 / max(1, char_count), 2
        ),
        "question_exclamation_per_k_chars": round(
            sum(text.count(mark) for mark in "？！?!") * 1000 / max(1, char_count), 2
        ),
        "explicit_sound_cue_count": len(_CUE_RE.findall(text)),
        "language": "zh" if len(_CJK_RE.findall(text)) >= max(1, char_count // 5) else "auto",
    }


def _deterministic_profile(
    text: str,
    *,
    source_name: str,
    source_hash: str,
) -> DubbingStyleProfile:
    metrics = _deterministic_metrics(text)
    mean_chars = float(metrics["mean_sentence_chars"])
    short_ratio = float(metrics["short_sentence_ratio"])
    dialogue_ratio = float(metrics["dialogue_line_ratio"])
    ellipsis_density = float(metrics["ellipsis_per_k_chars"])
    cue_count = int(metrics["explicit_sound_cue_count"])

    rhythm_rules = [
        (
            "以短意群和快速换气组织句子，避免连续长复句。"
            if mean_chars and mean_chars <= 22
            else "以完整意群组织中等长度句子，在语义转折处自然换气。"
        ),
        f"短句占比参考值约为 {short_ratio:.0%}，只模仿节奏密度，不复制措辞。",
    ]
    pause_rules = [
        (
            "适度保留省略式停顿，用于犹疑、余韵和情绪换挡。"
            if ellipsis_density >= 3.0
            else "停顿以语义边界为主，避免用省略号制造过度表演。"
        )
    ]
    narration_traits = [
        "旁白保持可连续朗读的呼吸长度，重心落在画面与行动锚点。",
        "段尾形成清晰落点，不用机械等长句。",
    ]
    dialogue_traits = [
        (
            "对白占比较高；优先保留人物轮次、口头节奏和潜台词。"
            if dialogue_ratio >= 0.35
            else "对白点到即止；与旁白之间保持明确的声腔层次。"
        )
    ]
    sound_rules = [
        (
            "参考稿有显式声音提示；沿用其疏密逻辑，把环境底床、剧情音效和 BGM 分层。"
            if cue_count
            else "参考稿缺少显式声音提示；仅模仿语言节奏，声音设计仍以场景证据为准。"
        )
    ]
    confidence = min(0.75, 0.35 + int(metrics["character_count"]) / 20_000)
    return DubbingStyleProfile(
        profile_id=f"dsp_{source_hash[:16]}",
        source_name=source_name.strip()[:200],
        source_hash=source_hash,
        language=str(metrics["language"]),
        confidence=round(confidence, 3),
        analysis_mode="deterministic",
        narration_traits=narration_traits,
        dialogue_traits=dialogue_traits,
        rhythm_rules=rhythm_rules,
        pause_rules=pause_rules,
        performance_direction_rules=[
            "表演指导写成可执行动作：气息、力度、距离、重音与停顿，不写抽象赞美词。"
        ],
        sound_design_rules=sound_rules,
        forbidden_tendencies=[
            "不得复制参考稿原句、角色名、专有名词或剧情事实。",
            "不得让参考风格覆盖当前作品的人物声纹、事实与叙述视角。",
        ],
        metrics=metrics,
    )


def _abstract_rules(values: object, source_text: str, *, limit: int = 8) -> list[str]:
    """Keep compact abstract rules and reject accidental verbatim source lines."""

    if not isinstance(values, list):
        return []
    rules: list[str] = []
    compact_source = re.sub(r"\s+", "", source_text)
    for value in values:
        rule = re.sub(r"\s+", " ", str(value or "")).strip()[:240]
        compact_rule = re.sub(r"\s+", "", rule)
        if not rule or (len(compact_rule) >= 8 and compact_rule in compact_source):
            continue
        rules.append(rule)
        if len(rules) >= limit:
            break
    return list(dict.fromkeys(rules))


async def analyze_dubbing_style_reference(
    reference_text: str,
    *,
    source_name: str,
    router: ModelRouter,
    builder: PromptBuilder,
    settings: Settings,
    on_step: Callable[[str, dict[str, Any]], None] | None = None,
) -> DubbingStyleProfile:
    """Analyze an uploaded script into a reusable, non-copying style profile."""

    max_chars = int(getattr(settings, "tts_style_reference_max_chars", 30_000))
    normalized = normalize_reference_script(reference_text, max_chars=max_chars)
    minimum_chars = int(getattr(settings, "tts_style_reference_min_chars", 160))
    if len(normalized) < minimum_chars:
        raise ValueError(f"参考配音脚本有效内容不足 {minimum_chars} 字，无法形成稳定风格画像")

    source_hash = _source_hash(normalized)
    fallback = _deterministic_profile(
        normalized,
        source_name=source_name,
        source_hash=source_hash,
    )
    if on_step is not None:
        on_step(
            "tts_style_reference_analysis_start",
            {"source_name": source_name, "source_chars": len(normalized), "source_hash": source_hash[:16]},
        )

    if not bool(getattr(settings, "tts_style_reference_llm_enabled", True)):
        return fallback

    try:
        from novel_forge.model_runtime import StructuredModelService

        service = StructuredModelService(
            router=router,
            builder=builder,
            on_step=on_step or (lambda _event, _data: None),
            settings=settings,
        )
        response = await service.call_with_retry(
            TaskType.TTS_ANALYZE_DUBBING_STYLE,
            {
                "stage_cards": {
                    "reference_script": normalized,
                    "source_name": source_name,
                    "deterministic_metrics": fallback.metrics,
                }
            },
            max_tokens=2400,
            temperature=float(getattr(settings, "tts_style_reference_temperature", 0.2)),
            required_keys=(
                "language",
                "confidence",
                "narration_traits",
                "dialogue_traits",
                "rhythm_rules",
                "pause_rules",
                "performance_direction_rules",
                "sound_design_rules",
                "forbidden_tendencies",
            ),
            max_retries=2,
        )
        if not isinstance(response, dict):
            raise TypeError("风格分析模型返回了非对象 JSON")
        profile = fallback.model_copy(
            update={
                "language": str(response.get("language") or fallback.language)[:32],
                "confidence": max(0.0, min(1.0, float(response.get("confidence") or 0.0))),
                "analysis_mode": "hybrid",
                "narration_traits": _abstract_rules(
                    response.get("narration_traits"), normalized
                )
                or fallback.narration_traits,
                "dialogue_traits": _abstract_rules(
                    response.get("dialogue_traits"), normalized
                )
                or fallback.dialogue_traits,
                "rhythm_rules": _abstract_rules(response.get("rhythm_rules"), normalized)
                or fallback.rhythm_rules,
                "pause_rules": _abstract_rules(response.get("pause_rules"), normalized)
                or fallback.pause_rules,
                "performance_direction_rules": _abstract_rules(
                    response.get("performance_direction_rules"), normalized
                )
                or fallback.performance_direction_rules,
                "sound_design_rules": _abstract_rules(
                    response.get("sound_design_rules"), normalized
                )
                or fallback.sound_design_rules,
                "forbidden_tendencies": list(
                    dict.fromkeys(
                        [
                            *fallback.forbidden_tendencies,
                            *_abstract_rules(response.get("forbidden_tendencies"), normalized),
                        ]
                    )
                )[:10],
            }
        )
    except Exception as exc:
        _log.warning("Reference dubbing style analysis degraded to deterministic mode: %s", exc)
        profile = fallback

    if on_step is not None:
        on_step(
            "tts_style_reference_analysis_complete",
            {
                "profile_id": profile.profile_id,
                "analysis_mode": profile.analysis_mode,
                "confidence": profile.confidence,
            },
        )
    return profile


__all__ = ["analyze_dubbing_style_reference", "normalize_reference_script"]
