"""Project-local benchmark runner for audio plugin recommendations."""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.core.local_model_resources import (
    LocalMemoryClass,
    LocalResourceLease,
    LocalResourcePriority,
    LocalResourceRequest,
    configure_local_model_resources,
)
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.tts.pipeline.align_speech_timeline_step import AlignSpeechTimelineStep
from novel_forge.tts.platform.hardware import detect_audio_hardware
from novel_forge.tts.platform.registry import AudioPluginRegistry
from novel_forge.tts.platform.schemas import (
    AudioBenchmarkReport,
    AudioBenchmarkSample,
    AudioCapability,
    ModelScorecard,
)
from novel_forge.tts.platform.sidecar import AudioSidecarClient
from novel_forge.tts.schemas import ChapterAudioResult, DubbingScript, SynthesisStatus


def benchmark_samples_from_chapter(
    script: DubbingScript,
    audio_result: ChapterAudioResult,
    *,
    limit: int = 30,
) -> list[AudioBenchmarkSample]:
    segments = {segment.segment_index: segment for segment in script.segments}
    samples: list[AudioBenchmarkSample] = []
    for result in audio_result.segment_results:
        segment = segments.get(result.segment_index)
        if (
            segment is None
            or result.status != SynthesisStatus.COMPLETED
            or not Path(result.audio_path).is_file()
        ):
            continue
        samples.append(
            AudioBenchmarkSample(
                sample_id=f"segment-{segment.segment_index}",
                audio_path=result.audio_path,
                expected_text=segment.text,
                language=segment.language_code,
            )
        )
        if len(samples) >= max(1, min(limit, 30)):
            break
    return samples


def _client_for_manifest(settings: Settings, manifest: Any) -> AudioSidecarClient:
    endpoint_setting = manifest.runtime.endpoint_setting
    base_url = str(getattr(settings, endpoint_setting, "") or "").strip()
    if not endpoint_setting or not base_url:
        raise RuntimeError("未配置 sidecar 地址")
    token = settings.audio_qwen3_asr_api_key if manifest.provider_id == "qwen3-asr" else ""
    return AudioSidecarClient(
        base_url,
        api_key=token,
        timeout_s=180,
        connect_timeout_s=1,
    )


async def run_audio_benchmark(
    *,
    settings: Settings,
    samples: list[AudioBenchmarkSample],
    registry: AudioPluginRegistry,
    plugin_ids: list[str] | None = None,
) -> AudioBenchmarkReport:
    """Benchmark ASR/alignment candidates on a user's own synthesized speech."""

    if not samples:
        return AudioBenchmarkReport(sample_count=0, failures={"samples": "没有可用人声样本"})
    candidates = [
        manifest
        for manifest in registry.all()
        if manifest.supplies(AudioCapability.ASR)
        or manifest.supplies(AudioCapability.FORCED_ALIGNMENT)
    ]
    if plugin_ids:
        selected = set(plugin_ids)
        candidates = [item for item in candidates if item.plugin_id in selected]
    scorecards: list[ModelScorecard] = []
    failures: dict[str, str] = {}
    broker = configure_local_model_resources(settings)
    hardware = detect_audio_hardware(accelerator_preference=settings.audio_accelerator_preference)
    for manifest in candidates:
        client: AudioSidecarClient | None = None
        resource_lease: LocalResourceLease | None = None
        latencies: list[float] = []
        quality_values: list[float] = []
        error_rates: list[float] = []
        peak_memory_mb: float | None = None
        try:
            if not manifest.runtime.is_cloud:
                accelerator = any(
                    item == hardware.accelerator for item in manifest.runtime.accelerators
                ) and hardware.accelerator in {"cuda", "mps"}
                resource_lease = await broker.acquire(
                    LocalResourceRequest(
                        workload="audio_benchmark",
                        label=f"{manifest.display_name} · 本机校准",
                        memory_class=LocalMemoryClass(manifest.runtime.memory_class.value),
                        accelerator=accelerator,
                        cpu_heavy=not accelerator,
                        priority=LocalResourcePriority.MAINTENANCE,
                        timeout_s=float(settings.local_model_resource_wait_timeout_s),
                    )
                )
            client = _client_for_manifest(settings, manifest)
            for sample in samples:
                started = time.monotonic()
                if manifest.supplies(AudioCapability.FORCED_ALIGNMENT):
                    payload = await client.align(
                        sample.audio_path,
                        text=sample.expected_text,
                        language=sample.language,
                    )
                    raw_items = (
                        payload.get("items") or payload.get("words") or payload.get("tokens") or []
                    )
                    aligned_text = "".join(
                        str(item.get("text") or item.get("word") or item.get("token") or "")
                        for item in raw_items
                        if isinstance(item, dict)
                    )
                    error = AlignSpeechTimelineStep._text_error_rate(
                        sample.expected_text,
                        aligned_text,
                    )
                else:
                    payload = await client.transcribe(
                        sample.audio_path,
                        language=sample.language,
                        expected_text=sample.expected_text,
                    )
                    error = AlignSpeechTimelineStep._text_error_rate(
                        sample.expected_text,
                        str(payload.get("text") or ""),
                    )
                latencies.append((time.monotonic() - started) * 1000)
                error_rates.append(error)
                quality_values.append(max(0.0, 1.0 - error))
            try:
                diagnostics = await client.diagnostics()
                memory_raw = diagnostics.get("peak_memory_mb")
                peak_memory_mb = float(memory_raw) if memory_raw is not None else None
            except Exception:
                pass
            scorecards.append(
                ModelScorecard(
                    plugin_id=manifest.plugin_id,
                    device_signature=(
                        f"{settings.audio_accelerator_preference}:{settings.audio_memory_budget}"
                    ),
                    languages=list(dict.fromkeys(sample.language for sample in samples)),
                    sample_count=len(samples),
                    quality_score=statistics.fmean(quality_values),
                    median_latency_ms=statistics.median(latencies),
                    peak_memory_mb=peak_memory_mb,
                    character_error_rate=statistics.fmean(error_rates),
                )
            )
        except Exception as exc:
            failures[manifest.plugin_id] = str(exc)[:500]
        finally:
            if client is not None:
                await client.aclose()
            if resource_lease is not None:
                resource_lease.release()
    return AudioBenchmarkReport(
        sample_count=len(samples),
        scorecards=scorecards,
        failures=failures,
    )


def save_audio_benchmark_report(path: str | Path, report: AudioBenchmarkReport) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, report.model_dump(mode="json"))
