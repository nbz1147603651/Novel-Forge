"""Unit tests for the call-site sync guard (scripts/check_call_site_sync.py).

The guard scans supervised function signatures against repository call sites
and reports keyword-style calls that omit guarded defaulted parameters, so
newly introduced defaults cannot be silently dropped at existing call sites.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_guard() -> object:
    spec = importlib.util.spec_from_file_location(
        "check_call_site_sync", _REPO_ROOT / "scripts" / "check_call_site_sync.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ccs = _load_guard()


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Miniature repo: a supervised module plus a caller package under pkg/."""

    helpers = tmp_path / "pkg" / "helpers.py"
    helpers.parent.mkdir(parents=True)
    helpers.write_text(
        "def compress(a, b, *, flag: bool = True) -> dict:\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ccs, "_ROOT", tmp_path)
    monkeypatch.setattr(ccs, "_SCAN_ROOTS", ("pkg",))
    monkeypatch.setattr(
        ccs,
        "SUPERVISED_FUNCTIONS",
        [("pkg/helpers.py", "compress", ["flag"])],
    )
    return tmp_path


def _write_caller(fake_repo: Path, content: str) -> Path:
    caller = fake_repo / "pkg" / "caller.py"
    caller.write_text(content, encoding="utf-8")
    return caller


def _missing_flag_messages(warnings: list[str]) -> list[str]:
    return [message for message in warnings if "omits guarded params: flag" in message]


def test_attribute_call_via_module_alias_is_checked(fake_repo: Path) -> None:
    """``ctx_h.compress(a, b)`` after ``from pkg import helpers as ctx_h`` must warn."""

    _write_caller(
        fake_repo,
        "from pkg import helpers as ctx_h\n"
        "def run(a, b):\n"
        "    return ctx_h.compress(a, b, packet=None)\n",
    )
    errors, warnings = ccs.check()
    assert errors == []
    assert _missing_flag_messages(warnings)


def test_attribute_call_passing_guarded_param_is_clean(fake_repo: Path) -> None:
    """Explicitly passing the guarded parameter silences the warning."""

    _write_caller(
        fake_repo,
        "from pkg import helpers as ctx_h\nctx_h.compress(a, b, packet=None, flag=True)\n",
    )
    errors, warnings = ccs.check()
    assert errors == []
    assert warnings == []


def test_aliased_name_import_is_checked(fake_repo: Path) -> None:
    """``from pkg.helpers import compress as c`` must be treated as a call site."""

    _write_caller(
        fake_repo,
        "from pkg.helpers import compress as c\nc(a, b, packet=None)\n",
    )
    errors, warnings = ccs.check()
    assert errors == []
    assert _missing_flag_messages(warnings)


def test_dotted_chain_call_is_checked(fake_repo: Path) -> None:
    """``pkg.helpers.compress(...)`` must be treated as a call site."""

    _write_caller(
        fake_repo,
        "import pkg.helpers\npkg.helpers.compress(a, b, packet=None)\n",
    )
    errors, warnings = ccs.check()
    assert errors == []
    assert _missing_flag_messages(warnings)


def test_relative_import_alias_is_checked(fake_repo: Path) -> None:
    """Relative ``from .. import helpers as ctx_h`` must resolve to the module."""

    sub = fake_repo / "pkg" / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("", encoding="utf-8")
    caller = sub / "caller.py"
    caller.write_text(
        "from .. import helpers as ctx_h\nctx_h.compress(a, b, packet=None)\n",
        encoding="utf-8",
    )
    errors, warnings = ccs.check()
    assert errors == []
    assert _missing_flag_messages(warnings)


def test_fully_positional_call_is_skipped(fake_repo: Path) -> None:
    """Calls with no keywords cannot be judged and must not warn."""

    _write_caller(
        fake_repo,
        "from pkg.helpers import compress\ncompress(a, b)\n",
    )
    errors, warnings = ccs.check()
    assert errors == []
    assert warnings == []


def test_opt_out_marker_suppresses_warning(fake_repo: Path) -> None:
    """A trailing ``# call-site-sync: ignore`` comment opts a call site out."""

    _write_caller(
        fake_repo,
        "from pkg import helpers as ctx_h\nctx_h.compress(a, b)  # call-site-sync: ignore\n",
    )
    errors, warnings = ccs.check()
    assert errors == []
    assert warnings == []


def test_strict_turns_missing_params_into_errors(fake_repo: Path) -> None:
    """``--strict`` treats missing guarded parameters as hard errors."""

    _write_caller(
        fake_repo,
        "from pkg import helpers as ctx_h\nctx_h.compress(a, b, packet=None)\n",
    )
    errors, warnings = ccs.check(strict=True)
    assert errors and not warnings


def test_supervised_param_no_longer_defaulted_is_error(fake_repo: Path) -> None:
    """A guarded parameter that lost its default is a supervised-list error."""

    helpers = fake_repo / "pkg" / "helpers.py"
    helpers.write_text(
        "def compress(a, b, *, flag: bool) -> dict:\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    errors, warnings = ccs.check()
    assert any("no longer defaulted" in error for error in errors)
    assert warnings == []


def test_baseline_sync_velocity_counts_matching_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only baseline-sync tagged commits are counted in the velocity window."""

    monkeypatch.setattr(
        ccs,
        "_git",
        lambda args: (
            "abc123 baseline-sync: regenerate snapshots\n"
            "def456 fix(editorial): rework metrics\n"
            "7890ab 基线同步: 过期断言修复\n"
        ),
    )
    count, samples = ccs.baseline_sync_velocity(days=30)
    assert count == 2
    assert len(samples) == 2


def test_main_warns_on_baseline_sync_threshold(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reaching the baseline-sync threshold surfaces a CI-visible warning."""

    monkeypatch.setattr(
        ccs, "_git", lambda args: "x1 baseline-sync\nx2 基线同步\nx3 stale assert fix\n"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["check_call_site_sync.py", "--baseline-sync-velocity", "--baseline-sync-threshold", "2"],
    )
    assert ccs.main() == 0
    output = capsys.readouterr().out
    assert "BASELINE-SYNC VELOCITY: 3 commit(s)" in output
    assert "WARNING: baseline-sync velocity reached the threshold" in output


def test_main_json_reports_threshold_exceeded(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """JSON output carries the velocity and threshold-exceeded flag."""

    monkeypatch.setattr(
        ccs, "_git", lambda args: "x1 baseline-sync\nx2 基线同步\nx3 stale assert fix\n"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_call_site_sync.py",
            "--json",
            "--baseline-sync-velocity",
            "--baseline-sync-threshold",
            "2",
        ],
    )
    assert ccs.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pass"
    assert payload["baselineSyncVelocity"]["commitCount"] == 3
    assert payload["baselineSyncVelocity"]["thresholdExceeded"] is True


def test_repo_all_compress_call_sites_carry_adaptive_skip_enabled() -> None:
    """Strict repo-level regression: the supervised entry must be fully satisfied.

    Every keyword-style call site of ``compress_prompt_context`` — including
    the ``ctx_h.compress_prompt_context`` module-alias call in
    ``chapter_runner.py`` — must pass ``adaptive_skip_enabled`` explicitly.
    """

    errors, warnings = ccs.check()
    assert errors == []
    assert warnings == []
