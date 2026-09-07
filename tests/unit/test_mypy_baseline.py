"""Regression tests for the Mypy baseline diagnostic identity."""

from __future__ import annotations

from scripts.check_mypy_baseline import _diagnostic_fingerprints


def test_diagnostic_fingerprint_ignores_source_lines_and_volatile_messages() -> None:
    output = "\n".join(
        [
            'novel_forge/example.py:7: error: Incompatible return value type (got "str", expected "int")  [return-value]',
            'novel_forge/example.py:93: error: Incompatible return value type (got "bytes", expected "int")  [return-value]',
        ]
    )

    fingerprints, unparsable = _diagnostic_fingerprints(output)

    assert fingerprints == [
        "novel_forge/example.py:return-value",
        "novel_forge/example.py:return-value",
    ]
    assert unparsable == []
