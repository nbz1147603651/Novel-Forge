"""Managed WhisperX ASR and forced-alignment sidecar."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
from pathlib import Path
from typing import Any

from novel_forge.core.config import get_application_models_dir
from novel_forge.tts.sidecars.audio_analysis_sidecar import create_analysis_app


class WhisperXBackend:
    backend_id = "whisperx"

    def __init__(self) -> None:
        self.model_name = os.getenv("WHISPERX_MODEL", "large-v2")
        self.device = os.getenv("WHISPERX_DEVICE", "cpu")
        self.compute_type = os.getenv(
            "WHISPERX_COMPUTE_TYPE", "float16" if self.device == "cuda" else "int8"
        )
        root = Path(
            os.getenv("NOVEL_FORGE_AUDIO_MODELS_ROOT", str(get_application_models_dir() / "audio"))
        )
        self.model_dir = Path(os.getenv("WHISPERX_MODEL_DIR", str(root / "plugins/whisperx/main")))
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self._model: Any = None

    @staticmethod
    def _module() -> Any:
        import whisperx  # type: ignore[import-not-found]

        return whisperx

    def _asr_model(self) -> Any:
        if self._model is None:
            whisperx = self._module()
            self._model = whisperx.load_model(
                self.model_name,
                self.device,
                compute_type=self.compute_type,
                download_root=str(self.model_dir),
            )
        return self._model

    def health(self) -> dict[str, Any]:
        self._module()
        return {"status": "ok", "backend": self.backend_id, "model_loaded": self._model is not None}

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.backend_id,
            "asr": True,
            "forced_alignment": True,
            "vad": True,
            "known_text_alignment": True,
            "granularity": ["word", "segment"],
            "languages": ["*"],
        }

    def models(self) -> list[dict[str, Any]]:
        return [
            {
                "model_id": self.model_name,
                "local_path": str(self.model_dir.resolve()),
                "installed": self._files_ready(),
                "loaded": self._model is not None,
            }
        ]

    def self_test(self, model_id: str) -> dict[str, Any]:
        self._module()
        ready = self._files_ready()
        return {
            "ok": ready,
            "model_id": model_id or self.model_name,
            "detail": "模型文件完整。" if ready else "WhisperX 运行时可用，但模型尚未下载。",
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
        del plugin_id, revision, local_path, accept_license
        if model_id:
            self.model_name = model_id
        if not self._files_ready():
            self._asr_model()
        return {
            "ok": self._files_ready(),
            "model_id": self.model_name,
            "local_path": str(self.model_dir.resolve()),
            "installed": self._files_ready(),
        }

    def delete_model(self, *, plugin_id: str, model_id: str) -> dict[str, Any]:
        del plugin_id
        self._model = None
        shutil.rmtree(self.model_dir, ignore_errors=True)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "model_id": model_id or self.model_name}

    def transcribe(self, audio_path: Path, *, language: str, expected_text: str) -> dict[str, Any]:
        del expected_text
        whisperx = self._module()
        audio = whisperx.load_audio(str(audio_path))
        result = self._asr_model().transcribe(audio, batch_size=8)
        return {
            "text": "".join(str(item.get("text") or "") for item in result.get("segments", [])),
            "language": result.get("language") or language,
            "segments": result.get("segments", []),
        }

    def align(self, audio_path: Path, *, text: str, language: str) -> dict[str, Any]:
        whisperx = self._module()
        audio = whisperx.load_audio(str(audio_path))
        resolved_language = language
        if resolved_language in {"", "auto"}:
            probe = self._asr_model().transcribe(audio, batch_size=8)
            resolved_language = str(probe.get("language") or "en")
        duration = len(audio) / 16000.0
        align_model, metadata = whisperx.load_align_model(
            language_code=resolved_language,
            device=self.device,
            model_dir=str(self.model_dir),
        )
        result = whisperx.align(
            [{"text": text, "start": 0.0, "end": duration}],
            align_model,
            metadata,
            audio,
            self.device,
            return_char_alignments=True,
        )
        words = [
            {
                "text": str(item.get("word") or ""),
                "start": float(item.get("start") or 0.0),
                "end": float(item.get("end") or item.get("start") or 0.0),
                "confidence": item.get("score"),
                "unit": "word",
            }
            for item in result.get("word_segments", [])
            if item.get("word") and item.get("start") is not None
        ]
        return {"language": resolved_language, "text": text, "items": words}

    def diagnostics(self) -> dict[str, Any]:
        return {
            "backend": self.backend_id,
            "python": platform.python_version(),
            "device": self.device,
            "compute_type": self.compute_type,
            "model": self.model_name,
            "model_dir": str(self.model_dir.resolve()),
            "model_loaded": self._model is not None,
        }

    def _files_ready(self) -> bool:
        try:
            model_files = [item for item in self.model_dir.rglob("model.bin") if item.is_file()]
            config_files = [item for item in self.model_dir.rglob("config.json") if item.is_file()]
            return any(item.stat().st_size > 0 for item in model_files) and any(
                item.stat().st_size > 0 for item in config_files
            )
        except OSError:
            return False


def create_app() -> Any:
    return create_analysis_app(
        WhisperXBackend,
        title="Novel Forge WhisperX sidecar",
        version=os.getenv("NOVEL_FORGE_SIDECAR_RUNTIME_VERSION", "3"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Novel Forge WhisperX sidecar")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8013, type=int)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
