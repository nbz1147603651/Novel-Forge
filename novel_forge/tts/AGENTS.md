# Novel Forge — TTS Module

Text-to-speech & voice cloning. Decoupled from the writing pipeline: the only
inbound dependency is the single projection `extract_tts_metadata()` →
`ChapterTTSMetadata` produced at chapter Finalize. TTS never imports from
`pipeline/` or `story_kernel/`.

## Subpackage Layout

| Subpackage | Contents | Know |
|-----------|----------|------|
| `sidecars/` | `whisperx_sidecar`, `qwen3_sidecar`, `qwen3_asr_sidecar`, `audio_analysis_sidecar`, `sherpa_onnx_sidecar` | External engine wrappers. **String-referenced** by `model_center/runtimes.py` `entry_module` — update those literals when moving/renaming. |
| `assets/` | `voice_library`, `voice_matching`, `voice_semantic_index`, `sound_library`, `reference_voice_store` | Voice/sound asset stores and matching. |
| `runtime/` | `audio_runtime`, `budget`, `cleanup`, `storage_limits`, `performance_policy` | Execution runtime, quotas, retention. |
| `services/` | `studio_service`, `automation`, `delivery` | User-facing services (VoiceStudio backend, automation modes, delivery readiness). |
| `pipeline/` | 13 step modules (script → voice team → synthesize → assemble → timeline) | The dubbing production pipeline. |
| `gateway/` | TTS provider adapters + factory | String-keyed adapter registry in `gateway/factory.py`. |
| `platform/` | Audio platform plugin schemas/config | Plugin platform abstraction. |
| `model_center/` | Model download/runtime management | `runtimes.py` holds `entry_module` dotted-path strings. |
| top level | `schemas.py` (ChapterTTSMetadata etc.), `script_review`, `script_integrity`, `audio_quality`, `emotion_inference`, `creative_direction` | Shared schemas and script-stage analysis. |

## Conventions

- New sidecar-like engine wrappers go to `sidecars/`; register the dotted path
  in `model_center/runtimes.py`.
- New asset stores go to `assets/`; new quota/retention logic to `runtime/`.
- Keep the writing-pipeline boundary one-way: consume `ChapterTTSMetadata`,
  never reach back into `pipeline/long` or `story_kernel`.
