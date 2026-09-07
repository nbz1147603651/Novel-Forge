"""Alibaba Cloud Model Studio (百炼) TTS adapter.

The adapter uses the current Qwen-Audio-TTS/CosyVoice HTTP contracts instead
of mutating the process-wide ``dashscope`` SDK globals.  A provider voice is
always treated as a ``(model_id, voice_id)`` identity: Alibaba custom voices
cannot be moved between models.
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from novel_forge.core.exceptions import AuthenticationError, ModelGatewayError, RateLimitError
from novel_forge.obs.logger import get_logger
from novel_forge.tts.gateway.base import TTSProviderAdapter
from novel_forge.tts.schemas import (
    ProviderTakeEvidence,
    TTSFeature,
    TTSProvider,
    TTSProviderCapabilities,
    TTSRequest,
    TTSResponse,
    VoiceCloneRequest,
    VoiceCloneResponse,
    VoiceCloneStatus,
    VoiceDesignRequest,
    VoiceDesignResponse,
)

_log = get_logger("tts.gateway.adapters.dashscope")

_DASHSCOPE_DEFAULT_MODEL = "qwen-audio-3.0-tts-plus"
_DASHSCOPE_DEFAULT_DESIGN_MODEL = "cosyvoice-v3.5-plus"
_DASHSCOPE_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
_SYNTHESIS_PATH = "services/audio/tts/SpeechSynthesizer"
_QWEN3_SYNTHESIS_PATH = "services/aigc/multimodal-generation/generation"
_CUSTOMIZATION_PATH = "services/audio/tts/customization"
_SUPPORTED_SAMPLE_RATES = (8000, 16000, 22050, 24000, 44100, 48000)
_SUPPORTED_FORMATS = {"mp3", "pcm", "wav", "opus"}
_AIGC_TAG_MODELS = {
    "qwen-audio-3.0-tts-plus",
    "qwen-audio-3.0-tts-flash",
    "cosyvoice-v3-flash",
    "cosyvoice-v3-plus",
    "cosyvoice-v2",
}
_HOT_FIX_UNSUPPORTED_MODELS = {
    "qwen-audio-3.0-tts-plus",
    "qwen-audio-3.0-tts-flash",
    "cosyvoice-v2",
}
_FREEFORM_INSTRUCTION_MODELS = {
    "qwen-audio-3.0-tts-plus",
    "qwen-audio-3.0-tts-flash",
    "cosyvoice-v3.5-plus",
    "cosyvoice-v3.5-flash",
}
_QWEN3_FLASH_MODELS = {
    "qwen3-tts-flash",
    "qwen3-tts-flash-2025-11-27",
    "qwen3-tts-flash-2025-09-18",
}
_QWEN3_INSTRUCT_MODELS = {
    "qwen3-tts-instruct-flash",
    "qwen3-tts-instruct-flash-2026-01-26",
}
_QWEN3_CLONE_MODELS = {"qwen3-tts-vc-2026-01-22"}
_QWEN3_DESIGN_MODELS = {"qwen3-tts-vd-2026-01-26"}
_QWEN3_MODELS = {
    *_QWEN3_FLASH_MODELS,
    *_QWEN3_INSTRUCT_MODELS,
    *_QWEN3_CLONE_MODELS,
    *_QWEN3_DESIGN_MODELS,
}
_VOICE_DESIGN_MODELS = {
    "qwen-audio-3.0-tts-plus",
    "qwen-audio-3.0-tts-flash",
    "cosyvoice-v3.5-plus",
    "cosyvoice-v3.5-flash",
    "cosyvoice-v3-plus",
    "cosyvoice-v3-flash",
    *_QWEN3_DESIGN_MODELS,
}
_VOICE_CLONE_MODELS = {
    *_FREEFORM_INSTRUCTION_MODELS,
    "cosyvoice-v3-plus",
    "cosyvoice-v3-flash",
    *_QWEN3_CLONE_MODELS,
}
_V3_FLASH_EMOTION_VOICES = {"longanyang", "longanhuan", "longhuhu_v3"}
_EMOTION_MAP = {
    "neutral": "neutral",
    "calm": "neutral",
    "tender": "happy",
    "happy": "happy",
    "joyful": "happy",
    "excited": "surprised",
    "surprised": "surprised",
    "sad": "sad",
    "melancholic": "sad",
    "angry": "angry",
    "tense": "fearful",
    "fearful": "fearful",
    "disgusted": "disgusted",
}
_QWEN_AUDIO_CONTROL_TAGS = {
    "sad": "[sad]",
    "melancholic": "[sad]",
    "surprised": "[amazed]",
    "excited": "[excited]",
    "angry": "[angry]",
    "fearful": "[panicked]",
    "tense": "[panicked]",
}
# Natural Chinese emotion phrasing for free-form instruction models
# (qwen-audio-3.0-tts-plus/flash, cosyvoice-v3.5-*).  The instruction
# channel understands natural language far better than raw English enum
# values, so "以愤怒的语气" outperforms "以angry情绪".
_INSTRUCTION_EMOTION_PHRASES: dict[str, str] = {
    "angry": "愤怒",
    "sad": "悲伤",
    "melancholic": "忧郁",
    "happy": "愉悦",
    "joyful": "欢快",
    "surprised": "惊讶",
    "excited": "兴奋",
    "fearful": "恐惧",
    "tense": "紧张",
    "disgusted": "厌恶",
    "tender": "温柔",
    "calm": "平静",
    "nostalgic": "怀念",
    "determined": "坚定",
    "anxious": "焦虑",
    "playful": "俏皮",
    "mocking": "戏谑",
    "contempt": "轻蔑",
    "whisper": "轻声耳语",
}
_QWEN_AUDIO_VOCAL_TAGS = {
    "laugh": "[laughing]",
    "laughter": "[laughing]",
    "sigh": "[sighing]",
    "breath": "[gasp]",
    "cough": "[cough]",
    "cry": "[crying]",
    "whisper": "[whispers]",
}
_PRICE_CNY_PER_10K_CHARS = {
    "qwen-audio-3.0-tts-plus": 1.4,
    "qwen-audio-3.0-tts-flash": 1.0,
    "cosyvoice-v3.5-plus": 1.5,
    "cosyvoice-v3.5-flash": 0.8,
    "qwen3-tts-flash": 0.8,
    "qwen3-tts-instruct-flash": 0.8,
    "qwen3-tts-instruct-flash-2026-01-26": 0.8,
    "qwen3-tts-vc-2026-01-22": 0.8,
    "qwen3-tts-vd-2026-01-26": 0.8,
}

# DashScope ``output.finish_reason`` → platform-agnostic provider status.
# Official semantics (Qwen-TTS API docs): "stop" = generation finished
# naturally (SUCCESS); "null" = still generating (streaming intermediate);
# any other value describes an abnormal termination reason.
_FINISH_REASON_NORMALIZATION: dict[str, str] = {
    "stop": "completed",
    "length": "truncated",
    "null": "",
}


def _normalize_finish_reason(raw: str) -> str:
    """Map DashScope finish_reason to a normalized provider status."""
    return _FINISH_REASON_NORMALIZATION.get(raw, raw)


# Curated from the official model-specific voice lists.  Keeping this catalog
# model-scoped prevents the old longxiaochun(v1) -> v3 model mismatch.
_QWEN_AUDIO_PLUS_VOICES: tuple[dict[str, Any], ...] = (
    {
        "voice_id": "longanlingxin",
        "name": "龙安聆心",
        "gender": "female",
        "age": "25",
        "tags": ["温暖", "共情", "陪伴"],
        "language": "zh",
        "trait": "知心温暖音",
        "personality": "温暖，共情，知心",
        "voice_description": "知心温暖音，适合社交陪伴、情感对话",
        "role": "社交陪伴",
    },
    {
        "voice_id": "longanlufeng",
        "name": "龙安陆风",
        "gender": "male",
        "age": "25",
        "tags": ["明亮", "开朗", "青年"],
        "language": "zh",
        "trait": "明亮开朗音",
        "personality": "明亮，开朗，阳光",
        "voice_description": "明亮开朗音，适合青年男性角色、活力场景",
        "role": "社交陪伴",
    },
    {
        "voice_id": "qwen-audio-3.0-tts-plus-longluanxuanling",
        "name": "龙鸾玄灵",
        "gender": "female",
        "age": "25",
        "tags": ["温柔", "知性", "有声书"],
        "language": "zh",
        "trait": "温柔姐姐音",
        "personality": "温柔，知性，优雅",
        "voice_description": "温柔姐姐音，适合有声书、知识分享",
        "role": "有声书",
    },
    {
        "voice_id": "qwen-audio-3.0-tts-plus-longhexiaoxuan",
        "name": "龙鹤晓玄",
        "gender": "male",
        "age": "38",
        "tags": ["儒雅", "沉稳", "有声书"],
        "language": "zh",
        "trait": "文雅书卷音",
        "personality": "儒雅，沉稳，博学",
        "voice_description": "文雅书卷音，适合有声书、成熟男性角色",
        "role": "有声书",
    },
    {
        "voice_id": "qwen-audio-3.0-tts-plus-longsonglinwang",
        "name": "龙松林望",
        "gender": "male",
        "age": "36",
        "tags": ["温暖", "磁性", "夜间电台", "有声书"],
        "language": "zh",
        "trait": "温润磁性音",
        "personality": "温暖，磁性，沉稳",
        "voice_description": "温润磁性音，适合深夜电台、有声书、情感陪伴",
        "role": "有声书",
    },
    {
        "voice_id": "qwen-audio-3.0-tts-plus-longhuiluling",
        "name": "龙惠露灵",
        "gender": "female",
        "age": "25",
        "tags": ["温柔", "关怀", "日常对话"],
        "language": "zh",
        "trait": "温柔贴心音",
        "personality": "温柔，贴心，关怀",
        "voice_description": "温柔贴心音，适合日常对话、情感陪伴、有声书",
        "role": "社交陪伴",
    },
    {
        "voice_id": "qwen-audio-3.0-tts-plus-longyufengmo",
        "name": "龙语风默",
        "gender": "female",
        "age": "22",
        "tags": ["温和", "坚韧", "青年"],
        "language": "zh",
        "trait": "温柔坚韧音",
        "personality": "温和，坚韧，内敛",
        "voice_description": "温柔坚韧音，适合日常对话、情感陪伴",
        "role": "社交陪伴",
    },
    {
        "voice_id": "qwen-audio-3.0-tts-plus-longyinghaizhe",
        "name": "龙颖海哲",
        "gender": "male",
        "age": "35",
        "tags": ["坚定", "有说服力", "成熟"],
        "language": "zh",
        "trait": "理性施压音",
        "personality": "坚定，理性，有说服力",
        "voice_description": "理性施压音，适合成熟男性角色、权威场景",
        "role": "客服",
    },
    {
        "voice_id": "qwen-audio-3.0-tts-plus-longshuojizhu",
        "name": "龙朔纪竹",
        "gender": "male",
        "age": "26",
        "tags": ["标准", "播音", "清晰"],
        "language": "zh",
        "trait": "标准播音音",
        "personality": "标准，清晰，专业",
        "voice_description": "标准播音音，适合新闻播报、正式场景",
        "role": "新闻播报",
    },
    {
        "voice_id": "qwen-audio-3.0-tts-plus-longyanzhihe",
        "name": "龙颜知禾",
        "gender": "female",
        "age": "8",
        "tags": ["温暖", "甜美", "童声"],
        "language": "zh",
        "trait": "温甜童音",
        "personality": "甜美，天真，温暖",
        "voice_description": "温甜童音，适合儿童角色、动漫配音",
        "role": "童声",
    },
    {
        "voice_id": "qwen-audio-3.0-tts-plus-longjutongying",
        "name": "龙聚童莺",
        "gender": "female",
        "age": "7",
        "tags": ["甜美", "可爱", "童声"],
        "language": "zh",
        "trait": "甜萌童音",
        "personality": "甜美，可爱，活泼",
        "voice_description": "甜萌童音，适合日常对话、动漫配音",
        "role": "童声",
    },
)
_QWEN_AUDIO_FLASH_VOICES: tuple[dict[str, Any], ...] = (
    {
        "voice_id": "longanhuan_v3.6",
        "name": "龙安欢 3.6",
        "gender": "female",
        "age": "25",
        "tags": ["自然", "陪伴"],
        "language": "zh",
        "trait": "欢脱元气女",
        "personality": "活泼，开朗，元气",
        "voice_description": "欢脱元气女，适合社交陪伴、日常对话",
        "role": "社交陪伴",
    },
    {
        "voice_id": "longjielidou_v3.6",
        "name": "龙杰力豆 3.6",
        "gender": "male",
        "age": "5",
        "tags": ["天真", "童声"],
        "language": "zh",
        "trait": "天真男童",
        "personality": "天真，活泼，顽皮",
        "voice_description": "天真男童，适合儿童角色、智能玩具",
        "role": "童声",
    },
)
_COSYVOICE_V3_FLASH_VOICES: tuple[dict[str, Any], ...] = (
    {
        "voice_id": "longanyang",
        "name": "龙安阳",
        "gender": "male",
        "age": "20-30",
        "tags": ["阳光", "青年", "可控情绪"],
        "language": "zh",
        "instruction_mode": "emotion",
        "trait": "阳光大男孩",
        "personality": "阳光，开朗，活力",
        "voice_description": "阳光大男孩，适合社交陪伴、青年男性角色",
        "role": "社交陪伴",
    },
    {
        "voice_id": "longanhuan",
        "name": "龙安欢",
        "gender": "female",
        "age": "20-30",
        "tags": ["活力", "开朗", "可控情绪"],
        "language": "zh",
        "instruction_mode": "emotion",
        "trait": "欢脱元气女",
        "personality": "活泼，开朗，元气",
        "voice_description": "欢脱元气女，适合社交陪伴、活力女性角色",
        "role": "社交陪伴",
    },
    {
        "voice_id": "longhuhu_v3",
        "name": "龙呼呼",
        "gender": "female",
        "age": "6-10",
        "tags": ["天真", "活泼", "童声", "可控情绪"],
        "language": "zh",
        "instruction_mode": "emotion",
        "trait": "天真烂漫女童",
        "personality": "天真，活泼，可爱",
        "voice_description": "天真烂漫女童，适合儿童角色、智能玩具",
        "role": "童声",
    },
    {
        "voice_id": "longjielidou_v3",
        "name": "龙杰力豆",
        "gender": "male",
        "age": "10",
        "tags": ["阳光", "顽皮", "童声"],
        "language": "zh",
        "trait": "阳光顽皮男童",
        "personality": "阳光，顽皮，活泼",
        "voice_description": "阳光顽皮男童，适合儿童角色、智能玩具",
        "role": "童声",
    },
    {
        "voice_id": "longxian_v3",
        "name": "龙仙",
        "gender": "female",
        "age": "12",
        "tags": ["大胆", "可爱", "少年"],
        "language": "zh",
        "trait": "豪放可爱女",
        "personality": "大胆，可爱，活泼",
        "voice_description": "豪放可爱女，适合少年角色、动漫配音",
        "role": "童声",
    },
    {
        "voice_id": "longlaotie_v3",
        "name": "龙老铁",
        "gender": "male",
        "age": "25-30",
        "tags": ["直率", "东北口音"],
        "language": "zh",
        "trait": "东北直率男",
        "personality": "直率，豪爽，接地气",
        "voice_description": "东北直率男，适合喜剧角色、接地气场景",
        "role": "方言",
    },
)
_QWEN3_SYSTEM_VOICES: tuple[dict[str, Any], ...] = (
    {
        "voice_id": "Cherry",
        "name": "Cherry",
        "gender": "female",
        "age": "young",
        "tags": ["阳光", "亲和", "自然"],
        "language": "multilingual",
        "trait": "阳光亲和女",
        "personality": "阳光，亲和，自然",
        "voice_description": "阳光亲和女，适合多语种场景、日常对话",
        "role": "社交陪伴",
    },
    {
        "voice_id": "Serena",
        "name": "Serena",
        "gender": "female",
        "age": "young",
        "tags": ["温柔", "青年"],
        "language": "multilingual",
        "trait": "温柔青年女",
        "personality": "温柔，安静，优雅",
        "voice_description": "温柔青年女，适合多语种场景、情感陪伴",
        "role": "社交陪伴",
    },
    {
        "voice_id": "Ethan",
        "name": "Ethan",
        "gender": "male",
        "age": "young",
        "tags": ["阳光", "温暖", "活力"],
        "language": "multilingual",
        "trait": "阳光活力男",
        "personality": "阳光，温暖，活力",
        "voice_description": "阳光活力男，适合多语种场景、青年男性角色",
        "role": "社交陪伴",
    },
    {
        "voice_id": "Chelsie",
        "name": "Chelsie",
        "gender": "female",
        "age": "young",
        "tags": ["柔和", "可爱", "亲密"],
        "language": "multilingual",
        "trait": "柔和可爱女",
        "personality": "柔和，可爱，亲密",
        "voice_description": "柔和可爱女，适合多语种场景、亲密对话",
        "role": "社交陪伴",
    },
)


class DashScopeTTSAdapter(TTSProviderAdapter):
    """Current Alibaba Model Studio HTTP adapter for Qwen-Audio/CosyVoice."""

    def __init__(
        self,
        api_key: str,
        *,
        default_model: str = _DASHSCOPE_DEFAULT_MODEL,
        voice_design_model: str = _DASHSCOPE_DEFAULT_DESIGN_MODEL,
        voice_clone_model: str = "",
        base_url: str = _DASHSCOPE_DEFAULT_BASE_URL,
        base_websocket_url: str = "",  # retained for constructor compatibility
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 300.0,
        optimize_instructions: bool = True,
        voice_clone_enable_preprocess: bool = False,
        cny_per_usd: float = 7.2,
        client: httpx.AsyncClient | None = None,
        download_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("DashScope API key is required")
        self._api_key = api_key
        self._default_model = default_model or _DASHSCOPE_DEFAULT_MODEL
        self._voice_design_model = voice_design_model or self._default_model
        self._voice_clone_model = voice_clone_model or self._default_model
        self._base_url = (base_url or _DASHSCOPE_DEFAULT_BASE_URL).rstrip("/") + "/"
        self._base_websocket_url = base_websocket_url
        self._last_health_error = ""
        self._optimize_instructions = bool(optimize_instructions)
        self._voice_clone_enable_preprocess = bool(voice_clone_enable_preprocess)
        self._cny_per_usd = max(float(cny_per_usd), 0.01)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(
                connect=connect_timeout_s, read=read_timeout_s, write=30.0, pool=10.0
            ),
        )
        # Dedicated client for OSS audio downloads — must NOT carry the API
        # Bearer token, otherwise Alibaba Cloud OSS rejects the request with
        # 403 because URL-signature and Header-signature cannot coexist.
        self._download_client = download_client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=connect_timeout_s, read=read_timeout_s, write=30.0, pool=10.0
            ),
            follow_redirects=True,
        )

    @property
    def provider_name(self) -> str:
        return "dashscope"

    @property
    def provider_type(self) -> TTSProvider:
        return TTSProvider.BAILIAN

    @property
    def capabilities(self) -> TTSProviderCapabilities:
        features = {TTSFeature.LANGUAGE_BOOST}
        if self._default_model not in _QWEN3_MODELS:
            features.update({TTSFeature.SPEED, TTSFeature.VOLUME, TTSFeature.PITCH})
        if (
            self._default_model in _FREEFORM_INSTRUCTION_MODELS
            or self._default_model in _QWEN3_INSTRUCT_MODELS
            or self._default_model == "cosyvoice-v3-flash"
        ):
            features.update({TTSFeature.EMOTION, TTSFeature.INSTRUCTION_CONTROL})
        if self._default_model.startswith("qwen-audio-3.0-tts-"):
            features.add(TTSFeature.PARALINGUISTIC)
        return TTSProviderCapabilities(
            provider=TTSProvider.BAILIAN,
            voice_clone=self._voice_clone_model in _VOICE_CLONE_MODELS,
            voice_design=self._voice_design_model in _VOICE_DESIGN_MODELS,
            system_voice_catalog=bool(self._system_voices_for_model(self._default_model)),
            synthesis_features=features,
            local_reference_audio=self._voice_clone_model in _QWEN3_CLONE_MODELS,
        )

    @property
    def last_health_error(self) -> str:
        return self._last_health_error

    async def prepare_clone_source(self, reference: str) -> str:
        """Keep local Qwen3 clone audio local until it is encoded into the request."""
        if self._voice_clone_model in _QWEN3_CLONE_MODELS:
            path = Path(reference).expanduser()
            if path.is_file():
                return str(path)
        return await super().prepare_clone_source(reference)

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        self._validate_request(request)
        model_id = request.model_id or self._default_model
        voice_id = request.voice_id or self._default_voice(model_id)
        self._validate_model_voice(model_id, voice_id)
        if model_id in _QWEN3_MODELS:
            return await self._synthesize_qwen3(request, model_id=model_id, voice_id=voice_id)
        return await self._synthesize_speech_synthesizer(
            request, model_id=model_id, voice_id=voice_id
        )

    async def _synthesize_speech_synthesizer(
        self, request: TTSRequest, *, model_id: str, voice_id: str
    ) -> TTSResponse:
        output_format = self._output_format(request.output_format)
        sample_rate = self._sample_rate(request.sample_rate)

        rich_text = self._qwen_audio_rich_text(request, model_id=model_id)
        input_payload: dict[str, Any] = {
            "text": rich_text,
            "voice": voice_id,
            "format": output_format,
            "sample_rate": sample_rate,
            "volume": round(max(0.0, min(2.0, request.volume)) * 50),
            "rate": max(0.5, min(2.0, request.speed)),
            "pitch": max(0.5, min(2.0, 2 ** (request.pitch / 12))),
        }
        language = self._language_hint(request)
        if language:
            input_payload["language_hints"] = [language]
        instruction = self._instruction(request, model_id=model_id, voice_id=voice_id)
        if instruction:
            input_payload["instruction"] = instruction
        extension = request.metadata.get("platform_extension") or {}
        if isinstance(extension, dict):
            if "enable_aigc_tag" in extension and model_id in _AIGC_TAG_MODELS:
                input_payload["enable_aigc_tag"] = bool(extension["enable_aigc_tag"])
            elif rich_text != request.text and model_id in _AIGC_TAG_MODELS:
                # Control tags ([sad]/[laughing]/...) were injected into the
                # text — enable platform-side tag interpretation so they are
                # performed rather than read aloud as literal text.
                input_payload["enable_aigc_tag"] = True
            if model_id not in _HOT_FIX_UNSUPPORTED_MODELS and isinstance(
                extension.get("hot_fix"), dict
            ):
                input_payload["hot_fix"] = extension["hot_fix"]
        seed_source = str(request.metadata.get("chapter_voice_seed") or "")
        if seed_source:
            input_payload["seed"] = int.from_bytes(
                hashlib.sha256(f"{seed_source}:{voice_id}".encode()).digest()[:2], "big"
            )

        start = time.monotonic()
        payload = await self._post_json(
            _SYNTHESIS_PATH,
            {"model": model_id, "input": input_payload},
            operation="speech synthesis",
        )
        output_value: Any = payload.get("output")
        if not isinstance(output_value, dict):
            raise ModelGatewayError("DashScope synthesis response did not contain output")
        output: dict[str, Any] = output_value
        audio_value: Any = output.get("audio")
        if not isinstance(audio_value, dict):
            raise ModelGatewayError("DashScope synthesis response did not contain output.audio")
        audio: dict[str, Any] = audio_value
        audio_data = self._decode_audio_data(audio.get("data"))
        audio_url = str(audio.get("url") or "")
        if not audio_data and audio_url:
            audio_data = await self._download_audio(audio_url)
        if not audio_data:
            raise ModelGatewayError("DashScope synthesis returned empty audio")
        usage_value: Any = payload.get("usage")
        usage: dict[str, Any] = usage_value if isinstance(usage_value, dict) else {}
        request_id = str(payload.get("request_id") or "")
        elapsed_ms = (time.monotonic() - start) * 1000
        text_units = int(usage.get("characters") or len(request.text))
        cost_usd, cost_cny = self._estimate_cost(model_id, text_units)
        self._last_health_error = ""
        return TTSResponse(
            audio_data=audio_data,
            duration_ms=0,
            model_id=model_id,
            voice_id=voice_id,
            cost_usd=cost_usd,
            latency_ms=round(elapsed_ms, 2),
            content_type=self._content_type(output_format),
            audio_format=output_format,
            metadata={
                "request_id": request_id,
                "audio_id": str(audio.get("id") or ""),
                "audio_expires_at": audio.get("expires_at"),
                "usage": usage,
                "instruction_applied": bool(instruction),
                "instruction": instruction,
                "sample_rate": sample_rate,
                "estimated_cost_cny": cost_cny,
                "api_family": "speech_synthesizer",
            },
            take_evidence=ProviderTakeEvidence(
                trace_id=request_id,
                provider_status=_normalize_finish_reason(str(output.get("finish_reason") or "")),
                sample_rate=sample_rate,
                audio_size_bytes=len(audio_data),
                text_units=text_units,
                provider_extension={"audio_id": str(audio.get("id") or "")},
            ),
        )

    async def _synthesize_qwen3(
        self, request: TTSRequest, *, model_id: str, voice_id: str
    ) -> TTSResponse:
        if len(request.text) > 600:
            raise ValueError("Qwen3-TTS 单次合成文本不能超过 600 个字符，请先分段")
        input_payload: dict[str, Any] = {"text": request.text, "voice": voice_id}
        language_type = self._qwen3_language_type(request)
        if language_type:
            input_payload["language_type"] = language_type
        instruction = self._qwen3_instruction(request, model_id=model_id)
        if instruction:
            input_payload["instructions"] = instruction
            input_payload["optimize_instructions"] = self._optimize_instructions

        start = time.monotonic()
        payload = await self._post_json(
            _QWEN3_SYNTHESIS_PATH,
            {"model": model_id, "input": input_payload},
            operation="Qwen3 speech synthesis",
        )
        output = payload.get("output")
        if not isinstance(output, dict):
            raise ModelGatewayError("DashScope Qwen3 synthesis response did not contain output")
        audio = output.get("audio")
        if not isinstance(audio, dict):
            raise ModelGatewayError(
                "DashScope Qwen3 synthesis response did not contain output.audio"
            )
        audio_data = self._decode_audio_data(audio.get("data"))
        audio_url = str(audio.get("url") or "")
        if not audio_data and audio_url:
            audio_data = await self._download_audio(audio_url)
        if not audio_data:
            raise ModelGatewayError("DashScope Qwen3 synthesis returned empty audio")
        usage_value = payload.get("usage")
        usage: dict[str, Any] = usage_value if isinstance(usage_value, dict) else {}
        request_id = str(payload.get("request_id") or "")
        text_units = int(usage.get("characters") or len(request.text))
        cost_usd, cost_cny = self._estimate_cost(model_id, text_units)
        audio_format = self._audio_format_from_url(audio_url) or "wav"
        elapsed_ms = (time.monotonic() - start) * 1000
        unsupported_controls = [
            name
            for name, active in (
                ("speed", request.speed != 1.0),
                ("volume", request.volume != 1.0),
                ("pitch", request.pitch != 0),
            )
            if active
        ]
        self._last_health_error = ""
        return TTSResponse(
            audio_data=audio_data,
            model_id=model_id,
            voice_id=voice_id,
            cost_usd=cost_usd,
            latency_ms=round(elapsed_ms, 2),
            content_type=self._content_type(audio_format),
            audio_format=audio_format,
            metadata={
                "request_id": request_id,
                "usage": usage,
                "instruction_applied": bool(instruction),
                "instruction": instruction,
                "optimize_instructions": bool(instruction and self._optimize_instructions),
                "estimated_cost_cny": cost_cny,
                "api_family": "qwen3_multimodal_generation",
                "unsupported_portable_controls": unsupported_controls,
            },
            take_evidence=ProviderTakeEvidence(
                trace_id=request_id,
                provider_status=_normalize_finish_reason(str(output.get("finish_reason") or "")),
                audio_size_bytes=len(audio_data),
                text_units=text_units,
                provider_extension={"api_family": "qwen3_multimodal_generation"},
            ),
        )

    async def clone_voice(self, request: VoiceCloneRequest) -> VoiceCloneResponse:
        model_id = request.model_id or self._voice_clone_model
        if not request.authorized:
            return self._clone_failure(request, model_id, "声音复刻需要明确确认参考说话人授权")
        if model_id not in _VOICE_CLONE_MODELS:
            return self._clone_failure(request, model_id, f"{model_id} 不支持百炼声音复刻")
        if model_id in _QWEN3_CLONE_MODELS:
            return await self._clone_qwen3_voice(request, model_id=model_id)
        if not self._is_public_url(request.file_id):
            return self._clone_failure(
                request,
                model_id,
                "百炼声音复刻需要公网可访问的音频 URL，不能使用本地路径或供应商文件 ID",
            )
        body = {
            "model": "voice-enrollment",
            "input": {
                "action": "create_voice",
                "target_model": model_id,
                "prefix": self._voice_prefix(request.voice_id),
                "url": request.file_id,
                "language_hints": [self._normalize_language(request.language) or "zh"],
                "max_prompt_audio_length": 20.0,
                "enable_preprocess": self._voice_clone_enable_preprocess,
            },
        }
        try:
            payload = await self._post_json(_CUSTOMIZATION_PATH, body, operation="voice cloning")
            output = payload.get("output") if isinstance(payload, dict) else None
            voice_id = str(output.get("voice_id") or "") if isinstance(output, dict) else ""
            if not voice_id:
                return self._clone_failure(request, model_id, "百炼声音复刻未返回 voice_id")
            return VoiceCloneResponse(
                voice_id=voice_id,
                model_id=model_id,
                provider=TTSProvider.BAILIAN,
                status=VoiceCloneStatus.READY,
                message="Voice cloned successfully",
            )
        except Exception as exc:
            _log.error("DashScope voice clone error: %s", exc)
            return self._clone_failure(request, model_id, f"Clone failed: {exc}")

    async def design_voice(self, request: VoiceDesignRequest) -> VoiceDesignResponse:
        model_id = request.model_id or self._voice_design_model
        if model_id not in _VOICE_DESIGN_MODELS:
            return self._design_failure(model_id, f"{model_id} 不支持百炼声音设计")
        language = self._normalize_language(request.language) or "zh"
        prefix = (
            "nf"
            + hashlib.sha256(
                f"{request.description}:{request.preview_text}".encode("utf-8")
            ).hexdigest()[:8]
        )
        if model_id in _QWEN3_DESIGN_MODELS:
            body = {
                "model": "qwen-voice-design",
                "input": {
                    "action": "create",
                    "target_model": model_id,
                    "voice_prompt": request.description[:1000],
                    "preview_text": request.preview_text[:500],
                    "preferred_name": self._qwen_preferred_name(prefix),
                    "language": language,
                },
                "parameters": {"sample_rate": 24000, "response_format": "wav"},
            }
        else:
            body = {
                "model": "voice-enrollment",
                "input": {
                    "action": "create_voice",
                    "target_model": model_id,
                    "voice_prompt": request.description[:500],
                    "preview_text": request.preview_text[:200],
                    "prefix": prefix,
                    "language_hints": [language],
                },
                "parameters": {"sample_rate": 24000, "response_format": "wav"},
            }
        try:
            payload = await self._post_json(_CUSTOMIZATION_PATH, body, operation="voice design")
            output = payload.get("output") if isinstance(payload, dict) else None
            if not isinstance(output, dict):
                return self._design_failure(model_id, "百炼声音设计未返回 output")
            voice_id = str(output.get("voice_id") or output.get("voice") or "")
            preview = output.get("preview_audio")
            preview_audio = self._decode_audio_data(
                preview.get("data") if isinstance(preview, dict) else ""
            )
            preview_format = (
                str(preview.get("response_format") or "wav") if isinstance(preview, dict) else "wav"
            )
            if not voice_id:
                return self._design_failure(model_id, "百炼声音设计未返回 voice_id")
            return VoiceDesignResponse(
                voice_id=voice_id,
                model_id=str(output.get("target_model") or model_id),
                provider=TTSProvider.BAILIAN,
                preview_audio_data=preview_audio,
                preview_audio_format=preview_format,
                status=VoiceCloneStatus.READY,
                message="Voice designed successfully",
            )
        except Exception as exc:
            _log.error("DashScope voice design error: %s", exc)
            return self._design_failure(model_id, f"Design failed: {exc}")

    async def _clone_qwen3_voice(
        self, request: VoiceCloneRequest, *, model_id: str
    ) -> VoiceCloneResponse:
        try:
            audio_data = self._qwen_clone_audio_data(request.file_id)
        except (OSError, ValueError) as exc:
            return self._clone_failure(request, model_id, str(exc))
        input_payload: dict[str, Any] = {
            "action": "create",
            "target_model": model_id,
            "preferred_name": self._qwen_preferred_name(request.voice_id),
            "audio": {"data": audio_data},
            "language": self._normalize_language(request.language) or "zh",
        }
        transcript = (request.reference_transcript or request.clone_prompt).strip()
        if transcript:
            input_payload["text"] = transcript
        try:
            payload = await self._post_json(
                _CUSTOMIZATION_PATH,
                {"model": "qwen-voice-enrollment", "input": input_payload},
                operation="Qwen3 voice cloning",
            )
            output_value = payload.get("output")
            output: dict[str, Any] = output_value if isinstance(output_value, dict) else {}
            voice_id = str(output.get("voice") or "")
            if not voice_id:
                return self._clone_failure(request, model_id, "百炼 Qwen3 声音复刻未返回 voice")
            fallback_reason = str(output.get("fallback_reason") or "")
            message = "Voice cloned successfully"
            if bool(output.get("fallback_mode")):
                message += f" (fallback: {fallback_reason or 'provider fallback mode'})"
            return VoiceCloneResponse(
                voice_id=voice_id,
                model_id=str(output.get("target_model") or model_id),
                provider=TTSProvider.BAILIAN,
                status=VoiceCloneStatus.READY,
                message=message,
            )
        except Exception as exc:
            _log.error("DashScope Qwen3 voice clone error: %s", exc)
            return self._clone_failure(request, model_id, f"Clone failed: {exc}")

    async def list_system_voices(
        self,
        *,
        gender: str | None = None,
        language: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        voices = [
            dict(item, model_id=self._default_model)
            for item in self._system_voices_for_model(self._default_model)
        ]
        if gender:
            voices = [item for item in voices if item.get("gender") == gender.lower()]
        normalized_language = self._normalize_language(language or "")
        if normalized_language:
            voices = [
                item
                for item in voices
                if str(item.get("language") or "").startswith(normalized_language)
                or item.get("language") == "multilingual"
            ]
        return voices[:limit]

    async def health_check(self) -> bool:
        parsed = urlparse(self._base_url)
        healthy = bool(self._api_key and parsed.scheme == "https" and parsed.netloc)
        self._last_health_error = (
            "" if healthy else "DashScope API key or HTTPS base URL is invalid"
        )
        return healthy

    async def shutdown(self) -> None:
        if self._owns_client:
            await self._client.aclose()
        await self._download_client.aclose()

    async def _post_json(
        self, path: str, body: dict[str, Any], *, operation: str
    ) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=body)
        except httpx.RequestError as exc:
            self._last_health_error = str(exc)
            raise ModelGatewayError(
                f"DashScope {operation} request failed: {exc}", is_transient=True
            ) from exc
        if response.status_code == 429:
            raise RateLimitError(
                f"DashScope {operation} rate limited: {response.text[:500]}",
                context={"status_code": 429},
            )
        if response.status_code in {401, 403}:
            raise AuthenticationError(
                f"DashScope {operation} authentication failed: {response.text[:500]}",
                context={"status_code": response.status_code},
            )
        if response.status_code >= 400:
            raise ModelGatewayError(
                f"DashScope {operation} failed ({response.status_code}): {response.text[:500]}",
                is_transient=response.status_code >= 500,
                context={"status_code": response.status_code},
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ModelGatewayError(f"DashScope {operation} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ModelGatewayError(f"DashScope {operation} returned a non-object response")
        if payload.get("code"):
            raise ModelGatewayError(
                f"DashScope {operation} API error {payload.get('code')}: {payload.get('message', '')}"
            )
        return payload

    async def _download_audio(self, url: str) -> bytes:
        # OSS signed URLs must not carry the API Bearer token;
        # upgrade http to https per Alibaba Cloud OSS HTTPS enforcement.
        if url.startswith("http://"):
            url = "https://" + url[7:]
        try:
            response = await self._download_client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ModelGatewayError(f"DashScope audio download failed: {exc}") from exc
        return response.content

    @staticmethod
    def _decode_audio_data(value: Any) -> bytes:
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        if not isinstance(value, str) or not value:
            return b""
        encoded = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
        try:
            return base64.b64decode(encoded, validate=True)
        except ValueError:
            return b""

    @staticmethod
    def _system_voices_for_model(model_id: str) -> tuple[dict[str, Any], ...]:
        if model_id == "qwen-audio-3.0-tts-plus":
            return _QWEN_AUDIO_PLUS_VOICES
        if model_id == "qwen-audio-3.0-tts-flash":
            return _QWEN_AUDIO_FLASH_VOICES
        if model_id == "cosyvoice-v3-flash":
            return _COSYVOICE_V3_FLASH_VOICES
        if model_id in _QWEN3_FLASH_MODELS or model_id in _QWEN3_INSTRUCT_MODELS:
            return _QWEN3_SYSTEM_VOICES
        return ()

    @classmethod
    def _default_voice(cls, model_id: str) -> str:
        voices = cls._system_voices_for_model(model_id)
        if voices:
            return str(voices[0]["voice_id"])
        raise ValueError(f"{model_id} 没有可安全推断的系统音色，请先构建或选择音色")

    @classmethod
    def _validate_model_voice(cls, model_id: str, voice_id: str) -> None:
        known_voices = cls._system_voices_for_model(model_id)
        all_known = {
            str(item["voice_id"])
            for group in (
                _QWEN_AUDIO_PLUS_VOICES,
                _QWEN_AUDIO_FLASH_VOICES,
                _COSYVOICE_V3_FLASH_VOICES,
                _QWEN3_SYSTEM_VOICES,
            )
            for item in group
        }
        if voice_id in all_known and voice_id not in {
            str(item["voice_id"]) for item in known_voices
        }:
            raise ValueError(
                f"百炼音色 {voice_id!r} 不属于模型 {model_id!r}；请重建配音团队或恢复音色绑定模型"
            )
        if voice_id in {"longxiaochun", "longxiaoxia", "longxiaobai", "longyue", "longshu"}:
            raise ValueError(
                f"旧版 CosyVoice 音色 {voice_id!r} 不能用于当前模型 {model_id!r}；请重建配音团队"
            )

    @classmethod
    def _instruction(cls, request: TTSRequest, *, model_id: str, voice_id: str) -> str:
        extension = request.metadata.get("platform_extension") or {}
        explicit = ""
        if isinstance(extension, dict):
            explicit = str(
                extension.get("instruction")
                or extension.get("instructions")
                or extension.get("instruct")
                or ""
            ).strip()
        if model_id == "cosyvoice-v3-flash":
            if voice_id not in _V3_FLASH_EMOTION_VOICES:
                return ""
            emotion = cls._native_emotion(request)
            return f"你说话的情感是 {emotion}."
        if model_id not in _FREEFORM_INSTRUCTION_MODELS:
            return ""
        if explicit:
            return cls._truncate_instruction(explicit)
        parts: list[str] = []
        speaker_kind = str(request.metadata.get("speaker_kind") or "角色")
        emotion = str(request.emotion or "neutral")
        # Qwen-Audio already receives a native emotion control tag in the text;
        # do not spend its official 100-unit instruction budget repeating it.
        if emotion and emotion != "neutral" and not model_id.startswith("qwen-audio-3.0-tts-"):
            phrase = _INSTRUCTION_EMOTION_PHRASES.get(emotion.lower(), emotion)
            parts.append(f"以{phrase}的语气")
        tone = str(request.metadata.get("tone_hint") or "").strip()
        if tone:
            parts.append(tone)
        direction = request.metadata.get("vocal_direction") or {}
        if isinstance(direction, dict):
            style = str(direction.get("delivery_style") or "").strip()
            if style and style != "natural":
                parts.append(cls._delivery_style_phrase(style))
        # Lines are synthesized independently, but acting must not be. Convert
        # projected neighboring turns into a compact continuity direction.
        # Their verbatim text is intentionally excluded so it cannot leak into
        # the generated speech or consume the short instruction budget.
        context = request.metadata.get("performance_context") or {}
        if isinstance(context, dict):
            current_speaker = str(request.metadata.get("character_name") or speaker_kind)
            previous_speaker = str(context.get("previous_speaker") or "")
            if previous_speaker:
                if current_speaker and previous_speaker == current_speaker:
                    parts.append("延续上一句语气")
                else:
                    parts.append("自然承接对话")

        source_text = str(request.metadata.get("source_text") or request.text).strip()
        if len(source_text) <= 8:
            parts.append("短句连贯说完")
        if isinstance(direction, dict):
            parts.extend(cls._performance_direction_parts(request, direction))
        parts.append(f"像真实{speaker_kind}说话，避免播报腔")
        return cls._truncate_instruction("，".join(dict.fromkeys(parts)) + "。")

    @classmethod
    def _qwen3_instruction(cls, request: TTSRequest, *, model_id: str) -> str:
        if model_id not in _QWEN3_INSTRUCT_MODELS:
            return ""
        extension = request.metadata.get("platform_extension") or {}
        if isinstance(extension, dict):
            explicit = str(
                extension.get("instructions")
                or extension.get("instruction")
                or extension.get("instruct")
                or ""
            ).strip()
            if explicit:
                return explicit
        parts: list[str] = []
        emotion = str(request.metadata.get("performance_emotion") or request.emotion or "")
        if emotion and emotion != "neutral":
            parts.append(f"情绪：{emotion}")
        tone = str(request.metadata.get("tone_hint") or "").strip()
        if tone:
            parts.append(tone)
        direction = request.metadata.get("vocal_direction") or {}
        if isinstance(direction, dict):
            style = str(direction.get("delivery_style") or "").strip()
            if style and style != "natural":
                parts.append(cls._delivery_style_phrase(style))
            parts.extend(cls._performance_direction_parts(request, direction))
        speaker_kind = str(request.metadata.get("speaker_kind") or "角色")
        parts.append(f"像真实{speaker_kind}说话，避免播报腔")
        return cls._truncate_instruction("，".join(dict.fromkeys(parts)) + "。", 192)

    @staticmethod
    def _delivery_style_phrase(style: str) -> str:
        return {
            "intimate": "亲密近讲",
            "narrative": "自然讲述",
            "conversational": "口语对话",
            "dramatic": "戏剧张力",
            "broadcast": "专业播讲但不做新闻腔",
        }.get(style, style)

    @classmethod
    def _performance_direction_parts(
        cls,
        request: TTSRequest,
        direction: dict[str, Any],
    ) -> list[str]:
        """Project provider-neutral acting intent into executable directions.

        Alibaba instruction-capable models can consume natural-language acting
        notes that MiniMax cannot.  Keep the projection compact and categorical
        so it guides a performance without turning normalized 0..1 dimensions
        into brittle pseudo-precision.
        """

        parts: list[str] = []
        intent = str(direction.get("intent") or "").strip()
        if intent:
            parts.append(f"行动意图：{cls._truncate_instruction(intent, 36)}")

        def value(name: str, default: float) -> float:
            try:
                return max(0.0, min(1.0, float(direction.get(name, default))))
            except (TypeError, ValueError):
                return default

        energy = value("energy", 0.5)
        articulation = value("articulation", 0.6)
        breathiness = value("breathiness", 0.2)
        tension = value("tension", 0.3)
        intimacy = value("intimacy", 0.5)
        if energy >= 0.72:
            parts.append("能量充足但不喊")
        elif energy <= 0.32:
            parts.append("收敛能量")
        if articulation >= 0.75:
            parts.append("咬字清楚但不逐字")
        elif articulation <= 0.38:
            parts.append("连贯口语化")
        if breathiness >= 0.55:
            parts.append("保留轻微气息感")
        if tension >= 0.68:
            parts.append("内在张力明显但不夸张")
        if intimacy >= 0.7:
            parts.append("贴近听者、近讲")
        elif intimacy <= 0.3:
            parts.append("保持叙述距离")

        narrator_distance = str(request.metadata.get("narrator_distance") or "").strip().lower()
        if narrator_distance == "close":
            parts.append("近距离旁白")
        elif narrator_distance == "distant":
            parts.append("远景旁白")

        raw_stress_words = request.metadata.get("stress_words") or []
        if isinstance(raw_stress_words, list):
            stress_words = [
                cls._truncate_instruction(str(word).strip(), 12)
                for word in raw_stress_words[:3]
                if str(word).strip()
            ]
            if stress_words:
                parts.append("重读" + "、".join(f"“{word}”" for word in stress_words))
        return parts

    @classmethod
    def _qwen_audio_rich_text(cls, request: TTSRequest, *, model_id: str) -> str:
        if not model_id.startswith("qwen-audio-3.0-tts-"):
            return request.text
        insertions: list[tuple[float, str]] = []
        emotion_tag = _QWEN_AUDIO_CONTROL_TAGS.get(str(request.emotion).lower())
        if emotion_tag:
            insertions.append((0.0, emotion_tag))
        raw_tags = request.metadata.get("paralinguistic_tags") or []
        if isinstance(raw_tags, list):
            for item in raw_tags:
                if not isinstance(item, dict):
                    continue
                tag = _QWEN_AUDIO_VOCAL_TAGS.get(str(item.get("tag_type") or "").lower())
                if tag:
                    insertions.append((float(item.get("position") or 0.5), tag))
        if not insertions:
            return request.text
        text = request.text
        offset = 0
        for position, tag in sorted(insertions, key=lambda value: value[0]):
            index = max(0, min(len(request.text), round(len(request.text) * position))) + offset
            insertion = f"{tag}"
            text = text[:index] + insertion + text[index:]
            offset += len(insertion)
        return text

    @staticmethod
    def _native_emotion(request: TTSRequest) -> str:
        raw = str(request.metadata.get("performance_emotion") or request.emotion or "neutral")
        return _EMOTION_MAP.get(raw.lower(), "neutral")

    @staticmethod
    def _truncate_instruction(value: str, maximum_units: int = 96) -> str:
        result: list[str] = []
        units = 0
        for char in value.strip():
            char_units = 2 if ord(char) > 127 else 1
            if units + char_units > maximum_units:
                break
            result.append(char)
            units += char_units
        return "".join(result).rstrip("，； ")

    @staticmethod
    def _normalize_language(value: str) -> str:
        normalized = value.strip().lower().replace("_", "-")
        aliases = {"auto": "", "chinese": "zh", "mandarin": "zh", "english": "en"}
        normalized = aliases.get(normalized, normalized)
        return normalized.split("-", 1)[0]

    @classmethod
    def _language_hint(cls, request: TTSRequest) -> str:
        language = str(request.metadata.get("language_code") or request.language_boost or "")
        return cls._normalize_language(language)

    @classmethod
    def _qwen3_language_type(cls, request: TTSRequest) -> str:
        normalized = cls._language_hint(request)
        return {
            "zh": "Chinese",
            "en": "English",
            "de": "German",
            "it": "Italian",
            "pt": "Portuguese",
            "es": "Spanish",
            "ja": "Japanese",
            "ko": "Korean",
            "fr": "French",
            "ru": "Russian",
        }.get(normalized, "Auto")

    @staticmethod
    def _sample_rate(requested: int) -> int:
        return min(_SUPPORTED_SAMPLE_RATES, key=lambda value: abs(value - requested))

    @staticmethod
    def _output_format(requested: str) -> str:
        normalized = requested.strip().lower()
        return normalized if normalized in _SUPPORTED_FORMATS else "wav"

    @staticmethod
    def _content_type(audio_format: str) -> str:
        return {
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "pcm": "audio/L16",
            "opus": "audio/opus",
        }.get(audio_format, "application/octet-stream")

    @staticmethod
    def _audio_format_from_url(url: str) -> str:
        suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
        return suffix if suffix in _SUPPORTED_FORMATS else ""

    @staticmethod
    def _voice_prefix(value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9]", "", value)[:10]
        if normalized:
            return normalized
        return "nf" + hashlib.sha256(value.encode()).hexdigest()[:8]

    @staticmethod
    def _qwen_preferred_name(value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")[:16]
        if normalized:
            return normalized
        return "nf_" + hashlib.sha256(value.encode()).hexdigest()[:10]

    @classmethod
    def _qwen_clone_audio_data(cls, value: str) -> str:
        if cls._is_public_url(value) or value.startswith("data:audio/"):
            return value
        path = Path(value).expanduser()
        if not path.is_file():
            raise ValueError(f"Qwen3 声音复刻参考音频不存在: {value}")
        mime = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".mp4": "audio/mp4",
        }.get(path.suffix.lower())
        if not mime:
            raise ValueError("Qwen3 声音复刻仅接受 WAV、MP3、M4A/MP4 参考音频")
        raw = path.read_bytes()
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError("Qwen3 声音复刻参考音频不能超过 10 MB")
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

    def _estimate_cost(self, model_id: str, text_units: int) -> tuple[float, float]:
        rate = _PRICE_CNY_PER_10K_CHARS.get(model_id)
        if rate is None:
            for stable_id, stable_rate in _PRICE_CNY_PER_10K_CHARS.items():
                if model_id.startswith(stable_id + "-"):
                    rate = stable_rate
                    break
        if rate is None:
            return 0.0, 0.0
        cost_cny = max(text_units, 0) / 10_000 * rate
        return round(cost_cny / self._cny_per_usd, 8), round(cost_cny, 8)

    @staticmethod
    def _is_public_url(value: str) -> bool:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _clone_failure(
        request: VoiceCloneRequest, model_id: str, message: str
    ) -> VoiceCloneResponse:
        return VoiceCloneResponse(
            voice_id=request.voice_id,
            model_id=model_id,
            provider=TTSProvider.BAILIAN,
            status=VoiceCloneStatus.FAILED,
            message=message,
        )

    @staticmethod
    def _design_failure(model_id: str, message: str) -> VoiceDesignResponse:
        return VoiceDesignResponse(
            model_id=model_id,
            provider=TTSProvider.BAILIAN,
            status=VoiceCloneStatus.FAILED,
            message=message,
        )


# ─── Bailian personality inference ────────────────────────────────────────

_BAILIAN_PERSONALITY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("温暖", ("温暖", "暖心", "温和", "温润", "温柔")),
    ("沉稳", ("沉稳", "冷静", "淡定", "从容", "沉稳")),
    ("活泼", ("活泼", "开朗", "元气", "活力", "欢脱")),
    ("知性", ("知性", "智慧", "睿智", "博学", "书卷")),
    ("甜美", ("甜美", "甜萌", "可爱", "娇气")),
    ("磁性", ("磁性", "浑厚", "厚实", "低沉")),
    ("天真", ("天真", "烂漫", "童真", "稚气")),
    ("坚定", ("坚定", "权威", "威严", "说服力")),
    ("儒雅", ("儒雅", "文雅", "优雅", "温润")),
    ("阳光", ("阳光", "明亮", "清爽", "清爽")),
    ("直率", ("直率", "豪爽", "接地气", "东北")),
    ("共情", ("共情", "贴心", "关怀", "陪伴")),
)


def _infer_bailian_personality(voice: dict[str, Any]) -> str:
    """Infer personality keywords from Bailian voice metadata.

    Uses trait, tags, and name to infer personality labels.
    Returns a comma-separated string of matched personality labels, or '' if none.
    """
    trait = str(voice.get("trait") or "")
    tags = voice.get("tags")
    tag_text = (
        " ".join(str(t) for t in tags) if isinstance(tags, (list, tuple)) else str(tags or "")
    )
    name = str(voice.get("name") or "")
    combined = f"{trait} {tag_text} {name}".lower()
    matched: list[str] = []
    for label, tokens in _BAILIAN_PERSONALITY_PATTERNS:
        if any(token in combined for token in tokens):
            matched.append(label)
    return "，".join(matched[:3]) if matched else ""
