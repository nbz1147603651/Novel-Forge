"""Tests for InitV2BlockCache with prompt-template version fingerprinting.

These tests verify that:
1. Same spec + same template version → cache hit
2. Same spec + different template version → cache miss (template drift)
3. Different spec + same template version → cache miss (payload drift)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from novel_forge.pipeline.long.services.init.init_cache import (
    _build_long_init_request_payload,
    _long_init_request_fingerprint,
)
from novel_forge.pipeline.long.services.init.init_v2 import InitV2BlockCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_payload(characters_hint: str = "默认角色") -> dict:
    """Build a standard long-init request payload for testing."""
    return _build_long_init_request_payload(
        premise="测试故事",
        genre="fantasy",
        tone="epic",
        title="测试",
        language="zh",
        characters_hint=characters_hint,
        world_hint="奇幻世界",
        conflict_hint="正邪之战",
        pov_hint="主角视角",
        opening_style="悬念开场",
        ending_style="开放式结局",
        extra_instructions="",
        total_chapters=10,
        words_per_chapter=2000,
        volume_mode="auto",
        chapters_per_volume=0,
        effective_volume_mode=False,
            effective_chapters_per_volume=0,
            blueprint_element_preferences=None,
        )


def _mock_version_manager(
    system_version: str = "2.1.3",
    cat_initialization: str = "2.1.3",
    cat_writing: str = "2.1.3",
    cat_planning: str = "2.1.3",
    cat_checking: str = "2.1.3",
) -> MagicMock:
    """Create a mock TemplateVersionManager with controlled version values."""
    mgr = MagicMock()
    mgr.get_current_version.return_value = system_version

    def _mock_get_category_version(cat: str) -> MagicMock | None:
        versions = {
            "initialization": cat_initialization,
            "writing": cat_writing,
            "planning": cat_planning,
            "checking": cat_checking,
        }
        ver = versions.get(cat)
        if ver:
            v = MagicMock()
            v.version = ver
            return v
        return None

    mgr.get_category_version.side_effect = _mock_get_category_version
    return mgr


# ---------------------------------------------------------------------------
# Test 1: Cache hit — same spec + same template version
# ---------------------------------------------------------------------------

@patch("novel_forge.pipeline.long.services.init.init_cache.get_version_manager")
def test_cache_hit_same_template_version(mock_get_mgr: MagicMock, tmp_path: Path) -> None:
    """相同 spec + 相同 template version → 缓存命中 (load_success returns payload)."""
    mock_get_mgr.return_value = _mock_version_manager()
    cache = InitV2BlockCache(tmp_path)
    payload = _make_payload()
    fp = _long_init_request_fingerprint(payload)
    upstream: dict[str, str] = {"spec": "abc123"}
    block_key = "story_bible_test"

    cache.save_success(
        block_key,
        request_fingerprint=fp,
        upstream_hashes=upstream,
        payload={"content": "cached_data"},
    )

    result = cache.load_success(block_key, request_fingerprint=fp, upstream_hashes=upstream)
    assert result == {"content": "cached_data"}, "Identical input should produce cache hit"


# ---------------------------------------------------------------------------
# Test 2: Cache miss — template version changed
# ---------------------------------------------------------------------------

@patch("novel_forge.pipeline.long.services.init.init_cache.get_version_manager")
def test_cache_miss_template_version_changed(mock_get_mgr: MagicMock, tmp_path: Path) -> None:
    """相同 spec + template version 变化 → 缓存失效 (fingerprint 变化导致 miss)."""
    # Phase 1: save with version "2.1.3"
    mock_get_mgr.return_value = _mock_version_manager(system_version="2.1.3")
    cache = InitV2BlockCache(tmp_path)
    payload = _make_payload()
    fp_v1 = _long_init_request_fingerprint(payload)
    upstream: dict[str, str] = {"spec": "abc123"}
    block_key = "story_bible_test"

    cache.save_success(
        block_key,
        request_fingerprint=fp_v1,
        upstream_hashes=upstream,
        payload={"content": "cached_data"},
    )

    # Phase 2: load with version "3.0.0" (template changed)
    mock_get_mgr.return_value = _mock_version_manager(system_version="3.0.0")
    fp_v2 = _long_init_request_fingerprint(payload)

    # Assert fingerprints differ
    assert fp_v1 != fp_v2, "Different template versions must produce different fingerprints"

    result = cache.load_success(block_key, request_fingerprint=fp_v2, upstream_hashes=upstream)
    assert result is None, "Template version change should invalidate cache"


# ---------------------------------------------------------------------------
# Test 3: Cache miss — payload content changed
# ---------------------------------------------------------------------------

@patch("novel_forge.pipeline.long.services.init.init_cache.get_version_manager")
def test_cache_miss_payload_changed(mock_get_mgr: MagicMock, tmp_path: Path) -> None:
    """相同 template + payload 细节变化 (characters_hint) → 缓存失效."""
    mock_get_mgr.return_value = _mock_version_manager()
    cache = InitV2BlockCache(tmp_path)
    payload_a = _make_payload(characters_hint="角色A：勇敢的战士")  # original
    payload_b = _make_payload(characters_hint="角色B：狡猾的谋士（修改了描述）")  # changed
    block_key = "story_bible_test"
    upstream: dict[str, str] = {"spec": "abc123"}

    fp_a = _long_init_request_fingerprint(payload_a)
    fp_b = _long_init_request_fingerprint(payload_b)

    # Assert fingerprints differ
    assert fp_a != fp_b, "Different payloads must produce different fingerprints"

    cache.save_success(
        block_key,
        request_fingerprint=fp_a,
        upstream_hashes=upstream,
        payload={"content": "cached_data"},
    )

    result = cache.load_success(block_key, request_fingerprint=fp_b, upstream_hashes=upstream)
    assert result is None, "Payload content change (characters_hint) should invalidate cache"


@patch("novel_forge.pipeline.long.services.init.init_v2.get_version_manager")
def test_failed_block_reports_stale_runtime_version(
    mock_get_mgr: MagicMock, tmp_path: Path
) -> None:
    mock_get_mgr.return_value = _mock_version_manager(system_version="2.1.3")
    cache = InitV2BlockCache(tmp_path)
    cache.save_error(
        "editorial_contract",
        request_fingerprint="request-v1",
        upstream_hashes={"blueprint": "hash-v1"},
        error=ValueError("unknown field at $.denouement_budget"),
    )

    mock_get_mgr.return_value = _mock_version_manager(system_version="3.0.0")
    diagnostic = cache.load_failure_diagnostic("editorial_contract")

    assert diagnostic is not None
    assert diagnostic["error_kind"] == "runtime_version_stale"
    assert diagnostic["error_type"] == "运行版本陈旧"
    assert diagnostic["action"] == "retry_failed_step_with_current_runtime"
