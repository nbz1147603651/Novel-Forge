# 火候页连接检测：性能边界与演进约定

## 结论

“检测全部通路”不是火候页的初始化工作，而是一个用户显式发起的后台
诊断作业。新 UI 的页面切换只能读取脱敏的 `SettingsView` 快照；它绝不
因为页面挂载、重新渲染、主题切换或打开路由工作台而探测 Provider。

这条边界同时消除两类卡顿：切页时的 UI 构建/布局峰值，以及多模型探测
完成后触发整页和路由矩阵重建的峰值。

## 已确认的旧端触发链

当前 PySide6 实现中，按钮调用
`ModelManagementMixin._test_all_connections()`，再以 `force_refresh=True`
进入 `_auto_test_connections()`。这次调用会：

1. 同步确保模型状态网格已构建；
2. 清除每个模型的检测缓存和能力缓存；
3. 取消/作废正在运行批次的令牌，再重新填充整个检测队列；
4. 在每张状态卡完成时更新 UI；批次结束后再刷新路由下拉框。

旧端为避免共享 Provider/API Key 的限流，自动探测为 **1** 个 worker，
手动“检测全部通路”为 **2** 个 worker（不是三路并发）。这说明瓶颈是
“强制失效 + 多处 UI 更新”的组合，而非单个 HTTP 请求。

## Phase 1：现有 React 壳的硬边界

- 火候页仅懒加载界面代码和一次 `SettingsView`；进入页面不创建检测 run。
- 模型管理、流程路由矩阵都按用户动作动态导入，不能占用火候页首屏。
- 检测卡状态是独立纯状态机：`idle → queued → checking → succeeded | failed`；
  未配置档案保持 `unavailable`，不进入队列。
- `ConnectivityController` 由应用壳持有，而不是由火候页面持有定时器。用户
  离开火候时页面订阅自然卸载，但已明确发起的本地演练继续；返回后复用同一
  快照，不会重置队列或再次启动检测。
- 模拟完成事件在控制器中以 `requestAnimationFrame` 合批：点击后的首次队列
  状态同步显示，随后同一帧内的多个完成结果只触发一次订阅通知。
- 本地演练遵守旧端手动检测的 **2** 路上限，不读取密钥、不发网络请求、
  不写 `model_profiles.json`。
- 卡片列表初始只挂载前 12 个档案；超过该数量时用户显式展开其余档案。
  活跃检测以一行简要、可访问的状态播报呈现，而不是把整个卡片网格放进
  `aria-live` 区域反复宣读。

这层实现的职责只是在第一阶段复刻状态与交互，不应演变成真正的
Provider 检测器。

## Phase 2：EngineClient 诊断作业合约

检测应由本地引擎（桌面）或云端控制面（Web）持有；UI 只订阅事件并渲染
脱敏结果。建议扩展 `EngineClient`，而不是让设置组件直接调用 Provider：

```ts
type ConnectivityCheckScope = "configured-profiles" | "selected-profiles";

type ConnectivityCheckEvent = {
  runId: string;
  configRevision: string;
  profileId?: string;
  state: "queued" | "checking" | "succeeded" | "failed" | "unavailable" | "completed";
  latencyMs?: number;
  capabilities?: { thinking: boolean; multiTurn: boolean };
  message?: string; // 已脱敏，绝不含 endpoint、key 或原始异常
};

interface EngineClient {
  startConnectivityCheck(input: {
    scope: ConnectivityCheckScope;
    profileIds?: readonly string[];
    configRevision: string;
    forceRefresh: boolean;
  }): Promise<{ runId: string; configRevision: string }>;
  subscribeConnectivityCheck(runId: string, listener: (event: ConnectivityCheckEvent) => void): () => void;
  getConnectivitySnapshot(configRevision: string): Promise<readonly ConnectivityCheckEvent[]>;
  cancelConnectivityCheck(runId: string): Promise<void>;
}
```

引擎侧规则：

- 按工作区/租户限制全局并发为 2；同一 Provider 或同一凭据指纹再限制为 1。
  未来如有可靠的 Provider 配额信息，才允许针对特定 Provider 提高上限。
- 检测 run 去重键为 `workspaceId + configRevision + scope + profileIds`。相同
  请求附着到现有 run，不重复探测。
- 结果以 `profileId + configRevision` 缓存；成功结果可短期复用，失败结果使用
  更短 TTL。强制检测只能让结果标为“正在刷新”，不能先清空旧的可用状态。
- 单探测、整批和排队均有超时；超时视为该档案失败，绝不阻塞同批其余档案。
- 用户明确取消时取消 run。单纯离开火候页只取消 UI 订阅，作业继续作为全局
  后台任务运行；返回页面时用 `runId + configRevision` 恢复快照，避免重新检测。
- 配置保存/路由变更会产生新的 `configRevision`。任何旧 revision 或旧 runId
  的迟到事件都被引擎和 UI 丢弃，不能覆盖最新状态。

## UI 渲染和状态归属

```text
火候页面 ──读取──> SettingsView + 最后诊断快照
   │ 用户点击
   ▼
ConnectivityController ──命令──> EngineClient / 后台诊断作业
   ▲                                      │
   └──── rAF 合批后的脱敏事件 ─────────────┘
```

- `ConnectivityController` 是应用级会话/外部 store，不放在火候页面的
  `useEffect` 中，也不持有 DOM 计时器。
- 事件在一个 `requestAnimationFrame` 内合批提交，最多一帧一次 React 状态更新；
  不为每张卡单独 `setState`。
- 路由矩阵只读已完成的快照。它在用户打开工作台后才挂载，检测完成时只更新
  受影响的档案选项，不重建火候页、主题树或整张矩阵。
- 超过 12 个档案采用渐进渲染；超过约 40 个时改用虚拟列表。状态摘要仍显示
  总量、活跃量和失败量，因此未挂载卡片也不会“失联”。

## 可验证的体验预算

| 场景 | 必须满足的行为 | 验证 |
| --- | --- | --- |
| 冷/热切换到火候 | 不发检测命令、不创建路由矩阵、不重置已有诊断快照 | 路由回归 + 网络/EngineClient spy |
| 点击检测 | 同一交互帧显示队列状态；最多 2 个档案进入 `checking` | 状态机单测 + 交互回归 |
| 大量模型档案 | 初始最多 12 张卡挂载；余项不参与首屏布局 | 组件回归 + React Profiler |
| 事件高频到达 | 每动画帧至多一次 UI 提交；不会重建路由工作台 | `connectivity-controller.test.ts` 帧合批测试 + Phase 2 trace |
| 离开/返回页面 | 断开订阅、保留用户发起的 run；返回后从快照恢复 | `settings-connectivity.spec.ts` 路由回归 |
| 配置变更 | 新 revision 生效，旧事件被丢弃 | EngineClient 合约测试 |

Phase 1 结束前，需要保留火候页的冷、热切换 trace、检测成功状态的同夹具
截图，以及上述状态机测试。达到这些预算后，才将该 surface 从 `foundation`
提升为视觉和性能均可接受的对等项。
