"""Qwen sidecar model registry protocol remains lightweight and persistent."""

from __future__ import annotations

import pytest

from novel_forge.tts.sidecars.qwen3_sidecar import (
    Qwen3Runtime,
    Qwen3SidecarConfig,
    resolve_qwen_device,
)


def test_qwen_sidecar_registers_lists_and_deletes_local_model(tmp_path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    config = Qwen3SidecarConfig(voice_store_dir=str(tmp_path / "voices"))
    runtime = Qwen3Runtime(config)
    runtime.register_model("Qwen/demo", str(model))
    models = runtime.list_models()
    assert models[0]["model_id"] == "Qwen/demo"

    restored = Qwen3Runtime(config)
    assert restored.list_models()[0]["local_path"] == str(model.resolve())
    restored.unregister_model("Qwen/demo")
    assert restored.list_models() == []


def test_qwen_sidecar_fails_closed_when_model_is_not_registered(tmp_path) -> None:
    runtime = Qwen3Runtime(Qwen3SidecarConfig(voice_store_dir=str(tmp_path / "voices")))

    with pytest.raises(RuntimeError, match="模型中心安装"):
        runtime._load_model("Qwen/missing")


def test_qwen_device_auto_selection_is_portable() -> None:
    assert resolve_qwen_device("auto", "auto", cuda_available=True, mps_available=True) == (
        "cuda:0",
        "bfloat16",
    )
    assert resolve_qwen_device("auto", "auto", cuda_available=False, mps_available=True) == (
        "mps",
        "float32",
    )


def test_qwen_sidecar_evicts_resident_models_to_configured_limit(tmp_path) -> None:
    runtime = Qwen3Runtime(
        Qwen3SidecarConfig(
            voice_store_dir=str(tmp_path / "voices"),
            max_resident_models=1,
        )
    )
    runtime._models["first"] = object()
    runtime._clone_prompts["voice"] = object()

    runtime._evict_for_new_model()

    assert runtime._models == {}
    assert runtime._clone_prompts == {}


async def test_qwen_sidecar_releases_models_after_idle_budget(tmp_path) -> None:
    runtime = Qwen3Runtime(
        Qwen3SidecarConfig(
            voice_store_dir=str(tmp_path / "voices"),
            idle_unload_s=1,
        )
    )
    runtime._models["formal"] = object()
    runtime._clone_prompts["voice"] = object()
    runtime._last_activity_at -= 2

    released = await runtime.release_if_idle()

    assert released == 1
    assert runtime._models == {}
    assert runtime._clone_prompts == {}
    assert resolve_qwen_device("auto", "auto", cuda_available=False, mps_available=False) == (
        "cpu",
        "float32",
    )
