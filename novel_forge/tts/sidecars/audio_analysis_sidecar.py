"""Shared HTTP protocol for isolated ASR and alignment runtimes."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from starlette.datastructures import UploadFile

_MAX_AUDIO_BYTES = 512 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024


class AnalysisBackend(Protocol):
    backend_id: str

    def health(self) -> dict[str, Any]: ...

    def capabilities(self) -> dict[str, Any]: ...

    def models(self) -> list[dict[str, Any]]: ...

    def self_test(self, model_id: str) -> dict[str, Any]: ...

    def install_model(
        self,
        *,
        plugin_id: str,
        model_id: str,
        revision: str,
        local_path: str,
        accept_license: bool,
    ) -> dict[str, Any]: ...

    def delete_model(self, *, plugin_id: str, model_id: str) -> dict[str, Any]: ...

    def transcribe(
        self, audio_path: Path, *, language: str, expected_text: str
    ) -> dict[str, Any]: ...

    def align(self, audio_path: Path, *, text: str, language: str) -> dict[str, Any]: ...

    def diagnostics(self) -> dict[str, Any]: ...


class SelfTestRequest(BaseModel):
    model_id: str = ""


class ModelInstallRequest(BaseModel):
    plugin_id: str = ""
    model_id: str
    revision: str = "main"
    local_path: str = ""
    accept_license: bool = False


class ModelDeleteRequest(BaseModel):
    plugin_id: str = ""
    model_id: str


def create_analysis_app(
    backend_factory: Callable[[], AnalysisBackend],
    *,
    title: str,
    version: str = "1.0",
) -> FastAPI:
    """Expose the stable Novel Forge sidecar contract around a lazy backend."""

    backend: AnalysisBackend | None = None

    def runtime() -> AnalysisBackend:
        nonlocal backend
        if backend is None:
            backend = backend_factory()
        return backend

    app = FastAPI(title=title, version=version)

    @app.get("/v1/health")
    async def health() -> dict[str, Any]:
        try:
            return await asyncio.to_thread(runtime().health)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, Any]:
        return await asyncio.to_thread(runtime().capabilities)

    @app.get("/v1/version")
    async def runtime_version() -> dict[str, Any]:
        return {"runtime": runtime().backend_id, "version": version, "protocol_version": 1}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {"models": await asyncio.to_thread(runtime().models)}

    @app.post("/v1/models/install")
    async def install_model(request: ModelInstallRequest) -> dict[str, Any]:
        return await asyncio.to_thread(
            runtime().install_model,
            plugin_id=request.plugin_id,
            model_id=request.model_id,
            revision=request.revision,
            local_path=request.local_path,
            accept_license=request.accept_license,
        )

    @app.post("/v1/models/delete")
    async def delete_model(request: ModelDeleteRequest) -> dict[str, Any]:
        return await asyncio.to_thread(
            runtime().delete_model,
            plugin_id=request.plugin_id,
            model_id=request.model_id,
        )

    @app.post("/v1/self-test")
    async def self_test(request: SelfTestRequest) -> dict[str, Any]:
        return await asyncio.to_thread(runtime().self_test, request.model_id)

    @app.get("/v1/diagnostics")
    async def diagnostics() -> dict[str, Any]:
        return await asyncio.to_thread(runtime().diagnostics)

    async def with_upload(
        upload: UploadFile,
        operation: Callable[[Path], dict[str, Any]],
    ) -> dict[str, Any]:
        suffix = Path(upload.filename or "audio.wav").suffix or ".wav"
        too_large = False
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            path = Path(handle.name)
            total = 0
            while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > _MAX_AUDIO_BYTES:
                    too_large = True
                    break
                handle.write(chunk)
        if too_large:
            path.unlink(missing_ok=True)
            raise HTTPException(status_code=413, detail="音频文件超过 512 MB 限制。")
        if not total:
            path.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="音频文件为空。")
        try:
            return await asyncio.to_thread(operation, path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            path.unlink(missing_ok=True)

    async def upload_from(request: Request) -> tuple[UploadFile, dict[str, str]]:
        try:
            form = await request.form()
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"无法解析音频表单：{exc}") from exc
        upload = form.get("audio")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=422, detail="缺少音频文件。")
        fields = {
            key: str(value)
            for key, value in form.multi_items()
            if key != "audio" and isinstance(value, str)
        }
        return upload, fields

    @app.post("/v1/transcribe")
    async def transcribe(request: Request) -> dict[str, Any]:
        audio, fields = await upload_from(request)
        language = fields.get("language", "auto")
        expected_text = fields.get("expected_text", "")
        return await with_upload(
            audio,
            lambda path: runtime().transcribe(
                path,
                language=language,
                expected_text=expected_text,
            ),
        )

    @app.post("/v1/align")
    async def align(request: Request) -> dict[str, Any]:
        audio, fields = await upload_from(request)
        text = fields.get("text", "")
        language = fields.get("language", "auto")
        if not text.strip():
            raise HTTPException(status_code=422, detail="强制对齐文本不能为空。")
        return await with_upload(
            audio,
            lambda path: runtime().align(path, text=text, language=language),
        )

    return app
