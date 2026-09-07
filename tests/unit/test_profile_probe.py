from __future__ import annotations

from typing import Any

from novel_forge.gateway.profile_probe import probe_model_profile
from novel_forge.gateway.profiles import ModelProfile


async def test_probe_model_profile_rejects_missing_key_without_network() -> None:
    result = await probe_model_profile(
        ModelProfile(
            profile_id="openai:gpt",
            display_name="GPT",
            provider="openai",
            model_id="gpt-4o",
        )
    )

    assert result.ok is False
    assert result.detail == "未配置 API Key"
    assert result.latency_ms is None


async def test_probe_model_profile_runs_adapter_health_check(monkeypatch: Any) -> None:
    class Adapter:
        last_health_error = ""
        closed = False

        async def health_check(self) -> bool:
            return True

        async def shutdown(self) -> None:
            self.closed = True

    adapter = Adapter()
    monkeypatch.setattr(
        "novel_forge.gateway.profile_probe._make_adapter",
        lambda _profile: adapter,
    )

    result = await probe_model_profile(
        ModelProfile(
            profile_id="openai:gpt",
            display_name="GPT",
            provider="openai",
            model_id="gpt-4o",
            api_key="secret",
        )
    )

    assert result.ok is True
    assert result.detail.startswith("连通 ")
    assert result.latency_ms is not None
    assert adapter.closed is True


async def test_engine_probe_request_preserves_existing_key_only_for_probe(monkeypatch: Any) -> None:
    from novel_forge.app_service.model_profile_probe import (
        ModelProfileProbeRequest,
        probe_model_profile_request,
    )

    previous = ModelProfile(
        profile_id="openai:previous",
        display_name="Previous",
        provider="openai",
        model_id="gpt-4o",
        api_key="existing-secret",
    )
    seen: list[ModelProfile] = []

    class Config:
        def get_profile(self, profile_id: str) -> ModelProfile | None:
            return previous if profile_id == "openai:previous" else None

    async def fake_probe(profile: ModelProfile):
        seen.append(profile)
        from novel_forge.gateway.profile_probe import ModelProfileProbeResult

        return ModelProfileProbeResult(True, "连通 1ms", 1, True, True)

    monkeypatch.setattr(
        "novel_forge.app_service.model_profile_probe.load_or_import_profiles",
        lambda: Config(),
    )
    monkeypatch.setattr(
        "novel_forge.app_service.model_profile_probe.probe_model_profile",
        fake_probe,
    )

    result = await probe_model_profile_request(
        ModelProfileProbeRequest(
            profile_id="openai:new",
            previous_profile_id="openai:previous",
            provider="OPENAI",
            model="gpt-5.6",
            api_key_action="preserve",
            base_url=" https://example.test/v1 ",
        )
    )

    assert result.ok is True
    assert seen == [
        ModelProfile(
            profile_id="openai:new",
            display_name="openai:new",
            provider="openai",
            model_id="gpt-5.6",
            api_key="existing-secret",
            base_url="https://example.test/v1",
        )
    ]
