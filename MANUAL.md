# Novel Forge 使用手册

**版本**: v0.1.0  
**入口与开源说明更新**: 2026-09-07

入口安装步骤以 [README](README.md) 为准；开发与恢复状态见 [文档导航](docs/README.md)，旧界面说明不代表主客户端的当前布局。

主界面已从 PySide6 转向 React/Tauri NIMO。下文保留部分历史界面和自动运行术语；配置共创策略的项目以当前后端权限为准，共创每章最终正文必须验收，旧维护命令不能绕过候选批准。完整边界见[当前工作流](docs/novel-workflow-current.md)。

本手册详细介绍 Novel Forge 小说创作系统的完整使用流程，涵盖 CLI、NIMO Desktop 和 API 三种使用方式。PySide6 仅作为安全、兼容与应急后备端。

## 目录

1. [快速开始](#快速开始)
2. [短篇创作](#短篇创作)
3. [长篇创作](#长篇创作)
4. [章节工作室](#章节工作室)
5. [质量保障系统](#质量保障系统)
6. [配置管理](#配置管理)
7. [API 参考](#api-参考)
8. [故障排查](#故障排查)
   1. [常见问题](#常见问题)
   2. [任务失败诊断](#任务失败诊断)

---

## 快速开始

### 1. 安装与配置

```bash
# 在已克隆的仓库中创建并激活虚拟环境后安装
python -m pip install -e ".[openai,anthropic]"

# NIMO 源码桌面依赖（Node.js 22+、pnpm、Rust/Tauri 工具链）
pnpm install --frozen-lockfile

# 配置 API Key
cp .env.example .env
# 编辑 .env 填入至少一个 provider 的 API Key
```

**可选依赖**：
- `.[pyside]` / `.[desktop]` - 冻结的 PySide6 兼容后备端
- `.[openai]` - OpenAI / DeepSeek / Tongyi / Kimi 支持
- `.[anthropic]` - Anthropic Claude 支持
- `.[dev]` - 开发工具（pytest, mypy, ruff）

### 2. 验证安装

```bash
# 健康检查
novel-forge healthcheck
```

### 3. 选择入口

| 入口 | 命令 | 适用场景 |
|------|------|----------|
| CLI | `novel-forge <command>` | 快速测试、批量处理 |
| NIMO Desktop | `nimo` | 默认 React/Tauri 创作工作台；自动管理本地 Engine |
| PySide fallback | `nimo-p` | 仅安全回退、兼容诊断和应急操作 |
| API | `uvicorn novel_forge.api.app:app` | 外部集成、自定义前端 |

---

## 短篇创作

短篇创作适合快速生成独立故事，流程为：`Spec → Blueprint → Beats → Draft → Edit → Evaluate`。

### CLI 方式

```bash
novel-forge run-short \
  --theme "一个遗物整理师发现委托人死了三次，每次对应不同人生" \
  --genre literary \
  --tone warm \
  --length 10000 \
  --rounds 2
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--theme` | 故事主题（必填） | - |
| `--genre` | 题材：literary/mystery/scifi/romance | literary |
| `--tone` | 基调：warm/dark/suspenseful/neutral | neutral |
| `--length` | 目标字数 | 5000 |
| `--rounds` | 编辑轮次 | 2 |
| `--writing_style` | 写作风格：default/webnovel/literary | default |

### Desktop 方式

1. 启动 Desktop：`nimo`
2. 切换到「机杼」页面
3. 选择「短篇创作」模式
4. 填写表单并点击「发起短篇创作」

### 执行流程

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Spec Enrichment (规格丰富)                               │
│    输入：用户填写的主题、提示                                │
│    输出：完整的故事规格 JSON                                 │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Beats Generation (节拍生成)                              │
│    输入：故事规格                                            │
│    输出：15-20 个故事节拍                                   │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Draft Writing (初稿撰写)                                 │
│    输入：节拍序列                                            │
│    输出：完整初稿                                            │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. Editing Rounds (编辑轮次)                                │
│    输入：初稿 + 编辑指令                                     │
│    输出：编辑后文稿（N 轮）                                  │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. Evaluation (质量评估)                                    │
│    输入：最终文稿                                            │
│    输出：质量评分 + 改进建议                                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 长篇创作

长篇创作分为两个阶段：**立项初始化** 和 **章节续写**。

### 阶段一：立项初始化

流程：`Spec → StoryBible → CharacterBible → ElementSelect → StyleProfile → Blueprint → Outline → NarrativeContract → Canon`

#### CLI 方式

```bash
novel-forge init-long \
  --premise "一个数字遗物整理师接到匿名委托，整理一位死了三次的青年才俊的数据遗产" \
  --genre mystery \
  --tone suspenseful \
  --total-chapters 24 \
  --words-per-chapter 4500 \
  --volume-mode auto
```

**立项产物**：

```
data/<project_id>/
├── spec.json                    # 故事规格
├── story_bible.json             # 故事圣经（世界观、设定）
├── character_bible.json         # 角色圣经（人物档案）
├── style_profile.json           # 风格规范（项目专属写作风格）
├── narrative_blueprint.json     # 叙事蓝图（结构规划 + 要素选择）
├── outline.json                 # 分章大纲（含 element_focus 字段）
├── plans/
│   └── element_progress.json    # 要素执行追踪
└── canon/
    └── state_0.json             # 初始典据状态
```

### 阶段二：章节续写

流程：`StatePacket → Bridge → Plan → DRAFT → WAVE → Alignment → ContinuityEval → ContinuityRepair → CausalValidation → CausalRepair → Polish/Humanize → Extract → Persist → Evaluate`

#### CLI 方式

```bash
# 续写第 3 章
novel-forge run-chapter \
  --project-id <project_id> \
  --chapter 3 \
  --rounds 2

# 批量续写
novel-forge run-chapter \
  --project-id <project_id> \
  --chapter 3 \
  --end-chapter 10 \
  --auto
```

#### Desktop 方式

1. 进入「章节工作室」
2. 选择长篇项目
3. 在章节轨道中选择章节
4. 查看三向上下文并点击「准备章节方案」

---

## 章节工作室

章节工作室是长篇创作的核心工作区，提供完整的章节创作工作流。

### 界面布局

```
┌─────────────────────────────────────────────────────────────────┐
│  项目选择  |  编辑轮次  |  模式选择 (手动/建议/自动)            │
├─────────────┬───────────────────────────────┬───────────────────┤
│             │                               │                   │
│  章节轨道   │      操作面板                  │   上下文检视     │
│  (左)       │      (中)                      │   (右)            │
│             │                               │                   │
│  [1] ✓      │  待决策节点                   │  待决策节点       │
│  [2] ✓      │  方案摘要                      │  承接要点        │
│  [3] ●      │  [确认方案] [调整]            │  后续提示        │
│  [4] ○      │                               │  角色关系        │
│  [5] ○      │  备注与补充约束                │  连贯性问题      │
│  ...        │  [文本域]                      │  评分            │
│             │                               │                   │
├─────────────┴───────────────────────────────┴───────────────────┤
│  任务流：最近任务进度与产物链接                                  │
├─────────────────────────────────────────────────────────────────┤
│  工作台产物：正文 | 计划 | 报告 | Canon 文件                     │
└─────────────────────────────────────────────────────────────────┘
```

### 章节状态

| 状态 | 图标 | 说明 |
|------|------|------|
| 已完成 | ✓ | 章节已归档 |
| 进行中 | ● | 当前正在处理 |
| 待处理 | ○ | 尚未开始 |

### 三向上下文

章节工作室的「上下文检视」面板提供：

1. **待决策节点**：当前需要确认的方案或归档决策
2. **承接要点**：上一章需要延续的关键信息
3. **后续提示**：对下一章的预设方向
4. **角色关系**：当前章节涉及的角色关系变化
5. **连贯性问题**：检测到的潜在连贯性问题
6. **评分**：质量与连贯性评分

### 全自动模式

| 模式 | 行为 |
|------|------|
| **手动 (MANUAL)** | 用户做所有决策，AI 仅提供选项 |
| **建议 (SUGGEST)** | AI 给建议并高亮推荐项，用户确认 |
| **自动 (AUTO)** | AI 自动决策并推进，用户可随时接管 |

---

## 质量保障系统

### Issue Ledger (问题追踪)

基于内容指纹的跨轮次问题追踪，区分问题状态变化：

| 状态 | 说明 |
|------|------|
| `unresolved` | 修复前存在，修复后仍存在 |
| `new` | 修复前不存在，修复后新增（回归） |
| `resolved` | 修复前存在，修复后消失 |
| `downgraded` | 严重程度降低 |
| `upgraded` | 严重程度升级（退化） |

### PatchExecutorV2 (补丁引擎)

精确文本匹配策略链，支持多种匹配模式：

| 匹配策略 | 说明 |
|----------|------|
| Exact Match | 精确文本匹配 |
| Trimmed Line | 修剪空白后行匹配 |
| Normalized | 规范化后匹配（去除标点/空白）|
| Block Anchor | 块锚点上下文匹配 |

### 连贯性修复

当检测到连贯性问题时：

1. 问题会显示在「连贯性问题」列表中
2. 每个问题标注严重程度（critical/high/medium/low）
3. 勾选需要修复的问题
4. 点击「修复选中」运行修复
5. 修复后自动重评估

### 因果链修复

针对因果关系问题的定向全文修复：

1. 问题分类：missing_causal_transition / event_without_cause / unmotivated_decision 等
2. 支持问题类型定向修复
3. 修复后重验证因果链

### 输出格式契约

系统为每个任务类型定义了输出格式契约（`format_contracts.py`），确保提示词生成的输出与解析器期望的格式一致：

- **JSON 任务**：要求输出必须是合法 JSON 对象，可配置必填字段
- **TEXT 任务**：纯文本输出，如草稿、编辑、修复等
- **格式校验**：缺失必填字段时自动触发解析重试

### 全书一致性审计

通过 `BookConsistencyStep` 对全书进行跨章节一致性检查：

1. 扫描所有章节的 Canon 记录
2. 检测角色状态、伏笔、世界观设定的跨章矛盾
3. 生成全局一致性报告

CLI 可直接运行全书审计，适合脚本、CI 或终稿前批处理：

```bash
novel-forge book-audit --project-id <project_id> --mode full_text
novel-forge book-audit --project-id <project_id> --chapters 1-12 --repair
```

审计报告写入 `reports/book_consistency_audit.json`；启用 `--repair` 时会额外生成
`reports/book_consistency_repair_report.json`。

### 世界构建一致性检查 ✨

检测章节内容是否与已建立的世界观事实和时间线一致：

1. 提取世界设定（地理、历史、规则体系）
2. 比对章节内容与设定库
3. 标记矛盾和不一致之处

### 中文引号规范化 ✨

自动规范章节草稿中的对话引号格式：

1. 将英文引号 `"` 转换为中文引号 `""` 或 `''`
2. 保持嵌套引号的一致性
3. 跳过代码块和特殊格式

### 决策引擎 ✨

封装章节流程中的修复/质量/回退决策逻辑：

1. **修复决策**：是否触发修复、选择修复策略
2. **质量门控**：质量阈值检查、通过/阻断决策
3. **回退决策**：是否回退到上一版本
4. **因果回归检测**：检测修复是否引入新的因果问题

### 叙事要素执行追踪

系统自动追踪每章对叙事要素（narrative elements）的执行情况，确保题材专属要素在全文中得到均衡体现。

#### 工作原理

1. **规划阶段**：从大纲的 `element_focus` 字段或动态推荐中获取本章重点要素（0-3 个），记录到 `element_progress.json`
2. **写作阶段**：要素约束注入 Draft 和 Edit 的 prompt，引导 LLM 在正文中体现
3. **终章评估**：章节完成后，规则引擎自动评估每个要素的命中情况：
   - 从叙事蓝图加载要素卡片（name, category, prompt_hint, description）
   - 提取术语锚点（要素名、类别、ID 分词、中文短语）
   - 在计划文本、正文、质量报告中搜索术语命中
   - 综合对齐分、连贯性分、修复问题数计算最终得分
   - 判定状态：`hit`（≥1.6）/ `weak`（≥0.6）/ `miss`（<0.6）
4. **跨章推荐**：自动累积近期 miss/weak 要素，生成下一章规划提示
5. **LLM 灰区仲裁**（可选）：对规则分数落在灰区的要素触发低频复判

#### 判定状态

| 状态 | 阈值 | 说明 |
|------|------|------|
| `hit` | score ≥ 1.6 | 要素在章节中明确体现（计划命中 + 正文命中）|
| `weak` | 0.6 ≤ score < 1.6 | 要素部分/隐式体现 |
| `miss` | score < 0.6 | 要素几乎未体现 |

#### 规则评分机制

| 信号 | 分值 | 说明 |
|------|------|------|
| 术语在计划中命中 | +0.7/term | 说明规划阶段已纳入考虑 |
| 术语在正文中命中 | +1.0/term | 说明实际写作中已体现 |
| 质量报告出现负面标记 | -0.8 | 如"缺失"、"不足"、"薄弱"等 |
| 对齐分 < 7.0 | -0.2 | 与大纲对齐度不足 |
| 连贯性分 < 7.0 | -0.2 | 连贯性问题较多 |
| 修复问题 ≥ 3 个 | -0.2 | 章节质量问题较多 |

#### Desktop 配置

进入「火候」页面 → 「叙事要素执行追踪（可选）」：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| 启用灰区仲裁 | 开启后对灰区结果触发低频复判 | false |
| 每章仲裁上限 | 每章最多仲裁的要素数量（0=禁用）| 1 |
| 灰区分数下界 | 规则分数灰区下界（含）| 0.8 |
| 灰区分数上界 | 规则分数灰区上界（含）| 1.4 |
| 仲裁输出上限 | 单次仲裁输出 token 上限 | 256 |
| 仲裁温度 | 仲裁温度（建议低温）| 0.0 |

#### 产物文件

```json
// plans/element_progress.json
{
  "schema_version": "1.0",
  "updated_at": "2026-04-14T...",
  "chapters": {
    "3": {
      "chapter_number": 3,
      "focus_source": "outline",
      "scheduled_element_ids": ["romance_emotional_barriers"],
      "results": [
        {
          "element_id": "romance_emotional_barriers",
          "status": "hit",
          "score": 2.4,
          "evidence": {
            "plan_hits": ["情感障碍", "心理防线"],
            "text_hits": ["她心中的壁垒", "无法跨越的鸿沟"],
            "penalties": []
          }
        }
      ],
      "summary": {"scheduled": 1, "hit": 1, "weak": 0, "miss": 0},
      "arbiter": {"enabled": false}
    }
  },
  "totals": {"scheduled": 3, "hit": 2, "weak": 1, "miss": 0},
  "pending_element_ids": ["romance_emotional_barriers"],
  "arbiter_totals": {"runs": 0, "reviewed": 0, "changed": 0}
}
```

---

## 配置管理

### 模型配置

#### 方式一：.env 文件

```bash
# .env
NOVEL_FORGE_OPENAI_API_KEY=sk-xxx
NOVEL_FORGE_DEEPSEEK_API_KEY=sk-xxx
NOVEL_FORGE_DEFAULT_PROVIDER=deepseek
```

#### 方式二：Desktop 配置页

1. 进入「火候」页面
2. 配置 Provider 和 API Key
3. 设置默认 Provider
4. 点击「保存并测试」

### 任务路由

可以为不同任务类型指定不同的 Provider 和模型：

```json
{
  "task_routing": {
    "DRAFT_CHAPTER": {
      "provider": "openai",
      "model": "gpt-4o"
    },
    "WAVE_CHAPTER": {
      "provider": "openai",
      "model": "gpt-4o"
    },
    "EDIT_CHAPTER": {
      "provider": "deepseek",
      "model": "deepseek-chat"
    },
    "CHECK_CONTINUITY": {
      "provider": "tongyi",
      "model": "qwen-max"
    }
  }
}
```

### 预设管理

#### 保存预设

1. 填写完表单后，点击「存为预设」
2. 输入预设名称
3. 预设保存到 `data/presets/<type>_<name>.json`

#### 载入预设

1. 点击表单上方的预设工具栏
2. 选择已保存的预设
3. 表单自动填充

---

## API 参考

### 主要路由

```
# 作品管理
POST   /api/v1/works/create                    # 创建作品
GET    /api/v1/works/list                      # 作品列表
GET    /api/v1/works/{project_id}              # 作品详情

# 短篇创作
POST   /api/v1/short/run                       # 运行短篇

# 章节创作
POST   /api/v1/chapters/init                   # 初始化长篇
POST   /api/v1/chapters/run                    # 运行章节

# 章节工具
POST   /api/v1/chapter-tools/polish            # 章节精修
POST   /api/v1/chapter-tools/book-consistency  # 全书一致性审计
POST   /api/v1/chapter-tools/export            # 导出书籍
POST   /api/v1/chapter-tools/prepare-chapter   # 准备章节
POST   /api/v1/chapter-tools/repair-continuity # 连贯性修复
POST   /api/v1/chapter-tools/repair-causal     # 因果链修复
POST   /api/v1/chapter-tools/repair-issues     # 问题修复
POST   /api/v1/chapter-tools/reevaluate        # 重新评估
POST   /api/v1/chapter-tools/reextract-relationships # 重新提取关系

# 记忆系统
POST   /api/v1/memory/search                   # 语义搜索
GET    /api/v1/memory/status                   # 记忆状态

# 大纲追踪
GET    /api/v1/outline-tracker/relationships   # 角色关系
GET    /api/v1/outline-tracker/events          # 关键事件

# 状态检查
GET    /api/v1/status/health                   # 健康检查

# 管理接口
GET    /api/v1/admin/config                    # 获取配置
POST   /api/v1/admin/config                    # 更新配置

# 健康检查 (根路径)
GET    /health                                 # 快速健康检查
```

### 请求示例

```bash
# 运行短篇
curl -X POST http://localhost:8000/api/v1/short/run \
  -H "Content-Type: application/json" \
  -d '{
    "theme": "一个遗物整理师的故事",
    "genre": "mystery",
    "tone": "suspenseful",
    "length_target": 10000
  }'

# 运行章节
curl -X POST http://localhost:8000/api/v1/chapters/run \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "my-novel",
    "chapter_number": 3,
    "max_edit_rounds": 2
  }'
```

---

## 故障排查

### 常见问题

#### 1. 安装问题

**问题**：`pip install` 失败

**解决**：
```bash
# 升级 pip
python -m pip install --upgrade pip

# 清理缓存重装
pip cache purge
pip install -e ".[all,dev]" --no-cache-dir
```

#### 2. API Key 问题

**问题**：提示 API Key 无效

**解决**：
1. 检查 `.env` 文件是否存在
2. 确认 API Key 格式正确
3. 测试 Provider 连通性（Desktop「火候」页）
4. 检查是否需要代理

#### 3. 模型调用失败

**问题**：请求超时或返回错误

**解决**：
```bash
# 切换到 mock 模式测试
novel-forge healthcheck --mock

# 检查日志
cat data/logs/<run_id>/events.jsonl | jq .
```

#### 4. NIMO Desktop 启动失败

**问题**：缺少 pnpm/Rust、前端端口被占用或本地 Engine 版本不一致

**解决**：
```bash
# 检查脱敏后的启动配置
nimo --print-config

# 开发环境重新安装前端依赖
pnpm install --frozen-lockfile

# 仅在没有活动任务时安全重启 NIMO 拥有的本地 Engine
nimo --restart-owned-backend
```

如果必须临时处理兼容问题，可运行 `nimo-p` 进入冻结的 PySide6 后备端。

#### 4.1 PySide6 后备端启动失败

**问题**：PySide6 导入错误

**解决**：
```bash
# 重装 PySide6 后备端（仅支持 6.6 至 6.10.x）
python -m pip install --upgrade --force-reinstall "PySide6>=6.6,<6.11"
python -m pip install -e ".[pyside]"
```

#### 5. 章节续写中断

**问题**：执行到一半失败

**解决**：
1. 查看错误日志
2. 修正配置或补充上下文
3. 直接重新运行，系统会从断点继续

### 任务失败诊断

当任务因 LLM 输出格式错误或 provider 故障而失败时，按以下步骤诊断：

#### 1. 定位错误日志

```bash
# 找到最新运行的日志目录
ls -lt data/<project_name>/logs/ | head -5

# 查看错误日志
cat data/<project_name>/logs/<run_id>/errors.jsonl
```

关键信息：
- 所有记录都有 `run_id`、`project_id`、`command`、`request_id`、递增的 `sequence` 与 `event_id`；用于跨文件关联同一次运行
- `event` 字段：`step`（format retry）、`api_call_error`（provider 错误）、`run_failed`（运行崩溃）
- `data.task` 字段：出错的 TaskType
- `data.error` 字段：具体错误描述

#### 2. 查看应用异常上下文

```bash
# JSON Lines，包含普通 Python INFO+ 日志、步骤/任务关联与异常堆栈
cat data/<project_name>/logs/<run_id>/application.jsonl | jq .

# 只看更适合人工浏览的 WARNING+ 记录
cat data/<project_name>/logs/<run_id>/python.log
```

`application.jsonl` 会截断过长值，并对常见 API key、Bearer token、密码和凭据字段脱敏；
模型请求与响应仍保存在 `model_calls/`，应仅在本地受控环境中查看。

#### 3. 查看 format 错误详情

```bash
ls data/<project_name>/logs/<run_id>/format_errors/
```

每个文件包含 schema 校验错误、缺失字段和 LLM 原始输出片段。

#### 4. 查看模型调用记录

```bash
ls data/<project_name>/logs/<run_id>/model_calls/
```

每个文件包含完整的 API 请求和响应，用于分析 LLM 实际输出。

#### 5. 检查路由配置

```bash
python3 scripts/audit_routing_config.py
```

确认出错的任务路由到了哪个 provider，以及 fallback 链是否合理。

#### 6. 常见错误与修复

| 错误 | 原因 | 修复 |
|------|------|------|
| `JSON schema validation failed` | LLM 输出格式不符合 contract | 修改 prompt 模板或 schema validator |
| `all route attempts failed` | 所有 provider 都无法处理请求 | 修改路由配置或减小 prompt 大小 |
| `context length exceeded` | prompt 超过 provider 上下文窗口 | 切换到更大窗口的 provider 或拆分 payload |
| `invalid params, 400` | provider 拒绝请求参数 | 检查 prompt 格式或切换到其他 provider |

#### 6. 完整诊断流程

详细的诊断流程和修复 SOP 请参考 [`docs/runbook_structured_output_llm.md`](docs/runbook_structured_output_llm.md)，包含：
- 五步诊断流程
- 四类修复 SOP（路由 / Schema / 提示词 / 上下文）
- 三个真实案例复盘
- 工具清单和预防性措施

### 获取帮助

```bash
# 查看帮助
novel-forge --help
novel-forge <command> --help

# 提交问题
# https://github.com/nbz1147603651/Novel-Forge/issues
```

---

## 附录

### 支持的题材

| 题材 | 值 |
|------|-----|
| 奇幻 | fantasy |
| 科幻 | scifi |
| 悬疑 | mystery |
| 言情 | romance |
| 惊悚 | thriller |
| 文学 | literary |
| 恐怖 | horror |
| 历史 | historical |

### 支持的基调

| 基调 | 值 |
|------|-----|
| 温暖 | warm |
| 黑暗 | dark |
| 悬疑 | suspenseful |
| 中性 | neutral |

### 支持的写作风格

| 风格 | 值 | 说明 |
|------|-----|------|
| 通用 | default | 无特殊约束，适合所有类型 |
| 网文 | webnovel | 对话驱动、快节奏、爽感直给 |
| 文学 | literary | 慢叙事、心理描写、含蓄克制 |

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `NOVEL_FORGE_STORAGE_ROOT` | 数据存储目录 | `./data` |
| `NOVEL_FORGE_DEFAULT_PROVIDER` | 默认 Provider | `mock` |
| `NOVEL_FORGE_LOG_LEVEL` | 日志级别 | `INFO` |
| `NOVEL_FORGE_OPENAI_API_KEY` | OpenAI API Key | - |
| `NOVEL_FORGE_ANTHROPIC_API_KEY` | Anthropic API Key | - |
| `NOVEL_FORGE_DEEPSEEK_API_KEY` | DeepSeek API Key | - |
| `NOVEL_FORGE_TONGYI_API_KEY` | 通义千问 API Key | - |
| `NOVEL_FORGE_KIMI_API_KEY` | Kimi API Key | - |
| `NOVEL_FORGE_MIMO_API_KEY` | 小米 MiMo API Key | - |
| `NOVEL_FORGE_TENCENT_API_KEY` | 腾讯混元 API Key | - |
| `NOVEL_FORGE_MINIMAX_API_KEY` | MiniMax API Key | - |
| `NOVEL_FORGE_OLLAMA_BASE_URL` | Ollama API 地址 | `http://localhost:11434/v1` |
| `NOVEL_FORGE_OLLAMA_MODEL` | Ollama 默认模型 | `llama3.2` |

---

## 高级配置

### 修复行为控制

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `long_continuity_repair_threshold` | 连贯性修复触发阈值 (0-10) | 8.5 |
| `long_continuity_max_repair_rounds` | 连贯性修复最大轮次 | 2 |
| `long_causal_repair_enabled` | 是否启用因果修复 | `True` |
| `long_causal_threshold` | 因果修复触发阈值 | 5.0 |
| `long_causal_max_repair_rounds` | 因果修复最大轮次 | 2 |
| `repair_must_fix_severity` | 必修问题严重程度 | `critical` |
| `repair_display_min_severity` | 问题面板显示最低严重程度 | `low` |
| `repair_always_reaudit` | 修复后是否重新审核 | `False` |
| `max_auto_repair_attempts` | 自动修复最大尝试次数 | 2 |
| `recheck_strategy` | 修复后复检策略 | `targeted_with_global_guard` |
| `change_budget_threshold` | 改动比例阈值 (触发全量复审) | 0.15 |

### 多轮对话控制

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `short_draft_multi_turn` | 短篇初稿多轮对话 | `True` |
| `outline_multi_turn` | 大纲续写多轮对话 | `True` |
| `edit_multi_turn` | 章节编辑多轮对话 | `False` |
| `outline_thinking` | 大纲生成思考模式 | `False` |
| `short_draft_multi_turn_providers` | 短篇多轮 Provider 列表 | `tongyi,deepseek` |
| `outline_multi_turn_providers` | 大纲多轮 Provider 列表 | `tongyi,deepseek` |
| `edit_multi_turn_providers` | 编辑多轮 Provider 列表 | `tongyi,deepseek` |

### Memory 系统配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `memory_episodic_enabled` | 启用情景记忆 | `True` |
| `memory_multi_granularity_summary_enabled` | 启用多粒度摘要 | `True` |
| `memory_chapter_summary_target_words` | 章节摘要目标字数 | `200` |
| `memory_adaptive_compression_enabled` | 启用自适应压缩 | `True` |
| `memory_motif_tracking_enabled` | 启用母题追踪 | `True` |
| `memory_critic_agent_enabled` | 启用 CriticAgent | `True` |
| `memory_critic_agent_run_async` | CriticAgent 异步运行 | `False` |
| `memory_concurrent_indexing` | 记忆索引并发执行 | `True` |

### 大纲生成控制

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `outline_batch_size` | 分批生成大纲每批章数上限，0 表示按模型输出能力与 JSON 稳定性自动计算 | 0 |
| `chapter_contract_batch_size` | 分批生成章节契约每批章数，0 表示按模型输出能力自动计算 | 0 |
| `outline_tracker_enabled` | 启用大纲关系追踪器 | `True` |
| `long_max_consistency_replans` | 一致性校验重新规划次数 | 2 |
| `forbidden_elements_cross_chapter_window` | 跨章禁用元素窗口 | 4 |
| `forbidden_elements_hard_max_items` | 单章硬禁修辞项上限 | 8 |
| `forbidden_elements_soft_max_items` | 单章软禁修辞项上限 | 12 |
| `forbidden_elements_quota_max_items` | 单章限额复用/回环项上限 | 6 |
| `forbidden_element_sources_max_items` | 单章禁用来源追踪记录上限 | 24 |
| `forbidden_elements_rank_by_relevance` | 按来源置信度与本章语境相关性排序后裁剪 | `True` |

### 其他高级配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `long_auto_chapter_cooldown_seconds` | 全书连跑章间冷却 (秒) | 5 |
| `long_polish_reaudit_skip_similarity_threshold` | 精修后跳过重审的文本相似度阈值 | 0.99 |
| `long_polish_enabled` | 启用精修润色步骤 | `False` |
| `style_profile_enabled` | 自动生成风格规范 | `True` |
| `auto_introduce_characters` | 自动检测并生成新角色档案 | `True` |
| `api_call_timeout_s` | API 调用超时 (秒) | 900 |
| `log_keep_runs` | 保留运行日志目录数 | 20 |
| `storage_cache_enabled` | 启用存储缓存 | `True` |
| `short_segment_mode` | 短篇分段模式 | `auto` |
