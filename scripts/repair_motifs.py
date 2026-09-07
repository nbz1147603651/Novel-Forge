"""Retroactively extract motifs for all chapters in a project.

Usage:
    python scripts/repair_motifs.py <project_id>
    python scripts/repair_motifs.py 浮京一梦
    python scripts/repair_motifs.py 浮京一梦 --from-chapter 1 --to-chapter 27
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


async def repair_motifs(
    project_id: str,
    from_chapter: int = 1,
    to_chapter: int = 999,
) -> None:
    from novel_forge.memory.integration import MemoryContext
    from novel_forge.workspace.runtime import create_runtime_services

    print(f"[repair_motifs] 项目: {project_id}")

    runtime = create_runtime_services()
    storage = runtime.storage

    memory_ctx = MemoryContext.create_from_settings(
        router=runtime.router,
        builder=runtime.builder,
        settings=runtime.settings,
        project_id=project_id,
        storage=storage,
    )

    if not memory_ctx.motif_tracker:
        print("[repair_motifs] 错误：母题追踪未启用（memory_motif_tracking_enabled=false）")
        return

    chapters_dir = storage.root / project_id / "chapters"
    if not chapters_dir.exists():
        print(f"[repair_motifs] 错误：章节目录不存在: {chapters_dir}")
        return

    # Collect chapter files
    chapter_files: list[tuple[int, Path]] = []
    for f in sorted(chapters_dir.glob("chapter_*.md")):
        stem = f.stem  # e.g. "chapter_027"
        parts = stem.split("_")
        if len(parts) == 2 and parts[1].isdigit():
            ch = int(parts[1])
            if from_chapter <= ch <= to_chapter:
                chapter_files.append((ch, f))

    print(f"[repair_motifs] 找到 {len(chapter_files)} 个章节文件（范围 {from_chapter}-{to_chapter}）")

    existing_motifs = memory_ctx.motif_tracker.motifs
    print(f"[repair_motifs] 当前已有母题: {len(existing_motifs)} 个")

    processed = 0
    skipped = 0
    failed = 0

    for ch_num, ch_file in chapter_files:
        text = ch_file.read_text(encoding="utf-8").strip()
        if len(text) < 500:
            print(f"  章节 {ch_num:3d}: 跳过（内容过短 {len(text)} 字节）")
            skipped += 1
            continue

        # Check if motifs already extracted for this chapter
        if memory_ctx._motif_cache.get(ch_num):
            print(f"  章节 {ch_num:3d}: 跳过（已有缓存 {len(memory_ctx._motif_cache[ch_num])} 条）")
            skipped += 1
            continue

        print(f"  章节 {ch_num:3d}: 提取母题中... ({len(text):,} 字节)", end="", flush=True)
        try:
            motifs = await memory_ctx.motif_tracker.extract_from_chapter(
                chapter_number=ch_num,
                chapter_text=text,
            )
            memory_ctx._motif_cache[ch_num] = motifs

            # Count total motifs now
            total_now = len(memory_ctx.motif_tracker.motifs)
            print(f" → {len(motifs)} 条出现，累计母题 {total_now} 个")
            processed += 1

            # Save after every chapter so progress is preserved on interrupt
            memory_ctx.save_to_disk()

        except Exception as exc:  # noqa: BLE001
            print(f" → 失败: {exc}")
            failed += 1

    print()
    print(f"[repair_motifs] 完成：处理 {processed} 章，跳过 {skipped} 章，失败 {failed} 章")
    print(f"[repair_motifs] 最终母题总数: {len(memory_ctx.motif_tracker.motifs)} 个")

    # Final save
    saved = memory_ctx.save_to_disk()
    print(f"[repair_motifs] 已保存到磁盘: {saved}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("用法: python scripts/repair_motifs.py <project_id> [--from-chapter N] [--to-chapter N]")
        sys.exit(1)

    project_id = args[0]
    from_chapter = 1
    to_chapter = 999

    i = 1
    while i < len(args):
        if args[i] == "--from-chapter" and i + 1 < len(args):
            from_chapter = int(args[i + 1])
            i += 2
        elif args[i] == "--to-chapter" and i + 1 < len(args):
            to_chapter = int(args[i + 1])
            i += 2
        else:
            i += 1

    asyncio.run(repair_motifs(project_id, from_chapter, to_chapter))


if __name__ == "__main__":
    main()
