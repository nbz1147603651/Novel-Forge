#!/usr/bin/env python3
"""Temporary benchmark script for UI performance baseline.

Measures:
- render_eval_report setHtml() time
- render_bridge setHtml() time
- render_story_bible setHtml() time
- CacheItem.__post_init__ pickle.dumps time with large object (>1MB)
"""

import json
import sys
import time
from pathlib import Path

# Ensure novel_forge is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from statistics import median

from novel_forge.core.advanced_cache import CacheItem
from novel_forge.desktop.pages.settings_page import SettingsPage
from PySide6.QtCore import QThreadPool, QTimer

# Qt imports - need QApplication for QTextBrowser
from PySide6.QtWidgets import QApplication, QTextBrowser

# Import the renderers
from novel_forge.desktop.pages.document_renderer_reports import (
    render_bridge,
    render_eval_report,
    render_story_bible,
)
from novel_forge.desktop.window import NovelForgeDesktopWindow


def create_qapp():
    """Create QApplication singleton if not exists."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def measure_setHtml(render_func, data, runs=10):
    """Measure setHtml() time for a renderer function.

    Steps:
    1. Call render_func(data) to get QTextBrowser with HTML already set
    2. Extract the HTML that was set (via JavaScript or re-render)
    3. Create fresh QTextBrowser and call setHtml() again, measuring time

    Returns median time in milliseconds.
    """
    # First call render to get HTML content
    browser = render_func(data)
    html = browser.html() if hasattr(browser, 'html') else None

    # If html() not available, re-extract via the render path
    if html is None:
        # Re-call the render but don't measure - just get HTML
        # We need to reconstruct what the render function produces
        # For simplicity, use a fresh browser and measure setHtml on same content
        html = _extract_html_from_render(render_func, data)

    times = []
    for _ in range(runs):
        fresh_browser = QTextBrowser()
        start = time.perf_counter()
        fresh_browser.setHtml(html)
        end = time.perf_counter()
        times.append((end - start) * 1000)  # Convert to ms

    return median(times)


def _extract_html_from_render(render_func, data):
    """Extract HTML from a render function by examining what it produces."""
    # We need to get the HTML without calling setHtml on a browser
    # The render functions build HTML strings and pass to make_browser
    # make_browser calls setHtml(html) then returns the browser

    # Alternative: Create browser, get HTML before setHtml completes
    # For this benchmark, we'll use a workaround: capture via page().toHtml()
    browser = render_func(data)
    # Use a synchronous way to get HTML
    # QTextBrowser doesn't have sync toHtml, so we'll use a timer approach
    result = []

    def capture():
        # toHtml is async, use document().toHtml() instead
        doc = browser.document()
        html = doc.toHtml()
        result.append(html)
        app.quit()

    app = create_qapp()
    QTimer.singleShot(0, capture)
    app.exec()

    return result[0] if result else ""


def measure_cache_item_init(runs=10):
    """Measure CacheItem.__post_init__ pickle.dumps time with large object (>1MB).

    Creates a large dict with {'text': 'x' * 1000000} (~1MB string)
    and measures time to create CacheItem with it.

    Returns median time in milliseconds.
    """
    large_data = {'text': 'x' * 1000000}  # ~1MB

    times = []
    for _ in range(runs):
        start = time.perf_counter()
        _item = CacheItem(value=large_data)
        end = time.perf_counter()
        times.append((end - start) * 1000)  # Convert to ms

    return median(times)


def measure_widget_factory(factory, runs=5):
    """Measure QWidget construction time and delete each instance."""
    app = create_qapp()
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        widget = factory()
        end = time.perf_counter()
        times.append((end - start) * 1000)
        widget.deleteLater()
        app.processEvents()
    return median(times)


def _drain_events(app, duration_ms=30):
    """Let queued QTimer.singleShot callbacks and deferred deletes run."""
    deadline = time.perf_counter() + (duration_ms / 1000)
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.001)
    app.processEvents()


def _drain_switch_settled(app, window, target, timeout_ms=300):
    """Wait until a deferred switch replaces its placeholder and can render once."""
    deadline = time.perf_counter() + (timeout_ms / 1000)
    while time.perf_counter() < deadline:
        app.processEvents()
        page = window._pages.get(target)
        if page is not None and window._stack.currentWidget() is page:
            page.ensurePolished()
            layout = page.layout()
            if layout is not None:
                layout.activate()
            if page.width() > 0 and page.height() > 0:
                page.grab()
            app.processEvents()
            return True
        time.sleep(0.001)
    app.processEvents()
    return False


def measure_switch_pages():
    """Measure cold/hot/prewarmed page switching in the desktop shell."""
    app = create_qapp()
    window = NovelForgeDesktopWindow()
    _drain_events(app)

    timings = {}
    page_ids = [page_id for page_id in window._pages.keys() if page_id != "dashboard"]
    for target in page_ids:
        start = time.perf_counter()
        window.switch_page(target)
        immediate_end = time.perf_counter()
        timings[f"switch_{target}_cold_ms"] = round((immediate_end - start) * 1000, 3)
        _drain_switch_settled(app, window, target)
        settled_end = time.perf_counter()
        timings[f"switch_{target}_cold_total_ms"] = round((settled_end - start) * 1000, 3)

        start = time.perf_counter()
        window.switch_page("dashboard")
        end = time.perf_counter()
        timings[f"switch_dashboard_after_{target}_ms"] = round((end - start) * 1000, 3)
        _drain_events(app)

        start = time.perf_counter()
        window.switch_page(target)
        end = time.perf_counter()
        timings[f"switch_{target}_hot_ms"] = round((end - start) * 1000, 3)
        _drain_events(app)

        window.switch_page("dashboard")
        _drain_events(app)

    window._pre_close_cleanup()
    QThreadPool.globalInstance().waitForDone(1000)
    window.deleteLater()
    _drain_events(app)

    prewarmed = NovelForgeDesktopWindow()
    _drain_events(app)
    for target in page_ids:
        prewarmed._prewarm_page(target)
        _drain_events(app, duration_ms=10)
    for target in page_ids:
        start = time.perf_counter()
        prewarmed.switch_page(target)
        end = time.perf_counter()
        timings[f"switch_{target}_prewarmed_ms"] = round((end - start) * 1000, 3)
        _drain_events(app)
        prewarmed.switch_page("dashboard")
        _drain_events(app)

    prewarmed._pre_close_cleanup()
    QThreadPool.globalInstance().waitForDone(1000)
    prewarmed.deleteLater()
    _drain_events(app)
    return timings


def main():
    """Run all benchmarks and output results."""
    # Create QApplication for Qt-dependent code
    _app = create_qapp()

    # Mock data for renderers
    eval_report_data = {
        "overall_score": 7.5,
        "passed": True,
        "threshold": 6.0,
        "summary": "这是一个测试评估报告，包含多个维度的评分和建议。故事整体框架完整，但存在一些连贯性问题需要修复。",
        "scores": [
            {"dimension": "consistency", "score": 8.0, "comment": "设定一致性好"},
            {"dimension": "continuity", "score": 7.0, "comment": "场景连贯性良好"},
            {"dimension": "character", "score": 7.5, "comment": "人物塑造饱满"},
            {"dimension": "style", "score": 8.0, "comment": "文笔流畅"},
            {"dimension": "engagement", "score": 7.0, "comment": "吸引力中等"},
            {"dimension": "pacing", "score": 7.5, "comment": "节奏控制得当"},
        ],
        "repair_suggestions": [
            {"dimension": "continuity", "priority": "high", "issue": "第3章场景跳转突兀", "location": "第3章", "suggestion": "增加过渡场景"},
            {"dimension": "character", "priority": "medium", "issue": "主角动机不够清晰", "location": "第5章", "suggestion": "补充内心独白"},
        ],
    }

    bridge_data = {
        "from_chapter": "第3章",
        "to_chapter": "第4章",
        "transition_mode": "action_handoff",
        "opening_pov": "主角",
        "opening_time": "黎明前",
        "opening_location": "城郊废墟",
        "bridge_summary": "主角穿越废墟，与追兵展开激烈追逐战，最终成功脱身并发现神秘遗迹入口。",
        "emotional_carryover": "紧张、期待、神秘感延续",
        "action_handoff": "主角进入神秘遗迹，开启新的冒险篇章",
        "causal_link": {
            "previous_event": "主角在城门口被发现行踪",
            "causal_mechanism": "通过地下水道逃脱追踪",
            "unresolved_question": "神秘遗迹的入口如何打开？",
            "open_threads": ["遗迹内部有什么？", "追兵会不会跟来？"],
        },
        "relationship_beat": {
            "current_trust_level": "中等",
            "unspoken_tension": "队友之间存在隐瞒",
            "power_dynamic": "主角逐渐成为领袖",
        },
        "sensory_anchors": ["清晨的雾气", "破损的石柱", "远处狼嚎"],
        "pending_questions": ["遗迹的来历是什么？", "为什么追兵能找到主角？"],
        "forbidden_repetition": ["避免再次使用'黎明'这个词", "不要重复追逐场景"],
    }

    story_bible_data = {
        "title": "测试世界观",
        "premise": "这是一个关于时间旅行的科幻故事，主角可以在不同的时间线之间穿梭。",
        "era": "近未来 2150年",
        "geography": "主要场景：新城、生物实验室、旧世界遗迹",
        "culture": "科技高度发达，但社会分层明显。底层人民生活在废墟中。",
        "magic_or_tech": "时间跳跃技术、意识上传、生物改造",
        "tone": "暗黑、紧张、悬疑",
        "rules": [
            "时间跳跃每次最多穿越10年",
            "意识不能回到身体死亡之前",
            "同一时间线最多存在3个穿越者",
        ],
        "themes": [
            "时间与记忆的关系",
            "科技进步带来的伦理困境",
            "个人命运与历史洪流的碰撞",
        ],
    }

    print("Starting benchmark measurements...")
    print("Each measurement runs 10 times, taking median.")
    print()

    # Warm up Qt
    _ = QTextBrowser()

    # Measure render_eval_report setHtml
    print("Measuring render_eval_report setHtml()...", flush=True)
    render_eval_ms = measure_setHtml(render_eval_report, eval_report_data, runs=10)
    print(f"  render_eval_report: {render_eval_ms:.2f} ms (median)")

    # Measure render_bridge setHtml
    print("Measuring render_bridge setHtml()...", flush=True)
    render_bridge_ms = measure_setHtml(render_bridge, bridge_data, runs=10)
    print(f"  render_bridge: {render_bridge_ms:.2f} ms (median)")

    # Measure render_story_bible setHtml
    print("Measuring render_story_bible setHtml()...", flush=True)
    render_story_bible_ms = measure_setHtml(render_story_bible, story_bible_data, runs=10)
    print(f"  render_story_bible: {render_story_bible_ms:.2f} ms (median)")

    # Measure CacheItem.__post_init__ with large object
    print("Measuring CacheItem.__post_init__ pickle.dumps (large object >1MB)...", flush=True)
    cache_item_ms = measure_cache_item_init(runs=10)
    print(f"  cache_item_init: {cache_item_ms:.2f} ms (median)")

    print("Measuring SettingsPage construction...", flush=True)
    settings_lazy_ms = measure_widget_factory(lambda: SettingsPage(eager_build=False), runs=5)
    settings_eager_ms = measure_widget_factory(SettingsPage, runs=3)
    print(f"  settings_page_lazy: {settings_lazy_ms:.2f} ms (median)")
    print(f"  settings_page_eager: {settings_eager_ms:.2f} ms (median)")

    print("Measuring switch_page timings...", flush=True)
    switch_timings = measure_switch_pages()
    for key, value in switch_timings.items():
        print(f"  {key}: {value:.2f} ms")

    # Build results
    results = {
        "render_eval_report_ms": round(render_eval_ms, 3),
        "render_bridge_ms": round(render_bridge_ms, 3),
        "render_story_bible_ms": round(render_story_bible_ms, 3),
        "cache_item_init_ms": round(cache_item_ms, 3),
        "settings_page_lazy_ms": round(settings_lazy_ms, 3),
        "settings_page_eager_ms": round(settings_eager_ms, 3),
        **switch_timings,
    }

    # Ensure output directory exists
    output_dir = Path(__file__).parent.parent / ".sisyphus" / "evidence"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "baseline.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print()
    print(f"Results written to: {output_path}")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
