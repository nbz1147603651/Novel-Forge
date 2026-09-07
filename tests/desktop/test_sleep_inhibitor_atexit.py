"""Tests for SleepInhibitor atexit fallback (Task 3 of p0-architecture-fixes)."""

from __future__ import annotations

import weakref
from types import SimpleNamespace

import pytest

from novel_forge.desktop.sleep_inhibitor import SleepInhibitor, _atexit_release, _MacOSBackend


@pytest.fixture
def fake_backend():
    """A no-op backend that avoids real OS calls."""
    return SimpleNamespace(acquire=lambda: None, release=lambda: None)


@pytest.fixture
def inhibitor_with_fake_backend(fake_backend, monkeypatch):
    """SleepInhibitor patched to use a fake backend."""
    monkeypatch.setattr(
        "novel_forge.desktop.sleep_inhibitor._create_backend",
        lambda: fake_backend,
    )
    return SleepInhibitor()


class TestAcquireRegistersAtexit:
    def test_acquire_registers_atexit(
        self, inhibitor_with_fake_backend, monkeypatch
    ):
        """First acquire() calls atexit.register with _atexit_release and weakref.ref(self)."""
        registered_calls = []

        def fake_register(func, *args, **kwargs):
            registered_calls.append((func, args))

        monkeypatch.setattr(
            "novel_forge.desktop.sleep_inhibitor.atexit.register",
            fake_register,
        )

        inhibitor = inhibitor_with_fake_backend
        inhibitor.acquire()

        assert len(registered_calls) == 1
        func, args = registered_calls[0]
        assert func is _atexit_release
        assert len(args) == 1
        # The arg should be a weakref.ref pointing to our inhibitor
        ref = args[0]
        assert isinstance(ref, weakref.ref)
        assert ref() is inhibitor

    def test_atexit_helper_calls_release(self, fake_backend):
        """Manually calling _atexit_release(ref) invokes obj.release()."""
        release_called = []
        fake_backend.release = lambda: release_called.append(True)

        monkeypatch_backend = lambda: fake_backend  # noqa: E731
        import novel_forge.desktop.sleep_inhibitor as mod

        original_create = mod._create_backend
        mod._create_backend = monkeypatch_backend
        try:
            obj = SleepInhibitor()
            obj.acquire()
            ref = weakref.ref(obj)
            _atexit_release(ref)
            assert release_called == [True]
        finally:
            mod._create_backend = original_create

    def test_second_acquire_does_not_reregister(
        self, inhibitor_with_fake_backend, monkeypatch
    ):
        """Second acquire() does NOT call atexit.register again."""
        registered_calls = []

        def fake_register(func, *args, **kwargs):
            registered_calls.append((func, args))

        monkeypatch.setattr(
            "novel_forge.desktop.sleep_inhibitor.atexit.register",
            fake_register,
        )

        inhibitor = inhibitor_with_fake_backend
        inhibitor.acquire()
        # Release so a second acquire can proceed
        inhibitor.release()
        inhibitor.acquire()

        assert len(registered_calls) == 1

    def test_release_exception_swallowed(self):
        """When obj.release() raises, _atexit_release swallows the exception."""

        class RaisingObj:
            def release(self):
                raise RuntimeError("boom")

        obj = RaisingObj()
        ref = weakref.ref(obj)
        # Should not raise
        _atexit_release(ref)


def test_macos_backend_scopes_caffeinate_to_current_process(monkeypatch):
    launched = []

    class FakeProc:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return None

    def fake_popen(command, **kwargs):
        launched.append((command, kwargs))
        return FakeProc()

    monkeypatch.setattr("novel_forge.desktop.sleep_inhibitor.os.getpid", lambda: 12345)
    monkeypatch.setattr("novel_forge.desktop.sleep_inhibitor.subprocess.Popen", fake_popen)

    backend = _MacOSBackend()
    backend.acquire()
    backend.release()

    assert launched
    assert launched[0][0] == ["caffeinate", "-i", "-w", "12345"]
