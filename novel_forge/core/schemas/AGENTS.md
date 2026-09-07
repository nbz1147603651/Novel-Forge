# Novel Forge — Core Schemas

**Source of truth for all data validation across the system.**

## Schema Files

| File | Models | Purpose |
|------|--------|---------|
| `base.py` | `VersionedSchema`, `_SchemaFixMixin` | Base: version tracking, field name case-insensitive fixing, `extra="forbid"` |
| `spec.py` | `StorySpec` | User-provided creative brief |
| `beats.py` | `Beat`, `StoryBeats` | Story structure节拍; Beat uses BaseModel (not VersionedSchema) to save tokens |
| `bible.py` | `CharacterProfile`, `CharacterBible`, `StoryBible` | World-building; StoryBible has detail-field merging + unknown-field folding |
| `outline.py` | `ChapterOutline`, `VolumeOutline`, `StoryOutline`, `NarrativeBlueprint`, `SubplotPlan`, `SuspenseScheduleItem`, `NarrativePhase`, `TurningPoint`, `CharacterArcPlan`, `HookPlan`, `PayoffPlan` | **element_focus** field (0-3 ids) drives narrative element progress tracking |
| `chapter.py` | `ChapterMeta`, `AlignmentReport`, `ChapterRepairReport`, `CausalIssue`, `CausalValidationReport`, `ChapterOutcome`, `ChapterResult`, `PlotGuardDecision`, `MacroGuardReport` | Chapter generation output + causal validation fields |
| `canon.py` | `CreativeReport`, `NewCharacterDetail`, `PlotDeviation` | Pipeline artifact schemas for creative reports |
| `draft.py` | `Draft`, `EditResult` | Draft iteration with auto word-count |
| `continuity.py` | `ChapterBridge`, `ChapterPlan`, `ContinuityReport`, `RepairPlan`, `ChapterStatePacket`, `NarrativeBlueprintContext` | Bridge contracts, scene-level planning, `ChapterPlan.opening_bridge`, continuity audit |
| `artifacts.py` | `ArtifactEnvelope`, `ChapterSourceSliceArtifact`, `StageArtifact`, source artifact envelopes | Init/runtime artifact lineage and bounded source projections |
| `blueprint_elements.py` | `BlueprintElementCard`, `BlueprintElementPreferenceItem`, `BlueprintElementSelection` | Narrative element library selection |
| `short_blueprint.py` | `ShortBlueprint`, `StoryAnchor`, `ShortNarrativePhase` | Short story structural plan |
| `short_creative.py` | `ShortCreativeSummary`, `CharacterAnalysis`, `NarrativeAnalysis`, `ThematicAnalysis` | Short story creative analysis |
| `story_state.py` | `CharacterState`, `CharacterStateDelta`, `RelationshipState`, `PlotThreadState`, `ChapterExitState` | Long-form state snapshots; CharacterState has nested physical/emotional/motivation/knowledge |
| `style_profile.py` | `ProjectStyleProfile`, `StyleModule`, `HookConfig`, `CoolPointConfig`, `MicroPayoffConfig`, `StrandConfig`, `PacingConfig`, `GlobalStyleConfig` | Project writing style spec (merged GenreProfile + _styles) |
| `volume.py` | `VolumeAuditReport`, `VolumeMilestoneStatus` | Volume-end audit |
| `eval_schema.py` | `EvalReport`, `EvalScore`, `RepairSuggestion` | Quality evaluation scores |
| `reading_power.py` | `ReadingPowerReport`, `ReadingPowerHint`, `MicroPayoff`, `HookType`, `HookStrength`, `MicroPayoffType` | Reader pull/追读力 scoring |
| `reading_power_window_config.py` | `ReadingPowerWindowConfig` | Sliding window system parameters |
| `repair.py` | `RepairCase`, `RepairCandidate`, `RepairVerificationBundle`, `RepairPublishReceipt`, workbench requests/views | Candidate-first non-canon evidence, exact CAS identities and guarded publication/recovery receipts; schemas grant no write authority |

## Critical Patterns

### Base Model
```python
class VersionedSchema(BaseModel, _SchemaFixMixin):
    schema_version: str = "2.0"      # always present
    created_at: datetime              # auto-set to UTC now
    model_config = {"extra": "forbid"}  # reject unknown fields
```
- `_SchemaFixMixin` auto-fixes camelCase/snake_case mismatches from LLM outputs
- `_correct_fields` + `_field_aliases` per model enable field normalization

### Schema Changes Cascade
Schema changes require updating ALL of:
1. `core/format_contracts.py` — TaskType → TaskFormatContract mapping
2. Corresponding `.j2` prompt templates
3. Pipeline step I/O type annotations

### Frozen Dataclasses
Some internal types are `@dataclass(frozen=True)`. Always use `dataclasses.replace()`:
```python
# ❌ state.field = new_value  # FrozenInstanceError
# ✅ state = dataclasses.replace(state, field=new_value)
```

### element_focus Pattern
`ChapterOutline.element_focus: list[str]` caps at 3 items (normalized in `model_validator`). Drives:
- Narrative element progress tracking (hit/weak/miss)
- Planning hint injection into `plan_chapter.j2`
- Dynamic element recommendation when outline doesn't specify focus

### Bridge / Plan Boundary
`ChapterBridge` is the audited transition artifact. Planning absorbs its executable opening fields into `ChapterPlan.opening_bridge`; writing stages should read the Plan field so `ChapterPlan` remains the single authoritative Draft constraint source.

### Chapter Source Boundary
`artifacts.py` defines raw init source envelopes and `ChapterSourceSliceArtifact`. Runtime prompts should not consume full `project_spec`, `character_system`, `entity_graph`, or `blueprint` schemas directly; service-layer projections select bounded chapter fields before template rendering.

## Anti-Patterns
- **DO NOT** add fields without updating matching TaskFormatContract
- **DO NOT** change field types without checking all pipeline step I/O
- **DO NOT** duplicate schema definitions — import from here
- **DO NOT** modify schemas at runtime — they are validation contracts
- **DO NOT** use VersionedSchema on frequently-instantiated nested objects (e.g., Beat) — tokens bloat

## Relationships
- `core/format_contracts.py` — maps TaskType → TaskFormatContract (JSON/TEXT + required keys)
- Pipeline steps — use schemas as InputT/OutputT generic types
- Prompts — reference schemas for JSON output format specification
- `story_kernel/store.py` — persists StoryKernel; `story_kernel/rules.py` — consistency validation
