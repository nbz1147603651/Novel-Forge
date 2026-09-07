"""Canonical TTS provider, model, and settings capability catalog.

The catalog is deliberately transport-neutral.  Gateway adapters use it to
declare model-dependent capabilities, while API/Desktop projections use the
same records to render provider switches and advanced settings.  Provider
secrets are represented only by guarded creation-parameter ids; values never
enter this module or any read model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from novel_forge.tts.schemas import TTSFeature, TTSProvider, TTSProviderCapabilities

TTSModelPurpose = Literal["formal", "preview", "clone", "design"]
TTSSettingKind = Literal["boolean", "number", "path", "secret", "select", "text"]


@dataclass(frozen=True)
class TTSSettingOption:
    """One stable option for a provider setting."""

    value: str
    label: str


@dataclass(frozen=True)
class TTSProviderSettingSpec:
    """A guarded setting rendered by any UI without provider-specific branches."""

    parameter_id: str
    label: str
    description: str
    kind: TTSSettingKind = "text"
    default_value: str = ""
    options: tuple[TTSSettingOption, ...] = ()
    model_purpose: TTSModelPurpose | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    advanced: bool = False
    experimental: bool = False
    visible_model_ids: tuple[str, ...] = ()
    visibility_parameter_id: str = ""
    visibility_values: tuple[str, ...] = ()

    @property
    def is_secret(self) -> bool:
        return self.kind == "secret"


@dataclass(frozen=True)
class TTSProviderModelSpec:
    """One provider model and the features that are native to that model."""

    model_id: str
    label: str
    purposes: tuple[TTSModelPurpose, ...] = ("formal",)
    synthesis_features: frozenset[TTSFeature] = frozenset()
    voice_clone: bool = False
    voice_design: bool = False
    system_voice_catalog: bool = False
    local_reference_audio: bool = False
    adapter_supported: bool = True
    recommended: bool = False
    max_input_chars: int = 0
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TTSProviderSpec:
    """Canonical provider metadata shared by gateway, API, and desktop clients."""

    provider_id: str
    label: str
    aliases: tuple[str, ...]
    official_docs_url: str
    default_model: str
    active_model_parameter_id: str
    models: tuple[TTSProviderModelSpec, ...]
    settings: tuple[TTSProviderSettingSpec, ...]
    highlights: tuple[str, ...]
    is_local: bool = False
    adapter_status: Literal["production", "preview", "mock"] = "production"

    def models_for(self, purpose: TTSModelPurpose) -> tuple[TTSProviderModelSpec, ...]:
        return tuple(
            model for model in self.models if purpose in model.purposes and model.adapter_supported
        )

    def model(self, model_id: str) -> TTSProviderModelSpec | None:
        normalized = model_id.strip()
        return next((model for model in self.models if model.model_id == normalized), None)


def _option(value: str, label: str = "") -> TTSSettingOption:
    return TTSSettingOption(value=value, label=label or value)


def _model(
    model_id: str,
    label: str,
    *,
    purposes: tuple[TTSModelPurpose, ...] = ("formal", "preview"),
    features: tuple[TTSFeature, ...] = (),
    voice_clone: bool = False,
    voice_design: bool = False,
    system_voices: bool = False,
    local_reference_audio: bool = False,
    adapter_supported: bool = True,
    recommended: bool = False,
    max_input_chars: int = 0,
    notes: tuple[str, ...] = (),
) -> TTSProviderModelSpec:
    return TTSProviderModelSpec(
        model_id=model_id,
        label=label,
        purposes=purposes,
        synthesis_features=frozenset(features),
        voice_clone=voice_clone,
        voice_design=voice_design,
        system_voice_catalog=system_voices,
        local_reference_audio=local_reference_audio,
        adapter_supported=adapter_supported,
        recommended=recommended,
        max_input_chars=max_input_chars,
        notes=notes,
    )


_PORTABLE_PROSODY = (TTSFeature.SPEED, TTSFeature.VOLUME, TTSFeature.PITCH)
_MINIMAX_COMMON = (
    *_PORTABLE_PROSODY,
    TTSFeature.EMOTION,
    TTSFeature.PRONUNCIATION,
    TTSFeature.LANGUAGE_BOOST,
    TTSFeature.VOICE_EFFECTS,
    TTSFeature.NATIVE_SUBTITLES,
    TTSFeature.STREAMING_AUDIO,
)
_MINIMAX_MODELS = tuple(
    _model(
        model_id,
        label,
        purposes=("formal", "preview", "clone", "design"),
        features=(
            *_MINIMAX_COMMON,
            *((TTSFeature.PARALINGUISTIC,) if model_id.startswith("speech-2.8") else ()),
        ),
        voice_clone=True,
        voice_design=True,
        system_voices=True,
        local_reference_audio=True,
        recommended=model_id == "speech-2.8-hd",
        max_input_chars=10_000,
        notes=(
            "speech-2.8 支持自然声音事件标签。"
            if model_id.startswith("speech-2.8")
            else "长文本由异步任务接口处理。",
        ),
    )
    for model_id, label in (
        ("speech-2.8-hd", "Speech 2.8 HD · 成片优先"),
        ("speech-2.8-turbo", "Speech 2.8 Turbo · 快速生成"),
        ("speech-2.6-hd", "Speech 2.6 HD"),
        ("speech-2.6-turbo", "Speech 2.6 Turbo"),
        ("speech-02-hd", "Speech 02 HD"),
        ("speech-02-turbo", "Speech 02 Turbo"),
        ("speech-01-hd", "Speech 01 HD"),
        ("speech-01-turbo", "Speech 01 Turbo"),
    )
)

_DASHSCOPE_EXPRESSIVE = (
    *_PORTABLE_PROSODY,
    TTSFeature.EMOTION,
    TTSFeature.INSTRUCTION_CONTROL,
    TTSFeature.LANGUAGE_BOOST,
    TTSFeature.STREAMING_AUDIO,
)
_DASHSCOPE_MODELS = (
    _model(
        "qwen-audio-3.0-tts-plus",
        "Qwen Audio 3.0 Plus · 有声内容优先",
        purposes=("formal", "preview", "clone", "design"),
        features=(*_DASHSCOPE_EXPRESSIVE, TTSFeature.PARALINGUISTIC),
        voice_clone=True,
        voice_design=True,
        system_voices=True,
        recommended=True,
    ),
    _model(
        "qwen-audio-3.0-tts-flash",
        "Qwen Audio 3.0 Flash",
        purposes=("formal", "preview", "clone", "design"),
        features=(*_DASHSCOPE_EXPRESSIVE, TTSFeature.PARALINGUISTIC),
        voice_clone=True,
        voice_design=True,
        system_voices=True,
    ),
    _model(
        "cosyvoice-v3.5-plus",
        "CosyVoice 3.5 Plus · 定制音色优先",
        purposes=("formal", "preview", "clone", "design"),
        features=_DASHSCOPE_EXPRESSIVE,
        voice_clone=True,
        voice_design=True,
    ),
    _model(
        "cosyvoice-v3.5-flash",
        "CosyVoice 3.5 Flash",
        purposes=("formal", "preview", "clone", "design"),
        features=_DASHSCOPE_EXPRESSIVE,
        voice_clone=True,
        voice_design=True,
    ),
    _model(
        "cosyvoice-v3-plus",
        "CosyVoice 3 Plus",
        purposes=("formal", "preview", "clone", "design"),
        features=(*_PORTABLE_PROSODY, TTSFeature.LANGUAGE_BOOST),
        voice_clone=True,
        voice_design=True,
        system_voices=True,
    ),
    _model(
        "cosyvoice-v3-flash",
        "CosyVoice 3 Flash",
        purposes=("formal", "preview", "clone", "design"),
        features=_DASHSCOPE_EXPRESSIVE,
        voice_clone=True,
        voice_design=True,
        system_voices=True,
    ),
    _model(
        "qwen3-tts-flash",
        "Qwen3 TTS Flash",
        features=(TTSFeature.LANGUAGE_BOOST,),
        system_voices=True,
        max_input_chars=600,
    ),
    _model(
        "qwen3-tts-instruct-flash",
        "Qwen3 TTS Instruct Flash",
        features=(
            TTSFeature.EMOTION,
            TTSFeature.INSTRUCTION_CONTROL,
            TTSFeature.LANGUAGE_BOOST,
        ),
        system_voices=True,
        max_input_chars=600,
    ),
    _model(
        "qwen3-tts-vc-2026-01-22",
        "Qwen3 TTS Voice Clone",
        purposes=("clone",),
        features=(TTSFeature.EMOTION, TTSFeature.INSTRUCTION_CONTROL),
        voice_clone=True,
        local_reference_audio=True,
        max_input_chars=600,
    ),
    _model(
        "qwen3-tts-vd-2026-01-26",
        "Qwen3 TTS Voice Design",
        purposes=("design",),
        features=(TTSFeature.EMOTION, TTSFeature.INSTRUCTION_CONTROL),
        voice_design=True,
        max_input_chars=600,
    ),
)

_QWEN3_MODELS = (
    _model(
        "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "Qwen3 TTS 1.7B CustomVoice · 正式",
        features=(TTSFeature.SPEED, TTSFeature.EMOTION, TTSFeature.INSTRUCTION_CONTROL),
        system_voices=True,
        recommended=True,
    ),
    _model(
        "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "Qwen3 TTS 0.6B CustomVoice · 试听",
        purposes=("preview",),
        features=(TTSFeature.SPEED, TTSFeature.EMOTION, TTSFeature.INSTRUCTION_CONTROL),
        system_voices=True,
    ),
    _model(
        "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        "Qwen3 TTS 1.7B VoiceDesign",
        purposes=("design",),
        features=(TTSFeature.EMOTION, TTSFeature.INSTRUCTION_CONTROL),
        voice_design=True,
    ),
    _model(
        "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        "Qwen3 TTS 1.7B Base · 高保真克隆",
        purposes=("clone",),
        voice_clone=True,
        local_reference_audio=True,
    ),
)

_COSYVOICE_MODELS = (
    _model(
        "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        "Fun-CosyVoice 3 0.5B · 推荐",
        purposes=("formal", "preview", "clone"),
        features=(TTSFeature.INSTRUCTION_CONTROL, TTSFeature.PRONUNCIATION),
        voice_clone=True,
        local_reference_audio=True,
        recommended=True,
    ),
    _model(
        "FunAudioLLM/CosyVoice2-0.5B",
        "CosyVoice 2 0.5B",
        purposes=("formal", "preview", "clone"),
        features=(TTSFeature.INSTRUCTION_CONTROL,),
        voice_clone=True,
        local_reference_audio=True,
    ),
    _model(
        "FunAudioLLM/CosyVoice-300M-Instruct",
        "CosyVoice 300M Instruct · 兼容",
        purposes=("formal", "preview", "clone"),
        features=(TTSFeature.INSTRUCTION_CONTROL,),
        voice_clone=True,
        local_reference_audio=True,
    ),
)

_LANGUAGE_BOOST_OPTIONS = tuple(
    _option(value, label)
    for value, label in (
        ("auto", "自动识别"),
        ("Chinese", "普通话"),
        ("Chinese,Yue", "粤语"),
        ("English", "英语"),
        ("Japanese", "日语"),
        ("Korean", "韩语"),
        ("Spanish", "西班牙语"),
        ("French", "法语"),
        ("German", "德语"),
        ("Portuguese", "葡萄牙语"),
    )
)

_PROVIDERS = (
    TTSProviderSpec(
        provider_id="minimax",
        label="MiniMax",
        aliases=("minimax",),
        official_docs_url="https://platform.minimax.io/docs/api-reference/speech-t2a-http",
        default_model="speech-2.8-hd",
        active_model_parameter_id="tts-model",
        models=_MINIMAX_MODELS,
        highlights=("自然声音标签", "原生字词字幕", "超长文本异步合成", "声音克隆与设计"),
        settings=(
            TTSProviderSettingSpec(
                "tts-minimax-api-key", "MiniMax API Key", "语音合成、克隆与设计凭据。", "secret"
            ),
            TTSProviderSettingSpec(
                "tts-minimax-base-url",
                "API 地址",
                "默认官方全球端点；区域账号可填写官方兼容端点。",
                default_value="https://api.minimax.io/v1",
            ),
            TTSProviderSettingSpec(
                "tts-minimax-group-id",
                "Group ID",
                "仅旧版账号兼容时填写。",
                advanced=True,
            ),
            TTSProviderSettingSpec(
                "tts-minimax-bitrate",
                "MP3 比特率",
                "仅 MP3 生效；有声成片建议 128 或 256 kbps。",
                "select",
                "128000",
                tuple(
                    _option(value, label)
                    for value, label in (
                        ("32000", "32 kbps"),
                        ("64000", "64 kbps"),
                        ("128000", "128 kbps · 推荐"),
                        ("256000", "256 kbps"),
                    )
                ),
                visibility_parameter_id="tts-output-format",
                visibility_values=("mp3",),
            ),
            TTSProviderSettingSpec(
                "tts-minimax-channel",
                "声道",
                "人声素材建议单声道，立体声声场在混音阶段完成。",
                "select",
                "1",
                (_option("1", "单声道 · 推荐"), _option("2", "双声道")),
            ),
            TTSProviderSettingSpec(
                "tts-minimax-language-boost",
                "语言增强",
                "显式语种可减少多音字和跨语种误读。",
                "select",
                "auto",
                _LANGUAGE_BOOST_OPTIONS,
            ),
            TTSProviderSettingSpec(
                "tts-minimax-force-cbr",
                "恒定比特率",
                "多段 MP3 成片建议开启，稳定时长与拼接。",
                "boolean",
                "true",
                visibility_parameter_id="tts-output-format",
                visibility_values=("mp3",),
            ),
            TTSProviderSettingSpec(
                "tts-minimax-english-normalization",
                "英文数字与日期规范化",
                "改善英文数字、日期和公式读法，但会增加少量处理延迟。",
                "boolean",
                "false",
            ),
            TTSProviderSettingSpec(
                "tts-minimax-continuous-sound",
                "长段连续推理兼容开关",
                "仅 Speech 2.8 兼容端点使用；官方公开 HTTP 契约未稳定列出该字段。",
                "boolean",
                "false",
                advanced=True,
                experimental=True,
                visible_model_ids=("speech-2.8-hd", "speech-2.8-turbo"),
            ),
            TTSProviderSettingSpec(
                "tts-minimax-async-timeout-s",
                "超长文本任务超时（秒）",
                "异步任务提交、轮询与下载的总等待上限。",
                "number",
                "7200",
                minimum=60,
                maximum=86400,
                step=60,
                advanced=True,
            ),
            TTSProviderSettingSpec(
                "tts-aigc-watermark",
                "AIGC 合规水印",
                "会在音频尾部加入可听节奏标识，仅合规传播明确要求时开启。",
                "boolean",
                "false",
                advanced=True,
            ),
        ),
    ),
    TTSProviderSpec(
        provider_id="dashscope",
        label="阿里百炼 / DashScope",
        aliases=("dashscope", "bailian", "百炼", "阿里百炼"),
        official_docs_url="https://help.aliyun.com/en/model-studio/tts-model/",
        default_model="qwen-audio-3.0-tts-plus",
        active_model_parameter_id="tts-dashscope-model",
        models=_DASHSCOPE_MODELS,
        highlights=("自然语言表演指令", "情绪与副语言标签", "模型绑定音色", "克隆与设计分路"),
        settings=(
            TTSProviderSettingSpec(
                "tts-dashscope-api-key", "百炼 API Key", "Model Studio 业务空间凭据。", "secret"
            ),
            TTSProviderSettingSpec(
                "tts-dashscope-base-url",
                "API 地址",
                "生产环境优先使用与 API Key 同地域的 Workspace 专属地址。",
                default_value="https://dashscope.aliyuncs.com/api/v1",
            ),
            TTSProviderSettingSpec(
                "tts-dashscope-preview-model",
                "试听模型",
                "留空时随正式模型；已绑定音色始终使用其所属模型。",
                "select",
                "",
                model_purpose="preview",
            ),
            TTSProviderSettingSpec(
                "tts-dashscope-voice-clone-model",
                "声音复刻模型",
                "自定义音色与目标模型强绑定，切换后不能复用旧 voice_id。",
                "select",
                "qwen-audio-3.0-tts-plus",
                model_purpose="clone",
            ),
            TTSProviderSettingSpec(
                "tts-dashscope-voice-design-model",
                "声音设计模型",
                "用角色描述创建新音色；Qwen Audio 与 CosyVoice 3.5 均可用。",
                "select",
                "cosyvoice-v3.5-plus",
                model_purpose="design",
            ),
            TTSProviderSettingSpec(
                "tts-dashscope-optimize-instructions",
                "Qwen3 指令优化",
                "只在 Qwen3 Instruct 模型且存在指令时生效。",
                "boolean",
                "true",
                visible_model_ids=("qwen3-tts-instruct-flash",),
            ),
            TTSProviderSettingSpec(
                "tts-dashscope-voice-clone-enable-preprocess",
                "复刻参考音频预处理",
                "嘈杂素材可开启；干净录音建议关闭以保留声纹细节。",
                "boolean",
                "false",
                advanced=True,
            ),
            TTSProviderSettingSpec(
                "tts-dashscope-cny-per-usd",
                "人民币兑美元预算汇率",
                "只用于项目成本预算折算，不影响平台账单。",
                "number",
                "7.2",
                minimum=0.1,
                maximum=20,
                step=0.1,
                advanced=True,
            ),
        ),
    ),
    TTSProviderSpec(
        provider_id="tencent",
        label="腾讯云",
        aliases=("tencent", "腾讯", "腾讯云"),
        official_docs_url="https://cloud.tencent.com/document/api/1073/37995",
        default_model="tencent-text-to-voice",
        active_model_parameter_id="",
        models=(
            _model(
                "tencent-text-to-voice",
                "基础语音合成 TextToVoice",
                features=(
                    TTSFeature.SPEED,
                    TTSFeature.EMOTION,
                    TTSFeature.NATIVE_SUBTITLES,
                    TTSFeature.SSML,
                ),
                system_voices=True,
                recommended=True,
            ),
        ),
        highlights=("多情感音色", "原生时间戳", "断句敏感度", "一句话复刻音色 ID"),
        settings=(
            TTSProviderSettingSpec(
                "tts-tencent-secret-id", "SecretId", "腾讯云 API 签名账号。", "secret"
            ),
            TTSProviderSettingSpec(
                "tts-tencent-secret-key", "SecretKey", "腾讯云 API 签名密钥。", "secret"
            ),
            TTSProviderSettingSpec(
                "tts-tencent-project-id",
                "ProjectId",
                "腾讯云项目 ID，默认 0。",
                "number",
                "0",
                minimum=0,
                step=1,
                advanced=True,
            ),
            TTSProviderSettingSpec(
                "tts-tencent-voice-type",
                "默认音色类型",
                "填写 VoiceType 数字 ID；角色选角会覆盖此默认值。",
                default_value="0",
            ),
            TTSProviderSettingSpec(
                "tts-tencent-primary-language",
                "主语言",
                "1 为中文，2 为英文。",
                "select",
                "1",
                (_option("1", "中文"), _option("2", "英文")),
            ),
            TTSProviderSettingSpec(
                "tts-tencent-emotion-intensity",
                "情感强度",
                "仅多情感音色生效；100 为平台默认。",
                "number",
                "100",
                minimum=50,
                maximum=200,
                step=10,
            ),
            TTSProviderSettingSpec(
                "tts-tencent-segment-rate",
                "断句敏感度",
                "值越大越倾向只按标点断句，官方建议谨慎调整。",
                "select",
                "0",
                (_option("0", "默认"), _option("1", "较少断句"), _option("2", "最少断句")),
                advanced=True,
            ),
        ),
    ),
    TTSProviderSpec(
        provider_id="volcengine_ark",
        label="火山方舟",
        aliases=("volcengine_ark", "volcengine", "火山方舟", "豆包"),
        official_docs_url="https://www.volcengine.com/docs/82379/2516286",
        default_model="doubao-seed-tts-2.0",
        active_model_parameter_id="tts-volcengine-model",
        models=(
            _model(
                "doubao-seed-tts-2.0",
                "Doubao Seed TTS 2.0 · Agent Plan",
                features=(
                    TTSFeature.SPEED,
                    TTSFeature.VOLUME,
                    TTSFeature.PITCH,
                    TTSFeature.EMOTION,
                    TTSFeature.INSTRUCTION_CONTROL,
                ),
                system_voices=True,
                recommended=True,
            ),
        ),
        highlights=("Seed TTS 2.0", "上下文表演指令", "HTTP Chunked 单向流", "小说音色目录"),
        settings=(
            TTSProviderSettingSpec(
                "volcengine-ark-api-key", "方舟 API Key", "Agent Plan API Key。", "secret"
            ),
            TTSProviderSettingSpec(
                "tts-volcengine-base-url",
                "接口地址",
                "Agent Plan HTTP Chunked 单向流端点。",
                default_value="https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional",
            ),
            TTSProviderSettingSpec(
                "tts-volcengine-resource-id",
                "资源 ID",
                "请求头 X-Api-Resource-Id，必须与已开通资源一致。",
                default_value="seed-tts-2.0",
            ),
        ),
    ),
    TTSProviderSpec(
        provider_id="mimo",
        label="小米 MiMo",
        aliases=("mimo", "小米", "小米 mimo"),
        official_docs_url="https://mimo.mi.com/docs/zh-CN/usage-guide/speech-synthesis",
        default_model="mimo-v2.5-tts",
        active_model_parameter_id="tts-mimo-model",
        models=(
            _model(
                "mimo-v2.5-tts",
                "MiMo V2.5 TTS · 预置精品音色",
                features=(
                    TTSFeature.EMOTION,
                    TTSFeature.PARALINGUISTIC,
                    TTSFeature.INSTRUCTION_CONTROL,
                    TTSFeature.STREAMING_AUDIO,
                ),
                system_voices=True,
                recommended=True,
            ),
            _model(
                "mimo-v2.5-tts-voicedesign",
                "MiMo V2.5 Voice Design · 单次直出",
                purposes=("design",),
                features=(TTSFeature.EMOTION, TTSFeature.INSTRUCTION_CONTROL),
                voice_design=True,
                adapter_supported=False,
                notes=("平台不返回可复用 voice_id，当前配音团队契约暂不接入。",),
            ),
            _model(
                "mimo-v2.5-tts-voiceclone",
                "MiMo V2.5 Voice Clone · 单次直出",
                purposes=("clone",),
                features=(TTSFeature.EMOTION, TTSFeature.INSTRUCTION_CONTROL),
                voice_clone=True,
                local_reference_audio=True,
                adapter_supported=False,
                notes=("平台以 Base64 参考音频单次直出，不返回可复用 voice_id。",),
            ),
        ),
        highlights=("自然语言风格控制", "细粒度音频标签", "预置精品音色", "低延迟流式输出"),
        settings=(
            TTSProviderSettingSpec(
                "tts-mimo-api-key", "MiMo API Key", "小米 MiMo 开放平台凭据。", "secret"
            ),
            TTSProviderSettingSpec(
                "tts-mimo-base-url",
                "API 地址",
                "OpenAI 兼容 Chat Completions 音频端点。",
                default_value="https://api.xiaomimimo.com/v1",
            ),
        ),
    ),
    TTSProviderSpec(
        provider_id="local",
        label="本地兼容服务",
        aliases=("local", "本地", "本地兼容服务"),
        official_docs_url="",
        default_model="default",
        active_model_parameter_id="tts-local-model",
        models=(),
        highlights=("OpenAI Speech 兼容协议", "运行时能力自动探测", "自定义模型 ID"),
        is_local=True,
        adapter_status="preview",
        settings=(
            TTSProviderSettingSpec(
                "tts-local-base-url",
                "服务地址",
                "兼容 /v1/audio/speech 的本地或内网服务。",
                default_value="http://localhost:8000/v1",
            ),
            TTSProviderSettingSpec(
                "tts-local-api-key", "API Key", "本地服务不鉴权时留空。", "secret"
            ),
        ),
    ),
    TTSProviderSpec(
        provider_id="qwen3",
        label="Qwen3 TTS",
        aliases=("qwen3", "qwen3 tts"),
        official_docs_url="https://github.com/QwenLM/Qwen3-TTS",
        default_model="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        active_model_parameter_id="tts-qwen3-formal-model",
        models=_QWEN3_MODELS,
        highlights=(
            "CustomVoice 正式/试听分路",
            "自然语言音色设计",
            "授权参考音频克隆",
            "本机隔离 sidecar",
        ),
        is_local=True,
        settings=(
            TTSProviderSettingSpec(
                "tts-qwen3-base-url",
                "Sidecar 地址",
                "独立 Qwen3-TTS 运行时的 OpenAI 兼容地址。",
                default_value="http://127.0.0.1:8011/v1",
            ),
            TTSProviderSettingSpec(
                "tts-qwen3-api-key", "Sidecar Token", "只绑定本机回环地址时可留空。", "secret"
            ),
            TTSProviderSettingSpec(
                "tts-qwen3-preview-model",
                "试听模型",
                "建议使用 0.6B 降低试听等待时间。",
                "select",
                "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
                model_purpose="preview",
            ),
            TTSProviderSettingSpec(
                "tts-qwen3-design-model",
                "声音设计模型",
                "根据自然语言角色描述生成新声线。",
                "select",
                "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
                model_purpose="design",
            ),
            TTSProviderSettingSpec(
                "tts-qwen3-clone-model",
                "声音克隆模型",
                "Base 模型支持参考音频与逐字稿 ICL 克隆。",
                "select",
                "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                model_purpose="clone",
            ),
        ),
    ),
    TTSProviderSpec(
        provider_id="cosyvoice",
        label="CosyVoice",
        aliases=("cosyvoice",),
        official_docs_url="https://github.com/QwenAudio/CosyVoice",
        default_model="FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        active_model_parameter_id="tts-cosyvoice-model",
        models=_COSYVOICE_MODELS,
        highlights=("多语种零样本克隆", "自然语言 Instruct", "发音修补", "双向流式运行时"),
        is_local=True,
        settings=(
            TTSProviderSettingSpec(
                "tts-cosyvoice-base-url",
                "FastAPI 地址",
                "官方 FastAPI 运行时地址，不带 /v1。",
                default_value="http://127.0.0.1:50000",
            ),
            TTSProviderSettingSpec(
                "tts-cosyvoice-mode",
                "推理模式",
                "instruct2 用于导演指令；zero_shot/cross_lingual 用于参考音频克隆。",
                "select",
                "instruct2",
                tuple(_option(item) for item in ("instruct2", "zero_shot", "cross_lingual", "sft")),
            ),
            TTSProviderSettingSpec(
                "tts-cosyvoice-sample-rate",
                "运行时采样率",
                "必须与 sidecar PCM 输出一致。",
                "select",
                "22050",
                tuple(_option(item, f"{item} Hz") for item in ("16000", "22050", "24000", "48000")),
                advanced=True,
            ),
        ),
    ),
    TTSProviderSpec(
        provider_id="openvoice",
        label="OpenVoice V2",
        aliases=("openvoice", "openvoice v2"),
        official_docs_url="https://github.com/myshell-ai/OpenVoice",
        default_model="openvoice-v2",
        active_model_parameter_id="tts-openvoice-model",
        models=(
            _model(
                "openvoice-v2",
                "OpenVoice V2",
                purposes=("formal", "preview", "clone"),
                features=(TTSFeature.SPEED,),
                voice_clone=True,
                system_voices=True,
                local_reference_audio=True,
                recommended=True,
            ),
        ),
        highlights=("即时声纹克隆", "跨语种音色迁移", "MIT 商用许可", "本地推理"),
        is_local=True,
        settings=(
            TTSProviderSettingSpec(
                "tts-openvoice-checkpoint-dir",
                "V2 权重目录",
                "指向官方 checkpoints_v2 目录。",
                "path",
            ),
            TTSProviderSettingSpec(
                "tts-openvoice-device",
                "推理设备",
                "auto 自动选择；也可固定 CPU 或 CUDA。",
                "select",
                "auto",
                (_option("auto", "自动"), _option("cpu", "CPU"), _option("cuda", "CUDA")),
            ),
            TTSProviderSettingSpec(
                "tts-openvoice-language",
                "基础发声语言",
                "音色克隆只迁移声纹，口音与情绪主要由基础 TTS 语言/风格决定。",
                "select",
                "ZH",
                tuple(
                    _option(value, label)
                    for value, label in (
                        ("ZH", "中文"),
                        ("EN_NEWEST", "英语"),
                        ("ES", "西班牙语"),
                        ("FR", "法语"),
                        ("JP", "日语"),
                        ("KR", "韩语"),
                    )
                ),
            ),
        ),
    ),
    TTSProviderSpec(
        provider_id="mock",
        label="Mock",
        aliases=("mock",),
        official_docs_url="",
        default_model="mock-model",
        active_model_parameter_id="",
        models=(_model("mock-model", "Mock Model", system_voices=True),),
        settings=(),
        highlights=("离线流程测试",),
        adapter_status="mock",
    ),
)

_PROVIDER_BY_ID = {provider.provider_id: provider for provider in _PROVIDERS}
_PROVIDER_ALIAS_MAP = {
    alias.lower().strip(): provider.provider_id
    for provider in _PROVIDERS
    for alias in (provider.provider_id, provider.label, *provider.aliases)
}


def tts_provider_catalog() -> tuple[TTSProviderSpec, ...]:
    """Return the immutable built-in provider catalog in UI display order."""

    return _PROVIDERS


def normalize_tts_provider_id(provider: str | TTSProvider) -> str:
    """Resolve stable ids, compatibility aliases, and display labels."""

    raw = provider.value if isinstance(provider, TTSProvider) else str(provider)
    normalized = raw.lower().strip()
    direct = _PROVIDER_ALIAS_MAP.get(normalized)
    if direct is not None:
        return direct
    for alias, provider_id in _PROVIDER_ALIAS_MAP.items():
        if alias and alias in normalized:
            return provider_id
    return normalized


def tts_provider_spec(provider: str | TTSProvider) -> TTSProviderSpec | None:
    """Return one provider spec, accepting legacy aliases such as ``bailian``."""

    return _PROVIDER_BY_ID.get(normalize_tts_provider_id(provider))


def tts_provider_capabilities(
    provider: str | TTSProvider,
    model_id: str = "",
) -> TTSProviderCapabilities:
    """Project catalog capabilities into the adapter-facing domain contract."""

    spec = tts_provider_spec(provider)
    if spec is None:
        try:
            provider_type = TTSProvider(normalize_tts_provider_id(provider))
        except ValueError:
            provider_type = TTSProvider.MOCK
        return TTSProviderCapabilities(provider=provider_type)
    selected = spec.model(model_id) if model_id else spec.model(spec.default_model)
    if selected is None and spec.models:
        selected = spec.models[0]
    provider_type = TTSProvider(spec.provider_id)
    if selected is None:
        return TTSProviderCapabilities(provider=provider_type)
    return TTSProviderCapabilities(
        provider=provider_type,
        voice_clone=selected.voice_clone,
        voice_design=selected.voice_design,
        system_voice_catalog=selected.system_voice_catalog,
        local_reference_audio=selected.local_reference_audio,
        synthesis_features=set(selected.synthesis_features),
    )


__all__ = [
    "TTSModelPurpose",
    "TTSProviderModelSpec",
    "TTSProviderSettingSpec",
    "TTSProviderSpec",
    "TTSSettingKind",
    "TTSSettingOption",
    "normalize_tts_provider_id",
    "tts_provider_capabilities",
    "tts_provider_catalog",
    "tts_provider_spec",
]
