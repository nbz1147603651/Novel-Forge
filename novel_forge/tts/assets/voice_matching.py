"""Explainable character-to-voice matching shared by library and catalog flows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

_GENDER_ALIASES = {
    "男": "male",
    "男性": "male",
    "男声": "male",
    "male": "male",
    "man": "male",
    "女": "female",
    "女性": "female",
    "女声": "female",
    "female": "female",
    "woman": "female",
    "中性": "neutral",
    "未知": "neutral",
    "neutral": "neutral",
}

# Language code normalization: map various aliases to a base language code.
_LANGUAGE_ALIASES: dict[str, str] = {
    "zh": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "chinese": "zh",
    "mandarin": "zh",
    "yue": "yue",
    "zh-yue": "yue",
    "cantonese": "yue",
    "en": "en",
    "english": "en",
    "ja": "ja",
    "japanese": "ja",
    "ko": "ko",
    "korean": "ko",
    "vi": "vi",
    "vietnamese": "vi",
    "th": "th",
    "thai": "th",
    "id": "id",
    "indonesian": "id",
    "es": "es",
    "spanish": "es",
    "fr": "fr",
    "french": "fr",
    "de": "de",
    "german": "de",
    "ru": "ru",
    "russian": "ru",
    "ar": "ar",
    "arabic": "ar",
    "pt": "pt",
    "portuguese": "pt",
    "tr": "tr",
    "turkish": "tr",
    "hi": "hi",
    "hindi": "hi",
    "ms": "ms",
    "malay": "ms",
    "fil": "fil",
    "filipino": "fil",
}

# Languages considered compatible (e.g. zh and yue are both Chinese variants).
_LANGUAGE_COMPAT_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"zh", "yue"}),
)

_EXPRESSIVE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("沉稳克制", ("沉稳", "克制", "冷静", "calm", "restrained", "steady")),
    ("温柔亲和", ("温柔", "柔和", "亲和", "gentle", "soft", "warm")),
    ("活泼明亮", ("活泼", "开朗", "明亮", "清亮", "lively", "bright", "cheerful")),
    ("低沉厚实", ("低沉", "浑厚", "厚实", "deep", "resonant", "rich")),
    ("沙哑粗粝", ("沙哑", "粗粝", "颗粒", "hoarse", "raspy", "gritty")),
    ("清脆轻盈", ("清脆", "轻盈", "甜", "crisp", "light", "sweet")),
    ("威严有压迫感", ("威严", "权威", "压迫", "authoritative", "commanding")),
    ("冷峻疏离", ("冷峻", "冷淡", "疏离", "cold", "distant", "aloof")),
    ("机敏狡黠", ("机敏", "狡黠", "戏谑", "witty", "sly", "playful")),
    ("稚嫩天真", ("稚嫩", "天真", "童真", "innocent", "childlike")),
    ("快节奏表达", ("语速快", "快速", "急促", "短句", "fast", "brisk", "rapid")),
    ("慢节奏表达", ("语速慢", "缓慢", "停顿", "留白", "slow", "measured", "pause")),
)


@dataclass(frozen=True)
class VoiceMatchAssessment:
    """Auditable result for one character and one reusable voice identity."""

    score: float
    hard_match: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    expressive_matches: int = 0


def normalize_gender(value: object) -> str:
    """Normalize known gender labels without guessing from unrelated prose."""
    text = str(value or "").strip().lower()
    return _GENDER_ALIASES.get(text, text)


def normalize_language(value: object) -> str:
    """Normalize a language code or name to a base language code.

    Returns '' if the value is empty or unrecognized.
    """
    text = str(value or "").strip().lower().replace("_", "-")
    if not text or text == "auto":
        return ""
    return _LANGUAGE_ALIASES.get(text, text.split("-")[0] if "-" in text else text)


def languages_compatible(lang_a: str, lang_b: str) -> bool:
    """Return whether two normalized language codes are compatible.

    Languages in the same compatibility group (e.g. zh and yue) are compatible.
    """
    if not lang_a or not lang_b:
        return True
    if lang_a == lang_b:
        return True
    for group in _LANGUAGE_COMPAT_GROUPS:
        if lang_a in group and lang_b in group:
            return True
    return False


def age_bucket(value: object) -> str:
    """Normalize explicit age hints into casting buckets."""
    text = str(value or "").strip().lower()
    numeric = re.search(r"(?<!\d)(\d{1,3})(?!\d)", text)
    if numeric:
        age = int(numeric.group(1))
        if age < 16:
            return "child"
        if age < 30:
            return "young"
        if age < 50:
            return "middle"
        return "senior"
    buckets = (
        ("child", ("儿童", "少儿", "孩童", "孩子", "男孩", "女孩", "child")),
        ("young", ("少年", "少女", "青年", "年轻", "young", "teen")),
        ("middle", ("中年", "壮年", "middle-aged", "middle aged")),
        ("senior", ("老年", "老者", "老人", "长者", "elder", "senior", "old")),
    )
    for bucket, tokens in buckets:
        if any(token in text for token in tokens):
            return bucket
    return ""


def assess_voice_match(
    character: Mapping[str, Any],
    voice: Mapping[str, Any] | object,
    *,
    matching_profile: Mapping[str, Any] | None = None,
) -> VoiceMatchAssessment:
    """Evaluate hard demographic constraints before expressive similarity.

    Gender, age, and language become non-negotiable whenever both sides expose
    reliable metadata.  Personality, timbre, cadence and role then rank the
    survivors.

    Args:
        character: Character profile dict.
        voice: Voice metadata dict or object.
        matching_profile: Optional platform-specific matching profile with
            scoring_weights and expressive_group_overrides.
    """
    # Extract platform weights (fallback to defaults)
    weights = _extract_scoring_weights(matching_profile)
    expressive_groups = _merge_expressive_groups(matching_profile)

    character_gender = normalize_gender(character.get("gender"))
    character_age = age_bucket(character.get("age"))
    voice_gender = normalize_gender(_voice_value(voice, "gender"))
    voice_age = age_bucket(_voice_value(voice, "age_hint") or _voice_value(voice, "age"))
    character_role = str(character.get("role") or "").strip().lower()
    voice_role = str(_voice_value(voice, "role") or "").strip().lower()

    # Language hard constraint: if the voice exposes a language tag and the
    # project/character has a target language, they must be compatible.
    voice_language = normalize_language(_voice_value(voice, "language"))
    project_language = normalize_language(
        character.get("project_language") or character.get("language")
    )

    reasons: list[str] = []
    warnings: list[str] = []
    hard_match = True
    score = weights["base"]

    # ── Language hard gate ────────────────────────────────────────────────
    if voice_language and project_language:
        if languages_compatible(project_language, voice_language):
            score += weights["language"]
            reasons.append("音色语言与项目语言一致")
        else:
            hard_match = False
            warnings.append(
                f"音色语言({voice_language})与项目语言({project_language})不匹配，已禁止自动分配"
            )

    if character_gender and character_gender != "neutral":
        if voice_gender and voice_gender != "neutral":
            if voice_gender == character_gender:
                score += weights["gender"]
                reasons.append("性别硬约束一致")
            else:
                hard_match = False
                warnings.append("性别与角色档案冲突，已禁止自动分配")
        else:
            score += weights["gender"] * 0.17  # ~0.05 partial credit
            warnings.append("候选音色缺少性别标签，需要试听确认")
    else:
        warnings.append("角色档案缺少明确性别，无法执行性别硬校验")

    if character_age:
        if voice_age:
            if voice_age == character_age:
                score += weights["age"]
                reasons.append("年龄段硬约束一致")
            else:
                hard_match = False
                warnings.append("年龄感与角色档案冲突，已禁止自动分配")
        else:
            score += weights["age"] * 0.5  # partial credit
            warnings.append("候选音色年龄感未知，建议试听确认")
    else:
        warnings.append("角色档案缺少明确年龄，无法执行年龄硬校验")

    character_text = _character_expression_text(character)
    voice_text = _voice_expression_text(voice)
    desired_groups = [
        (label, tokens)
        for label, tokens in expressive_groups
        if any(token in character_text for token in tokens)
    ]
    matched_labels = [
        label for label, tokens in desired_groups if any(token in voice_text for token in tokens)
    ]
    if desired_groups:
        score += weights["expressive"] * (len(matched_labels) / len(desired_groups))
        if matched_labels:
            reasons.append("角色特质吻合：" + "、".join(matched_labels[:3]))
        if len(matched_labels) < len(desired_groups):
            missing = [label for label, _ in desired_groups if label not in matched_labels]
            warnings.append("仍需试听确认：" + "、".join(missing[:3]))
    else:
        warnings.append("角色档案缺少可判断的音色气质或说话节奏")

    if character_role and voice_role and character_role == voice_role:
        score += weights["role"]
        reasons.append("角色定位与音色用途一致")

    if not hard_match:
        score = min(score, 0.25)
    return VoiceMatchAssessment(
        score=round(max(0.0, min(1.0, score)), 2),
        hard_match=hard_match,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        expressive_matches=len(matched_labels),
    )


def designed_voice_assessment(character: Mapping[str, Any]) -> VoiceMatchAssessment:
    """Explain confidence for a voice generated directly from the role brief."""
    reasons: list[str] = []
    warnings: list[str] = []
    score = 0.68
    if normalize_gender(character.get("gender")) not in {"", "neutral"}:
        score += 0.09
        reasons.append("设计简报锁定角色性别")
    else:
        warnings.append("角色性别缺失，设计结果需重点试听")
    if age_bucket(character.get("age")):
        score += 0.08
        reasons.append("设计简报锁定角色年龄感")
    else:
        warnings.append("角色年龄缺失，设计结果需重点试听")
    if _character_expression_text(character):
        score += 0.10
        reasons.append("已纳入性格、声线与说话节奏")
    else:
        warnings.append("缺少性格或说话习惯，专属音色区分度有限")
    hints = character.get("tts_voice_hints")
    if isinstance(hints, Mapping) and any(value not in (None, "", []) for value in hints.values()):
        score += 0.05
        reasons.append("已纳入结构化音色提示")
    return VoiceMatchAssessment(
        score=round(min(1.0, score), 2),
        hard_match=True,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        expressive_matches=1 if _character_expression_text(character) else 0,
    )


def with_semantic_evidence(
    assessment: VoiceMatchAssessment,
    semantic_score: float | None,
) -> VoiceMatchAssessment:
    """Blend vector similarity into an already hard-filtered assessment."""
    if semantic_score is None:
        return assessment
    similarity = max(0.0, min(1.0, float(semantic_score)))
    reasons = list(assessment.reasons)
    warnings = list(assessment.warnings)
    reasons.append(f"音色库语义相似度 {similarity:.0%}")
    if similarity < 0.68:
        warnings.append("语义候选信心一般，建议试听或交由 LLM 复核")
    blended = 0.65 * assessment.score + 0.35 * similarity
    return VoiceMatchAssessment(
        score=round(max(0.0, min(1.0, blended)), 2),
        hard_match=assessment.hard_match,
        reasons=tuple(dict.fromkeys(reasons)),
        warnings=tuple(dict.fromkeys(warnings)),
        expressive_matches=assessment.expressive_matches,
    )


def _voice_value(voice: Mapping[str, Any] | object, key: str) -> Any:
    if isinstance(voice, Mapping):
        return voice.get(key)
    return getattr(voice, key, None)


def _character_expression_text(character: Mapping[str, Any]) -> str:
    parts = [str(character.get(key) or "") for key in ("personality", "voice", "voice_description")]
    hints = character.get("tts_voice_hints")
    if isinstance(hints, Mapping):
        parts.extend(str(value or "") for value in hints.values())
    return " ".join(parts).lower()


def _voice_expression_text(voice: Mapping[str, Any] | object) -> str:
    parts: list[str] = []
    for key in (
        "name",
        "voice_id",
        "personality",
        "voice_description",
        "voice_design_prompt",
        "trait",
    ):
        parts.append(str(_voice_value(voice, key) or ""))
    tags = _voice_value(voice, "tags")
    if isinstance(tags, (list, tuple, set)):
        parts.extend(str(tag) for tag in tags)
    elif tags:
        parts.append(str(tags))
    return " ".join(parts).lower()


# ─── Platform profile helpers ─────────────────────────────────────────────

_DEFAULT_WEIGHTS: dict[str, float] = {
    "base": 0.15,
    "language": 0.05,
    "gender": 0.30,
    "age": 0.20,
    "expressive": 0.25,
    "role": 0.10,
    "semantic_blend": 0.35,
}


def _extract_scoring_weights(
    matching_profile: Mapping[str, Any] | None,
) -> dict[str, float]:
    """Extract scoring weights from matching profile, with defaults."""
    if not matching_profile:
        return dict(_DEFAULT_WEIGHTS)
    weights_raw = matching_profile.get("scoring_weights")
    if not isinstance(weights_raw, Mapping):
        return dict(_DEFAULT_WEIGHTS)
    weights = dict(_DEFAULT_WEIGHTS)
    for key in _DEFAULT_WEIGHTS:
        value = weights_raw.get(key)
        if isinstance(value, (int, float)):
            weights[key] = float(value)
    return weights


def _merge_expressive_groups(
    matching_profile: Mapping[str, Any] | None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Merge platform-specific expressive group overrides with defaults.

    Platform overrides replace the token list for matching group labels.
    """
    if not matching_profile:
        return _EXPRESSIVE_GROUPS
    overrides_raw = matching_profile.get("expressive_group_overrides")
    if not isinstance(overrides_raw, Mapping) or not overrides_raw:
        return _EXPRESSIVE_GROUPS
    merged: list[tuple[str, tuple[str, ...]]] = []
    for label, default_tokens in _EXPRESSIVE_GROUPS:
        override_tokens = overrides_raw.get(label)
        if isinstance(override_tokens, (list, tuple)) and override_tokens:
            merged.append((label, tuple(str(t) for t in override_tokens)))
        else:
            merged.append((label, default_tokens))
    return tuple(merged)
