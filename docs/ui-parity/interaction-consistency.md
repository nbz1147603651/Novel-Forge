# UI-Parity 交互一致性规范

Last verified: 2026-07-17

本文档定义新架构 UI 与 PySide6 源端之间的统一交互规范。所有页面级组件必须遵循这些规则，确保视觉与交互 1:1 复刻。

## 1. 按钮交互一致性

### 1.1 变体使用规则

| 变体 | 用途 | PySide6 对应 |
|------|------|------|
| `primary` | 主操作（提交、确认、保存） | `ActionButton(variant="primary")` |
| `secondary` | 次操作（取消、关闭、编辑） | `ActionButton(variant="secondary")` |
| `quiet` | 文本操作（链接、链接式按钮） | `ActionButton(variant="quiet")` |
| `danger` | 破坏性操作（删除、清空） | `ActionButton(variant="danger")` |
| `outline` | 强调但不主导（打开、查看） | — |

### 1.2 交互时序

所有按钮遵循统一的交互时序：

| 状态 | 属性变化 | 时长 | 缓动 |
|------|----------|------|------|
| 默认 | 基础样式 | — | — |
| hover | scale(1.02) + 阴影/背景 | 120ms | cubic-bezier(.22, .61, .36, 1) |
| active | scale(0.98) + 颜色加深 | 150ms | OutCubic |
| focus | 2px 描边 offset 2px | 即时 | — |
| disabled | opacity 0.48 + cursor not-allowed | 150ms | ease |
| loading | opacity 0.7 + 旋转 spinner | 600ms/圈 | linear |

### 1.3 尺寸规范

| 尺寸 | 高度 | 字号 | 用途 |
|------|------|------|------|
| 共用 | 32px | 12px | 默认工具栏、表单操作、页内主次操作 |
| 密集 (`.button-compact`) | 28px | 11px | 章节轨道、筛选、表格式操作栏 |
| 可读 (`.button-relaxed`) | 36px | 13px | 量测过的源端弹窗、长文本编辑器操作 |

`38px/14px` 不再是共用按钮的默认值。14px 只保留给阅读正文或有源端量测证据的局部组件；不能以“大号按钮”替代主次层级。

### 1.4 全局密度阶梯

共享几何只从 `design-tokens.css` 取得：`4 / 8 / 12 / 16 / 20 / 24px` 间距，以及 `10 / 11 / 12 / 13 / 16 / 22 / 24px` 字体阶梯。页面不可另设一套通用按钮、字号或间距；若源端固定大小的组件需要例外，必须在 `fidelity-ledger.md` 记录量测证据与适用范围。

## 2. 容器 Surface 一致性

### 2.1 Tone 使用规则

| Tone | 用途 | 背景/边框 |
|------|------|----------|
| hero | 顶部欢迎区、引导卡片 | 渐变 + 强阴影 |
| elevated | 弹窗、浮动层、对话 | 浅米色 + 中阴影 |
| card | 列表项、卡片 | 米色 + 轻阴影 |
| panel | 内容区、子模块 | 沙色 + 轻阴影 |
| flat | 内嵌区块 | 半透明白 |
| rail | 侧栏 | 暖色 + 透明 |

### 2.2 通用属性

```css
border: 1px solid color-mix(in srgb, var(--nf-border-default) 22%, transparent);
border-radius: 17px;
background: <tone-specific>;
box-shadow: <tone-specific>;
```

## 3. 状态徽标一致性

### 3.1 颜色映射

| 状态 | 前景 | 背景 | 语义 |
|------|------|------|------|
| success | `var(--nf-status-success)` | `var(--nf-status-success-bg)` | 已完成、已配、已接受 |
| warning | `var(--nf-accent-primary-pressed)` | warning-bg | 待处理、待配、暂停 |
| failed | `var(--nf-brand-logo-frame)` | `var(--nf-accent-primary-pressed)` | 失败、已中断 |
| paused | `var(--nf-text-secondary)` | `var(--nf-bg-control)` | 暂停 |
| running | `var(--nf-accent-primary)` | `var(--nf-bg-surface)` | 执行中 |
| completed | `var(--nf-status-success)` | `var(--nf-status-success-bg)` | 已完成 |

### 3.2 形状

- 圆形状态点：12-20px 圆形
- 胶囊徽标：pill 形，padding 0 12px，min-height 32px
- 卡片徽标：pill 形，padding 4px 9px，min-height 24px

## 4. 列表项一致性

### 4.1 章节轨道 / 角色列表 / 任务卡片

```
未选中：
  border: 1px solid transparent
  background: transparent
  hover：background hover-secondary + translateX(2-3px)

已选中：
  border-color: var(--nf-accent-primary) + alpha
  background: var(--nf-accent-primary) 8-12% + surface
  box-shadow: 0 2px 8px shadow 10%
```

### 4.2 选中指示器

对于多选/单选列表项，左侧使用 3px 宽色条作为指示：

```
position: absolute; left: 0; top: 50%; transform: translateY(-50%);
width: 3px; height: 60%;
background: var(--nf-accent-primary);
border-radius: 0 2px 2px 0;
animation: 从 0% 到 60% 高度生长 (250ms)
```

## 5. 表单字段一致性

### 5.1 输入框

```css
input, select, textarea {
  border: 1px solid var(--nf-border-default) 26%;
  border-radius: 8-11px;
  background: var(--nf-bg-input) 或 var(--nf-bg-input-soft);
  transition: border-color 160ms ease, box-shadow 160ms ease;
}

input:focus {
  outline: 0;
  border-color: var(--nf-accent-primary);
  box-shadow: 0 0 0 3px var(--nf-accent-primary) 12%;
}
```

### 5.2 错误状态

```
border-color: var(--nf-accent-primary-pressed);
helper-text: 12px, var(--nf-text-muted-soft);
```

## 6. 加载状态一致性

### 6.1 Skeleton 占位

```
height: 17px
border-radius: 99px
background: linear-gradient(90deg, #f3e9dd, #fffaf3, #f3e9dd)
background-size: 200% 100%
animation: shimmer 1.4s ease-in-out infinite
```

### 6.2 Spinner

```
border: 3px solid accent-primary 22%
border-top-color: accent-primary
border-radius: 50%
animation: spin 800ms linear infinite
```

## 7. 间距规范

| Token | 值 | 用途 |
|-------|-----|------|
| xs | 4px | 内联元素间距 |
| sm | 8px | 标签-值间距 |
| md | 12px | 卡片内边距 |
| lg | 16px | 区块内边距 |
| xl | 24px | 页面大区块 |
| xxl | 32px | 弹窗内边距 |

## 8. 动效时序规范

| 交互 | 时长 | 缓动函数 |
|------|------|----------|
| hover | 120ms | cubic-bezier(.22, .61, .36, 1) |
| press | 150ms | OutCubic |
| focus | 即时 | — |
| dialog enter | 180ms | cubic-bezier(.22, .61, .36, 1) |
| dialog exit | 150ms | ease-out |
| tab switch | 200ms | cubic-bezier(.22, .61, .36, 1) |
| page enter | 180ms | cubic-bezier(.22, .61, .36, 1) |

## 9. 无障碍规范

- 所有交互元素必须支持键盘焦点（Tab 键）
- focus-visible 必须有 2px 描边 + 2px offset
- 所有动效必须支持 `prefers-reduced-motion`
- 颜色对比度必须满足 WCAG AA（4.5:1）
- 文字大小不小于 11px

## 10. 实施清单

- [x] design-tokens.css — 统一设计令牌
- [x] voice-studio-main.css — 声腔页样式
- [x] workflow-run-actions.css — 机杼任务按钮
- [x] chapter-studio-parity.css — 章台轨道指示器
- [x] dashboard-page.css — 案头筛选 + 卡片
- [ ] 统一所有页面使用 .surface / .surface--<tone>
- [ ] 替换 .button-primary/secondary 为 .button .button-primary 模式
- [ ] 添加 keyboard navigation 测试
- [ ] 添加 focus-visible 焦点环到所有页面
- [ ] 添加 aria-label 到所有 icon-only 按钮
- [ ] 验证 prefers-reduced-motion 在所有页面
- [ ] 添加视觉回归测试覆盖所有交互状态

## 11. 验证命令

```bash
# 类型检查
cd clients/nimo-desktop && pnpm check

# 单元测试
pnpm test

# 构建验证
pnpm build

# 视觉回归（需要 Tauri 环境）
pnpm visual
```
