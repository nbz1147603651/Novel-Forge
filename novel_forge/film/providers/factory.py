"""Runtime construction for film media providers."""

from __future__ import annotations

from typing import Any

from .bailian import BailianFilmProvider
from .base import FilmGenerationProvider, FilmProviderError
from .minimax import MiniMaxFilmProvider
from .volcengine_ark import VolcengineArkFilmProvider


def create_film_provider(provider_id: str, settings: Any) -> FilmGenerationProvider:
    normalized = str(provider_id or "").strip().lower()
    if normalized == "bailian":
        api_key = str(getattr(settings, "tts_dashscope_api_key", "") or "").strip()
        if not api_key:
            raise FilmProviderError("未配置阿里百炼 API Key，请先在火候 / 声腔平台设置中配置")
        return BailianFilmProvider(
            api_key=api_key,
            base_url=str(
                getattr(settings, "tts_dashscope_base_url", "") or "https://dashscope.aliyuncs.com"
            ),
        )
    if normalized == "minimax":
        api_key = str(getattr(settings, "film_minimax_api_key", "") or "").strip()
        if not api_key:
            raise FilmProviderError("未配置映界 MiniMax API Key，请先在火候 / 映界平台设置中配置")
        return MiniMaxFilmProvider(
            api_key=api_key,
            base_url=str(
                getattr(settings, "film_minimax_base_url", "") or "https://api.minimaxi.com"
            ),
        )
    if normalized in {"volcengine_ark", "volcengine", "ark", "doubao"}:
        api_key = str(getattr(settings, "volcengine_ark_api_key", "") or "").strip()
        if not api_key:
            raise FilmProviderError("未配置火山方舟 API Key，请先在火候 / 声腔平台设置中配置")
        return VolcengineArkFilmProvider(
            api_key=api_key,
            base_url=str(
                getattr(settings, "volcengine_ark_base_url", "")
                or "https://ark.cn-beijing.volces.com"
            ),
        )
    raise FilmProviderError(f"不支持的影视生成平台：{provider_id}")
