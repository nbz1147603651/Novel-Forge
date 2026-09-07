"""Tests for filesystem project path behavior."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread

import pytest

from novel_forge.core.config import Settings
from novel_forge.desktop.files import ProjectFileService
from novel_forge.persistence.factory import create_storage_backend
from novel_forge.persistence.filesystem import CachedFileSystemStorage, FileSystemStorage


def test_project_path_does_not_create_directory(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)

    path = storage.project_path("missing_project")

    assert path == tmp_path / "missing_project"
    assert not path.exists()


@pytest.mark.parametrize("project_id", ["../escape", "nested/project", "nested\\project", ".", ""])
def test_project_path_rejects_path_syntax(tmp_path, project_id: str) -> None:
    storage = FileSystemStorage(tmp_path)

    with pytest.raises(ValueError):
        storage.project_path(project_id)


def test_project_file_service_rejects_delete_escape(tmp_path) -> None:
    service = ProjectFileService(tmp_path / "data")
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError):
        service.delete_project("../outside")

    assert outside.exists()


def test_existing_project_dir_requires_real_directory(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)

    with pytest.raises(FileNotFoundError):
        storage.existing_project_dir("missing_project")


def test_save_text_and_json_are_persisted(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    text_path = tmp_path / "demo" / "chapter.md"
    json_path = tmp_path / "demo" / "meta.json"

    storage.save_text(text_path, "正文")
    storage.save_json(json_path, {"ok": True})

    assert storage.load_text(text_path) == "正文"
    assert storage.load_json(json_path) == {"ok": True}


def test_project_lock_creates_lock_file(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)

    with storage.project_lock("demo_project"):
        assert (tmp_path / ".locks" / "demo_project.lock").exists()


def test_project_lock_is_reentrant_in_process(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)

    with storage.project_lock("demo_project"):
        with storage.project_lock("demo_project"):
            with storage.project_shared_lock("demo_project"):
                assert (tmp_path / ".locks" / "demo_project.lock").exists()


def test_project_lock_does_not_treat_another_thread_as_reentrant(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    acquired = Event()

    def acquire_from_worker() -> None:
        with storage.project_lock("demo_project"):
            acquired.set()

    with storage.project_lock("demo_project"):
        worker = Thread(target=acquire_from_worker)
        worker.start()
        assert not acquired.wait(timeout=0.05)

    worker.join(timeout=1.0)
    assert acquired.is_set()
    assert not worker.is_alive()


def test_cached_storage_reuses_json_until_file_changes(tmp_path) -> None:
    # cache_ttl=0 disables TTL so stat-based invalidation is tested directly.
    storage = CachedFileSystemStorage(tmp_path, max_entries=4, cache_ttl=0)
    json_path = tmp_path / "demo" / "meta.json"
    storage.save_json(json_path, {"step": 1})

    first = storage.load_json(json_path)
    first["step"] = 99

    second = storage.load_json(json_path)
    assert second == {"step": 1}

    json_path.write_text(json.dumps({"step": 2}, ensure_ascii=False), encoding="utf-8")
    third = storage.load_json(json_path)
    assert third == {"step": 2}


def test_cached_storage_tracks_text_updates(tmp_path) -> None:
    # cache_ttl=0 disables TTL so stat-based invalidation is tested directly.
    storage = CachedFileSystemStorage(tmp_path, max_entries=2, cache_ttl=0)
    text_path = tmp_path / "demo" / "chapter.md"
    storage.save_text(text_path, "第一稿")

    assert storage.load_text(text_path) == "第一稿"

    text_path.write_text("第二稿", encoding="utf-8")

    assert storage.load_text(text_path) == "第二稿"


def test_cached_storage_allows_concurrent_reads(tmp_path) -> None:
    storage = CachedFileSystemStorage(tmp_path, max_entries=4)
    paths = []
    for idx in range(8):
        path = tmp_path / "demo" / f"meta_{idx}.json"
        storage.save_json(path, {"idx": idx})
        paths.append(path)

    def load_round(path_index: int) -> int:
        path = paths[path_index % len(paths)]
        return int(storage.load_json(path)["idx"])

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(load_round, range(160)))

    assert set(results) == set(range(8))
    assert storage.cache_stats()["json_entries"] <= 4


def test_storage_factory_honors_cache_settings(tmp_path) -> None:
    cached = create_storage_backend(
        Settings(
            _env_file=None,
            storage_root=tmp_path / "cached",
            storage_cache_enabled=True,
            storage_cache_max_entries=32,
        )
    )
    plain = create_storage_backend(
        Settings(
            _env_file=None,
            storage_root=tmp_path / "plain",
            storage_cache_enabled=False,
        )
    )

    assert isinstance(cached, CachedFileSystemStorage)
    assert cached.root == tmp_path / "cached"
    assert cached.cache_stats()["max_entries"] == 32
    assert type(plain) is FileSystemStorage
