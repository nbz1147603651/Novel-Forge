"""Migrate existing project data to voice + relationship architecture.

Reads character_bible.json, editorial_contract.json, and canon_current.json
to ensure voice fields exist and cross-file consistency is maintained.

Usage:
    .venv/bin/python scripts/migrate_character_voice.py --project data/渡口 --dry-run
    .venv/bin/python scripts/migrate_character_voice.py --project data/渡口
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

# Add project root to path for imports
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from novel_forge.persistence.filesystem import atomic_write_text  # noqa: E402


@dataclass
class MigrationReport:
    """Tracks all migration actions for final report."""

    # Voice field additions
    voice_added: list[str] = field(default_factory=list)
    voice_already_present: list[str] = field(default_factory=list)

    # Voice matching (editorial_contract ↔ character_bible)
    voice_matched: list[str] = field(default_factory=list)
    voice_repaired: list[str] = field(default_factory=list)
    voice_unmatched: list[str] = field(default_factory=list)
    voice_backfilled: list[str] = field(default_factory=list)
    bible_voice_synced: list[str] = field(default_factory=list)

    # Canon social_status sync
    canon_social_status_synced: list[str] = field(default_factory=list)
    canon_social_status_already_present: list[str] = field(default_factory=list)
    canon_social_status_no_source: list[str] = field(default_factory=list)
    canon_voice_synced: list[str] = field(default_factory=list)

    # Gender fixes (character_bible → canon)
    canon_gender_fixed: list[str] = field(default_factory=list)


def _load_json(path: Path) -> dict:
    """Load a JSON file and return parsed dict."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: dict) -> None:
    """Atomically save a JSON file."""
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, text)


def _build_bible_name_map(bible: dict) -> dict[str, dict]:
    """Build name → character dict from character_bible.json."""
    return {c["name"]: c for c in bible.get("characters", [])}


def _resolve_character_name(raw_name: str, valid_names: list[str]) -> str:
    name = str(raw_name or "").strip()
    if not valid_names:
        return name
    if name in valid_names:
        return name
    if name:
        for candidate in valid_names:
            if len(name) >= 2 and (name in candidate or candidate in name):
                return candidate
        scored = [(SequenceMatcher(None, name, candidate).ratio(), candidate) for candidate in valid_names]
        score, candidate = max(scored, default=(0.0, ""))
        if score >= 0.78:
            return candidate
    return ""


def _default_voice_profile(character: str) -> dict:
    return {
        "character": character,
        "sentence_profile": f"{character}的句式保持清晰可辨。",
        "explanation_bias": f"{character}通过行动和细节表达立场。",
        "emotion_syntax": f"{character}的情绪通过句法变化而非直白陈述表达。",
        "signature_moves": [f"{character}保持可识别的动作或句法习惯"],
        "taboo_patterns": ["不得使用与角色身份不符的通用口吻"],
        "sample_lines": [],
    }


def _voice_text(voice_profile: dict) -> str:
    parts: list[str] = []
    for key in ("sentence_profile", "explanation_bias", "emotion_syntax"):
        text = str(voice_profile.get(key) or "").strip()
        if text:
            parts.append(text)
    moves = voice_profile.get("signature_moves") or []
    if isinstance(moves, list):
        move_text = "；".join(str(item).strip() for item in moves if str(item).strip())
        if move_text:
            parts.append(f"标志动作/句法：{move_text}")
    return "；".join(parts)


def migrate_voice_field(bible: dict, report: MigrationReport) -> dict:
    """Add 'voice' field to all CharacterProfile entries missing it."""
    for char in bible.get("characters", []):
        name = char.get("name", "<unknown>")
        if "voice" not in char:
            char["voice"] = ""
            report.voice_added.append(name)
        else:
            report.voice_already_present.append(name)
    return bible


def check_voice_matching(bible: dict, editorial: dict, report: MigrationReport) -> None:
    """Check editorial_contract character_voices against character_bible names."""
    bible_names = {c["name"] for c in bible.get("characters", [])}
    for cv in editorial.get("character_voices", []):
        voice_name = cv.get("character", "")
        if voice_name in bible_names:
            report.voice_matched.append(voice_name)
        else:
            report.voice_unmatched.append(voice_name)


def repair_editorial_voice_names(
    bible: dict,
    editorial: dict,
    report: MigrationReport,
    *,
    dry_run: bool,
) -> dict:
    """Drop/repair voice profiles that do not point at real CharacterBible names."""
    valid_names = [
        str(c.get("name") or "").strip()
        for c in bible.get("characters", [])
        if str(c.get("name") or "").strip()
    ]
    target_count = min(6, len(valid_names))
    repaired: list[dict] = []
    seen: set[str] = set()
    for raw_voice in editorial.get("character_voices", []) or []:
        if not isinstance(raw_voice, dict):
            continue
        raw_name = str(raw_voice.get("character") or "").strip()
        resolved = _resolve_character_name(raw_name, valid_names)
        if not resolved:
            report.voice_unmatched.append(raw_name or "<empty>")
            continue
        if resolved in seen:
            continue
        voice = dict(raw_voice)
        voice["character"] = resolved
        repaired.append(voice)
        seen.add(resolved)
        if raw_name == resolved:
            report.voice_matched.append(resolved)
        else:
            report.voice_repaired.append(f"{raw_name} -> {resolved}")

    for name in valid_names:
        if len(repaired) >= target_count:
            break
        if name in seen:
            continue
        repaired.append(_default_voice_profile(name))
        seen.add(name)
        report.voice_backfilled.append(name)

    editorial["character_voices"] = repaired
    return editorial


def sync_bible_voice_from_editorial(
    bible: dict,
    editorial: dict,
    report: MigrationReport,
    *,
    dry_run: bool,
) -> dict:
    """Populate empty CharacterProfile.voice from matched EditorialContract voices."""
    bible_map = _build_bible_name_map(bible)
    for voice in editorial.get("character_voices", []) or []:
        if not isinstance(voice, dict):
            continue
        name = str(voice.get("character") or "").strip()
        char = bible_map.get(name)
        if not char or str(char.get("voice") or "").strip():
            continue
        text = _voice_text(voice)
        if not text:
            continue
        char["voice"] = text
        report.bible_voice_synced.append(name)
    return bible


def sync_canon_social_status(
    bible: dict, canon: dict, report: MigrationReport, *, dry_run: bool
) -> dict:
    """Sync social_status from character_bible to canon character states (if empty)."""
    bible_map = _build_bible_name_map(bible)
    canon_chars = canon.get("characters", {})

    for char_name, canon_state in canon_chars.items():
        bible_char = bible_map.get(char_name)
        if bible_char is None:
            report.canon_social_status_no_source.append(char_name)
            continue

        current_status = canon_state.get("social_status", "")
        bible_status = bible_char.get("social_status", "")

        if current_status:
            report.canon_social_status_already_present.append(char_name)
            continue

        if bible_status:
            canon_state["social_status"] = bible_status
            report.canon_social_status_synced.append(char_name)
        else:
            report.canon_social_status_no_source.append(char_name)

    return canon


def sync_canon_voice(bible: dict, canon: dict, report: MigrationReport, *, dry_run: bool) -> dict:
    """Sync voice from character_bible to canon character states (if empty)."""
    bible_map = _build_bible_name_map(bible)
    for char_name, canon_state in canon.get("characters", {}).items():
        bible_char = bible_map.get(char_name)
        if bible_char is None or str(canon_state.get("voice") or "").strip():
            continue
        voice = str(bible_char.get("voice") or "").strip()
        if not voice:
            continue
        canon_state["voice"] = voice
        report.canon_voice_synced.append(char_name)
    return canon


def fix_canon_gender(
    bible: dict, canon: dict, report: MigrationReport, *, dry_run: bool
) -> dict:
    """Fix gender mismatches between character_bible and canon."""
    bible_map = _build_bible_name_map(bible)
    canon_chars = canon.get("characters", {})

    for char_name, canon_state in canon_chars.items():
        bible_char = bible_map.get(char_name)
        if bible_char is None:
            continue

        canon_gender = canon_state.get("gender", "")
        bible_gender = bible_char.get("gender", "")

        if bible_gender and canon_gender and canon_gender != bible_gender:
            canon_state["gender"] = bible_gender
            report.canon_gender_fixed.append(
                f"{char_name}: canon='{canon_gender}' → bible='{bible_gender}'"
            )

    return canon


def print_report(report: MigrationReport, *, dry_run: bool) -> None:
    """Print migration report to stdout."""
    mode = "DRY-RUN (no files modified)" if dry_run else "LIVE (files modified)"
    print(f"\n{'='*60}")
    print(f"Migration Report — {mode}")
    print(f"{'='*60}")

    # 1. Voice field additions
    print("\n[1] Voice field in character_bible.json")
    if report.voice_added:
        print(f"  + Added 'voice': '' to {len(report.voice_added)} character(s):")
        for name in report.voice_added:
            print(f"    - {name}")
    if report.voice_already_present:
        print(f"  = Already present in {len(report.voice_already_present)} character(s):")
        for name in report.voice_already_present:
            print(f"    - {name}")
    if not report.voice_added and not report.voice_already_present:
        print("  (no characters found)")

    # 2. Voice matching
    print("\n[2] Editorial voice matching (editorial_contract ↔ character_bible)")
    if report.voice_matched:
        print(f"  ✓ Matched {len(report.voice_matched)} voice(s):")
        for name in report.voice_matched:
            print(f"    - {name}")
    if report.voice_repaired:
        print(f"  ~ Repaired {len(report.voice_repaired)} voice name(s):")
        for entry in report.voice_repaired:
            print(f"    - {entry}")
    if report.voice_unmatched:
        print(f"  ✗ Dropped {len(report.voice_unmatched)} unmatched voice(s) (not in character_bible):")
        for name in report.voice_unmatched:
            print(f"    - {name}")
    if report.voice_backfilled:
        print(f"  + Backfilled {len(report.voice_backfilled)} default voice profile(s):")
        for name in report.voice_backfilled:
            print(f"    - {name}")
    if report.bible_voice_synced:
        print(f"  + Synced CharacterProfile.voice for {len(report.bible_voice_synced)} character(s):")
        for name in report.bible_voice_synced:
            print(f"    - {name}")

    # 3. Canon social_status sync
    print("\n[3] Canon social_status sync (character_bible → canon)")
    if report.canon_social_status_synced:
        print(f"  + Synced {len(report.canon_social_status_synced)} character(s):")
        for name in report.canon_social_status_synced:
            print(f"    - {name}")
    if report.canon_social_status_already_present:
        print(f"  = Already present in {len(report.canon_social_status_already_present)} character(s):")
        for name in report.canon_social_status_already_present:
            print(f"    - {name}")
    if report.canon_social_status_no_source:
        print(f"  ⚠ No source in character_bible for {len(report.canon_social_status_no_source)} character(s):")
        for name in report.canon_social_status_no_source:
            print(f"    - {name}")
    if report.canon_voice_synced:
        print(f"  + Synced voice for {len(report.canon_voice_synced)} canon character(s):")
        for name in report.canon_voice_synced:
            print(f"    - {name}")

    # 4. Gender fixes
    print("\n[4] Canon gender fixes (character_bible → canon)")
    if report.canon_gender_fixed:
        print(f"  ~ Fixed {len(report.canon_gender_fixed)} mismatch(es):")
        for entry in report.canon_gender_fixed:
            print(f"    - {entry}")
    else:
        print("  (no gender mismatches found)")

    # Summary
    total_changes = (
        len(report.voice_added)
        + len(report.voice_repaired)
        + len(report.voice_backfilled)
        + len(report.bible_voice_synced)
        + len(report.canon_social_status_synced)
        + len(report.canon_voice_synced)
        + len(report.canon_gender_fixed)
    )
    print(f"\n{'='*60}")
    print(f"Total changes: {total_changes}")
    if dry_run:
        print("Re-run without --dry-run to apply changes.")
    print(f"{'='*60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate project data to voice + relationship architecture."
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Path to project directory (e.g., data/渡口)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report planned changes, do not modify files.",
    )
    args = parser.parse_args()

    project_dir = Path(args.project)
    if not project_dir.is_dir():
        print(f"Error: project directory not found: {project_dir}", file=sys.stderr)
        sys.exit(1)

    bible_path = project_dir / "character_bible.json"
    editorial_path = project_dir / "plans" / "editorial_contract.json"
    canon_path = project_dir / "canon" / "canon_current.json"

    # Validate required files exist
    for path, label in [
        (bible_path, "character_bible.json"),
        (editorial_path, "plans/editorial_contract.json"),
        (canon_path, "canon/canon_current.json"),
    ]:
        if not path.is_file():
            print(f"Error: {label} not found at {path}", file=sys.stderr)
            sys.exit(1)

    # Load data
    bible = _load_json(bible_path)
    editorial = _load_json(editorial_path)
    canon = _load_json(canon_path)

    report = MigrationReport()

    # Step 1: Add voice field to character_bible
    bible = migrate_voice_field(bible, report)

    # Step 2: Repair editorial voice matching and sync CharacterProfile.voice
    editorial = repair_editorial_voice_names(bible, editorial, report, dry_run=args.dry_run)
    bible = sync_bible_voice_from_editorial(bible, editorial, report, dry_run=args.dry_run)

    # Step 3: Sync stable profile fields from character_bible to canon
    canon = sync_canon_social_status(bible, canon, report, dry_run=args.dry_run)
    canon = sync_canon_voice(bible, canon, report, dry_run=args.dry_run)

    # Step 4: Fix gender mismatches
    canon = fix_canon_gender(bible, canon, report, dry_run=args.dry_run)

    # Save modified files (unless dry-run)
    if not args.dry_run:
        _save_json(bible_path, bible)
        _save_json(editorial_path, editorial)
        _save_json(canon_path, canon)
        print("Files saved:")
        print(f"  - {bible_path}")
        print(f"  - {editorial_path}")
        print(f"  - {canon_path}")

    # Print report
    print_report(report, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
