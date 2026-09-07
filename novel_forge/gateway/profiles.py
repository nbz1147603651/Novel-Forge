"""Model profile management — user-defined model configurations and task routing.

Architecture:
    ModelProfile   — A single AI model entry (provider + model_id + API key).
    TaskRouteEntry — Per-pipeline-task assignment: which profile + capability flags.
    ProfilesConfig — The full config object: list of profiles + route maps
                     (primary + fallback).

Persistence:
    Saved as ``model_profiles.json`` in the workspace root without API keys.
    On first launch, imported from ``.env`` / Settings for backward compat.
    Legacy JSON entries that still contain ``api_key`` are migrated into ``.env``
    and scrubbed on load so runtime can keep working without re-saving.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

from novel_forge.core.config import get_runtime_config_dir, get_writable_env_path
from novel_forge.core.parsing.token_utils import estimate_text_length_tokens
from novel_forge.gateway.embedding_config import is_embedding_model
from novel_forge.gateway.model_capabilities import (
    resolve_model_capability,
    resolve_model_capability_any_provider,
)
from novel_forge.gateway.reasoning import normalize_thinking_mode, thinking_mode_enabled
from novel_forge.persistence.filesystem import atomic_write_text

logger = logging.getLogger(__name__)

# ── Known providers and their common models (for UI hints) ────────────────────

TONGYI_CODING_PLAN_BASE_URL = "https://coding.dashscope.aliyuncs.com/compatible-mode/v1"
TONGYI_TOKEN_PLAN_BASE_URL = (
    "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)

KNOWN_PROVIDERS: dict[str, dict[str, Any]] = {
    "tongyi": {
        "label": "阿里百炼",
        "models": [
            # Qwen3.7 系列 (2026-06 最新旗舰)
            "qwen3.7-max",
            "qwen3.7-plus",
            # Qwen3.6 系列
            "qwen3.6-flash",
            # Qwen3.5 系列
            "qwen3.5-plus",
            "qwen3.5-plus-2026-02-15",
            "qwen3.5-flash",
            "qwen3.5-flash-2026-02-23",
            "qwen3.5-omni-plus",
            # Qwen Plus 系列
            "qwen-plus",
            "qwen-plus-latest",
            "qwen-plus-2025-12-01",
            "qwen-plus-2025-09-11",
            "qwen-plus-2025-07-28",
            # Qwen Flash 系列
            "qwen-flash",
            "qwen-flash-2025-07-28",
            # Qwen Turbo 系列
            "qwen-turbo",
            "qwen-turbo-latest",
            "qwen-turbo-2025-07-15",
            # Qwen Long 系列
            "qwen-long",
            "qwen-long-latest",
            "qwen-long-2025-01-25",
            # Qwen3 Max 系列
            "qwen3-max",
            "qwen3-max-2026-01-23",
            "qwen-max",
            "qwen-max-latest",
            # Qwen3 Next / Qwen3 2507
            "qwen3-next-80b-a3b-thinking",
            "qwen3-next-80b-a3b-instruct",
            "qwen3-235b-a22b-thinking-2507",
            "qwen3-235b-a22b-instruct-2507",
            "qwen3-30b-a3b-thinking-2507",
            "qwen3-30b-a3b-instruct-2507",
            # Qwen3 Dense 系列（2025-04 发布，全系支持思考模式）
            "qwen3-235b-a22b",
            "qwen3-32b",
            "qwen3-30b-a3b",
            "qwen3-14b",
            "qwen3-8b",
            # Qwen3 Coder
            "qwen3-coder-next",
            "qwen3-coder-plus",
            # QwQ 推理系列
            "qwq-32b",
            "qwq-plus",
            # 百炼托管 DeepSeek（含版本钉）
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            "deepseek-v3.2",
            "deepseek-v3.2-exp",
            "deepseek-v3.1",
            "deepseek-r1",
            "deepseek-r1-0528",
            "deepseek-v3",
            "deepseek-v3-0324",
            # 嵌入模型
            "text-embedding-v4",
            "text-embedding-v3",
            "text-embedding-v2",
        ],
    },
    "tongyi_coding": {
        "label": "阿里百炼 Coding Plan",
        # coding.dashscope.aliyuncs.com — 面向 IDE 编码工具的专用端点（Pro 套餐）
        # 整合了千问、GLM、Kimi、MiniMax 等顶级模型
        # 不支持 enable_thinking，不需要额外参数
        # 官方支持列表（2026-04-08）：https://help.aliyun.com/zh/model-studio/coding-plan
        "models": [
            # 官方精确白名单（2026-07-13 核验）
            "qwen3.7-plus",
            "qwen3.6-plus",
            "kimi-k2.5",
            "glm-5",
            "MiniMax-M2.5",
            "qwen3.5-plus",
            "qwen3-max-2026-01-23",
            "qwen3-coder-next",
            "qwen3-coder-plus",
            "glm-4.7",
        ],
    },
    "tongyi_token_plan": {
        "label": "阿里百炼 Token Plan",
        "default_base_url": TONGYI_TOKEN_PLAN_BASE_URL,
        "connection_hint": (
            "仅使用以 sk-sp- 开头的 Token Plan 专属 API Key；"
            "不得与按量付费或 Coding Plan 的密钥、接口地址混用。"
        ),
        # 官方 Token Plan 概述（2026-08）中的文本生成模型；需要其他已开通模型时可填写自定义 ID。
        "models": [
            "qwen3.8-max",
            "qwen3.8-flash",
            "qwen3.7-max",
            "qwen3.7-plus",
            "qwen3.6-flash",
            "deepseek-v4-pro",
            "deepseek-v4-pro-0813",
            "deepseek-v4-flash-0731",
            "glm-5.2",
        ],
    },
    "deepseek": {
        "label": "DeepSeek",
        # 官方文档 2026-04: V4 系列为最新模型
        # deepseek-chat / deepseek-reasoner 将于 2026-07-24 废弃
        # 对应关系: deepseek-chat -> v4-flash 非思考, deepseek-reasoner -> v4-flash 思考
        "models": [
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "deepseek-chat",  # 废弃于 2026-07-24
            "deepseek-reasoner",  # 废弃于 2026-07-24
        ],
    },
    "openai": {
        "label": "OpenAI",
        "models": [
            # GPT-5.6 系列（2026-07 当前）
            "gpt-5.6",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            # GPT-5.5 系列 (2026-06 最新旗舰)
            "gpt-5.5",
            "gpt-5.5-pro",
            # GPT-5.4 系列 (经济型)
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.4-nano",
            # GPT-4o 系列
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            # 推理模型
            "o1",
            "o1-mini",
            "o3-mini",
            "o3",
        ],
    },
    "anthropic": {
        "label": "Anthropic",
        "models": [
            # Claude 5 系列
            "claude-fable-5",
            "claude-sonnet-5",
            # Claude 4.8 系列 (2026-06 最新旗舰，NextOpus)
            "claude-opus-4-8",
            # Claude 4.7/4.6 系列
            "claude-opus-4-7",
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            # Claude 4.5 系列
            "claude-haiku-4-5",
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-5-20250929",
        ],
    },
    "kimi": {
        "label": "Kimi (Moonshot)",
        "models": [
            # K2.7 Code 系列（2026-07 当前）
            "kimi-k2.7-code",
            "kimi-k2.7-code-highspeed",
            # K2.6 系列 (2026-06 最新)
            "kimi-k2.6",
            # K2.5 系列
            "kimi-k2.5",
            # Moonshot V1 系列 (旧版)
            "moonshot-v1-8k",
            "moonshot-v1-32k",
            "moonshot-v1-128k",
            "moonshot-v1-8k-vision-preview",
            "moonshot-v1-32k-vision-preview",
            "moonshot-v1-128k-vision-preview",
        ],
    },
    "mimo": {
        "label": "小米 MiMo",
        "models": [
            "mimo-v2.5-pro",
            "mimo-v2.5",
            "mimo-v2.5-pro-ultraspeed",
        ],
    },
    "tencent": {
        "label": "腾讯混元",
        "models": [
            # TokenHub 当前文本模型；旧 2.0/T1 系列已于 2026-06-22 退役
            "hy3",
            "hy3-preview",
            "hy-mt2-pro",
            "hy-mt2-plus",
            "hy-mt2-lite",
            "hunyuan-role-latest",
            "hy-role",
        ],
    },
    "ollama": {
        "label": "Ollama (本地)",
        "models": [
            # 文本生成模型
            "llama3.2",
            "llama3.1",
            "llama3",
            "llama3.1-8b",
            "llama3.1-70b",
            "qwen2.5:7b",
            "qwen2.5:14b",
            "qwen2.5:32b",
            "mistral",
            "mixtral",
            "deepseek-coder",
            # 嵌入模型
            "nomic-embed-text",
            "mxbai-embed-large",
            "all-minilm",
        ],
    },
    "minimax": {
        "label": "MiniMax",
        "models": [
            # M3 系列
            "MiniMax-M3",
            # M2.7 系列
            "MiniMax-M2.7",
            "MiniMax-M2.7-highspeed",
            # M2.5 系列
            "MiniMax-M2.5",
            "MiniMax-M2.5-highspeed",
            # M2.1 系列
            "MiniMax-M2.1",
            "MiniMax-M2.1-highspeed",
            # M2 系列
            "MiniMax-M2",
        ],
    },
    "siliconflow": {
        "label": "硅基流动",
        "models": [
            # DeepSeek 系列
            "deepseek-ai/DeepSeek-V4-Flash",
            "deepseek-ai/DeepSeek-V4-Pro",
            "deepseek-ai/DeepSeek-V3.2",
            "deepseek-ai/DeepSeek-V3.1-Terminus",
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
            # Kimi 系列
            "moonshotai/Kimi-K2.6",
            "moonshotai/Kimi-K2.5",
            # GLM 系列
            "zai-org/GLM-5.1",
            "zai-org/GLM-5",
            "zai-org/GLM-4.7",
            "zai-org/GLM-4.5V",
            "zai-org/GLM-4.5-Air",
            # Qwen 系列
            "Qwen/Qwen3.6-35B-A3B",
            "Qwen/Qwen3.6-27B",
            "Qwen/Qwen3.5-397B-A17B",
            "Qwen/Qwen3.5-122B-A10B",
            "Qwen/Qwen3.5-35B-A3B",
            "Qwen/Qwen3.5-27B",
            "Qwen/Qwen3-235B-A22B",
            "Qwen/Qwen3-32B",
            "Qwen/Qwen3-8B",
            "Qwen/Qwen2.5-72B-Instruct",
            # MiniMax 系列
            "MiniMaxAI/MiniMax-M2.5",
            # 腾讯混元系列
            "tencent/Hunyuan-A13B-Instruct",
            # 嵌入模型
            "BAAI/bge-m3",
            "BAAI/bge-reranker-v2-m3",
        ],
    },
    "volcengine_ark": {
        "label": "火山方舟 (Volcano Ark)",
        # 火山方舟 Agent Plan (智能体套餐) - OpenAI 兼容端点
        # https://ark.cn-beijing.volces.com/api/plan/v3
        # Agent Plan 使用点号格式的 Model Name（无日期后缀）
        "models": [
            "deepseek-v3.2",
            "doubao-seed-2.0-code",
            "doubao-seed-2.0-pro",
            "doubao-seed-2.0-lite",
            "minimax-m2.7",
            "glm-5.1",
            "kimi-k2.6",
            "doubao-seed-2.0-mini",
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            "minimax-m3",
        ],
    },
    "opencode": {
        "label": "OpenCode Go",
        # OpenCode Go - 开源模型聚合订阅服务 (首月 $5, 之后 $10/月)
        # https://opencode.ai/zen/go/v1 - OpenAI 兼容端点
        # API key 从 https://opencode.ai/auth 获取
        # 模型清单核验自 models.dev (2026-07-18, 21 个模型)
        "models": [
            # DeepSeek 系列
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            # GLM 系列
            "glm-5.2",
            "glm-5.1",
            "glm-5",
            # xAI Grok
            "grok-4.5",
            # Kimi 系列
            "kimi-k3",
            "kimi-k2.7-code",
            "kimi-k2.6",
            "kimi-k2.5",
            # 小米 MiMo 系列
            "mimo-v2.5-pro",
            "mimo-v2.5",
            "mimo-v2-pro",
            "mimo-v2-omni",
            # MiniMax 系列
            "minimax-m3",
            "minimax-m2.7",
            "minimax-m2.5",
            # Qwen 系列
            "qwen3.7-max",
            "qwen3.7-plus",
            "qwen3.6-plus",
            "qwen3.5-plus",
        ],
    },
}

# Per-model capability flags: (supports_thinking, supports_multi_turn)
# Based on each provider's official documentation as of 2026-03.
# Models not listed default to (False, True).
#
# Tongyi (DashScope):
#   - qwen3-max / qwen-max 系列: 支持 enable_thinking 和多轮
#   - qwen3.5-plus / qwen-plus 系列: 支持 enable_thinking 和多轮
#   - qwen3.5-flash / qwen-flash 系列: 支持 enable_thinking 和多轮
#   - qwen-turbo 系列: 支持 enable_thinking 和多轮，但官方建议逐步迁移到 flash
#   - qwen-long 系列: 长文本模型，不支持思考，支持多轮
#   - qwen3 全系 (Dense + MoE): 支持思考+多轮 (2025-04 发布)
#   - qwq 系列: 推理专用, 天然支持思考+多轮
#   - 百炼托管 deepseek-r1/r1-0528: 思考+多轮
#   - 百炼托管 deepseek-v3/v3-0324/v3.1/v3.2/v3.2-exp: 仅多轮
# DeepSeek 官方 API:
#   - deepseek-chat (V3 / V3-0324): 不支持思考, 支持多轮
#   - deepseek-reasoner (R1): 支持思考, 多轮 (官方文档明确支持)
# OpenAI:
#   - gpt-4o 系列: 不支持 reasoning, 支持多轮
#   - o1/o3 系列: 支持 reasoning, 支持多轮
_MODEL_CAPABILITIES: dict[str, dict[str, tuple[bool, bool]]] = {
    "tongyi_coding": {
        # Coding Plan 端点不支持 enable_thinking，所有模型 thinking=False
        "qwen3.7-plus": (False, True),
        "qwen3.6-plus": (False, True),
        "qwen3.5-plus": (False, True),
        "kimi-k2.6": (False, True),
        "kimi-k2.5": (False, True),
        "glm-5.1": (False, True),
        "glm-5": (False, True),
        "MiniMax-M2.7": (False, True),
        "MiniMax-M2.5": (False, True),
        "qwen3-max-2026-01-23": (False, True),
        "qwen3-coder-next": (False, True),
        "qwen3-coder-plus": (False, True),
        "glm-4.7": (False, True),
    },
    "tongyi_token_plan": {
        # Token Plan 的文本模型可进行推理和多轮对话；适配器仍通过其专属端点调用。
        "qwen3.8-max": (True, True),
        "qwen3.8-flash": (True, True),
        "qwen3.7-max": (True, True),
        "qwen3.7-plus": (True, True),
        "qwen3.6-flash": (True, True),
        "deepseek-v4-pro": (True, True),
        "deepseek-v4-pro-0813": (True, True),
        "deepseek-v4-flash-0731": (True, True),
        "glm-5.2": (True, True),
    },
    "tongyi": {
        # Qwen3.7 系列 (2026-06 最新旗舰)
        "qwen3.7-max": (True, True),
        "qwen3.7-plus": (True, True),
        # Qwen3.6 系列
        "qwen3.6-flash": (True, True),
        # Qwen Max
        "qwen3-max": (True, True),
        "qwen3-max-2026-01-23": (True, True),
        "qwen-max": (True, True),
        "qwen-max-latest": (True, True),
        # Qwen Plus / Qwen3.5 Plus
        "qwen3.5-plus": (True, True),
        "qwen3.5-plus-2026-02-15": (True, True),
        "qwen-plus": (True, True),
        "qwen-plus-latest": (True, True),
        "qwen-plus-2025-12-01": (True, True),
        "qwen-plus-2025-09-11": (True, True),
        "qwen-plus-2025-07-28": (True, True),
        # Qwen Flash / Qwen3.5 Flash
        "qwen3.5-flash": (True, True),
        "qwen3.5-flash-2026-02-23": (True, True),
        "qwen3.5-omni-plus": (True, True),
        "qwen-flash": (True, True),
        "qwen-flash-2025-07-28": (True, True),
        # Qwen Turbo
        "qwen-turbo": (True, True),
        "qwen-turbo-latest": (True, True),
        "qwen-turbo-2025-07-15": (True, True),
        # Qwen Long
        "qwen-long": (False, True),
        "qwen-long-latest": (False, True),
        "qwen-long-2025-01-25": (False, True),
        # Qwen3 MoE 系列
        "qwen3-235b-a22b": (True, True),
        "qwen3-235b-a22b-thinking-2507": (True, True),
        "qwen3-235b-a22b-instruct-2507": (False, True),
        "qwen3-30b-a3b": (True, True),
        "qwen3-30b-a3b-thinking-2507": (True, True),
        "qwen3-30b-a3b-instruct-2507": (False, True),
        "qwen3-next-80b-a3b-thinking": (True, True),
        "qwen3-next-80b-a3b-instruct": (False, True),
        # Qwen3 Dense 系列 (2025-04)
        "qwen3-32b": (True, True),
        "qwen3-14b": (True, True),
        "qwen3-8b": (True, True),
        # Qwen3 Coder
        "qwen3-coder-next": (True, True),
        "qwen3-coder-plus": (True, True),
        # QwQ 推理系列
        "qwq-32b": (True, True),
        "qwq-plus": (True, True),
        # 百炼托管 DeepSeek（含版本钉）
        "deepseek-v4-pro": (True, True),
        "deepseek-v4-flash": (True, True),
        "deepseek-v3.2": (False, True),
        "deepseek-v3.2-exp": (False, True),
        "deepseek-v3.1": (False, True),
        "deepseek-r1": (True, True),
        "deepseek-r1-0528": (True, True),
        "deepseek-v3": (False, True),
        "deepseek-v3-0324": (False, True),
        # 嵌入模型 - 不用于生成，仅用于检索
        "text-embedding-v4": (False, False),
        "text-embedding-v3": (False, False),
        "text-embedding-v2": (False, False),
    },
    "deepseek": {
        # V4 系列 (2026-04): 1M 上下文, 384K 输出, 支持思考+多轮
        "deepseek-v4-flash": (True, True),  # V4-Flash, 低成本高性能
        "deepseek-v4-pro": (True, True),  # V4-Pro, 旗舰性能
        # 旧模型 (废弃于 2026-07-24)
        "deepseek-chat": (False, True),  # V3, 8K 输出
        "deepseek-reasoner": (True, True),  # R1, 64K 输出
    },
    "siliconflow": {
        # DeepSeek 系列 (via SiliconFlow)
        "deepseek-ai/DeepSeek-V4-Flash": (True, True),
        "deepseek-ai/DeepSeek-V4-Pro": (True, True),
        "deepseek-ai/DeepSeek-V3.2": (True, True),
        "deepseek-ai/DeepSeek-V3.1-Terminus": (False, True),
        "deepseek-ai/DeepSeek-V3": (False, True),
        "deepseek-ai/DeepSeek-R1": (True, True),
        # Kimi 系列 (via SiliconFlow)
        "moonshotai/Kimi-K2.6": (True, True),
        "moonshotai/Kimi-K2.5": (True, True),
        # GLM 系列 (via SiliconFlow)
        "zai-org/GLM-5.1": (True, True),
        "zai-org/GLM-5": (True, True),
        "zai-org/GLM-4.7": (True, True),
        "zai-org/GLM-4.5V": (False, True),
        "zai-org/GLM-4.5-Air": (False, True),
        # Qwen 系列 (via SiliconFlow) — 支持 enable_thinking
        "Qwen/Qwen3.6-35B-A3B": (True, True),
        "Qwen/Qwen3.6-27B": (True, True),
        "Qwen/Qwen3.5-397B-A17B": (True, True),
        "Qwen/Qwen3.5-122B-A10B": (True, True),
        "Qwen/Qwen3.5-35B-A3B": (True, True),
        "Qwen/Qwen3.5-27B": (True, True),
        "Qwen/Qwen3-235B-A22B": (True, True),
        "Qwen/Qwen3-32B": (True, True),
        "Qwen/Qwen3-8B": (True, True),
        "Qwen/Qwen2.5-72B-Instruct": (False, True),
        # MiniMax 系列 (via SiliconFlow)
        "MiniMaxAI/MiniMax-M2.5": (True, True),
        # 腾讯混元系列 (via SiliconFlow)
        "tencent/Hunyuan-A13B-Instruct": (True, True),
        # 嵌入模型 - 不用于生成，仅用于检索
        "BAAI/bge-m3": (False, False),
        "BAAI/bge-reranker-v2-m3": (False, False),
    },
    "openai": {
        "gpt-5.5": (True, True),
        "gpt-5.5-pro": (True, True),
        "gpt-5.4": (True, True),
        "gpt-5.4-mini": (True, True),
        "gpt-5.4-nano": (True, True),
        "gpt-4o": (False, True),
        "gpt-4o-mini": (False, True),
        "gpt-4-turbo": (False, True),
        "o1": (True, True),
        "o1-mini": (True, True),
        "o3-mini": (True, True),
        "o3": (True, True),
    },
    "anthropic": {
        # Claude 4.8 系列 (2026-06 最新旗舰，NextOpus)
        "claude-opus-4-8": (True, True),
        # Claude 4.7/4.6 系列
        "claude-opus-4-7": (True, True),
        "claude-opus-4-6": (True, True),
        "claude-sonnet-4-6": (True, True),
        # Claude 4.5 系列
        "claude-haiku-4-5": (True, True),
        "claude-haiku-4-5-20251001": (True, True),
        "claude-sonnet-4-5-20250929": (True, True),
        # 废弃模型 (将于 2026-06-15 退役)
        "claude-sonnet-4-20250514": (True, True),
        "claude-opus-4-20250514": (True, True),
        # 旧版模型
        "claude-3-5-haiku-20241022": (False, True),
        "claude-3-opus-20240229": (True, True),
    },
    "kimi": {
        "kimi-k2.6": (True, True),
        "kimi-k2.5": (True, True),
        "kimi-k2-thinking": (True, True),
        "kimi-k2-thinking-turbo": (True, True),
        "kimi-k2-turbo-preview": (False, True),
        "kimi-k2-0905-preview": (False, True),
        "kimi-k2-0711-preview": (False, True),
        "moonshot-v1-8k": (False, True),
        "moonshot-v1-32k": (False, True),
        "moonshot-v1-128k": (False, True),
        "moonshot-v1-8k-vision-preview": (False, True),
        "moonshot-v1-32k-vision-preview": (False, True),
        "moonshot-v1-128k-vision-preview": (False, True),
    },
    "mimo": {
        "mimo-v2.5-pro": (True, True),
        "mimo-v2.5": (True, True),
        "mimo-v2.5-pro-ultraspeed": (True, True),
    },
    "minimax": {
        # M3 系列 (2026-06 最新旗舰，1M 上下文)
        "MiniMax-M3": (True, True),
        # M2.7 系列
        "MiniMax-M2.7": (True, True),
        "MiniMax-M2.7-highspeed": (True, True),
        # M2.5 系列
        "MiniMax-M2.5": (True, True),
        "MiniMax-M2.5-highspeed": (True, True),
        # M2.1 系列
        "MiniMax-M2.1": (True, True),
        "MiniMax-M2.1-highspeed": (True, True),
        # M2 系列
        "MiniMax-M2": (False, True),
    },
    "tencent": {
        # HY 2.0 系列 (2026-06 最新)
        "hunyuan-2.0-thinking": (True, True),
        "hunyuan-2.0-instruct": (False, True),
        # T1 推理系列
        "hunyuan-t1-latest": (True, True),
        # TurboS 快速系列
        "hunyuan-turbos-latest": (False, True),
        # 其他模型
        "hunyuan-pro": (False, True),
        "hunyuan-standard": (False, True),
        "hunyuan-a13b": (True, True),
        "hunyuan-lite": (False, True),
        # 角色/翻译模型
        "hunyuan-role-latest": (False, True),
        "hunyuan-large-role-latest": (False, True),
        "hunyuan-translation": (False, True),
        "hunyuan-translation-lite": (False, True),
        # 嵌入模型 - 不用于生成，仅用于检索
        "hunyuan-embedding": (False, False),
    },
    "volcengine_ark": {
        "deepseek-v3.2": (False, True),
        "doubao-seed-2.0-code": (False, True),
        "doubao-seed-2.0-pro": (True, True),
        "doubao-seed-2.0-lite": (True, True),
        "minimax-m2.7": (False, True),
        "glm-5.1": (False, True),
        "kimi-k2.6": (False, True),
        "doubao-seed-2.0-mini": (True, True),
        "deepseek-v4-pro": (True, True),
        "deepseek-v4-flash": (True, True),
        "minimax-m3": (False, True),
    },
    # OpenCode Go - 聚合网关, 各模型思考能力依据 models.dev 标注 (2026-07-18)
    # 思考参数透传: adapter 不塞 extra_body, 由 Go 后端按上游模型默认行为处理。
    "opencode": {
        # DeepSeek V4 系列 - 支持思考
        "deepseek-v4-flash": (True, True),
        "deepseek-v4-pro": (True, True),
        # GLM 系列 - 支持思考
        "glm-5.2": (True, True),
        "glm-5.1": (True, True),
        "glm-5": (True, True),
        # Grok 4.5 - 支持思考
        "grok-4.5": (True, True),
        # Kimi 系列 - 支持思考
        "kimi-k3": (True, True),
        "kimi-k2.7-code": (True, True),
        "kimi-k2.6": (True, True),
        "kimi-k2.5": (True, True),
        # MiMo 系列 - 支持思考
        "mimo-v2.5-pro": (True, True),
        "mimo-v2.5": (True, True),
        "mimo-v2-pro": (True, True),
        "mimo-v2-omni": (True, True),
        # MiniMax 系列 - 支持思考
        "minimax-m3": (True, True),
        "minimax-m2.7": (True, True),
        "minimax-m2.5": (True, True),
        # Qwen 系列 - 支持思考
        "qwen3.7-max": (True, True),
        "qwen3.7-plus": (True, True),
        "qwen3.6-plus": (True, True),
        "qwen3.5-plus": (True, True),
    },
    "ollama": {
        # Ollama 本地模型能力
        # 文本生成模型 - 不支持思考，支持多轮
        "llama3.2": (False, True),
        "llama3.1": (False, True),
        "llama3": (False, True),
        "llama3.1-8b": (False, True),
        "llama3.1-70b": (False, True),
        "qwen2.5:7b": (False, True),
        "qwen2.5:14b": (False, True),
        "qwen2.5:32b": (False, True),
        "mistral": (False, True),
        "mixtral": (False, True),
        "deepseek-coder": (False, True),
        # 嵌入模型 - 不用于生成，仅用于检索
        "nomic-embed-text": (False, False),
        "mxbai-embed-large": (False, False),
        "all-minilm": (False, False),
    },
}


def normalize_provider_id(provider: str) -> str:
    """Normalize provider names used by profile config and UI forms."""
    value = str(provider or "").strip().lower()
    return {
        "aliyun": "tongyi",
        "dashscope": "tongyi",
        "alibaba": "tongyi",
        "moonshot": "kimi",
        "moonshotai": "kimi",
        "xiaomi": "mimo",
        "xiaomi_mimo": "mimo",
        "xiaomimimo": "mimo",
        "hunyuan": "tencent",
        "tencent_hunyuan": "tencent",
        # 火山方舟别名 — 官方名称是「火山方舟」/ Volcano Ark / Volcengine Ark
        "ark": "volcengine_ark",
        "volcengine": "volcengine_ark",
        "volcengineark": "volcengine_ark",
        "volc_ark": "volcengine_ark",
        "volces": "volcengine_ark",
        "doubao": "volcengine_ark",
        "bytedance": "volcengine_ark",
    }.get(value, value)


def _case_insensitive_get(mapping: dict[str, Any], key: str) -> Any | None:
    """Lookup helper for providers with mixed-case model identifiers."""
    if key in mapping:
        return mapping[key]
    lowered = key.lower()
    for candidate, value in mapping.items():
        if candidate.lower() == lowered:
            return value
    return None


def get_model_capabilities(provider: str, model_id: str) -> tuple[bool, bool]:
    """Return ``(supports_thinking, supports_multi_turn)`` for a model."""
    normalized_provider = normalize_provider_id(provider)
    canonical_id = canonical_model_id(model_id)
    record = resolve_model_capability(normalized_provider, canonical_id)
    if record.profile_match:
        if not record.is_available:
            return (False, bool(record.supports_multi_turn))
        if record.supports_thinking is not None and record.supports_multi_turn is not None:
            return (record.supports_thinking, record.supports_multi_turn)
    provider_map = _MODEL_CAPABILITIES.get(normalized_provider, {})
    capability = _case_insensitive_get(provider_map, canonical_id)
    if capability is None and normalized_provider == "custom":
        for known_map in _MODEL_CAPABILITIES.values():
            capability = _case_insensitive_get(known_map, canonical_id)
            if capability is not None:
                break
    return cast(tuple[bool, bool], capability) if capability is not None else (False, True)


@dataclass(frozen=True)
class ModelThinkingCapability:
    """Audited reasoning controls exposed by one provider/model API surface."""

    control: str
    modes: tuple[str, ...]
    default_mode: str
    source_urls: tuple[str, ...] = ()
    notes: str = ""

    @property
    def supported(self) -> bool:
        return self.control not in {"", "unsupported"}

    @property
    def user_selectable(self) -> bool:
        return self.supported and self.control != "forced" and len(self.modes) > 1

    def normalize(self, mode: str = "", *, legacy_thinking: bool = False) -> str:
        candidate = normalize_thinking_mode(mode, thinking=legacy_thinking)
        if candidate in self.modes:
            return candidate
        if candidate == "on":
            for preferred in ("adaptive", "medium", "high", "max", "forced"):
                if preferred in self.modes:
                    return preferred
        if candidate == "off" and "off" in self.modes:
            return "off"
        return self.default_mode or (self.modes[0] if self.modes else "off")


def get_model_thinking_capability(provider: str, model_id: str) -> ModelThinkingCapability:
    """Return the documented reasoning control instead of a lossy boolean."""

    normalized_provider = normalize_provider_id(provider)
    canonical_id = canonical_model_id(model_id)
    record = resolve_model_capability(normalized_provider, canonical_id)
    if record.thinking_control and record.thinking_modes:
        return ModelThinkingCapability(
            control=record.thinking_control,
            modes=record.thinking_modes,
            default_mode=(record.default_thinking_mode or record.thinking_modes[0]),
            source_urls=record.source_urls,
            notes=record.notes,
        )

    supports_thinking, _supports_multi = get_model_capabilities(provider, model_id)
    if not supports_thinking:
        return ModelThinkingCapability(
            control="unsupported",
            modes=("off",),
            default_mode="off",
            source_urls=record.source_urls,
            notes=record.notes,
        )
    return ModelThinkingCapability(
        control="toggle",
        modes=("off", "on"),
        default_mode="off",
        source_urls=record.source_urls,
        notes=record.notes,
    )


# ── Per-model maximum output token limits ─────────────────────────────────────
# Used by the router to clamp ``max_tokens`` before sending to the API.
# Models not listed fall back to a conservative default (8192).

_MODEL_MAX_OUTPUT_TOKENS: dict[str, int] = {
    # DeepSeek 官方 API (api.deepseek.com)
    # V4 系列: 1M 上下文窗口, 384K 最大输出
    "deepseek-v4-flash": 384000,  # V4-Flash, 低成本 (¥0.14/1M input)
    "deepseek-v4-pro": 384000,  # V4-Pro, 旗舰性能 (¥1.74/1M input)
    # 旧模型 (废弃于 2026-07-24)
    "deepseek-chat": 8192,  # V3, 8K 输出
    "deepseek-reasoner": 65536,  # R1, 64K 输出
    # 百炼托管 DeepSeek
    "deepseek-v3": 8192,  # 早期 V3，保守取 8K
    "deepseek-v3-0324": 8192,
    "deepseek-v3.1": 65536,  # 百炼文档: 与 V3.2 同组
    "deepseek-v3.2-exp": 65536,
    "deepseek-r1": 16384,  # 百炼文档: 最大回复 16,384
    "deepseek-r1-0528": 16384,
    # Tongyi (Qwen) — 数值取自百炼官方模型列表，使用非思考模式最大输出
    "qwen3.7-max": 65536,  # 2026-06 最新旗舰
    "qwen3.7-plus": 65536,
    "qwen3.6-flash": 65536,
    "qwen3-max": 65536,  # 非思考 65,536 / 思考 32,768
    "qwen3-max-2026-01-23": 65536,
    "qwen-max": 8192,  # 稳定版别名 — DashScope API 实际限制 [1, 8192]
    "qwen-max-latest": 8192,
    "qwen3.5-plus": 65536,
    "qwen3.5-plus-2026-02-15": 65536,
    "qwen3.5-omni-plus": 65536,
    "qwen-plus": 32768,  # 稳定版 Qwen-Plus 最大 32,768
    "qwen-plus-latest": 32768,
    "qwen-plus-2025-12-01": 32768,
    "qwen-plus-2025-09-11": 32768,
    "qwen-plus-2025-07-28": 32768,
    "qwen3.5-flash": 65536,
    "qwen3.5-flash-2026-02-23": 65536,
    "qwen-flash": 32768,
    "qwen-flash-2025-07-28": 32768,
    "qwen-turbo": 16384,  # 百炼文档: 最大 16,384
    "qwen-turbo-latest": 16384,
    "qwen-turbo-2025-07-15": 16384,
    "qwen-long": 32768,  # 百炼文档: 最大 32,768
    "qwen-long-latest": 32768,
    "qwen-long-2025-01-25": 32768,
    "qwen3-next-80b-a3b-thinking": 32768,
    "qwen3-next-80b-a3b-instruct": 129024,
    "qwen3-235b-a22b": 16384,  # 开源版 非思考 16,384
    "qwen3-235b-a22b-thinking-2507": 32768,
    "qwen3-235b-a22b-instruct-2507": 129024,
    "qwen3-32b": 16384,
    "qwen3-30b-a3b": 16384,
    "qwen3-30b-a3b-thinking-2507": 32768,
    "qwen3-30b-a3b-instruct-2507": 129024,
    "qwen3-14b": 8192,  # 百炼文档: 最大 8,192
    "qwen3-8b": 8192,
    "qwen3-coder-next": 65536,  # 百炼文档: 与 coder-plus 同级，支持上下文缓存
    "qwen3-coder-plus": 65536,  # 百炼文档: 最大 65,536
    "qwq-32b": 8192,  # 百炼文档: 最大回复 8,192
    "qwq-plus": 8192,  # 百炼文档: 最大回复 8,192
    # OpenAI
    "gpt-5.5": 128000,
    "gpt-5.5-pro": 128000,
    "gpt-5.4": 128000,
    "gpt-5.4-mini": 128000,
    "gpt-5.4-nano": 128000,
    "gpt-4o": 16384,
    "gpt-4o-mini": 16384,
    "gpt-4-turbo": 4096,
    "o1": 100000,
    "o1-mini": 65536,
    "o3-mini": 100000,
    "o3": 100000,
    # Anthropic
    "claude-opus-4-8": 128000,  # 2026-06 最新旗舰 (NextOpus)
    "claude-opus-4-7": 128000,
    "claude-opus-4-6": 128000,
    "claude-sonnet-4-6": 64000,
    "claude-haiku-4-5": 64000,
    "claude-haiku-4-5-20251001": 64000,
    "claude-sonnet-4-5-20250929": 64000,
    "claude-sonnet-4-20250514": 64000,
    "claude-opus-4-20250514": 32000,
    "claude-3-5-haiku-20241022": 8192,
    "claude-3-opus-20240229": 4096,
    # Kimi (Moonshot)
    "kimi-k2.6": 262144,
    "kimi-k2.5": 262144,
    "kimi-k2-thinking": 262144,
    "kimi-k2-thinking-turbo": 262144,
    "kimi-k2-turbo-preview": 262144,
    "kimi-k2-0905-preview": 262144,
    "kimi-k2-0711-preview": 131072,
    "moonshot-v1-8k": 8192,
    "moonshot-v1-32k": 32768,
    "moonshot-v1-128k": 131072,
    "moonshot-v1-8k-vision-preview": 8192,
    "moonshot-v1-32k-vision-preview": 32768,
    "moonshot-v1-128k-vision-preview": 131072,
    # Xiaomi MiMo V2.5 (1M context; UltraSpeed documents 128K output)
    "mimo-v2.5-pro": 131072,
    "mimo-v2.5": 131072,
    "mimo-v2.5-pro-ultraspeed": 131072,
    # MiniMax
    "MiniMax-M3": 524288,  # 服务端限制: max_tokens must be <= 524,288
    "MiniMax-M1-80k": 65536,
    "MiniMax-M1-40k": 16384,
    "MiniMax-M2.5": 65536,
    "MiniMax-M2.5-highspeed": 65536,
    "MiniMax-M2.1": 65536,
    "MiniMax-M2.1-highspeed": 65536,
    "MiniMax-M2": 65536,
    "MiniMax-M2.7": 65536,
    "MiniMax-M2.7-highspeed": 65536,
    # Tencent 混元
    "hunyuan-2.0-thinking": 65536,  # 2026-06 最新，官方文档: 最大输出 64K
    "hunyuan-2.0-instruct": 4096,
    "hunyuan-t1-latest": 65536,  # 官方文档: 最大输出 64K
    "hunyuan-turbos-latest": 4096,
    "hunyuan-pro": 4096,
    "hunyuan-standard": 4096,
    "hunyuan-lite": 4096,
    "hunyuan-a13b": 16384,
    "hunyuan-role-latest": 4096,
    "hunyuan-large-role-latest": 4096,
    "hunyuan-translation": 4096,
    "hunyuan-translation-lite": 4096,
    # GLM (Zhipu AI) — 通过 Coding Plan 或 SiliconFlow 访问
    "glm-5.1": 16384,  # 2026-06 最新
    "glm-5": 16384,  # Coding Plan 推荐模型，保守取 16K
    "glm-4.7": 8192,  # Coding Plan 更多模型
    "glm-4.5V": 8192,
    "glm-4.5-Air": 8192,
    "qwen3.6-plus": 16384,  # Coding Plan 暂无公开输出上限，保守取 16K
    # 火山方舟 Agent Plan 模型
    "doubao-seed-2.0-pro": 32768,
    "doubao-seed-2.0-lite": 32768,
    "doubao-seed-2.0-mini": 32768,
    "doubao-seed-2.0-code": 16384,
    "deepseek-v3.2": 384000,
    "minimax-m2.7": 65536,
    "minimax-m3": 524288,
    # OpenCode Go 专有模型 (核验自 models.dev 2026-07-18)
    # 与其它 provider 重叠的 model_id (deepseek-v4-*/kimi-k2.6/glm-5.1/minimax-m3/
    # qwen3.5-plus/qwen3.6-plus/qwen3.7-max/qwen3.7-plus 等) 复用上方已有条目,
    # 不在此覆盖。OpenCode Go 的 max output 见各模型文档。
    "grok-4.5": 500000,
    "glm-5.2": 131072,
    "kimi-k3": 131072,
    "kimi-k2.7-code": 262144,
    "mimo-v2-omni": 128000,
    "mimo-v2-pro": 128000,
}

DEFAULT_MAX_OUTPUT_TOKENS = 8192
_MODEL_MAX_OUTPUT_TOKENS_BY_LOWER = {
    model_id.lower(): limit for model_id, limit in _MODEL_MAX_OUTPUT_TOKENS.items()
}


_MODEL_ID_ALIASES: dict[str, str] = {
    "deepseek-v4-pro": "deepseek-v4-pro",
    "deepseekv4pro": "deepseek-v4-pro",
    "deepseek-v4pro": "deepseek-v4-pro",
    "deepseek_v4_pro": "deepseek-v4-pro",
    "deepseek-v4_pro": "deepseek-v4-pro",
    "deepseek-v4-flash": "deepseek-v4-flash",
    "deepseekv4flash": "deepseek-v4-flash",
    "deepseek-v4flash": "deepseek-v4-flash",
    "deepseek_v4_flash": "deepseek-v4-flash",
    "deepseek-v4_flash": "deepseek-v4-flash",
    "hunyuan-t1": "hunyuan-t1-latest",
    "hunyuan-turbos": "hunyuan-turbos-latest",
    "hunyuan-large-role": "hunyuan-large-role-latest",
    "hunyuan-2.0-think": "hunyuan-2.0-thinking",
    "hunyuan-2.0-thinking": "hunyuan-2.0-thinking",
    "hunyuan-2.0-instruct": "hunyuan-2.0-instruct",
    "claude-opus-4.8": "claude-opus-4-8",
    "claude-opus-4.7": "claude-opus-4-7",
    "claude-opus-4.6": "claude-opus-4-6",
    "claude-sonnet-4.6": "claude-sonnet-4-6",
    "claude-haiku-4.5": "claude-haiku-4-5",
    "kimi-k2.6": "kimi-k2.6",
    "kimi-k2.5": "kimi-k2.5",
    "kimi-k2-thinking-turbo-preview": "kimi-k2-thinking-turbo",
    "minimax-m3": "MiniMax-M3",
    "minimax-m2.7": "MiniMax-M2.7",
    "minimax-m2.7-highspeed": "MiniMax-M2.7-highspeed",
    "minimax-m2.5": "MiniMax-M2.5",
    "minimax-m2.5-highspeed": "MiniMax-M2.5-highspeed",
    "minimax-m2.1": "MiniMax-M2.1",
    "minimax-m2.1-highspeed": "MiniMax-M2.1-highspeed",
    "minimax-m2": "MiniMax-M2",
}


def canonical_model_id(model_id: str) -> str:
    """Normalize common model aliases while preserving unknown model ids."""
    raw = str(model_id or "").strip()
    lowered = raw.lower()
    if lowered in _MODEL_ID_ALIASES:
        return _MODEL_ID_ALIASES[lowered]
    return raw


def get_model_max_output_tokens(model_id: str) -> int:
    """Return the maximum output tokens for a model, with prefix fallback."""
    canonical_id = canonical_model_id(model_id)
    record = resolve_model_capability_any_provider(canonical_id)
    if record.max_output_tokens is not None:
        return record.max_output_tokens
    limit = _MODEL_MAX_OUTPUT_TOKENS.get(canonical_id)
    if limit is not None:
        return limit
    limit = _MODEL_MAX_OUTPUT_TOKENS_BY_LOWER.get(canonical_id.lower())
    if limit is not None:
        return limit
    # Try prefix matching for versioned model names
    for key, val in _MODEL_MAX_OUTPUT_TOKENS.items():
        if canonical_id.lower().startswith(key.lower()):
            return val
    return DEFAULT_MAX_OUTPUT_TOKENS


# ── Per-model total context window sizes ──────────────────────────────────────
# Used by pre-flight context-window validation to ensure prompts fit before
# sending to the API.  Models not listed fall back to DEFAULT_CONTEXT_WINDOW.

_MODEL_CONTEXT_WINDOW: dict[str, int] = {
    # OpenAI
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-3.5-turbo": 16385,
    "o1": 200000,
    "o1-mini": 128000,
    "o3-mini": 200000,
    "o3": 200000,
    "gpt-5.5": 200000,
    "gpt-5.5-pro": 200000,
    "gpt-5.4": 200000,
    "gpt-5.4-mini": 200000,
    "gpt-5.4-nano": 200000,
    # Anthropic
    "claude-opus-4-8": 200000,
    "claude-opus-4-7": 200000,
    "claude-opus-4-6": 200000,
    "claude-sonnet-4-6": 200000,
    "claude-haiku-4-5": 200000,
    "claude-haiku-4-5-20251001": 200000,
    "claude-sonnet-4-5-20250929": 200000,
    "claude-sonnet-4-20250514": 200000,
    "claude-opus-4-20250514": 200000,
    "claude-3-5-sonnet": 200000,
    "claude-3-5-haiku-20241022": 200000,
    "claude-3-opus": 200000,
    "claude-3-opus-20240229": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    # DeepSeek
    "deepseek-chat": 128000,
    "deepseek-reasoner": 128000,
    "deepseek-v4-flash": 1048576,
    "deepseek-v4-pro": 1048576,
    "deepseek-v3": 128000,
    "deepseek-v3-0324": 128000,
    "deepseek-v3.1": 128000,
    "deepseek-v3.2": 128000,
    "deepseek-v3.2-exp": 128000,
    "deepseek-r1": 128000,
    "deepseek-r1-0528": 128000,
    # Qwen / Tongyi
    "qwen-max": 32768,
    "qwen-max-latest": 32768,
    "qwen3-max": 131072,
    "qwen3-max-2026-01-23": 131072,
    "qwen3.7-max": 131072,
    "qwen3.7-plus": 131072,
    "qwen3.6-flash": 131072,
    "qwen-plus": 131072,
    "qwen-plus-latest": 131072,
    "qwen-plus-2025-12-01": 131072,
    "qwen-plus-2025-09-11": 131072,
    "qwen-plus-2025-07-28": 131072,
    "qwen3.5-plus": 131072,
    "qwen3.5-plus-2026-02-15": 131072,
    "qwen3.5-omni-plus": 131072,
    "qwen-turbo": 131072,
    "qwen-turbo-latest": 131072,
    "qwen-turbo-2025-07-15": 131072,
    "qwen3.5-flash": 131072,
    "qwen3.5-flash-2026-02-23": 131072,
    "qwen-flash": 131072,
    "qwen-flash-2025-07-28": 131072,
    "qwen-long": 1048576,
    "qwen-long-latest": 1048576,
    "qwen-long-2025-01-25": 1048576,
    "qwen3-235b-a22b": 131072,
    "qwen3-235b-a22b-thinking-2507": 131072,
    "qwen3-235b-a22b-instruct-2507": 131072,
    "qwen3-32b": 131072,
    "qwen3-30b-a3b": 131072,
    "qwen3-30b-a3b-thinking-2507": 131072,
    "qwen3-30b-a3b-instruct-2507": 131072,
    "qwen3-14b": 131072,
    "qwen3-8b": 131072,
    "qwen3-next-80b-a3b-thinking": 131072,
    "qwen3-next-80b-a3b-instruct": 131072,
    "qwen3-coder-next": 131072,
    "qwen3-coder-plus": 131072,
    "qwq-32b": 131072,
    "qwq-plus": 131072,
    # Kimi (Moonshot)
    "moonshot-v1-8k": 8192,
    "moonshot-v1-32k": 32768,
    "moonshot-v1-128k": 131072,
    "moonshot-v1-8k-vision-preview": 8192,
    "moonshot-v1-32k-vision-preview": 32768,
    "moonshot-v1-128k-vision-preview": 131072,
    "kimi-k2.6": 131072,
    "kimi-k2.5": 131072,
    "kimi-k2-thinking": 131072,
    "kimi-k2-thinking-turbo": 131072,
    "kimi-k2-turbo-preview": 131072,
    "kimi-k2-0905-preview": 131072,
    "kimi-k2-0711-preview": 131072,
    # MiniMax
    "MiniMax-M3": 1048576,
    "MiniMax-M2.7": 1048576,
    "MiniMax-M2.7-highspeed": 1048576,
    "MiniMax-M2.5": 1048576,
    "MiniMax-M2.5-highspeed": 1048576,
    "MiniMax-M2.1": 1048576,
    "MiniMax-M2.1-highspeed": 1048576,
    "MiniMax-M2": 1048576,
    "minimax-01": 1048576,
    # Tencent Hunyuan
    "hunyuan-turbos-latest": 128000,
    "hunyuan-lite": 256000,
    "hunyuan-2.0-thinking": 256000,
    "hunyuan-2.0-instruct": 256000,
    "hunyuan-t1-latest": 256000,
    "hunyuan-pro": 128000,
    "hunyuan-standard": 128000,
    "hunyuan-a13b": 128000,
    "hunyuan-role-latest": 128000,
    "hunyuan-large-role-latest": 128000,
    "hunyuan-translation": 128000,
    "hunyuan-translation-lite": 256000,
    # GLM (Zhipu AI)
    "glm-5.1": 131072,
    "glm-5": 131072,
    "glm-4.7": 131072,
    "glm-4.5V": 131072,
    "glm-4.5-Air": 131072,
    # Volcengine Ark
    "doubao-seed-2.0-pro": 131072,
    "doubao-seed-2.0-lite": 131072,
    "doubao-seed-2.0-mini": 131072,
    "doubao-seed-2.0-code": 131072,
    # Ollama (local models — conservative default)
    "llama3.2": 8192,
    "llama3.1": 8192,
    "llama3": 8192,
    "llama3.1-8b": 8192,
    "llama3.1-70b": 8192,
    "qwen2.5:7b": 8192,
    "qwen2.5:14b": 8192,
    "qwen2.5:32b": 8192,
    "mistral": 8192,
    "mixtral": 8192,
    "deepseek-coder": 8192,
}

DEFAULT_CONTEXT_WINDOW = 128000
_MODEL_CONTEXT_WINDOW_BY_LOWER: dict[str, int] = {
    model_id.lower(): window for model_id, window in _MODEL_CONTEXT_WINDOW.items()
}


def get_model_context_window(model_id: str) -> int:
    """Return the total context window size for a model, with prefix fallback."""
    canonical_id = canonical_model_id(model_id)
    record = resolve_model_capability_any_provider(canonical_id)
    if record.context_window_tokens is not None:
        return record.context_window_tokens
    window = _MODEL_CONTEXT_WINDOW.get(canonical_id)
    if window is not None:
        return window
    window = _MODEL_CONTEXT_WINDOW_BY_LOWER.get(canonical_id.lower())
    if window is not None:
        return window
    # Try prefix matching for versioned model names
    for key, val in _MODEL_CONTEXT_WINDOW.items():
        if canonical_id.lower().startswith(key.lower()):
            return val
    return DEFAULT_CONTEXT_WINDOW


# Thinking models consume part of max_tokens for reasoning.
# This reserve is a conservative estimate; actual usage varies by task complexity.
_THINKING_TOKEN_RESERVE = 20_000


def check_draft_feasibility(
    model_id: str,
    target_chars: int,
    *,
    thinking: bool = False,
) -> tuple[str, str]:
    """Check whether a target word count is feasible for the given model.

    Args:
        model_id: The model that will be used for generation.
        target_chars: Target output length in Chinese characters (≈ tokens).
        thinking: Whether the model will run in thinking/reasoning mode.
            Thinking tokens consume part of the max_tokens budget.

    Returns:
        ``(level, message)`` where *level* is one of:
        - ``'ok'``    — target is well within capacity, no warning needed.
        - ``'warn'``  — target is close to the limit; output may be truncated.
        - ``'error'`` — target almost certainly exceeds effective capacity.
        *message* is an empty string when level is ``'ok'``.
    """
    model_limit = get_model_max_output_tokens(model_id)
    if thinking:
        effective_output = max(model_limit - _THINKING_TOKEN_RESERVE, 1024)
        thinking_note = (
            f"\n（思考模型会额外消耗约 {_THINKING_TOKEN_RESERVE:,} token 用于内部推理，"
            "实际可用输出空间已相应缩减。）"
        )
    else:
        effective_output = model_limit
        thinking_note = ""

    # Only a target length is available here, so use the shared conservative
    # character-only estimator; real tokenizers require the actual output text.
    estimated_tokens = estimate_text_length_tokens(target_chars)
    effective_chars = int(effective_output / 1.5)
    ratio = estimated_tokens / effective_output

    if ratio <= 0.80:
        return "ok", ""

    if ratio <= 1.0:
        pct = int(ratio * 100)
        return "warn", (
            f"目标字数 {target_chars:,} 字约需 {estimated_tokens:,} token，"
            f"已达模型有效输出容量的 {pct}%。\n"
            f"模型：{model_id}（有效输出上限约 {effective_chars:,} 字）{thinking_note}\n\n"
            "生成可能无法完整收尾，建议适当缩减目标字数，或换用输出上限更高的模型。"
        )

    return "error", (
        f"目标字数 {target_chars:,} 字约需 {estimated_tokens:,} token，"
        f"已超出模型有效输出容量（约 {effective_chars:,} 字）。\n"
        f"模型：{model_id}{thinking_note}\n\n"
        "正文极可能被截断，强烈建议降低目标字数，或切换至输出上限更高的模型后再生成。"
    )


_ENV_KEY_MAP: dict[str, str] = {
    "openai": "NOVEL_FORGE_OPENAI_API_KEY",
    "anthropic": "NOVEL_FORGE_ANTHROPIC_API_KEY",
    "deepseek": "NOVEL_FORGE_DEEPSEEK_API_KEY",
    "tongyi": "NOVEL_FORGE_TONGYI_API_KEY",
    "tongyi_coding": "NOVEL_FORGE_TONGYI_CODING_API_KEY",
    "tongyi_token_plan": "NOVEL_FORGE_TONGYI_TOKEN_PLAN_API_KEY",
    "kimi": "NOVEL_FORGE_KIMI_API_KEY",
    "mimo": "NOVEL_FORGE_MIMO_API_KEY",
    "tencent": "NOVEL_FORGE_TENCENT_API_KEY",
    "minimax": "NOVEL_FORGE_MINIMAX_API_KEY",
    "siliconflow": "NOVEL_FORGE_SILICONFLOW_API_KEY",
    "volcengine_ark": "NOVEL_FORGE_VOLCENGINE_ARK_API_KEY",
    "opencode": "NOVEL_FORGE_OPENCODE_API_KEY",
    # Ollama doesn't require API key
}

_PROVIDER_SETTING_ATTRS: dict[str, str] = {
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "deepseek": "deepseek_api_key",
    "tongyi": "tongyi_api_key",
    "tongyi_coding": "tongyi_coding_api_key",
    "tongyi_token_plan": "tongyi_token_plan_api_key",
    "kimi": "kimi_api_key",
    "mimo": "mimo_api_key",
    "tencent": "tencent_api_key",
    "minimax": "minimax_api_key",
    "siliconflow": "siliconflow_api_key",
    "volcengine_ark": "volcengine_ark_api_key",
    "opencode": "opencode_api_key",
    # Ollama doesn't require API key
}

_NO_KEY_PROVIDERS: frozenset[str] = frozenset({"ollama"})

_PLACEHOLDER_KEYS = {"sk-...", "sk-ant-...", ""}

_PROFILE_API_KEY_PREFIX = "NOVEL_FORGE_PROFILE_API_KEY"


def profile_api_key_env_var(profile_id: str) -> str:
    """Return a stable per-profile API-key env var name for dynamic providers."""
    raw = str(profile_id or "").strip()
    if not raw:
        raw = "profile"
    slug = re.sub(r"[^A-Z0-9]+", "_", raw.upper()).strip("_") or "PROFILE"
    slug = slug[:72].strip("_") or "PROFILE"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10].upper()
    return f"{_PROFILE_API_KEY_PREFIX}_{slug}_{digest}"


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class ModelProfile:
    """A user-defined AI model configuration."""

    profile_id: str  # unique key, e.g. "tongyi:qwen-max"
    display_name: str  # human label, e.g. "通义千问 Max"
    provider: str  # "tongyi", "deepseek", etc.
    model_id: str  # "qwen-max", "deepseek-reasoner"
    api_key: str = ""  # hydrated from env/settings; not persisted to JSON
    base_url: str = ""  # optional custom endpoint

    @property
    def is_key_configured(self) -> bool:
        if self.provider in _NO_KEY_PROVIDERS:
            return True
        return bool(self.api_key) and self.api_key not in _PLACEHOLDER_KEYS

    @property
    def masked_key(self) -> str:
        if not self.api_key or self.api_key in _PLACEHOLDER_KEYS:
            return "(未配置)"
        if len(self.api_key) <= 8:
            return "****"
        return f"{self.api_key[:3]}***{self.api_key[-4:]}"


@dataclass
class TaskRouteEntry:
    """Per-task routing configuration."""

    profile_id: str  # references ModelProfile.profile_id
    thinking: bool = False
    thinking_mode: str = ""
    multi_turn: bool = False

    def __post_init__(self) -> None:
        self.thinking_mode = normalize_thinking_mode(self.thinking_mode, thinking=self.thinking)
        self.thinking = thinking_mode_enabled(self.thinking_mode)


# ── Config container ──────────────────────────────────────────────────────────


@dataclass
class ProfilesConfig:
    """Complete model configuration: profiles + task routing."""

    profiles: list[ModelProfile] = field(default_factory=list)
    routes: dict[str, TaskRouteEntry] = field(default_factory=dict)
    fallback_routes: dict[str, list[TaskRouteEntry]] = field(default_factory=dict)
    default_profile_id: str = ""
    group_bulk_routes: dict[str, dict[str, object]] = field(default_factory=dict)

    def _normalize_default_profile_id(self) -> None:
        """Keep the default profile stable and always pointing to an existing profile."""
        if not self.profiles:
            self.default_profile_id = ""
            return
        if self.get_profile(self.default_profile_id) is not None:
            return
        self.default_profile_id = self.profiles[0].profile_id

    # ── Profile CRUD ──────────────────────────────────

    def get_profile(self, profile_id: str) -> ModelProfile | None:
        return next((p for p in self.profiles if p.profile_id == profile_id), None)

    def add_profile(self, profile: ModelProfile) -> None:
        self.profiles = [p for p in self.profiles if p.profile_id != profile.profile_id]
        self.profiles.append(profile)
        self._normalize_default_profile_id()

    def remove_profile(self, profile_id: str) -> None:
        self.profiles = [p for p in self.profiles if p.profile_id != profile_id]
        self.routes = {k: v for k, v in self.routes.items() if v.profile_id != profile_id}
        cleaned_fallbacks: dict[str, list[TaskRouteEntry]] = {}
        for task_key, entries in self.fallback_routes.items():
            kept = [entry for entry in entries if entry.profile_id != profile_id]
            if kept:
                cleaned_fallbacks[task_key] = kept
        self.fallback_routes = cleaned_fallbacks
        self._normalize_default_profile_id()

    def managed_api_key_env_vars(self) -> set[str]:
        """Return the set of env var names that hold API keys for current profiles.

        Used by the desktop save layer to identify orphaned .env keys that should
        be stripped when a profile is deleted — preventing _merge_env_discovered_profiles
        from resurrecting deleted profiles on next load.
        """
        keys: set[str] = set()
        for p in self.profiles:
            if not p.is_key_configured:
                continue
            env_key = _ENV_KEY_MAP.get(p.provider)
            if env_key:
                keys.add(env_key)
            elif p.provider not in _NO_KEY_PROVIDERS:
                keys.add(profile_api_key_env_var(p.profile_id))
        return keys

    def ensure_fallback_coverage(self) -> None:
        """Expand saved bulk routes and give every primary route a fallback chain."""

        self._apply_group_bulk_route_defaults()
        default_chain = self._default_fallback_chain()
        if not default_chain:
            return

        for task_key, route in list(self.routes.items()):
            if self.fallback_routes.get(task_key):
                continue
            fallback = self._filter_fallback_entries(default_chain, primary=route)
            if fallback:
                self.fallback_routes[task_key] = fallback

    def _apply_group_bulk_route_defaults(self) -> None:
        if not self.group_bulk_routes:
            return

        try:
            from novel_forge.core.task_catalog import ROUTING_GROUPS
        except Exception:
            return

        for group in ROUTING_GROUPS:
            for subgroup in group.subgroups:
                group_key = f"{group.name} / {subgroup.title}"
                raw = self.group_bulk_routes.get(group_key)
                route = self._task_route_entry_from_mapping(raw)
                fallback_entries = self._fallback_entries_from_group_mapping(raw)
                if route is None and not fallback_entries:
                    continue
                for task_key in self._subgroup_task_keys(group, subgroup):
                    if route is not None:
                        self.routes.setdefault(task_key, self._clone_entry(route))
                    if task_key not in self.fallback_routes and fallback_entries:
                        primary = self.routes.get(task_key)
                        fallback = self._filter_fallback_entries(
                            fallback_entries,
                            primary=primary,
                        )
                        if fallback:
                            self.fallback_routes[task_key] = fallback

    def _default_fallback_chain(self) -> list[TaskRouteEntry]:
        counts: dict[tuple[tuple[str, str, bool], ...], int] = {}
        chains: dict[tuple[tuple[str, str, bool], ...], list[TaskRouteEntry]] = {}
        for entries in self.fallback_routes.values():
            fallback = self._filter_fallback_entries(entries)
            if not fallback:
                continue
            key = tuple(
                (entry.profile_id, entry.thinking_mode, entry.multi_turn) for entry in fallback
            )
            counts[key] = counts.get(key, 0) + 1
            chains.setdefault(key, fallback)
        if not counts:
            return []
        best_key = max(counts, key=lambda item: (counts[item], len(item)))
        return [self._clone_entry(entry) for entry in chains[best_key]]

    def _filter_fallback_entries(
        self,
        entries: list[TaskRouteEntry],
        *,
        primary: TaskRouteEntry | None = None,
    ) -> list[TaskRouteEntry]:
        result: list[TaskRouteEntry] = []
        seen: set[str] = set()
        primary_profile_id = primary.profile_id if primary is not None else ""
        for entry in entries:
            if not entry.profile_id or entry.profile_id == primary_profile_id:
                continue
            if entry.profile_id in seen:
                continue
            profile = self.get_profile(entry.profile_id)
            if profile is None:
                continue
            # Never route generation tasks through embedding-only models:
            # they cannot produce text and would burn a fallback attempt on a
            # provider-level 4xx (e.g. OpenAI-compat "Unsupported model").
            if is_embedding_model(profile.provider, profile.model_id):
                continue
            seen.add(entry.profile_id)
            result.append(self._clone_entry(entry))
            if len(result) >= 3:
                break
        return result

    @staticmethod
    def _clone_entry(entry: TaskRouteEntry) -> TaskRouteEntry:
        return TaskRouteEntry(
            profile_id=entry.profile_id,
            thinking=entry.thinking,
            thinking_mode=entry.thinking_mode,
            multi_turn=entry.multi_turn,
        )

    @staticmethod
    def _task_route_entry_from_mapping(raw: object) -> TaskRouteEntry | None:
        if not isinstance(raw, dict):
            return None
        profile_id = str(raw.get("profile_id", "") or "").strip()
        if not profile_id:
            return None
        return TaskRouteEntry(
            profile_id=profile_id,
            thinking=bool(raw.get("thinking", False)),
            thinking_mode=str(raw.get("thinking_mode", "") or ""),
            multi_turn=bool(raw.get("multi_turn", False)),
        )

    @classmethod
    def _fallback_entries_from_group_mapping(cls, raw: object) -> list[TaskRouteEntry]:
        if not isinstance(raw, dict):
            return []
        entries: list[TaskRouteEntry] = []
        for item in raw.get("fallback_routes", []) or []:
            entry = cls._task_route_entry_from_mapping(item)
            if entry is not None:
                entries.append(entry)
        return entries[:3]

    @staticmethod
    def _subgroup_task_keys(group: Any, subgroup: Any) -> list[str]:
        # The same range is rendered in both desktop clients and expanded when
        # loading persisted group-bulk routes.  Do not let those paths drift.
        from novel_forge.core.task_catalog import routing_subgroup_task_keys

        return list(routing_subgroup_task_keys(group, subgroup))

    def profile_choices(self) -> list[tuple[str, str]]:
        """(profile_id, display_name) pairs for dropdowns."""
        return [(p.profile_id, p.display_name) for p in self.profiles]

    # ── Persistence ───────────────────────────────────

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "profiles": [
                {
                    "profile_id": p.profile_id,
                    "display_name": p.display_name,
                    "provider": p.provider,
                    "model_id": p.model_id,
                    "base_url": p.base_url,
                }
                for p in self.profiles
            ],
            "routes": {k: asdict(v) for k, v in self.routes.items()},
            "fallback_routes": {
                k: [asdict(entry) for entry in entries]
                for k, entries in self.fallback_routes.items()
            },
            "default_profile_id": self.default_profile_id,
            "group_bulk_routes": self.group_bulk_routes,
        }
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        settings: Any | None = None,
        fallback_api_keys: dict[str, str] | None = None,
    ) -> ProfilesConfig:
        if not path.is_file():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning(
                "model_profiles_load_failed | path=%s | line=%s | col=%s | error=%s",
                path,
                exc.lineno,
                exc.colno,
                exc.msg,
            )
            return cls()
        except OSError as exc:
            logger.warning("model_profiles_read_failed | path=%s | error=%s", path, exc)
            return cls()
        profiles: list[ModelProfile] = []
        for raw in data.get("profiles", []):
            if not isinstance(raw, dict):
                logger.warning("model_profile_invalid_entry | path=%s | entry=%r", path, raw)
                continue
            profile_id = str(raw.get("profile_id", ""))
            provider = str(raw.get("provider", "")).strip().lower()
            legacy_api_key = str(raw.get("api_key", "") or "").strip()
            fallback_api_key = str(
                (fallback_api_keys or {}).get(profile_id)
                or (fallback_api_keys or {}).get(profile_api_key_env_var(profile_id))
                or (fallback_api_keys or {}).get(provider, "")
                or ""
            ).strip()
            resolved_api_key = (
                _resolve_api_key(provider, settings, profile_id=profile_id)
                or fallback_api_key
                or legacy_api_key
            )
            if legacy_api_key and legacy_api_key not in _PLACEHOLDER_KEYS:
                logger.warning(
                    "legacy_model_profile_api_key_detected | path=%s | profile_id=%s | action=migrate_to_env",
                    path,
                    raw.get("profile_id", ""),
                )
            try:
                profiles.append(
                    ModelProfile(
                        profile_id=profile_id,
                        display_name=str(raw.get("display_name", "")),
                        provider=provider,
                        model_id=str(raw.get("model_id", "")),
                        api_key=resolved_api_key,
                        base_url=str(raw.get("base_url", "")),
                    )
                )
            except TypeError as exc:
                logger.warning(
                    "model_profile_invalid_fields | path=%s | error=%s | entry=%r", path, exc, raw
                )
        routes: dict[str, TaskRouteEntry] = {}
        for task_key, raw in data.get("routes", {}).items():
            if not isinstance(raw, dict):
                logger.warning(
                    "model_profile_route_invalid_entry | path=%s | task=%s | entry=%r",
                    path,
                    task_key,
                    raw,
                )
                continue
            try:
                routes[str(task_key)] = TaskRouteEntry(**raw)
            except TypeError as exc:
                logger.warning(
                    "model_profile_route_invalid_fields | path=%s | task=%s | error=%s | entry=%r",
                    path,
                    task_key,
                    exc,
                    raw,
                )
        fallback_routes: dict[str, list[TaskRouteEntry]] = {}
        raw_fallback_routes = data.get("fallback_routes", {})
        if isinstance(raw_fallback_routes, dict):
            for task_key, raw_entries in raw_fallback_routes.items():
                if not isinstance(raw_entries, list):
                    logger.warning(
                        "model_profile_fallback_route_invalid_entry | path=%s | task=%s | entry=%r",
                        path,
                        task_key,
                        raw_entries,
                    )
                    continue
                parsed_entries: list[TaskRouteEntry] = []
                for raw_entry in raw_entries:
                    if not isinstance(raw_entry, dict):
                        logger.warning(
                            "model_profile_fallback_route_invalid_item | path=%s | task=%s | entry=%r",
                            path,
                            task_key,
                            raw_entry,
                        )
                        continue
                    try:
                        parsed_entries.append(TaskRouteEntry(**raw_entry))
                    except TypeError as exc:
                        logger.warning(
                            "model_profile_fallback_route_invalid_fields | path=%s | task=%s | error=%s | entry=%r",
                            path,
                            task_key,
                            exc,
                            raw_entry,
                        )
                if parsed_entries:
                    fallback_routes[str(task_key)] = parsed_entries[:3]

        config = cls(
            profiles=profiles,
            routes=routes,
            fallback_routes=fallback_routes,
            default_profile_id=data.get("default_profile_id", ""),
            group_bulk_routes=data.get("group_bulk_routes", {}),
        )
        config._normalize_default_profile_id()
        config.ensure_fallback_coverage()
        return config

    # ── Import from existing .env ─────────────────────

    @classmethod
    def from_env(cls, settings: Any) -> ProfilesConfig:
        """Import model profiles from existing .env / Settings object."""
        config = cls()
        routing_json = getattr(settings, "task_routing", "") or ""
        fallback_routing_json = getattr(settings, "task_fallback_routing", "") or ""
        seen: dict[str, tuple[str, str, str]] = {}  # profile_id → (provider, model, key)

        def _parse_route_spec(spec: str) -> tuple[str, str, bool, bool]:
            segments = [s.strip() for s in spec.split(",") if s.strip()]
            head = segments[0] if segments else ""
            pm = [p.strip() for p in head.split(":")]
            provider = pm[0].lower() if pm else ""
            model_id = pm[1] if len(pm) > 1 else ""
            raw_features: list[str] = []
            if len(pm) > 2:
                raw_features.extend(pm[2:])
            raw_features.extend(segments[1:])
            features = {f.strip().lower().replace("-", "_") for f in raw_features}
            thinking = "thinking" in features
            multi_turn = any(k in features for k in ("multi_turn", "multiturn", "multi"))
            return provider, model_id, thinking, multi_turn

        if routing_json:
            try:
                routing = json.loads(routing_json)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "task_routing_import_failed | line=%s | col=%s | error=%s",
                    exc.lineno,
                    exc.colno,
                    exc.msg,
                )
                routing = {}
            for task_key, spec in routing.items():
                if not isinstance(spec, str):
                    continue
                provider, model_id, thinking, multi_turn = _parse_route_spec(spec)
                if not provider:
                    continue
                attr = _PROVIDER_SETTING_ATTRS.get(provider, "")
                api_key = getattr(settings, attr, "") if attr else ""
                profile_id = f"{provider}:{model_id}" if model_id else provider
                seen[profile_id] = (provider, model_id, api_key)

                config.routes[task_key] = TaskRouteEntry(
                    profile_id=profile_id,
                    thinking=thinking,
                    multi_turn=multi_turn,
                )

        if fallback_routing_json:
            try:
                fallback_routing = json.loads(fallback_routing_json)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "task_fallback_routing_import_failed | line=%s | col=%s | error=%s",
                    exc.lineno,
                    exc.colno,
                    exc.msg,
                )
                fallback_routing = {}
            if isinstance(fallback_routing, dict):
                for task_key, specs in fallback_routing.items():
                    if not isinstance(specs, list):
                        continue
                    parsed_entries: list[TaskRouteEntry] = []
                    for item in specs:
                        if isinstance(item, str):
                            provider, model_id, thinking, multi_turn = _parse_route_spec(item)
                        elif isinstance(item, dict):
                            provider = str(item.get("provider", "") or "").strip().lower()
                            model_id = str(
                                item.get("model_id", item.get("model", "")) or ""
                            ).strip()
                            thinking = bool(item.get("thinking", False))
                            multi_turn = bool(
                                item.get(
                                    "multi_turn",
                                    item.get("multi", item.get("multiTurn", False)),
                                )
                            )
                        else:
                            continue
                        if not provider:
                            continue
                        attr = _PROVIDER_SETTING_ATTRS.get(provider, "")
                        api_key = getattr(settings, attr, "") if attr else ""
                        profile_id = f"{provider}:{model_id}" if model_id else provider
                        seen[profile_id] = (provider, model_id, api_key)
                        parsed_entries.append(
                            TaskRouteEntry(
                                profile_id=profile_id,
                                thinking=thinking,
                                multi_turn=multi_turn,
                            )
                        )
                    if parsed_entries:
                        config.fallback_routes[str(task_key)] = parsed_entries[:3]

        # Add providers with API keys that aren't already in routing
        for provider, attr in _PROVIDER_SETTING_ATTRS.items():
            api_key = getattr(settings, attr, "") or ""
            if api_key and api_key not in _PLACEHOLDER_KEYS:
                has_profile = any(pid == provider or pid.startswith(f"{provider}:") for pid in seen)
                if not has_profile:
                    models = KNOWN_PROVIDERS.get(provider, {}).get("models", [])
                    model_id = models[0] if models else ""
                    profile_id = f"{provider}:{model_id}" if model_id else provider
                    seen[profile_id] = (provider, model_id, api_key)

        # Build ModelProfile objects
        for profile_id, (provider, model_id, api_key) in seen.items():
            label = KNOWN_PROVIDERS.get(provider, {}).get("label", provider)
            display_name = f"{label} {model_id}" if model_id else label
            config.profiles.append(
                ModelProfile(
                    profile_id=profile_id,
                    display_name=display_name,
                    provider=provider,
                    model_id=model_id,
                    api_key=api_key,
                )
            )

        # Set default profile
        default_prov = (getattr(settings, "default_provider", "") or "").strip()
        if default_prov:
            for p in config.profiles:
                if p.provider == default_prov:
                    config.default_profile_id = p.profile_id
                    break

        config._normalize_default_profile_id()
        config.ensure_fallback_coverage()
        return config

    # ── Import from UnifiedSettingsLoader ─────────────

    @classmethod
    def from_unified_loader(cls, loader: Any | None = None) -> ProfilesConfig:
        """Import model profiles from a :class:`UnifiedSettingsLoader`.

        Uses ``load("task_routing")`` and ``load("task_fallback_routing")``
        so that environment variables and ``novel_forge.cli.json`` defaults
        participate in profile construction with the same priority as the
        loader.

        # TODO: migrate to UnifiedSettingsLoader — this is a parallel path to
        :meth:`from_env`; once ``get_settings`` fully delegates to the loader,
        this method can become the primary import source.
        """
        from novel_forge.core.infra.settings_loader import UnifiedSettingsLoader

        if loader is None:
            loader = UnifiedSettingsLoader()

        config = cls()
        # task_routing and task_fallback_routing are guaranteed to exist in the
        # loader's built-in defaults (empty dicts), so load() won't raise.
        routing: dict[str, str] = loader.load("task_routing")
        fallback_routing: dict[str, list[str]] = loader.load("task_fallback_routing")
        default_provider: str = loader.load("default_provider")

        seen: dict[str, tuple[str, str, str]] = {}  # profile_id → (provider, model, key)

        def _parse_route_spec(spec: str) -> tuple[str, str, bool, bool]:
            segments = [s.strip() for s in spec.split(",") if s.strip()]
            head = segments[0] if segments else ""
            pm = [p.strip() for p in head.split(":")]
            provider = pm[0].lower() if pm else ""
            model_id = pm[1] if len(pm) > 1 else ""
            raw_features: list[str] = []
            if len(pm) > 2:
                raw_features.extend(pm[2:])
            raw_features.extend(segments[1:])
            features = {f.strip().lower().replace("-", "_") for f in raw_features}
            thinking = "thinking" in features
            multi_turn = any(k in features for k in ("multi_turn", "multiturn", "multi"))
            return provider, model_id, thinking, multi_turn

        for task_key, spec in routing.items():
            if not isinstance(spec, str):
                continue
            provider, model_id, thinking, multi_turn = _parse_route_spec(spec)
            if not provider:
                continue
            profile_id = f"{provider}:{model_id}" if model_id else provider
            api_key = _resolve_api_key(provider)
            seen[profile_id] = (provider, model_id, api_key)
            config.routes[task_key] = TaskRouteEntry(
                profile_id=profile_id,
                thinking=thinking,
                multi_turn=multi_turn,
            )

        for task_key, specs in fallback_routing.items():
            if not isinstance(specs, list):
                continue
            parsed_entries: list[TaskRouteEntry] = []
            for item in specs:
                if not isinstance(item, str):
                    continue
                provider, model_id, thinking, multi_turn = _parse_route_spec(item)
                if not provider:
                    continue
                profile_id = f"{provider}:{model_id}" if model_id else provider
                api_key = _resolve_api_key(provider)
                seen[profile_id] = (provider, model_id, api_key)
                parsed_entries.append(
                    TaskRouteEntry(
                        profile_id=profile_id,
                        thinking=thinking,
                        multi_turn=multi_turn,
                    )
                )
            if parsed_entries:
                config.fallback_routes[str(task_key)] = parsed_entries[:3]

        # Add providers with API keys that aren't already in routing
        for provider, _attr in _PROVIDER_SETTING_ATTRS.items():
            api_key = _resolve_api_key(provider)
            if api_key and api_key not in _PLACEHOLDER_KEYS:
                has_profile = any(pid == provider or pid.startswith(f"{provider}:") for pid in seen)
                if not has_profile:
                    models = KNOWN_PROVIDERS.get(provider, {}).get("models", [])
                    model_id = models[0] if models else ""
                    profile_id = f"{provider}:{model_id}" if model_id else provider
                    seen[profile_id] = (provider, model_id, api_key)

        # Build ModelProfile objects
        for profile_id, (provider, model_id, api_key) in seen.items():
            label = KNOWN_PROVIDERS.get(provider, {}).get("label", provider)
            display_name = f"{label} {model_id}" if model_id else label
            config.profiles.append(
                ModelProfile(
                    profile_id=profile_id,
                    display_name=display_name,
                    provider=provider,
                    model_id=model_id,
                    api_key=api_key,
                )
            )

        if default_provider:
            for p in config.profiles:
                if p.provider == default_provider:
                    config.default_profile_id = p.profile_id
                    break

        config._normalize_default_profile_id()
        config.ensure_fallback_coverage()
        return config

    # ── Export to .env ────────────────────────────────

    def to_env_pairs(self) -> dict[str, str]:
        """Generate .env key-value pairs for API keys and default provider only.

        Routing is persisted exclusively in ``model_profiles.json``.
        Writing ``NOVEL_FORGE_TASK_ROUTING`` / ``NOVEL_FORGE_TASK_FALLBACK_ROUTING``
        to ``.env`` was deprecated — the JSON file is the single source of truth
        for task routing once it exists.
        """
        pairs: dict[str, str] = {}

        # Collect API keys per provider (first non-empty key wins)
        provider_keys: dict[str, str] = {}
        for p in self.profiles:
            if p.is_key_configured and p.provider not in provider_keys:
                provider_keys[p.provider] = p.api_key

        for provider, key in provider_keys.items():
            env_key = _ENV_KEY_MAP.get(provider)
            if env_key:
                pairs[env_key] = key
        for profile in self.profiles:
            if (
                profile.is_key_configured
                and profile.provider not in _ENV_KEY_MAP
                and profile.provider not in _NO_KEY_PROVIDERS
            ):
                pairs[profile_api_key_env_var(profile.profile_id)] = profile.api_key

        # Default provider
        profile = self.get_profile(self.default_profile_id)
        pairs["NOVEL_FORGE_DEFAULT_PROVIDER"] = profile.provider if profile else ""

        return pairs


# ── Module-level helpers ──────────────────────────────────────────────────────


def get_profiles_path() -> Path:
    """Return the path to model_profiles.json in workspace root."""
    return get_runtime_config_dir() / "model_profiles.json"


def _read_env_pairs(env_path: Path) -> dict[str, str]:
    if not env_path.is_file():
        return {}
    try:
        original = env_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("env_file_read_failed | path=%s | error=%s", env_path, exc)
        return {}

    pairs: dict[str, str] = {}
    for line in original.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        pairs[key.strip()] = value.strip()
    return pairs


def _merge_env_pairs(env_path: Path, new_map: dict[str, str]) -> None:
    """Merge new values into .env while preserving comments and unmanaged keys."""
    original = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    result_lines: list[str] = []
    written_keys: set[str] = set()

    for orig_line in original.splitlines():
        stripped = orig_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            result_lines.append(orig_line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in new_map:
            result_lines.append(f"{key}={new_map[key]}")
            written_keys.add(key)
        else:
            result_lines.append(orig_line)

    for key, value in new_map.items():
        if key not in written_keys:
            result_lines.append(f"{key}={value}")

    atomic_write_text(env_path, "\n".join(result_lines) + "\n")


def _read_provider_api_keys_from_env(env_path: Path) -> dict[str, str]:
    env_pairs = _read_env_pairs(env_path)
    provider_keys: dict[str, str] = {}
    for provider, env_key in _ENV_KEY_MAP.items():
        value = str(env_pairs.get(env_key, "") or "").strip()
        if value and value not in _PLACEHOLDER_KEYS:
            provider_keys[provider] = value
    for env_key, value in env_pairs.items():
        if not env_key.startswith(f"{_PROFILE_API_KEY_PREFIX}_"):
            continue
        clean_value = str(value or "").strip()
        if clean_value and clean_value not in _PLACEHOLDER_KEYS:
            provider_keys[env_key] = clean_value
    return provider_keys


def migrate_legacy_profile_api_keys(
    path: Path,
    *,
    settings: Any | None = None,
    env_path: Path | None = None,
) -> dict[str, str]:
    """Move legacy profile API keys into .env and scrub them from JSON."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning(
            "model_profiles_load_failed | path=%s | line=%s | col=%s | error=%s",
            path,
            exc.lineno,
            exc.colno,
            exc.msg,
        )
        return {}
    except OSError as exc:
        logger.warning("model_profiles_read_failed | path=%s | error=%s", path, exc)
        return {}

    raw_profiles = data.get("profiles", [])
    if not isinstance(raw_profiles, list):
        return {}

    env_pairs = _read_env_pairs(env_path) if env_path is not None else {}
    pending_env_pairs: dict[str, str] = {}
    provider_keys: dict[str, str] = {}
    changed = False

    for raw in raw_profiles:
        if not isinstance(raw, dict) or "api_key" not in raw:
            continue
        provider = str(raw.get("provider", "") or "").strip().lower()
        profile_id = str(raw.get("profile_id", "") or "")
        legacy_api_key = str(raw.get("api_key", "") or "").strip()
        if not legacy_api_key or legacy_api_key in _PLACEHOLDER_KEYS:
            raw.pop("api_key", None)
            changed = True
            continue

        env_key = _ENV_KEY_MAP.get(provider) or profile_api_key_env_var(profile_id)
        existing_api_key = (
            _resolve_api_key(provider, settings, profile_id=profile_id)
            or str(env_pairs.get(env_key, "") or "").strip()
        )
        pending_api_key = str(pending_env_pairs.get(env_key, "") or "").strip()
        provider_key = provider if provider in _ENV_KEY_MAP else profile_id

        if existing_api_key:
            if existing_api_key != legacy_api_key:
                logger.warning(
                    "legacy_model_profile_api_key_conflict | path=%s | profile_id=%s | action=prefer_existing_key",
                    path,
                    profile_id,
                )
            else:
                logger.warning(
                    "legacy_model_profile_api_key_detected | path=%s | profile_id=%s | action=strip_from_json",
                    path,
                    profile_id,
                )
            provider_keys[provider_key] = existing_api_key
            raw.pop("api_key", None)
            changed = True
            continue

        if pending_api_key:
            if pending_api_key != legacy_api_key:
                logger.warning(
                    "legacy_model_profile_api_key_conflict | path=%s | profile_id=%s | action=prefer_first_detected_key",
                    path,
                    profile_id,
                )
            provider_keys[provider_key] = pending_api_key
            raw.pop("api_key", None)
            changed = True
            continue

        if env_path is None:
            logger.warning(
                "legacy_model_profile_api_key_retained | path=%s | profile_id=%s | reason=no_env_target",
                path,
                profile_id,
            )
            provider_keys[provider_key] = legacy_api_key
            continue

        logger.warning(
            "legacy_model_profile_api_key_detected | path=%s | profile_id=%s | action=migrate_to_env",
            path,
            profile_id,
        )
        pending_env_pairs[env_key] = legacy_api_key
        provider_keys[provider_key] = legacy_api_key
        raw.pop("api_key", None)
        changed = True

    if pending_env_pairs and env_path is not None:
        try:
            _merge_env_pairs(env_path, pending_env_pairs)
        except OSError as exc:
            logger.warning("env_file_write_failed | path=%s | error=%s", env_path, exc)
            return provider_keys

    if changed:
        try:
            atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
        except OSError as exc:
            logger.warning("model_profiles_write_failed | path=%s | error=%s", path, exc)

    return provider_keys


def _resolve_api_key(
    provider: str,
    settings: Any | None = None,
    *,
    profile_id: str = "",
) -> str:
    provider_name = str(provider or "").strip().lower()
    profile_key_name = ""
    profile_env_name = ""
    if profile_id:
        profile_env_name = profile_api_key_env_var(profile_id)
        profile_key_name = profile_env_name.removeprefix("NOVEL_FORGE_").lower()

    # 1. Try OS keyring first (if available)
    from novel_forge.gateway.secure_keys import get_key as _keyring_get

    if profile_key_name:
        kr_value = _keyring_get(profile_key_name)
        if kr_value and kr_value not in _PLACEHOLDER_KEYS:
            return kr_value

    keyring_key_name = f"{provider_name}_api_key"
    kr_value = _keyring_get(keyring_key_name)
    if kr_value and kr_value not in _PLACEHOLDER_KEYS:
        return kr_value

    # 2. Settings (.env / environment variable via pydantic-settings)
    attr = _PROVIDER_SETTING_ATTRS.get(provider_name, "")
    if settings is not None and attr:
        value = str(getattr(settings, attr, "") or "").strip()
        if value and value not in _PLACEHOLDER_KEYS:
            return value

    # 3. Direct os.getenv fallback
    env_key = _ENV_KEY_MAP.get(provider_name, "")
    if env_key:
        value = str(os.getenv(env_key, "") or "").strip()
        if value and value not in _PLACEHOLDER_KEYS:
            return value
    if profile_env_name:
        value = str(os.getenv(profile_env_name, "") or "").strip()
        if value and value not in _PLACEHOLDER_KEYS:
            return value
    return ""


def _merge_fallback_routes_from_settings(
    config: ProfilesConfig,
    settings: Any | None,
) -> None:
    """Merge legacy settings fallback routes into missing profile config entries.

    ``model_profiles.json`` is the desktop source of truth once it exists.  The
    settings value can come from an older ``.env`` and must not overwrite a
    route order that the UI already saved in the profile file.
    """
    if settings is None:
        return
    raw = str(getattr(settings, "task_fallback_routing", "") or "").strip()
    if not raw:
        return
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(mapping, dict):
        return

    profile_index: dict[str, str] = {}
    for profile in config.profiles:
        key = f"{profile.provider}:{profile.model_id}"
        profile_index[key] = profile.profile_id

    def _parse_spec(spec: str) -> tuple[str, str, bool, bool]:
        segments = [s.strip() for s in spec.split(",") if s.strip()]
        head = segments[0] if segments else ""
        parts = [p.strip() for p in head.split(":")]
        provider = parts[0].lower() if parts else ""
        model_id = parts[1] if len(parts) > 1 else ""
        raw_features: list[str] = []
        if len(parts) > 2:
            raw_features.extend(parts[2:])
        raw_features.extend(segments[1:])
        features = {f.strip().lower().replace("-", "_") for f in raw_features}
        thinking = "thinking" in features
        multi_turn = any(k in features for k in ("multi_turn", "multiturn", "multi"))
        return provider, model_id, thinking, multi_turn

    for task_key, entries in mapping.items():
        if not isinstance(entries, list):
            continue
        normalized_task_key = str(task_key)
        if normalized_task_key in config.fallback_routes:
            continue
        parsed: list[TaskRouteEntry] = []
        for entry in entries[:3]:
            if isinstance(entry, str):
                provider, model_id, thinking, multi_turn = _parse_spec(entry)
            elif isinstance(entry, dict):
                provider = str(entry.get("provider", "") or "").strip().lower()
                model_id = str(entry.get("model_id", entry.get("model", "")) or "").strip()
                thinking = bool(entry.get("thinking", False))
                multi_turn = bool(
                    entry.get("multi_turn", entry.get("multi", entry.get("multiTurn", False)))
                )
            else:
                continue
            if not provider:
                continue
            route_key = f"{provider}:{model_id}" if model_id else provider
            profile_id = profile_index.get(route_key, route_key)
            if config.get_profile(profile_id) is None:
                continue
            parsed.append(
                TaskRouteEntry(
                    profile_id=profile_id,
                    thinking=thinking,
                    multi_turn=multi_turn,
                )
            )
        if parsed:
            config.fallback_routes[normalized_task_key] = parsed[:3]


def _merge_env_discovered_profiles(
    config: ProfilesConfig,
    settings: Any | None,
    fallback_api_keys: dict[str, str],
) -> None:
    """Auto-discover providers with valid API keys missing from loaded profiles.

    After loading ``model_profiles.json``, this function scans the environment
    (via *settings* and *fallback_api_keys*) for providers that have a configured
    API key but no corresponding entry in ``config.profiles``.  For each such
    provider it appends a new :class:`ModelProfile` using the first known model
    from :data:`KNOWN_PROVIDERS`.

    This makes the system self-healing: if the JSON file is accidentally
    overwritten with a subset of profiles, or if the user adds a new API key to
    ``.env``, the missing providers will reappear on next load without requiring
    manual intervention.

    Note: This only activates when the JSON already contains at least one profile.
    An empty profiles list in JSON is treated as an explicit signal to use
    env-based routing (handled by the caller's fallback path).
    """
    # Only merge when JSON already has profiles (user is managing via JSON).
    # Empty profiles = explicit "use env routing" signal; don't interfere.
    if not config.profiles:
        return

    # Collect providers already represented in the loaded config
    existing_providers: set[str] = set()
    for p in config.profiles:
        existing_providers.add(p.provider)

    # Scan standard providers for valid API keys not yet in profiles
    for provider, attr in _PROVIDER_SETTING_ATTRS.items():
        if provider in existing_providers:
            continue
        # Resolve API key from settings or fallback dict
        api_key = ""
        if settings is not None:
            api_key = str(getattr(settings, attr, "") or "").strip()
        if not api_key or api_key in _PLACEHOLDER_KEYS:
            api_key = str(fallback_api_keys.get(provider, "") or "").strip()
        if not api_key or api_key in _PLACEHOLDER_KEYS:
            continue
        # Provider has a valid key but no profile — auto-create one
        models = KNOWN_PROVIDERS.get(provider, {}).get("models", [])
        model_id = models[0] if models else ""
        profile_id = f"{provider}:{model_id}" if model_id else provider
        # Double-check: skip if this exact profile_id was somehow already loaded
        if config.get_profile(profile_id) is not None:
            continue
        label = KNOWN_PROVIDERS.get(provider, {}).get("label", provider)
        display_name = f"{label} {model_id}" if model_id else label
        config.profiles.append(
            ModelProfile(
                profile_id=profile_id,
                display_name=display_name,
                provider=provider,
                model_id=model_id,
                api_key=api_key,
            )
        )
        existing_providers.add(provider)
        logger.info(
            "env_discovered_profile_added | provider=%s | profile_id=%s",
            provider,
            profile_id,
        )


# Module-level cache for load_or_import_profiles to avoid repeated disk I/O
_PROFILES_CACHE: dict[str, tuple[float, ProfilesConfig]] = {}  # path_str -> (mtime, config)


def invalidate_profiles_cache() -> None:
    """Clear the module-level profiles cache.

    Call after saving config changes (e.g. profile deletion) so that the next
    ``load_or_import_profiles()`` call re-reads from disk instead of returning
    a stale cached config that still contains deleted profiles.
    """
    _PROFILES_CACHE.clear()


def load_or_import_profiles(settings: Any | None = None) -> ProfilesConfig:
    """Load profiles from JSON, or import from .env if JSON doesn't exist.

    Uses a simple mtime-based cache to avoid repeated disk I/O when the
    profiles file hasn't changed since the last load.
    """
    path = get_profiles_path()
    env_path = get_writable_env_path()
    path_str = str(path)

    # Check cache: if file exists and mtime matches, return cached config
    if path.is_file():
        try:
            current_mtime = path.stat().st_mtime
        except OSError:
            current_mtime = None
        if current_mtime is not None and path_str in _PROFILES_CACHE:
            cached_mtime, cached_config = _PROFILES_CACHE[path_str]
            if cached_mtime == current_mtime:
                return cached_config

    if path.is_file():
        fallback_api_keys = _read_provider_api_keys_from_env(env_path)
        fallback_api_keys.update(
            migrate_legacy_profile_api_keys(path, settings=settings, env_path=env_path)
        )
        config = ProfilesConfig.load(
            path,
            settings=settings,
            fallback_api_keys=fallback_api_keys,
        )
        _merge_fallback_routes_from_settings(config, settings)
        # Auto-discover providers with valid API keys that are missing from JSON.
        # This ensures that adding a new API key to .env automatically surfaces
        # the provider in the UI without requiring manual re-addition or file deletion.
        _merge_env_discovered_profiles(config, settings, fallback_api_keys)
        # Warn if .env still carries routing that is now ignored
        if settings is not None and config.routes:
            env_routing = str(getattr(settings, "task_routing", "") or "").strip()
            if env_routing and env_routing not in ("{}", ""):
                logger.warning(
                    "env_routing_ignored | reason=model_profiles.json takes priority | "
                    "action=remove NOVEL_FORGE_TASK_ROUTING from .env to silence"
                )
        # Update cache
        try:
            _PROFILES_CACHE[path_str] = (path.stat().st_mtime, config)
        except OSError:
            pass
        return config
    if settings is not None:
        config = ProfilesConfig.from_env(settings)
        if config.profiles:
            config.save(path)
        return config
    # TODO: migrate to UnifiedSettingsLoader — when neither JSON nor settings
    # are available, try importing directly from the unified loader.
    config = ProfilesConfig.from_unified_loader()
    if config.profiles:
        config.save(path)
    return config
