"""MiniMax adapter for Image-01, Hailuo and H3 asynchronous video generation.

H3 model family (``MiniMax-H3*``) uses the Open Platform v2 endpoint
(``/v2/video_generation``) with 4–15 s output and 768P/2K resolutions;
legacy Hailuo models keep the v1 ``/video_generation`` endpoint.  Both share
the ``subject_reference`` wire format, so reference shots work across the
family (H3 Ref2VA accepts up to 9 reference images).
"""

from __future__ import annotations

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

_IMAGE_MODES = {FilmGenerationMode.TEXT_TO_IMAGE, FilmGenerationMode.IMAGE_EDIT}
_VIDEO_MODES = {
    FilmGenerationMode.TEXT_TO_VIDEO,
    FilmGenerationMode.IMAGE_TO_VIDEO,
    FilmGenerationMode.FIRST_LAST_FRAME,
    FilmGenerationMode.SUBJECT_REFERENCE_VIDEO,
    FilmGenerationMode.REFERENCE_TO_VIDEO,
}

_H3_MODEL_PREFIX = "MiniMax-H3"
# H3 output spec: 4–15 s with direct 768P or 2K generation.
_H3_DURATION_RANGE = (4, 15)
_H3_RESOLUTIONS = {"768P", "2K"}
_MAX_SUBJECT_REFERENCES = 9
# H3 Open Platform v2 ratio presets (adaptive is chosen by the platform default).
_H3_RATIOS = {"adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"}


def _is_h3_model(model_id: str) -> bool:
    return str(model_id or "").startswith(_H3_MODEL_PREFIX)


def _normalize_h3_ratio(aspect_ratio: str) -> str:
    """Map the provider-neutral aspect ratio onto the H3 ratio preset.

    Empty string means "let the platform default (adaptive) decide"; the
    aspect ratio is only sent when it is an explicit H3 preset so the
    platform never receives an unsupported value.
    """
    ratio = str(aspect_ratio or "").strip()
    return ratio if ratio in _H3_RATIOS else ""


def _minimax_api_root(base_url: str) -> str:
    """Return the account-region API origin without inheriting a v1 path."""

    candidate = (base_url or "https://api.minimaxi.com").strip().rstrip("/")
    parsed = urlsplit(candidate)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return candidate.removesuffix("/v1").removesuffix("/v2")


class MiniMaxFilmProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.minimaxi.com",
        timeout_s: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("MiniMax API key is required")
        self._api_key = api_key
        self._base_url = _minimax_api_root(base_url)
        self._timeout_s = timeout_s
        self._transport = transport

    @property
    def provider_id(self) -> str:
        return "minimax"

    async def submit(self, request: FilmGenerationRequest) -> FilmProviderTask:
        if request.mode in _IMAGE_MODES:
            return await self._submit_image(request)
        if request.mode in _VIDEO_MODES:
            if _is_h3_model(request.model_id or self._default_h3_model(request.mode)):
                return await self._submit_h3_v2(request)
            return await self._submit_video(request)
        raise FilmProviderError(f"MiniMax does not support mode {request.mode.value}")

    @staticmethod
    def _default_h3_model(mode: FilmGenerationMode) -> str:
        return {
            FilmGenerationMode.TEXT_TO_VIDEO: "MiniMax-H3",
            FilmGenerationMode.IMAGE_TO_VIDEO: "MiniMax-H3",
            FilmGenerationMode.FIRST_LAST_FRAME: "MiniMax-H3",
            FilmGenerationMode.SUBJECT_REFERENCE_VIDEO: "MiniMax-H3",
            FilmGenerationMode.REFERENCE_TO_VIDEO: "MiniMax-H3",
        }.get(mode, "MiniMax-H3")

    async def query(self, task: FilmProviderTask) -> FilmProviderTask:
        if task.provider_id != self.provider_id:
            raise FilmProviderError("Cannot query a task owned by another provider")
        if task.state in {
            FilmProviderTaskState.SUCCEEDED,
            FilmProviderTaskState.FAILED,
            FilmProviderTaskState.CANCELLED,
        }:
            return task
        if not task.task_id:
            raise FilmProviderError("MiniMax task_id is required for polling")
        if task.api_version == "v2":
            return await self._query_h3_v2(task)
        payload = await self._request_json(
            "GET",
            "/v1/query/video_generation",
            params={"task_id": task.task_id},
        )
        raw_status = str(payload.get("status") or "").lower()
        state = normalize_task_status(raw_status)
        file_id = str(payload.get("file_id") or task.file_id or "")
        asset_urls = list(task.asset_urls)
        backup_urls = list(task.backup_urls)
        if state == FilmProviderTaskState.SUCCEEDED and file_id and not asset_urls:
            file_payload = await self._request_json(
                "GET",
                "/v1/files/retrieve",
                params={"file_id": file_id},
            )
            raw_file_data = file_payload.get("file")
            file_data: dict[str, Any] = raw_file_data if isinstance(raw_file_data, dict) else {}
            download_url = str(
                file_data.get("download_url") or file_payload.get("download_url") or ""
            )
            backup_url = str(file_data.get("backup_download_url") or "")
            if download_url:
                asset_urls.append(download_url)
            if backup_url:
                backup_urls.append(backup_url)
        return task.model_copy(
            update={
                "state": state,
                "file_id": file_id,
                "asset_urls": asset_urls,
                "backup_urls": backup_urls,
                "error_message": str(payload.get("error_message") or "")
                if state == FilmProviderTaskState.FAILED
                else "",
                "raw": payload,
            }
        )

    async def _query_h3_v2(self, task: FilmProviderTask) -> FilmProviderTask:
        """Poll the H3 Open Platform v2 task endpoint.

        Path-scoped task id (``/v2/query/video_generation/{task_id}``) with
        ``task.content.url`` for video output and ``task.content.prompt`` for
        H3 Context IR output.
        """

        payload = await self._request_json(
            "GET",
            f"/v2/query/video_generation/{task.task_id}",
        )
        raw_task = payload.get("task")
        task_data: dict[str, Any] = raw_task if isinstance(raw_task, dict) else {}
        raw_status = str(task_data.get("status") or "").lower()
        state = normalize_task_status(raw_status)
        asset_urls = list(task.asset_urls)
        backup_urls = list(task.backup_urls)
        if state == FilmProviderTaskState.SUCCEEDED and not asset_urls:
            raw_content = task_data.get("content")
            content: dict[str, Any] = raw_content if isinstance(raw_content, dict) else {}
            video_url = str(content.get("url") or "")
            if video_url:
                asset_urls.append(video_url)
        raw_content = task_data.get("content")
        content = raw_content if isinstance(raw_content, dict) else {}
        enhanced_prompt = str(content.get("prompt") or "")
        raw_error = task_data.get("error")
        error_detail = ""
        if isinstance(raw_error, dict):
            error_detail = str(raw_error.get("message") or "")
        return task.model_copy(
            update={
                "state": state,
                "asset_urls": asset_urls,
                "backup_urls": backup_urls,
                "error_message": error_detail if state == FilmProviderTaskState.FAILED else "",
                "raw": {**payload, "enhanced_prompt": enhanced_prompt},
            }
        )

    async def _submit_image(self, request: FilmGenerationRequest) -> FilmProviderTask:
        model_id = request.model_id or "image-01"
        body: dict[str, Any] = {
            "model": model_id,
            "prompt": request.prompt,
            "aspect_ratio": request.aspect_ratio,
            "response_format": str(request.metadata.get("response_format") or "url"),
            "n": min(request.image_count, 9),
        }
        subject_references = [
            {"type": "character", "image_file": item.url}
            for item in request.references
            if item.kind in {"subject", "reference_image"}
        ]
        if subject_references:
            body["subject_reference"] = subject_references
        if request.seed is not None:
            body["seed"] = request.seed
        payload = await self._request_json("POST", "/v1/image_generation", json=body)
        raw_data = payload.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        urls: list[str] = []
        for key in ("image_urls", "images", "image_base64"):
            raw = data.get(key)
            values = raw if isinstance(raw, list) else [raw] if raw else []
            for value in values:
                text = str(value or "")
                if not text:
                    continue
                if key == "image_base64" and not text.startswith("data:"):
                    text = f"data:image/jpeg;base64,{text}"
                urls.append(text)
        if not urls:
            raise FilmProviderError("MiniMax image generation returned no image", retryable=True)
        return FilmProviderTask(
            provider_id=self.provider_id,
            model_id=model_id,
            mode=request.mode,
            state=FilmProviderTaskState.SUCCEEDED,
            asset_urls=urls,
            trace_id=str(payload.get("trace_id") or ""),
            raw=payload,
        )

    async def _submit_h3_v2(self, request: FilmGenerationRequest) -> FilmProviderTask:
        """Submit an H3 task through the Open Platform v2 content[] contract.

        Mirrors the ComfyUI Hailuo03 nodes: one ``content`` array carrying
        text / image_url / video_url / audio_url entries with roles, plus
        resolution (768P/2K), duration (4–15), ratio and watermark.
        Reference images cap at 9, videos and audios at 3 each.
        """

        model_id = request.model_id or self._default_h3_model(request.mode)
        self._validate_h3_request(request)
        content = self._h3_context_content(request)
        body: dict[str, Any] = {
            "model": model_id,
            "content": content,
            "resolution": str(request.resolution).upper(),
            "duration": request.duration_s,
            "ratio": self._h3_ratio_for_mode(request),
        }
        if request.watermark:
            body["aigc_watermark"] = True
        if request.metadata.get("callback_url"):
            body["callback_url"] = str(request.metadata["callback_url"])
        payload = await self._request_json("POST", "/v2/video_generation", json=body)
        task_id = str(payload.get("task_id") or "")
        if not task_id:
            raise FilmProviderError("MiniMax H3 v2 returned no task_id", retryable=True)
        return FilmProviderTask(
            provider_id=self.provider_id,
            model_id=model_id,
            mode=request.mode,
            state=FilmProviderTaskState.PENDING,
            task_id=task_id,
            api_version="v2",
            trace_id=str(payload.get("trace_id") or ""),
            raw=payload,
        )

    @staticmethod
    def _validate_h3_request(
        request: FilmGenerationRequest,
        *,
        require_resolution: bool = True,
    ) -> None:
        references = request.references
        resolution = str(request.resolution).upper()
        if not (_H3_DURATION_RANGE[0] <= request.duration_s <= _H3_DURATION_RANGE[1]):
            raise FilmProviderError("H3 视频时长必须是 4–15 秒整数")
        if require_resolution and resolution not in _H3_RESOLUTIONS:
            raise FilmProviderError("H3 分辨率必须是 768P 或 2K")
        supported_kinds = {
            "subject",
            "reference_image",
            "first_frame",
            "last_frame",
            "reference_video",
            "reference_audio",
        }
        unknown_kinds = sorted({item.kind for item in references} - supported_kinds)
        if unknown_kinds:
            raise FilmProviderError(f"H3 不支持参考素材角色：{', '.join(unknown_kinds)}")
        image_refs = [
            item
            for item in references
            if item.kind in {"subject", "reference_image", "first_frame", "last_frame"}
        ]
        video_refs = [item for item in references if item.kind == "reference_video"]
        audio_refs = [item for item in references if item.kind == "reference_audio"]
        if request.mode == FilmGenerationMode.TEXT_TO_VIDEO:
            if references:
                raise FilmProviderError("H3 文生视频不能同时携带参考媒体")
            if _normalize_h3_ratio(request.aspect_ratio) in {"", "adaptive"}:
                raise FilmProviderError("H3 文生视频必须指定受支持的画面比例")
        elif request.mode == FilmGenerationMode.IMAGE_TO_VIDEO:
            if (
                len(image_refs) != 1
                or image_refs[0].kind not in {"reference_image", "first_frame"}
                or video_refs
                or audio_refs
            ):
                raise FilmProviderError("H3 图生视频需要且只能使用一张首帧图")
        elif request.mode == FilmGenerationMode.FIRST_LAST_FRAME:
            kinds = {item.kind for item in references}
            if kinds != {"first_frame", "last_frame"} or len(references) != 2:
                raise FilmProviderError("H3 首尾帧模式只能使用一张首帧和一张尾帧")
        else:
            if any(item.kind in {"first_frame", "last_frame"} for item in references):
                raise FilmProviderError("H3 多模态参考模式不能混用首尾帧角色")
            if len(image_refs) > 9 or len(video_refs) > 3 or len(audio_refs) > 3:
                raise FilmProviderError("H3 参考素材超过图片 9 / 视频 3 / 音频 3 的上限")
            if len(references) > 12:
                raise FilmProviderError("H3 参考素材总数不能超过 12")
            if not references:
                raise FilmProviderError("H3 多模态参考模式至少需要一项参考素材")
            if audio_refs and not (image_refs or video_refs):
                raise FilmProviderError("H3 音频参考必须与图片或视频参考共同使用")
        for reference in references:
            MiniMaxFilmProvider._validate_reference_format(reference.kind, reference.url)

    @staticmethod
    def _h3_ratio_for_mode(request: FilmGenerationRequest) -> str:
        if request.mode in {
            FilmGenerationMode.IMAGE_TO_VIDEO,
            FilmGenerationMode.FIRST_LAST_FRAME,
        }:
            return "adaptive"
        return _normalize_h3_ratio(request.aspect_ratio) or "adaptive"

    @staticmethod
    def _validate_reference_format(kind: str, url: str) -> None:
        lower = url.lower().split("?", 1)[0]
        if lower.startswith("data:"):
            accepted = {
                "reference_audio": ("data:audio/wav", "data:audio/mp3", "data:audio/mpeg"),
                "reference_video": ("data:video/mp4",),
            }.get(kind, ("data:image/jpeg", "data:image/png", "data:image/webp"))
            if not lower.startswith(accepted):
                raise FilmProviderError(f"H3 参考素材格式不受支持：{kind}")
            return
        suffixes = {
            "reference_audio": (".wav", ".mp3"),
            "reference_video": (".mp4", ".mov"),
        }.get(kind, (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"))
        path = urlsplit(lower).path
        if "." in path.rsplit("/", 1)[-1] and not path.endswith(suffixes):
            raise FilmProviderError(f"H3 参考素材格式不受支持：{kind}")

    async def submit_context_ir(self, request: FilmGenerationRequest) -> FilmProviderTask:
        """Submit the H3 prompt-enhancement task without creating a video."""

        self._validate_h3_request(request, require_resolution=False)
        content = self._h3_context_content(request)
        body: dict[str, Any] = {
            "model": request.model_id or "MiniMax-H3",
            "content": content,
            "duration": request.duration_s,
            "ratio": self._h3_ratio_for_mode(request),
        }
        if request.metadata.get("callback_url"):
            body["callback_url"] = str(request.metadata["callback_url"])
        payload = await self._request_json(
            "POST",
            "/v2/h3_context_ir",
            json=body,
        )
        task_id = str(payload.get("task_id") or "")
        if not task_id:
            raise FilmProviderError("MiniMax H3 Context IR returned no task_id", retryable=True)
        return FilmProviderTask(
            provider_id=self.provider_id,
            model_id=request.model_id or "MiniMax-H3",
            mode=request.mode,
            state=FilmProviderTaskState.PENDING,
            task_id=task_id,
            api_version="v2",
            raw={**payload, "task_type": "h3_context_ir"},
        )

    @staticmethod
    def _h3_context_content(request: FilmGenerationRequest) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
        type_by_kind = {
            "subject": ("image_url", "reference_image"),
            "reference_image": ("image_url", "reference_image"),
            "first_frame": ("image_url", "first_frame"),
            "last_frame": ("image_url", "last_frame"),
            "reference_video": ("video_url", "reference_video"),
            "reference_audio": ("audio_url", "reference_audio"),
        }
        for reference in request.references:
            kind = type_by_kind.get(reference.kind)
            if kind is None:
                continue
            content_type, role = kind
            content.append(
                {
                    "type": content_type,
                    content_type: {"url": reference.url},
                    "role": role,
                }
            )
        return content

    async def regenerate_2k(
        self,
        *,
        source_task_id: str = "",
        base_video_url: str = "",
        source_request: FilmGenerationRequest | None = None,
        callback_url: str = "",
        watermark: bool = False,
    ) -> FilmProviderTask:
        if bool(source_task_id) == bool(base_video_url):
            raise FilmProviderError("H3 2K 重生成必须且只能指定 source_task_id 或 base_video_url")
        body: dict[str, Any] = {"model": "MiniMax-H3", "resolution": "2K"}
        if source_task_id:
            body["source_task_id"] = source_task_id
        else:
            if source_request is None:
                raise FilmProviderError("按源视频重生成必须提供生成 768P 时的完整原始请求")
            self._validate_h3_request(source_request)
            if str(source_request.resolution).upper() != "768P":
                raise FilmProviderError("H3 2K 重生成的源请求必须是 768P")
            content = self._h3_context_content(source_request)
            content.append(
                {
                    "type": "video_url",
                    "video_url": {"url": base_video_url},
                    "role": "base_video",
                }
            )
            body["content"] = content
        if callback_url:
            body["callback_url"] = callback_url
        if watermark:
            body["aigc_watermark"] = True
        payload = await self._request_json("POST", "/v2/video_regeneration", json=body)
        task_id = str(payload.get("task_id") or "")
        if not task_id:
            raise FilmProviderError("MiniMax H3 regeneration returned no task_id", retryable=True)
        return FilmProviderTask(
            provider_id=self.provider_id,
            model_id="MiniMax-H3",
            mode=FilmGenerationMode.REFERENCE_TO_VIDEO,
            state=FilmProviderTaskState.PENDING,
            task_id=task_id,
            api_version="v2",
            raw={**payload, "task_type": "regeneration"},
        )

    async def cancel(self, task: FilmProviderTask) -> FilmProviderTask:
        if task.provider_id != self.provider_id or not task.task_id:
            raise FilmProviderError("MiniMax task_id is required for cancellation")
        if task.state != FilmProviderTaskState.PENDING:
            raise FilmProviderError("MiniMax 仅允许取消 queued 状态的视频任务")
        payload = await self._request_json("DELETE", f"/v2/video_generation/{task.task_id}")
        return task.model_copy(update={"state": FilmProviderTaskState.CANCELLED, "raw": payload})

    async def _submit_video(self, request: FilmGenerationRequest) -> FilmProviderTask:
        """Legacy Hailuo / v2-create submission path (non-H3 models).

        Kept for Hailuo-02 / S2V-01 compatibility; MiniMax-H3* models are
        routed to :meth:`_submit_h3_v2` by :meth:`submit`.
        """

        defaults = {
            FilmGenerationMode.TEXT_TO_VIDEO: "MiniMax-H3",
            FilmGenerationMode.IMAGE_TO_VIDEO: "MiniMax-H3",
            FilmGenerationMode.FIRST_LAST_FRAME: "MiniMax-H3",
            FilmGenerationMode.SUBJECT_REFERENCE_VIDEO: "MiniMax-H3",
            FilmGenerationMode.REFERENCE_TO_VIDEO: "MiniMax-H3",
        }
        model_id = request.model_id or defaults[request.mode]
        h3 = _is_h3_model(model_id)
        duration = request.duration_s
        if h3:
            low, high = _H3_DURATION_RANGE
            duration = max(low, min(high, duration))
        resolution = request.resolution
        if h3 and resolution not in _H3_RESOLUTIONS:
            # Legacy compatibility only. H3 v2 supports direct 2K generation.
            resolution = "768P"
        body: dict[str, Any] = {
            "model": model_id,
            "prompt": request.prompt,
            "duration": duration,
            "resolution": resolution,
            "prompt_optimizer": request.prompt_optimizer,
        }
        if h3:
            # H3 (Open Platform v2) generation knobs: seed reproducibility,
            # explicit aspect ratio and the AIGC watermark flag.  All three
            # were declared in the request contract but previously dropped
            # before hitting the wire; catalog aspect_ratios / seed are now
            # honoured end to end (catalog → request → body).
            if request.seed is not None:
                body["seed"] = request.seed
            ratio = _normalize_h3_ratio(request.aspect_ratio)
            if ratio:
                body["ratio"] = ratio
            if request.watermark:
                body["aigc_watermark"] = True
        reference_by_kind = {item.kind: item.url for item in request.references}
        if request.mode == FilmGenerationMode.IMAGE_TO_VIDEO:
            first_frame = reference_by_kind.get("first_frame") or next(
                (item.url for item in request.references if item.kind == "reference_image"),
                "",
            )
            body["first_frame_image"] = first_frame
        elif request.mode == FilmGenerationMode.FIRST_LAST_FRAME:
            body["first_frame_image"] = reference_by_kind["first_frame"]
            body["last_frame_image"] = reference_by_kind["last_frame"]
        elif request.mode in {
            FilmGenerationMode.SUBJECT_REFERENCE_VIDEO,
            FilmGenerationMode.REFERENCE_TO_VIDEO,
        }:
            images = [
                item.url
                for item in request.references
                if item.kind in {"subject", "reference_image"}
            ][:_MAX_SUBJECT_REFERENCES]
            body["subject_reference"] = [{"type": "character", "image": images}]
        if request.metadata.get("callback_url"):
            body["callback_url"] = str(request.metadata["callback_url"])
        endpoint = "/v2/video_generation" if h3 else "/v1/video_generation"
        payload = await self._request_json("POST", endpoint, json=body)
        task_id = str(payload.get("task_id") or "")
        if not task_id:
            raise FilmProviderError("MiniMax video generation returned no task_id", retryable=True)
        return FilmProviderTask(
            provider_id=self.provider_id,
            model_id=model_id,
            mode=request.mode,
            state=FilmProviderTaskState.PENDING,
            task_id=task_id,
            trace_id=str(payload.get("trace_id") or ""),
            raw=payload,
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
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
                    params=params,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:800]
            raise FilmProviderError(
                f"MiniMax HTTP {exc.response.status_code}: {detail}",
                retryable=exc.response.status_code >= 500 or exc.response.status_code == 429,
            ) from exc
        except httpx.RequestError as exc:
            raise FilmProviderError(f"MiniMax request failed: {exc}", retryable=True) from exc
        if not isinstance(payload, dict):
            raise FilmProviderError("MiniMax returned a non-object response")
        raw_base_resp = payload.get("base_resp")
        base_resp: dict[str, Any] = raw_base_resp if isinstance(raw_base_resp, dict) else {}
        status_code = int(base_resp.get("status_code") or 0)
        if status_code != 0:
            raise FilmProviderError(
                f"MiniMax API error {status_code}: {base_resp.get('status_msg')}",
                retryable=status_code in {1004, 1008, 1013},
            )
        return payload
