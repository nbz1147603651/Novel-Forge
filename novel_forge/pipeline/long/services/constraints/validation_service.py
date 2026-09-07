"""Validation service for data coercion and fallback handling."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from pydantic import ValidationError

from novel_forge.core.schemas.bible import CharacterBible


class ValidationService:
    """Service for validating and coercing domain model instances."""

    _REPAIR_PATTERNS: list[tuple[str, str]] = [
        (r'(")}\s*,\s*"name"\s*:', r'"},{"name":'),
        (r'}\s*,\s*"name"\s*:', r'},{"name":'),
    ]

    @staticmethod
    def _repair_malformed_json(raw: str) -> str:
        for pattern, replacement in ValidationService._REPAIR_PATTERNS:
            raw = re.sub(pattern, replacement, raw)
        return raw

    @staticmethod
    def _filter_and_repair_characters(characters: list[Any]) -> tuple[list[dict[str, Any]], bool]:
        repaired: list[dict[str, Any]] = []
        needs_repair = False
        for item in characters:
            if isinstance(item, dict):
                repaired.append(item)
            elif isinstance(item, str):
                needs_repair = True
                try:
                    parsed = json.loads(item)
                    if isinstance(parsed, dict):
                        repaired.append(parsed)
                except (json.JSONDecodeError, TypeError):
                    pass
        return repaired, needs_repair

    @staticmethod
    def coerce_character_bible(
        payload: Any,
        on_step: Callable[[str, Any], None],
    ) -> CharacterBible:
        try:
            result = CharacterBible.model_validate(payload)
            if len(result.characters) == 1:
                on_step(
                    "init_character_bible_single_character_warning",
                    {
                        "name": result.characters[0].name,
                        "hint": "LLM may have flattened multiple character objects "
                                "into one due to missing JSON braces. Prompt fix applied.",
                    },
                )
            return result
        except ValidationError as first_error:
            characters: Any = None
            if isinstance(payload, dict):
                characters = payload.get("characters")

            if isinstance(characters, list) and len(characters) > 0:
                fixed_characters, needs_repair = ValidationService._filter_and_repair_characters(characters)
                if needs_repair and fixed_characters:
                    on_step(
                        "init_character_bible_repaired",
                        {
                            "reason": "filtered_invalid_character_items",
                            "kept": len(fixed_characters),
                            "removed": len(characters) - len(fixed_characters),
                        },
                    )
                    try:
                        return CharacterBible.model_validate({"characters": fixed_characters})
                    except ValidationError:
                        pass

                try:
                    raw_json = json.dumps(payload, ensure_ascii=False)
                    repaired_json = ValidationService._repair_malformed_json(raw_json)
                    if repaired_json != raw_json:
                        repaired_payload = json.loads(repaired_json)
                        on_step(
                            "init_character_bible_json_repaired",
                            {
                                "reason": "malformed_json_repaired",
                                "original_error": str(first_error),
                            },
                        )
                        return CharacterBible.model_validate(repaired_payload)
                except (json.JSONDecodeError, ValidationError):
                    pass

            if isinstance(characters, list) and len(characters) == 0:
                fallback = CharacterBible.model_validate(
                    {
                        "characters": [
                            {
                                "name": "\u4e3b\u89d2",
                                "role": "protagonist",
                                "notes": "\u81ea\u52a8\u515c\u5e95\uff1a\u6a21\u578b\u8fd4\u56de\u7a7a\u89d2\u8272\u5217\u8868",
                            }
                        ]
                    }
                )
                on_step(
                    "init_character_bible_fallback",
                    {
                        "reason": "empty_character_list",
                        "added": [c.model_dump() for c in fallback.characters],
                    },
                )
                return fallback

            raise
