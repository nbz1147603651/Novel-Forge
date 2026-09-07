"""Built-in film style library (G3).

12 categories x 3 entries = 36 reference styles, each carrying keywords,
reference directors/works, dominant palette, a reusable prompt template and
mood tags.  ``match_style`` deterministically selects the best entry for a
project from its genre/tone so ``FilmStyleLock`` can initialize from curated
data and the PLANNING stage exposes the choice as a decision item.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StyleLibraryEntry:
    style_id: str
    category: str
    name: str
    keywords: tuple[str, ...] = field(default_factory=tuple)
    directors: tuple[str, ...] = field(default_factory=tuple)
    reference_works: tuple[str, ...] = field(default_factory=tuple)
    palette: str = ""
    prompt_template: str = ""
    mood_tags: tuple[str, ...] = field(default_factory=tuple)


STYLE_LIBRARY: tuple[StyleLibraryEntry, ...] = (
    # ── 1. 写实剧情 ──
    StyleLibraryEntry(
        style_id="realism-social",
        category="写实剧情",
        name="社会写实",
        keywords=("写实", "现实", "社会", "生活流"),
        directors=("是枝裕和", "贾樟柯"),
        reference_works=("小偷家族", "三峡好人"),
        palette="低饱和自然色、灰绿基调",
        prompt_template="自然光纪实影像，{scene}，低饱和色彩，保留环境质感与人物层次",
        mood_tags=("克制", "烟火气", "沉静"),
    ),
    StyleLibraryEntry(
        style_id="realism-family",
        category="写实剧情",
        name="家庭温情",
        keywords=("家庭", "温情", "亲情"),
        directors=("李安",),
        reference_works=("饮食男女",),
        palette="暖黄室内光、柔和肤色",
        prompt_template="温暖室内光家庭场景，{scene}，柔和肤色与食物热气细节",
        mood_tags=("温暖", "细腻", "团圆"),
    ),
    StyleLibraryEntry(
        style_id="realism-road",
        category="写实剧情",
        name="公路纪行",
        keywords=("公路", "旅行", "漂泊"),
        directors=("维姆·文德斯",),
        reference_works=("德州巴黎",),
        palette="荒漠黄与天空蓝、逆光剪影",
        prompt_template="公路电影构图，{scene}，大远景与逆光剪影，空旷地平线",
        mood_tags=("孤独", "辽阔", "疏离"),
    ),
    # ── 2. 悬疑犯罪 ──
    StyleLibraryEntry(
        style_id="noir-classic",
        category="悬疑犯罪",
        name="黑色电影",
        keywords=("黑色", "noir", "犯罪", "硬汉"),
        directors=("比利·怀尔德",),
        reference_works=("双重赔偿",),
        palette="高反差黑白、硬边阴影",
        prompt_template="黑色电影布光，{scene}，百叶窗阴影、高反差硬光与烟雾",
        mood_tags=("阴郁", "危险", "宿命"),
    ),
    StyleLibraryEntry(
        style_id="thriller-neon",
        category="悬疑犯罪",
        name="霓虹惊悚",
        keywords=("惊悚", "都市", "夜戏", "霓虹"),
        directors=("尼古拉斯·温丁·雷弗恩",),
        reference_works=("亡命驾驶",),
        palette="霓虹粉蓝对比、深夜街灯",
        prompt_template="霓虹都市夜景，{scene}，粉蓝撞色与湿润路面反光",
        mood_tags=("迷离", "暴力美学", "孤独"),
    ),
    StyleLibraryEntry(
        style_id="mystery-cold",
        category="悬疑犯罪",
        name="冷峻推理",
        keywords=("推理", "悬疑", "刑侦"),
        directors=("大卫·芬奇",),
        reference_works=("十二宫",),
        palette="冷灰绿、低照度均匀光",
        prompt_template="冷峻推理影像，{scene}，低照度均匀布光与克制的镜头运动",
        mood_tags=("压抑", "冷静", "细节控"),
    ),
    # ── 3. 动作冒险 ──
    StyleLibraryEntry(
        style_id="action-practical",
        category="动作冒险",
        name="实拍硬派动作",
        keywords=("动作", "特技", "追车"),
        directors=("乔治·米勒",),
        reference_works=("疯狂的麦克斯4",),
        palette="橙黄沙暴、高饱和高对比",
        prompt_template="硬派动作场面，{scene}，广角中心构图与实拍特技质感",
        mood_tags=("狂野", "肾上腺素", "废土"),
    ),
    StyleLibraryEntry(
        style_id="action-wuxia",
        category="动作冒险",
        name="武侠写意",
        keywords=("武侠", "功夫", "江湖"),
        directors=("胡金铨", "李安"),
        reference_works=("侠女", "卧虎藏龙"),
        palette="水墨青绿、竹林与雾气",
        prompt_template="武侠写意镜头，{scene}，轻功飞行与水墨般的山水层次",
        mood_tags=("飘逸", "侠义", "空灵"),
    ),
    StyleLibraryEntry(
        style_id="action-spy",
        category="动作冒险",
        name="谍战特工",
        keywords=("谍战", "特工", "卧底"),
        directors=("萨姆·门德斯",),
        reference_works=("007：大破天幕杀机",),
        palette="冷蓝都会、剪影轮廓光",
        prompt_template="谍战影像，{scene}，冷蓝都会夜景与轮廓剪影布光",
        mood_tags=(" sleek", "紧张", "阴谋"),
    ),
    # ── 4. 科幻未来 ──
    StyleLibraryEntry(
        style_id="scifi-cyber",
        category="科幻未来",
        name="赛博朋克",
        keywords=("赛博", "朋克", "义体", "未来都市"),
        directors=("雷德利·斯科特", "丹尼斯·维伦纽瓦"),
        reference_works=("银翼杀手", "银翼杀手2049"),
        palette="橙雾与青蓝、巨型光源",
        prompt_template="赛博朋克都市，{scene}，雨雾中的巨型全息广告与橙青对比",
        mood_tags=("迷幻", "孤独", "科技压迫"),
    ),
    StyleLibraryEntry(
        style_id="scifi-hard",
        category="科幻未来",
        name="硬科幻太空",
        keywords=("太空", "航天", "硬科幻"),
        directors=("斯坦利·库布里克", "克里斯托弗·诺兰"),
        reference_works=("2001太空漫游", "星际穿越"),
        palette="纯黑深空、舱内冷白光",
        prompt_template="硬科幻太空影像，{scene}，真实光源逻辑与静谧深空",
        mood_tags=("宏大", "敬畏", "寂静"),
    ),
    StyleLibraryEntry(
        style_id="scifi-retro",
        category="科幻未来",
        name="复古未来主义",
        keywords=("复古未来", "蒸汽", "真空管"),
        directors=("特里·吉列姆",),
        reference_works=("巴西",),
        palette="黄铜暖调、工业灰",
        prompt_template="复古未来主义，{scene}，黄铜管道与真空管显示器的机械质感",
        mood_tags=("荒诞", "怀旧", "官僚感"),
    ),
    # ── 5. 奇幻史诗 ──
    StyleLibraryEntry(
        style_id="fantasy-epic",
        category="奇幻史诗",
        name="史诗奇幻",
        keywords=("史诗", "奇幻", "中土", "王国"),
        directors=("彼得·杰克逊",),
        reference_works=("指环王",),
        palette="晨曦金与森林绿、大远景",
        prompt_template="史诗奇幻航拍，{scene}，晨曦中的辽阔地貌与远征队列",
        mood_tags=("壮阔", "使命", "神话感"),
    ),
    StyleLibraryEntry(
        style_id="fantasy-dark",
        category="奇幻史诗",
        name="黑暗奇幻",
        keywords=("黑暗奇幻", "哥特", "诅咒"),
        directors=("吉尔莫·德尔·托罗",),
        reference_works=("潘神的迷宫",),
        palette="墨绿与琥珀、烛光质感",
        prompt_template="黑暗奇幻，{scene}，烛光琥珀色与墨绿阴影中的生物质感",
        mood_tags=("诡谲", "童话暗面", "潮湿"),
    ),
    StyleLibraryEntry(
        style_id="fantasy-xianxia",
        category="奇幻史诗",
        name="仙侠玄幻",
        keywords=("仙侠", "玄幻", "修真", "法术"),
        directors=("程小东",),
        reference_works=("东方不败",),
        palette="云雾白与鎏金、飘逸丝绸",
        prompt_template="仙侠影像，{scene}，云雾缭绕的仙山与流光法术粒子",
        mood_tags=("飘逸", "宏大", "出尘"),
    ),
    # ── 6. 恐怖惊悚 ──
    StyleLibraryEntry(
        style_id="horror-folk",
        category="恐怖惊悚",
        name="民俗恐怖",
        keywords=("民俗", "恐怖", "祭祀", "村庄"),
        directors=("阿里·艾斯特",),
        reference_works=("仲夏夜惊魂",),
        palette="高曝光白昼、仪式红",
        prompt_template="民俗恐怖，{scene}，反常的明亮白昼光与仪式感构图",
        mood_tags=("不安", "仪式", "诡异"),
    ),
    StyleLibraryEntry(
        style_id="horror-gothic",
        category="恐怖惊悚",
        name="哥特惊悚",
        keywords=("哥特", "古宅", "鬼魂"),
        directors=("詹姆斯·温",),
        reference_works=("招魂",),
        palette="烛光褐与阴影黑",
        prompt_template="哥特惊悚，{scene}，老宅烛光与门后阴影的缓慢揭示",
        mood_tags=("压迫", "阴冷", "未知"),
    ),
    StyleLibraryEntry(
        style_id="horror-body",
        category="恐怖惊悚",
        name="心理惊悚",
        keywords=("心理", "惊悚", "精神分裂"),
        directors=("达伦·阿伦诺夫斯基",),
        reference_works=("黑天鹅",),
        palette="冷白与镜面反射",
        prompt_template="心理惊悚，{scene}，贴身跟拍与镜面反射的自我凝视",
        mood_tags=("崩坏", "焦虑", "迷失"),
    ),
    # ── 7. 爱情浪漫 ──
    StyleLibraryEntry(
        style_id="romance-city",
        category="爱情浪漫",
        name="都市爱情",
        keywords=("都市", "爱情", "邂逅"),
        directors=("理查德·林克莱特",),
        reference_works=("爱在黎明破晓前",),
        palette="黄昏暖橙、街灯柔光",
        prompt_template="都市爱情，{scene}，黄昏漫步与自然对话感的长镜头",
        mood_tags=("悸动", "松弛", "浪漫"),
    ),
    StyleLibraryEntry(
        style_id="romance-period",
        category="爱情浪漫",
        name="年代纯爱",
        keywords=("年代", "纯爱", "怀旧"),
        directors=("王家卫",),
        reference_works=("花样年华",),
        palette="暗红与墨绿、窄巷灯光",
        prompt_template="年代纯爱，{scene}，窄巷灯光与旗袍布料的慢动作擦肩",
        mood_tags=("暧昧", "克制", "遗憾"),
    ),
    StyleLibraryEntry(
        style_id="romance-teen",
        category="爱情浪漫",
        name="青春校园",
        keywords=("青春", "校园", "初恋"),
        directors=("岩井俊二",),
        reference_works=("情书",),
        palette="过曝白与天空蓝",
        prompt_template="青春校园，{scene}，逆光窗帘与过曝天空的清新质感",
        mood_tags=("清新", "悸动", "离别"),
    ),
    # ── 8. 喜剧 ──
    StyleLibraryEntry(
        style_id="comedy-screwball",
        category="喜剧",
        name="高速喜剧",
        keywords=("喜剧", "闹剧", "错位"),
        directors=("刘别谦",),
        reference_works=("你逃我也逃",),
        palette="明亮均匀光、高饱和服装",
        prompt_template="高速喜剧，{scene}，明亮均匀的布光与快节奏的走位调度",
        mood_tags=("机智", "欢闹", "讽刺"),
    ),
    StyleLibraryEntry(
        style_id="comedy-dark",
        category="喜剧",
        name="黑色幽默",
        keywords=("黑色幽默", "荒诞", "讽刺"),
        directors=("科恩兄弟",),
        reference_works=("冰血暴",),
        palette="雪原白与血迹红",
        prompt_template="黑色幽默，{scene}，冷静旁观构图与荒诞反差",
        mood_tags=("荒诞", "冷峻", "讽刺"),
    ),
    StyleLibraryEntry(
        style_id="comedy-family",
        category="喜剧",
        name="合家欢",
        keywords=("合家欢", "亲子", "冒险喜剧"),
        directors=("克里斯·哥伦布",),
        reference_works=("小鬼当家",),
        palette="暖黄家居光、节日色",
        prompt_template="合家欢喜剧，{scene}，温暖家居布光与夸张表情特写",
        mood_tags=("欢乐", "温情", "淘气"),
    ),
    # ── 9. 战争历史 ──
    StyleLibraryEntry(
        style_id="war-epic",
        category="战争历史",
        name="战地史诗",
        keywords=("战争", "战场", "史诗"),
        directors=("史蒂文·斯皮尔伯格",),
        reference_works=("拯救大兵瑞恩",),
        palette="去饱和灰绿、手持颗粒",
        prompt_template="战地影像，{scene}，去饱和胶片颗粒与手持跟拍的临场感",
        mood_tags=("残酷", "兄弟情", "牺牲"),
    ),
    StyleLibraryEntry(
        style_id="war-costume",
        category="战争历史",
        name="宫廷年代",
        keywords=("宫廷", "年代", "历史正剧"),
        directors=("张艺谋",),
        reference_works=("英雄",),
        palette="单色块大场面、仪式化构图",
        prompt_template="宫廷年代影像，{scene}，仪式化对称构图与单色块服装阵列",
        mood_tags=("庄重", "权力", "仪式感"),
    ),
    StyleLibraryEntry(
        style_id="war-biopic",
        category="战争历史",
        name="传记正剧",
        keywords=("传记", "人物", "正剧"),
        directors=("乔·赖特",),
        reference_works=("至暗时刻",),
        palette="烟褐室内光、低角度",
        prompt_template="传记正剧，{scene}，烟雾中的顶光与低角度人物特写",
        mood_tags=("沉重", "抉择", "时代"),
    ),
    # ── 10. 动画风格 ──
    StyleLibraryEntry(
        style_id="anime-ghibli",
        category="动画风格",
        name="手绘治愈",
        keywords=("治愈", "手绘", "童话"),
        directors=("宫崎骏",),
        reference_works=("龙猫",),
        palette="水彩绿与天空蓝",
        prompt_template="手绘治愈动画，{scene}，水彩质感背景与圆润角色动态",
        mood_tags=("治愈", "童真", "自然"),
    ),
    StyleLibraryEntry(
        style_id="anime-cyber",
        category="动画风格",
        name="赛博动画",
        keywords=("赛博", "机甲", "动画"),
        directors=("大友克洋", "押井守"),
        reference_works=("阿基拉", "攻壳机动队"),
        palette="都市霓虹与机械冷灰",
        prompt_template="赛博动画，{scene}，高密度都市细节与机械光泽",
        mood_tags=("冷酷", "哲思", "未来"),
    ),
    StyleLibraryEntry(
        style_id="anime-ink",
        category="动画风格",
        name="水墨国风",
        keywords=("水墨", "国风", "传统动画"),
        directors=("特伟",),
        reference_works=("山水情",),
        palette="留白与墨分五色",
        prompt_template="水墨国风动画，{scene}，留白构图与晕染墨色层次",
        mood_tags=("写意", "空灵", "禅意"),
    ),
    # ── 11. 纪录观察 ──
    StyleLibraryEntry(
        style_id="doc-observational",
        category="纪录观察",
        name="直接电影",
        keywords=("纪录", "观察", "真实"),
        directors=("弗雷德里克·怀斯曼",),
        reference_works=("高中",),
        palette="现场自然光、无修饰色彩",
        prompt_template="直接电影，{scene}，不打断的长镜头与现场自然光",
        mood_tags=("客观", "沉浸", "粗粝"),
    ),
    StyleLibraryEntry(
        style_id="doc-nature",
        category="纪录观察",
        name="自然生态",
        keywords=("自然", "生态", "动物"),
        directors=("雅克·贝汉",),
        reference_works=("迁徙的鸟",),
        palette="高饱和自然色、航拍视角",
        prompt_template="自然生态影像，{scene}，航拍跟随与超长焦压缩空间",
        mood_tags=("壮美", "生命", "律动"),
    ),
    StyleLibraryEntry(
        style_id="doc-archive",
        category="纪录观察",
        name="档案重构",
        keywords=("档案", "历史纪录", "重构"),
        directors=("彼得·杰克逊",),
        reference_works=("他们已不再变老",),
        palette="修复黑白与上色片段交替",
        prompt_template="档案重构，{scene}，修复颗粒质感与克制的现代旁白留白",
        mood_tags=("追忆", "庄重", "时间感"),
    ),
    # ── 12. 实验先锋 ──
    StyleLibraryEntry(
        style_id="avant-garde-dream",
        category="实验先锋",
        name="梦境超现实",
        keywords=("超现实", "梦", "意识流"),
        directors=("大卫·林奇",),
        reference_works=("穆赫兰道",),
        palette="红幕布与暗部噪点",
        prompt_template="超现实梦境，{scene}，红幕布空间与不合逻辑的光源",
        mood_tags=("梦魇", "迷失", "符号化"),
    ),
    StyleLibraryEntry(
        style_id="avant-garde-minimal",
        category="实验先锋",
        name="极简结构",
        keywords=("极简", "结构", "静观"),
        directors=("香特尔·阿克曼",),
        reference_works=("让娜·迪尔曼",),
        palette="中性白墙、固定机位",
        prompt_template="极简结构影像，{scene}，固定机位与完整时间的动作呈现",
        mood_tags=("凝视", "秩序", "压抑"),
    ),
    StyleLibraryEntry(
        style_id="avant-garde-collage",
        category="实验先锋",
        name="拼贴波普",
        keywords=("拼贴", "波普", "混搭"),
        directors=("韦斯·安德森",),
        reference_works=("布达佩斯大饭店",),
        palette="粉彩对称构图、平面化",
        prompt_template="拼贴波普，{scene}，严格对称构图与粉彩平面化置景",
        mood_tags=("童趣", "精致", "疏离"),
    ),
)


def match_style(genre: str, tone: str) -> StyleLibraryEntry | None:
    """Deterministic best-match: rank by keyword hits against genre+tone,
    tie-broken by library order."""
    text = f"{genre} {tone}".strip()
    if not text:
        return None
    best: StyleLibraryEntry | None = None
    best_hits = 0
    for entry in STYLE_LIBRARY:
        hits = sum(
            1
            for keyword in entry.keywords
            if keyword and keyword in text
        )
        category_hit = 1 if entry.category in text else 0
        score = hits * 2 + category_hit
        if score > best_hits:
            best = entry
            best_hits = score
    return best


def style_decision_choices(genre: str, tone: str, limit: int = 4) -> list[str]:
    """Choices for the PLANNING decision item: best match first, then
    category neighbors."""
    primary = match_style(genre, tone)
    choices: list[str] = []
    if primary is not None:
        choices.append(f"{primary.name}（{primary.category}）")
        for entry in STYLE_LIBRARY:
            if entry.category == primary.category and entry.style_id != primary.style_id:
                choices.append(f"{entry.name}（{entry.category}）")
    if len(choices) < limit:
        for entry in STYLE_LIBRARY:
            label = f"{entry.name}（{entry.category}）"
            if label not in choices:
                choices.append(label)
            if len(choices) >= limit:
                break
    return choices[:limit]
