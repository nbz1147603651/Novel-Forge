#!/usr/bin/env python3
"""Lint TaskType provider routing against task_type_provider_recommendation.json.

Reads the recommendation file + current routing sources (.env, model_profiles.json)
and checks whether each TaskType's current provider matches the recommendation.

Modes:
    --check     Exit 1 if any TaskType routes to an "avoid" provider.
    --task T    Show lint result for a single TaskType only.
    --verbose   Show OK results in addition to warnings and errors.

Severity levels:
    ERROR       Current provider is in the "avoid" list.
    WARNING     Current provider is NOT in "recommended" and NOT in "avoid".
    OK          Current provider is in the "recommended" list.
    NOT_FOUND   TaskType has no recommendation entry.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ── Repo bootstrap ──────────────────────────────────────────────────────────
_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))

from novel_forge.common.constants import TaskType  # noqa: E402

# ── Constants ────────────────────────────────────────────────────────────────
_RECOMMENDATION_PATH = _repo_root / "config" / "task_type_provider_recommendation.json"
_CAPABILITY_PATH = _repo_root / "config" / "model_capability_profile.json"
_ENV_PATH = _repo_root / ".env"
_PROFILES_PATH = _repo_root / "model_profiles.json"


# ── Helpers ──────────────────────────────────────────────────────────────────
def _read_json(path: Path) -> dict:
    """Read a JSON file, returning {} on any error."""
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _read_env_file(env_path: Path) -> dict[str, str]:
    """Parse a simple .env file into key-value pairs."""
    if not env_path.is_file():
        return {}
    pairs: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        pairs[key.strip()] = value.strip()
    return pairs


def _strip_quotes(value: str) -> str:
    """Remove surrounding single/double quotes from a value."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_env_routing(env_pairs: dict[str, str]) -> dict[str, str]:
    """Parse NOVEL_FORGE_TASK_ROUTING from .env pairs."""
    raw = env_pairs.get("NOVEL_FORGE_TASK_ROUTING", "")
    if not raw:
        return {}
    raw = _strip_quotes(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _resolve_current_provider(
    task_type: TaskType,
    env_routing: dict[str, str],
    profiles: dict,
) -> str:
    """Resolve the current provider for a TaskType.

    Priority: model_profiles.json routes > .env routing.

    Uses TaskType.value (lowercase) for env/profiles lookup since both
    .env and model_profiles.json use lowercase keys.
    """
    task_key = task_type.value

    # 1. Check model_profiles.json routes (highest priority)
    profile_routes = profiles.get("routes", {})
    profile_route = profile_routes.get(task_key)
    if profile_route is not None:
        if isinstance(profile_route, dict):
            profile_id = profile_route.get("profile_id", "")
        else:
            profile_id = str(profile_route)
        if ":" in profile_id:
            return profile_id.split(":")[0]
        return profile_id

    # 2. Check .env NOVEL_FORGE_TASK_ROUTING
    env_route = env_routing.get(task_key)
    if env_route is not None:
        route_str = env_route if isinstance(env_route, str) else str(env_route)
        if ":" in route_str:
            return route_str.split(":")[0]
        return route_str

    return "unknown"


def _extract_provider_model(route_spec: str) -> str:
    """Extract provider:model from a route spec like 'provider:model' or 'provider:model,thinking'."""
    if not route_spec:
        return "unknown"
    # Handle comma-separated variants
    main = route_spec.split(",")[0].strip()
    return main if ":" in main else f"{main}:default"


def _check_context_window(
    profiles: dict,
    current_provider: str,
    min_tokens: int,
    capability_profile: dict,
) -> tuple[bool, str]:
    """Check if the current provider has enough context window.

    Returns (ok: bool, detail: str).
    """
    # Check capability profile first
    for model_key, model_info in capability_profile.get("models", {}).items():
        provider = model_key.split("/")[0] if "/" in model_key else ""
        if provider == current_provider:
            ctx = model_info.get("context_window_tokens", 0)
            if ctx < min_tokens:
                return False, f"context_window={ctx} < min={min_tokens}"
            return True, f"context_window={ctx} >= min={min_tokens}"

    # Check model_profiles.json for model info
    for p in profiles.get("profiles", []):
        if p.get("provider") == current_provider:
            ctx = p.get("context_window_tokens", 0)
            if ctx and ctx < min_tokens:
                return False, f"context_window={ctx} < min={min_tokens}"
            return True, ""
    return True, ""


# ── Lint logic ───────────────────────────────────────────────────────────────
def _lint_task_type(
    task_type: TaskType,
    recommendation: dict,
    env_routing: dict[str, str],
    profiles: dict,
    capability_profile: dict,
) -> dict:
    """Lint a single TaskType against its recommendation.

    Returns a result dict with: task, provider, route_spec, severity, message, recommendation.

    JSON keys use TaskType.name (uppercase, e.g. "INIT_STORY_BIBLE").
    Routing sources (.env, model_profiles.json) use TaskType.value (lowercase, e.g. "init_story_bible").
    """
    task_key = task_type.value  # lowercase key for routing sources
    rec = recommendation.get(task_type.name, recommendation.get(task_key, {}))

    # Resolve current routing
    current_provider = _resolve_current_provider(task_type, env_routing, profiles)

    # Resolve the full route spec
    profile_routes = profiles.get("routes", {})
    profile_route = profile_routes.get(task_key)
    if profile_route is not None:
        if isinstance(profile_route, dict):
            route_spec = profile_route.get("profile_id", "unknown")
        else:
            route_spec = str(profile_route)
    else:
        env_route = env_routing.get(task_key)
        route_spec = _extract_provider_model(env_route) if env_route else f"{current_provider}:default"

    if not rec:
        return {
            "task": task_key,
            "provider": current_provider,
            "route_spec": route_spec,
            "severity": "NOT_FOUND",
            "message": "No recommendation entry for this TaskType",
            "recommendation": None,
            "checks": {},
        }

    avoid_list = rec.get("avoid", [])
    recommended_list = rec.get("recommended", [])
    min_ctx = rec.get("min_context_window_tokens", 0)
    rationale = rec.get("rationale", "")
    tier = rec.get("tier", "unknown")

    checks: dict[str, str] = {}

    # Determine severity
    severity = "WARNING"
    message_parts: list[str] = []

    # Check avoid list
    for avoid_spec in avoid_list:
        avoid_provider = avoid_spec.split(":")[0] if ":" in avoid_spec else avoid_spec
        if current_provider == avoid_provider:
            severity = "ERROR"
            message_parts.append(f"Provider '{current_provider}' is in AVOID list")
            checks["avoid"] = f"FAIL: {avoid_spec} in avoid list"
            break
    else:
        checks["avoid"] = "pass"

    # Check recommended list
    if severity != "ERROR":
        recommended_providers = {r.split(":")[0] if ":" in r else r for r in recommended_list}
        if current_provider in recommended_providers:
            severity = "OK"
        else:
            message_parts.append(
                f"Provider '{current_provider}' not in recommended ({', '.join(recommended_providers)})"
            )
        checks["recommended"] = (
            "pass" if current_provider in recommended_providers
            else f"not in {recommended_providers}"
        )

    # Check context window
    if min_ctx > 0:
        ctx_ok, ctx_detail = _check_context_window(profiles, current_provider, min_ctx, capability_profile)
        checks["context_window"] = f"{'pass' if ctx_ok else 'FAIL'}: {ctx_detail}"
        if not ctx_ok and severity == "OK":
            severity = "WARNING"
            message_parts.append(f"Context window too small ({ctx_detail})")

    if not message_parts:
        message_parts.append("OK: provider in recommended list")

    return {
        "task": task_key,
        "provider": current_provider,
        "route_spec": route_spec,
        "severity": severity,
        "message": "; ".join(message_parts),
        "recommendation": {
            "tier": tier,
            "recommended": recommended_list,
            "avoid": avoid_list,
            "min_context_window_tokens": min_ctx,
            "rationale": rationale,
        },
        "checks": checks,
    }


# ── Report generation ───────────────────────────────────────────────────────
def _render_report(results: list[dict], *, verbose: bool = False) -> str:
    """Render lint results as a markdown report."""
    lines = [
        "# TaskType Provider Recommendation Lint",
        "",
        f"Generated for {_repo_root.name}",
        "",
        "## Summary",
        "",
    ]

    severities: dict[str, int] = {}
    for r in results:
        severities[r["severity"]] = severities.get(r["severity"], 0) + 1

    lines.append(f"- **Total TaskTypes with recommendations**: {len(results)}")
    for sev in ("ERROR", "WARNING", "OK", "NOT_FOUND"):
        if sev in severities:
            emoji = {"ERROR": "❌", "WARNING": "⚠️", "OK": "✅", "NOT_FOUND": "❓"}[sev]
            lines.append(f"- {emoji} **{sev}**: {severities[sev]}")

    error_count = severities.get("ERROR", 0)
    warning_count = severities.get("WARNING", 0)
    lines.append("")
    lines.append(f"**Result**: {error_count} errors, {warning_count} warnings")
    lines.append("")

    # Detail table
    lines.append("## Details")
    lines.append("")
    lines.append(
        "| TaskType | Current Provider | Route Spec | Severity | Message | Recommended | Avoid |"
    )
    lines.append(
        "|----------|-----------------|------------|----------|---------|-------------|-------|"
    )

    for r in results:
        sev = r["severity"]
        emoji = {"ERROR": "❌", "WARNING": "⚠️", "OK": "✅", "NOT_FOUND": "❓"}[sev]

        if sev == "OK" and not verbose:
            continue

        rec = r.get("recommendation") or {}
        recommended_str = ", ".join(rec.get("recommended", [])[:3])
        if len(rec.get("recommended", [])) > 3:
            recommended_str += " ..."
        avoid_str = ", ".join(rec.get("avoid", [])[:3])
        if len(rec.get("avoid", [])) > 3:
            avoid_str += " ..."

        message = r["message"][:80] + ("..." if len(r["message"]) > 80 else "")

        lines.append(
            f"| `{r['task']}` | `{r['provider']}` | `{r['route_spec']}` "
            f"| {emoji} {sev} | {message} | {recommended_str} | {avoid_str} |"
        )

    lines.append("")

    # Recommendations reference
    if verbose:
        lines.append("## Recommendation Details")
        lines.append("")
        for r in results:
            rec = r.get("recommendation")
            if not rec:
                continue
            lines.append(f"### `{r['task']}`")
            lines.append(f"- **Tier**: {rec.get('tier', '?')}")
            lines.append(f"- **Recommended**: {', '.join(rec.get('recommended', []))}")
            lines.append(f"- **Avoid**: {', '.join(rec.get('avoid', []))}")
            lines.append(f"- **Min Context Window**: {rec.get('min_context_window_tokens', 0):,} tokens")
            lines.append(f"- **Rationale**: {rec.get('rationale', 'N/A')}")
            lines.append("")

    return "\n".join(lines)


def _find_tasktype(name: str) -> TaskType:
    """Find a TaskType by name (case-insensitive)."""
    name_lower = name.lower()
    for tt in TaskType:
        if tt.value == name_lower or tt.name.lower() == name_lower:
            return tt
    raise ValueError(f"Unknown TaskType: {name}")


# ── Main ─────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any TaskType routes to an 'avoid' provider.",
    )
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Show lint result for a single TaskType (e.g. PLAN_OUTLINE).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show OK results and full recommendation details.",
    )
    args = parser.parse_args(argv)

    # Load data sources
    recommendations = _read_json(_RECOMMENDATION_PATH).get("recommendations", {})
    capability_profile = _read_json(_CAPABILITY_PATH)
    env_pairs = _read_env_file(_ENV_PATH)
    env_routing = _parse_env_routing(env_pairs)
    profiles = _read_json(_PROFILES_PATH)

    if not recommendations:
        print("ERROR: No recommendations found in task_type_provider_recommendation.json", file=sys.stderr)
        return 2

    # Determine tasks to lint
    if args.task:
        try:
            target_task = _find_tasktype(args.task)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        tasks_to_lint = [target_task]
    else:
        # Lint all TaskTypes that have recommendations
        # JSON uses TaskType.name (uppercase) as keys
        tasks_to_lint = [
            tt for tt in TaskType if tt.name in recommendations or tt.value in recommendations
        ]

    # Run lint
    results: list[dict] = []
    for tt in tasks_to_lint:
        result = _lint_task_type(tt, recommendations, env_routing, profiles, capability_profile)
        results.append(result)

    # Sort by severity (ERROR first)
    severity_order = {"ERROR": 0, "WARNING": 1, "OK": 2, "NOT_FOUND": 3}
    results.sort(key=lambda r: severity_order.get(r["severity"], 99))

    # Output
    print(_render_report(results, verbose=args.verbose))

    # --check mode
    if args.check:
        error_count = sum(1 for r in results if r["severity"] == "ERROR")
        if error_count > 0:
            print(f"\n❌ CHECK FAILED: {error_count} TaskType(s) route to avoid-listed providers.")
            for r in results:
                if r["severity"] == "ERROR":
                    rec = r.get("recommendation") or {}
                    print(f"   - `{r['task']}` → `{r['provider']}` (avoid: {rec.get('avoid', [])})")
            return 1
        print("\n✅ CHECK PASSED: No TaskType routes to an avoid-listed provider.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
