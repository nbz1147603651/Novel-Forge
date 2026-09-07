"""Smoke-test that desktop.jobs re-exports the public API after split."""

from __future__ import annotations

REQUIRED_NAMES = (
    "DesktopJobManager",
    "DesktopJobState",
    "DesktopJobRecord",
)


def test_jobs_public_api_importable():
    from novel_forge.desktop import jobs
    for name in REQUIRED_NAMES:
        assert hasattr(jobs, name), f"missing: {name}"
