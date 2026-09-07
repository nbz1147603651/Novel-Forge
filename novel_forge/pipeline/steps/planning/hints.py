"""Normalization, enforcement, and payload processing for plan step."""

from __future__ import annotations

import ast
import re
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.guardrails import (
    is_system_artifact_name,
    sanitize_story_text,
    text_has_custody_signal,
)
from novel_forge.core.format_contracts import merge_required_keys_for_task
from novel_forge.core.schemas.continuity import ChapterPlan, ChapterStatePacket
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.utils.string import clean_str
from novel_forge.obs.logger import get_logger

_log = get_logger("pipeline.steps.planning.hints")
_PLAN_REQUIRED_TOP_LEVEL_KEYS = merge_required_keys_for_task(TaskType.PLAN_CHAPTER)

_GENERIC_GROUP_HINTS = (
    "其他",
    "其余",
    "群体",
    "参与者",
    "众人",
    "路人",
    "群众",
    "人群",
    "某人",
    "若干",
)
_OPENING_SKIP_PROCESS_RE = re.compile(
    r"(?:不得|不要|无需|不必).{0,8}(?:回溯|解释|交代|说明).{0,6}(?:过程|缘由|经过)"
    r"|(?:直接|立刻).{0,6}(?:切入|进入).{0,6}(?:审问|新场景|冲突)"
    r"|开场即"
)
_TRANSITION_VERB_RE = re.compile(
    r"(?:押解|带离|脱身|离开|前往|赶到|转入|移送|传唤|获释|放出|抵达|回到|启程)"
)


def _split_long_text(text: str, max_chars: int) -> list[str]:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return [compact]
    parts = [p.strip() for p in re.split(r"[；;。！？!?]", compact) if p.strip()]
    if not parts:
        return [compact[:max_chars]]
    chunks: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}；{part}" if current else part
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = part[:max_chars]
    if current:
        chunks.append(current)
    return chunks or [compact[:max_chars]]


def _looks_like_generic_group(value: str) -> bool:
    text = clean_str(value)
    return not text or any(token in text for token in _GENERIC_GROUP_HINTS)


def _coerce_non_negative_int(value: Any) -> int:
    text = clean_str(value)
    if not text:
        return 0
    try:
        return max(0, int(float(text)))
    except (TypeError, ValueError):
        return 0


def _coerce_body_signal_budget(value: Any) -> int:
    budget = _coerce_non_negative_int(value)
    if budget == 0 and str(value or "").strip() not in {"0", "0.0"}:
        return 1
    return max(0, min(5, budget))


def _coerce_unit_interval(value: Any, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, numeric))


def _relationship_band(value: float) -> str:
    if value >= 0.7:
        return "高"
    if value <= 0.3:
        return "低"
    return "中"


def _relationship_pressure_note(rel: Any) -> str:
    trust = _coerce_unit_interval(getattr(rel, "trust", 0.5), 0.5)
    tension = _coerce_unit_interval(getattr(rel, "tension", 0.5), 0.5)
    parts = [
        "信任" + _relationship_band(trust),
        "张力" + _relationship_band(tension),
    ]
    dependency_raw = getattr(rel, "dependency", None)
    if dependency_raw is not None:
        dependency = _coerce_unit_interval(dependency_raw, 0.0)
        parts.append("依赖" + _relationship_band(dependency))
    return "（" + "、".join(parts) + "）"


def _normalize_dialogue_voice_targets(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, raw in value.items():
        name = sanitize_story_text(clean_str(key))
        text = sanitize_story_text(clean_str(raw))
        if name and text:
            result[name[:40]] = text[:160]
    return result


def _compact_contract_text(value: Any, *, max_chars: int) -> str:
    cleaned = sanitize_story_text(clean_str(value))
    if len(cleaned) <= max_chars:
        return cleaned
    parts = [
        p.strip()
        for p in re.split(r"[。！？；;，,\n]+", cleaned)
        if 2 <= len(p.strip()) <= max_chars
    ]
    compact = "；".join(parts[:3]).strip("；")
    return (
        compact[:max_chars].rstrip("，。、；： ")
        if compact
        else cleaned[:max_chars].rstrip("，。、；： ")
    )


def _normalize_string_list(value: Any, *, max_chars: int = 220) -> list[str]:
    raw: list[str] = []
    if isinstance(value, list):
        for item in value:
            t = sanitize_story_text(clean_str(item))
            if t:
                raw.append(t)
    elif isinstance(value, dict):
        for v in value.values():
            t = sanitize_story_text(clean_str(v))
            if t:
                raw.append(t)
    elif isinstance(value, str):
        t = sanitize_story_text(clean_str(value))
        if t:
            raw = [t]
    normalized: list[str] = []
    for item in raw:
        normalized.extend(_split_long_text(item, max_chars))
    seen: set[str] = set()
    deduped: list[str] = []
    for item in normalized:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _compact_sensory_notes(
    value: Any,
    *,
    max_items: int = 2,
    item_max_chars: int = 72,
    total_max_chars: int = 160,
) -> str:
    """Keep scene sensory notes as a small set of candidate anchors."""

    def _clean_item(item: Any) -> str:
        text = sanitize_story_text(clean_str(item)).strip(" \t\r\n-[]()（）'\"")
        return text[:item_max_chars].rstrip("，。、；：,; ")

    candidates: list[str] = []
    if isinstance(value, (list, tuple)):
        candidates = [_clean_item(item) for item in value]
    elif isinstance(value, str):
        text = sanitize_story_text(clean_str(value))
        stripped = text.strip()

        def _extend_literal_items(fragment: str) -> None:
            try:
                parsed = ast.literal_eval(fragment)
            except (SyntaxError, ValueError):
                return
            if isinstance(parsed, (list, tuple)):
                candidates.extend(_clean_item(item) for item in parsed)

        if stripped.startswith("[") and stripped.endswith("]"):
            _extend_literal_items(stripped)
        if not candidates and "[" in stripped and "]" in stripped:
            for fragment in re.findall(r"\[[^\[\]]+\]", stripped):
                _extend_literal_items(fragment)
        if not candidates:
            parts = [part.strip() for part in re.split(r"[；;\n]+", text) if part.strip()]
            if len(parts) > 1:
                for part in parts:
                    if part.startswith("[") and part.endswith("]"):
                        before = len(candidates)
                        _extend_literal_items(part)
                        if len(candidates) > before:
                            continue
                    candidates.append(_clean_item(part))
            else:
                return text[:total_max_chars].rstrip("，。、；：,; ")
    else:
        text = _clean_item(value)
        return text[:total_max_chars].rstrip("，。、；：,; ")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
        if len(deduped) >= max_items:
            break
    return "；".join(deduped)[:total_max_chars].rstrip("，。、；：,; ")


def _normalize_chapter_type(value: Any) -> str:
    raw = clean_str(value).lower()
    if not raw:
        return "crisis"
    aliases = {
        "crisis": "crisis",
        "action": "crisis",
        "conflict": "crisis",
        "危机": "crisis",
        "冲突": "crisis",
        "discovery": "discovery",
        "exploration": "discovery",
        "觉醒": "discovery",
        "探索": "discovery",
        "emotional": "emotional",
        "emotion": "emotional",
        "relationship": "emotional",
        "情感": "emotional",
        "关系": "emotional",
        "transition": "transition",
        "setup": "transition",
        "过渡": "transition",
        "铺垫": "transition",
    }
    if raw in aliases:
        return aliases[raw]
    for token, normalized in aliases.items():
        if token and token in raw:
            return normalized
    return "crisis"


def _normalize_required_characters(value: Any, *, fallback_pov: str = "") -> list[str]:
    raw_names = _normalize_string_list(value, max_chars=40)
    names: list[str] = []
    seen: set[str] = set()
    for raw in raw_names:
        name = sanitize_story_text(clean_str(raw))
        if (
            not name
            or name in seen
            or is_system_artifact_name(name)
            or _looks_like_generic_group(name)
        ):
            continue
        seen.add(name)
        names.append(name)
    if not names and fallback_pov:
        pov = sanitize_story_text(clean_str(fallback_pov))
        if pov and not is_system_artifact_name(pov):
            names = [pov]
    return names


def _normalize_beats(value: Any, *, min_beats: int, max_beats: int, max_chars: int) -> list[str]:
    min_items, max_items, max_len = (
        max(1, min_beats),
        max(max(1, min_beats), max_beats),
        max(20, max_chars),
    )
    beats = _normalize_string_list(value, max_chars=max_len)[:max_items]
    defaults = [
        "承接上一章的交接点，明确当前行动目标。",
        "推进主线冲突并引入新的阻力。",
        "让关键角色做出代价明确的选择。",
        "在章末形成可直接承接的钩子。",
        "补足支线与主线之间的因果连接。",
        "强化人物情绪变化与关系张力。",
    ]
    idx = 0
    while len(beats) < min_items and idx < len(defaults):
        fb = sanitize_story_text(defaults[idx]).strip()
        idx += 1
        if fb:
            for part in _split_long_text(fb, max_len):
                if len(beats) >= min_items:
                    break
                beats.append(part)
    while len(beats) < min_items:
        beats.append("补足节拍" + str(len(beats) + 1))
    return beats[:max_items]


def _fallback_character_motivations(
    required_characters: list[str], *, outline: ChapterOutline
) -> list[dict[str, str]]:
    goal = outline.goal
    return [
        {
            "character": n,
            "motivation": "推动\u201c" + goal + "\u201d在当前场景取得实质进展。",
            "stake": "若失败将失去主动权、情报或关系优势。",
        }
        for n in required_characters[:2]
    ]


def _fallback_scene_purpose(index: int, total: int, *, outline: ChapterOutline) -> str:
    if index == 0:
        return "承接开场状态并把本章目标落入第一组可执行行动。"
    if index >= total - 1:
        return "完成本章阶段性收束，并为下一章留下可承接出口。"
    return "推进本章目标：“" + outline.goal + "”。"


def _fallback_scene_conflict(
    index: int, total: int, *, outline: ChapterOutline, scene: dict[str, Any]
) -> str:
    summary = sanitize_story_text(clean_str(scene.get("summary")))
    if index == 0:
        return "开场承接压力与角色当前目标发生碰撞，迫使行动进入本章主线。"
    if index >= total - 1:
        return "章末出口目标与未解决压力并存，角色必须带着代价进入下一阶段。"
    if summary:
        return "角色推进“" + summary[:80] + "”时遭遇阻力，必须做出可见取舍。"
    return "角色推进本章目标时遭遇阻力，必须做出可见取舍。"


def _fallback_time_marker(index: int, total: int, *, outline: ChapterOutline) -> str:
    explicit = (
        clean_str(getattr(outline, "time_anchor", ""))
        or clean_str(getattr(outline, "time_span", ""))
        or clean_str(getattr(outline, "time_gap_from_prev", ""))
    )
    if not explicit:
        setting = clean_str(getattr(outline, "setting", ""))
        match = re.search(r"\d{4}年[^，,。；;、\s]{0,12}", setting)
        if match:
            explicit = match.group(0)
    if not explicit:
        explicit = "本章叙事当下"
    if total <= 1:
        return explicit
    if index == 0:
        return explicit + "·开场"
    if index >= total - 1:
        return explicit + "·章末"
    return explicit + "·推进段" + str(index + 1)


def _fallback_relationship_dynamics(
    required_characters: list[str], *, active_relationships: list[Any]
) -> str:
    if not required_characters:
        return "通过互动反馈映射当前关系压力，避免纯说明式推进。"
    for rel in active_relationships:
        rel_chars = [clean_str(n) for n in getattr(rel, "characters", []) if clean_str(n)]
        if not rel_chars:
            continue
        if set(required_characters) & set(rel_chars):
            pair = "与".join(rel_chars[:2])
            status = clean_str(getattr(rel, "public_status", "")) or "关系张力"
            shift = clean_str(getattr(rel, "last_shift_event", ""))
            pressure = _relationship_pressure_note(rel)
            if shift:
                return (
                    "让"
                    + pair
                    + "围绕当前目标发生可感知互动，延续\u201c"
                    + status
                    + "\u201d"
                    + pressure
                    + "并回应\u201c"
                    + shift
                    + "\u201d的后果。"
                )
            return (
                "让"
                + pair
                + "围绕当前目标发生可感知互动，关系基调保持\u201c"
                + status
                + "\u201d"
                + pressure
                + "但必须出现细微变化。"
            )
    if len(required_characters) >= 2:
        joined = "与".join(required_characters[:2])
        return "让" + joined + "通过对话或动作推进关系，不要停留在信息交换层面。"
    return "通过" + required_characters[0] + "对他人的期待、防备或回避，折射当前关系压力。"


def _fallback_emotional_beat(
    index: int, total: int, *, outline: ChapterOutline, opening_emotion: str
) -> str:
    if index == 0:
        return sanitize_story_text(opening_emotion) or "承接上一场余波，情绪未稳但行动已被迫继续。"
    if index >= total - 1:
        return "情绪完成阶段性收束，但必须留下仍在发热的未决压力。"
    if index < max(1, total // 2):
        return "情绪逐步收紧，让\u201c" + outline.goal + "\u201d的代价开始显形。"
    return "把情绪推向抉择或后果显化，让冲突由潜伏转为正面碰撞。"


def _fallback_sensory_notes(
    *, location: str, forbidden_elements: list[str], is_opening: bool, is_closing: bool
) -> str:
    avoid = (
        ("避开已禁用意象：" + "、".join(forbidden_elements[:2]) + "。")
        if forbidden_elements
        else "避免重复旧意象。"
    )
    place = location or "当前场景"
    if is_opening:
        return "优先用" + place + "里的声音、气味、温度或触感把读者放进现场，" + avoid
    if is_closing:
        return "在" + place + "里留下能延续到下一章的感官尾音或环境余震，" + avoid
    return "在" + place + "中加入具体可感的声响、触感或光影反馈，" + avoid


def _fallback_relationship_evolution(packet: ChapterStatePacket | None) -> list[str]:
    if packet is None or not packet.active_relationships:
        return []
    evolution: list[str] = []
    for rel in packet.active_relationships[:2]:
        chars = [clean_str(n) for n in getattr(rel, "characters", []) if clean_str(n)]
        if not chars:
            continue
        status = clean_str(getattr(rel, "public_status", "")) or "关系张力"
        shift = clean_str(getattr(rel, "last_shift_event", ""))
        pressure = _relationship_pressure_note(rel)
        s = (
            "\u201c"
            + "与".join(chars[:2])
            + "\u201d的关系需围绕当前冲突出现可感知推进，基调保持\u201c"
            + status
            + "\u201d"
            + pressure
            + "。"
        )
        if shift:
            s += " 要回应\u201c" + shift + "\u201d带来的后果。"
        evolution.append(s)
    return evolution


def _derive_automatic_callbacks(
    *,
    forbidden_elements: list[str],
    opening_contract: str,
    closing_contract: str,
    scene_intents: list[dict[str, Any]],
    foreshadowing_plan: list[str],
    key_revelations: list[str],
    relationship_evolution: list[str],
    required_state_transitions: list[str],
) -> list[str]:
    """Promote forbidden items to callbacks when the plan already treats them as anchors.

    This is intentionally data-driven: only terms that are explicitly reused by
    the current chapter plan's own anchor fields are protected.
    """

    anchor_texts = [
        opening_contract,
        closing_contract,
        *foreshadowing_plan,
        *key_revelations,
        *relationship_evolution,
        *required_state_transitions,
    ]
    for scene in scene_intents:
        if not isinstance(scene, dict):
            continue
        anchor_texts.extend(
            [
                clean_str(scene.get("summary")),
                clean_str(scene.get("required_outcome")),
                clean_str(scene.get("emotional_beat")),
                clean_str(scene.get("relationship_dynamics")),
            ]
        )

    normalized_anchors = [text for text in anchor_texts if text]
    auto_callbacks: list[str] = []
    seen: set[str] = set()
    for item in forbidden_elements:
        token = sanitize_story_text(clean_str(item))
        if len(token) < 2 or token in seen:
            continue
        if any(token in anchor for anchor in normalized_anchors):
            seen.add(token)
            auto_callbacks.append(token)
    return auto_callbacks


def _filter_abstract_emotion_labels(
    elements: list[str],
    *,
    packet: ChapterStatePacket | None = None,
    plan: ChapterPlan | None = None,
    extra_known_terms: list[str] | None = None,
    motif_context: dict[str, Any] | None = None,
) -> list[str]:
    from novel_forge.core.domain.bible_derived_provider import _load_defaults_cached
    from novel_forge.pipeline.steps.continuity_eval_step import _filter_forbidden_elements

    _rhetorical_hints, _kinship_terms, _emotion_keywords = _load_defaults_cached()
    return _filter_forbidden_elements(
        elements,
        packet=packet,
        bridge=packet.bridge if packet is not None else None,
        plan=plan,
        extra_known_terms=extra_known_terms,
        motif_context=motif_context,
        emotion_keywords=_emotion_keywords,
        kinship_terms=_kinship_terms,
        rhetorical_hints=_rhetorical_hints,
    )


def response_is_token_capped(response: Any, request_max_tokens: int) -> bool:
    used = int(getattr(response, "completion_tokens", 0) or 0)
    return used > 0 and used >= max(request_max_tokens - 16, int(request_max_tokens * 0.97))


def missing_core_plan_keys(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return list(_PLAN_REQUIRED_TOP_LEVEL_KEYS)
    return [k for k in _PLAN_REQUIRED_TOP_LEVEL_KEYS if k not in data]


def resolve_revelation_budget(narrative_contract: dict[str, Any] | None, *, fallback: int) -> int:
    if isinstance(narrative_contract, dict):
        protocol = narrative_contract.get("continuity_protocol")
        if isinstance(protocol, dict):
            v = _coerce_non_negative_int(protocol.get("max_key_revelations_per_chapter", 0))
            if v > 0:
                return max(1, min(8, v))
    return max(1, min(8, _coerce_non_negative_int(fallback) or 2))


def resolve_min_unresolved_threads(
    narrative_contract: dict[str, Any] | None, *, fallback: int = 0
) -> int:
    if isinstance(narrative_contract, dict):
        protocol = narrative_contract.get("continuity_protocol")
        if isinstance(protocol, dict):
            return max(
                0,
                min(6, _coerce_non_negative_int(protocol.get("min_unresolved_threads_to_keep", 0))),
            )
    return max(0, min(6, _coerce_non_negative_int(fallback)))


def enforce_revelation_budget(plan_payload: dict[str, Any], *, max_revelations: int) -> bool:
    key_rev = _normalize_string_list(plan_payload.get("key_revelations", []), max_chars=160)
    if len(key_rev) <= max_revelations:
        plan_payload["key_revelations"] = key_rev
        return False
    kept, overflow = key_rev[:max_revelations], key_rev[max_revelations:]
    foreshadowing = _normalize_string_list(
        plan_payload.get("foreshadowing_plan", []), max_chars=160
    )
    for item in overflow:
        h = "延后揭示：" + item
        if h not in foreshadowing:
            foreshadowing.append(h)
    plan_payload["key_revelations"] = kept
    plan_payload["foreshadowing_plan"] = foreshadowing[:3]
    return True


def enforce_unresolved_retention(
    plan_payload: dict[str, Any], *, packet: ChapterStatePacket | None, min_keep: int
) -> bool:
    if min_keep <= 0 or packet is None or packet.bridge is None:
        return False
    causal = getattr(packet.bridge, "causal_link", None)
    if causal is None:
        return False
    uq = clean_str(getattr(causal, "unresolved_question", ""))
    ot = [clean_str(i) for i in (getattr(causal, "open_threads", []) or []) if clean_str(i)]
    available = len(ot) + (1 if uq else 0)
    if available <= 0:
        return False
    keep_target = min(min_keep, available)
    clause = "章末至少保留" + str(keep_target) + "条未决线索，不得一次性揭晓全部答案。"
    cc = clean_str(plan_payload.get("closing_contract"))
    if clause in cc:
        return False
    plan_payload["closing_contract"] = (cc + "\uff1b" + clause) if cc else clause
    return True


def enforce_scene_switch_limit(
    plan_payload: dict[str, Any],
    *,
    max_scenes: int,
    sensory_notes_max_items: int = 2,
) -> bool:
    """Cap scene count while preserving the model's opening and closing beats."""

    scenes = plan_payload.get("scene_intents")
    if not isinstance(scenes, list) or len(scenes) <= max_scenes:
        return False
    source = [_copy_scene(scene) for scene in scenes if isinstance(scene, dict)]
    if len(source) <= max_scenes:
        plan_payload["scene_intents"] = source
        return len(source) != len(scenes)
    if max_scenes <= 2:
        kept = [source[0], source[-1]]
        overflow = source[1:-1]
        merge_index = 0
    else:
        kept = [*source[: max_scenes - 1], source[-1]]
        overflow = source[max_scenes - 1 : -1]
        merge_index = max(0, len(kept) - 2)
    for extra in overflow:
        _merge_scene_into(
            kept[merge_index],
            extra,
            sensory_notes_max_items=sensory_notes_max_items,
        )
    for idx, scene in enumerate(kept, start=1):
        scene["scene_id"] = "scene_" + str(idx).zfill(2)
    plan_payload["scene_intents"] = kept
    return True


def _enforce_opening_handoff_constraints(
    *, plan_payload: dict[str, Any], packet: ChapterStatePacket | None
) -> None:
    if packet is None or packet.bridge is None:
        return
    scenes = plan_payload.get("scene_intents")
    if not isinstance(scenes, list) or not scenes:
        return
    first = scenes[0]
    if not isinstance(first, dict):
        return
    bridge = packet.bridge
    prev_loc = clean_str(
        getattr(packet.previous_exit_state, "location", "") if packet.previous_exit_state else ""
    )
    open_loc = clean_str(getattr(bridge, "opening_location", ""))
    oc = clean_str(plan_payload.get("opening_contract"))
    custody = text_has_custody_signal(clean_str(getattr(packet, "previous_chapter_ending", "")))
    needs = bool(prev_loc and open_loc and prev_loc != open_loc) or custody
    if needs and _OPENING_SKIP_PROCESS_RE.search(oc):
        cleaned = _compact_contract_text(_OPENING_SKIP_PROCESS_RE.sub("", oc), max_chars=120)
        enforced = (
            "开场先交代角色如何从\u201c"
            + (prev_loc or "上一场景")
            + "\u201d转入\u201c"
            + (open_loc or "本章场景")
            + "\u201d，再进入主冲突。"
        )
        plan_payload["opening_contract"] = (cleaned + "\uff1b" + enforced) if cleaned else enforced
        _log.warning(
            "plan_opening_lint | rewritten_conflicting_opening_contract | prev=%s | open=%s",
            prev_loc or "-",
            open_loc or "-",
        )
    refs = _normalize_string_list(first.get("entry_state_refs", []), max_chars=120)
    handoff = clean_str(getattr(bridge, "action_handoff", ""))
    if handoff and not any(r and (r in handoff or handoff in r) for r in refs):
        refs.insert(0, handoff[:80])
    if prev_loc and not any(prev_loc in r for r in refs):
        refs.append("上一场景：" + prev_loc)
    first["entry_state_refs"] = refs[:4]
    ro = clean_str(first.get("required_outcome"))
    if needs and not _TRANSITION_VERB_RE.search(ro):
        bt = open_loc or first.get("location") or "本章主场景"
        tc = "先完成离场/押解/脱身交代，再进入" + bt + "的主行动。"
        first["required_outcome"] = (ro + "\uff1b" + tc) if ro else tc
    if open_loc and not clean_str(first.get("location")):
        first["location"] = open_loc
    ot = clean_str(getattr(bridge, "opening_time", ""))
    if ot and not clean_str(first.get("time_marker")):
        first["time_marker"] = ot


def _normalize_scene_intents(
    payload: Any,
    *,
    outline: ChapterOutline,
    min_beats: int = 4,
    max_beats: int = 8,
    beat_max_chars: int = 220,
    sensory_notes_max_items: int = 2,
) -> list[dict[str, Any]]:
    scenes: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for index, raw in enumerate(payload, start=1):
            if isinstance(raw, dict):
                summary = sanitize_story_text(clean_str(raw.get("summary")))
                if not summary:
                    continue
                character_motivations = []
                cm_raw = raw.get("character_motivations", [])
                if isinstance(cm_raw, list):
                    for cm in cm_raw:
                        if isinstance(cm, dict):
                            character_motivations.append(
                                {
                                    "character": sanitize_story_text(
                                        clean_str(cm.get("character", ""))
                                    ),
                                    "motivation": sanitize_story_text(
                                        clean_str(cm.get("motivation", ""))
                                    ),
                                    "stake": sanitize_story_text(clean_str(cm.get("stake", ""))),
                                }
                            )
                scenes.append(
                    {
                        "scene_id": clean_str(raw.get("scene_id"))
                        or "scene_" + str(index).zfill(2),
                        "summary": summary,
                        "purpose": sanitize_story_text(clean_str(raw.get("purpose"))),
                        "conflict": sanitize_story_text(clean_str(raw.get("conflict"))),
                        "required_characters": _normalize_required_characters(
                            raw.get("required_characters", []), fallback_pov=outline.pov_character
                        ),
                        "character_motivations": character_motivations,
                        "entry_state_refs": _normalize_string_list(raw.get("entry_state_refs", [])),
                        "required_outcome": sanitize_story_text(
                            clean_str(raw.get("required_outcome"))
                        ),
                        "dramatic_question": sanitize_story_text(
                            clean_str(raw.get("dramatic_question"))
                        ),
                        "exit_target_state": sanitize_story_text(
                            clean_str(raw.get("exit_target_state"))
                        ),
                        "location": clean_str(raw.get("location")) or outline.setting,
                        "time_marker": clean_str(raw.get("time_marker")),
                        "relationship_dynamics": sanitize_story_text(
                            clean_str(raw.get("relationship_dynamics"))
                        ),
                        "emotional_beat": sanitize_story_text(clean_str(raw.get("emotional_beat"))),
                        "sensory_notes": _compact_sensory_notes(
                            raw.get("sensory_notes"),
                            max_items=sensory_notes_max_items,
                        ),
                        "choice_pressure": sanitize_story_text(
                            clean_str(raw.get("choice_pressure"))
                        ),
                        "scene_resistance": sanitize_story_text(
                            clean_str(raw.get("scene_resistance"))
                        ),
                        "dialogue_voice_targets": _normalize_dialogue_voice_targets(
                            raw.get("dialogue_voice_targets")
                        ),
                        "revelation_level": sanitize_story_text(
                            clean_str(raw.get("revelation_level"))
                        ),
                        "symbol_usage_policy": sanitize_story_text(
                            clean_str(raw.get("symbol_usage_policy"))
                        ),
                        "body_signal_budget": _coerce_body_signal_budget(
                            raw.get("body_signal_budget")
                        ),
                        "target_words": _coerce_non_negative_int(raw.get("target_words", 0)),
                        "pov_character": sanitize_story_text(clean_str(raw.get("pov_character")))
                        or outline.pov_character,
                        "pov_scope": clean_str(raw.get("pov_scope")) or "limited",
                        "pov_switch_allowed": bool(raw.get("pov_switch_allowed", False)),
                        "pov_switch_marker_required": bool(
                            raw.get("pov_switch_marker_required", True)
                        ),
                        "scene_goal": sanitize_story_text(clean_str(raw.get("scene_goal"))),
                        "owned_events": _normalize_string_list(raw.get("owned_events", [])),
                        "owned_revelations": _normalize_string_list(
                            raw.get("owned_revelations", [])
                        ),
                        "owned_state_changes": _normalize_string_list(
                            raw.get("owned_state_changes", [])
                        ),
                        "forbidden_overlap": _normalize_string_list(
                            raw.get("forbidden_overlap", [])
                        ),
                        "handoff_to_next": sanitize_story_text(
                            clean_str(raw.get("handoff_to_next"))
                        ),
                        "dependency_scene_ids": _normalize_string_list(
                            raw.get("dependency_scene_ids", [])
                        ),
                        "parallel_group": clean_str(raw.get("parallel_group")),
                        "draft_order": _coerce_non_negative_int(raw.get("draft_order", index)),
                        "entry_state": sanitize_story_text(clean_str(raw.get("entry_state"))),
                        "exit_state": sanitize_story_text(clean_str(raw.get("exit_state"))),
                    }
                )
            else:
                summary = sanitize_story_text(clean_str(raw))
                if not summary:
                    continue
                scenes.append(
                    {
                        "scene_id": "scene_" + str(index).zfill(2),
                        "summary": summary,
                        "purpose": outline.goal,
                        "conflict": "",
                        "required_characters": _normalize_required_characters(
                            [outline.pov_character], fallback_pov=outline.pov_character
                        ),
                        "entry_state_refs": [],
                        "required_outcome": "",
                        "exit_target_state": "",
                        "location": outline.setting,
                        "time_marker": "",
                        "relationship_dynamics": "",
                        "emotional_beat": "",
                        "sensory_notes": "",
                        "choice_pressure": "",
                        "scene_resistance": "",
                        "dialogue_voice_targets": {},
                        "revelation_level": "",
                        "symbol_usage_policy": "",
                        "body_signal_budget": 1,
                        "target_words": 0,
                        "pov_character": outline.pov_character,
                        "pov_scope": "limited",
                        "pov_switch_allowed": False,
                        "pov_switch_marker_required": True,
                        "scene_goal": "",
                        "owned_events": [],
                        "owned_revelations": [],
                        "owned_state_changes": [],
                        "forbidden_overlap": [],
                        "handoff_to_next": "",
                        "dependency_scene_ids": [],
                        "parallel_group": "",
                        "draft_order": index,
                        "entry_state": "",
                        "exit_state": "",
                    }
                )
    if scenes:
        return scenes
    beats = _normalize_beats(
        outline.beats_summary, min_beats=min_beats, max_beats=max_beats, max_chars=beat_max_chars
    )
    if not beats:
        beats = [
            "承接上一章并落实本章目标：" + outline.goal,
            "推进主线冲突并引入新阻力。",
            "让角色做出代价明确的选择。",
            "形成可直接承接到下一章的交接点。",
        ]
    return [
        {
            "scene_id": "scene_" + str(i).zfill(2),
            "summary": b,
            "purpose": outline.goal,
            "conflict": "",
            "required_characters": _normalize_required_characters(
                [outline.pov_character], fallback_pov=outline.pov_character
            ),
            "entry_state_refs": [],
            "required_outcome": "",
            "exit_target_state": "",
            "location": outline.setting,
            "time_marker": "",
            "relationship_dynamics": "",
            "emotional_beat": "",
            "sensory_notes": "",
            "choice_pressure": "",
            "scene_resistance": "",
            "dialogue_voice_targets": {},
            "revelation_level": "",
            "symbol_usage_policy": "",
            "body_signal_budget": 1,
            "target_words": 0,
            "pov_character": outline.pov_character,
            "pov_scope": "limited",
            "pov_switch_allowed": False,
            "pov_switch_marker_required": True,
            "scene_goal": "",
            "owned_events": [],
            "owned_revelations": [],
            "owned_state_changes": [],
            "forbidden_overlap": [],
            "handoff_to_next": "",
            "dependency_scene_ids": [],
            "parallel_group": "",
            "draft_order": i,
            "entry_state": "",
            "exit_state": "",
        }
        for i, b in enumerate(beats, start=1)
    ]


def _enrich_scene_intents(
    scenes: list[dict[str, Any]],
    *,
    outline: ChapterOutline,
    packet: ChapterStatePacket | None,
    opening_contract: str,
    closing_contract: str,
    forbidden_elements: list[str],
    sensory_notes_max_items: int = 2,
) -> list[dict[str, Any]]:
    if not scenes:
        return scenes
    active_relationships = packet.active_relationships if packet else []
    opening_emotion = packet.bridge.emotional_carryover if packet and packet.bridge else ""
    total = len(scenes)
    enriched: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes):
        es = dict(scene)
        req_chars = _normalize_required_characters(
            es.get("required_characters", []), fallback_pov=outline.pov_character
        )
        es["required_characters"] = req_chars
        if not sanitize_story_text(es.get("pov_character") or ""):
            es["pov_character"] = outline.pov_character
        if not sanitize_story_text(es.get("pov_scope") or ""):
            es["pov_scope"] = "limited"
        es["pov_switch_allowed"] = bool(es.get("pov_switch_allowed", False))
        es["pov_switch_marker_required"] = bool(es.get("pov_switch_marker_required", True))
        if not sanitize_story_text(es.get("purpose") or ""):
            es["purpose"] = _fallback_scene_purpose(index, total, outline=outline)
        if not sanitize_story_text(es.get("scene_goal") or ""):
            es["scene_goal"] = sanitize_story_text(es.get("purpose") or es.get("summary") or "")
        if not sanitize_story_text(es.get("conflict") or ""):
            es["conflict"] = _fallback_scene_conflict(
                index,
                total,
                outline=outline,
                scene=es,
            )
        if not sanitize_story_text(es.get("location") or ""):
            es["location"] = clean_str(getattr(outline, "setting", "")) or "当前场景"
        if not sanitize_story_text(es.get("time_marker") or ""):
            es["time_marker"] = _fallback_time_marker(index, total, outline=outline)
        if not es.get("character_motivations") and req_chars:
            es["character_motivations"] = _fallback_character_motivations(
                req_chars, outline=outline
            )
        if not sanitize_story_text(es.get("relationship_dynamics") or ""):
            es["relationship_dynamics"] = _fallback_relationship_dynamics(
                req_chars, active_relationships=active_relationships
            )
        if not sanitize_story_text(es.get("emotional_beat") or ""):
            es["emotional_beat"] = _fallback_emotional_beat(
                index, total, outline=outline, opening_emotion=opening_emotion
            )
        if not sanitize_story_text(es.get("choice_pressure") or ""):
            es["choice_pressure"] = "本场必须让角色在目标、风险或关系代价之间做出可见选择。"
        if not sanitize_story_text(es.get("scene_resistance") or ""):
            es["scene_resistance"] = "用空间、流程、人群、时间窗口或物件传递制造阻力。"
        es["dialogue_voice_targets"] = _normalize_dialogue_voice_targets(
            es.get("dialogue_voice_targets")
        )
        if not sanitize_story_text(es.get("revelation_level") or ""):
            es["revelation_level"] = "按本章 key_revelations 控制揭示，未列明则只铺垫不解释。"
        if not sanitize_story_text(es.get("symbol_usage_policy") or ""):
            es["symbol_usage_policy"] = "象征物可出现，但默认不解释含义；只改变语境或动作功能。"
        es["body_signal_budget"] = _coerce_body_signal_budget(es.get("body_signal_budget", 1))
        if not sanitize_story_text(es.get("sensory_notes") or "") and (
            index == 0 or index == total - 1
        ):
            es["sensory_notes"] = _fallback_sensory_notes(
                location=clean_str(es.get("location")),
                forbidden_elements=forbidden_elements,
                is_opening=index == 0,
                is_closing=index == total - 1,
            )
        es["sensory_notes"] = _compact_sensory_notes(
            es.get("sensory_notes"),
            max_items=sensory_notes_max_items,
        )
        if not sanitize_story_text(es.get("required_outcome") or ""):
            if index == 0:
                es["required_outcome"] = opening_contract or "完成开场落地并明确当前行动方向。"
            elif index == total - 1:
                es["required_outcome"] = closing_contract or "在章末留下可直接承接的状态出口。"
            else:
                es["required_outcome"] = "让当前冲突产生新的可见变化。"
        if not es.get("owned_events"):
            es["owned_events"] = [
                sanitize_story_text(es.get("required_outcome") or es.get("summary") or "")
            ]
        if not es.get("owned_revelations"):
            revelation = sanitize_story_text(es.get("revelation_level") or "")
            if "不揭示" in revelation or "按本章 key_revelations" in revelation:
                es["owned_revelations"] = []
            else:
                es["owned_revelations"] = [revelation] if revelation else []
        if not es.get("owned_state_changes"):
            state_change = sanitize_story_text(es.get("exit_target_state") or "")
            es["owned_state_changes"] = [state_change] if state_change else []
        es["dependency_scene_ids"] = _normalize_string_list(es.get("dependency_scene_ids", []))
        if index > 0 and not es["dependency_scene_ids"]:
            es["dependency_scene_ids"] = [f"scene_{index:02d}"]
        es["parallel_group"] = clean_str(es.get("parallel_group"))
        es["draft_order"] = _coerce_non_negative_int(es.get("draft_order", index + 1)) or index + 1
        if not sanitize_story_text(es.get("entry_state") or ""):
            es["entry_state"] = "；".join(_normalize_string_list(es.get("entry_state_refs", [])))
        if not sanitize_story_text(es.get("exit_state") or ""):
            es["exit_state"] = sanitize_story_text(es.get("exit_target_state") or "")
        if not sanitize_story_text(es.get("handoff_to_next") or ""):
            es["handoff_to_next"] = sanitize_story_text(
                es.get("exit_state") or es.get("required_outcome") or ""
            )
        if not sanitize_story_text(es.get("exit_target_state") or ""):
            es["exit_target_state"] = (
                "局势较进入场景时发生可感知变化，并把压力顺势传给下一场。"
                if index < total - 1
                else "形成下一章可以直接接住的行动、情绪或信息出口。"
            )
        if not es.get("owned_state_changes"):
            state_change = sanitize_story_text(es.get("exit_target_state") or "")
            es["owned_state_changes"] = [state_change] if state_change else []
        if not sanitize_story_text(es.get("exit_state") or ""):
            es["exit_state"] = sanitize_story_text(es.get("exit_target_state") or "")
        if not sanitize_story_text(es.get("handoff_to_next") or ""):
            es["handoff_to_next"] = sanitize_story_text(
                es.get("exit_state") or es.get("required_outcome") or ""
            )
        enriched.append(es)
    if forbidden_elements:
        from novel_forge.pipeline.steps.continuity_eval_step import _detect_forbidden_elements

        _TEXT_FIELDS = ("sensory_notes", "required_outcome", "emotional_beat", "exit_target_state")
        for idx, scene in enumerate(enriched):
            for fld in _TEXT_FIELDS:
                val = scene.get(fld) or ""
                if not val:
                    continue
                hits = _detect_forbidden_elements(val, forbidden_elements)
                if hits:
                    _log.info(
                        "plan_forbidden_sanitize | scene=%d field=%s hits=%s -> cleared",
                        idx,
                        fld,
                        [(fe, m) for fe, m in hits],
                    )
                    scene[fld] = ""
        for idx, scene in enumerate(enriched):
            if not sanitize_story_text(scene.get("sensory_notes") or "") and (
                idx == 0 or idx == len(enriched) - 1
            ):
                scene["sensory_notes"] = _fallback_sensory_notes(
                    location=clean_str(scene.get("location")),
                    forbidden_elements=forbidden_elements,
                    is_opening=idx == 0,
                    is_closing=idx == len(enriched) - 1,
                )
            scene["sensory_notes"] = _compact_sensory_notes(
                scene.get("sensory_notes"),
                max_items=sensory_notes_max_items,
            )
            if not sanitize_story_text(scene.get("required_outcome") or ""):
                scene["required_outcome"] = (
                    opening_contract
                    if idx == 0
                    else closing_contract
                    if idx == len(enriched) - 1
                    else "让当前冲突产生新的可见变化。"
                )
            if not sanitize_story_text(scene.get("exit_target_state") or ""):
                scene["exit_target_state"] = (
                    "局势较进入场景时发生可感知变化，并把压力顺势传给下一场。"
                    if idx < len(enriched) - 1
                    else "形成下一章可以直接接住的行动、情绪或信息出口。"
                )
            if not sanitize_story_text(scene.get("conflict") or ""):
                scene["conflict"] = _fallback_scene_conflict(
                    idx,
                    len(enriched),
                    outline=outline,
                    scene=scene,
                )
            if not sanitize_story_text(scene.get("time_marker") or ""):
                scene["time_marker"] = _fallback_time_marker(
                    idx,
                    len(enriched),
                    outline=outline,
                )
    return enriched


def _patch_missing_beat_scenes(
    scenes: list[dict[str, Any]],
    outline_beats: list[str],
) -> list[dict[str, Any]]:
    """Merge uncovered outline beats into existing scenes without inventing new scenes."""

    covered: set[int] = set()
    for beat_idx, beat_text in enumerate(outline_beats):
        beat_tokens = {t for t in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", beat_text)}
        if not beat_tokens:
            covered.add(beat_idx)
            continue
        for scene in scenes:
            scene_text = "；".join(
                str(scene.get(key) or "")
                for key in (
                    "summary",
                    "purpose",
                    "required_outcome",
                    "exit_target_state",
                    "scene_goal",
                )
            )
            scene_text += "；".join(
                str(item) for item in list(scene.get("owned_events") or []) if item
            )
            overlap = sum(1 for t in beat_tokens if t in scene_text)
            if overlap >= max(1, len(beat_tokens) // 4):
                covered.add(beat_idx)
                break
    missing_indices = sorted(set(range(len(outline_beats))) - covered)
    if not missing_indices:
        return scenes

    patched = [_copy_scene(scene) for scene in scenes]
    if not patched:
        return scenes
    for beat_idx in missing_indices:
        beat_text = outline_beats[beat_idx]
        scene_idx = min(
            len(patched) - 1,
            max(0, int(beat_idx * len(patched) / max(1, len(outline_beats)))),
        )
        _merge_missing_beat_into_scene(patched[scene_idx], beat_idx, beat_text)
    for idx, scene in enumerate(patched, start=1):
        scene["scene_id"] = "scene_" + str(idx).zfill(2)
    _log.info("beat_coverage_merge | merged=%d uncovered beats", len(missing_indices))
    return patched


def _copy_scene(scene: Any) -> dict[str, Any]:
    if not isinstance(scene, dict):
        return {}
    copied = dict(scene)
    for key in ("required_characters", "character_motivations", "entry_state_refs"):
        value = copied.get(key)
        if isinstance(value, list):
            copied[key] = [dict(item) if isinstance(item, dict) else item for item in value]
    return copied


def _append_unique_text(existing: Any, addition: Any, *, max_chars: int = 360) -> str:
    first = sanitize_story_text(clean_str(existing))
    second = sanitize_story_text(clean_str(addition))
    if not second:
        return first[:max_chars]
    if not first:
        return second[:max_chars]
    if second in first or first in second:
        return first[:max_chars]
    return (first + "；" + second)[:max_chars]


def _append_unique_items(existing: Any, additions: Any, *, max_chars: int = 120) -> list[str]:
    items = _normalize_string_list(existing, max_chars=max_chars)
    for item in _normalize_string_list(additions, max_chars=max_chars):
        if item not in items:
            items.append(item)
    return items


def _append_character_motivations(existing: Any, additions: Any) -> list[Any]:
    result: list[Any] = list(existing) if isinstance(existing, list) else []
    seen: set[tuple[str, str]] = set()
    for item in result:
        if isinstance(item, dict):
            seen.add((clean_str(item.get("character")), clean_str(item.get("motivation"))))
        else:
            seen.add((clean_str(item), ""))
    if isinstance(additions, list):
        candidates = additions
    else:
        candidates = [additions] if additions else []
    for item in candidates:
        if isinstance(item, dict):
            marker = (clean_str(item.get("character")), clean_str(item.get("motivation")))
            if marker in seen:
                continue
            seen.add(marker)
            result.append(dict(item))
    return result


def _merge_scene_into(
    target: dict[str, Any],
    extra: dict[str, Any],
    *,
    sensory_notes_max_items: int = 2,
) -> None:
    for field, max_chars in (
        ("summary", 420),
        ("purpose", 320),
        ("conflict", 320),
        ("required_outcome", 420),
        ("exit_target_state", 360),
        ("relationship_dynamics", 280),
        ("emotional_beat", 260),
        ("sensory_notes", 260),
    ):
        merged = _append_unique_text(
            target.get(field),
            extra.get(field),
            max_chars=max_chars,
        )
        target[field] = (
            _compact_sensory_notes(merged, max_items=sensory_notes_max_items)
            if field == "sensory_notes"
            else merged
        )
    for field in ("location", "time_marker"):
        target[field] = _append_unique_text(target.get(field), extra.get(field), max_chars=180)
    target["required_characters"] = _append_unique_items(
        target.get("required_characters", []),
        extra.get("required_characters", []),
        max_chars=40,
    )
    target["entry_state_refs"] = _append_unique_items(
        target.get("entry_state_refs", []),
        extra.get("entry_state_refs", []),
        max_chars=120,
    )
    target["character_motivations"] = _append_character_motivations(
        target.get("character_motivations", []),
        extra.get("character_motivations", []),
    )
    target["target_words"] = _coerce_non_negative_int(
        target.get("target_words", 0)
    ) + _coerce_non_negative_int(extra.get("target_words", 0))


def _merge_missing_beat_into_scene(scene: dict[str, Any], beat_idx: int, beat_text: str) -> None:
    summary = sanitize_story_text(clean_str(beat_text))
    if not summary:
        return
    # Preserve the actual beat as an executable ownership anchor.  Do not
    # inject meta-text such as “补充节拍 3” into prose-facing summary/purpose
    # fields: it dilutes the writer's scene intent and can leak into drafts.
    scene["owned_events"] = _append_unique_items(
        scene.get("owned_events", []),
        [summary],
        max_chars=220,
    )


def _normalize_scene_target_words(scenes: list[dict[str, Any]], *, total_target: int) -> None:
    if not scenes or total_target <= 0:
        return
    count = len(scenes)
    min_floor = 300 if total_target >= count * 300 else max(120, total_target // count)
    default_words = max(min_floor, total_target // count)
    for scene in scenes:
        current = _coerce_non_negative_int(scene.get("target_words", 0))
        scene["target_words"] = default_words if current <= 0 else max(min_floor, current)
    total_allocated = sum(_coerce_non_negative_int(s.get("target_words", 0)) for s in scenes)
    if total_allocated <= 0:
        return
    tolerance = max(120, int(total_target * 0.08))
    if abs(total_allocated - total_target) <= tolerance:
        return
    ratio = total_target / total_allocated
    scaled = [
        max(min_floor, int(round(_coerce_non_negative_int(s.get("target_words", 0)) * ratio)))
        for s in scenes
    ]
    diff = total_target - sum(scaled)
    if diff != 0:
        order = sorted(range(count), key=lambda idx: scaled[idx], reverse=diff < 0)
        safety = 0
        while diff != 0 and safety < count * 4:
            changed = False
            for idx in order:
                if diff == 0:
                    break
                if diff > 0:
                    scaled[idx] += 1
                    diff -= 1
                    changed = True
                elif scaled[idx] > min_floor:
                    scaled[idx] -= 1
                    diff += 1
                    changed = True
            if not changed:
                break
            safety += 1
    for idx, scene in enumerate(scenes):
        scene["target_words"] = scaled[idx]
