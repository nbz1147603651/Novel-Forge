"""Tests for concise CLI error classification output."""

from __future__ import annotations

from novel_forge.cli.main import _classify_cli_error


def test_classify_timeout_error_returns_actionable_message() -> None:
    exc = RuntimeError(
        "InternalServerError: Error code: 500 - {'error': {'message': 'Request timed out, please try again later.', 'type': 'RequestTimeOut'}}"
    )

    title, reason, actions = _classify_cli_error(exc)

    assert title == "模型请求超时"
    assert "限定时间" in reason
    assert len(actions) >= 2
