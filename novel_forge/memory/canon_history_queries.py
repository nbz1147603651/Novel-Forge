"""Legacy canon-current history query helpers."""

from __future__ import annotations

from typing import Any

from novel_forge.obs.logger import get_logger

_log = get_logger("memory.context")


def get_relationship_evolution(
    *,
    storage: Any,
    project_id: str,
    character_name: str,
    current_chapter: int,
    lookback: int = 10,
    logger: Any = _log,
) -> list[dict[str, Any]]:
    """Get relationship evolution for a character across recent chapters."""
    if not storage or not project_id:
        return []

    try:
        canon_path = storage.project_path(project_id) / "canon" / "canon_current.json"
        if not storage.exists(canon_path):
            return []

        canon_state = storage.load_json(canon_path)
        chapter_exit_states = canon_state.get("chapter_exit_states", {})

        evolution: list[dict[str, Any]] = []
        for ch in range(max(1, current_chapter - lookback), current_chapter):
            ch_str = str(ch)
            if ch_str not in chapter_exit_states:
                continue
            exit_state = chapter_exit_states[ch_str]
            relationships = exit_state.get("relationships", {})
            if not relationships:
                continue

            for _rel_key, rel_data in relationships.items():
                if not isinstance(rel_data, dict):
                    continue
                chars = rel_data.get("characters", [])
                if character_name not in chars:
                    continue
                other = next((c for c in chars if c != character_name), "")
                if not other:
                    continue
                evolution.append(
                    {
                        "chapter": ch,
                        "partner": other,
                        "public_status": rel_data.get("public_status", ""),
                        "trust": rel_data.get("trust", 0.5),
                        "tension": rel_data.get("tension", 0.5),
                        "shift_event": rel_data.get("last_shift_event", ""),
                    }
                )

        return evolution

    except Exception as exc:
        logger.warning("Failed to get relationship evolution for %s: %s", character_name, exc)
        return []


def get_character_history(
    *,
    storage: Any,
    project_id: str,
    character_name: str,
    current_chapter: int,
    lookback: int = 10,
    logger: Any = _log,
) -> list[dict[str, Any]]:
    """Get character's historical state from recent chapters."""
    if not storage or not project_id:
        return []

    try:
        canon_path = storage.project_path(project_id) / "canon" / "canon_current.json"
        if not storage.exists(canon_path):
            return []

        canon_state = storage.load_json(canon_path)
        chapter_exit_states = canon_state.get("chapter_exit_states", {})

        history = []
        for ch in range(max(1, current_chapter - lookback), current_chapter):
            ch_str = str(ch)
            if ch_str in chapter_exit_states:
                exit_state = chapter_exit_states[ch_str]
                character_states = exit_state.get("character_end_states", {})

                if character_name in character_states:
                    char_state = character_states[character_name]
                    history.append(
                        {
                            "chapter": ch,
                            "location": char_state.get("physical", {}).get("location", "")
                            if isinstance(char_state, dict)
                            else getattr(char_state, "physical", None),
                            "emotional_state": char_state.get("emotional", {}).get(
                                "primary_emotion", ""
                            )
                            if isinstance(char_state, dict)
                            else getattr(char_state, "emotional", None),
                            "inventory": char_state.get("physical", {}).get("inventory", [])
                            if isinstance(char_state, dict)
                            else getattr(char_state, "inventory", []),
                            "alive": char_state.get("alive", True)
                            if isinstance(char_state, dict)
                            else getattr(char_state, "alive", True),
                        }
                    )

        return history

    except Exception as exc:
        logger.warning("Failed to get character history for %s: %s", character_name, exc)
        return []


__all__ = ("get_character_history", "get_relationship_evolution")
