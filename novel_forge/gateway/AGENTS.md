# Gateway — AGENTS.md

## Core Files

| File | Purpose |
|------|---------|
| `router.py` | Routes `TaskType → provider:model`. Reads `NOVEL_FORGE_TASK_ROUTING` env |
| `factory.py` | Builds Router with provider adapters plus mock fallback wired |
| `base.py` | `ProviderAdapter` ABC — all adapters implement this |
| `mock.py` | `MockAdapter` fallback — returns plausible mock when no provider configured |

## Adapter Modules (13)

| Adapter | Extends | Notes |
|---------|---------|-------|
| `openai.py` | - | Official OpenAI SDK |
| `openai_compat.py` | - | Base for DeepSeek/Tongyi/Kimi/MiniMax/Ollama/SiliconFlow/Volcengine Ark/OpenCode |
| `anthropic.py` | - | Claude (standalone, not OpenAI compat) |
| `deepseek.py` | `OpenAICompatAdapter` | |
| `tongyi.py` | `OpenAICompatAdapter` | Alibaba 通义千问 |
| `kimi.py` | `OpenAICompatAdapter` | Moonshot |
| `tencent_hunyuan.py` | - | Tencent Cloud API (not OpenAI compat) |
| `minimax.py` | `OpenAICompatAdapter` | |
| `ollama.py` | `OpenAICompatAdapter` | Local models |
| `siliconflow.py` | `OpenAICompatAdapter` | SiliconFlow-hosted models |
| `volcengine_ark.py` | `OpenAICompatAdapter` | 火山方舟 / Volcano Ark (Agent Plan + pay-as-you-go). Default base URL = `/api/plan/v3` (智能体套餐). Thinking uses Volcengine-native `{"thinking": {"type": "enabled"}}` payload. |
| `opencode.py` | `OpenAICompatAdapter` | OpenCode Go (开源模型聚合订阅, `https://opencode.ai/zen/go/v1`). 21 个开源模型 (Grok/GLM/Kimi/MiMo/MiniMax/Qwen/DeepSeek). 思考模式透传, 不注入 extra_body. |

## Routing Config

```bash
# Primary routing
NOVEL_FORGE_TASK_ROUTING='{"DRAFT_CHAPTER":"openai:gpt-4o","EDIT_CHAPTER":"deepseek:deepseek-chat,thinking"}'

# Fallback routing (tried in order if primary fails)
NOVEL_FORGE_TASK_FALLBACK_ROUTING='{"draft_chapter":["tencent:hy3","deepseek:deepseek-v4-flash"]}'
```

- `model_profiles.json` (gitignored) overrides routing when present
- Format: `provider:model` — comma for thinking + non-thinking variants

## Supporting Files

| File | Purpose |
|------|---------|
| `rate_limiter.py` | Rate limiting across providers |
| `pricing.py` | Per-model price calculation |
| `profiles.py` | Model profiles. **Migration pending**: `UnifiedSettingsLoader` (TODOs at lines 929, 1399) |
| `secure_keys.py` | Secure API key handling |
| `types.py` | Type definitions |
| `cache.py` | Response caching |
| `client_lifecycle.py` | Client lifecycle management |
| `embedding.py` / `embedding_config.py` | Embedding service config |

## Anti-Patterns

- **Never** hardcode provider selection — use routing config
- **Never** skip `factory.py` — it wires all adapters
- **Always** use `MockAdapter` fallback in tests
- **Do not** modify `profiles.py` for config — use env vars or `model_profiles.json`

## Debugging

| Problem | Check |
|---------|-------|
| Wrong model called | `router.py` + `NOVEL_FORGE_TASK_ROUTING` env |
| All calls go to Mock | No real provider API key configured |
| Adapter init failure | `factory.py` — verify adapter class passed correctly |
