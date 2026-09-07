"""Model pricing table and cost estimation for LLM API calls."""

from __future__ import annotations

import logging
import threading
from datetime import date

_log = logging.getLogger(__name__)

# Exchange rate: 1 USD = 7.2 CNY (as of 2026-07)
_USD_TO_CNY = 7.2

# Pricing: (input_cost_per_million_tokens, output_cost_per_million_tokens)
# All prices in USD per million tokens
# Updated: 2026-07
_PRICING_TABLE: dict[str, tuple[float, float]] = {
    # ─── OpenAI (USD) ─────────────────────────────────────────────
    "gpt-5.5": (5.00, 30.00),
    "gpt-5.5-pro": (30.00, 180.00),
    "gpt-5.4": (2.50, 15.00),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4": (30.00, 60.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
    "o3-mini": (1.10, 4.40),
    "o3": (2.00, 8.00),

    # ─── Anthropic (USD) ──────────────────────────────────────────
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-opus-4-20250514": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5-20250929": (3.00, 15.00),
    "claude-sonnet-4-20250514": (3.00, 15.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-sonnet-20240229": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-haiku-20240307": (0.25, 1.25),
    "claude-3-opus-20240229": (15.00, 75.00),

    # ─── DeepSeek (CNY → USD, official price non-peak) ────────────
    "deepseek-v4-pro": (0.42, 0.83),          # ¥3/¥6
    "deepseek-v4-flash": (0.14, 0.28),        # ¥1/¥2
    "deepseek-chat": (0.14, 0.28),            # → V4-Flash (deprecated 2026-07-24)
    "deepseek-reasoner": (0.14, 0.28),        # → V4-Flash thinking (deprecated)

    # ─── Tongyi / Qwen (CNY → USD) ────────────────────────────────
    # Qwen3.7 series (2026-06 latest flagship)
    "qwen3.7-max": (1.65, 4.95),
    "qwen3.7-plus": (0.40, 1.60),             # ≤256K
    # Qwen3.6 series
    "qwen3.6-flash": (0.20, 1.00),
    "qwen3.6-plus": (0.50, 3.00),             # ≤256K
    # Qwen3.5 series
    "qwen3.5-plus": (0.40, 2.40),             # ≤256K
    "qwen3.5-plus-2026-02-15": (0.40, 2.40),
    "qwen3.5-flash": (0.10, 0.60),
    "qwen3.5-flash-2026-02-23": (0.10, 0.60),
    "qwen3.5-omni-plus": (0.40, 2.40),
    # Qwen Max / Plus / Flash / Turbo (with version aliases)
    "qwen-max": (1.60, 6.40),
    "qwen-max-latest": (1.60, 6.40),
    "qwen3-max": (1.20, 6.00),                # ≤32K tier
    "qwen3-max-2026-01-23": (1.20, 6.00),
    "qwen-plus": (0.40, 1.20),                # ≤256K
    "qwen-plus-latest": (0.40, 1.20),
    "qwen-plus-2025-12-01": (0.40, 1.20),
    "qwen-plus-2025-09-11": (0.40, 1.20),
    "qwen-plus-2025-07-28": (0.40, 1.20),
    "qwen-flash": (0.05, 0.40),
    "qwen-flash-2025-07-28": (0.05, 0.40),
    "qwen-turbo": (0.05, 0.20),
    "qwen-turbo-latest": (0.05, 0.20),
    "qwen-turbo-2025-07-15": (0.05, 0.20),
    "qwen-long": (0.08, 0.20),
    "qwen-long-latest": (0.08, 0.20),
    "qwen-long-2025-01-25": (0.08, 0.20),
    # QwQ reasoning series
    "qwq-32b": (0.80, 3.20),
    "qwq-plus": (0.80, 3.20),
    # Qwen3 Coder
    "qwen3-coder-plus": (3.50, 7.00),
    "qwen3-coder-next": (3.50, 7.00),
    # Qwen3 open-weight models (hosted on Tongyi API)
    "qwen3-235b-a22b": (0.42, 1.67),
    "qwen3-235b-a22b-thinking-2507": (0.42, 1.67),
    "qwen3-235b-a22b-instruct-2507": (0.42, 1.67),
    "qwen3-30b-a3b": (0.21, 0.83),
    "qwen3-30b-a3b-thinking-2507": (0.21, 0.83),
    "qwen3-30b-a3b-instruct-2507": (0.21, 0.83),
    "qwen3-next-80b-a3b-thinking": (0.42, 1.67),
    "qwen3-next-80b-a3b-instruct": (0.42, 1.67),
    "qwen3-32b": (0.42, 1.67),
    "qwen3-14b": (0.21, 0.83),
    "qwen3-8b": (0.14, 0.56),
    # Tongyi-hosted DeepSeek models
    "deepseek-v3.2": (0.28, 1.11),
    "deepseek-v3.2-exp": (0.28, 1.11),
    "deepseek-v3.1": (0.28, 1.11),
    "deepseek-v3": (0.28, 1.11),
    "deepseek-v3-0324": (0.28, 1.11),
    "deepseek-r1": (0.56, 2.22),
    "deepseek-r1-0528": (0.56, 2.22),
    # Tongyi embedding models (very low cost)
    "text-embedding-v4": (0.07, 0.07),
    "text-embedding-v3": (0.07, 0.07),
    "text-embedding-v2": (0.07, 0.07),

    # ─── Kimi / Moonshot (CNY → USD) ──────────────────────────────
    "kimi-k2.6": (0.90, 3.75),                # ¥6.5/¥27
    "kimi-k2.5": (0.56, 2.92),                # ¥4/¥21
    "kimi-k2-thinking": (0.56, 2.92),
    "kimi-k2-thinking-turbo": (0.56, 2.92),
    "kimi-k2-turbo-preview": (0.56, 2.92),
    "kimi-k2-0905-preview": (0.56, 2.92),
    "kimi-k2-0711-preview": (0.56, 2.92),
    "moonshot-v1-8k": (0.85, 0.85),
    "moonshot-v1-32k": (1.70, 1.70),
    "moonshot-v1-128k": (4.25, 4.25),
    "moonshot-v1-8k-vision-preview": (0.85, 0.85),
    "moonshot-v1-32k-vision-preview": (1.70, 1.70),
    "moonshot-v1-128k-vision-preview": (4.25, 4.25),

    # ─── Tencent Hunyuan (CNY → USD) ──────────────────────────────
    "hunyuan-2.0-thinking": (5.30, 21.20),
    "hunyuan-2.0-instruct": (4.51, 11.13),
    "hunyuan-2.0-thinking-20251109": (5.30, 21.20),
    "hunyuan-2.0-instruct-20251111": (4.51, 11.13),
    "hunyuan-t1-latest": (0.80, 2.00),
    "hunyuan-turbos-latest": (1.00, 4.00),
    "hunyuan-pro": (0.80, 3.20),
    "hunyuan-standard": (0.30, 1.20),
    "hunyuan-lite": (0.06, 0.24),
    "hunyuan-a13b": (0.80, 3.20),
    "hunyuan-role-latest": (0.60, 2.40),
    "hunyuan-large-role-latest": (0.60, 2.40),
    "hunyuan-translation": (0.20, 0.80),
    "hunyuan-translation-lite": (0.20, 0.80),
    "hunyuan-embedding": (0.01, 0.01),

    # ─── MiniMax (CNY → USD) ──────────────────────────────────────
    "minimax-m3": (0.60, 2.40),               # >512K: double
    "minimax-m2.7": (0.30, 1.20),
    "minimax-m2.7-highspeed": (0.60, 2.40),   # 2x highspeed tier
    "minimax-m2.5": (0.30, 1.20),
    "minimax-m2.5-highspeed": (0.60, 2.40),
    "minimax-m2.1": (0.30, 1.20),
    "minimax-m2.1-highspeed": (0.60, 2.40),
    "minimax-m2": (0.30, 1.20),

    # ─── SiliconFlow (CNY → USD) ──────────────────────────────────
    "deepseek-ai/DeepSeek-V4-Pro": (0.42, 0.83),
    "deepseek-ai/DeepSeek-V4-Flash": (0.14, 0.28),
    "deepseek-ai/DeepSeek-V3.2": (0.28, 1.11),
    "deepseek-ai/DeepSeek-V3.1-Terminus": (0.28, 1.11),
    "deepseek-ai/DeepSeek-V3": (0.28, 1.11),
    "deepseek-ai/DeepSeek-R1": (0.56, 2.22),
    "Pro/deepseek-ai/DeepSeek-V3.2": (0.42, 1.67),
    "Pro/deepseek-ai/DeepSeek-V3.1-Terminus": (0.42, 1.67),
    "Pro/deepseek-ai/DeepSeek-V3": (0.42, 1.67),
    "Pro/deepseek-ai/DeepSeek-R1": (0.83, 3.33),
    "moonshotai/Kimi-K2.6": (0.90, 3.75),
    "moonshotai/Kimi-K2.5": (0.56, 2.92),
    "Qwen/Qwen3.6-35B-A3B": (0.25, 1.49),
    "Qwen/Qwen3.6-27B": (0.41, 2.48),
    "Qwen/Qwen3.5-397B-A17B": (1.39, 5.56),
    "Qwen/Qwen3.5-122B-A10B": (0.56, 2.22),
    "Qwen/Qwen3.5-35B-A3B": (0.28, 1.11),
    "Qwen/Qwen3.5-27B": (0.28, 1.11),
    "Qwen/Qwen3.5-9B": (0.14, 0.56),
    "Qwen/Qwen3.5-4B": (0.07, 0.28),
    "Qwen/Qwen3-235B-A22B": (0.42, 1.67),
    "Qwen/Qwen3-32B": (0.42, 1.67),
    "Qwen/Qwen3-8B": (0.14, 0.56),
    "Qwen/Qwen2.5-72B-Instruct": (0.42, 1.67),
    "zai-org/GLM-5.1": (0.56, 2.22),
    "zai-org/GLM-5": (0.56, 2.22),
    "zai-org/GLM-4.7": (0.42, 1.67),
    "zai-org/GLM-4.5V": (0.28, 1.11),
    "zai-org/GLM-4.5-Air": (0.28, 1.11),
    "Pro/zai-org/GLM-5": (0.56, 2.22),
    "Pro/zai-org/GLM-4.7": (0.42, 1.67),
    "tencent/Hunyuan-A13B-Instruct": (0.21, 0.83),
    "MiniMaxAI/MiniMax-M2.5": (0.30, 1.20),
    "BAAI/bge-m3": (0.01, 0.01),              # embedding model
    "BAAI/bge-reranker-v2-m3": (0.01, 0.01),  # reranker model

    # ─── Volcengine Ark / Doubao (CNY → USD) ──────────────────────
    "doubao-seed-2.1-pro": (0.83, 4.17),      # ¥6/¥30
    "doubao-seed-2.1-turbo": (0.42, 2.08),    # ¥3/¥15
    "doubao-seed-2.0-pro": (1.67, 6.67),      # legacy pricing
    "doubao-seed-2.0-lite": (0.42, 1.67),
    "doubao-seed-2.0-mini": (0.14, 0.56),
    "doubao-seed-2.0-code": (0.42, 1.67),
    "glm-5.1": (0.56, 2.22),                  # via Volcengine
    "glm-5": (0.56, 2.22),                     # via tongyi_coding
    "glm-4.7": (0.42, 1.67),                   # via tongyi_coding

    # ─── OpenCode Go (USD, 核验自 models.dev 2026-07-18) ───────────
    # OpenCode Go 是聚合订阅, 定价与各上游官方一致 (单位: USD/1M tokens)。
    # 与其它 provider 重叠的 model_id (deepseek-v4-*/kimi-k2.5/kimi-k2.6/
    # minimax-m2.5/minimax-m3/glm-5/glm-5.1/qwen3.5-plus/qwen3.6-plus/
    # qwen3.7-max/qwen3.7-plus 等) 复用上方已有条目, 不在此覆盖;
    # 此处只收录 OpenCode Go 专有或未收录的模型。
    "grok-4.5": (2.00, 6.00),
    "glm-5.2": (1.40, 4.40),
    "kimi-k3": (3.00, 15.00),
    "kimi-k2.7-code": (0.95, 4.00),
    "mimo-v2-omni": (0.40, 2.00),
    "mimo-v2-pro": (1.00, 3.00),
    "mimo-v2.5-pro": (0.43, 0.87),
    "mimo-v2.5": (0.14, 0.28),

    # ─── Ollama (local, free) ─────────────────────────────────────
    # All local models have $0 API cost
    "llama3.2": (0.0, 0.0),
    "llama3.1": (0.0, 0.0),
    "llama3.1-8b": (0.0, 0.0),
    "llama3.1-70b": (0.0, 0.0),
    "llama3": (0.0, 0.0),
    "qwen2.5:7b": (0.0, 0.0),
    "qwen2.5:14b": (0.0, 0.0),
    "qwen2.5:32b": (0.0, 0.0),
    "mistral": (0.0, 0.0),
    "mixtral": (0.0, 0.0),
    "deepseek-coder": (0.0, 0.0),
    "nomic-embed-text": (0.0, 0.0),
    "mxbai-embed-large": (0.0, 0.0),
    "all-minilm": (0.0, 0.0),
}

# Pre-sorted pricing keys for prefix matching (cached at module level).
# Sorted by length descending to match longest prefix first.
_SORTED_PRICING_KEYS: tuple[str, ...] = tuple(
    sorted(_PRICING_TABLE.keys(), key=len, reverse=True)
)


def estimate_cost(
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Estimate cost in USD based on model pricing table.

    Returns 0.0 for unknown models.
    """
    pricing = _PRICING_TABLE.get(model_id)
    if pricing is None:
        # Try prefix matching for versioned model names
        # Uses pre-sorted keys cached at module level for O(n) scan without sort overhead
        for key in _SORTED_PRICING_KEYS:
            if model_id.startswith(key):
                pricing = _PRICING_TABLE[key]
                break
    if pricing is None:
        return 0.0
    input_rate, output_rate = pricing
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000


def estimate_cost_cny(
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Estimate cost in CNY based on model pricing table.

    Returns 0.0 for unknown models.
    """
    usd = estimate_cost(model_id, prompt_tokens, completion_tokens)
    return usd * _USD_TO_CNY


def get_known_models() -> list[str]:
    """Return list of all models with known pricing."""
    return sorted(_PRICING_TABLE.keys())


def get_pricing(model_id: str) -> tuple[float, float] | None:
    """Get (input_rate, output_rate) in USD per million tokens for a model.
    
    Returns None if model pricing is unknown.
    """
    pricing = _PRICING_TABLE.get(model_id)
    if pricing is None:
        # Try prefix matching using pre-sorted keys
        for key in _SORTED_PRICING_KEYS:
            if model_id.startswith(key):
                return _PRICING_TABLE[key]
    return pricing


class SpendingTracker:
    """Thread-safe in-process spending tracker with daily/monthly budget enforcement.

    .. warning::
        This tracker is **GLOBAL** across all projects. All concurrent projects
        share the same budget pool. One project's spending affects every other
        project's remaining budget headroom. Per-project budget isolation is not
        supported by this class.
    """

    def __init__(
        self,
        *,
        daily_limit: float = 0.0,  #: Daily budget limit in USD (GLOBAL across all projects)
        monthly_limit: float = 0.0,  #: Monthly budget limit in USD (GLOBAL across all projects)
        warn_threshold: float = 0.8,
    ) -> None:
        self._daily_limit = daily_limit
        self._monthly_limit = monthly_limit
        self._warn_threshold = warn_threshold
        self._lock = threading.Lock()
        self._daily_total: float = 0.0
        self._monthly_total: float = 0.0
        self._current_day: date = date.today()
        self._current_month: tuple[int, int] = (self._current_day.year, self._current_day.month)
        self._warned_daily = False
        self._warned_monthly = False

    def _maybe_reset(self) -> None:
        """Reset counters on day/month rollover (must hold lock)."""
        today = date.today()
        if today != self._current_day:
            self._daily_total = 0.0
            self._current_day = today
            self._warned_daily = False
        month_key = (today.year, today.month)
        if month_key != self._current_month:
            self._monthly_total = 0.0
            self._current_month = month_key
            self._warned_monthly = False

    def check_budget(self) -> None:
        """Raise BudgetExceededError if current spending exceeds a configured limit."""
        from novel_forge.core.exceptions import BudgetExceededError

        with self._lock:
            self._maybe_reset()
            if self._daily_limit > 0 and self._daily_total >= self._daily_limit:
                raise BudgetExceededError("daily", self._daily_total, self._daily_limit)
            if self._monthly_limit > 0 and self._monthly_total >= self._monthly_limit:
                raise BudgetExceededError("monthly", self._monthly_total, self._monthly_limit)

    def record(self, cost_usd: float) -> None:
        """Record spending and emit warnings when approaching limits."""
        if cost_usd <= 0:
            return
        with self._lock:
            self._maybe_reset()
            self._daily_total += cost_usd
            self._monthly_total += cost_usd

            if self._daily_limit > 0:
                ratio = self._daily_total / self._daily_limit
                if ratio >= 1.0:
                    _log.warning(
                        "budget_daily_exceeded | spent=%.4f | limit=%.2f",
                        self._daily_total,
                        self._daily_limit,
                    )
                elif ratio >= self._warn_threshold and not self._warned_daily:
                    _log.warning(
                        "budget_daily_warning | spent=%.4f | limit=%.2f | pct=%.0f%%",
                        self._daily_total,
                        self._daily_limit,
                        ratio * 100,
                    )
                    self._warned_daily = True

            if self._monthly_limit > 0:
                ratio = self._monthly_total / self._monthly_limit
                if ratio >= 1.0:
                    _log.warning(
                        "budget_monthly_exceeded | spent=%.4f | limit=%.2f",
                        self._monthly_total,
                        self._monthly_limit,
                    )
                elif ratio >= self._warn_threshold and not self._warned_monthly:
                    _log.warning(
                        "budget_monthly_warning | spent=%.4f | limit=%.2f | pct=%.0f%%",
                        self._monthly_total,
                        self._monthly_limit,
                        ratio * 100,
                    )
                    self._warned_monthly = True

    @property
    def daily_spent(self) -> float:
        with self._lock:
            self._maybe_reset()
            return self._daily_total

    @property
    def monthly_spent(self) -> float:
        with self._lock:
            self._maybe_reset()
            return self._monthly_total
