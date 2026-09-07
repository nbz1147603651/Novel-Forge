"""Tests for compress_prompt_context quality verification."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from novel_forge.pipeline.long.services.context.context_helpers import (
    _verify_compression_quality,
    compress_prompt_context,
)


class TestVerifyCompressionQuality:
    """Unit tests for the heuristic quality scorer."""

    def test_perfect_preservation(self):
        """Identical text scores 1.0."""
        text = "因为天气不好，所以他决定留在家里。"
        assert _verify_compression_quality(text, text) == 1.0

    def test_excessive_compression_penalty(self):
        """Ratio < 0.2 incurs -0.3 penalty."""
        original = "因为天气不好，所以他决定留在家里。" * 100
        compressed = "天气不好，留在家。"
        score = _verify_compression_quality(original, compressed)
        assert score < 0.7

    def test_moderate_compression_penalty(self):
        """Ratio < 0.4 incurs -0.1 penalty."""
        original = "因为天气不好，所以他决定留在家里。" * 10
        compressed = "天气不好留在家。" * 2
        score = _verify_compression_quality(original, compressed)
        assert score < 1.0

    def test_marker_preservation_bonus(self):
        """Preserving causal markers boosts score."""
        original = "因为下雨，所以他决定不出门。但是如果雨停了，他发现太阳出来了。"
        compressed = "因为下雨，他决定不出门。但是雨停后，他发现太阳出来了。"
        score = _verify_compression_quality(original, compressed)
        assert score > 0.7

    def test_marker_loss_penalty(self):
        """Losing causal markers reduces score."""
        original = "因为下雨，所以他决定不出门。但是如果雨停了，他发现太阳出来了。"
        compressed = "下雨了，不出门。雨停后，太阳出来了。"
        score = _verify_compression_quality(original, compressed)
        assert score < 0.8

    def test_empty_original(self):
        """Empty original returns 1.0."""
        assert _verify_compression_quality("", "anything") == 1.0

    def test_score_bounds(self):
        """Score is always in [0.0, 1.0]."""
        original = "因为所以但是如果决定发现" * 50
        compressed = "x"
        score = _verify_compression_quality(original, compressed)
        assert 0.0 <= score <= 1.0


class TestCompressQualityVerification:
    """Integration tests for quality verification in compress_prompt_context."""

    def _make_packet(self) -> MagicMock:
        packet = MagicMock()
        packet.bridge_brief = ""
        packet.planning_brief = ""
        packet.continuity_brief = ""
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
    async def test_compress_with_quality_check(self):
        """Quality verification is performed after compression."""
        original_text = "因为天气不好，所以他决定留在家里。" * 50
        compressed_text = "因为天气不好，他决定留在家里。" * 50

        call_count = 0

        async def mock_call_with_retry(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "items": [
                    {
                        "id": "ctx_1",
                        "compressed": compressed_text,
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
            quality_check=True,
            adaptive_skip_enabled=False,
        )

        assert result["candidates"] >= 0
        assert "quality_retries" in result

    @pytest.mark.asyncio
    async def test_compress_quality_low_retry(self):
        """When quality is low, retry with simpler approach."""
        original_text = "因为天气不好，所以他决定留在家里。但是如果雨停了，他发现太阳出来了。" * 30
        bad_compressed = "天气。家。"

        call_count = 0

        async def mock_call_with_retry(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"items": [{"id": "ctx_1", "compressed": bad_compressed}]}
            else:
                return {
                    "items": [
                        {
                            "id": "ctx_1",
                            "compressed": "因为天气不好，他决定留在家。但是雨停后，他发现太阳出来了。",
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
            temperature=0.5,
            call_with_retry=mock_call_with_retry,
            on_step=lambda *a: None,
            quality_check=True,
            adaptive_skip_enabled=False,
        )

        assert call_count >= 2, "Expected retry call for low quality"
        assert result["quality_retries"] >= 1

    @pytest.mark.asyncio
    async def test_compress_quality_check_disabled(self):
        """When quality_check=False, skip verification."""
        original_text = "因为天气不好，所以他决定留在家里。" * 50
        compressed_text = "天气。家。"

        call_count = 0

        async def mock_call_with_retry(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"items": [{"id": "ctx_1", "compressed": compressed_text}]}

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
            quality_check=False,
            adaptive_skip_enabled=False,
        )

        assert call_count == 1, "Should only call once, no retry"
        assert result["quality_retries"] == 0

    @pytest.mark.asyncio
    async def test_low_quality_after_retry_preserves_complete_source(self):
        """A failed compression preserves the complete planning source."""
        original_text = "因为天气不好，所以他决定留在家里。但是如果雨停了，他发现太阳出来了。" * 30
        bad_compressed = "天气。家。"
        calls: list[tuple[object, dict]] = []

        async def mock_call_with_retry(task_type, context, **kwargs):
            calls.append((task_type, context))
            return {"items": [{"id": "ctx_1", "compressed": bad_compressed}]}

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
            temperature=0.5,
            call_with_retry=mock_call_with_retry,
            on_step=lambda *a: None,
            quality_check=True,
            quality_min_score=0.9,
            adaptive_skip_enabled=False,
        )

        assert result["quality_failures"] >= 1
        assert result["fallback"] >= 1
        assert creative_report["plot_deviations"][0]["outline_plan"] == original_text
        assert result["samples"][0]["method"] == "fallback_preserve_source"
        assert result["hard_truncation_allowed"] is False
        assert result["preserved_overflow"] >= 1
        assert getattr(calls[1][0], "value", str(calls[1][0])) == "adaptive_compress"
        assert calls[1][1]["mode"] == "fact_priority"

    @pytest.mark.asyncio
    async def test_borderline_compression_can_be_rejected_by_llm_verifier(self):
        """VERIFY_COMPRESSION can veto a borderline heuristic pass."""
        original_text = "因为天气不好，所以他决定留在家里。但是如果雨停了，他发现太阳出来了。" * 30
        compressed_text = "因为天气不好，他决定留在家里。但是雨停后，他发现太阳出来了。" * 5
        calls: list[object] = []
        verify_kwargs: list[dict[str, object]] = []

        async def mock_call_with_retry(task_type, *args, **kwargs):
            calls.append(task_type)
            if getattr(task_type, "value", str(task_type)) == "verify_compression":
                verify_kwargs.append(kwargs)
                return {"quality_score": 0.2, "recommendation": "recompress"}
            return {"items": [{"id": "ctx_1", "compressed": compressed_text}]}

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
            quality_check=True,
            quality_min_score=0.5,
            adaptive_skip_enabled=False,
            llm_verify_enabled=True,
            llm_verify_margin=0.5,
            llm_verify_max_items=1,
        )

        assert result["llm_verify_calls"] == 1
        assert result["llm_verify_rejections"] == 1
        assert result["fallback"] >= 1
        assert verify_kwargs[0]["max_tokens"] == 512
