"""Lightweight chapter-level narrative element progress tracking.

This module tracks chapter-level execution for ``element_focus`` with:
1) schedule recording after planning
2) rule-based hit/weak/miss evaluation after finalization
3) compact planning hints for subsequent chapters
4) optional low-frequency LLM arbitration for gray-zone results
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.services.generation import llm_helpers as llm_h
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens

_log = get_logger("pipeline.long.services.element_progress")

_NEGATIVE_MARKERS = (
    "缺失",
    "不足",
    "薄弱",
    "遗漏",
    "未体现",
    "未覆盖",
    "未推进",
    "偏离",
    "问题",
    "错误",
    "修复",
    "风险",
)

_ID_STOPWORDS = {
    "core",
    "required",
    "extension",
    "element",
    "elements",
    "narrative",
}

_PHRASE_STOPWORDS = {
    "本章",
    "阶段",
    "关键",
    "安排",
    "规划",
    "给出",
    "明确",
    "必须",
    "至少",
    "每个",
    "用于",
    "通过",
    "体现",
    "推进",
    "保持",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_ws(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _clean_terms(values: list[str], *, limit: int = 16) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        term = _normalize_ws(raw)
        if not term:
            continue
        term_lower = term.lower()
        if term_lower in seen:
            continue
        seen.add(term_lower)
        out.append(term)
        if len(out) >= limit:
            break
    return out


def _iter_text_values(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        text = _normalize_ws(raw)
        if not text:
            continue
        text_lower = text.lower()
        if text_lower in seen:
            continue
        seen.add(text_lower)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _split_id_terms(element_id: str) -> list[str]:
    parts: list[str] = []
    for token in re.split(r"[_\-\s]+", str(element_id or "").lower()):
        token = token.strip()
        if len(token) < 3 or token in _ID_STOPWORDS:
            continue
        parts.append(token)
    return parts


def _split_cn_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    for raw in re.split(r"[，,。；;：:、（）()《》“”\"'！？!\?\s/\\\-]+", str(text or "")):
        token = raw.strip()
        if len(token) < 2 or len(token) > 18 or token in _PHRASE_STOPWORDS:
            continue
        phrases.append(token)
    return phrases


def _build_anchor_terms(card: dict[str, Any]) -> list[str]:
    seeds: list[str] = []
    name = str(card.get("name", "") or "").strip()
    category = str(card.get("category", "") or "").strip()
    element_id = str(card.get("element_id", "") or "").strip()
    prompt_hint = str(card.get("prompt_hint", "") or "").strip()
    description = str(card.get("description", "") or "").strip()
    implementation_guide = str(card.get("implementation_guide", "") or "").strip()
    verification_anchors = _iter_text_values(card.get("verification_anchors", []), limit=8)

    if name:
        seeds.append(name)
    if category:
        seeds.append(category)
    if element_id:
        seeds.extend(_split_id_terms(element_id))
    seeds.extend(_split_cn_phrases(prompt_hint)[:8])
    seeds.extend(_split_cn_phrases(description)[:6])
    seeds.extend(_split_cn_phrases(implementation_guide)[:6])
    seeds.extend(verification_anchors)
    return _clean_terms(seeds, limit=24)


def _truncate_text(text: str, *, limit: int = 900) -> str:
    compact = _normalize_ws(text)
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit].rstrip()}…"


def _flatten_plan_text(plan: Any) -> str:
    if plan is None:
        return ""
    chunks: list[str] = []
    scene_intents = list(getattr(plan, "scene_intents", []) or [])
    for scene in scene_intents:
        chunks.extend(
            [
                getattr(scene, "summary", ""),
                getattr(scene, "purpose", ""),
                getattr(scene, "conflict", ""),
                getattr(scene, "required_outcome", ""),
                getattr(scene, "exit_target_state", ""),
                getattr(scene, "relationship_dynamics", ""),
                getattr(scene, "emotional_beat", ""),
                getattr(scene, "sensory_notes", ""),
                getattr(scene, "location", ""),
                getattr(scene, "time_marker", ""),
            ]
        )
    chunks.extend(list(getattr(plan, "required_state_transitions", []) or []))
    chunks.extend(list(getattr(plan, "relationship_evolution", []) or []))
    chunks.extend(list(getattr(plan, "key_revelations", []) or []))
    chunks.extend(
        [
            getattr(plan, "opening_contract", ""),
            getattr(plan, "closing_contract", ""),
            getattr(plan, "emotional_arc", ""),
        ]
    )
    return _normalize_ws(" ".join(str(item or "") for item in chunks))


def _flatten_quality_text(
    alignment_report: Any | None,
    chapter_repair_report: Any | None,
    continuity_report: Any | None,
) -> str:
    chunks: list[str] = []
    if alignment_report is not None:
        chunks.append(getattr(alignment_report, "summary", ""))
        chunks.extend(list(getattr(alignment_report, "missing_main_points", []) or []))
        chunks.extend(list(getattr(alignment_report, "weak_subplot_points", []) or []))
        chunks.extend(list(getattr(alignment_report, "repair_actions", []) or []))
    if chapter_repair_report is not None:
        chunks.append(getattr(chapter_repair_report, "summary", ""))
        chunks.extend(list(getattr(chapter_repair_report, "factual_errors", []) or []))
        chunks.extend(list(getattr(chapter_repair_report, "continuity_errors", []) or []))
        chunks.extend(list(getattr(chapter_repair_report, "expression_errors", []) or []))
        chunks.extend(list(getattr(chapter_repair_report, "repair_actions", []) or []))
    if continuity_report is not None:
        chunks.append(getattr(continuity_report, "summary", ""))
        for issue in list(getattr(continuity_report, "issues", []) or []):
            chunks.append(getattr(issue, "summary", ""))
            chunks.extend(list(getattr(issue, "fix_actions", []) or []))
    return _normalize_ws(" ".join(str(item or "") for item in chunks))


def _recompute_totals(payload: dict[str, Any]) -> None:
    chapters = payload.get("chapters", {})
    if not isinstance(chapters, dict):
        payload["totals"] = {"scheduled": 0, "hit": 0, "weak": 0, "miss": 0}
        payload["pending_element_ids"] = []
        payload["arbiter_totals"] = {"runs": 0, "reviewed": 0, "changed": 0}
        return

    totals = {"scheduled": 0, "hit": 0, "weak": 0, "miss": 0}
    arbiter_totals = {"runs": 0, "reviewed": 0, "changed": 0}
    latest_by_element: dict[str, tuple[int, str]] = {}
    for chapter_key, chapter_data in chapters.items():
        if not isinstance(chapter_data, dict):
            continue
        try:
            chapter_no = int(chapter_data.get("chapter_number", chapter_key) or 0)
        except Exception as _exc:
            _log.debug("element_progress_chapter_no_parse_skip | key=%s | %s", chapter_key, _exc)
            chapter_no = 0
        arbiter = chapter_data.get("arbiter")
        if isinstance(arbiter, dict):
            try:
                reviewed = int(arbiter.get("reviewed_count", 0) or 0)
            except Exception as _exc:
                _log.debug("element_progress_arbiter_reviewed_skip | %s", _exc)
                reviewed = 0
            try:
                changed = int(arbiter.get("changed_count", 0) or 0)
            except Exception as _exc:
                _log.debug("element_progress_arbiter_changed_skip | %s", _exc)
                changed = 0
            if reviewed > 0:
                arbiter_totals["runs"] += 1
            arbiter_totals["reviewed"] += max(0, reviewed)
            arbiter_totals["changed"] += max(0, changed)
        for item in list(chapter_data.get("results", []) or []):
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "") or "").strip().lower()
            if status in totals:
                totals[status] += 1
            eid = str(item.get("element_id", "") or "").strip()
            if not eid:
                continue
            prev = latest_by_element.get(eid)
            if prev is None or chapter_no >= prev[0]:
                latest_by_element[eid] = (chapter_no, status)

    payload["totals"] = totals
    payload["pending_element_ids"] = sorted(
        eid
        for eid, (_, status) in latest_by_element.items()
        if status in {"scheduled", "weak", "miss"}
    )
    payload["arbiter_totals"] = arbiter_totals


def _default_payload() -> dict[str, Any]:
    now = _now_iso()
    return {
        "schema_version": "1.0",
        "updated_at": now,
        "chapters": {},
        "totals": {"scheduled": 0, "hit": 0, "weak": 0, "miss": 0},
        "pending_element_ids": [],
        "arbiter_totals": {"runs": 0, "reviewed": 0, "changed": 0},
    }


def load_element_progress(storage: Any, layout: Any) -> dict[str, Any]:
    path = layout.element_progress_path
    if not storage.exists(path):
        return _default_payload()
    try:
        payload = storage.load_json(path)
    except Exception as exc:
        _log.warning("element_progress_load_failed | error=%s", exc)
        return _default_payload()
    if not isinstance(payload, dict):
        return _default_payload()
    payload.setdefault("schema_version", "1.0")
    payload.setdefault("chapters", {})
    payload.setdefault("totals", {"scheduled": 0, "hit": 0, "weak": 0, "miss": 0})
    payload.setdefault("pending_element_ids", [])
    payload.setdefault("arbiter_totals", {"runs": 0, "reviewed": 0, "changed": 0})
    _recompute_totals(payload)
    return payload


def _extract_latest_reason(item: dict[str, Any]) -> str:
    arbiter = item.get("arbiter")
    if isinstance(arbiter, dict):
        reason = _normalize_ws(arbiter.get("reason", ""))
        if reason:
            return reason
    evidence = item.get("evidence")
    if isinstance(evidence, dict):
        penalties = list(evidence.get("penalties", []) or [])
        if penalties:
            first = _normalize_ws(penalties[0])
            if first:
                return f"规则信号：{first}"
    status = str(item.get("status", "") or "").strip().lower()
    if status == "miss":
        return "缺少可验证的计划或正文命中证据"
    if status == "weak":
        return "命中证据偏弱，需要后续章节强化"
    return "未记录具体原因"


def save_element_progress(storage: Any, layout: Any, payload: dict[str, Any]) -> None:
    payload["updated_at"] = _now_iso()
    _recompute_totals(payload)
    storage.save_json(layout.element_progress_path, payload)


def _safe_focus_ids(value: Any, *, max_items: int = 3) -> list[str]:
    focus_ids: list[str] = []
    seen: set[str] = set()
    for raw in list(value or []):
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        focus_ids.append(item)
        if len(focus_ids) >= max_items:
            break
    return focus_ids


def record_schedule(
    storage: Any,
    layout: Any,
    *,
    chapter_number: int,
    scheduled_ids: list[str],
    focus_source: str,
) -> dict[str, Any]:
    payload = load_element_progress(storage, layout)
    chapters = payload.setdefault("chapters", {})
    chapter_key = str(int(chapter_number))
    now = _now_iso()
    focus_ids = _safe_focus_ids(scheduled_ids, max_items=3)
    chapter_data = chapters.get(chapter_key)
    if not isinstance(chapter_data, dict):
        chapter_data = {"chapter_number": int(chapter_number)}

    chapter_data.update(
        {
            "chapter_number": int(chapter_number),
            "focus_source": str(focus_source or "outline"),
            "scheduled_element_ids": focus_ids,
            "scheduled_at": now,
            "updated_at": now,
            "results": [
                {
                    "element_id": eid,
                    "status": "scheduled",
                    "score": 0.0,
                    "evidence": {"plan_hits": [], "text_hits": [], "penalties": []},
                }
                for eid in focus_ids
            ],
        }
    )
    chapters[chapter_key] = chapter_data
    save_element_progress(storage, layout, payload)
    return chapter_data


def build_planning_hint(
    storage: Any,
    layout: Any,
    *,
    chapter_number: int,
    max_items: int = 3,
    lookback_chapters: int = 0,
    extension_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    payload = load_element_progress(storage, layout)
    chapters = payload.get("chapters", {})
    if not isinstance(chapters, dict) or not chapters:
        return None

    # lookback_chapters=0 means scan all history (original behavior)
    _oldest_ch = max(1, chapter_number - lookback_chapters) if lookback_chapters > 0 else 1

    focus_score: dict[str, float] = {}
    focus_meta: dict[str, dict[str, Any]] = {}
    for key, chapter_data in chapters.items():
        if not isinstance(chapter_data, dict):
            continue
        try:
            ch_no = int(chapter_data.get("chapter_number", key) or 0)
        except Exception as _exc:
            _log.debug("element_progress_recent_ch_no_parse_skip | key=%s | %s", key, _exc)
            ch_no = 0
        if ch_no <= 0 or ch_no >= int(chapter_number) or ch_no < _oldest_ch:
            continue
        distance = max(1, int(chapter_number) - ch_no)
        recency = 1.0 / float(distance)
        for item in list(chapter_data.get("results", []) or []):
            if not isinstance(item, dict):
                continue
            eid = str(item.get("element_id", "") or "").strip()
            status = str(item.get("status", "") or "").strip().lower()
            if not eid:
                continue
            base = 0.0
            if status == "miss":
                base = 2.0
            elif status == "weak":
                base = 1.0
            elif status == "scheduled":
                base = 0.4
            if base <= 0:
                continue
            focus_score[eid] = focus_score.get(eid, 0.0) + base + recency
            meta = focus_meta.setdefault(
                eid,
                {
                    "last_chapter": ch_no,
                    "counts": {"miss": 0, "weak": 0, "scheduled": 0},
                    "latest_reason": "",
                    "miss_reason": "",
                    "remediation_hint": "",
                },
            )
            meta["last_chapter"] = max(int(meta.get("last_chapter", 0) or 0), ch_no)
            counts = meta.setdefault("counts", {})
            counts[status] = int(counts.get(status, 0) or 0) + 1
            if status in {"miss", "weak"} and ch_no >= int(meta.get("last_reason_chapter", 0) or 0):
                meta["last_reason_chapter"] = ch_no
                meta["latest_reason"] = _extract_latest_reason(item)
                meta["miss_reason"] = _normalize_ws(item.get("miss_reason", ""))
                meta["remediation_hint"] = _normalize_ws(item.get("remediation_hint", ""))

    if extension_ids:
        scheduled_ever: set[str] = set()
        for chapter_data in chapters.values():
            if isinstance(chapter_data, dict):
                for eid in chapter_data.get("scheduled_element_ids", []) or []:
                    scheduled_ever.add(str(eid).strip())
        for eid in extension_ids:
            eid = str(eid).strip()
            if eid and eid not in scheduled_ever:
                focus_score[eid] = focus_score.get(eid, 0.0) + 1.5
                focus_meta.setdefault(
                    eid,
                    {
                        "last_chapter": 0,
                        "counts": {"miss": 0, "weak": 0, "scheduled": 0},
                        "latest_reason": "",
                        "miss_reason": "",
                        "remediation_hint": "",
                    },
                )

    if not focus_score:
        return None

    ranked = sorted(focus_score.items(), key=lambda x: x[1], reverse=True)
    recommended = [eid for eid, _ in ranked[:max_items]]
    missed = [
        {
            "element_id": eid,
            "last_chapter": focus_meta[eid]["last_chapter"],
            "count": int(focus_meta[eid]["counts"].get("miss", 0) or 0),
            "latest_reason": str(focus_meta[eid].get("latest_reason", "") or ""),
            "miss_reason": str(focus_meta[eid].get("miss_reason", "") or ""),
            "remediation_hint": str(focus_meta[eid].get("remediation_hint", "") or ""),
        }
        for eid, _ in ranked
        if int(focus_meta.get(eid, {}).get("counts", {}).get("miss", 0) or 0) > 0
    ][:max_items]
    weak = [
        {
            "element_id": eid,
            "last_chapter": focus_meta[eid]["last_chapter"],
            "count": int(focus_meta[eid]["counts"].get("weak", 0) or 0),
            "latest_reason": str(focus_meta[eid].get("latest_reason", "") or ""),
            "miss_reason": str(focus_meta[eid].get("miss_reason", "") or ""),
            "remediation_hint": str(focus_meta[eid].get("remediation_hint", "") or ""),
        }
        for eid, _ in ranked
        if int(focus_meta.get(eid, {}).get("counts", {}).get("weak", 0) or 0) > 0
    ][:max_items]
    return {
        "summary": f"近几章要素执行待加强：建议优先关注 {', '.join(recommended)}",
        "recommended_focus_ids": recommended,
        "recent_missed": missed,
        "recent_weak": weak,
    }


def suggest_dynamic_focus_ids(
    *,
    chapter_outline: Any,
    extension_ids: list[str],
    planning_hint: dict[str, Any] | None,
    max_items: int = 3,
) -> list[str]:
    current_focus = _safe_focus_ids(
        getattr(chapter_outline, "element_focus", []), max_items=max_items
    )
    if current_focus:
        return []
    if not isinstance(planning_hint, dict):
        return []
    recommended = _safe_focus_ids(
        planning_hint.get("mandated_focus_ids", [])
        or planning_hint.get("recommended_focus_ids", []),
        max_items=max_items,
    )
    if not recommended:
        return []
    allowed = set(_safe_focus_ids(extension_ids, max_items=64))
    return [eid for eid in recommended if eid in allowed][:max_items]


def _element_score_thresholds(
    settings: Any | None,
    *,
    category: str = "",
    verification_mode: str = "anchor",
) -> tuple[float, float]:
    if settings is None:
        hit = 1.6
        weak = 0.6
    else:
        try:
            hit = float(getattr(settings, "element_progress_hit_threshold", 1.6))
        except Exception as _exc:
            _log.debug("element_progress_hit_threshold_fallback | %s", _exc)
            hit = 1.6
        try:
            weak = float(getattr(settings, "element_progress_weak_threshold", 0.6))
        except Exception as _exc:
            _log.debug("element_progress_weak_threshold_fallback | %s", _exc)
            weak = 0.6
    abstract_categories = ("文学", "主题", "关系", "叙事节奏", "场景设计")
    if verification_mode in {"structural", "llm"} or any(
        marker in category for marker in abstract_categories
    ):
        hit -= 0.25
        weak -= 0.1
    return max(0.0, hit), max(0.0, min(weak, hit))


_TERM_WEIGHTS = {
    "name": 1.0,
    "category": 0.3,
    "prompt_hint": 0.8,
    "description": 0.5,
    "implementation_guide": 0.45,
    "verification_anchor": 0.7,
    "element_id": 0.4,
}


def _build_weighted_terms(card: dict[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    name = str(card.get("name", "") or "").strip()
    if name:
        result.append((name, "name"))
    category = str(card.get("category", "") or "").strip()
    if category:
        result.append((category, "category"))
    prompt_hint = str(card.get("prompt_hint", "") or "").strip()
    for phrase in _split_cn_phrases(prompt_hint)[:8]:
        result.append((phrase, "prompt_hint"))
    description = str(card.get("description", "") or "").strip()
    for phrase in _split_cn_phrases(description)[:6]:
        result.append((phrase, "description"))
    implementation_guide = str(card.get("implementation_guide", "") or "").strip()
    for phrase in _split_cn_phrases(implementation_guide)[:6]:
        result.append((phrase, "implementation_guide"))
    for anchor in _iter_text_values(card.get("verification_anchors", []), limit=8):
        result.append((anchor, "verification_anchor"))
    element_id = str(card.get("element_id", "") or "").strip()
    for token in _split_id_terms(element_id):
        result.append((token, "element_id"))
    return result


def _term_in_text(term: str, text: str) -> bool:
    if not term or not text:
        return False
    if term not in text:
        return False
    if len(term) >= 4:
        return True
    idx = text.find(term)
    while idx != -1:
        prev_ok = True
        next_ok = True
        if idx > 0:
            prev_char = text[idx - 1]
            if "\u4e00" <= prev_char <= "\u9fff":
                prev_ok = False
        end = idx + len(term)
        if end < len(text):
            next_char = text[end]
            if "\u4e00" <= next_char <= "\u9fff":
                next_ok = False
        if prev_ok or next_ok:
            return True
        idx = text.find(term, idx + 1)
    return False


_STRUCTURAL_ANCHOR_HINTS: dict[str, tuple[str, ...]] = {
    "新出现的可记录物件": ("物件", "账册", "纸条", "钥匙", "照片", "痕迹", "记录"),
    "未被解释的对话片段": ("没有解释", "话没说完", "停了一下", "欲言又止"),
    "角色的异常行为记录": ("反常", "异常", "避开", "迟疑", "慌乱", "遮掩"),
    "情绪转折点": ("忽然", "终于", "却", "转身", "僵住", "沉默"),
    "回暖窗口": ("缓和", "松动", "回暖", "低声", "递给", "靠近"),
    "障碍升级事件": ("阻止", "误会", "第三人", "压力", "拒绝", "代价"),
    "未直说的情绪段落": ("没说", "没有回答", "别开眼", "垂下眼", "握紧", "沉默"),
    "环境细节承载心理": ("窗外", "雨声", "灯影", "风声", "杯沿", "门缝", "尘埃"),
    "对话停顿或省略": ("……", "...", "停顿", "顿了顿", "话到一半", "没再说"),
}


def _structural_terms_for_anchor(anchor: str) -> list[str]:
    terms = list(_STRUCTURAL_ANCHOR_HINTS.get(anchor, ()))
    terms.extend(_split_cn_phrases(anchor)[:4])
    return _clean_terms(terms, limit=8)


def _structural_match_score(
    card: dict[str, Any],
    *,
    plan_lower: str,
    chapter_lower: str,
) -> tuple[float, list[str]]:
    anchors = _iter_text_values(card.get("verification_anchors", []), limit=8)
    if not anchors:
        return 0.0, []

    score = 0.0
    hits: list[str] = []
    for anchor in anchors:
        terms = _structural_terms_for_anchor(anchor)
        if not terms:
            continue
        plan_hit = any(_term_in_text(term.lower(), plan_lower) for term in terms)
        text_hit = any(_term_in_text(term.lower(), chapter_lower) for term in terms)
        if plan_hit:
            score += 0.35
        if text_hit:
            score += 0.7
        if plan_hit or text_hit:
            hits.append(anchor)
    return score, hits[:6]


def _verification_mode(card: dict[str, Any]) -> str:
    mode = str(card.get("verification_mode", "anchor") or "anchor").strip().lower()
    if mode in {"anchor", "structural", "llm"}:
        return mode
    return "anchor"


def _miss_reason_for(card: dict[str, Any], evidence: dict[str, Any]) -> str:
    anchors = _iter_text_values(card.get("verification_anchors", []), limit=3)
    if anchors:
        missing = [
            anchor
            for anchor in anchors
            if anchor not in set(evidence.get("structural_hits", []) or [])
        ]
        if missing:
            return f"未检测到{'、'.join(missing[:3])}"
    if not evidence.get("text_hits"):
        return "未检测到正文层可验证落实证据"
    return "正文证据不足，仍需后续章节强化"


def _remediation_hint_for(card: dict[str, Any]) -> str:
    guide = _normalize_ws(card.get("implementation_guide", ""))
    if guide:
        return guide[:220]
    hint = _normalize_ws(card.get("prompt_hint", ""))
    if hint:
        return hint[:220]
    anchors = _iter_text_values(card.get("verification_anchors", []), limit=3)
    if anchors:
        return f"下一章需补足：{'、'.join(anchors)}。"
    return "下一章需把该要素转译为具体动作、对白、冲突或感官细节。"


def _evaluate_one_element(
    *,
    element_id: str,
    card: dict[str, Any] | None,
    plan_text: str,
    chapter_text: str,
    quality_text: str,
    alignment_score: float,
    continuity_score: float,
    chapter_repair_issue_count: int,
    settings: Any | None = None,
) -> dict[str, Any]:
    if not isinstance(card, dict):
        return {
            "element_id": element_id,
            "status": "weak",
            "score": 0.5,
            "evidence": {"plan_hits": [], "text_hits": [], "penalties": ["missing_element_card"]},
        }

    plan_lower = plan_text.lower()
    chapter_lower = chapter_text.lower()
    quality_lower = quality_text.lower()

    plan_hits: list[str] = []
    text_hits: list[str] = []
    structural_hits: list[str] = []
    score = 0.0
    verification_mode = _verification_mode(card)

    for term, source in _build_weighted_terms(card):
        term_l = term.lower()
        if not term_l:
            continue
        weight = _TERM_WEIGHTS.get(source, 0.5)
        if _term_in_text(term_l, plan_lower):
            plan_hits.append(term)
            score += 0.7 * weight
        if _term_in_text(term_l, chapter_lower):
            text_hits.append(term)
            score += 1.0 * weight

    if verification_mode in {"structural", "llm"}:
        structural_score, structural_hits = _structural_match_score(
            card,
            plan_lower=plan_lower,
            chapter_lower=chapter_lower,
        )
        score += structural_score

    penalties: list[str] = []
    if verification_mode == "llm":
        penalties.append("requires_llm_arbiter")
    has_negative = any(flag in quality_text for flag in _NEGATIVE_MARKERS)
    if has_negative and any(
        term.lower() in quality_lower for term in _build_anchor_terms(card)[:8]
    ):
        score -= 0.8
        penalties.append("quality_negative_match")
    if alignment_score > 0 and alignment_score < 7.0:
        score -= 0.2
        penalties.append("low_alignment_score")
    if continuity_score > 0 and continuity_score < 7.0:
        score -= 0.2
        penalties.append("low_continuity_score")
    if chapter_repair_issue_count >= 3:
        score -= 0.2
        penalties.append("chapter_repair_issues")

    hit_threshold, weak_threshold = _element_score_thresholds(
        settings,
        category=str(card.get("category", "") or ""),
        verification_mode=verification_mode,
    )
    score = round(max(0.0, score), 2)
    if score >= hit_threshold:
        status = "hit"
    elif score >= weak_threshold:
        status = "weak"
    else:
        status = "miss"
    if verification_mode == "llm" and status == "miss":
        status = "weak"
        score = max(score, weak_threshold)
    evidence = {
        "plan_hits": plan_hits[:6],
        "text_hits": text_hits[:6],
        "structural_hits": structural_hits[:6],
        "penalties": penalties,
    }
    miss_reason = _miss_reason_for(card, evidence) if status in {"weak", "miss"} else ""
    remediation_hint = _remediation_hint_for(card) if status in {"weak", "miss"} else ""
    return {
        "element_id": element_id,
        "status": status,
        "score": score,
        "verification_mode": verification_mode,
        "miss_reason": miss_reason,
        "remediation_hint": remediation_hint,
        "evidence": evidence,
    }


def finalize_chapter_progress(
    storage: Any,
    layout: Any,
    *,
    chapter_number: int,
    fallback_scheduled_ids: list[str],
    focus_source: str,
    element_cards_by_id: dict[str, dict[str, Any]],
    plan: Any,
    chapter_text: str,
    alignment_report: Any | None,
    chapter_repair_report: Any | None,
    continuity_report: Any | None,
    settings: Any | None = None,
) -> dict[str, Any] | None:
    payload = load_element_progress(storage, layout)
    chapters = payload.setdefault("chapters", {})
    chapter_key = str(int(chapter_number))
    chapter_data = chapters.get(chapter_key)
    if not isinstance(chapter_data, dict):
        chapter_data = {"chapter_number": int(chapter_number)}

    scheduled_ids = _safe_focus_ids(
        chapter_data.get("scheduled_element_ids", []) or fallback_scheduled_ids,
        max_items=3,
    )
    if not scheduled_ids:
        return None

    plan_text = _flatten_plan_text(plan)
    quality_text = _flatten_quality_text(alignment_report, chapter_repair_report, continuity_report)
    chapter_text = _normalize_ws(chapter_text)
    alignment_score = float(getattr(alignment_report, "alignment_score", 0.0) or 0.0)
    continuity_score = float(getattr(continuity_report, "continuity_score", 0.0) or 0.0)
    chapter_repair_issue_count = 0
    if chapter_repair_report is not None:
        chapter_repair_issue_count += len(
            list(getattr(chapter_repair_report, "factual_errors", []) or [])
        )
        chapter_repair_issue_count += len(
            list(getattr(chapter_repair_report, "continuity_errors", []) or [])
        )
        chapter_repair_issue_count += len(
            list(getattr(chapter_repair_report, "expression_errors", []) or [])
        )

    results: list[dict[str, Any]] = []
    for eid in scheduled_ids:
        results.append(
            _evaluate_one_element(
                element_id=eid,
                card=element_cards_by_id.get(eid),
                plan_text=plan_text,
                chapter_text=chapter_text,
                quality_text=quality_text,
                alignment_score=alignment_score,
                continuity_score=continuity_score,
                chapter_repair_issue_count=chapter_repair_issue_count,
                settings=settings,
            )
        )

    now = _now_iso()
    summary = {
        "scheduled": len(scheduled_ids),
        "hit": len([r for r in results if r.get("status") == "hit"]),
        "weak": len([r for r in results if r.get("status") == "weak"]),
        "miss": len([r for r in results if r.get("status") == "miss"]),
    }
    chapter_data.update(
        {
            "chapter_number": int(chapter_number),
            "focus_source": str(chapter_data.get("focus_source") or focus_source or "outline"),
            "scheduled_element_ids": scheduled_ids,
            "results": results,
            "evaluated_at": now,
            "updated_at": now,
            "summary": summary,
            "quality_snapshot": {
                "alignment_score": round(alignment_score, 2),
                "continuity_score": round(continuity_score, 2),
                "chapter_repair_issue_count": int(chapter_repair_issue_count),
            },
        }
    )
    chapters[chapter_key] = chapter_data
    save_element_progress(storage, layout, payload)
    return chapter_data


def _is_arbiter_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "element_progress_llm_arbiter_enabled", False))


def _arbiter_score_range(settings: Any) -> tuple[float, float]:
    try:
        low = float(getattr(settings, "element_progress_llm_gray_score_low", 0.8))
    except Exception as _exc:
        _log.debug("element_progress_gray_low_fallback | %s", _exc)
        low = 0.8
    try:
        high = float(getattr(settings, "element_progress_llm_gray_score_high", 1.4))
    except Exception as _exc:
        _log.debug("element_progress_gray_high_fallback | %s", _exc)
        high = 1.4
    if high < low:
        low, high = high, low
    return low, high


def _arbiter_max_items(settings: Any) -> int:
    return max(
        0, int(getattr(settings, "element_progress_llm_arbiter_max_items_per_chapter", 3) or 0)
    )


def _candidate_for_arbiter(item: dict[str, Any], *, low: float, high: float) -> bool:
    status = str(item.get("status", "") or "").strip().lower()
    if status not in {"hit", "weak", "miss"}:
        return False
    if str(item.get("verification_mode", "") or "").strip().lower() == "llm":
        return True
    try:
        score = float(item.get("score", 0.0) or 0.0)
    except Exception as _exc:
        _log.debug("element_progress_score_parse_skip | %s", _exc)
        return False
    return low <= score <= high


def _arbiter_prompt_context(
    *,
    element_id: str,
    card: dict[str, Any],
    rule_item: dict[str, Any],
    plan_text: str,
    chapter_text: str,
    quality_text: str,
) -> dict[str, Any]:
    return {
        "element": {
            "element_id": element_id,
            "name": str(card.get("name", "") or ""),
            "category": str(card.get("category", "") or ""),
            "prompt_hint": str(card.get("prompt_hint", "") or ""),
            "description": str(card.get("description", "") or ""),
            "implementation_guide": str(card.get("implementation_guide", "") or ""),
            "verification_anchors": _iter_text_values(card.get("verification_anchors", []), limit=8),
            "verification_mode": _verification_mode(card),
        },
        "rule_eval": {
            "status": str(rule_item.get("status", "") or ""),
            "score": float(rule_item.get("score", 0.0) or 0.0),
            "evidence": rule_item.get("evidence", {}),
        },
        "context": {
            "plan_excerpt": _truncate_text(plan_text, limit=1000),
            "chapter_excerpt": _truncate_text(chapter_text, limit=1800),
            "quality_excerpt": _truncate_text(quality_text, limit=500),
        },
        "task": "Judge element execution status for this chapter.",
    }


async def _run_arbiter_for_item(
    builder: Any,
    router: Any,
    settings: Any,
    *,
    element_id: str,
    card: dict[str, Any],
    rule_item: dict[str, Any],
    plan_text: str,
    chapter_text: str,
    quality_text: str,
) -> dict[str, Any] | None:
    context = _arbiter_prompt_context(
        element_id=element_id,
        card=card,
        rule_item=rule_item,
        plan_text=plan_text,
        chapter_text=chapter_text,
        quality_text=quality_text,
    )
    configured_cap = max(
        64,
        int(getattr(settings, "element_progress_llm_arbiter_max_tokens", 256) or 256),
    )
    max_tokens = calculate_route_aware_max_tokens(
        router,
        TaskType.ELEMENT_PROGRESS_ARBITER,
        max(160, len(str(rule_item.get("description", "") or "")) + 160),
        prompt_overhead=1200,
        min_tokens=min(256, configured_cap),
        max_cap=configured_cap,
    )
    try:
        request = builder.build(
            TaskType.ELEMENT_PROGRESS_ARBITER,
            context,
            max_tokens=max_tokens,
            temperature=float(
                getattr(settings, "element_progress_llm_arbiter_temperature", 0.0) or 0.0
            ),
        )
    except Exception as exc:
        _log.warning(
            "element_progress_arbiter_prompt_build_failed | element=%s | error=%s", element_id, exc
        )
        return None
    try:
        payload = await llm_h.route_json_object_with_retry(
            router,
            request,
            task_type=TaskType.ELEMENT_PROGRESS_ARBITER,
            context=context,
        )
    except Exception as exc:
        _log.warning("element_progress_arbiter_failed | element=%s | error=%s", element_id, exc)
        return None
    status = str(payload.get("status", "") or "").strip().lower()
    if status not in {"hit", "weak", "miss"}:
        return None
    try:
        confidence = float(payload.get("confidence", 0.5) or 0.5)
    except Exception as _exc:
        _log.debug("element_progress_confidence_parse_fallback | %s", _exc)
        confidence = 0.5
    reason = _normalize_ws(payload.get("reason", ""))
    return {
        "status": status,
        "confidence": max(0.0, min(confidence, 1.0)),
        "reason": reason[:220],
    }


def _rebuild_summary(results: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "scheduled": len(results),
        "hit": len([r for r in results if str(r.get("status", "")).lower() == "hit"]),
        "weak": len([r for r in results if str(r.get("status", "")).lower() == "weak"]),
        "miss": len([r for r in results if str(r.get("status", "")).lower() == "miss"]),
    }


async def finalize_chapter_progress_with_optional_arbiter(
    storage: Any,
    layout: Any,
    *,
    chapter_number: int,
    fallback_scheduled_ids: list[str],
    focus_source: str,
    element_cards_by_id: dict[str, dict[str, Any]],
    plan: Any,
    chapter_text: str,
    alignment_report: Any | None,
    chapter_repair_report: Any | None,
    continuity_report: Any | None,
    router: Any | None = None,
    settings: Any | None = None,
    builder: Any | None = None,
) -> dict[str, Any] | None:
    chapter_data = finalize_chapter_progress(
        storage,
        layout,
        chapter_number=chapter_number,
        fallback_scheduled_ids=fallback_scheduled_ids,
        focus_source=focus_source,
        element_cards_by_id=element_cards_by_id,
        plan=plan,
        chapter_text=chapter_text,
        alignment_report=alignment_report,
        chapter_repair_report=chapter_repair_report,
        continuity_report=continuity_report,
        settings=settings,
    )
    if chapter_data is None:
        return None
    if settings is None or router is None or not _is_arbiter_enabled(settings):
        return chapter_data
    if builder is None:
        try:
            from novel_forge.prompts.builder import PromptBuilder

            builder = PromptBuilder()
        except Exception as exc:
            _log.warning(
                "element_progress_arbiter_builder_init_failed | chapter=%s | error=%s",
                chapter_number,
                exc,
            )
            return chapter_data
    max_items = _arbiter_max_items(settings)
    if max_items <= 0:
        return chapter_data

    low, high = _arbiter_score_range(settings)
    results = list(chapter_data.get("results", []) or [])
    candidates = [
        item
        for item in results
        if isinstance(item, dict) and _candidate_for_arbiter(item, low=low, high=high)
    ]
    if not candidates:
        return chapter_data

    plan_text = _flatten_plan_text(plan)
    quality_text = _flatten_quality_text(alignment_report, chapter_repair_report, continuity_report)
    chapter_compact_text = _normalize_ws(chapter_text)

    changed = 0
    reviewed = 0
    for item in candidates[:max_items]:
        eid = str(item.get("element_id", "") or "").strip()
        card = element_cards_by_id.get(eid)
        if not eid or not isinstance(card, dict):
            continue
        arbiter = await _run_arbiter_for_item(
            builder,
            router,
            settings,
            element_id=eid,
            card=card,
            rule_item=item,
            plan_text=plan_text,
            chapter_text=chapter_compact_text,
            quality_text=quality_text,
        )
        reviewed += 1
        if not isinstance(arbiter, dict):
            continue
        prev_status = str(item.get("status", "") or "").strip().lower()
        next_status = str(arbiter.get("status", "") or "").strip().lower()
        item["arbiter"] = {
            "status": next_status,
            "confidence": float(arbiter.get("confidence", 0.5) or 0.5),
            "reason": str(arbiter.get("reason", "") or ""),
        }
        if next_status in {"hit", "weak", "miss"} and next_status != prev_status:
            item["status"] = next_status
            changed += 1

    if reviewed <= 0:
        return chapter_data

    chapter_data["summary"] = _rebuild_summary(results)
    chapter_data["arbiter"] = {
        "enabled": True,
        "reviewed_count": reviewed,
        "changed_count": changed,
        "score_gray_zone": {"low": low, "high": high},
    }
    payload = load_element_progress(storage, layout)
    chapters = payload.setdefault("chapters", {})
    chapters[str(int(chapter_number))] = chapter_data
    save_element_progress(storage, layout, payload)
    return chapter_data
