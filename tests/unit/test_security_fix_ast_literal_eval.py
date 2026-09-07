"""Tests for the security fix in gateway router's error payload parsing."""

import pytest

from novel_forge.gateway.router import _extract_embedded_error_payload


def test_extract_embedded_error_payload_valid_json():
    """Test that valid JSON payloads are correctly parsed."""
    message = 'Error code: 500 - {"error": {"message": "test"}, "request_id": "123"}'
    
    result = _extract_embedded_error_payload(message)
    
    assert result == {"error": {"message": "test"}, "request_id": "123"}


def test_extract_embedded_error_payload_invalid_json():
    """Test that invalid JSON payloads return None."""
    message = "Error code: 500 - {'error': {'message': 'test'"  # Invalid JSON (single quotes)
    
    result = _extract_embedded_error_payload(message)
    
    assert result is None


def test_extract_embedded_error_payload_malformed_content():
    """Test that malformed content returns None."""
    malicious_message = "Error code: 500 - {'error': __import__('os').system('echo test')}"
    
    result = _extract_embedded_error_payload(malicious_message)
    
    assert result is None


def test_extract_embedded_error_payload_large_input():
    """Test that very large inputs are rejected."""
    large_message = "Error code: 500 - " + '{"key": "' + "x" * 11000 + '"}'
    
    result = _extract_embedded_error_payload(large_message)
    
    assert result is None


def test_extract_embedded_error_payload_no_match():
    """Test that messages without JSON pattern return None."""
    message = "Simple error message without JSON"
    
    result = _extract_embedded_error_payload(message)
    
    assert result is None


def test_extract_embedded_error_payload_non_dict_result():
    """Test that non-dict JSON values return None."""
    message = 'Error code: 500 - "not a dict"'
    
    result = _extract_embedded_error_payload(message)
    
    assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
