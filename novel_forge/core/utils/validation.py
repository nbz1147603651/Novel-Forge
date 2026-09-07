"""Validation utilities for common checks."""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

T = TypeVar("T")


def is_valid_json(text: str) -> bool:
    """Check if text is valid JSON.
    
    Args:
        text: Text to validate
        
    Returns:
        True if text is valid JSON, False otherwise
        
    Examples:
        >>> is_valid_json('{"a": 1}')
        True
        >>> is_valid_json('invalid')
        False
    """
    try:
        json.loads(text, strict=False)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def validate_keys(
    obj: dict[str, Any],
    required: list[str],
    *,
    strict: bool = False,
) -> tuple[bool, list[str], list[str]]:
    """Validate that required keys exist in dictionary.

    Args:
        obj: Dictionary to validate
        required: List of required keys
        strict: If True, also check that no extra keys exist

    Returns:
        Tuple of (is_valid, missing_keys, extra_keys)

    Examples:
        >>> validate_keys({"a": 1, "b": 2}, ["a"])
        (True, [], [])
        >>> validate_keys({"a": 1}, ["a", "b"])
        (False, ['b'], [])
        >>> validate_keys({"a": 1, "c": 3}, ["a", "b"], strict=True)
        (False, ['b'], ['c'])
    """
    if not isinstance(obj, dict):
        return False, required, []

    missing_keys = [key for key in required if key not in obj]

    if strict:
        obj_keys = set(obj.keys())
        required_keys = set(required)
        extra_keys = list(obj_keys - required_keys)
        is_valid = len(missing_keys) == 0 and len(extra_keys) == 0
        return is_valid, missing_keys, extra_keys

    if missing_keys:
        return False, missing_keys, []

    return True, [], []


def validate_project_id(project_id: str) -> tuple[bool, str]:
    """Validate project ID format.
    
    Args:
        project_id: Project identifier to validate
        
    Returns:
        Tuple of (is_valid, error_message)
        
    Examples:
        >>> validate_project_id("my_project_123")
        (True, '')
        >>> validate_project_id("")
        (False, 'Project ID cannot be empty')
        >>> validate_project_id("my project")
        (False, 'Project ID contains invalid characters')
    """
    if not project_id or not project_id.strip():
        return False, "Project ID cannot be empty"
    
    if len(project_id) > 255:
        return False, "Project ID exceeds maximum length of 255 characters"
    
    if not re.match(r'^[a-zA-Z0-9_-]+$', project_id):
        return False, "Project ID contains invalid characters. Use only letters, numbers, hyphens, and underscores"
    
    return True, ""


def validate_chapter_number(chapter: int, max_chapters: int = 10000) -> tuple[bool, str]:
    """Validate chapter number.
    
    Args:
        chapter: Chapter number to validate
        max_chapters: Maximum allowed chapters
        
    Returns:
        Tuple of (is_valid, error_message)
        
    Examples:
        >>> validate_chapter_number(1)
        (True, '')
        >>> validate_chapter_number(0)
        (False, 'Chapter number must be at least 1')
        >>> validate_chapter_number(-1)
        (False, 'Chapter number must be at least 1')
    """
    if not isinstance(chapter, int):
        return False, f"Chapter number must be an integer, got {type(chapter).__name__}"
    
    if chapter < 1:
        return False, "Chapter number must be at least 1"
    
    if chapter > max_chapters:
        return False, f"Chapter number exceeds maximum of {max_chapters}"
    
    return True, ""


def validate_word_count(word_count: int, min_words: int = 0, max_words: int = 1000000) -> tuple[bool, str]:
    """Validate word count.
    
    Args:
        word_count: Word count to validate
        min_words: Minimum allowed words
        max_words: Maximum allowed words
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(word_count, int):
        return False, f"Word count must be an integer, got {type(word_count).__name__}"
    
    if word_count < min_words:
        return False, f"Word count below minimum of {min_words}"
    
    if word_count > max_words:
        return False, f"Word count exceeds maximum of {max_words}"
    
    return True, ""


def validate_temperature(temp: float) -> tuple[bool, str]:
    """Validate temperature parameter for LLM calls.
    
    Args:
        temp: Temperature value to validate
        
    Returns:
        Tuple of (is_valid, error_message)
        
    Examples:
        >>> validate_temperature(0.7)
        (True, '')
        >>> validate_temperature(-0.1)
        (False, 'Temperature must be between 0.0 and 2.0')
        >>> validate_temperature(2.5)
        (False, 'Temperature must be between 0.0 and 2.0')
    """
    if not isinstance(temp, (int, float)):
        return False, f"Temperature must be a number, got {type(temp).__name__}"
    
    if temp < 0.0 or temp > 2.0:
        return False, "Temperature must be between 0.0 and 2.0"
    
    return True, ""


def validate_max_tokens(max_tokens: int) -> tuple[bool, str]:
    """Validate max_tokens parameter for LLM calls.
    
    Args:
        max_tokens: Maximum tokens value to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(max_tokens, int):
        return False, f"max_tokens must be an integer, got {type(max_tokens).__name__}"
    
    if max_tokens < 1:
        return False, "max_tokens must be at least 1"
    
    if max_tokens > 200000:
        return False, "max_tokens exceeds maximum of 200000"
    
    return True, ""


def safe_coerce_type(
    value: Any,
    target_type: type[T],
    *,
    default: T | None = None,
) -> T | None:
    """Safely coerce value to target type.
    
    Args:
        value: Value to coerce
        target_type: Target type
        default: Default value if coercion fails
        
    Returns:
        Coerced value or default
        
    Examples:
        >>> safe_coerce_type("123", int)
        123
        >>> safe_coerce_type("invalid", int, default=0)
        0
    """
    if value is None:
        return default
    
    if isinstance(value, target_type):
        return value
    
    try:
        if target_type is int:
            return int(value)  # type: ignore
        elif target_type is float:
            return float(value)  # type: ignore
        elif target_type is bool:
            if isinstance(value, str):
                return value.lower() in ("true", "yes", "1", "on")  # type: ignore
            return bool(value)  # type: ignore
        elif target_type is str:
            return str(value)  # type: ignore
        elif target_type is list:
            if isinstance(value, str):
                # Try to parse as JSON list
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed  # type: ignore
            elif isinstance(value, (tuple, set)):
                return list(value)  # type: ignore
        elif target_type is dict:
            if isinstance(value, str):
                return json.loads(value)  # type: ignore
        else:
            # Try direct instantiation
            return target_type(value)  # type: ignore
    except (ValueError, TypeError, json.JSONDecodeError):
        pass
    
    return default


def is_non_empty(value: Any) -> bool:
    """Check if value is non-empty.
    
    Args:
        value: Value to check
        
    Returns:
        True if value is not None, not empty string, not empty collection
        
    Examples:
        >>> is_non_empty("hello")
        True
        >>> is_non_empty("")
        False
        >>> is_non_empty(None)
        False
        >>> is_non_empty([])
        False
    """
    if value is None:
        return False
    if isinstance(value, (str, list, dict, set, tuple)):
        return len(value) > 0
    return True


def is_empty(value: Any) -> bool:
    """Check if value is empty.
    
    Opposite of is_non_empty.
    """
    return not is_non_empty(value)


def ensure_list(value: Any) -> list[Any]:
    """Ensure value is a list.
    
    Args:
        value: Value to convert
        
    Returns:
        Value if already list, [value] if not, [] if None
        
    Examples:
        >>> ensure_list([1, 2, 3])
        [1, 2, 3]
        >>> ensure_list("hello")
        ["hello"]
        >>> ensure_list(None)
        []
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def ensure_dict(value: Any) -> dict[str, Any]:
    """Ensure value is a dictionary.
    
    Args:
        value: Value to convert
        
    Returns:
        Value if already dict, {} if None or invalid
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return {}


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent path traversal and invalid characters.
    
    Args:
        filename: Original filename
        
    Returns:
        Sanitized filename safe for filesystem operations
        
    Examples:
        >>> sanitize_filename("my_file.txt")
        'my_file.txt'
        >>> sanitize_filename("../etc/passwd")
        '_etc_passwd'
        >>> sanitize_filename("file<>:|?*.txt")
        'file______.txt'
    """
    if not filename:
        return "unnamed_file"
    
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
    
    sanitized = re.sub(r'\.\.+', '_', sanitized)
    
    sanitized = sanitized.strip('. ')
    
    if not sanitized:
        return "unnamed_file"
    
    if len(sanitized) > 255:
        name, ext = sanitized.rsplit('.', 1) if '.' in sanitized else (sanitized, '')
        max_name_len = 255 - len(ext) - 1 if ext else 255
        sanitized = name[:max_name_len] + ('.' + ext if ext else '')
    
    return sanitized


def validate_content_length(content: str, max_chars: int = 10000000) -> tuple[bool, str]:
    """Validate content length to prevent memory issues.
    
    Args:
        content: Content to validate
        max_chars: Maximum allowed characters
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if content is None:
        return True, ""
    
    if not isinstance(content, str):
        return False, f"Content must be a string, got {type(content).__name__}"
    
    if len(content) > max_chars:
        return False, f"Content exceeds maximum length of {max_chars} characters"
    
    return True, ""


def validate_api_key_format(api_key: str, provider: str) -> tuple[bool, str]:
    """Validate API key format for different providers.

    Args:
        api_key: API key to validate
        provider: Provider name (openai, anthropic, deepseek, etc.)

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not api_key:
        return False, f"API key for {provider} is empty"

    key_patterns = {
        'openai': r'^sk-[A-Za-z0-9_-]{20,}$',
        'anthropic': r'^sk-ant-[A-Za-z0-9_-]{40,}$',
        'deepseek': r'^sk-[A-Za-z0-9]{30,}$',
        'tongyi': r'^[A-Za-z0-9]{20,}$',
        'kimi': r'^sk-[A-Za-z0-9]{30,}$',
    }

    pattern = key_patterns.get(provider.lower())

    if pattern is None:
        if len(api_key) < 10:
            return False, f"Unknown provider '{provider}': API key too short"
        return False, f"Unknown provider '{provider}': No validation pattern available"

    if not re.match(pattern, api_key):
        return False, f"Invalid API key format for {provider}"

    return True, ""
