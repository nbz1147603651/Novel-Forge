"""嵌入模型配置 — 统一的嵌入模型检测和管理。

职责：
- 定义所有已知的嵌入模型列表
- 提供嵌入模型检测函数
- 提供嵌入模型元数据查询

使用方式：
    from novel_forge.gateway.embedding_config import is_embedding_model

    if is_embedding_model("ollama", "nomic-embed-text"):
        print("这是一个嵌入模型")
"""

from __future__ import annotations

# ── 已知嵌入模型列表 ────────────────────────────────────────────────────────────
# 格式: { "提供商": { "模型ID", ... } }
# 保持小写，与 provider.lower() 和 model_id.lower() 比较

KNOWN_EMBEDDING_MODELS: dict[str, set[str]] = {
    "ollama": {
        "nomic-embed-text",
        "mxbai-embed-large",
        "all-minilm",
        "bge-m3",
        "qwen3-embedding:0.6b",
    },
    "openai": {
        "text-embedding-3-small",
        "text-embedding-3-large",
        "text-embedding-ada-002",
    },
    "tongyi": {
        "text-embedding-v4",
        "text-embedding-v3",
        "text-embedding-v2",
    },
    "tencent": {
        "hunyuan-embedding",
    },
    "volcengine_ark": {
        "doubao-embedding-vision",
        "doubao-embedding-large",
    },
    "siliconflow": {
        "bge-m3",
    },
}

# ── 启发式匹配关键字 ──────────────────────────────────────────────────────────
# 用于检测未知但符合命名规范的嵌入模型
_EMBEDDING_KEYWORDS: tuple[str, ...] = (
    "embedding",
    "embed-text",
    "text-embedding",
    "embed-english",
    "embed-multilingual",
)


# ── 检测函数 ──────────────────────────────────────────────────────────────────

def is_embedding_model(provider: str, model_id: str) -> bool:
    """检测模型是否为嵌入模型。

    检测策略：
    1. 精确匹配已知嵌入模型列表
    2. 启发式匹配（包含嵌入相关关键字）

    Args:
        provider: 提供商名称 (e.g., "ollama", "openai", "tencent")
        model_id: 模型标识符 (e.g., "nomic-embed-text")

    Returns:
        True 如果是嵌入模型，否则 False

    Examples:
        >>> is_embedding_model("ollama", "nomic-embed-text")
        True
        >>> is_embedding_model("openai", "gpt-4")
        False
        >>> is_embedding_model("custom", "my-embedding-model")
        True  # 启发式匹配
    """
    prov = provider.strip().lower()
    mid = model_id.strip()
    mid_lower = mid.lower()

    # 1. 精确匹配已知模型
    if prov in KNOWN_EMBEDDING_MODELS:
        known = KNOWN_EMBEDDING_MODELS[prov]
        if mid_lower in known:
            return True

    # 2. 腾讯云特殊处理：模型名包含 "embed" 也视为嵌入模型
    if prov == "tencent":
        if "embed" in mid_lower:
            return True

    # 3. 启发式匹配：模型名包含嵌入关键字
    if any(keyword in mid_lower for keyword in _EMBEDDING_KEYWORDS):
        return True

    return False


def get_supported_providers() -> list[str]:
    """获取所有支持嵌入模型的提供商列表。

    Returns:
        提供商名称列表
    """
    return list(KNOWN_EMBEDDING_MODELS.keys())


def get_models_by_provider(provider: str) -> set[str]:
    """获取指定提供商的已知嵌入模型列表。

    Args:
        provider: 提供商名称

    Returns:
        模型ID集合，如果提供商不支持则返回空集合
    """
    prov = provider.strip().lower()
    return KNOWN_EMBEDDING_MODELS.get(prov, set()).copy()


def get_all_known_models() -> dict[str, set[str]]:
    """获取所有已知嵌入模型的完整映射。

    Returns:
        {提供商: {模型ID集合}} 的字典副本
    """
    return {
        provider: models.copy()
        for provider, models in KNOWN_EMBEDDING_MODELS.items()
    }
