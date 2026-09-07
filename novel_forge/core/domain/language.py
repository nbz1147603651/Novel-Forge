"""Language helpers for prompt rendering and generated text normalization."""

from __future__ import annotations

import importlib
import re
from functools import lru_cache
from typing import Any

_ZH_SIMPLIFIED_ALIASES = {
    "zh",
    "zh-cn",
    "zh-sg",
    "zh-hans",
    "cn",
    "chinese",
    "chinese-simplified",
    "simplified-chinese",
    "简体",
    "简体中文",
}

_ZH_TRADITIONAL_ALIASES = {
    "zh-tw",
    "zh-hk",
    "zh-mo",
    "zh-hant",
    "chinese-traditional",
    "traditional-chinese",
    "繁体",
    "繁體",
    "繁体中文",
    "繁體中文",
}

_EN_ALIASES = {"en", "en-us", "en-gb", "english"}

# Lightweight fallback when OpenCC is unavailable. OpenCC is preferred because
# Chinese conversion is context-sensitive; this table only covers common drift
# observed in model outputs and keeps the app functional in minimal installs.
_FALLBACK_T2S = str.maketrans(
    {
        "與": "与",
        "於": "于",
        "這": "这",
        "個": "个",
        "為": "为",
        "來": "来",
        "時": "时",
        "後": "后",
        "對": "对",
        "還": "还",
        "會": "会",
        "無": "无",
        "麼": "么",
        "說": "说",
        "過": "过",
        "裡": "里",
        "裏": "里",
        "當": "当",
        "雲": "云",
        "鳴": "鸣",
        "鶴": "鹤",
        "陸": "陆",
        "崢": "峥",
        "學": "学",
        "準": "准",
        "備": "备",
        "發": "发",
        "現": "现",
        "鐘": "钟",
        "聲": "声",
        "圓": "圆",
        "滿": "满",
        "階": "阶",
        "溫": "温",
        "環": "环",
        "關": "关",
        "聯": "联",
        "記": "记",
        "憶": "忆",
        "舊": "旧",
        "漸": "渐",
        "護": "护",
        "纏": "缠",
        "終": "终",
        "點": "点",
        "氣": "气",
        "團": "团",
        "慶": "庆",
        "節": "节",
        "進": "进",
        "導": "导",
        "師": "师",
        "係": "系",
        "內": "内",
        "斂": "敛",
        "萬": "万",
        "虛": "虚",
        "書": "书",
        "寫": "写",
        "機": "机",
        "將": "将",
        "臺": "台",
        "檯": "台",
        "幟": "帜",
        "湧": "涌",
        "彷": "仿",
        "彿": "佛",
        "靈": "灵",
        "處": "处",
        "劇": "剧",
        "顧": "顾",
        "隱": "隐",
        "祕": "秘",
        "脈": "脉",
        "費": "费",
        "業": "业",
        "頂": "顶",
        "觸": "触",
        "暈": "晕",
        "曉": "晓",
        "犧": "牺",
        "創": "创",
        "傷": "伤",
        "殘": "残",
        "懼": "惧",
        "諾": "诺",
        "轉": "转",
        "擁": "拥",
        "離": "离",
        "標": "标",
        "誌": "志",
        "習": "习",
        "撫": "抚",
        "繫": "系",
        "淺": "浅",
        "鈕": "纽",
        "間": "间",
        "義": "义",
        "輪": "轮",
        "線": "线",
        "懷": "怀",
        "錶": "表",
        "蓋": "盖",
        "損": "损",
        "數": "数",
        "視": "视",
        "專": "专",
        "尋": "寻",
        "燒": "烧",
        "兌": "兑",
        "夢": "梦",
        "絕": "绝",
        "複": "复",
        "場": "场",
        "灘": "滩",
        "藍": "蓝",
        "執": "执",
        "態": "态",
        "穩": "稳",
        "總": "总",
        "選": "选",
        "層": "层",
        "鎖": "锁",
        "鏈": "链",
        "並": "并",
        "緒": "绪",
        "歲": "岁",
        "臉": "脸",
        "溝": "沟",
        "雙": "双",
        "佈": "布",
        "繭": "茧",
        "紗": "纱",
        "廠": "厂",
        "跡": "迹",
        "黃": "黄",
        "張": "张",
        "變": "变",
        "覺": "觉",
        "歡": "欢",
        "註": "注",
        "產": "产",
        "實": "实",
        "華": "华",
        "強": "强",
        "細": "细",
        "夥": "伙",
        "擲": "掷",
        "謊": "谎",
        "迴": "回",
        "優": "优",
        "緻": "致",
        "襯": "衬",
        "銳": "锐",
        "勁": "劲",
        "憐": "怜",
        "謀": "谋",
        "獨": "独",
        "據": "据",
        "佔": "占",
        "錯": "错",
        "誤": "误",
        "塵": "尘",
        "戰": "战",
        "國": "国",
        "東": "东",
        "淚": "泪",
        "陰": "阴",
        "陽": "阳",
        "壓": "压",
        "獲": "获",
        "蘇": "苏",
        "區": "区",
        "響": "响",
        "襲": "袭",
        "顯": "显",
        "畫": "画",
        "寬": "宽",
        "寧": "宁",
        "塊": "块",
        "幫": "帮",
        "讓": "让",
        "驚": "惊",
        "體": "体",
        "開": "开",
        "動": "动",
    }
)

_TRADITIONAL_MARKERS = frozenset(chr(codepoint) for codepoint in _FALLBACK_T2S)

_SIMPLIFIED_PHRASE_REPLACEMENTS = (
    ("执著", "执着"),
    ("尖叫著", "尖叫着"),
    ("有著", "有着"),
    ("藏著", "藏着"),
    ("透著", "透着"),
    ("戴著", "戴着"),
    ("揣著", "揣着"),
    ("来著", "来着"),
    ("盼著", "盼着"),
    ("守护著", "守护着"),
    ("穿著", "穿着"),
    ("隐藏著", "隐藏着"),
    ("遍寻不著", "遍寻不着"),
    ("意味著", "意味着"),
    ("背负著", "背负着"),
    ("对著", "对着"),
    ("提醒著", "提醒着"),
    ("写著", "写着"),
)

_ASPECT_MARKER_RE = re.compile(
    r"(?<![土显顯卓昭原编編译譯名])著"
    r"(?=(?:的|地|了|一|二|三|四|五|六|七|八|九|十|个|些|种|条|枚|张|位|"
    r"缕|份|段|层|股|丝|颗|片|阵|声|眼|口|场|面|某|那|这|他|她|它|"
    r"自己|前|后|里|外|对|向|从|为|在|把|将|要|却|仍|也|都|和|与|或|"
    r"「|『|“|‘|，|。|；|、|：|！|？|$))"
)


def _language_key(language: Any) -> str:
    return str(language or "zh").strip().lower().replace("_", "-")


def is_simplified_chinese_language(language: Any) -> bool:
    """Return True when *language* should be treated as Simplified Chinese.

    Plain ``zh`` is intentionally mapped to Simplified Chinese. Traditional
    output must be requested explicitly with zh-Hant / zh-TW / zh-HK.
    """

    key = _language_key(language)
    return key in _ZH_SIMPLIFIED_ALIASES or (
        key.startswith("zh") and key not in _ZH_TRADITIONAL_ALIASES
    )


def is_traditional_chinese_language(language: Any) -> bool:
    """Return True for explicit Traditional Chinese language codes."""

    return _language_key(language) in _ZH_TRADITIONAL_ALIASES


def describe_language(language: Any) -> str:
    """Human-readable language label for prompts."""

    key = _language_key(language)
    if is_traditional_chinese_language(key):
        return "繁体中文（zh-Hant）"
    if is_simplified_chinese_language(key):
        return "简体中文（zh-Hans）"
    if key in _EN_ALIASES or key.startswith("en"):
        return "English"
    if key in {"jp", "jpn"} or key.startswith("ja"):
        return "Japanese"
    if key in {"kr", "kor"} or key.startswith("ko"):
        return "Korean"
    return str(language or "").strip() or "简体中文（zh-Hans）"


def language_output_rule(language: Any) -> str:
    """Return a hard prompt rule for the requested output language."""

    if is_traditional_chinese_language(language):
        return "输出语言：繁体中文（zh-Hant）。中文内容必须统一使用繁体中文，不要繁简混排。"
    if is_simplified_chinese_language(language):
        return (
            "输出语言：简体中文（zh-Hans）。中文内容必须统一使用简体字，"
            "禁止输出繁体字、异体字或繁简混排；专有名词和必要英文术语可保留原文。"
        )
    if _language_key(language).startswith("en"):
        return "Output language: English."
    key = _language_key(language)
    if key in {"jp", "jpn"} or key.startswith("ja"):
        return "Output language: Japanese."
    if key in {"kr", "kor"} or key.startswith("ko"):
        return "Output language: Korean."
    return ""


def normalize_language_tag(language: Any) -> str:
    """Return a stable BCP-47-ish language tag for metadata."""

    key = _language_key(language)
    if is_traditional_chinese_language(key):
        return "zh-Hant"
    if is_simplified_chinese_language(key):
        return "zh-Hans"
    if key.startswith("en"):
        return key or "en"
    if key in {"jp", "jpn"} or key.startswith("ja"):
        return "ja"
    if key in {"kr", "kor"} or key.startswith("ko"):
        return "ko"
    return str(language or "").strip() or "zh-Hans"


def contains_traditional_chinese(text: str) -> bool:
    """Heuristic Traditional-Chinese marker detection."""

    return any(ch in _TRADITIONAL_MARKERS for ch in str(text or ""))


@lru_cache(maxsize=2)
def _opencc_converter(config: str) -> Any | None:
    try:
        module: Any = importlib.import_module("opencc")
        return module.OpenCC(config)
    except Exception:
        return None


def normalize_text_for_language(text: str, language: Any) -> str:
    """Normalize generated text to the script implied by *language*.

    Uses OpenCC when available and falls back to a conservative common-character
    table otherwise.
    """

    value = str(text)
    if is_simplified_chinese_language(language):
        converter = _opencc_converter("t2s")
        if converter is not None:
            value = str(converter.convert(value))
        else:
            value = value.translate(_FALLBACK_T2S)
        for old, new in _SIMPLIFIED_PHRASE_REPLACEMENTS:
            value = value.replace(old, new)
        return _ASPECT_MARKER_RE.sub("着", value)
    if is_traditional_chinese_language(language):
        converter = _opencc_converter("s2t")
        if converter is not None:
            return str(converter.convert(value))
    return value


def normalize_payload_for_language(payload: Any, language: Any) -> Any:
    """Recursively normalize strings in a JSON-like payload."""

    if isinstance(payload, str):
        return normalize_text_for_language(payload, language)
    if isinstance(payload, list):
        return [normalize_payload_for_language(item, language) for item in payload]
    if isinstance(payload, tuple):
        return tuple(normalize_payload_for_language(item, language) for item in payload)
    if isinstance(payload, dict):
        return {
            key: normalize_payload_for_language(value, language)
            for key, value in payload.items()
        }
    return payload
