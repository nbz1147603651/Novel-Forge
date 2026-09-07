# Novel Forge Desktop Pet Standard

Novel Forge Desktop pets use a Codex-compatible atlas package with a single
manifest per pet. New pets should be added as data, not by changing window or
task-observation code.

## Package Layout

Place each pet under:

```text
novel_forge/desktop/resources/pets/<pet_id>/
  pet.json
  spritesheet.webp      # preferred animated atlas
  sprite.png            # fallback still image
  source.png            # optional provenance/reference image
```

`pet_id` must be lowercase kebab-case or snake_case and stable across releases.

## Atlas Standard

Use `standard: "codex-atlas-v1"` in `pet.json`.

The animated atlas must use:

- Columns: `8`
- Rows: `9`
- Cell size: `192 x 208`
- Total image size: `1536 x 1872`
- Format: `webp` preferred, `png` accepted for development
- Transparent background in every unused pixel
- Unused cells in a row must be fully transparent

Standard row order:

| Row | State | Frames | Purpose |
| --- | --- | ---: | --- |
| 0 | `idle` | 6 | neutral breathing/blinking loop |
| 1 | `running-right` | 8 | rightward locomotion loop |
| 2 | `running-left` | 8 | leftward locomotion loop |
| 3 | `waving` | 4 | greeting gesture |
| 4 | `jumping` | 5 | anticipation, lift, peak, descent, settle |
| 5 | `failed` | 8 | sad or deflated reaction |
| 6 | `waiting` | 6 | patient waiting loop |
| 7 | `running` | 6 | active working/in-progress loop |
| 8 | `review` | 6 | focused inspecting or review loop |

Novel Forge may define semantic aliases in `states` that point to these rows.
For example, `decision` can point to the `review` row and `paused` can point to
the `waiting` row.

## Manifest

Minimum `pet.json`:

```json
{
  "schema_version": 2,
  "standard": "codex-atlas-v1",
  "pet_id": "nimo",
  "display_name": "Nimo",
  "description": "A short user-facing description.",
  "assets": {
    "spritesheet": "spritesheet.webp",
    "sprite": "sprite.png",
    "source": "source.png"
  },
  "atlas": {
    "columns": 8,
    "rows": 9,
    "cell_width": 192,
    "cell_height": 208,
    "row_order": [
      {"state": "idle", "row": 0, "frames": 6, "fps": 6},
      {"state": "running-right", "row": 1, "frames": 8, "fps": 8},
      {"state": "running-left", "row": 2, "frames": 8, "fps": 8},
      {"state": "waving", "row": 3, "frames": 4, "fps": 6},
      {"state": "jumping", "row": 4, "frames": 5, "fps": 7},
      {"state": "failed", "row": 5, "frames": 8, "fps": 6},
      {"state": "waiting", "row": 6, "frames": 6, "fps": 5},
      {"state": "running", "row": 7, "frames": 6, "fps": 7},
      {"state": "review", "row": 8, "frames": 6, "fps": 5}
    ]
  }
}
```

`sprite` is required as a fallback so the UI remains usable while an animated
atlas is missing, being repaired, or disabled by reduced-motion settings.

## Visual Rules

Pet art should match Codex-style digital pets:

- Pixel-art-adjacent, compact chibi proportions
- Thick dark 1-2 px outline
- Limited palette and flat cel shading
- Simple readable face and silhouette at small sizes
- No text, UI panels, loose symbols, scenery, cast shadows, glow, or detached effects
- Effects, when needed, must touch or overlap the pet silhouette and stay inside one cell

## Runtime Mapping

The Desktop task companion maps task state to pet state:

- Active task: `running`
- Human decision needed: `decision` alias, normally row `review`
- Paused task: `paused` alias, normally row `waiting`
- Failure states may use `failed`
- Reduced-motion or missing atlas falls back to the still `sprite`

## Validation

Before adding a new animated pet:

1. Verify the atlas dimensions are exactly `1536 x 1872`.
2. Verify each used cell has visible pixels and unused cells are transparent.
3. Verify every row preserves the same identity, palette, silhouette, and prop design.
4. Run the Desktop pet unit tests.
5. Inspect the pet at both normal and compact companion sizes.

Command:

```bash
.venv/bin/python scripts/validate_desktop_pets.py
```

Use this for release-ready animated pets:

```bash
.venv/bin/python scripts/validate_desktop_pets.py --require-atlas
```
