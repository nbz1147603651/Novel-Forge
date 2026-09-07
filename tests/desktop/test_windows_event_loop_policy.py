"""Tests for Windows event loop policy (I-7)."""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.desktop


def test_apply_windows_policy_calls_set_on_windows(monkeypatch):
    """When on win32 with policy available, asyncio.set_event_loop_policy is called."""
    from novel_forge.desktop import main

    monkeypatch.setattr(sys, "platform", "win32")

    class FakePolicy:
        pass

    monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy", FakePolicy, raising=False)

    with patch("asyncio.set_event_loop_policy") as mock_set:
        main._apply_windows_event_loop_policy()
        mock_set.assert_called_once()
        assert isinstance(mock_set.call_args[0][0], FakePolicy)


def test_apply_windows_policy_noop_on_macos(monkeypatch):
    """On darwin, even if policy class exists, do not apply."""
    from novel_forge.desktop import main

    monkeypatch.setattr(sys, "platform", "darwin")
    class FakePolicy:
        pass
    monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy", FakePolicy, raising=False)

    with patch("asyncio.set_event_loop_policy") as mock_set:
        main._apply_windows_event_loop_policy()
        mock_set.assert_not_called()


def test_apply_windows_policy_noop_when_class_missing(monkeypatch):
    """On macOS/Linux where policy class doesn't exist, hasattr guard skips."""
    from novel_forge.desktop import main

    monkeypatch.setattr(sys, "platform", "win32")
    # Delete the attribute to simulate a Python build that doesn't have it
    monkeypatch.delattr(asyncio, "WindowsSelectorEventLoopPolicy", raising=False)

    with patch("asyncio.set_event_loop_policy") as mock_set:
        main._apply_windows_event_loop_policy()
        mock_set.assert_not_called()