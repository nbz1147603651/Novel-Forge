"""Parity tests for canonical ``source_text_hash``.

Ensures the new ``novel_forge.core.utils.text_hash.source_text_hash`` produces
identical output to:
1. The existing public ``novel_forge.core.review_contracts.source_text_hash``
2. Every in-scope private ``_source_text_hash`` copy (workspace/ + pipeline/ + common/)
"""

from __future__ import annotations

import hashlib
import importlib

import pytest

from novel_forge.core.utils.text_hash import source_text_hash

# ---------------------------------------------------------------------------
# Test inputs — must cover ASCII, CJK, and empty string
# ---------------------------------------------------------------------------
TEST_INPUTS: list[str] = [
    "test",
    "测试文本",
    "",
    "Hello, 世界!\n" * 100,  # longer mixed content
]


# ---------------------------------------------------------------------------
# Reference implementations to compare against
# ---------------------------------------------------------------------------
def _expected_hash(text: str) -> str:
    """Ground-truth implementation (SHA-256 of UTF-8)."""
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


# Public reference in review_contracts
def _ref_review_contracts(text: str) -> str:
    from novel_forge.core.review.review_contracts import source_text_hash as _ref
    return _ref(text)


# Private copies — in-scope modules only
_PRIVATE_COPIES: dict[str, str] = {
    "workspace.execution_state": "novel_forge.workspace.helpers.execution_state",
    "workspace.chapter_session_handlers": "novel_forge.workspace.sessions.chapter_session_handlers",
    "workspace.projects": "novel_forge.workspace.projects",
    "pipeline.steps.contract_execution_audit_step": "novel_forge.pipeline.steps.contract_execution_audit_step",
    "pipeline.steps.macro_guard_step": "novel_forge.pipeline.steps.macro_guard_step",
    "pipeline.long.stages.reading_power_repair": "novel_forge.pipeline.long.stages.reading_power_repair",
    "pipeline.long.stages.finalize": "novel_forge.pipeline.long.stages.finalize",
    "pipeline.long.services.contract_execution_repair": "novel_forge.pipeline.long.services.contract_execution_repair",
    "common.plot_guard": "novel_forge.common.plot_guard",
}


def _get_private(module_path: str):
    """Import and return ``_source_text_hash`` from *module_path*, or None if migrated."""
    mod = importlib.import_module(module_path)
    return getattr(mod, "_source_text_hash", None)  # noqa: B009


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
class TestSmoke:
    def test_import(self) -> None:
        """Canonical function is importable."""
        assert callable(source_text_hash)

    def test_returns_hex_string(self) -> None:
        result = source_text_hash("hello")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest length
        assert all(c in "0123456789abcdef" for c in result)


# ---------------------------------------------------------------------------
# Parity: canonical vs ground truth
# ---------------------------------------------------------------------------
class TestCanonicalVsGroundTruth:
    @pytest.mark.parametrize("text", TEST_INPUTS)
    def test_parity(self, text: str) -> None:
        assert source_text_hash(text) == _expected_hash(text)


# ---------------------------------------------------------------------------
# Parity: canonical vs review_contracts.source_text_hash (public reference)
# ---------------------------------------------------------------------------
class TestCanonicalVsPublicReference:
    @pytest.mark.parametrize("text", TEST_INPUTS)
    def test_parity_with_review_contracts(self, text: str) -> None:
        assert source_text_hash(text) == _ref_review_contracts(text)


# ---------------------------------------------------------------------------
# Parity: canonical vs each in-scope private _source_text_hash
# ---------------------------------------------------------------------------
class TestCanonicalVsPrivateCopies:
    @pytest.mark.parametrize("module_label,module_path", list(_PRIVATE_COPIES.items()))
    @pytest.mark.parametrize("text", TEST_INPUTS)
    def test_parity(self, module_label: str, module_path: str, text: str) -> None:
        private_fn = _get_private(module_path)
        if private_fn is None:
            pytest.skip(f"{module_label}: private _source_text_hash already migrated to canonical")
        canonical = source_text_hash(text)
        private = private_fn(text)
        assert canonical == private, (
            f"Parity mismatch for {module_label} on input {text!r:.40}: "
            f"canonical={canonical}, private={private}"
        )
