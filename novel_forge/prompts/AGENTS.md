# Novel Forge — Prompts Module AGENTS.md

## Module Overview

Prompt template system with 123 Jinja2 templates, LLM-driven style derivation, schema-first output contracts, compliance guarding, and automated prompt catalog linting.

## File Structure

| File | Responsibility |
|------|----------------|
| `builder.py` | PromptBuilder assembles prompts, injects system preamble, runs compliance |
| `registry.py` | TaskType → Template mapping, per-locale Jinja2 Environment with context helpers |
| `packs.py` | Prompt pack discovery, manifest parsing, output-language → prompt-locale mapping |
| `metadata.py` | Static prompt catalog, layer detection, and maintainability lint helpers |
| `compliance.py` | ComplianceGuard blocks copyright-infringing prompts |
| `version.py` | TemplateVersionManager tracks 8 category versions (writing 2.1.0, etc.) |
| `context_helpers.py` | 12 format helpers: fmt_list, fmt_characters, fmt_events, etc. |

## Template Directory Structure

```
prompts/packs/zh/templates/  # stable zh prompt pack; current canonical template source
├── _base/               # 11 base shared templates (narrative/state/style helpers; no output-format macros)
├── _styles/             # 1 style macro (render profile)
│   └── _render_style_profile.j2
├── beats/               # 2 templates
├── kernel/              # 11 templates
├── checking/            # 41 templates (largest category)
│   ├── _causal_core.j2, _continuity_core.j2
│   ├── book_consistency.j2, book_consistency_verify.j2
│   └── critic_*.j2, element_progress_arbiter.j2
├── compression/         # 5 templates
├── initialization/      # 17 templates
├── planning/           # 20 templates (plan_chapter, plan_outline_batch/continue have element_focus)
├── summary/            # 5 templates
└── writing/           # 8 templates (draft_chapter, edit_chapter have element focus constraints)

`prompts/prompts/` is retained for compatibility with older tests and references. New prompt
work should target the active prompt pack directory and keep the relevant manifest in
`prompts/packs/<locale>/manifest.toml` accurate. Draft locale packs may exist without templates;
only `status = "stable"` packs are exposed in default Desktop language choices.
```

## Critical Patterns

### Author Intent and Workflow Synchronization

Before changing novel prompt semantics, read [the current workflow](../../docs/novel-workflow-current.md) and [author-control design D04/D08/D10](../../docs/novel-authoring-control-design.md). Update their affected steps and implementation status in the same change, alongside the prompt/format/parser contract and any API/UI explanation affected.

Reuse the canonical intent and guidance projections: distinguish invariant facts, narrative obligations, explicit required literals and optional expression. Attachments/research are source material, not user authority. Do not promote a model's preference or score into author intent, turn optional motifs into mandatory repetition, or erase intentional voice/pacing to optimize an aggregate score. Existing objective gates still apply; unresolved conflicts need evidence and the appropriate decision, not silent prompt retcons. Respect scoped source slices and context completeness without building duplicate canon.

### Adding a New Template

1. Create `.j2` file in appropriate subdirectory
2. Add `TaskType → filename` mapping in `registry.py`
3. Add matching `TaskFormatContract` in `core/format_contracts.py`
4. Add a Jinja comment `【备注】` block documenting purpose, upstream inputs, downstream output/consumers, and P0/P1/P2 priority
5. Keep the prompt body under an explicit `指导层`
6. Do not write output-format blocks in task templates. `PromptBuilder` appends the single output boundary from `TaskFormatContract` after rendering.
7. Run prompt maintenance checks and regenerate the index:

```bash
.venv/bin/python scripts/lint_prompt_layers.py
.venv/bin/python scripts/audit_prompt_format_layers.py --all
.venv/bin/python scripts/audit_prompt_packs.py
.venv/bin/python scripts/generate_prompt_index.py
.venv/bin/python scripts/generate_prompt_index.py --check
.venv/bin/python scripts/verify_templates.py
.venv/bin/python scripts/verify_format_contracts.py
```

Use `scripts/scaffold_prompt.py <task_type> <category> --output-kind json|text` to create a three-layer template skeleton before registering a new task.

### PromptBuilder Flow

```
render(task_type, context)
  → resolve output_language + prompt_locale
  → registry.render()      # locale-specific Jinja2 template + context helpers
  → render_prompt_contract_block()  # inject localized format contract
  → _optimize_prompt_text()       # compact whitespace
  → compliance.check_prompt()      # raise on forbidden patterns
```

### Maintainability Layers

Registered task templates must expose three AI-readable layers:

1. **备注层**: Jinja comment only; documents task id, service step, purpose, upstream data, downstream consumers, and priority.
2. **指导层**: Rendered prompt instructions and context consumption rules.
3. **格式层**: Injected by `PromptBuilder` from `TaskFormatContract`; task templates must not contain visible handwritten JSON/TEXT protocol blocks.

`scripts/lint_prompt_layers.py` enforces the layer contract. `scripts/audit_prompt_format_layers.py --all` prints the per-TaskType output kind, required keys, and Builder-injected boundary source. `scripts/generate_prompt_index.py` rebuilds `prompts/INDEX.md` from registry + contracts + template metadata, so do not hand-edit the template inventory.

### Style System

Writing styles are NOT hardcoded into template files. Instead, the system uses
**`ProjectStyleProfile`** (generated by PROFILE_STYLE step via `style_profile_derive.j2`)
as the primary style mechanism. It derives detailed rules from story synopsis,
world bible, character bible, blueprint elements, tone, genre, and user
`extra_instructions`. Legacy `writing_style` values may exist in old specs, but
new prompt work should not introduce or depend on them.

The `_styles/` directory contains rendering macros (not style templates). Style rules are LLM-derived, not hardcoded.

### Long-Chapter Source Boundaries

For long-chapter writing templates, Bridge is only an upstream Planning input. After Planning, executable opening continuity lives in `stage_cards.plan.opening_bridge`. `draft_chapter.j2` and `draft_scene.j2` must not read `stage_cards.bridge`; WAVE uses Plan `cross_scene_intent`, not raw Bridge.

`stage_cards.source` is also a bounded projection. Do not render full raw `project_spec`, `character_system`, `entity_graph`, or `blueprint` directly in runtime writing/checking prompts; use `chapter_contract`, `relevant_entities`, `story_foundation`, `style_voice`, `creative_direction`, and StoryKernel-derived cards.

### Context Helper Naming

| Helper | Purpose |
|--------|---------|
| `fmt_list` | Join items with Chinese semicolon |
| `fmt_characters` | Render character states block |
| `fmt_events` | Numbered timeline events |
| `fmt_foreshadowing` | Active foreshadowing items |
| `fmt_relationships` | Character relationship lines |
| `fmt_plot_threads` | Active plot threads |
| `fmt_alignment` | Alignment report text |
| `fmt_repair` | Repair report text |
| `fmt_exit_state` | Chapter exit state block |
| `recommend_character_count` | Word count → character count guide |

## Anti-Patterns

- Never modify templates at runtime — loaded once, cached
- Never mix language-pack loaders with `ChoiceLoader`; each prompt pack must render through its
  own Jinja2 Environment so imports cannot cross locale boundaries.
- Never skip ComplianceGuard.check_prompt()
- Never create template without matching TaskFormatContract
- Never hand-maintain `prompts/INDEX.md`; regenerate it with `scripts/generate_prompt_index.py`
- Never add visible output-format blocks, `standard_json_output()`, `standard_text_output()`, `## 输出格式`, handwritten strict JSON instructions, or template-local JSON examples as protocol. Put required keys and schemas in `core/format_contracts.py`.
- Never let a step consumer parse a different shape than `TaskFormatContract` declares; use `audit_prompt_format_layers.py --all` to catch drift
- Never add styles in code — styles are LLM-derived via PROFILE_STYLE, not stored as template files

## Key Constants

- `CURRENT_VERSION`: "2.1.3"
- Min compatible: "1.0.0"
- Writing/planning/checking/initialization/canon/beats categories: "2.1.3"
