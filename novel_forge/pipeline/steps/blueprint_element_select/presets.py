"""Genre preset definitions and heuristic mapping tables."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class _GenrePresetDefinition:
    preset_id: str
    label: str
    description: str
    recommended_genres: tuple[str, ...]
    default_enabled: tuple[str, ...]
    default_weights: dict[str, float]
    role_weights: dict[str, float] = field(default_factory=dict)
    incompatible_with: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "label": self.label,
            "description": self.description,
            "recommended_genres": list(self.recommended_genres),
            "default_enabled": list(self.default_enabled),
            "default_weights": dict(self.default_weights),
            "role_weights": dict(self.role_weights),
            "incompatible_with": list(self.incompatible_with),
        }


_GENRE_HEURISTIC_BASE: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("言情", "romance", "爱情", "现言", "古言", "甜宠"),
        (
            "romance_emotional_barriers",
            "romance_relationship_contract",
            "romance_sweet_bitter_ratio",
            "editorial_voice_matrix",
            "editorial_symbol_restraint",
        ),
    ),
    (
        ("种田", "经营", "乡村", "农", "基建"),
        ("farming_resource_loop", "farming_season_calendar", "farming_community_network"),
    ),
    (
        ("悬疑", "推理", "刑侦", "mystery", "detective"),
        ("mystery_clue_ledger", "mystery_red_herring", "mystery_reveal_order"),
    ),
    (
        ("科幻", "scifi", "sci-fi", "赛博"),
        ("scifi_rule_reveal", "scifi_ethics_tradeoff", "scifi_first_contact"),
    ),
    (
        ("升级", "玄幻", "修仙", "异能", "系统"),
        ("progression_power_curve", "progression_bottleneck_break", "xianxia_realm_resource"),
    ),
    (
        ("历史", "古代", "宫廷", "权谋"),
        (
            "historical_protocol_constraint",
            "political_faction_balance",
            "court_document_trail",
            "court_ceremony_power_map",
        ),
    ),
    (("战争", "军事"), ("war_frontline_shift", "political_faction_balance")),
    (
        ("恐怖", "惊悚", "怪谈", "thriller"),
        ("horror_dread_rhythm", "thriller_countdown", "horror_unreliable_perception"),
    ),
    (("喜剧", "轻松", "日常"), ("comedy_setup_payoff", "comedy_misunderstanding_chain")),
    (("青春", "校园", "成长"), ("youth_growth_trigger", "youth_identity_mirror")),
    (("职场", "都市现实"), ("workplace_goal_kpi", "workplace_office_politics")),
    (
        ("商战", "创业", "商业"),
        (
            "business_market_feedback",
            "workplace_goal_kpi",
            "workplace_office_politics",
            "editorial_scene_resistance",
        ),
    ),
    (
        ("长篇", "出版", "改编", "连载"),
        (
            "editorial_denouement_budget",
            "editorial_voice_matrix",
            "editorial_revelation_ladder",
            "editorial_revision_plan",
        ),
    ),
    (
        ("宅斗", "家族", "宫斗"),
        (
            "family_inheritance_pressure",
            "palace_private_public_split",
            "family_dynamics_pressure",
            "historical_protocol_constraint",
        ),
    ),
    (
        ("武侠", "江湖", "门派"),
        ("wuxia_code_vs_survival", "wuxia_school_rivalry", "mentor_apprentice_bond"),
    ),
    (
        ("复仇", "清算"),
        ("revenge_cost_ladder", "mystery_clue_ledger", "political_faction_balance"),
    ),
    (
        ("文艺", "文学", "女性", "现实主义"),
        ("literary_symbol_motif", "literary_silence_subtext", "healing_daily_ritual"),
    ),
    (
        ("治愈", "美食"),
        ("healing_daily_ritual", "food_narrative_vehicle", "farming_community_network"),
    ),
    (
        ("重生", "循环", "时间旅行"),
        ("time_loop_variation", "mystery_reveal_order", "scifi_rule_reveal"),
    ),
    (
        ("末日", "废土", "丧尸", "后启示录", "灾难"),
        (
            "apocalypse_resource_scarcity",
            "apocalypse_sanctuary_politics",
            "apocalypse_moral_erosion",
        ),
    ),
    (
        ("奇幻", "西幻", "魔法", "黑暗奇幻", "史诗"),
        ("fantasy_magic_cost", "fantasy_prophecy_subversion", "fantasy_race_culture"),
    ),
    (("都市异能", "灵异", "现代奇幻"), ("urban_hidden_world", "urban_instance_rules")),
    (
        ("无限流", "规则怪谈", "生存游戏"),
        ("urban_instance_rules", "horror_dread_rhythm", "thriller_countdown"),
    ),
    (
        ("竞技", "体育", "电竞", "格斗"),
        ("sports_training_arc", "sports_match_rhythm", "sports_team_chemistry"),
    ),
    (
        ("谍战", "间谍", "卧底", "潜伏"),
        ("spy_cover_integrity", "spy_intelligence_flow", "spy_loyalty_web"),
    ),
    (("太空歌剧", "星际"), ("scifi_first_contact", "scifi_rule_reveal", "scifi_ethics_tradeoff")),
    # 新增：通用叙事机制
    (
        ("长篇", "群像", "出版"),
        (
            "supporting_character_arc",
            "character_voice_differentiation",
            "scene_purpose_clarity",
        ),
    ),
    (("悬疑", "推理", "刑侦"), ("foreshadowing_thread", "information_asymmetry")),
    (("言情", "爱情", "婚恋"), ("relationship_power_shift", "relationship_trust_arc")),
    (("惊悚", "恐怖", "动作"), ("pacing_breath_window", "pacing_tension_release")),
    (("文学", "文艺", "现实主义"), ("theme_question_progression", "moral_complexity")),
    (("历史", "古代", "乡土"), ("cultural_detail_integration", "worldbuilding_show_dont_tell")),
    (("科幻", "奇幻", "末日"), ("worldbuilding_show_dont_tell", "information_asymmetry")),
    (("谍战", "间谍", "权谋"), ("information_asymmetry", "relationship_boundary_test")),
    (("治愈", "美食", "日常"), ("food_narrative_vehicle", "scene_atmosphere_layer")),
    (("武侠", "仙侠", "玄幻"), ("mentor_apprentice_bond", "supporting_character_arc")),
    (("宅斗", "家族", "宫斗"), ("family_dynamics_pressure", "relationship_power_shift")),
)

_GENRE_HEURISTIC_ENTRY_WEIGHTS: dict[str, float] = {
    "editorial_denouement_budget": 0.7,
    "supporting_character_arc": 0.75,
    "foreshadowing_thread": 0.85,
    "relationship_power_shift": 0.85,
    "pacing_breath_window": 0.85,
    "theme_question_progression": 0.8,
    "cultural_detail_integration": 0.8,
    "worldbuilding_show_dont_tell": 0.85,
}

_GENRE_HEURISTIC_MAP: tuple[tuple[tuple[str, ...], tuple[str, ...], float], ...] = tuple(
    (
        keywords,
        element_ids,
        _GENRE_HEURISTIC_ENTRY_WEIGHTS.get(element_ids[0], 1.0) if element_ids else 1.0,
    )
    for keywords, element_ids in _GENRE_HEURISTIC_BASE
)

_GENRE_PRESET_HINT_MAP: tuple[tuple[tuple[str, ...], str], ...] = (
    (("言情", "romance", "爱情", "现言", "古言", "甜宠"), "romance"),
    (("种田", "经营", "乡村", "农", "基建"), "farming"),
    (("悬疑", "推理", "刑侦", "mystery", "detective"), "mystery"),
    (("科幻", "scifi", "sci-fi", "赛博"), "scifi"),
    (("太空歌剧", "星际"), "space_opera"),
    (("升级", "异能", "系统"), "progression"),
    (("修仙", "仙侠"), "xianxia"),
    (("历史", "古代", "宫廷", "权谋"), "historical"),
    (("战争", "军事"), "war"),
    (("恐怖", "惊悚", "怪谈", "thriller"), "horror"),
    (("喜剧", "轻松", "日常"), "comedy"),
    (("青春", "校园", "成长"), "youth"),
    (("职场", "都市现实", "商战"), "workplace"),
    (("宅斗", "家族", "宫斗"), "family"),
    (("武侠", "江湖", "门派"), "wuxia"),
    (("文艺", "文学", "女性", "现实主义"), "literary"),
    (("治愈", "美食"), "healing"),
    (("末日", "废土", "丧尸", "后启示录"), "apocalypse"),
    (("奇幻", "西幻", "魔法", "黑暗奇幻"), "fantasy"),
    (("都市异能", "灵异", "现代奇幻"), "urban_fantasy"),
    (("无限流", "规则怪谈", "生存游戏"), "infinite_stream"),
    (("竞技", "体育", "电竞"), "sports"),
    (("谍战", "间谍", "卧底", "潜伏"), "spy"),
)

_GENRE_PRESETS: tuple[_GenrePresetDefinition, ...] = (
    _GenrePresetDefinition(
        preset_id="romance",
        label="言情",
        description="强化情感障碍、关系边界与甜虐节奏，适合现言/古言/婚恋。",
        recommended_genres=("言情", "现言", "古言", "婚恋"),
        default_enabled=(
            "romance_emotional_barriers",
            "romance_relationship_contract",
            "romance_sweet_bitter_ratio",
        ),
        default_weights={
            "romance_emotional_barriers": 92.0,
            "romance_relationship_contract": 88.0,
            "romance_sweet_bitter_ratio": 84.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="farming",
        label="种田经营",
        description="突出资源循环、季节节律与乡里关系，适合种田/基建/家长里短。",
        recommended_genres=("种田", "经营", "乡土", "基建"),
        default_enabled=(
            "farming_resource_loop",
            "farming_season_calendar",
            "farming_community_network",
        ),
        default_weights={
            "farming_resource_loop": 94.0,
            "farming_season_calendar": 88.0,
            "farming_community_network": 82.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="mystery",
        label="悬疑推理",
        description="优先线索账本、误导反证和真相揭示顺序，适合推理/刑侦/本格。",
        recommended_genres=("悬疑", "推理", "刑侦", "本格"),
        default_enabled=("mystery_clue_ledger", "mystery_red_herring", "mystery_reveal_order"),
        default_weights={
            "mystery_clue_ledger": 95.0,
            "mystery_red_herring": 90.0,
            "mystery_reveal_order": 90.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="scifi",
        label="硬科幻",
        description="强调规则揭示与技术代价，适合硬科幻/赛博/反乌托邦。",
        recommended_genres=("科幻", "硬科幻", "赛博", "反乌托邦"),
        default_enabled=("scifi_rule_reveal", "scifi_ethics_tradeoff"),
        default_weights={"scifi_rule_reveal": 93.0, "scifi_ethics_tradeoff": 86.0},
    ),
    _GenrePresetDefinition(
        preset_id="progression",
        label="升级成长",
        description="突出能力曲线与瓶颈突破，适合升级流/异能/系统文。",
        recommended_genres=("升级流", "玄幻", "异能", "系统"),
        default_enabled=("progression_power_curve", "progression_bottleneck_break"),
        default_weights={
            "progression_power_curve": 92.0,
            "progression_bottleneck_break": 88.0,
            "xianxia_realm_resource": 80.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="xianxia",
        label="修仙仙侠",
        description="强调境界体系、资源竞争和突破代价，适合修仙/仙侠。",
        recommended_genres=("修仙", "仙侠", "玄幻"),
        default_enabled=(
            "xianxia_realm_resource",
            "progression_power_curve",
            "progression_bottleneck_break",
        ),
        default_weights={
            "xianxia_realm_resource": 94.0,
            "progression_power_curve": 86.0,
            "progression_bottleneck_break": 84.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="historical",
        label="历史权谋",
        description="强调礼制约束与阵营博弈，适合历史/宫廷/朝堂。",
        recommended_genres=("历史", "古代", "宫廷", "权谋"),
        default_enabled=("historical_protocol_constraint", "political_faction_balance"),
        default_weights={
            "historical_protocol_constraint": 90.0,
            "political_faction_balance": 86.0,
            "war_frontline_shift": 74.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="war",
        label="战争军事",
        description="突出战线推进、补给约束与阵营博弈，适合战争/军事。",
        recommended_genres=("战争", "军事", "历史战争"),
        default_enabled=("war_frontline_shift", "political_faction_balance"),
        default_weights={"war_frontline_shift": 92.0, "political_faction_balance": 84.0},
    ),
    _GenrePresetDefinition(
        preset_id="horror",
        label="恐怖惊悚",
        description="强调恐惧节奏、倒计时压力与不可靠感知，适合惊悚/怪谈/心理恐怖。",
        recommended_genres=("恐怖", "惊悚", "怪谈", "灾难", "心理恐怖", "克苏鲁"),
        default_enabled=(
            "horror_dread_rhythm",
            "thriller_countdown",
            "horror_unreliable_perception",
        ),
        default_weights={
            "horror_dread_rhythm": 92.0,
            "thriller_countdown": 80.0,
            "horror_unreliable_perception": 86.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="comedy",
        label="喜剧日常",
        description="强调包袱铺垫回收与误会连锁，适合轻喜剧/轻小说/日常。",
        recommended_genres=("喜剧", "轻小说", "日常"),
        default_enabled=("comedy_setup_payoff", "comedy_misunderstanding_chain"),
        default_weights={"comedy_setup_payoff": 92.0, "comedy_misunderstanding_chain": 84.0},
    ),
    _GenrePresetDefinition(
        preset_id="youth",
        label="青春成长",
        description="突出成长触发与身份认同镜像，适合校园/成长线叙事。",
        recommended_genres=("青春", "校园", "成长"),
        default_enabled=("youth_growth_trigger", "youth_identity_mirror"),
        default_weights={"youth_growth_trigger": 90.0, "youth_identity_mirror": 84.0},
    ),
    _GenrePresetDefinition(
        preset_id="workplace",
        label="职场现实",
        description="强调任务闭环与绩效反馈，适合职场/都市现实。",
        recommended_genres=("职场", "都市", "现实", "商战"),
        default_enabled=(
            "workplace_goal_kpi",
            "workplace_office_politics",
            "business_market_feedback",
        ),
        default_weights={
            "workplace_goal_kpi": 90.0,
            "workplace_office_politics": 86.0,
            "business_market_feedback": 82.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="family",
        label="家族宅斗",
        description="强调继承压力、公私场域与家族动力，适合宅斗/家族/宫斗。",
        recommended_genres=("宅斗", "家族", "宫斗", "古代"),
        default_enabled=(
            "family_inheritance_pressure",
            "palace_private_public_split",
            "family_dynamics_pressure",
        ),
        default_weights={
            "family_inheritance_pressure": 92.0,
            "palace_private_public_split": 88.0,
            "family_dynamics_pressure": 84.0,
            "historical_protocol_constraint": 78.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="wuxia",
        label="武侠江湖",
        description="强调侠义代价、门派恩怨与师承关系，适合武侠/江湖/古风。",
        recommended_genres=("武侠", "江湖", "门派", "古风"),
        default_enabled=(
            "wuxia_code_vs_survival",
            "wuxia_school_rivalry",
            "mentor_apprentice_bond",
        ),
        default_weights={
            "wuxia_code_vs_survival": 92.0,
            "wuxia_school_rivalry": 88.0,
            "mentor_apprentice_bond": 82.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="literary",
        label="文学现实",
        description="强调象征母题、潜台词和克制情绪，适合文学/文艺/现实主义。",
        recommended_genres=("文学", "文艺", "现实主义", "女性"),
        default_enabled=("literary_symbol_motif", "literary_silence_subtext"),
        default_weights={"literary_symbol_motif": 92.0, "literary_silence_subtext": 88.0},
    ),
    _GenrePresetDefinition(
        preset_id="healing",
        label="治愈日常",
        description="强调日常仪式、食物叙事与微小关系修复，适合治愈/日常/美食。",
        recommended_genres=("治愈", "日常", "美食", "种田"),
        default_enabled=("healing_daily_ritual", "food_narrative_vehicle"),
        default_weights={"healing_daily_ritual": 90.0, "food_narrative_vehicle": 84.0},
    ),
    _GenrePresetDefinition(
        preset_id="apocalypse",
        label="末日生存",
        description="突出资源稀缺、庇护所政治与道德底线侵蚀，适合末日/废土/丧尸。",
        recommended_genres=("末日", "废土", "丧尸", "后启示录", "灾难"),
        default_enabled=(
            "apocalypse_resource_scarcity",
            "apocalypse_sanctuary_politics",
            "apocalypse_moral_erosion",
        ),
        default_weights={
            "apocalypse_resource_scarcity": 94.0,
            "apocalypse_sanctuary_politics": 88.0,
            "apocalypse_moral_erosion": 86.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="fantasy",
        label="奇幻西幻",
        description="强调魔法代价、预言颠覆与种族文化碰撞，适合奇幻/西幻/史诗。",
        recommended_genres=("奇幻", "西幻", "魔法", "黑暗奇幻", "史诗"),
        default_enabled=(
            "fantasy_magic_cost",
            "fantasy_prophecy_subversion",
            "fantasy_race_culture",
        ),
        default_weights={
            "fantasy_magic_cost": 94.0,
            "fantasy_prophecy_subversion": 88.0,
            "fantasy_race_culture": 84.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="urban_fantasy",
        label="都市异能",
        description="突出表里世界设定与副本规则揭示，适合都市异能/灵异/现代奇幻。",
        recommended_genres=("都市异能", "灵异", "现代奇幻"),
        default_enabled=("urban_hidden_world", "urban_instance_rules"),
        default_weights={"urban_hidden_world": 92.0, "urban_instance_rules": 86.0},
    ),
    _GenrePresetDefinition(
        preset_id="infinite_stream",
        label="无限流",
        description="强调副本规则推理与恐惧节奏，适合无限流/规则怪谈/生存游戏。",
        recommended_genres=("无限流", "规则怪谈", "生存游戏"),
        default_enabled=("urban_instance_rules", "horror_dread_rhythm", "thriller_countdown"),
        default_weights={
            "urban_instance_rules": 94.0,
            "horror_dread_rhythm": 86.0,
            "thriller_countdown": 82.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="sports",
        label="体育竞技",
        description="突出训练突破、赛事节奏与团队化学反应，适合竞技/体育/电竞。",
        recommended_genres=("竞技", "体育", "电竞", "格斗"),
        default_enabled=("sports_training_arc", "sports_match_rhythm", "sports_team_chemistry"),
        default_weights={
            "sports_training_arc": 92.0,
            "sports_match_rhythm": 90.0,
            "sports_team_chemistry": 84.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="spy",
        label="谍战间谍",
        description="强调身份伪装、情报流转与忠诚反转，适合谍战/间谍/卧底。",
        recommended_genres=("谍战", "间谍", "卧底", "潜伏", "冷战"),
        default_enabled=("spy_cover_integrity", "spy_intelligence_flow", "spy_loyalty_web"),
        default_weights={
            "spy_cover_integrity": 94.0,
            "spy_intelligence_flow": 90.0,
            "spy_loyalty_web": 86.0,
        },
    ),
    _GenrePresetDefinition(
        preset_id="space_opera",
        label="太空歌剧",
        description="强调第一接触、规则揭示与科技伦理，适合太空歌剧/星际。",
        recommended_genres=("太空歌剧", "星际", "科幻"),
        default_enabled=("scifi_first_contact", "scifi_rule_reveal", "scifi_ethics_tradeoff"),
        default_weights={
            "scifi_first_contact": 92.0,
            "scifi_rule_reveal": 88.0,
            "scifi_ethics_tradeoff": 84.0,
        },
    ),
)

_GENRE_PRESET_BY_ID: dict[str, _GenrePresetDefinition] = {
    preset.preset_id: preset for preset in _GENRE_PRESETS
}
