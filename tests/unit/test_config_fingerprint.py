"""Tests for Settings singleton thread-safety and env fingerprint behavior."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from novel_forge.core.config import get_settings, reset_settings
from novel_forge.core.infra.settings_loader import UnifiedSettingsLoader


class TestSettingsFingerprint:
    def setup_method(self) -> None:
        reset_settings()

    def teardown_method(self) -> None:
        reset_settings()

    def test_env_change_returns_new_instance(self) -> None:
        settings1 = get_settings()
        assert settings1 is not None

        old_value = os.environ.get("NOVEL_FORGE_STORAGE_ROOT")
        try:
            os.environ["NOVEL_FORGE_STORAGE_ROOT"] = "/tmp/test_novel_forge_env_change"
            settings2 = get_settings()
            assert settings2 is not settings1
        finally:
            if old_value is None:
                os.environ.pop("NOVEL_FORGE_STORAGE_ROOT", None)
            else:
                os.environ["NOVEL_FORGE_STORAGE_ROOT"] = old_value

    def test_concurrent_access_thread_safe(self) -> None:
        results: list[object] = []
        lock = threading.Lock()

        def fetch_settings() -> None:
            settings = get_settings()
            with lock:
                results.append(settings)

        num_threads = 10
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(fetch_settings) for _ in range(num_threads)]
            for f in futures:
                f.result()

        assert len(set(id(r) for r in results)) == 1

    def test_reset_settings_rebuilds_on_next_call(self) -> None:
        settings1 = get_settings()
        assert settings1 is not None

        reset_settings()

        settings2 = get_settings()
        assert settings2 is not settings1

    def test_dotenv_change_returns_new_instance(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        dotenv = tmp_path / ".env"
        profiles = tmp_path / "model_profiles.json"
        dotenv.write_text(
            f"NOVEL_FORGE_STORAGE_ROOT={tmp_path / 'first'}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            UnifiedSettingsLoader,
            "_auto_dotenv_path",
            staticmethod(lambda: dotenv),
        )
        monkeypatch.setattr(
            UnifiedSettingsLoader,
            "_auto_profiles_path",
            staticmethod(lambda: profiles),
        )
        monkeypatch.delenv("NOVEL_FORGE_STORAGE_ROOT", raising=False)

        settings1 = get_settings()
        assert settings1.storage_root == tmp_path / "first"

        dotenv.write_text(
            f"NOVEL_FORGE_STORAGE_ROOT={tmp_path / 'second'}\n",
            encoding="utf-8",
        )

        settings2 = get_settings()
        assert settings2 is not settings1
        assert settings2.storage_root == tmp_path / "second"
