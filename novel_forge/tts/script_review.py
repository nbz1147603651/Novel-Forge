"""Professional dubbing-script review and conservative performance repair.

The authoritative novel text remains immutable.  This layer reviews only the
derived performance plan, rolls back unsafe spoken adaptations, removes
machine-generated numeric acting tricks, and recovers high-confidence story
events that deserve a short foreground sound effect.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping

from novel_forge.pipeline.steps.humanize_scan_step import HumanizeScanStep
from novel_forge.tts.platform.dashscope_contract import (
    dashscope_supported_vocal_tags,
    dashscope_supports_aigc_tags,
    is_bailian_provider,
)
from novel_forge.tts.platform.minimax_contract import minimax_supports_interjections
from novel_forge.tts.rules import EventSoundRule as _EventSoundRule
from novel_forge.tts.rules import load_event_sound_rules as _load_event_sound_rules
from novel_forge.tts.runtime.performance_policy import voice_performance_profile
from novel_forge.tts.runtime.speakable_text import normalize_speakable_text
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    EmotionTag,
    ParalinguisticTag,
    SegmentType,
    SFXCue,
    VoiceTeamContract,
)
from novel_forge.tts.script_integrity import refresh_segment_uid

# Load rules from externalized JSON; falls back to empty tuple on failure.
_EVENT_SOUND_RULES: tuple[_EventSoundRule, ...] = _load_event_sound_rules()

# Legacy inline rules retained as documentation reference only.
# The active rules are loaded from novel_forge/tts/rules/event_sound_rules.json.
_LEGACY_EVENT_SOUND_RULES_REFERENCE = (
    _EventSoundRule(
        "玻璃碎裂",
        re.compile(
            r"(?:玻璃(?:杯|窗|门)?|(?:杯子|酒杯)).{0,8}(?:摔碎|砸碎|炸裂|破裂|碎了|碎开|落地而碎)"
        ),
        "单次玻璃碎裂，有清晰破裂瞬态与短促碎片落地尾音",
        1_200,
        0.56,
    ),
    _EventSoundRule(
        "敲门声",
        re.compile(r"(?:敲门|叩门|门外响起.{0,4}(?:敲|叩).{0,2}声)"),
        "近距离的一组敲门声，房门材质清晰，无人声",
        1_500,
        0.46,
        92,
    ),
    _EventSoundRule(
        "开关门声",
        re.compile(r"(?:门.{0,5}(?:砰地|重重地)?(?:关上|摔上|推开)|(?:关上|推开).{0,4}门)"),
        "一次清晰的开门或关门动作，保留空间反射，无脚步和人声",
        1_200,
        0.44,
        90,
    ),
    _EventSoundRule(
        "手机铃声",
        re.compile(r"(?:手机|电话).{0,6}(?:响了|响起|震动起来|铃声大作)"),
        "短促清晰的手机来电或震动提示，无人声和音乐",
        2_000,
        0.40,
        90,
    ),
    _EventSoundRule(
        "雷声",
        re.compile(r"(?:惊雷|闷雷|雷声).{0,8}(?:炸响|滚过|响起|传来|作响)"),
        "一次远近层次清晰的雷声，有自然余响，不带持续雨声",
        3_000,
        0.48,
        91,
    ),
    _EventSoundRule(
        "枪声",
        re.compile(r"(?:枪声.{0,6}(?:响起|炸响)|(?:扣动|扣下).{0,4}扳机|开了一枪)"),
        "单次枪响，短促干净，保留与当前空间一致的尾响",
        1_200,
        0.58,
        100,
    ),
    _EventSoundRule(
        "重物落地",
        re.compile(r"(?:摔在|砸在|跌落|掉在|坠落).{0,6}(?:地上|地面|桌面).{0,4}(?:砰|响)?"),
        "一次重物撞击表面的短音效，材质感清晰，无人声",
        1_000,
        0.48,
        91,
    ),
    _EventSoundRule(
        "玻璃器皿轻碰",
        re.compile(
            r"(?:玻璃罐|玻璃杯|酒杯|茶杯|瓷杯|杯子).{0,10}"
            r"(?:放下|搁下|推到|轻碰|相碰|磕在|发出.{0,3}轻响)"
        ),
        "一件玻璃或瓷质器皿接触桌面的短促轻响，克制、近距离，无碎裂",
        900,
        0.38,
        88,
    ),
    _EventSoundRule(
        "脚步声",
        re.compile(
            r"(?:脚步|鞋跟|鞋底).{0,10}(?:响起|靠近|远去|停下|落在|敲在|擦过)"
            r"|(?:踩着|踏过).{0,8}(?:地板|楼梯|石板|木地板)"
        ),
        "与当前地面材质一致的一小段脚步，不含对白与环境底噪",
        2_200,
        0.38,
        87,
    ),
    _EventSoundRule(
        "纸张文件声",
        re.compile(
            r"(?:纸张|纸页|信纸|报纸|文件袋|档案袋|牛皮纸袋).{0,10}"
            r"(?:翻开|翻动|抽出|抖开|放下|推过|摩擦|窸窣|沙沙作响)"
        ),
        "纸张或文件袋的一次近距离翻动与摩擦声，干净、短促，无人声",
        1_300,
        0.34,
        86,
    ),
    _EventSoundRule(
        "包装纸轻响",
        re.compile(
            r"(?:糖纸|包装纸|塑料袋|包装袋|包装纸).{0,10}"
            r"(?:拆开|撕开|揉|攥|摩擦|窸窣|发出.{0,3}响)"
        ),
        "一小片糖纸或塑料包装被触碰的细碎轻响，近距离、不过度夸张",
        1_000,
        0.32,
        85,
    ),
    _EventSoundRule(
        "钥匙门锁声",
        re.compile(
            r"(?:钥匙).{0,10}(?:插入|转动|拔出|碰撞|串在一起)"
            r"|(?:门锁|铜锁|锁芯).{0,8}(?:咔哒|转动|弹开|锁上|打开)"
        ),
        "钥匙与锁芯的一次金属操作声，机械细节清楚，短尾音",
        1_200,
        0.40,
        89,
    ),
    _EventSoundRule(
        "抽屉柜门声",
        re.compile(
            r"(?:抽屉|柜门|柜子|文件柜).{0,10}"
            r"(?:拉开|抽开|推开|关上|合上|撞上|滑开)"
        ),
        "一次抽屉或柜门开合声，材质与空间一致，无其他动作混入",
        1_200,
        0.39,
        87,
    ),
    _EventSoundRule(
        "桌椅摩擦",
        re.compile(r"(?:椅子|木椅|桌椅|凳子).{0,10}(?:挪动|拖动|推开|拉开|摩擦)"),
        "桌椅在地面上的一次短促移动摩擦，保留材质感，不拖长",
        1_200,
        0.36,
        84,
    ),
    _EventSoundRule(
        "钟表声",
        re.compile(r"(?:时钟|挂钟|座钟|钟摆|闹钟).{0,10}(?:滴答|敲响|报时|响起)"),
        "钟表的一小段滴答或一次报时，清晰但不压过对白",
        2_000,
        0.33,
        85,
    ),
    _EventSoundRule(
        "键盘敲击",
        re.compile(r"(?:键盘).{0,8}(?:敲击|敲下|噼啪|按下|输入)"),
        "一小段克制的电脑键盘敲击，节奏自然，无系统提示音",
        1_500,
        0.32,
        83,
    ),
    _EventSoundRule(
        "电梯提示音",
        re.compile(r"(?:电梯).{0,10}(?:叮|到达|提示音|门开了|门缓缓打开)"),
        "一次电梯到达提示音与极短机械尾音，无人声播报",
        1_200,
        0.36,
        86,
    ),
    _EventSoundRule(
        "拉链声",
        re.compile(r"(?:拉链).{0,8}(?:拉开|拉上|合上|滑动)"),
        "一次包袋或衣物拉链的短促滑动声，近距离、无布料夸张摩擦",
        900,
        0.32,
        83,
    ),
    _EventSoundRule(
        "倒水声",
        re.compile(r"(?:倒水|斟茶|斟酒|水流).{0,8}(?:杯|壶|响|落入)?"),
        "一次短促自然的液体倒入杯中声，无持续环境水声",
        1_500,
        0.34,
        84,
    ),
    # ── 自然环境 ─────────────────────────────────────────────────────────────
    _EventSoundRule(
        "风声呼啸",
        re.compile(r"(?:风).{0,6}(?:呼啸|刮起|怒吼|卷过|掠过|吹过|大作)"),
        "一阵由远及近的风声，有空间感，不含雨声和人声",
        2_500,
        0.36,
        82,
    ),
    _EventSoundRule(
        "雨滴敲打",
        re.compile(r"(?:雨滴|雨点|雨珠).{0,8}(?:敲打|拍打|落在|滴在|溅起)"),
        "一小段雨滴敲打表面的声音，材质感清晰，不含持续大雨底噪",
        2_000,
        0.32,
        81,
    ),
    _EventSoundRule(
        "水花溅起",
        re.compile(r"(?:水花|浪花|水珠).{0,8}(?:溅起|飞溅|四溅|迸溅)"),
        "一次短促的水花溅起声，液体质感清晰，无持续水流",
        1_000,
        0.38,
        83,
    ),
    _EventSoundRule(
        "鸟鸣啁啾",
        re.compile(r"(?:鸟|鸟鸣|鸟叫|麻雀|喜鹊|黄鹂).{0,8}(?:啁啾|鸣叫|啼叫|叫了|响起)"),
        "一两声清脆的鸟鸣，远近层次自然，不含持续环境底噪",
        1_800,
        0.30,
        80,
    ),
    _EventSoundRule(
        "树叶沙沙",
        re.compile(r"(?:树叶|枝叶|树梢|梧桐|柳叶).{0,8}(?:沙沙|簌簌|作响|摇动|晃动)"),
        "一小段树叶被风或触碰发出的沙沙声，轻柔自然",
        1_500,
        0.28,
        79,
    ),
    # ── 城市环境 ─────────────────────────────────────────────────────────────
    _EventSoundRule(
        "汽车驶过",
        re.compile(r"(?:汽车|轿车|卡车|货车|车子).{0,8}(?:驶过|开过|呼啸而过|疾驰|刹住)"),
        "一辆车由远及近再远去的行驶声，多普勒感自然，无喇叭和人声",
        2_500,
        0.34,
        82,
    ),
    _EventSoundRule(
        "喇叭声",
        re.compile(r"(?:喇叭|汽笛|车笛).{0,8}(?:响起|按响|鸣响|催促)"),
        "一次短促的汽车喇叭声，远近清晰，不含持续交通底噪",
        1_200,
        0.40,
        84,
    ),
    _EventSoundRule(
        "自行车铃",
        re.compile(r"(?:车铃|铃铛|自行车).{0,8}(?:叮铃|响起|按响|摇响)"),
        "一两声清脆的自行车铃声，金属质感，短促",
        1_000,
        0.34,
        82,
    ),
    # ── 室内动作 ─────────────────────────────────────────────────────────────
    _EventSoundRule(
        "翻书声",
        re.compile(r"(?:书|书页|书本|杂志).{0,8}(?:翻开|翻动|翻过|合上|打开)"),
        "一次纸张翻动的轻柔声响，近距离，干净短促",
        1_000,
        0.30,
        82,
    ),
    _EventSoundRule(
        "笔尖书写",
        re.compile(r"(?:笔|笔尖|钢笔|铅笔).{0,8}(?:写下|划过|沙沙|书写|落下)"),
        "一小段笔尖在纸上书写的沙沙声，近距离、克制",
        1_500,
        0.28,
        80,
    ),
    _EventSoundRule(
        "杯子轻放",
        re.compile(r"(?:杯子|茶杯|咖啡杯|马克杯).{0,8}(?:放下|搁下|推到|磕在|落在)"),
        "一只杯子接触桌面的短促轻响，材质感清晰，无碎裂",
        800,
        0.34,
        83,
    ),
    _EventSoundRule(
        "开关灯声",
        re.compile(r"(?:灯|开关|电灯|台灯).{0,8}(?:打开|关掉|按下|啪地|亮起|熄灭)"),
        "一次灯具开关的短促咔哒声，机械质感清晰",
        600,
        0.32,
        82,
    ),
    _EventSoundRule(
        "水龙头声",
        re.compile(r"(?:水龙头|水管|龙头).{0,8}(?:打开|拧开|关上|水流|哗哗)"),
        "一次水龙头开启或关闭的机械声加短促水流，无持续大量水声",
        1_500,
        0.34,
        83,
    ),
    # ── 身体动作 ─────────────────────────────────────────────────────────────
    _EventSoundRule(
        "拍掌声",
        re.compile(r"(?:掌声|拍手|鼓掌).{0,8}(?:响起|雷动|热烈|稀疏|停止)"),
        "一小段掌声，远近层次自然，不含人声和音乐",
        2_000,
        0.38,
        85,
    ),
    _EventSoundRule(
        "拳头砸桌",
        re.compile(r"(?:拳头|拳|巴掌|手掌).{0,8}(?:砸在|拍在|捶在|落在).{0,4}(?:桌|台|柜)"),
        "一次拳头或手掌重重击打桌面的短促冲击声，木质质感",
        800,
        0.46,
        88,
    ),
    _EventSoundRule(
        "衣物摩擦",
        re.compile(r"(?:衣服|衣袖|裙摆|布料).{0,8}(?:摩擦|窸窣|沙沙|作响)"),
        "一小段衣物布料摩擦的轻柔声响，近距离、克制",
        1_000,
        0.26,
        78,
    ),
    # ── 特殊事件 ─────────────────────────────────────────────────────────────
    _EventSoundRule(
        "远处爆炸",
        re.compile(r"(?:爆炸|轰|爆破).{0,8}(?:响起|传来|远处|轰鸣)"),
        "一次远景爆炸的低沉轰鸣，有空间距离感，不含近处碎片",
        2_500,
        0.44,
        90,
    ),
    _EventSoundRule(
        "火焰噼啪",
        re.compile(r"(?:火焰|火苗|篝火|炉火).{0,8}(?:噼啪|燃烧|跳动|窜起)"),
        "一小段火焰燃烧的噼啪声，温暖近距离，无爆炸和浓烟",
        2_000,
        0.32,
        82,
    ),
    _EventSoundRule(
        "警报声",
        re.compile(r"(?:警报|警铃|警笛).{0,8}(?:响起|大作|拉响|鸣响)"),
        "一段短促的警报声，电子或机械质感，不含人声播报",
        2_500,
        0.42,
        88,
    ),
    _EventSoundRule(
        "铃铛声",
        re.compile(r"(?:铃铛|风铃|铜铃|门铃).{0,8}(?:响起|叮当|摇响|清脆)"),
        "一两声清脆的铃铛声，金属余韵自然，短促",
        1_200,
        0.34,
        83,
    ),
    _EventSoundRule(
        "金属碰撞",
        re.compile(r"(?:金属|铁器|钢|铜器|器具).{0,8}(?:碰撞|撞击|相碰|叮当|落地)"),
        "一次金属物件碰撞的短促清脆声响，材质感清晰",
        1_000,
        0.40,
        85,
    ),
    _EventSoundRule(
        "窗户推开",
        re.compile(r"(?:窗户|窗扇|窗子|落地窗).{0,8}(?:推开|拉开|打开|关上|合上)"),
        "一次窗户开合的机械声，含轻微轨道滑动和定位咔哒",
        1_200,
        0.36,
        84,
    ),
    _EventSoundRule(
        "电话挂断",
        re.compile(r"(?:电话|听筒|话筒).{0,8}(?:挂断|挂上|放下|嘟声)"),
        "一次电话挂断的短促咔哒声加极短忙音，无人声",
        1_000,
        0.36,
        84,
    ),
    _EventSoundRule(
        "行李箱滚动",
        re.compile(r"(?:行李箱|拉杆箱|箱子).{0,8}(?:滚过|拖动|拉过|推行)"),
        "一只带轮行李箱在地面滚动的短促声，轮子质感清晰",
        2_000,
        0.32,
        81,
    ),
)

_EVENT_DEDUPE_TERMS = {
    "玻璃碎裂": ("玻璃", "碎"),
    "敲门声": ("敲", "门"),
    "开关门声": ("门",),
    "手机铃声": ("手机",),
    "雷声": ("雷",),
    "枪声": ("枪",),
    "重物落地": ("落地",),
    "玻璃器皿轻碰": ("玻璃",),
    "脚步声": ("脚步",),
    "纸张文件声": ("纸",),
    "包装纸轻响": ("包装",),
    "钥匙门锁声": ("锁",),
    "抽屉柜门声": ("柜",),
    "桌椅摩擦": ("椅",),
    "钟表声": ("钟",),
    "键盘敲击": ("键盘",),
    "电梯提示音": ("电梯",),
    "拉链声": ("拉链",),
    "倒水声": ("倒水",),
    "风声呼啸": ("风",),
    "雨滴敲打": ("雨",),
    "水花溅起": ("水花",),
    "鸟鸣啁啾": ("鸟",),
    "树叶沙沙": ("树叶",),
    "汽车驶过": ("车",),
    "喇叭声": ("喇叭",),
    "自行车铃": ("铃",),
    "翻书声": ("书",),
    "笔尖书写": ("笔",),
    "杯子轻放": ("杯",),
    "开关灯声": ("灯",),
    "水龙头声": ("水龙头",),
    "拍掌声": ("掌",),
    "拳头砸桌": ("拳",),
    "衣物摩擦": ("衣",),
    "远处爆炸": ("爆炸",),
    "火焰噼啪": ("火",),
    "警报声": ("警报",),
    "铃铛声": ("铃铛",),
    "金属碰撞": ("金属",),
    "窗户推开": ("窗",),
    "电话挂断": ("电话",),
    "行李箱滚动": ("行李",),
}

_EVIDENCE_BY_TAG = {
    "laugh": re.compile(r"笑|轻笑|嗤笑|冷笑"),
    "chuckle": re.compile(r"轻笑|低笑"),
    "cough": re.compile(r"咳|干咳"),
    "clear_throat": re.compile(r"清嗓|清了清嗓"),
    "groan": re.compile(r"呻吟|闷哼|低吟"),
    "pant": re.compile(r"喘|气喘吁吁"),
    "inhale": re.compile(r"吸气|倒吸"),
    "exhale": re.compile(r"呼气|吐出一口气"),
    "choke": re.compile(r"倒吸|哽咽|噤住"),
    "sniff": re.compile(r"吸鼻|抽鼻"),
    "sigh": re.compile(r"叹|叹息|叹了口气"),
    "snort": re.compile(r"哼了一声|嗤鼻"),
    "hum": re.compile(r"哼唱|鼻腔里哼"),
    "hiss": re.compile(r"嘶|倒抽冷气"),
    "hesitate": re.compile(r"犹豫|沉吟|嗯"),
    "sneeze": re.compile(r"喷嚏"),
}

# These rules detect medium-independent residue that is also suspicious in a
# spoken script.  Novel-only signals such as three-part lists, promotional
# prose, sentence rhythm, or persuasive language are deliberately not reused
# as dubbing defects: they may be intentional character voice.
_PORTABLE_HUMANIZE_PATTERN_IDS = frozenset(
    {
        "collaborative_artifact",
        "knowledge_cutoff_disclaimer",
        "markdown_formatting_residue",
        "sycophantic_tone",
    }
)
_ROLE_OR_SPEAKER_ISSUES = frozenset({"speaker_ownership", "acoustic_role"})
_PERFORMANCE_ISSUES = frozenset(
    {
        "emotional_intent",
        "performance_naturalness",
        "tts_stability",
        "paralinguistic_overuse",
    }
)
_MINIMAX_NON_INTERJECTION_TAGS = frozenset({"pause", "silence", "stutter", "emphasis"})


def _humanize_hits(
    text: str,
    *,
    enabled_pattern_ids: frozenset[str] | None = None,
    library_hits: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Reuse the novel humanize prescreen without mutating source prose."""

    if not text.strip():
        return []
    local_hits = HumanizeScanStep._canonicalize_prescreen_candidates(
        HumanizeScanStep.prescreen_text(
            text,
            filter_dialogue=False,
            enabled_pattern_ids=enabled_pattern_ids,
        )
    )
    return [
        *local_hits,
        *HumanizeScanStep._remove_duplicate_library_candidates(
            local_hits,
            list(library_hits or []),
        ),
    ]


def _dubbing_humanize_candidates(
    text: str,
    *,
    enabled_pattern_ids: frozenset[str] | None = None,
    library_hits: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Project novel prescreen signals into a dubbing-safe advisory shape."""

    projected: list[dict[str, object]] = []
    for hit in _humanize_hits(
        text,
        enabled_pattern_ids=enabled_pattern_ids,
        library_hits=library_hits,
    ):
        pattern_id = str(hit.get("pattern_id") or "")
        projected.append(
            {
                "pattern_id": pattern_id,
                "pattern_name": str(hit.get("pattern_name") or ""),
                "category": str(hit.get("category") or ""),
                "severity": str(hit.get("severity") or "medium"),
                "evidence_quote": str(hit.get("evidence_quote") or ""),
                "paragraph_index": hit.get("paragraph_index", 0),
                "span_start": hit.get("span_start"),
                "span_end": hit.get("span_end"),
                "confidence": hit.get("confidence", hit.get("similarity", 0.0)),
                "actionable": False,
                "source": str(hit.get("source") or "local"),
                "applicability": (
                    "portable_residue"
                    if pattern_id in _PORTABLE_HUMANIZE_PATTERN_IDS
                    else "novel_context_only"
                ),
            }
        )
    severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}

    def sort_key(item: dict[str, object]) -> tuple[bool, int, float, int]:
        confidence = item.get("confidence")
        span_start = item.get("span_start")
        return (
            item["applicability"] != "portable_residue",
            -severity_rank.get(str(item["severity"]), 0),
            -float(confidence) if isinstance(confidence, (int, float)) else 0.0,
            span_start if isinstance(span_start, int) else 0,
        )

    projected.sort(key=sort_key)
    return projected[:8]


def _spoken_adaptation_is_safe(segment: DubbingSegment) -> bool:
    """Baseline safety check for spoken_text (relaxed from legacy substring check).

    The legacy implementation required the original text to be a verbatim
    substring of spoken_text with at most +8 extra characters.  This effectively
    blocked all meaningful oral rewrites.  The new independent
    ``rewrite_spoken_text`` step performs proper semantic validation; this
    function now only guards against obviously dangerous content.

    Checks retained:
    - No HTML/Markdown tags in spoken_text.
    - spoken_text length does not exceed 3x the original text length.
    - spoken_text does not introduce quality regressions (humanize hits).
    """
    spoken = segment.spoken_text.strip()
    if not spoken:
        return True
    # Guard against HTML/Markdown injection.
    if re.search(r"<[^>]+>|\[/?[a-z_]+\]", spoken, re.IGNORECASE):
        return False
    # Guard against extreme length expansion.
    source_core = re.sub(r"[\s\W_]+", "", segment.text, flags=re.UNICODE)
    spoken_core = re.sub(r"[\s\W_]+", "", spoken, flags=re.UNICODE)
    if source_core and len(spoken_core) > len(source_core) * 3:
        return False
    # Retain humanize regression check.
    source_hits = _humanize_hits(segment.text)
    spoken_hits = _humanize_hits(spoken)
    if len(spoken_hits) > len(source_hits):
        return False
    return not HumanizeScanStep.introduced_quality_regressions(segment.text, spoken)


def _review_paralinguistic_tags(
    segment: DubbingSegment,
) -> tuple[list[ParalinguisticTag], int]:
    """Keep pauses freely, but require textual evidence for performed sounds."""

    kept: list[ParalinguisticTag] = []
    removed = 0
    seen: set[tuple[str, int]] = set()
    for tag in sorted(segment.paralinguistic_tags, key=lambda item: item.position):
        tag_type = str(tag.tag_type or "").strip().lower()
        key = (tag_type, round(tag.position * 10))
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        if tag_type in {"pause", "silence", "emphasis", "stutter", "breath"}:
            kept.append(tag)
            continue
        evidence = _EVIDENCE_BY_TAG.get(tag_type)
        context = f"{segment.text} {tag.description}"
        if evidence is not None and evidence.search(context):
            kept.append(tag)
        else:
            removed += 1
    # Multiple performed noises in one sentence sound synthetic very quickly.
    if len(kept) > 2:
        removed += len(kept) - 2
        kept = kept[:2]
    return kept, removed


def _event_offset_ms(segment: DubbingSegment, match_start: int) -> int:
    text_length = max(1, len(segment.synthesis_text))
    estimated_duration = max(900, text_length * 190)
    return min(30_000, int(estimated_duration * match_start / text_length))


def _recover_event_sfx(
    segments: list[DubbingSegment],
    existing: list[SFXCue],
) -> tuple[list[SFXCue], list[dict[str, object]]]:
    cues = list(existing)
    added: list[dict[str, object]] = []
    occupied = {
        (cue.trigger_segment_index, re.sub(r"\s+", "", cue.effect_name).casefold()) for cue in cues
    }
    per_segment = Counter(
        cue.trigger_segment_index for cue in cues if cue.trigger_segment_index is not None
    )
    max_recovered = min(32, max(12, len(segments) // 6))
    for segment in segments:
        if len(added) >= max_recovered:
            break
        for rule in _EVENT_SOUND_RULES:
            if per_segment[segment.segment_index] >= 2:
                break
            match = rule.pattern.search(segment.text)
            if match is None:
                continue
            key = (segment.segment_index, rule.effect_name.casefold())
            if key in occupied:
                continue
            dedupe_terms = _EVENT_DEDUPE_TERMS.get(rule.effect_name, ())
            if any(
                cue.trigger_segment_index == segment.segment_index
                and all(term in f"{cue.effect_name}{cue.description}" for term in dedupe_terms)
                for cue in cues
            ):
                continue
            cue = SFXCue(
                effect_name=rule.effect_name,
                description=rule.description,
                trigger_segment_index=segment.segment_index,
                offset_ms=_event_offset_ms(segment, match.start()),
                duration_ms=rule.duration_ms,
                volume=rule.volume,
                allow_dialogue_overlap=True,
                maximum_timing_shift_ms=max(1000, rule.duration_ms + 500),
                narrative_priority=rule.priority,
            )
            cues.append(cue)
            occupied.add(key)
            per_segment[segment.segment_index] += 1
            added.append(
                {
                    "segment_index": segment.segment_index,
                    "effect_name": rule.effect_name,
                    "evidence": match.group(0)[:80],
                    "offset_ms": cue.offset_ms,
                }
            )
    return cues, added


def _strip_generated_performance_overrides(
    segments: list[DubbingSegment],
) -> tuple[list[DubbingSegment], int, int, list[int]]:
    """Remove LLM-generated numeric overrides, speed curves, and unsafe spoken_text.

    Returns (cleaned_segments, removed_numeric_overrides, removed_paralinguistic_tags,
    spoken_text_rollbacks).

    Author: novel-forge
    """
    cleaned: list[DubbingSegment] = []
    removed_numeric_overrides = 0
    removed_paralinguistic_tags = 0
    spoken_text_rollbacks: list[int] = []
    for segment in segments:
        updates: dict[str, object] = {}
        for field_name in ("speed_override", "vol_override", "pitch_override"):
            if getattr(segment, field_name) is not None:
                updates[field_name] = None
                removed_numeric_overrides += 1
        if segment.speed_curve:
            # speed_curve is LLM-generated but unstable and zero-consumed by
            # synthesize_audio_step.  Clear to prevent downstream misuse.
            updates["speed_curve"] = []
        if not _spoken_adaptation_is_safe(segment):
            updates["spoken_text"] = ""
            spoken_text_rollbacks.append(segment.segment_index)
        tags, removed = _review_paralinguistic_tags(segment)
        removed_paralinguistic_tags += removed
        if tags != list(segment.paralinguistic_tags):
            updates["paralinguistic_tags"] = tags
        cleaned.append(
            refresh_segment_uid(segment.model_copy(update=updates)) if updates else segment
        )
    return cleaned, removed_numeric_overrides, removed_paralinguistic_tags, spoken_text_rollbacks


def _scan_humanize_candidates(
    segments: list[DubbingSegment],
    *,
    use_humanize_library: bool,
) -> dict[str, object]:
    """Scan segments for AI-flavor residue using the humanize library.

    Returns a dict with keys: source_humanize_hits, candidate_segment_indices,
    segment_humanize_candidates, humanize_counts.

    Author: novel-forge
    """
    segment_humanize_candidates: list[dict[str, object]] = []
    source_humanize_hits: list[dict[str, object]] = []
    candidate_segment_indices: list[int] = []
    library = None
    retriever = None
    enabled_pattern_ids: frozenset[str] | None = None
    try:
        if not use_humanize_library:
            raise FileNotFoundError("humanize library not requested")
        from novel_forge.core.config import Settings
        from novel_forge.memory.humanize_library_store import HumanizeLibrary
        from novel_forge.memory.humanize_retrieval import HumanizeLibraryRetriever

        library_path = Settings().humanize_library_resolved_path / "library.db"
        if not library_path.is_file():
            raise FileNotFoundError(library_path)
        library = HumanizeLibrary.from_path(library_path)
        if library.is_healthy():
            enabled_pattern_ids = frozenset(
                entry.pattern_id
                for entry in library.list_all()
                if entry.enabled and entry.project_id is None
            )
            retriever = HumanizeLibraryRetriever(embedder=None)
    except Exception:
        library = None
        retriever = None

    try:
        for segment in segments:
            library_hits: list[dict[str, object]] = []
            if library is not None and retriever is not None:
                library_hits = retriever.retrieve(
                    library,
                    segment.synthesis_text,
                    top_k=8,
                )
            candidates = _dubbing_humanize_candidates(
                segment.synthesis_text,
                enabled_pattern_ids=enabled_pattern_ids,
                library_hits=library_hits,
            )
            if not candidates:
                continue
            candidate_segment_indices.append(segment.segment_index)
            source_humanize_hits.extend(candidates)
            segment_humanize_candidates.append(
                {
                    "segment_index": segment.segment_index,
                    "candidates": candidates,
                }
            )
    finally:
        if library is not None:
            library.close()
    humanize_counts = Counter(str(hit.get("pattern_id") or "") for hit in source_humanize_hits)
    return {
        "source_humanize_hits": source_humanize_hits,
        "candidate_segment_indices": candidate_segment_indices,
        "segment_humanize_candidates": segment_humanize_candidates,
        "humanize_counts": humanize_counts,
    }


def _neutralize_single_char_dialogue(
    segments: list[DubbingSegment],
) -> tuple[list[DubbingSegment], int]:
    """Strip emotion from <=1 speakable-char dialogue/inner_thought segments.

    Defense-in-depth: if any single-char spoken segments survive the upstream
    fold (e.g. rule-based fallback path), neutralize them so they synthesize
    as flat, minimal utterances rather than emotionally charged blips.

    Author: novel-forge
    """
    single_char_neutralized = 0
    for idx, segment in enumerate(segments):
        if segment.segment_type not in (SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT):
            continue
        speakable = re.sub(r"[\s\W_]+", "", segment.text, flags=re.UNICODE)
        if len(speakable) > 1:
            continue
        if segment.paralinguistic_tags or segment.transition is not None:
            continue
        if segment.emotion != EmotionTag.NEUTRAL or segment.speed_override is not None:
            segments[idx] = refresh_segment_uid(
                segment.model_copy(
                    update={
                        "emotion": EmotionTag.NEUTRAL,
                        "sub_emotion": None,
                        "emotion_intensity": 0.0,
                        "speed_override": None,
                        "vol_override": None,
                        "pitch_override": None,
                        "tone_hint": "",
                    }
                )
            )
            single_char_neutralized += 1
    return segments, single_char_neutralized


def _merge_pure_punct_narration(
    segments: list[DubbingSegment],
) -> tuple[list[DubbingSegment], int]:
    """Merge or silence narration segments with no speakable characters.

    Safety net for cases where _merge_narration_continuations could not fire
    (e.g. the pure-punct segment is the first in its paragraph).  Merges into
    the nearest adjacent same-paragraph segment, or converts to SILENCE.

    Author: novel-forge
    """
    pure_punct_merged = 0
    indices_to_remove: set[int] = set()
    for idx, segment in enumerate(segments):
        if segment.segment_type != SegmentType.NARRATION:
            continue
        if not segment.text.strip() or normalize_speakable_text(segment.text):
            continue
        # Try merging into previous same-paragraph segment.
        merged_into = -1
        if idx > 0 and segments[idx - 1].source_paragraph == segment.source_paragraph:
            merged_into = idx - 1
        elif idx + 1 < len(segments) and segments[idx + 1].source_paragraph == segment.source_paragraph:
            merged_into = idx + 1
        if merged_into >= 0 and merged_into not in indices_to_remove:
            target = segments[merged_into]
            if merged_into < idx:
                new_text = target.text + segment.text
            else:
                new_text = segment.text + target.text
            segments[merged_into] = refresh_segment_uid(
                target.model_copy(update={"text": new_text, "spoken_text": ""})
            )
            indices_to_remove.add(idx)
            pure_punct_merged += 1
    if indices_to_remove:
        segments = [
            seg.model_copy(update={"segment_index": new_idx})
            for new_idx, seg in enumerate(
                s for i, s in enumerate(segments) if i not in indices_to_remove
            )
        ]

    # Convert any remaining un-mergeable pure-punct narration to SILENCE.
    for idx, segment in enumerate(segments):
        if (
            segment.segment_type == SegmentType.NARRATION
            and segment.text.strip()
            and not normalize_speakable_text(segment.text)
        ):
            segments[idx] = segment.model_copy(
                update={"segment_type": SegmentType.SILENCE}
            )
            pure_punct_merged += 1
    return segments, pure_punct_merged


def review_and_repair_dubbing_script(
    script: DubbingScript,
    *,
    use_humanize_library: bool = False,
) -> DubbingScript:
    """Apply a source-safe professional review to an AI-produced script.

    Numeric speed/volume/pitch overrides are removed here because they were
    generated rather than authored.  The segment editor runs after this layer,
    so an explicit user adjustment remains authoritative and is never stripped.

    Author: novel-forge
    """

    # Step 1: Strip generated performance overrides and unsafe spoken_text.
    segments, removed_numeric_overrides, removed_paralinguistic_tags, spoken_text_rollbacks = (
        _strip_generated_performance_overrides(list(script.segments))
    )

    # Step 2: Recover high-confidence event sound effects from narration text.
    sfx_cues, recovered_events = _recover_event_sfx(segments, list(script.sfx_cues))

    # Step 3: Scan for AI-flavor residue (advisory only for authoritative text).
    humanize_result = _scan_humanize_candidates(
        segments, use_humanize_library=use_humanize_library
    )
    source_humanize_hits = humanize_result["source_humanize_hits"]
    candidate_segment_indices = humanize_result["candidate_segment_indices"]
    segment_humanize_candidates = humanize_result["segment_humanize_candidates"]
    humanize_counts = humanize_result["humanize_counts"]

    # Step 4: Neutralize single-char dialogue segments.
    segments, single_char_neutralized = _neutralize_single_char_dialogue(segments)

    # Step 5: Merge or silence pure-punctuation narration.
    segments, pure_punct_merged = _merge_pure_punct_narration(segments)

    # Assemble review metadata.
    metadata = dict(script.metadata)
    metadata["professional_script_review"] = {
        "status": "passed",
        "source_text_immutable": True,
        "removed_generated_numeric_overrides": removed_numeric_overrides,
        "removed_unsupported_performance_tags": removed_paralinguistic_tags,
        "spoken_text_rollback_segment_indices": spoken_text_rollbacks,
        "single_char_neutralized": single_char_neutralized,
        "pure_punct_narration_merged": pure_punct_merged,
        "recovered_event_sfx": recovered_events,
        "sound_effect_audit": {
            "mode": "high_confidence_foley_recovery",
            "recovered_count": len(recovered_events),
            "total_sfx_cues": len(sfx_cues),
            "chapter_cap": min(32, max(12, len(segments) // 6)),
        },
        "humanize_prescreen": {
            "engine": "HumanizeScanStep",
            "candidate_count": len(source_humanize_hits),
            "counts_by_pattern": dict(humanize_counts),
            "portable_residue_count": sum(
                1 for hit in source_humanize_hits if hit.get("applicability") == "portable_residue"
            ),
            "candidate_segment_indices": candidate_segment_indices,
            "segment_candidates": segment_humanize_candidates,
            "advisory_only_for_authoritative_text": True,
        },
    }
    return script.model_copy(
        update={
            "segments": segments,
            "sfx_cues": sfx_cues,
            "metadata": metadata,
        }
    )


def apply_tts_platform_contract(
    script: DubbingScript,
    *,
    provider_id: str,
    model_id: str,
) -> DubbingScript:
    """Make the persisted performance plan executable by its selected TTS model.

    The LLM may suggest a valid dramatic action that a particular model cannot
    render.  Persisting it as though it would be performed makes audition
    feedback misleading, so unsupported MiniMax interjections and Bailian
    performance tags are removed before the script reaches the Voice Studio.
    Explicit pauses remain valid timing controls for all current model families.

    Bailian (DashScope) branch: AIGC-tag models (qwen-audio-3.0-tts-*,
    cosyvoice-v3-flash/plus, cosyvoice-v2) keep the adapter's performable
    vocal-tag set; instruction-only models (qwen3-tts-flash, cosyvoice-v3.5-*,
    ...) keep only pause/silence — every other intent must travel through the
    natural-language instruction channel.
    """

    provider = str(provider_id or "").strip().lower()
    bailian = is_bailian_provider(provider)
    if bailian:
        supported_vocal_tags: frozenset[str] | None = dashscope_supported_vocal_tags(model_id)
        supports_interjections = "laugh" in supported_vocal_tags
    elif provider == "minimax":
        supports_interjections = minimax_supports_interjections(model_id)
        supported_vocal_tags = None if supports_interjections else _MINIMAX_NON_INTERJECTION_TAGS
    else:
        supports_interjections = True
        supported_vocal_tags = None

    removed = 0
    segments: list[DubbingSegment] = []
    for segment in script.segments:
        if supported_vocal_tags is None:
            segments.append(segment)
            continue
        supported_tags = [
            tag
            for tag in segment.paralinguistic_tags
            if str(tag.tag_type or "").strip().lower() in supported_vocal_tags
        ]
        removed += len(segment.paralinguistic_tags) - len(supported_tags)
        segments.append(
            refresh_segment_uid(segment.model_copy(update={"paralinguistic_tags": supported_tags}))
            if len(supported_tags) != len(segment.paralinguistic_tags)
            else segment
        )

    metadata = dict(script.metadata)
    contract_record: dict[str, object] = {
        "provider": provider,
        "model": str(model_id or ""),
        "interjections_supported": supports_interjections,
        "removed_unsupported_interjections": removed,
    }
    if bailian:
        contract_record["platform"] = "bailian"
        contract_record["aigc_tag_model"] = dashscope_supports_aigc_tags(model_id)
        contract_record["supported_vocal_tags"] = sorted(supported_vocal_tags or ())
    metadata["tts_platform_contract"] = contract_record
    return script.model_copy(update={"segments": segments, "metadata": metadata})


def build_dubbing_review_stage_cards(
    script: DubbingScript,
    voice_team: VoiceTeamContract,
    *,
    tts_platform: str = "minimax",
    tts_model: str = "speech-2.8-hd",
) -> dict[str, object]:
    """Build bounded, performance-oriented cards for the specialist reviewer."""

    cast_names = {
        entry.character_id: entry.character_name
        for entry in voice_team.entries
        if entry.character_id and entry.character_name
    }
    segments: list[dict[str, object]] = []
    for position, segment in enumerate(script.segments):
        previous = script.segments[position - 1] if position > 0 else None
        following = script.segments[position + 1] if position + 1 < len(script.segments) else None
        paragraph_text = "".join(
            item.text
            for item in script.segments
            if item.source_paragraph == segment.source_paragraph
        )
        segments.append(
            {
                "segment_index": segment.segment_index,
                "segment_type": segment.segment_type.value,
                "character_id": segment.character_id,
                "character_name": segment.character_name,
                "text": segment.text,
                "spoken_text": segment.spoken_text,
                "emotion": segment.emotion.value,
                "sub_emotion": segment.sub_emotion.value if segment.sub_emotion else "",
                "tone_hint": segment.tone_hint,
                "paralinguistic_tags": [
                    tag.model_dump(mode="json", exclude={"schema_version", "created_at"})
                    for tag in segment.paralinguistic_tags
                ],
                "scene_context": segment.scene_context,
                "source_paragraph": segment.source_paragraph,
                "source_paragraph_text": paragraph_text,
                "characters_named_in_paragraph": [
                    {"character_id": character_id, "character_name": character_name}
                    for character_id, character_name in cast_names.items()
                    if character_name in paragraph_text
                ],
                "recent_spoken_turns": [
                    {
                        "segment_index": item.segment_index,
                        "character_id": item.character_id,
                        "character_name": item.character_name,
                        "text": item.text,
                    }
                    for item in script.segments[max(0, position - 4) : position]
                    if item.character_id
                ][-2:],
                "previous_segment": (
                    {
                        "segment_type": previous.segment_type.value,
                        "character_id": previous.character_id,
                        "text": previous.text,
                    }
                    if previous is not None
                    else None
                ),
                "next_segment": (
                    {
                        "segment_type": following.segment_type.value,
                        "character_id": following.character_id,
                        "text": following.text,
                    }
                    if following is not None
                    else None
                ),
                "humanize_candidates": _dubbing_humanize_candidates(segment.synthesis_text),
            }
        )
    
    # Load platform-specific emotion vocabulary for LLM guidance.
    from novel_forge.tts.platform.rewrite_profiles import load_rewrite_profile
    
    rewrite_profile = load_rewrite_profile(tts_platform)
    
    # Build chapter emotion arc sampling (max 20 sample points).
    total = len(script.segments)
    arc_step = max(1, total // 20)
    chapter_emotion_arc = [
        {
            "pos": i,
            "type": script.segments[i].segment_type.value,
            "char": script.segments[i].character_name or "旁白",
            "emotion": script.segments[i].emotion.value,
            "intensity": script.segments[i].emotion_intensity,
        }
        for i in range(0, total, arc_step)
    ]
    
    # Narrative position for opening-calm principle awareness.
    narrative_position = {
        "chapter_number": script.chapter_number,
        "is_first_chapter": script.chapter_number == 1,
        "is_opening": True,
    }

    # Quality metrics: give the LLM reviewer awareness of current emotion coverage
    # so it can proactively correct under-differentiated segments.
    total_segments = len(script.segments)
    non_neutral = sum(
        1 for s in script.segments if s.emotion.value != "neutral"
    )
    emotion_differentiation = non_neutral / total_segments if total_segments > 0 else 0.0
    quality_metrics = {
        "emotion_differentiation": round(emotion_differentiation, 3),
        "emotion_differentiation_target": 0.15,
        "neutral_segment_ratio": round(1.0 - emotion_differentiation, 3),
    }

    return {
        "chapter_number": script.chapter_number,
        "tts_platform": tts_platform,
        "tts_model": tts_model,
        "segments": segments,
        "voice_team": [
            {
                "character_id": entry.character_id,
                "character_name": entry.character_name,
                # 身份硬约束：审校时据此发现“女角色被标了男声 tone_hint”等错配。
                "gender": entry.character_gender,
                "age": entry.character_age,
                "role": entry.character_role,
                "conditional_performance_directions": [
                    direction.model_dump(mode="json")
                    for direction in voice_performance_profile(entry).conditional_directions
                ],
            }
            for entry in voice_team.entries
            if entry.character_id
        ],
        "review_boundaries": {
            "authoritative_text_immutable": True,
            "numeric_speed_pitch_volume_forbidden": True,
            "speaker_or_role_changes_require_manual_review": True,
            "humanize_reuse": "candidate_signals_only",
        },
        "rewrite_profile": rewrite_profile,
        "chapter_emotion_arc": chapter_emotion_arc,
        "narrative_position": narrative_position,
        "quality_metrics": quality_metrics,
    }


def _decision_evidence_context(script: DubbingScript, position: int) -> str:
    paragraph_index = script.segments[position].source_paragraph
    paragraph_text = "".join(
        segment.text for segment in script.segments if segment.source_paragraph == paragraph_index
    )
    start = max(0, position - 1)
    end = min(len(script.segments), position + 2)
    adjacent_text = "\n".join(segment.text for segment in script.segments[start:end])
    return f"{paragraph_text}\n{adjacent_text}"


def _coerce_confidence(value: object) -> float:
    if not isinstance(value, (int, float, str)):
        return 0.0
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _coerce_issue_types(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _reviewed_tags(
    segment: DubbingSegment,
    raw_tags: object,
) -> tuple[list[ParalinguisticTag] | None, str]:
    if not isinstance(raw_tags, list):
        return None, "recommended_paralinguistic_tags_not_list"
    parsed: list[ParalinguisticTag] = []
    try:
        for raw_tag in raw_tags[:2]:
            if not isinstance(raw_tag, Mapping):
                return None, "recommended_paralinguistic_tag_not_object"
            parsed.append(ParalinguisticTag.model_validate(dict(raw_tag)))
    except (TypeError, ValueError) as exc:
        return None, f"invalid_paralinguistic_tag:{type(exc).__name__}"
    candidate = segment.model_copy(update={"paralinguistic_tags": parsed})
    kept, removed = _review_paralinguistic_tags(candidate)
    if removed:
        return kept, "unsupported_paralinguistic_tag_removed"
    return kept, ""


def apply_dubbing_review_decisions(
    script: DubbingScript,
    response: Mapping[str, object],
    *,
    allowed_character_ids: set[str],
) -> DubbingScript:
    """Apply only source-safe performance decisions from the specialist LLM.

    Acoustic-role and speaker changes are never silently applied.  High-confidence,
    source-anchored concerns are persisted as blocking manual-review indices so a
    suspicious dialogue segment cannot fall through to the narrator voice.
    """

    segments = list(script.segments)
    positions = {segment.segment_index: position for position, segment in enumerate(segments)}
    raw_decisions = response.get("decisions", [])
    decisions = raw_decisions if isinstance(raw_decisions, list) else []
    seen: set[int] = set()
    applied: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    manual_reviews: list[dict[str, object]] = []
    manual_review_indices: set[int] = set()
    novel_text_advisories: list[dict[str, object]] = []

    for raw_decision in decisions:
        if not isinstance(raw_decision, Mapping):
            rejected.append({"reason": "decision_not_object"})
            continue
        try:
            segment_index = int(raw_decision.get("segment_index", -1))
        except (TypeError, ValueError):
            segment_index = -1
        if segment_index not in positions or segment_index in seen:
            rejected.append({"segment_index": segment_index, "reason": "unknown_or_duplicate"})
            continue
        seen.add(segment_index)
        position = positions[segment_index]
        segment = segments[position]
        issues = _coerce_issue_types(raw_decision.get("issue_types"))
        confidence = _coerce_confidence(raw_decision.get("confidence"))
        evidence = str(raw_decision.get("evidence") or "").strip()
        context = _decision_evidence_context(script, position)
        evidence_anchored = bool(evidence and evidence in context)
        verdict = str(raw_decision.get("verdict") or "").strip()

        if "novel_text_advisory" in issues:
            novel_text_advisories.append(
                {
                    "segment_index": segment_index,
                    "evidence": evidence[:160],
                    "rationale": str(raw_decision.get("rationale") or "")[:240],
                    "auto_repaired": False,
                }
            )

        if issues & _ROLE_OR_SPEAKER_ISSUES:
            if verdict == "manual_review" and confidence >= 0.85 and evidence_anchored:
                recommended_character_id = str(
                    raw_decision.get("recommended_character_id") or ""
                ).strip()
                if (
                    recommended_character_id
                    and recommended_character_id not in allowed_character_ids
                ):
                    recommended_character_id = ""
                manual_reviews.append(
                    {
                        "segment_index": segment_index,
                        "issue_types": sorted(issues & _ROLE_OR_SPEAKER_ISSUES),
                        "confidence": confidence,
                        "evidence": evidence[:160],
                        "recommended_segment_type": str(
                            raw_decision.get("recommended_segment_type") or "unchanged"
                        ),
                        "recommended_character_id": recommended_character_id,
                        "rationale": str(raw_decision.get("rationale") or "")[:240],
                    }
                )
                manual_review_indices.add(segment_index)
            else:
                rejected.append(
                    {
                        "segment_index": segment_index,
                        "reason": "unverified_role_or_speaker_concern",
                    }
                )

        if not (issues & _PERFORMANCE_ISSUES):
            continue
        if verdict != "revise_performance" or confidence < 0.72 or not evidence_anchored:
            rejected.append(
                {
                    "segment_index": segment_index,
                    "reason": "performance_decision_below_safety_threshold",
                }
            )
            continue

        updates: dict[str, object] = {}
        fields: list[str] = []
        raw_emotion = str(raw_decision.get("recommended_emotion") or "unchanged").strip()
        if raw_emotion != "unchanged":
            try:
                emotion = EmotionTag(raw_emotion)
            except ValueError:
                emotion = None
            if emotion is not None and emotion != segment.emotion:
                updates["emotion"] = emotion
                fields.append("emotion")

        tone_hint = str(raw_decision.get("recommended_tone_hint") or "").strip()
        if tone_hint and len(tone_hint) <= 40 and tone_hint != segment.tone_hint:
            updates["tone_hint"] = tone_hint
            fields.append("tone_hint")

        raw_tags = raw_decision.get("recommended_paralinguistic_tags")
        if "paralinguistic_overuse" in issues or (isinstance(raw_tags, list) and raw_tags):
            tags, tag_warning = _reviewed_tags(segment, raw_tags)
            if tags is not None and tags != list(segment.paralinguistic_tags):
                updates["paralinguistic_tags"] = tags
                fields.append("paralinguistic_tags")
            if tag_warning:
                rejected.append({"segment_index": segment_index, "reason": tag_warning})

        # Oral adaptation: apply recommended_spoken_text when confidence is high
        # and the rewrite is a reasonable length relative to the source text.
        recommended_spoken = str(raw_decision.get("recommended_spoken_text") or "").strip()
        if recommended_spoken and confidence >= 0.75:
            source_len = max(1, len(segment.text))
            if len(recommended_spoken) <= int(source_len * 1.5) and len(recommended_spoken) >= 2:
                if recommended_spoken != segment.spoken_text:
                    updates["spoken_text"] = recommended_spoken
                    fields.append("spoken_text")

        if updates:
            segments[position] = refresh_segment_uid(segment.model_copy(update=updates))
            applied.append(
                {
                    "segment_index": segment_index,
                    "fields": fields,
                    "confidence": confidence,
                }
            )

    raw_reviewed_count = response.get("reviewed_segment_count", 0)
    if isinstance(raw_reviewed_count, (int, float, str)):
        try:
            reviewed_count = int(raw_reviewed_count)
        except (TypeError, ValueError):
            reviewed_count = 0
    else:
        reviewed_count = 0
    coverage_complete = reviewed_count == len(script.segments)
    metadata = dict(script.metadata)
    review = dict(metadata.get("professional_script_review") or {})
    blocking_indices = sorted(manual_review_indices)
    review["llm_review"] = {
        "status": "needs_review"
        if blocking_indices
        else ("passed" if coverage_complete else "incomplete"),
        "task_type": "tts_review_dubbing_script",
        "independently_routable": True,
        "reviewed_segment_count": reviewed_count,
        "expected_segment_count": len(script.segments),
        "coverage_complete": coverage_complete,
        "overall_verdict": str(response.get("overall_verdict") or "needs_review"),
        "applied_performance_repairs": applied,
        "manual_review_items": manual_reviews,
        "manual_review_segment_indices": blocking_indices,
        "novel_text_advisories": novel_text_advisories,
        "rejected_decisions": rejected,
        "summary": str(response.get("summary") or "")[:500],
        "humanize_reuse_boundary": (
            "HumanizeScanStep only supplies advisory candidates; novel-oriented style signals "
            "never directly modify dubbing performance or authoritative text."
        ),
    }
    metadata["professional_script_review"] = review
    return script.model_copy(update={"segments": segments, "metadata": metadata})


def annotate_dubbing_llm_review_status(
    script: DubbingScript,
    *,
    status: str,
    reason: str = "",
) -> DubbingScript:
    """Record an explicit safe fallback when specialist review cannot run."""

    metadata = dict(script.metadata)
    review = dict(metadata.get("professional_script_review") or {})
    review["llm_review"] = {
        "status": status,
        "task_type": "tts_review_dubbing_script",
        "independently_routable": True,
        "reason": reason[:500],
        "humanize_reuse_boundary": "candidate_signals_only",
    }
    metadata["professional_script_review"] = review
    return script.model_copy(update={"metadata": metadata})


__all__ = [
    "annotate_dubbing_llm_review_status",
    "apply_dubbing_review_decisions",
    "build_dubbing_review_stage_cards",
    "review_and_repair_dubbing_script",
]
