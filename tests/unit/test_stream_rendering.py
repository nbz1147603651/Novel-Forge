"""Symmetric render-kind detection tests for the PySide6 stream renderer.

These mirror the nimo frontend fixtures in
``clients/nimo-desktop/src/components/StreamContent.test.tsx`` so the two
surfaces classify the same payloads identically (text / json / json_partial /
report).  Keep the fixture set in sync when adding new render kinds.
"""

from __future__ import annotations

import pytest

from novel_forge.desktop.components.stream_rendering import (
    StreamRenderKind,
    detect_stream_render_kind,
    stream_html_from_text,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Plain prose → TEXT (mirrors StreamContent.test.tsx "declared text").
        ("灰瓦在雨里发亮。", StreamRenderKind.TEXT),
        # Complete JSON object → JSON.
        ('{"街景":"骑楼"}', StreamRenderKind.JSON),
        # Complete JSON array → JSON.
        ('[{"a": 1}, {"b": 2}]', StreamRenderKind.JSON),
        # Incomplete JSON fragment → JSON_PARTIAL (mirrors the nimo partial
        # fixture that must stay readable instead of being tokenized).
        ('{"街景":"骑楼、青石板', StreamRenderKind.JSON_PARTIAL),
        # Evaluation report key → REPORT.
        ('{"overall_score": 7.5, "summary": "ok"}', StreamRenderKind.REPORT),
        # Guard report decision envelope → REPORT.
        (
            '{"decision": {"risk_level": "high", "outline_action": "patch"}}',
            StreamRenderKind.REPORT,
        ),
        # Empty / whitespace-only text → TEXT.
        ("", StreamRenderKind.TEXT),
        ("   \n  ", StreamRenderKind.TEXT),
    ],
)
def test_detect_stream_render_kind_matches_nimo_fixtures(
    text: str,
    expected: StreamRenderKind,
) -> None:
    assert detect_stream_render_kind(text) == expected


def test_stream_html_from_text_renders_partial_json_as_source_text() -> None:
    """Mid-stream JSON stays readable; no tokenizer is run over it."""
    html = stream_html_from_text('{"街景":"骑楼、青石板')
    assert "JSON 块尚未完成" in html
    assert "骑楼、青石板" in html


def test_stream_html_from_text_renders_report_card() -> None:
    html = stream_html_from_text('{"overall_score": 7.5}')
    assert "质量评估报告" in html
    assert "7.5" in html


def test_stream_html_from_text_renders_json_object_card() -> None:
    html = stream_html_from_text('{"街景":"骑楼"}')
    assert "JSON 输出" in html
    assert "骑楼" in html
