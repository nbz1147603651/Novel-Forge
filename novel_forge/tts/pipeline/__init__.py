"""TTS Pipeline package — dubbing workflow stages."""

from __future__ import annotations

from novel_forge.tts.pipeline.assemble_audio_step import (
    AssembleAudioInput,
    AssembleAudioStep,
)
from novel_forge.tts.pipeline.build_narrator_profile_step import (
    BuildNarratorProfileInput,
    BuildNarratorProfileStep,
)
from novel_forge.tts.pipeline.build_voice_team_step import (
    BuildVoiceTeamInput,
    BuildVoiceTeamStep,
)
from novel_forge.tts.pipeline.generate_script_step import (
    GenerateDubbingScriptInput,
    GenerateDubbingScriptStep,
)
from novel_forge.tts.pipeline.ssml_builder import (
    build_minimax_emotion_text,
    build_ssml,
    should_use_ssml,
)

# ``synthesis_pool`` is a legacy scheduling implementation superseded by the
# semaphore + pacer + MiniMax wave-pool path inside ``SynthesizeAudioStep``.
# It stays importable (and its tests stay green) as a historical reference but
# is intentionally NOT re-exported here so new callers use the active path.
from novel_forge.tts.pipeline.synthesize_audio_step import (
    SynthesizeAudioInput,
    SynthesizeAudioStep,
)
from novel_forge.tts.pipeline.timeline_builder import (
    PlaybackTimeline,
    TimelineEntry,
    build_timeline,
)

__all__ = [
    "AssembleAudioInput",
    "AssembleAudioStep",
    "BuildNarratorProfileInput",
    "BuildNarratorProfileStep",
    "BuildVoiceTeamInput",
    "BuildVoiceTeamStep",
    "GenerateDubbingScriptInput",
    "GenerateDubbingScriptStep",
    "PlaybackTimeline",
    "SynthesizeAudioInput",
    "SynthesizeAudioStep",
    "TimelineEntry",
    "build_minimax_emotion_text",
    "build_ssml",
    "build_timeline",
    "should_use_ssml",
]
