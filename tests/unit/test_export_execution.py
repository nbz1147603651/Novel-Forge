"""Unit tests for execute_export_book return contract.

The dialog reads ``output_paths`` + ``bytes_written`` straight off the
direct ``export_book`` daemon result; these tests pin both fields so the
UI wiring cannot drift back to a silent-success shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.contracts import ExportBookRequest
from novel_forge.workspace.execution_export import execute_export_book


class _StubRuntime:
    """Minimal runtime surface used by execute_export_book.

    Only ``storage`` is accessed by the export path.  We give it a real
    ``FileSystemStorage`` so the export actually writes files to a temp
    directory and we can assert on bytes_written.
    """

    def __init__(self, storage: FileSystemStorage) -> None:
        self.storage = storage


def _seed_project(tmp_path: Path, *, project_id: str, chapters: list[int]) -> FileSystemStorage:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.project_dir(project_id))
    layout.root.mkdir(parents=True, exist_ok=True)
    layout.chapters_dir.mkdir(parents=True, exist_ok=True)
    for n in chapters:
        layout.chapter_path(n).write_text(
            f"# 第 {n} 章\n\n正文内容：" + ("一段情节。" * 80),
            encoding="utf-8",
        )
    # Seed outline for title metadata
    layout.outline_path.write_text(
        json.dumps(
            {
                "title": "测试书",
                "chapters": [
                    {"chapter_number": n, "title": f"第 {n} 章 标题"} for n in chapters
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    layout.spec_path.write_text(
        json.dumps(
            {"title": "Spec Title", "output_language": "zh"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return storage


@pytest.mark.asyncio
async def test_export_book_markdown_returns_output_paths_and_bytes_written(tmp_path) -> None:
    storage = _seed_project(tmp_path, project_id="book", chapters=[1, 2, 3])
    runtime = _StubRuntime(storage)
    request = ExportBookRequest(project_id="book", format="markdown")

    step_events: list[dict[str, Any]] = []

    def _on_step(kind: str, payload: dict[str, Any]) -> None:
        step_events.append({"kind": kind, "payload": payload})

    result = await execute_export_book(runtime, request, on_step_progress=_on_step)

    payload = result.result
    assert payload["format"] == "markdown"
    assert isinstance(payload["output_paths"], list) and len(payload["output_paths"]) == 1
    main_path = Path(payload["output_paths"][0])
    assert main_path.exists()
    assert payload["bytes_written"] == main_path.stat().st_size
    assert payload["bytes_written"] > 0
    # Legacy `path` field kept for backwards compatibility.
    assert payload["path"] == str(main_path)
    assert payload["chapters_exported"] == 3
    assert payload["total_chars"] > 0

    # Step callback emitted with the same shape the dialog consumes.
    assert step_events == [{"kind": "export", "payload": payload}]


@pytest.mark.asyncio
async def test_export_book_txt_strips_markdown_and_reports_bytes(tmp_path) -> None:
    storage = _seed_project(tmp_path, project_id="book", chapters=[1, 2])
    runtime = _StubRuntime(storage)
    request = ExportBookRequest(project_id="book", format="txt")

    result = await execute_export_book(runtime, request)

    payload = result.result
    assert payload["format"] == "txt"
    main_path = Path(payload["output_paths"][0])
    assert main_path.suffix == ".txt"
    # Markdown headers must be stripped in TXT mode.
    body = main_path.read_text(encoding="utf-8")
    assert "##" not in body
    assert payload["bytes_written"] == main_path.stat().st_size
    assert payload["bytes_written"] > 0


@pytest.mark.asyncio
async def test_export_book_custom_output_dir_used(tmp_path) -> None:
    storage = _seed_project(tmp_path, project_id="book", chapters=[1])
    runtime = _StubRuntime(storage)
    out_dir = tmp_path / "exports"
    request = ExportBookRequest(
        project_id="book",
        format="markdown",
        output_dir=str(out_dir),
        book_title="My Custom Title",
    )

    result = await execute_export_book(runtime, request)

    payload = result.result
    main_path = Path(payload["output_paths"][0])
    assert main_path.parent == out_dir
    assert main_path.name == "My Custom Title.md"
    assert payload["bytes_written"] > 0


@pytest.mark.asyncio
async def test_export_book_counts_only_existing_requested_chapters(tmp_path) -> None:
    storage = _seed_project(tmp_path, project_id="book", chapters=[1, 3])
    runtime = _StubRuntime(storage)
    request = ExportBookRequest(
        project_id="book",
        format="markdown",
        chapter_range=[1, 2, 3],
    )

    result = await execute_export_book(runtime, request)

    payload = result.result
    assert payload["chapters_exported"] == 2
    body = Path(payload["output_paths"][0]).read_text(encoding="utf-8")
    assert "第 1 章" in body
    assert "第 2 章" not in body
    assert "第 3 章" in body


@pytest.mark.asyncio
async def test_export_book_rejects_unknown_format(tmp_path) -> None:
    storage = _seed_project(tmp_path, project_id="book", chapters=[1])
    runtime = _StubRuntime(storage)
    request = ExportBookRequest(project_id="book", format="markdown")

    # Inject an unsupported value past the literal type check by mutating
    # the attribute after validation (defensive sanity check that the
    # function refuses values outside the Literal set).
    request.format = "pdf"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="不支持的导出格式"):
        await execute_export_book(runtime, request)
    # And nothing was written.
    assert not list((tmp_path / "book" / "exports").glob("*")) if (tmp_path / "book" / "exports").exists() else True
