"""Secure API key provider with optional OS keyring integration.

Falls back to environment variables when ``keyring`` is not installed.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

_logger = logging.getLogger(__name__)

_SERVICE_NAME = "novel_forge"

_keyring_mod: Optional[object] = None
_keyring_checked = False


def _get_keyring():
    """Lazy-import keyring, return module or None."""
    global _keyring_mod, _keyring_checked
    if _keyring_checked:
        return _keyring_mod
    _keyring_checked = True
    try:
        import keyring as kr  # type: ignore[import-untyped]

        _keyring_mod = kr
    except ImportError:
        _keyring_mod = None
    return _keyring_mod


def get_key(key_name: str) -> str:
    """Retrieve an API key.

    Lookup order:
    1. OS keyring (if ``keyring`` package available)
    2. Environment variable ``NOVEL_FORGE_{KEY_NAME}``
    3. Empty string (not configured)
    """
    kr = _get_keyring()
    if kr is not None:
        try:
            value = kr.get_password(_SERVICE_NAME, key_name)  # type: ignore[union-attr]
            if value:
                return value
        except Exception as exc:
            _logger.debug("Keyring lookup failed for %s: %s", key_name, exc)

    env_name = f"NOVEL_FORGE_{key_name.upper()}"
    return os.getenv(env_name, "")


def set_key(key_name: str, value: str) -> bool:
    """Store a key in the OS keyring. Returns False if keyring unavailable."""
    kr = _get_keyring()
    if kr is None:
        _logger.warning("keyring package not installed — cannot store key securely")
        return False
    try:
        kr.set_password(_SERVICE_NAME, key_name, value)  # type: ignore[union-attr]
        return True
    except Exception as exc:
        _logger.error("Failed to store key %s in keyring: %s", key_name, exc)
        return False


def delete_key(key_name: str) -> bool:
    """Remove a key from the OS keyring."""
    kr = _get_keyring()
    if kr is None:
        return False
    try:
        kr.delete_password(_SERVICE_NAME, key_name)  # type: ignore[union-attr]
        return True
    except Exception as exc:
        _logger.debug("Failed to delete key %s from keyring: %s", key_name, exc)
        return False


def keyring_available() -> bool:
    """Return True if the keyring backend is usable."""
    return _get_keyring() is not None
