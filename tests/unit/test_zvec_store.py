"""Tests for the Zvec vector-store adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit._fake_zvec import install_fake_zvec


def test_zvec_store_add_search_filter_remove_clear(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)

    from novel_forge.memory.zvec_store import ZvecVectorStore

    store = ZvecVectorStore(path=str(tmp_path / "zvec"), dimension=3)
    store.add(
        "a",
        [1.0, 0.0, 0.0],
        {"chapter": 1, "scene_index": 0, "characters": ["林远"]},
    )
    store.add(
        "b",
        [0.0, 1.0, 0.0],
        {"chapter": 3, "scene_index": 0, "is_outline": True},
    )

    matches = store.search(
        [1.0, 0.0, 0.0],
        top_k=5,
        filter={"chapter": {"$gte": 1, "$lte": 2}},
    )

    assert [item[0] for item in matches] == ["a"]
    assert matches[0][1] == pytest.approx(1.0)
    assert matches[0][2]["characters"] == ["林远"]
    assert matches[0][2]["content"] == "林远"

    outline_matches = store.search(
        [0.0, 1.0, 0.0],
        top_k=5,
        filter={"is_outline": True},
    )
    assert [item[0] for item in outline_matches] == ["b"]

    store.remove("b")
    assert store.search([0.0, 1.0, 0.0], top_k=5, filter={"is_outline": True}) == []

    store.clear()
    assert store.search([1.0, 0.0, 0.0], top_k=5) == []


def test_zvec_store_rejects_wrong_dimension(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)

    from novel_forge.memory.zvec_store import ZvecVectorStore

    store = ZvecVectorStore(path=str(tmp_path / "zvec"), dimension=2)
    with pytest.raises(ValueError, match="dimension mismatch"):
        store.add("bad", [1.0, 2.0, 3.0], {"chapter": 1})


def test_zvec_store_add_many_uses_batch_upsert(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)

    from novel_forge.memory.zvec_store import ZvecVectorStore

    store = ZvecVectorStore(path=str(tmp_path / "zvec"), dimension=3)
    original_upsert = store._collection.upsert
    batch_flags: list[bool] = []

    def traced_upsert(docs):
        batch_flags.append(isinstance(docs, list))
        return original_upsert(docs)

    monkeypatch.setattr(store._collection, "upsert", traced_upsert)

    store.add_many(
        [
            ("a", [1.0, 0.0, 0.0], {"chapter": 1, "characters": ["林远"]}),
            ("b", [0.0, 1.0, 0.0], {"chapter": 2, "characters": ["许棠"]}),
        ]
    )

    assert batch_flags == [True]
    assert [item[0] for item in store.search([0.0, 1.0, 0.0], top_k=2)] == ["b", "a"]
    assert store._metadata["a"]["characters"] == ["林远"]


def test_zvec_store_uses_zvec_05_query_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    fake_zvec = install_fake_zvec(monkeypatch)
    query_calls: list[str] = []

    original_query = fake_zvec.Query

    class TracedQuery(original_query):
        def __init__(self, field_name: str, vector=None, **kwargs):
            query_calls.append(field_name)
            super().__init__(field_name=field_name, vector=vector, **kwargs)

    fake_zvec.Query = TracedQuery

    from novel_forge.memory.zvec_store import ZvecVectorStore

    store = ZvecVectorStore(path=str(tmp_path / "zvec"), dimension=3)
    store.add("a", [1.0, 0.0, 0.0], {"chapter": 1})

    assert [item[0] for item in store.search([1.0, 0.0, 0.0], top_k=1)] == ["a"]
    assert query_calls == ["embedding"]


def test_zvec_store_requires_zvec_05_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    fake_zvec = install_fake_zvec(monkeypatch)
    delattr(fake_zvec, "Query")

    from novel_forge.memory.zvec_store import ZvecVectorStore

    with pytest.raises(RuntimeError, match="requires zvec>=0.5.0"):
        ZvecVectorStore(path=str(tmp_path / "zvec"), dimension=3)


def test_zvec_store_text_and_hybrid_search_use_content_field(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)

    from novel_forge.memory.zvec_store import ZvecVectorStore

    store = ZvecVectorStore(path=str(tmp_path / "zvec"), dimension=3)
    store.add(
        "a",
        [1.0, 0.0, 0.0],
        {
            "chapter": 1,
            "scene_index": 0,
            "content": "林远 雨夜 找到 铜印",
            "characters": ["林远"],
        },
    )
    store.add(
        "b",
        [0.0, 1.0, 0.0],
        {
            "chapter": 2,
            "scene_index": 0,
            "content": "许棠 追查 旧案",
            "characters": ["许棠"],
        },
    )

    text_matches = store.text_search("林远 铜印", top_k=2)
    assert [item[0] for item in text_matches] == ["a"]

    hybrid_matches = store.hybrid_search(
        [0.0, 1.0, 0.0],
        "林远 铜印",
        top_k=2,
        vector_weight=0.2,
        text_weight=0.8,
    )
    assert hybrid_matches[0][0] == "a"
    assert hybrid_matches[0][2]["content"] == "林远 雨夜 找到 铜印"


def test_zvec_store_accepts_diskann_index_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    fake_zvec = install_fake_zvec(monkeypatch)
    diskann_calls: list[dict] = []

    class TracedDiskAnnIndexParam:
        def __init__(self, **kwargs):
            diskann_calls.append(dict(kwargs))

    fake_zvec.DiskAnnIndexParam = TracedDiskAnnIndexParam

    from novel_forge.memory.zvec_store import ZvecVectorStore

    store = ZvecVectorStore(path=str(tmp_path / "zvec"), dimension=3, index_type="disk-ann")

    assert store.index_type == "diskann"
    assert diskann_calls == [{"metric_type": fake_zvec.MetricType.COSINE}]


def test_zvec_store_quarantines_partial_collection_after_create_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    fake_zvec = install_fake_zvec(monkeypatch)
    original_create = fake_zvec.create_and_open
    calls = 0

    def flaky_create_and_open(path: str, schema):
        nonlocal calls
        calls += 1
        if calls == 1:
            collection_path = Path(path)
            (collection_path / "idmap.0").mkdir(parents=True)
            (collection_path / "LOCK").touch()
            raise RuntimeError(f"create id map failed, path: {collection_path / 'idmap.0'}")
        return original_create(path, schema)

    fake_zvec.create_and_open = flaky_create_and_open

    from novel_forge.memory.zvec_store import ZvecVectorStore

    store = ZvecVectorStore(path=str(tmp_path / "zvec"), dimension=3)
    store.add("a", [1.0, 0.0, 0.0], {"chapter": 1})

    assert calls == 2
    assert [item[0] for item in store.search([1.0, 0.0, 0.0], top_k=1)] == ["a"]
    quarantined = list(tmp_path.glob("zvec.corrupt-*"))
    assert len(quarantined) == 1
    assert (quarantined[0] / "idmap.0").is_dir()


def test_zvec_store_quarantines_existing_corrupt_collection_on_open_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)
    collection_path = tmp_path / "zvec"
    (collection_path / "idmap.0").mkdir(parents=True)
    (collection_path / "LOCK").touch()

    from novel_forge.memory.zvec_store import ZvecVectorStore

    store = ZvecVectorStore(path=str(collection_path), dimension=3)
    store.add("a", [1.0, 0.0, 0.0], {"chapter": 1})

    assert [item[0] for item in store.search([1.0, 0.0, 0.0], top_k=1)] == ["a"]
    quarantined = list(tmp_path.glob("zvec.corrupt-*open_failed*"))
    assert len(quarantined) == 1
    assert (quarantined[0] / "idmap.0").is_dir()


def test_zvec_store_reset_existing_quarantines_without_opening_old_collection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    fake_zvec = install_fake_zvec(monkeypatch)

    from novel_forge.memory.zvec_store import ZvecVectorStore

    collection_path = tmp_path / "zvec"
    old_store = ZvecVectorStore(path=str(collection_path), dimension=4)
    old_store.add("old", [1.0, 0.0, 0.0, 0.0], {"chapter": 1, "content": "old"})

    open_calls = 0

    def fail_if_opened(_path: str):
        nonlocal open_calls
        open_calls += 1
        raise AssertionError("reset rebuild should not open the old collection")

    fake_zvec.open = fail_if_opened

    new_store = ZvecVectorStore(
        path=str(collection_path),
        dimension=3,
        reset_existing=True,
    )
    new_store.add("new", [1.0, 0.0, 0.0], {"chapter": 2, "content": "new"})

    assert open_calls == 0
    assert [item[0] for item in new_store.search([1.0, 0.0, 0.0], top_k=1)] == ["new"]
    assert len(list(tmp_path.glob("zvec.corrupt-*rebuild*"))) == 1


def test_zvec_store_does_not_quarantine_locked_collection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    fake_zvec = install_fake_zvec(monkeypatch)
    collection_path = tmp_path / "zvec"
    collection_path.mkdir()

    def locked_open(_path: str):
        raise RuntimeError("Can't lock read-write collection")

    fake_zvec.open = locked_open

    from novel_forge.memory.zvec_store import ZvecVectorStore

    with pytest.raises(RuntimeError, match="locked or already open"):
        ZvecVectorStore(path=str(collection_path), dimension=3)

    assert collection_path.exists()
    assert not list(tmp_path.glob("zvec.corrupt-*"))
