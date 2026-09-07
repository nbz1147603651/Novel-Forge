"""Unit tests for the exception handling framework."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from novel_forge.core.exceptions import (
    ConsistencyError,
    StateError,
    ValidationError,
)
from novel_forge.core.exceptions_framework import (
    ExceptionReport,
    JSONParsingException,
    LLMException,
    NovelForgeException,
    RecoverySuggestion,
    ValidationException,
    ensure_exception_type,
    guard_file_operation,
    handle_exception,
    log_exception,
    safe_execute,
    safe_load_json,
    with_exception_handling,
    with_logging,
)

# ── RecoverySuggestion & ExceptionReport ────────────────────────────────


class TestRecoverySuggestion:
    def test_defaults(self) -> None:
        rs = RecoverySuggestion(action="retry", description="try again")
        assert rs.confidence == 1.0

    def test_custom_confidence(self) -> None:
        rs = RecoverySuggestion(action="retry", description="try again", confidence=0.8)
        assert rs.confidence == 0.8


class TestExceptionReport:
    def test_to_dict(self) -> None:
        report = ExceptionReport(
            error_code="E001",
            message="boom",
            suggestions=[RecoverySuggestion("retry", "again")],
        )
        d = report.to_dict()
        assert d["error_code"] == "E001"
        assert d["message"] == "boom"
        assert d["recoverable"] is False
        assert len(d["suggestions"]) == 1

    def test_to_json(self) -> None:
        report = ExceptionReport(error_code="E002", message="bad")
        text = report.to_json()
        parsed = json.loads(text)
        assert parsed["error_code"] == "E002"

    def test_save_to_file(self, tmp_path: Path) -> None:
        report = ExceptionReport(error_code="E003", message="saved")
        path = tmp_path / "report.json"
        report.save_to_file(str(path))
        assert path.exists()
        assert "E003" in path.read_text()


# ── NovelForgeException base class ──────────────────────────────────────


class TestNovelForgeException:
    @pytest.mark.parametrize(
        "context,expected_contains",
        [
            ({"k": "v"}, ["[code] msg", "Context:"]),
            (None, ["[code] msg"]),
        ],
    )
    def test_str(self, context, expected_contains) -> None:
        exc = NovelForgeException("msg", "code", context=context)
        for part in expected_contains:
            assert part in str(exc)

    def test_to_report_includes_stack(self) -> None:
        exc = NovelForgeException("msg", "code")
        try:
            raise exc
        except NovelForgeException:
            report = exc.to_report(include_stack=True)
            assert "Traceback" in report.stack_trace

    def test_to_report_excludes_stack(self) -> None:
        exc = NovelForgeException("msg", "code")
        report = exc.to_report(include_stack=False)
        assert report.stack_trace == ""


# ── Concrete exception types ────────────────────────────────────────────


class TestValidationException:
    def test_init(self) -> None:
        exc = ValidationException("bad value", field="age", value=-1)
        assert exc.error_code == "validation_error"
        assert exc.context["field"] == "age"

    def test_recoverable(self) -> None:
        exc = ValidationException("bad")
        assert exc._is_recoverable() is False


class TestJSONParsingException:
    @pytest.mark.parametrize(
        "repair_attempts,expected_recoverable,expected_action",
        [
            (1, True, "retry_repair"),
            (3, False, None),
        ],
    )
    def test_recoverable(self, repair_attempts, expected_recoverable, expected_action) -> None:
        exc = JSONParsingException("bad", repair_attempts=repair_attempts)
        assert exc._is_recoverable() is expected_recoverable
        if expected_action:
            suggestions = exc._get_suggestions()
            assert any(s.action == expected_action for s in suggestions)


class TestLLMException:
    @pytest.mark.parametrize(
        "is_transient,task_type,expected_recoverable,expected_action",
        [
            (True, "draft", True, "retry_llm_call"),
            (False, None, False, None),
        ],
    )
    def test_recoverable(self, is_transient, task_type, expected_recoverable, expected_action) -> None:
        kwargs = {"is_transient": is_transient}
        if task_type:
            kwargs["task_type"] = task_type
        exc = LLMException("timeout" if is_transient else "bad key", **kwargs)
        assert exc._is_recoverable() is expected_recoverable
        if expected_action:
            suggestions = exc._get_suggestions()
            assert any(s.action == expected_action for s in suggestions)


# ── New exception types from exceptions.py ──────────────────────────────


class TestValidationError:
    def test_init(self) -> None:
        exc = ValidationError("invalid", field="name", value="")
        assert exc.error_code == "validation_error"
        assert exc.field == "name"
        assert exc.value == ""

    def test_message_format(self) -> None:
        exc = ValidationError("invalid", field="name")
        assert "invalid" in exc.message


class TestStateError:
    @pytest.mark.parametrize(
        "expected,actual,expected_message_parts",
        [
            ("idle", "running", ["expected 'idle'", "got 'running'"]),
            ("idle", None, ["expected 'idle'"]),
            (None, "running", ["got 'running'"]),
            (None, None, ["Invalid state for 'session'"]),
        ],
    )
    def test_message(self, expected, actual, expected_message_parts) -> None:
        kwargs = {}
        if expected:
            kwargs["expected"] = expected
        if actual:
            kwargs["actual"] = actual
        exc = StateError("session", **kwargs)
        for part in expected_message_parts:
            assert part in exc.message

    def test_context(self) -> None:
        exc = StateError("x", expected="a", actual="b")
        assert exc.context["state_name"] == "x"


class TestConsistencyError:
    @pytest.mark.parametrize(
        "details,expected_details",
        [
            (["a", "b"], ["a", "b"]),
            (None, []),
        ],
    )
    def test_init(self, details, expected_details) -> None:
        exc = ConsistencyError("broken", details=details)
        assert exc.error_code == "consistency_error"
        assert exc.details == expected_details


# ── handle_exception ────────────────────────────────────────────────────


class TestHandleException:
    def test_logs_novel_forge_exception(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.ERROR)
        exc = NovelForgeException("msg", "code")
        handle_exception(exc)
        assert "msg" in caplog.text
        assert "code" in caplog.text

    def test_reraise(self) -> None:
        exc = ValueError("boom")
        with pytest.raises(ValueError):
            handle_exception(exc, reraise=True)

    def test_validation_uses_warning_level(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        exc = ValidationException("bad", field="f")
        handle_exception(exc)
        assert "bad" in caplog.text

    def test_custom_context(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.ERROR)
        exc = NovelForgeException("msg", "code")
        handle_exception(exc, context={"user_id": 42})
        record = caplog.records[0]
        assert getattr(record, "user_id", None) == 42


# ── ensure_exception_type ───────────────────────────────────────────────


class TestEnsureExceptionType:
    def test_already_target_type(self) -> None:
        exc = ValidationException("bad")
        result = ensure_exception_type(exc, ValidationException)
        assert result is exc

    def test_converts_unknown(self) -> None:
        exc = ValueError("plain error")
        result = ensure_exception_type(exc, NovelForgeException, error_code="converted")
        assert isinstance(result, NovelForgeException)
        assert result.error_code == "converted"
        assert result.cause is exc

    def test_empty_message_fallback(self) -> None:
        exc = ValueError("")
        result = ensure_exception_type(exc, NovelForgeException)
        assert "Converted from ValueError" in result.message
        assert result.error_code == "converted_error"


# ── log_exception ───────────────────────────────────────────────────────


class TestLogException:
    def test_returns_report(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.ERROR)
        exc = NovelForgeException("msg", "code")
        report = log_exception(exc)
        assert isinstance(report, ExceptionReport)
        assert report.error_code == "code"

    def test_includes_suggestions(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.ERROR)
        exc = JSONParsingException("bad", repair_attempts=1)
        log_exception(exc)
        assert "retry_repair" in caplog.text

    def test_uses_custom_logger(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.ERROR)
        custom_logger = logging.getLogger("test_logger")
        exc = ValueError("plain")
        report = log_exception(exc, logger_instance=custom_logger)
        assert report.error_code == "unknown_error"


# ── with_exception_handling decorator ───────────────────────────────────


class TestWithExceptionHandling:
    def test_returns_result_when_ok(self) -> None:
        @with_exception_handling(ValueError, default_value=99)
        def good() -> int:
            return 42

        assert good() == 42

    def test_returns_default_on_exception(self) -> None:
        @with_exception_handling(ValueError, default_value=99)
        def bad() -> int:
            raise ValueError("boom")

        assert bad() == 99

    def test_calls_on_exception_callback(self) -> None:
        called_with: dict[str, Any] = {}

        def callback(exc: Exception, report: ExceptionReport) -> None:
            called_with["exc"] = exc
            called_with["report"] = report

        @with_exception_handling(ValueError, on_exception=callback)
        def bad() -> None:
            raise ValueError("boom")

        bad()
        assert isinstance(called_with["exc"], ValueError)
        assert isinstance(called_with["report"], ExceptionReport)

    def test_reraise_option(self) -> None:
        @with_exception_handling(ValueError, reraise=True)
        def bad() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError):
            bad()

    def test_does_not_catch_unexpected(self) -> None:
        @with_exception_handling(ValueError, default_value=99)
        def bad() -> None:
            raise TypeError("boom")

        with pytest.raises(TypeError):
            bad()


# ── safe_execute ────────────────────────────────────────────────────────


class TestSafeExecute:
    @pytest.mark.parametrize(
        "func,args,default,expected",
        [
            (int, ("42",), None, 42),
            (int, ("abc",), None, None),
            (int, ("abc",), 0, 0),
        ],
    )
    def test_safe_execute(self, func, args, default, expected) -> None:
        assert safe_execute(func, *args, default=default) == expected

    def test_logs_on_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        safe_execute(int, "abc", default=-1)
        assert "safe_execute suppressed" in caplog.text


# ── safe_load_json ──────────────────────────────────────────────────────


class _SampleModel(BaseModel):
    name: str
    age: int


class TestSafeLoadJson:
    def test_valid_pydantic_model(self) -> None:
        result = safe_load_json('{"name": "Ada", "age": 30}', _SampleModel)
        assert result is not None
        assert result.name == "Ada"
        assert result.age == 30

    @pytest.mark.parametrize(
        "json_text,expected_none",
        [
            ("not json", True),
            ('{"name": "Ada"}', True),  # missing required field
        ],
    )
    def test_invalid_returns_none(self, json_text, expected_none) -> None:
        assert safe_load_json(json_text, _SampleModel) is None

    def test_logs_on_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        safe_load_json("bad", _SampleModel)
        assert "safe_load_json failed" in caplog.text

    def test_non_pydantic_model_fallback(self) -> None:
        class Simple:
            def __init__(self, name: str) -> None:
                self.name = name

        result = safe_load_json('{"name": "test"}', Simple)
        assert result is not None
        assert result.name == "test"

    def test_non_pydantic_model_invalid_kwargs(self) -> None:
        class Bad:
            def __init__(self, missing: str) -> None:
                self.missing = missing

        assert safe_load_json('{"other": 1}', Bad) is None


# ── guard_file_operation ────────────────────────────────────────────────


class TestGuardFileOperation:
    def test_success(self, tmp_path: Path) -> None:
        file = tmp_path / "test.txt"
        file.write_text("hello")
        result = guard_file_operation(file, file.read_text)
        assert result == "hello"

    def test_missing_file_returns_none(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        missing = Path("/nonexistent/path/file.txt")
        result = guard_file_operation(missing, missing.read_text)
        assert result is None
        assert "File operation failed" in caplog.text

    def test_logs_path(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        missing = Path("/nonexistent/path/file.txt")
        guard_file_operation(missing, missing.read_text)
        assert "/nonexistent/path/file.txt" in caplog.text


# ── with_logging ────────────────────────────────────────────────────────


class TestWithLogging:
    def test_context_manager_logs_exception(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        with pytest.raises(ValueError):
            with with_logging(level=logging.WARNING, label="block"):
                raise ValueError("boom")
        assert "[block] Caught ValueError" in caplog.text

    def test_context_manager_no_exception_no_log(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        with with_logging(level=logging.WARNING):
            pass
        assert "Caught" not in caplog.text

    def test_decorator_logs_exception(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)

        @with_logging(level=logging.WARNING, label="func")
        def explode() -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            explode()
        assert "[func] Caught RuntimeError" in caplog.text

    def test_custom_logger(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.ERROR)
        custom_logger = logging.getLogger("custom_test_logger")
        custom_logger.setLevel(logging.ERROR)

        @with_logging(logger_instance=custom_logger, level=logging.ERROR)
        def explode() -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            explode()
        assert "Caught RuntimeError" in caplog.text

    def test_re_raises_after_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        with pytest.raises(KeyError):
            with with_logging(level=logging.WARNING):
                raise KeyError("missing")

    def test_custom_level(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        with pytest.raises(ValueError):
            with with_logging(level=logging.DEBUG):
                raise ValueError("low")
        assert "Caught ValueError" in caplog.text


# ── Export coverage ─────────────────────────────────────────────────────


def test_all_public_symbols_importable() -> None:
    """Ensure every public symbol can be imported from the framework module."""
    import novel_forge.core.exceptions_framework as mod

    public_names = [
        "ExceptionReport",
        "JSONParsingException",
        "LLMException",
        "NovelForgeException",
        "RecoverySuggestion",
        "ValidationException",
        "ConfigurationException",
        "PersistenceException",
        "NetworkException",
        "TimeoutException",
        "ensure_exception_type",
        "guard_file_operation",
        "handle_exception",
        "log_exception",
        "safe_execute",
        "safe_load_json",
        "with_exception_handling",
        "with_logging",
    ]
    for name in public_names:
        assert hasattr(mod, name), f"{name} missing from exceptions_framework"
