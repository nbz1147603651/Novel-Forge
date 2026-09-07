"""Regression tests for compress_prompt_context backward compatibility."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from novel_forge.pipeline.long.services.context.context_helpers import (
    compress_prompt_context,
)


class TestCompressBackwardCompat:
    """Regression tests for backward compatibility of compress_prompt_context."""

    def _make_packet(self) -> MagicMock:
        packet = MagicMock()
        packet.bridge_context_brief = ""
        packet.planning_context_brief = ""
        packet.continuity_context_brief = ""
        packet.canon_context = {}
        return packet

    def _make_creative_report(self, long_text: str) -> dict:
        return {
            "plot_deviations": [
                {
                    "outline_plan": long_text,
                    "actual_plot": "test",
                    "reason": "test",
                    "impact_on_future": "test",
                }
            ],
            "suggestions_for_next_chapter": "test",
        }

    @pytest.mark.asyncio
    async def test_compress_prompt_context_backward_compat(self):
        """Existing callers without quality_check param should still work.

        Verifies that default quality_check=True does not break callers
        that don't explicitly pass quality_check.
        """
        original_text = "因为天气不好，所以他决定留在家里。" * 50

        async def mock_call_with_retry(*args, **kwargs):
            return {
                "items": [
                    {
                        "id": "ctx_1",
                        "compressed": "因为天气不好，他决定留在家里。" * 20,
                    }
                ]
            }

        creative_report = self._make_creative_report(original_text)
        packet = self._make_packet()

        result = await compress_prompt_context(
            creative_report,
            [],
            packet=packet,
            report_text_chars=200,
            profile_field_chars=200,
            bridge_brief_chars=200,
            planning_brief_chars=200,
            continuity_brief_chars=200,
            compress_enabled=True,
            compress_min_chars=10,
            compress_max_tokens=500,
            temperature=0.2,
            call_with_retry=mock_call_with_retry,
            on_step=lambda *a: None,
        )  # call-site-sync: ignore — intentionally omits new defaults to prove backward compatibility

        assert result is not None
        assert "candidates" in result
        assert "compressed" in result
        assert "fallback" in result
        assert "direct_trim" in result
        assert "quality_retries" in result
        assert "before_chars" in result
        assert "after_chars" in result
        assert "samples" in result
        assert "briefs_generated" in result
        assert "budget_pressure" in result
        assert "estimated_chars" in result

    @pytest.mark.asyncio
    async def test_compress_fallback_still_works(self):
        """When LLM compression fails, verify fallback to trim still works.

        Existing behavior: when call_with_retry raises an exception,
        the function should fall back to direct trim on all candidates.
        """
        original_text = "因为天气不好，所以他决定留在家里。" * 50

        async def mock_failing_call(*args, **kwargs):
            raise RuntimeError("Simulated LLM failure")

        creative_report = self._make_creative_report(original_text)
        packet = self._make_packet()

        result = await compress_prompt_context(
            creative_report,
            [],
            packet=packet,
            report_text_chars=200,
            profile_field_chars=200,
            bridge_brief_chars=200,
            planning_brief_chars=200,
            continuity_brief_chars=200,
            compress_enabled=True,
            compress_min_chars=10,
            compress_max_tokens=500,
            temperature=0.2,
            call_with_retry=mock_failing_call,
            on_step=lambda *a: None,
            adaptive_skip_enabled=False,
        )

        assert result is not None
        assert "candidates" in result
        assert result["fallback"] == result["candidates"]
        assert result["compressed"] == 0
        assert "before_chars" in result
        assert "after_chars" in result
        assert "samples" in result
