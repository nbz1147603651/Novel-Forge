"""Managed sherpa-onnx SenseVoice ASR sidecar."""

from __future__ import annotations

import argparse
import os
import platform
import wave
from pathlib import Path
from typing import Any

import numpy as np

from novel_forge.core.config import get_application_models_dir
from novel_forge.tts.sidecars.audio_analysis_sidecar import create_analysis_app


class SherpaOnnxBackend:
    backend_id = "sherpa-onnx"

    def __init__(self) -> None:
        root = Path(
            os.getenv("NOVEL_FORGE_AUDIO_MODELS_ROOT", str(get_application_models_dir() / "audio"))
        )
        self.model_dir = Path(
            os.getenv(
                "SHERPA_SENSEVOICE_MODEL_DIR",
                str(root / "plugins/sherpa-sensevoice-int8/main"),
            )
        )
        self.vad_path = Path(
            os.getenv(
                "SHERPA_VAD_MODEL",
                str(root / "plugins/silero-vad-onnx/main/silero_vad.onnx"),
            )
        )
        self._recognizer: Any = None

    @staticmethod
    def _module() -> Any:
        import sherpa_onnx  # type: ignore[import-not-found]

        return sherpa_onnx

    def _model_file(self) -> Path:
        candidates = list(self.model_dir.rglob("model.int8.onnx"))
        if not candidates:
            candidates = list(self.model_dir.rglob("model.onnx"))
        if not candidates:
            raise FileNotFoundError("SenseVoice 模型未安装：缺少 model.int8.onnx/model.onnx。")
        return candidates[0]

    def _tokens_file(self) -> Path:
        candidates = list(self.model_dir.rglob("tokens.txt"))
        if not candidates:
            raise FileNotFoundError("SenseVoice 模型未安装：缺少 tokens.txt。")
        return candidates[0]

    def _asr(self) -> Any:
        if self._recognizer is None:
            sherpa_onnx = self._module()
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                sense_voice=str(self._model_file()),
                tokens=str(self._tokens_file()),
                num_threads=max(1, min(4, os.cpu_count() or 1)),
                use_itn=True,
                debug=False,
            )
        return self._recognizer

    def health(self) -> dict[str, Any]:
        self._module()
        return {
            "status": "ok",
            "backend": self.backend_id,
            "model_ready": self._files_ready(),
            "model_loaded": self._recognizer is not None,
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.backend_id,
            "asr": True,
            "forced_alignment": False,
            "vad": self.vad_path.is_file(),
            "known_text_alignment": False,
            "granularity": ["token", "character", "segment"],
            "languages": ["zh", "en", "ja", "ko", "yue"],
        }

    def models(self) -> list[dict[str, Any]]:
        return [
            {
                "model_id": "sensevoice-zh-en-ja-ko-yue-int8",
                "local_path": str(self.model_dir.resolve()),
                "installed": self._files_ready(),
                "loaded": self._recognizer is not None,
            },
            {
                "model_id": "silero-vad.onnx",
                "local_path": str(self.vad_path.resolve()),
                "installed": self.vad_path.is_file(),
            },
        ]

    def self_test(self, model_id: str) -> dict[str, Any]:
        self._module()
        ready = self._files_ready()
        return {
            "ok": ready,
            "model_id": model_id or "sensevoice-zh-en-ja-ko-yue-int8",
            "detail": "模型文件完整。" if ready else "运行时可用，但 SenseVoice 模型尚未安装。",
        }

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
        path = Path(local_path).expanduser().resolve() if local_path else None
        if plugin_id == "silero-vad-onnx" and path is not None:
            self.vad_path = path / "silero_vad.onnx"
            installed = self.vad_path.is_file()
        elif path is not None:
            self.model_dir = path
            installed = self._files_ready()
        else:
            installed = self._files_ready()
        return {
            "ok": installed,
            "model_id": model_id,
            "local_path": str(path or self.model_dir),
            "installed": installed,
        }

    def delete_model(self, *, plugin_id: str, model_id: str) -> dict[str, Any]:
        self._recognizer = None
        return {"ok": True, "plugin_id": plugin_id, "model_id": model_id}

    def transcribe(self, audio_path: Path, *, language: str, expected_text: str) -> dict[str, Any]:
        del expected_text
        samples, sample_rate = self._read_wave(audio_path)
        stream = self._asr().create_stream()
        stream.accept_waveform(sample_rate, samples)
        self._asr().decode_stream(stream)
        result = stream.result
        timestamps = list(getattr(result, "timestamps", []) or [])
        tokens = list(getattr(result, "tokens", []) or [])
        items = []
        for index, token in enumerate(tokens):
            start = float(timestamps[index]) if index < len(timestamps) else 0.0
            next_start = float(timestamps[index + 1]) if index + 1 < len(timestamps) else start
            items.append(
                {"text": str(token), "start": start, "end": max(start, next_start), "unit": "token"}
            )
        return {
            "text": str(getattr(result, "text", "")),
            "language": language,
            "items": items,
        }

    def align(self, audio_path: Path, *, text: str, language: str) -> dict[str, Any]:
        del audio_path, text, language
        raise ValueError(
            "Sherpa SenseVoice 负责 ASR/VAD，不提供已知文本强制对齐；请使用 Qwen3 ForcedAligner 或 WhisperX。"
        )

    def diagnostics(self) -> dict[str, Any]:
        return {
            "backend": self.backend_id,
            "python": platform.python_version(),
            "model_dir": str(self.model_dir.resolve()),
            "model_ready": self._files_ready(),
            "vad_path": str(self.vad_path.resolve()),
            "vad_ready": self.vad_path.is_file(),
            "model_loaded": self._recognizer is not None,
        }

    def _files_ready(self) -> bool:
        try:
            self._model_file()
            self._tokens_file()
        except FileNotFoundError:
            return False
        return True

    @staticmethod
    def _read_wave(path: Path) -> tuple[np.ndarray, int]:
        try:
            with wave.open(str(path), "rb") as handle:
                channels = handle.getnchannels()
                sample_width = handle.getsampwidth()
                sample_rate = handle.getframerate()
                frames = handle.readframes(handle.getnframes())
        except (wave.Error, EOFError) as exc:
            raise ValueError("Sherpa sidecar 当前要求 PCM WAV 输入。") from exc
        if sample_width != 2:
            raise ValueError("Sherpa sidecar 当前仅支持 16-bit PCM WAV。")
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        return samples, sample_rate


def create_app() -> Any:
    return create_analysis_app(
        SherpaOnnxBackend,
        title="Novel Forge sherpa-onnx sidecar",
        version=os.getenv("NOVEL_FORGE_SIDECAR_RUNTIME_VERSION", "4"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Novel Forge sherpa-onnx sidecar")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8014, type=int)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
