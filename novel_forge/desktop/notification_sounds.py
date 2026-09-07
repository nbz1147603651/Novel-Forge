"""Desktop task notification sound helpers.

Optimized synthesis engine with harmonic overtones, ADSR envelope,
exponential decay, and debounce protection against batch-task spam.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Literal

from novel_forge.core.config import get_settings

try:  # pragma: no cover - depends on optional desktop extra.
    from PySide6.QtWidgets import QApplication

    _QApplication: Any = QApplication
except Exception:  # pragma: no cover
    _QApplication = None


NotificationEvent = Literal["success", "decision", "failure"]

NOTIFICATION_SOUND_CHOICES: tuple[tuple[str, str], ...] = (
    ("mute", "静音"),
    ("system", "系统默认"),
    ("soft", "轻提示"),
    ("chime", "清脆双响"),
    ("bell", "完成铃"),
    ("alert", "警示三连"),
)
_SOUND_CHOICE_KEYS = frozenset(key for key, _label in NOTIFICATION_SOUND_CHOICES)

_EVENT_SOUND_ATTRS: dict[NotificationEvent, str] = {
    "success": "desktop_notification_success_sound",
    "decision": "desktop_notification_decision_sound",
    "failure": "desktop_notification_failure_sound",
}
_EVENT_SOUND_DEFAULTS: dict[NotificationEvent, str] = {
    "success": "chime",
    "decision": "bell",
    "failure": "alert",
}

# ---------------------------------------------------------------------------
# Synthesis constants
# ---------------------------------------------------------------------------
_SAMPLE_RATE = 44100
_AMPLITUDE = 0.38  # slightly louder for better audibility
_CACHE_VERSION = 2  # bump to invalidate old cached WAVs
_DEBOUNCE_INTERVAL_S = 1.8  # minimum seconds between consecutive plays

# ADSR envelope parameters (fractions of note duration)
_ATTACK_FRAC = 0.06
_DECAY_FRAC = 0.12
_SUSTAIN_LEVEL = 0.65
_RELEASE_FRAC = 0.22

# Harmonic partials: (multiplier, amplitude_weight) — gives warmth vs pure sine
_HARMONICS: tuple[tuple[float, float], ...] = (
    (1.0, 1.0),   # fundamental
    (2.0, 0.38),  # octave
    (3.0, 0.14),  # fifth above octave
    (4.0, 0.06),  # double octave
)

# ---------------------------------------------------------------------------
# Tone specifications — each note is (frequency_hz, duration_ms)
# frequency <= 0 means silence/rest
# ---------------------------------------------------------------------------
_TONE_SPECS: dict[str, tuple[tuple[int, int], ...]] = {
    # Gentle ascending major-third, short and unobtrusive
    "soft": ((523, 100), (0, 30), (659, 130)),
    # Bright ascending perfect-fifth with shimmer
    "chime": ((784, 110), (0, 40), (1175, 180)),
    # Warm bell: root + octave resolution
    "bell": ((698, 160), (0, 35), (1047, 260)),
    # Attention: descending minor-third pattern, three pulses
    "alert": ((440, 130), (0, 40), (349, 130), (0, 40), (294, 200)),
}

_LINUX_PLAYERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pw-play", ()),
    ("paplay", ()),
    ("aplay", ()),
    ("canberra-gtk-play", ("-f",)),
)
_preview_player: "DesktopNotificationSoundPlayer | None" = None
_last_play_time: float = 0.0


def normalize_notification_sound_key(value: object, *, default: str = "chime") -> str:
    """Return a supported sound key, falling back to *default*."""

    key = str(value or "").strip().lower()
    if key in _SOUND_CHOICE_KEYS:
        return key
    if default in _SOUND_CHOICE_KEYS:
        return default
    return "chime"


def notification_event_for_job(job: Any) -> NotificationEvent | None:
    """Classify a desktop job terminal state into a sound notification event."""

    status = getattr(job, "status", "")
    status_value = getattr(status, "value", status)
    if status_value == "succeeded":
        return "success"
    if status_value == "paused":
        return "decision"
    if status_value == "failed" and str(getattr(job, "error", "") or "") != "用户已取消":
        return "failure"
    return None


def sound_key_for_event(settings: Any, event: NotificationEvent) -> str:
    """Resolve the configured sound key for *event* from a settings object."""

    attr = _EVENT_SOUND_ATTRS[event]
    default = _EVENT_SOUND_DEFAULTS[event]
    return normalize_notification_sound_key(getattr(settings, attr, default), default=default)


def preview_notification_sound(sound_key: str) -> None:
    """Play a single configured sound key for settings-page previews."""

    global _preview_player
    if _preview_player is None:
        _preview_player = DesktopNotificationSoundPlayer()
    _preview_player.play_sound(sound_key, _bypass_debounce=True)


class DesktopNotificationSoundPlayer:
    """Native-backed player for short generated notification tones.

    Features:
    - Harmonic-rich synthesis with ADSR envelope
    - Debounce protection (skips plays within _DEBOUNCE_INTERVAL_S)
    - Versioned WAV cache (auto-regenerates on engine upgrade)
    """

    def __init__(self, parent: object | None = None) -> None:
        _ = parent

    def reload_settings(self) -> None:
        """Compatibility hook for callers after settings are saved."""

        return

    def play_for_job(self, job: Any) -> None:
        """Play the configured notification sound for a terminal job state."""

        settings = get_settings()
        if not bool(getattr(settings, "desktop_notification_sound_enabled", True)):
            return
        event = notification_event_for_job(job)
        if event is None:
            return
        self.play_sound(sound_key_for_event(settings, event))

    def play_sound(self, sound_key: str, *, _bypass_debounce: bool = False) -> None:
        """Play one supported sound key with debounce protection."""

        global _last_play_time
        key = normalize_notification_sound_key(sound_key, default="system")
        if key == "mute":
            return
        # Debounce: skip if played too recently (prevents batch-task spam)
        now = time.monotonic()
        if not _bypass_debounce and (now - _last_play_time) < _DEBOUNCE_INTERVAL_S:
            return
        _last_play_time = now

        if key == "system":
            self._beep()
            return
        path = _ensure_sound_file(key)
        if path is None:
            self._beep()
            return
        if not _play_native_sound(path):
            self._beep()

    @staticmethod
    def _beep() -> None:
        if _QApplication is None:
            return
        try:
            _QApplication.beep()
        except Exception:
            return


def _ensure_sound_file(sound_key: str) -> Path | None:
    spec = _TONE_SPECS.get(sound_key)
    if spec is None:
        return None
    sound_dir = Path(tempfile.gettempdir()) / f"novel_forge_notification_sounds_v{_CACHE_VERSION}"
    path = sound_dir / f"{sound_key}.wav"
    if path.exists():
        return path
    try:
        sound_dir.mkdir(parents=True, exist_ok=True)
        _write_tone_sequence(path, spec)
    except OSError:
        return None
    return path


def _play_native_sound(path: Path) -> bool:
    if sys.platform == "darwin":
        return _spawn_sound_player(("/usr/bin/afplay", str(path)))
    if sys.platform == "win32":
        return _play_windows_sound(path)
    if sys.platform.startswith("linux"):
        return _play_linux_sound(path)
    return False


def _play_windows_sound(path: Path) -> bool:
    try:  # pragma: no cover - Windows-only stdlib module.
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        return False
    return True


def _play_linux_sound(path: Path) -> bool:
    for player, args in _LINUX_PLAYERS:
        executable = shutil.which(player)
        if executable is None:
            continue
        if _spawn_sound_player((executable, *args, str(path))):
            return True
    return False


def _spawn_sound_player(command: tuple[str, ...]) -> bool:
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------------
# Synthesis engine — harmonic-rich tones with ADSR envelope
# ---------------------------------------------------------------------------


def _adsr_envelope(index: int, total: int) -> float:
    """Compute ADSR envelope value for sample *index* out of *total* samples."""

    attack_end = int(total * _ATTACK_FRAC)
    decay_end = attack_end + int(total * _DECAY_FRAC)
    release_start = total - int(total * _RELEASE_FRAC)

    if index < attack_end:
        # Attack: linear ramp 0→1
        return index / max(1, attack_end)
    if index < decay_end:
        # Decay: 1→sustain level
        progress = (index - attack_end) / max(1, decay_end - attack_end)
        return 1.0 - progress * (1.0 - _SUSTAIN_LEVEL)
    if index < release_start:
        # Sustain: hold at sustain level
        return _SUSTAIN_LEVEL
    # Release: sustain→0 with smooth curve
    progress = (index - release_start) / max(1, total - release_start)
    return _SUSTAIN_LEVEL * (1.0 - progress) ** 1.5


def _render_note(frequency: int, duration_ms: int) -> bytearray:
    """Render a single note with harmonics and ADSR envelope."""

    total = max(1, int(_SAMPLE_RATE * duration_ms / 1000))
    samples = bytearray(total * 2)  # 16-bit mono

    if frequency <= 0:
        return samples  # silence

    for index in range(total):
        envelope = _adsr_envelope(index, total)
        # Sum harmonic partials for richer timbre
        value = 0.0
        for multiplier, weight in _HARMONICS:
            partial_freq = frequency * multiplier
            if partial_freq > _SAMPLE_RATE / 2:  # Nyquist guard
                break
            value += math.sin(2.0 * math.pi * partial_freq * index / _SAMPLE_RATE) * weight
        # Normalize by total harmonic weight to avoid clipping
        value /= 1.58  # sum of weights ≈ 1.58
        value *= envelope
        samples[index * 2 : index * 2 + 2] = struct.pack(
            "<h", int(value * _AMPLITUDE * 32767)
        )
    return samples


def _write_tone_sequence(path: Path, spec: tuple[tuple[int, int], ...]) -> None:
    """Write a complete notification tone (sequence of notes) to a WAV file."""

    segments: list[bytearray] = [_render_note(freq, dur) for freq, dur in spec]
    all_samples = b"".join(bytes(seg) for seg in segments)

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(_SAMPLE_RATE)
        wav.writeframes(all_samples)
