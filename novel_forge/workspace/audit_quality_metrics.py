"""Re-export shim — canonical location is core/audit_metrics.py."""

from novel_forge.core.audit_metrics import AuditQualityMetrics  # noqa: F401

__all__ = ["AuditQualityMetrics"]
