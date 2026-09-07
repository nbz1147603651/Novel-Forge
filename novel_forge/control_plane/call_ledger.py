"""Call ledger for per-model-call artifact manifest recording.

Computes prompt_hash and response_hash from router events and records
them in the control plane's ArtifactManifest. This complements the existing
``ProjectRunLogger.record_router_event`` which writes full request/response
JSON to ``logs/<run_id>/model_calls/`` - the call ledger adds the
content-addressed hash and links calls to stage executions.

Design:
- :func:`compute_prompt_hash` computes SHA-256 of the rendered messages.
- :func:`compute_response_hash` computes SHA-256 of the response payload.
- :class:`CallLedger` wraps a :class:`StageRecorder` and records each call.
- All methods are fire-and-forget (swallow exceptions).
- When the control plane is disabled, behaves as pure no-op.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from novel_forge.control_plane.enums import ArtifactKind
from novel_forge.control_plane.stage_recorder import _compute_sha256, _hash_json

if TYPE_CHECKING:
    from novel_forge.control_plane.stage_recorder import StageRecorder

_log = logging.getLogger("novel_forge.control_plane.call_ledger")


# ---------------------------------------------------------------------------
# Hash computation
# ---------------------------------------------------------------------------


def compute_prompt_hash(messages: list[dict[str, Any]] | Any) -> str:
    """Compute SHA-256 of the rendered prompt messages.

    The messages list is JSON-serialized with sorted keys for determinism.
    """
    if messages is None:
        return ""
    return _hash_json(messages)


def compute_response_hash(response: Any) -> str:
    """Compute SHA-256 of the model response payload."""
    if response is None:
        return ""
    if isinstance(response, str):
        return _compute_sha256(response)
    return _hash_json(response)


# ---------------------------------------------------------------------------
# CallLedger
# ---------------------------------------------------------------------------


class CallLedger:
    """Records per-model-call artifacts in the control plane manifest.

    Each call records: prompt_hash, response_hash, template_version,
    task_type, route, model_params. This creates an immutable, queryable
    record of every LLM invocation, linked to the stage that produced it.
    """

    def __init__(self, recorder: StageRecorder | None) -> None:
        self._recorder = recorder

    @property
    def enabled(self) -> bool:
        return self._recorder is not None and self._recorder.enabled

    def record_call(
        self,
        *,
        project_id: str = "",
        task_type: str = "",
        route: str = "",
        provider: str = "",
        model: str = "",
        messages: list[dict[str, Any]] | Any = None,
        response: Any = None,
        template_version: str = "",
        model_params: dict[str, Any] | None = None,
        validation_result: dict[str, Any] | None = None,
        content_path: str = "",
        run_attempt_id: str | None = None,
        stage_execution_id: str | None = None,
        call_id: str = "",
    ) -> str | None:
        """Record a single model call in the artifact manifest.

        Returns the artifact_id, or None if disabled or on failure.
        """
        if not self.enabled:
            return None
        try:
            from novel_forge.control_plane.context import get_current_execution_context

            execution_context = get_current_execution_context()
            project_id = project_id or (
                execution_context.project_id if execution_context is not None else ""
            )
            run_attempt_id = run_attempt_id or (
                execution_context.run_attempt_id if execution_context is not None else None
            )
            prompt_hash = compute_prompt_hash(messages)
            response_hash = compute_response_hash(response)

            # Build a composite hash for the artifact's sha256 field:
            # combines prompt + response + task + route for uniqueness
            composite = {
                "prompt_hash": prompt_hash,
                "response_hash": response_hash,
                "task_type": task_type,
                "route": route,
                "call_id": call_id,
            }
            sha256 = _hash_json(composite)

            params = dict(model_params or {})
            if provider:
                params["provider"] = provider
            if model:
                params["model"] = model
            if run_attempt_id:
                # ArtifactManifest links directly to a stage execution when
                # one is known.  A model call can happen before/after a stage
                # boundary, so retain its owning attempt in metadata as well.
                params["run_attempt_id"] = run_attempt_id

            return self._recorder.record_artifact(  # type: ignore[union-attr]
                sha256=sha256,
                artifact_kind=ArtifactKind.MODEL_CALL,
                project_id=project_id,
                content_path=content_path,
                source_artifact_hashes={"prompt": prompt_hash} if prompt_hash else {},
                task_type=task_type,
                route=route,
                prompt_hash=prompt_hash,
                response_hash=response_hash,
                template_version=template_version,
                model_params=params,
                validation_result=validation_result or {},
                created_by_stage_execution_id=stage_execution_id,
            )
        except Exception:
            _log.exception("CallLedger.record_call failed")
            return None

    def record_router_event(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        project_id: str = "",
        run_attempt_id: str | None = None,
        stage_execution_id: str | None = None,
    ) -> None:
        """Process a router event and record the call when it completes.

        Called from the observer pattern. On ``api_call_start`` events,
        stores the request. On ``api_call_done`` / ``api_call_error`` events,
        records the full call with both prompt and response hashes.
        """
        if not self.enabled:
            return

        if event in {"api_call_start", "api_stream_start"}:
            # Just note the start - the actual recording happens on done/error
            return

        if event not in {
            "api_call_done",
            "api_stream_done",
            "api_call_error",
            "api_stream_error",
            "api_call_timeout",
            "api_call_incomplete",
        }:
            return

        try:
            from novel_forge.control_plane.context import get_current_execution_context

            execution_context = get_current_execution_context()
            project_id = project_id or (
                execution_context.project_id if execution_context is not None else ""
            )
            run_attempt_id = run_attempt_id or (
                execution_context.run_attempt_id if execution_context is not None else None
            )
            # Extract request and response from the combined payload
            request = payload.get("request") or payload.get("routed_request") or {}
            response = payload.get("response") or {}

            messages = request.get("messages") if isinstance(request, dict) else None
            task_type = str(payload.get("task") or "")
            provider = str(payload.get("provider") or "")
            model = str(payload.get("model") or "")
            route = str(payload.get("route") or f"{provider}:{model}" if provider or model else "")
            call_id = str(payload.get("call_id") or "")

            # Extract model params
            model_params: dict[str, Any] = {}
            for key in ("max_tokens", "temperature", "top_p", "thinking"):
                val = payload.get(key) if isinstance(payload, dict) else None
                if val is not None:
                    model_params[key] = val
            if isinstance(request, dict):
                for key in ("max_tokens", "temperature", "top_p", "thinking"):
                    val = request.get(key)
                    if val is not None and key not in model_params:
                        model_params[key] = val

            # Validation result from structured output
            validation_result: dict[str, Any] = {}
            if isinstance(response, dict):
                for key in (
                    "structured_output_mode",
                    "structured_output_downgraded_from",
                    "structured_output_reason",
                ):
                    val = response.get(key)
                    if val:
                        validation_result[key] = val

            self.record_call(
                project_id=project_id,
                task_type=task_type,
                route=route,
                provider=provider,
                model=model,
                messages=messages,
                response=response,
                model_params=model_params,
                validation_result=validation_result,
                run_attempt_id=run_attempt_id,
                stage_execution_id=stage_execution_id,
                call_id=call_id,
            )
        except Exception:
            _log.exception("CallLedger.record_router_event failed for event %s", event)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def get_call_ledger(settings: Any) -> CallLedger:
    """Create a CallLedger bound to the control-plane store (or disabled)."""
    from novel_forge.control_plane.stage_recorder import get_stage_recorder

    return CallLedger(get_stage_recorder(settings))
