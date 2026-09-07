"""Unified exception handling framework for Novel Forge.

This module defines a consistent exception hierarchy and provides
utilities for proper error handling across the codebase.
"""

from __future__ import annotations

import contextlib
import json
import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, TypeVar

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

T = TypeVar("T")

logger = logging.getLogger(__name__)


@dataclass
class RecoverySuggestion:
    """Suggested recovery action for an exception."""
    
    action: str
    description: str
    confidence: float = 1.0


@dataclass
class ExceptionReport:
    """Structured exception report for debugging and logging."""
    
    error_code: str
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    context: dict[str, Any] = field(default_factory=dict)
    stack_trace: str = ""
    suggestions: list[RecoverySuggestion] = field(default_factory=list)
    recoverable: bool = False
    
    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary."""
        return {
            "error_code": self.error_code,
            "message": self.message,
            "timestamp": self.timestamp,
            "context": self.context,
            "stack_trace": self.stack_trace,
            "suggestions": [
                {"action": s.action, "description": s.description, "confidence": s.confidence}
                for s in self.suggestions
            ],
            "recoverable": self.recoverable,
        }
    
    def to_json(self, indent: int = 2) -> str:
        """Convert report to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
    
    def save_to_file(self, path: str) -> None:
        """Save report to JSON file."""
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.to_json())


class NovelForgeException(Exception):
    """Base exception for all Novel Forge errors."""

    def __init__(
        self,
        message: str,
        error_code: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ):
        """Initialize exception.
        
        Args:
            message: Human-readable error message
            error_code: Machine-readable error code (e.g., 'json_parse_error')
            context: Additional context information
            cause: Original exception that caused this
        """
        self.message = message
        self.error_code = error_code
        self.context = context or {}
        self.cause = cause
        super().__init__(message)

    def __str__(self) -> str:
        """Return string representation."""
        parts = [f"[{self.error_code}] {self.message}"]
        if self.context:
            parts.append(f"Context: {self.context}")
        return " ".join(parts)
    
    def to_report(self, include_stack: bool = True) -> ExceptionReport:
        """Convert exception to structured report."""
        stack = ""
        if include_stack:
            stack = traceback.format_exc()
        
        return ExceptionReport(
            error_code=self.error_code,
            message=self.message,
            context=self.context.copy(),
            stack_trace=stack,
            suggestions=self._get_suggestions(),
            recoverable=self._is_recoverable(),
        )
    
    def _get_suggestions(self) -> list[RecoverySuggestion]:
        """Get recovery suggestions for this exception type."""
        return []
    
    def _is_recoverable(self) -> bool:
        """Check if this exception is potentially recoverable."""
        if isinstance(self, LLMException):
            return bool(self.context.get("is_transient", False))
        return False


class ValidationException(NovelForgeException):
    """Data validation failed."""

    def __init__(
        self,
        message: str,
        field: str | None = None,
        value: Any = None,
        cause: Exception | None = None,
    ):
        """Initialize validation exception.
        
        Args:
            message: Error description
            field: Field that failed validation
            value: Value that caused validation failure
            cause: Original exception
        """
        context = {"field": field, "value": value}
        super().__init__(message, "validation_error", context, cause)


class JSONParsingException(NovelForgeException):
    """JSON parsing or repair failed."""

    MAX_REPAIR_ATTEMPTS = 3

    def __init__(
        self,
        message: str,
        original_text: str | None = None,
        repair_attempts: int = 0,
        cause: Exception | None = None,
    ):
        """Initialize JSON parsing exception.

        Args:
            message: Error description
            original_text: Original JSON text that failed to parse
            repair_attempts: Number of repair attempts made
            cause: Original exception
        """
        context = {
            "original_text": original_text[:100] + "..." if original_text and len(original_text) > 100 else original_text,
            "repair_attempts": repair_attempts,
        }
        super().__init__(message, "json_parse_error", context, cause)

    def _is_recoverable(self) -> bool:
        """JSON parsing may be recoverable if repair attempts not exhausted."""
        return bool(self.context.get("repair_attempts", 0) < self.MAX_REPAIR_ATTEMPTS)

    def _get_suggestions(self) -> list[RecoverySuggestion]:
        """Get recovery suggestions for JSON parsing errors."""
        suggestions = []
        attempts = self.context.get("repair_attempts", 0)

        if attempts < self.MAX_REPAIR_ATTEMPTS:
            suggestions.append(RecoverySuggestion(
                action="retry_repair",
                description=f"Retry JSON repair (attempt {attempts + 1}/{self.MAX_REPAIR_ATTEMPTS})",
                confidence=0.7,
            ))

        suggestions.append(RecoverySuggestion(
            action="validate_input",
            description="Validate and fix JSON syntax before parsing",
            confidence=0.85,
        ))

        return suggestions


class LLMException(NovelForgeException):
    """LLM call failed."""

    def __init__(
        self,
        message: str,
        is_transient: bool = False,
        task_type: str | None = None,
        cause: Exception | None = None,
    ):
        """Initialize LLM exception.

        Args:
            message: Error description
            is_transient: Whether this is a transient/retryable error
            task_type: Type of LLM task that failed
            cause: Original exception
        """
        context = {"is_transient": is_transient, "task_type": task_type}
        super().__init__(message, "llm_error", context, cause)

    def _is_recoverable(self) -> bool:
        """LLM errors are recoverable if marked as transient."""
        return bool(self.context.get("is_transient", False))

    def _get_suggestions(self) -> list[RecoverySuggestion]:
        """Get recovery suggestions for LLM errors."""
        suggestions = []
        task_type = self.context.get("task_type")

        if self.context.get("is_transient"):
            suggestions.append(RecoverySuggestion(
                action="retry_llm_call",
                description="Retry the LLM call (transient error detected)",
                confidence=0.8,
            ))
        else:
            suggestions.append(RecoverySuggestion(
                action="check_model_status",
                description="Check if the LLM model is available and properly configured",
                confidence=0.7,
            ))

        if task_type:
            suggestions.append(RecoverySuggestion(
                action="simplify_task",
                description=f"Simplify the {task_type} task or reduce input size",
                confidence=0.6,
            ))

        return suggestions


class ConfigurationException(NovelForgeException):
    """Configuration error."""

    def __init__(
        self,
        message: str,
        config_key: str | None = None,
        expected_type: str | None = None,
        cause: Exception | None = None,
    ):
        """Initialize configuration exception.
        
        Args:
            message: Error description
            config_key: Configuration key that failed
            expected_type: Expected value type
            cause: Original exception
        """
        context = {"config_key": config_key, "expected_type": expected_type}
        super().__init__(message, "config_error", context, cause)


class PersistenceException(NovelForgeException):
    """Data persistence operation failed."""

    def __init__(
        self,
        message: str,
        operation: str | None = None,
        path: str | None = None,
        cause: Exception | None = None,
    ):
        """Initialize persistence exception.
        
        Args:
            message: Error description
            operation: Type of operation (read/write/delete)
            path: File/resource path involved
            cause: Original exception
        """
        context = {"operation": operation, "path": path}
        super().__init__(message, "persistence_error", context, cause)


def handle_exception(
    exc: Exception,
    context: dict[str, Any] | None = None,
    *,
    log_level: int = logging.ERROR,
    reraise: bool = False,
) -> None:
    """Unified exception handling function.
    
    Logs exception with appropriate level and provides diagnostic info.
    
    Args:
        exc: Exception to handle
        context: Additional context for logging
        log_level: Logging level (default ERROR)
        reraise: Whether to re-raise after handling
    """
    extra: dict[str, Any] = {}
    
    if isinstance(exc, NovelForgeException):
        extra["error_code"] = exc.error_code
        extra["cause"] = type(exc.cause).__name__ if exc.cause else None
        
        if isinstance(exc, ValidationException):
            extra["field"] = exc.context.get("field")
            log_level = logging.WARNING
        
        elif isinstance(exc, JSONParsingException):
            extra["repair_attempts"] = exc.context.get("repair_attempts")
        
        elif isinstance(exc, LLMException):
            is_transient = exc.context.get("is_transient")
            if is_transient:
                log_level = logging.WARNING
            extra["is_transient"] = is_transient
        
        elif isinstance(exc, ConfigurationException):
            extra["config_key"] = exc.context.get("config_key")
        
        elif isinstance(exc, PersistenceException):
            extra["operation"] = exc.context.get("operation")
            extra["path"] = exc.context.get("path")
    
    # Add custom context
    if context:
        extra.update(context)
    
    # Log with appropriate level
    logger.log(
        log_level,
        "%s",
        str(exc),
        exc_info=exc,
        extra=extra if extra else None,
    )
    
    if reraise:
        raise exc


def ensure_exception_type(
    exc: Exception,
    target_exc_type: type[NovelForgeException],
    **kwargs: Any,
) -> NovelForgeException:
    """Convert exception to target type if not already.
    
    Args:
        exc: Exception to potentially convert
        target_exc_type: Target exception type
        **kwargs: Arguments to pass to target exception constructor
        
    Returns:
        Exception of target type
    """
    if isinstance(exc, target_exc_type):
        return exc
    
    message = str(exc)
    if not message:
        message = f"Converted from {type(exc).__name__}"

    kwargs.setdefault("error_code", "converted_error")
    kwargs["cause"] = exc
    return target_exc_type(message, **kwargs)


class NetworkException(NovelForgeException):
    """Network-related operation failed."""
    
    def __init__(
        self,
        message: str,
        url: str | None = None,
        status_code: int | None = None,
        timeout: bool = False,
        cause: Exception | None = None,
    ):
        context = {
            "url": url,
            "status_code": status_code,
            "timeout": timeout,
        }
        super().__init__(message, "network_error", context, cause)
        self.url = url
        self.status_code = status_code
        self.timeout = timeout
    
    def _is_recoverable(self) -> bool:
        """Network errors may be recoverable if transient."""
        return self.timeout or (self.status_code is not None and 500 <= self.status_code < 600)
    
    def _get_suggestions(self) -> list[RecoverySuggestion]:
        """Get recovery suggestions for network errors."""
        suggestions = []
        
        if self.timeout:
            suggestions.append(RecoverySuggestion(
                action="increase_timeout",
                description="Increase API timeout value in settings",
                confidence=0.9,
            ))
            suggestions.append(RecoverySuggestion(
                action="retry_later",
                description="Retry the request after a short delay",
                confidence=0.7,
            ))
        
        if self.status_code == 429:
            suggestions.append(RecoverySuggestion(
                action="rate_limit",
                description="Rate limit hit. Enable rate limiting or wait before retrying",
                confidence=0.95,
            ))
        
        if self.status_code and 500 <= self.status_code < 600:
            suggestions.append(RecoverySuggestion(
                action="server_error",
                description="Server error. Retry after waiting for server to recover",
                confidence=0.8,
            ))
        
        return suggestions


class TimeoutException(NovelForgeException):
    """Operation timed out."""
    
    def __init__(
        self,
        message: str,
        operation: str | None = None,
        timeout_seconds: float | None = None,
        cause: Exception | None = None,
    ):
        context = {
            "operation": operation,
            "timeout_seconds": timeout_seconds,
        }
        super().__init__(message, "timeout_error", context, cause)
    
    def _is_recoverable(self) -> bool:
        return True
    
    def _get_suggestions(self) -> list[RecoverySuggestion]:
        return [
            RecoverySuggestion(
                action="increase_timeout",
                description=f"Increase timeout limit (current: {self.context.get('timeout_seconds')}s)",
                confidence=0.9,
            ),
            RecoverySuggestion(
                action="simplify_request",
                description="Reduce request complexity or split into smaller operations",
                confidence=0.6,
            ),
        ]


def log_exception(
    exc: Exception,
    context: dict[str, Any] | None = None,
    *,
    logger_instance: logging.Logger | None = None,
    log_level: int = logging.ERROR,
    include_suggestions: bool = True,
) -> ExceptionReport:
    """Enhanced exception logging with structured reports.
    
    Args:
        exc: Exception to log
        context: Additional context information
        logger_instance: Logger to use (defaults to module logger)
        log_level: Logging level
        include_suggestions: Include recovery suggestions in log
        
    Returns:
        ExceptionReport for further analysis or storage
    """
    log = logger_instance or logger
    
    extra_context = context.copy() if context else {}
    
    if isinstance(exc, NovelForgeException):
        report = exc.to_report(include_stack=True)
        if exc.cause:
            extra_context["root_cause"] = type(exc.cause).__name__
    else:
        report = ExceptionReport(
            error_code="unknown_error",
            message=str(exc),
            context=extra_context,
            stack_trace=traceback.format_exc(),
        )
    
    extra_context.update(report.context)
    
    log_msg = f"[{report.error_code}] {report.message}"
    
    if include_suggestions and report.suggestions:
        suggestions_text = "; ".join(
            f"{s.action} ({s.confidence:.0%} confidence)" 
            for s in report.suggestions[:3]
        )
        log_msg += f" | Suggestions: {suggestions_text}"
    
    log.log(
        log_level,
        "%s | Context: %s",
        log_msg,
        extra_context,
        exc_info=True,
    )
    
    return report


def with_exception_handling(
    *expected_exceptions: type[Exception],
    default_value: Any = None,
    on_exception: Callable[[Exception, ExceptionReport], Any] | None = None,
    reraise: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator for automatic exception handling.

    Args:
        *expected_exceptions: Exception types to catch
        default_value: Value to return on exception
        on_exception: Callback function(exception, context)
        reraise: Whether to re-raise after handling

    Returns:
        Decorated function

    Example:
        @with_exception_handling(ValueError, TypeError, default_value=[])
        def risky_operation():
            return json.loads(user_input)
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except expected_exceptions as e:
                report = log_exception(e, {"function": func.__name__})

                if on_exception:
                    on_exception(e, report)

                if reraise:
                    raise

                return default_value

        return wrapper
    return decorator


def safe_execute(
    func: Callable[..., T],
    *args: Any,
    default: T | None = None,
    **kwargs: Any,
) -> T | None:
    """Execute a callable safely, returning *default* on any exception.

    This is a lightweight alternative to :func:`with_exception_handling`
    for one-off call sites where a decorator is inconvenient.

    Args:
        func: Callable to execute.
        *args: Positional arguments for *func*.
        default: Value to return if *func* raises.
        **kwargs: Keyword arguments for *func*.

    Returns:
        Result of ``func(*args, **kwargs)`` or *default* on failure.

    Example:
        >>> result = safe_execute(int, "42")
        >>> result
        42
        >>> result = safe_execute(int, "not_a_number", default=0)
        >>> result
        0
    """
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        logger.debug(
            "safe_execute suppressed %s in %s: %s",
            type(exc).__name__,
            getattr(func, "__name__", "<callable>"),
            exc,
        )
        return default


def safe_load_json(data: str, model: type[T]) -> T | None:
    """Safely parse JSON and validate against a Pydantic *model*.

    Args:
        data: Raw JSON string.
        model: Pydantic model class (must be a subclass of ``BaseModel``).

    Returns:
        Validated model instance, or ``None`` if parsing/validation fails.

    Example:
        >>> from pydantic import BaseModel
        >>> class User(BaseModel):
        ...     name: str
        >>> user = safe_load_json('{"name": "Ada"}', User)
        >>> user is not None
        True
        >>> safe_load_json('bad json', User) is None
        True
    """
    try:
        if issubclass(model, BaseModel):
            return model.model_validate_json(data)
        # Fallback for non-pydantic models — attempt direct JSON parsing
        parsed = json.loads(data)
        return model(**parsed)
    except (json.JSONDecodeError, PydanticValidationError, TypeError) as exc:
        logger.warning(
            "safe_load_json failed for %s: %s",
            getattr(model, "__name__", "<unknown>"),
            exc,
        )
        return None


def guard_file_operation(
    path: Path,
    operation: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T | None:
    """Guard a file *operation* so that failures are logged but not raised.

    Args:
        path: File path being operated on (used for logging).
        operation: Callable performing the I/O (e.g. ``Path.read_text``).
        *args: Positional arguments forwarded to *operation*.
        **kwargs: Keyword arguments forwarded to *operation*.

    Returns:
        Result of ``operation(*args, **kwargs)`` or ``None`` on failure.

    Example:
        >>> text = guard_file_operation(
        ...     Path("/tmp/missing.txt"), Path("/tmp/missing.txt").read_text
        ... )
        >>> text is None
        True
    """
    try:
        return operation(*args, **kwargs)
    except OSError as exc:
        logger.warning(
            "File operation failed for %s: %s",
            path,
            exc,
        )
        return None


class with_logging(contextlib.ContextDecorator):
    """Context manager / decorator that catches and logs exceptions.

    When used as a context manager, any exception raised inside the block
    is logged at the configured level and then re-raised so the caller can
    still handle it.

    When used as a decorator, exceptions are logged and then re-raised.

    Args:
        logger_instance: Logger to use. Defaults to the module logger.
        level: Logging level (default ``logging.WARNING``).
        label: Optional label included in the log message for context.

    Example (context manager):
        >>> import logging
        >>> with with_logging(level=logging.ERROR, label="parse"):
        ...     json.loads("invalid")
        Traceback (most recent call last):
            ...
        json.JSONDecodeError: ...

    Example (decorator):
        >>> @with_logging(level=logging.INFO, label="worker")
        ... def do_work():
        ...     raise RuntimeError("boom")
        >>> do_work()  # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
            ...
        RuntimeError: boom
    """

    def __init__(
        self,
        logger_instance: logging.Logger | None = None,
        level: int = logging.WARNING,
        label: str | None = None,
    ) -> None:
        self.logger = logger_instance or logger
        self.level = level
        self.label = label

    def __enter__(self) -> with_logging:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> Literal[False]:
        if exc_val is not None:
            prefix = f"[{self.label}] " if self.label else ""
            self.logger.log(
                self.level,
                "%sCaught %s: %s",
                prefix,
                exc_type.__name__ if exc_type else "<unknown>",
                exc_val,
                exc_info=True,
            )
        return False


__all__ = [
    "ConfigurationException",
    "ExceptionReport",
    "JSONParsingException",
    "LLMException",
    "NetworkException",
    "NovelForgeException",
    "PersistenceException",
    "RecoverySuggestion",
    "TimeoutException",
    "ValidationException",
    "ensure_exception_type",
    "guard_file_operation",
    "handle_exception",
    "log_exception",
    "safe_execute",
    "safe_load_json",
    "with_exception_handling",
    "with_logging",
]
