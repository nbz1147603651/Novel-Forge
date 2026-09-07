#!/usr/bin/env python3
"""Audit task routing configuration across .env and model_profiles.json.

Reports the effective route (provider:model) for every TaskType, including
the source that determined the route (.env > model_profiles.json > default tier).

Modes:
    --check   Exit 1 if any high-compliance target tasks route to minimax.
    --verify  Runtime verification via ModelRouter.resolve_model_id_for_task.
    --task T  Show routing for a single TaskType only.
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
from novel_forge.core.task_catalog import DEFAULT_TASK_TIERS  # noqa: E402

# ── Constants ────────────────────────────────────────────────────────────────
_HIGH_COMPLIANCE_TARGETS: frozenset[TaskType] = frozenset(
    {
        TaskType.REPAIR_INIT_ARTIFACT_PATCH,
        TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
        TaskType.DERIVE_EDITORIAL_CONTRACT,
        TaskType.PLAN_OUTLINE,
    }
)

_ENV_KEYS = ("NOVEL_FORGE_TASK_ROUTING", "NOVEL_FORGE_TASK_FALLBACK_ROUTING")
_PROFILES_FILENAME = "model_profiles.json"


# ── Helpers ──────────────────────────────────────────────────────────────────
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


def _parse_env_routing(env_pairs: dict[str, str]) -> dict[str, dict]:
    """Parse NOVEL_FORGE_TASK_ROUTING from .env pairs."""
    raw = env_pairs.get("NOVEL_FORGE_TASK_ROUTING", "")
    if not raw:
        return {}
    raw = _strip_quotes(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _parse_env_fallback_routing(env_pairs: dict[str, str]) -> dict[str, list]:
    """Parse NOVEL_FORGE_TASK_FALLBACK_ROUTING from .env pairs."""
    raw = env_pairs.get("NOVEL_FORGE_TASK_FALLBACK_ROUTING", "")
    if not raw:
        return {}
    raw = _strip_quotes(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _load_profiles(profiles_path: Path) -> dict:
    """Load model_profiles.json if it exists."""
    if not profiles_path.is_file():
        return {}
    try:
        return json.loads(profiles_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _resolve_route_for_task(
    task_type: TaskType,
    env_routing: dict[str, dict],
    env_fallback: dict[str, list],
    profiles: dict,
) -> dict:
    """Determine the effective route for a single TaskType.

    Priority: .env routing > model_profiles.json routes > default tier.
    """
    task_key = task_type.value
    task_key_lower = task_key.lower()

    # 1. Check .env NOVEL_FORGE_TASK_ROUTING (case-insensitive lookup)
    env_route = env_routing.get(task_key) or env_routing.get(task_key_lower)
    if env_route is not None:
        route_str = env_route if isinstance(env_route, str) else json.dumps(env_route)
        return {
            "route": route_str,
            "source": ".env (NOVEL_FORGE_TASK_ROUTING)",
            "provider": _extract_provider(route_str),
        }

    # 2. Check model_profiles.json routes
    profile_routes = profiles.get("routes", {})
    profile_route = profile_routes.get(task_key) or profile_routes.get(task_key_lower)
    if profile_route is not None:
        profile_id = profile_route.get("profile_id", "") if isinstance(profile_route, dict) else ""
        return {
            "route": profile_id or str(profile_route),
            "source": "model_profiles.json (routes)",
            "provider": _profile_id_to_provider(profile_id, profiles),
        }

    # 3. Default tier → model mapping
    tier = DEFAULT_TASK_TIERS.get(task_type)
    tier_name = tier.value if tier else "unknown"
    return {
        "route": f"tier:{tier_name}",
        "source": "DEFAULT_TASK_TIERS (fallback)",
        "provider": f"tier:{tier_name}",
    }


def _extract_provider(route_str: str) -> str:
    """Extract provider name from a route spec like 'provider:model,thinking'."""
    if not route_str or ":" not in route_str:
        return route_str
    return route_str.split(":")[0].strip()


def _profile_id_to_provider(profile_id: str, profiles: dict) -> str:
    """Map a profile_id to its provider name from model_profiles.json."""
    if not profile_id:
        return ""
    for p in profiles.get("profiles", []):
        if p.get("profile_id") == profile_id:
            return p.get("provider", "")
    return profile_id


# ── Runtime verification ────────────────────────────────────────────────────
def _verify_runtime(task_type: TaskType | None = None) -> list[dict]:
    """Use ModelRouter.resolve_model_id_for_task for runtime verification."""
    try:
        from novel_forge.core.config import get_settings  # noqa: E402
        from novel_forge.gateway.factory import ModelRouterBuilder  # noqa: E402

        settings = get_settings()
        builder = ModelRouterBuilder(settings)
        router = builder.build()

        tasks_to_check = [task_type] if task_type else list(TaskType)
        results: list[dict] = []
        for tt in tasks_to_check:
            try:
                model_id = router.resolve_model_id_for_task(tt)
                overrides = router.task_route_overrides
                provider = "unknown"
                if tt in overrides:
                    override = overrides[tt]
                    provider = override.provider
                    model_id = override.model_id or model_id

                results.append(
                    {
                        "task": tt.value,
                        "provider": provider,
                        "model_id": model_id or "default",
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "task": tt.value,
                        "provider": "error",
                        "model_id": str(exc),
                    }
                )
        return results
    except Exception as exc:
        return [{"task": "ERROR", "provider": "error", "model_id": str(exc)}]


# ── Report generation ───────────────────────────────────────────────────────
def _render_table(
    routing_results: list[dict],
    *,
    verify_results: list[dict] | None = None,
    show_all: bool = True,
) -> str:
    """Render routing results as a markdown table."""
    verify_map: dict[str, dict] = {}
    if verify_results:
        for vr in verify_results:
            verify_map[vr["task"]] = vr

    lines = [
        "# Routing Configuration Audit",
        "",
        f"Generated for {_repo_root.name}",
        "",
        "## Summary",
        "",
    ]

    # Count sources
    source_counts: dict[str, int] = {}
    minimax_count = 0
    for r in routing_results:
        source_counts[r["source"]] = source_counts.get(r["source"], 0) + 1
        if "minimax" in r.get("provider", "").lower():
            minimax_count += 1

    for source, count in sorted(source_counts.items()):
        lines.append(f"- **{source}**: {count} tasks")
    lines.append(f"- **Total tasks**: {len(routing_results)}")
    lines.append(f"- **Tasks routed to minimax**: {minimax_count}")
    lines.append("")

    # High-compliance targets
    lines.append("## High-Compliance Target Tasks")
    lines.append("")
    lines.append(
        "| TaskType | Route | Provider | Source | Runtime Verify |"
    )
    lines.append(
        "|----------|-------|----------|--------|----------------|"
    )
    for r in routing_results:
        tt = r["task_type"]
        if tt not in _HIGH_COMPLIANCE_TARGETS:
            continue
        vr = verify_map.get(r["task"], {})
        runtime = f"{vr.get('provider', '?')}:{vr.get('model_id', '?')}" if vr else "N/A"
        flag = " ⚠️ MINIMAX" if "minimax" in r.get("provider", "").lower() else ""
        lines.append(
            f"| `{r['task']}` | `{r['route']}` | `{r['provider']}{flag}` | "
            f"`{r['source']}` | `{runtime}` |"
        )
    lines.append("")

    if show_all:
        lines.append("## All TaskType Routes")
        lines.append("")
        lines.append(
            "| TaskType | Route | Provider | Source | Runtime Verify |"
        )
        lines.append(
            "|----------|-------|----------|--------|----------------|"
        )
        for r in routing_results:
            vr = verify_map.get(r["task"], {})
            runtime = f"{vr.get('provider', '?')}:{vr.get('model_id', '?')}" if vr else "N/A"
            flag = " ⚠️" if "minimax" in r.get("provider", "").lower() else ""
            lines.append(
                f"| `{r['task']}` | `{r['route']}` | `{r['provider']}{flag}` | "
                f"`{r['source']}` | `{runtime}` |"
            )
        lines.append("")

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any high-compliance target tasks route to minimax.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Runtime verification via ModelRouter.resolve_model_id_for_task.",
    )
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Show routing for a single TaskType (e.g. PLAN_OUTLINE).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include the full per-task matrix (default when no --task).",
    )
    parser.add_argument(
        "--lint-providers",
        action="store_true",
        help="Run provider recommendation lint (lint_task_type_provider.py) after the audit.",
    )
    args = parser.parse_args(argv)

    # Locate config files
    env_path = _repo_root / ".env"
    profiles_path = _repo_root / _PROFILES_FILENAME

    # Parse sources
    env_pairs = _read_env_file(env_path)
    env_routing = _parse_env_routing(env_pairs)
    env_fallback = _parse_env_fallback_routing(env_pairs)
    profiles = _load_profiles(profiles_path)

    # Determine which tasks to audit
    if args.task:
        try:
            target_task = TaskType(args.task.lower())
        except ValueError:
            print(f"ERROR: Unknown TaskType '{args.task}'", file=sys.stderr)
            return 2
        tasks_to_audit = [target_task]
        show_all = False
    else:
        tasks_to_audit = list(TaskType)
        show_all = args.all

    # Resolve routes
    routing_results: list[dict] = []
    for tt in tasks_to_audit:
        info = _resolve_route_for_task(tt, env_routing, env_fallback, profiles)
        routing_results.append({"task": tt.value, "task_type": tt, **info})

    # Runtime verification
    verify_results: list[dict] | None = None
    if args.verify:
        verify_task = None
        if args.task:
            try:
                verify_task = TaskType(args.task.lower())
            except ValueError:
                pass
        verify_results = _verify_runtime(verify_task)

    # Output
    print(_render_table(routing_results, verify_results=verify_results, show_all=show_all))

    # --check mode: exit 1 if any high-compliance target routes to minimax
    if args.check:
        violations = [
            r
            for r in routing_results
            if r["task_type"] in _HIGH_COMPLIANCE_TARGETS
            and "minimax" in r.get("provider", "").lower()
        ]
        if violations:
            print(f"\n❌ CHECK FAILED: {len(violations)} high-compliance task(s) routed to minimax:")
            for v in violations:
                print(f"   - `{v['task']}` → `{v['route']}` (source: {v['source']})")
            return 1
        print("\n✅ CHECK PASSED: No high-compliance targets routed to minimax.")
        return 0

    # --lint-providers: run the provider recommendation lint
    if args.lint_providers:
        from scripts.lint_task_type_provider import main as lint_main  # noqa: E402

        print("\n---\n")
        lint_exit = lint_main(["--check"] if args.check or True else [])
        return lint_exit if lint_exit != 0 else 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
