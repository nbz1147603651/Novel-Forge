"""Initialization imports must not depend on namespace-linking side effects."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "modules",
    [
        [
            "novel_forge.pipeline.long.services.init.init_service",
            "novel_forge.pipeline.long.services.init.init_orchestrator",
            "novel_forge.pipeline.long.services.init.init_source_resume",
        ],
        [
            "novel_forge.pipeline.long.services.init.init_source_resume",
            "novel_forge.pipeline.long.services.init.init_character_bible",
            "novel_forge.pipeline.long.services.init.init_service",
        ],
    ],
)
def test_init_facade_is_stable_across_import_orders(modules: list[str]) -> None:
    imports = ";".join(f"import {name}" for name in modules)
    script = (
        f"{imports};"
        "from novel_forge.pipeline.long.services.init import init_service as facade;"
        "from novel_forge.pipeline.long.services.init import init_orchestrator as impl;"
        "assert facade.init_long_project is impl.init_long_project;"
        "assert facade.__all__ == sorted(facade.__all__)"
    )

    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)
