"""Tests for INTRODUCE_CHARACTER task output adapter normalization."""

from __future__ import annotations

from novel_forge.common.constants import TaskType
from novel_forge.pipeline.long.services.task_output_adapters import (
    _CHARACTER_TEXT_FIELDS,
    apply_task_output_adapter,
)


def test_introduce_character_adapter_flattens_nested_dict_fields() -> None:
    data = {
        "name": "张三",
        "role": "主角",
        "social_status": {"current": "平民", "origin": "贵族"},
        "abilities": ["剑术", "魔法"],
        "appearance": {"height": "高", "hair": "黑"},
        "personality": "沉稳",
        "backstory": "普通人",
        "arc": "成长",
        "relationships": {"李四": {"relation": "朋友", "history": "多年"}},
        "notes": "",
    }

    result = apply_task_output_adapter(data, TaskType.INTRODUCE_CHARACTER)

    assert result.changed is True
    for field in _CHARACTER_TEXT_FIELDS:
        if field in data:
            assert isinstance(data[field], str), f"{field} should be string after normalization"


def test_introduce_character_adapter_noop_when_already_flat() -> None:
    data = {
        "name": "张三",
        "role": "主角",
        "social_status": "平民",
        "abilities": "剑术",
        "appearance": "高个子",
        "personality": "沉稳",
        "backstory": "普通人",
        "arc": "成长",
        "relationships": {"李四": "朋友"},
        "notes": "",
    }

    result = apply_task_output_adapter(data, TaskType.INTRODUCE_CHARACTER)

    assert result.changed is False


def test_introduce_character_adapter_stringifies_relationships_values() -> None:
    data = {
        "name": "张三",
        "role": "主角",
        "social_status": "平民",
        "abilities": "剑术",
        "appearance": "高个子",
        "personality": "沉稳",
        "backstory": "普通人",
        "arc": "成长",
        "relationships": {"李四": {"detail": "多年好友", "trust": "高"}},
        "notes": "",
    }

    apply_task_output_adapter(data, TaskType.INTRODUCE_CHARACTER)

    assert isinstance(data["relationships"]["李四"], str)
    assert "多年好友" in data["relationships"]["李四"]
