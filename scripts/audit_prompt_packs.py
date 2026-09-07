#!/usr/bin/env python3
"""Audit prompt pack coverage, imports, and translation drift."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))

from novel_forge.prompts.packs import (  # noqa: E402
    DEFAULT_PROMPT_LOCALE,
    PromptPack,
    discover_prompt_packs,
)
from novel_forge.prompts.registry import _TASK_TEMPLATE_MAP  # noqa: E402

_IMPORT_RE = re.compile(r'{%-?\s*(?:from|import)\s+"([^"]+)"(?:\s+import|\s+as)')
_SOURCE_HASH_RE = re.compile(r"source_template_hash:\s*([a-fA-F0-9]{64})")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_COMPAT_TEMPLATES_DIR = _repo_root / "novel_forge" / "prompts" / "prompts"


@dataclass(frozen=True)
class PackIssue:
    locale: str
    code: str
    message: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _template_paths(pack: PromptPack) -> list[Path]:
    if not pack.templates_dir.exists():
        return []
    return sorted(pack.templates_dir.rglob("*.j2"))


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _audit_coverage(pack: PromptPack) -> list[PackIssue]:
    issues: list[PackIssue] = []
    if not pack.templates_dir.exists():
        if pack.coverage_required or pack.is_stable:
            issues.append(
                PackIssue(pack.locale, "missing-templates-dir", str(pack.templates_dir))
            )
        return issues

    existing = {_rel(path, pack.templates_dir) for path in _template_paths(pack)}
    required = set(_TASK_TEMPLATE_MAP.values())
    missing = sorted(required - existing)
    if missing and (pack.coverage_required or pack.is_stable):
        preview = ", ".join(missing[:12])
        suffix = f"; +{len(missing) - 12} more" if len(missing) > 12 else ""
        issues.append(PackIssue(pack.locale, "missing-registered-template", preview + suffix))
    return issues


def _audit_syntax(pack: PromptPack) -> list[PackIssue]:
    issues: list[PackIssue] = []
    if not pack.templates_dir.exists():
        return issues
    env = Environment(loader=FileSystemLoader(str(pack.templates_dir)), extensions=["jinja2.ext.do"])
    for path in _template_paths(pack):
        rel_path = _rel(path, pack.templates_dir)
        try:
            env.get_template(rel_path)
        except Exception as exc:
            issues.append(PackIssue(pack.locale, "template-syntax", f"{rel_path}: {exc}"))
    return issues


def _audit_imports(pack: PromptPack) -> list[PackIssue]:
    issues: list[PackIssue] = []
    if not pack.templates_dir.exists():
        return issues
    for path in _template_paths(pack):
        rel_path = _rel(path, pack.templates_dir)
        text = path.read_text(encoding="utf-8")
        for imported in sorted(set(_IMPORT_RE.findall(text))):
            if not (pack.templates_dir / imported).exists():
                issues.append(
                    PackIssue(
                        pack.locale,
                        "missing-pack-import",
                        f"{rel_path} imports {imported}, but it is absent from {pack.locale}",
                    )
                )
    return issues


def _audit_source_hash(pack: PromptPack, base_pack: PromptPack) -> list[PackIssue]:
    issues: list[PackIssue] = []
    if pack.locale == base_pack.locale or not pack.templates_dir.exists():
        return issues
    for path in _template_paths(pack):
        rel_path = _rel(path, pack.templates_dir)
        source_path = base_pack.templates_dir / rel_path
        if not source_path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        match = _SOURCE_HASH_RE.search(text)
        if not match:
            issues.append(PackIssue(pack.locale, "missing-source-template-hash", rel_path))
            continue
        current = _sha256(source_path)
        if match.group(1).lower() != current:
            issues.append(PackIssue(pack.locale, "stale-source-template-hash", rel_path))
    return issues


def _audit_english_cjk_residue(pack: PromptPack) -> list[PackIssue]:
    issues: list[PackIssue] = []
    if pack.locale != "en" or not pack.templates_dir.exists():
        return issues
    for path in _template_paths(pack):
        rel_path = _rel(path, pack.templates_dir)
        text = path.read_text(encoding="utf-8")
        if _CJK_RE.search(text):
            issues.append(PackIssue(pack.locale, "cjk-residue", rel_path))
    return issues


def _audit_compatibility_copy(base_pack: PromptPack) -> list[PackIssue]:
    """Keep the legacy lookup tree as a mechanical copy of canonical Chinese."""

    issues: list[PackIssue] = []
    for source in _template_paths(base_pack):
        rel_path = _rel(source, base_pack.templates_dir)
        target = _COMPAT_TEMPLATES_DIR / rel_path
        if not target.exists():
            issues.append(PackIssue("compat", "missing-canonical-copy", rel_path))
        elif source.read_bytes() != target.read_bytes():
            issues.append(PackIssue("compat", "canonical-copy-drift", rel_path))
    return issues


def sync_compatibility_copy(base_pack: PromptPack) -> int:
    """Mechanically mirror canonical Chinese templates into the compatibility tree."""

    changed = 0
    for source in _template_paths(base_pack):
        rel_path = _rel(source, base_pack.templates_dir)
        target = _COMPAT_TEMPLATES_DIR / rel_path
        if target.exists() and source.read_bytes() == target.read_bytes():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        changed += 1
    return changed


def audit_prompt_packs(*, include_draft: bool = False) -> list[PackIssue]:
    packs = discover_prompt_packs()
    base_pack = packs[DEFAULT_PROMPT_LOCALE]
    issues: list[PackIssue] = []
    for pack in packs.values():
        if not include_draft and not pack.is_stable:
            continue
        issues.extend(_audit_coverage(pack))
        issues.extend(_audit_syntax(pack))
        issues.extend(_audit_imports(pack))
        issues.extend(_audit_source_hash(pack, base_pack))
        issues.extend(_audit_english_cjk_residue(pack))
    issues.extend(_audit_compatibility_copy(base_pack))
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-draft",
        action="store_true",
        help="Also audit draft prompt packs; missing templates are allowed unless coverage_required.",
    )
    parser.add_argument(
        "--sync-compat",
        action="store_true",
        help="Mechanically copy canonical Chinese templates into the legacy compatibility tree.",
    )
    args = parser.parse_args(argv)

    if args.sync_compat:
        base_pack = discover_prompt_packs()[DEFAULT_PROMPT_LOCALE]
        changed = sync_compatibility_copy(base_pack)
        print(f"Prompt compatibility sync: {changed} file(s) updated")

    issues = audit_prompt_packs(include_draft=args.include_draft)
    if not issues:
        print("Prompt pack audit: PASS")
        return 0
    print("Prompt pack audit: FAIL")
    for issue in issues:
        print(f"[{issue.locale}] {issue.code}: {issue.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
