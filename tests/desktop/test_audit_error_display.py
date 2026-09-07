"""Tests for AuditError display in desktop job failure handling."""

from __future__ import annotations

from novel_forge.workspace.book_ops.execution_book_common import AuditError


class TestAuditErrorDataclass:
    """Tests for AuditError dataclass structure."""

    def test_structured_error_fields(self) -> None:
        """Verify AuditError contains all required structured fields."""
        error = AuditError(
            message="上下文长度超限",
            recoverable=True,
            suggested_action="减少审计章节数或增加上下文预算",
            checkpoint_available=False,
            error_type="timeout",
        )
        assert error.message == "上下文长度超限"
        assert error.recoverable is True
        assert error.suggested_action == "减少审计章节数或增加上下文预算"
        assert error.checkpoint_available is False
        assert error.error_type == "timeout"

    def test_to_dict_includes_all_fields(self) -> None:
        """Verify to_dict() returns all structured fields."""
        error = AuditError(
            message="测试错误",
            recoverable=False,
            suggested_action="检查模型配置后重试",
            checkpoint_available=True,
            error_type="model",
        )
        error_dict = error.to_dict()
        assert error_dict["message"] == "测试错误"
        assert error_dict["recoverable"] is False
        assert error_dict["suggested_action"] == "检查模型配置后重试"
        assert error_dict["checkpoint_available"] is True
        assert error_dict["error_type"] == "model"

    def test_default_error_type(self) -> None:
        """Verify error_type defaults to 'unknown'."""
        error = AuditError(
            message="未知错误",
            recoverable=False,
            suggested_action="请联系支持",
            checkpoint_available=False,
        )
        assert error.error_type == "unknown"


class TestAuditErrorDisplay:
    """Tests for AuditError display formatting."""

    def test_recoverable_shows_retry(self) -> None:
        """Verify recoverable errors show retry option in formatted message."""
        error = AuditError(
            message="模型调用失败",
            recoverable=True,
            suggested_action="检查模型配置后重试",
            checkpoint_available=True,
            error_type="model",
        )
        parts = [f"【审计错误 · {error.error_type}】", error.message]
        if error.suggested_action:
            parts.append(f"建议：{error.suggested_action}")
        if error.recoverable:
            parts.append("✓ 可重试")
        if error.checkpoint_available:
            parts.append("✓ 检查点可用")
        formatted = "\n".join(parts)
        assert "✓ 可重试" in formatted
        assert "建议：检查模型配置后重试" in formatted

    def test_checkpoint_available_mentioned(self) -> None:
        """Verify checkpoint availability is mentioned in formatted message."""
        error = AuditError(
            message="存储错误",
            recoverable=False,
            suggested_action="检查项目目录权限",
            checkpoint_available=True,
            error_type="storage",
        )
        parts = [f"【审计错误 · {error.error_type}】", error.message]
        if error.suggested_action:
            parts.append(f"建议：{error.suggested_action}")
        if error.recoverable:
            parts.append("✓ 可重试")
        if error.checkpoint_available:
            parts.append("✓ 检查点可用")
        formatted = "\n".join(parts)
        assert "✓ 检查点可用" in formatted
        assert "【审计错误 · storage】" in formatted

    def test_non_recoverable_no_retry(self) -> None:
        """Verify non-recoverable errors do not show retry option."""
        error = AuditError(
            message="至少需要2个已完成章节",
            recoverable=False,
            suggested_action="至少需要2个已完成章节",
            checkpoint_available=False,
            error_type="validation",
        )
        parts = [f"【审计错误 · {error.error_type}】", error.message]
        if error.suggested_action:
            parts.append(f"建议：{error.suggested_action}")
        if error.recoverable:
            parts.append("✓ 可重试")
        if error.checkpoint_available:
            parts.append("✓ 检查点可用")
        formatted = "\n".join(parts)
        assert "✓ 可重试" not in formatted
        assert "✓ 检查点可用" not in formatted
        assert "【审计错误 · validation】" in formatted

    def test_context_length_error_classification(self) -> None:
        """Verify context length errors are properly classified."""
        from novel_forge.workspace.book_ops.execution_book_entry import _classify_audit_exception

        exc = Exception("context length exceeded")
        error = _classify_audit_exception(exc, checkpoint_exists=False)
        assert error.recoverable is True
        assert error.error_type == "timeout"
        assert "减少审计章节数" in error.suggested_action

    def test_storage_error_classification(self) -> None:
        """Verify storage errors are properly classified."""
        from novel_forge.core.exceptions import StorageError
        from novel_forge.workspace.book_ops.execution_book_entry import _classify_audit_exception

        exc = StorageError("permission denied")
        error = _classify_audit_exception(exc, checkpoint_exists=False)
        assert error.recoverable is False
        assert error.error_type == "storage"
        assert "检查项目目录权限" in error.suggested_action

    def test_validation_error_classification(self) -> None:
        """Verify validation errors are properly classified."""
        from novel_forge.workspace.book_ops.execution_book_entry import _classify_audit_exception

        exc = ValueError("至少需要 2 个已完成章节才能进行全书一致性检查。")
        error = _classify_audit_exception(exc, checkpoint_exists=False)
        assert error.recoverable is False
        assert error.error_type == "validation"
        assert "至少需要2个已完成章节" in error.suggested_action
