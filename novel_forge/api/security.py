"""API boundary protection middleware."""

from __future__ import annotations

import ipaddress
import secrets
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from novel_forge.core.config import get_settings

CallNext = Callable[[Request], Awaitable[Response]]

_EXEMPT_METHODS = {"OPTIONS"}
_LOCAL_CLIENT_NAMES = {"localhost", "testclient", "testserver"}


def _split_config_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _is_path_exempt(path: str, raw_patterns: object) -> bool:
    for pattern in _split_config_list(raw_patterns):
        if pattern.endswith("*"):
            if path.startswith(pattern[:-1]):
                return True
        elif path == pattern:
            return True
    return False


def _host_matches_pattern(host: str, pattern: str) -> bool:
    normalized_host = host.lower().strip("[]")
    normalized_pattern = pattern.lower().strip("[]")
    if normalized_pattern == "*":
        return True
    if normalized_pattern.startswith("*."):
        suffix = normalized_pattern[1:]
        return normalized_host.endswith(suffix)
    return normalized_host == normalized_pattern


def _is_host_allowed(host: str | None, raw_allowed_hosts: object) -> bool:
    allowed_hosts = _split_config_list(raw_allowed_hosts)
    if not allowed_hosts:
        return True
    if not host:
        return False
    return any(_host_matches_pattern(host, pattern) for pattern in allowed_hosts)


def _is_local_client(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.lower().strip("[]")
    if normalized in _LOCAL_CLIENT_NAMES:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _token_from_request(request: Request) -> str:
    auth_header = request.headers.get("authorization", "").strip()
    scheme, _, value = auth_header.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    return request.headers.get("x-novel-forge-token", "").strip()


def _unauthorized_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"error": "unauthorized", "message": "Missing or invalid API token"},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def enforce_api_security(request: Request, call_next: CallNext) -> Response:
    """Enforce host, local-only, and optional token policy for every request."""
    settings = get_settings()
    path = request.url.path

    if not _is_host_allowed(request.url.hostname, getattr(settings, "api_trusted_hosts", "")):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_host", "message": "Host header is not trusted"},
        )

    if bool(getattr(settings, "api_local_only", True)):
        client_host = request.client.host if request.client else None
        if not _is_local_client(client_host):
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden", "message": "Remote API access is disabled"},
            )

    if request.method in _EXEMPT_METHODS:
        return await call_next(request)

    token = str(getattr(settings, "api_access_token", "") or "").strip()
    if token and not _is_path_exempt(path, getattr(settings, "api_auth_exempt_paths", "")):
        request_token = _token_from_request(request)
        if not request_token or not secrets.compare_digest(request_token, token):
            return _unauthorized_response()

    return await call_next(request)


def configure_api_security(app: FastAPI) -> None:
    """Attach API boundary protection to a FastAPI app."""

    @app.middleware("http")
    async def _api_security_middleware(request: Request, call_next: CallNext) -> Response:
        return await enforce_api_security(request, call_next)
