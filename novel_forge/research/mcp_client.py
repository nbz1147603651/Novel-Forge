"""MCP stdio client and search-tool adapter for initialization research."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from novel_forge.research.contracts import ResearchQuery, ResearchResult, ResearchSource

_log = logging.getLogger(__name__)

_DEFAULT_PROTOCOL_VERSION = "2024-11-05"
_FRAMING_NEWLINE = "newline"
_FRAMING_CONTENT_LENGTH = "content_length"
_WEB_SEARCH_TOOL_NAMES = ("web_search", "search", "web-search")
_QUERY_ARGUMENT_CANDIDATES = ("query", "q", "keyword", "keywords", "search_query")
_MAX_RESULTS_ARGUMENT_CANDIDATES = ("max_results", "limit", "count", "top_k", "num_results")
_MINIMAL_ENV_KEYS = {
    "ALL_PROXY",
    "HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LANG",
    "LC_ALL",
    "NO_PROXY",
    "PATH",
    "REQUESTS_CA_BUNDLE",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TMPDIR",
    "USER",
    "UV_CACHE_DIR",
    "UV_LINK_MODE",
}


@dataclass(frozen=True)
class McpTool:
    """Small normalized view of an MCP tool declaration."""

    name: str
    input_schema: dict[str, Any]


class McpStdioClient:
    """Minimal MCP JSON-RPC client over stdio.

    The official stdio transport is newline-delimited JSON-RPC. Content-Length
    decoding is kept for compatibility with older local tools and tests, but
    writing defaults to newline framing.
    """

    def __init__(
        self,
        *,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float = 30.0,
        protocol_version: str = _DEFAULT_PROTOCOL_VERSION,
        stdio_framing: str = _FRAMING_NEWLINE,
        inherit_environment: bool = False,
    ) -> None:
        self._command = command.strip()
        self._args = list(args or [])
        self._env = dict(env or {})
        self._timeout_s = max(5.0, float(timeout_s or 30.0))
        self._protocol_version = (protocol_version or _DEFAULT_PROTOCOL_VERSION).strip()
        self._stdio_framing = normalize_mcp_stdio_framing(stdio_framing)
        self._inherit_environment = bool(inherit_environment)
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._reader_error: RuntimeError | None = None
        self._stdout_buffer = b""
        self._initialized = False
        self._request_id = 0
        self._pending_responses: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()

    async def __aenter__(self) -> McpStdioClient:
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def start(self) -> None:
        """Start and initialize the MCP server."""
        if self._process is not None and self._initialized:
            return
        if not self._command:
            raise RuntimeError("MCP command is not configured")
        try:
            await self._start_process()
            await self._initialize()
        except Exception:
            # __aenter__ does not call __aexit__ when initialization itself
            # fails, so clean up the subprocess here as well.
            await self.close()
            raise

    async def close(self) -> None:
        """Close stdin first, then terminate if the server does not exit."""
        process = self._process
        stderr_task = self._stderr_task
        reader_task = self._reader_task
        self._process = None
        self._stderr_task = None
        self._reader_task = None
        self._reader_error = None
        self._stdout_buffer = b""
        self._initialized = False
        self._fail_pending(RuntimeError("MCP client closed"))

        if reader_task is not None:
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task

        if stderr_task is not None:
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task

        if process is None or process.returncode is not None:
            return

        stdin = process.stdin
        if stdin is not None:
            with contextlib.suppress(Exception):
                stdin.close()
                await stdin.wait_closed()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
            return
        except asyncio.TimeoutError:
            pass

        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for its matching response."""
        loop = asyncio.get_running_loop()
        async with self._write_lock:
            process = self._process
            if process is None or process.stdin is None:
                raise RuntimeError("MCP client is not started")
            reader_task = self._reader_task
            if reader_task is None or reader_task.done():
                if self._reader_error is not None:
                    raise self._reader_error
                raise RuntimeError("MCP server stdout reader is not running")

            self._request_id += 1
            request_id = self._request_id
            response_waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._pending_responses[str(request_id)] = response_waiter
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
            try:
                process.stdin.write(self._encode_message(payload))
                await process.stdin.drain()
            except Exception:
                self._discard_pending(request_id, response_waiter)
                if not response_waiter.done():
                    response_waiter.cancel()
                raise

        try:
            response = await asyncio.wait_for(
                asyncio.shield(response_waiter),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError:
            self._discard_pending(request_id, response_waiter)
            if not response_waiter.done():
                response_waiter.cancel()
            with contextlib.suppress(Exception):
                await self.notify(
                    "notifications/cancelled",
                    {"requestId": request_id, "reason": "request timed out"},
                )
            raise
        finally:
            self._discard_pending(request_id, response_waiter)
        if "error" in response:
            err = response["error"] if isinstance(response["error"], dict) else {}
            raise RuntimeError(
                f"MCP error [{err.get('code', '?')}]: {err.get('message', 'unknown')}"
            )
        result = response.get("result", {})
        return result if isinstance(result, dict) else {"value": result}

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification."""
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        await self._write_message(payload)

    async def _start_process(self) -> None:
        env = self._build_child_env()
        try:
            self._process = await asyncio.create_subprocess_exec(
                self._command,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except (OSError, FileNotFoundError) as exc:
            raise RuntimeError(f"Failed to start MCP server: {self._command}: {exc}") from exc
        self._reader_error = None
        self._reader_task = asyncio.create_task(self._read_responses())
        if self._process.stderr is not None:
            self._stderr_task = asyncio.create_task(self._drain_stderr(self._process.stderr))

    def _build_child_env(self) -> dict[str, str]:
        if self._inherit_environment:
            env = os.environ.copy()
        else:
            env = {
                key: value
                for key, value in os.environ.items()
                if key in _MINIMAL_ENV_KEYS or key.startswith(("UV_", "NODE_", "NPM_"))
            }
        env.update(self._env)
        return env

    async def _initialize(self) -> None:
        await self.request(
            "initialize",
            {
                "protocolVersion": self._protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "novel-forge", "version": "1.0.0"},
            },
        )
        await self.notify("notifications/initialized", {})
        self._initialized = True

    async def _read_responses(self) -> None:
        """Read stdout once and route JSON-RPC responses to their request futures.

        asyncio.StreamReader permits only one concurrent read.  A single
        dispatcher also lets the MCP server return parallel tool calls in any
        order while keeping request/response correlation correct.
        """
        try:
            while True:
                message = await self._read_message()
                request_id = message.get("id")
                if request_id is not None and "method" in message:
                    await self._handle_server_request(message)
                    continue
                waiter = (
                    self._pending_responses.pop(str(request_id), None)
                    if request_id is not None
                    else None
                )
                if waiter is not None:
                    if not waiter.done():
                        waiter.set_result(message)
                    continue
                _log.debug(
                    "mcp_search_ignored_jsonrpc_message | message=%s",
                    message.get("method") or request_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - wake all in-flight callers consistently
            self._reader_error = RuntimeError(
                f"MCP server stdout reader failed: {type(exc).__name__}: {exc}"
            )
            self._fail_pending(self._reader_error)

    def _discard_pending(
        self,
        request_id: int,
        waiter: asyncio.Future[dict[str, Any]],
    ) -> None:
        key = str(request_id)
        if self._pending_responses.get(key) is waiter:
            self._pending_responses.pop(key, None)

    def _fail_pending(self, error: Exception) -> None:
        pending = list(self._pending_responses.values())
        self._pending_responses.clear()
        for waiter in pending:
            if not waiter.done():
                waiter.set_exception(error)

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        request_id = message.get("id")
        if method == "ping":
            await self._send_response(request_id, result={})
            return
        await self._send_response(
            request_id,
            error={"code": -32601, "message": f"Client method not implemented: {method}"},
        )

    async def _send_response(
        self,
        request_id: object,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result or {}
        await self._write_message(payload)

    async def _write_message(self, payload: dict[str, Any]) -> None:
        """Write one complete frame without interleaving concurrent callers."""
        async with self._write_lock:
            process = self._process
            if process is None or process.stdin is None:
                raise RuntimeError("MCP client is not started")
            process.stdin.write(self._encode_message(payload))
            await process.stdin.drain()

    async def _read_message(self) -> dict[str, Any]:
        assert self._process is not None
        assert self._process.stdout is not None

        while True:
            message, remainder = extract_mcp_message_from_buffer(self._stdout_buffer)
            if message is not None:
                self._stdout_buffer = remainder
                return message
            chunk = await self._process.stdout.read(4096)
            if not chunk:
                raise RuntimeError("MCP server closed stdout unexpectedly")
            self._stdout_buffer += chunk

    def _encode_message(self, payload: dict[str, Any]) -> bytes:
        if self._stdio_framing == _FRAMING_CONTENT_LENGTH:
            return encode_content_length_message(payload)
        return encode_newline_message(payload)

    @staticmethod
    async def _drain_stderr(stream: asyncio.StreamReader) -> None:
        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                text = chunk.decode("utf-8", errors="replace").strip()
                if text:
                    _log.debug("mcp_search_stderr | %s", text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - stderr logging must never block cleanup
            _log.debug("mcp_search_stderr_drain_failed | error=%s", exc)


class McpToolSearchAdapter:
    """Discover and call a web-search-like MCP tool."""

    def __init__(
        self,
        client: McpStdioClient,
        *,
        tool_name: str = "",
        query_argument: str = "query",
        tool_arguments: dict[str, Any] | None = None,
        results_per_query: int = 5,
    ) -> None:
        self._client = client
        self._tool_name = tool_name.strip()
        self._query_argument = query_argument.strip() or "query"
        self._tool_arguments = dict(tool_arguments or {})
        self._results_per_query = max(1, min(int(results_per_query or 5), 20))
        self._cached_tool: McpTool | None = None
        self._tool_discovery_lock = asyncio.Lock()

    async def call_search(self, query: str) -> dict[str, Any]:
        tool = await self._find_search_tool()
        arguments = self._build_arguments(query, tool)
        return await self._client.request(
            "tools/call",
            {"name": tool.name, "arguments": arguments},
        )

    async def _find_search_tool(self) -> McpTool:
        if self._cached_tool is not None:
            return self._cached_tool
        async with self._tool_discovery_lock:
            if self._cached_tool is not None:
                return self._cached_tool
            result = await self._client.request("tools/list", {})
            raw_tools = result.get("tools", [])
            tools: list[McpTool] = []
            if isinstance(raw_tools, list):
                for raw_tool in raw_tools:
                    if not isinstance(raw_tool, dict):
                        continue
                    tool = _normalize_tool(raw_tool)
                    if tool is not None:
                        tools.append(tool)
            if self._tool_name:
                for tool in tools:
                    if tool.name == self._tool_name:
                        self._cached_tool = tool
                        return tool
                raise RuntimeError(f"Configured MCP search tool not found: {self._tool_name}")
            for tool_name in _WEB_SEARCH_TOOL_NAMES:
                for tool in tools:
                    if tool.name == tool_name:
                        self._cached_tool = tool
                        return tool
            for tool in tools:
                if "search" in tool.name.lower():
                    self._cached_tool = tool
                    return tool
            raise RuntimeError(
                "No MCP web-search tool found; set research_mcp_tool_name explicitly"
            )

    def _build_arguments(self, query: str, tool: McpTool) -> dict[str, Any]:
        raw_arguments = _replace_placeholders(
            self._tool_arguments,
            query=query,
            results_per_query=self._results_per_query,
        )
        arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
        if not _contains_placeholder(self._tool_arguments, "{query}"):
            argument_name = self._query_argument or _infer_schema_key(
                tool.input_schema,
                _QUERY_ARGUMENT_CANDIDATES,
                "query",
            )
            arguments[argument_name] = query
        if not _contains_any_key(arguments, _MAX_RESULTS_ARGUMENT_CANDIDATES):
            max_key = _infer_schema_key(tool.input_schema, _MAX_RESULTS_ARGUMENT_CANDIDATES, "")
            if max_key:
                arguments[max_key] = self._results_per_query
        return arguments


class McpSearchProvider:
    """Research provider backed by an MCP stdio search tool."""

    name = "mcp_search"

    def __init__(
        self,
        *,
        command: str = "",
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float = 30.0,
        max_results: int = 5,
        results_per_query: int = 5,
        retry_attempts: int = 1,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        protocol_version: str = _DEFAULT_PROTOCOL_VERSION,
        stdio_framing: str = _FRAMING_NEWLINE,
        inherit_environment: bool = False,
        tool_name: str = "",
        query_argument: str = "query",
        tool_arguments: dict[str, Any] | None = None,
        query_max_parallel: int = 2,
    ) -> None:
        self._command = command.strip()
        self._args = list(args or [])
        self._env = dict(env or {})
        self._timeout_s = max(5.0, float(timeout_s or 30.0))
        self._max_results = max(1, min(int(max_results or 5), 30))
        self._results_per_query = max(1, min(int(results_per_query or 5), 20))
        self._retry_attempts = max(0, min(int(retry_attempts or 0), 3))
        self._include_domains = tuple(_normalize_domains(include_domains or []))
        self._exclude_domains = tuple(_normalize_domains(exclude_domains or []))
        self._protocol_version = protocol_version
        self._stdio_framing = normalize_mcp_stdio_framing(stdio_framing)
        self._inherit_environment = bool(inherit_environment)
        self._tool_name = tool_name.strip()
        self._query_argument = query_argument.strip() or "query"
        self._tool_arguments = dict(tool_arguments or {})
        self._query_max_parallel = max(1, min(int(query_max_parallel or 2), 8))

    @property
    def configured(self) -> bool:
        return bool(self._command)

    async def search(self, queries: list[ResearchQuery]) -> ResearchResult:
        warnings: list[str] = []
        sources: list[ResearchSource] = []
        if not self.configured:
            return ResearchResult(
                provider=self.name,
                queries=queries,
                warnings=[f"{self.name}: MCP command is not configured"],
            )
        try:
            async with McpStdioClient(
                command=self._command,
                args=self._args,
                env=self._env,
                timeout_s=self._timeout_s,
                protocol_version=self._protocol_version,
                stdio_framing=self._stdio_framing,
                inherit_environment=self._inherit_environment,
            ) as client:
                adapter = McpToolSearchAdapter(
                    client,
                    tool_name=self._tool_name,
                    query_argument=self._query_argument,
                    tool_arguments=self._tool_arguments,
                    results_per_query=self._results_per_query,
                )
                semaphore = asyncio.Semaphore(self._query_max_parallel)

                async def _call_one(
                    query: ResearchQuery,
                ) -> tuple[list[ResearchSource], str]:
                    async with semaphore:
                        try:
                            result = await self._call_with_retry(adapter, query.query)
                        except Exception as exc:  # noqa: BLE001 - research is non-blocking
                            _log.warning(
                                "mcp_search_query_failed | query=%s error=%s",
                                query.query,
                                exc,
                            )
                            return [], f"{self.name}: {type(exc).__name__}: {exc}"
                        return parse_mcp_search_result(result), ""

                # gather preserves argument order so downstream dedup/truncation
                # matches the previous serial loop byte for byte.
                per_query_results = await asyncio.gather(*(_call_one(query) for query in queries))
                for query_sources, warning in per_query_results:
                    sources.extend(query_sources)
                    if warning:
                        warnings.append(warning)
        except Exception as exc:  # noqa: BLE001 - provider init failures are non-blocking
            return ResearchResult(
                provider=self.name,
                queries=queries,
                warnings=[f"{self.name}: MCP server init failed: {exc}"],
            )

        if self._include_domains:
            sources = [
                source
                for source in sources
                if _url_matches_any_domain(source.url, self._include_domains)
            ]
        if self._exclude_domains:
            sources = [
                source
                for source in sources
                if not _url_matches_any_domain(source.url, self._exclude_domains)
            ]
        return ResearchResult(
            provider=self.name,
            queries=queries,
            sources=_dedupe_sources(sources, limit=self._max_results),
            warnings=warnings,
        )

    async def _call_with_retry(self, adapter: McpToolSearchAdapter, query: str) -> dict[str, Any]:
        last_exc: Exception | None = None
        for _attempt in range(self._retry_attempts + 1):
            try:
                return await adapter.call_search(query)
            except Exception as exc:  # noqa: BLE001 - normalized by caller
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("MCP search failed without an exception")


def encode_newline_message(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def encode_content_length_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def extract_mcp_message_from_buffer(buf: bytes) -> tuple[dict[str, Any] | None, bytes]:
    stripped = buf.lstrip(b" \t\r\n")
    if not stripped:
        return None, b""
    if stripped.lower().startswith(b"content-length:"):
        return _extract_content_length_message(stripped, original=buf)
    newline_idx = stripped.find(b"\n")
    if newline_idx >= 0:
        line = stripped[:newline_idx].strip()
        if not line:
            return None, stripped[newline_idx + 1 :]
        return _loads_jsonrpc_object(line), stripped[newline_idx + 1 :]
    if stripped.startswith(b"{"):
        return _extract_json_object_from_buffer(stripped)
    return None, buf


def parse_mcp_args(legacy_args: str = "", args_json: str = "") -> list[str]:
    raw_json = str(args_json or "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
            _log.warning("mcp_args_json_ignored | reason=not_a_list")
        except (json.JSONDecodeError, ValueError) as exc:
            _log.warning("mcp_args_json_ignored | error=%s", exc)
    raw = str(legacy_args or "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def parse_mcp_env(legacy_env: str = "", env_json: str = "") -> dict[str, str]:
    env: dict[str, str] = {}
    raw = str(legacy_env or "").strip()
    if raw:
        for pair in raw.split(","):
            item = pair.strip()
            if "=" not in item:
                continue
            key, _, value = item.partition("=")
            env[key.strip()] = value.strip()
    raw_json = str(env_json or "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                env.update({str(key).strip(): str(value) for key, value in parsed.items()})
            else:
                _log.warning("mcp_env_json_ignored | reason=not_an_object")
        except (json.JSONDecodeError, ValueError) as exc:
            _log.warning("mcp_env_json_ignored | error=%s", exc)
    return {key: value for key, value in env.items() if key}


def parse_mcp_tool_arguments(tool_arguments_json: str = "") -> dict[str, Any]:
    raw = str(tool_arguments_json or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("mcp_tool_arguments_json_ignored | error=%s", exc)
        return {}
    if not isinstance(parsed, dict):
        _log.warning("mcp_tool_arguments_json_ignored | reason=not_an_object")
        return {}
    return parsed


def normalize_mcp_stdio_framing(value: str) -> str:
    normalized = str(value or _FRAMING_NEWLINE).strip().lower().replace("-", "_")
    if normalized in {"jsonl", "json_lines", "line", "lines", "newline"}:
        return _FRAMING_NEWLINE
    if normalized in {"content_length", "header", "headers", "lsp"}:
        return _FRAMING_CONTENT_LENGTH
    return _FRAMING_NEWLINE


def parse_mcp_search_result(result: dict[str, Any]) -> list[ResearchSource]:
    """Parse an MCP tools/call result into normalized sources."""
    if result.get("isError"):
        raise RuntimeError(_tool_result_error_text(result) or "MCP tool returned isError=true")
    error_text = _tool_result_error_text(result)
    if _looks_like_tool_error(error_text):
        raise RuntimeError(error_text)
    sources: list[ResearchSource] = []
    if "structuredContent" in result:
        sources.extend(_extract_from_structured(result.get("structuredContent")))
    content = result.get("content", [])
    if not isinstance(content, list):
        return sources
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type == "text":
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            parsed = _try_parse_json(text)
            sources.extend(
                _extract_from_structured(parsed) if parsed is not None else _extract_from_text(text)
            )
        elif item_type == "resource_link":
            source = _source_from_mapping(
                {
                    "title": item.get("name"),
                    "url": item.get("uri"),
                    "snippet": item.get("description"),
                }
            )
            if source is not None:
                sources.append(source)
        elif item_type == "resource" and isinstance(item.get("resource"), dict):
            resource = item["resource"]
            source = _source_from_mapping(
                {
                    "title": resource.get("name") or resource.get("uri"),
                    "url": resource.get("uri"),
                    "snippet": resource.get("text") or resource.get("description"),
                }
            )
            if source is not None:
                sources.append(source)
    return sources


def _extract_content_length_message(
    buf: bytes,
    *,
    original: bytes,
) -> tuple[dict[str, Any] | None, bytes]:
    header_end = buf.find(b"\r\n\r\n")
    separator_len = 4
    if header_end < 0:
        header_end = buf.find(b"\n\n")
        separator_len = 2
    if header_end < 0:
        return None, original
    header_text = buf[:header_end].decode("ascii", errors="replace")
    content_length: int | None = None
    for line in header_text.replace("\r\n", "\n").split("\n"):
        key, _, value = line.partition(":")
        if key.strip().lower() == "content-length":
            try:
                content_length = int(value.strip())
            except ValueError as exc:
                raise RuntimeError(f"Invalid MCP Content-Length: {value!r}") from exc
            break
    if content_length is None:
        raise RuntimeError("MCP message missing Content-Length header")
    body_start = header_end + separator_len
    body_end = body_start + content_length
    if len(buf) < body_end:
        return None, original
    return _loads_jsonrpc_object(buf[body_start:body_end]), buf[body_end:]


def _extract_json_object_from_buffer(buf: bytes) -> tuple[dict[str, Any] | None, bytes]:
    text = buf.decode("utf-8", errors="replace")
    depth = 0
    in_string = False
    escape = False
    for idx, ch in enumerate(text):
        if escape:
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = idx + 1
                    try:
                        message = json.loads(text[:end])
                    except (json.JSONDecodeError, ValueError):
                        return None, buf
                    if not isinstance(message, dict):
                        raise RuntimeError("MCP JSON-RPC message body is not an object")
                    return message, text[end:].encode("utf-8", errors="replace")
    return None, buf


def _loads_jsonrpc_object(raw: bytes) -> dict[str, Any]:
    try:
        message = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("Invalid MCP JSON-RPC message") from exc
    if not isinstance(message, dict):
        raise RuntimeError("MCP JSON-RPC message is not an object")
    return message


def _normalize_tool(raw: dict[str, Any]) -> McpTool | None:
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    schema = raw.get("inputSchema")
    return McpTool(name=name, input_schema=schema if isinstance(schema, dict) else {})


def _infer_schema_key(schema: dict[str, Any], candidates: tuple[str, ...], fallback: str) -> str:
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return fallback
    lower_to_key = {str(key).lower(): str(key) for key in properties}
    for candidate in candidates:
        if candidate in lower_to_key:
            return lower_to_key[candidate]
    return fallback


def _replace_placeholders(value: Any, *, query: str, results_per_query: int) -> Any:
    if isinstance(value, str):
        if value == "{query}":
            return query
        if value == "{results_per_query}":
            return results_per_query
        return value.replace("{query}", query).replace(
            "{results_per_query}", str(results_per_query)
        )
    if isinstance(value, dict):
        return {
            str(key): _replace_placeholders(item, query=query, results_per_query=results_per_query)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _replace_placeholders(item, query=query, results_per_query=results_per_query)
            for item in value
        ]
    return value


def _contains_placeholder(value: Any, placeholder: str) -> bool:
    if isinstance(value, str):
        return placeholder in value
    if isinstance(value, dict):
        return any(_contains_placeholder(item, placeholder) for item in value.values())
    if isinstance(value, list):
        return any(_contains_placeholder(item, placeholder) for item in value)
    return False


def _contains_any_key(mapping: dict[str, Any], keys: tuple[str, ...]) -> bool:
    existing = {str(key).lower() for key in mapping}
    return any(key in existing for key in keys)


def _tool_result_error_text(result: dict[str, Any]) -> str:
    chunks: list[str] = []
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and structured.get("type") == "text":
        chunks.append(str(structured.get("text") or "").strip())
    content = result.get("content", [])
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text") or "").strip())
    return " ".join(chunk for chunk in chunks if chunk)


def _looks_like_tool_error(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    markers = (
        "api error",
        "authentication",
        "authorization",
        "failed to perform search",
        "forbidden",
        "invalid api",
        "login fail",
        "rate limit",
        "unauthorized",
    )
    return any(marker in lowered for marker in markers)


def _try_parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _extract_from_structured(data: Any) -> list[ResearchSource]:
    sources: list[ResearchSource] = []
    for item in _iter_structured_items(data):
        source = _source_from_mapping(item)
        if source is not None:
            sources.append(source)
    return sources


def _iter_structured_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        list_items: list[dict[str, Any]] = []
        for item in data:
            list_items.extend(_iter_structured_items(item))
        return list_items
    if not isinstance(data, dict):
        return []
    if _source_from_mapping(data) is not None:
        return [data]
    dict_items: list[dict[str, Any]] = []
    for key in (
        "results",
        "items",
        "data",
        "sources",
        "links",
        "documents",
        "organic",
        "organic_results",
        "webPages",
        "web_pages",
    ):
        value = data.get(key)
        if value is not None:
            dict_items.extend(_iter_structured_items(value))
    return dict_items


def _source_from_mapping(item: dict[str, Any]) -> ResearchSource | None:
    if item.get("type") == "text" and not any(
        item.get(key) for key in ("title", "name", "url", "link", "href", "uri")
    ):
        return None
    title = str(item.get("title") or item.get("name") or "").strip()
    url = str(
        item.get("url") or item.get("link") or item.get("href") or item.get("uri") or ""
    ).strip()
    snippet = str(
        item.get("snippet")
        or item.get("content")
        or item.get("description")
        or item.get("summary")
        or item.get("text")
        or ""
    ).strip()
    if url.startswith("file:"):
        url = ""
    if not url and not snippet:
        return None
    try:
        score = float(item.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return ResearchSource(
        title=_clip(title, 120),
        url=url,
        snippet=_clip(snippet, 500),
        score=score,
    )


def _extract_from_text(text: str) -> list[ResearchSource]:
    sources: list[ResearchSource] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines) and len(sources) < 30:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("[") and "]" in line:
            title = line[line.index("]") + 1 :].strip()
            url = ""
            snippet = ""
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("http"):
                url = lines[i + 1].strip()
                i += 1
            if i + 1 < len(lines) and lines[i + 1].strip():
                snippet = lines[i + 1].strip()
                i += 1
            if url or snippet:
                sources.append(
                    ResearchSource(title=_clip(title, 120), url=url, snippet=_clip(snippet, 500))
                )
        elif line.startswith("http"):
            sources.append(ResearchSource(title="", url=line, snippet=""))
        i += 1
    return sources


def _dedupe_sources(sources: list[ResearchSource], *, limit: int) -> list[ResearchSource]:
    seen: set[str] = set()
    result: list[ResearchSource] = []
    for source in sources:
        key = (source.url or source.title or source.snippet).strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(source)
        if len(result) >= limit:
            break
    return result


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _normalize_domains(domains: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in domains:
        domain = str(raw or "").strip().lower()
        if not domain:
            continue
        parsed = urlparse(domain if "://" in domain else f"https://{domain}")
        host = (parsed.hostname or domain).strip().lower().lstrip(".")
        if not host or host in seen:
            continue
        seen.add(host)
        result.append(host)
    return result


def _url_matches_any_domain(url: str, domains: tuple[str, ...]) -> bool:
    if not url:
        return False
    host = (urlparse(url).hostname or "").strip().lower().lstrip(".")
    if not host:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)
