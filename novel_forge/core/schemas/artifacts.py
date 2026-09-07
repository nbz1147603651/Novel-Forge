"""Unified artifact contracts for init-to-chapter data flow."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from novel_forge.core.authoring import SemanticConsistencyView
from novel_forge.core.schemas.base import VersionedSchema

ArtifactScopeKind = Literal["project", "volume", "chapter", "scene"]
ArtifactQualityStatus = Literal["pass", "fail"]
ArtifactExecutionQuality = Literal["actual", "degraded", "fallback", "blocked", "legacy_unknown"]
ArtifactDerivationStatus = Literal["fresh", "stale", "conflict", "blocked", "legacy_unknown"]
ArtifactReusePolicy = Literal["signature_cache", "same_run_resume", "never"]


class ArtifactScope(VersionedSchema):
    """Scope covered by a pipeline artifact."""

    model_config = ConfigDict(extra="forbid")

    kind: ArtifactScopeKind
    ids: list[str] = Field(default_factory=list)

    @field_validator("ids", mode="before")
    @classmethod
    def _coerce_ids(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, int)):
            return [str(value)]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []


class ArtifactIssue(VersionedSchema):
    """A blocking or diagnostic issue attached to an artifact."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    severity: Literal["info", "low", "medium", "high", "critical"] = "high"
    source: str = ""
    path: str = ""

    @field_validator("code", "message", "severity", "source", "path", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return str(value or "").strip()


class CanonicalEntityRef(VersionedSchema):
    """Canonical entity reference carried by an artifact envelope."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    canonical_name: str
    entity_type: str = "unknown"
    aliases: list[str] = Field(default_factory=list)

    @field_validator("entity_id", "canonical_name", "entity_type", mode="before")
    @classmethod
    def _clean_required_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("aliases", mode="before")
    @classmethod
    def _clean_aliases(cls, value: Any) -> list[str]:
        values = value if isinstance(value, list) else ([] if value is None else [value])
        result: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result


class ArtifactEnvelope(VersionedSchema):
    """Uniform artifact wrapper for source, chapter, and stage products."""

    model_config = ConfigDict(extra="forbid")

    artifact_type: str
    project_id: str
    scope: ArtifactScope
    artifact_id: str = ""
    source_artifact_ids: list[str] = Field(default_factory=list)
    source_hashes: dict[str, str] = Field(default_factory=dict)
    canonical_entity_refs: list[CanonicalEntityRef] = Field(default_factory=list)
    quality_status: ArtifactQualityStatus = "pass"
    blocking_issues: list[ArtifactIssue] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("artifact_type", "project_id", "artifact_id", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("source_artifact_ids", mode="before")
    @classmethod
    def _clean_source_ids(cls, value: Any) -> list[str]:
        values = value if isinstance(value, list) else ([] if value is None else [value])
        result: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    @field_validator("source_hashes", mode="before")
    @classmethod
    def _clean_hashes(cls, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {
            str(key).strip(): str(item).strip()
            for key, item in value.items()
            if str(key).strip() and str(item).strip()
        }

    @model_validator(mode="after")
    def _validate_envelope(self) -> ArtifactEnvelope:
        if not self.artifact_type:
            raise ValueError("artifact_type is required")
        if not self.project_id:
            raise ValueError("project_id is required")
        if self.source_artifact_ids:
            missing_hashes = [
                source_id
                for source_id in self.source_artifact_ids
                if source_id not in self.source_hashes
            ]
            if missing_hashes:
                raise ValueError(
                    "source_hashes missing entries for: " + ", ".join(sorted(missing_hashes))
                )
        if self.quality_status == "pass" and self.blocking_issues:
            raise ValueError("quality_status cannot be pass when blocking_issues are present")
        _validate_canonical_refs(self.canonical_entity_refs)
        return self


class ProjectSpecArtifact(ArtifactEnvelope):
    artifact_type: Literal["project_spec"] = "project_spec"


class StoryFoundationArtifact(ArtifactEnvelope):
    artifact_type: Literal["story_foundation"] = "story_foundation"


class CharacterSystemArtifact(ArtifactEnvelope):
    artifact_type: Literal["character_system"] = "character_system"


class EntityGraphArtifact(ArtifactEnvelope):
    artifact_type: Literal["entity_graph"] = "entity_graph"


class StyleVoiceArtifact(ArtifactEnvelope):
    artifact_type: Literal["style_voice"] = "style_voice"


class CreativeDirectionArtifact(ArtifactEnvelope):
    artifact_type: Literal["creative_direction"] = "creative_direction"


class BlueprintArtifact(ArtifactEnvelope):
    artifact_type: Literal["blueprint"] = "blueprint"


class OutlineArtifact(ArtifactEnvelope):
    artifact_type: Literal["outline"] = "outline"


class NarrativeContractArtifact(ArtifactEnvelope):
    artifact_type: Literal["narrative_contract"] = "narrative_contract"


class ChapterContractIndexArtifact(ArtifactEnvelope):
    artifact_type: Literal["chapter_contract_index"] = "chapter_contract_index"


class InitReadinessArtifact(ArtifactEnvelope):
    artifact_type: Literal["init_readiness"] = "init_readiness"


class ChapterSourceSliceArtifact(ArtifactEnvelope):
    artifact_type: Literal["chapter_source_slice"] = "chapter_source_slice"
    semantic_consistency: SemanticConsistencyView = Field(
        default_factory=SemanticConsistencyView
    )


class StageArtifact(ArtifactEnvelope):
    """Common contract for generated chapter-stage artifacts."""

    artifact_type: Literal[
        "bridge",
        "plan",
        "scene_draft",
        "wave",
        "review",
        "repair",
        "polish",
        "humanize",
        "final",
        "canon_memory",
    ]
    previous_artifact_id: str = ""
    workflow_version: str = "novel.chapter.v2"
    artifact_schema_version: int = Field(default=2, ge=1)
    input_signature: str = "legacy_unknown"
    output_hash: str = "legacy_unknown"
    output_version: int = Field(default=1, ge=0)
    parent_artifact_versions: dict[str, int] = Field(default_factory=dict)
    source_text_hash: str = ""
    prompt_fingerprint: str = ""
    config_fingerprint: str = ""
    model_fingerprint: str = ""
    execution_quality_status: ArtifactExecutionQuality = "actual"
    degradation_reason: str = ""
    derivation_status: ArtifactDerivationStatus = "fresh"
    reuse_policy: ArtifactReusePolicy = "same_run_resume"
    run_attempt_id: str = ""
    event_ledger: list[dict[str, Any]] = Field(default_factory=list)
    forbidden_reveal_boundaries: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _mark_unsigned_legacy_artifact(cls, value: Any) -> Any:
        """Make unsigned historical records explicit instead of guessing freshness."""

        if not isinstance(value, dict) or "input_signature" in value:
            return value
        migrated = dict(value)
        migrated.setdefault("workflow_version", "legacy_unknown")
        migrated.setdefault("artifact_schema_version", 1)
        migrated.setdefault("input_signature", "legacy_unknown")
        migrated.setdefault("output_hash", "legacy_unknown")
        migrated.setdefault("output_version", 0)
        migrated.setdefault("execution_quality_status", "legacy_unknown")
        migrated.setdefault("derivation_status", "legacy_unknown")
        return migrated

    @model_validator(mode="after")
    def _validate_stage_sources(self) -> StageArtifact:
        if self.previous_artifact_id and self.previous_artifact_id not in self.source_hashes:
            raise ValueError("previous_artifact_id must be present in source_hashes")
        if self.execution_quality_status == "blocked" and self.derivation_status == "fresh":
            raise ValueError("blocked execution quality cannot be marked fresh")
        if self.degradation_reason and self.execution_quality_status == "actual":
            raise ValueError("actual execution quality cannot carry a degradation_reason")
        return self


def _validate_canonical_refs(refs: list[CanonicalEntityRef]) -> None:
    ids: set[str] = set()
    names: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for ref in refs:
        if not ref.entity_id:
            raise ValueError("canonical_entity_refs[].entity_id is required")
        if not ref.canonical_name:
            raise ValueError("canonical_entity_refs[].canonical_name is required")
        if ref.entity_id in ids:
            raise ValueError(f"duplicate entity_id in canonical_entity_refs: {ref.entity_id}")
        ids.add(ref.entity_id)
        normalized_name = ref.canonical_name.strip()
        if normalized_name in names:
            raise ValueError(
                f"duplicate canonical_name in canonical_entity_refs: {normalized_name}"
            )
        names[normalized_name] = ref.entity_id
        for alias in ref.aliases:
            if alias == normalized_name:
                continue
            existing = aliases.get(alias) or names.get(alias)
            if existing and existing != ref.entity_id:
                raise ValueError(f"alias '{alias}' maps to multiple canonical entities")
            aliases[alias] = ref.entity_id


__all__ = [
    "ArtifactEnvelope",
    "ArtifactIssue",
    "ArtifactDerivationStatus",
    "ArtifactExecutionQuality",
    "ArtifactQualityStatus",
    "ArtifactReusePolicy",
    "ArtifactScope",
    "ArtifactScopeKind",
    "BlueprintArtifact",
    "CanonicalEntityRef",
    "ChapterContractIndexArtifact",
    "ChapterSourceSliceArtifact",
    "CharacterSystemArtifact",
    "CreativeDirectionArtifact",
    "EntityGraphArtifact",
    "InitReadinessArtifact",
    "NarrativeContractArtifact",
    "OutlineArtifact",
    "ProjectSpecArtifact",
    "StageArtifact",
    "StoryFoundationArtifact",
    "StyleVoiceArtifact",
]
