"""Alibaba Model Studio adapter for Wan 2.7 image/video generation."""

from __future__ import annotations

from typing import Any

import httpx

from novel_forge.film.providers._client import normalize_task_status
from novel_forge.film.providers.base import (
    FilmGenerationMode,
    FilmGenerationRequest,
    FilmProviderError,
    FilmProviderTask,
    FilmProviderTaskState,
)

_IMAGE_MODES = {
    FilmGenerationMode.TEXT_TO_IMAGE,
    FilmGenerationMode.IMAGE_EDIT,
    FilmGenerationMode.IMAGE_SET,
}
_VIDEO_MODES = (
    set(FilmGenerationMode)
    - _IMAGE_MODES
    - {
        FilmGenerationMode.SUBJECT_REFERENCE_VIDEO,
    }
)


class BailianFilmProvider:
    """Calls current Wan 2.7 synchronous image and asynchronous video APIs."""

    def __init__(
        self,
        *,
        api_key: str,
        workspace_id: str = "",
        region: str = "cn-beijing",
        base_url: str = "",
        timeout_s: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Bailian API key is required")
        if base_url:
            resolved_base_url = base_url.rstrip("/")
        elif workspace_id:
            domain = (
                f"{workspace_id}.ap-southeast-1.maas.aliyuncs.com"
                if region == "ap-southeast-1"
                else f"{workspace_id}.cn-beijing.maas.aliyuncs.com"
            )
            resolved_base_url = f"https://{domain}"
        else:
            resolved_base_url = (
                "https://dashscope-intl.aliyuncs.com"
                if region == "ap-southeast-1"
                else "https://dashscope.aliyuncs.com"
            )
        self._api_key = api_key
        self._base_url = resolved_base_url
        self._timeout_s = timeout_s
        self._transport = transport

    @property
    def provider_id(self) -> str:
        return "bailian"

    async def submit(self, request: FilmGenerationRequest) -> FilmProviderTask:
        if request.mode in _IMAGE_MODES:
            return await self._submit_image(request)
        if request.mode in _VIDEO_MODES:
            return await self._submit_video(request)
        raise FilmProviderError(f"Bailian does not support mode {request.mode.value}")

    async def query(self, task: FilmProviderTask) -> FilmProviderTask:
        if task.provider_id != self.provider_id:
            raise FilmProviderError("Cannot query a task owned by another provider")
        if task.state in {FilmProviderTaskState.SUCCEEDED, FilmProviderTaskState.FAILED}:
            return task
        if not task.task_id:
            raise FilmProviderError("Bailian task_id is required for polling")
        payload = await self._request_json("GET", f"/api/v1/tasks/{task.task_id}")
        raw_output = payload.get("output")
        output: dict[str, Any] = raw_output if isinstance(raw_output, dict) else {}
        raw_status = str(output.get("task_status") or "").upper()
        state = normalize_task_status(raw_status)
        urls = self._asset_urls(output)
        return task.model_copy(
            update={
                "state": state,
                "asset_urls": urls,
                "error_message": str(output.get("message") or payload.get("message") or "")
                if state == FilmProviderTaskState.FAILED
                else "",
                "raw": payload,
            }
        )

    async def _submit_image(self, request: FilmGenerationRequest) -> FilmProviderTask:
        model_id = request.model_id or "wan2.7-image-pro"
        content: list[dict[str, str]] = []
        for reference in request.references:
            content.append({"image": reference.url})
        content.append({"text": request.prompt})
        parameters: dict[str, Any] = {
            "n": request.image_count,
            "size": str(request.metadata.get("size") or "2K"),
            "watermark": request.watermark,
        }
        if request.mode == FilmGenerationMode.IMAGE_SET:
            parameters["enable_sequential"] = True
        if request.seed is not None:
            parameters["seed"] = request.seed
        payload = await self._request_json(
            "POST",
            "/api/v1/services/aigc/multimodal-generation/generation",
            json={
                "model": model_id,
                "input": {"messages": [{"role": "user", "content": content}]},
                "parameters": parameters,
            },
        )
        urls = self._asset_urls(payload.get("output"))
        if not urls:
            raise FilmProviderError("Bailian image generation returned no image", retryable=True)
        return FilmProviderTask(
            provider_id=self.provider_id,
            model_id=model_id,
            mode=request.mode,
            state=FilmProviderTaskState.SUCCEEDED,
            asset_urls=urls,
            trace_id=str(payload.get("request_id") or ""),
            raw=payload,
        )

    async def _submit_video(self, request: FilmGenerationRequest) -> FilmProviderTask:
        defaults = {
            FilmGenerationMode.TEXT_TO_VIDEO: "wan2.7-t2v",
            FilmGenerationMode.IMAGE_TO_VIDEO: "wan2.7-i2v",
            FilmGenerationMode.FIRST_LAST_FRAME: "wan2.7-i2v",
            FilmGenerationMode.VIDEO_CONTINUATION: "wan2.7-i2v",
            FilmGenerationMode.REFERENCE_TO_VIDEO: "wan2.7-r2v",
        }
        model_id = request.model_id or defaults[request.mode]
        input_payload: dict[str, Any] = {
            "prompt": request.prompt,
            "media": [{"type": item.kind, "url": item.url} for item in request.references],
        }
        if request.negative_prompt:
            input_payload["negative_prompt"] = request.negative_prompt[:500]
        parameters: dict[str, Any] = {
            "resolution": request.resolution,
            "duration": request.duration_s,
            "prompt_extend": request.prompt_optimizer,
            "watermark": request.watermark,
        }
        if request.mode == FilmGenerationMode.TEXT_TO_VIDEO:
            parameters["ratio"] = request.aspect_ratio
            audio_url = str(request.metadata.get("audio_url") or "")
            if audio_url:
                input_payload["audio_url"] = audio_url
        if request.seed is not None:
            parameters["seed"] = request.seed
        payload = await self._request_json(
            "POST",
            "/api/v1/services/aigc/video-generation/video-synthesis",
            json={"model": model_id, "input": input_payload, "parameters": parameters},
            headers={"X-DashScope-Async": "enable"},
        )
        raw_output = payload.get("output")
        output: dict[str, Any] = raw_output if isinstance(raw_output, dict) else {}
        task_id = str(output.get("task_id") or "")
        if not task_id:
            raise FilmProviderError("Bailian video generation returned no task_id", retryable=True)
        return FilmProviderTask(
            provider_id=self.provider_id,
            model_id=model_id,
            mode=request.mode,
            state=FilmProviderTaskState.PENDING,
            task_id=task_id,
            trace_id=str(payload.get("request_id") or ""),
            raw=payload,
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            **(headers or {}),
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_s,
                transport=self._transport,
            ) as client:
                response = await client.request(method, path, json=json, headers=request_headers)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:800]
            raise FilmProviderError(
                f"Bailian HTTP {exc.response.status_code}: {detail}",
                retryable=exc.response.status_code >= 500 or exc.response.status_code == 429,
            ) from exc
        except httpx.RequestError as exc:
            raise FilmProviderError(f"Bailian request failed: {exc}", retryable=True) from exc
        if not isinstance(payload, dict):
            raise FilmProviderError("Bailian returned a non-object response")
        if payload.get("code"):
            raise FilmProviderError(
                f"Bailian API error {payload.get('code')}: {payload.get('message')}",
                retryable=str(payload.get("code")) in {"Throttling", "InternalError"},
            )
        return payload

    @classmethod
    def _asset_urls(cls, value: Any) -> list[str]:
        urls: list[str] = []

        def visit(item: Any) -> None:
            if isinstance(item, str) and item.startswith(("http://", "https://", "data:")):
                urls.append(item)
                return
            if isinstance(item, list):
                for child in item:
                    visit(child)
                return
            if not isinstance(item, dict):
                return
            for key, child in item.items():
                normalized = str(key).lower()
                if normalized in {"url", "image", "video_url", "image_url", "result_url"}:
                    visit(child)
                elif normalized in {
                    "results",
                    "choices",
                    "content",
                    "images",
                    "video",
                    "output",
                    "message",
                    "data",
                }:
                    visit(child)

        visit(value)
        return list(dict.fromkeys(urls))
