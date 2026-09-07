from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from novel_forge.core.schemas.spec import StorySpec
from novel_forge.pipeline.long.services.init.init_cache import (
    _build_long_init_request_payload,
    _long_init_request_fingerprint,
)
from novel_forge.pipeline.long.services.init.init_orchestrator import (
    _load_reusable_init_research_report,
)
from novel_forge.research.contracts import (
    ModelPriorNotes,
    ResearchBrief,
    ResearchQuery,
    ResearchReport,
    ResearchResult,
    ResearchSource,
)
from novel_forge.research.mcp_client import (
    McpStdioClient,
    encode_content_length_message,
    encode_newline_message,
    extract_mcp_message_from_buffer,
    parse_mcp_search_result,
)
from novel_forge.research.providers import (
    HttpJsonResearchProvider,
    McpSearchProvider,
    provider_from_settings,
)
from novel_forge.research.service import (
    build_research_runtime_config,
    ground_outline_research,
    plan_research_queries_with_llm,
    research_runtime_fingerprint,
    run_init_web_research,
    synthesize_init_research_dossier,
    synthesize_model_prior_notes,
)


class _FakeStorage:
    def __init__(self, payload: dict[str, object] | None) -> None:
        self.payload = payload

    def exists(self, _path: object) -> bool:
        return self.payload is not None

    def load_json(self, _path: object) -> dict[str, object]:
        if self.payload is None:
            raise FileNotFoundError
        return self.payload


class _FakeResearchCtx:
    def __init__(self, response: dict[str, object], settings: object | None = None) -> None:
        self.response = response
        self.settings = settings or SimpleNamespace()
        self.calls: list[tuple[object, dict[str, object]]] = []

    async def call_with_retry(
        self,
        task_type: object,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        self.calls.append((task_type, payload))
        return self.response


def _spec() -> StorySpec:
    return StorySpec(
        theme="一名记者调查旧城档案",
        genre="现实悬疑",
        tone="冷峻",
        world_hint="旧城改造与档案馆",
        conflict_hint="城市记忆被商业利益改写",
    )


@pytest.mark.asyncio
async def test_init_web_research_disabled_returns_skipped_report() -> None:
    report = await run_init_web_research(
        spec=_spec(),
        settings=SimpleNamespace(),
        enabled=False,
        provider_name="auto",
        query_hint="",
    )

    assert report.status == "skipped"
    assert report.enabled is False
    assert report.sources == []
    assert report.prompt_context()["status"] == "skipped"


@pytest.mark.asyncio
async def test_init_web_research_enabled_without_provider_is_non_blocking() -> None:
    report = await run_init_web_research(
        spec=_spec(),
        settings=SimpleNamespace(research_default_provider="auto"),
        enabled=True,
        provider_name="auto",
        query_hint="城市更新 档案制度",
    )

    assert report.status == "skipped"
    assert report.enabled is True
    assert report.provider == "noop"
    assert report.queries[0].query == "城市更新 档案制度"


@pytest.mark.asyncio
async def test_init_web_research_unconfigured_endpoint_is_skipped() -> None:
    report = await run_init_web_research(
        spec=_spec(),
        settings=SimpleNamespace(research_default_provider="searxng"),
        enabled=True,
        provider_name="auto",
        query_hint="",
    )

    assert report.status == "skipped"
    assert report.provider == "searxng"
    assert any("endpoint is not configured" in warning for warning in report.warnings)


@pytest.mark.asyncio
async def test_init_web_research_provider_warning_is_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _WarningProvider:
        name = "fake_search"

        async def search(self, queries: list[ResearchQuery]) -> ResearchResult:
            return ResearchResult(
                provider=self.name,
                queries=queries,
                warnings=["fake_search: TimeoutError: timed out"],
            )

    monkeypatch.setattr(
        "novel_forge.research.service.provider_from_settings",
        lambda _settings, _provider_name: _WarningProvider(),
    )

    report = await run_init_web_research(
        spec=_spec(),
        settings=SimpleNamespace(research_default_provider="fake_search"),
        enabled=True,
        provider_name="auto",
        query_hint="",
    )

    assert report.status == "failed"
    assert report.provider == "fake_search"


@pytest.mark.asyncio
async def test_init_web_research_respects_query_cap_and_records_config() -> None:
    report = await run_init_web_research(
        spec=_spec(),
        settings=SimpleNamespace(research_default_provider="auto", research_max_queries=1),
        enabled=True,
        provider_name="auto",
        query_hint="城市更新 档案制度",
    )

    assert len(report.queries) == 1
    assert report.config["max_queries"] == 1
    assert report.config_fingerprint


@pytest.mark.asyncio
async def test_research_dossier_disabled_returns_skipped_report() -> None:
    report = await run_init_web_research(
        spec=_spec(),
        settings=SimpleNamespace(),
        enabled=False,
        provider_name="auto",
        query_hint="",
    )
    ctx = _FakeResearchCtx({}, settings=SimpleNamespace(research_dossier_enabled=True))

    dossier = await synthesize_init_research_dossier(
        ctx=ctx,
        spec=_spec(),
        research_report=report,
        enabled=False,
    )

    assert dossier.status == "skipped"
    assert dossier.enabled is False
    assert ctx.calls == []


@pytest.mark.asyncio
async def test_research_dossier_empty_report_does_not_call_model() -> None:
    report = ResearchReport(
        enabled=True,
        provider="noop",
        status="empty",
        warnings=["no sources"],
        spec_fingerprint="ignored",
    )
    ctx = _FakeResearchCtx({}, settings=SimpleNamespace(research_dossier_enabled=True))

    dossier = await synthesize_init_research_dossier(
        ctx=ctx,
        spec=_spec(),
        research_report=report,
        enabled=True,
    )

    assert dossier.status == "empty"
    assert dossier.enabled is True
    assert "no sources" in dossier.warnings
    assert ctx.calls == []


@pytest.mark.asyncio
async def test_research_dossier_succeeded_uses_model_payload() -> None:
    report = ResearchReport(
        enabled=True,
        provider="http_json",
        status="succeeded",
        sources=[ResearchSource(title="旧城档案", url="https://example.test/a", snippet="档案馆")],
        brief=ResearchBrief(summary="旧城档案资料", source_notes=["[1] 旧城档案"]),
    )
    ctx = _FakeResearchCtx(
        {
            "summary": "资料可用于旧城改造与档案制度真实感。",
            "real_world_constraints": ["档案调阅通常受权限和登记流程限制。"],
            "terminology": ["档案调阅"],
            "inspiration_notes": ["以旧城更新听证会制造公开冲突。"],
            "uncertainty_notes": ["具体城市制度需作者决定。"],
            "source_refs": ["[1] 旧城档案"],
        },
        settings=SimpleNamespace(research_dossier_enabled=True, research_dossier_max_sources=1),
    )

    dossier = await synthesize_init_research_dossier(
        ctx=ctx,
        spec=_spec(),
        research_report=report,
        enabled=True,
    )

    assert dossier.status == "succeeded"
    assert dossier.summary
    assert dossier.real_world_constraints == ["档案调阅通常受权限和登记流程限制。"]
    assert len(ctx.calls) == 1


@pytest.mark.asyncio
async def test_outline_research_grounding_skips_without_succeeded_dossier() -> None:
    ctx = _FakeResearchCtx(
        {},
        settings=SimpleNamespace(outline_research_grounding_enabled=True),
    )
    dossier = await synthesize_init_research_dossier(
        ctx=ctx,
        spec=_spec(),
        research_report=ResearchReport(enabled=False, status="skipped"),
        enabled=False,
    )

    grounding = await ground_outline_research(
        ctx=ctx,
        spec=_spec(),
        story_bible={},
        blueprint={},
        outline={"chapters": []},
        dossier=dossier,
        enabled=True,
    )

    assert grounding.status == "skipped"
    assert grounding.enabled is False


@pytest.mark.asyncio
async def test_outline_research_grounding_succeeded_normalizes_chapter_notes() -> None:
    ctx = _FakeResearchCtx(
        {
            "summary": "大纲需要注意档案调阅流程。",
            "global_notes": ["档案馆流程要前后一致。"],
            "chapter_notes": [
                {
                    "chapter_number": 2,
                    "reminders": ["登记流程不要一笔带过。", "多余提醒"],
                    "fact_risks": ["权限越级需要解释。"],
                    "source_refs": ["[1]"],
                }
            ],
            "fact_risks": ["档案权限风险"],
            "terminology": ["调阅登记"],
            "source_refs": ["[1]"],
        },
        settings=SimpleNamespace(
            outline_research_grounding_enabled=True,
            outline_research_grounding_notes_per_chapter=1,
        ),
    )
    report = ResearchReport(
        enabled=True,
        provider="http_json",
        status="succeeded",
        sources=[ResearchSource(title="旧城档案", url="https://example.test/a")],
        brief=ResearchBrief(summary="旧城档案资料"),
    )
    dossier = await synthesize_init_research_dossier(
        ctx=_FakeResearchCtx(
            {
                "summary": "资料摘要",
                "real_world_constraints": [],
                "terminology": [],
                "inspiration_notes": [],
                "uncertainty_notes": [],
                "source_refs": [],
            },
            settings=SimpleNamespace(research_dossier_enabled=True),
        ),
        spec=_spec(),
        research_report=report,
        enabled=True,
    )

    grounding = await ground_outline_research(
        ctx=ctx,
        spec=_spec(),
        story_bible={},
        blueprint={},
        outline={"chapters": [{"chapter_number": 2}]},
        dossier=dossier,
        enabled=True,
    )

    assert grounding.status == "succeeded"
    assert grounding.chapter_notes[0].chapter_number == 2
    assert grounding.chapter_notes[0].reminders == ["登记流程不要一笔带过。"]


@pytest.mark.asyncio
async def test_outline_research_grounding_partitions_all_chapters_without_loss() -> None:
    class _BatchCtx:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                outline_research_grounding_enabled=True,
                outline_research_grounding_notes_per_chapter=1,
                outline_research_grounding_batch_size=2,
                outline_research_grounding_prompt_char_budget=100000,
                outline_research_grounding_coverage_retries=0,
            )
            self.calls: list[dict[str, object]] = []

        async def call_with_retry(
            self,
            _task_type: object,
            payload: dict[str, object],
            **_kwargs: object,
        ) -> dict[str, object]:
            self.calls.append(payload)
            chapters = payload["outline"]["chapters"]  # type: ignore[index]
            return {
                "summary": "批次完成",
                "global_notes": [],
                "chapter_notes": [
                    {
                        "chapter_number": item["chapter_number"],
                        "reminders": [f"提醒{item['chapter_number']}"],
                        "fact_risks": [],
                        "source_refs": [],
                    }
                    for item in chapters  # type: ignore[union-attr]
                ],
                "fact_risks": [],
                "terminology": [],
                "source_refs": [],
            }

    ctx = _BatchCtx()
    dossier = ResearchReport(enabled=False, status="skipped")
    research_dossier = await synthesize_init_research_dossier(
        ctx=_FakeResearchCtx({}, settings=SimpleNamespace()),
        spec=_spec(),
        research_report=dossier,
        enabled=False,
    )
    research_dossier = research_dossier.model_copy(
        update={"enabled": True, "status": "succeeded", "summary": "资料"}
    )
    chapters = [{"chapter_number": number, "goal": "目标" * 40} for number in range(1, 6)]

    grounding = await ground_outline_research(
        ctx=ctx,
        spec=_spec(),
        story_bible={"world_rules": ["规则"]},
        blueprint={"summary": "蓝图"},
        outline={"chapters": chapters},
        dossier=research_dossier,
        enabled=True,
    )

    assert len(ctx.calls) == 3
    assert all(len(call["outline"]["chapters"]) <= 2 for call in ctx.calls)  # type: ignore[index]
    assert [note.chapter_number for note in grounding.chapter_notes] == [1, 2, 3, 4, 5]
    assert {
        number
        for call in ctx.calls
        for number in call["grounding_batch"]["chapter_numbers"]  # type: ignore[index]
    } == {1, 2, 3, 4, 5}


def test_research_runtime_fingerprint_changes_with_route_params() -> None:
    base = SimpleNamespace(
        research_default_provider="searxng",
        research_http_endpoint="http://localhost/search",
        research_max_results=5,
    )
    changed = SimpleNamespace(
        research_default_provider="searxng",
        research_http_endpoint="http://localhost/search",
        research_max_results=8,
    )

    assert research_runtime_fingerprint(base, "auto") != research_runtime_fingerprint(
        changed, "auto"
    )


def test_mcp_stdio_client_uses_newline_jsonrpc_frames() -> None:
    first = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    second = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}

    encoded = encode_newline_message(first)
    encoded += encode_newline_message(second)

    message, remainder = extract_mcp_message_from_buffer(encoded)
    assert message == first

    next_message, next_remainder = extract_mcp_message_from_buffer(remainder)
    assert next_message == second
    assert next_remainder == b""


def test_mcp_stdio_client_can_decode_content_length_frames() -> None:
    first = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    second = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}

    encoded = encode_content_length_message(first)
    encoded += encode_content_length_message(second)

    message, remainder = extract_mcp_message_from_buffer(encoded)
    assert message == first

    next_message, next_remainder = extract_mcp_message_from_buffer(remainder)
    assert next_message == second
    assert next_remainder == b""


def test_provider_from_settings_builds_mcp_search_provider() -> None:
    settings = SimpleNamespace(
        research_default_provider="mcp_search",
        research_mcp_command="uvx",
        research_mcp_args="minimax-coding-plan-mcp",
        research_mcp_args_json="",
        research_mcp_env="MINIMAX_API_HOST=https://api.minimax.io",
        research_mcp_env_json="",
        research_mcp_api_key_env="MINIMAX_API_KEY",
        research_api_key="secret",
        research_timeout_s=10,
        research_max_results=5,
        research_results_per_query=5,
        research_retry_attempts=1,
    )

    provider = provider_from_settings(settings, "auto")

    assert isinstance(provider, McpSearchProvider)
    assert provider._command == "uvx"  # noqa: SLF001
    assert provider._args == ["minimax-coding-plan-mcp"]  # noqa: SLF001
    assert provider._env["MINIMAX_API_KEY"] == "secret"  # noqa: SLF001
    assert provider._env["MINIMAX_API_HOST"] == "https://api.minimax.io"  # noqa: SLF001


def test_provider_from_settings_prefers_mcp_json_config() -> None:
    settings = SimpleNamespace(
        research_default_provider="mcp_search",
        research_mcp_command="uvx",
        research_mcp_args="-y,old-package",
        research_mcp_args_json='["minimax-coding-plan-mcp"]',
        research_mcp_env="MINIMAX_API_HOST=https://old.example",
        research_mcp_env_json='{"MINIMAX_API_HOST":"https://api.minimax.io"}',
        research_mcp_api_key_env="ALT_SEARCH_API_KEY",
        research_api_key="secret",
        research_timeout_s=10,
        research_max_results=5,
        research_results_per_query=5,
        research_retry_attempts=1,
        research_mcp_stdio_framing="newline",
        research_mcp_tool_name="web_search",
        research_mcp_query_argument="query",
        research_mcp_tool_arguments_json='{"query":"{query}","limit":"{results_per_query}"}',
    )

    provider = provider_from_settings(settings, "auto")

    assert isinstance(provider, McpSearchProvider)
    assert provider._args == ["minimax-coding-plan-mcp"]  # noqa: SLF001
    assert provider._env["MINIMAX_API_HOST"] == "https://api.minimax.io"  # noqa: SLF001
    assert provider._env["ALT_SEARCH_API_KEY"] == "secret"  # noqa: SLF001
    assert "MINIMAX_API_KEY" not in provider._env  # noqa: SLF001
    assert provider._tool_arguments == {  # noqa: SLF001
        "query": "{query}",
        "limit": "{results_per_query}",
    }


def test_research_runtime_config_reports_mcp_backend_readiness() -> None:
    settings = SimpleNamespace(
        research_enabled=True,
        research_default_provider="mcp_search",
        research_http_endpoint="",
        research_mcp_command="uvx",
        research_mcp_args_json='["minimax-coding-plan-mcp"]',
        research_mcp_env_json='{"MINIMAX_API_HOST":"https://api.minimax.chat"}',
        research_api_key="secret",
    )

    config = build_research_runtime_config(settings, "auto")

    assert config["resolved_provider"] == "mcp_search"
    assert config["provider_configured"] is True
    assert config["research_enabled_default"] is True
    assert config["endpoint_configured"] is False
    assert config["mcp_configured"] is True
    assert config["mcp_args_configured"] is True
    assert config["mcp_stdio_dispatcher_version"] == 2


@pytest.mark.asyncio
async def test_mcp_search_provider_calls_newline_stdio_fake_server() -> None:
    server_code = r"""
import json
import sys

def send(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    if not line.strip():
        continue
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        send({
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "1"},
            },
        })
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        send({
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {
                "tools": [{
                    "name": "web_search",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "max_results": {"type": "integer"},
                        },
                    },
                }],
            },
        })
    elif method == "tools/call":
        args = msg["params"]["arguments"]
        send({
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {
                "structuredContent": {
                    "results": [{
                        "title": "MiniMax MCP result",
                        "url": "https://example.com/minimax",
                        "snippet": args["query"],
                        "score": 0.9,
                    }],
                },
            },
        })
"""
    provider = McpSearchProvider(
        command=sys.executable,
        args=["-c", server_code],
        max_results=3,
        results_per_query=2,
        timeout_s=5,
        stdio_framing="newline",
    )

    result = await provider.search([ResearchQuery(query="MiniMax 联网搜索", rationale="test")])

    assert result.warnings == []
    assert len(result.sources) == 1
    assert result.sources[0].title == "MiniMax MCP result"
    assert result.sources[0].snippet == "MiniMax 联网搜索"


@pytest.mark.asyncio
async def test_mcp_search_provider_routes_concurrent_out_of_order_responses() -> None:
    """Parallel MCP calls share one stdout reader and retain query ordering."""
    server_code = r"""
import json
import sys
import threading
import time

output_lock = threading.Lock()
tool_list_calls = 0

def send(payload):
    with output_lock:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()

def reply_to_search(request_id, query):
    time.sleep({"q-slow": 0.06, "q-medium": 0.03}.get(query, 0.01))
    send({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "structuredContent": {
                "results": [{
                    "title": query,
                    "url": f"https://{query}.example",
                    "snippet": query,
                }],
            },
        },
    })

for line in sys.stdin:
    if not line.strip():
        continue
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}})
    elif method == "tools/list":
        tool_list_calls += 1
        if tool_list_calls > 1:
            send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -1, "message": "duplicate tools/list"}})
        else:
            send({
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"tools": [{"name": "web_search", "inputSchema": {"type": "object"}}]},
            })
    elif method == "tools/call":
        query = message["params"]["arguments"]["query"]
        threading.Thread(
            target=reply_to_search,
            args=(message["id"], query),
            daemon=True,
        ).start()
"""
    provider = McpSearchProvider(
        command=sys.executable,
        args=["-c", server_code],
        max_results=3,
        timeout_s=5,
        query_max_parallel=3,
    )

    result = await provider.search(
        [ResearchQuery(query=query) for query in ("q-slow", "q-medium", "q-fast")]
    )

    assert result.warnings == []
    assert [source.title for source in result.sources] == ["q-slow", "q-medium", "q-fast"]


@pytest.mark.asyncio
async def test_mcp_stdio_client_fails_fast_after_stdout_reader_stops() -> None:
    server_code = r"""
import json
import sys

def send(payload):
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}})
    elif message.get("method") == "notifications/initialized":
        break
"""
    async with McpStdioClient(
        command=sys.executable,
        args=["-c", server_code],
        timeout_s=5,
    ) as client:
        reader_task = client._reader_task  # noqa: SLF001 - validate failure propagation
        assert reader_task is not None
        await reader_task

        with pytest.raises(RuntimeError, match="stdout reader failed"):
            await client.request("tools/list")


def test_mcp_search_parser_treats_minimax_auth_text_as_failure() -> None:
    payload = {
        "content": [
            {
                "type": "text",
                "text": "Failed to perform search: API Error: login fail: invalid key",
            }
        ],
        "structuredContent": {
            "type": "text",
            "text": "Failed to perform search: API Error: login fail: invalid key",
        },
        "isError": False,
    }

    with pytest.raises(RuntimeError, match="login fail"):
        parse_mcp_search_result(payload)


@pytest.mark.asyncio
async def test_http_json_provider_retries_and_filters_domains() -> None:
    provider = HttpJsonResearchProvider(
        provider="http_json",
        endpoint="https://search.example.test",
        max_results=5,
        results_per_query=3,
        retry_attempts=1,
        include_domains=["example.com"],
        exclude_domains=["blocked.example.com"],
    )
    calls = 0

    async def _fake_fetch(_client: object, _query: str) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary")
        return {
            "results": [
                {
                    "title": "kept",
                    "url": "https://source.example.com/a",
                    "snippet": "useful",
                },
                {
                    "title": "blocked",
                    "url": "https://blocked.example.com/a",
                    "snippet": "bad",
                },
                {
                    "title": "other",
                    "url": "https://other.test/a",
                    "snippet": "ignored",
                },
            ]
        }

    provider._fetch_query = _fake_fetch  # type: ignore[method-assign]

    result = await provider.search([ResearchQuery(query="旧城档案")])

    assert calls == 2
    assert [source.title for source in result.sources] == ["kept"]


def test_init_request_fingerprint_changes_with_research_options() -> None:
    base = _build_long_init_request_payload(
        premise="一名记者调查旧城档案",
        genre="现实悬疑",
        tone="冷峻",
        title="",
        language="zh",
        characters_hint="",
        world_hint="旧城改造与档案馆",
        conflict_hint="城市记忆被商业利益改写",
        pov_hint="",
        opening_style="",
        ending_style="",
        extra_instructions="",
        total_chapters=20,
        words_per_chapter=3000,
        volume_mode="auto",
        chapters_per_volume=0,
        effective_volume_mode=False,
        effective_chapters_per_volume=20,
        blueprint_element_preferences=None,
    )
    with_research = _build_long_init_request_payload(
        **{
            **base["init_input"],
            "total_chapters": 20,
            "words_per_chapter": 3000,
            "volume_mode": "auto",
            "chapters_per_volume": 0,
            "effective_volume_mode": False,
            "effective_chapters_per_volume": 20,
            "blueprint_element_preferences": None,
            "research_enabled": True,
            "research_provider": "searxng",
            "research_query_hint": "城市更新 档案制度",
        }
    )

    assert _long_init_request_fingerprint(base) != _long_init_request_fingerprint(with_research)


def test_init_request_fingerprint_changes_with_research_config_fingerprint() -> None:
    base = _build_long_init_request_payload(
        premise="一名记者调查旧城档案",
        genre="现实悬疑",
        tone="冷峻",
        title="",
        language="zh",
        characters_hint="",
        world_hint="旧城改造与档案馆",
        conflict_hint="城市记忆被商业利益改写",
        pov_hint="",
        opening_style="",
        ending_style="",
        extra_instructions="",
        total_chapters=20,
        words_per_chapter=3000,
        volume_mode="auto",
        chapters_per_volume=0,
        effective_volume_mode=False,
        effective_chapters_per_volume=20,
        blueprint_element_preferences=None,
        research_enabled=True,
        research_provider="auto",
        research_config_fingerprint="a",
    )
    changed = _build_long_init_request_payload(
        **{
            **base["init_input"],
            "total_chapters": 20,
            "words_per_chapter": 3000,
            "volume_mode": "auto",
            "chapters_per_volume": 0,
            "effective_volume_mode": False,
            "effective_chapters_per_volume": 20,
            "blueprint_element_preferences": None,
            "research_enabled": True,
            "research_provider": "auto",
            "research_config_fingerprint": "b",
        }
    )

    assert _long_init_request_fingerprint(base) != _long_init_request_fingerprint(changed)


@pytest.mark.asyncio
async def test_init_web_research_report_can_resume_when_fingerprint_matches() -> None:
    spec = _spec()
    report = await run_init_web_research(
        spec=spec,
        settings=SimpleNamespace(),
        enabled=False,
        provider_name="auto",
        query_hint="",
    )
    ctx = SimpleNamespace(storage=_FakeStorage(report.model_dump(mode="json")))

    cached = _load_reusable_init_research_report(
        ctx,
        path="reports/init_web_research.json",
        spec=spec,
        enabled=False,
        provider_name="auto",
    )

    assert cached is not None
    assert cached.status == "skipped"


@pytest.mark.asyncio
async def test_init_web_research_failed_report_requires_explicit_resume_opt_in() -> None:
    spec = _spec()
    report = await run_init_web_research(
        spec=spec,
        settings=SimpleNamespace(),
        enabled=False,
        provider_name="auto",
        query_hint="",
    )
    payload = report.model_copy(update={"status": "failed"}).model_dump(mode="json")
    ctx = SimpleNamespace(storage=_FakeStorage(payload))

    assert (
        _load_reusable_init_research_report(
            ctx,
            path="reports/init_web_research.json",
            spec=spec,
            enabled=False,
            provider_name="auto",
        )
        is None
    )
    resumed = _load_reusable_init_research_report(
        ctx,
        path="reports/init_web_research.json",
        spec=spec,
        enabled=False,
        provider_name="auto",
        allow_failed=True,
    )

    assert resumed is not None
    assert resumed.status == "failed"


@pytest.mark.asyncio
async def test_init_web_research_report_not_reused_when_config_fingerprint_changes() -> None:
    spec = _spec()
    report = await run_init_web_research(
        spec=spec,
        settings=SimpleNamespace(research_default_provider="auto"),
        enabled=True,
        provider_name="auto",
        query_hint="",
    )
    ctx = SimpleNamespace(storage=_FakeStorage(report.model_dump(mode="json")))

    assert (
        _load_reusable_init_research_report(
            ctx,
            path="reports/init_web_research.json",
            spec=spec,
            enabled=True,
            provider_name="auto",
            config_fingerprint="different",
        )
        is None
    )


# ---------------------------------------------------------------------------
# LLM query planning tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_query_planning_succeeds() -> None:
    ctx = _FakeResearchCtx(
        {
            "queries": [
                {
                    "query": "旧城改造 档案管理制度 流程",
                    "rationale": "补充档案馆运作的真实细节",
                    "intent": "验证时代细节",
                    "priority": "must",
                    "locale": "zh-CN",
                    "source_preferences": ["学术", "百科"],
                    "recency_required": False,
                    "risk_if_missing": "档案调阅流程可能不符合实际制度",
                },
            ],
            "knowledge_gaps": ["具体城市档案馆权限制度"],
        },
        settings=SimpleNamespace(temp_plan_init_research_queries=0.3),
    )
    queries, gaps, warnings = await plan_research_queries_with_llm(
        ctx=ctx, spec=_spec(), max_queries=3, query_hint=""
    )

    assert len(queries) == 1
    assert queries[0].intent == "验证时代细节"
    assert queries[0].priority == "must"
    assert queries[0].locale == "zh-CN"
    assert queries[0].risk_if_missing
    assert gaps == ["具体城市档案馆权限制度"]
    assert len(ctx.calls) == 1


@pytest.mark.asyncio
async def test_llm_query_planning_falls_back_on_error() -> None:
    class _FailCtx:
        settings = SimpleNamespace()

        async def call_with_retry(self, *_a: object, **_kw: object) -> dict[str, object]:
            raise RuntimeError("LLM unavailable")

    # Simulate what run_init_web_research does when LLM fails
    try:
        await plan_research_queries_with_llm(
            ctx=_FailCtx(), spec=_spec(), max_queries=3, query_hint=""
        )
        raise AssertionError("should have raised")
    except RuntimeError:
        from novel_forge.research.service import build_research_queries

        queries = build_research_queries(_spec(), max_queries=3)
        assert len(queries) >= 1


@pytest.mark.asyncio
async def test_llm_query_planning_rejects_content_contract_typo() -> None:
    ctx = _FakeResearchCtx(
        {
            "queries": [
                {
                    "query": "旧城改造听证会",
                    "rationale": "核实制度",
                    "intent": "补充真实性",
                    "priority": "must",
                    "locale": "zh-CN",
                    "source_preferences": ["政府公报"],
                    "recurrency_required": False,
                    "risk_if_missing": "制度错误",
                }
            ],
            "knowledge_gaps": [],
        },
        settings=SimpleNamespace(),
    )

    with pytest.raises(ValueError, match="content contract mismatch"):
        await plan_research_queries_with_llm(ctx=ctx, spec=_spec(), max_queries=3)


@pytest.mark.asyncio
async def test_run_init_web_research_with_llm_planning() -> None:
    ctx = _FakeResearchCtx(
        {
            "queries": [
                {
                    "query": "城市更新 档案管理制度",
                    "rationale": "核心背景",
                    "intent": "补充世界观",
                    "priority": "must",
                    "locale": "zh-CN",
                    "source_preferences": [],
                    "recency_required": False,
                    "risk_if_missing": "缺少制度细节",
                }
            ],
            "knowledge_gaps": ["档案权限"],
        },
        settings=SimpleNamespace(research_default_provider="auto"),
    )
    report = await run_init_web_research(
        spec=_spec(),
        settings=ctx.settings,
        enabled=True,
        provider_name="auto",
        query_hint="",
        ctx=ctx,
        use_llm_planning=True,
    )

    assert report.llm_planned is True
    assert report.knowledge_gaps == ["档案权限"]
    assert report.queries[0].intent == "补充世界观"


@pytest.mark.asyncio
async def test_run_init_web_research_uses_rule_fallback_for_invalid_query_content() -> None:
    ctx = _FakeResearchCtx(
        {
            "queries": [
                {
                    "query": "城市更新 档案制度",
                    "rationale": "核实制度",
                    "intent": "补充世界观",
                    "priority": "must",
                    "locale": "zh-CN",
                    "source_preferences": ["政府公报"],
                    "recurrency_required": False,
                    "risk_if_missing": "缺少制度细节",
                }
            ],
            "knowledge_gaps": [],
        },
        settings=SimpleNamespace(research_default_provider="auto"),
    )

    report = await run_init_web_research(
        spec=_spec(),
        settings=ctx.settings,
        enabled=True,
        provider_name="auto",
        ctx=ctx,
        use_llm_planning=True,
    )

    assert report.llm_planned is False
    assert report.fallback_used == "rule_based"
    assert report.queries
    assert "content contract mismatch" in report.query_plan_warnings[0]


@pytest.mark.asyncio
async def test_run_init_web_research_llm_disabled_uses_rules() -> None:
    report = await run_init_web_research(
        spec=_spec(),
        settings=SimpleNamespace(research_default_provider="auto"),
        enabled=True,
        provider_name="auto",
        query_hint="城市更新",
        use_llm_planning=False,
    )

    assert report.llm_planned is False
    assert report.fallback_used == ""
    assert any("城市更新" in q.query for q in report.queries)


@pytest.mark.asyncio
async def test_model_prior_notes_synthesized() -> None:
    ctx = _FakeResearchCtx(
        {
            "notes": ["档案馆一般实行预约调阅制度"],
            "terminology": ["调阅单", "卷宗号"],
            "uncertainty_notes": ["具体城市可能有地方规定"],
        },
        settings=SimpleNamespace(temp_synthesize_model_prior_research=0.2),
    )
    notes = await synthesize_model_prior_notes(
        ctx=ctx,
        spec=_spec(),
        queries=[ResearchQuery(query="档案管理制度")],
        existing_sources_count=0,
        enabled=True,
    )

    assert notes.status == "succeeded"
    assert notes.enabled is True
    assert len(notes.notes) == 1
    assert notes.terminology == ["调阅单", "卷宗号"]


@pytest.mark.asyncio
async def test_model_prior_notes_disabled_returns_skipped() -> None:
    ctx = _FakeResearchCtx({})
    notes = await synthesize_model_prior_notes(
        ctx=ctx,
        spec=_spec(),
        queries=[],
        existing_sources_count=0,
        enabled=False,
    )

    assert notes.status == "skipped"
    assert notes.enabled is False
    assert ctx.calls == []


@pytest.mark.asyncio
async def test_model_prior_does_not_pollute_sources() -> None:
    ctx = _FakeResearchCtx(
        {"notes": ["先验知识"], "terminology": [], "uncertainty_notes": []},
        settings=SimpleNamespace(),
    )
    report = await run_init_web_research(
        spec=_spec(),
        settings=SimpleNamespace(research_default_provider="auto"),
        enabled=True,
        provider_name="auto",
        query_hint="",
        ctx=ctx,
        model_prior_enabled=True,
    )

    assert report.sources == []
    assert report.model_prior.status == "succeeded"
    assert report.model_prior.notes == ["先验知识"]
    assert report.prompt_context()["model_prior"]["source_type"] == "model_prior"


@pytest.mark.asyncio
async def test_research_dossier_can_use_model_prior_without_external_sources() -> None:
    report = ResearchReport(
        enabled=True,
        provider="noop",
        status="skipped",
        model_prior=ModelPriorNotes(
            enabled=True,
            status="succeeded",
            notes=["档案馆通常有调阅登记流程。"],
            terminology=["调阅登记"],
            uncertainty_notes=["具体城市规则需要外部验证。"],
        ),
    )
    ctx = _FakeResearchCtx(
        {
            "summary": "模型先验可作为低优先级背景补充。",
            "real_world_constraints": [],
            "terminology": ["调阅登记"],
            "inspiration_notes": ["可参考档案调阅流程组织冲突。"],
            "uncertainty_notes": ["具体城市规则需要外部验证。"],
            "source_refs": [],
        },
        settings=SimpleNamespace(research_dossier_enabled=True),
    )

    dossier = await synthesize_init_research_dossier(
        ctx=ctx,
        spec=_spec(),
        research_report=report,
        enabled=True,
    )

    assert dossier.status == "succeeded"
    assert dossier.source_refs == []
    assert dossier.model_prior_notes == ["档案馆通常有调阅登记流程。"]
    assert dossier.model_prior_terminology == ["调阅登记"]
    assert dossier.prompt_context()["model_prior_uncertainty_notes"] == [
        "具体城市规则需要外部验证。"
    ]
    assert len(ctx.calls) == 1


def test_research_fingerprint_changes_with_llm_planning_toggle() -> None:
    base = SimpleNamespace(
        research_default_provider="auto",
        research_http_endpoint="",
        research_use_llm_planning=True,
        research_model_prior_enabled=False,
    )
    toggled = SimpleNamespace(
        research_default_provider="auto",
        research_http_endpoint="",
        research_use_llm_planning=False,
        research_model_prior_enabled=False,
    )

    assert research_runtime_fingerprint(base, "auto") != research_runtime_fingerprint(
        toggled, "auto"
    )


def test_research_fingerprint_changes_with_mcp_env_json() -> None:
    base = SimpleNamespace(
        research_default_provider="mcp_search",
        research_mcp_command="uvx",
        research_mcp_args_json='["minimax-coding-plan-mcp"]',
        research_mcp_env_json='{"MINIMAX_API_HOST":"https://api.minimax.io"}',
        research_mcp_api_key_env="MINIMAX_API_KEY",
        research_api_key="secret",
    )
    changed = SimpleNamespace(
        research_default_provider="mcp_search",
        research_mcp_command="uvx",
        research_mcp_args_json='["minimax-coding-plan-mcp"]',
        research_mcp_env_json='{"MINIMAX_API_HOST":"https://api.minimax.chat"}',
        research_mcp_api_key_env="MINIMAX_API_KEY",
        research_api_key="secret",
    )

    assert research_runtime_fingerprint(base, "auto") != research_runtime_fingerprint(
        changed, "auto"
    )


def test_research_fingerprint_changes_with_mcp_api_key_env() -> None:
    base = SimpleNamespace(
        research_default_provider="mcp_search",
        research_mcp_command="uvx",
        research_mcp_api_key_env="MINIMAX_API_KEY",
    )
    changed = SimpleNamespace(
        research_default_provider="mcp_search",
        research_mcp_command="uvx",
        research_mcp_api_key_env="ALT_SEARCH_API_KEY",
    )

    assert research_runtime_fingerprint(base, "auto") != research_runtime_fingerprint(
        changed, "auto"
    )


@pytest.mark.asyncio
async def test_research_query_enriched_fields() -> None:
    ctx = _FakeResearchCtx(
        {
            "queries": [
                {
                    "query": "城市更新听证会流程",
                    "rationale": "听证会是关键冲突场景",
                    "intent": "验证制度细节",
                    "priority": "must",
                    "locale": "zh-CN",
                    "source_preferences": ["政府公报"],
                    "recency_required": True,
                    "risk_if_missing": "听证流程可能不符合实际制度",
                }
            ],
            "knowledge_gaps": ["听证会公众参与规则"],
        },
        settings=SimpleNamespace(),
    )
    queries, gaps, _ = await plan_research_queries_with_llm(ctx=ctx, spec=_spec(), max_queries=3)

    q = queries[0]
    assert q.intent == "验证制度细节"
    assert q.priority == "must"
    assert q.locale == "zh-CN"
    assert q.source_preferences == ["政府公报"]
    assert q.recency_required is True
    assert q.risk_if_missing == "听证流程可能不符合实际制度"


# ────────────────────────────────────────────────────────────────────────
# Batched query parallelism (research_query_max_parallel): the parallel path
# must stay behaviorally equivalent to the previous serial loop — same source
# order, same first-wins dedup/truncation, same per-query failure degradation.
# ────────────────────────────────────────────────────────────────────────


def _payload(*items: tuple[str, str]) -> dict[str, object]:
    return {
        "results": [
            {"title": title, "url": url, "content": f"snippet-{title}"} for title, url in items
        ]
    }


@pytest.mark.asyncio
async def test_http_provider_parallel_matches_serial_dedup_and_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = HttpJsonResearchProvider(
        provider="tavily",
        endpoint="https://api.tavily.com/search",
        max_results=3,
        query_max_parallel=2,
    )
    per_query_payloads = [
        _payload(("A", "https://a.example"), ("B", "https://b.example")),
        _payload(("A2", "https://a.example"), ("C", "https://c.example")),
        _payload(("D", "https://d.example")),
    ]

    async def _fake_fetch(client: object, query: str) -> dict[str, object]:
        return per_query_payloads[int(query.rsplit("-", 1)[-1])]

    monkeypatch.setattr(provider, "_fetch_query", _fake_fetch)

    result = await provider.search([ResearchQuery(query=f"q-{index}") for index in range(3)])

    # Serial expectation: q0(A,B) + q1(A2 dropped as duplicate, C) + q2(D),
    # truncated at max_results=3 keeping the first occurrence order.
    assert [source.url for source in result.sources] == [
        "https://a.example",
        "https://b.example",
        "https://c.example",
    ]
    assert [source.title for source in result.sources] == ["A", "B", "C"]
    assert result.warnings == []


@pytest.mark.asyncio
async def test_http_provider_parallel_single_query_failure_degrades_to_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = HttpJsonResearchProvider(
        provider="tavily",
        endpoint="https://api.tavily.com/search",
        query_max_parallel=3,
    )

    async def _fake_fetch(client: object, query: str) -> dict[str, object]:
        if query == "q-1":
            raise RuntimeError("boom")
        return _payload((f"ok-{query}", f"https://{query}.example"))

    monkeypatch.setattr(provider, "_fetch_query", _fake_fetch)

    result = await provider.search([ResearchQuery(query=f"q-{i}") for i in range(3)])

    assert [source.url for source in result.sources] == [
        "https://q-0.example",
        "https://q-2.example",
    ]
    assert len(result.warnings) == 1
    assert "RuntimeError: boom" in result.warnings[0]


@pytest.mark.asyncio
async def test_http_provider_parallel_respects_semaphore_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = HttpJsonResearchProvider(
        provider="tavily",
        endpoint="https://api.tavily.com/search",
        query_max_parallel=2,
    )
    active = 0
    peak = 0

    async def _fake_fetch(client: object, query: str) -> dict[str, object]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return _payload((query, f"https://{query}.example"))

    monkeypatch.setattr(provider, "_fetch_query", _fake_fetch)

    await provider.search([ResearchQuery(query=f"q-{i}") for i in range(5)])

    assert peak <= 2
    assert peak >= 1


@pytest.mark.asyncio
async def test_mcp_provider_parallel_preserves_query_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import novel_forge.research.mcp_client as mcp_module

    class _FakeMcpClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeMcpClient":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(mcp_module, "McpStdioClient", _FakeMcpClient)

    provider = McpSearchProvider(command="fake-mcp", query_max_parallel=2)

    async def _fake_call(adapter: object, query: str) -> dict[str, object]:
        if query == "q-1":
            raise RuntimeError("mcp-boom")
        return {
            "structuredContent": {"results": [{"title": query, "url": f"https://{query}.example"}]}
        }

    monkeypatch.setattr(provider, "_call_with_retry", _fake_call)

    result = await provider.search([ResearchQuery(query=f"q-{i}") for i in range(3)])

    assert [source.url for source in result.sources] == [
        "https://q-0.example",
        "https://q-2.example",
    ]
    assert len(result.warnings) == 1
    assert "RuntimeError: mcp-boom" in result.warnings[0]


def test_provider_from_settings_applies_research_query_max_parallel() -> None:
    settings = SimpleNamespace(
        research_default_provider="tavily",
        research_api_key="key",
        research_query_max_parallel=4,
    )
    provider = provider_from_settings(settings, "auto")
    assert isinstance(provider, HttpJsonResearchProvider)
    assert provider._query_max_parallel == 4

    mcp_settings = SimpleNamespace(
        research_default_provider="mcp_search",
        research_mcp_command="fake-mcp",
        research_api_key="",
        research_query_max_parallel=3,
    )
    mcp_provider = provider_from_settings(mcp_settings, "auto")
    assert isinstance(mcp_provider, McpSearchProvider)
    assert mcp_provider._query_max_parallel == 3
