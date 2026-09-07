"""Init-time canon seeding and narrative contract helpers."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from novel_forge.core.domain.world_context import extract_world_context

if TYPE_CHECKING:
    from novel_forge.story_kernel.schemas import StoryKernel

_log = logging.getLogger(__name__)


def _seed_canon_from_character_bible(
    canon_state: Any,
    character_bible: Any,
    story_bible: Any,
    narrative_contract: Any | None = None,
) -> StoryKernel:
    """Seed CanonState with initial relationships, world rules, and macro plot threads.

    This ensures Chapter 1 starts with a populated relationship network and
    world facts instead of an empty state. Narrative-contract plot threads are
    seeded as chapter-0 baseline threads so extract/canon updates can reuse
    stable IDs without exposing future foreshadowing as already planted.
    """
    from novel_forge.story_kernel.relationship_sync import sync_relationships_from_bible_to_kernel
    from novel_forge.story_kernel.schemas import Entity

    seeded_canon = (
        canon_state.model_copy(deep=True)
        if hasattr(canon_state, "model_copy")
        else deepcopy(canon_state)
    )

    # Seed character entries into entities list
    existing_char_names = {e.name for e in seeded_canon.entities if e.entity_type == "character"}
    for profile in character_bible.characters:
        if not profile.name:
            continue
        if profile.name not in existing_char_names:
            seed_notes = profile.personality or ""
            attrs: dict[str, Any] = {}
            if profile.gender:
                attrs["gender"] = profile.gender
            if getattr(profile, "social_status", None):
                attrs["social_status"] = profile.social_status
            if getattr(profile, "voice", None):
                attrs["voice"] = profile.voice
            if seed_notes:
                attrs["notes"] = seed_notes
            seeded_canon.entities.append(
                Entity(
                    entity_id=f"char_{profile.name}",
                    name=profile.name,
                    entity_type="character",
                    attributes=attrs,
                    description=seed_notes,
                )
            )
            existing_char_names.add(profile.name)

    synced_relationships = sync_relationships_from_bible_to_kernel(character_bible, seeded_canon)

    # Seed world facts from StoryBible.rules
    if hasattr(story_bible, "rules") and story_bible.rules:
        for i, rule in enumerate(story_bible.rules[:10]):
            rule_text = str(rule).strip()
            if rule_text:
                seeded_canon.set_world_rule(f"world_rule_{i + 1}", rule_text)

    seeded_plot_threads = _seed_canon_plot_threads(seeded_canon, narrative_contract)

    char_count = len([e for e in seeded_canon.entities if e.entity_type == "character"])
    _log.info(
        "Canon seeded: %d characters, %d relationships, %d world facts, %d plot threads",
        char_count,
        len(seeded_canon.relationships),
        len(seeded_canon.world_facts),
        seeded_plot_threads,
    )
    if synced_relationships:
        _log.info("Canon relationship sync seeded %d character pairs", synced_relationships)
    return seeded_canon


def _seed_canon_plot_threads(canon_state: Any, narrative_contract: Any | None) -> int:
    """Seed CanonState.plot_threads from init narrative-contract metadata."""

    from novel_forge.core.schemas.story_state import PlotThreadState

    contract = _canon_seed_contract_payload(narrative_contract)
    if not contract:
        return 0

    seeded = 0
    for index, raw_thread in enumerate(_iter_canon_seed_threads(contract), start=1):
        thread_id = _clean_text(
            raw_thread.get("thread_id")
            or raw_thread.get("id")
            or raw_thread.get("promise_id")
            or raw_thread.get("thread_name")
            or raw_thread.get("name")
            or raw_thread.get("title")
        )
        title = _clean_text(
            raw_thread.get("thread_name")
            or raw_thread.get("name")
            or raw_thread.get("title")
            or raw_thread.get("promise")
            or thread_id
        )
        if not title:
            continue
        if not thread_id:
            thread_id = f"init_thread_{index:03d}"
        existing_thread_ids = {t.thread_id for t in canon_state.plot_threads}
        if thread_id in existing_thread_ids:
            continue

        owners = _clean_text_list(
            raw_thread.get("owners")
            or raw_thread.get("characters")
            or raw_thread.get("involved_characters")
            or raw_thread.get("main_characters")
        )[:8]
        next_payoff_window = _thread_chapter_window(raw_thread)
        summary = _thread_summary(raw_thread)
        status = _clean_plot_thread_status(raw_thread.get("status"))
        canon_state.plot_threads.append(PlotThreadState(
            thread_id=thread_id,
            title=title[:120],
            status=status,
            owners=owners,
            last_touched_chapter=0,
            next_payoff_window=next_payoff_window[:120],
            blocking_condition=_clean_text(
                raw_thread.get("blocking_condition")
                or raw_thread.get("payoff_rule")
                or raw_thread.get("resolution")
            )[:180],
            summary=summary[:280],
        ))
        seeded += 1
    return seeded


def _canon_seed_contract_payload(payload: Any | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    raw_llm_contract = payload.get("llm_contract")
    try:
        if isinstance(raw_llm_contract, dict):
            return normalize_llm_narrative_contract(raw_llm_contract)
        return build_adjudication_narrative_contract(payload)
    except Exception as exc:
        _log.warning("canon_plot_thread_seed_contract_invalid | error=%s", exc)
        return {}


def _iter_canon_seed_threads(contract: dict[str, Any]) -> list[dict[str, Any]]:
    threads = [item for item in _coerce_list(contract.get("plot_threads")) if isinstance(item, dict)]
    for raw_promise in _coerce_list(contract.get("promise_plan")):
        if not isinstance(raw_promise, dict):
            continue
        promise = _clean_text(
            raw_promise.get("promise")
            or raw_promise.get("title")
            or raw_promise.get("description")
            or raw_promise.get("promise_id")
        )
        if not promise:
            continue
        setup_chapter = _coerce_positive_int(raw_promise.get("setup_chapter"))
        payoff_chapter = _coerce_positive_int(raw_promise.get("payoff_chapter"))
        threads.append(
            {
                "thread_id": raw_promise.get("promise_id") or raw_promise.get("thread_id"),
                "thread_name": promise,
                "description": raw_promise.get("description") or raw_promise.get("payoff_rule"),
                "setup_chapter": setup_chapter,
                "payoff_chapter": payoff_chapter,
                "resolution": raw_promise.get("payoff_rule"),
                "source": "narrative_contract.promise_plan",
            }
        )
    return threads[:30]


def _clean_plot_thread_status(value: Any) -> str:
    status = _clean_text(value).lower()
    allowed = {"active", "resolved", "dormant", "escalated", "advancing", "planted", "revealed"}
    return status if status in allowed else "active"


def _clean_text_list(value: Any) -> list[str]:
    return [text for text in (_clean_text(item) for item in _coerce_list(value)) if text]


def _thread_chapter_window(thread: dict[str, Any]) -> str:
    explicit = _clean_text(thread.get("chapters") or thread.get("next_payoff_window"))
    if explicit:
        return explicit
    start_chapter = _first_positive_int(thread.get("start_chapter"), thread.get("setup_chapter"))
    end_chapter = _first_positive_int(
        thread.get("end_chapter"),
        thread.get("resolve_chapter"),
        thread.get("payoff_chapter"),
    )
    if start_chapter and end_chapter and start_chapter != end_chapter:
        lo, hi = sorted((start_chapter, end_chapter))
        return f"{lo}-{hi}"
    chapter_numbers = _chapter_numbers_from_values(
        thread.get("chapter_numbers"),
        thread.get("involved_chapters"),
        thread.get("chapters"),
        thread.get("start_chapter"),
        thread.get("end_chapter"),
        thread.get("setup_chapter"),
        thread.get("introduce_chapter"),
        thread.get("resolve_chapter"),
        thread.get("payoff_chapter"),
        *[
            scene.get("chapter") or scene.get("chapter_number")
            for scene in _coerce_list(thread.get("key_scenes"))
            if isinstance(scene, dict)
        ],
    )
    return _chapter_range_label(chapter_numbers)


def _thread_summary(thread: dict[str, Any]) -> str:
    parts = [
        _clean_text(thread.get("description") or thread.get("summary")),
        _clean_text(thread.get("resolution") or thread.get("payoff_rule")),
        _clean_text(thread.get("source")),
    ]
    return "；".join(part for part in parts if part)


def _build_and_persist_narrative_contract(
    *,
    storage: Any,
    layout: Any,
    story_bible: Any,
    character_bible: Any,
    blueprint_data: dict[str, Any] | None,
    language: str = "zh",
    words_per_chapter: int | None = None,
    continuity_defaults: dict[str, Any] | None = None,
) -> None:
    """Build a narrative contract from init-time data (no LLM call).

    The contract captures persistent cross-chapter rules that should be
    enforced throughout the entire novel.

    This file is read at chapter-time and injected into bridge/plan contexts.
    """
    contract: dict[str, Any] = {}

    # World rules
    if hasattr(story_bible, "rules") and story_bible.rules:
        contract["world_rules"] = [str(r).strip() for r in story_bible.rules if str(r).strip()]

    # Tone & themes
    contract["tone"] = getattr(story_bible, "tone", "") or ""
    contract["themes"] = [
        str(t).strip() for t in getattr(story_bible, "themes", []) if str(t).strip()
    ]
    world_context = extract_world_context(story_bible)
    if world_context:
        contract["world_context"] = world_context

    # Character arc summaries
    character_arcs: list[dict[str, str]] = []
    for profile in character_bible.characters:
        if profile.arc and profile.role in ("protagonist", "antagonist", "deuteragonist"):
            character_arcs.append(
                {
                    "name": profile.name,
                    "role": profile.role,
                    "arc": profile.arc,
                }
            )
    if character_arcs:
        contract["character_arcs"] = character_arcs

    # From blueprint
    if blueprint_data:
        if blueprint_data.get("ending_strategy"):
            contract["ending_strategy"] = str(blueprint_data["ending_strategy"]).strip()
        if blueprint_data.get("key_turning_points"):
            contract["key_turning_points"] = [
                {
                    "chapter": tp.get("chapter_number") or tp.get("chapter", 0),
                    "description": str(tp.get("description", "")).strip(),
                }
                for tp in blueprint_data["key_turning_points"]
                if tp.get("description")
            ][:10]
        plot_threads = _plot_threads_from_blueprint(blueprint_data)
        if plot_threads:
            contract["plot_threads"] = plot_threads

    # Process-level continuity protocol defaults
    def _coerce_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
        try:
            iv = int(value)
        except Exception:
            return default
        return max(min_value, min(max_value, iv))

    def _coerce_text(value: Any, *, default: str, max_len: int = 120) -> str:
        text = str(value or "").strip()
        return text[:max_len] if text else default

    continuity_defaults = continuity_defaults or {}

    bridge_ratio = float(continuity_defaults.get("bridge_echo_ratio", 0.28) or 0.28)
    bridge_min_chars = _coerce_int(
        continuity_defaults.get("bridge_echo_min_chars"),
        default=450,
        min_value=200,
        max_value=2000,
    )
    bridge_max_chars = _coerce_int(
        continuity_defaults.get("bridge_echo_max_chars"),
        default=1400,
        min_value=300,
        max_value=4000,
    )
    if bridge_max_chars < bridge_min_chars:
        bridge_min_chars, bridge_max_chars = bridge_max_chars, bridge_min_chars

    bridge_echo_default = 900
    if words_per_chapter and words_per_chapter > 0:
        bridge_echo_default = max(
            bridge_min_chars,
            min(bridge_max_chars, int(words_per_chapter * bridge_ratio)),
        )

    transition_window_default = _coerce_int(
        continuity_defaults.get("location_transition_window_sentences"),
        default=3,
        min_value=1,
        max_value=6,
    )
    location_required_default = bool(continuity_defaults.get("location_transition_required", True))

    defaults: dict[str, Any] = {
        "location_transition_required": location_required_default,
        "location_transition_window_sentences": transition_window_default,
        "bridge_echo_window_chars": bridge_echo_default,
        "pov_visibility_rule": _coerce_text(
            continuity_defaults.get("pov_visibility_rule"),
            default="限知视角仅描写可观察事实，禁止直接写非POV角色内心。",
            max_len=160,
        ),
        "forbidden_repetition_rule": _coerce_text(
            continuity_defaults.get("forbidden_repetition_rule"),
            default=(
                "禁复用仅针对修辞性意象；人物、实体与剧情锚点不在此限。"
                "命中后必须替换为全新意象，不得使用近义改写。"
            ),
            max_len=160,
        ),
        "max_key_revelations_per_chapter": _coerce_int(
            continuity_defaults.get("max_key_revelations_per_chapter"),
            default=2,
            min_value=1,
            max_value=8,
        ),
        "min_unresolved_threads_to_keep": _coerce_int(
            continuity_defaults.get("min_unresolved_threads_to_keep"),
            default=1,
            min_value=0,
            max_value=6,
        ),
    }
    traditional_time_range_default = _coerce_text(
        continuity_defaults.get("traditional_time_ke_range"),
        default="一至四刻",
        max_len=32,
    )

    time_profile = (
        str(continuity_defaults.get("time_notation_profile", "auto") or "auto").strip().lower()
    )
    if time_profile not in {"auto", "traditional_cn", "locale_default"}:
        time_profile = "auto"

    if time_profile == "traditional_cn":
        defaults["traditional_time_ke_range"] = traditional_time_range_default
        defaults["time_notation_profile"] = "traditional_cn"
    elif time_profile == "locale_default":
        defaults["time_notation_profile"] = "locale_default"
    elif str(language or "zh").lower().startswith("zh"):
        defaults["traditional_time_ke_range"] = traditional_time_range_default
        defaults["time_notation_profile"] = "traditional_cn"
    else:
        defaults["time_notation_profile"] = "locale_default"

    raw_protocol = (
        blueprint_data.get("continuity_protocol") if isinstance(blueprint_data, dict) else None
    )
    protocol = dict(defaults)
    if isinstance(raw_protocol, dict):
        if "location_transition_required" in raw_protocol:
            protocol["location_transition_required"] = bool(
                raw_protocol.get("location_transition_required")
            )
        if "location_transition_window_sentences" in raw_protocol:
            protocol["location_transition_window_sentences"] = _coerce_int(
                raw_protocol.get("location_transition_window_sentences"),
                default=protocol["location_transition_window_sentences"],
                min_value=1,
                max_value=6,
            )
        if "bridge_echo_window_chars" in raw_protocol:
            protocol["bridge_echo_window_chars"] = _coerce_int(
                raw_protocol.get("bridge_echo_window_chars"),
                default=protocol["bridge_echo_window_chars"],
                min_value=300,
                max_value=2400,
            )
        if "traditional_time_ke_range" in raw_protocol and "traditional_time_ke_range" in protocol:
            protocol["traditional_time_ke_range"] = _coerce_text(
                raw_protocol.get("traditional_time_ke_range"),
                default=protocol["traditional_time_ke_range"],
                max_len=32,
            )
        if "pov_visibility_rule" in raw_protocol:
            protocol["pov_visibility_rule"] = _coerce_text(
                raw_protocol.get("pov_visibility_rule"),
                default=protocol["pov_visibility_rule"],
                max_len=160,
            )
        if "forbidden_repetition_rule" in raw_protocol:
            protocol["forbidden_repetition_rule"] = _coerce_text(
                raw_protocol.get("forbidden_repetition_rule"),
                default=protocol["forbidden_repetition_rule"],
                max_len=160,
            )
        if "max_key_revelations_per_chapter" in raw_protocol:
            protocol["max_key_revelations_per_chapter"] = _coerce_int(
                raw_protocol.get("max_key_revelations_per_chapter"),
                default=protocol["max_key_revelations_per_chapter"],
                min_value=1,
                max_value=8,
            )
        if "min_unresolved_threads_to_keep" in raw_protocol:
            protocol["min_unresolved_threads_to_keep"] = _coerce_int(
                raw_protocol.get("min_unresolved_threads_to_keep"),
                default=protocol["min_unresolved_threads_to_keep"],
                min_value=0,
                max_value=6,
            )

    protocol_enabled = bool(continuity_defaults.get("enabled", True))
    if protocol_enabled:
        contract["continuity_protocol"] = protocol

    try:
        storage.save_json(layout.narrative_contract_path, contract)
        _log.info("Narrative contract persisted with %d keys", len(contract))
    except Exception as exc:
        _log.warning("Failed to persist narrative contract: %s", exc)


def build_adjudication_narrative_contract(payload: Any) -> dict[str, Any]:
    """Project the deterministic init contract into the adjudication contract shape."""

    if not isinstance(payload, dict):
        payload = {}

    contract: dict[str, Any] = {
        "world_rules": [
            _normalize_world_rule_for_adjudication(item, index)
            for index, item in enumerate(_coerce_list(payload.get("world_rules")), start=1)
        ],
        "character_arcs": [
            _normalize_character_arc_for_adjudication(item, index)
            for index, item in enumerate(_coerce_list(payload.get("character_arcs")), start=1)
        ],
        "plot_threads": _coerce_list(payload.get("plot_threads")),
    }
    promise_plan = _coerce_list(payload.get("promise_plan"))
    if not promise_plan:
        promise_plan = _promise_plan_from_plot_threads(contract["plot_threads"])
    if promise_plan:
        contract["promise_plan"] = promise_plan
    for key in ("title", "ending_strategy", "key_turning_points", "themes", "world_context"):
        if payload.get(key):
            contract[key] = payload[key]
    contract["notes"] = "由规则型 narrative_contract 确定性派生，跳过初始化契约 LLM 重推导。"
    return normalize_llm_narrative_contract(contract)


def normalize_llm_narrative_contract(payload: Any) -> dict[str, Any]:
    """Normalize the LLM-authored narrative contract into stable downstream fields."""

    if not isinstance(payload, dict):
        payload = {}
    contract = dict(payload)
    contract["world_rules"] = _coerce_list(contract.get("world_rules"))
    contract["character_arcs"] = _coerce_list(contract.get("character_arcs"))
    contract["plot_threads"] = [
        _normalize_plot_thread(item)
        for item in _coerce_list(contract.get("plot_threads"))
        if isinstance(item, dict)
    ]
    if "promise_plan" in contract:
        contract["promise_plan"] = _coerce_list(contract.get("promise_plan"))
    return contract


def _normalize_world_rule_for_adjudication(raw: Any, index: int) -> dict[str, Any]:
    if isinstance(raw, dict):
        rule = dict(raw)
        rule.setdefault("rule_id", f"WR{index:03d}")
        rule.setdefault("binding_level", "hard")
        return rule
    text = _clean_text(raw)
    return {
        "rule_id": f"WR{index:03d}",
        "title": text[:24] or f"世界规则{index}",
        "content": text,
        "enforcement": "章节生成和契约裁判必须保持一致。",
        "forbidden_violations": "不得写出与该规则冲突的事件、能力或常识。",
        "binding_level": "hard",
    }


def _normalize_character_arc_for_adjudication(raw: Any, index: int) -> dict[str, Any]:
    if isinstance(raw, dict):
        arc = dict(raw)
        name = _clean_text(arc.get("char_name") or arc.get("name") or arc.get("character"))
        summary = _clean_text(arc.get("arc") or arc.get("arc_summary") or arc.get("description"))
        if name and "char_name" not in arc:
            arc["char_name"] = name
        if summary and "arc_stages" not in arc:
            arc["arc_stages"] = [
                {
                    "stage_name": "全书弧光",
                    "chapters": _clean_text(arc.get("chapters")),
                    "narrative_requirement": summary,
                }
            ]
        arc.setdefault("char_id", f"char_{index:03d}")
        arc.setdefault("completion_criteria", summary)
        return arc
    text = _clean_text(raw)
    return {
        "char_id": f"char_{index:03d}",
        "char_name": text[:24] or f"角色{index}",
        "arc_stages": [{"stage_name": "全书弧光", "chapters": "", "narrative_requirement": text}],
        "completion_criteria": text,
    }


def _promise_plan_from_plot_threads(plot_threads: list[Any]) -> list[dict[str, Any]]:
    promises: list[dict[str, Any]] = []
    for index, raw in enumerate(plot_threads, start=1):
        if not isinstance(raw, dict):
            continue
        thread = _normalize_plot_thread(raw)
        name = _clean_text(thread.get("thread_name") or thread.get("thread_id"))
        if not name:
            continue
        chapter_numbers = _chapter_numbers_from_values(
            thread.get("chapter_numbers"),
            thread.get("chapters"),
            thread.get("start_chapter"),
            thread.get("end_chapter"),
            thread.get("setup_chapter"),
            thread.get("payoff_chapter"),
        )
        setup = min(chapter_numbers) if chapter_numbers else 0
        payoff = max(chapter_numbers) if chapter_numbers else 0
        promise_id = _clean_text(thread.get("thread_id")) or f"promise_{index:03d}"
        promises.append(
            {
                "promise_id": promise_id,
                "promise": name,
                "setup_chapter": setup,
                "payoff_chapter": payoff,
                "payoff_rule": _clean_text(thread.get("resolution")) or "按章节契约逐步兑现。",
            }
        )
    return promises[:20]


def _plot_threads_from_blueprint(blueprint_data: dict[str, Any]) -> list[dict[str, Any]]:
    threads: list[dict[str, Any]] = []
    for raw in _coerce_list(blueprint_data.get("subplot_plan")):
        if not isinstance(raw, dict):
            continue
        name = _clean_text(raw.get("name") or raw.get("thread_name") or raw.get("title"))
        if not name:
            continue
        chapter_numbers = _chapter_numbers_from_values(
            raw.get("involved_chapters"),
            raw.get("chapter_numbers"),
            *[
                event.get("chapter_number") or event.get("chapter")
                for event in _coerce_list(raw.get("chapter_events"))
                if isinstance(event, dict)
            ],
            raw.get("resolution_chapter"),
        )
        thread = _normalize_plot_thread(
            {
                "thread_id": raw.get("thread_id") or raw.get("id") or name,
                "thread_name": name,
                "description": raw.get("description", ""),
                "priority": raw.get("priority", "normal"),
                "chapter_numbers": chapter_numbers,
                "key_scenes": [
                    {
                        "chapter": event.get("chapter_number") or event.get("chapter"),
                        "title": event.get("event") or event.get("title") or "支线节点",
                        "description": event.get("weave_note") or event.get("description") or "",
                    }
                    for event in _coerce_list(raw.get("chapter_events"))
                    if isinstance(event, dict)
                ],
                "resolution": raw.get("resolution_target")
                or raw.get("resolution_type")
                or (
                    f"第 {raw.get('resolution_chapter')} 章收束"
                    if raw.get("resolution_chapter")
                    else ""
                ),
                "source": "blueprint.subplot_plan",
            }
        )
        threads.append(thread)

    for raw in _coerce_list(blueprint_data.get("suspense_schedule")):
        if not isinstance(raw, dict):
            continue
        name = _clean_text(raw.get("suspense_id") or raw.get("thread_name") or raw.get("description"))
        if not name:
            continue
        introduce = raw.get("introduce_chapter") or raw.get("setup_chapter")
        resolve = raw.get("resolve_chapter") or raw.get("payoff_chapter")
        thread = _normalize_plot_thread(
            {
                "thread_id": raw.get("suspense_id") or name,
                "thread_name": name,
                "description": raw.get("description", ""),
                "priority": raw.get("urgency_level", "normal"),
                "start_chapter": introduce,
                "end_chapter": resolve,
                "resolution": (
                    f"第 {resolve} 章兑现" if _coerce_positive_int(resolve) else "未规划兑现章"
                ),
                "source": "blueprint.suspense_schedule",
            }
        )
        threads.append(thread)

    return threads[:20]


def _normalize_plot_thread(raw: dict[str, Any]) -> dict[str, Any]:
    thread = dict(raw)
    name = _clean_text(
        thread.get("thread_name")
        or thread.get("name")
        or thread.get("thread")
        or thread.get("title")
        or thread.get("thread_id")
    )
    if name and not _clean_text(thread.get("thread_name")):
        thread["thread_name"] = name

    chapter_numbers = _chapter_numbers_from_values(
        thread.get("chapter_numbers"),
        thread.get("involved_chapters"),
        thread.get("chapters"),
        thread.get("start_chapter"),
        thread.get("end_chapter"),
        thread.get("setup_chapter"),
        thread.get("introduce_chapter"),
        thread.get("resolve_chapter"),
        thread.get("payoff_chapter"),
        *[
            scene.get("chapter") or scene.get("chapter_number")
            for scene in _coerce_list(thread.get("key_scenes"))
            if isinstance(scene, dict)
        ],
    )
    if chapter_numbers and not _coerce_list(thread.get("chapter_numbers")):
        thread["chapter_numbers"] = chapter_numbers
    if not _clean_text(thread.get("chapters")):
        label = _explicit_thread_range_label(thread) or _chapter_range_label(chapter_numbers)
        if label:
            thread["chapters"] = label
    return thread


def _explicit_thread_range_label(thread: dict[str, Any]) -> str:
    start = _first_positive_int(
        thread.get("start_chapter"),
        thread.get("setup_chapter"),
        thread.get("introduce_chapter"),
    )
    end = _first_positive_int(
        thread.get("end_chapter"),
        thread.get("resolve_chapter"),
        thread.get("payoff_chapter"),
    )
    if not start or not end:
        return ""
    lo, hi = sorted((start, end))
    return str(lo) if lo == hi else f"{lo}-{hi}"


def _chapter_range_label(chapter_numbers: list[int]) -> str:
    if not chapter_numbers:
        return ""
    numbers = sorted(set(chapter_numbers))
    if len(numbers) == 1:
        return str(numbers[0])
    if numbers == list(range(numbers[0], numbers[-1] + 1)):
        return f"{numbers[0]}-{numbers[-1]}"
    return "、".join(str(item) for item in numbers[:12])


def _chapter_numbers_from_values(*values: Any) -> list[int]:
    numbers: list[int] = []
    for value in values:
        numbers.extend(_extract_chapter_numbers(value))
    return sorted(set(numbers))


def _extract_chapter_numbers(value: Any) -> list[int]:
    if value is None or value == "":
        return []
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value] if value > 0 else []
    if isinstance(value, float):
        iv = int(value)
        return [iv] if iv > 0 and iv == value else []
    if isinstance(value, (list, tuple, set)):
        numbers: list[int] = []
        for item in value:
            numbers.extend(_extract_chapter_numbers(item))
        return numbers
    if isinstance(value, dict):
        return _chapter_numbers_from_values(
            value.get("chapter"),
            value.get("chapter_number"),
            value.get("start_chapter"),
            value.get("end_chapter"),
            value.get("chapters"),
            value.get("chapter_numbers"),
        )
    text = str(value).strip()
    if not text:
        return []
    import re

    range_match = re.search(r"(\d+)\s*[-~至到]\s*(\d+)", text)
    if range_match:
        start = _coerce_positive_int(range_match.group(1))
        end = _coerce_positive_int(range_match.group(2))
        if start and end:
            lo, hi = sorted((start, end))
            if hi - lo <= 200:
                return list(range(lo, hi + 1))
            return [lo, hi]
    return [
        item
        for item in (_coerce_positive_int(match) for match in re.findall(r"\d+", text))
        if item
    ]


def _coerce_positive_int(value: Any) -> int:
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return 0
    return iv if iv > 0 else 0


def _first_positive_int(*values: Any) -> int:
    for value in values:
        iv = _coerce_positive_int(value)
        if iv:
            return iv
    return 0


def _coerce_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _clean_text(value: Any) -> str:
    return str(value or "").strip()
