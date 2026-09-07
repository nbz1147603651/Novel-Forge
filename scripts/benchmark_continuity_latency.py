"""Performance benchmark for _filter_forbidden_elements motif-aware latency.

Measures the median / p95 / max latency of `_filter_forbidden_elements()` under
three configurations:
  1. static only   – no motif_context or bible_anchor_terms
  2. dynamic motif – motif_context with active_motifs
  3. dynamic motif + bible – both active_motifs and bible_anchor_terms

Each configuration runs 100 iterations per representative chapter text.
Assertion: dynamic configurations add < 50 ms median latency vs static.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from novel_forge.pipeline.steps.continuity_eval_step import _filter_forbidden_elements

CHAPTER_TEXTS: list[str] = [
    (
        "夜色沉沉，月光从窗棂漏进来，照在她苍白的脸上。\n\n"
        "她指尖微微颤抖，想起父亲临走前那句嘱咐，心头涌起一阵寒意。\n\n"
        "周明守在耳房，屏息听着外面的风声，脊背发紧。\n\n"
        "‘必须镇定。’他对自己说，强自镇定地翻开账簿残页。\n\n"
        "灯光昏黄，空气中弥漫着铁锈味。"
    ),
    (
        "雨幕如帘，将整条街巷裹进一片潮湿的灰白。\n\n"
        "她埋进膝盖，蜷缩在门槛旁，呼吸急促，心跳如鼓。\n\n"
        "凉意从石缝渗上来，骨缝都在发麻。\n\n"
        "耳畔嗡鸣不止，眼前白光一闪，喉间涌上血腥味。\n\n"
        "她咬牙忍住刺痛，眼底掠过一丝决绝。"
    ),
    (
        "黄昏时分，暮色四合。\n\n"
        "殿下站在廊下，眸光微敛，神色内敛而警觉。\n\n"
        "‘王爷那边可有动静？’她低声问。\n\n"
        "侍从摇头：‘回娘娘，暂无。’\n\n"
        "她轻叹一声，思绪清明，却仍感不安。\n\n"
        "远处雪意渐浓，雾气缭绕，一片惨淡。"
    ),
    (
        "他掌心出汗，冷汗沿着额角滑落。\n\n"
        "热浪扑面，仿佛置身冰窟又骤然被抛入沸汤。\n\n"
        "周明战栗了一下，如坠深渊，却强压下惊恐。\n\n"
        "‘夫人还在等。’他提醒自己，按捺住忐忑。\n\n"
        "香气忽近，潮气弥漫，他缓缓平复心绪。"
    ),
    (
        "晨光熹微，阴影拉长。\n\n"
        "她发紧的肩线终于松弛，露出一丝欣慰。\n\n"
        "‘兄长会理解的。’她喃喃道，带着释然。\n\n"
        "愧疚与懊悔交织，她决绝转身，走向夜色深处。\n\n"
        "灯光次第熄灭，只剩下冷白月光。"
    ),
]

FORBIDDEN_ELEMENTS: list[str] = [
    "月光", "光芒", "灯光", "冷光", "白光",
    "夜色", "暮色", "晨光", "黄昏",
    "雨幕", "雨丝", "雪幕", "雪意", "风声", "雾气", "阴影", "寒意", "凉意",
    "暖意", "热浪", "铁锈味", "血腥味", "香气", "潮气",
    "耳中", "耳畔", "指尖", "掌心", "脊背", "喉间", "眼底", "眸光",
    "呼吸", "心跳", "冷汗", "颤动", "颤抖", "战栗", "屏息", "发紧",
    "刺痛", "发麻", "埋进", "蜷缩", "骨缝", "如坠", "冰窟", "惨淡", "冰凉",
    "父亲", "母亲", "爹", "娘", "祖父", "祖母",
    "兄长", "长兄", "哥哥", "姐姐", "弟弟", "妹妹",
    "夫君", "娘子", "相公", "夫人",
    "先生", "姑娘", "公子", "少爷", "小姐", "老爷",
    "殿下", "陛下", "王爷", "王妃", "娘娘", "太后",
    "师父", "师兄", "师姐", "师弟", "师妹", "老师",
    "掌柜", "老板",
    "警觉", "内敛", "警觉内敛", "思绪清明", "强自镇定", "心绪如沸",
    "紧张", "焦虑", "愤怒", "悲伤", "恐惧", "绝望", "平静", "冷静",
    "镇定", "清明", "坚定", "犹豫", "惊恐", "惶恐", "不安", "忐忑",
    "欣慰", "释然", "愧疚", "懊悔", "决绝", "隐忍", "克制", "压抑",
]

CONFIG_STATIC_ONLY: dict = {"motif_context": None, "bible_anchor_terms": None}

CONFIG_DYNAMIC_MOTIF: dict = {
    "motif_context": {
        "active_motifs": [
            {"name": "月光", "category": "颜色"},
            {"name": "寒意", "category": "感官"},
            {"name": "颤抖", "category": "动作"},
            {"name": "夜色", "category": "意象"},
            {"name": "风声", "category": "声音"},
            {"name": "宿命", "category": "主题"},
            {"name": "金丝", "category": "符号"},
        ]
    },
    "bible_anchor_terms": None,
}

CONFIG_DYNAMIC_MOTIF_BIBLE: dict = {
    "motif_context": {
        "active_motifs": [
            {"name": "月光", "category": "颜色"},
            {"name": "寒意", "category": "感官"},
            {"name": "颤抖", "category": "动作"},
            {"name": "夜色", "category": "意象"},
            {"name": "风声", "category": "声音"},
            {"name": "宿命", "category": "主题"},
            {"name": "金丝", "category": "符号"},
        ]
    },
    "bible_anchor_terms": [
        "周明", "赵成安", "殿下", "王爷", "娘娘", "夫人",
        "镇北将军", "六品翰林", "西官仓耳房", "账簿残页",
    ],
}

ITERATIONS = 100


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p / 100.0
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_values) else f
    return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])


def _bench_one(text: str, config: dict) -> list[float]:
    latencies: list[float] = []
    _filter_forbidden_elements(FORBIDDEN_ELEMENTS, **config)
    for _ in range(ITERATIONS):
        t0 = time.perf_counter()
        _filter_forbidden_elements(FORBIDDEN_ELEMENTS, **config)
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)
    return latencies


def main() -> None:
    print("=" * 60)
    print("Benchmark: _filter_forbidden_elements() latency")
    print(f"Iterations per config x text: {ITERATIONS}")
    print(f"Texts: {len(CHAPTER_TEXTS)}")
    print("=" * 60)

    results: dict[str, dict[int, list[float]]] = {
        "static_only": {},
        "dynamic_motif": {},
        "dynamic_motif_bible": {},
    }

    for idx, text in enumerate(CHAPTER_TEXTS, 1):
        print(f"\n--- Text sample {idx}/{len(CHAPTER_TEXTS)} ({len(text)} chars) ---")
        for label, config in [
            ("static_only", CONFIG_STATIC_ONLY),
            ("dynamic_motif", CONFIG_DYNAMIC_MOTIF),
            ("dynamic_motif_bible", CONFIG_DYNAMIC_MOTIF_BIBLE),
        ]:
            latencies = _bench_one(text, config)
            results[label][idx] = latencies
            sorted_lat = sorted(latencies)
            med = statistics.median(latencies)
            p95 = _percentile(sorted_lat, 95)
            max_ = max(latencies)
            print(f"  {label:25s}  median={med:7.3f}ms  p95={p95:7.3f}ms  max={max_:7.3f}ms")

    print("\n" + "=" * 60)
    print("Aggregate across all texts")
    print("=" * 60)
    all_static: list[float] = []
    all_motif: list[float] = []
    all_motif_bible: list[float] = []
    for idx in range(1, len(CHAPTER_TEXTS) + 1):
        all_static.extend(results["static_only"][idx])
        all_motif.extend(results["dynamic_motif"][idx])
        all_motif_bible.extend(results["dynamic_motif_bible"][idx])

    def _summarize(name: str, values: list[float]) -> tuple[float, float, float]:
        s = sorted(values)
        return statistics.median(values), _percentile(s, 95), max(values)

    med_s, p95_s, max_s = _summarize("static", all_static)
    med_m, p95_m, max_m = _summarize("motif", all_motif)
    med_mb, p95_mb, max_mb = _summarize("motif+bible", all_motif_bible)

    print(f"  static_only           median={med_s:7.3f}ms  p95={p95_s:7.3f}ms  max={max_s:7.3f}ms")
    print(f"  dynamic_motif         median={med_m:7.3f}ms  p95={p95_m:7.3f}ms  max={max_m:7.3f}ms")
    print(f"  dynamic_motif_bible   median={med_mb:7.3f}ms  p95={p95_mb:7.3f}ms  max={max_mb:7.3f}ms")

    delta_m = med_m - med_s
    delta_mb = med_mb - med_s
    print(f"\n  delta dynamic_motif       = {delta_m:+.3f}ms")
    print(f"  delta dynamic_motif_bible = {delta_mb:+.3f}ms")

    passed = delta_m < 50.0 and delta_mb < 50.0
    if passed:
        print("\nPASS: median latency increase < 50 ms")
    else:
        print("\nFAIL: median latency increase >= 50 ms")
    print("=" * 60)

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
