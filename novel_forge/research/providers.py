"""Research providers used before long-form worldbuilding."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from novel_forge.research.contracts import ResearchQuery, ResearchResult, ResearchSource
from novel_forge.research.mcp_client import (
    McpSearchProvider,
    parse_mcp_args,
    parse_mcp_env,
    parse_mcp_tool_arguments,
)

_log = logging.getLogger(__name__)


class ResearchProvider(Protocol):
    """Provider port for external search backends."""

    name: str

    async def search(self, queries: list[ResearchQuery]) -> ResearchResult:
        """Return normalized search results for *queries*."""


class NoopResearchProvider:
    """Provider used when research is disabled or not configured."""

    name = "noop"

    async def search(self, queries: list[ResearchQuery]) -> ResearchResult:
        return ResearchResult(provider=self.name, queries=queries)


class HttpJsonResearchProvider:
    """HTTP JSON provider for Tavily, Brave, SearXNG, or a compatible endpoint."""

    def __init__(
        self,
        *,
        provider: str,
        endpoint: str,
        api_key: str = "",
        timeout_s: float = 10.0,
        max_results: int = 5,
        results_per_query: int = 5,
        retry_attempts: int = 1,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        locale: str = "",
        search_depth: str = "basic",
        query_max_parallel: int = 2,
    ) -> None:
        self.name = provider
        self._endpoint = endpoint.strip()
        self._api_key = api_key.strip()
        self._timeout_s = max(1.0, float(timeout_s or 10.0))
        self._max_results = max(1, min(int(max_results or 5), 30))
        self._results_per_query = max(1, min(int(results_per_query or 5), 20))
        self._retry_attempts = max(0, min(int(retry_attempts or 0), 3))
        self._include_domains = tuple(_normalize_domains(include_domains or []))
        self._exclude_domains = tuple(_normalize_domains(exclude_domains or []))
        self._locale = locale.strip()
        self._search_depth = search_depth.strip().lower() or "basic"
        self._query_max_parallel = max(1, min(int(query_max_parallel or 2), 8))

    @property
    def configured(self) -> bool:
        return bool(self._endpoint)

    async def search(self, queries: list[ResearchQuery]) -> ResearchResult:
        warnings: list[str] = []
        sources: list[ResearchSource] = []
        if not self.configured:
            return ResearchResult(
                provider=self.name,
                queries=queries,
                warnings=[f"{self.name}: research endpoint is not configured"],
            )
        async with httpx.AsyncClient(timeout=self._timeout_s, follow_redirects=True) as client:
            semaphore = asyncio.Semaphore(self._query_max_parallel)

            async def _fetch_one(query: ResearchQuery) -> tuple[list[ResearchSource], str]:
                async with semaphore:
                    try:
                        payload = await self._fetch_query_with_retry(client, query.query)
                    except Exception as exc:  # noqa: BLE001 - provider failures are non-blocking
                        _log.warning(
                            "research_provider_failed | provider=%s error=%s", self.name, exc
                        )
                        return [], f"{self.name}: {type(exc).__name__}: {exc}"
                    return self._normalize_sources(payload), ""

            # gather preserves argument order: results are merged in planned query
            # order so _dedupe_sources keeps the same first-wins truncation as the
            # previous serial loop.
            per_query_results = await asyncio.gather(*(_fetch_one(query) for query in queries))
            for query_sources, warning in per_query_results:
                sources.extend(query_sources)
                if warning:
                    warnings.append(warning)
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

    async def _fetch_query_with_retry(self, client: httpx.AsyncClient, query: str) -> Any:
        last_exc: Exception | None = None
        for _attempt in range(self._retry_attempts + 1):
            try:
                return await self._fetch_query(client, query)
            except Exception as exc:  # noqa: BLE001 - normalized into a warning by caller
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("research fetch failed without an exception")

    async def _fetch_query(self, client: httpx.AsyncClient, query: str) -> Any:
        provider = self.name.lower()
        if provider == "tavily":
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            body: dict[str, Any] = {
                "query": query,
                "search_depth": self._search_depth,
                "max_results": self._results_per_query,
                "include_answer": False,
                "include_raw_content": False,
            }
            if self._include_domains:
                body["include_domains"] = list(self._include_domains)
            if self._exclude_domains:
                body["exclude_domains"] = list(self._exclude_domains)
            response = await client.post(
                self._endpoint,
                json=body,
                headers=headers,
            )
        elif provider == "brave":
            headers = {"Accept": "application/json"}
            if self._api_key:
                headers["X-Subscription-Token"] = self._api_key
            brave_params: dict[str, Any] = {
                "q": query,
                "count": self._results_per_query,
                "safesearch": "moderate",
            }
            if self._locale:
                brave_params["search_lang"] = self._locale
            response = await client.get(
                self._endpoint,
                params=brave_params,
                headers=headers,
            )
        elif provider == "searxng":
            searxng_params: dict[str, Any] = {"q": query, "format": "json", "safesearch": 1}
            if self._locale:
                searxng_params["language"] = self._locale
            response = await client.get(
                self._endpoint,
                params=searxng_params,
            )
        else:
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            body = {
                "query": query,
                "max_results": self._results_per_query,
                "locale": self._locale,
                "include_domains": list(self._include_domains),
                "exclude_domains": list(self._exclude_domains),
                "search_depth": self._search_depth,
            }
            response = await client.post(
                self._endpoint,
                json=body,
                headers=headers,
            )
        response.raise_for_status()
        return response.json()

    def _normalize_sources(self, payload: Any) -> list[ResearchSource]:
        if not isinstance(payload, dict):
            return []
        provider = self.name.lower()
        if provider == "brave":
            raw_results = payload.get("web", {}).get("results", [])
        else:
            raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            return []
        sources: list[ResearchSource] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            url = str(item.get("url") or item.get("link") or "").strip()
            snippet = str(
                item.get("content")
                or item.get("snippet")
                or item.get("description")
                or item.get("text")
                or ""
            ).strip()
            if not url and not snippet:
                continue
            try:
                score = float(item.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            sources.append(
                ResearchSource(
                    title=_clip(title, 120),
                    url=url,
                    snippet=_clip(snippet, 500),
                    score=score,
                )
            )
        return sources


def provider_from_settings(settings: Any, provider_name: str) -> ResearchProvider:
    """Build a provider without requiring model gateway changes."""
    requested = (provider_name or "auto").strip().lower()
    default_provider = str(getattr(settings, "research_default_provider", "auto") or "auto")
    provider = default_provider.strip().lower() if requested == "auto" else requested
    if provider in {"", "auto", "noop", "none"}:
        return NoopResearchProvider()
    endpoint = str(getattr(settings, "research_http_endpoint", "") or "").strip()
    if provider == "tavily" and not endpoint:
        endpoint = "https://api.tavily.com/search"
    elif provider == "brave" and not endpoint:
        endpoint = "https://api.search.brave.com/res/v1/web/search"
    api_key = str(getattr(settings, "research_api_key", "") or "").strip()
    timeout_s = float(getattr(settings, "research_timeout_s", 10.0) or 10.0)
    max_results = int(getattr(settings, "research_max_results", 5) or 5)
    results_per_query = int(getattr(settings, "research_results_per_query", 5) or 5)
    retry_attempts = int(getattr(settings, "research_retry_attempts", 1) or 0)
    query_max_parallel = int(getattr(settings, "research_query_max_parallel", 2) or 2)
    include_domains = _split_domains(str(getattr(settings, "research_include_domains", "") or ""))
    exclude_domains = _split_domains(str(getattr(settings, "research_exclude_domains", "") or ""))
    locale = str(getattr(settings, "research_locale", "") or "").strip()
    search_depth = str(getattr(settings, "research_search_depth", "basic") or "basic")

    # ── MCP stdio provider ──────────────────────────────────────────────
    if provider == "mcp_search":
        mcp_command = str(getattr(settings, "research_mcp_command", "") or "").strip()
        if not mcp_command:
            return NoopResearchProvider()
        mcp_args = parse_mcp_args(
            str(getattr(settings, "research_mcp_args", "") or ""),
            str(getattr(settings, "research_mcp_args_json", "") or ""),
        )
        mcp_env = parse_mcp_env(
            str(getattr(settings, "research_mcp_env", "") or ""),
            str(getattr(settings, "research_mcp_env_json", "") or ""),
        )
        mcp_tool_arguments = parse_mcp_tool_arguments(
            str(getattr(settings, "research_mcp_tool_arguments_json", "") or "")
        )
        # Inject research_api_key under the MCP server's expected key name.
        api_key = str(getattr(settings, "research_api_key", "") or "").strip()
        mcp_api_key_env = str(
            getattr(settings, "research_mcp_api_key_env", "MINIMAX_API_KEY") or ""
        ).strip()
        if api_key and mcp_api_key_env and mcp_api_key_env not in mcp_env:
            mcp_env[mcp_api_key_env] = api_key
        return McpSearchProvider(
            command=mcp_command,
            args=mcp_args,
            env=mcp_env,
            timeout_s=timeout_s,
            max_results=max_results,
            results_per_query=results_per_query,
            retry_attempts=retry_attempts,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            protocol_version=str(
                getattr(settings, "research_mcp_protocol_version", "2024-11-05") or "2024-11-05"
            ),
            stdio_framing=str(
                getattr(settings, "research_mcp_stdio_framing", "newline") or "newline"
            ),
            inherit_environment=bool(getattr(settings, "research_mcp_inherit_environment", False)),
            tool_name=str(getattr(settings, "research_mcp_tool_name", "") or ""),
            query_argument=str(
                getattr(settings, "research_mcp_query_argument", "query") or "query"
            ),
            tool_arguments=mcp_tool_arguments,
            query_max_parallel=query_max_parallel,
        )

    return HttpJsonResearchProvider(
        provider=provider,
        endpoint=endpoint,
        api_key=api_key,
        timeout_s=timeout_s,
        max_results=max_results,
        results_per_query=results_per_query,
        retry_attempts=retry_attempts,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        locale=locale,
        search_depth=search_depth,
        query_max_parallel=query_max_parallel,
    )


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


def _split_domains(value: str) -> list[str]:
    return _normalize_domains(value.replace(";", ",").split(","))


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
