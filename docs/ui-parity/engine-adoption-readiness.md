# Engine adapter readiness

Last verified: 2026-08-31

## Current decision

The new UI now has two explicit modes behind one composition boundary:

- `mock`: deterministic visual/interaction fixtures only.
- `legacy` (HTTP): the real local or cloud Python Engine. Startup performs
  contract/capability negotiation and never silently falls back to Mock.

`LegacyLocalEngineClient` is a transport name retained for compatibility; it
does not imply local-only deployment. The same adapter accepts a document-start
HTTPS base URL and ephemeral bearer token for cloud operation.

React/Tauri NIMO is now the default desktop entry. This is a controlled
primary-UI decision with `nimo-p` retained as a frozen fallback; it is not a
claim that every remaining native comparison or eventual PySide removal gate
has completed.

## Implemented backend boundary

`/api/v1/engine` supplies:

| Area | Implemented |
| --- | --- |
| Negotiation | Contract/API versions, module capabilities, features and command flags |
| Read models | Workspace, settings, workflow, project reader, narrative tools, chapter studio, voice studio |
| Jobs | Durable records, snapshots, cancellation, resume/retry, and persisted cancellation reasons. Long-running voice-team, script, synthesis, full-pipeline, and delivery jobs retain their task-stream state across restart. |
| Commands | Settings and routing use the sole `save-settings` Engine command (profile registry, route validation, allowlisted runtime parameters and hot reload); non-persistent model-profile probes and task-error archive cleanup; workflow draft/preset/history; long-init; chapter preparation, repair, consistency audit, publication editorial audit, guarded global repair queue, export, cleanup, and memory rebuild; reader revision; narrative subplot/character/relationship edits; humanize library edits; and Voice Studio control actions. |
| Voice clone reference | The Engine provider catalog declares whether the configured model accepts local reference audio. Nimo uses its native picker only for a loopback Engine and sends the selected absolute path to the protected clone command; providers that require an upstream file use their File ID instead. |
| Audio delivery | Chapter/book audio and audiobook exports submit durable Engine jobs. On completion, task-stream projection exposes only a validated delivery filename and download URL; Nimo observes, cancels, retries, and downloads through that bounded view. |
| Novel→voice lineage | Voice Studio compares both compact and full source hashes against the current publishable chapter. Revisions make old scripts/audio visibly stale and block synthesis, playback, and delivery until regeneration. |
| Streaming | Cursor-bearing task snapshots are canonical. The durable job SSE channel is used to wake observers; clients re-read the authoritative snapshot rather than treating a UI timer as task state. |

Python responses use the shared camelCase boundary and have cross-language
contract tests. Capabilities distinguish durable jobs from synchronous Engine
operations; Nimo must render the latter as synchronous controls rather than
inventing local background state.

## Local and cloud transport

`nimo` defaults to a managed loopback Engine. It waits for `/health`, starts
the client only after readiness, and stops only the backend process it created.
`nimo-t` remains a compatibility alias for existing scripts.

For cloud operation:

```bash
nimo \
  --backend-url https://engine.example.com \
  --access-token "$NIMO_ENGINE_ACCESS_TOKEN" \
  --no-backend
```

Remote deployment must also configure:

```text
NOVEL_FORGE_API_LOCAL_ONLY=false
NOVEL_FORGE_API_TRUSTED_HOSTS=engine.example.com
NOVEL_FORGE_API_CORS_ALLOW_ORIGINS=<exact deployed web/Tauri origin>
NOVEL_FORGE_API_ACCESS_TOKEN=<secret>
```

Remote plain HTTP is rejected by the launcher unless the operator makes the
insecure override explicit. Tokens are injected into WebView memory at document
start and are neither persisted nor compiled into assets.

## Remaining production gates

1. Audit newly added Engine operations for genuinely long-running work before
   shipping them as synchronous HTTP handlers. Promote only those that need
   restart recovery and task-stream projection; do not mark a command durable
   merely to animate a client-side progress indicator.
2. Finish reconnect and ordering coverage for the SSE wake-up plus
   cursor-snapshot protocol under interrupted network and application restart.
3. Move any newly discovered PySide-only mutation behind a versioned Engine
   command before porting its UI. Existing reader revision, narrative editing,
   cleanup, consistency/editorial book audits, guarded global repair, export,
   and Voice Studio controls are Engine-backed and must not regain a local-only
   fallback.
4. Add multi-user identity/tenant authorization before public cloud exposure;
   the current bearer-token boundary is suitable for controlled deployments,
   not a complete hosted account system.
5. Finish same-state native Tauri visual comparisons. Chromium snapshots remain
   regression evidence, not proof of PySide6 pixel acceptance.
6. Retire PySide only in an explicit single-stack migration after all production
   surfaces have Engine-backed control/observation paths, the consumer suite is
   green, and the native comparison evidence is recorded. Until then it remains
   the behavior reference, not a duplicate state owner.
