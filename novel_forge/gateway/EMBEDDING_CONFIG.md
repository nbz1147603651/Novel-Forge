# 嵌入服务配置指南

## 概述

嵌入服务（Embedding Service）用于将文本转换为向量表示，支持语义搜索、相似度匹配等功能。在写作助手中主要用于记忆模块的语义检索。

## 支持的提供商

### 1. OpenAI

**推荐模型**: `text-embedding-3-small`
- 维度：1536（可降至 512/256 以节省空间）
- 价格：$0.00002 / 1K tokens
- 性能：优秀，支持多语言

**配置示例**:
```json
{
    "embedding": {
        "provider": "openai",
        "api_key": "sk-...",
        "model": "text-embedding-3-small",
        "dimensions": 512
    }
}
```

### 2. OpenAI 兼容 API

支持任何 OpenAI 兼容的 API 端点，如：
- Azure OpenAI
- 本地部署的模型（Ollama、vLLM 等）
- 第三方兼容服务

**配置示例**:
```json
{
    "embedding": {
        "provider": "custom",
        "api_key": "your-api-key",
        "model": "text-embedding-3-small",
        "base_url": "https://your-api.com/v1",
        "dimensions": 512
    }
}
```

### 3. Ollama（本地模型）⭐ 推荐

支持通过 Ollama 运行的本地嵌入模型：
- `nomic-embed-text` ⭐ 推荐（768 维，效果好）
- `mxbai-embed-large`
- `all-minilm`

**配置示例**:
```json
{
    "embedding": {
        "provider": "ollama",
        "model": "nomic-embed-text",
        "base_url": "http://localhost:11434/v1"
    }
}
```

**安装 Ollama**:
```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# 拉取模型
ollama pull nomic-embed-text
ollama pull llama3.2  # 用于文本生成

# 启动服务
ollama serve
```

**Ollama 优势**:
- ✅ 完全免费（本地运行）
- ✅ 隐私保护（数据不出本地）
- ✅ 低延迟（无网络请求）
- ✅ 支持多种模型
- ✅ OpenAI 兼容 API
- ✅ 可用于创作生成和嵌入

**推荐模型组合**:
| 用途 | 模型 | 维度 | 大小 |
|------|------|------|------|
| 嵌入检索 | nomic-embed-text | 768 | ~280MB |
| 快速生成 | llama3.2 | - | ~2GB |
| 高质量生成 | llama3.1:8b | - | ~4.7GB |
| 中文优化 | qwen2.5:7b | - | ~4GB |

## 完整配置示例

### 环境变量方式

在 `.env` 文件中添加：

```bash
# OpenAI 嵌入服务配置
EMBEDDING_PROVIDER=openai
EMBEDDING_API_KEY=sk-your-api-key-here
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=512

# 或使用自定义 API
# EMBEDDING_PROVIDER=custom
# EMBEDDING_BASE_URL=https://your-api.com/v1
```

### 配置文件方式

在 `novel_forge.cli.json` 或项目配置文件中：

```json
{
    "memory": {
        "episodic": {
            "enabled": true,
            "embedding_config": {
                "provider": "openai",
                "api_key": "sk-your-api-key-here",
                "model": "text-embedding-3-small",
                "base_url": null,
                "dimensions": 512
            },
            "use_mock_embeddings": false
        }
    }
}
```

## 程序化使用

### 基本使用

```python
from novel_forge.gateway.embedding import EmbeddingService

# 创建服务实例
service = EmbeddingService(
    provider="openai",
    api_key="sk-your-api-key",
    model="text-embedding-3-small",
    dimensions=512
)

# 生成单个嵌入
result = await service.generate_embedding("你好，世界！")
print(f"向量维度：{len(result.embedding)}")
print(f"消耗 tokens: {result.input_tokens}")
print(f"成本：${result.cost_usd}")

# 批量生成
texts = ["文本 1", "文本 2", "文本 3"]
results = await service.generate_batch(texts)
for r in results:
    print(f"{r.model}: {len(r.embedding)}维")
```

### 在 Episodic Memory 中使用

```python
from novel_forge.memory.episodic import EpisodicMemory

# 配置嵌入服务
embedding_config = {
    "provider": "openai",
    "api_key": "sk-your-api-key",
    "model": "text-embedding-3-small",
    "dimensions": 512
}

# 创建记忆实例（使用真实嵌入）
memory = EpisodicMemory(
    embedding_config=embedding_config,
    use_mock_embeddings=False
)

# 创建记忆实例（使用 mock 嵌入，用于测试）
memory_mock = EpisodicMemory(use_mock_embeddings=True)
```

## 模型选择建议

### 推荐配置

| 使用场景 | 推荐模型 | 维度 | 说明 |
|---------|---------|------|------|
| 生产环境 | OpenAI text-embedding-3-small | 512 | 性价比高，效果好 |
| 高质量需求 | OpenAI text-embedding-3-large | 1024 | 更精确，价格更高 |
| 本地部署 | Ollama nomic-embed-text | 768 | 免费，隐私好 |
| 测试开发 | Mock Embedding | 1536 | 无需 API key |

### 维度选择

较低维度可以：
- 减少存储空间
- 加快检索速度
- 可能略微降低精度

建议：
- 512 维：平衡选择，推荐默认使用
- 256 维：资源受限场景
- 1536 维：需要最高精度

## 成本估算

以 OpenAI text-embedding-3-small 为例：

- 价格：$0.00002 / 1K tokens
- 中文约 500 字 = 1K tokens

**示例**：
- 100 章 × 每章 5 个事件 = 500 次嵌入
- 每次约 100 tokens = 50K tokens
- 总成本：50 × $0.00002 = $0.001

## 故障排除

### 问题：Embedding API 调用失败

**可能原因**：
1. API key 无效或过期
2. 网络连接问题
3. 模型名称错误

**解决方法**：
```python
# 检查服务状态
service = EmbeddingService(...)
healthy = await service.health_check()
print(f"服务健康：{healthy}")
```

### 问题：向量维度不匹配

确保所有嵌入使用相同的维度设置：
```python
# 初始化时指定维度
service = EmbeddingService(dimensions=512)

# 或在配置文件中设置
{
    "dimensions": 512
}
```

### 问题：Mock Embedding 效果不佳

Mock Embedding 使用 hash 生成伪向量，仅用于测试。生产环境请使用真实 API：

```python
# 使用真实嵌入
memory = EpisodicMemory(
    embedding_config={...},
    use_mock_embeddings=False  # 设为 False
)
```

## 性能优化

### 批量处理

```python
# 批量生成比单个生成更高效
texts = [f"文本{i}" for i in range(100)]
results = await service.generate_batch(texts)
```

### 缓存嵌入结果

```python
import hashlib
import json

class EmbeddingCache:
    def __init__(self):
        self._cache = {}
    
    def _hash(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()
    
    async def get_or_generate(self, text: str, service):
        key = self._hash(text)
        if key not in self._cache:
            result = await service.generate_embedding(text)
            self._cache[key] = result.embedding
        return self._cache[key]
```

## 安全建议

1. **不要硬编码 API Key**
   - 使用环境变量
   - 使用密钥管理服务

2. **限制 API Key 权限**
   - 创建仅用于嵌入的 Key
   - 设置使用限额

3. **监控使用情况**
   - 定期检查 token 消耗
   - 设置预算告警