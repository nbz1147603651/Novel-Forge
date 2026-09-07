"""Guard call sites of supervised functions against silently dropped defaults.

When a function gains a new parameter with a default (for example
``adaptive_skip_enabled`` on ``compress_prompt_context``), existing call sites
keep compiling while silently exercising the default path.  This checker
parses the supervised function signatures with the ``ast`` module and scans
every call site in ``novel_forge/`` and ``tests/``: any keyword-style call
that omits one of the declared defaulted parameters is reported.  Calls that
use only positional arguments cannot be judged and are skipped.

Call sites are matched three ways so module-alias and attribute-style calls
are not silently lost:

- bare-name calls: ``compress_prompt_context(...)``;
- module-alias attribute calls: ``from ... import context_helpers as ctx_h``
  followed by ``ctx_h.compress_prompt_context(...)``;
- dotted-chain calls: ``pkg.mod.compress_prompt_context(...)``.

The supervised list lives in ``SUPERVISED_FUNCTIONS`` below.  When a new
defaulted parameter is introduced, add it to the matching supervised
signature; the checker then keeps every call site honest in CI.  Call sites
that legitimately exercise the default may opt out with a trailing
``# call-site-sync: ignore`` comment.

Baseline-sync traffic is measured separately: commits tagged as baseline
synchronization (stale-assertion fixes, snapshot regeneration, migration
assertion cleanup) are counted over a window with ``--baseline-sync-velocity``
and warned on once the count reaches ``--baseline-sync-threshold``, so the
channel stays visible instead of being absorbed into ordinary fixes or
becoming routine.

Run ``python scripts/check_call_site_sync.py`` (or ``--json``, ``--strict``
for error exit, ``--baseline-sync-velocity [--baseline-sync-threshold N]``
for the velocity report).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCAN_ROOTS = ("novel_forge", "tests")
_BASELINE_SYNC_RE = re.compile(
    r"baseline[-_ ]?sync|基线同步|过期断言|stale assert|snapshot.*regenerat|迁移.*断言|回归.*基线",
    re.IGNORECASE,
)

# Supervised signatures: (module path relative to repo root, function name,
# guarded defaulted parameter names).  When a new defaulted parameter is
# introduced, add it here; every keyword-style call site must then pass it
# unless the call site opts out with a trailing call-site-sync opt-out marker
# comment (legitimate default-behavior regression tests).
SUPERVISED_FUNCTIONS: list[tuple[str, str, list[str]]] = [
    (
        "novel_forge/pipeline/long/services/context/context_helpers.py",
        "compress_prompt_context",
        ["adaptive_skip_enabled"],
    ),
]


def _git(args: list[str]) -> str:
    """Run a read-only git command inside the repository root."""

    result = subprocess.run(
        ["git", *args],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def signature_defaulted_params(module_path: Path, func_name: str) -> set[str]:
    """Return the names of parameters with defaults on the named function."""

    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != func_name:
            continue
        args = node.args
        positional = args.posonlyargs + args.args
        positional_defaults = args.defaults or []
        defaulted_positional = {
            argument.arg
            for argument, default in zip(
                positional[-len(positional_defaults) :],
                positional_defaults,
                strict=False,
            )
            if default is not None
        }
        defaulted_kwonly = {
            argument.arg
            for argument, default in zip(args.kwonlyargs, args.kw_defaults, strict=False)
            if default is not None
        }
        return defaulted_positional | defaulted_kwonly
    return set()


def _module_dotted(module_rel: str) -> str:
    """Convert a repo-relative ``.py`` path to its dotted module path."""

    return module_rel[:-3].replace("/", ".")


def _package_of(target: Path) -> str:
    """Dotted package of a scanned file (repo root is the source root)."""

    rel = target.relative_to(_ROOT).as_posix()
    return rel.rsplit("/", 1)[0].replace("/", ".")


def _import_aliases(
    tree: ast.AST, package: str
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Map top-level bound names to the modules/functions they resolve to.

    ``module_aliases`` maps a bound name to a dotted module path
    (``import a.b as c`` -> ``c: a.b``, ``from pkg import mod as m`` ->
    ``m: pkg.mod``).  ``name_aliases`` maps a bound name to ``(module, name)``
    for ``from module import name as alias`` imports so aliased function
    calls are recognised too.
    """

    module_aliases: dict[str, str] = {}
    name_aliases: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                full = alias.name
                if alias.asname:
                    module_aliases[alias.asname] = full
                else:
                    module_aliases[full.split(".")[0]] = full
                    module_aliases[full.rsplit(".", 1)[-1]] = full
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:  # relative import: resolve against the file's package
                parts = package.split(".") if package else []
                base = ".".join(parts[: len(parts) - (node.level - 1)] + ([base] if base else []))
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                full = f"{base}.{alias.name}" if base else alias.name
                module_aliases[bound] = full
                name_aliases[bound] = (base, alias.name)
    return module_aliases, name_aliases


def import_aliases_for(target: Path) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Return ``_import_aliases`` for a file on disk."""

    tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    return _import_aliases(tree, _package_of(target))


def _dotted_chain(node: ast.AST) -> str | None:
    """Render a Name/Attribute chain (``a.b.c``) as a dotted string."""

    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _matching_calls(
    tree: ast.AST,
    func_name: str,
    supervised_module: str,
    module_aliases: dict[str, str],
    name_aliases: dict[str, tuple[str, str]],
) -> list[ast.Call]:
    """Return Call nodes whose target resolves to the supervised function."""

    matches: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name):
            if fn.id == func_name or name_aliases.get(fn.id) == (supervised_module, func_name):
                matches.append(node)
            continue
        if isinstance(fn, ast.Attribute) and fn.attr == func_name:
            dotted = _dotted_chain(fn.value)
            if dotted and (
                dotted == supervised_module or module_aliases.get(dotted) == supervised_module
            ):
                matches.append(node)
    return matches


def call_sites_for(func_name: str, supervised_module: str) -> list[tuple[Path, int]]:
    """Return (file, lineno) for call sites of func_name outside its module."""

    sites: list[tuple[Path, int]] = []
    for root in _SCAN_ROOTS:
        for py_file in _ROOT.glob(f"{root}/**/*.py"):
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            except (OSError, SyntaxError):
                continue
            module_aliases, name_aliases = _import_aliases(tree, _package_of(py_file))
            for node in _matching_calls(
                tree, func_name, supervised_module, module_aliases, name_aliases
            ):
                sites.append((py_file, node.lineno))
    return sites


def call_keywords_for(
    func_name: str,
    supervised_module: str,
    target: Path,
    lineno: int,
) -> set[str] | None:
    """Return the keyword names passed at the call site, or None if not keyword-style."""

    tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    module_aliases, name_aliases = _import_aliases(tree, _package_of(target))
    for node in _matching_calls(tree, func_name, supervised_module, module_aliases, name_aliases):
        if node.lineno != lineno:
            continue
        if any(keyword.arg is None for keyword in node.keywords):
            return None  # **kwargs splat: cannot judge
        passed = {keyword.arg for keyword in node.keywords if keyword.arg}
        return passed if passed else None  # fully positional call: cannot judge
    return None


def call_has_opt_out(target: Path, lineno: int) -> bool:
    """Return True when the call's last line carries a call-site-sync opt-out comment."""

    tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.lineno == lineno:
            lines = target.read_text(encoding="utf-8").splitlines()
            last_line = lines[node.end_lineno - 1] if node.end_lineno <= len(lines) else ""
            return "# call-site-sync: ignore" in last_line
    return False


def check(strict: bool = False) -> tuple[list[str], list[str]]:
    """Return (errors, warnings); errors fail the run."""

    errors: list[str] = []
    warnings: list[str] = []
    for module_rel, func_name, guarded in SUPERVISED_FUNCTIONS:
        module_path = _ROOT / module_rel
        if not module_path.exists():
            errors.append(f"supervised module not found: {module_rel}")
            continue
        supervised_module = _module_dotted(module_rel)
        defaulted = signature_defaulted_params(module_path, func_name)
        stale = [param for param in guarded if param not in defaulted]
        if stale:
            errors.append(
                f"supervised params no longer defaulted on {func_name}(): {', '.join(stale)}"
            )
            continue
        for target, lineno in call_sites_for(func_name, supervised_module):
            if target == module_path:
                continue
            passed = call_keywords_for(func_name, supervised_module, target, lineno)
            if passed is None or call_has_opt_out(target, lineno):
                continue
            missing = [param for param in guarded if param not in passed]
            if missing:
                relative = target.relative_to(_ROOT).as_posix()
                message = f"{relative}:{lineno} call to {func_name}() omits guarded params: {', '.join(missing)}"
                if strict:
                    errors.append(message)
                else:
                    warnings.append(message)
    return errors, warnings


def baseline_sync_velocity(days: int = 30) -> tuple[int, list[str]]:
    """Return (commit_count, sample_hashes) of baseline-sync commits in the window."""

    since = _git(["log", f"--since={days} days ago", "--pretty=%h %s"])
    matched = [line for line in since.splitlines() if _BASELINE_SYNC_RE.search(line)]
    return len(matched), matched[:8]


def main() -> int:
    """Parse arguments, run the call-site guard, and report the result."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="treat missing parameters as errors")
    parser.add_argument(
        "--baseline-sync-velocity", action="store_true", help="report baseline-sync commit velocity"
    )
    parser.add_argument(
        "--window-days", type=int, default=30, help="velocity window in days (default: 30)"
    )
    parser.add_argument(
        "--baseline-sync-threshold",
        type=int,
        default=3,
        help="warn when baseline-sync commits in the window reach this count (default: 3)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    errors, warnings = check(strict=args.strict)
    velocity: dict[str, object] | None = None
    if args.baseline_sync_velocity:
        count, samples = baseline_sync_velocity(args.window_days)
        velocity = {
            "windowDays": args.window_days,
            "commitCount": count,
            "samples": samples,
            "threshold": args.baseline_sync_threshold,
            "thresholdExceeded": count >= args.baseline_sync_threshold,
        }

    result = {
        "kind": "call-site-sync-check",
        "status": "fail" if errors else "pass",
        "errors": errors,
        "warnings": warnings,
        "baselineSyncVelocity": velocity,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for message in warnings:
            print(f"WARNING: {message}")
        for message in errors:
            print(f"ERROR: {message}")
        if velocity:
            print(
                f"BASELINE-SYNC VELOCITY: {velocity['commitCount']} commit(s) in "
                f"{velocity['windowDays']} days (threshold {velocity['threshold']})"
            )
            if velocity["thresholdExceeded"]:
                print(
                    "WARNING: baseline-sync velocity reached the threshold; "
                    "stale-assertion/snapshot traffic is normalizing into the fix flow"
                )
        if not errors and not warnings:
            print("OK: all supervised call sites pass the guarded defaulted parameters")
        elif not errors:
            print("OK: warnings above are informational")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
