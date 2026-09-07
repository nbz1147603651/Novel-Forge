from novel_forge.gateway.embedding_config import is_embedding_model
from novel_forge.gateway.profiles import ModelProfile, get_model_capabilities


def test_ollama_profile_does_not_require_api_key() -> None:
    profile = ModelProfile(
        profile_id="ollama:llama3.2",
        display_name="llama3.2",
        provider="ollama",
        model_id="llama3.2",
        api_key="",
    )
    assert profile.is_key_configured is True


def test_remote_profile_requires_api_key() -> None:
    profile = ModelProfile(
        profile_id="openai:gpt-4o",
        display_name="gpt-4o",
        provider="openai",
        model_id="gpt-4o",
        api_key="",
    )
    assert profile.is_key_configured is False


def test_is_embedding_model_flags_ollama_embedding_models() -> None:
    assert is_embedding_model("ollama", "nomic-embed-text") is True
    assert is_embedding_model("ollama", "qwen3-embedding:0.6b") is True
    assert is_embedding_model("ollama", "llama3.2") is False
    assert is_embedding_model("tencent", "hunyuan-embedding") is True


def test_custom_provider_reuses_known_model_capabilities() -> None:
    assert get_model_capabilities("custom", "qwen3-235b-a22b") == (True, True)
