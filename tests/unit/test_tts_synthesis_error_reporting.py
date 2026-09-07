"""Regression coverage for preflight failures surfaced by Voice Studio."""

from __future__ import annotations

from novel_forge.desktop.pages.voice_studio.workers import _synthesis_failure_payload


def test_audio_preflight_failure_is_not_reported_as_resumable_segment_failure() -> None:
    payload = _synthesis_failure_payload(
        {
            "error": "音频生产预检未通过，请先修复鉴权、路由、隐私或本地资源问题。",
            "error_code": "audio_preflight_failed",
            "checks": [
                {
                    "status": "failed",
                    "stage": "tts_formal",
                    "plugin_id": "minimax-tts-formal",
                    "message": "云端路由缺少凭据。",
                },
                {
                    "status": "failed",
                    "stage": "soundscape",
                    "plugin_id": "stable-audio-small-sfx",
                    "message": "本地模型尚未安装或文件不完整。",
                },
            ],
        },
        step="synthesize",
    )

    assert payload["summary"] == "合成尚未开始：音频生产预检未通过"
    assert "没有可续跑的片段" in payload["detail"]
    assert "正式人声合成 / minimax-tts-formal：云端路由缺少凭据。" in payload["detail"]
    assert (
        "环境声生成 / stable-audio-small-sfx：本地模型尚未安装或文件不完整。" in payload["detail"]
    )
    assert "failed_segments" not in payload


def test_enriched_failed_checks_grouped_by_severity() -> None:
    """When the backend provides severity/guidance, hard and soft failures are separated."""
    payload = _synthesis_failure_payload(
        {
            "error_code": "audio_preflight_failed",
            "failed_checks": [
                {
                    "status": "failed",
                    "stage": "tts_formal",
                    "plugin_id": "minimax-tts-formal",
                    "message": "正式 TTS 路由不可用。",
                    "severity": "hard",
                    "guidance": "正式人声合成路由不可用；请检查 TTS provider 配置与网络。",
                },
                {
                    "status": "failed",
                    "stage": "asr",
                    "plugin_id": "whisperx",
                    "message": "对齐 sidecar 离线。",
                    "severity": "soft",
                    "guidance": "对齐 sidecar 离线，时间线将降级为片段近似；如需精确对齐，请在「本机音频模型中心」启动。",
                },
            ],
        },
        step="synthesize",
    )

    detail = payload["detail"]
    # Hard failures come first under the blocking header.
    assert "阻断性预检项" in detail
    assert "正式人声合成 / minimax-tts-formal：正式 TTS 路由不可用。" in detail
    assert "请检查 TTS provider 配置与网络" in detail
    # Soft failures appear under the non-blocking header.
    assert "非阻断性预检项" in detail
    assert "语音识别校验 / whisperx：对齐 sidecar 离线。" in detail
    assert "时间线将降级为片段近似" in detail
    assert "failed_segments" not in payload
