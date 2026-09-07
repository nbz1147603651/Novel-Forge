"""Regression gate for retired namespace and dependency escape hatches."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_architecture_ratchets_pass_for_repository() -> None:
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [sys.executable, "scripts/check_architecture_ratchets.py", "--check"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
