"""Executable builtin catalog for AI-writing-pattern detection.

This module is the single source of truth shared by the novel prescreen and the
persisted humanize library.  Keeping executable expressions here prevents the
library metadata and the scanner implementation from silently drifting apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

HumanizeSeverity = Literal["critical", "high", "medium", "low"]


@dataclass(frozen=True)
class HumanizeRule:
    pattern_id: str
    pattern_name: str
    category: str
    severity: HumanizeSeverity
    regex: re.Pattern[str]
    confidence: float
    actionable: bool = False

    def detection_config(self) -> dict[str, object]:
        """Return the portable library representation of this rule."""

        return {
            "patterns": [self.regex.pattern],
            "flags": self.regex.flags,
            "confidence": self.confidence,
            "actionable": self.actionable,
        }


HUMANIZE_RULES: tuple[HumanizeRule, ...] = (
    HumanizeRule(
        "significance_inflation",
        "显著性通胀",
        "叙事轻重",
        "high",
        re.compile(r"标志着|具有里程碑意义|划时代|开创性|关键时刻|分水岭|前所未有"),
        0.9,
        True,
    ),
    HumanizeRule(
        "promotional_language",
        "宣传腔",
        "宣传式描写",
        "medium",
        re.compile(r"坐落于|令人叹为观止|迷人的|充满活力的|壮丽的|美不胜收|雄伟的|自然之美"),
        0.82,
    ),
    HumanizeRule(
        "ai_vocabulary",
        "AI 高频词汇",
        "AI 词汇",
        "medium",
        re.compile(r"此外|至关重要|深入探讨|彰显|凸显|复杂性|持久的|格局|珍贵的|相互作用"),
        0.84,
    ),
    HumanizeRule(
        "negative_parallelism",
        "否定式并列",
        "模板句式",
        "high",
        re.compile(
            r"不(?:是|仅仅|只|只不过)[^。！？\n]{0,30}(?:而是|更是|而且|恰恰是)[^。！？\n]{1,40}"
        ),
        0.9,
        True,
    ),
    HumanizeRule(
        "rule_of_three",
        "三项列举",
        "模板句式",
        "medium",
        re.compile(
            r"[\u4e00-\u9fffA-Za-z0-9]{1,12}、"
            r"[\u4e00-\u9fffA-Za-z0-9]{1,12}(?:、|和|与|及)"
            r"[\u4e00-\u9fffA-Za-z0-9]{1,12}"
        ),
        0.80,
    ),
    HumanizeRule(
        "false_ranges",
        "假范围",
        "模板句式",
        "medium",
        re.compile(r"(?:从|大到|上至)[^。！？\n]{1,24}(?:到|小到|下至)[^。！？\n]{1,24}"),
        0.78,
    ),
    HumanizeRule(
        "filler_phrases",
        "填充短语",
        "元语言",
        "high",
        re.compile(r"值得注意的是|不难发现|基于以上分析|综上所述|换句话说|不可否认的是"),
        0.94,
        True,
    ),
    HumanizeRule(
        "generic_conclusions",
        "万能结尾",
        "模板结尾",
        "high",
        re.compile(r"未来充满希望|新的篇章即将开启|一切才刚刚开始|前路漫漫|故事还在继续"),
        0.9,
        True,
    ),
    HumanizeRule(
        "hollow_aspect_marker",
        "空洞进行态",
        "动作虚化",
        "medium",
        re.compile(r"凝望着|沉思着|注视着|思考着|感受着|回忆着|等待着"),
        0.82,
    ),
    HumanizeRule(
        "em_dash_overuse", "破折号滥用", "标点习惯", "high", re.compile(r"——"), 0.92, True
    ),
    HumanizeRule(
        "quotation_mark_misuse",
        "引号强调",
        "标点习惯",
        "medium",
        re.compile(r'(?:所谓的|被称作)?["“「][^"”」\n]{1,16}["”」]'),
        0.74,
    ),
    HumanizeRule(
        "passive_subjectless",
        "被动/无主语",
        "句法虚化",
        "high",
        re.compile(r"被[^。！？\n]{1,16}的是|需要注意的是|被指出的是|被发现的是"),
        0.88,
        True,
    ),
    HumanizeRule(
        "persuasive_authority",
        "说教权威腔",
        "说教腔",
        "high",
        re.compile(r"从根本上说|核心问题是|归根结底|我们必须承认|事实上|真正的问题是|本质上"),
        0.9,
        True,
    ),
    HumanizeRule(
        "vague_attribution",
        "模糊归因",
        "证据空泛",
        "medium",
        re.compile(r"专家认为|业内人士指出|观察者指出|一些批评者认为|行业报告显示|多个来源显示"),
        0.82,
    ),
    HumanizeRule(
        "challenge_future_template",
        "挑战/未来模板",
        "结构模板",
        "medium",
        re.compile(r"尽管存在这些挑战|面临若干挑战|未来展望|挑战与机遇|继续蓬勃发展"),
        0.86,
    ),
    HumanizeRule(
        "collaborative_artifact",
        "协作对话残留",
        "聊天残留",
        "critical",
        re.compile(r"希望这对[你您]有帮助|当然！|一定！|请告诉我|如果[你您]想让我"),
        0.96,
        True,
    ),
    HumanizeRule(
        "knowledge_cutoff_disclaimer",
        "知识截止声明",
        "聊天残留",
        "critical",
        re.compile(r"截至\s*\d{4}|根据我最后的训练|基于可用信息|虽然具体细节有限|我无法访问实时信息"),
        0.96,
        True,
    ),
    HumanizeRule(
        "sycophantic_tone",
        "谄媚语气",
        "聊天残留",
        "high",
        re.compile(r"好问题！|您说得完全正确|这是一个很好的观点|非常棒的问题"),
        0.94,
        True,
    ),
    HumanizeRule(
        "markdown_formatting_residue",
        "Markdown/表情残留",
        "格式残留",
        "high",
        re.compile(r"\*\*[^*\n]{1,40}\*\*|^[ \t]*[-*+]\s+|[✅🚀💡✨🔥📌]", re.MULTILINE),
        0.9,
        True,
    ),
    HumanizeRule(
        "outline_heading_voice",
        "结构性标题/提纲腔",
        "格式残留",
        "medium",
        re.compile(
            r"^\s{0,3}#{1,6}\s+|^\s*(?:一|二|三|四|五|六|七|八|九|十)、|^\s*\d+[.)、]",
            re.MULTILINE,
        ),
        0.84,
    ),
    HumanizeRule(
        "diff_anchored_writing",
        "改动叙述腔",
        "元语言",
        "medium",
        re.compile(r"本次(?:更新|改动|修改)|新增了|替代了原先|相较于之前|进行了优化"),
        0.82,
    ),
    HumanizeRule(
        "weak_verb_stacking",
        "弱动词堆叠",
        "叙事轻重",
        "high",
        re.compile(r"(?:感到|觉得|意识到|似乎|仿佛|不禁|不由得|情不自禁)[^。！？\n]{0,40}"),
        0.85,
        True,
    ),
    HumanizeRule(
        "tautology_marker",
        "抽象虚指三连",
        "模板句式",
        "high",
        re.compile(
            r"某种[\u4e00-\u9fff]{2,8}.{0,30}某种[\u4e00-\u9fff]{2,8}.{0,30}某种[\u4e00-\u9fff]{2,8}",
            re.DOTALL,
        ),
        0.92,
        True,
    ),
    HumanizeRule(
        "binary_judgment_closing",
        "二元判断收束",
        "模板结尾",
        "high",
        re.compile(
            r"[，,。\s]{0,2}(?:不是|并不是)[^。！？\n]{2,20}[，。\s]+(?:而是|是|正是)[^。！？\n]{2,20}[。！？]"
        ),
        0.88,
        True,
    ),
    HumanizeRule(
        "pronoun_disappearance_run",
        "主语弱化句式",
        "叙事轻重",
        "medium",
        re.compile(
            r"(?:她没有|他没|她不)[^。！？\n]{0,15}[，,。][^。！？\n]{0,15}"
            r"(?:她没有|他没|她不)[^。！？\n]{0,15}[，,。]"
            r"[^。！？\n]{0,15}(?:她没有|他没|她不)"
        ),
        0.78,
    ),
    HumanizeRule(
        "precise_timestamp_overuse",
        "精确时长堆叠",
        "AI 习惯",
        "low",
        re.compile(
            r"(?:\d{1,2}\s*分\s*\d{1,2}\s*秒|\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2}"
            r"|[一二三四五六七八九十百零〇]{1,3}\s*分\s*[一二三四五六七八九十百零〇两]{1,4}\s*秒)"
        ),
        0.65,
    ),
)

HUMANIZE_RULE_MAP: dict[str, HumanizeRule] = {rule.pattern_id: rule for rule in HUMANIZE_RULES}
