// Auto-generated from novel_forge element library. Do not edit manually.
// Source: data/element_library.json + pipeline/steps/blueprint_element_select/

export interface BlueprintElementCard {
  readonly id: string;
  readonly name: string;
  readonly category: string;
  readonly description: string;
  readonly recommendedGenres: readonly string[];
}

export interface GenrePreset {
  readonly id: string;
  readonly label: string;
  readonly description: string;
  readonly defaultEnabled: readonly string[];
  readonly defaultWeights: Readonly<Record<string, number>>;
}

export const EXTENSION_CARDS: readonly BlueprintElementCard[] = [
  { id: 'romance_emotional_barriers', name: '情感障碍阶梯', category: '言情机制', description: '设计关系推进中的外部障碍与内部障碍升级序列。', recommendedGenres: ["言情", "现言", "古言", "青春", "都市"] },
  { id: 'romance_relationship_contract', name: '关系契约与边界', category: '言情机制', description: '定义角色关系底线、承诺变化与关系拐点契约。', recommendedGenres: ["言情", "婚恋", "破镜重圆", "双强"] },
  { id: 'romance_sweet_bitter_ratio', name: '甜虐配比节奏', category: '言情机制', description: '控制糖点、虐点与回暖节点比例，防止情绪失衡。', recommendedGenres: ["言情", "甜宠", "虐恋", "都市情感"] },
  { id: 'farming_resource_loop', name: '资源经营循环', category: '种田经营', description: '建立资源投入-产出-再投入的成长循环。', recommendedGenres: ["种田", "经营", "古代日常", "基建"] },
  { id: 'farming_season_calendar', name: '农事季节节律', category: '种田经营', description: '绑定季节、节气与生产活动的时间推进规则。', recommendedGenres: ["种田", "古代", "乡土", "经营"] },
  { id: 'farming_community_network', name: '乡里关系网', category: '种田经营', description: '规划家族、邻里、乡绅、商贩关系带来的机会和阻力。', recommendedGenres: ["种田", "家长里短", "乡村", "古代日常"] },
  { id: 'mystery_clue_ledger', name: '线索账本', category: '悬疑推理', description: '记录线索投放、发现者、误导状态与回收章节。', recommendedGenres: ["悬疑", "推理", "刑侦", "本格"] },
  { id: 'mystery_red_herring', name: '误导线与反证', category: '悬疑推理', description: '设置合理红鲱鱼并安排反证揭示顺序。', recommendedGenres: ["悬疑", "推理", "反转"] },
  { id: 'mystery_reveal_order', name: '真相揭示顺序', category: '悬疑推理', description: '规划真相拆解层级与揭秘节奏。', recommendedGenres: ["悬疑", "推理", "刑侦"] },
  { id: 'scifi_rule_reveal', name: '规则揭示顺序', category: '硬科幻', description: '定义世界规则/科技规则的分层揭示路径。', recommendedGenres: ["科幻", "硬科幻", "太空歌剧"] },
  { id: 'scifi_ethics_tradeoff', name: '科技伦理代价', category: '硬科幻', description: '为关键技术决策绑定伦理冲突与社会代价。', recommendedGenres: ["科幻", "赛博", "反乌托邦"] },
  { id: 'progression_power_curve', name: '能力成长曲线', category: '升级成长', description: '规划主角能力阶段、上限与成长触发条件。', recommendedGenres: ["升级流", "玄幻", "异能", "游戏"] },
  { id: 'progression_bottleneck_break', name: '瓶颈与突破机制', category: '升级成长', description: '明确瓶颈类型、突破代价与失败后果。', recommendedGenres: ["升级流", "玄幻", "修仙", "系统"] },
  { id: 'xianxia_realm_resource', name: '境界-资源耦合', category: '仙侠玄幻', description: '绑定境界层级与资源获取、势力关系变化。', recommendedGenres: ["修仙", "玄幻", "仙侠"] },
  { id: 'political_faction_balance', name: '阵营博弈平衡', category: '权谋战争', description: '维护势力目标、盟约变化与背叛成本。', recommendedGenres: ["权谋", "历史", "战争", "朝堂"] },
  { id: 'war_frontline_shift', name: '战线推进与补给', category: '权谋战争', description: '规划战线推进、补给压力与战略目标切换。', recommendedGenres: ["战争", "军事", "历史"] },
  { id: 'horror_dread_rhythm', name: '恐惧节奏管理', category: '恐怖惊悚', description: '安排压迫段、缓冲段与爆发段的节奏组合。', recommendedGenres: ["恐怖", "惊悚", "怪谈"] },
  { id: 'historical_protocol_constraint', name: '礼制与时代约束', category: '历史古代', description: '约束角色行为在时代制度与礼法框架内。', recommendedGenres: ["历史", "古代", "宫廷", "权谋"] },
  { id: 'comedy_setup_payoff', name: '包袱铺垫回收', category: '喜剧轻松', description: '规划笑点铺垫、递进和回收节点。', recommendedGenres: ["喜剧", "轻小说", "日常"] },
  { id: 'youth_growth_trigger', name: '成长触发器', category: '青春成长', description: '定义身份认同变化的触发事件与关系镜像。', recommendedGenres: ["青春", "校园", "成长"] },
  { id: 'workplace_goal_kpi', name: '任务闭环与KPI', category: '职场现实', description: '将职场目标拆解为阶段任务与结果反馈。', recommendedGenres: ["职场", "现实", "都市"] },
  { id: 'thriller_countdown', name: '倒计时压力', category: '惊悚动作', description: '引入可量化倒计时并绑定失败后果。', recommendedGenres: ["惊悚", "动作", "灾难"] },
  { id: 'apocalypse_resource_scarcity', name: '末日资源稀缺', category: '末日生存', description: '追踪食物、水源、燃料、弹药等核心生存资源的消耗与争夺。', recommendedGenres: ["末日", "废土", "丧尸", "后启示录", "灾难"] },
  { id: 'apocalypse_sanctuary_politics', name: '庇护所政治', category: '末日生存', description: '规划幸存者聚居点内部的权力结构、准入规则与领袖更替。', recommendedGenres: ["末日", "废土", "后启示录", "灾难"] },
  { id: 'apocalypse_moral_erosion', name: '道德底线侵蚀', category: '末日生存', description: '追踪主角/群体道德标准在极端压力下的逐步滑移。', recommendedGenres: ["末日", "废土", "丧尸", "后启示录"] },
  { id: 'fantasy_magic_cost', name: '魔法代价体系', category: '奇幻魔法', description: '定义魔法/超自然力量的使用规则、资源消耗与反噬代价。', recommendedGenres: ["奇幻", "西幻", "魔法", "巫师", "黑暗奇幻"] },
  { id: 'fantasy_prophecy_subversion', name: '预言与颠覆', category: '奇幻魔法', description: '管理预言/命运线索的投放、角色回应与最终颠覆或应验。', recommendedGenres: ["奇幻", "西幻", "史诗", "命运", "神话"] },
  { id: 'fantasy_race_culture', name: '种族文化碰撞', category: '奇幻魔法', description: '规划不同种族/文明间的文化差异、偏见壁垒与融合契机。', recommendedGenres: ["奇幻", "西幻", "史诗", "异世界"] },
  { id: 'urban_hidden_world', name: '表里世界设定', category: '都市异能', description: '维护现实世界与超自然世界的交界规则、暴露风险与维持机制。', recommendedGenres: ["都市异能", "无限流", "灵异", "现代奇幻"] },
  { id: 'urban_instance_rules', name: '副本规则揭示', category: '无限流', description: '分层揭示副本/任务世界的隐藏规则，从表面规则到底层真相。', recommendedGenres: ["无限流", "规则怪谈", "生存游戏", "系统"] },
  { id: 'sports_training_arc', name: '训练-突破弧线', category: '体育竞技', description: '规划技能精进、体能极限与心理瓶颈的突破序列。', recommendedGenres: ["竞技", "体育", "电竞", "棋牌"] },
  { id: 'sports_match_rhythm', name: '赛事节奏编排', category: '体育竞技', description: '控制比赛场景的攻防节奏、逆转时机与高光时刻安排。', recommendedGenres: ["竞技", "体育", "电竞", "格斗"] },
  { id: 'sports_team_chemistry', name: '团队化学反应', category: '体育竞技', description: '追踪队友间信任、配合默契与内部冲突的演化。', recommendedGenres: ["竞技", "体育", "电竞", "团队"] },
  { id: 'spy_cover_integrity', name: '身份伪装维护', category: '谍战间谍', description: '追踪主角/关键角色的伪装身份完整度与暴露风险。', recommendedGenres: ["谍战", "间谍", "卧底", "潜伏"] },
  { id: 'spy_intelligence_flow', name: '情报流转与验真', category: '谍战间谍', description: '管理情报获取、传递、验证和利用的完整链条。', recommendedGenres: ["谍战", "间谍", "军事", "冷战"] },
  { id: 'spy_loyalty_web', name: '忠诚网与双面人', category: '谍战间谍', description: '维护角色真实忠诚关系图，标注双面人身份和转变节点。', recommendedGenres: ["谍战", "间谍", "卧底", "反转"] },
  { id: 'scifi_first_contact', name: '第一接触协议', category: '太空歌剧', description: '规划异文明接触时的沟通障碍、文化误读与冲突升级序列。', recommendedGenres: ["科幻", "太空歌剧", "星际", "外星"] },
  { id: 'comedy_misunderstanding_chain', name: '误会连锁反应', category: '喜剧机制', description: '设计误会的投放、发酵和最终澄清的节奏曲线。', recommendedGenres: ["喜剧", "轻小说", "日常", "言情"] },
  { id: 'youth_identity_mirror', name: '身份认同镜像', category: '青春成长', description: '通过与他人的关系映射来推动主角的自我认知变化。', recommendedGenres: ["青春", "校园", "成长", "文艺"] },
  { id: 'workplace_office_politics', name: '职场权力博弈', category: '职场现实', description: '追踪办公室政治中的站队、利益交换与派系冲突。', recommendedGenres: ["职场", "都市", "现实", "商战"] },
  { id: 'horror_unreliable_perception', name: '不可靠感知', category: '心理恐怖', description: '系统性引入知觉怀疑——主角（和读者）无法确定所见是否真实。', recommendedGenres: ["恐怖", "心理恐怖", "克苏鲁", "怪谈"] },
  { id: 'court_document_trail', name: '文书证据链', category: '官场权谋', description: '追踪奏疏、账册、判牍、密信等文书的来源、流转与证据效力。', recommendedGenres: ["权谋", "历史", "官场", "朝堂", "悬疑"] },
  { id: 'court_ceremony_power_map', name: '礼仪权力场', category: '官场权谋', description: '用朝会、宴饮、祭祀、审讯等仪式空间呈现权力排序与关系变化。', recommendedGenres: ["权谋", "历史", "宫廷", "朝堂", "古代"] },
  { id: 'palace_private_public_split', name: '公私场域切换', category: '宫廷宅斗', description: '区分公开场合的身份表演与私密场合的真实交易、试探或崩溃。', recommendedGenres: ["宫廷", "宅斗", "家族", "古言", "权谋"] },
  { id: 'family_inheritance_pressure', name: '继承与家族债务', category: '家族宅斗', description: '追踪家族名分、财产、爵位、债务和声誉如何压迫角色选择。', recommendedGenres: ["宅斗", "家族", "古代", "宫斗", "现实"] },
  { id: 'wuxia_code_vs_survival', name: '侠义与生存冲突', category: '武侠江湖', description: '让侠义承诺、门派规矩与现实求生之间形成可检验冲突。', recommendedGenres: ["武侠", "江湖", "古风", "复仇"] },
  { id: 'wuxia_school_rivalry', name: '门派恩怨谱系', category: '武侠江湖', description: '管理门派、师承、旧仇、盟约与武学传承之间的关系网。', recommendedGenres: ["武侠", "江湖", "仙侠", "古风"] },
  { id: 'revenge_cost_ladder', name: '复仇代价阶梯', category: '复仇机制', description: '规划复仇行动从取证、试探、反击到终局清算的代价升级。', recommendedGenres: ["复仇", "权谋", "悬疑", "武侠", "都市"] },
  { id: 'investigation_procedure_chain', name: '调查程序链', category: '刑侦调查', description: '把发现、取证、验证、推翻、复盘拆成清晰程序，约束破案逻辑。', recommendedGenres: ["刑侦", "悬疑", "推理", "谍战", "现实"] },
  { id: 'business_market_feedback', name: '市场反馈回路', category: '商战经营', description: '追踪产品/策略投入市场后的竞争反应、现金流变化与信誉损益。', recommendedGenres: ["商战", "经营", "职场", "都市", "创业"] },
  { id: 'campus_social_rank', name: '校园关系阶层', category: '校园青春', description: '管理班级、社团、寝室、教师与家庭期待构成的隐性等级。', recommendedGenres: ["校园", "青春", "成长", "现实"] },
  { id: 'healing_daily_ritual', name: '治愈日常仪式', category: '治愈日常', description: '用重复但渐变的日常仪式承载角色恢复、关系升温和主题回响。', recommendedGenres: ["治愈", "日常", "美食", "种田", "青春"] },
  { id: 'literary_symbol_motif', name: '象征母题织入', category: '文学风格', description: '选择少量核心物象/声音/动作，作为主题与人物变化的重复变奏。', recommendedGenres: ["文学", "文艺", "现实", "历史", "女性"] },
  { id: 'literary_silence_subtext', name: '沉默与潜台词', category: '文学风格', description: '让沉默、错答、转移话题和动作细节承担未说出口的信息。', recommendedGenres: ["文学", "文艺", "悬疑", "言情", "现实"] },
  { id: 'time_loop_variation', name: '循环差异记录', category: '时间机制', description: '追踪时间循环/重生/多周目的固定点、变量和记忆保留规则。', recommendedGenres: ["重生", "循环", "时间旅行", "科幻", "悬疑"] },
  { id: 'nonhuman_viewpoint_constraint', name: '非人视角约束', category: '特殊视角', description: '约束非人、AI、怪物、动物或异族 POV 的感知边界与价值判断。', recommendedGenres: ["科幻", "奇幻", "怪谈", "实验文学", "异族"] },
  { id: 'editorial_denouement_budget', name: '高潮后余波预算', category: '出版级编辑', description: '约束主高潮后章节数量、功能分配与确认性场景复用，防止多次尾声。', recommendedGenres: ["通用", "长篇", "言情", "商战", "悬疑", "都市"] },
  { id: 'editorial_voice_matrix', name: '角色声纹矩阵', category: '出版级编辑', description: '为主要角色定义句长、停顿、解释倾向、情绪句法和禁用口吻。', recommendedGenres: ["通用", "长篇", "言情", "群像", "商战", "权谋"] },
  { id: 'editorial_revelation_ladder', name: '揭示阶梯', category: '出版级编辑', description: '把悬疑、记忆、前史、信物或世界规则拆成分级揭示，并要求每级带来行动后果。', recommendedGenres: ["悬疑", "言情", "前世今生", "奇幻", "科幻", "长篇"] },
  { id: 'editorial_scene_resistance', name: '场景阻力设计', category: '出版级编辑', description: '把谈判、对峙、等待、告白等场景的阻力落实为空间、流程、物件、人群或时间窗口。', recommendedGenres: ["通用", "商战", "言情", "悬疑", "职场", "权谋"] },
  { id: 'editorial_symbol_restraint', name: '象征解释克制', category: '出版级编辑', description: '约束核心信物、地点、主题句和反复意象的解释次数与升级方式。', recommendedGenres: ["文学", "言情", "前世今生", "历史", "都市", "长篇"] },
  { id: 'editorial_revision_plan', name: '出版修订方案', category: '出版级编辑', description: '把全书编辑审计结果转成章节合并、补戏、重命名、时间桥和语言删改的修订方案。', recommendedGenres: ["通用", "长篇", "出版", "连载", "改编"] },
  { id: 'quality_hook_score_config', name: '钩子评分权重配置', category: '质量评估', description: '定义追读力评估中钩子强度的评分权重和微兑现上限，支持不同题材的差异化评分。', recommendedGenres: ["通用", "可配置"] },
  { id: 'quality_strand_keywords', name: '情节线关键词配置', category: '质量评估', description: '定义三条情节线（Quest/Fire/Constellation）的关键词集合，用于章节内容类型推断。', recommendedGenres: ["通用", "可配置"] },
  { id: 'quality_time_keywords', name: '时间验证关键词配置', category: '质量评估', description: '定义时间跨度验证的关键词集合，用于检测时间回跳和大跨度间隔。', recommendedGenres: ["通用", "可配置"] },
  { id: 'relationship_power_shift', name: '关系权力转移', category: '关系机制', description: '追踪角色间权力/地位/依赖关系的变化轨迹。', recommendedGenres: ["言情", "权谋", "职场", "家族", "通用"] },
  { id: 'relationship_trust_arc', name: '信任弧线', category: '关系机制', description: '管理角色间信任的建立、破裂与修复周期。', recommendedGenres: ["言情", "谍战", "团队", "通用"] },
  { id: 'relationship_boundary_test', name: '边界试探与突破', category: '关系机制', description: '设计角色间物理/情感/道德边界的试探、跨越与后果。', recommendedGenres: ["言情", "悬疑", "心理", "通用"] },
  { id: 'pacing_breath_window', name: '节奏呼吸窗口', category: '叙事节奏', description: '在高强度场景后安排缓冲段，防止读者疲劳。', recommendedGenres: ["通用", "惊悚", "动作", "悬疑"] },
  { id: 'pacing_tension_release', name: '张力释放曲线', category: '叙事节奏', description: '规划悬念/冲突的积累与释放节奏，避免过早泄底或持续悬空。', recommendedGenres: ["通用", "悬疑", "言情", "惊悚"] },
  { id: 'foreshadowing_thread', name: '伏笔线索管理', category: '伏笔管理', description: '追踪伏笔的投放、暗示强度与回收时机。', recommendedGenres: ["通用", "悬疑", "奇幻", "科幻"] },
  { id: 'information_asymmetry', name: '信息不对称设计', category: '信息管理', description: '管理读者、角色、POV之间的信息差，制造悬念或戏剧性讽刺。', recommendedGenres: ["通用", "悬疑", "喜剧", "言情"] },
  { id: 'supporting_character_arc', name: '配角弧光', category: '角色发展', description: '为主要配角设计独立的变化轨迹，避免工具人化。', recommendedGenres: ["通用", "长篇", "群像"] },
  { id: 'antagonist_motivation', name: '反派动机深度', category: '角色发展', description: '为对手角色建立可信的动机体系，避免脸谱化。', recommendedGenres: ["通用", "悬疑", "权谋", "奇幻"] },
  { id: 'character_voice_differentiation', name: '角色声纹区分', category: '角色发展', description: '为每个主要角色定义独特的语言习惯、句式节奏和禁用表达。', recommendedGenres: ["通用", "长篇", "群像", "出版"] },
  { id: 'scene_atmosphere_layer', name: '场景氛围层次', category: '场景设计', description: '用感官细节、空间布局和环境变化构建场景氛围。', recommendedGenres: ["通用", "文学", "恐怖", "言情"] },
  { id: 'scene_purpose_clarity', name: '场景目的清晰度', category: '场景设计', description: '确保每个场景有明确的叙事功能：推进情节、揭示角色、建立关系或传递信息。', recommendedGenres: ["通用"] },
  { id: 'theme_question_progression', name: '主题问题推进', category: '主题表达', description: '将核心主题转化为可检验的问题，通过角色选择逐步回答。', recommendedGenres: ["通用", "文学", "长篇"] },
  { id: 'moral_complexity', name: '道德复杂性', category: '主题表达', description: '避免非黑即白的道德判断，让角色在灰色地带做选择。', recommendedGenres: ["通用", "文学", "权谋", "末日"] },
  { id: 'cultural_detail_integration', name: '文化细节融入', category: '世界构建', description: '通过饮食、服饰、礼仪、节日等日常细节展现文化背景。', recommendedGenres: ["历史", "奇幻", "古代", "乡土"] },
  { id: 'worldbuilding_show_dont_tell', name: '世界观展示原则', category: '世界构建', description: '通过角色行为、冲突和后果展示世界规则，而非直接说明。', recommendedGenres: ["科幻", "奇幻", "末日", "无限流"] },
  { id: 'family_dynamics_pressure', name: '家族动力压力', category: '家族宅斗', description: '追踪家族内部的权力结构、期望压力与代际冲突如何塑造角色选择。', recommendedGenres: ["宅斗", "家族", "古代", "现实"] },
  { id: 'mentor_apprentice_bond', name: '师徒羁绊', category: '武侠江湖', description: '管理师徒关系中的传承、期望、背叛与和解。', recommendedGenres: ["武侠", "仙侠", "玄幻", "竞技"] },
  { id: 'food_narrative_vehicle', name: '食物叙事载体', category: '治愈日常', description: '用食物/烹饪作为情感表达、关系推进和主题承载的媒介。', recommendedGenres: ["治愈", "日常", "美食", "种田", "言情"] },
  { id: 'custom_family_dynamics', name: 'Family Dynamics', category: 'Family Drama', description: 'Track power structures, generational conflict, and resource allocation within families. Core tension in family drama narratives comes from the dynamic evolution of hierarchical relationships.', recommendedGenres: ["family drama", "historical fiction", "literary fiction", "gothic"] },
  { id: 'custom_mentor_apprentice_arc', name: 'Mentor-Protégé Arc', category: 'Relationship Arcs', description: 'Plan the establishment, trials, betrayal, or legacy of a mentor-protégé relationship. The mentor archetype is a cornerstone of Western narrative tradition from Homer to modern fantasy.', recommendedGenres: ["fantasy", "sci-fi", "literary fiction", "coming-of-age"] },
  { id: 'custom_food_as_narrative', name: 'Food as Narrative', category: 'Sensory Details', description: 'Use food as an emotional carrier, cultural symbol, or plot trigger. Food is a universal emotional language across cultures, especially potent in domestic, healing, and slice-of-life genres.', recommendedGenres: ["literary fiction", "domestic fiction", "healing fiction", "food writing"] },
  { id: 'custom_chosen_one_arc', name: 'Chosen One Arc', category: "Hero's Journey", description: "Track the reluctant hero's call to adventure, threshold crossing, mentorship, ordeal, and transformation. The classic monomyth structure adapted for modern genre fiction.", recommendedGenres: ["fantasy", "sci-fi", "YA", "epic"] },
  { id: 'custom_unreliable_narrator', name: 'Unreliable Narrator', category: 'Narrative Technique', description: "Plan deliberate information gaps between narrator knowledge and reader understanding. The narrator's blind spots, biases, or deceptions become the story's engine.", recommendedGenres: ["psychological thriller", "literary fiction", "mystery", "gothic"] },
  { id: 'custom_redemption_arc', name: 'Redemption Arc', category: 'Character Arcs', description: "Structure a character's fall from grace and path to redemption. Track the original sin, the consequences, the catalyst for change, the struggle, and the cost of atonement.", recommendedGenres: ["literary fiction", "fantasy", "crime", "historical fiction"] },
  { id: 'custom_found_family', name: 'Found Family', category: 'Relationship Arcs', description: 'Build bonds between unrelated characters forming a surrogate family unit. Track how strangers become chosen kin through shared trials, vulnerability, and mutual protection.', recommendedGenres: ["fantasy", "sci-fi", "YA", "adventure"] },
  { id: 'custom_moral_ambiguity', name: 'Moral Ambiguity', category: 'Theme', description: 'Track morally gray decisions and their cascading consequences. No character is purely good or evil; every choice carries trade-offs that complicate the moral landscape.', recommendedGenres: ["literary fiction", "crime", "political thriller", "dark fantasy"] },
];

export const GENRE_PRESETS: readonly GenrePreset[] = [
  { id: 'romance', label: '言情', description: '强化情感障碍、关系边界与甜虐节奏，适合现言/古言/婚恋。', defaultEnabled: ["romance_emotional_barriers", "romance_relationship_contract", "romance_sweet_bitter_ratio"], defaultWeights: {"romance_emotional_barriers": 92.0, "romance_relationship_contract": 88.0, "romance_sweet_bitter_ratio": 84.0} },
  { id: 'farming', label: '种田经营', description: '突出资源循环、季节节律与乡里关系，适合种田/基建/家长里短。', defaultEnabled: ["farming_resource_loop", "farming_season_calendar", "farming_community_network"], defaultWeights: {"farming_resource_loop": 94.0, "farming_season_calendar": 88.0, "farming_community_network": 82.0} },
  { id: 'mystery', label: '悬疑推理', description: '优先线索账本、误导反证和真相揭示顺序，适合推理/刑侦/本格。', defaultEnabled: ["mystery_clue_ledger", "mystery_red_herring", "mystery_reveal_order"], defaultWeights: {"mystery_clue_ledger": 95.0, "mystery_red_herring": 90.0, "mystery_reveal_order": 90.0} },
  { id: 'scifi', label: '硬科幻', description: '强调规则揭示与技术代价，适合硬科幻/赛博/反乌托邦。', defaultEnabled: ["scifi_rule_reveal", "scifi_ethics_tradeoff"], defaultWeights: {"scifi_rule_reveal": 93.0, "scifi_ethics_tradeoff": 86.0} },
  { id: 'progression', label: '升级成长', description: '突出能力曲线与瓶颈突破，适合升级流/异能/系统文。', defaultEnabled: ["progression_power_curve", "progression_bottleneck_break"], defaultWeights: {"progression_power_curve": 92.0, "progression_bottleneck_break": 88.0, "xianxia_realm_resource": 80.0} },
  { id: 'xianxia', label: '修仙仙侠', description: '强调境界体系、资源竞争和突破代价，适合修仙/仙侠。', defaultEnabled: ["xianxia_realm_resource", "progression_power_curve", "progression_bottleneck_break"], defaultWeights: {"xianxia_realm_resource": 94.0, "progression_power_curve": 86.0, "progression_bottleneck_break": 84.0} },
  { id: 'historical', label: '历史权谋', description: '强调礼制约束与阵营博弈，适合历史/宫廷/朝堂。', defaultEnabled: ["historical_protocol_constraint", "political_faction_balance"], defaultWeights: {"historical_protocol_constraint": 90.0, "political_faction_balance": 86.0, "war_frontline_shift": 74.0} },
  { id: 'war', label: '战争军事', description: '突出战线推进、补给约束与阵营博弈，适合战争/军事。', defaultEnabled: ["war_frontline_shift", "political_faction_balance"], defaultWeights: {"war_frontline_shift": 92.0, "political_faction_balance": 84.0} },
  { id: 'horror', label: '恐怖惊悚', description: '强调恐惧节奏、倒计时压力与不可靠感知，适合惊悚/怪谈/心理恐怖。', defaultEnabled: ["horror_dread_rhythm", "thriller_countdown", "horror_unreliable_perception"], defaultWeights: {"horror_dread_rhythm": 92.0, "thriller_countdown": 80.0, "horror_unreliable_perception": 86.0} },
  { id: 'comedy', label: '喜剧日常', description: '强调包袱铺垫回收与误会连锁，适合轻喜剧/轻小说/日常。', defaultEnabled: ["comedy_setup_payoff", "comedy_misunderstanding_chain"], defaultWeights: {"comedy_setup_payoff": 92.0, "comedy_misunderstanding_chain": 84.0} },
  { id: 'youth', label: '青春成长', description: '突出成长触发与身份认同镜像，适合校园/成长线叙事。', defaultEnabled: ["youth_growth_trigger", "youth_identity_mirror"], defaultWeights: {"youth_growth_trigger": 90.0, "youth_identity_mirror": 84.0} },
  { id: 'workplace', label: '职场现实', description: '强调任务闭环与绩效反馈，适合职场/都市现实。', defaultEnabled: ["workplace_goal_kpi", "workplace_office_politics", "business_market_feedback"], defaultWeights: {"workplace_goal_kpi": 90.0, "workplace_office_politics": 86.0, "business_market_feedback": 82.0} },
  { id: 'family', label: '家族宅斗', description: '强调继承压力、公私场域与家族动力，适合宅斗/家族/宫斗。', defaultEnabled: ["family_inheritance_pressure", "palace_private_public_split", "family_dynamics_pressure"], defaultWeights: {"family_inheritance_pressure": 92.0, "palace_private_public_split": 88.0, "family_dynamics_pressure": 84.0, "historical_protocol_constraint": 78.0} },
  { id: 'wuxia', label: '武侠江湖', description: '强调侠义代价、门派恩怨与师承关系，适合武侠/江湖/古风。', defaultEnabled: ["wuxia_code_vs_survival", "wuxia_school_rivalry", "mentor_apprentice_bond"], defaultWeights: {"wuxia_code_vs_survival": 92.0, "wuxia_school_rivalry": 88.0, "mentor_apprentice_bond": 82.0} },
  { id: 'literary', label: '文学现实', description: '强调象征母题、潜台词和克制情绪，适合文学/文艺/现实主义。', defaultEnabled: ["literary_symbol_motif", "literary_silence_subtext"], defaultWeights: {"literary_symbol_motif": 92.0, "literary_silence_subtext": 88.0} },
  { id: 'healing', label: '治愈日常', description: '强调日常仪式、食物叙事与微小关系修复，适合治愈/日常/美食。', defaultEnabled: ["healing_daily_ritual", "food_narrative_vehicle"], defaultWeights: {"healing_daily_ritual": 90.0, "food_narrative_vehicle": 84.0} },
  { id: 'apocalypse', label: '末日生存', description: '突出资源稀缺、庇护所政治与道德底线侵蚀，适合末日/废土/丧尸。', defaultEnabled: ["apocalypse_resource_scarcity", "apocalypse_sanctuary_politics", "apocalypse_moral_erosion"], defaultWeights: {"apocalypse_resource_scarcity": 94.0, "apocalypse_sanctuary_politics": 88.0, "apocalypse_moral_erosion": 86.0} },
  { id: 'fantasy', label: '奇幻西幻', description: '强调魔法代价、预言颠覆与种族文化碰撞，适合奇幻/西幻/史诗。', defaultEnabled: ["fantasy_magic_cost", "fantasy_prophecy_subversion", "fantasy_race_culture"], defaultWeights: {"fantasy_magic_cost": 94.0, "fantasy_prophecy_subversion": 88.0, "fantasy_race_culture": 84.0} },
  { id: 'urban_fantasy', label: '都市异能', description: '突出表里世界设定与副本规则揭示，适合都市异能/灵异/现代奇幻。', defaultEnabled: ["urban_hidden_world", "urban_instance_rules"], defaultWeights: {"urban_hidden_world": 92.0, "urban_instance_rules": 86.0} },
  { id: 'infinite_stream', label: '无限流', description: '强调副本规则推理与恐惧节奏，适合无限流/规则怪谈/生存游戏。', defaultEnabled: ["urban_instance_rules", "horror_dread_rhythm", "thriller_countdown"], defaultWeights: {"urban_instance_rules": 94.0, "horror_dread_rhythm": 86.0, "thriller_countdown": 82.0} },
  { id: 'sports', label: '体育竞技', description: '突出训练突破、赛事节奏与团队化学反应，适合竞技/体育/电竞。', defaultEnabled: ["sports_training_arc", "sports_match_rhythm", "sports_team_chemistry"], defaultWeights: {"sports_training_arc": 92.0, "sports_match_rhythm": 90.0, "sports_team_chemistry": 84.0} },
  { id: 'spy', label: '谍战间谍', description: '强调身份伪装、情报流转与忠诚反转，适合谍战/间谍/卧底。', defaultEnabled: ["spy_cover_integrity", "spy_intelligence_flow", "spy_loyalty_web"], defaultWeights: {"spy_cover_integrity": 94.0, "spy_intelligence_flow": 90.0, "spy_loyalty_web": 86.0} },
  { id: 'space_opera', label: '太空歌剧', description: '强调第一接触、规则揭示与科技伦理，适合太空歌剧/星际。', defaultEnabled: ["scifi_first_contact", "scifi_rule_reveal", "scifi_ethics_tradeoff"], defaultWeights: {"scifi_first_contact": 92.0, "scifi_rule_reveal": 88.0, "scifi_ethics_tradeoff": 84.0} },
];

/**
 * Mirrors `_GENRE_PRESET_HINT_MAP` from the Python pipeline.
 * Each entry maps a set of genre keywords to a preset id.
 */
export const GENRE_PRESET_HINT_MAP: readonly (readonly [readonly string[], string])[] = [
  [["言情", "romance", "爱情", "现言", "古言", "甜宠"], "romance"],
  [["种田", "经营", "乡村", "农", "基建"], "farming"],
  [["悬疑", "推理", "刑侦", "mystery", "detective"], "mystery"],
  [["科幻", "scifi", "sci-fi", "赛博"], "scifi"],
  [["太空歌剧", "星际"], "space_opera"],
  [["升级", "异能", "系统"], "progression"],
  [["修仙", "仙侠"], "xianxia"],
  [["历史", "古代", "宫廷", "权谋"], "historical"],
  [["战争", "军事"], "war"],
  [["恐怖", "惊悚", "怪谈", "thriller"], "horror"],
  [["喜剧", "轻松", "日常"], "comedy"],
  [["青春", "校园", "成长"], "youth"],
  [["职场", "都市现实", "商战"], "workplace"],
  [["宅斗", "家族", "宫斗"], "family"],
  [["武侠", "江湖", "门派"], "wuxia"],
  [["文艺", "文学", "女性", "现实主义"], "literary"],
  [["治愈", "美食"], "healing"],
  [["末日", "废土", "丧尸", "后启示录"], "apocalypse"],
  [["奇幻", "西幻", "魔法", "黑暗奇幻"], "fantasy"],
  [["都市异能", "灵异", "现代奇幻"], "urban_fantasy"],
  [["无限流", "规则怪谈", "生存游戏"], "infinite_stream"],
  [["竞技", "体育", "电竞"], "sports"],
  [["谍战", "间谍", "卧底", "潜伏"], "spy"],
];

/** Recommend a preset id for a given genre text, mirroring Python `recommend_preset_for_genre`. */
export function recommendPresetForGenre(genreText: string): string {
  const haystack = genreText.trim().toLowerCase();
  if (!haystack) return "";
  for (const [keywords, presetId] of GENRE_PRESET_HINT_MAP) {
    if (keywords.some((kw) => haystack.includes(kw.toLowerCase()))) {
      return presetId;
    }
  }
  return "";
}

/** Get extension ids related to a genre, mirroring Python `get_related_extension_ids_for_genre`. */
export function getRelatedExtensionIdsForGenre(genreText: string, presetId = ""): string[] {
  const related: string[] = [];
  const seen = new Set<string>();
  const suggestedPresetId = presetId || recommendPresetForGenre(genreText);
  const preset = GENRE_PRESETS.find((p) => p.id === suggestedPresetId);
  if (preset) {
    for (const elementId of preset.defaultEnabled) {
      if (!seen.has(elementId) && EXTENSION_CARDS.some((c) => c.id === elementId)) {
        seen.add(elementId);
        related.push(elementId);
      }
    }
  }
  return related;
}
