# 压缩类模板 (Compression)

上下文压缩模板，用于压缩和管理长上下文的记忆。

## 模板列表

| 文件 | 用途 | 任务类型 |
|------|------|---------|
| `context_compress.j2` | 上下文压缩 | `CONTEXT_COMPRESS` |
| `adaptive_compress.j2` | 自适应压缩 | `ADAPTIVE_COMPRESS` |
| `verify_compression.j2` | 压缩验证 | `VERIFY_COMPRESSION` |
| `plot_guard_judge.j2` | 情节守卫判断 | `PLOT_GUARD_JUDGE` |
| `guard_constraint_check.j2` | 护栏约束履行检查 | `GUARD_CONSTRAINT_CHECK` |

## 模板说明

### context_compress.j2
压缩长上下文为精简版本。

**主要功能**：
- 识别关键信息
- 移除冗余内容
- 保持信息完整性
- 生成压缩摘要

**压缩策略**：
- 事件压缩：合并同类事件
- 角色压缩：合并角色状态
- 规范压缩：合并规范变更
- 伏笔压缩：保留未回收伏笔

### adaptive_compress.j2
根据上下文类型自适应调整压缩策略。

**主要功能**：
- 分析上下文类型
- 选择最佳压缩策略
- 保留类型相关信息
- 生成自适应摘要

**上下文类型**：
- 章节内容
- 规划文档
- 规范变更
- 角色设定

### verify_compression.j2
验证压缩后的信息是否完整和正确。

**主要功能**：
- 检查关键信息保留
- 验证事实一致性
- 评估压缩质量
- 识别压缩损失

### plot_guard_judge.j2
判断当前上下文是否满足情节守卫的要求。

**主要功能**：
- 评估上下文充足性
- 判断情节进展
- 提供改进建议
- 决定是否继续

## 使用方式

```python
from novel_forge.prompts.registry import PromptRegistry
from novel_forge.core.constants import TaskType

registry = PromptRegistry()

# 压缩上下文
prompt = registry.render(
    TaskType.CONTEXT_COMPRESS,
    content="长上下文内容...",
    compression_level=0.3,
    preserve_types=["canon", "foreshadowing"]
)

# 验证压缩
result = registry.render(
    TaskType.VERIFY_COMPRESSION,
    original="原始内容...",
    compressed="压缩内容..."
)

# 情节守卫判断
result = registry.render(
    TaskType.PLOT_GUARD_JUDGE,
    context="当前上下文...",
    plot_progress="情节进度..."
)
```

## 压缩质量标准

### 关键信息保留
- ✓ 主要情节点
- ✓ 角色状态变化
- ✓ 规范建立和变更
- ✓ 伏笔设置

### 可丢弃信息
- ✗ 重复的环境描写
- ✗ 冗长的对话细节
- ✗ 次要角色的一般描述
- ✗ 过渡性场景描写

## 相关模块

- **规范模块**: `../canon/` - 规范信息管理
- **总结模块**: `../summary/` - 内容摘要
- **检查模块**: `../checking/` - 一致性检查
