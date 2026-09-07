"""MockAdapter — deterministic adapter for testing without real API calls."""

from __future__ import annotations

import json
import re
import time
from typing import Any, cast

from novel_forge.core.constants import TaskType
from novel_forge.core.parsing.token_utils import count_message_tokens, count_text_tokens
from novel_forge.gateway.base import ProviderAdapter
from novel_forge.gateway.pricing import estimate_cost
from novel_forge.gateway.structured_output import (
    StructuredOutputDialect,
    build_structured_output_request_plan,
)
from novel_forge.gateway.types import ModelRequest, ModelResponse

# ── Pre-baked mock responses per task type ─────────────────────

_MOCK_BEATS = json.dumps(
    {
        "beats": [
            {
                "sequence": 1,
                "summary": "主角在雨中的小镇醒来，发现自己失去了所有记忆。",
                "tension_level": 3,
                "characters_involved": ["林远"],
                "setting": "雾霭小镇",
            },
            {
                "sequence": 2,
                "summary": "一位神秘老人交给主角一封未拆的信，声称这是他自己写的。",
                "tension_level": 5,
                "characters_involved": ["林远", "老守夜人"],
                "setting": "钟楼",
            },
            {
                "sequence": 3,
                "summary": "主角追寻信中线索，在废弃图书馆发现关于时间裂缝的记载，决定踏入裂缝寻找真相。",
                "tension_level": 8,
                "characters_involved": ["林远"],
                "setting": "废弃图书馆",
            },
        ],
        "total_estimated_words": 900,
    },
    ensure_ascii=False,
)

_MOCK_DRAFT = (
    "雨丝如织，无声地落在青石板路上。林远睁开眼睛的那一刻，世界是陌生的。\n\n"
    "他不记得自己是谁，不记得这座被薄雾笼罩的小镇叫什么名字。唯一确定的，是胸口那枚冰凉的铜质怀表，"
    "指针停在午夜十二点，一动不动。\n\n"
    "钟楼下，一位佝偻的老人拦住了他的去路。老人浑浊的眼睛里闪过一丝精光，从褪色的大衣内袋里掏出一封信。\n\n"
    "\u201c这是你自己写的。\u201d老人的声音沙哑而笃定，\u201c在你忘记一切之前。\u201d\n\n"
    "信封上没有署名，只有一行墨迹未干的字：去图书馆，找到裂缝。\n\n"
    "废弃图书馆坐落在小镇尽头，藤蔓攀满了半塌的穹顶。林远推开沉重的木门，"
    "灰尘在昏暗的光线中飞舞。书架间散落着泛黄的纸页，而在最深处的墙壁上，"
    "一道几乎不可见的裂痕正微微发光。\n\n"
    "他伸出手，指尖触到裂缝的瞬间，时间开始倒流。\n\n"
    "眼前的景象如同旧胶片倒放，灰尘逆着光柱飞升，书页自动翻合，"
    "门在身后无声关闭。林远感到一阵剧烈的眩晕，双膝跪倒在冰凉的石板上。"
    "当他再次抬头，图书馆变了模样——穹顶完整无缺，书架排列齐整，"
    "空气中弥漫着新鲜墨水和松香的气味。\n\n"
    "一个穿学生制服的少年正站在窗前，手里捧着一本厚重的皮面笔记。"
    "少年转过头来，林远的心猛地一缩——那张脸，和他在水洼倒影中看到的自己一模一样。\n\n"
    "\u201c你终于来了。\u201d少年轻声说，把笔记递了过来，\u201c第七十二页，你会需要的。\u201d\n\n"
    "林远伸手接过笔记，翻开第七十二页。那上面画着一张地图，标注了小镇十二个不同的时间节点，"
    "每一个节点旁边都用红墨水圈出了一个名字。他认出了其中三个：钟楼老人的名字赫然在列，"
    "而排在第一位的，是他自己。\n\n"
    "窗外传来钟声，沉闷而悠远，连续敲了十二下。少年的身影开始变得透明，"
    "如同晨雾被阳光穿透。林远想要抓住他的手臂，指尖却只触到了冰冷的空气。\n\n"
    "\u201c记住，\u201d少年的声音从远处飘来，\u201c每一次选择都会改变裂缝的形状。"
    "而你只剩三次机会。\u201d\n\n"
    "图书馆的灯光全部熄灭。黑暗中，林远低头看向怀表，指针开始缓缓转动——"
    "不是顺时针，而是逆时针，一格一格地，吞噬着他所剩无几的时间。"
    "他攥紧笔记本，深吸一口气，在黑暗中摸索着推开了图书馆的侧门，"
    "踏入那条通往钟楼的青石小巷。夜风裹挟着远处河面的潮气扑面而来，"
    "让他打了一个寒颤，却也让混沌的头脑清醒了几分。"
    "巷口的路灯忽明忽暗，和怀表的逆行节拍诡异地同步着。"
)

_MOCK_EDIT = (
    "雨丝如织，悄然落在青石板路上，汇成细流蜿蜒而去。林远睁开眼的刹那，世界对他而言是全然陌生的。\n\n"
    "他不记得自己的名字，不记得这座被晨雾笼罩的小镇有过怎样的故事。唯一能确认的，"
    "是胸口那枚冰凉的铜质怀表——指针凝固在午夜十二点整，仿佛时间本身也在此处断裂。\n\n"
    "钟楼的影子斜斜地投在他脚前。一位佝偻的老人从阴影中缓步走出，浑浊的双眼里倏然掠过一丝锐利的光。"
    "他从褪色的呢大衣内袋掏出一封泛黄的信。\n\n"
    "\u201c这是你自己写的。\u201d老人的嗓音沙哑却不容置疑，\u201c在你选择遗忘之前。\u201d\n\n"
    "信封上没有落款，只有一行仿佛刚写就的墨字：去图书馆，找到裂缝。\n\n"
    "废弃图书馆伫立在小镇尽头，常春藤攀满了半坍的穹顶，将最后一缕天光也吞没。"
    "林远推开厚重的橡木门，扬起的灰尘在斜射的光柱中缓缓起舞。"
    "书架间散落着泛黄的残页，而在最深处的墙壁上，一道纤如发丝的裂痕正发出幽微的光。\n\n"
    "他屏住呼吸，伸出手。指尖触及裂缝的瞬间，怀表的指针猛然逆转。\n\n"
    "世界在他眼前急速倒退。灰尘循着光柱上升，残页飞回书架，坍塌的穹顶一块块拼合复原。"
    "林远的头疼欲裂，他单膝跪地，掌心撑住冰凉的石砖地面，"
    "等眩晕退去后才发现自己身处一座完好如新的图书馆——空气里漂浮着松节油与新裁纸张的淡香。\n\n"
    "一名身穿藏蓝色校服的少年立于高窗前，逆光中轮廓模糊，手中握着一本厚实的牛皮笔记。"
    "少年闻声回头，一张与镜中自己别无二致的面孔映入眼帘，令林远浑身僵住。\n\n"
    "\u201c你终于来了。\u201d少年的语气平静得近乎残忍，将笔记稳稳递出，\u201c翻到第七十二页。\u201d\n\n"
    "林远接过笔记，粗糙的封皮摩擦掌心。第七十二页上绘着小镇的俯瞰图，"
    "十二个时间节点以红色墨水标注，每个节点旁写着一个名字。"
    "他一眼认出了钟楼老人，而位列第一的名字让他血液发凉——正是他自己。\n\n"
    "远处的钟声骤然响起，低沉的共鸣穿透胸腔，一声接一声地敲了十二下。"
    "少年的身体开始透明，像水墨画被雨浸湿，轮廓一层层淡去。"
    "林远伸手想抓住他，却只握到了穿堂而过的冷风。\n\n"
    "\u201c记住，\u201d少年的声音仿佛从时间的夹缝中渗出，\u201c每一次抉择都改变裂缝的走向，"
    "而你仅剩三次机会。\u201d\n\n"
    "所有灯火同时熄灭，黑暗如潮水般涌来。林远低头凝视怀表——秒针终于动了，"
    "却是逆时针旋转，缓慢而坚定地，吞没着他仅存的时间。"
    "他将笔记本紧紧夹在臂弯，推开侧门走入夜色的小巷，"
    "耳畔回响着少年最后的告诫。"
)

_MOCK_EVAL = json.dumps(
    {
        "scores": [
            {"dimension": "consistency", "score": 7.5, "comment": "设定前后一致，因果链完整"},
            {"dimension": "continuity", "score": 8.0, "comment": "场景衔接自然，无断裂"},
            {"dimension": "character", "score": 7.5, "comment": "人物性格稳定，对话得体"},
            {"dimension": "style", "score": 8.0, "comment": "文笔流畅，意象生动"},
            {"dimension": "engagement", "score": 7.0, "comment": "悬念设置有效，感官描写丰富"},
            {"dimension": "pacing", "score": 7.5, "comment": "节奏张弛有度"},
        ],
        "overall_score": 7.6,
        "passed": True,
        "threshold": 6.0,
        "summary": "整体质量良好，叙事连贯，风格稳定。建议在第二节拍处加强情感张力。",
        "repair_suggestions": [],
    },
    ensure_ascii=False,
)

_MOCK_WORLD_RULE_BOOK = {
    "version": "1",
    "description": "MockAdapter 用于端到端测试的结构化世界规则。",
    "rules": [
        {
            "rule_id": "wr_mock_01",
            "content": "时间裂缝只在小镇当地时间的午夜显现。",
            "category": "time",
            "severity": "hard",
            "always_on": True,
            "forbidden_behavior": ["非午夜直接进入裂缝"],
        },
        {
            "rule_id": "wr_mock_02",
            "content": "进入裂缝会丢失一段可识别的个人记忆。",
            "category": "cost",
            "severity": "hard",
            "always_on": True,
            "cost_or_consequence": ["记忆缺失必须在后续行动中留下证据"],
        },
        {
            "rule_id": "wr_mock_03",
            "content": "铜质怀表是唯一能稳定感知裂缝方位的时间锚点。",
            "category": "ability_tech",
            "severity": "hard",
            "always_on": True,
            "forbidden_behavior": ["其他普通物品无代价地取代怀表"],
        },
        {
            "rule_id": "wr_mock_04",
            "content": "裂缝内的时间顺序可与外界不同，但因果结果不能无故消失。",
            "category": "causality",
            "severity": "hard",
            "always_on": False,
            "applicability_tags": ["rift_scene"],
            "forbidden_behavior": ["删除已发生行动的所有后果"],
        },
        {
            "rule_id": "wr_mock_05",
            "content": "小镇居民使用循环历法记录裂缝出现的周期。",
            "category": "culture",
            "severity": "soft",
            "always_on": False,
            "applicability_tags": ["town", "calendar"],
        },
        {
            "rule_id": "wr_mock_06",
            "content": "普通居民只知道异常循环，不默认知道裂缝真相。",
            "category": "knowledge",
            "severity": "soft",
            "always_on": False,
            "applicability_tags": ["resident_pov"],
        },
        {
            "rule_id": "wr_mock_07",
            "content": "裂缝造成的伤势与物件磨损会保留可观察痕迹。",
            "category": "physics",
            "severity": "soft",
            "always_on": False,
            "applicability_tags": ["aftermath"],
        },
        {
            "rule_id": "wr_mock_08",
            "content": "人物不能仅凭直觉确认被改写的记忆，必须获得证据。",
            "category": "knowledge",
            "severity": "soft",
            "always_on": False,
            "applicability_tags": ["memory_reveal"],
        },
        {
            "rule_id": "wr_mock_09",
            "content": "同一时间节点的重复干预会增加裂缝失控风险。",
            "category": "cost",
            "severity": "soft",
            "always_on": False,
            "applicability_tags": ["repeat_intervention"],
        },
        {
            "rule_id": "wr_mock_10",
            "content": "离开小镇边界不会自动恢复已丢失的记忆。",
            "category": "geography",
            "severity": "soft",
            "always_on": False,
            "applicability_tags": ["town_boundary"],
        },
    ],
}


_MOCK_STORY_BIBLE = json.dumps(
    {
        "story_bible": {
            "title": "时间裂缝",
            "premise": "在一个时间流动异常的小镇，失忆的年轻人必须找到时间裂缝的真相，以恢复被扭曲的现实。",
            "era": "当代，但小镇时间停滞",
            "geography": "雾霭小镇——被群山环绕的封闭小镇",
            "culture": "居民习惯了时间异常，形成了独特的\u201c循环历法\u201d",
            "magic_or_tech": "时间裂缝：自然现象，可被特定物品（铜质怀表）感知",
            "rules": ["时间裂缝只在午夜出现", "进入裂缝者会失去部分记忆", "怀表是唯一的时间锚点"],
            "world_rule_book": _MOCK_WORLD_RULE_BOOK,
            "tone": "悬疑、诗意、略带忧伤",
            "themes": ["记忆与身份", "时间的本质", "选择与代价"],
        },
    },
    ensure_ascii=False,
)

_MOCK_CHARACTER_BIBLE = json.dumps(
    {
        "character_bible": {
            "characters": [
                {
                    "name": "林远",
                    "role": "protagonist",
                    "age": "25",
                    "appearance": "清瘦，黑发，总是穿一件旧风衣",
                    "personality": "沉静、执着、偶尔流露出不属于这个年纪的疲惫",
                    "backstory": "原为物理学研究生，因一次实验意外坠入时间裂缝",
                    "arc": "从迷失自我到接受真相，最终选择牺牲部分记忆来修复裂缝",
                    "relationships": {"老守夜人": "引路人/谜题出题者"},
                    "voice": "语速偏慢，习惯先描述可验证的现象，在焦虑时句子会变短。",
                },
                {
                    "name": "老守夜人",
                    "role": "supporting",
                    "age": "70+",
                    "appearance": "佝偻、褪色呢大衣、浑浊但偶尔锐利的目光",
                    "personality": "神秘、少言、似乎知晓一切",
                    "backstory": "小镇最初的时间裂缝见证者",
                    "arc": "从隐匿真相到最终揭示自己就是林远的未来",
                    "relationships": {"林远": "未来的自己/守护者"},
                    "voice": "声音沙哑、少言，总用含混的短句提示时间和代价。",
                },
            ]
        },
    },
    ensure_ascii=False,
)

_MOCK_STORY_PAYLOAD = json.loads(_MOCK_STORY_BIBLE)["story_bible"]
_MOCK_STORY_CORE_PREMISE = json.dumps(
    {
        "story_core": {
            "title": _MOCK_STORY_PAYLOAD["title"],
            "premise": _MOCK_STORY_PAYLOAD["premise"],
            "tone": _MOCK_STORY_PAYLOAD["tone"],
        }
    },
    ensure_ascii=False,
)
_MOCK_STORY_WORLD_RULES = json.dumps(
    {
        "world_rules": {
            "era": _MOCK_STORY_PAYLOAD["era"],
            "geography": _MOCK_STORY_PAYLOAD["geography"],
            "culture": _MOCK_STORY_PAYLOAD["culture"],
            "magic_or_tech": _MOCK_STORY_PAYLOAD["magic_or_tech"],
            "rules": _MOCK_STORY_PAYLOAD["rules"],
            "world_rule_book": _MOCK_WORLD_RULE_BOOK,
        }
    },
    ensure_ascii=False,
)
_MOCK_STORY_CONTINUITY_RULES = json.dumps(
    {"continuity_rules": {"time_convention": "小镇使用循环历法，午夜是裂缝活跃边界。"}},
    ensure_ascii=False,
)
_MOCK_STORY_THEMES_AND_SYMBOLS = json.dumps(
    {
        "themes_and_symbols": {
            "themes": _MOCK_STORY_PAYLOAD["themes"],
            "banned_intent_rules": [],
            "notes": "",
        }
    },
    ensure_ascii=False,
)

_MOCK_CHARACTER_PAYLOAD = json.loads(_MOCK_CHARACTER_BIBLE)["character_bible"]
_MOCK_CHARACTER_ROSTER = json.dumps(
    {
        "character_roster": [
            {
                "name": item["name"],
                "role": item.get("role", "supporting"),
                "age": item.get("age", ""),
                "gender": "男",
                "status": "active",
                "time_layer": "default",
                "function": item.get("arc", ""),
            }
            for item in _MOCK_CHARACTER_PAYLOAD["characters"]
        ]
    },
    ensure_ascii=False,
)
_MOCK_CHARACTER_PROFILE_BATCH = json.dumps(
    {"character_profiles": _MOCK_CHARACTER_PAYLOAD["characters"]},
    ensure_ascii=False,
)
_MOCK_CHARACTER_RELATIONSHIP_MATRIX = json.dumps(
    {
        "relationship_matrix": [
            {
                "character_a": "林远",
                "character_b": "老守夜人",
                "relation_type": "mentor_student",
                "identity_link_type": "",
                "description": "引路人与被引导者，也隐藏未来身份谜题。",
                "confidence": 0.9,
            }
        ]
    },
    ensure_ascii=False,
)
_MOCK_CHARACTER_ARC_PLAN = json.dumps(
    {
        "character_arcs": [
            {"name": item["name"], "arc": item.get("arc", "")}
            for item in _MOCK_CHARACTER_PAYLOAD["characters"]
        ]
    },
    ensure_ascii=False,
)

_MOCK_OUTLINE = json.dumps(
    {
        "synopsis": "失忆青年林远在时间异常的雾霭小镇中追寻真相，经历三次时间回溯，最终发现守夜老人就是未来的自己。",
        "volume_mode": False,
        "volumes": [],
        "narrative_phases": [
            {
                "phase_name": "开篇·迷雾降临",
                "chapter_start": 1,
                "chapter_end": 1,
                "description": "建立世界观，引入主角和核心悬念",
                "key_events": ["失忆醒来", "遇见守夜人", "获得信件线索"],
                "tension_level": "低→中",
            },
            {
                "phase_name": "发展·裂缝探秘",
                "chapter_start": 2,
                "chapter_end": 2,
                "description": "深入时间裂缝，揭示世界规则",
                "key_events": ["探索图书馆", "进入裂缝", "第一次回溯体验"],
                "tension_level": "中→高",
            },
            {
                "phase_name": "高潮·真相与抉择",
                "chapter_start": 3,
                "chapter_end": 3,
                "description": "真相揭露与最终选择",
                "key_events": ["发现老人身份", "面对选择", "修复裂缝的代价"],
                "tension_level": "高→释放",
            },
        ],
        "key_turning_points": [
            {"chapter_number": 2, "description": "林远第一次进入时间裂缝，发现回溯的可能"},
            {"chapter_number": 3, "description": "林远发现守夜老人就是未来的自己"},
        ],
        "character_arcs": [
            {
                "character": "林远",
                "arc_summary": "从失忆迷茫到直面真相、承担代价",
                "milestones": [
                    {"chapter_start": 1, "chapter_end": 1, "description": "失忆状态，被动探索"},
                    {
                        "chapter_start": 2,
                        "chapter_end": 2,
                        "description": "主动深入裂缝，开始追寻真相",
                    },
                    {
                        "chapter_start": 3,
                        "chapter_end": 3,
                        "description": "面对身份真相，做出牺牲选择",
                    },
                ],
            }
        ],
        "subplot_plan": [
            {
                "name": "怀表之谜",
                "description": "围绕怀表为何能感应裂缝、为何停在午夜展开的支线。",
                "involved_chapters": [1, 2, 3],
                "chapter_events": [
                    {
                        "chapter_number": 1,
                        "event": "林远确认怀表与裂缝存在同步反应。",
                        "weave_notes": "把个人失忆困境与世界规则连接起来。",
                        "depends_on": [],
                    },
                    {
                        "chapter_number": 2,
                        "event": "林远发现怀表其实记录了多次回溯残痕。",
                        "weave_notes": "为身份真相铺垫。",
                        "depends_on": ["怀表之谜:1"],
                    },
                    {
                        "chapter_number": 3,
                        "event": "怀表揭示林远曾主动参与裂缝实验。",
                        "weave_notes": "直接回馈主线真相揭露。",
                        "depends_on": ["怀表之谜:2"],
                    },
                ],
                "weave_links": [
                    {
                        "source_type": "main_plot",
                        "source_ref": "失忆醒来并收到信件",
                        "target_subplot": "怀表之谜",
                        "trigger_chapter": 1,
                        "link_type": "trigger_start",
                        "description": "主线悬念触发怀表调查。",
                    },
                    {
                        "source_type": "subplot",
                        "source_ref": "怀表之谜",
                        "target_subplot": "怀表之谜",
                        "trigger_chapter": 3,
                        "link_type": "reveal_key",
                        "description": "怀表线索反哺主线真相。",
                    },
                ],
                "priority": "primary",
                "resolution_chapter": 3,
                "resolution_target": "main_turning_point:2",
                "resolution_type": "reveal",
            },
            {
                "name": "守夜人身份",
                "description": "追查老守夜人为何熟知裂缝规则以及他与林远的关系。",
                "involved_chapters": [1, 2, 3],
                "chapter_events": [
                    {
                        "chapter_number": 1,
                        "event": "守夜人以含混方式引导林远前往图书馆。",
                        "weave_notes": "建立人物间的不对等信息差。",
                        "depends_on": [],
                    },
                    {
                        "chapter_number": 2,
                        "event": "守夜人留下的痕迹表明他提前布置过回溯路径。",
                        "weave_notes": "把人物谜团推进为身份谜团。",
                        "depends_on": ["守夜人身份:1"],
                    },
                    {
                        "chapter_number": 3,
                        "event": "林远确认守夜人就是未来的自己。",
                        "weave_notes": "人物支线与主线高潮合流。",
                        "depends_on": ["守夜人身份:2"],
                    },
                ],
                "weave_links": [
                    {
                        "source_type": "main_plot",
                        "source_ref": "老守夜人主动介入林远的调查",
                        "target_subplot": "守夜人身份",
                        "trigger_chapter": 1,
                        "link_type": "trigger_start",
                        "description": "主线中的异常引导启动守夜人身份追查。",
                    },
                    {
                        "source_type": "turning_point",
                        "source_ref": "林远第一次进入时间裂缝",
                        "target_subplot": "守夜人身份",
                        "trigger_chapter": 2,
                        "link_type": "trigger_turn",
                        "description": "裂缝体验把守夜人身份从疑点升级为核心谜团。",
                    },
                    {
                        "source_type": "subplot",
                        "source_ref": "守夜人身份",
                        "target_subplot": "怀表之谜",
                        "trigger_chapter": 3,
                        "link_type": "feed_main",
                        "description": "身份揭露与怀表真相共同收束主线。",
                    },
                ],
                "priority": "normal",
                "resolution_chapter": 3,
                "resolution_target": "character_fate:老守夜人",
                "resolution_type": "resolve",
            },
        ],
        "suspense_schedule": [
            {
                "suspense_id": "watch-midnight",
                "suspense_type": "mystery",
                "introduce_chapter": 1,
                "resolve_chapter": 3,
                "description": "怀表为何停在午夜十二点，以及它与时间裂缝的关系。",
                "urgency_level": "high",
                "related_subplot": "怀表之谜",
                "strand_affinity": {"quest": 0.5, "fire": 0.1, "constellation": 0.4},
            },
            {
                "suspense_id": "watchman-identity",
                "suspense_type": "emotion",
                "introduce_chapter": 1,
                "resolve_chapter": 3,
                "description": "老守夜人为什么像是提前看过林远的每一次选择。",
                "urgency_level": "normal",
                "related_subplot": "守夜人身份",
                "strand_affinity": {"quest": 0.2, "fire": 0.3, "constellation": 0.5},
            },
        ],
        "ending_strategy": "余韵式收束：林远修复裂缝但失去所有记忆，守夜老人的身份悖论被解开。",
        "emotional_arcs": [],
        "causal_chains": [],
        "subplot_collisions": [],
        "subversion_points": [],
        "chapter_rhythm_curve": [
            {
                "chapter_number": 1,
                "target_pacing": 3,
                "target_tension": 3,
                "beat_pattern": "开局悬念-行动确认-章尾钩子",
                "description": "建立时间裂缝悬念并完成主角入局。",
            },
            {
                "chapter_number": 2,
                "target_pacing": 4,
                "target_tension": 4,
                "beat_pattern": "探索-冲击-短暂回落",
                "description": "推进图书馆探索并揭示裂缝规则。",
            },
            {
                "chapter_number": 3,
                "target_pacing": 4,
                "target_tension": 5,
                "beat_pattern": "逼近真相-身份揭示-代价选择",
                "description": "完成主线身份真相与最终选择。",
            },
        ],
    },
    ensure_ascii=False,
)

_MOCK_CHAPTER_PLAN = json.dumps(
    {
        "scene_intents": [
            {
                "scene_id": "scene_01",
                "summary": "林远带着上一章遗留的不安走进废弃图书馆外侧，先确认信件与怀表是否仍在响应。",
                "purpose": "完成开场桥接，回应上一章交接点",
                "conflict": "他必须在恐惧和求真之间做选择",
                "required_characters": ["林远"],
                "entry_state_refs": ["承接上一章的怀表线索与不安情绪"],
                "required_outcome": "明确进入图书馆的动机",
                "exit_target_state": "林远决定深入裂缝",
                "location": "废弃图书馆外廊",
                "time_marker": "第一天，黄昏",
            },
            {
                "scene_id": "scene_02",
                "summary": "林远进入图书馆，与老守夜人留下的痕迹形成间接对话，意识到自己被引导而来。",
                "purpose": "推进主线并强化谜团",
                "conflict": "林远想掌控局面，但线索明显超出他的认知",
                "required_characters": ["林远", "老守夜人"],
                "entry_state_refs": ["怀表与信件同时指向裂缝"],
                "required_outcome": "林远获得新的可验证线索：确认时间裂缝的真实存在。",
                "exit_target_state": "林远准备触碰裂缝",
                "location": "废弃图书馆深处",
                "time_marker": "第一天，夜色初降",
            },
            {
                "scene_id": "scene_03",
                "summary": "裂缝回应林远的触碰，让他第一次短暂回溯并看见不属于此刻的自己。",
                "purpose": "制造第一次明确的世界规则冲击",
                "conflict": "真相靠近，但代价是记忆与稳定感继续流失",
                "required_characters": ["林远"],
                "entry_state_refs": ["林远已决定触碰裂缝"],
                "required_outcome": "林远获得新的可验证线索后仍留下一个需要下一章处理的证据缺口。",
                "exit_target_state": "林远被迫继续追索裂缝真相，形成可承接的离章状态。",
                "location": "裂缝入口",
                "time_marker": "第一天，深夜",
            },
        ],
        "world_rule_applications": [
            {
                "rule_id": "wr_mock_01",
                "scene_id": "scene_03",
                "usage": "裂缝只在深夜接近午夜时显现。",
                "expected_evidence": "正文给出深夜时间锚点后裂缝才回应。",
                "forbidden_boundary": "不得在黄昏场景直接打开裂缝。",
            },
            {
                "rule_id": "wr_mock_02",
                "scene_id": "scene_03",
                "usage": "林远触碰裂缝后出现可追踪的记忆与稳定感流失。",
                "expected_evidence": "正文出现具体记忆缺口或行动后果。",
                "forbidden_boundary": "不得无代价完成回溯。",
            },
            {
                "rule_id": "wr_mock_03",
                "scene_id": "scene_01",
                "usage": "怀表作为裂缝方位的唯一稳定感知锚点。",
                "expected_evidence": "怀表的反应直接推动林远前往图书馆。",
                "forbidden_boundary": "不得让其他普通物品取代怀表定位。",
            },
            {
                "rule_id": "wr_mock_04",
                "scene_id": "scene_03",
                "usage": "短暂回溯保留林远触碰裂缝所造成的因果后果。",
                "expected_evidence": "回溯结束后人物状态与线索发生可观察变化。",
                "forbidden_boundary": "不得用回溯抹除本章所有行动结果。",
            },
        ],
        "opening_contract": "本章开头必须直接承接上一章信件与怀表的交接点，交代林远为何立刻前往图书馆。",
        "closing_contract": "章末必须让裂缝给出清晰后果，形成可承接的离章状态，方便下一章直接承接探索。",
        "required_state_transitions": [
            "林远从被动困惑转为主动追索真相",
            "林远对老守夜人的信任与戒备同时上升",
        ],
        "required_literals": [],
        "chapter_type": "discovery",
        "emotional_arc": "困惑 → 压抑不安 → 被真相吸引 → 带着恐惧的决心",
        "relationship_evolution": [
            "林远对老守夜人的信任与戒备同时上升",
            "林远与裂缝背后布局者形成尚未明说的追索关系",
        ],
        "forbidden_elements": ["不要复用上一章结尾的信件递交场面"],
        "forbidden_elements_soft": ["减少抽象恐惧描写，优先用行动和环境压力呈现"],
        "forbidden_elements_quota": ["怀表意象最多出现两次，且每次必须推动信息变化"],
        "intentional_callbacks": ["开场回应信件与怀表的交接点", "章末留下裂缝后果供下一章承接"],
        "foreshadowing_plan": ["裂缝中的未来回声将在第3章形成身份真相回收"],
        "key_revelations": ["时间裂缝会回应怀表", "林远已被老守夜人提前布局引导"],
        "cross_scene_intent": {
            "cross_scene_references": [
                {
                    "from_scene": "scene_01",
                    "to_scene": "scene_02",
                    "ref_type": "callback",
                    "description": "scene_02 回呼 scene_01 中发现的怀表线索",
                },
                {
                    "from_scene": "scene_02",
                    "to_scene": "scene_03",
                    "ref_type": "foreshadow",
                    "description": "scene_03 的裂缝回溯在 scene_02 已埋下伏笔",
                },
            ],
            "pacing_curve": [3, 4, 5],
        },
    },
    ensure_ascii=False,
)
_MOCK_SCENE_CHAPTER_PLAN_PAYLOAD = json.loads(_MOCK_CHAPTER_PLAN)
for _idx, _scene in enumerate(_MOCK_SCENE_CHAPTER_PLAN_PAYLOAD["scene_intents"], start=1):
    _scene["scene_goal"] = _scene.get("purpose", "") or _scene.get("summary", "")
    _scene["owned_events"] = [_scene.get("required_outcome", "") or _scene.get("summary", "")]
    _scene["owned_revelations"] = (
        [_scene.get("revelation_level", "")]
        if _scene.get("revelation_level") and "不揭示" not in _scene.get("revelation_level", "")
        else []
    )
    _scene["owned_state_changes"] = [_scene.get("exit_target_state", "")]
    _scene["forbidden_overlap"] = []
    _scene["handoff_to_next"] = _scene.get("exit_target_state", "")
    _scene["dependency_scene_ids"] = [] if _idx == 1 else [f"scene_{_idx - 1:02d}"]
    _scene["parallel_group"] = f"group_{_idx:02d}"
    _scene["draft_order"] = _idx
    _scene["entry_state"] = "；".join(_scene.get("entry_state_refs", []) or [])
    _scene["exit_state"] = _scene.get("exit_target_state", "")
    # Scene-mode execution contract fields required by ``_PlanSceneIntentResponse``
    # (creative payload published to scene-mode providers).
    _scene["character_motivations"] = [
        {
            "character": char,
            "motivation": "推动场景目标",
            "stake": "关系到剧情走向",
        }
        for char in (_scene.get("required_characters", []) or [])[:2]
    ]
    _scene["relationship_dynamics"] = "人物关系随冲突发展而微妙变化"
    _scene["emotional_beat"] = "紧张感逐步累积"
    _scene["sensory_notes"] = "环境细节烘托氛围"
    _scene["sensory_focus"] = "视觉与听觉线索为主"
    _scene["dialogue_subtext"] = "台词之下藏着言外之意"
    _scene["choice_pressure"] = "角色必须在有限信息下抉择"
    _scene["scene_resistance"] = "阻力来自对手的隐藏动机"
    _scene["dialogue_voice_targets"] = {
        char: "克制而坚定" for char in (_scene.get("required_characters", []) or [])[:2]
    }
    _scene["revelation_level"] = (
        "不揭示核心真相"
        if _idx == 1
        else ("局部揭示：裂缝可被怀表定位" if _idx == 2 else "核心揭示：触碰裂缝会带来记忆代价")
    )
    _scene["symbol_usage_policy"] = "仅在关键节点使用象征意象"
    _scene["body_signal_budget"] = 2
    _scene["target_words"] = 1800
    _scene["pov_character"] = (_scene.get("required_characters", []) or [""])[0] or "叙述者"
    _scene["pov_scope"] = "limited"
    _scene["pov_switch_allowed"] = False
    _scene["pov_switch_marker_required"] = False
    _scene["pov_knowledge_constraints"] = {
        "forbidden_knowledge": [],
        "sensory_limits": [],
        "scope_label": "limited",
    }
_MOCK_CHAPTER_SCENE_PLAN = json.dumps(
    {"scene_plan": _MOCK_SCENE_CHAPTER_PLAN_PAYLOAD},
    ensure_ascii=False,
)
_MOCK_SCENE_PLAN_VALIDATION = json.dumps(
    {
        "valid": True,
        "issues": [],
        "parallel_groups": [["scene_01"], ["scene_02"], ["scene_03"]],
        "serial_edges": [
            {"before": "scene_01", "after": "scene_02", "reason": "后场依赖前场出口"},
            {"before": "scene_02", "after": "scene_03", "reason": "后场依赖前场出口"},
        ],
        "summary": "场景边界清晰，建议按顺序生成。",
    },
    ensure_ascii=False,
)

_MOCK_BLUEPRINT_ELEMENT_SELECT = json.dumps(
    {
        "genre_inference": ["悬疑", "线索回收"],
        "required_ids": [
            "core_time_anchor",
            "core_phase_progression",
            "core_character_arc",
            "core_conflict_escalation",
            "core_promise_payoff",
            "core_scene_function",
        ],
        "extension_ids": ["mystery_clue_ledger", "mystery_red_herring"],
        "extension_selection": [
            {
                "element_id": "mystery_clue_ledger",
                "reason": "核心谜团需要追踪线索投放、误导和最终回收。",
            },
            {
                "element_id": "mystery_red_herring",
                "reason": "调查过程需要可控误导，避免提前泄露真相。",
            },
        ],
        "focus_constraints": ["每个关键线索都必须有投放、误导或回收位置。"],
        "selector_summary": "Mock 选择保留核心叙事要素，并加入悬疑线索管理扩展。",
    },
    ensure_ascii=False,
)

_MOCK_BRIDGE = json.dumps(
    {
        "opening_time": "第一天，黄昏",
        "opening_location": "废弃图书馆外廊",
        "opening_pov": "林远",
        "transition_mode": "action_handoff",
        "emotional_carryover": "上一章被点燃的不安与求真冲动继续发酵",
        "action_handoff": "林远顺着信件的指引赶到图书馆，准备验证怀表与裂缝的关系",
        "causal_link": {
            "previous_event": "林远收到指向废弃图书馆的信件",
            "causal_mechanism": "信件与怀表异常共同指向图书馆，因此林远赶去验证裂缝线索",
            "unresolved_question": "怀表为何只对裂缝有反应",
            "open_threads": ["老守夜人的预知", "怀表与裂缝的关系"],
        },
        "pending_questions": ["老守夜人为什么提前知道裂缝存在", "怀表为何只对裂缝有反应"],
        "forbidden_repetition": ["不要重复上一章完整开场，不要重新解释失忆设定"],
        "opening_acceptance_criteria": [
            "开头必须交代信件如何把林远带到图书馆",
            "开头必须承接怀表异常带来的不安",
        ],
        "bridge_summary": "延续上一章的行动线，让林远带着信件和怀表直接抵达图书馆。",
        "relationship_beat": {
            "current_trust_level": "试探",
            "unspoken_tension": "林远对匿名信的来历保持警惕。",
            "power_dynamic": "掌握线索的人暂居上风。",
        },
        "sensory_anchors": ["潮湿纸页的霉味", "裂缝边缘的冷光"],
    },
    ensure_ascii=False,
)

_MOCK_CANON_DELTA = json.dumps(
    {
        "canon_delta": {
            "source_chapter": 1,
            "character_updates": {
                "林远": {
                    "name": "林远",
                    "alive": True,
                    "location": "废弃图书馆",
                    "emotional_state": "困惑但坚定",
                    "inventory": ["铜质怀表", "神秘信件"],
                    "knowledge": ["信中提到的图书馆线索"],
                },
                "老守夜人": {
                    "name": "老守夜人",
                    "alive": True,
                    "location": "钟楼",
                    "emotional_state": "平静而神秘",
                    "inventory": [],
                    "knowledge": ["林远的真实身份", "时间裂缝的秘密"],
                },
            },
            "new_events": [
                {
                    "chapter": 1,
                    "event": "林远在雾霭小镇失忆醒来",
                    "characters_involved": ["林远"],
                    "timestamp_in_story": "第一天，清晨",
                },
                {
                    "chapter": 1,
                    "event": "老守夜人交给林远一封信",
                    "characters_involved": ["林远", "老守夜人"],
                    "timestamp_in_story": "第一天，上午",
                },
            ],
            "foreshadowing_updates": [
                {
                    "id": "fs_watch",
                    "description": "怀表停在午夜十二点——暗示时间裂缝出现的时刻",
                    "planted_chapter": 1,
                    "status": "planted",
                },
                {
                    "id": "fs_old_man_eyes",
                    "description": "老人眼中的锐利光芒——暗示他并非普通老人",
                    "planted_chapter": 1,
                    "status": "planted",
                },
            ],
            "new_world_facts": {"时间裂缝入口": "废弃图书馆深处的墙壁裂痕"},
            "chapter_summary": "失忆的林远在雾霭小镇醒来，遇到神秘的老守夜人并获得一封自己所写的信，信中指引他前往废弃图书馆寻找时间裂缝。",
        },
        "creative_report": {
            "new_characters": [
                {
                    "name": "老守夜人",
                    "first_appearance_chapter": 1,
                    "role_in_story": "supporting",
                    "description": "神秘的老人，掌握时间裂缝的秘密",
                    "relationship_to_existing": {"林远": "引路人"},
                    "should_add_to_bible": True,
                }
            ],
            "new_locations": ["雾霭小镇", "废弃图书馆", "钟楼"],
            "new_key_items": ["铜质怀表", "神秘信件"],
            "plot_deviations": [],
            "suggestions_for_next_chapter": "林远进入废弃图书馆，探索时间裂缝的真相",
            "creative_highlights": ["失忆设定增加悬念", "怀表伏笔埋得巧妙"],
            "structured_summary": "本章完成主角入局，并把行动线稳定交接到废弃图书馆与时间裂缝。",
            "must_carry_forward": [
                "林远必须立刻前往废弃图书馆",
                "怀表与裂缝之间的联系需要在下一章验证",
            ],
            "bridge_hints": ["下一章应直接承接图书馆入口场景"],
            "character_state_deltas": [
                {
                    "name": "林远",
                    "gender": "男",
                    "change_summary": "从失忆后的茫然转为主动追索怀表与裂缝真相",
                    "to_state": {
                        "name": "林远",
                        "gender": "男",
                        "alive": True,
                        "physical": {
                            "location": "废弃图书馆",
                            "injuries": [],
                            "fatigue": "轻度疲惫",
                            "inventory": ["铜质怀表", "神秘信件"],
                        },
                        "emotional": {
                            "primary_emotion": "困惑但坚定",
                            "secondary_emotion": "压抑不安",
                            "stability": 0.52,
                            "desire": "查明自己的过去",
                            "fear": "再次失去记忆",
                        },
                        "motivation": {
                            "short_term_goal": "进入图书馆找到裂缝",
                            "long_term_goal": "恢复身份并修复现实",
                            "current_drive": "验证信件与怀表是否可信",
                            "internal_conflict": "害怕真相，但更害怕继续无知",
                        },
                        "knowledge_state": {
                            "known_facts": ["信中提到的图书馆线索"],
                            "suspicions": ["老守夜人知道自己的真实身份"],
                            "misbeliefs": [],
                            "secrets_kept": [],
                        },
                        "notes": "",
                    },
                }
            ],
            "relationship_deltas": [
                {
                    "pair_id": "林远__老守夜人",
                    "change_summary": "林远开始把老守夜人视为危险但必要的引路者",
                    "relationship": {
                        "pair_id": "林远__老守夜人",
                        "characters": ["林远", "老守夜人"],
                        "public_status": "引路人与被引导者",
                        "trust": 0.42,
                        "tension": 0.68,
                        "dependency": 0.35,
                        "last_shift_event": "老守夜人交出信件",
                        "last_updated_chapter": 1,
                        "notes": "",
                    },
                }
            ],
            "plot_thread_updates": [
                {
                    "thread_id": "time_rift_truth",
                    "change_summary": "核心主线被正式开启",
                    "thread": {
                        "thread_id": "time_rift_truth",
                        "title": "时间裂缝真相",
                        "status": "active",
                        "owners": ["林远", "老守夜人"],
                        "last_touched_chapter": 1,
                        "next_payoff_window": "第2-3章",
                        "blocking_condition": "林远尚未进入裂缝",
                        "summary": "林远必须查明裂缝、怀表和自己身份的关系",
                    },
                }
            ],
        },
        "chapter_exit_state": {
            "chapter_number": 1,
            "time_marker": "第一天，黄昏前",
            "location": "废弃图书馆入口外",
            "pov": "林远",
            "active_goals": ["进入图书馆", "验证怀表与裂缝的联系"],
            "open_questions": ["老守夜人是谁", "信件为何像是自己所写"],
            "must_carry_forward": [
                "林远必须立刻前往废弃图书馆",
                "怀表与裂缝之间的联系需要在下一章验证",
            ],
            "character_end_states": {
                "林远": {
                    "name": "林远",
                    "alive": True,
                    "location": "废弃图书馆入口外",
                    "emotional_state": "困惑但坚定",
                    "inventory": ["铜质怀表", "神秘信件"],
                    "knowledge": ["信中提到的图书馆线索"],
                    "notes": "",
                }
            },
        },
        "character_state_deltas": [
            {
                "name": "林远",
                "gender": "男",
                "change_summary": "从茫然被动转为主动追索",
                "to_state": {
                    "name": "林远",
                    "gender": "男",
                    "alive": True,
                    "location": "废弃图书馆入口外",
                    "emotional_state": "困惑但坚定",
                    "inventory": ["铜质怀表", "神秘信件"],
                    "knowledge": ["信中提到的图书馆线索"],
                    "notes": "",
                },
            }
        ],
        "relationship_deltas": [
            {
                "pair_id": "林远__老守夜人",
                "change_summary": "林远开始依赖老守夜人的线索",
                "relationship": {
                    "pair_id": "林远__老守夜人",
                    "characters": ["林远", "老守夜人"],
                    "public_status": "引路人与被引导者",
                    "trust": 0.42,
                    "tension": 0.68,
                    "dependency": 0.35,
                    "last_shift_event": "老守夜人交出信件",
                    "last_updated_chapter": 1,
                    "notes": "",
                },
            }
        ],
        "plot_thread_deltas": [
            {
                "thread_id": "time_rift_truth",
                "change_summary": "裂缝真相线被正式开启",
                "thread": {
                    "thread_id": "time_rift_truth",
                    "title": "时间裂缝真相",
                    "status": "active",
                    "owners": ["林远", "老守夜人"],
                    "last_touched_chapter": 1,
                    "next_payoff_window": "第2-3章",
                    "blocking_condition": "林远尚未进入裂缝",
                    "summary": "林远必须查明裂缝、怀表和自己身份的关系",
                },
            }
        ],
        "structured_summary": "本章完成主角入局，并把行动线稳定交接到废弃图书馆与时间裂缝。",
    },
    ensure_ascii=False,
)

_MOCK_CANON_PAYLOAD = json.loads(_MOCK_CANON_DELTA)
_MOCK_EXTRACT_CHAPTER_SUMMARY_EXIT = json.dumps(
    {
        "chapter_exit_state": _MOCK_CANON_PAYLOAD["chapter_exit_state"],
        "structured_summary": _MOCK_CANON_PAYLOAD["structured_summary"],
    },
    ensure_ascii=False,
)
_MOCK_EXTRACT_CANON_DELTA_FRAGMENT = json.dumps(
    {"canon_delta": _MOCK_CANON_PAYLOAD["canon_delta"]},
    ensure_ascii=False,
)
_MOCK_EXTRACT_CREATIVE_REPORT = json.dumps(
    {"creative_report": _MOCK_CANON_PAYLOAD["creative_report"]},
    ensure_ascii=False,
)
_MOCK_EXTRACT_CHARACTER_STATE_DELTAS = json.dumps(
    {"character_state_deltas": _MOCK_CANON_PAYLOAD["character_state_deltas"]},
    ensure_ascii=False,
)
_MOCK_EXTRACT_RELATIONSHIP_DELTAS = json.dumps(
    {"relationship_deltas": _MOCK_CANON_PAYLOAD["relationship_deltas"]},
    ensure_ascii=False,
)
_MOCK_EXTRACT_PLOT_THREAD_DELTAS = json.dumps(
    {"plot_thread_deltas": _MOCK_CANON_PAYLOAD["plot_thread_deltas"]},
    ensure_ascii=False,
)

_MOCK_ALIGNMENT = json.dumps(
    {
        "alignment_score": 8.2,
        "risk_level": "low",
        "summary": "本章主线推进充分，支线占比可控。",
        "findings": [],
        "missing_main_points": [],
        "supportive_subplot_points": [],
        "weak_subplot_points": [],
        "repair_actions": [],
    },
    ensure_ascii=False,
)

_MOCK_CONTINUITY_REPORT = json.dumps(
    {
        "continuity_score": 8.8,
        "summary": "章节桥接整体稳定，开场时间、地点与行动接力清晰。",
        "issues": [],
    },
    ensure_ascii=False,
)

_MOCK_CAUSAL_REPORT = json.dumps(
    {
        "causal_score": 9.0,
        "summary": "因果链完整，开头承接自然，无明显断裂。",
        "causal_link_verified": True,
        "issues": [],
    },
    ensure_ascii=False,
)

_MOCK_CHECK_CHAPTER = json.dumps(
    {
        "risk_level": "low",
        "summary": "Mock 章节检查未发现阻断性问题。",
        "prompt_leaks": [],
        "factual_errors": [],
        "continuity_errors": [],
        "expression_errors": [],
        "repair_actions": [],
        "forbidden_element_findings": [],
    },
    ensure_ascii=False,
)

_MOCK_PATCH_CHAPTER = json.dumps(
    {"patches": []},
    ensure_ascii=False,
)

_MOCK_CONTINUITY_REPAIR = json.dumps(
    {"revised_text": _MOCK_EDIT},
    ensure_ascii=False,
)

_MOCK_CONTEXT_COMPRESS = json.dumps(
    {"items": [{"id": "ctx_1", "compressed": "保留主线和事实约束的精简描述。"}]},
    ensure_ascii=False,
)

_MOCK_ADAPTIVE_COMPRESS = json.dumps(
    {
        "items": [
            {
                "id": "adaptive_ctx_1",
                "compressed": "保留当前章节最关键的因果、角色和禁改事实。",
                "importance": "high",
            }
        ]
    },
    ensure_ascii=False,
)

_MOCK_VERIFY_COMPRESSION = json.dumps(
    {
        "quality_score": 0.92,
        "recommendation": "accept",
        "missing_critical_items": [],
        "notes": "Mock 压缩质量通过。",
    },
    ensure_ascii=False,
)

_MOCK_PLOT_GUARD_JUDGE = json.dumps(
    {
        "decision": "continue_with_constraints",
        "risk_level": "medium",
        "outline_action": "none",
        "entity_actions": [
            {
                "entity_type": "character",
                "name": "老守夜人",
                "action": "keep",
                "reason": "与主线冲突直接相关，具备后续复用价值。",
            }
        ],
        "next_chapter_constraints": [
            "第一节拍需直接回扣主线目标。",
            "限制新增角色数量，优先复用已有角色。",
        ],
        "reasoning_brief": "当前偏离可控，不必改大纲；下一章通过主线回扣即可收敛。",
        "targeted_repairs": [],
    },
    ensure_ascii=False,
)

_MOCK_GUARD_CONSTRAINT_CHECK = json.dumps(
    {
        "status": "pass",
        "confidence": 0.9,
        "evidence": "正文未出现违反当前约束的事件或表述。",
        "notes": "Mock 约束检查通过。",
    },
    ensure_ascii=False,
)

_MOCK_MACRO_GUARD_AUDIT = json.dumps(
    {
        "dimensions": {
            "outline_alignment": 1.0,
            "character_arc_consistency": 1.0,
            "pacing_curve": 1.0,
            "foreshadowing_recovery": 1.0,
            "thematic_cohesion": 1.0,
        },
        "recommended_action": "pass",
        "drift_score": 0.0,
        "findings": [],
        "adjustment_plan": {
            "window_size": 0,
            "strategy": "",
            "target_outline_v": "",
            "adjusted_chapter_goals": [],
            "reasoning": "Mock 宏观轨迹无需调整。",
        },
        "confidence": 1.0,
        "reasoning": "Mock 宏观守护检查通过。",
    },
    ensure_ascii=False,
)

_MOCK_CRITIC_CONTINUITY = json.dumps({"issues": []}, ensure_ascii=False)
_MOCK_CRITIC_CHARACTER = json.dumps({"issues": []}, ensure_ascii=False)
_MOCK_CRITIC_CAUSAL = json.dumps({"causal_breaks": []}, ensure_ascii=False)
_MOCK_CRITIC_STRENGTHS = json.dumps(
    {"strengths": [{"dimension": "hook", "summary": "Mock 开场钩子清晰。"}]},
    ensure_ascii=False,
)

_MOCK_VOLUME_AUDIT = json.dumps(
    {
        "volume_number": 1,
        "volume_title": "第一卷",
        "chapter_range": "1-1",
        "volume_summary": "本卷完成开局与核心冲突埋设，主角完成阶段性转折。",
        "milestone_status": [
            {
                "milestone": "主角完成入局并确认主线目标",
                "status": "done",
                "evidence": "章节结尾已形成下一卷主冲突钩子。",
            }
        ],
        "consistency_score": 8.5,
        "consistency_issues": [],
        "carry_over_characters": ["林远", "老守夜人"],
        "retire_characters": [],
        "carry_over_items": ["铜质怀表", "神秘信件"],
        "retire_items": [],
        "carry_over_world_fact_keys": ["时间裂缝入口"],
        "retire_world_fact_keys": [],
        "carry_over_foreshadowing_ids": ["fs_watch", "fs_old_man_eyes"],
        "resolved_foreshadowing_ids": [],
        "next_volume_focus": "推进裂缝真相线并升级主角代价选择。",
        "token_optimization_notes": [
            "次要角色可归档，保持上下文角色数在20以内。",
            "只保留活跃伏笔与关键世界事实。",
        ],
    },
    ensure_ascii=False,
)

_MOCK_ENRICHED_SPEC = json.dumps(
    {
        "title": "雾中之约",
        "genre": "fantasy",
        "theme": "一个关于勇气与牺牲的奇幻故事",
        "tone": "epic",
        "length_target": 900,
        "language": "zh",
        "characters_hint": "林远（沉着冷静的青年剑士，因师父之死踏上复仇之路，内心深处渴望救赎而非杀戮）；"
        "苏晚（神秘的药师女子，表面冷漠实则背负灭族之恨，与林远目标交汇却理念对立）",
        "world_hint": "架空大陆·云渊纪，灵力与剑道并存的武侠世界，雾海将大陆一分为二",
        "conflict_hint": "外部：雾海异变吞噬村庄，幕后势力试图利用雾海之力称霸；内在：林远在复仇与守护之间抉择",
        "pov_hint": "第三人称有限视角，紧跟林远",
        "opening_style": "以林远在浓雾中发现被吞噬的村庄废墟开场，用感官细节建立紧张氛围",
        "ending_style": "余韵式收束：雾海退去，林远放下仇恨，但苏晚独自走入远方",
        "extra_instructions": "",
    },
    ensure_ascii=False,
)

_MOCK_GENERATE_CONFIG = json.dumps(
    {
        "title": "雾中之约",
        "genre": "fantasy",
        "theme": "勇气、记忆与自我救赎",
        "premise": "失忆青年林远进入被雾海隔绝的小镇，追查自己亲手留下的旧信，并在时间裂缝前选择终止复仇循环。",
        "tone": "suspenseful",
        "length_target": 900,
        "total_chapters": 8,
        "words_per_chapter": 3000,
        "chapters_per_volume": 4,
        "volume_mode": "auto",
        "writing_mode": "auto",
        "language": "zh",
        "characters_hint": "林远（男，沉着冷静的青年剑士，渴望救赎）；苏晚（女，神秘药师，与林远理念对立但目标交汇）",
        "world_hint": "架空大陆·云渊纪，灵力与剑道并存，雾海和时间裂缝改变边境小镇的秩序。",
        "conflict_hint": "外部：雾海异变吞噬村庄；内在：林远在复仇与守护之间抉择。",
        "pov_hint": "第三人称有限视角，紧跟林远。",
        "opening_style": "以林远在浓雾中发现倒走的车站时钟开场。",
        "ending_style": "余韵式收束：雾海退去，林远放下仇恨。",
        "polish_hint": "",
        "extra_instructions": "",
        "project_id": "mock_project",
        "max_edit_rounds": 1,
        "polish_suggestions": [],
        "creative_note": {
            "core_pitch": "雾中小镇与时间裂缝推动主角完成自我救赎。",
            "design_intent": "保持悬疑推进、清晰人物动机和可执行章节规划。",
            "preserved_constraints": ["中文输出", "第三人称有限视角", "章节目标可验证"],
            "field_rationales": {
                "genre": "奇幻悬疑适合雾海与时间裂缝设定。",
                "tone": "悬疑语气能支撑线索递进。",
            },
            "risks": ["避免过早解释时间裂缝真相"],
            "next_moves": ["生成故事圣经", "规划角色弧线", "拆分章节契约"],
            "anti_drift_check": "后续输出不得偏离主角追查旧信与裂缝的核心线。",
        },
    },
    ensure_ascii=False,
)

_MOCK_SHORT_BLUEPRINT = json.dumps(
    {
        "synopsis": "失忆青年在雾中小镇追寻一封自己留下的信，并在裂缝前做出选择。",
        "anchor_elements": {
            "time_frame": "一个雾气弥漫的夜晚",
            "primary_locations": ["小镇车站", "钟楼", "裂缝边缘"],
            "core_characters": [{"name": "林远", "role": "主角"}],
            "central_event": "林远按旧信指引寻找时间裂缝的真相。",
        },
        "narrative_phases": [
            {
                "phase_name": "入雾",
                "position_start": 0,
                "position_end": 30,
                "description": "林远收到旧信并进入异常小镇。",
                "tension_level": "渐升",
                "emotional_focus": "困惑",
                "time_setting": "夜晚",
                "location": "小镇车站",
                "characters_present": ["林远"],
                "key_event": "林远发现车站时钟倒走。",
            },
            {
                "phase_name": "逼近真相",
                "position_start": 30,
                "position_end": 75,
                "description": "线索把他带到钟楼和裂缝边缘。",
                "tension_level": "高",
                "emotional_focus": "紧张",
                "time_setting": "深夜",
                "location": "钟楼",
                "characters_present": ["林远"],
                "key_event": "林远确认旧信来自过去的自己。",
            },
            {
                "phase_name": "选择",
                "position_start": 75,
                "position_end": 100,
                "description": "林远在代价与真相之间做出选择。",
                "tension_level": "回落",
                "emotional_focus": "释然",
                "time_setting": "黎明前",
                "location": "裂缝边缘",
                "characters_present": ["林远"],
                "key_event": "林远选择进入裂缝寻找真相。",
            },
        ],
        "turning_points": [
            {"position_percent": 30, "description": "收到旧信", "impact": "驱动主角行动"},
            {"position_percent": 70, "description": "发现裂缝代价", "impact": "迫使主角重新选择"},
        ],
        "character_arcs": [
            {
                "character": "林远",
                "arc_summary": "从被动失忆者到主动承担代价",
                "key_moment": "裂缝边缘",
            }
        ],
        "emotional_arc": "困惑→紧张→释然",
        "ending_strategy": "余韵式收束，主角进入裂缝寻找真相。",
    },
    ensure_ascii=False,
)

_MOCK_SHORT_CREATIVE_SUMMARY = json.dumps(
    {
        "characters": [{"name": "林远", "arc": "从迷惘到主动选择"}],
        "narrative_analysis": {
            "pacing_assessment": "节奏紧凑，线索逐步升级。",
            "tension_curve": "medium",
            "structure_type": "三幕式",
            "turning_points": [],
            "opening_hook": "开篇以异常时钟制造悬念。",
            "ending_impact": "结尾保留余韵。",
        },
        "thematic_analysis": {
            "core_theme": "身份、选择与代价。",
            "theme_delivery": "通过追寻旧信和进入裂缝的行动传达。",
            "symbolic_elements": ["倒走的时钟"],
            "emotional_resonance": "主动面对未知带来释然。",
        },
        "creative_highlights": ["异常小镇氛围清晰"],
        "improvement_suggestions": [],
        "beat_fulfillment": [],
    },
    ensure_ascii=False,
)

_MOCK_ENRICH_CHARACTER = json.dumps(
    {
        "appearance": "青年剑士，黑发，常携一枚停摆怀表。",
        "personality": "沉着、谨慎，在压力下仍会保护弱者。",
        "backstory": "曾在雾海边缘失去记忆，只保留对裂缝的直觉恐惧。",
        "arc": "从被线索牵引到主动承担选择后果。",
    },
    ensure_ascii=False,
)

_MOCK_INTRODUCE_CHARACTER = json.dumps(
    {
        "name": "林远",
        "role": "protagonist",
        "gender": "男",
        "social_status": "流浪剑士",
        "abilities": "剑术、观察力、对时间裂缝的异常感知",
        "appearance": "黑发青年，随身携带铜质怀表。",
        "personality": "冷静、克制、保护欲强。",
        "backstory": "在雾海事故后失忆，追寻自己留下的信件。",
        "arc": "从寻找身份到承担守护小镇的责任。",
        "relationships": {"老守夜人": "引路人与被引导者"},
        "voice": "",
        "notes": "Mock 角色，可用于测试人物引入流程。",
    },
    ensure_ascii=False,
)

_MOCK_ADJUDICATE_CHARACTER_INTRODUCTION = json.dumps(
    {
        "decisions": [
            {
                "name": "林远",
                "decision": "accept",
                "reason": "角色与主线目标直接相关。",
            }
        ],
        "summary": "Mock 人物引入裁判通过。",
    },
    ensure_ascii=False,
)

_MOCK_PROFILE_STYLE = json.dumps(
    {
        "modules": [
            {
                "name": "节奏推进",
                "rules": [
                    "每段优先落动作结果，再补情绪解释。",
                    "段末尽量留可感知钩子。",
                ],
                "positive_example": "她抬手关灯，门外脚步声却更近了。",
                "negative_example": "她觉得事情可能很危险，心里有些紧张。",
            },
            {
                "name": "对话信息量",
                "rules": [
                    "对话每轮至少推进一条信息或关系变化。",
                    "避免无信息寒暄连续出现。",
                ],
                "positive_example": "“钥匙在你那。”他停顿，“但门不是你开的。”",
                "negative_example": "“嗯。” “哦。” “好的。”",
            },
        ],
        "source_elements": ["story_bible", "character_bible", "blueprint_elements"],
        "summary": "悬疑导向，快节奏推进，动作与对话共同驱动信息释放。",
        "global_style": {
            "dialogue_ratio": "medium",
            "pace_mode": "fast",
            "emotional_style": "subtle",
            "environment_ratio": "medium",
            "info_density": "high",
            "banned_phrases": [],
        },
    },
    ensure_ascii=False,
)

_MOCK_PROFILE_STRUCTURE = json.dumps(
    {
        "hook_config": {
            "preferred_types": ["mystery", "crisis"],
            "strength_baseline": "medium",
            "chapter_end_required": True,
        },
        "strand_config": {
            "quest_max_consecutive": 5,
            "fire_max_absent": 10,
            "constellation_max_absent": 15,
            "stagnation_threshold": 3,
        },
        "micro_payoff_config": {
            "preferred_types": ["information", "clue"],
            "min_per_chapter": 1,
        },
        "cool_point_config": {
            "preferred_patterns": ["真相揭露"],
            "density_per_chapter": "medium",
        },
    },
    ensure_ascii=False,
)

_MOCK_EDITORIAL_CONTRACT = json.dumps(
    {
        "project_title": "Mock 长篇",
        "character_voices": [
            {
                "character": "林远",
                "sentence_profile": "短句为主，先给事实再给判断。",
                "explanation_bias": "少解释，用行动和问题推进。",
                "emotion_syntax": "情绪升高时句子更短，出现停顿。",
                "signature_moves": ["用问题截断对方", "先确认风险再行动"],
                "taboo_patterns": ["大段抒情独白"],
                "sample_lines": ["先看门。门开了，再谈答案。"],
            },
            {
                "character": "苏晚",
                "sentence_profile": "句子更完整，常用反问和含蓄转折。",
                "explanation_bias": "隐藏关键动机，只给必要线索。",
                "emotion_syntax": "情绪升高时转为冷静陈述。",
                "signature_moves": ["反问", "转移到证据"],
                "taboo_patterns": ["直接说明全部伤口"],
                "sample_lines": ["你要的不是答案，是一个能承担答案的人。"],
            },
        ],
        "climax_markers": [
            {
                "chapter_number": 16,
                "climax_type": "main",
                "description": "主角确认时间裂缝真相并完成核心选择。",
                "expected_aftermath_chapters": 4,
            }
        ],
        "denouement_budget": {
            "expected_chapters": 4,
            "max_confirmation_scenes": 2,
            "required_new_functions": ["余波后果", "关系制度化", "终场意象"],
            "forbidden_repeats": ["重复确认同一主题句"],
        },
        "theme_policies": ["主题通过角色选择和后果呈现，不用叙述者直接解释。"],
        "symbol_policies": [
            {
                "symbol": "怀表",
                "narrative_function": "时间承诺与记忆缺口",
                "explanation_policy": "explain_once",
                "escalation_rule": "每次出现必须改变信息功能或关系功能。",
                "max_explicit_explanations": 1,
            }
        ],
        "scene_resistance_rules": [
            {
                "scene_type": "对峙",
                "required_resistance": "必须有空间、流程或物件阻力迫使角色选择。",
                "examples": ["门被堵住", "签字窗口关闭", "人群遮挡视线"],
            }
        ],
        "expression_channel_budget": {
            "somatic_reaction": 1,
            "action_tag": 2,
            "dialogue_tag": 2,
            "sensory_anchor": 3,
        },
        "expression_channel_profiles": [
            {
                "channel_id": "timepiece_body_signal",
                "channel": "somatic_reaction",
                "label": "怀表触发后的身体惊动",
                "surface_forms": ["心口一紧", "指尖发凉"],
                "trigger_contexts": ["时间裂缝", "记忆缺口"],
                "risk_reason": "容易把每次时间异常都写成同一种身体反应。",
                "replacement_axes": ["物件操作", "对白停顿", "场景阻力"],
                "allowed_when": "首次强感应或主线揭示节点可少量保留。",
                "cooldown_chapters": 3,
                "actor_scope": "global",
                "confidence": 0.8,
            }
        ],
        "body_signal_budget_per_high_emotion_scene": 1,
        "forbidden_confirmation_phrases": ["核心主题句过度复现"],
        "revision_priorities": ["先压结构", "再分声纹", "最后删解释"],
        "revelation_ladder": [
            {
                "thread": "怀表真相线",
                "stage": "物件感应",
                "stage_order": 1,
                "target_chapter": 3,
                "trigger": "怀表第一次启动",
                "allowed_disclosure": "只允许出现不完整感应，不解释真相。",
                "required_action_consequence": "主角决定主动查证怀表来源。",
            },
            {
                "thread": "怀表真相线",
                "stage": "行动后果",
                "stage_order": 2,
                "target_chapter": 12,
                "trigger": "市场反制失败后重新核对旧档案",
                "allowed_disclosure": "给出可验证物证，不给完整答案。",
                "required_action_consequence": "角色改变调查方向并承担时间成本。",
            },
        ],
        "editorial_element_directives": [
            {
                "element_id": "editorial_denouement_budget",
                "element_name": "高潮后余波预算",
                "directive_type": "主高潮后结构压缩",
                "target_window": "主高潮后",
                "linked_characters": [],
                "requirement": "余波章节必须承担新后果，不重复确认同一圆满。",
                "success_criteria": "读者能区分余波、制度化和终场意象三个功能。",
            },
            {
                "element_id": "editorial_voice_matrix",
                "element_name": "角色声纹矩阵",
                "directive_type": "对白分化",
                "target_window": "全书",
                "linked_characters": ["林远", "苏晚"],
                "requirement": "同场对白体现句长、停顿和解释倾向差异。",
                "success_criteria": "去掉说话人标记后仍能大致分辨角色。",
            },
        ],
        "time_bridge_policies": ["跨月或跨季转场必须在章节开头给出明确时间桥。"],
        "title_policy": {
            "max_reuse": 2,
            "allowed_repeated_titles": ["核心回环标题"],
            "naming_strategy": "章节标题应提示节点功能；只保留少量有意回环标题。",
        },
    },
    ensure_ascii=False,
)

_MOCK_EDITORIAL_PAYLOAD = json.loads(_MOCK_EDITORIAL_CONTRACT)
_MOCK_EDITORIAL_CHARACTER_VOICES = json.dumps(
    {"character_voices": _MOCK_EDITORIAL_PAYLOAD["character_voices"]},
    ensure_ascii=False,
)
_MOCK_EDITORIAL_STRUCTURE = json.dumps(
    {
        "climax_markers": _MOCK_EDITORIAL_PAYLOAD["climax_markers"],
        "denouement_budget": _MOCK_EDITORIAL_PAYLOAD["denouement_budget"],
        "revelation_ladder": _MOCK_EDITORIAL_PAYLOAD["revelation_ladder"],
        "time_bridge_policies": _MOCK_EDITORIAL_PAYLOAD["time_bridge_policies"],
        "title_policy": _MOCK_EDITORIAL_PAYLOAD["title_policy"],
    },
    ensure_ascii=False,
)
_MOCK_EDITORIAL_STYLE_CONSTRAINTS = json.dumps(
    {
        "theme_policies": _MOCK_EDITORIAL_PAYLOAD["theme_policies"],
        "symbol_policies": _MOCK_EDITORIAL_PAYLOAD["symbol_policies"],
        "scene_resistance_rules": _MOCK_EDITORIAL_PAYLOAD["scene_resistance_rules"],
        "expression_channel_budget": _MOCK_EDITORIAL_PAYLOAD["expression_channel_budget"],
        "expression_channel_profiles": _MOCK_EDITORIAL_PAYLOAD["expression_channel_profiles"],
        "body_signal_budget_per_high_emotion_scene": _MOCK_EDITORIAL_PAYLOAD[
            "body_signal_budget_per_high_emotion_scene"
        ],
        "forbidden_confirmation_phrases": _MOCK_EDITORIAL_PAYLOAD["forbidden_confirmation_phrases"],
        "revision_priorities": _MOCK_EDITORIAL_PAYLOAD["revision_priorities"],
    },
    ensure_ascii=False,
)

_MOCK_EXPRESSION_OBSERVATIONS = json.dumps(
    {
        "observations": [
            {
                "scene_index": 1,
                "quote": "心口一紧",
                "channel_id": "timepiece_body_signal",
                "channel": "somatic_reaction",
                "profile_label": "怀表触发后的身体惊动",
                "trigger_context": "时间裂缝",
                "semantic_role": "身体惊动",
                "actor": "",
                "confidence": 0.82,
            }
        ],
        "skipped_reason": "",
        "source_text_hash": "",
    },
    ensure_ascii=False,
)
_MOCK_EDITORIAL_ELEMENT_DIRECTIVES = json.dumps(
    {"editorial_element_directives": _MOCK_EDITORIAL_PAYLOAD["editorial_element_directives"]},
    ensure_ascii=False,
)

_MOCK_READING_POWER = json.dumps(
    {
        "hook_type": "mystery",
        "hook_strength": "strong",
        "hook_description": "章尾留下时间裂缝与身份谜题。",
        "prev_hook_fulfilled": True,
        "micro_payoffs": [
            {
                "payoff_type": "clue",
                "description": "怀表与图书馆裂缝形成明确线索链。",
                "strength": "strong",
            },
            {
                "payoff_type": "information",
                "description": "第七十二页地图揭示十二个时间节点。",
                "strength": "strong",
            },
            {
                "payoff_type": "emotion",
                "description": "林远从失忆惊惶转入主动追索。",
                "strength": "medium",
            },
        ],
        "is_transition": False,
        "next_chapter_reason": "读者会想知道裂缝背后的真实身份关系。",
        "information_pacing": "balanced",
        "information_pacing_score": 2.0,
        "main_plot_depth": "deep",
        "main_plot_advancement_notes": "主角获得下一步调查方向。",
        "tension_match": "matched",
        "tension_match_score": 2.0,
        "revelation_count": 1,
        "revelation_over_budget": False,
        "character_drive": "strong",
        "character_drive_notes": "主角从被动苏醒转为主动追索线索。",
        "suggestions": [],
    },
    ensure_ascii=False,
)

_MOCK_BOOK_CONSISTENCY = json.dumps(
    {
        "issues": [],
        "summary": "Mock 审计：章节间人物、时间线与世界规则保持一致。",
        "consistency_score": 9.0,
        "repair_plan": [],
    },
    ensure_ascii=False,
)

_MOCK_BOOK_CONSISTENCY_ISSUES = json.dumps({"issues": []}, ensure_ascii=False)

_MOCK_BOOK_CONSISTENCY_VERIFY = json.dumps(
    {
        "verified_issues": [],
    },
    ensure_ascii=False,
)

_MOCK_EDITORIAL_AUDIT = json.dumps(
    {
        "summary": "Mock 编辑审计：结构、声纹和象征物解释预算可控。",
        "findings": [],
        "revision_plan": [],
        "metrics": {"editorial_score": 9.0},
    },
    ensure_ascii=False,
)

_MOCK_EXTRACT_MOTIFS = json.dumps(
    {
        "motifs": [
            {
                "motif_id": "mock_clock_crack",
                "name": "凝固的怀表",
                "category": "符号",
                "description": "怀表与时间裂缝共同指向主角被抹去的过去。",
                "thematic_meaning": "时间停滞与记忆缺口",
                "is_intentional": True,
                "occurrences": [
                    {
                        "paragraph_index": 2,
                        "text_snippet": "胸口那枚冰凉的铜质怀表",
                        "context": "怀表指针凝固在午夜，提示时间规则异常。",
                        "associated_characters": ["林远"],
                        "emotional_tone": "悬疑",
                    }
                ],
            }
        ],
    },
    ensure_ascii=False,
)

_MOCK_ADJUST_OUTLINE = json.dumps(
    {
        "adjusted_chapters": [
            {
                "chapter_number": 4,
                "title": "裂缝之后",
                "goal": "承接已完成章节的实际结尾，让林远进入废弃图书馆并确认时间裂缝扩散。",
                "beats_summary": [
                    "林远根据神秘信件进入废弃图书馆。",
                    "怀表在裂缝附近产生异常反应。",
                    "老守夜人透露裂缝扩散会影响整个小镇。",
                ],
                "pov_character": "林远",
                "setting": "废弃图书馆",
                "expected_word_count": 3000,
                "notes": "Mock 大纲调整：保持主线方向，只调整下一章衔接方式。",
            }
        ],
        "adjustment_summary": "后续章节从图书馆入口自然衔接，强化时间裂缝主线。",
    },
    ensure_ascii=False,
)

_MOCK_SUMMARY = json.dumps(
    {
        "summary": "Mock 摘要：主角获得线索并继续追查时间裂缝。",
    },
    ensure_ascii=False,
)

_MOCK_POLISH_OUTLINE = json.dumps(
    {
        "adjusted_chapters": [
            {
                "chapter_number": 1,
                "goal": "林远在雾霭小镇醒来并获得第一条时间裂缝线索，开篇悬念更集中。",
                "beats_summary": ["失忆醒来", "怀表停摆", "守夜人交出未拆信件"],
                "main_plot_points": ["主角确认时间异常与自身记忆缺失有关"],
                "involved_characters": ["林远", "老守夜人"],
                "notes": "强化章尾未拆信件带来的追读钩子。",
            }
        ],
        "polish_suggestions": [
            "强化每章结尾钩子与下一章开场之间的因果衔接",
            "为主角记忆缺失设置可逐步兑现的线索链",
        ],
    },
    ensure_ascii=False,
)

_MOCK_POLISH_SUBPLOT = json.dumps(
    {
        "subplots": [
            {
                "subplot_id": "trust",
                "summary": "主角与守夜人的互信线保留为低频推进。",
                "status": "active",
            }
        ]
    },
    ensure_ascii=False,
)

_MOCK_REPAIR_SEMANTIC_VERIFY = json.dumps(
    {
        "issue_resolved": True,
        "confidence": 0.9,
        "reasoning": "Mock 语义复核确认问题已解决。",
    },
    ensure_ascii=False,
)

_MOCK_EXTRACT_KNOWLEDGE_DELTAS = json.dumps(
    {"knowledge_deltas": []},
    ensure_ascii=False,
)

_MOCK_REPAIR_STRATEGY_DIAGNOSE = json.dumps(
    {
        "preferred_strategy": "no_op",
        "confidence": 0.9,
        "reason": "Mock 未发现需要修复的知识边界问题。",
        "root_causes": [],
        "risk_flags": [],
        "diagnostic_summary": "Mock 诊断通过，无需修复。",
    },
    ensure_ascii=False,
)

_MOCK_REPAIR_KNOWLEDGE_BOUNDARY = json.dumps(
    {
        "repaired_text": "",
        "changes": [],
    },
    ensure_ascii=False,
)

_MOCK_SUMMARY_DRIFT_CHECK = json.dumps(
    {
        "issues": [],
        "facts_checked": 0,
        "facts_missing": 0,
        "facts_contradicted": 0,
        "summary": "Mock 摘要漂移检查通过。",
    },
    ensure_ascii=False,
)

_MOCK_AUDIT_POV_DRIFT = json.dumps(
    {
        "verdict": "pass",
        "issues": [],
    },
    ensure_ascii=False,
)

_MOCK_INIT_KNOWLEDGE_BOUNDARIES = json.dumps(
    {
        "characters": [],
    },
    ensure_ascii=False,
)

_MOCK_RECONCILE_ENTITIES = json.dumps(
    {
        "resolutions": [],
    },
    ensure_ascii=False,
)

_MOCK_CANDIDATE_STATE_DELTAS = json.dumps(
    {
        "candidates": [
            {
                "candidate_id": "mock_candidate_1",
                "delta_type": "event",
                "summary": "Mock 候选：主角继续追查时间裂缝。",
                "entity_ids": [],
                "proposed_delta": {"state_path": "plot.mock", "value": "advancing"},
                "evidence": [{"quote": "时间裂缝"}],
                "extraction_notes": "Mock 候选状态变化。",
            }
        ]
    },
    ensure_ascii=False,
)

_MOCK_STATE_DELTA_ADJUDICATION = json.dumps(
    {
        "candidate_id": "mock_candidate_1",
        "verdict": "accept",
        "severity": "low",
        "confidence": 0.82,
        "rationale": "证据窗口支持该候选状态变化。",
        "evidence_quotes": ["时间裂缝"],
        "affected_state_paths": ["plot.mock"],
        "repair_instruction": "",
        "pending_reason": "",
    },
    ensure_ascii=False,
)

_MOCK_CONTRACT_COMPLETION_ADJUDICATION = json.dumps(
    {
        "verdict": "accept",
        "severity": "low",
        "rationale": "章节契约已完成全部必达事件。",
        "repair_or_replan_decision": "continue",
        "missing_required_progressions": [],
        "missing_knowledge_ops": [],
        "forbidden_progression_hits": [],
        "future_leak_hits": [],
        "unexpected_progressions": [],
        "unaccepted_knowledge_ops": [],
        "evidence_quotes": ["时间裂缝"],
        "should_block_archive": False,
        "contract_completion_score": 9.4,
    },
    ensure_ascii=False,
)

_MOCK_FINAL_STATE_ADJUDICATION = json.dumps(
    {
        "chapter_number": 1,
        "verdict": "accept",
        "severity": "low",
        "confidence": 0.82,
        "accepted_candidate_ids": ["mock_candidate_1"],
        "rejected_candidate_ids": [],
        "pending_candidate_ids": [],
        "repair_candidate_ids": [],
        "state_updates": [
            {
                "candidate_id": "mock_candidate_1",
                "state_path": "plot.mock",
                "value": "advancing",
            }
        ],
        "pending_items": [],
        "repair_issues": [],
        "should_block_archive": False,
        "summary": "Mock 最终状态裁判通过。",
    },
    ensure_ascii=False,
)

_RESPONSES: dict[TaskType, str] = {
    TaskType.SPEC_ENRICH: _MOCK_ENRICHED_SPEC,
    TaskType.SHORT_BLUEPRINT: _MOCK_SHORT_BLUEPRINT,
    TaskType.BEATS: _MOCK_BEATS,
    TaskType.DRAFT: _MOCK_DRAFT,
    TaskType.EDIT: _MOCK_EDIT,
    TaskType.EVALUATE: _MOCK_EVAL,
    TaskType.SHORT_CREATIVE_SUMMARY: _MOCK_SHORT_CREATIVE_SUMMARY,
    TaskType.ENRICH_CHARACTER: _MOCK_ENRICH_CHARACTER,
    TaskType.INTRODUCE_CHARACTER: _MOCK_INTRODUCE_CHARACTER,
    TaskType.ADJUDICATE_CHARACTER_INTRODUCTION: _MOCK_ADJUDICATE_CHARACTER_INTRODUCTION,
    TaskType.INIT_STORY_BIBLE: _MOCK_STORY_BIBLE,
    TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES: json.dumps(
        {
            "candidates": [
                {
                    "candidate_id": "candidate_a",
                    "packet": {
                        "emotional_engine": ["信任在选择中反复受压"],
                        "thematic_promises": ["选择必须付出可见代价"],
                        "signature_motifs": ["旧钥匙"],
                        "relationship_tensions": ["主角与盟友互相需要又彼此怀疑"],
                        "anti_cliche_rules": ["关键转折必须由人物选择触发"],
                        "scene_potential": ["公开场合的两难选择"],
                        "notes": "Mock 候选 A",
                    },
                    "preserved_intent_ids": [],
                    "assumptions": [],
                    "hard_constraint_risks": [],
                    "intent_conflicts": [],
                },
                {
                    "candidate_id": "candidate_b",
                    "packet": {
                        "emotional_engine": ["身份秘密改变亲密关系"],
                        "thematic_promises": ["真相与归属不能同时轻易获得"],
                        "signature_motifs": ["未寄出的信"],
                        "relationship_tensions": ["保护行为被误读为背叛"],
                        "anti_cliche_rules": ["谜底通过证据链而非独白揭晓"],
                        "scene_potential": ["证据与情感诉求正面冲突"],
                        "notes": "Mock 候选 B",
                    },
                    "preserved_intent_ids": [],
                    "assumptions": [],
                    "hard_constraint_risks": [],
                    "intent_conflicts": [],
                },
            ]
        },
        ensure_ascii=False,
    ),
    TaskType.INIT_CREATIVE_DIRECTION_SELECT: json.dumps(
        {
            "selected_candidate_id": "candidate_a",
            "diversity_score": 0.72,
            "confidence": 0.78,
            "need_third_candidate": False,
            "selection_reason": "候选 A 的因果推进更清晰。",
        },
        ensure_ascii=False,
    ),
    TaskType.INIT_STORY_CORE_PREMISE: _MOCK_STORY_CORE_PREMISE,
    TaskType.INIT_STORY_WORLD_RULES: _MOCK_STORY_WORLD_RULES,
    TaskType.INIT_STORY_CONTINUITY_RULES: _MOCK_STORY_CONTINUITY_RULES,
    TaskType.INIT_STORY_THEMES_AND_SYMBOLS: _MOCK_STORY_THEMES_AND_SYMBOLS,
    TaskType.INIT_CHARACTER_BIBLE: _MOCK_CHARACTER_BIBLE,
    TaskType.INIT_CHARACTER_ROSTER: _MOCK_CHARACTER_ROSTER,
    TaskType.INIT_CHARACTER_PROFILE_BATCH: _MOCK_CHARACTER_PROFILE_BATCH,
    TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX: _MOCK_CHARACTER_RELATIONSHIP_MATRIX,
    TaskType.INIT_CHARACTER_ARC_PLAN: _MOCK_CHARACTER_ARC_PLAN,
    TaskType.INIT_KNOWLEDGE_BOUNDARIES: _MOCK_INIT_KNOWLEDGE_BOUNDARIES,
    TaskType.RECONCILE_ENTITIES: _MOCK_RECONCILE_ENTITIES,
    TaskType.BLUEPRINT_ELEMENT_SELECT: _MOCK_BLUEPRINT_ELEMENT_SELECT,
    TaskType.PLAN_OUTLINE: _MOCK_OUTLINE,
    TaskType.INIT_ENTITY_REGISTRY: json.dumps({"entities": []}, ensure_ascii=False),
    TaskType.INIT_NARRATIVE_CONTRACT: json.dumps(
        {
            "world_rules": [],
            "character_arcs": [],
            "plot_threads": [],
            "promise_plan": [],
            "notes": "",
        },
        ensure_ascii=False,
    ),
    TaskType.SYNTHESIZE_INIT_RESEARCH_DOSSIER: json.dumps(
        {
            "summary": "Mock 资料包：无真实联网来源时仅提供占位摘要。",
            "real_world_constraints": [],
            "terminology": [],
            "inspiration_notes": [],
            "uncertainty_notes": ["当前为 mock 响应，不能作为真实资料依据。"],
            "source_refs": [],
            "warnings": ["mock research dossier"],
        },
        ensure_ascii=False,
    ),
    TaskType.GROUND_OUTLINE_RESEARCH: json.dumps(
        {
            "summary": "Mock 大纲资料校准：无真实资料提醒。",
            "global_notes": [],
            "chapter_notes": [],
            "fact_risks": [],
            "terminology": [],
            "source_refs": [],
            "warnings": ["mock outline research grounding"],
        },
        ensure_ascii=False,
    ),
    TaskType.PLAN_INIT_RESEARCH_QUERIES: json.dumps(
        {
            "queries": [
                {
                    "query": "Mock 研究查询",
                    "rationale": "mock 占位查询",
                    "intent": "mock",
                    "priority": "should",
                    "locale": "",
                    "source_preferences": [],
                    "recency_required": False,
                    "risk_if_missing": "",
                }
            ],
            "knowledge_gaps": [],
            "warnings": ["mock research query planning"],
        },
        ensure_ascii=False,
    ),
    TaskType.SYNTHESIZE_MODEL_PRIOR_RESEARCH: json.dumps(
        {
            "notes": ["Mock 模型先验知识：无真实来源时仅提供占位要点。"],
            "terminology": [],
            "uncertainty_notes": ["当前为 mock 响应，不能作为真实资料依据。"],
            "warnings": ["mock model prior research"],
        },
        ensure_ascii=False,
    ),
    TaskType.PLAN_CHAPTER_CONTRACTS: json.dumps({"chapter_contracts": []}, ensure_ascii=False),
    TaskType.DERIVE_INIT_COHERENCE_PROFILE: json.dumps(
        {
            "genre_tags": ["通用长篇"],
            "narrative_modes": ["线性推进"],
            "project_ontology": {
                "domains": ["relationship", "plot", "world_rule"],
                "entity_types": ["character", "location", "item", "concept"],
                "state_axes": ["relationship_status", "knowledge", "promise", "identity"],
                "relationship_axes": ["trust", "alliance", "romance"],
                "payoff_types": ["information", "relationship", "clue"],
                "irreversible_event_markers": ["完成", "揭示", "兑现"],
                "temporal_markers": ["过去", "现在", "未来"],
                "terminology": {},
            },
            "conflict_lens": ["状态顺序", "兑现重复", "不可逆事件"],
            "extraction_guidance": ["抽取状态变化、承诺/兑现、知识揭示和世界规则。"],
            "summary": "Mock 初始化一致性画像。",
        },
        ensure_ascii=False,
    ),
    TaskType.REFINE_INIT_COHERENCE_PROFILE: json.dumps(
        {
            "genre_tags": ["通用长篇"],
            "narrative_modes": ["线性推进"],
            "project_ontology": {
                "domains": ["relationship", "plot", "world_rule"],
                "entity_types": ["character", "location", "item", "concept"],
                "state_axes": ["relationship_status", "knowledge", "promise", "identity"],
                "relationship_axes": ["trust", "alliance", "romance"],
                "payoff_types": ["information", "relationship", "clue"],
                "irreversible_event_markers": ["完成", "揭示", "兑现"],
                "temporal_markers": ["过去", "现在", "未来"],
                "terminology": {},
            },
            "conflict_lens": ["状态顺序", "兑现重复", "不可逆事件"],
            "extraction_guidance": ["结合蓝图补充状态轴和 payoff 识别线索。"],
            "summary": "Mock 蓝图反哺后一致性画像。",
        },
        ensure_ascii=False,
    ),
    TaskType.INIT_COHERENCE_ONTOLOGY: json.dumps(
        {
            "genre_tags": ["通用长篇"],
            "narrative_modes": ["线性推进"],
            "project_ontology": {
                "domains": ["relationship", "plot", "world_rule"],
                "entity_types": ["character", "location", "item", "concept"],
                "state_axes": ["relationship_status", "knowledge", "promise", "identity"],
                "relationship_axes": ["trust", "alliance", "romance"],
                "payoff_types": ["information", "relationship", "clue"],
                "irreversible_event_markers": ["完成", "揭示", "兑现"],
                "temporal_markers": ["过去", "现在", "未来"],
                "terminology": {},
            },
        },
        ensure_ascii=False,
    ),
    TaskType.INIT_COHERENCE_EXTRACTION_GUIDE: json.dumps(
        {"extraction_guidance": ["结合蓝图补充状态轴和 payoff 识别线索。"]},
        ensure_ascii=False,
    ),
    TaskType.INIT_COHERENCE_CONFLICT_RULES: json.dumps(
        {"conflict_lens": ["状态顺序", "兑现重复", "不可逆事件"]},
        ensure_ascii=False,
    ),
    TaskType.INIT_COHERENCE_PAYOFF_RULES: json.dumps(
        {
            "payoff_types": ["information", "relationship", "clue"],
            "irreversible_event_markers": ["完成", "揭示", "兑现"],
            "temporal_markers": ["过去", "现在", "未来"],
            "summary": "Mock 蓝图反哺后一致性画像。",
        },
        ensure_ascii=False,
    ),
    TaskType.EXTRACT_INIT_COHERENCE_CLAIMS: json.dumps(
        {
            "claims": [],
            "coverage_status": "complete",
            "unprocessed_source_refs": [],
            "summary": "Mock 未抽取到冲突性 claims。",
        },
        ensure_ascii=False,
    ),
    TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES: json.dumps(
        {
            "schema_version": "audit_v2",
            "dimension": "init_coherence",
            "verdict": "accept",
            "score": 1.0,
            "issues": [],
            "summary": "Mock 候选冲突裁判通过。",
            "metadata": {"mock": True},
            "source_refs": [],
            "repair_scope": [],
            "preserve": [],
            "change_intent": "",
            "blocked": False,
        },
        ensure_ascii=False,
    ),
    TaskType.ADJUDICATE_BLUEPRINT_COHERENCE: json.dumps(
        {
            "schema_version": "audit_v2",
            "dimension": "blueprint_coherence",
            "verdict": "accept",
            "score": 1.0,
            "issues": [],
            "metadata": {"mock": True},
            "source_refs": [],
            "repair_scope": [],
            "preserve": [],
            "change_intent": "",
            "blocked": False,
            "summary": "Mock 蓝图自洽。",
        },
        ensure_ascii=False,
    ),
    TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS: json.dumps(
        {
            "claims": [],
            "coverage_status": "complete",
            "unprocessed_source_refs": [],
            "summary": "Mock 未抽取到蓝图整体冲突性 claims。",
        },
        ensure_ascii=False,
    ),
    TaskType.ADJUDICATE_OUTLINE_INHERITANCE: json.dumps(
        {
            "schema_version": "audit_v2",
            "dimension": "outline_inheritance",
            "verdict": "accept",
            "score": 1.0,
            "issues": [],
            "metadata": {"mock": True},
            "source_refs": [],
            "repair_scope": [],
            "preserve": [],
            "change_intent": "",
            "blocked": False,
            "summary": "Mock 大纲继承一致。",
        },
        ensure_ascii=False,
    ),
    TaskType.ADJUDICATE_CONTRACT_COHERENCE: json.dumps(
        {
            "verdict": "accept",
            "issues": [],
            "source_refs": [],
            "repair_scope": [],
            "preserve": [],
            "change_intent": "",
            "blocked": False,
            "summary": "Mock 契约自洽。",
        },
        ensure_ascii=False,
    ),
    TaskType.REPAIR_INIT_ARTIFACT_PATCH: json.dumps(
        {"patches": [], "summary": "Mock 无需修复。"},
        ensure_ascii=False,
    ),
    TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS: json.dumps(
        {
            "suggestions": [],
            "repair_scope": [],
            "patches": [],
            "preserve": [],
            "risks": [],
            "summary": "Mock 无需基于梗概增强初始化 artifact。",
        },
        ensure_ascii=False,
    ),
    TaskType.PLAN_OUTLINE_BATCH: json.dumps(
        {
            "chapters": [
                {
                    "chapter_number": 4,
                    "title": "裂缝之外",
                    "goal": "林远探索小镇边界，发现时间裂缝扩散的迹象",
                    "beats_summary": [
                        "林远在小镇东侧发现异常建筑",
                        "与守夜人再次相遇",
                        "时间裂缝短暂扩大",
                    ],
                    "main_plot_points": ["时间裂缝扩散威胁整个小镇"],
                    "subplot_points": [],
                    "subplot_focus": "",
                    "element_focus": [],
                    "pov_character_id": "char_lin_yuan",
                    "pov_character_name": "林远",
                    "pov_character": "林远",
                    "pov_switch": False,
                    "setting": "小镇东侧废墟",
                    "expected_word_count": 900,
                    "involved_character_ids": ["char_lin_yuan"],
                    "required_character_ids": ["char_lin_yuan"],
                    "support_character_ids": [],
                    "involved_character_names": ["林远"],
                    "involved_characters": ["林远"],
                    "cast_plan": {
                        "pov_entity_id": "char_lin_yuan",
                        "required_character_ids": ["char_lin_yuan"],
                        "support_character_ids": [],
                        "mention_only_entity_ids": [],
                        "forbidden_active_character_ids": [],
                    },
                    "emotional_plan": {
                        "subject_entity_id": "char_lin_yuan",
                        "entry_state": "林远带着边界异常的疑问进入废墟。",
                        "pressure_source": "裂缝扩大迫使他重新判断守夜人的警告。",
                        "relationship_choice": "林远必须决定是否与守夜人有限合作。",
                        "turning_emotion": "从戒备转为带条件的信任。",
                        "exit_aftertaste": "合作成立，但裂缝中的人影带来新疑问。",
                        "expression_channels": ["action", "object_detail"],
                    },
                    "scene_design_goals": [
                        "确认时间裂缝扩散的可见证据。",
                        "通过共同应对迫使林远作出关系选择。",
                    ],
                    "notes": "Mock 完整章节设计契约。",
                    "expected_hook": {
                        "hook_type": "mystery",
                        "hook_strength": "strong",
                        "hook_description": "裂缝中出现林远熟悉的人影。",
                    },
                    "expected_payoffs": [
                        {
                            "payoff_type": "clue",
                            "description": "守夜人关于裂缝扩散的警告得到证实。",
                        }
                    ],
                }
            ]
        },
        ensure_ascii=False,
    ),
    TaskType.PLAN_OUTLINE_CONTINUE: json.dumps({"chapters": []}, ensure_ascii=False),
    TaskType.PLAN_CHAPTER: _MOCK_CHAPTER_PLAN,
    TaskType.PLAN_CHAPTER_SCENES: _MOCK_CHAPTER_SCENE_PLAN,
    TaskType.VALIDATE_SCENE_PLAN: _MOCK_SCENE_PLAN_VALIDATION,
    TaskType.BRIDGE_CHAPTER: _MOCK_BRIDGE,
    TaskType.DRAFT_CHAPTER: _MOCK_DRAFT,
    TaskType.DRAFT_SCENE: _MOCK_DRAFT,
    TaskType.EDIT_CHAPTER: _MOCK_EDIT,
    TaskType.EXTRACT_CANON: _MOCK_CANON_DELTA,
    TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT: _MOCK_EXTRACT_CHAPTER_SUMMARY_EXIT,
    TaskType.EXTRACT_CANON_DELTA: _MOCK_EXTRACT_CANON_DELTA_FRAGMENT,
    TaskType.EXTRACT_CREATIVE_REPORT: _MOCK_EXTRACT_CREATIVE_REPORT,
    TaskType.EXTRACT_CHARACTER_STATE_DELTAS: _MOCK_EXTRACT_CHARACTER_STATE_DELTAS,
    TaskType.EXTRACT_RELATIONSHIP_DELTAS: _MOCK_EXTRACT_RELATIONSHIP_DELTAS,
    TaskType.EXTRACT_PLOT_THREAD_DELTAS: _MOCK_EXTRACT_PLOT_THREAD_DELTAS,
    TaskType.EXTRACT_KNOWLEDGE_DELTAS: _MOCK_EXTRACT_KNOWLEDGE_DELTAS,
    TaskType.EXTRACT_CANDIDATE_STATE_DELTAS: _MOCK_CANDIDATE_STATE_DELTAS,
    TaskType.ADJUDICATE_STATE_DELTA: _MOCK_STATE_DELTA_ADJUDICATION,
    TaskType.ADJUDICATE_CONTRACT_COMPLETION: _MOCK_CONTRACT_COMPLETION_ADJUDICATION,
    TaskType.ADJUDICATE_FACT_CONFLICT: _MOCK_STATE_DELTA_ADJUDICATION,
    TaskType.ADJUDICATE_FINAL_STATE: _MOCK_FINAL_STATE_ADJUDICATION,
    TaskType.CHECK_ALIGNMENT: _MOCK_ALIGNMENT,
    TaskType.ELEMENT_PROGRESS_ARBITER: json.dumps(
        {"status": "weak", "confidence": 0.75, "reason": "Mock: evidence is partial."},
        ensure_ascii=False,
    ),
    TaskType.CHECK_CHAPTER: _MOCK_CHECK_CHAPTER,
    TaskType.CHECK_CONTINUITY: _MOCK_CONTINUITY_REPORT,
    TaskType.VALIDATE_CAUSAL: _MOCK_CAUSAL_REPORT,
    TaskType.PATCH_CHAPTER: _MOCK_PATCH_CHAPTER,
    TaskType.REPAIR_CONTINUITY: _MOCK_CONTINUITY_REPAIR,
    TaskType.REPAIR_STRATEGY_DIAGNOSE: _MOCK_REPAIR_STRATEGY_DIAGNOSE,
    TaskType.REPAIR_KNOWLEDGE_BOUNDARY: _MOCK_REPAIR_KNOWLEDGE_BOUNDARY,
    TaskType.PROFILE_STYLE: _MOCK_PROFILE_STYLE,
    TaskType.PROFILE_STRUCTURE: _MOCK_PROFILE_STRUCTURE,
    TaskType.DERIVE_EDITORIAL_CONTRACT: _MOCK_EDITORIAL_CONTRACT,
    TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES: _MOCK_EDITORIAL_CHARACTER_VOICES,
    TaskType.DERIVE_EDITORIAL_STRUCTURE: _MOCK_EDITORIAL_STRUCTURE,
    TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS: _MOCK_EDITORIAL_STYLE_CONSTRAINTS,
    TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES: _MOCK_EDITORIAL_ELEMENT_DIRECTIVES,
    TaskType.EXTRACT_EXPRESSION_OBSERVATIONS: _MOCK_EXPRESSION_OBSERVATIONS,
    TaskType.CHECK_EDITORIAL: _MOCK_EDITORIAL_AUDIT,
    TaskType.BOOK_EDITORIAL_AUDIT: _MOCK_EDITORIAL_AUDIT,
    TaskType.BOOK_EDITORIAL_STRUCTURE_AUDIT: _MOCK_EDITORIAL_AUDIT,
    TaskType.BOOK_EDITORIAL_VOICE_AUDIT: _MOCK_EDITORIAL_AUDIT,
    TaskType.BOOK_EDITORIAL_LANGUAGE_AUDIT: _MOCK_EDITORIAL_AUDIT,
    TaskType.BOOK_EDITORIAL_THEME_SYMBOL_AUDIT: _MOCK_EDITORIAL_AUDIT,
    TaskType.BOOK_EDITORIAL_ELEMENT_AUDIT: _MOCK_EDITORIAL_AUDIT,
    TaskType.EVALUATE_READING_POWER: _MOCK_READING_POWER,
    TaskType.VOLUME_AUDIT: _MOCK_VOLUME_AUDIT,
    TaskType.CONTEXT_COMPRESS: _MOCK_CONTEXT_COMPRESS,
    TaskType.ADAPTIVE_COMPRESS: _MOCK_ADAPTIVE_COMPRESS,
    TaskType.VERIFY_COMPRESSION: _MOCK_VERIFY_COMPRESSION,
    TaskType.PLOT_GUARD_JUDGE: _MOCK_PLOT_GUARD_JUDGE,
    TaskType.GUARD_CONSTRAINT_CHECK: _MOCK_GUARD_CONSTRAINT_CHECK,
    TaskType.GENERATE_CONFIG: _MOCK_GENERATE_CONFIG,
    TaskType.ADJUST_OUTLINE: _MOCK_ADJUST_OUTLINE,
    TaskType.BOOK_CONSISTENCY: _MOCK_BOOK_CONSISTENCY,
    TaskType.BOOK_CONSISTENCY_NAMING: _MOCK_BOOK_CONSISTENCY_ISSUES,
    TaskType.BOOK_CONSISTENCY_TIMELINE: _MOCK_BOOK_CONSISTENCY_ISSUES,
    TaskType.BOOK_CONSISTENCY_WORLD_RULE: _MOCK_BOOK_CONSISTENCY_ISSUES,
    TaskType.BOOK_CONSISTENCY_CHARACTER_STATE: _MOCK_BOOK_CONSISTENCY_ISSUES,
    TaskType.BOOK_CONSISTENCY_PLOT_THREAD: _MOCK_BOOK_CONSISTENCY_ISSUES,
    TaskType.BOOK_CONSISTENCY_NARRATIVE_DRIFT: _MOCK_BOOK_CONSISTENCY_ISSUES,
    TaskType.BOOK_CONSISTENCY_VERIFY: _MOCK_BOOK_CONSISTENCY_VERIFY,
    TaskType.EXTRACT_MOTIFS: _MOCK_EXTRACT_MOTIFS,
    TaskType.CRITIC_CONTINUITY: _MOCK_CRITIC_CONTINUITY,
    TaskType.CRITIC_CHARACTER: _MOCK_CRITIC_CHARACTER,
    TaskType.CRITIC_CAUSAL: _MOCK_CRITIC_CAUSAL,
    TaskType.CRITIC_STRENGTHS: _MOCK_CRITIC_STRENGTHS,
    TaskType.SUMMARIZE_CHAPTER: _MOCK_SUMMARY,
    TaskType.SUMMARIZE_VOLUME: _MOCK_SUMMARY,
    TaskType.SUMMARIZE_ARC: _MOCK_SUMMARY,
    TaskType.SUMMARIZE_SCENE: _MOCK_SUMMARY,
    TaskType.POLISH_CONFIG: _MOCK_ENRICHED_SPEC,
    TaskType.POLISH_OUTLINE: _MOCK_POLISH_OUTLINE,
    TaskType.REVIEW_FUTURE_OUTLINE: json.dumps(
        {
            "selected": "original",
            "creative_gain": "Mock review preserves the existing plan.",
            "confidence": 0.0,
            **{
                key: {"passed": False, "evidence": "Mock mode cannot verify creative improvement."}
                for key in (
                    "facts",
                    "user_intent",
                    "motivation",
                    "causality",
                    "promises",
                    "contracts",
                )
            },
        }
    ),
    TaskType.POLISH_SUBPLOT: _MOCK_POLISH_SUBPLOT,
    TaskType.MACRO_GUARD_AUDIT: _MOCK_MACRO_GUARD_AUDIT,
    TaskType.TTS_BUILD_NARRATOR_PROFILE: json.dumps(
        {
            "voice_type": "克制的中性旁白",
            "base_speed": 1.0,
            "emotional_range": "moderate",
            "narration_distance": "medium",
            "style_keywords": ["清晰", "克制"],
        },
        ensure_ascii=False,
    ),
    TaskType.TTS_GENERATE_DUBBING_SCRIPT: json.dumps(
        {
            "segments": [],
            "bgm_suggestions": [],
            "sfx_cues": [],
            "soundscapes": [],
            "scene_transitions": [],
        },
        ensure_ascii=False,
    ),
    TaskType.TTS_REWRITE_SPOKEN_TEXT: json.dumps(
        {"rewrites": []},
        ensure_ascii=False,
    ),
    TaskType.TTS_EMOTION_LABEL: json.dumps(
        {"emotions": []},
        ensure_ascii=False,
    ),
    TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS: json.dumps(
        {
            "decisions": [],
            "summary": "Mock script-segment adjudication.",
        },
        ensure_ascii=False,
    ),
    TaskType.TTS_REVIEW_DUBBING_SCRIPT: json.dumps(
        {
            "decisions": [],
            "reviewed_segment_count": 0,
            "overall_verdict": "passed",
            "summary": "Mock dubbing-script review.",
        },
        ensure_ascii=False,
    ),
    TaskType.TTS_ANALYZE_DUBBING_STYLE: json.dumps(
        {
            "language": "zh",
            "confidence": 0.72,
            "narration_traits": ["克制、清晰、以画面落点收句"],
            "dialogue_traits": ["短轮次、潜台词优先"],
            "rhythm_rules": ["按自然呼吸拆分意群"],
            "pause_rules": ["只在语义换挡处使用停顿"],
            "performance_direction_rules": ["用气息、力度、距离描述表演"],
            "sound_design_rules": ["环境底床连续，剧情音效稀疏而准确"],
            "forbidden_tendencies": ["不得复制参考稿措辞或事实"],
        },
        ensure_ascii=False,
    ),
    TaskType.TTS_ADJUDICATE_VOICE_MATCH: json.dumps(
        {
            "decisions": [],
            "summary": "Mock voice-match review.",
        },
        ensure_ascii=False,
    ),
    TaskType.TTS_SOUND_DESIGN: json.dumps(
        {
            "sfx_cues": [],
            "bgm_needs": [],
            "soundscapes": [],
            "scene_transitions": [],
        },
        ensure_ascii=False,
    ),
    TaskType.ADAPT_SCREENPLAY: json.dumps(
        {
            "title": "时间裂缝·试映稿",
            "scenes": [
                {
                    "scene_id": "scene_001",
                    "source_chapter": 1,
                    "location": "雾霭小镇钟楼外",
                    "time": "雨夜",
                    "dramatic_goal": "让林远接过来自未来的线索并决定行动",
                    "visual_action": "怀表在掌心逆向跳动，钟楼阴影切过湿漉漉的石板路。",
                    "adaptation_boundary": "保留小说中的怀表、信件与行动代价，不提前揭示身份真相。",
                }
            ],
        },
        ensure_ascii=False,
    ),
    TaskType.COMPLIANCE_CHECK: json.dumps(
        {
            "findings": [],
            "summary": "Mock 审查完成：未发现需要阻断当前试映稿的合规问题。",
        },
        ensure_ascii=False,
    ),
    TaskType.VISION_QC_SCORE: json.dumps(
        {
            "dimensions": [
                {
                    "dimension": "novel_alignment",
                    "score": 8.6,
                    "evidence": "镜头动作保留怀表、信件和人物主动选择三个来源锚点。",
                },
                {
                    "dimension": "visual_continuity",
                    "score": 8.2,
                    "evidence": "雨夜、钟楼与怀表反应在镜头间保持连续。",
                },
            ],
            "overall_score": 8.4,
            "summary": "Mock 视觉质量通过，来源锚点和镜头连续性均有明确证据。",
        },
        ensure_ascii=False,
    ),
    TaskType.DRAMA_SERIES_PLAN: json.dumps(
        {
            "title": "时间裂缝",
            "total_episodes": 3,
            "three_acts": ["失忆者入局", "裂缝规则与身份压力升级", "承担代价并修复时间"],
            "paywall_beats": [
                {
                    "episode_number": 1,
                    "hook": "林远发现信件笔迹属于自己，怀表却开始逆行。",
                    "source_chapters": [1],
                }
            ],
            "thrill_matrix": [{"episode_number": 1, "suspense": 8, "emotion": 6, "payoff": 4}],
            "waveform_stages": [
                {"stage": "entry", "episodes": [1], "purpose": "建立规则、人物目标与核心悬念"}
            ],
            "antagonist_system": [
                {"force": "失控的时间裂缝", "pressure": "每次干预都会吞噬可识别记忆"}
            ],
        },
        ensure_ascii=False,
    ),
    TaskType.EPISODE_OUTLINE: json.dumps(
        {
            "episodes": [
                {
                    "episode_number": 1,
                    "source_chapters": [1],
                    "goal": "林远确认裂缝真实存在并主动进入图书馆",
                    "turn": "信件笔迹与自己的笔迹完全一致",
                    "cliffhanger": "怀表指针开始逆行",
                }
            ]
        },
        ensure_ascii=False,
    ),
    TaskType.EPISODE_SCREENPLAY: json.dumps(
        {
            "episode_number": 1,
            "scenes": [
                {
                    "scene_id": "ep01_scene01",
                    "source_chapters": [1],
                    "location": "钟楼外",
                    "action": "林远核对信件笔迹，怀表在雷声后逆向跳动。",
                    "end_state": "林远决定前往废弃图书馆。",
                }
            ],
        },
        ensure_ascii=False,
    ),
    TaskType.FILM_SHOT_LAYOUT: json.dumps(
        {
            "scenes": [
                {
                    "scene_id": "ep01_scene01",
                    "shots": [
                        {
                            "shot_id": "shot_001",
                            "size": "close_up",
                            "subject": "逆行的铜质怀表",
                            "continuity_anchor": "雨水、午夜刻度、林远右手",
                        }
                    ],
                }
            ]
        },
        ensure_ascii=False,
    ),
    TaskType.COMIC_PANEL_LAYOUT: json.dumps(
        {
            "pages": [
                {
                    "page_number": 1,
                    "source_chapters": [1],
                    "panels": [
                        {
                            "panel_number": 1,
                            "visual": "雨夜钟楼下，林远摊开信件，怀表指针逆行。",
                            "story_function": "建立主角、核心道具与时间异常悬念",
                        }
                    ],
                }
            ]
        },
        ensure_ascii=False,
    ),
    TaskType.KNOWLEDGE_BOUNDARY_AUDIT: json.dumps(
        {"verdict": "pass", "issues": []},
        ensure_ascii=False,
    ),
    TaskType.REPAIR_SEMANTIC_VERIFY: _MOCK_REPAIR_SEMANTIC_VERIFY,
    TaskType.SUMMARY_DRIFT_CHECK: _MOCK_SUMMARY_DRIFT_CHECK,
    TaskType.AUDIT_POV_DRIFT: _MOCK_AUDIT_POV_DRIFT,
    TaskType.REPAIR_ADJUDICATED_ISSUE: _MOCK_EDIT,
    TaskType.HUMANIZE_SCAN: json.dumps(
        {
            "source_text_hash": "abc123def456",
            "chapter_number": 1,
            "total_hits": 0,
            "hits_by_category": {},
            "critical_hits": 0,
            "pattern_hits": [],
            "humanize_score": 9.5,
            "summary": "Mock humanize scan result.",
        },
        ensure_ascii=False,
    ),
}


class MockAdapter(ProviderAdapter):
    """Deterministic adapter that returns pre-baked responses for every TaskType."""

    @property
    def provider_name(self) -> str:
        return "mock"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        start = time.monotonic()

        if request.task_type == TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES:
            content = self._generate_creative_direction_candidates_response(request)
        elif request.task_type in (TaskType.PLAN_OUTLINE_BATCH, TaskType.PLAN_OUTLINE_CONTINUE):
            content = self._generate_outline_batch_response(request)
        elif request.task_type == TaskType.PLAN_CHAPTER_CONTRACTS:
            content = self._generate_chapter_contracts_response(request)
        elif request.task_type == TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES:
            content = self._generate_editorial_character_voices_response(request)
        elif request.task_type == TaskType.DERIVE_EDITORIAL_CONTRACT:
            content = self._generate_editorial_contract_response(request)
        elif request.task_type == TaskType.ADJUDICATE_ENTITY_REFERENCES:
            content = self._generate_entity_reference_adjudication_response(request)
        elif request.task_type == TaskType.EXTRACT_RELATIONSHIP_DELTAS:
            content = self._generate_relationship_deltas_response(request)
        elif request.task_type in (TaskType.PLAN_CHAPTER, TaskType.PLAN_CHAPTER_SCENES):
            content = self._generate_chapter_plan_response(request)
        elif request.task_type in (TaskType.DRAFT_CHAPTER, TaskType.DRAFT_SCENE):
            content = self._generate_mock_prose_response(request, polished=False)
        elif request.task_type in (TaskType.WAVE_CHAPTER, TaskType.POLISH_CHAPTER):
            content = self._generate_mock_prose_response(request, polished=True)
        elif request.task_type == TaskType.PLAN_OUTLINE:
            # Blueprint-only response (no chapters)
            content = self._generate_outline_response(request)
        elif request.task_type == TaskType.POLISH_OUTLINE and self._is_title_repair_request(
            request
        ):
            content = self._generate_outline_title_repair_response(request)
        else:
            content = _RESPONSES.get(request.task_type, '{"ok": true}')

        elapsed = (time.monotonic() - start) * 1000

        mock_model = request.model_id or "mock-model"
        prompt_tokens = count_message_tokens(
            request.messages,
            provider="mock",
            model_id=mock_model,
        ).tokens
        completion_tokens = count_text_tokens(
            content,
            provider="mock",
            model_id=mock_model,
        ).tokens
        structured_plan = build_structured_output_request_plan(
            request,
            provider="mock",
            model_id=mock_model,
            dialect=StructuredOutputDialect.OPENAI_RESPONSE_FORMAT,
        )
        return ModelResponse(
            content=content,
            model_id=mock_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            latency_ms=round(elapsed, 2),
            cost_usd=estimate_cost(mock_model, prompt_tokens, completion_tokens),
            **structured_plan.response_fields(),
        )

    @staticmethod
    def _generate_creative_direction_candidates_response(request: ModelRequest) -> str:
        """Mirror request intent ids so mock runs exercise the real selection path."""

        payload = json.loads(_RESPONSES[TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES])
        prompt = "\n".join(str(message.get("content") or "") for message in request.messages)
        match = re.search(r'"immutable_intent_ids"\s*:\s*(\[[^\]]*\])', prompt)
        intent_ids: list[str] = []
        if match:
            try:
                parsed = json.loads(match.group(1))
            except json.JSONDecodeError:
                parsed = []
            intent_ids = [str(item) for item in parsed if str(item).strip()]
        requested_count = 1 if ("提出 1 个" in prompt or "Produce 1 " in prompt) else 2
        payload["candidates"] = payload["candidates"][:requested_count]
        if requested_count == 1 and payload["candidates"]:
            payload["candidates"][0]["candidate_id"] = "candidate_c"
        for candidate in payload["candidates"]:
            candidate["preserved_intent_ids"] = intent_ids
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _generate_chapter_plan_response(request: ModelRequest) -> str:
        """Echo bounded chapter duties into an otherwise deterministic plan.

        A static mock plan is useful for repeatable offline tests, but it must
        not pretend to satisfy an arbitrary chapter's literary contract.  The
        rendered planning prompt already contains only the bounded duty slice,
        so the mock projects those duties into existing scene fields.  This
        keeps semantic hard gates meaningful without teaching the mock about
        StoryKernel or raw project artifacts.
        """

        template = (
            _MOCK_CHAPTER_SCENE_PLAN
            if request.task_type == TaskType.PLAN_CHAPTER_SCENES
            else _MOCK_CHAPTER_PLAN
        )
        payload = json.loads(template)
        plan = payload.get("scene_plan") if isinstance(payload.get("scene_plan"), dict) else payload
        prompt_text = "\n".join(message.get("content", "") for message in request.messages)

        evidence: list[str] = []
        prefixes = (
            "- 情节：",
            "- 必达事件：",
            "- 必达推进：",
            "- 完成标准：",
            "- 出口目标：",
        )
        field_labels = ("必达事件=", "必达推进=", "出口=", "必达事件：", "必达推进：")
        for raw_line in prompt_text.splitlines():
            line = raw_line.strip()
            if not line.startswith(prefixes):
                continue
            projected = line[2:].strip()
            for label in field_labels:
                projected = projected.replace(label, "")
            projected = projected.strip("；： ")
            if projected and projected not in evidence:
                evidence.append(projected)

        scenes = plan.get("scene_intents") if isinstance(plan, dict) else None
        if evidence and isinstance(scenes, list) and scenes and isinstance(scenes[0], dict):
            duty_text = "；".join(evidence)
            first_scene = scenes[0]
            first_scene["purpose"] = "；".join(
                item for item in (str(first_scene.get("purpose") or ""), duty_text) if item
            )
            first_scene["required_outcome"] = "；".join(
                item for item in (str(first_scene.get("required_outcome") or ""), duty_text) if item
            )
            if "owned_events" in first_scene:
                first_scene["owned_events"] = [duty_text]
            transitions = plan.get("required_state_transitions")
            if isinstance(transitions, list):
                transitions.extend(item for item in evidence if item not in transitions)

        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _generate_entity_reference_adjudication_response(request: ModelRequest) -> str:
        """Return schema-valid entity decisions for offline/init tests.

        The mock intentionally does not infer aliases.  It only echoes the first
        candidate supplied by the caller, preserving the same contract shape as
        the production adjudicator while keeping test behavior deterministic.

        The prompt input block is parsed as JSON (Jinja ``tojson`` sorts keys
        alphabetically, so position-based regexes are not reliable).
        """
        import re as _re

        prompt_text = "\n".join(m.get("content", "") for m in request.messages)
        mentions: list[dict[str, Any]] = []
        canonical_by_id: dict[str, str] = {}

        blocks = _re.findall(r"```json\s*(\{.*)\s*```", prompt_text, flags=_re.DOTALL)
        if blocks:
            try:
                payload = json.loads(blocks[-1])
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                mentions = [
                    item for item in (payload.get("mentions") or []) if isinstance(item, dict)
                ]
                for card in (payload.get("evidence_pack") or {}).get("evidence_cards", []) or []:
                    if not isinstance(card, dict):
                        continue
                    canonical_name = str(card.get("canonical_name") or "")
                    for entity_id in card.get("entity_ids", []) or []:
                        if entity_id and canonical_name:
                            canonical_by_id.setdefault(str(entity_id), canonical_name)

        decisions: list[dict[str, Any]] = []
        for item in mentions:
            mention = str(item.get("mention") or "").strip()
            if not mention:
                continue
            candidate_ids = [
                str(candidate)
                for candidate in (item.get("candidate_entity_ids") or [])
                if candidate
            ]
            if candidate_ids:
                decisions.append(
                    {
                        "mention": mention,
                        "verdict": "resolved",
                        "selected_entity_id": candidate_ids[0],
                        "selected_canonical_name": canonical_by_id.get(
                            candidate_ids[0], candidate_ids[0]
                        ),
                        "rationale": "Mock selects the first bounded candidate.",
                        "candidate_entity_ids": candidate_ids,
                        "evidence_refs": [],
                        "requires_independent_review": False,
                    }
                )
            else:
                decisions.append(
                    {
                        "mention": mention,
                        "verdict": "insufficient_evidence",
                        "selected_entity_id": "",
                        "selected_canonical_name": "",
                        "rationale": "Mock received no candidate entity.",
                        "candidate_entity_ids": [],
                        "evidence_refs": [],
                        "requires_independent_review": False,
                    }
                )
        return json.dumps(
            {
                "decisions": decisions,
                "summary": "Mock entity-reference adjudication.",
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _extract_relationship_character_enum(request: ModelRequest) -> list[str]:
        schema = request.response_json_schema or {}
        try:
            raw_enum = schema["properties"]["relationship_deltas"]["items"]["properties"][
                "relationship"
            ]["properties"]["characters"]["items"]["enum"]
        except (KeyError, TypeError):
            raw_enum = []
        names: list[str] = []
        for item in raw_enum if isinstance(raw_enum, (list, tuple)) else []:
            name = str(item or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    @classmethod
    def _generate_relationship_deltas_response(cls, request: ModelRequest) -> str:
        """Keep deterministic mock deltas inside the runtime entity enum."""

        names = cls._extract_relationship_character_enum(request)
        if not names:
            return _MOCK_EXTRACT_RELATIONSHIP_DELTAS
        if len(names) < 2:
            return json.dumps({"relationship_deltas": []}, ensure_ascii=False)

        first, second = names[:2]
        pair_id = f"{first}__{second}"
        relationship_delta = {
            "pair_id": pair_id,
            "change_summary": f"{first}与{second}的关系在本章发生变化",
            "relationship": {
                "pair_id": pair_id,
                "characters": [first, second],
                "public_status": "彼此关注",
                "trust": 0.42,
                "tension": 0.36,
                "dependency": 0.2,
                "last_shift_event": "本章事件改变了双方认知",
                "last_updated_chapter": 1,
                "notes": "mock runtime-enum projection",
            },
        }
        return json.dumps({"relationship_deltas": [relationship_delta]}, ensure_ascii=False)

    @staticmethod
    def _requested_prose_target_chars(request: ModelRequest, default: int = 3000) -> int:
        import re as _re

        prompt_text = "\n".join(m.get("content", "") for m in request.messages)
        patterns = (
            r'"target_word_count"\s*:\s*(\d+)',
            r'"expected_word_count"\s*:\s*(\d+)',
            r"target_word_count\s*[=:：]\s*(\d+)",
            r"expected_word_count\s*[=:：]\s*(\d+)",
            r"目标字数\s*[=:：]\s*(\d+)",
            r"预期字数\s*[=:：]\s*(\d+)",
        )
        for pattern in patterns:
            match = _re.search(pattern, prompt_text)
            if match:
                try:
                    return min(5000, max(800, int(match.group(1))))
                except (TypeError, ValueError):
                    return default
        return default

    @classmethod
    def _generate_mock_prose_response(cls, request: ModelRequest, *, polished: bool) -> str:
        target = cls._requested_prose_target_chars(request)
        base = _MOCK_EDIT if polished else _MOCK_DRAFT
        # The deterministic exit-state fixture carries this obligation into
        # chapter 2, so the deterministic prose must visibly land it as a real
        # model following Plan. This keeps the multi-chapter mock flow honest.
        base = (
            base + "\n\n林远把怀表贴近墙上的裂缝，指针立即随着裂光同步逆转。"
            "他反复移开又靠近，终于确认怀表与裂缝之间确实存在联系。"
        )
        if len(base) >= int(target * 0.92):
            return base

        extension_templates = (
            "林远没有立刻回答。他把怀表翻到掌心，听见细小的齿轮声像被雾气裹住。"
            "老守夜人站在钟楼的阴影里，只给出一个方向，却没有给出解释。林远因此明白，"
            "这一次他不能再等别人把答案递到面前，只能先确认脚下的路是否仍然真实。",
            "他沿着湿冷的石阶往前走，墙面上的水痕在灯下缓慢下坠。每一道痕迹都像被擦掉的记忆，"
            "留下形状，却不给出来源。林远停下脚步，把信纸重新折好，决定先保护这条线索，"
            "再去判断守夜人到底是在引路，还是在把他推向另一个陷阱。",
            "图书馆的门缝里透出陈旧纸页的味道。林远伸手推门时，指节因为寒意微微发紧。"
            "他想起自己醒来后说过的每一句话，发现其中没有一句能证明过去。这个念头让他沉默下来，"
            "也让他第一次把恐惧压成行动：进去，找到裂缝，至少先知道自己失去了什么。",
            "钟声在远处落下，雾气短暂散开又合拢。林远回头看见守夜人的身影被街灯切成两半，"
            "一半像警告，一半像请求。他没有追问，因为追问只会换来更多含混的答案。"
            "他把选择落在脚步上，向图书馆深处走去，让沉默替自己保留最后一点戒备。",
            "当怀表指针轻轻逆转时，林远的呼吸停了一瞬。那不是奇观，更像某种迟来的证明："
            "他确实被卷入了一条有人提前布置好的路。可证明并没有带来安全，反而让每一步都更重。"
            "他握紧怀表，在心里把守夜人的话、信纸的墨迹和图书馆的裂光一一对上。",
        )
        paragraphs = [base]
        index = 0
        while sum(len(item) for item in paragraphs) < int(target * 0.95):
            paragraphs.append(extension_templates[index % len(extension_templates)])
            index += 1
        return "\n\n".join(paragraphs)

    @staticmethod
    def _requested_total_chapters(request: ModelRequest, default: int = 3) -> int:
        """Best-effort extraction of requested total chapters from a prompt."""
        import re as _re

        prompt_text = "\n".join(m.get("content", "") for m in request.messages)
        patterns = (
            r'"total_chapters"\s*:\s*(\d+)',
            r"total_chapters\s*[=:：]\s*(\d+)",
            r"总章数\s*[=:：]?\s*(\d+)",
            r"总章节数\s*[=:：]?\s*(\d+)",
            r"共\s*(\d+)\s*章",
        )
        for pattern in patterns:
            match = _re.search(pattern, prompt_text)
            if match:
                try:
                    return max(1, int(match.group(1)))
                except (TypeError, ValueError):
                    return default
        return default

    @staticmethod
    def _is_title_repair_request(request: ModelRequest) -> bool:
        prompt_text = "\n".join(m.get("content", "") for m in request.messages)
        return "标题补救模式" in prompt_text or "title_repair" in prompt_text

    @staticmethod
    def _generate_outline_title_repair_response(request: ModelRequest) -> str:
        import re as _re

        prompt_text = "\n".join(m.get("content", "") for m in request.messages)
        numbers = [
            int(match) for match in _re.findall(r'"chapter_number"\s*:\s*(\d+)', prompt_text)
        ]
        if not numbers:
            numbers = [1]
        chapters = [
            {"chapter_number": number, "title": f"章名{number}"} for number in sorted(set(numbers))
        ]
        return json.dumps(
            {"adjusted_chapters": chapters, "polish_suggestions": []},
            ensure_ascii=False,
        )

    @classmethod
    def _generate_outline_response(cls, request: ModelRequest) -> str:
        """Generate a mock blueprint that matches the requested chapter count."""
        total_chapters = cls._requested_total_chapters(request)
        payload = json.loads(_MOCK_OUTLINE)
        if total_chapters > 3:
            payload["synopsis"] = (
                "失忆青年林远在时间异常的雾霭小镇中追寻真相，经历多次时间回溯，"
                "最终发现守夜老人就是未来的自己。"
            )
            if payload.get("narrative_phases"):
                payload["narrative_phases"][-1]["chapter_end"] = total_chapters
            for arc in payload.get("character_arcs", []) or []:
                for milestone in arc.get("milestones", []) or []:
                    if milestone.get("chapter_end") == 3:
                        milestone["chapter_end"] = total_chapters
            for subplot in payload.get("subplot_plan", []) or []:
                if int(subplot.get("resolution_chapter", 0) or 0) == 3:
                    subplot["resolution_chapter"] = total_chapters
            for suspense in payload.get("suspense_schedule", []) or []:
                if int(suspense.get("resolve_chapter", 0) or 0) == 3:
                    suspense["resolve_chapter"] = total_chapters
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def _generate_editorial_contract_response(cls, request: ModelRequest) -> str:
        """Generate a mock editorial contract that fits the requested book length."""
        total_chapters = cls._requested_total_chapters(request, default=12)
        payload = json.loads(_MOCK_EDITORIAL_CONTRACT)
        if total_chapters > 1:
            expected_aftermath = max(1, min(3, total_chapters // 5))
            main_climax = max(1, min(total_chapters - 1, total_chapters - expected_aftermath))
        else:
            expected_aftermath = 0
            main_climax = 1
        for marker in payload.get("climax_markers", []) or []:
            if marker.get("climax_type") == "main":
                marker["chapter_number"] = main_climax
                marker["expected_aftermath_chapters"] = expected_aftermath
        if isinstance(payload.get("denouement_budget"), dict):
            payload["denouement_budget"]["expected_chapters"] = expected_aftermath
        for step in payload.get("revelation_ladder", []) or []:
            target = int(step.get("target_chapter", 1) or 1)
            step["target_chapter"] = max(1, min(total_chapters, target))
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _requested_words_per_chapter(request: ModelRequest, default: int = 3000) -> int:
        import re as _re

        prompt_text = "\n".join(m.get("content", "") for m in request.messages)
        patterns = (
            r'"words_per_chapter"\s*:\s*(\d+)',
            r"words_per_chapter\s*[=:：]\s*(\d+)",
            r"每章字数\s*[=:：]?\s*(\d+)",
        )
        for pattern in patterns:
            match = _re.search(pattern, prompt_text)
            if match:
                try:
                    return max(1000, int(match.group(1)))
                except (TypeError, ValueError):
                    return default
        return default

    @staticmethod
    def _compact_outline_phrase(value: Any, *, max_chars: int = 12) -> str:
        import re as _re

        text = " ".join(str(value or "").split()).strip()
        text = _re.sub(r"[\"'`{}\[\]，,。；;：:、！？!?\n\r]", "", text)
        text = text.strip()
        return text[:max_chars] if text else ""

    @classmethod
    def _extract_design_matrix_rows(
        cls,
        prompt_text: str,
    ) -> tuple[dict[str, str], dict[int, dict[str, Any]]]:
        decoder = json.JSONDecoder()
        entity_names: dict[str, str] = {}
        catalog_marker = "### Entity Catalog"
        catalog_index = prompt_text.find(catalog_marker)
        if catalog_index >= 0:
            fragment = prompt_text[catalog_index + len(catalog_marker) :]
            start = fragment.find("[")
            if start >= 0:
                try:
                    catalog, _ = decoder.raw_decode(fragment[start:])
                except json.JSONDecodeError:
                    catalog = []
                for item in catalog if isinstance(catalog, list | tuple) else []:
                    if not isinstance(item, dict):
                        continue
                    entity_id = str(item.get("entity_id") or "").strip()
                    name = str(item.get("name") or item.get("display_name") or "").strip()
                    if entity_id and name:
                        entity_names[entity_id] = name

        rows: dict[int, dict[str, Any]] = {}
        current_number: int | None = None
        for raw_line in prompt_text.splitlines():
            line = raw_line.strip()
            if line.startswith("- 第") and line.endswith("章："):
                number_text = line.removeprefix("- 第").removesuffix("章：").strip()
                try:
                    current_number = int(number_text)
                except ValueError:
                    current_number = None
                if current_number is not None:
                    rows.setdefault(current_number, {"chapter_number": current_number})
                continue
            if current_number is None or not line.startswith("- ") or "=" not in line:
                continue
            key, raw_value = line[2:].split("=", 1)
            key = key.strip()
            try:
                rows[current_number][key] = json.loads(raw_value)
            except json.JSONDecodeError:
                rows[current_number][key] = raw_value.strip()
        return entity_names, rows

    @staticmethod
    def _chapter_active_ids(cast_plan: dict[str, Any]) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for raw in [
            cast_plan.get("pov_entity_id"),
            *(cast_plan.get("required_character_ids") or []),
            *(cast_plan.get("support_character_ids") or []),
        ]:
            entity_id = str(raw or "").strip()
            if entity_id and entity_id not in seen:
                ids.append(entity_id)
                seen.add(entity_id)
        return ids

    @classmethod
    def _mock_outline_chapter_from_design(
        cls,
        *,
        chapter_number: int,
        row: dict[str, Any] | None,
        entity_names: dict[str, str],
        words_per_chapter: int,
    ) -> dict[str, Any]:
        row = row or {}
        raw_plot_duties = row.get("plot_duties")
        plot_duties = raw_plot_duties if isinstance(raw_plot_duties, list) else []
        raw_hook_payoff_duties = row.get("hook_payoff_duties")
        hook_payoff_duties = (
            raw_hook_payoff_duties if isinstance(raw_hook_payoff_duties, list) else []
        )
        raw_scene_design_goals = row.get("scene_design_goals")
        scene_design_goals = (
            raw_scene_design_goals if isinstance(raw_scene_design_goals, list) else []
        )
        raw_cast_plan = row.get("cast_plan")
        cast_plan = cast(dict[str, Any], raw_cast_plan) if isinstance(raw_cast_plan, dict) else {}
        raw_emotional_plan = row.get("emotional_plan")
        emotional_plan = (
            cast(dict[str, Any], raw_emotional_plan) if isinstance(raw_emotional_plan, dict) else {}
        )

        if not cast_plan:
            cast_plan = {
                "pov_entity_id": "char_林远",
                "required_character_ids": ["char_林远"],
                "support_character_ids": [],
                "mention_only_entity_ids": [],
                "forbidden_active_character_ids": [],
            }
            entity_names.setdefault("char_林远", "林远")
        active_ids = cls._chapter_active_ids(cast_plan)
        pov_id = str(
            cast_plan.get("pov_entity_id") or (active_ids[0] if active_ids else "")
        ).strip()
        pov_name = entity_names.get(pov_id, pov_id or "主角")
        involved_names = [entity_names.get(entity_id, entity_id) for entity_id in active_ids]

        main_duty = cls._compact_outline_phrase(
            plot_duties[0] if plot_duties else f"{pov_name}追查本章核心线索",
            max_chars=22,
        )
        payoff_duty = cls._compact_outline_phrase(
            hook_payoff_duties[0] if hook_payoff_duties else f"{pov_name}获得新的可验证线索",
            max_chars=24,
        )
        pressure = cls._compact_outline_phrase(
            emotional_plan.get("pressure_source") or f"{pov_name}面临证据与风险的夹击",
            max_chars=24,
        )
        relationship_choice = cls._compact_outline_phrase(
            emotional_plan.get("relationship_choice") or f"{pov_name}选择承担行动代价",
            max_chars=24,
        )
        entry_state = str(emotional_plan.get("entry_state") or "警惕").strip()
        turning_emotion = str(emotional_plan.get("turning_emotion") or "主动承担").strip()
        exit_aftertaste = str(emotional_plan.get("exit_aftertaste") or "带着新的不安离场").strip()
        subject_id = str(emotional_plan.get("subject_entity_id") or pov_id).strip()
        expression_channels = emotional_plan.get("expression_channels")
        if not isinstance(expression_channels, list) or not expression_channels:
            expression_channels = ["行动", "对白", "物件反应"]
        emotional_plan = {
            "subject_entity_id": subject_id,
            "entry_state": entry_state,
            "pressure_source": pressure,
            "relationship_choice": relationship_choice,
            "turning_emotion": turning_emotion,
            "exit_aftertaste": exit_aftertaste,
            "expression_channels": expression_channels,
        }

        title_seed = cls._compact_outline_phrase(main_duty or payoff_duty, max_chars=4) or "线索"
        title = f"{title_seed}转折"
        word_count = max(1000, int(words_per_chapter or 3000)) + ((chapter_number - 1) % 3) * 120
        scene_goal_texts = [
            str(item).strip() for item in scene_design_goals if str(item).strip()
        ] or [main_duty, pressure, relationship_choice]
        return {
            "chapter_number": chapter_number,
            "title": title[:14],
            "goal": (
                f"让{pov_name}围绕{main_duty}采取具体行动，并在{pressure}下完成"
                f"{relationship_choice}。"
            ),
            "beats_summary": [
                f"{pov_name}以{entry_state}进入{main_duty}，确认本章必须处理的直接风险。",
                f"{pressure}迫使{pov_name}核验证据，并暴露一个会改变行动顺序的缺口。",
                f"{pov_name}通过{relationship_choice}推进局势，离章时留下{exit_aftertaste}。",
            ],
            "main_plot_points": [main_duty, payoff_duty],
            "subplot_points": [],
            "subplot_focus": "",
            "element_focus": [],
            "pov_character_id": pov_id,
            "pov_character_name": pov_name,
            "pov_character": pov_name,
            "pov_switch": False,
            "setting": f"{cls._compact_outline_phrase(main_duty, max_chars=6) or '核心'}现场",
            "expected_word_count": word_count,
            "involved_character_ids": active_ids,
            "required_character_ids": list(cast_plan.get("required_character_ids") or []),
            "support_character_ids": list(cast_plan.get("support_character_ids") or []),
            "involved_character_names": involved_names,
            "involved_characters": involved_names,
            "cast_plan": cast_plan,
            "emotional_plan": emotional_plan,
            "scene_design_goals": scene_goal_texts,
            "notes": f"Mock 按第{chapter_number}章设计矩阵生成，结构化身份以 entity_id 为准。",
            "time_anchor": f"主线第{chapter_number}节点",
            "time_span": "一段连续行动",
            "time_gap_from_prev": "承接上一章余波" if chapter_number > 1 else "开篇即时进入",
            "countdown_state": "",
            "is_flashback": False,
            "expected_hook": {
                "hook_type": "mystery",
                "hook_strength": "medium",
                "hook_description": f"{payoff_duty}后仍留下一个需要下一章处理的证据缺口。",
            },
            "expected_payoffs": [
                {
                    "payoff_type": "information",
                    "description": f"{payoff_duty}得到阶段性兑现，并改变{pov_name}的下一步选择。",
                }
            ],
        }

    @classmethod
    def _generate_outline_batch_response(cls, request: ModelRequest) -> str:
        """Dynamically generate mock chapters matching the requested batch range."""
        import re as _re

        # Prefer the latest user turn, then fall back to the complete user
        # transcript. Some routers append a compact retry turn after the
        # rendered prompt; the requested range must survive that retry path.
        last_user_content = next(
            (m.get("content", "") for m in reversed(request.messages) if m.get("role") == "user"),
            "",
        )
        # Match both formats:
        #   "生成第 X–Y 章" (compact, plan_outline_batch.j2)
        #   "生成第 X 章到第 Y 章" (verbose, plan_outline_continue.j2)
        match = _re.search(r"第\s*(\d+)\s*章到第\s*(\d+)\s*章", last_user_content)
        if not match:
            match = _re.search(r"生成第\s*(\d+)\s*[–\-到]\s*(\d+)\s*章", last_user_content)
        if not match:
            user_transcript = "\n".join(
                m.get("content", "") for m in request.messages if m.get("role") == "user"
            )
            match = _re.search(
                r"(?:请[^\n]{0,40})?生成第\s*(\d+)\s*章到第\s*(\d+)\s*章",
                user_transcript,
            )
        if not match:
            user_transcript = "\n".join(
                m.get("content", "") for m in request.messages if m.get("role") == "user"
            )
            match = _re.search(
                r"章节范围[：:]\s*第\s*(\d+)\s*[–\-到]\s*(\d+)\s*章",
                user_transcript,
            )
        if not match:
            return _RESPONSES.get(TaskType.PLAN_OUTLINE_BATCH, '{"chapters": []}')
        batch_start = int(match.group(1))
        batch_end = int(match.group(2))
        words_per_chapter = cls._requested_words_per_chapter(request)
        entity_names, design_rows = cls._extract_design_matrix_rows(last_user_content)
        chapters = [
            cls._mock_outline_chapter_from_design(
                chapter_number=ch_num,
                row=design_rows.get(ch_num),
                entity_names=entity_names,
                words_per_chapter=words_per_chapter,
            )
            for ch_num in range(batch_start, batch_end + 1)
        ]
        return json.dumps({"chapters": chapters}, ensure_ascii=False)

    @staticmethod
    def _generate_chapter_contracts_response(request: ModelRequest) -> str:
        """Generate mock chapter contracts for every chapter found in the outline prompt."""
        import re as _re

        last_user_content = next(
            (m.get("content", "") for m in reversed(request.messages) if m.get("role") == "user"),
            "",
        )
        outline_section = last_user_content
        if "章节大纲：" in outline_section:
            outline_section = outline_section.split("章节大纲：", 1)[1]
        if "## 输出格式" in outline_section:
            outline_section = outline_section.split("## 输出格式", 1)[0]
        chapter_numbers = sorted(
            {
                int(match.group(1))
                for match in _re.finditer(r"[\"']chapter_number[\"']\s*:\s*(\d+)", outline_section)
            }
        )
        if not chapter_numbers:
            return _RESPONSES.get(TaskType.PLAN_CHAPTER_CONTRACTS, '{"chapter_contracts": []}')
        contracts = [
            {
                "chapter_number": number,
                "title": f"第{number}章（Mock）",
                "entry_state_requirements": [],
                "required_events": [f"完成第{number}章主线推进"],
                "allowed_changes": [f"第{number}章允许的状态变化"],
                "forbidden_changes": [],
                "promise_ops": [],
                "relationship_ops": [],
                "item_ops": [],
                "knowledge_ops": [],
                "exit_state_targets": [f"第{number}章形成可承接的离章状态"],
                "source": "mock_plan_chapter_contracts",
            }
            for number in chapter_numbers
        ]
        return json.dumps({"chapter_contracts": contracts}, ensure_ascii=False)

    @staticmethod
    def _extract_character_voice_enum(request: ModelRequest) -> list[str]:
        schema = request.response_json_schema or {}
        try:
            raw_enum = schema["properties"]["character_voices"]["items"]["properties"]["character"][
                "enum"
            ]
        except (KeyError, TypeError):
            raw_enum = []
        names: list[str] = []
        seen: set[str] = set()
        for item in raw_enum if isinstance(raw_enum, list | tuple) else []:
            name = str(item or "").strip()
            if name and name not in seen:
                names.append(name)
                seen.add(name)
        return names

    @staticmethod
    def _extract_voice_whitelist_from_prompt(request: ModelRequest) -> list[str]:
        prompt_text = "\n".join(m.get("content", "") for m in request.messages)
        marker = "## 角色名白名单（运行时 JSON Schema enum）"
        marker_index = prompt_text.find(marker)
        if marker_index < 0:
            return []
        fragment = prompt_text[marker_index + len(marker) :]
        start = fragment.find("[")
        if start < 0:
            return []
        try:
            raw_names, _ = json.JSONDecoder().raw_decode(fragment[start:])
        except json.JSONDecodeError:
            return []
        names: list[str] = []
        seen: set[str] = set()
        for item in raw_names if isinstance(raw_names, list | tuple) else []:
            name = str(item or "").strip()
            if name and name not in seen:
                names.append(name)
                seen.add(name)
        return names

    @staticmethod
    def _default_mock_voice(character: str, index: int) -> dict[str, Any]:
        if index == 0:
            return {
                "character": character,
                "sentence_profile": "短句为主，先给事实再给判断。",
                "explanation_bias": "少解释，用行动和问题推进。",
                "emotion_syntax": "情绪升高时句子更短，出现停顿。",
                "signature_moves": ["用问题截断对方", "先确认风险再行动"],
                "taboo_patterns": ["大段抒情独白"],
                "sample_lines": ["先看门。门开了，再谈答案。"],
            }
        return {
            "character": character,
            "sentence_profile": "句子保留人物身份感，关键信息后置。",
            "explanation_bias": "只给必要线索，用反应和选择暴露立场。",
            "emotion_syntax": "情绪升高时转为更克制的短句或反问。",
            "signature_moves": ["先观察对方反应", "把判断落到具体证据"],
            "taboo_patterns": ["直接说明全部动机"],
            "sample_lines": ["现在还不是把话说完的时候。"],
        }

    @classmethod
    def _generate_editorial_character_voices_response(cls, request: ModelRequest) -> str:
        names = cls._extract_character_voice_enum(request)
        if not names:
            names = cls._extract_voice_whitelist_from_prompt(request)
        if not names:
            names = [
                str(item.get("name") or "").strip()
                for item in _MOCK_CHARACTER_PAYLOAD.get("characters", [])
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ]
        voices = [cls._default_mock_voice(name, index) for index, name in enumerate(names[:6])]
        return json.dumps({"character_voices": voices}, ensure_ascii=False)
