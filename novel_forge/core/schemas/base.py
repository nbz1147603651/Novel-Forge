"""Base schema with mandatory version tracking."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import BaseModel, Field, model_validator


class _SchemaFixMixin:
    """Mixin providing automatic field name case-insensitive fixing for LLM outputs.

    Subclasses should define _correct_fields as a class attribute containing
    all valid field names in lowercase.

    Subclasses may also define _field_aliases as a legacy-compatibility dict
    mapping historical/LLM-drifted names to the canonical field name, e.g.
    {"subplot_name": "name"}. This is intentionally a rescue path for old
    persisted artifacts and rare provider drift; normal model responses must
    satisfy TaskFormatContract / JSON Schema with canonical field names.

    The base mixin also provides a common set of LLM field name variants (>=20)
    that cover camelCase/snake_case mismatches, abbreviations, and common typos.
    """

    _correct_fields: ClassVar[set[str]] = set()
    _enable_legacy_field_aliases: ClassVar[bool] = True
    _field_aliases: ClassVar[dict[str, str]] = {
        # camelCase → snake_case variants (most common LLM mistake)
        "characterName": "character_name",
        "chapterNumber": "chapter_number",
        "plotPoint": "plot_point",
        "mainPlotPoint": "main_plot_point",
        "subplotPlan": "subplot_plan",
        "elementFocus": "element_focus",
        "beatDescription": "beat_description",
        "alignmentScore": "alignment_score",
        "continuityScore": "continuity_score",
        "evaluationScore": "evaluation_score",
        "relationshipDescription": "relationship_description",
        "wordCount": "word_count",
        "timeStamp": "timestamp",
        "povCharacter": "pov_character",
        "involvedCharacter": "involved_character",
        "chapterTitle": "chapter_title",
        "storyTheme": "story_theme",
        "tensionLevel": "tension_level",
        # Abbreviation variants
        "desc": "description",
        "motif": "motifs",
        "char": "character",
        "cnt": "count",
        "num": "number",
        "charName": "character_name",
        "relDesc": "relationship_description",
        "entityName": "entity",
        "povChar": "pov_character",
        # Content variants
        "titleText": "title",
        "summaryText": "summary",
        "chapterSummary": "summary",
        "plotSummary": "plot_summary",
    }
    
    @model_validator(mode="before")
    @classmethod
    def _fix_field_names(cls, data: Any) -> Any:
        """Fix common field name case variations from LLM outputs."""
        if not isinstance(data, dict):
            return data
        
        # Fast path: if no aliases and no correct_fields defined, skip all processing
        has_aliases = cls._enable_legacy_field_aliases and cls._field_aliases
        has_correct_fields = bool(cls._correct_fields)
        if not has_aliases and not has_correct_fields:
            return data
        
        # Legacy rescue path only: normal parser contracts should reject
        # non-canonical response keys before domain schemas see them.
        if has_aliases:
            for alias, correct in cls._field_aliases.items():
                if alias in data and correct not in data:
                    data[correct] = data.pop(alias)
                elif alias in data:
                    data.pop(alias)
        
        if not has_correct_fields:
            return data
        
        typo_map = {}
        for key in list(data.keys()):
            if key not in cls._correct_fields:
                lower_key = key.lower()
                if lower_key in cls._correct_fields:
                    typo_map[key] = lower_key
        
        for typo, correct in typo_map.items():
            if correct not in data:
                data[correct] = data.pop(typo)
        
        return data


class VersionedSchema(BaseModel, _SchemaFixMixin):
    """Every domain schema inherits this to carry a version tag.
    
    Also inherits _SchemaFixMixin which provides automatic field name
    case-insensitive fixing for LLM outputs (e.g., "Name" → "name").
    """

    schema_version: str = Field(
        default="2.0",
        description="Semantic version of this schema for forward-compat migrations.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of creation.",
    )

    model_config = {"extra": "forbid"}

    def __str__(self) -> str:
        """Render as clean JSON for LLM prompt injection, omitting internal metadata."""
        data = self.model_dump(
            mode="json",
            exclude={"schema_version", "created_at"},
        )
        return json.dumps(data, ensure_ascii=False, indent=2)
