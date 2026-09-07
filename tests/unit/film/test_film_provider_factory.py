from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.film.providers.factory import create_film_provider
from novel_forge.film.providers.minimax import MiniMaxFilmProvider


def test_minimax_film_provider_uses_dedicated_credentials_and_api_root() -> None:
    settings = SimpleNamespace(
        film_minimax_api_key="film-secret",
        film_minimax_base_url="https://api.minimaxi.com/v1",
        tts_minimax_api_key="tts-secret",
        tts_minimax_base_url="https://api.minimax.io/v1",
    )

    provider = create_film_provider("minimax", settings)

    assert isinstance(provider, MiniMaxFilmProvider)
    assert provider._base_url == "https://api.minimaxi.com"


def test_minimax_film_provider_does_not_fall_back_to_tts_credentials() -> None:
    settings = SimpleNamespace(
        film_minimax_api_key="",
        tts_minimax_api_key="tts-secret",
    )

    with pytest.raises(RuntimeError, match="映界 MiniMax API Key"):
        create_film_provider("minimax", settings)
