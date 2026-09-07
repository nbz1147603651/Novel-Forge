from __future__ import annotations

import httpx
import pytest

from novel_forge.film.providers.bailian import BailianFilmProvider
from novel_forge.film.providers.base import (
    FilmGenerationMode,
    FilmGenerationRequest,
    FilmProviderTask,
    FilmProviderTaskState,
    FilmReferenceMedia,
)
from novel_forge.film.providers.minimax import MiniMaxFilmProvider
from novel_forge.film.providers.volcengine_ark import VolcengineArkFilmProvider


async def test_bailian_image_set_uses_wan_sequential_protocol() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={
                "output": {"choices": [{"message": {"content": [{"image": "https://x/1.png"}]}}]},
                "request_id": "req-1",
            },
        )

    provider = BailianFilmProvider(
        api_key="test",
        base_url="https://bailian.test",
        transport=httpx.MockTransport(handler),
    )
    result = await provider.submit(
        FilmGenerationRequest(
            mode=FilmGenerationMode.IMAGE_SET,
            prompt="same character across four seasons",
            image_count=4,
        )
    )

    assert result.state == FilmProviderTaskState.SUCCEEDED
    assert result.asset_urls == ["https://x/1.png"]
    assert seen["path"] == "/api/v1/services/aigc/multimodal-generation/generation"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["parameters"]["enable_sequential"] is True


async def test_bailian_r2v_preserves_ordered_storyboard_and_audio_media() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"output": {"task_id": "wan-task"}})
        return httpx.Response(
            200,
            json={"output": {"task_status": "SUCCEEDED", "video_url": "https://x/shot.mp4"}},
        )

    provider = BailianFilmProvider(
        api_key="test",
        base_url="https://bailian.test",
        transport=httpx.MockTransport(handler),
    )
    submitted = await provider.submit(
        FilmGenerationRequest(
            mode=FilmGenerationMode.REFERENCE_TO_VIDEO,
            prompt="Generate a multi-shot video from Image 1",
            references=[
                FilmReferenceMedia(kind="reference_image", url="https://x/board.png"),
                FilmReferenceMedia(kind="driving_audio", url="https://x/dialogue.mp3"),
            ],
        )
    )
    completed = await provider.query(submitted)

    assert submitted.task_id == "wan-task"
    assert completed.state == FilmProviderTaskState.SUCCEEDED
    assert completed.asset_urls == ["https://x/shot.mp4"]
    body = __import__("json").loads(requests[0].content)
    assert body["model"] == "wan2.7-r2v"
    assert body["input"]["media"] == [
        {"type": "reference_image", "url": "https://x/board.png"},
        {"type": "driving_audio", "url": "https://x/dialogue.mp3"},
    ]
    assert requests[0].headers["X-DashScope-Async"] == "enable"


async def test_minimax_subject_reference_video_resolves_file_url() -> None:
    seen_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/query/video_generation"):
            return httpx.Response(
                200,
                json={"status": "Success", "file_id": 42, "base_resp": {"status_code": 0}},
            )
        if request.url.path.endswith("/files/retrieve"):
            return httpx.Response(
                200,
                json={
                    "file": {"download_url": "https://x/subject.mp4"},
                    "base_resp": {"status_code": 0},
                },
            )
        if request.url.path.endswith("/video_generation"):
            seen_bodies.append(__import__("json").loads(request.content))
            return httpx.Response(200, json={"task_id": "mm-task", "base_resp": {"status_code": 0}})
        return httpx.Response(
            200,
            json={
                "file": {"download_url": "https://x/subject.mp4"},
                "base_resp": {"status_code": 0},
            },
        )

    provider = MiniMaxFilmProvider(
        api_key="test",
        base_url="https://minimax.test/v1",
        transport=httpx.MockTransport(handler),
    )
    submitted = await provider.submit(
        FilmGenerationRequest(
            mode=FilmGenerationMode.SUBJECT_REFERENCE_VIDEO,
            model_id="S2V-01",
            prompt="the heroine walks through rain",
            references=[FilmReferenceMedia(kind="subject", url="https://x/face.png")],
        )
    )
    completed = await provider.query(submitted)

    assert seen_bodies[0]["model"] == "S2V-01"
    assert seen_bodies[0]["subject_reference"] == [
        {"type": "character", "image": ["https://x/face.png"]}
    ]
    assert completed.file_id == "42"
    assert completed.asset_urls == ["https://x/subject.mp4"]


async def test_minimax_h3_uses_v2_endpoint_with_h3_output_spec() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"task_id": "h3-task", "base_resp": {"status_code": 0}})

    provider = MiniMaxFilmProvider(
        api_key="test",
        base_url="https://minimax.test/v1",
        transport=httpx.MockTransport(handler),
    )
    task = await provider.submit(
        FilmGenerationRequest(
            mode=FilmGenerationMode.TEXT_TO_VIDEO,
            prompt="integrated_multimodal_description: [Shot 1] ...",
            duration_s=4,
            resolution="768P",
            prompt_optimizer=False,
        )
    )

    # H3 统一走 Open Platform v2 content[] 端点（ComfyUI Hailuo03 同款）。
    assert str(seen["path"]).endswith("/v2/video_generation")
    body = seen["body"]
    assert body["model"] == "MiniMax-H3"
    assert body["content"] == [
        {"type": "text", "text": "integrated_multimodal_description: [Shot 1] ..."}
    ]
    # H3 输出规格：4–15s 整数，直接支持 768P/2K。
    assert body["duration"] == 4
    assert body["resolution"] == "768P"
    assert "prompt_optimizer" not in body  # v2 content 契约无该参数
    assert task.task_id == "h3-task"
    assert task.api_version == "v2"


@pytest.mark.parametrize("duration", range(4, 16))
async def test_minimax_h3_accepts_every_official_duration(duration: int) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"task_id": f"h3-{duration}"})

    provider = MiniMaxFilmProvider(
        api_key="test",
        transport=httpx.MockTransport(handler),
    )
    await provider.submit(
        FilmGenerationRequest(
            mode=FilmGenerationMode.TEXT_TO_VIDEO,
            prompt="duration contract",
            resolution="768P",
            duration_s=duration,
        )
    )

    assert seen["body"]["duration"] == duration


async def test_minimax_h3_passes_ratio_and_watermark_but_not_seed() -> None:
    """The public H3 v2 contract has no seed field."""

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"task_id": "h3-knobs", "base_resp": {"status_code": 0}})

    provider = MiniMaxFilmProvider(
        api_key="test",
        base_url="https://minimax.test/v1",
        transport=httpx.MockTransport(handler),
    )
    await provider.submit(
        FilmGenerationRequest(
            mode=FilmGenerationMode.TEXT_TO_VIDEO,
            prompt="a slow push across the rain-soaked street",
            aspect_ratio="9:16",
            seed=123456789,
            watermark=True,
            resolution="2K",
            duration_s=8,
        )
    )
    body = seen["body"]
    assert body["ratio"] == "9:16"
    assert "seed" not in body
    assert body["aigc_watermark"] is True
    assert body["resolution"] == "2K"
    assert body["duration"] == 8


async def test_minimax_h3_rejects_unsupported_t2v_ratio() -> None:

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"task_id": "h3-ratio", "base_resp": {"status_code": 0}})

    provider = MiniMaxFilmProvider(
        api_key="test",
        base_url="https://minimax.test/v1",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RuntimeError, match="必须指定受支持"):
        await provider.submit(
            FilmGenerationRequest(
                mode=FilmGenerationMode.TEXT_TO_VIDEO,
                prompt="ambient street shot",
                aspect_ratio="2.39:1",
                resolution="768P",
            )
        )
    assert "body" not in seen


async def test_minimax_h3_rejects_more_than_nine_images() -> None:
    seen_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_bodies.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={"task_id": "h3-ref", "base_resp": {"status_code": 0}})

    provider = MiniMaxFilmProvider(
        api_key="test",
        base_url="https://minimax.test/v1",
        transport=httpx.MockTransport(handler),
    )
    references = [
        FilmReferenceMedia(kind="subject", url=f"https://x/ref-{i}.png") for i in range(12)
    ]
    with pytest.raises(RuntimeError, match="图片 9"):
        await provider.submit(
            FilmGenerationRequest(
                mode=FilmGenerationMode.REFERENCE_TO_VIDEO,
                prompt="reference-driven shot",
                resolution="768P",
                references=references,
            )
        )
    assert seen_bodies == []


async def test_minimax_h3_v2_multimodal_references() -> None:
    """H3 Ref2VA carries images (≤9) plus videos (≤3) and audios (≤3) in one
    content[] array, mirroring the ComfyUI Hailuo03 Reference node."""

    seen_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_bodies.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={"task_id": "h3-mm", "base_resp": {"status_code": 0}})

    provider = MiniMaxFilmProvider(
        api_key="test",
        base_url="https://minimax.test/v1",
        transport=httpx.MockTransport(handler),
    )
    await provider.submit(
        FilmGenerationRequest(
            mode=FilmGenerationMode.REFERENCE_TO_VIDEO,
            prompt="match the motion of Video 1 with the voice of Audio 1",
            resolution="768P",
            references=[
                FilmReferenceMedia(kind="subject", url="https://x/hero.png"),
                FilmReferenceMedia(kind="reference_video", url="https://x/motion.mp4"),
                FilmReferenceMedia(kind="reference_video", url="https://x/more.mp4"),
                FilmReferenceMedia(kind="reference_video", url="https://x/x.mp4"),
                FilmReferenceMedia(kind="reference_audio", url="https://x/voice.mp3"),
            ],
        )
    )
    content = seen_bodies[0]["content"]
    assert content[0] == {
        "type": "text",
        "text": "match the motion of Video 1 with the voice of Audio 1",
    }
    images = [item for item in content if item["type"] == "image_url"]
    videos = [item for item in content if item["type"] == "video_url"]
    audios = [item for item in content if item["type"] == "audio_url"]
    assert images == [
        {"type": "image_url", "image_url": {"url": "https://x/hero.png"}, "role": "reference_image"}
    ]
    assert len(videos) == 3
    assert videos[0]["video_url"]["url"] == "https://x/motion.mp4"
    assert all(item["role"] == "reference_video" for item in videos)
    assert audios == [
        {
            "type": "audio_url",
            "audio_url": {"url": "https://x/voice.mp3"},
            "role": "reference_audio",
        }
    ]


async def test_minimax_h3_i2v_forces_adaptive_ratio_and_rejects_audio_only() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"task_id": "h3-i2v"})

    provider = MiniMaxFilmProvider(
        api_key="test",
        transport=httpx.MockTransport(handler),
    )
    await provider.submit(
        FilmGenerationRequest(
            mode=FilmGenerationMode.IMAGE_TO_VIDEO,
            prompt="首帧自然运动",
            aspect_ratio="21:9",
            resolution="768P",
            references=[FilmReferenceMedia(kind="first_frame", url="https://x/first.png")],
        )
    )
    assert seen["body"]["ratio"] == "adaptive"

    with pytest.raises(RuntimeError, match="音频参考必须"):
        await provider.submit(
            FilmGenerationRequest(
                mode=FilmGenerationMode.REFERENCE_TO_VIDEO,
                prompt="voice only",
                resolution="768P",
                references=[
                    FilmReferenceMedia(
                        kind="reference_audio",
                        url="https://x/voice.mp3",
                    )
                ],
            )
        )


async def test_minimax_h3_v2_query_uses_task_path_and_content_url() -> None:
    """v2 polling hits the path-scoped task endpoint and reads the result
    from ``task.content.url`` (backup URLs are surfaced for materialize)."""

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"task_id": "h3-q", "base_resp": {"status_code": 0}})
        return httpx.Response(
            200,
            json={
                "task": {
                    "id": "h3-q",
                    "status": "succeeded",
                    "content": {"url": "https://x/h3-final.mp4"},
                    "usage": {"total_seconds": 6.0},
                }
            },
        )

    provider = MiniMaxFilmProvider(
        api_key="test",
        base_url="https://minimax.test/v1",
        transport=httpx.MockTransport(handler),
    )
    submitted = await provider.submit(
        FilmGenerationRequest(
            mode=FilmGenerationMode.TEXT_TO_VIDEO,
            prompt="shot",
            resolution="768P",
        )
    )
    completed = await provider.query(submitted)

    assert submitted.api_version == "v2"
    assert requests[1].url.path.endswith("/v2/query/video_generation/h3-q")
    assert completed.state == FilmProviderTaskState.SUCCEEDED
    assert completed.asset_urls == ["https://x/h3-final.mp4"]


async def test_minimax_h3_context_ir_includes_duration_ratio_and_returns_prompt() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"task_id": "ir-1"})
        return httpx.Response(
            200,
            json={
                "task": {
                    "id": "ir-1",
                    "status": "succeeded",
                    "task_type": "h3_context_ir",
                    "content": {"prompt": "integrated_multimodal_description: [Shot 1] ..."},
                }
            },
        )

    provider = MiniMaxFilmProvider(
        api_key="test",
        base_url="https://minimax.test/v1",
        transport=httpx.MockTransport(handler),
    )
    submitted = await provider.submit_context_ir(
        FilmGenerationRequest(
            mode=FilmGenerationMode.TEXT_TO_VIDEO,
            prompt="雨夜档案库",
            resolution="1080P",  # Context IR does not accept a resolution field.
            duration_s=9,
            aspect_ratio="16:9",
        )
    )
    completed = await provider.query(submitted)

    body = __import__("json").loads(requests[0].content)
    assert requests[0].url.path == "/v2/h3_context_ir"
    assert body["duration"] == 9
    assert body["ratio"] == "16:9"
    assert "resolution" not in body
    assert completed.raw["enhanced_prompt"].startswith("integrated_multimodal_description")


async def test_minimax_h3_regeneration_uses_official_two_input_contracts() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"task_id": f"regen-{len(requests)}"})

    provider = MiniMaxFilmProvider(
        api_key="test",
        base_url="https://minimax.test/v2",
        transport=httpx.MockTransport(handler),
    )
    by_task = await provider.regenerate_2k(source_task_id="generation-768")
    source_request = FilmGenerationRequest(
        mode=FilmGenerationMode.TEXT_TO_VIDEO,
        prompt="最终增强提示词",
        resolution="768P",
        duration_s=5,
        aspect_ratio="16:9",
    )
    by_video = await provider.regenerate_2k(
        base_video_url="https://x/source-768.mp4",
        source_request=source_request,
    )

    task_body = __import__("json").loads(requests[0].content)
    video_body = __import__("json").loads(requests[1].content)
    assert requests[0].url.path == "/v2/video_regeneration"
    assert task_body == {
        "model": "MiniMax-H3",
        "resolution": "2K",
        "source_task_id": "generation-768",
    }
    assert video_body["content"][-1] == {
        "type": "video_url",
        "video_url": {"url": "https://x/source-768.mp4"},
        "role": "base_video",
    }
    assert by_task.raw["task_type"] == "regeneration"
    assert by_video.task_id == "regen-2"


async def test_minimax_h3_cancel_only_accepts_queued_task() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"task_id": "queued-1", "action": "cancelled", "status": "cancelled"},
        )

    provider = MiniMaxFilmProvider(
        api_key="test",
        transport=httpx.MockTransport(handler),
    )
    queued = FilmProviderTask(
        provider_id="minimax",
        model_id="MiniMax-H3",
        mode=FilmGenerationMode.TEXT_TO_VIDEO,
        state=FilmProviderTaskState.PENDING,
        task_id="queued-1",
        api_version="v2",
    )
    cancelled = await provider.cancel(queued)

    assert requests[0].method == "DELETE"
    assert requests[0].url.path == "/v2/video_generation/queued-1"
    assert cancelled.state == FilmProviderTaskState.CANCELLED
    with pytest.raises(RuntimeError, match="仅允许取消 queued"):
        await provider.cancel(queued.model_copy(update={"state": FilmProviderTaskState.RUNNING}))


async def test_minimax_v1_query_surfaces_backup_download_url() -> None:
    """Legacy file retrieval keeps the backup download URL so materialize can
    retry when the primary URL is stale."""

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/query/video_generation"):
            return httpx.Response(
                200,
                json={
                    "status": "Success",
                    "file_id": 7,
                    "base_resp": {"status_code": 0},
                },
            )
        if request.url.path.endswith("/files/retrieve"):
            return httpx.Response(
                200,
                json={
                    "file": {
                        "download_url": "https://x/v1.mp4",
                        "backup_download_url": "https://x/v1-backup.mp4",
                    },
                    "base_resp": {"status_code": 0},
                },
            )
        return httpx.Response(200, json={"task_id": "v1-task", "base_resp": {"status_code": 0}})

    provider = MiniMaxFilmProvider(
        api_key="test",
        base_url="https://minimax.test/v1",
        transport=httpx.MockTransport(handler),
    )
    submitted = await provider.submit(
        FilmGenerationRequest(
            mode=FilmGenerationMode.SUBJECT_REFERENCE_VIDEO,
            model_id="S2V-01",
            prompt="subject clip",
            references=[FilmReferenceMedia(kind="subject", url="https://x/face.png")],
        )
    )
    completed = await provider.query(submitted)

    assert completed.asset_urls == ["https://x/v1.mp4"]
    assert completed.backup_urls == ["https://x/v1-backup.mp4"]


def test_first_last_frame_request_requires_both_frames() -> None:
    with pytest.raises(ValueError, match="first_frame and last_frame"):
        FilmGenerationRequest(
            mode=FilmGenerationMode.FIRST_LAST_FRAME,
            prompt="transition",
            references=[FilmReferenceMedia(kind="first_frame", url="https://x/first.png")],
        )


async def test_volcengine_seedream_preserves_reference_assets_and_sequence_options() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={"data": [{"url": "https://x/identity-sheet.png"}], "request_id": "ark-1"},
        )

    provider = VolcengineArkFilmProvider(
        api_key="test",
        base_url="https://ark.test/api/plan/v3",
        transport=httpx.MockTransport(handler),
    )
    result = await provider.submit(
        FilmGenerationRequest(
            mode=FilmGenerationMode.IMAGE_SET,
            prompt="same actor turnaround sheet",
            image_count=8,
            references=[FilmReferenceMedia(kind="subject", url="asset://actor-approved")],
        )
    )

    assert result.asset_urls == ["https://x/identity-sheet.png"]
    assert seen["path"] == "/api/v3/images/generations"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["image"] == ["asset://actor-approved"]
    assert body["sequential_image_generation"] == "auto"
    assert body["sequential_image_generation_options"] == {"max_images": 8}


async def test_volcengine_seedance_uses_content_task_and_returns_video() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "seedance-task", "request_id": "ark-2"})
        return httpx.Response(
            200,
            json={
                "id": "seedance-task",
                "status": "succeeded",
                "content": {"video_url": "https://x/seedance.mp4"},
            },
        )

    provider = VolcengineArkFilmProvider(
        api_key="test",
        base_url="https://ark.test/api/v3",
        transport=httpx.MockTransport(handler),
    )
    submitted = await provider.submit(
        FilmGenerationRequest(
            mode=FilmGenerationMode.REFERENCE_TO_VIDEO,
            prompt="an approved actor walks through the fixed radio studio",
            references=[
                FilmReferenceMedia(kind="subject", url="asset://actor-approved"),
                FilmReferenceMedia(kind="reference_image", url="https://x/studio.png"),
            ],
        )
    )
    completed = await provider.query(submitted)

    assert submitted.task_id == "seedance-task"
    assert completed.state == FilmProviderTaskState.SUCCEEDED
    assert completed.asset_urls == ["https://x/seedance.mp4"]
    body = __import__("json").loads(requests[0].content)
    assert body["model"] == "doubao-seedance-2-0-260128"
    assert body["return_last_frame"] is True
    assert body["content"][1]["image_url"]["url"] == "asset://actor-approved"
