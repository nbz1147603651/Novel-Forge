"""Platform depth-adaptation projections for 映界 generation prompts.

Batch 2 produces *semantic-level intermediate prompts* (platform-neutral).
This module projects one intermediate prompt onto each visual platform
deterministically — one rewrite, many projections:

- ``project_prompt_for_platform`` — budget truncation, camera-vocabulary
  mapping, negative-policy composition and capability gating; the prompt
  *structure* is selected by the platform profile's ``structure.kind`` via
  a renderer registry (``flat`` default, ``h3_timeline`` for MiniMax H3).
  Adding a future platform's prompt grammar = JSON profile + one renderer
  registered in ``register_structure_renderer``;
- ``detect_real_face_dependency`` / ``apply_ark_face_compliance`` — 方舟
  real-face compliance branch (trusted actor asset vs virtual human);
- ``select_platform_for_shot`` — capability-driven routing over the provider
  catalog features;
- ``estimate_generation_cost`` — deterministic cost estimation consumed by the
  job ledger and run-plan nodes.

All functions are pure state transformations, fully unit-testable without
network or provider access.

Author: novel-forge
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from novel_forge.film.platform_profiles import _normalize_provider, load_platform_profile
from novel_forge.film.providers.catalog import FILM_PROVIDER_CATALOG

# ─── Prompt projection ────────────────────────────────────────────────────────

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])")


@dataclass(frozen=True)
class PlatformPromptProjection:
    """One platform-specialized projection of an intermediate prompt.

    ``flags`` carries renderer-level directives for the caller/provider,
    e.g. ``disable_prompt_optimizer`` when a structured prompt must pass
    through the provider untouched (MiniMax H3 timeline prompts).
    """

    provider_id: str
    prompt: str
    negative_prompt: str
    truncated: bool = False
    capability_gates: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    flags: dict[str, bool] = field(default_factory=dict)


def _truncate_by_sentence_budget(text: str, budget: int) -> tuple[str, bool]:
    """Deterministically truncate ``text`` at sentence boundaries."""
    if len(text) <= budget:
        return text, False
    sentences = [item for item in _SENTENCE_SPLIT.split(text) if item]
    kept: list[str] = []
    total = 0
    for sentence in sentences:
        if total + len(sentence) > budget:
            break
        kept.append(sentence)
        total += len(sentence)
    if not kept:
        # Single overlong sentence: hard cut, keep deterministic prefix.
        return text[:budget], True
    return "".join(kept), True


def _compose_negative(intermediate: dict[str, Any], profile: dict[str, Any]) -> str:
    """Merge platform-neutral negative terms with the platform's required set."""
    negative_policy = profile.get("negative_policy", {}) or {}
    negative_budget = int((profile.get("char_budget", {}) or {}).get("negative_zh", 200))
    negative = str(intermediate.get("negative_prompt", "") or "")
    required_terms = [str(item) for item in negative_policy.get("required_terms", [])]
    existing = [item.strip() for item in re.split(r"[,，、;；]", negative) if item.strip()]
    merged = existing + [term for term in required_terms if term not in existing]
    negative = "，".join(merged)
    negative, _ = _truncate_by_sentence_budget(negative, negative_budget)
    return negative


def _capability_gates(
    intermediate: dict[str, Any], profile: dict[str, Any], provider_id: str
) -> tuple[dict[str, bool], list[str]]:
    """Surface capability demands the platform cannot satisfy."""
    switches = profile.get("capability_switches", {}) or {}
    demand_map = {
        "requires_multi_shot": "multi_shot",
        "requires_native_audio": "native_audio",
        "requires_camera_command": "camera_command",
        "requires_multi_image_reference": "multi_image_reference",
    }
    gates: dict[str, bool] = {}
    notes: list[str] = []
    for demand_key, switch_key in demand_map.items():
        if not intermediate.get(demand_key):
            continue
        supported = bool(switches.get(switch_key, False))
        gates[switch_key] = supported
        if not supported:
            notes.append(f"能力 {switch_key} 不被 {provider_id} 支持，需要改道或降级")
    return gates, notes


def _render_flat_projection(
    intermediate: dict[str, Any], profile: dict[str, Any]
) -> PlatformPromptProjection:
    """Default vendor-neutral projection: budget + camera vocab + negative."""
    provider_id = str(profile.get("provider_id", "default"))
    budget = profile.get("char_budget", {}) or {}
    prompt_budget = int(budget.get("prompt_zh", 800))
    vocabulary = profile.get("camera_vocabulary", {}) or {}

    notes: list[str] = []
    prompt = str(intermediate.get("prompt", "") or "")

    motion = str(intermediate.get("camera_motion", "") or "")
    mapped_motion = str(vocabulary.get(motion, "")) if motion else ""
    if motion and not mapped_motion:
        mapped_motion = motion
        notes.append(f"camera_motion '{motion}' 无平台词表映射，原样保留")
    if mapped_motion and mapped_motion not in prompt:
        prompt = f"{prompt}；{mapped_motion}" if prompt else mapped_motion

    prompt, truncated = _truncate_by_sentence_budget(prompt, prompt_budget)
    if truncated:
        notes.append(f"提示词按 {provider_id} 字数预算截断至 {len(prompt)} 字")

    gates, gate_notes = _capability_gates(intermediate, profile, provider_id)
    notes.extend(gate_notes)

    return PlatformPromptProjection(
        provider_id=provider_id,
        prompt=prompt,
        negative_prompt=_compose_negative(intermediate, profile),
        truncated=truncated,
        capability_gates=gates,
        notes=notes,
    )


# ─── H3 timeline structure renderer (MiniMax Context-IR grammar) ─────────────

_SPEAKER_ID_PREFIX = "S"


def _h3_dialogue_fragments(intermediate: dict[str, Any], structure: dict[str, Any]) -> list[str]:
    """Render dialogue lines as H3 speaker clauses.

    Speakers keep stable IDs ((S1), (S2), ...) across the shot; dialogue is
    preserved verbatim inside ``<d>[language] ...</d>``; voiceover clauses
    state that the on-screen lips remain closed (H3 guide §4.4).
    """
    language_tag = str(structure.get("dialogue_language_tag", "Chinese"))
    entries: list[dict[str, Any]] = []
    raw_dialogues = intermediate.get("dialogues")
    if isinstance(raw_dialogues, list) and raw_dialogues:
        for item in raw_dialogues:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                entries.append(item)
    else:
        text = str(intermediate.get("dialogue_text", "") or "").strip()
        if text:
            entries.append(
                {
                    "speaker": str(intermediate.get("dialogue_speaker", "") or ""),
                    "text": text,
                    "offscreen": bool(intermediate.get("dialogue_offscreen", False)),
                }
            )
    fragments: list[str] = []
    for index, entry in enumerate(entries, start=1):
        speaker_id = f"({_SPEAKER_ID_PREFIX}{index})"
        speaker = str(entry.get("speaker") or "").strip()
        subject = f"{speaker} {speaker_id}" if speaker else speaker_id
        line = str(entry.get("text") or "").strip()
        if entry.get("offscreen"):
            fragments.append(
                f"{subject} says in an off-screen voiceover: "
                f"<d>[{language_tag}] {line}</d> while their lips remain completely closed."
            )
        else:
            fragments.append(f"{subject} says: <d>[{language_tag}] {line}</d>")
    return fragments


def _render_h3_timeline_projection(
    intermediate: dict[str, Any], profile: dict[str, Any]
) -> PlatformPromptProjection:
    """MiniMax H3 Context-IR timeline projection (guide-driven, data-driven).

    Assembles: optional keyframe-alignment instruction (first line) +
    ``integrated_multimodal_description`` ([Shot 1] timeline body with
    natural camera-motion sentences and speaker-tagged dialogue) +
    ``overall_soundscape`` + ``non_diegetic_music``.  Section labels and
    templates come from the profile JSON, so future grammar revisions are
    configuration changes, not code changes.
    """
    provider_id = str(profile.get("provider_id", "default"))
    structure = profile.get("structure", {}) or {}
    budget = profile.get("char_budget", {}) or {}
    prompt_budget = int(budget.get("prompt_zh", 2000))
    vocabulary = profile.get("camera_vocabulary", {}) or {}
    sections = list(structure.get("sections") or []) or [
        "integrated_multimodal_description",
        "overall_soundscape",
        "non_diegetic_music",
    ]

    notes: list[str] = []

    # 1) Keyframe alignment instruction — always the first line when present.
    alignment_mode = str(intermediate.get("alignment_mode", "") or "").lower()
    templates = structure.get("alignment_instructions", {}) or {}
    header_template = str(templates.get(alignment_mode, "") or "")
    header = ""
    if header_template:
        try:
            header = header_template.format(duration=float(intermediate.get("duration_s", 0) or 0))
        except (KeyError, ValueError, IndexError):
            header = header_template
            notes.append("对齐指令模板格式化失败，原样使用模板文本")

    # 2) Timeline body: base description with the canonical camera token
    #    replaced by the platform's natural camera-motion sentence.
    body = str(intermediate.get("prompt", "") or "").strip()
    motion = str(intermediate.get("camera_motion", "") or "")
    mapped_motion = str(vocabulary.get(motion, "")) if motion else ""
    if motion and not mapped_motion:
        mapped_motion = motion
        notes.append(f"camera_motion '{motion}' 无平台词表映射，原样保留")
    if mapped_motion:
        if motion and motion in body:
            body = body.replace(motion, mapped_motion)
        elif mapped_motion not in body:
            # Camera motion reads as a natural English sentence appended after
            # the description (H3 guide §4.3); avoid doubled punctuation.
            body = f"{body.rstrip('。，,.')}, {mapped_motion}" if body else mapped_motion

    dialogue_fragments = _h3_dialogue_fragments(intermediate, structure)
    if dialogue_fragments:
        body = f"{body} {' '.join(dialogue_fragments)}" if body else " ".join(dialogue_fragments)
    description = f"[Shot 1] {body}".strip()

    # 3) Sound sections: soundscape (ambient/action/non-verbal) and music.
    soundscape = str(intermediate.get("sound_design", "") or "").strip() or str(
        structure.get("soundscape_fallback", "") or "N/A"
    )
    music = str(intermediate.get("music", "") or "").strip() or str(
        structure.get("music_na", "N/A")
    )
    section_values = {
        "integrated_multimodal_description": description,
        "overall_soundscape": soundscape,
        "non_diegetic_music": music,
    }

    # 4) Budget: structural sections are preserved; only the timeline body
    #    is truncated at sentence boundaries.
    fixed_overhead = len(header) + sum(len(name) + 4 for name in sections)
    fixed_overhead += len(soundscape) + len(music)
    body_budget = max(64, prompt_budget - fixed_overhead)
    description, truncated = _truncate_by_sentence_budget(description, body_budget)
    if truncated:
        notes.append(f"H3 时间线正文按字数预算截断至 {len(description)} 字")
    section_values["integrated_multimodal_description"] = description

    blocks = [
        f"{name}: {section_values.get(name, '')}" for name in sections if section_values.get(name)
    ]
    prompt = (f"{header}\n\n" if header else "") + "\n\n".join(blocks)

    gates, gate_notes = _capability_gates(intermediate, profile, provider_id)
    notes.extend(gate_notes)

    flags: dict[str, bool] = {}
    if bool(structure.get("disable_prompt_optimizer", False)):
        flags["disable_prompt_optimizer"] = True

    return PlatformPromptProjection(
        provider_id=provider_id,
        prompt=prompt,
        negative_prompt=_compose_negative(intermediate, profile),
        truncated=truncated,
        capability_gates=gates,
        notes=notes,
        flags=flags,
    )


# ─── Structure renderer registry ──────────────────────────────────────────────
#
# profile ``structure.kind`` → renderer.  New platform grammars register here;
# unknown kinds fall back to the vendor-neutral flat projection with a note.

StructureRenderer = Callable[[dict[str, Any], dict[str, Any]], PlatformPromptProjection]

_STRUCTURE_RENDERERS: dict[str, StructureRenderer] = {
    "flat": _render_flat_projection,
    "h3_timeline": _render_h3_timeline_projection,
}


def register_structure_renderer(kind: str, renderer: StructureRenderer) -> None:
    """Register (or override) a prompt-structure renderer for a profile kind."""
    _STRUCTURE_RENDERERS[str(kind)] = renderer


def project_prompt_for_platform(
    intermediate: dict[str, Any],
    platform_id: str,
) -> PlatformPromptProjection:
    """Project a semantic-level intermediate prompt onto one platform.

    ``intermediate`` keys (all optional):
    - ``prompt``: platform-neutral main prompt text;
    - ``negative_prompt``: platform-neutral negative terms;
    - ``camera_motion``: canonical motion token (e.g. ``slow_push``);
    - ``dialogue_text`` / ``dialogue_speaker`` / ``dialogue_offscreen``:
      single-speaker dialogue, or ``dialogues``: list of
      ``{"speaker", "text", "offscreen"}`` entries (stable ID order);
    - ``sound_design``: ambient/action sound summary (soundscape section);
    - ``music``: non-diegetic score description (empty → platform N/A);
    - ``alignment_mode``: ``t2va`` / ``i2va`` / ``fl2va`` / ``l2va`` —
      selects the keyframe alignment instruction on structured platforms;
    - ``duration_s``: effective clip duration (alignment timestamps);
    - ``requires_multi_shot`` / ``requires_native_audio`` /
      ``requires_camera_command``: capability demands of the shot.

    The projection applies the platform profile's structure renderer,
    character budget, camera-vocabulary mapping, negative policy and
    capability switches.  Unsupported capability demands are surfaced in
    ``notes`` — the caller decides whether to reroute (see
    :func:`select_platform_for_shot`).
    """
    profile = load_platform_profile(platform_id)
    structure = profile.get("structure", {}) or {}
    kind = str(structure.get("kind", "flat"))
    renderer = _STRUCTURE_RENDERERS.get(kind)
    if renderer is None:
        projection = _render_flat_projection(intermediate, profile)
        notes = [f"未知 structure.kind '{kind}'，回退 flat 投影", *projection.notes]
        return PlatformPromptProjection(
            provider_id=projection.provider_id,
            prompt=projection.prompt,
            negative_prompt=projection.negative_prompt,
            truncated=projection.truncated,
            capability_gates=projection.capability_gates,
            notes=notes,
            flags=projection.flags,
        )
    return renderer(intermediate, profile)


# ─── 方舟 real-face compliance branch ─────────────────────────────────────────

_REAL_FACE_PATTERNS = re.compile(
    r"(真人演员|真人脸|真实人脸|实拍人像|演员本人|真人肖像|real\s*person|real\s*face)",
    re.IGNORECASE,
)
_TRUSTED_ASSET_PREFIX = "asset://"


@dataclass(frozen=True)
class FaceComplianceDecision:
    """Outcome of the real-face dependency check for one shot."""

    required: bool
    path: str = ""  # "" | "trusted_actor_asset" | "virtual_human"
    reason: str = ""
    run_plan_note: str = ""


def detect_real_face_dependency(
    prompt: str,
    *,
    reference_urls: tuple[str, ...] | list[str] = (),
) -> bool:
    """Detect whether a shot depends on real human face material."""
    for url in reference_urls:
        if str(url).startswith(_TRUSTED_ASSET_PREFIX):
            return True
    return bool(_REAL_FACE_PATTERNS.search(str(prompt or "")))


def apply_ark_face_compliance(
    prompt: str,
    *,
    platform_id: str,
    reference_urls: tuple[str, ...] | list[str] = (),
) -> FaceComplianceDecision:
    """Resolve the real-face compliance branch for a shot.

    Only 火山方舟 exposes trusted actor assets; other platforms must use the
    virtual-human substitution path whenever a real-face dependency exists.
    Non-dependent shots pass through unchanged.
    """
    required = detect_real_face_dependency(prompt, reference_urls=reference_urls)
    if not required:
        return FaceComplianceDecision(required=False)

    profile = load_platform_profile(platform_id)
    switches = profile.get("capability_switches", {}) or {}
    has_trusted_refs = any(str(url).startswith(_TRUSTED_ASSET_PREFIX) for url in reference_urls)
    if bool(switches.get("trusted_actor_asset")) and has_trusted_refs:
        return FaceComplianceDecision(
            required=True,
            path="trusted_actor_asset",
            reason="检测到真人脸依赖，方舟受信演员资产路径可用",
            run_plan_note="真人脸镜头走受信演员资产（asset://），保留授权凭证",
        )
    return FaceComplianceDecision(
        required=True,
        path="virtual_human",
        reason="检测到真人脸依赖，切换为虚拟人像/受信素材替代路径",
        run_plan_note="真人脸镜头需人工确认：使用虚拟人像替代或补充受信素材授权",
    )


# ─── Capability-driven routing ────────────────────────────────────────────────

# feature → provider score weight; catalog features decide the route.
_FEATURE_WEIGHTS: dict[str, float] = {
    "multi_shot": 3.0,
    "storyboard_reference": 1.5,
    "multi_subject_reference": 2.0,
    "multi_image_reference": 2.0,
    "multi_modal_reference": 2.5,
    "audio_visual_sync": 1.5,
    "native_audio": 1.0,
    "camera_commands": 2.5,
    "face_identity_lock": 2.0,
    "character_consistent_sequence": 1.5,
    "trusted_actor_asset": 2.0,
}

_REQUIREMENT_FEATURES: dict[str, str] = {
    "requires_multi_shot": "multi_shot",
    "requires_native_audio": "native_audio",
    "requires_camera_command": "camera_commands",
    "requires_face_identity": "face_identity_lock",
    "requires_trusted_actor": "trusted_actor_asset",
    "requires_audio_visual_sync": "audio_visual_sync",
}


def _provider_feature_set(provider_id: str) -> set[str]:
    entry = FILM_PROVIDER_CATALOG.get(provider_id, {})
    features: set[str] = set()
    for model in [*entry.get("image_models", []), *entry.get("video_models", [])]:
        features.update(str(item) for item in model.get("features", []))
    return features


def select_platform_for_shot(
    requirements: dict[str, Any],
    *,
    preferred_provider: str = "",
    allowed_providers: tuple[str, ...] | list[str] = (),
) -> tuple[str, dict[str, float]]:
    """Pick the visual platform whose catalog features best match the shot.

    Routing rules encoded in feature weights:
    - 多镜头叙事 → 百炼 (multi_shot, storyboard_reference)
    - 多参考图/多模态参考 → 方舟 (multi_modal_reference)
    - 运镜控制 → MiniMax (camera_commands)
    - 真人受信素材 → 方舟 (trusted_actor_asset)

    ``preferred_provider`` gets a loyalty bonus so explicit user choices win
    unless another platform dominates.  Returns ``(provider_id, scores)``.
    """
    candidates = list(allowed_providers) or list(FILM_PROVIDER_CATALOG)
    scores: dict[str, float] = {}
    demanded: set[str] = set()
    for key, feature in _REQUIREMENT_FEATURES.items():
        if requirements.get(key):
            demanded.add(feature)
    reference_count = int(requirements.get("reference_image_count", 0) or 0)
    if reference_count >= 3:
        # 多参考图/多模态参考 → 方舟 multi_modal_reference 优先
        demanded.add("multi_modal_reference")

    for provider_id in candidates:
        features = _provider_feature_set(provider_id)
        score = 0.0
        for feature in demanded:
            if feature in features:
                score += _FEATURE_WEIGHTS.get(feature, 1.0)
        if preferred_provider and provider_id == preferred_provider:
            score += 2.0
        scores[provider_id] = round(score, 2)

    best = max(scores, key=lambda key: (scores[key], key == preferred_provider))
    return best, scores


# ─── Cost estimation ──────────────────────────────────────────────────────────

# Deterministic planning prices (USD). Video: base + per-second rate;
# image: per-output rate. Values are planning estimates, not billing truths.
_PRICE_TABLE: dict[str, dict[str, Any]] = {
    "bailian": {
        "video": {"base": 0.10, "per_second": 0.03},
        "image": {"per_output": 0.04},
    },
    "minimax": {
        "video": {"base": 0.12, "per_second": 0.055},
        "image": {"per_output": 0.03},
    },
    "volcengine_ark": {
        "video": {"base": 0.11, "per_second": 0.032},
        "image": {"per_output": 0.035},
    },
    "default": {
        "video": {"base": 0.10, "per_second": 0.03},
        "image": {"per_output": 0.035},
    },
}

# MiniMax H3 official per-second rates (USD) by resolution tier; matches the
# ComfyUI Hailuo03 price badge expression ($0.1287/s @ 768P, $0.1859/s @ 2K).
_H3_VIDEO_RATES = {"768P": 0.1287, "2K": 0.1859}
# H3 Ref2VA surcharge: every reference image beyond the fifth adds $0.0572.
_H3_EXTRA_IMAGE_COST = 0.0572
_H3_FREE_REFERENCE_IMAGES = 5


def estimate_generation_cost(
    provider_id: str,
    *,
    media_kind: str = "video",
    duration_s: float = 0,
    image_count: int = 1,
    resolution: str = "",
    model_id: str = "",
    reference_image_count: int = 0,
) -> float:
    """Estimate the USD cost of one generation for planning and gating.

    MiniMax H3 uses its official per-second tier (768P/2K) plus the
    Ref2VA reference-image surcharge; every other provider keeps the
    flat planning table.
    """
    normalized = _normalize_provider(provider_id) or "default"
    if normalized == "minimax" and str(model_id or "").startswith("MiniMax-H3"):
        if media_kind == "image":
            table = _PRICE_TABLE["minimax"]
            return round(float(table["image"]["per_output"]) * max(1, int(image_count)), 4)
        rate = _H3_VIDEO_RATES.get(str(resolution).upper(), _H3_VIDEO_RATES["768P"])
        cost = rate * max(0.0, duration_s)
        extra = max(0, int(reference_image_count) - _H3_FREE_REFERENCE_IMAGES)
        cost += extra * _H3_EXTRA_IMAGE_COST
        return round(cost, 4)
    table = _PRICE_TABLE.get(normalized, _PRICE_TABLE["default"])
    if media_kind == "image":
        per_output = float(table["image"]["per_output"])
        cost = per_output * max(1, int(image_count))
    else:
        video = table["video"]
        cost = float(video["base"]) + float(video["per_second"]) * max(0.0, duration_s)
    if resolution and str(resolution).upper() in {"1080P", "2K", "4K"}:
        cost *= 1.25
    return round(cost, 4)


__all__ = [
    "FaceComplianceDecision",
    "PlatformPromptProjection",
    "StructureRenderer",
    "apply_ark_face_compliance",
    "detect_real_face_dependency",
    "estimate_generation_cost",
    "project_prompt_for_platform",
    "register_structure_renderer",
    "select_platform_for_shot",
]
