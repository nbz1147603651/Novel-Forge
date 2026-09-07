"""Tests for _emit_audit_update helper and repair callback wiring."""

from __future__ import annotations

import ast
import inspect
import logging
from pathlib import Path
from unittest.mock import MagicMock

from novel_forge.workspace.audit_callback import _emit_audit_update
from novel_forge.workspace.repair_ops import (
    execution_repair_causal,
    execution_repair_continuity,
)


class TestEmitAuditUpdate:
    def test_callback_none_is_noop(self) -> None:
        _emit_audit_update(None, "proj-1", 1, {"score": 0.9})

    def test_callback_invoked_with_correct_args(self) -> None:
        cb = MagicMock()
        result = {"critique": {"issues": []}, "continuity_score": 8.5}
        _emit_audit_update(cb, "proj-1", 3, result)
        cb.assert_called_once_with("proj-1", 3, result)

    def test_callback_exception_is_swallowed(self, caplog: logging.LogCaptureFixture) -> None:
        cb = MagicMock(side_effect=RuntimeError("boom"))
        with caplog.at_level(logging.ERROR, logger="novel_forge.workspace.audit_callback"):
            _emit_audit_update(cb, "proj-1", 2, {"score": 0.5})
        assert any("audit callback failed" in rec.message for rec in caplog.records)


def _emit_call_node_passes_param(source_path: Path, fn_name: str) -> bool:
    """Assert that within `fn_name`, every call to _emit_audit_update passes
    on_audit_update as its first argument. This guards against future regressions
    where someone replaces the param with a literal None.
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == fn_name:
            calls = [
                c
                for c in ast.walk(node)
                if isinstance(c, ast.Call)
                and isinstance(c.func, ast.Name)
                and c.func.id == "_emit_audit_update"
            ]
            if not calls:
                return False
            for call in calls:
                if not call.args:
                    return False
                first = call.args[0]
                if not (isinstance(first, ast.Name) and first.id == "on_audit_update"):
                    return False
            return True
    return False


class TestRepairFunctionAcceptsCallback:
    """Executor-level guarantees that repair callbacks are still wired.

    The public workspace entrypoint is Repair Orchestration v2; these checks cover
    the internal chapter executors that still emit merged audit updates.
    """

    def test_chapter_continuity_repair_impl_accepts_on_audit_update(self) -> None:
        sig = inspect.signature(
            execution_repair_continuity._execute_chapter_continuity_repair_impl
        )
        param = sig.parameters.get("on_audit_update")
        assert param is not None
        assert param.kind == inspect.Parameter.KEYWORD_ONLY
        assert param.default is None

    def test_chapter_causal_repair_impl_accepts_on_audit_update(self) -> None:
        sig = inspect.signature(execution_repair_causal._execute_chapter_causal_repair_impl)
        param = sig.parameters.get("on_audit_update")
        assert param is not None
        assert param.kind == inspect.Parameter.KEYWORD_ONLY
        assert param.default is None

    def test_chapter_continuity_repair_impl_calls_emit_with_param(self) -> None:
        path = Path(execution_repair_continuity.__file__)
        assert _emit_call_node_passes_param(path, "_execute_chapter_continuity_repair_impl")

    def test_chapter_causal_repair_impl_calls_emit_with_param(self) -> None:
        path = Path(execution_repair_causal.__file__)
        assert _emit_call_node_passes_param(path, "_execute_chapter_causal_repair_impl")
