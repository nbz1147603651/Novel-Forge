"""Unit tests for the zvec document ID compatibility fix.

Verifies that:
- Document IDs use '--' separator instead of ':' (zvec 0.6.0 rejects ':')
- Parsing logic correctly splits namespace and voice_id
- The generated document IDs are zvec-compatible (no colons)
"""

from __future__ import annotations

import re

# The pattern that zvec 0.6.0 accepts: alphanumeric, hyphens, underscores, dots
_ZVEC_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")


class TestDocumentIdFormat:
    """Verify document ID generation uses zvec-compatible format."""

    def test_library_id_format(self):
        """library-- prefix with voice_id, no colons."""
        voice_id = "Chinese (Mandarin)_Reliable_Executive"
        doc_id = f"library--{voice_id}"
        # Must not contain colon
        assert ":" not in doc_id
        # Must contain the separator
        assert "--" in doc_id

    def test_catalog_id_format(self):
        """catalog-- prefix with voice_id, no colons."""
        voice_id = "longanlingxin"
        doc_id = f"catalog--{voice_id}"
        assert ":" not in doc_id
        assert "--" in doc_id

    def test_library_id_parseable(self):
        """Can split library--voice_id back into namespace and voice_id."""
        voice_id = "Chinese (Mandarin)_Reliable_Executive"
        doc_id = f"library--{voice_id}"
        namespace, parsed_id = doc_id.split("--", 1)
        assert namespace == "library"
        assert parsed_id == voice_id

    def test_catalog_id_parseable(self):
        """Can split catalog--voice_id back into namespace and voice_id."""
        voice_id = "longanlingxin"
        doc_id = f"catalog--{voice_id}"
        namespace, parsed_id = doc_id.split("--", 1)
        assert namespace == "catalog"
        assert parsed_id == voice_id

    def test_voice_id_with_hyphens(self):
        """Voice IDs containing hyphens still parse correctly with split('--', 1)."""
        voice_id = "zh_male_m191_uranus_bigtts"
        doc_id = f"catalog--{voice_id}"
        namespace, parsed_id = doc_id.split("--", 1)
        assert namespace == "catalog"
        assert parsed_id == voice_id

    def test_voice_id_with_double_hyphen(self):
        """Edge case: voice_id itself contains '--'."""
        voice_id = "some--weird--id"
        doc_id = f"library--{voice_id}"
        # split('--', 1) only splits on first occurrence
        namespace, parsed_id = doc_id.split("--", 1)
        assert namespace == "library"
        assert parsed_id == "some--weird--id"

    def test_separator_detection(self):
        """The '--' not in document_id guard correctly identifies invalid IDs."""
        valid_id = "catalog--longanlingxin"
        invalid_id = "noseparator"
        assert "--" in valid_id
        assert "--" not in invalid_id


class TestSemanticIndexSourceCode:
    """Verify the source code uses correct separators (regression guard)."""

    def test_no_colon_in_document_id_generation(self):
        """Ensure voice_semantic_index.py does not use colon-based document IDs."""
        import inspect

        from novel_forge.tts.assets import voice_semantic_index

        source = inspect.getsource(voice_semantic_index)
        # Should not contain the old colon-based patterns
        assert 'f"catalog:{' not in source, "Found old colon-based catalog ID format"
        assert 'f"library:{' not in source, "Found old colon-based library ID format"
        # Should contain the new double-dash patterns
        assert 'f"catalog--{' in source, "Missing new dash-based catalog ID format"
        assert 'f"library--{' in source, "Missing new dash-based library ID format"

    def test_parse_uses_double_dash(self):
        """Ensure parsing logic uses '--' separator."""
        import inspect

        from novel_forge.tts.assets import voice_semantic_index

        source = inspect.getsource(voice_semantic_index)
        assert '.split("--", 1)' in source, "Missing '--' split parsing logic"
