"""Managed audio model catalogue independent from inference implementations."""

from __future__ import annotations

from novel_forge.tts.model_center.schemas import AudioModelDescriptor, AudioModelSource
from novel_forge.tts.sound_generation.models.catalog import SOUND_MODEL_CATALOG


def _stable_audio_descriptors() -> tuple[AudioModelDescriptor, ...]:
    """Project the canonical sound catalogue into installable model records."""
    descriptors: list[AudioModelDescriptor] = []
    for model_id in ("small-sfx", "small-music"):
        sound_model = SOUND_MODEL_CATALOG[model_id]
        descriptors.append(
            AudioModelDescriptor(
                plugin_id=f"stable-audio-3-{model_id}",
                model_id=sound_model.model_id,
                display_name=sound_model.display_name,
                family="Stable Audio",
                roles=[kind.value for kind in sorted(sound_model.supported_kinds, key=str)],
                source=AudioModelSource.STABLE_AUDIO,
                repository_id=sound_model.repository_id,
                estimated_download_bytes=sound_model.estimated_download_bytes,
                license_name=sound_model.license_summary,
                requires_license_acceptance=sound_model.requires_access_approval,
                runtime_package="stable-audio-3",
                recommended_for=(
                    "本地短音效与环境声生成"
                    if model_id == "small-sfx"
                    else "本地纯伴奏与章节主题音乐生成"
                ),
            )
        )
    return tuple(descriptors)


def audio_model_catalog() -> tuple[AudioModelDescriptor, ...]:
    hf = AudioModelSource.HUGGINGFACE
    return (
        AudioModelDescriptor(
            plugin_id="qwen3-tts-0.6b-customvoice",
            model_id="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
            display_name="Qwen3-TTS 0.6B CustomVoice",
            family="Qwen3-TTS",
            roles=["快速试听"],
            source=hf,
            repository_id="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
            required_files=["config.json", "*.safetensors"],
            estimated_download_bytes=1_800_000_000,
            license_name="Apache-2.0 / 模型卡条款",
            endpoint_setting="tts_qwen3_base_url",
            runtime_package="qwen-tts",
            runtime_id="qwen3-tts",
            minimum_runtime_version="1",
            recommended_for="低延迟角色与旁白试听",
        ),
        AudioModelDescriptor(
            plugin_id="qwen3-tts-1.7b-customvoice",
            model_id="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
            display_name="Qwen3-TTS 1.7B CustomVoice",
            family="Qwen3-TTS",
            roles=["正式人声"],
            source=hf,
            repository_id="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
            required_files=["config.json", "*.safetensors"],
            estimated_download_bytes=4_000_000_000,
            license_name="Apache-2.0 / 模型卡条款",
            endpoint_setting="tts_qwen3_base_url",
            runtime_package="qwen-tts",
            runtime_id="qwen3-tts",
            minimum_runtime_version="1",
            recommended_for="正式章节旁白与对白",
        ),
        AudioModelDescriptor(
            plugin_id="qwen3-tts-1.7b-voice-design",
            model_id="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
            display_name="Qwen3-TTS 1.7B VoiceDesign",
            family="Qwen3-TTS",
            roles=["音色设计"],
            source=hf,
            repository_id="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
            required_files=["config.json", "*.safetensors"],
            estimated_download_bytes=4_000_000_000,
            license_name="Apache-2.0 / 模型卡条款",
            endpoint_setting="tts_qwen3_base_url",
            runtime_package="qwen-tts",
            runtime_id="qwen3-tts",
            minimum_runtime_version="1",
            recommended_for="品牌音色与角色原型设计",
        ),
        AudioModelDescriptor(
            plugin_id="qwen3-tts-1.7b-base",
            model_id="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
            display_name="Qwen3-TTS 1.7B Base",
            family="Qwen3-TTS",
            roles=["授权克隆"],
            source=hf,
            repository_id="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
            required_files=["config.json", "*.safetensors"],
            estimated_download_bytes=4_000_000_000,
            license_name="Apache-2.0 / 模型卡条款",
            endpoint_setting="tts_qwen3_base_url",
            runtime_package="qwen-tts",
            runtime_id="qwen3-tts",
            minimum_runtime_version="1",
            recommended_for="经授权的长期角色音色克隆",
        ),
        AudioModelDescriptor(
            plugin_id="qwen3-asr-0.6b",
            model_id="Qwen/Qwen3-ASR-0.6B",
            display_name="Qwen3-ASR 0.6B",
            family="Qwen3-ASR",
            roles=["转写核验"],
            source=hf,
            repository_id="Qwen/Qwen3-ASR-0.6B",
            required_files=["config.json", "*.safetensors"],
            estimated_download_bytes=1_800_000_000,
            license_name="Apache-2.0 / 模型卡条款",
            endpoint_setting="audio_qwen3_asr_base_url",
            runtime_package="qwen-asr",
            runtime_id="qwen3-asr",
            minimum_runtime_version="1",
            recommended_for="正式人声转写核验",
        ),
        AudioModelDescriptor(
            plugin_id="qwen3-forced-aligner-0.6b",
            model_id="Qwen/Qwen3-ForcedAligner-0.6B",
            display_name="Qwen3 ForcedAligner 0.6B",
            family="Qwen3-ASR",
            roles=["真实时间线"],
            source=hf,
            repository_id="Qwen/Qwen3-ForcedAligner-0.6B",
            required_files=["config.json", "*.safetensors"],
            estimated_download_bytes=1_800_000_000,
            license_name="Apache-2.0 / 模型卡条款",
            endpoint_setting="audio_qwen3_asr_base_url",
            runtime_package="qwen-asr",
            runtime_id="qwen3-asr",
            minimum_runtime_version="1",
            recommended_for="已知文本词/字级强制对齐",
        ),
        AudioModelDescriptor(
            plugin_id="whisperx",
            model_id="large-v2",
            display_name="WhisperX · large-v2 + 语言对齐包",
            family="WhisperX",
            roles=["多语种复核"],
            source=AudioModelSource.SIDECAR,
            estimated_download_bytes=3_200_000_000,
            license_name="BSD-2-Clause + 上游模型许可",
            endpoint_setting="audio_whisperx_base_url",
            runtime_package="whisperx",
            runtime_id="whisperx",
            minimum_runtime_version="1",
            recommended_for="多语言词级时间戳与独立复核",
        ),
        AudioModelDescriptor(
            plugin_id="sherpa-sensevoice-int8",
            model_id="sensevoice-zh-en-ja-ko-yue-int8",
            display_name="Sherpa ONNX · SenseVoice Int8",
            family="Sherpa ONNX",
            roles=["轻量 ASR", "VAD"],
            source=AudioModelSource.RELEASE_ARCHIVE,
            download_url=(
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"
            ),
            required_files=["model.int8.onnx", "tokens.txt"],
            estimated_download_bytes=260_000_000,
            license_name="Apache-2.0 / 模型包 LICENSE",
            endpoint_setting="audio_sherpa_base_url",
            runtime_package="sherpa-onnx",
            runtime_id="sherpa-onnx",
            minimum_runtime_version="1",
            recommended_for="CPU 快速试听与中英日韩粤识别",
        ),
        AudioModelDescriptor(
            plugin_id="silero-vad-onnx",
            model_id="silero-vad.onnx",
            display_name="Silero VAD · ONNX",
            family="Sherpa ONNX",
            roles=["轻量 VAD"],
            source=AudioModelSource.DIRECT_FILE,
            download_url=(
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
            ),
            required_files=["silero_vad.onnx"],
            estimated_download_bytes=3_000_000,
            license_name="MIT / 上游模型许可",
            endpoint_setting="audio_sherpa_base_url",
            runtime_package="sherpa-onnx",
            runtime_id="sherpa-onnx",
            minimum_runtime_version="1",
            recommended_for="低资源停顿与语音活动检测",
        ),
        *_stable_audio_descriptors(),
    )
