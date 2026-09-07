"""Volcengine Ark adapter for Seedream image and Seedance video generation.

The visual endpoints live under Ark's pay-as-you-go ``/api/v3`` surface even
when the text gateway is configured for an Agent Plan URL.  The adapter keeps
that transport detail behind the provider-neutral film contract and preserves
``asset://`` references used by Ark's trusted actor asset library.

Official references (checked 2026-08):
* https://www.volcengine.com/docs/82379/1829186
* https://www.volcengine.com/docs/82379/1520757
* https://www.volcengine.com/docs/82379/2315856
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlsplit

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
_VIDEO_MODES = {
    FilmGenerationMode.TEXT_TO_VIDEO,
    FilmGenerationMode.IMAGE_TO_VIDEO,
    FilmGenerationMode.FIRST_LAST_FRAME,
    FilmGenerationMode.VIDEO_CONTINUATION,
    FilmGenerationMode.REFERENCE_TO_VIDEO,
    FilmGenerationMode.SUBJECT_REFERENCE_VIDEO,
}


def _visual_api_root(base_url: str) -> str:
    """Resolve an Ark host without inheriting a chat-only ``/api/plan/v3`` path."""

    candidate = (base_url or "https://ark.cn-beijing.volces.com").strip().rstrip("/")
    parsed = urlsplit(candidate)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return candidate.removesuffix("/api/plan/v3").removesuffix("/api/v3")


class VolcengineArkFilmProvider:
    """Calls Seedream synchronously and Seedance through Ark's task API."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://ark.cn-beijing.volces.com",
        timeout_s: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Volcengine Ark API key is required")
        self._api_key = api_key
        self._base_url = _visual_api_root(base_url)
        self._timeout_s = timeout_s
        self._transport = transport

    @property
    def provider_id(self) -> str:
        return "volcengine_ark"

    async def submit(self, request: FilmGenerationRequest) -> FilmProviderTask:
        if request.mode in _IMAGE_MODES:
            return await self._submit_image(request)
        if request.mode in _VIDEO_MODES:
            return await self._submit_video(request)
        raise FilmProviderError(f"Volcengine Ark does not support mode {request.mode.value}")

    async def query(self, task: FilmProviderTask) -> FilmProviderTask:
        if task.provider_id != self.provider_id:
            raise FilmProviderError("Cannot query a task owned by another provider")
        if task.state in {FilmProviderTaskState.SUCCEEDED, FilmProviderTaskState.FAILED}:
            return task
        if not task.task_id:
            raise FilmProviderError("Volcengine Ark task_id is required for polling")
        payload = await self._request_json(
            "GET", f"/api/v3/contents/generations/tasks/{task.task_id}"
        )
        status = str(payload.get("status") or "").lower()
        state = normalize_task_status(status)
        raw_content = payload.get("content")
        content: dict[str, Any] = raw_content if isinstance(raw_content, dict) else {}
        video_url = str(content.get("video_url") or payload.get("video_url") or "")
        return task.model_copy(
            update={
                "state": state,
                "asset_urls": [video_url] if video_url else list(task.asset_urls),
                "error_message": self._error_message(payload)
                if state == FilmProviderTaskState.FAILED
                else "",
                "trace_id": str(payload.get("request_id") or task.trace_id),
                "raw": payload,
            }
        )

    async def _submit_image(self, request: FilmGenerationRequest) -> FilmProviderTask:
        model_id = request.model_id or "doubao-seedream-5-0-lite-260128"
        body: dict[str, Any] = {
            "model": model_id,
            "prompt": request.prompt,
            "size": str(request.metadata.get("size") or self._image_size(request)),
            "response_format": str(request.metadata.get("response_format") or "url"),
            "watermark": request.watermark,
        }
        if request.references:
            body["image"] = [item.url for item in request.references]
        if request.seed is not None:
            body["seed"] = request.seed
        if request.mode == FilmGenerationMode.IMAGE_SET or request.image_count > 1:
            body["sequential_image_generation"] = "auto"
            body["sequential_image_generation_options"] = {
                "max_images": min(request.image_count, 15)
            }
        payload = await self._request_json("POST", "/api/v3/images/generations", json=body)
        urls = self._image_urls(payload)
        if not urls:
            raise FilmProviderError(
                "Volcengine Ark image generation returned no image", retryable=True
            )
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
        model_id = request.model_id or "doubao-seedance-2-0-260128"
        content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
        role_by_kind = {
            "first_frame": "first_frame",
            "last_frame": "last_frame",
            "first_clip": "first_frame",
            "subject": "reference_image",
            "reference_image": "reference_image",
            "style_reference": "reference_image",
        }
        for reference in request.references:
            if reference.kind == "driving_audio":
                content.append({"type": "audio_url", "audio_url": {"url": reference.url}})
                continue
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": reference.url},
                    "role": role_by_kind.get(reference.kind, "reference_image"),
                }
            )
        body: dict[str, Any] = {
            "model": model_id,
            "content": content,
            "ratio": request.aspect_ratio,
            "resolution": request.resolution.lower(),
            "duration": request.duration_s,
            "watermark": request.watermark,
            "generate_audio": bool(request.metadata.get("generate_audio", True)),
            "return_last_frame": bool(request.metadata.get("return_last_frame", True)),
        }
        if request.seed is not None:
            body["seed"] = request.seed
        callback_url = str(request.metadata.get("callback_url") or "")
        if callback_url:
            body["callback_url"] = callback_url
        payload = await self._request_json("POST", "/api/v3/contents/generations/tasks", json=body)
        task_id = str(payload.get("id") or payload.get("task_id") or "")
        if not task_id:
            raise FilmProviderError(
                "Volcengine Ark video generation returned no task id", retryable=True
            )
        return FilmProviderTask(
            provider_id=self.provider_id,
            model_id=model_id,
            mode=request.mode,
            state=FilmProviderTaskState.PENDING,
            task_id=task_id,
            trace_id=str(payload.get("request_id") or ""),
            raw=payload,
        )

    @staticmethod
    def _image_size(request: FilmGenerationRequest) -> str:
        width, height = {
            "16:9": (2560, 1440),
            "9:16": (1440, 2560),
            "4:3": (2304, 1728),
            "3:4": (1728, 2304),
            "1:1": (2048, 2048),
        }.get(request.aspect_ratio, (2560, 1440))
        return f"{width}x{height}"

    @staticmethod
    def _image_urls(payload: dict[str, Any]) -> list[str]:
        raw_data = payload.get("data")
        data = raw_data if isinstance(raw_data, list) else []
        urls: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            image_base64 = str(item.get("b64_json") or "")
            if url:
                urls.append(url)
            elif image_base64:
                try:
                    base64.b64decode(image_base64, validate=True)
                except ValueError:
                    continue
                urls.append(f"data:image/png;base64,{image_base64}")
        return urls

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_s,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method,
                    path,
                    json=json,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:800]
            raise FilmProviderError(
                f"Volcengine Ark HTTP {exc.response.status_code}: {detail}",
                retryable=exc.response.status_code >= 500 or exc.response.status_code == 429,
            ) from exc
        except httpx.RequestError as exc:
            raise FilmProviderError(
                f"Volcengine Ark request failed: {exc}", retryable=True
            ) from exc
        if not isinstance(payload, dict):
            raise FilmProviderError("Volcengine Ark returned a non-object response")
        if payload.get("error"):
            raise FilmProviderError(
                f"Volcengine Ark API error: {self._error_message(payload)}",
                retryable=False,
            )
        return payload

    @staticmethod
    def _error_message(payload: dict[str, Any]) -> str:
        raw_error = payload.get("error")
        if isinstance(raw_error, dict):
            return str(raw_error.get("message") or raw_error.get("code") or "unknown error")
        return str(raw_error or payload.get("message") or "unknown error")
