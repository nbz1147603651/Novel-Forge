# NIMO Desktop Architecture

Current decision (2026-08-31): React/Tauri NIMO is the primary desktop and the
only target for new product UI work. The parity fixtures in this directory
remain regression and migration evidence; they no longer make PySide the
default entry or require new interactions to be implemented there first.

## Purpose

The React + TypeScript + Tauri client consumes the versioned Engine read/command
boundary. Python pipeline execution, storage, model routing, authorization and
provider keys remain server-owned. PySide is retained as a frozen safety,
compatibility, regression-reference and emergency fallback client.

## Non-negotiable boundaries

- The Engine contracts and backend authority are the behavior source of truth;
  the PySide6 baselines remain migration/reference evidence.
- New product interactions and visual work are implemented in NIMO, not
  duplicated in PySide.
- React feature components depend only on `@nimo/engine-contracts` and never
  import Python, access `.env`, or make direct provider requests.
- The NIMO mark is governed by [brand-contract.md](brand-contract.md): web,
  native window, and macOS Dock icons must use the same source SVG and semantic
  theme color.
- Deterministic parity fixtures use `MockEngineClient`; `nimo` uses the
  capability-negotiated HTTP Engine adapter and never silently falls back to
  Mock when the backend is unavailable or incompatible.
- `nimo-p` remains operable for safety and compatibility, but is frozen against
  new product surfaces and client-owned business state.

## Dual UI entry points

- `nimo`: primary React/Tauri client. For a loopback URL it starts and owns the local
  FastAPI Engine unless `--no-backend` is supplied.
- `nimo-t`: compatibility alias for saved scripts that launches the same client.
- `nimo-p`: frozen PySide6 fallback.
- `nimo --backend-url https://engine.example --no-backend`: connect the same
  client to a cloud Engine. Remote HTTP is rejected unless explicitly allowed.

The Tauri launcher injects mode, URL and optional bearer token at document
start. The frontend does not persist the token or compile it into the bundle.

## Fidelity gates

1. Capture deterministic PySide6 screenshots for every surface in
   `surface-index.yml`.
2. Reproduce desktop shell before building individual pages.
3. Compare the same fixture, theme, viewport, and interaction state before
   accepting a React surface.
4. Fix shared tokens/components before writing page-local CSS overrides.
5. Record every intentional deviation in `fidelity-ledger.md`; unrecorded
   visual differences are defects.

## Target surfaces

The six registered primary pages are `dashboard`, `projects`, `workflow`,
`settings`, `chapter_studio`, and `voice_studio`. Secondary surfaces are tracked
separately in the manifest because users reach them from the primary pages.

两端长期共存时的职责、替换边界和更新流程见
[dual-ui-maintenance.md](dual-ui-maintenance.md)。

## Verification strategy

- Unit/interaction: Vitest + Testing Library.
- Browser layout: Playwright at the PySide6 reference viewport.
- Desktop release validation: Tauri WebView on macOS.
- Visual comparison: deterministic fixtures, frozen time, and explicit masks
  for nondeterministic regions only.
