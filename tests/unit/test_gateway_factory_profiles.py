"""Tests for runtime router construction from desktop profile config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from novel_forge.core.config import Settings
from novel_forge.core.constants import ModelTier, TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.gateway.base import ProviderAdapter
from novel_forge.gateway.factory import ModelRouterBuilder
from novel_forge.gateway.profiles import (
    TONGYI_TOKEN_PLAN_BASE_URL,
    ModelProfile,
    ProfilesConfig,
    TaskRouteEntry,
    get_model_capabilities,
    load_or_import_profiles,
    profile_api_key_env_var,
)
from novel_forge.gateway.types import ModelRequest, ModelResponse


@pytest.fixture(autouse=True)
def _isolate_profile_env(monkeypatch: MonkeyPatch, tmp_path: Path):  # noqa: ANN201
    """Keep profile-key tests away from the developer's real workspace .env."""
    from novel_forge.gateway import profiles as profiles_module

    monkeypatch.setattr(
        profiles_module,
        "get_writable_env_path",
        lambda: tmp_path / ".env",
    )
    profiles_module._PROFILES_CACHE.clear()
    yield
    profiles_module._PROFILES_CACHE.clear()


class _FailingAdapter(ProviderAdapter):
    def __init__(self, provider_name: str) -> None:
        self._provider_name = provider_name
        self.calls: list[ModelRequest] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def default_model(self) -> str | None:
        return "primary-model"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        raise ModelGatewayError("primary profile failed", is_transient=False)


class _StaticAdapter(ProviderAdapter):
    def __init__(self, provider_name: str, content: str) -> None:
        self._provider_name = provider_name
        self._content = content
        self.calls: list[ModelRequest] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def default_model(self) -> str | None:
        return "fallback-model"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(
            content=self._content,
            model_id=request.model_id or self.default_model,
            prompt_tokens=8,
            completion_tokens=16,
            total_tokens=24,
            finish_reason="stop",
        )


def test_model_router_builder_prefers_model_profiles_json(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "deepseek:writer",
                        "display_name": "DeepSeek Writer",
                        "provider": "deepseek",
                        "model_id": "deepseek-chat",
                        "api_key": "sk-real",
                        "base_url": "https://api.deepseek.com",
                    }
                ],
                "routes": {
                    "draft": {
                        "profile_id": "deepseek:writer",
                        "thinking": True,
                        "multi_turn": False,
                    }
                },
                "default_profile_id": "deepseek:writer",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=None,
        premium_model="deepseek-reasoner",
        standard_model="deepseek-chat",
        budget_model="deepseek-chat",
    )

    router = ModelRouterBuilder(settings).build(mock=False)

    assert "deepseek:writer" in router.adapters
    assert "deepseek" in router.adapters
    assert router.default_provider == "deepseek"
    override = router.task_route_overrides[TaskType.DRAFT]
    assert override.provider == "deepseek:writer"
    assert override.model_id == "deepseek-chat"
    assert override.thinking is True


def test_outline_batch_inherits_outline_route_from_model_profiles(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "tongyi:default",
                        "display_name": "Tongyi Default",
                        "provider": "tongyi",
                        "model_id": "qwen-max",
                        "api_key": "sk-tongyi",
                    },
                    {
                        "profile_id": "deepseek:outline",
                        "display_name": "DeepSeek Outline",
                        "provider": "deepseek",
                        "model_id": "deepseek-reasoner",
                        "api_key": "sk-deepseek",
                    },
                ],
                "routes": {
                    "plan_outline": {
                        "profile_id": "deepseek:outline",
                        "thinking": True,
                        "multi_turn": True,
                    },
                },
                "default_profile_id": "tongyi:default",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    router = ModelRouterBuilder(Settings(_env_file=None)).build(mock=False)

    batch = router.task_route_overrides[TaskType.PLAN_OUTLINE_BATCH]
    cont = router.task_route_overrides[TaskType.PLAN_OUTLINE_CONTINUE]

    assert batch.provider == "deepseek:outline"
    assert batch.model_id == "deepseek-reasoner"
    assert batch.thinking is True
    assert batch.multi_turn is False
    assert cont.provider == "deepseek:outline"
    assert cont.model_id == "deepseek-reasoner"
    assert cont.multi_turn is True


def test_profiles_save_omits_api_key_and_load_hydrates_from_settings(tmp_path: Path) -> None:
    config = ProfilesConfig(
        profiles=[
            ModelProfile(
                profile_id="deepseek:writer",
                display_name="DeepSeek Writer",
                provider="deepseek",
                model_id="deepseek-chat",
                api_key="sk-real",
                base_url="https://api.deepseek.com",
            )
        ],
        default_profile_id="deepseek:writer",
    )
    path = tmp_path / "model_profiles.json"

    config.save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "api_key" not in raw["profiles"][0]

    loaded = ProfilesConfig.load(
        path,
        settings=Settings(_env_file=None, deepseek_api_key="sk-from-env"),
    )

    assert loaded.profiles[0].api_key == "sk-from-env"


def test_profiles_round_trip_provider_specific_thinking_modes(tmp_path: Path) -> None:
    config = ProfilesConfig(
        profiles=[
            ModelProfile(
                profile_id="deepseek:v4",
                display_name="DeepSeek V4",
                provider="deepseek",
                model_id="deepseek-v4-pro",
            )
        ],
        routes={
            "draft_chapter": TaskRouteEntry(
                profile_id="deepseek:v4",
                thinking_mode="max",
            )
        },
        fallback_routes={
            "draft_chapter": [TaskRouteEntry(profile_id="minimax:m3", thinking_mode="adaptive")]
        },
    )
    path = tmp_path / "model_profiles.json"

    config.save(path)
    loaded = ProfilesConfig.load(path)

    assert loaded.routes["draft_chapter"].thinking_mode == "max"
    assert loaded.routes["draft_chapter"].thinking is True
    assert loaded.fallback_routes["draft_chapter"][0].thinking_mode == "adaptive"


def test_custom_profile_api_key_round_trips_through_profile_env(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    profile_id = "custom:qwen3-235b-a22b"
    config = ProfilesConfig(
        profiles=[
            ModelProfile(
                profile_id=profile_id,
                display_name="Qwen3 235B",
                provider="custom",
                model_id="qwen3-235b-a22b",
                api_key="sk-custom",
                base_url="https://example.test/v1",
            )
        ],
        default_profile_id=profile_id,
    )
    path = tmp_path / "model_profiles.json"
    config.save(path)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "api_key" not in raw["profiles"][0]

    env_var = profile_api_key_env_var(profile_id)
    assert config.to_env_pairs()[env_var] == "sk-custom"
    (tmp_path / ".env").write_text(f"{env_var}=sk-custom\n", encoding="utf-8")

    loaded = load_or_import_profiles(Settings(_env_file=None))

    assert loaded.profiles[0].api_key == "sk-custom"


def test_model_router_builder_registers_custom_openai_compatible_profile(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    profile_id = "custom:qwen3-235b-a22b"
    (tmp_path / "model_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": profile_id,
                        "display_name": "Qwen3 235B",
                        "provider": "custom",
                        "model_id": "qwen3-235b-a22b",
                        "base_url": "https://example.test/v1",
                    }
                ],
                "routes": {
                    "draft_chapter": {
                        "profile_id": profile_id,
                        "thinking": True,
                        "multi_turn": True,
                    }
                },
                "default_profile_id": profile_id,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        f"{profile_api_key_env_var(profile_id)}=sk-custom\n",
        encoding="utf-8",
    )

    router = ModelRouterBuilder(Settings(_env_file=None)).build(mock=False)

    assert profile_id in router.adapters
    assert "custom" in router.adapters
    assert router.default_provider == "custom"
    override = router.task_route_overrides[TaskType.DRAFT_CHAPTER]
    assert override.provider == profile_id
    assert override.model_id == "qwen3-235b-a22b"
    assert override.thinking is True


def test_model_router_builder_registers_mimo_profile(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    profile_id = "mimo:mimo-v2.5-pro"
    (tmp_path / "model_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": profile_id,
                        "display_name": "小米 MiMo V2.5 Pro",
                        "provider": "mimo",
                        "model_id": "mimo-v2.5-pro",
                    }
                ],
                "routes": {
                    "draft_chapter": {
                        "profile_id": profile_id,
                        "thinking": True,
                        "multi_turn": True,
                    }
                },
                "default_profile_id": profile_id,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("NOVEL_FORGE_MIMO_API_KEY=mimo-test-key\n", encoding="utf-8")

    router = ModelRouterBuilder(Settings(_env_file=None)).build(mock=False)

    assert profile_id in router.adapters
    assert "mimo" in router.adapters
    assert router.default_provider == "mimo"
    adapter = router.adapters[profile_id]
    assert adapter.provider_name == "mimo"
    assert adapter.default_model == "mimo-v2.5-pro"
    override = router.task_route_overrides[TaskType.DRAFT_CHAPTER]
    assert override.provider == profile_id
    assert override.thinking is True


def test_load_or_import_profiles_migrates_legacy_api_key_into_env(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "model_profiles.json"
    path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "deepseek:writer",
                        "display_name": "DeepSeek Writer",
                        "provider": "deepseek",
                        "model_id": "deepseek-chat",
                        "api_key": "sk-legacy",
                    }
                ],
                "routes": {},
                "default_profile_id": "deepseek:writer",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = load_or_import_profiles(Settings(_env_file=None))

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "api_key" not in saved["profiles"][0]
    assert (tmp_path / ".env").read_text(encoding="utf-8").splitlines() == [
        "NOVEL_FORGE_DEEPSEEK_API_KEY=sk-legacy",
    ]
    assert loaded.profiles[0].api_key == "sk-legacy"


def test_outline_batch_inherits_outline_route_from_env(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model_profiles.json").write_text(
        json.dumps({"profiles": [], "routes": {}}, ensure_ascii=False),
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=None,
        default_provider="tongyi",
        tongyi_api_key="sk-tongyi",
        deepseek_api_key="sk-deepseek",
        task_routing=json.dumps(
            {
                "plan_outline": "deepseek:deepseek-reasoner,thinking,multi_turn",
            },
            ensure_ascii=False,
        ),
    )

    router = ModelRouterBuilder(settings).build(mock=False)

    batch = router.task_route_overrides[TaskType.PLAN_OUTLINE_BATCH]
    cont = router.task_route_overrides[TaskType.PLAN_OUTLINE_CONTINUE]

    assert batch.provider == "deepseek"
    assert batch.model_id == "deepseek-reasoner"
    assert batch.thinking is True
    assert batch.multi_turn is False
    assert cont.provider == "deepseek"
    assert cont.model_id == "deepseek-reasoner"
    assert cont.multi_turn is True


def test_outline_continue_inherits_batch_fallback_from_env(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model_profiles.json").write_text(
        json.dumps({"profiles": [], "routes": {}}, ensure_ascii=False),
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=None,
        default_provider="tongyi",
        tongyi_api_key="sk-tongyi",
        deepseek_api_key="sk-deepseek",
        task_fallback_routing=json.dumps(
            {
                "plan_outline_batch": [
                    "deepseek:deepseek-reasoner",
                    "tongyi:qwen-long",
                ],
            },
            ensure_ascii=False,
        ),
    )

    router = ModelRouterBuilder(settings).build(mock=False)

    assert TaskType.PLAN_OUTLINE_BATCH in router.task_fallback_routes
    assert TaskType.PLAN_OUTLINE_CONTINUE in router.task_fallback_routes
    batch = router.task_fallback_routes[TaskType.PLAN_OUTLINE_BATCH]
    cont = router.task_fallback_routes[TaskType.PLAN_OUTLINE_CONTINUE]

    assert len(batch) == 2
    assert len(cont) == 2
    assert batch[0].provider == "deepseek"
    assert batch[0].model_id == "deepseek-reasoner"
    assert cont[1].provider == "tongyi"
    assert cont[1].model_id == "qwen-long"


def test_element_progress_arbiter_inherits_alignment_route_from_env(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model_profiles.json").write_text(
        json.dumps({"profiles": [], "routes": {}}, ensure_ascii=False),
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=None,
        default_provider="tongyi",
        tongyi_api_key="sk-tongyi",
        task_routing=json.dumps(
            {
                "check_alignment": "tongyi:qwen-plus,thinking",
            },
            ensure_ascii=False,
        ),
    )

    router = ModelRouterBuilder(settings).build(mock=False)

    alignment = router.task_route_overrides[TaskType.CHECK_ALIGNMENT]
    arbiter = router.task_route_overrides[TaskType.ELEMENT_PROGRESS_ARBITER]

    assert arbiter.provider == alignment.provider
    assert arbiter.model_id == alignment.model_id
    assert arbiter.thinking == alignment.thinking
    assert arbiter.multi_turn == alignment.multi_turn


def test_model_router_builder_ignores_env_task_routing_when_profiles_exist(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "deepseek:writer",
                        "display_name": "DeepSeek Writer",
                        "provider": "deepseek",
                        "model_id": "deepseek-chat",
                        "api_key": "sk-real",
                    }
                ],
                "routes": {},
                "default_profile_id": "deepseek:writer",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=None,
        deepseek_api_key="sk-real",
        tongyi_api_key="sk-tongyi",
        task_routing=json.dumps(
            {
                "draft": "tongyi:qwen-max,thinking",
            },
            ensure_ascii=False,
        ),
    )

    router = ModelRouterBuilder(settings).build(mock=False)

    assert TaskType.DRAFT not in router.task_route_overrides


def test_model_router_builder_prefers_profile_default_over_env_default(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "deepseek:writer",
                        "display_name": "DeepSeek Writer",
                        "provider": "deepseek",
                        "model_id": "deepseek-chat",
                        "api_key": "sk-real",
                    },
                    {
                        "profile_id": "tongyi:editor",
                        "display_name": "Tongyi Editor",
                        "provider": "tongyi",
                        "model_id": "qwen-max",
                        "api_key": "sk-tongyi",
                    },
                ],
                "routes": {},
                "default_profile_id": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=None,
        deepseek_api_key="sk-real",
        tongyi_api_key="sk-tongyi",
        default_provider="tongyi",
    )

    router = ModelRouterBuilder(settings).build(mock=False)

    assert router.default_provider == "deepseek"


def test_model_router_builder_uses_default_profile_model_for_unrouted_tasks(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "deepseek:writer",
                        "display_name": "DeepSeek Writer",
                        "provider": "deepseek",
                        "model_id": "deepseek-chat",
                        "api_key": "sk-real",
                    }
                ],
                "routes": {},
                "default_profile_id": "deepseek:writer",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=None,
        deepseek_api_key="sk-real",
        premium_model="gpt-4o",
        standard_model="gpt-4o-mini",
        budget_model="gpt-3.5-turbo",
    )

    router = ModelRouterBuilder(settings).build(mock=False)

    assert router._tier_to_model[ModelTier.PREMIUM] == "deepseek-chat"
    assert router._tier_to_model[ModelTier.STANDARD] == "deepseek-chat"
    assert router._tier_to_model[ModelTier.BUDGET] == "deepseek-chat"


def test_model_router_builder_uses_valid_tencent_default_model_from_env() -> None:
    builder = ModelRouterBuilder(Settings(_env_file=None, tencent_api_key="sk-real"))
    builder._register_real_adapters()

    adapter = builder.adapters["tencent"]

    assert getattr(adapter, "default_model", None) == "hy3"


def test_model_router_builder_registers_minimax_from_env_key() -> None:
    builder = ModelRouterBuilder(Settings(_env_file=None, minimax_api_key="sk-real"))
    builder._register_real_adapters()

    adapter = builder.adapters["minimax"]

    assert getattr(adapter, "provider_name", None) == "minimax"
    assert getattr(adapter, "default_model", None) == "MiniMax-M2.7-highspeed"


def test_token_plan_profile_uses_its_dedicated_endpoint_and_key_namespace() -> None:
    profile = ModelProfile(
        profile_id="tongyi_token_plan:qwen3.7-plus",
        display_name="阿里百炼 Token Plan qwen3.7-plus",
        provider="tongyi_token_plan",
        model_id="qwen3.7-plus",
        api_key="sk-sp-test-token-plan",
    )

    adapter = ModelRouterBuilder(Settings(_env_file=None))._create_profile_adapter(profile)

    assert adapter is not None
    assert getattr(adapter, "_base_url", None) == TONGYI_TOKEN_PLAN_BASE_URL
    assert getattr(adapter, "default_model", None) == "qwen3.7-plus"
    assert get_model_capabilities("tongyi_token_plan", "qwen3.7-plus") == (True, True)
    assert ProfilesConfig(profiles=[profile]).to_env_pairs()[
        "NOVEL_FORGE_TONGYI_TOKEN_PLAN_API_KEY"
    ] == "sk-sp-test-token-plan"


def test_profiles_to_env_pairs_exports_fallback_routes() -> None:
    """Routing is no longer exported to .env; verify only keys + default."""
    config = ProfilesConfig(
        profiles=[
            ModelProfile(
                profile_id="deepseek:writer",
                display_name="DeepSeek Writer",
                provider="deepseek",
                model_id="deepseek-chat",
                api_key="sk-deepseek",
            ),
            ModelProfile(
                profile_id="tongyi:editor",
                display_name="Tongyi Editor",
                provider="tongyi",
                model_id="qwen-max",
                api_key="sk-tongyi",
            ),
        ],
        routes={
            "draft_chapter": TaskRouteEntry(profile_id="deepseek:writer"),
        },
        fallback_routes={
            "draft_chapter": [
                TaskRouteEntry(profile_id="tongyi:editor"),
            ],
        },
        default_profile_id="deepseek:writer",
    )

    env_pairs = config.to_env_pairs()

    # Routing no longer in env pairs
    assert "NOVEL_FORGE_TASK_ROUTING" not in env_pairs
    assert "NOVEL_FORGE_TASK_FALLBACK_ROUTING" not in env_pairs
    # API keys and default provider still present
    assert env_pairs["NOVEL_FORGE_DEEPSEEK_API_KEY"] == "sk-deepseek"
    assert env_pairs["NOVEL_FORGE_TONGYI_API_KEY"] == "sk-tongyi"
    assert env_pairs["NOVEL_FORGE_DEFAULT_PROVIDER"] == "deepseek"


def test_profiles_to_env_pairs_fills_missing_fallback_routes() -> None:
    """Routing no longer exported to .env; verify API keys only."""
    config = ProfilesConfig(
        profiles=[
            ModelProfile(
                profile_id="deepseek:writer",
                display_name="DeepSeek Writer",
                provider="deepseek",
                model_id="deepseek-chat",
                api_key="sk-deepseek",
            ),
            ModelProfile(
                profile_id="tongyi:editor",
                display_name="Tongyi Editor",
                provider="tongyi",
                model_id="qwen-max",
                api_key="sk-tongyi",
            ),
        ],
        routes={
            "draft_chapter": TaskRouteEntry(profile_id="deepseek:writer"),
            "adjudicate_contract_completion": TaskRouteEntry(profile_id="deepseek:writer"),
        },
        fallback_routes={
            "draft_chapter": [
                TaskRouteEntry(profile_id="tongyi:editor"),
            ],
        },
        default_profile_id="deepseek:writer",
    )

    env_pairs = config.to_env_pairs()

    # Routing no longer in env pairs
    assert "NOVEL_FORGE_TASK_ROUTING" not in env_pairs
    assert "NOVEL_FORGE_TASK_FALLBACK_ROUTING" not in env_pairs
    # API keys still present
    assert env_pairs["NOVEL_FORGE_DEEPSEEK_API_KEY"] == "sk-deepseek"
    assert env_pairs["NOVEL_FORGE_DEFAULT_PROVIDER"] == "deepseek"


def test_profiles_load_expands_group_bulk_routes_to_missing_tasks(tmp_path: Path) -> None:
    path = tmp_path / "model_profiles.json"
    path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "minimax:fast",
                        "display_name": "MiniMax Fast",
                        "provider": "minimax",
                        "model_id": "MiniMax-M2.7-highspeed",
                        "api_key": "sk-minimax",
                    },
                    {
                        "profile_id": "deepseek:backup",
                        "display_name": "DeepSeek Backup",
                        "provider": "deepseek",
                        "model_id": "deepseek-chat",
                        "api_key": "sk-deepseek",
                    },
                ],
                "routes": {},
                "fallback_routes": {},
                "default_profile_id": "minimax:fast",
                "group_bulk_routes": {
                    "长篇章节创作 / 归档与状态": {
                        "profile_id": "minimax:fast",
                        "thinking": False,
                        "multi_turn": False,
                        "fallback_routes": [
                            {
                                "profile_id": "deepseek:backup",
                                "thinking": False,
                                "multi_turn": False,
                            }
                        ],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = ProfilesConfig.load(path)

    assert loaded.routes["adjudicate_contract_completion"] == TaskRouteEntry(
        profile_id="minimax:fast"
    )
    assert loaded.fallback_routes["adjudicate_contract_completion"] == [
        TaskRouteEntry(profile_id="deepseek:backup")
    ]


def test_model_router_builder_loads_fallback_routes_from_profiles(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "deepseek:writer",
                        "display_name": "DeepSeek Writer",
                        "provider": "deepseek",
                        "model_id": "deepseek-chat",
                        "api_key": "sk-real",
                    },
                    {
                        "profile_id": "tongyi:editor",
                        "display_name": "Tongyi Editor",
                        "provider": "tongyi",
                        "model_id": "qwen-max",
                        "api_key": "sk-tongyi",
                    },
                ],
                "routes": {
                    "draft_chapter": {
                        "profile_id": "deepseek:writer",
                        "thinking": False,
                        "multi_turn": False,
                    }
                },
                "fallback_routes": {
                    "draft_chapter": [
                        {
                            "profile_id": "tongyi:editor",
                            "thinking": False,
                            "multi_turn": False,
                        }
                    ]
                },
                "default_profile_id": "deepseek:writer",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    router = ModelRouterBuilder(Settings(_env_file=None)).build(mock=False)

    assert TaskType.DRAFT_CHAPTER in router.task_fallback_routes
    fallback = router.task_fallback_routes[TaskType.DRAFT_CHAPTER]
    assert len(fallback) == 1
    assert fallback[0].provider == "tongyi:editor"
    assert fallback[0].model_id == "qwen-max"


def test_profile_fallback_routes_win_over_stale_env_fallbacks(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "tongyi:turbo",
                        "display_name": "Qwen Turbo",
                        "provider": "tongyi",
                        "model_id": "qwen-turbo",
                    },
                    {
                        "profile_id": "tongyi:max",
                        "display_name": "Qwen Max",
                        "provider": "tongyi",
                        "model_id": "qwen-max",
                    },
                ],
                "fallback_routes": {
                    "draft_chapter": [
                        {
                            "profile_id": "tongyi:max",
                            "thinking": False,
                            "multi_turn": False,
                        }
                    ]
                },
                "default_profile_id": "tongyi:max",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stale_settings = Settings(
        _env_file=None,
        task_fallback_routing=json.dumps({"draft_chapter": ["tongyi:qwen-turbo"]}),
    )

    loaded = load_or_import_profiles(stale_settings)

    assert [entry.profile_id for entry in loaded.fallback_routes["draft_chapter"]] == ["tongyi:max"]


async def test_profile_fallback_routes_are_used_after_primary_failure(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A fallback chain saved by the desktop UI reaches ModelRouter failover."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "openai:primary",
                        "display_name": "Primary",
                        "provider": "openai",
                        "model_id": "primary-model",
                        "api_key": "sk-primary",
                    },
                    {
                        "profile_id": "openai:fallback",
                        "display_name": "Fallback",
                        "provider": "openai",
                        "model_id": "fallback-model",
                        "api_key": "sk-fallback",
                    },
                ],
                "routes": {
                    "draft_chapter": {
                        "profile_id": "openai:primary",
                        "thinking": False,
                        "multi_turn": False,
                    }
                },
                "fallback_routes": {
                    "draft_chapter": [
                        {
                            "profile_id": "openai:fallback",
                            "thinking": False,
                            "multi_turn": False,
                        }
                    ]
                },
                "default_profile_id": "openai:primary",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    adapters: dict[str, ProviderAdapter] = {}

    def fake_build_adapter(self, module_path, class_name, *args, **kwargs):
        _ = (self, module_path, class_name, args)
        model = str(kwargs.get("default_model") or "")
        if model == "primary-model":
            adapter = _FailingAdapter("primary-profile")
        else:
            adapter = _StaticAdapter("fallback-profile", "来自备用链路的正文")
        adapters[model] = adapter
        return adapter

    monkeypatch.setattr(ModelRouterBuilder, "_build_adapter", fake_build_adapter)

    router = ModelRouterBuilder(
        Settings(_env_file=None, api_call_timeout_s=0, workflow_timeout_s=0)
    ).build(mock=False)

    response = await router.route(
        ModelRequest(
            task_type=TaskType.DRAFT_CHAPTER,
            messages=[{"role": "user", "content": "draft"}],
            max_tokens=128,
        )
    )

    assert response.content == "来自备用链路的正文"
    assert len(adapters["primary-model"].calls) == 1
    assert len(adapters["fallback-model"].calls) == 1


def test_mock_fallback_logs_unregistered_providers(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    caplog,
) -> None:
    """Verify MockAdapter fallback logs WARNING with unregistered provider list."""
    import logging

    monkeypatch.chdir(tmp_path)
    (tmp_path / "model_profiles.json").write_text(
        json.dumps({"profiles": [], "routes": {}}, ensure_ascii=False),
        encoding="utf-8",
    )

    # Mock ollama adapter to fail so fallback triggers (ollama always tries to register without API key)
    def mock_build_adapter(self, module_path, class_name, *args, **kwargs):
        if "ollama" in module_path:
            return None
        from novel_forge.gateway.adapters.openai import OpenAIAdapter

        return OpenAIAdapter("sk-fake")

    monkeypatch.setattr(
        "novel_forge.gateway.factory.ModelRouterBuilder._build_adapter",
        mock_build_adapter,
    )

    settings = Settings(_env_file=None)

    router = ModelRouterBuilder(settings).build(mock=False)

    assert router.default_provider == "mock"
    assert "mock" in router.adapters

    warning_messages = [
        r"mock_fallback",
        r"unregistered_providers=",
        r"deepseek: missing_api_key",
        r"tongyi: missing_api_key",
        r"tongyi_coding: missing_api_key",
        r"kimi: missing_api_key",
        r"openai: missing_api_key",
        r"anthropic: missing_api_key",
        r"tencent: missing_api_key",
        r"minimax: missing_api_key",
        r"opencode: missing_api_key",
        r"note=configure .env or model_profiles.json",
    ]
    for msg in warning_messages:
        assert any(
            msg in record.message for record in caplog.records if record.levelno == logging.WARNING
        ), f"Missing: {msg}"


def test_fallback_chain_filters_embedding_only_models(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Embedding-only models must never be registered as generation fallbacks.

    Observed in production: ``tongyi:text-embedding-v4`` was configured as a
    generation fallback and burned a route attempt with an OpenAI-compat 404
    ("Unsupported model ... for OpenAI compatibility mode").
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "tongyi:qwen-max",
                        "display_name": "Tongyi Qwen Max",
                        "provider": "tongyi",
                        "model_id": "qwen-max",
                        "api_key": "sk-tongyi",
                        "base_url": "",
                    },
                    {
                        "profile_id": "tongyi:text-embedding-v4",
                        "display_name": "Tongyi Embedding v4",
                        "provider": "tongyi",
                        "model_id": "text-embedding-v4",
                        "api_key": "sk-tongyi",
                        "base_url": "",
                    },
                ],
                "routes": {},
                "fallback_routes": {
                    "draft_chapter": [
                        {
                            "profile_id": "tongyi:qwen-max",
                            "thinking": False,
                            "thinking_mode": "off",
                            "multi_turn": False,
                        },
                        {
                            "profile_id": "tongyi:text-embedding-v4",
                            "thinking": False,
                            "thinking_mode": "off",
                            "multi_turn": False,
                        },
                    ]
                },
                "default_profile_id": "tongyi:qwen-max",
                "group_bulk_routes": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "novel_forge.gateway.factory.ModelRouterBuilder._build_adapter",
        lambda self, provider, *args, **kwargs: _StaticAdapter(provider, "ok"),
    )
    settings = Settings(_env_file=None)
    router = ModelRouterBuilder(settings).build(mock=False)

    fallbacks = router.task_fallback_routes.get(TaskType.DRAFT_CHAPTER, [])
    assert len(fallbacks) == 1
    assert fallbacks[0].provider == "tongyi:qwen-max"
    assert all("embedding" not in str(entry.model_id or "") for entry in fallbacks)
