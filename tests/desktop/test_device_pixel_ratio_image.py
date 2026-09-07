"""Tests for devicePixelRatio aware ring image (I-8)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.desktop


def _make_page(palette: list[str]):
    from novel_forge.desktop.pages.standalone import token_analytics

    # Create a minimal instance via __new__ (bypass __init__ which may be heavy)
    page = token_analytics.TokenAnalyticsTab.__new__(token_analytics.TokenAnalyticsTab)
    page._chart_palette = MagicMock(return_value=palette)
    return page


def test_render_uses_dpr_2x_size_for_high_dpi():
    """When devicePixelRatio is 2.0, image is created with 2x logical size."""
    from novel_forge.desktop.pages.standalone import token_analytics

    page = _make_page(["#000000", "#ffffff"])

    with patch.object(token_analytics, "QImage") as mock_qimage, \
         patch.object(token_analytics, "QPainter") as mock_qpainter:
        # Make mock return values behave like real QImage/QPainter
        mock_image_instance = MagicMock()
        mock_qimage.return_value = mock_image_instance

        mock_painter_instance = MagicMock()
        mock_qpainter.return_value = mock_painter_instance

        # Mock _encode_png_data_url so it doesn't need a real encoded image
        with patch.object(
            type(page),
            "_encode_png_data_url",
            return_value="data:image/png;base64,AAAA",
        ):
            # Mock devicePixelRatioF to return 2.0
            with patch.object(type(page), "devicePixelRatioF", return_value=2.0):
                page._render_model_cost_ring_image(
                    [("a", 50.0), ("b", 50.0)], total_cost_cny=1.0
                )

        # Verify QImage was called with 352x352 (176 logical * 2.0 dpr)
        mock_qimage.assert_called_once()
        args = mock_qimage.call_args[0]
        # First two args are width, height
        assert args[0] == 352, f"Expected width=352 (176*2), got {args[0]}"
        assert args[1] == 352, f"Expected height=352 (176*2), got {args[1]}"
        # Verify setDevicePixelRatio was called with 2.0
        mock_image_instance.setDevicePixelRatio.assert_called_once_with(2.0)


def test_render_uses_dpr_1x_for_normal_dpi():
    """When devicePixelRatio is 1.0, image is 176x176 (no scaling)."""
    from novel_forge.desktop.pages.standalone import token_analytics

    page = _make_page(["#000000"])

    with patch.object(token_analytics, "QImage") as mock_qimage, \
         patch.object(token_analytics, "QPainter") as mock_qpainter:
        mock_image_instance = MagicMock()
        mock_qimage.return_value = mock_image_instance

        mock_painter_instance = MagicMock()
        mock_qpainter.return_value = mock_painter_instance

        with patch.object(
            type(page),
            "_encode_png_data_url",
            return_value="data:image/png;base64,AAAA",
        ):
            with patch.object(type(page), "devicePixelRatioF", return_value=1.0):
                page._render_model_cost_ring_image(
                    [("a", 100.0)], total_cost_cny=1.0
                )

        mock_qimage.assert_called_once()
        args = mock_qimage.call_args[0]
        assert args[0] == 176
        assert args[1] == 176
        mock_image_instance.setDevicePixelRatio.assert_called_once_with(1.0)