"""Multi-candidate voice A/B preview planning, generation and confirmation.

对齐 Reference/audiobook 的 ``preview_data.json`` 结构（voiceId / voiceName /
description / sampleText / speed / volume / candidates）：为每个角色从系统
音色目录挑选 3 个候选，用同一段试听文本批量合成，作者试听后确认选定音色并
持久化到配音团队（对应参考的 feedback 确认环节）。

分层约定（对齐"确定性优先"哲学）：
- ``build_preview_plan`` 是确定性过滤：语言 → 性别 → 年龄硬过滤，性格软匹配
- ``generate_candidate_previews`` 只做合成，不改选角
- ``confirm_preview`` 是作者确认的唯一写入口，选中后供正式合成消费
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, cast

from pydantic import BaseModel, Field

from novel_forge.core.config import Settings
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.gateway.base import TTSProviderAdapter
from novel_forge.tts.gateway.factory import TTSAdapterRegistry, resolve_tts_model
from novel_forge.tts.runtime.performance_policy import voice_performance_profile
from novel_forge.tts.schemas import (
    TTSProvider,
    TTSRequest,
    TTSResponse,
    VoiceCloneStatus,
    VoicePerformanceOverrides,
    VoiceTeamContract,
)

_log = get_logger("tts.services.voice_preview")

# 每角色候选数量（对齐参考 preview_plan：3 候选）。
DEFAULT_CANDIDATE_COUNT = 3
# 系统音色目录拉取上限（覆盖 MiniMax 300+ 系统音色）。
_CATALOG_LIMIT = 500
# 试听文本目标时长（秒）；用于生成默认试听台词。
SAMPLE_TEXT_TARGET_SECONDS = 10

_PREVIEW_AUDIO_FORMATS = {"mp3", "wav", "flac", "pcm", "ogg", "m4a"}

# 性格软匹配关键词表：目录 personality 描述命中任一关键词即加分。
_PERSONALITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "warm": ("warm", "温暖", "柔和", "温柔", "soft", "gentle"),
    "energetic": ("energetic", "活力", "活泼", "俏皮", "playful", "cheerful"),
    "calm": ("calm", "沉稳", "冷静", "serene", "steady", "深沉"),
    "stern": ("stern", "严厉", "严肃", "冷峻", "authoritative", "mature"),
    "youthful": ("youth", "young", "少年", "少女", "青涩", "teen"),
    "elderly": ("elder", "old", "年长", "苍老", "沉稳年长"),
    "mysterious": ("mysterious", "神秘", "空灵", "飘渺", "ethereal"),
}


class VoicePreviewCandidate(BaseModel):
    """单个候选音色条目（字段对齐参考 preview_data.json 的 candidates）。"""

    voice_id: str = Field(min_length=1, description="TTS 平台音色 ID")
    voice_name: str = Field(description="音色展示名")
    description: str = Field(default="", description="音色描述（目录标签）")
    sample_path: str = Field(default="", description="该候选的试听音频路径（生成后填充）")
    gender: str = Field(default="neutral", description="目录推断性别")
    age_hint: str = Field(default="", description="目录推断年龄段")
    personality: str = Field(default="", description="目录推断性格")
    match_reasons: list[str] = Field(default_factory=list, description="选中该候选的依据")
    error: str = Field(default="", description="该候选试听生成失败时的错误信息")


class VoicePreviewPlan(BaseModel):
    """一个角色的多候选 A/B 预览计划（对齐 preview_data.json 的角色条目）。"""

    character_id: str = Field(description="角色 ID（写回 voice team 的主键）")
    character_name: str = Field(description="角色名")
    label: str = Field(default="", description="角色 label（小写下划线）")
    description: str = Field(default="", description="角色画像描述")
    voice_id: str = Field(default="", description="当前选定音色（默认 candidates[0]）")
    sample_text: str = Field(default="", description="全部候选共用的试听文本（约 10 秒）")
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="试听语速倍率")
    volume: float = Field(default=1.0, ge=0.0, le=2.0, description="试听音量倍率")
    candidates: list[VoicePreviewCandidate] = Field(default_factory=list)


# ─── 角色画像提取 ────────────────────────────────────────────────────────────


def _character_profile(character: dict[str, Any]) -> dict[str, str]:
    """从角色 dict 提取预览规划所需的画像字段（缺省宽容）。"""
    return {
        "character_id": str(character.get("character_id") or character.get("id") or "").strip(),
        "character_name": str(character.get("name") or character.get("character_name") or "").strip(),
        "gender": str(character.get("gender") or "").strip().lower(),
        "age_hint": str(character.get("age_hint") or character.get("age") or "").strip().lower(),
        "personality": str(
            character.get("personality")
            or character.get("personality_tags")
            or character.get("traits")
            or ""
        ).strip().lower(),
        "description": str(character.get("description") or character.get("summary") or "").strip(),
    }


def _personality_score(candidate: dict[str, Any], profile: dict[str, str]) -> int:
    """性格软匹配：候选性格描述与角色画像命中的关键词组交集大小。

    以关键词组为单位计数（同一组内多词命中只算一次），避免角色画像中的
    共享词汇对全部候选同额加分而稀释区分度。
    """
    candidate_text = " ".join(
        [
            str(candidate.get("personality") or ""),
            str(candidate.get("voice_description") or ""),
            str(candidate.get("name") or ""),
        ]
    ).lower()
    profile_text = f"{profile['personality']} {profile['description']}".lower()

    def _groups(text: str) -> set[str]:
        return {
            group
            for group, keywords in _PERSONALITY_KEYWORDS.items()
            if any(keyword in text for keyword in keywords)
        }

    return len(_groups(candidate_text) & _groups(profile_text))


def _age_conflicts(candidate: dict[str, Any], profile: dict[str, str]) -> bool:
    """年龄软冲突：候选与角色年龄画像明显矛盾时排后。"""
    role_age = profile["age_hint"]
    candidate_age = str(candidate.get("age_hint") or "").lower()
    if not role_age or not candidate_age:
        return False
    child_roles = any(token in role_age for token in ("少年", "少女", "小孩", "儿童", "teen", "kid", "child"))
    if child_roles and any(token in candidate_age for token in ("老年", "中老年", "elder", "senior")):
        return True
    elder_roles = any(token in role_age for token in ("老年", "老者", "elder", "senior"))
    if elder_roles and any(token in candidate_age for token in ("少年", "儿童", "teen", "child")):
        return True
    return False


def _sample_text_for_character(character_name: str) -> str:
    """生成约 10 秒的默认试听文本（角色可替换占位）。"""
    return (
        f"{character_name}。黄昏时分，我站在老槐树下，"
        "风从巷口吹来，带着炊烟和远山的味道。"
        "有些事情，总要走到这一步，才明白当初的犹豫都是多余的。"
    )


# ─── 预览计划构建（确定性过滤） ──────────────────────────────────────────────


async def _resolve_provider(settings: Settings, provider: str = "") -> TTSProvider:
    if provider:
        try:
            return TTSProvider(provider.strip().lower())
        except ValueError:
            pass
    default = str(settings.tts_default_provider).strip().lower()
    try:
        return TTSProvider(default)
    except ValueError:
        return TTSProvider.MINIMAX


async def _ensure_managed_runtime(settings: Settings, provider: TTSProvider) -> None:
    """Qwen3 本地 sidecar 按需启动（best-effort，失败由 provider 预检兜底）。"""
    if provider != TTSProvider.QWEN3:
        return
    try:
        from novel_forge.tts.model_center.service import AudioModelCenterService

        state = await AudioModelCenterService(settings).ensure_runtime_ready("qwen3-tts")
        if state and state.environment_path:
            _log.info("Managed Qwen3-TTS runtime ready: %s", state.environment_path)
    except Exception as exc:  # pragma: no cover - best-effort path
        _log.warning("Unable to start managed Qwen3-TTS runtime on demand: %s", exc)


async def build_preview_plan(
    *,
    layout: ProjectLayout,
    settings: Settings,
    characters: list[dict[str, Any]],
    provider: str = "",
    sample_text: str = "",
    candidate_count: int = DEFAULT_CANDIDATE_COUNT,
    language: str = "zh",
) -> list[VoicePreviewPlan]:
    """为每个角色构建多候选预览计划。

    过滤顺序（确定性优先）：
    1. 语言硬过滤：``list_system_voices(language=...)`` 直接排除冲突语种
    2. 性别硬过滤：目录 gender 与角色性别不一致的排除
    3. 年龄软过滤：年龄画像明显冲突的候选排后
    4. 性格软匹配：目录性格与角色性格关键词命中数排序
    """
    provider_enum = await _resolve_provider(settings, provider)
    await _ensure_managed_runtime(settings, provider_enum)
    adapter = TTSAdapterRegistry.get_instance(settings).get_adapter(provider_enum)
    catalog = await adapter.list_system_voices(
        gender=None,
        language=language,
        limit=_CATALOG_LIMIT,
    )

    plans: list[VoicePreviewPlan] = []
    for character in characters:
        profile = _character_profile(character)
        character_id = profile["character_id"]
        character_name = profile["character_name"]
        if not character_id:
            continue
        label = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in character_name.lower())

        pool = list(catalog)
        if profile["gender"] in {"male", "female"}:
            gender_pool = [v for v in pool if v.get("gender") == profile["gender"]]
            if gender_pool:
                pool = gender_pool

        def _rank(
            candidate: dict[str, Any], profile: dict[str, str] = profile
        ) -> tuple[int, int]:
            """(-性格命中数, 年龄冲突) 升序 → 性格优先、冲突排后。"""
            return (
                -_personality_score(candidate, profile),
                1 if _age_conflicts(candidate, profile) else 0,
            )

        ranked = sorted(pool, key=_rank)
        selected = ranked[:candidate_count]

        candidates: list[VoicePreviewCandidate] = []
        for voice in selected:
            reasons: list[str] = []
            if _personality_score(voice, profile) > 0:
                reasons.append("性格画像匹配")
            if profile["gender"] in {"male", "female"} and voice.get("gender") == profile["gender"]:
                reasons.append(f"性别匹配（{profile['gender']}）")
            if not reasons:
                reasons.append("目录排序")
            candidates.append(
                VoicePreviewCandidate(
                    voice_id=str(voice["voice_id"]),
                    voice_name=str(voice.get("name") or voice["voice_id"]),
                    description=str(voice.get("voice_description") or ""),
                    gender=str(voice.get("gender") or "neutral"),
                    age_hint=str(voice.get("age_hint") or ""),
                    personality=str(voice.get("personality") or ""),
                    match_reasons=reasons,
                )
            )

        text = sample_text.strip() or _sample_text_for_character(character_name or "角色")
        plans.append(
            VoicePreviewPlan(
                character_id=character_id,
                character_name=character_name,
                label=label,
                description=profile["description"],
                voice_id=candidates[0].voice_id if candidates else "",
                sample_text=text,
                speed=float(settings.tts_default_speed),
                volume=1.0,
                candidates=candidates,
            )
        )
    return plans


# ─── 候选试听生成 ────────────────────────────────────────────────────────────


def _preview_format(settings: Settings, response_format: str = "") -> str:
    """Normalize a provider's preview format to a supported file extension."""
    audio_format = str(response_format or settings.tts_output_format).strip().lower()
    return audio_format if audio_format in _PREVIEW_AUDIO_FORMATS else "mp3"


def _candidate_cache_path(
    layout: ProjectLayout,
    *,
    character_id: str,
    voice_id: str,
    fingerprint: str,
    audio_format: str,
) -> Path:
    """预览试听缓存路径：preview_{character}_{voice}_{fingerprint}.{fmt}。"""
    def _safe(value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)

    return (
        layout.tts_audio_dir(0)
        / f"preview_{_safe(character_id)}_{_safe(voice_id)}_{fingerprint}.{audio_format}"
    )


def _write_preview_audio(preview_path: Path, audio_data: bytes) -> None:
    """Persist an audition clip in a form Qt's player can decode.

    部分供应商（如 MiniMax）会在音频头部附带大段 AIGC 水印 ID3 标签，Qt 的
    FFmpeg 探测无法越过（表现为 InvalidMedia、试听无声）。用 FFmpeg 重封装
    保证缓存文件始终可播；FFmpeg 不可用时退化为原始写入。
    """
    import shutil
    import subprocess

    preview_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(suffix=preview_path.suffix, delete=False) as tmp:
            tmp.write(audio_data)
            tmp_path = Path(tmp.name)
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise FileNotFoundError("ffmpeg not available")
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-probesize",
                "96M",
                "-analyzeduration",
                "96M",
                "-i",
                str(tmp_path),
                "-y",
                "-acodec",
                "copy",
                str(preview_path),
            ],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0 or not preview_path.is_file():
            raise RuntimeError(result.stderr.decode(errors="replace")[:300])
    except Exception as exc:
        _log.warning("FFmpeg remux failed (%s); writing raw preview audio", exc)
        preview_path.write_bytes(audio_data)
    finally:
        if "tmp_path" in locals():
            tmp_path.unlink(missing_ok=True)


def _preview_fingerprint(*, voice_id: str, text: str, speed: float, volume: float, provider: str, model_id: str) -> str:
    """稳定指纹：音色/文本/参数任一变化都会生成新试听。"""
    raw = "|".join([voice_id, text, f"{speed:.3f}", f"{volume:.3f}", provider, model_id])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


async def _synthesize_candidate(
    adapter: TTSProviderAdapter,
    request: TTSRequest,
    *,
    streaming: bool,
    on_chunk: Callable[[bytes], Awaitable[None] | None] | None,
) -> TTSResponse:
    """流式优先合成；包装层已透传 synthesize_streaming，否则回退普通合成。"""
    if streaming:
        streaming_method = getattr(adapter, "synthesize_streaming", None)
        if callable(streaming_method):
            return cast(
                TTSResponse,
                await streaming_method(request, on_chunk=on_chunk),
            )
    return await adapter.synthesize(request)


async def generate_candidate_previews(
    *,
    layout: ProjectLayout,
    settings: Settings,
    plan: VoicePreviewPlan,
    provider: str = "",
    streaming: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
    on_chunk: Callable[[bytes], Awaitable[None] | None] | None = None,
) -> VoicePreviewPlan:
    """合成一个角色全部候选的试听音频（同角色同文本），填充 sample_path。

    Args:
        streaming: True 时优先走 WebSocket 流式合成（边生成边播放）；
            当前仅 MiniMax 原生支持，其他平台自动回退普通合成。
    """
    provider_enum = await _resolve_provider(settings, provider)
    await _ensure_managed_runtime(settings, provider_enum)
    adapter = TTSAdapterRegistry.get_instance(settings).get_adapter(provider_enum)
    model_id = resolve_tts_model(settings, provider_enum, purpose="preview")
    text = plan.sample_text.strip() or _sample_text_for_character(plan.character_name)

    updated_candidates: list[VoicePreviewCandidate] = []
    total = len(plan.candidates)
    for index, candidate in enumerate(plan.candidates, start=1):
        if on_progress is not None:
            on_progress(index - 1, total)
        fingerprint = _preview_fingerprint(
            voice_id=candidate.voice_id,
            text=text,
            speed=plan.speed,
            volume=plan.volume,
            provider=provider_enum.value,
            model_id=model_id,
        )
        cached = sorted(
            layout.tts_audio_dir(0).glob(
                f"preview_*_{candidate.voice_id}_{fingerprint}.*"
            )
        )
        cached = [path for path in cached if path.is_file()]
        if cached:
            updated_candidates.append(
                candidate.model_copy(update={"sample_path": str(cached[0])})
            )
            continue

        request = TTSRequest(
            text=text,
            voice_id=candidate.voice_id,
            model_id=model_id,
            speed=plan.speed,
            volume=plan.volume,
            output_format=settings.tts_output_format,
            sample_rate=settings.tts_sample_rate,
            provider=provider_enum,
            metadata={
                "resource_priority": "interactive",
                "resource_label": f"{plan.character_name} 音色候选试听",
                "speaker_kind": "角色",
                "character_name": plan.character_name,
            },
        )
        error = ""
        try:
            response = await _synthesize_candidate(
                adapter, request, streaming=streaming, on_chunk=on_chunk
            )
            if not response.audio_data:
                raise RuntimeError("Provider returned empty audio")
            preview_format = _preview_format(settings, response.audio_format)
            preview_path = _candidate_cache_path(
                layout,
                character_id=plan.character_id,
                voice_id=candidate.voice_id,
                fingerprint=fingerprint,
                audio_format=preview_format,
            )
            _write_preview_audio(preview_path, response.audio_data)
        except Exception as exc:
            _log.warning(
                "Candidate preview failed for %s/%s: %s",
                plan.character_name,
                candidate.voice_name,
                exc,
            )
            error = str(exc).strip() or type(exc).__name__
        updated_candidates.append(
            candidate.model_copy(
                update={
                    "sample_path": str(preview_path) if not error and "preview_path" in locals() else "",
                    "error": error,
                }
            )
        )
    if on_progress is not None:
        on_progress(total, total)
    return plan.model_copy(update={"candidates": updated_candidates})


# ─── 确认选定（写回配音团队） ────────────────────────────────────────────────


def _load_voice_team(layout: ProjectLayout) -> VoiceTeamContract | None:
    if not layout.tts_voice_team_path.exists():
        return None
    try:
        return VoiceTeamContract.model_validate_json(
            layout.tts_voice_team_path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        _log.warning("Invalid voice team %s: %s", layout.tts_voice_team_path, exc)
        return None


def confirm_preview(
    *,
    layout: ProjectLayout,
    settings: Settings,
    character_id: str,
    voice_id: str,
    speed: float = 1.0,
    volume: float = 1.0,
    provider: str = "",
    model_id: str = "",
    sample_text: str = "",
    sample_path: str = "",
) -> dict[str, Any]:
    """持久化作者选中的候选音色（对应参考的 feedback 确认环节）。

    更新 voice team 条目：voice_id / provider / model_id / voice_source=system /
    clone_status=READY / approval_status=approved（A/B 试听后作者主动确认）/
    speed、volume 写入 performance_profile.manual_overrides（供正式合成消费）。
    """
    team = _load_voice_team(layout)
    if team is None:
        raise ValueError("当前项目还没有配音团队，请先构建配音团队")
    entry = team.get_entry(character_id)
    if entry is None:
        raise ValueError(f"角色 {character_id} 不在配音团队中")
    provider_enum = entry.provider
    if provider:
        provider_enum = _resolve_provider_sync(settings, provider)

    profile = voice_performance_profile(entry)
    resolved_speed = max(0.5, min(2.0, speed))
    resolved_volume = max(0.0, min(2.0, volume))
    default_speed = float(getattr(settings, "tts_default_speed", 1.0) or 1.0)
    speed_offset = round(resolved_speed - default_speed, 4)
    vol_offset = round(resolved_volume - 1.0, 4)
    new_profile = profile.model_copy(
        update={
            "manual_overrides": VoicePerformanceOverrides(
                speed_offset=speed_offset,
                pitch_offset=profile.manual_overrides.pitch_offset,
                vol_offset=vol_offset,
            ),
            "derivation_source": "manual",
            "derivation_reasons": ["用户在音色 A/B 对比中试听确认"],
        }
    )

    updates: dict[str, Any] = {
        "voice_id": voice_id,
        "provider": provider_enum,
        "model_id": model_id or entry.model_id,
        "voice_source": "system",
        "clone_status": VoiceCloneStatus.READY,
        "approval_status": "approved",
        "expires_at": None,
        "activation_deadline": None,
        "performance_profile": new_profile,
    }
    if sample_path or sample_text:
        entry_with_variant = entry.model_copy(update=updates)
        entry_with_variant = entry_with_variant.with_variant_preview(
            "identity",
            audio_path=sample_path,
            text=sample_text,
        )
        updates = dict(updates)
        updates.update(
            {
                "preview_variants": entry_with_variant.preview_variants,
                "preview_audio_path": entry_with_variant.preview_audio_path,
                "preview_text": entry_with_variant.preview_text,
            }
        )
    updated = entry.model_copy(update=updates)
    team.entries = [
        updated if item.character_id == character_id else item for item in team.entries
    ]
    # 任何 entry 变更都会使整队确认失效（防止静默复用旧团队）。
    team.confirmed = False
    team.confirmed_at = None
    atomic_write_json(layout.tts_voice_team_path, team.model_dump(mode="json"))
    _log.info(
        "Voice preview confirmed: %s -> %s (speed=%s, volume=%s)",
        character_id,
        voice_id,
        resolved_speed,
        resolved_volume,
    )
    return team.model_dump(mode="json")


def _resolve_provider_sync(settings: Settings, provider: str) -> TTSProvider:
    try:
        return TTSProvider(provider.strip().lower())
    except ValueError:
        default = str(settings.tts_default_provider).strip().lower()
        try:
            return TTSProvider(default)
        except ValueError:
            return TTSProvider.MINIMAX
