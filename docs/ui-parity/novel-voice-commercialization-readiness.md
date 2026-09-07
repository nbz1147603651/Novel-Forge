# Novel and voice commercialization readiness

Last verified: 2026-08-31

## Decision

Nimo now covers the core local-desktop novel workflow through the shared Engine,
but it is not yet safe to declare unconditional PySide retirement or public-cloud
commercial readiness. The remaining gap is no longer the basic novel pipeline;
it is production proof, interrupted-task recovery for the remaining synchronous
model operations, native delivery behavior, and hosted identity isolation.

The controlled local-desktop cutover is now active: `nimo` is the primary
React/Tauri entry and `nimo-p` keeps PySide as a frozen recovery/reference
fallback. This advances the default UI without claiming the deletion or public
commercialization gates below are complete.

## Audited flows

| Flow | Engine ownership | Nimo status | Commercial note |
| --- | --- | --- | --- |
| Short story | `run_short` durable job and persisted artifacts | Engine-backed | Real-provider and restart E2E still required. |
| Long initialization | `init_long`, continue/restart, retry and versioned manual repair | Engine-backed | Initialization admission and artifact repair no longer require PySide. |
| Long chapter | prepare, DRAFT/WAVE, review/repair, polish, humanize, finalize, checkpoint and book autorun | Engine-backed | Durable checkpoint/job recovery exists; native interrupted-run proof remains a release gate. |
| Whole-book consistency | read-only global audit plus precision repair queue | Engine-backed | Nimo can now explicitly execute the latest `ready` queue with source verification and rollback. |
| Publication editorial audit | editorial-contract audit and manual-review revision queue | Engine-backed | Report is visible under the reader's whole-book governance group; prose is not silently mutated. |
| Final rewrite | selection candidate, optimistic revision save, invalidation and reevaluation | Engine-backed | Candidate generation is still a synchronous long-running model request. |
| Humanize | library mutation plus chapter humanize/finalize chain | Engine-backed | Final verification status remains the authority after a revision. |
| Voice | cast/script/room/post/settings plus durable core synthesis and delivery | Engine-backed | Several preview/design/take operations remain synchronous and need interruption testing. |

## Corrections made in this audit

1. Unified TTS lineage checks accept the canonical full SHA-256 and the legacy
   compact 16-character form. Fresh audio is no longer falsely classified as
   stale by the publication view.
2. Voice Studio now derives script and audio freshness from the current novel
   text and current script identity. A rewritten or publication-blocked chapter
   disables synthesis, playback, reassembly, and delivery until regeneration.
3. Added a durable `global_repair_queue` Engine command and Nimo confirmation
   surface. The commercial UI fixes `statuses=[ready]`, source verification, and
   rollback as mandatory safety gates.
4. Added the publication-level `book_editorial_audit` durable command, Nimo
   surface, task artifacts, and reader document projection.
5. Removed the hard-coded chapter 1–4 scope from audit/export dialogs. Their
   selectable range now comes from actually archived chapters.
6. Removed the fake export-directory choice. Nimo now states the real Engine
   delivery boundary: the project `exports` directory.

## PySide retirement gates

The following must be green before deleting the old UI:

1. Native Tauri E2E with real workspaces for short generation, long init,
   chapter checkpoint/resume, both book audits, global repair, manual revision,
   voice regeneration after revision, and chapter/audiobook export.
2. Network interruption and application-restart tests for SSE wake-up ordering,
   cursor snapshots, cancellation, retry, and idempotent resubmission.
3. Promote remaining long-running synchronous model calls—notably selection
   revision candidate and selected voice design/preview/take operations—to
   cancellable durable jobs where restart recovery is required.
4. Add a bounded novel-export download/reveal contract for remote Engine mode;
   a server filesystem path is not a cloud delivery contract.
5. Complete the configured real-provider matrix, including TTS provider/model
   compatibility, voice-clone authorization, and commercial delivery evidence.
6. For a hosted product, add account, tenant, authorization, quota, billing,
   audit-log retention, and data-deletion boundaries. The current bearer token
   is appropriate for a controlled deployment, not a multi-tenant service.
7. Execute a single-stack cutover: backup representative projects, run the
   contract-consumer suite, freeze PySide writes, compare Nimo read models, and
   only then remove PySide entrypoints.

## Recommended launch sequence

- Current: controlled local-desktop NIMO primary with PySide retained only as
  the explicit `nimo-p` recovery/reference path.
- Next: native Tauri release after the real-workspace and restart matrix passes.
- Last: public-cloud commercialization after tenant security and bounded novel
  delivery are implemented.
