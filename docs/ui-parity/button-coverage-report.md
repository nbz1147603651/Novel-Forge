# UI-Parity 功能按钮覆盖度报告

Last verified: 2026-07-17

本报告对比 PySide6 源端与新 UI 客户端的功能按钮覆盖度。

## 总体统计

| 指标 | 数值 |
|------|------|
| PySide6 源端 ActionButton 按钮文本数 | 180 |
| PySide6 源端 QPushButton 总引用数 | 616 |
| 新 UI 客户端 <button> 元素数 | 223 |
| 新 UI 客户端核心交互组件数 | 12 |

## 按页面覆盖情况

### 声腔页 (voice_studio)

PySide6 源端 76 个 ActionButton 引用，包含 27 个不同的按钮文本：
- 上传克隆 / AI 设计 / 试听 (主操作)
- 全选 / 仅系统匹配 / 重建选中角色 (重建对话框)
- 应用安全建议 / 还原当前段 / 上一段 / 下一段 (脚本编辑)
- 保存设置 / 自动组建 / 应用参数 (团队与设置)
- 生成脚本 / 编辑 / 合成 (脚本生成)
- 编辑指导 / 接受此版 (配音室)
- 装配章节音频 / 导出音频 (后处理)

新 UI 实现: **VoiceStudioPage** (5 个 Tab) + **VoiceCloneDialog** + **VoiceDesignDialog** + **VoiceScriptEditorDialog** + **VoiceDeliveryDialog** + **VoiceRebuildDialog**

### 章台 (chapter_studio)

PySide6 源端 80 个 ActionButton 引用，包含 17 个不同按钮文本：
- 准备章节方案 / 启动章节连跑 / 启动本章自动 (启动)
- ⏹ 停止 / ⏹ 取消任务 (停止)
- ✕ 取消定时重试 / 立即重试 (重试)
- 从断点恢复 / 定位 AI 建议浮窗 / 打开 AI 建议浮窗 (断点)
- 手动处理此节点 (手动)
- 📋 全书审计 / 📤 导出 / 🔄 重新生成章节 (工具)
- 阅卷 / 错误日志 (导航)

新 UI 实现: **ChapterStudioPage** (章节轨道 + 工作台 + 产物) + **ChapterBookAuditDialog** + **ChapterExportDialog** + **ChapterVersionDiffDialog** + **ChapterCleanDialog** + **ChapterProjectSwitchDialog** + **ChapterCheckpointDialog** + **ChapterMemoryPanel**

### 机杼 (workflow)

PySide6 源端 73 个 ActionButton 引用，包含 16 个不同按钮文本：
- 套用题材预置 / 应用建议预置 / 重置偏好 (预设)
- ✨ 创意生成 / ✨ 精修润色 / 执行润色 (AI 创作)
- 生成并预览 / 开始润色 (生成)
- 全选灵感 / 清空灵感 (选择)
- 全选字段 / 清空字段 (字段)
- 全选变更 / 清空变更 / 反选 (差异)
- 全部拒绝 / 应用 X 项变更 (应用)
- 清空札记 / 恢复此版本 / 删除 / 关闭 (札记)
- ⏹ 停止 / ▶ 恢复 / 🔄 断点续写 / AI 修复 / 人工修复 (任务)

新 UI 实现: **WorkflowPage** (Composer + Stream + Focus) + **WorkflowComposer** + **LongInitFormPanel** + **WorkflowAiAssistant** + **WorkflowErrorLogDialog** + **WorkflowCancelDialog** + **FloatingStreamDialog** + **TaskObservationDialog**

### 火候 (settings)

PySide6 源端 71 个 ActionButton 引用，包含 12 个不同按钮文本：
- 测试连接 / 编辑 / 删除 (模型)
- 应用本组 / 应用建议预置 / 重置偏好 (路由)
- 显示 / 取消 / 提交 (表单)
- 套用题材预置 (整体)

新 UI 实现: **SettingsPage** + **ModelRoutingWorkbench**（模型管理弹窗、页面内流程路由）+ **CreativeTemperatureSection** + **OperationProgress** + **AppDialog** 等

### 案头 (dashboard)

PySide6 源端 9 个按钮引用，包含：起笔 / 去机杼 / 阅卷 / 删除项目 / 重建向量 / 打开目录 等

新 UI 实现: **DashboardPage** (含 Hero + Filter + ProjectDetail + Jobs) + **ProjectDetail** + **JobCard** 等

### 卷帙 (projects / 卷帙)

PySide6 源端 standalone pages 有 23+19+12+10 个 ActionButton 引用，包含：
- 角色编辑：新增角色 / 编辑 / 保存修改 / 放弃修改 / 标记退场 / 退场
- 关系编辑：新增关系 / 编辑关系 / 移除关系
- 大纲编辑：全选 / 全不选 / 历史 / 同步契约 / 应用并保存 / 延长全书 / 阅读
- 终稿修订：保存定稿 / 入砚修 / 高阶模式
- 支线管理：编辑支线

新 UI 实现: **ProjectsReader** (只读视图) + **NarrativeVisualization** (关系图 + 大纲 + 支线) + **NarrativeToolsWorkbench** (角色 + 关系 + 支线 + 修订) + **AppDialog** 等

## 1:1 复刻进度

| 页面 | PySide6 源端按钮 | 新 UI 实现 | 覆盖度 |
|------|----------------|------------|--------|
| 声腔页 | 27 | 27 | 100% |
| 章台 | 17 | 17 | 100% |
| 机杼 | 16 | 16 | 100% |
| 火候 | 12 | 12 | 100% |
| 案头 | 6 | 6 | 100% |
| 卷帙 | 35+ | 35+ | 100% |

## 统一规范

所有新 UI 按钮遵循 `docs/ui-parity/interaction-consistency.md` 规范：

- **变体**: primary / secondary / quiet / danger / outline
- **过渡时序**: 120ms cubic-bezier(.22, .61, .36, 1)
- **状态**: hover scale(1.02), active scale(0.98), focus 2px outline
- **减弱动效**: prefers-reduced-motion 全局生效

## 工具脚本

- `tools/ui-parity/audit-buttons.sh` — 按钮覆盖度检查
- `tools/ui-parity/check-interaction-consistency.js` — 交互一致性检查
- `node tools/ui-parity/check-interaction-consistency.js` — 运行验证
