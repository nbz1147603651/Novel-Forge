"""Tests for motif state sharding (motif_state.json).

Verifies that motif data is persisted to a separate file and
loaded correctly with backward compatibility for old format.
"""

from __future__ import annotations

from pathlib import Path

from novel_forge.memory.integration import MemoryContext


def _make_ctx(tmp_path: Path, project_id: str):
    """Create a MemoryContext with real FileSystemStorage."""
    from novel_forge.persistence.filesystem import FileSystemStorage

    storage = FileSystemStorage(tmp_path)
    return MemoryContext(
        _project_id=project_id,
        _storage=storage,
        _router=None,
        _builder=None,
    )


class TestMotifShardSave:
    """Verify save_to_disk writes motif_state.json separately."""

    def test_save_writes_motif_state_json(self, tmp_path: Path) -> None:
        """After save_to_disk, motif_state.json exists alongside project_memory.json."""
        project_id = "test_motif_shard_save"
        ctx = _make_ctx(tmp_path, project_id)

        # Populate motif cache with test data
        ctx._motif_cache[1] = [{"motif_id": "m1", "name": "红绳", "category": "意象"}]
        ctx._motif_cache[2] = [{"motif_id": "m2", "name": "钟声", "category": "声音"}]

        result = ctx.save_to_disk()
        assert result is True

        memory_dir = tmp_path / project_id / "memory"
        motif_state_path = memory_dir / "motif_state.json"
        assert motif_state_path.exists(), "motif_state.json should be written"

        project_memory_path = memory_dir / "project_memory.json"
        assert project_memory_path.exists(), "project_memory.json should still be written"

    def test_project_memory_json_excludes_motif_fields(self, tmp_path: Path) -> None:
        """project_memory.json should NOT contain motif_cache or motif_tracker after save."""
        project_id = "test_motif_excluded"
        ctx = _make_ctx(tmp_path, project_id)

        ctx._motif_cache[1] = [{"motif_id": "m1", "name": "红绳"}]
        ctx._last_indexed_chapter = 3

        ctx.save_to_disk()

        memory_dir = tmp_path / project_id / "memory"
        project_memory = (memory_dir / "project_memory.json").read_text()

        assert "motif_cache" not in project_memory, (
            "motif_cache should NOT be in project_memory.json"
        )
        assert "motif_tracker" not in project_memory, (
            "motif_tracker should NOT be in project_memory.json"
        )

        # Verify non-motif fields are still present
        assert "last_indexed_chapter" in project_memory
        assert "episodic_index" in project_memory

    def test_motif_state_json_contains_motif_data(self, tmp_path: Path) -> None:
        """motif_state.json should contain motif_cache and motif_tracker."""
        import json

        project_id = "test_motif_content"
        ctx = _make_ctx(tmp_path, project_id)

        ctx._motif_cache[1] = [{"motif_id": "m1", "name": "红绳", "category": "意象"}]
        ctx._motif_cache[3] = [{"motif_id": "m3", "name": "月光", "category": "感官"}]

        ctx.save_to_disk()

        memory_dir = tmp_path / project_id / "memory"
        motif_data = json.loads((memory_dir / "motif_state.json").read_text())

        assert "motif_cache" in motif_data
        assert "motif_tracker" in motif_data
        assert "1" in motif_data["motif_cache"]
        assert "3" in motif_data["motif_cache"]
        assert len(motif_data["motif_cache"]["1"]) == 1
        assert motif_data["motif_cache"]["1"][0]["name"] == "红绳"


class TestMotifShardLoad:
    """Verify load_from_disk reads from motif_state.json with fallback."""

    def test_load_reads_from_motif_state_json(self, tmp_path: Path) -> None:
        """load_from_disk should read motif data from motif_state.json when it exists."""
        project_id = "test_motif_load_new"
        ctx = _make_ctx(tmp_path, project_id)

        # Write both files manually to simulate a prior save
        memory_dir = tmp_path / project_id / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)

        motif_state = {
            "motif_cache": {
                "1": [{"motif_id": "m1", "name": "红绳", "category": "意象"}],
                "2": [{"motif_id": "m2", "name": "钟声", "category": "声音"}],
            },
            "motif_tracker": {
                "motifs": {
                    "m1": {
                        "name": "红绳",
                        "category": "意象",
                        "description": "命运的象征",
                        "occurrence_count": 5,
                        "first_appearance_chapter": 1,
                        "last_appearance_chapter": 2,
                        "associated_characters": ["张三"],
                        "thematic_meaning": "命运相连",
                        "is_intentional": True,
                        "retired": False,
                        "metadata": {},
                    }
                },
                "chapter_motifs": {"1": ["m1"], "2": ["m2"]},
            },
        }
        (memory_dir / "motif_state.json").write_text(__import__("json").dumps(motif_state))
        (memory_dir / "project_memory.json").write_text(
            __import__("json").dumps(
                {
                    "last_indexed_chapter": 2,
                    "summary_cache": {},
                    "chapter_content_hash": {},
                    "episodic_index": {},
                }
            )
        )

        result = ctx.load_from_disk()
        assert result is True

        assert 1 in ctx._motif_cache
        assert 2 in ctx._motif_cache
        assert ctx._motif_cache[1][0]["name"] == "红绳"
        assert ctx._motif_cache[2][0]["name"] == "钟声"

    def test_backward_compat_falls_back_to_old_format(self, tmp_path: Path) -> None:
        """When motif_state.json doesn't exist, load from project_memory.json motif fields."""
        project_id = "test_motif_old_format"
        ctx = _make_ctx(tmp_path, project_id)

        memory_dir = tmp_path / project_id / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)

        # Old format: motif data embedded in project_memory.json
        old_format = {
            "last_indexed_chapter": 1,
            "summary_cache": {},
            "chapter_content_hash": {},
            "episodic_index": {},
            "motif_cache": {
                "1": [{"motif_id": "old_m1", "name": "旧 motif"}],
            },
            "motif_tracker": {
                "motifs": {},
                "chapter_motifs": {},
            },
        }
        (memory_dir / "project_memory.json").write_text(__import__("json").dumps(old_format))

        # Ensure no motif_state.json exists
        assert not (memory_dir / "motif_state.json").exists()

        result = ctx.load_from_disk()
        assert result is True

        assert 1 in ctx._motif_cache
        assert ctx._motif_cache[1][0]["name"] == "旧 motif"

    def test_no_motif_data_empty_start(self, tmp_path: Path) -> None:
        """When neither file has motif data, start with empty motif cache."""
        project_id = "test_no_motif"
        ctx = _make_ctx(tmp_path, project_id)

        memory_dir = tmp_path / project_id / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        (memory_dir / "project_memory.json").write_text(
            __import__("json").dumps(
                {
                    "last_indexed_chapter": 0,
                    "summary_cache": {},
                    "chapter_content_hash": {},
                    "episodic_index": {},
                }
            )
        )

        result = ctx.load_from_disk()
        assert result is True
        assert ctx._motif_cache == {}


class TestMotifShardRoundtrip:
    """Verify full save -> load roundtrip with sharded motif data."""

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        """Save motif data, create new context, load it back — data matches."""
        project_id = "test_roundtrip"
        ctx1 = _make_ctx(tmp_path, project_id)

        ctx1._motif_cache[1] = [{"motif_id": "m1", "name": "红绳", "category": "意象"}]
        ctx1._motif_cache[5] = [
            {"motif_id": "m2", "name": "钟声", "category": "声音"},
            {"motif_id": "m3", "name": "月光", "category": "感官"},
        ]
        ctx1._last_indexed_chapter = 5

        assert ctx1.save_to_disk() is True

        # Create a fresh context and load
        ctx2 = _make_ctx(tmp_path, project_id)
        assert ctx2.load_from_disk() is True

        assert ctx2._last_indexed_chapter == 5
        assert 1 in ctx2._motif_cache
        assert 5 in ctx2._motif_cache
        assert ctx2._motif_cache[1][0]["name"] == "红绳"
        assert len(ctx2._motif_cache[5]) == 2

    def test_project_memory_clean_after_roundtrip(self, tmp_path: Path) -> None:
        """After roundtrip, project_memory.json still has no motif fields."""
        project_id = "test_clean_roundtrip"
        ctx = _make_ctx(tmp_path, project_id)

        ctx._motif_cache[1] = [{"motif_id": "m1", "name": "test"}]
        ctx.save_to_disk()

        memory_dir = tmp_path / project_id / "memory"
        pm_text = (memory_dir / "project_memory.json").read_text()
        assert "motif_cache" not in pm_text
        assert "motif_tracker" not in pm_text
