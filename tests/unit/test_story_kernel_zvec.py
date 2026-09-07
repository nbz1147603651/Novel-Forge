"""Tests for StoryKernelZVec — optional semantic enhancement layer."""

from __future__ import annotations

import math

from tests.unit._fake_zvec import install_fake_zvec

# ---------------------------------------------------------------------------
# Helper: deterministic mock embedding
# ---------------------------------------------------------------------------

def _mock_embed(text: str, dimension: int = 16) -> list[float]:
    """Character n-gram embedding for testing — overlapping text → similar vectors."""
    vec = [0.0] * dimension
    for ch in text:
        idx = ord(ch) % dimension
        vec[idx] += 1.0
    # Normalize to unit vector
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm > 0 else vec


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStoryKernelZVecDisabled:
    """When ZVec is disabled, all methods return empty."""

    def test_disabled_search_entities_returns_empty(self, monkeypatch, tmp_path):
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
            enabled=False,
        )
        results = layer.search_entities("test query", top_k=5)
        assert results == []

    def test_disabled_search_knowledge_returns_empty(self, monkeypatch, tmp_path):
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
            enabled=False,
        )
        results = layer.search_knowledge("test query", top_k=5)
        assert results == []

    def test_disabled_search_motifs_returns_empty(self, monkeypatch, tmp_path):
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
            enabled=False,
        )
        results = layer.search_motifs("test text")
        assert results == []

    def test_disabled_index_kernel_is_noop(self, monkeypatch, tmp_path):
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.schemas import StoryKernel
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
            enabled=False,
        )
        kernel = StoryKernel(project_id="test")
        # Should not raise
        layer.index_kernel(kernel)


class TestStoryKernelZVecEntitySearch:
    """Test entity indexing and semantic search."""

    def _make_kernel_with_entities(self):
        from novel_forge.story_kernel.schemas import Entity, StoryKernel

        return StoryKernel(
            project_id="test",
            entities=[
                Entity(
                    entity_id="char_001",
                    name="林远",
                    entity_type="character",
                    aliases=["小林"],
                    notes="主角，记忆回收师",
                    source_chapter=1,
                ),
                Entity(
                    entity_id="loc_001",
                    name="幽影城",
                    entity_type="location",
                    notes="位于迷雾山脉深处的古城",
                    source_chapter=3,
                ),
                Entity(
                    entity_id="item_001",
                    name="时光之钥",
                    entity_type="item",
                    notes="打开记忆之门的钥匙",
                    source_chapter=5,
                ),
            ],
        )

    def test_index_and_search_entities(self, monkeypatch, tmp_path):
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
        )
        kernel = self._make_kernel_with_entities()
        layer.index_kernel(kernel)

        # Search for character-related text
        results = layer.search_entities("记忆回收师", top_k=3)
        assert len(results) > 0
        # The character entity should be in results
        entity_ids = [e.entity_id for e in results]
        assert "char_001" in entity_ids

    def test_search_entities_respects_top_k(self, monkeypatch, tmp_path):
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
        )
        kernel = self._make_kernel_with_entities()
        layer.index_kernel(kernel)

        results = layer.search_entities("entity", top_k=1)
        assert len(results) <= 1

    def test_search_entities_returns_empty_on_empty_kernel(self, monkeypatch, tmp_path):
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.schemas import StoryKernel
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
        )
        layer.index_kernel(StoryKernel(project_id="empty"))
        results = layer.search_entities("anything", top_k=5)
        assert results == []

    def test_reindex_clears_previous(self, monkeypatch, tmp_path):
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.schemas import Entity, StoryKernel
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
        )
        kernel1 = StoryKernel(
            project_id="test",
            entities=[
                Entity(entity_id="old", name="OldEntity", entity_type="character", notes="old"),
            ],
        )
        layer.index_kernel(kernel1)
        assert len(layer.search_entities("OldEntity", top_k=5)) > 0

        kernel2 = StoryKernel(
            project_id="test",
            entities=[
                Entity(entity_id="new", name="NewEntity", entity_type="character", notes="new"),
            ],
        )
        layer.index_kernel(kernel2)
        # Old entity should be gone
        results = layer.search_entities("OldEntity", top_k=5)
        old_ids = [e.entity_id for e in results]
        assert "old" not in old_ids


class TestStoryKernelZVecKnowledgeSearch:
    """Test knowledge ledger indexing and semantic search."""

    def test_index_and_search_knowledge(self, monkeypatch, tmp_path):
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.schemas import KnowledgeLedger, StoryKernel
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
        )
        kernel = StoryKernel(
            project_id="test",
            knowledge_ledger=[
                KnowledgeLedger(
                    entry_id="k001",
                    entity_id="char_001",
                    fact="林远知道幽影城的位置",
                    knowledge_type="known",
                    source_chapter=3,
                ),
                KnowledgeLedger(
                    entry_id="k002",
                    entity_id="char_001",
                    fact="林远怀疑时光之钥有副作用",
                    knowledge_type="suspected",
                    source_chapter=5,
                ),
            ],
        )
        layer.index_kernel(kernel)

        results = layer.search_knowledge("幽影城位置", top_k=3)
        assert len(results) > 0
        entry_ids = [k.entry_id for k in results]
        assert "k001" in entry_ids

    def test_search_knowledge_returns_empty_on_empty(self, monkeypatch, tmp_path):
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.schemas import StoryKernel
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
        )
        layer.index_kernel(StoryKernel(project_id="empty"))
        results = layer.search_knowledge("anything", top_k=5)
        assert results == []

    def test_search_knowledge_entity_ids_filter(
        self, monkeypatch, tmp_path
    ):
        """entity_ids filter should return only entries matching those entities."""
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.schemas import KnowledgeLedger, StoryKernel
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
        )
        kernel = StoryKernel(
            project_id="test",
            knowledge_ledger=[
                KnowledgeLedger(
                    entry_id="k001",
                    entity_id="char_001",
                    fact="林远知道幽影城的位置",
                    knowledge_type="known",
                    source_chapter=3,
                ),
                KnowledgeLedger(
                    entry_id="k002",
                    entity_id="char_002",
                    fact="苏晴知道时光之钥的秘密",
                    knowledge_type="known",
                    source_chapter=5,
                ),
            ],
        )
        layer.index_kernel(kernel)

        results = layer.search_knowledge(
            "幽影城位置", top_k=5, entity_ids=["char_001"]
        )
        assert len(results) >= 1
        assert all(k.entity_id == "char_001" for k in results)

        results = layer.search_knowledge(
            "幽影城位置", top_k=5, entity_ids=["char_002"]
        )
        assert len(results) >= 1
        assert all(k.entity_id == "char_002" for k in results)

    def test_search_knowledge_entity_ids_none_returns_all(
        self, monkeypatch, tmp_path
    ):
        """entity_ids=None should be backward-compatible — return all matches."""
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.schemas import KnowledgeLedger, StoryKernel
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
        )
        kernel = StoryKernel(
            project_id="test",
            knowledge_ledger=[
                KnowledgeLedger(
                    entry_id="k001",
                    entity_id="char_001",
                    fact="林远知道幽影城的位置",
                    knowledge_type="known",
                    source_chapter=3,
                ),
                KnowledgeLedger(
                    entry_id="k002",
                    entity_id="char_002",
                    fact="苏晴知道时光之钥的秘密",
                    knowledge_type="known",
                    source_chapter=5,
                ),
            ],
        )
        layer.index_kernel(kernel)

        results_no_filter = layer.search_knowledge("知识", top_k=5)
        results_none = layer.search_knowledge("知识", top_k=5, entity_ids=None)
        assert len(results_no_filter) == len(results_none)
        assert len(results_no_filter) >= 2

    def test_search_knowledge_entity_ids_empty_list_returns_empty(
        self, monkeypatch, tmp_path
    ):
        """entity_ids=[] should filter out everything."""
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.schemas import KnowledgeLedger, StoryKernel
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
        )
        kernel = StoryKernel(
            project_id="test",
            knowledge_ledger=[
                KnowledgeLedger(
                    entry_id="k001",
                    entity_id="char_001",
                    fact="林远知道幽影城的位置",
                    knowledge_type="known",
                    source_chapter=3,
                ),
            ],
        )
        layer.index_kernel(kernel)

        results = layer.search_knowledge(
            "幽影城位置", top_k=5, entity_ids=[]
        )
        assert results == []

    def test_search_knowledge_entity_ids_multi_filter(
        self, monkeypatch, tmp_path
    ):
        """entity_ids with multiple IDs should return entries matching any of them."""
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.schemas import KnowledgeLedger, StoryKernel
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
        )
        kernel = StoryKernel(
            project_id="test",
            knowledge_ledger=[
                KnowledgeLedger(
                    entry_id="k001",
                    entity_id="char_001",
                    fact="林远知道幽影城的位置",
                    knowledge_type="known",
                    source_chapter=3,
                ),
                KnowledgeLedger(
                    entry_id="k002",
                    entity_id="char_002",
                    fact="苏晴知道时光之钥的秘密",
                    knowledge_type="known",
                    source_chapter=5,
                ),
                KnowledgeLedger(
                    entry_id="k003",
                    entity_id="char_003",
                    fact="老陈什么都不知道",
                    knowledge_type="known",
                    source_chapter=1,
                ),
            ],
        )
        layer.index_kernel(kernel)

        results = layer.search_knowledge(
            "知识", top_k=5, entity_ids=["char_001", "char_002"]
        )
        result_entity_ids = {k.entity_id for k in results}
        assert "char_003" not in result_entity_ids
        assert len(result_entity_ids.intersection({"char_001", "char_002"})) >= 1

    def test_index_knowledge_stores_entity_id_in_metadata(
        self, monkeypatch, tmp_path
    ):
        """_index_knowledge should store entity_id in ZVec metadata."""
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.schemas import KnowledgeLedger, StoryKernel
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
        )
        kernel = StoryKernel(
            project_id="test",
            knowledge_ledger=[
                KnowledgeLedger(
                    entry_id="k001",
                    entity_id="char_001",
                    fact="林远知道幽影城的位置",
                    knowledge_type="known",
                    source_chapter=3,
                ),
            ],
        )
        layer.index_kernel(kernel)

        store = layer._knowledge_store
        assert store is not None
        meta = store._metadata.get("k001")
        assert meta is not None
        assert meta.get("entity_id") == "char_001"


class TestStoryKernelZVecMotifSearch:
    """Test motif protocol indexing and search."""

    def test_index_and_search_motifs(self, monkeypatch, tmp_path):
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.schemas import MotifProtocol, StoryKernel
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
        )
        kernel = StoryKernel(
            project_id="test",
            motif_protocols=[
                MotifProtocol(
                    entry_id="m001",
                    motif_name="镜中人",
                    description="角色在镜中看到不同版本的自己",
                    motif_type="symbol",
                    channel="sensory_anchor",
                    examples=["林远在古镜中看到另一个自己"],
                ),
                MotifProtocol(
                    entry_id="m002",
                    motif_name="时间循环",
                    description="事件以不同方式重复发生",
                    motif_type="scene_pattern",
                    channel="sentence_pattern",
                    examples=["每次醒来都是同一天"],
                ),
            ],
        )
        layer.index_kernel(kernel)

        results = layer.search_motifs("镜子中看到自己")
        assert len(results) > 0
        entry_ids = [m.entry_id for m in results]
        assert "m001" in entry_ids

    def test_search_motifs_returns_empty_on_empty(self, monkeypatch, tmp_path):
        install_fake_zvec(monkeypatch)
        from novel_forge.story_kernel.schemas import StoryKernel
        from novel_forge.story_kernel.zvec_layer import StoryKernelZVec

        layer = StoryKernelZVec(
            path=str(tmp_path / "kernel_zvec"),
            dimension=16,
            embed_fn=_mock_embed,
        )
        layer.index_kernel(StoryKernel(project_id="empty"))
        results = layer.search_motifs("anything")
        assert results == []
