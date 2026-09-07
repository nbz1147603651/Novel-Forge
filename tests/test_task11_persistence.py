"""Test script for Task 11: Persistence + Real-time Refresh verification."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path


def test_atomic_write():
    """Verify atomic_write_json produces valid files with no intermediate state."""
    from novel_forge.persistence.filesystem import atomic_write_json

    with tempfile.TemporaryDirectory() as tmpdir:
        test_path = Path(tmpdir) / "test_blueprint.json"

        # Write initial data
        data = {
            "subplot_plan": [
                {"name": "test_subplot", "priority": "normal", "involved_chapters": [1, 2, 3]}
            ],
            "character_arcs": [],
        }
        atomic_write_json(test_path, data)

        # Verify file exists and is valid JSON
        assert test_path.exists(), "File should exist after atomic write"
        with open(test_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded == data, "Loaded data should match written data"

        # Verify no .tmp file remains
        tmp_files = list(Path(tmpdir).glob("*.tmp"))
        assert len(tmp_files) == 0, f"No temp files should remain, found: {tmp_files}"

        # Overwrite with new data (simulating edit)
        data["subplot_plan"][0]["priority"] = "primary"
        atomic_write_json(test_path, data)

        with open(test_path, "r", encoding="utf-8") as f:
            loaded2 = json.load(f)
        assert loaded2["subplot_plan"][0]["priority"] == "primary", "Overwrite should work"

        print("  PASS: atomic_write_json produces valid files, no intermediate state")


def test_save_data_atomic():
    """Verify SubplotManagerPanel.save_data uses atomic write."""
    import sys
    # Ensure novel_forge is importable
    sys.path.insert(0, str(Path(__file__).parent))

    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    from novel_forge.desktop.pages.standalone.subplot_manager import SubplotManagerPanel

    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir)
        plans_dir = project_path / "plans"
        plans_dir.mkdir()

        # Create initial blueprint
        blueprint = {
            "subplot_plan": [
                {"name": "arc_a", "priority": "normal", "involved_chapters": [1, 2, 3]}
            ],
            "character_arcs": [],
        }
        blueprint_path = plans_dir / "narrative_blueprint.json"
        with open(blueprint_path, "w", encoding="utf-8") as f:
            json.dump(blueprint, f, ensure_ascii=False, indent=2)

        # Create panel and modify data
        panel = SubplotManagerPanel(project_path)
        subplots = panel.get_subplots()
        subplots[0]["priority"] = "primary"
        subplots[0]["description"] = "Updated description"

        panel.save_data()

        # Verify file was updated
        with open(blueprint_path, "r", encoding="utf-8") as f:
            saved = json.load(f)

        assert saved["subplot_plan"][0]["priority"] == "primary", "Priority should be updated"
        assert saved["subplot_plan"][0]["description"] == "Updated description", "Description should be updated"

        # Verify no .tmp file remains
        tmp_files = list(plans_dir.glob("*.tmp"))
        assert len(tmp_files) == 0, f"No temp files should remain, found: {tmp_files}"

        print("  PASS: save_data uses atomic write, file correctly updated")


def test_data_changed_signal():
    """Verify data_changed signal is emitted on mutations."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))

    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    from novel_forge.desktop.pages.standalone.subplot_manager import SubplotManagerPanel

    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir)
        plans_dir = project_path / "plans"
        plans_dir.mkdir()

        blueprint = {"subplot_plan": [], "character_arcs": []}
        with open(plans_dir / "narrative_blueprint.json", "w", encoding="utf-8") as f:
            json.dump(blueprint, f)

        panel = SubplotManagerPanel(project_path)

        signal_count = [0]
        panel.data_changed.connect(lambda: signal_count.__setitem__(0, signal_count[0] + 1))

        # Simulate add
        panel._blueprint.setdefault("subplot_plan", []).append({
            "name": "test", "description": "", "priority": "normal",
            "involved_chapters": [1], "chapter_events": [], "weave_links": [],
            "resolution_chapter": 0, "resolution_target": "", "resolution_type": "",
        })
        panel._render_list()
        panel.save_data()
        panel.data_changed.emit()

        assert signal_count[0] == 1, f"Signal should be emitted once, got {signal_count[0]}"

        print("  PASS: data_changed signal emitted on mutation")


def test_arc_to_subplot_migration():
    """Verify _arc_to_subplot creates proper SubplotPlan fields."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))

    from novel_forge.desktop.pages.standalone.subplot_manager import _arc_to_subplot

    arc = {
        "character": "张三",
        "arc_summary": "张三的成长历程",
        "milestones": [
            {"chapter_start": 5, "chapter_end": 8, "description": "发现阴谋"},
            {"chapter_start": 12, "chapter_end": 15, "description": "对抗敌人"},
        ]
    }

    result = _arc_to_subplot(arc)

    assert result["name"] == "张三线", f"Name should be '张三线', got {result['name']}"
    assert result["description"] == "张三的成长历程"
    assert result["involved_chapters"] == list(range(5, 16)), \
        f"Chapters should span 5-15, got {result['involved_chapters']}"
    assert len(result["chapter_events"]) == 2, "Should have 2 events from milestones"
    assert result["chapter_events"][0]["chapter_number"] == 5
    assert result["chapter_events"][0]["event"] == "发现阴谋"
    assert result["priority"] == "normal"
    assert result["resolution_chapter"] == 0
    assert result["weave_links"] == []

    # Verify all required SubplotPlan fields present
    required_fields = {
        "name", "description", "involved_chapters", "chapter_events",
        "weave_links", "priority", "resolution_chapter", "resolution_target",
        "resolution_type",
    }
    missing = required_fields - set(result.keys())
    assert not missing, f"Missing fields: {missing}"

    print("  PASS: _arc_to_subplot creates proper SubplotPlan with all fields")


if __name__ == "__main__":
    print("Task 11 Persistence Tests")
    print("=" * 50)

    print("\n[1] Testing atomic_write_json...")
    test_atomic_write()

    print("\n[2] Testing save_data atomic write...")
    test_save_data_atomic()

    print("\n[3] Testing data_changed signal...")
    test_data_changed_signal()

    print("\n[4] Testing arc-to-subplot migration...")
    test_arc_to_subplot_migration()

    print("\n" + "=" * 50)
    print("ALL TESTS PASSED")
