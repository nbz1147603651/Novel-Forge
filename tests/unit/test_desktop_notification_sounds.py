from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_forge.desktop import notification_sounds as sounds
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.notification_sounds import (
    DesktopNotificationSoundPlayer,
    normalize_notification_sound_key,
    notification_event_for_job,
    sound_key_for_event,
)


@pytest.fixture(autouse=True)
def _reset_debounce(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset debounce timer so tests are not affected by previous plays."""
    monkeypatch.setattr(sounds, "_last_play_time", 0.0)


def _job(status: DesktopJobState, *, error: str = "") -> DesktopJobRecord:
    return DesktopJobRecord(job_id="job-1", kind="run_short", label="短篇创作", status=status, error=error)


def test_notification_event_for_terminal_job_states() -> None:
    assert notification_event_for_job(_job(DesktopJobState.SUCCEEDED)) == "success"
    assert notification_event_for_job(_job(DesktopJobState.PAUSED)) == "decision"
    assert notification_event_for_job(_job(DesktopJobState.FAILED, error="模型调用失败")) == "failure"


def test_notification_event_ignores_cancelled_and_active_jobs() -> None:
    assert notification_event_for_job(_job(DesktopJobState.FAILED, error="用户已取消")) is None
    assert notification_event_for_job(_job(DesktopJobState.RUNNING)) is None
    assert notification_event_for_job(_job(DesktopJobState.QUEUED)) is None


def test_sound_key_resolution_falls_back_per_event() -> None:
    settings = SimpleNamespace(
        desktop_notification_success_sound="soft",
        desktop_notification_decision_sound="missing",
        desktop_notification_failure_sound="alert",
    )

    assert sound_key_for_event(settings, "success") == "soft"
    assert sound_key_for_event(settings, "decision") == "bell"
    assert sound_key_for_event(settings, "failure") == "alert"
    assert normalize_notification_sound_key("unknown", default="system") == "system"


def test_notification_sound_module_does_not_reference_qt_audio_backend() -> None:
    source = Path(sounds.__file__).read_text(encoding="utf-8")
    forbidden = (
        "Qt" + "Multimedia",
        "Q" + "SoundEffect",
        "PySide6.Qt" + "Core",
        "Q" + "Object",
    )

    for token in forbidden:
        assert token not in source


def test_custom_sound_uses_native_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "soft.wav"
    played: list[Path] = []
    beeped: list[bool] = []

    monkeypatch.setattr(sounds, "_ensure_sound_file", lambda _key: path)
    monkeypatch.setattr(sounds, "_play_native_sound", lambda value: played.append(value) or True)
    monkeypatch.setattr(
        DesktopNotificationSoundPlayer,
        "_beep",
        staticmethod(lambda: beeped.append(True)),
    )

    DesktopNotificationSoundPlayer().play_sound("soft")

    assert played == [path]
    assert beeped == []


def test_system_sound_uses_beep_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    played: list[Path] = []
    beeped: list[bool] = []

    monkeypatch.setattr(sounds, "_ensure_sound_file", lambda _key: tmp_path / "system.wav")
    monkeypatch.setattr(sounds, "_play_native_sound", lambda value: played.append(value) or True)
    monkeypatch.setattr(
        DesktopNotificationSoundPlayer,
        "_beep",
        staticmethod(lambda: beeped.append(True)),
    )

    DesktopNotificationSoundPlayer().play_sound("system")

    assert played == []
    assert beeped == [True]


def test_mute_sound_does_not_play_or_beep(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    played: list[Path] = []
    beeped: list[bool] = []

    monkeypatch.setattr(sounds, "_ensure_sound_file", lambda _key: tmp_path / "mute.wav")
    monkeypatch.setattr(sounds, "_play_native_sound", lambda value: played.append(value) or True)
    monkeypatch.setattr(
        DesktopNotificationSoundPlayer,
        "_beep",
        staticmethod(lambda: beeped.append(True)),
    )

    DesktopNotificationSoundPlayer().play_sound("mute")

    assert played == []
    assert beeped == []


def test_native_sound_failure_falls_back_to_beep(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "alert.wav"
    beeped: list[bool] = []

    monkeypatch.setattr(sounds, "_ensure_sound_file", lambda _key: path)
    monkeypatch.setattr(sounds, "_play_native_sound", lambda _path: False)
    monkeypatch.setattr(
        DesktopNotificationSoundPlayer,
        "_beep",
        staticmethod(lambda: beeped.append(True)),
    )

    DesktopNotificationSoundPlayer().play_sound("alert")

    assert beeped == [True]


def test_macos_native_sound_spawns_afplay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "chime.wav"
    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr(sounds.sys, "platform", "darwin")
    monkeypatch.setattr(sounds, "_spawn_sound_player", lambda command: commands.append(command) or True)

    assert sounds._play_native_sound(path) is True
    assert commands == [("/usr/bin/afplay", str(path))]


def test_linux_native_sound_uses_first_available_player(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "bell.wav"
    commands: list[tuple[str, ...]] = []

    def fake_which(command: str) -> str | None:
        return "/usr/bin/paplay" if command == "paplay" else None

    monkeypatch.setattr(sounds.sys, "platform", "linux")
    monkeypatch.setattr(sounds.shutil, "which", fake_which)
    monkeypatch.setattr(sounds, "_spawn_sound_player", lambda command: commands.append(command) or True)

    assert sounds._play_native_sound(path) is True
    assert commands == [("/usr/bin/paplay", str(path))]


def test_windows_native_sound_uses_winsound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "alert.wav"
    calls: list[tuple[str, int]] = []
    fake_winsound = SimpleNamespace(
        SND_FILENAME=1,
        SND_ASYNC=2,
        PlaySound=lambda filename, flags: calls.append((filename, flags)),
    )

    monkeypatch.setattr(sounds.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winsound", fake_winsound)

    assert sounds._play_native_sound(path) is True
    assert calls == [(str(path), 3)]


def test_debounce_skips_rapid_consecutive_plays(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Second play within debounce interval is silently skipped."""
    played: list[Path] = []
    path = tmp_path / "soft.wav"

    monkeypatch.setattr(sounds, "_ensure_sound_file", lambda _key: path)
    monkeypatch.setattr(sounds, "_play_native_sound", lambda value: played.append(value) or True)
    monkeypatch.setattr(
        DesktopNotificationSoundPlayer,
        "_beep",
        staticmethod(lambda: None),
    )

    player = DesktopNotificationSoundPlayer()
    player.play_sound("soft")
    player.play_sound("soft")  # within debounce interval → skipped

    assert len(played) == 1


def test_debounce_bypass_for_preview(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Preview bypasses debounce so settings page '试听' always works."""
    played: list[Path] = []
    path = tmp_path / "chime.wav"

    monkeypatch.setattr(sounds, "_ensure_sound_file", lambda _key: path)
    monkeypatch.setattr(sounds, "_play_native_sound", lambda value: played.append(value) or True)

    player = DesktopNotificationSoundPlayer()
    player.play_sound("chime", _bypass_debounce=True)
    player.play_sound("chime", _bypass_debounce=True)

    assert len(played) == 2


def test_cache_uses_versioned_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache directory includes version suffix for invalidation."""
    monkeypatch.setattr(sounds, "_CACHE_VERSION", 99)
    result = sounds._ensure_sound_file("nonexistent_key")
    assert result is None  # unknown key still returns None

    # Verify the path pattern for a valid key
    import tempfile

    expected_dir = Path(tempfile.gettempdir()) / "novel_forge_notification_sounds_v99"
    monkeypatch.setattr(sounds, "_write_tone_sequence", lambda _p, _s: None)
    result = sounds._ensure_sound_file("soft")
    assert result is not None
    assert result.parent == expected_dir


def test_adsr_envelope_boundaries() -> None:
    """ADSR envelope starts at 0, peaks near 1, and ends at 0."""
    total = 1000
    start = sounds._adsr_envelope(0, total)
    peak = sounds._adsr_envelope(int(total * sounds._ATTACK_FRAC), total)
    end = sounds._adsr_envelope(total - 1, total)

    assert start == 0.0
    assert 0.9 <= peak <= 1.0
    assert end < 0.05  # near zero at release end


def test_render_note_silence_for_zero_frequency() -> None:
    """Zero frequency produces all-zero samples (silence)."""
    samples = sounds._render_note(0, 100)
    assert all(b == 0 for b in samples)


def test_render_note_produces_nonzero_audio() -> None:
    """A real frequency produces non-silent audio data."""
    samples = sounds._render_note(440, 100)
    assert len(samples) > 0
    assert any(b != 0 for b in samples)
