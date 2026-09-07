"""Managed lightweight Qwen3-ASR and ForcedAligner sidecar."""

from __future__ import annotations

import argparse
import os
import platform
from pathlib import Path
from typing import Any

from novel_forge.core.config import get_application_models_dir
from novel_forge.tts.sidecars.audio_analysis_sidecar import create_analysis_app


def _language_name(value: str) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "auto", "unknown"}:
        return None
    return {
        "zh": "Chinese",
        "zh-cn": "Chinese",
        "chinese": "Chinese",
        "yue": "Cantonese",
        "cantonese": "Cantonese",
        "en": "English",
        "english": "English",
        "ja": "Japanese",
        "japanese": "Japanese",
        "ko": "Korean",
        "korean": "Korean",
    }.get(normalized, value)


class Qwen3ASRBackend:
    """Load only the requested 0.6B analysis model inside an isolated process."""

    backend_id = "qwen3-asr"

    def __init__(self) -> None:
        root = Path(
            os.getenv("NOVEL_FORGE_AUDIO_MODELS_ROOT", str(get_application_models_dir() / "audio"))
        )
        self.asr_dir = Path(
            os.getenv(
                "QWEN3_ASR_MODEL_DIR",
                str(root / "plugins/qwen3-asr-0.6b/main"),
            )
        )
        self.aligner_dir = Path(
            os.getenv(
                "QWEN3_ALIGNER_MODEL_DIR",
                str(root / "plugins/qwen3-forced-aligner-0.6b/main"),
            )
        )
        self._asr: Any = None
        self._aligner: Any = None

    @staticmethod
    def _module() -> tuple[Any, Any, Any]:
        import torch
        from qwen_asr import Qwen3ASRModel, Qwen3ForcedAligner

        return torch, Qwen3ASRModel, Qwen3ForcedAligner

    @staticmethod
    def _model_ready(path: Path) -> bool:
        return path.joinpath("config.json").is_file() and any(path.glob("*.safetensors"))

    def _load_kwargs(self, torch: Any) -> dict[str, Any]:
        if bool(getattr(getattr(torch, "backends", None), "mps", None)) and (
            torch.backends.mps.is_available()
        ):
            return {"device_map": "mps", "dtype": torch.float16}
        return {"device_map": "cpu", "dtype": torch.float32}

    def _asr_model(self) -> Any:
        if self._asr is None:
            if not self._model_ready(self.asr_dir):
                raise FileNotFoundError("Qwen3-ASR 0.6B 模型未安装。")
            torch, model_type, _ = self._module()
            self._asr = model_type.from_pretrained(
                str(self.asr_dir),
                max_inference_batch_size=1,
                max_new_tokens=512,
                **self._load_kwargs(torch),
            )
        return self._asr

    def _aligner_model(self) -> Any:
        if self._aligner is None:
            if not self._model_ready(self.aligner_dir):
                raise FileNotFoundError("Qwen3 ForcedAligner 0.6B 模型未安装。")
            torch, _, model_type = self._module()
            self._aligner = model_type.from_pretrained(
                str(self.aligner_dir),
                **self._load_kwargs(torch),
            )
        return self._aligner

    def health(self) -> dict[str, Any]:
        self._module()
        return {
            "status": "ok",
            "backend": self.backend_id,
            "asr_ready": self._model_ready(self.asr_dir),
            "aligner_ready": self._model_ready(self.aligner_dir),
            "asr_loaded": self._asr is not None,
            "aligner_loaded": self._aligner is not None,
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.backend_id,
            "asr": True,
            "forced_alignment": True,
            "known_text_alignment": True,
            "vad": False,
            "granularity": ["word", "character", "segment"],
            "languages": [
                "zh",
                "yue",
                "en",
                "fr",
                "de",
                "it",
                "ja",
                "ko",
                "pt",
                "ru",
                "es",
            ],
        }

    def models(self) -> list[dict[str, Any]]:
        return [
            {
                "model_id": "Qwen/Qwen3-ASR-0.6B",
                "local_path": str(self.asr_dir.resolve()),
                "installed": self._model_ready(self.asr_dir),
                "loaded": self._asr is not None,
            },
            {
                "model_id": "Qwen/Qwen3-ForcedAligner-0.6B",
                "local_path": str(self.aligner_dir.resolve()),
                "installed": self._model_ready(self.aligner_dir),
                "loaded": self._aligner is not None,
            },
        ]

    def self_test(self, model_id: str) -> dict[str, Any]:
        aligner = "ForcedAligner" in model_id
        path = self.aligner_dir if aligner else self.asr_dir
        ready = self._model_ready(path)
        return {
            "ok": ready,
            "model_id": model_id,
            "detail": "模型文件完整。" if ready else "运行时可用，但模型文件不完整。",
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
        if path is not None:
            if plugin_id == "qwen3-forced-aligner-0.6b":
                self.aligner_dir = path
                self._aligner = None
            else:
                self.asr_dir = path
                self._asr = None
        target = self.aligner_dir if plugin_id == "qwen3-forced-aligner-0.6b" else self.asr_dir
        return {
            "ok": self._model_ready(target),
            "model_id": model_id,
            "local_path": str(target),
            "installed": self._model_ready(target),
        }

    def delete_model(self, *, plugin_id: str, model_id: str) -> dict[str, Any]:
        if plugin_id == "qwen3-forced-aligner-0.6b":
            self._aligner = None
        else:
            self._asr = None
        return {"ok": True, "plugin_id": plugin_id, "model_id": model_id}

    def transcribe(self, audio_path: Path, *, language: str, expected_text: str) -> dict[str, Any]:
        del expected_text
        results = self._asr_model().transcribe(
            audio=str(audio_path),
            language=_language_name(language),
        )
        result = results[0]
        return {
            "text": str(getattr(result, "text", "")),
            "language": str(getattr(result, "language", language) or language),
            "items": [],
        }

    def align(self, audio_path: Path, *, text: str, language: str) -> dict[str, Any]:
        results = self._aligner_model().align(
            audio=str(audio_path),
            text=text,
            language=_language_name(language) or "Chinese",
        )
        result = results[0]
        items = [
            {
                "text": str(getattr(item, "text", "")),
                "start": float(getattr(item, "start_time", 0.0)),
                "end": float(getattr(item, "end_time", 0.0)),
                "unit": "character" if len(str(getattr(item, "text", ""))) == 1 else "word",
            }
            for item in list(getattr(result, "items", []) or [])
        ]
        return {"items": items, "language": _language_name(language) or language}

    def diagnostics(self) -> dict[str, Any]:
        return {
            "backend": self.backend_id,
            "python": platform.python_version(),
            "asr_dir": str(self.asr_dir.resolve()),
            "aligner_dir": str(self.aligner_dir.resolve()),
            "asr_ready": self._model_ready(self.asr_dir),
            "aligner_ready": self._model_ready(self.aligner_dir),
            "asr_loaded": self._asr is not None,
            "aligner_loaded": self._aligner is not None,
        }


def create_app() -> Any:
    return create_analysis_app(
        Qwen3ASRBackend,
        title="Novel Forge Qwen3-ASR sidecar",
        version=os.getenv("NOVEL_FORGE_SIDECAR_RUNTIME_VERSION", "1"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Novel Forge Qwen3-ASR sidecar")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8012, type=int)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
