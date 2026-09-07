"""Novel Forge CLI entrypoint."""

from __future__ import annotations

import typer

from novel_forge.cli.commands.humanize_library import app as humanize_library_app
from novel_forge.cli.commands.init_long import register as register_init_long
from novel_forge.cli.commands.maintenance import register as register_maintenance
from novel_forge.cli.commands.run_chapter import register as register_run_chapter
from novel_forge.cli.commands.run_short import register as register_run_short
from novel_forge.cli.commands.system import register as register_system
from novel_forge.cli.error_handling import _build_error_trace_summary, _classify_cli_error

__all__ = ["app", "_build_error_trace_summary", "_classify_cli_error"]

app = typer.Typer(
    name="novel-forge",
    help="多模型协作小说创作系统 CLI",
    add_completion=False,
    pretty_exceptions_show_locals=False,
)

for register in (
    register_system,
    register_run_short,
    register_init_long,
    register_run_chapter,
    register_maintenance,
):
    register(app)

app.add_typer(
    humanize_library_app,
    name="humanize-library",
    help="拟人化库管理",
)

if __name__ == "__main__":
    app()
