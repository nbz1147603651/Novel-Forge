"""Shared guardrails for prompt artifacts and continuity-sensitive signals."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from novel_forge.core.constants import (
    FEMALE_GENDER_INDICATORS,
    MALE_GENDER_INDICATORS,
)

KNOWN_PROMPT_MARKERS = (
    "【情绪锚】",
    "【交接】",
    "【钩子】",
    "【新线索】",
    "【回收】",
    "预期扰动路径",
    "系统响应",
    "B 方案",
    "Success",
    "延续上章末尾的视角与情绪",
    "此处的状态承接上章退场时的情绪标签",
    "以承接上章确认的",
    "这是一个未决的线索",
    "这些伏笔终将回收",
)

ALLOWED_NARRATIVE_MARKERS = frozenset(
    {
        "【闪回】",
        "【回忆】",
        "【梦境】",
        "【现实】",
        "【片段】",
    }
)

_BRACKETED_MARKER_RE = re.compile(r"^【([^】\n]{1,120})】$")
_KNOWN_BRACKETED_MARKER_INNERS = frozenset(
    marker[1:-1]
    for marker in KNOWN_PROMPT_MARKERS
    if marker.startswith("【") and marker.endswith("】")
)
_PROMPT_LEAK_SCHEMA_TOKEN_RE = re.compile(
    r"(?:"
    r"opening_contract|closing_contract|required_state_transitions|scene[_ -]?\d+|scene_intent|"
    r"time_marker|location|pov|required_outcome|exit_target_state|entry_state_refs|"
    r"relationship_dynamics|emotional_beat|sensory_notes|bridge_summary|"
    r"输出格式|只返回纯\s*JSON|预期扰动路径|系统响应(?:延迟)?|系统级回滚机制|"
    r"自动校验|自动溯源|超阈值|\b(?:A|B)方案\b|\bSuccess\b|"
    r"落地场景|伪装压力等级|暴露风险信号|承接上章|情绪标签|"
    r"悬念钩子|埋下伏笔|伏笔终将回收|未决(?:的)?线索"
    r")",
    re.IGNORECASE,
)
_IN_WORLD_BRACKET_CONTEXT_RE = re.compile(
    r"(?:"
    r"写着|写道|题为|标题|题签|署着|落款|标着|贴着|挂着|刻着|印着|盖着|抬头|"
    r"信上|纸上|封面|扉页|页眉|牌匾|匾额|告示|公告|通知|邮件|信笺|信纸|"
    r"公文|文书|卷宗|案卷|账簿|册子|木牌|铜牌|石碑|横幅|门楣|铭牌|标签"
    r")"
)

PROMPT_LEAK_VERDICT = "prompt_leak"
IN_WORLD_TEXT_VERDICT = "in_world_text"
AMBIGUOUS_PROMPT_LEAK_VERDICT = "ambiguous"

SYSTEM_ARTIFACT_NAMES = frozenset(
    {
        "canon_delta",
        "chapter_exit_state",
        "character_state_deltas",
        "creative_report",
        "new_events",
        "new_world_facts",
        "plot_thread_deltas",
        "plot_thread_updates",
        "relationship_deltas",
        "source_chapter",
        "structured_summary",
    }
)

_PROMPT_LEAK_PATTERNS = [
    re.compile(r"【[^】\n]{1,120}】"),
    re.compile(r"\*\*[^*\n]{0,80}(?:落地场景|伪装压力等级|暴露风险信号)[^*\n]{0,120}\*\*"),
    re.compile(r"预期扰动路径"),
    re.compile(r"系统响应(?:延迟)?"),
    re.compile(r"系统级回滚机制"),
    re.compile(r"自动校验"),
    re.compile(r"自动溯源"),
    re.compile(r"超阈值"),
    re.compile(r"\b(?:A|B)方案\b", re.IGNORECASE),
    re.compile(r"\bSuccess\b"),
    re.compile(r"只返回纯 JSON", re.IGNORECASE),
    re.compile(r"输出格式"),
    re.compile(r"opening_contract", re.IGNORECASE),
    re.compile(r"closing_contract", re.IGNORECASE),
    re.compile(r"required_state_transitions", re.IGNORECASE),
    re.compile(r"scene[_ -]?\d+", re.IGNORECASE),
    re.compile(r"延续上章末尾的视角与情绪"),
    re.compile(r"此处的状态承接上章退场时的情绪标签"),
    re.compile(r"以承接上章确认的"),
    re.compile(r"上章退场时的情绪标签"),
    re.compile(r"\bPOV\b", re.IGNORECASE),
    # 新增规划层标记检测
    re.compile(r"time_marker", re.IGNORECASE),
    re.compile(r"location", re.IGNORECASE),
    re.compile(r"pov", re.IGNORECASE),
    re.compile(r"required_outcome", re.IGNORECASE),
    re.compile(r"exit_target_state", re.IGNORECASE),
    re.compile(r"entry_state_refs", re.IGNORECASE),
    re.compile(r"relationship_dynamics", re.IGNORECASE),
    re.compile(r"emotional_beat", re.IGNORECASE),
    re.compile(r"sensory_notes", re.IGNORECASE),
    re.compile(r"bridge_summary", re.IGNORECASE),
    re.compile(r"完成场景转换", re.IGNORECASE),
    re.compile(r"无直接人际互动", re.IGNORECASE),
    re.compile(r"留出悬念钩子", re.IGNORECASE),
    re.compile(r"为第[一二三四五六七八九十0-9]+章.*埋下伏笔", re.IGNORECASE),
    re.compile(r"这是.*?未决(?:的)?线索", re.IGNORECASE),
    re.compile(r"这些?伏笔终将回收", re.IGNORECASE),
    re.compile(r"(?:一切|故事|博弈|此[事场]|这场)[^。！？\n]{0,15}才刚刚拉开序幕", re.IGNORECASE),
    re.compile(r"暗示[^。！？\n]{0,40}(?:真相|命运|后续|局势|影响)", re.IGNORECASE),
    re.compile(r"意味着[^。！？\n]{0,40}(?:真相|命运|后续|局势|影响)", re.IGNORECASE),
    # World-rule identifiers and the evidence labels below are internal
    # guidance vocabulary.  They are never narrative prose when used as a
    # leading label, and must not reach a DRAFT/WAVE artifact.
    re.compile(
        r"(?<![A-Za-z0-9_])(?:WR|WORLD[_ -]?RULE|RULE)[_-]\d{1,4}(?:之律|规则)?(?![A-Za-z0-9_])",
        re.IGNORECASE,
    ),
    re.compile(r"(?m)^(?:辨真|疑[已己]|置信度)\s*[：:]"),
]

_SYSTEM_NAME_HINT_RE = re.compile(
    r"^(?:chapter|creative|structured|relationship|plot_thread|character_state|chapter_exit|canon)_",
    re.IGNORECASE,
)
_CUSTODY_SIGNAL_RE = re.compile(
    r"(牢房|大牢|监牢|囚室|羁押|看守|押(?:进|回|入|往|到)|锁链|镣铐|刑场待决|待决|收监)"
)
_RELEASE_SIGNAL_RE = re.compile(
    r"(提审|押解|获释|放出|交保|保释|奉命传唤|传唤|被带离|带离|移送|转押|准其离开)"
)
_TRANSITION_SIGNAL_RE = re.compile(
    r"(前往|赶往|转入|折返|离开|出门|回到|移步|来到|奔赴|穿过|步入|赶去"
    r"|离去|离开|离了|趁.*?离|至.*?城|行至|抵达|抵(?:了|至|到|府|家|院|门)|乘(?:车|马|船|轿|驴|舟)"
    r"|途径|踏出|踏上|踏进|踏入|带她|押解|跟着.*?走|跟了|随.*?前往|走出|起身|奔|"
    r"行了|行在|启程|出发|脱身|摆脱|逃出|转赴|换了|换乘|绕(?:回|行|道)|折向|疾行|赶回|"
    r"翻(?:墙|过|入|出)|潜(?:入|行)|登(?:车|船|舟)|下(?:车|马|船|轿|驴|舟)|"
    r"回(?:府|家|院|房|城|营)|归(?:府|家|来)|返(?:府|家|程|回)|离(?:府|城|家)|入(?:府|院|城|宫|门))"
)
_TRANSITION_SIGNAL_EN_RE = re.compile(
    r"\b("
    r"go(?:es|ing)?\s+to|head(?:s|ed|ing)?\s+to|return(?:s|ed|ing)?|arriv(?:e|es|ed|ing)|"
    r"leave(?:s|d|ing)?|depart(?:s|ed|ing)?|travel(?:s|ed|ing)?|"
    r"walk(?:s|ed|ing)?\s+(?:to|into)|run(?:s|ning)?\s+to|ride(?:s|n)?\s+to|"
    r"drive(?:s|n|d)?\s+to|board(?:s|ed|ing)|disembark(?:s|ed|ing)|"
    r"slip(?:s|ped|ping)\s+back|sneak(?:s|ed|ing)\s+(?:into|back)|made?\s+it\s+to"
    r")\b",
    re.IGNORECASE,
)
_TRADITIONAL_TIME_WITH_KE_RE = re.compile(
    r"(子|丑|寅|卯|辰|巳|午|未|申|酉|戌|亥)(?:时|時)?(?:初|正)?([零〇一二三四五六七八九十两\d]{1,3})刻"
)
_CN_NUMERAL_DIGIT_MAP = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_PROMPT_ARTIFACT_SENTENCE_PATTERNS = [
    re.compile(r"\*\*[^*\n]{0,80}(?:落地场景|伪装压力等级)[^*\n]{0,120}\*\*[。！？]?"),
    re.compile(r"\*\*[^*\n]{0,80}暴露风险信号[:：][^*\n]{0,120}\*\*[。！？]?"),
    re.compile(r"完成场景转换至[^。！？\n]{0,160}[。！？]?"),
    re.compile(r"无直接人际互动[^。！？\n]{0,160}[。！？]?"),
    re.compile(r"为第[一二三四五六七八九十0-9]+章[^。！？\n]{0,160}埋下伏笔[。！？]?"),
    re.compile(r"本章以高度不安的悬念收束[^。！？\n]{0,120}[。！？]?"),
    # 情绪节拍计划泄漏：「从X到Y的情绪落幕/收束/转换……」
    re.compile(
        r"从[^。！？\n]{2,10}到[^。！？\n]{2,10}的(?:情绪|心理|内心)(?:落幕|收束|转换|递进|释放)[^。！？\n]{0,80}[。！？]?"
    ),
    # 叙述总结性泄漏：「她从X沦为Y，命运的急转令她……」
    re.compile(
        r"(?:她|他)从[^。！？\n]{2,15}(?:沦为|变成|转为|成为)[^。！？\n]{2,15}(?:命运|转折|急转)[^。！？\n]{0,80}[。！？]?"
    ),
    # 规划性全景描述：「满座宾客侧目，XX面露惊愕，XX沉默注视，XX在暗处皱眉」
    re.compile(
        r"(?:金丝|幻象|异能)[^。！？\n]{0,20}(?:彻底|完全)(?:失控|爆发|外显)[^。！？\n]{0,80}[。！？]?"
    ),
    # 场景意图泄漏
    re.compile(
        r"(?:以|通过|用)(?:环境|场景|细节|意象)[^。！？\n]{0,20}(?:锚定|确立|铺垫|过渡|转入)[^。！？\n]{0,80}[。！？]?"
    ),
    # 情绪标签泄漏
    re.compile(
        r"(?:极致|最终)的(?:克制|恐惧|紧张|愤怒)与(?:恐惧|挣扎|不安|绝望)交织[^。！？\n]{0,60}[。！？]?"
    ),
    # 叙事功能泄漏
    re.compile(r"(?:留出|制造|营造|铺设)(?:悬念|伏笔|钩子|暗线)[^。！？\n]{0,80}[。！？]?"),
    re.compile(r"这是一个未决(?:的)?线索[。！？]?"),
    re.compile(r"[^。！？\n]{0,80}这些?伏笔终将回收[。！？]?"),
    re.compile(r"[^。！？\n]{0,80}(?:才刚刚|刚刚)?拉开序幕[。！？]?"),
    re.compile(
        r"[^。！？\n]{0,80}(?:暗示|意味着)[^。！？\n]{0,80}(?:真相|命运|后续|局势)[。！？]?"
    ),
]
_PROMPT_ARTIFACT_LINE_RE = re.compile(
    r"(?:输出格式|评估规则|scene[_ -]?intent|opening_contract|closing_contract|"
    r"required_outcome|exit_target_state|time_marker|bridge_summary|自动修复提示|阻断归档|"
    r"情绪落幕|悬念钩子|叙事钩|情感钩|关系张力|chapter_type|sensory_notes|emotional_beat|"
    r"这是一个未决|伏笔终将回收|才刚刚拉开序幕|落地场景|伪装压力等级|暴露风险信号)",
    re.IGNORECASE,
)


def is_system_artifact_name(name: str) -> bool:
    """Return True when *name* looks like a leaked schema key, not a character name."""
    text = str(name or "").strip()
    if not text:
        return True
    lowered = text.lower()
    if text in SYSTEM_ARTIFACT_NAMES or lowered in SYSTEM_ARTIFACT_NAMES:
        return True
    if _SYSTEM_NAME_HINT_RE.match(lowered):
        return True
    return False


def detect_prompt_leaks(
    text: str,
    *,
    extra_markers: Iterable[str] | None = None,
    max_hits: int = 12,
) -> list[str]:
    """Return matched prompt/system artifacts found in *text*."""
    source = str(text or "")
    if not source.strip():
        return []

    hits: list[str] = []
    seen: set[str] = set()

    def _append(snippet: str) -> None:
        cleaned = re.sub(r"\s+", " ", snippet).strip()
        if cleaned in ALLOWED_NARRATIVE_MARKERS:
            return
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        hits.append(cleaned[:160])

    for marker in (*KNOWN_PROMPT_MARKERS, *(extra_markers or ())):
        cleaned = str(marker or "").strip()
        if cleaned and cleaned in source:
            _append(cleaned)
            if len(hits) >= max_hits:
                return hits

    for pattern in _PROMPT_LEAK_PATTERNS:
        for match in pattern.finditer(source):
            _append(match.group(0))
            if len(hits) >= max_hits:
                return hits
    return hits


def confirmed_reported_prompt_leaks(text: str, reported_leaks: Iterable[str] | None) -> list[str]:
    """Return reported prompt leaks that are still locally confirmed in prose.

    LLM repair reports sometimes describe a leak rather than quote it exactly.
    We only auto-repair exact snippets that are still present and also match the
    local prompt-leak detector, which keeps ordinary prose from being deleted
    just because a reviewer used a broad phrase in its report.
    """
    return classify_reported_prompt_leaks(text, reported_leaks)[PROMPT_LEAK_VERDICT]


def classify_prompt_leak_candidate(text: str, candidate: str) -> str:
    """Classify a prompt-leak candidate before any automatic repair.

    Returns one of:
    - ``prompt_leak``: planning/system meta language that can be repaired.
    - ``in_world_text``: a bracketed title/label that appears to belong to the story world.
    - ``ambiguous``: still suspicious, but not safe to auto-repair.
    """
    source = str(text or "")
    cleaned = re.sub(r"\s+", " ", str(candidate or "")).strip()
    if not source.strip() or not cleaned or cleaned not in source:
        return AMBIGUOUS_PROMPT_LEAK_VERDICT
    if cleaned in ALLOWED_NARRATIVE_MARKERS:
        return IN_WORLD_TEXT_VERDICT
    if cleaned in KNOWN_PROMPT_MARKERS or _PROMPT_LEAK_SCHEMA_TOKEN_RE.search(cleaned):
        return PROMPT_LEAK_VERDICT

    bracketed = _BRACKETED_MARKER_RE.match(cleaned)
    if bracketed:
        inner = bracketed.group(1).strip()
        if inner in _KNOWN_BRACKETED_MARKER_INNERS or _PROMPT_LEAK_SCHEMA_TOKEN_RE.search(inner):
            return PROMPT_LEAK_VERDICT
        pos = source.find(cleaned)
        start = max(0, pos - 36)
        end = min(len(source), pos + len(cleaned) + 36)
        context = source[start:end]
        if _IN_WORLD_BRACKET_CONTEXT_RE.search(context):
            return IN_WORLD_TEXT_VERDICT
        return AMBIGUOUS_PROMPT_LEAK_VERDICT

    return PROMPT_LEAK_VERDICT


def classify_reported_prompt_leaks(
    text: str,
    reported_leaks: Iterable[str] | None,
) -> dict[str, list[str]]:
    """Classify reported prompt leaks into repairable, in-world, and ambiguous buckets."""
    source = str(text or "")
    local_hits = detect_prompt_leaks(source, max_hits=64) if source.strip() else []
    buckets: dict[str, list[str]] = {
        PROMPT_LEAK_VERDICT: [],
        IN_WORLD_TEXT_VERDICT: [],
        AMBIGUOUS_PROMPT_LEAK_VERDICT: [],
    }
    seen: set[str] = set()
    for item in reported_leaks or ():
        leak = str(item or "").strip()
        if not leak or leak in seen or leak not in source:
            continue
        if not any(leak == hit or leak in hit or hit in leak for hit in local_hits):
            continue
        seen.add(leak)
        buckets[classify_prompt_leak_candidate(source, leak)].append(leak)
    return buckets


def repair_confirmed_prompt_leaks(
    text: str, confirmed_leaks: Iterable[str]
) -> tuple[str, list[str]]:
    """Deterministically clean confirmed prompt/planning artifacts from prose."""
    cleaned = str(text or "")
    repaired: list[str] = []
    seen: set[str] = set()
    for item in confirmed_leaks:
        leak = str(item or "").strip()
        if not leak or leak in seen or leak not in cleaned:
            continue
        seen.add(leak)
        replacement = ""
        bracketed = _BRACKETED_MARKER_RE.match(leak)
        if bracketed:
            inner = bracketed.group(1).strip()
            # Narrative document labels such as a fictional email title can be
            # preserved as prose once the prompt-like brackets are removed.
            # Known structural prompt markers are removed outright.
            if inner not in _KNOWN_BRACKETED_MARKER_INNERS:
                replacement = inner
        cleaned = cleaned.replace(leak, replacement)
        repaired.append(leak)
    if repaired:
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, repaired


def build_prompt_leak_patch_issues(text: str, confirmed_leaks: Iterable[str]) -> list[Any]:
    """Build patch-compatible issues for confirmed prompt leaks.

    This keeps the preferred repair path model-driven and localised: the LLM
    sees only the paragraph window containing the leak and returns an exact
    text patch. Deterministic deletion remains a last-resort fallback elsewhere.
    """
    from novel_forge.core.schemas.continuity import ContinuityIssue

    source = str(text or "")
    if not source.strip():
        return []

    issues: list[Any] = []
    seen: set[str] = set()
    for idx, item in enumerate(confirmed_leaks, start=1):
        leak = str(item or "").strip()
        if not leak or leak in seen or leak not in source:
            continue
        seen.add(leak)
        issues.append(
            ContinuityIssue(
                issue_id=f"prompt_leak_{idx}",
                issue_type="prompt_leak",
                severity="critical",
                confidence=0.95,
                source="local",
                blocking=True,
                summary=f"正文混入提示词或规划层标记：{leak[:80]}",
                evidence=leak,
                evidence_quote=leak,
                fix_mode="replace",
                rewrite_scope="paragraph",
                location="包含提示词/规划层泄露的段落",
                location_confidence=0.95,
                anchor_type="evidence_exact",
                fix_actions=[
                    "用补丁方式删除提示词/规划层标记；若该片段是小说内物件、信件、牌匾、告示标题，"
                    "只去掉元标记形式，保留能自然出现在正文里的叙事内容。",
                    "不得改变情节走向、人物状态或上下文事实。",
                ],
            )
        )
    return issues


def sanitize_story_text(text: str) -> str:
    """Drop plan/story fragments that still contain prompt artifacts."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"^【AI护栏约束】\s*", "", cleaned)
    cleaned = re.sub(r"^AI护栏约束[:：]?\s*", "", cleaned)
    if detect_prompt_leaks(cleaned, max_hits=1):
        return ""
    return cleaned


def scrub_prompt_artifacts(
    text: str,
    *,
    max_removed: int = 40,
) -> tuple[str, list[str]]:
    """Remove planning/system leakage snippets from narrative prose."""
    source = str(text or "")
    if not source.strip():
        return "", []

    cleaned = source
    removed: list[str] = []
    seen: set[str] = set()

    def _record(snippet: str) -> None:
        if len(removed) >= max_removed:
            return
        normalized = re.sub(r"\s+", " ", str(snippet or "")).strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        removed.append(normalized[:200])

    for pattern in _PROMPT_ARTIFACT_SENTENCE_PATTERNS:
        while True:
            match = pattern.search(cleaned)
            if not match:
                break
            _record(match.group(0))
            start, end = match.span()
            cleaned = cleaned[:start] + cleaned[end:]

    filtered_lines: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if stripped and _PROMPT_ARTIFACT_LINE_RE.search(stripped):
            _record(stripped)
            continue
        filtered_lines.append(line)

    cleaned = "\n".join(filtered_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, removed


def text_has_custody_signal(text: str) -> bool:
    """Heuristic: the text implies the POV character is confined or under guard."""
    return bool(_CUSTODY_SIGNAL_RE.search(str(text or "")))


def text_has_release_signal(text: str) -> bool:
    """Heuristic: the text explicitly explains release / escort / transfer."""
    return bool(_RELEASE_SIGNAL_RE.search(str(text or "")))


def text_has_transition_signal(text: str) -> bool:
    """Heuristic: the text contains an explicit movement / scene transition cue."""
    source = str(text or "")
    if not source:
        return False
    return bool(_TRANSITION_SIGNAL_RE.search(source) or _TRANSITION_SIGNAL_EN_RE.search(source))


def _parse_cn_number(text: str) -> int | None:
    """Parse a simple Chinese numeral string into int (supports 0-99)."""
    source = str(text or "").strip()
    if not source:
        return None
    if source.isdigit():
        return int(source)
    if source in _CN_NUMERAL_DIGIT_MAP:
        return _CN_NUMERAL_DIGIT_MAP[source]
    if source == "十":
        return 10
    if "十" in source:
        parts = source.split("十", 1)
        left = parts[0]
        right = parts[1]
        if left == "":
            tens = 1
        elif left in _CN_NUMERAL_DIGIT_MAP:
            tens = _CN_NUMERAL_DIGIT_MAP[left]
        else:
            return None
        if right == "":
            ones = 0
        elif right in _CN_NUMERAL_DIGIT_MAP:
            ones = _CN_NUMERAL_DIGIT_MAP[right]
        else:
            return None
        return tens * 10 + ones
    return None


def detect_invalid_time_markers(text: str) -> list[dict[str, str]]:
    """Detect invalid traditional Chinese time markers such as '亥初六刻'.

    Rule enforced:
    - In one 时辰 (子/丑/.../亥), `X刻` must be in 1..4.
    """
    source = str(text or "")
    if not source.strip():
        return []

    findings: list[dict[str, str]] = []
    lines = source.split("\n")
    for line_idx, line in enumerate(lines, start=1):
        for match in _TRADITIONAL_TIME_WITH_KE_RE.finditer(line):
            marker = match.group(0)
            ke_value_raw = match.group(2)
            ke_value = _parse_cn_number(ke_value_raw)
            if ke_value is None:
                findings.append(
                    {
                        "marker": marker,
                        "line_number": str(line_idx),
                        "reason": "刻值无法解析",
                    }
                )
                continue
            if ke_value < 1 or ke_value > 4:
                findings.append(
                    {
                        "marker": marker,
                        "line_number": str(line_idx),
                        "reason": "刻值超出时辰范围（应为一至四刻）",
                    }
                )
    return findings


def normalize_invalid_time_markers(text: str) -> tuple[str, list[dict[str, str]]]:
    """Replace mechanically invalid 时辰刻度 with a legal fuzzy time anchor.

    The deterministic repair intentionally handles only clear overflow forms
    caught by :func:`detect_invalid_time_markers`, such as ``亥时六刻``. Valid
    first-to-fourth 刻 expressions are left untouched.
    """
    source = str(text or "")
    if not source:
        return source, []

    replacements: list[dict[str, str]] = []

    def _replace(match: re.Match[str]) -> str:
        marker = match.group(0)
        ke_value = _parse_cn_number(match.group(2))
        if ke_value is None or 1 <= ke_value <= 4:
            return marker
        replacement = f"{match.group(1)}时末"
        replacements.append({"marker": marker, "replacement": replacement})
        return replacement

    normalized = _TRADITIONAL_TIME_WITH_KE_RE.sub(_replace, source)
    return normalized, replacements


def detect_self_repetition(
    text: str,
    *,
    window_chars: int = 80,
    step_chars: int = 40,
    similarity_threshold: float = 0.85,
    min_distance: int = 120,
) -> list[dict[str, str | int]]:
    """Detect near-duplicate text segments within the same chapter.

    Uses a two-layer approach:
    1. Sliding-window with character-level 4-gram Jaccard similarity (fine-grained).
    2. Paragraph-level exact/fuzzy match (catches full-paragraph copy-paste).

    Parameters:
        min_distance: minimum character distance between windows to consider as
            potential duplicate.  Lowered from 400 → 120 to catch near-adjacent
            repetitions that previously slipped through.
    """
    source = str(text or "").strip()
    if len(source) < window_chars * 2:
        return []

    def _char_ngrams(s: str, n: int = 4) -> set[str]:
        return {s[i : i + n] for i in range(len(s) - n + 1)} if len(s) >= n else {s}

    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    # ── Layer 1: sliding-window detection ──
    windows: list[tuple[int, str, set[str]]] = []
    pos = 0
    while pos + window_chars <= len(source):
        seg = source[pos : pos + window_chars]
        windows.append((pos, seg, _char_ngrams(seg)))
        pos += step_chars

    duplicates: list[dict[str, str | int]] = []
    flagged_starts: set[int] = set()
    for i in range(len(windows)):
        if windows[i][0] in flagged_starts:
            continue
        for j in range(i + 2, len(windows)):  # skip adjacent
            if windows[j][0] in flagged_starts:
                continue
            if abs(windows[j][0] - windows[i][0]) < min_distance:
                continue
            sim = _jaccard(windows[i][2], windows[j][2])
            if sim >= similarity_threshold:
                flagged_starts.add(windows[j][0])
                duplicates.append(
                    {
                        "start": windows[j][0],
                        "end": windows[j][0] + window_chars,
                        "duplicate_of_start": windows[i][0],
                        "snippet": windows[j][1][:60],
                    }
                )

    # ── Layer 2: paragraph-level duplicate detection ──
    # Catches whole-paragraph copy-paste that the sliding window may miss.
    _MIN_PARA_LEN = 50
    paragraphs = source.split("\n\n")
    para_offset = 0
    para_entries: list[tuple[int, int, str]] = []  # (start, end, normalized)
    for para in paragraphs:
        stripped = para.strip()
        idx = source.find(para, para_offset)
        if idx == -1:
            idx = para_offset
        end = idx + len(para)
        para_offset = end
        if len(stripped) >= _MIN_PARA_LEN:
            normalized = re.sub(r"\s+", "", stripped)
            para_entries.append((idx, end, normalized))

    seen_paras: dict[str, int] = {}  # normalized → first start offset
    for start, end, normalized in para_entries:
        if start in flagged_starts:
            continue
        if normalized in seen_paras:
            # Exact paragraph duplicate
            flagged_starts.add(start)
            duplicates.append(
                {
                    "start": start,
                    "end": end,
                    "duplicate_of_start": seen_paras[normalized],
                    "snippet": normalized[:60],
                }
            )
        else:
            # Near-identical paragraph check
            ngrams_new = _char_ngrams(normalized)
            matched = False
            for seen_text, seen_start in seen_paras.items():
                if _jaccard(ngrams_new, _char_ngrams(seen_text)) >= similarity_threshold:
                    flagged_starts.add(start)
                    duplicates.append(
                        {
                            "start": start,
                            "end": end,
                            "duplicate_of_start": seen_start,
                            "snippet": normalized[:60],
                        }
                    )
                    matched = True
                    break
            if not matched:
                seen_paras[normalized] = start

    return duplicates


# ── POV intrusion detection ───────────────────────────────────────────────────

# Internal-thought markers: patterns that indicate direct access to a character's mind.
# Excludes speculative/observational language (e.g. "想必", "大概", "似乎").
_POV_INTRUSION_THOUGHT_RE = re.compile(
    r"(?:心想|暗忖|心中[暗默]|默想|心道|内心[一暗深]|暗自[思想]|脑海[中里]|心头[一涌]|心底[暗深])"
)
# Softer cognitive-inference markers (less explicit than "心想"), used as
# lower-confidence hints for omniscient leakage.
_POV_INTRUSION_COGNITIVE_RE = re.compile(
    r"(?:意识到|知道|明白|笃定|认定|判断|盘算|权衡|并不(?:惊讶|意外|担心|慌张)|真实(?:地)?动摇|心绪(?:微乱|不宁)?|念头一闪)"
)
# Speculative / observational phrases that should NOT be flagged
_POV_SPECULATIVE_RE = re.compile(r"(?:想必|大概|似乎|仿佛|恐怕|或许|约莫|怕是|看起来|看上去)")


def detect_pov_intrusion(
    text: str,
    pov_character: str,
    character_names: list[str],
    pov_scope: str = "limited",
) -> list[dict[str, Any]]:
    """Detect non-POV characters' internal thoughts in chapter text (heuristic).

    Returns a list of dicts with ``character``, ``evidence``, ``line_number``,
    ``confidence`` and ``intrusion_type``.
    Only flags patterns where a non-POV character name is followed by internal
    thought markers, excluding speculative/observational language.
    """
    if not pov_character or not character_names:
        return []

    scope = str(pov_scope or "limited").strip().lower()
    if scope in {"omniscient", "objective"}:
        return []

    non_pov = [n for n in character_names if n and n != pov_character and len(n) >= 2]
    if not non_pov:
        return []

    intrusions: list[dict[str, Any]] = []
    lines = text.split("\n")
    for line_idx, line in enumerate(lines, start=1):
        for name in non_pov:
            # Hard pattern: {name} + optional particles + explicit thought marker
            hard_pattern = re.compile(
                re.escape(name) + r"[，、的]?\s{0,2}" + _POV_INTRUSION_THOUGHT_RE.pattern
            )
            for match in hard_pattern.finditer(line):
                # Exclude if preceded by speculative language
                context_start = max(0, match.start() - 10)
                preceding = line[context_start : match.start()]
                if _POV_SPECULATIVE_RE.search(preceding):
                    continue
                # Exclude dialogue content (inside quotes)
                if _is_inside_dialogue_fast(text, text.find(line) + match.start()):
                    continue
                intrusions.append(
                    {
                        "character": name,
                        "evidence": line[max(0, match.start() - 5) : match.end() + 20].strip(),
                        "line_number": str(line_idx),
                        "confidence": 0.90,
                        "intrusion_type": "inner_thought",
                    }
                )

            # Soft pattern: {name} ... cognitive inference marker
            soft_pattern = re.compile(
                re.escape(name)
                + r"[\u4e00-\u9fff，、的]{0,6}"
                + _POV_INTRUSION_COGNITIVE_RE.pattern
            )
            for match in soft_pattern.finditer(line):
                context_start = max(0, match.start() - 10)
                preceding = line[context_start : match.start()]
                if _POV_SPECULATIVE_RE.search(preceding):
                    continue
                if _is_inside_dialogue_fast(text, text.find(line) + match.start()):
                    continue
                intrusions.append(
                    {
                        "character": name,
                        "evidence": line[max(0, match.start() - 5) : match.end() + 20].strip(),
                        "line_number": str(line_idx),
                        "confidence": 0.65,
                        "intrusion_type": "cognitive_inference",
                    }
                )
    return intrusions


def _is_inside_dialogue_fast(text: str, pos: int) -> bool:
    """Quick check if position is inside Chinese quotes (used by POV detection)."""
    # Count quote opens/closes before pos
    depth = 0
    for ch in text[max(0, pos - 500) : pos]:  # look back up to 500 chars for efficiency
        if ch in ("\u201c", "\u300c"):
            depth += 1
        elif ch in ("\u201d", "\u300d"):
            depth = max(0, depth - 1)
    return depth > 0


# ── Mechanical pronoun repair ─────────────────────────────────────────────────

# Chinese quote pairs for dialogue detection
_QUOTE_OPEN = frozenset(["\u201c", "\u2018", "\u300c", "\u300e"])  # " ' 「 『
_QUOTE_CLOSE = {"\u201c": "\u201d", "\u2018": "\u2019", "\u300c": "\u300d", "\u300e": "\u300f"}


def _is_inside_dialogue(text: str, pos: int) -> bool:
    """Fast check: is *pos* inside a dialogue quote span?"""
    depth = 0
    for ch in text[:pos]:
        if ch in _QUOTE_OPEN:
            depth += 1
        elif depth > 0 and ch in _QUOTE_CLOSE.values():
            depth -= 1
    return depth > 0


def _is_likely_subject_pronoun(text: str, pos: int) -> bool:
    """Heuristic: pronoun at *pos* is likely a clause subject.

    We only allow broad POV replacement when the pronoun appears at the
    beginning of a sentence/sub-clause (after punctuation/quote/newline).
    This avoids rewriting object pronouns such as "需要她".
    """
    if pos < 0 or pos >= len(text):
        return False
    left = pos - 1
    while left >= 0 and text[left].isspace():
        left -= 1
    if left < 0:
        return True
    return text[left] in {
        "。",
        "！",
        "？",
        "；",
        "：",
        "，",
        "\n",
        "“",
        "”",
        "「",
        "」",
        "『",
        "』",
        "（",
        "(",
        "[",
        "【",
    }


def fix_pronouns_mechanical(
    text: str,
    characters: dict[str, dict[str, Any]],
    pov_character: str = "",
    *,
    window: int = 200,
) -> tuple[str, int, int]:
    """Perform regex-based pronoun correction for known characters.

    For each character with a known gender, scan the text for occurrences of
    their name followed (within *window* chars) by the wrong pronoun outside
    of dialogue, and replace with the correct one.

    For the POV character, also scan for *any* wrong-gender pronoun outside
    dialogue in narrative paragraphs (since the POV often isn't named every
    sentence).

    Returns:
        (corrected_text, replacement_count, skipped_ambiguous_count)

        *skipped_ambiguous_count* is the number of candidate replacements that
        were intentionally skipped because an opposite-gender character name or
        indicator was found nearby.  A high value means there are potentially
        unresolved pronoun issues that require manual review.
    """
    if not text or not characters:
        return text, 0, 0

    gender_map = {"女": ("她", "他"), "男": ("他", "她")}
    replacements = 0
    skipped_ambiguous = 0
    result = list(text)

    # Phase 1: Name-anchored replacement for all characters
    for name, info in characters.items():
        gender = info.get("gender", "")
        if gender not in gender_map or not name:
            continue
        correct, wrong = gender_map[gender]

        # pattern: name ... wrong_pronoun (within window)
        pattern = re.compile(
            rf"({re.escape(name)})"
            rf"([\s\S]{{0,{window}}}?)"
            rf"({re.escape(wrong)})"
            rf"(?=[，。；：、！？\s"
            "''\"'\n])",
        )
        offset = 0
        for match in pattern.finditer(text):
            pronoun_start = match.start(3) + offset
            pronoun_end = match.end(3) + offset
            # Skip dialogue
            if _is_inside_dialogue("".join(result), pronoun_start):
                continue
            # Check there isn't another character name of the opposite gender
            # in the gap that would make this a false positive
            gap = match.group(2)
            has_other = False
            for other_name, other_info in characters.items():
                if (
                    other_name != name
                    and other_info.get("gender", "") != gender
                    and other_name in gap
                ):
                    has_other = True
                    break
            if has_other:
                skipped_ambiguous += 1
                continue

            result[pronoun_start:pronoun_end] = list(correct)
            offset += len(correct) - len(wrong)
            replacements += 1

    # Phase 2: POV character — broader scan for wrong pronouns in narrative
    if pov_character and pov_character in characters:
        pov_gender = characters[pov_character].get("gender", "")
        if pov_gender in gender_map:
            correct, wrong = gender_map[pov_gender]
            # Indicators of the OPPOSITE gender: if any appear near the pronoun,
            # the pronoun likely refers to an unnamed NPC, not the POV character.
            opposite_indicators = (
                MALE_GENDER_INDICATORS if pov_gender == "女" else FEMALE_GENDER_INDICATORS
            )
            final_text = "".join(result)
            pov_fixed = []
            i = 0
            while i < len(final_text):
                if (
                    final_text[i] == wrong
                    and not _is_inside_dialogue(final_text, i)
                    and _is_likely_subject_pronoun(final_text, i)
                ):
                    # Verify no other-gender character within ±100 chars
                    context_start = max(0, i - 100)
                    context_end = min(len(final_text), i + 100)
                    local = final_text[context_start:context_end]
                    has_other = any(
                        n in local
                        for n, info in characters.items()
                        if n != pov_character and info.get("gender", "") != pov_gender and n
                    )
                    # Also protect pronouns anchored by gender-indicator noun phrases
                    # (e.g. "中年男人…他抬起头" — "他" correctly refers to the shopkeeper,
                    # not the female POV character, even though "中年男人" is not in the
                    # character bible as a named entry).
                    if not has_other:
                        has_other = any(ind in local for ind in opposite_indicators)
                    if not has_other:
                        pov_fixed.append(correct)
                        replacements += 1
                        i += 1
                        continue
                    else:
                        skipped_ambiguous += 1
                pov_fixed.append(final_text[i])
                i += 1
            result = pov_fixed

    return "".join(result), replacements, skipped_ambiguous


# ── Non-CJK text detection ───────────────────────────────────────────────────

_NON_CJK_WORD_RE = re.compile(r"[a-zA-Z]{3,}")


def detect_non_cjk_words(text: str) -> list[str]:
    """Return list of non-CJK words (>=3 letters) found in *text*.

    Useful for catching English leakage in Chinese-language generation.
    """
    if not text:
        return []
    return _NON_CJK_WORD_RE.findall(text)


def fix_non_cjk_leakage(text: str) -> tuple[str, list[str]]:
    """Remove isolated English words from Chinese prose.

    Only removes words NOT inside quotation marks (could be intentional
    foreign dialogue). Returns (fixed_text, removed_words).
    """
    if not text:
        return text, []
    removed: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        word = m.group(0)
        pos = m.start()
        if _is_inside_dialogue(text, pos):
            return word
        removed.append(word)
        return ""

    fixed = _NON_CJK_WORD_RE.sub(_replace, text)
    # Clean up leftover double-spaces or orphaned commas.
    # Use [ \t] instead of \s to preserve paragraph-separating newlines.
    fixed = re.sub(r"，\s*，", "，", fixed)
    fixed = re.sub(r"[ \t]{2,}", " ", fixed)
    return fixed, removed
