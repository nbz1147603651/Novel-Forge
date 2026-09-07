"""Current visual-model capability catalog for 映界.

The catalog is intentionally explicit and testable.  UI model selectors and
request validation consume the same source, preventing a model from being
offered for a mode its provider does not support.

Official references (checked 2026-08):
* https://help.aliyun.com/en/model-studio/wan-image-generation-and-editing-api-reference
* https://help.aliyun.com/en/model-studio/text-to-video-api-reference
* https://help.aliyun.com/en/model-studio/image-to-video-general-api-reference
* https://help.aliyun.com/en/model-studio/wan-video-to-video-api-reference
* https://platform.minimax.io/docs/guides/image-generation
* https://platform.minimax.io/docs/guides/video-generation
* https://huggingface.co/MiniMaxAI/MiniMax-H3 (H3 prompt writing guide +
  v2 endpoints: /video-generation-v2-create, /video-generation-v2-h3-context-ir,
  /video-generation-v2-regeneration; checked 2026-08)
* https://www.volcengine.com/docs/82379/1829186
* https://www.volcengine.com/docs/82379/1520757
* https://www.volcengine.com/docs/82379/2315856
"""

from __future__ import annotations

from typing import Any

from novel_forge.film.providers.base import FilmGenerationMode

FILM_PROVIDER_CATALOG: dict[str, dict[str, Any]] = {
    "bailian": {
        "recommended": False,
        "label": "阿里百炼",
        "short_label": "百炼",
        "strengths": ["长时多镜头", "多主体参考", "声画同步", "角色图集"],
        "best_for": "多镜头叙事、参考图驱动和同一角色成组资产",
        "docs_url": "https://help.aliyun.com/en/model-studio/video-generate-edit-model/",
        "image_models": [
            {
                "id": "wan2.7-image-pro",
                "label": "Wan 2.7 Image Pro",
                "recommended": True,
                "max_outputs": 12,
                "max_resolution": "4K",
                "modes": [
                    FilmGenerationMode.TEXT_TO_IMAGE.value,
                    FilmGenerationMode.IMAGE_EDIT.value,
                    FilmGenerationMode.IMAGE_SET.value,
                ],
                "features": [
                    "character_consistent_sequence",
                    "multi_image_reference",
                    "brand_color_control",
                    "text_rendering",
                    "bounding_box_edit",
                ],
            },
            {
                "id": "wan2.7-image",
                "label": "Wan 2.7 Image",
                "recommended": False,
                "max_outputs": 12,
                "max_resolution": "2K",
                "modes": [
                    FilmGenerationMode.TEXT_TO_IMAGE.value,
                    FilmGenerationMode.IMAGE_EDIT.value,
                    FilmGenerationMode.IMAGE_SET.value,
                ],
                "features": ["character_consistent_sequence", "multi_image_reference"],
            },
            {
                "id": "qwen-image-2.0-pro",
                "label": "Qwen Image 2.0 Pro",
                "recommended": False,
                "max_outputs": 6,
                "max_resolution": "2K",
                "modes": [
                    FilmGenerationMode.TEXT_TO_IMAGE.value,
                    FilmGenerationMode.IMAGE_EDIT.value,
                ],
                "features": ["negative_prompt", "complex_text_layout", "multilingual_text"],
            },
        ],
        "video_models": [
            {
                "id": "wan2.7-t2v",
                "label": "Wan 2.7 Text-to-Video",
                "recommended": True,
                "modes": [FilmGenerationMode.TEXT_TO_VIDEO.value],
                "durations": {"min": 2, "max": 15},
                "resolutions": ["720P", "1080P"],
                "default_duration": 5,
                "default_resolution": "1080P",
                "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
                "features": [
                    "multi_shot",
                    "driving_audio",
                    "auto_audio",
                    "negative_prompt",
                    "prompt_optimizer",
                    "watermark",
                    "seed",
                ],
            },
            {
                "id": "wan2.7-i2v",
                "label": "Wan 2.7 Image-to-Video",
                "recommended": True,
                "modes": [
                    FilmGenerationMode.IMAGE_TO_VIDEO.value,
                    FilmGenerationMode.FIRST_LAST_FRAME.value,
                    FilmGenerationMode.VIDEO_CONTINUATION.value,
                ],
                "durations": {"min": 2, "max": 15},
                "resolutions": ["720P", "1080P"],
                "default_duration": 5,
                "default_resolution": "1080P",
                "features": [
                    "multi_shot",
                    "driving_audio",
                    "auto_audio",
                    "continuation",
                    "negative_prompt",
                    "prompt_optimizer",
                    "watermark",
                    "seed",
                ],
            },
            {
                "id": "wan2.7-r2v",
                "label": "Wan 2.7 Reference-to-Video",
                "recommended": True,
                "modes": [FilmGenerationMode.REFERENCE_TO_VIDEO.value],
                "durations": {"min": 2, "max": 15},
                "resolutions": ["720P", "1080P"],
                "default_duration": 10,
                "default_resolution": "720P",
                "features": [
                    "storyboard_reference",
                    "multi_subject_reference",
                    "voice_reference",
                    "audio_visual_sync",
                    "multi_shot",
                    "negative_prompt",
                    "prompt_optimizer",
                    "watermark",
                    "seed",
                ],
            },
            {
                "id": "wan2.7-r2v-2026-06-12",
                "label": "Wan 2.7 Reference-to-Video (2026-06-12)",
                "recommended": False,
                "modes": [FilmGenerationMode.REFERENCE_TO_VIDEO.value],
                "durations": {"min": 2, "max": 15},
                "resolutions": ["720P", "1080P"],
                "default_duration": 10,
                "default_resolution": "720P",
                "features": [
                    "version_pinned",
                    "multi_subject_reference",
                    "voice_reference",
                    "audio_visual_sync",
                    "negative_prompt",
                    "prompt_optimizer",
                    "watermark",
                    "seed",
                ],
            },
        ],
    },
    "minimax": {
        "recommended": True,
        "label": "MiniMax",
        "short_label": "MiniMax",
        "strengths": ["H3 全模态有声", "原生立体声", "镜头运动", "多参考图", "主体身份"],
        "best_for": "带原生音频的有声镜头、H3 时间线结构提示词、镜头运动控制和角色脸部身份保持",
        "docs_url": "https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create",
        "image_models": [
            {
                "id": "image-01",
                "label": "MiniMax Image-01",
                "recommended": True,
                "max_outputs": 9,
                "max_resolution": "provider_native",
                "modes": [
                    FilmGenerationMode.TEXT_TO_IMAGE.value,
                    FilmGenerationMode.IMAGE_EDIT.value,
                ],
                "features": ["subject_reference", "character_consistency"],
            }
        ],
        "video_models": [
            {
                "id": "MiniMax-H3",
                "label": "MiniMax H3（全模态有声）",
                "recommended": True,
                "modes": [
                    FilmGenerationMode.TEXT_TO_VIDEO.value,
                    FilmGenerationMode.IMAGE_TO_VIDEO.value,
                    FilmGenerationMode.FIRST_LAST_FRAME.value,
                    FilmGenerationMode.REFERENCE_TO_VIDEO.value,
                    FilmGenerationMode.SUBJECT_REFERENCE_VIDEO.value,
                ],
                "durations": {"min": 4, "max": 15},
                "resolutions": ["768P", "2K"],
                "default_duration": 10,
                "default_resolution": "768P",
                "aspect_ratios": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
                "features": [
                    "native_audio",
                    "multi_modal_reference",
                    "multi_image_reference",
                    "multi_shot",
                    "camera_commands",
                    "first_last_frame",
                    "audio_reference",
                    "regenerate_2k",
                    "structured_timeline_prompt",
                    "context_ir",
                    "queued_cancel",
                    "watermark",
                ],
                # H3-Base-Ref2VA input limits (images / videos / audios / total).
                "reference_limits": {"images": 9, "videos": 3, "audios": 3, "total_files": 12},
            },
            {
                "id": "MiniMax-Hailuo-2.3",
                "label": "Hailuo 2.3",
                "recommended": False,
                "modes": [
                    FilmGenerationMode.TEXT_TO_VIDEO.value,
                    FilmGenerationMode.IMAGE_TO_VIDEO.value,
                ],
                "durations": [6, 10],
                "resolutions": ["768P", "1080P"],
                "features": ["camera_commands", "prompt_optimizer"],
            },
            {
                "id": "MiniMax-Hailuo-2.3-Fast",
                "label": "Hailuo 2.3 Fast",
                "recommended": False,
                "modes": [FilmGenerationMode.IMAGE_TO_VIDEO.value],
                "durations": [6, 10],
                "resolutions": ["768P", "1080P"],
                "features": ["fast_generation"],
            },
            {
                "id": "MiniMax-Hailuo-02",
                "label": "Hailuo 02 首尾帧",
                "recommended": False,
                "modes": [FilmGenerationMode.FIRST_LAST_FRAME.value],
                "durations": [6, 10],
                "resolutions": ["768P", "1080P"],
                "features": ["first_last_frame"],
            },
            {
                "id": "S2V-01",
                "label": "S2V-01 主体参考",
                "recommended": False,
                "modes": [FilmGenerationMode.SUBJECT_REFERENCE_VIDEO.value],
                "durations": [6],
                "resolutions": ["768P", "1080P"],
                "features": ["face_identity_lock"],
            },
        ],
        "audio_models": {
            "speech": ["speech-2.8-hd", "speech-2.8-turbo"],
            "music": ["music-3.0", "music-2.6", "music-cover"],
        },
    },
    "volcengine_ark": {
        "recommended": False,
        "label": "火山方舟",
        "short_label": "方舟",
        "strengths": ["多模态参考", "原生声画", "可信演员资产", "首尾帧"],
        "best_for": "多模态镜头、带声音成片和已授权真人资产复用",
        "docs_url": "https://www.volcengine.com/docs/82379/1795150",
        "image_models": [
            {
                "id": "doubao-seedream-5-0-lite-260128",
                "label": "Seedream 5.0 Lite",
                "recommended": True,
                "max_outputs": 15,
                "max_resolution": "4K",
                "modes": [
                    FilmGenerationMode.TEXT_TO_IMAGE.value,
                    FilmGenerationMode.IMAGE_EDIT.value,
                    FilmGenerationMode.IMAGE_SET.value,
                ],
                "features": [
                    "multi_image_reference",
                    "sequential_image_generation",
                    "character_consistency",
                    "image_editing",
                ],
            },
            {
                "id": "doubao-seedream-4-5-251128",
                "label": "Seedream 4.5",
                "recommended": False,
                "max_outputs": 15,
                "max_resolution": "4K",
                "modes": [
                    FilmGenerationMode.TEXT_TO_IMAGE.value,
                    FilmGenerationMode.IMAGE_EDIT.value,
                    FilmGenerationMode.IMAGE_SET.value,
                ],
                "features": ["multi_image_reference", "sequential_image_generation"],
            },
        ],
        "video_models": [
            {
                "id": "doubao-seedance-2-0-260128",
                "label": "Seedance 2.0",
                "recommended": True,
                "modes": [
                    FilmGenerationMode.TEXT_TO_VIDEO.value,
                    FilmGenerationMode.IMAGE_TO_VIDEO.value,
                    FilmGenerationMode.FIRST_LAST_FRAME.value,
                    FilmGenerationMode.VIDEO_CONTINUATION.value,
                    FilmGenerationMode.REFERENCE_TO_VIDEO.value,
                    FilmGenerationMode.SUBJECT_REFERENCE_VIDEO.value,
                ],
                "durations": {"min": 2, "max": 15},
                "resolutions": ["720P", "1080P"],
                "default_duration": 5,
                "default_resolution": "1080P",
                "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
                "features": [
                    "multi_modal_reference",
                    "native_audio",
                    "first_last_frame",
                    "trusted_actor_asset",
                    "return_last_frame",
                    "generate_audio",
                    "watermark",
                    "seed",
                ],
            },
            {
                "id": "doubao-seedance-2-0-fast-260128",
                "label": "Seedance 2.0 Fast",
                "recommended": False,
                "modes": [
                    FilmGenerationMode.TEXT_TO_VIDEO.value,
                    FilmGenerationMode.IMAGE_TO_VIDEO.value,
                ],
                "durations": {"min": 2, "max": 15},
                "resolutions": ["720P", "1080P"],
                "default_duration": 5,
                "default_resolution": "720P",
                "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
                "features": [
                    "fast_generation",
                    "native_audio",
                    "generate_audio",
                    "return_last_frame",
                    "watermark",
                    "seed",
                ],
            },
        ],
        "asset_schemes": ["https://", "asset://"],
    },
}


def film_provider_catalog() -> dict[str, dict[str, Any]]:
    """Return a defensive copy safe for API and UI callers."""

    import copy

    return copy.deepcopy(FILM_PROVIDER_CATALOG)
