"""Tests for the exception hierarchy unification."""

from __future__ import annotations

from novel_forge.core.exceptions import (
    ComplianceViolationError,
    ConsistencyViolationError,
    ModelGatewayError,
    NovelForgeError,
    PipelineError,
    RuntimeConfigError,
    StorageError,
)
from novel_forge.core.exceptions_framework import (
    ConfigurationException,
    LLMException,
    NovelForgeException,
    PersistenceException,
    ValidationException,
)


class TestExceptionUnification:
    """Verify old exceptions are unified with the new framework."""

    def test_novel_forge_error_is_novel_forge_exception(self):
        err = NovelForgeError("test")
        assert isinstance(err, NovelForgeException)
        assert err.error_code == "novel_forge_error"

    def test_consistency_violation_is_validation(self):
        err = ConsistencyViolationError(["violation_1", "violation_2"])
        assert isinstance(err, NovelForgeError)
        assert isinstance(err, ValidationException)
        assert isinstance(err, NovelForgeException)
        assert err.violations == ["violation_1", "violation_2"]
        assert "consistency_violation" in err.error_code

    def test_compliance_violation_is_validation(self):
        err = ComplianceViolationError("bad content")
        assert isinstance(err, ValidationException)
        assert err.reason == "bad content"

    def test_model_gateway_error_is_llm(self):
        err = ModelGatewayError("connection failed", is_transient=True)
        assert isinstance(err, LLMException)
        assert err.is_transient_error is True

    def test_storage_error_is_persistence(self):
        err = StorageError("write failed", operation="save")
        assert isinstance(err, PersistenceException)

    def test_pipeline_error(self):
        err = PipelineError("draft", "timeout")
        assert isinstance(err, NovelForgeError)
        assert err.step_name == "draft"
        assert "pipeline_error" in err.error_code

    def test_runtime_config_error_is_configuration(self):
        err = RuntimeConfigError("missing key", config_key="api_key")
        assert isinstance(err, ConfigurationException)

    def test_all_have_error_code(self):
        """All unified exceptions should carry an error_code."""
        exceptions = [
            NovelForgeError("test"),
            ConsistencyViolationError(["v"]),
            ComplianceViolationError("r"),
            ModelGatewayError("m"),
            StorageError("s"),
            PipelineError("step", "detail"),
            RuntimeConfigError("cfg"),
        ]
        for exc in exceptions:
            assert hasattr(exc, "error_code")
            assert exc.error_code
