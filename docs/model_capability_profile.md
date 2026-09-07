# 模型能力事实注册表

> 版本: 1.2.0 | 核验日期: 2026-07-16 | 证据策略: 只使用 provider 官方文档，运行时限制取保守值

`config/model_capability_profile.json` 是模型能力的单一事实源。它现在同时驱动结构化输出策略、思考/多轮能力、上下文窗口和最大输出预算，不再只是 lint 参考。

## 思考能力不是统一布尔开关

官方 API 实际存在五种不同语义：

- `forced`：模型始终思考，不存在关闭参数。
- `toggle`：只有开启/关闭。
- `adaptive`：开启后由模型根据任务选择是否及如何思考。
- `effort`：支持 `low / medium / high / xhigh / max` 中的某些档位。
- `budget`：使用整数 token budget，而不是标准化档位。

因此，火候页现在存储 `thinking_mode`，旧的 `thinking: bool` 只作兼容字段。界面根据 provider + model 的能力档案动态显示官方实际选项；固定思考模型显示为不可编辑的“思考：固定”。

### 逐平台官方核验摘要

| Provider / API | 官方控制语义 | 程序中的选择 |
|---|---|---|
| DeepSeek V4 | 默认开启；`thinking` 可关闭；有效 effort 为 `high / max` | `关闭 / 高 / 最高` |
| MiniMax M3 (Chat Completions) | 省略 `thinking` 时默认 adaptive；可显式 disabled | `关闭 / 自适应` |
| MiniMax M2.x | 官方明确说明不可关闭；`reasoning_split` 只分离返回字段 | `固定`（不可编辑） |
| Xiaomi MiMo V2.5 / Pro | 默认开启；支持 `enabled / disabled`，没有官方等级 | `关闭 / 开启` |
| Kimi K2.5 / K2.6 | 混合思考默认开启，可关闭；专用 thinking 型号固定 | `关闭 / 开启`或 `固定` |
| Tencent TokenHub Hy3 | 可开关；开启后支持 `low / medium / high` | `关闭 / 低 / 中 / 高` |
| Alibaba Qwen | 型号分为非思考、固定思考和混合思考；混合型可用 `enable_thinking`，部分型号支持 `thinking_budget` | 仅对已核验型号显示开关；数值 budget 不伪装成官方等级 |
| SiliconFlow | 部分托管模型支持 `enable_thinking` 和整数 `thinking_budget` | 按运行时模型能力保守开关 |
| Ollama | 多数模型是布尔值；GPT-OSS 等使用 effort 且可能不能完全关闭 | 只在型号证据充足时显示档位 |
| OpenAI | reasoning effort 档位随型号不同 | 按精确型号档案显示 |
| Anthropic | adaptive/manual/always-on 随型号不同；effort 档位也随型号不同 | 按精确型号档案显示 |
| Volcengine Ark | Doubao Seed 2.0 支持显式 enabled/disabled | `关闭 / 开启` |

未获得官方证据的型号不会自动继承同平台其他模型的档位。这是有意的保守设计，用来避免把无效参数发到不兼容端点。

## MiniMax-M2.7-highspeed 核验结论

| 能力 | 结论 |
|---|---|
| 可用性 | 在役；与 `MiniMax-M2.7` 使用同一模型质量 |
| 上下文 | 204,800 tokens |
| 速度 | 官方标称约 100 tokens/s；普通 M2.7 约 60 tokens/s |
| 思考/工具 | 固定 reasoning，不可关闭；支持 streaming、tool/function calling |
| 原生 JSON Schema | 不支持 |
| 原生 JSON object mode | 不支持 |
| 生产输出上限 | 65,536 tokens（应用保守上限） |

MiniMax 官方 API 将 `response_format` 限定为 `MiniMax-Text-01`，因此 M2.7-highspeed 的 JSON 任务必须使用以下链路：

`prompt-only JSON 契约 → 本地 JSON 解析 → 嵌套 schema/内容校验 → 定向格式重试 → 确定性规则兜底`

本次 `PLAN_INIT_RESEARCH_QUERIES` 错误不是上下文或输出 token 不足，而是 prompt-only 模式下的非法 JSON 和内容字段偏离。`finish_reason=stop` 时继续放大 token 预算不会修复这类错误。

## 1.2.0 数据模型

注册表有两层：

- `model_families`: 官方明确的模型族能力，使用 provider + glob pattern 匹配。
- `models`: 精确模型记录，用于当前主力、特殊限制和退役例外；优先级高于族规则。

每条可用于运行时的记录必须带：

- `availability`: `active / legacy / retired / runtime_discovered / unknown`
- `verified_at` 和非空 `source_urls`
- `context_window_tokens` 与 `max_output_tokens`（若官方未公布，只声明经验性保守上限）
- `supports_thinking / supports_multi_turn / supports_function_calling`
- `thinking_control / thinking_modes / default_thinking_mode`（已核验思考控制时）
- 完整的 `structured_output` 声明

结构化输出能力使用 `supported / unsupported / unknown`。`unknown` 不等于支持；运行时会降级到更保守的模式。用户自定义 OpenAI-compatible 端点不再默认获得 JSON Schema 能力。

## 当前 provider 能力摘要

| Provider | 当前模型/发现方式 | 结构化输出策略 |
|---|---|---|
| OpenAI | GPT-5.6 家族 | strict JSON Schema |
| Anthropic | Claude 5 及在役 4.5–4.8 | `output_config.format=json_schema` |
| DeepSeek | V4 Flash/Pro | JSON object，本地 schema 校验 |
| Kimi | K2.7 Code/Highspeed、K2.6、K2.5 | JSON Mode；不宣称 strict JSON Schema |
| Xiaomi MiMo | V2.5 Pro / V2.5 / Pro UltraSpeed | JSON object；本地 schema 校验 |
| MiniMax | M3/M2.x | prompt-only；`MiniMax-Text-01` 例外 |
| Tongyi | 百炼官方模型目录 | JSON object；thinking 模式下禁用 |
| Tongyi Coding Plan | 只保留官方精确白名单 | 原生格式能力未明确，保守处理 |
| Tencent | TokenHub `hy3 / hy3-preview` | JSON object/结构化输出，不假设 strict schema |
| SiliconFlow | 调用 `GET /models` 运行时发现 | JSON object，应用端 schema 校验 |
| Ollama | 本地 `tags/show` 发现 | 本地支持 schema；Cloud 当前降级 |
| Volcengine Ark | Doubao Seed 2.0 族 | JSON Schema/JSON object，按型号匹配 |

SiliconFlow 和 Ollama 的具体模型可用性是运行时状态，不能靠代码中的静态列表保证。静态列表只作 UI 输入提示，真正调用仍以 provider 的模型发现接口为准。

## 官方来源

- MiniMax: <https://platform.minimax.io/docs/guides/text-generation>, <https://platform.minimax.io/docs/guides/models-intro>, <https://platform.minimax.io/docs/api-reference/text-chat-openai>, <https://platform.minimax.io/docs/api-reference/responses-create>, <https://platform.minimax.io/docs/token-plan/faq>
- OpenAI: <https://developers.openai.com/api/docs/models>, <https://developers.openai.com/api/docs/guides/structured-outputs>
- Anthropic: <https://platform.claude.com/docs/en/about-claude/models/overview>, <https://platform.claude.com/docs/en/build-with-claude/effort>, <https://platform.claude.com/docs/en/docs/build-with-claude/extended-thinking>
- DeepSeek: <https://api-docs.deepseek.com/quick_start/pricing>, <https://api-docs.deepseek.com/guides/thinking_mode>
- Xiaomi MiMo: <https://mimo.mi.com/docs/en-US/api/chat/openai-api>, <https://mimo.mi.com/docs/en-US/quick-start/usage-guide/other/deep-thinking>
- Kimi: <https://platform.kimi.com/docs/models>, <https://platform.kimi.com/docs/guide/use-kimi-k2-thinking-model>
- Alibaba: <https://help.aliyun.com/zh/model-studio/deep-thinking>, <https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses>, <https://help.aliyun.com/zh/model-studio/coding-plan-faq>
- Tencent: <https://cloud.tencent.com/document/product/1729/131925>, <https://cloud.tencent.com/document/product/1823/130051>, <https://cloud.tencent.com/document/product/1823/131208>
- SiliconFlow: <https://docs.siliconflow.cn/cn/api-reference/chat-completions/chat-completions_copy>, <https://docs.siliconflow.cn/cn/api-reference/models/get-model-list>
- Ollama: <https://docs.ollama.com/capabilities/thinking>, <https://docs.ollama.com/api/openai-compatibility>
- Volcengine: <https://www.volcengine.com/docs/82379/1795150>, <https://www.volcengine.com/docs/82379/1958524>

## 使用与校验

```bash
python3 scripts/load_model_capability.py minimax/MiniMax-M2.7-highspeed
python3 scripts/load_model_capability.py --all
python3 scripts/load_model_capability.py --verify
```

`--verify` 会检查 1.2.0 版本、族规则唯一性、能力字段类型、结构化输出五项声明、核验日期和官方来源。

维护时应先更新族规则，只在当前主力型号、特殊限制或退役例外时添加精确记录。无官方证据的能力必须保持 `unknown`。
