"""Contract tests for managed audio-analysis sidecars."""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest

from novel_forge.tts.model_center.runtimes import audio_runtime_catalog
from novel_forge.tts.sidecars.audio_analysis_sidecar import create_analysis_app
from novel_forge.tts.sidecars.qwen3_asr_sidecar import Qwen3ASRBackend
from novel_forge.tts.sidecars.sherpa_onnx_sidecar import SherpaOnnxBackend
from novel_forge.tts.sidecars.whisperx_sidecar import WhisperXBackend


class _FakeBackend:
    backend_id = "fake"

    def health(self) -> dict[str, Any]:
        return {"status": "ok"}

    def capabilities(self) -> dict[str, Any]:
        return {"asr": True}

    def models(self) -> list[dict[str, Any]]:
        return [{"model_id": "fake-model"}]

    def self_test(self, model_id: str) -> dict[str, Any]:
        return {"ok": True, "model_id": model_id}

    def install_model(
        self,
        *,
        plugin_id: str,
        model_id: str,
        revision: str,
        local_path: str,
        accept_license: bool,
    ) -> dict[str, Any]:
        del revision, accept_license
        return {
            "ok": True,
            "plugin_id": plugin_id,
            "model_id": model_id,
            "local_path": local_path,
            "installed": True,
        }

    def delete_model(self, *, plugin_id: str, model_id: str) -> dict[str, Any]:
        return {"ok": True, "plugin_id": plugin_id, "model_id": model_id}

    def transcribe(self, audio_path: Path, *, language: str, expected_text: str) -> dict[str, Any]:
        assert audio_path.read_bytes() == b"wave"
        return {"text": expected_text or "ok", "language": language}

    def align(self, audio_path: Path, *, text: str, language: str) -> dict[str, Any]:
        assert audio_path.is_file()
        return {"items": [{"text": text, "start": 0.0, "end": 1.0}], "language": language}

    def diagnostics(self) -> dict[str, Any]:
        return {"backend": self.backend_id}


async def test_shared_analysis_sidecar_protocol() -> None:
    pytest.importorskip("multipart")
    app = create_analysis_app(_FakeBackend, title="test")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/v1") as client:
        assert (await client.get("/health")).json()["status"] == "ok"
        assert (await client.get("/capabilities")).json()["asr"] is True
        response = await client.post(
            "/transcribe",
            data={"language": "zh", "expected_text": "你好"},
            files={"audio": ("voice.wav", b"wave", "audio/wav")},
        )
        assert response.status_code == 200
        assert response.json() == {"text": "你好", "language": "zh"}
        installed = await client.post(
            "/models/install",
            json={"plugin_id": "fake", "model_id": "fake-model", "local_path": "/model"},
        )
        assert installed.status_code == 200
        assert installed.json()["installed"] is True
        deleted = await client.post(
            "/models/delete",
            json={"plugin_id": "fake", "model_id": "fake-model"},
        )
        assert deleted.status_code == 200
        assert deleted.json()["ok"] is True


def test_whisperx_and_sherpa_are_managed_runtimes() -> None:
    descriptors = {item.runtime_id: item for item in audio_runtime_catalog()}
    assert descriptors["whisperx"].managed_process is True
    assert descriptors["whisperx"].entry_module == "novel_forge.tts.sidecars.whisperx_sidecar"
    assert descriptors["sherpa-onnx"].managed_process is True
    assert descriptors["sherpa-onnx"].entry_module == "novel_forge.tts.sidecars.sherpa_onnx_sidecar"
    assert descriptors["whisperx"].health_path == "/v1/health"
    assert descriptors["sherpa-onnx"].health_path == "/v1/health"
    assert descriptors["qwen3-asr"].entry_module == "novel_forge.tts.sidecars.qwen3_asr_sidecar"
    assert descriptors["qwen3-asr"].supported_plugins == [
        "qwen3-asr-0.6b",
        "qwen3-forced-aligner-0.6b",
    ]


def test_qwen3_asr_reports_lightweight_installed_models(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NOVEL_FORGE_AUDIO_MODELS_ROOT", str(tmp_path / "models"))
    backend = Qwen3ASRBackend()
    monkeypatch.setattr(backend, "_module", lambda: (object(), object(), object()))

    assert backend.health()["aligner_ready"] is False
    for path in (backend.asr_dir, backend.aligner_dir):
        path.mkdir(parents=True)
        path.joinpath("config.json").write_text("{}", encoding="utf-8")
        path.joinpath("model.safetensors").write_bytes(b"weights")

    assert backend.health()["asr_ready"] is True
    assert backend.health()["aligner_ready"] is True
    assert all(item["installed"] for item in backend.models())


def test_whisperx_reports_installed_only_after_files_exist(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NOVEL_FORGE_AUDIO_MODELS_ROOT", str(tmp_path / "models"))
    backend = WhisperXBackend()

    assert backend.models()[0]["installed"] is False

    model_marker = backend.model_dir / "model.bin"
    config_marker = backend.model_dir / "config.json"

    def fake_load():
        model_marker.write_bytes(b"weights")
        config_marker.write_text("{}", encoding="utf-8")
        return object()

    monkeypatch.setattr(backend, "_asr_model", fake_load)
    result = backend.install_model(
        plugin_id="whisperx",
        model_id="large-v2",
        revision="main",
        local_path="",
        accept_license=False,
    )

    assert result["installed"] is True
    assert backend.models()[0]["installed"] is True


def test_sherpa_wave_reader_downmixes_pcm16(tmp_path) -> None:
    path = tmp_path / "stereo.wav"
    frames = np.array([[32767, -32768], [16384, 16384]], dtype="<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(frames.tobytes())

    samples, sample_rate = SherpaOnnxBackend._read_wave(path)

    assert sample_rate == 16000
    assert len(samples) == 2
    assert abs(float(samples[0])) < 0.001
    assert 0.49 < float(samples[1]) < 0.51


def test_whisperx_known_text_alignment_normalizes_words(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NOVEL_FORGE_AUDIO_MODELS_ROOT", str(tmp_path / "models"))
    backend = WhisperXBackend()

    class FakeWhisperX:
        @staticmethod
        def load_audio(_path):
            return np.zeros(16000, dtype=np.float32)

        @staticmethod
        def load_align_model(**_kwargs):
            return object(), {"language": "zh"}

        @staticmethod
        def align(*_args, **_kwargs):
            return {"word_segments": [{"word": "你好", "start": 0.1, "end": 0.5, "score": 0.98}]}

    monkeypatch.setattr(backend, "_module", lambda: FakeWhisperX)
    result = backend.align(tmp_path / "voice.wav", text="你好", language="zh")

    assert result["language"] == "zh"
    assert result["items"] == [
        {"text": "你好", "start": 0.1, "end": 0.5, "confidence": 0.98, "unit": "word"}
    ]
