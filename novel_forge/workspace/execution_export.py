"""Book export execution helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from novel_forge.core.domain.language import normalize_language_tag
from novel_forge.core.exceptions import StorageError
from novel_forge.core.infra.resource_locks import (
    ResourceLockType,
    ResourceName,
    get_resource_lock_manager,
)
from novel_forge.film.schemas import DeliveryManifest, DeliveryMediaType
from novel_forge.obs.logger import get_logger
from novel_forge.workspace.async_context import sync_to_async_context
from novel_forge.workspace.contracts import ExportBookRequest
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.runtime import RuntimeServices

_log = get_logger("workspace.execution_export")


@asynccontextmanager
async def _project_lock(runtime: Any, project_id: str) -> AsyncIterator[None]:
    storage = getattr(runtime, "storage", None)

    async with AsyncExitStack() as stack:
        try:
            lock_mgr = get_resource_lock_manager()
            await stack.enter_async_context(
                lock_mgr.lock(
                    ResourceName.CANON,
                    ResourceLockType.EXCLUSIVE,
                    project_id=project_id,
                )
            )
        except Exception:
            pass

        from novel_forge.persistence.filesystem import FileSystemStorage

        if isinstance(storage, FileSystemStorage):
            sync_ctx = storage.project_lock(project_id)
            await stack.enter_async_context(sync_to_async_context(sync_ctx))

        yield


async def execute_export_book(
    runtime: RuntimeServices,
    request: ExportBookRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[Any]:
    """Export completed chapters to the requested format."""
    import re as _re
    from pathlib import Path

    def _sanitize_filename(name: str) -> str:
        """Strip characters illegal in file names on all major platforms."""
        sanitized = _re.sub(r'[/\\:*?"<>|]', "_", name).strip()
        return sanitized or "未命名作品"

    from novel_forge.persistence.models import ProjectLayout

    async with _project_lock(runtime, request.project_id):
        layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))

        chapter_nums: list[int] = []
        if request.chapter_range:
            chapter_nums = sorted(request.chapter_range)
        else:
            for p in sorted(layout.chapters_dir.glob("chapter_*.md")):
                try:
                    num = int(p.stem.split("_")[1])
                    chapter_nums.append(num)
                except (IndexError, ValueError):
                    continue

        if not chapter_nums:
            raise ValueError("没有可导出的已完成章节。")

        outline_raw = (
            runtime.storage.load_json(layout.outline_path) if layout.outline_path.exists() else {}
        )
        chapters_list = outline_raw.get("chapters", [])
        title_map = {c.get("chapter_number"): c.get("title", "") for c in chapters_list}
        spec_raw: dict[str, Any] = {}
        try:
            spec_path = layout.root / "spec.json"
            if spec_path.exists():
                loaded_spec = runtime.storage.load_json(spec_path)
                spec_raw = loaded_spec if isinstance(loaded_spec, dict) else {}
        except (OSError, StorageError):
            _log.debug("spec load failed for export metadata", exc_info=True)

        # Use user-supplied title first, then fall back to outline, then spec
        if request.book_title:
            book_title = request.book_title.strip()
        else:
            book_title = outline_raw.get("title", "")
        if not book_title:
            book_title = str(spec_raw.get("title", "") or "")
        if not book_title:
            book_title = "未命名作品"
        export_language = normalize_language_tag(
            spec_raw.get("output_language") or spec_raw.get("language") or "zh"
        )

        parts: list[str] = [f"# {book_title}\n"]
        exported_chapter_nums: list[int] = []
        for ch_num in chapter_nums:
            ch_path = layout.chapter_path(ch_num)
            if not ch_path.exists():
                continue
            ch_text = ch_path.read_text(encoding="utf-8")
            ch_title = title_map.get(ch_num, f"第 {ch_num} 章")
            parts.append(f"\n## 第 {ch_num} 章 {ch_title}\n\n{ch_text}")
            exported_chapter_nums.append(ch_num)

        if not exported_chapter_nums:
            raise ValueError("没有可导出的已完成章节。")

        full_text = "\n".join(parts)

        if request.output_dir:
            export_dir = Path(request.output_dir)
        else:
            export_dir = layout.root / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)

        safe_title = _sanitize_filename(book_title)
        if request.format == "markdown":
            out_path = export_dir / f"{safe_title}.md"
            runtime.storage.save_text(out_path, full_text)
        elif request.format == "txt":
            import re

            plain = re.sub(r"^#{1,6}\s+", "", full_text, flags=re.MULTILINE)
            plain = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", plain)
            plain = re.sub(r"_{1,3}(.+?)_{1,3}", r"\1", plain)
            plain = re.sub(r"`(.+?)`", r"\1", plain)
            plain = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", plain)
            plain = re.sub(r"^[-*_]{3,}\s*$", "", plain, flags=re.MULTILINE)
            out_path = export_dir / f"{safe_title}.txt"
            runtime.storage.save_text(out_path, plain)
        elif request.format == "epub":
            out_path = _export_epub(
                export_dir,
                safe_title,
                book_title,
                exported_chapter_nums,
                title_map,
                layout,
                export_language,
            )
        else:
            raise ValueError(f"不支持的导出格式: {request.format}")

        # Surface the real output to the UI: a list of paths (single file
        # today, but list shape leaves room for sidecar metadata without
        # breaking callers) plus the actual byte count of the main file.
        # Fall back to 0 if the file went missing between write + stat so
        # the dialog can still render something rather than crash.
        try:
            bytes_written = int(out_path.stat().st_size)
        except OSError:
            bytes_written = 0
        output_paths = [str(out_path)]

        # Unified delivery layer: register the novel artifact on a manifest
        # sidecar so all four media share one registration shape.
        from novel_forge.persistence.filesystem import atomic_write_json

        novel_manifest = DeliveryManifest(
            project_id=request.project_id, title=book_title
        ).register_artifact(
            DeliveryMediaType.NOVEL,
            str(out_path),
            item_count=len(exported_chapter_nums),
            note=f"{request.format} export",
        )
        manifest_path = export_dir / "delivery_manifest.json"
        from novel_forge.persistence.authoring_store import content_version
        from novel_forge.workspace.publication import build_chapter_publication_view

        source_versions = [
            build_chapter_publication_view(layout, request.project_id, number).model_dump(
                mode="json", exclude={"text", "tts_metadata"}
            )
            for number in exported_chapter_nums
        ]
        warnings = [
            f"第 {item['chapter_number']} 章：当前正文未完成版本一致的交付验证（{item['publication_status']}）"
            for item in source_versions
            if item["publication_status"] != "ready"
        ]
        source_manifest_path = out_path.with_suffix(out_path.suffix + ".sources.json")
        source_signature = content_version(source_versions)
        atomic_write_json(
            source_manifest_path,
            {
                "project_id": request.project_id,
                "source_signature": source_signature,
                "chapters": source_versions,
                "warnings": warnings,
            },
        )
        novel_manifest = novel_manifest.model_copy(
            update={
                "source_signature": source_signature,
                "derivation_status": "stale" if warnings else "fresh",
                "notes": [f"正文版本清单：{source_manifest_path.name}", *warnings],
            }
        )
        atomic_write_json(manifest_path, novel_manifest.model_dump(mode="json"))

        result = {
            "format": request.format,
            "path": str(out_path),
            "output_paths": output_paths,
            "bytes_written": bytes_written,
            "chapters_exported": len(exported_chapter_nums),
            "total_chars": len(full_text),
            "output_language": export_language,
            "manifest_path": str(manifest_path),
            "source_manifest_path": str(source_manifest_path),
            "warnings": warnings,
            "message": "书稿已导出，正文版本清单已保存。"
            + ("部分章节待重验，本次导出不代表验证通过。" if warnings else ""),
        }

        if on_step_progress:
            on_step_progress("export", result)

    return ExecutionResult(project_id=request.project_id, result=result)


def _export_epub(
    export_dir: Any,
    safe_title: str,
    book_title: str,
    chapter_nums: list[int],
    title_map: dict[Any, Any],
    layout: Any,
    output_language: str,
) -> Any:
    """Export to EPUB using a minimal builder (no external dependency)."""
    import html as html_mod
    import zipfile

    epub_path = export_dir / f"{safe_title}.epub"
    cover_asset = _find_epub_cover_asset(layout)

    # Build EPUB structure
    with zipfile.ZipFile(epub_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype must be first and uncompressed
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)

        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            "  <rootfiles>\n"
            '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
            "  </rootfiles>\n"
            "</container>",
        )

        stylesheet = (
            "body { font-family: serif; line-height: 1.8; margin: 5%; }\n"
            "h1 { text-align: center; margin: 2em 0 1.5em; }\n"
            "p { text-indent: 2em; margin: 0 0 0.85em; }\n"
            ".scene-break { border: 0; margin: 1.5em auto; width: 40%; }\n"
            ".scene-break::after { content: '* * *'; display: block; text-align: center; }\n"
        )
        zf.writestr("OEBPS/styles.css", stylesheet)

        if cover_asset is not None:
            zf.write(cover_asset, f"OEBPS/images/{cover_asset.name}")

        # Build chapter XHTML files
        manifest_items = []
        spine_items = []
        nav_items = []
        for ch_num in chapter_nums:
            ch_path = layout.chapter_path(ch_num)
            if not ch_path.exists():
                continue
            ch_text = ch_path.read_text(encoding="utf-8")
            ch_title = html_mod.escape(title_map.get(ch_num, f"第 {ch_num} 章"))
            body_html = _chapter_markdown_to_epub_html(ch_text, html_mod)
            xhtml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<!DOCTYPE html>\n"
                f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{output_language}">\n'
                f"<head><title>{ch_title}</title>"
                '<link rel="stylesheet" type="text/css" href="styles.css"/></head>\n'
                f"<body>\n<h1>第 {ch_num} 章 {ch_title}</h1>\n{body_html}\n</body>\n</html>"
            )
            filename = f"chapter_{ch_num:03d}.xhtml"
            zf.writestr(f"OEBPS/{filename}", xhtml)
            item_id = f"ch{ch_num:03d}"
            manifest_items.append(
                f'    <item id="{item_id}" href="{filename}" media-type="application/xhtml+xml"/>'
            )
            spine_items.append(f'    <itemref idref="{item_id}"/>')
            nav_items.append(f'      <li><a href="{filename}">第 {ch_num} 章 {ch_title}</a></li>')

        # EPUB 3 nav.xhtml — required for compliant readers (Kindle/Apple Books)
        escaped_title = html_mod.escape(book_title)
        nav_xhtml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<!DOCTYPE html>\n"
            '<html xmlns="http://www.w3.org/1999/xhtml"'
            f' xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{output_language}">\n'
            f"<head><title>{escaped_title}</title>"
            '<link rel="stylesheet" type="text/css" href="styles.css"/></head>\n'
            "<body>\n"
            '  <nav epub:type="toc" id="toc">\n'
            f"    <h1>{escaped_title}</h1>\n"
            "    <ol>\n" + "\n".join(nav_items) + "\n"
            "    </ol>\n"
            "  </nav>\n"
            "</body>\n</html>"
        )
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml)

        cover_manifest = ""
        cover_metadata = ""
        if cover_asset is not None:
            media_type = _epub_cover_media_type(cover_asset.name)
            cover_manifest = (
                f'\n    <item id="cover-image" href="images/{html_mod.escape(cover_asset.name)}"'
                f' media-type="{media_type}" properties="cover-image"/>'
            )
            cover_metadata = '\n    <meta name="cover" content="cover-image"/>'

        opf = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f"    <dc:title>{escaped_title}</dc:title>\n"
            f"    <dc:language>{output_language}</dc:language>\n"
            '    <dc:identifier id="uid">novel-forge-export</dc:identifier>\n'
            "    <dc:creator>Novel Forge</dc:creator>\n"
            "    <dc:publisher>Novel Forge</dc:publisher>"
            f"{cover_metadata}\n"
            "  </metadata>\n"
            "  <manifest>\n"
            '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>\n'
            '    <item id="css" href="styles.css" media-type="text/css"/>'
            f"{cover_manifest}\n" + "\n".join(manifest_items) + "\n"
            "  </manifest>\n"
            "  <spine>\n" + "\n".join(spine_items) + "\n"
            "  </spine>\n"
            "</package>"
        )
        zf.writestr("OEBPS/content.opf", opf)

    return epub_path


def _chapter_markdown_to_epub_html(text: str, html_mod: Any) -> str:
    blocks: list[str] = []
    for paragraph in text.strip().split("\n\n"):
        raw = paragraph.strip()
        if not raw:
            continue
        if raw in {"***", "* * *", "---"}:
            blocks.append('<hr class="scene-break"/>')
            continue
        if raw.startswith("#"):
            raw = raw.lstrip("#").strip()
        escaped = html_mod.escape(raw).replace("\n", "<br/>")
        blocks.append(f"<p>{escaped}</p>")
    return "\n".join(blocks)


def _find_epub_cover_asset(layout: Any) -> Any | None:
    for name in ("cover.jpg", "cover.jpeg", "cover.png"):
        path = layout.root / name
        if path.exists():
            return path
    return None


def _epub_cover_media_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".png"):
        return "image/png"
    return "image/jpeg"
