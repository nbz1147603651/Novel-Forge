"""Version diff utilities — compare draft versions for a chapter."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from novel_forge.core.utils.text_validation import display_word_count

RESERVED_DRAFT_VERSION_LABELS: dict[int, str] = {
    95: "归档前字数重整",
    96: "追读力修复",
    97: "因果修复",
    98: "代词修复",
    99: "对齐修复",
}
WAVE_DRAFT_VERSION = 1
WAVE_DRAFT_STEM = "v1_wave"
WAVE_DRAFT_LABEL = "初稿成章"


def draft_version_display_label(
    version: int,
    *,
    initial_label: str = "初始草稿",
    edit_template: str = "第 {version} 轮编辑",
) -> str:
    """Return a user-facing label for public and reserved draft versions."""
    if version == 0:
        return initial_label
    label = RESERVED_DRAFT_VERSION_LABELS.get(version)
    if label:
        return label
    return edit_template.format(version=version)


def draft_stem_display_label(
    filename: str,
    *,
    initial_label: str = "DRAFT 原稿",
    edit_template: str = "第{version}轮润色",
    review_label: str = "评审定稿",
) -> str:
    """Turn a draft filename stem into a readable label."""
    stem = str(filename or "").strip()
    if stem == "v_final_review":
        return review_label
    if stem == WAVE_DRAFT_STEM:
        return WAVE_DRAFT_LABEL
    if stem.startswith("v0"):
        return initial_label
    if "_edited" not in stem:
        return stem
    try:
        version = int(stem.split("_", 1)[0][1:])
    except (IndexError, ValueError):
        return stem
    return draft_version_display_label(
        version,
        initial_label=initial_label,
        edit_template=edit_template,
    )


@dataclass
class DraftVersionInfo:
    """Metadata for a single draft version."""

    version: int
    label: str
    path: str
    word_count: int = 0
    exists: bool = True


@dataclass
class DiffHunk:
    """One hunk (changed region) in a diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)


@dataclass
class VersionDiffResult:
    """Result of comparing two draft versions."""

    version_a: int
    version_b: int
    label_a: str
    label_b: str
    total_additions: int = 0
    total_deletions: int = 0
    total_changes: int = 0
    hunks: list[DiffHunk] = field(default_factory=list)
    unified_diff: str = ""
    similarity_ratio: float = 1.0


def list_draft_versions(drafts_dir: Path, chapter_num: int) -> list[DraftVersionInfo]:
    """List all available draft versions for a chapter."""
    chapter_draft_dir = drafts_dir / f"chapter_{chapter_num:03d}"
    versions: list[DraftVersionInfo] = []

    if not chapter_draft_dir.exists():
        return versions

    seen_versions: set[int] = set()

    # v0_draft.md — raw DRAFT-stage text, not the reviewable initial draft.
    v0_path = chapter_draft_dir / "v0_draft.md"
    if v0_path.exists():
        text = v0_path.read_text(encoding="utf-8")
        versions.append(
            DraftVersionInfo(
                version=0,
                label="DRAFT 原稿",
                path=str(v0_path),
                word_count=display_word_count(text),
            )
        )
        seen_versions.add(0)

    # v1_wave.md — DRAFT + WAVE handoff, the reviewable initial draft.
    wave_path = chapter_draft_dir / f"{WAVE_DRAFT_STEM}.md"
    if wave_path.exists():
        text = wave_path.read_text(encoding="utf-8")
        versions.append(
            DraftVersionInfo(
                version=WAVE_DRAFT_VERSION,
                label=WAVE_DRAFT_LABEL,
                path=str(wave_path),
                word_count=display_word_count(text),
            )
        )
        seen_versions.add(WAVE_DRAFT_VERSION)

    # v{N}_edited.md legacy/manual repair snapshots.
    for p in sorted(chapter_draft_dir.glob("v*_edited.md")):
        try:
            ver = int(p.stem.split("_")[0][1:])
        except (IndexError, ValueError):
            continue
        if ver in seen_versions:
            continue
        text = p.read_text(encoding="utf-8")
        versions.append(
            DraftVersionInfo(
                version=ver,
                label=draft_version_display_label(ver),
                path=str(p),
                word_count=display_word_count(text),
            )
        )
        seen_versions.add(ver)

    # v_final_review.md
    review_path = chapter_draft_dir / "v_final_review.md"
    if review_path.exists():
        text = review_path.read_text(encoding="utf-8")
        versions.append(
            DraftVersionInfo(
                version=99,
                label="评审定稿",
                path=str(review_path),
                word_count=display_word_count(text),
            )
        )

    # Final published chapter
    chapters_dir = drafts_dir.parent / "chapters"
    final_path = chapters_dir / f"chapter_{chapter_num:03d}.md"
    if final_path.exists():
        text = final_path.read_text(encoding="utf-8")
        versions.append(
            DraftVersionInfo(
                version=100,
                label="最终版本",
                path=str(final_path),
                word_count=display_word_count(text),
            )
        )

    return versions


def compute_diff(
    text_a: str,
    text_b: str,
    label_a: str = "版本 A",
    label_b: str = "版本 B",
    version_a: int = 0,
    version_b: int = 1,
) -> VersionDiffResult:
    """Compute a unified diff between two texts."""
    lines_a = text_a.splitlines(keepends=True)
    lines_b = text_b.splitlines(keepends=True)

    matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
    ratio = matcher.ratio()

    unified = list(
        difflib.unified_diff(
            lines_a,
            lines_b,
            fromfile=label_a,
            tofile=label_b,
            lineterm="",
        )
    )

    additions = sum(1 for line in unified if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in unified if line.startswith("-") and not line.startswith("---"))

    # Parse hunks
    hunks: list[DiffHunk] = []
    current_hunk: DiffHunk | None = None
    for line in unified:
        if line.startswith("@@"):
            if current_hunk is not None:
                hunks.append(current_hunk)
            # Parse @@ -old_start,old_count +new_start,new_count @@
            parts = line.split()
            try:
                old_part = parts[1].lstrip("-").split(",")
                new_part = parts[2].lstrip("+").split(",")
                current_hunk = DiffHunk(
                    old_start=int(old_part[0]),
                    old_count=int(old_part[1]) if len(old_part) > 1 else 1,
                    new_start=int(new_part[0]),
                    new_count=int(new_part[1]) if len(new_part) > 1 else 1,
                )
            except (IndexError, ValueError):
                current_hunk = DiffHunk(old_start=0, old_count=0, new_start=0, new_count=0)
        elif current_hunk is not None:
            current_hunk.lines.append(line)
    if current_hunk is not None:
        hunks.append(current_hunk)

    return VersionDiffResult(
        version_a=version_a,
        version_b=version_b,
        label_a=label_a,
        label_b=label_b,
        total_additions=additions,
        total_deletions=deletions,
        total_changes=additions + deletions,
        hunks=hunks,
        unified_diff="\n".join(unified),
        similarity_ratio=ratio,
    )


def diff_chapter_versions(
    drafts_dir: Path,
    chapter_num: int,
    version_a: int,
    version_b: int,
) -> VersionDiffResult:
    """Compare two specific draft versions of a chapter."""
    versions = list_draft_versions(drafts_dir, chapter_num)
    version_map = {v.version: v for v in versions}

    if version_a not in version_map:
        raise ValueError(f"版本 {version_a} 不存在")
    if version_b not in version_map:
        raise ValueError(f"版本 {version_b} 不存在")

    info_a = version_map[version_a]
    info_b = version_map[version_b]

    text_a = Path(info_a.path).read_text(encoding="utf-8")
    text_b = Path(info_b.path).read_text(encoding="utf-8")

    return compute_diff(
        text_a,
        text_b,
        label_a=info_a.label,
        label_b=info_b.label,
        version_a=version_a,
        version_b=version_b,
    )


def restore_chapter_version(
    drafts_dir: Path,
    chapters_dir: Path,
    chapter_num: int,
    target_version: int,
) -> DraftVersionInfo:
    """Restore a chapter to a specific draft version.

    Copies the content of the target draft version to the published
    ``chapters/chapter_NNN.md`` file and updates the edit snapshot.

    Returns the DraftVersionInfo of the restored version.

    Raises:
        ValueError: If the target version does not exist.
    """
    import shutil

    from novel_forge.persistence.filesystem import atomic_write_text

    versions = list_draft_versions(drafts_dir, chapter_num)
    version_map = {v.version: v for v in versions}

    if target_version not in version_map:
        available = sorted(version_map.keys())
        raise ValueError(f"版本 {target_version} 不存在。可用版本: {available}")

    info = version_map[target_version]
    source_path = Path(info.path)
    dest_path = chapters_dir / f"chapter_{chapter_num:03d}.md"

    # Read source content
    content = source_path.read_text(encoding="utf-8")

    # Backup current published file before overwriting
    if dest_path.exists():
        backup_dir = drafts_dir / f"chapter_{chapter_num:03d}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / "v_pre_restore_backup.md"
        shutil.copy2(dest_path, backup_path)

    # Write restored content
    atomic_write_text(dest_path, content)

    # Update edit snapshot so edit_tracker won't flag this as a manual edit
    snapshot_dir = drafts_dir.parent / "states" / "edit_snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"chapter_{chapter_num:03d}_snapshot.txt"
    atomic_write_text(snapshot_path, content)

    return info
