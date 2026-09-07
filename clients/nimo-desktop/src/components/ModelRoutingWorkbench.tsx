import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { EngineCommandClient, ModelProfileView, RoutingGroupView, SettingsView, TaskRouteView } from "@nimo/engine-contracts";

import {
  MAX_ACTIVE_FALLBACK_ROUTES,
  applySessionGroupRouteDraft,
  clearSessionGroupRouteOverrides,
  effectivePrimaryProfileId,
  isGenerationProfile,
  isGenerationRouteableProfile,
  isRouteableProfile,
  modelRoutingSessionFromSettings,
  normalizeDefaultProfileId,
  profileDraftFromProfile,
  removeLocalModelProfile,
  reorderFallbackRoute,
  replaceProfileInRouteDrafts,
  sanitizeRouteDraft,
  saveLocalModelProfile,
  setSessionBulkDraft,
  setSessionDefaultProfile,
  setSessionRouteDraft,
  type ModelProfileDraft,
  type ModelRoutingSessionState,
  type RouteDraft,
} from "../lib/model-routing-session";
import { ModelProfileForm } from "./ModelProfileDialog";

type WorkbenchTab = "models" | "routes";
type ModelSessionMode = "add" | "edit" | "delete" | null;

function profileById(profiles: readonly ModelProfileView[], profileId: string): ModelProfileView | undefined {
  return profiles.find((profile) => profile.id === profileId);
}

function profileStatusClass(statusLabel: string): string {
  return isRouteableProfile({ statusLabel } as ModelProfileView) ? "" : "is-unavailable";
}

function emptyProfileDraft(): ModelProfileDraft {
  return { label: "", provider: "openai", model: "", tierLabel: "标准", supportsThinking: false, supportsMultiTurn: true };
}

const EMPTY_ROUTE_DRAFT: RouteDraft = {
  primaryProfileId: "",
  fallbackRoutes: [],
  thinkingEnabled: false,
  multiTurnEnabled: false,
  temperature: null,
};

function fallbackSummary(draft: RouteDraft, profiles: readonly ModelProfileView[]): string {
  if (!draft.primaryProfileId) return "备用路由 0/3";
  const active = draft.fallbackRoutes
    .map((candidate) => profileById(profiles, candidate.profileId))
    .filter((profile): profile is ModelProfileView => profile !== undefined && isGenerationRouteableProfile(profile))
    .slice(0, MAX_ACTIVE_FALLBACK_ROUTES);
  return active.length === 0 ? "备用路由 0/3" : `已配置：${active.map((profile) => profile.label).join(" → ")}`;
}

function CapabilityToggle({ checked, disabled, label, onChange, title }: {
  readonly checked: boolean;
  readonly disabled: boolean;
  readonly label: string;
  readonly onChange: (checked: boolean) => void;
  readonly title: string;
}) {
  return <label title={title}><input checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} type="checkbox" />{label}</label>;
}

function FallbackRouteEditor({ draft, onChange, profiles, supportsMultiTurn }: {
  readonly draft: RouteDraft;
  readonly onChange: (draft: RouteDraft) => void;
  readonly profiles: readonly ModelProfileView[];
  readonly supportsMultiTurn: boolean;
}) {
  const activeRouteable = (draft.primaryProfileId ? draft.fallbackRoutes : []).filter((candidate) => {
    const profile = profileById(profiles, candidate.profileId);
    return profile !== undefined && isGenerationRouteableProfile(profile);
  }).slice(0, MAX_ACTIVE_FALLBACK_ROUTES).map((candidate) => candidate.profileId);
  const updateFallback = (profileId: string, patch: Partial<RouteDraft["fallbackRoutes"][number]>) => {
    onChange(sanitizeRouteDraft({
      ...draft,
      fallbackRoutes: draft.fallbackRoutes.map((candidate) => candidate.profileId === profileId ? { ...candidate, ...patch } : candidate),
    }, profiles, supportsMultiTurn));
  };

  return <details className={`route-fallbacks${activeRouteable.length > 0 ? " has-fallback" : ""}`}>
    <summary title="拖拽排序在 Web 端以可访问的上下移动按钮呈现；仅前 3 个可调用备用模型生效。">{fallbackSummary(draft, profiles)}</summary>
    <div className="route-fallback-panel">
      <header><div><strong>备用路由排序</strong><small>前 3 个可调用候选参与自动切换；能力按各自模型判断。</small></div><span>前 3 生效</span></header>
      <div className="route-fallback-list">
        {draft.fallbackRoutes.map((candidate, index) => {
          const profile = profileById(profiles, candidate.profileId);
          if (profile === undefined) return null;
          const routeable = isGenerationRouteableProfile(profile);
          const activeIndex = activeRouteable.indexOf(candidate.profileId);
          const active = activeIndex >= 0;
          const blockedReason = !routeable ? "该模型尚未配置 API Key，当前不可调用" : !active ? "仅前 3 个可调用备用路由生效" : "";
          return <article className={!routeable ? "is-unavailable" : active ? "is-active" : ""} key={candidate.profileId}>
            <span aria-label={`备用优先级 ${index + 1}`} className="fallback-rank">{index + 1}</span>
            <div className="fallback-profile"><strong>{profile.label}</strong><small>{routeable ? `${profile.provider} · ${profile.tierLabel}` : profile.statusLabel}</small></div>
            <div className="fallback-reorder"><button aria-label={`将 ${profile.label} 上移`} disabled={index === 0} onClick={() => onChange(reorderFallbackRoute(draft, candidate.profileId, "up", profiles, supportsMultiTurn))} type="button">↑</button><button aria-label={`将 ${profile.label} 下移`} disabled={index === draft.fallbackRoutes.length - 1} onClick={() => onChange(reorderFallbackRoute(draft, candidate.profileId, "down", profiles, supportsMultiTurn))} type="button">↓</button></div>
            <div className="fallback-capabilities">
              <CapabilityToggle checked={candidate.thinkingEnabled} disabled={!active || !profile.supportsThinking} label="思" onChange={(thinkingEnabled) => updateFallback(candidate.profileId, { thinkingEnabled })} title={blockedReason || (profile.supportsThinking ? "备用路由：思考模式" : "该模型不支持思考/推理模式")} />
              {supportsMultiTurn && <CapabilityToggle checked={candidate.multiTurnEnabled} disabled={!active || !profile.supportsMultiTurn} label="多" onChange={(multiTurnEnabled) => updateFallback(candidate.profileId, { multiTurnEnabled })} title={blockedReason || (profile.supportsMultiTurn ? "备用路由：多轮模式" : "该模型不支持多轮对话")} />}
            </div>
            <span className="fallback-state">{active ? `#${activeIndex + 1} 生效` : routeable ? "候补" : "不可用"}</span>
          </article>;
        })}
      </div>
    </div>
  </details>;
}

function DefaultModelControl({ defaultProfileId, explicitRouteCount, onChange, profiles }: {
  readonly defaultProfileId: string;
  readonly explicitRouteCount: number;
  readonly onChange: (profileId: string) => void;
  readonly profiles: readonly ModelProfileView[];
}) {
  const generationProfiles = profiles.filter(isGenerationProfile);
  const selected = profileById(generationProfiles, defaultProfileId);
  return <section className="routing-default-model">
    <div><strong>默认文本模型</strong><small>{explicitRouteCount === 0 ? "所有未指定路由的步骤均使用此模型。" : `当前有 ${explicitRouteCount} 个步骤设置了显式主路由；修改默认模型不会覆盖它们。`}</small></div>
    <label><span className="sr-only">默认模型</span><select aria-label="默认模型" onChange={(event) => onChange(event.target.value)} value={selected?.id ?? ""}><option disabled value="">请选择生成模型</option>{generationProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.label}{isRouteableProfile(profile) ? "" : " · 未配置"}</option>)}</select></label>
    <span className={selected === undefined || !isRouteableProfile(selected) ? "default-model-status is-warning" : "default-model-status"}>{selected === undefined ? "尚未设置" : isRouteableProfile(selected) ? "默认通路可用" : "需补 API Key"}</span>
  </section>;
}

const RouteRow = memo(function RouteRow({ defaultProfileId, draft, onRouteChange, profiles, route }: {
  readonly defaultProfileId: string;
  readonly draft: RouteDraft;
  readonly onRouteChange: (routeId: string, draft: RouteDraft, route: TaskRouteView) => void;
  readonly profiles: readonly ModelProfileView[];
  readonly route: TaskRouteView;
}) {
  const generationProfiles = profiles.filter(isGenerationProfile);
  const onChange = (nextDraft: RouteDraft) => onRouteChange(route.id, nextDraft, route);
  const explicitPrimary = profileById(generationProfiles, draft.primaryProfileId);
  const resolvedPrimary = profileById(generationProfiles, effectivePrimaryProfileId(draft, defaultProfileId));
  const inheritsDefault = draft.primaryProfileId.length === 0;
  const updatePrimary = (primaryProfileId: string) => onChange(sanitizeRouteDraft({ ...draft, primaryProfileId }, generationProfiles, route.supportsMultiTurn));
  const updateTemperature = (temperature: number) => onChange(sanitizeRouteDraft({ ...draft, temperature }, generationProfiles, route.supportsMultiTurn));
  const capabilityTitle = inheritsDefault ? "此任务继承默认模型；请先单独指定主路由后再覆盖能力。" : explicitPrimary?.supportsThinking ? "启用深度推理模式" : "当前主模型不支持思考/推理模式";
  const multiTurnTitle = inheritsDefault ? "此任务继承默认模型；请先单独指定主路由后再覆盖能力。" : !route.supportsMultiTurn ? "该任务不会注入多轮上下文" : !explicitPrimary?.supportsMultiTurn ? "当前主模型不支持多轮对话" : "启用多轮对话上下文注入";

  return <article className={inheritsDefault ? "route-row is-inherited" : "route-row"}>
    <div className="route-task-copy"><div><strong>{route.label}</strong>{route.cadenceLabel !== undefined && <span className="route-cadence" title="仅在火候页开启灰区仲裁且命中灰区时触发">{route.cadenceLabel}</span>}</div><small title={route.hint}>{route.hint}</small></div>
    <label className="route-primary"><span className="sr-only">{route.label} 主路由</span><select aria-label={`${route.label} 主路由`} onChange={(event) => updatePrimary(event.target.value)} value={draft.primaryProfileId}><option value="">（未指定 · 继承默认模型）</option>{generationProfiles.map((profile) => <option disabled={!isRouteableProfile(profile)} key={profile.id} value={profile.id}>{profile.label}{isRouteableProfile(profile) ? "" : " · 未配置"}</option>)}</select></label>
    <div className="route-capabilities"><CapabilityToggle checked={draft.thinkingEnabled} disabled={inheritsDefault || !explicitPrimary?.supportsThinking} label="思考" onChange={(thinkingEnabled) => onChange({ ...draft, thinkingEnabled })} title={capabilityTitle} />{route.supportsMultiTurn && <CapabilityToggle checked={draft.multiTurnEnabled} disabled={inheritsDefault || !explicitPrimary?.supportsMultiTurn} label="多轮" onChange={(multiTurnEnabled) => onChange({ ...draft, multiTurnEnabled })} title={multiTurnTitle} />}</div>
    <FallbackRouteEditor draft={draft} onChange={onChange} profiles={generationProfiles} supportsMultiTurn={route.supportsMultiTurn} />
    {route.temperature === null ? <span className="route-temperature-spacer" /> : <label className="route-temperature" title={route.temperatureKind === "fixed" ? "固定温度；此步骤不使用创意火候浮动" : "基础火候；适用范围包含此步骤时会围绕此值随机"}><span>温</span><input aria-label={`${route.label} 温度`} max="2" min="0" onChange={(event) => updateTemperature(Number(event.target.value))} step="0.01" type="number" value={draft.temperature ?? 0} /></label>}
    <span className="route-tier" title={`${route.taskKey}${inheritsDefault ? ` · 继承 ${resolvedPrimary?.label ?? "未设置"}` : ""}`}>{inheritsDefault ? `继承 · ${resolvedPrimary?.tierLabel ?? "未设置"}` : explicitPrimary?.tierLabel ?? "未分配"}</span>
  </article>;
});

function GroupBulkRow({ bulkKey, draft, group, label, onApply, onChange, onInheritDefault, profiles, routeIds }: {
  readonly bulkKey: string;
  readonly draft: RouteDraft;
  readonly group: RoutingGroupView;
  readonly label: string;
  readonly onApply: (draft: RouteDraft, routeIds: readonly string[]) => void;
  readonly onChange: (draft: RouteDraft) => void;
  readonly onInheritDefault: (routeIds: readonly string[]) => void;
  readonly profiles: readonly ModelProfileView[];
  readonly routeIds: readonly string[];
}) {
  const generationProfiles = profiles.filter(isGenerationProfile);
  const primary = profileById(generationProfiles, draft.primaryProfileId);
  const supportsMultiTurn = routeIds.some((routeId) => group.routes.find((route) => route.id === routeId)?.supportsMultiTurn);
  const supportsTemperature = routeIds.some((routeId) => group.routes.find((route) => route.id === routeId)?.temperature !== null);
  const updatePrimary = (primaryProfileId: string) => onChange(sanitizeRouteDraft({ ...draft, primaryProfileId }, generationProfiles, supportsMultiTurn));
  const updateTemperature = (value: string) => onChange(sanitizeRouteDraft({
    ...draft,
    temperature: value === "" ? null : Number(value),
  }, generationProfiles, supportsMultiTurn));
  const canApply = draft.primaryProfileId.length > 0 || draft.temperature !== null;
  return <div className="route-group-bulk" data-bulk-key={bulkKey}>
    <div className="route-group-copy"><strong>{label}</strong><small>仅文本生成模型可用。统一主路由会覆盖本组；温度留空则保持任务级设置。</small></div>
    <select aria-label={`${label} 统一主路由`} onChange={(event) => updatePrimary(event.target.value)} value={draft.primaryProfileId}><option value="">（选择文本模型）</option>{generationProfiles.map((profile) => <option disabled={!isRouteableProfile(profile)} key={profile.id} value={profile.id}>{profile.label}</option>)}</select>
    <div className="route-group-capabilities"><CapabilityToggle checked={draft.thinkingEnabled} disabled={!primary?.supportsThinking} label="思考" onChange={(thinkingEnabled) => onChange({ ...draft, thinkingEnabled })} title={primary?.supportsThinking ? "批量启用主路由思考" : "当前主模型不支持思考/推理模式"} />
    {supportsMultiTurn && <CapabilityToggle checked={draft.multiTurnEnabled} disabled={!primary?.supportsMultiTurn} label="多轮" onChange={(multiTurnEnabled) => onChange({ ...draft, multiTurnEnabled })} title={primary?.supportsMultiTurn ? "批量启用多轮上下文" : "当前主模型不支持多轮对话"} />}</div>
    <FallbackRouteEditor draft={draft} onChange={onChange} profiles={generationProfiles} supportsMultiTurn={supportsMultiTurn} />
    {supportsTemperature && <label className="route-temperature route-group-temperature" title="留空时保留各步骤的温度；填写后会统一应用到本组可设置温度的步骤。"><span>温</span><input aria-label={`${label} 统一温度`} max="2" min="0" onChange={(event) => updateTemperature(event.target.value)} placeholder="不变" step="0.01" type="number" value={draft.temperature ?? ""} /></label>}
    <button className="button button-primary route-group-apply" disabled={!canApply} onClick={() => onApply(sanitizeRouteDraft(draft, generationProfiles, supportsMultiTurn), routeIds)} type="button">应用本组</button>
    <button className="button button-secondary route-group-inherit" onClick={() => onInheritDefault(routeIds)} title="清除本组显式主路由和备用链，改用上方默认文本模型" type="button">改用默认模型</button>
  </div>;
}

function RoutingMatrix({ onApply, onBulkDraftChange, onInheritDefault, onRouteChange, profiles, session, settings }: {
  readonly onApply: (bulkKey: string, group: RoutingGroupView, draft: RouteDraft, routeIds: readonly string[]) => void;
  readonly onBulkDraftChange: (bulkKey: string, draft: RouteDraft, supportsMultiTurn: boolean) => void;
  readonly onInheritDefault: (routeIds: readonly string[]) => void;
  readonly onRouteChange: (routeId: string, draft: RouteDraft, route: TaskRouteView) => void;
  readonly profiles: readonly ModelProfileView[];
  readonly session: ModelRoutingSessionState;
  readonly settings: SettingsView;
}) {
  // PySide opens its routing QTabWidget on the short-story flow. Keep that
  // deterministic baseline even if a transport returns routing groups in a
  // different order while its view is loading.
  const firstGroupId = settings.routingGroups.find(
    (group) => group.id === "短篇流程" || group.label === "短篇流程",
  )?.id
    ?? settings.routingGroups[0]?.id
    ?? "";
  const groupSignature = settings.routingGroups.map((group) => group.id).join("\u0001");
  const [activeGroup, setActiveGroup] = useState(() => ({ id: firstGroupId, signature: groupSignature }));
  const [activeSubgroups, setActiveSubgroups] = useState<Readonly<Record<string, string>>>({});
  useEffect(() => {
    setActiveGroup((current) => current.signature === groupSignature
      ? current
      : { id: firstGroupId, signature: groupSignature });
    setActiveSubgroups({});
  }, [firstGroupId, groupSignature]);
  const selectedGroup = settings.routingGroups.find((group) => group.id === activeGroup.id) ?? settings.routingGroups[0];
  if (selectedGroup === undefined) return <p className="routing-empty">尚未收到可编辑的流程路由视图。</p>;
  const activeSubgroupId = activeSubgroups[selectedGroup.id] ?? selectedGroup.subgroups[0]?.id ?? "";
  const activeSubgroup = selectedGroup.subgroups.find((subgroup) => subgroup.id === activeSubgroupId);
  const visibleRouteIds = activeSubgroup?.routeIds ?? selectedGroup.routes.map((route) => route.id);
  const visibleRoutes = selectedGroup.routes.filter((route) => visibleRouteIds.includes(route.id));
  const groupBulkKey = `group:${selectedGroup.id}`;
  const subgroupBulkKey = activeSubgroup === undefined ? "" : `subgroup:${selectedGroup.id}:${activeSubgroup.id}`;
  const groupSupportsMultiTurn = selectedGroup.routes.some((route) => route.supportsMultiTurn);
  const subgroupSupportsMultiTurn = visibleRoutes.some((route) => route.supportsMultiTurn);
  return <div className="routing-matrix">
    <div aria-label="一级流程分类" className="routing-group-tabs" role="tablist">{settings.routingGroups.map((group) => <button aria-selected={group.id === selectedGroup.id} className={group.id === selectedGroup.id ? "is-active" : ""} key={group.id} onClick={() => setActiveGroup((current) => ({ ...current, id: group.id }))} role="tab" title={group.description} type="button">{group.label}</button>)}</div>
    <section className="routing-group-panel">
      <header><div><span>{selectedGroup.icon ?? "✦"}</span><h3>{selectedGroup.label}</h3></div><p>{selectedGroup.description}</p></header>
      <GroupBulkRow bulkKey={groupBulkKey} draft={session.bulkDrafts[groupBulkKey] ?? EMPTY_ROUTE_DRAFT} group={selectedGroup} label="全组统一设定" onApply={(draft, routeIds) => onApply(groupBulkKey, selectedGroup, draft, routeIds)} onChange={(draft) => onBulkDraftChange(groupBulkKey, draft, groupSupportsMultiTurn)} onInheritDefault={onInheritDefault} profiles={profiles} routeIds={selectedGroup.routes.map((route) => route.id)} />
      {selectedGroup.subgroups.length > 0 && <div aria-label={`${selectedGroup.label} 二级阶段分类`} className="routing-subgroup-tabs" role="tablist">{selectedGroup.subgroups.map((subgroup) => <button aria-selected={subgroup.id === activeSubgroupId} className={subgroup.id === activeSubgroupId ? "is-active" : ""} key={subgroup.id} onClick={() => setActiveSubgroups((current) => ({ ...current, [selectedGroup.id]: subgroup.id }))} role="tab" title={subgroup.description} type="button">{subgroup.label}</button>)}</div>}
      {activeSubgroup !== undefined && <><p className="routing-subgroup-description">{activeSubgroup.description}</p><GroupBulkRow bulkKey={subgroupBulkKey} draft={session.bulkDrafts[subgroupBulkKey] ?? EMPTY_ROUTE_DRAFT} group={selectedGroup} label={`${activeSubgroup.label} 批量设定`} onApply={(draft, routeIds) => onApply(subgroupBulkKey, selectedGroup, draft, routeIds)} onChange={(draft) => onBulkDraftChange(subgroupBulkKey, draft, subgroupSupportsMultiTurn)} onInheritDefault={onInheritDefault} profiles={profiles} routeIds={visibleRouteIds} /></>}
      <div className="routing-route-list">{visibleRoutes.map((route) => <RouteRow defaultProfileId={session.defaultProfileId} draft={session.drafts[route.id] ?? EMPTY_ROUTE_DRAFT} key={route.id} onRouteChange={onRouteChange} profiles={profiles} route={route} />)}</div>
    </section>
  </div>;
}

function rewriteDraftReferences(draft: RouteDraft, previousId: string, nextProfile: ModelProfileView, profiles: readonly ModelProfileView[], supportsMultiTurn: boolean): RouteDraft {
  return sanitizeRouteDraft({
    ...draft,
    primaryProfileId: draft.primaryProfileId === previousId ? nextProfile.id : draft.primaryProfileId,
    fallbackRoutes: draft.fallbackRoutes.map((candidate) => candidate.profileId === previousId ? { ...candidate, profileId: nextProfile.id } : candidate),
  }, profiles, supportsMultiTurn);
}

/** Shared registry/route session, persisted by SettingsPage on save. */
export function ModelRoutingWorkbench({ commandClient, embedded = false, initialTab = "models", onRouteChange, onSessionChange, session, settings, surface = "both" }: {
  readonly commandClient?: EngineCommandClient;
  readonly embedded?: boolean;
  readonly initialTab?: WorkbenchTab;
  readonly onRouteChange?: (change: ModelRoutingChange) => void;
  readonly onSessionChange?: (session: ModelRoutingSessionState) => void;
  readonly session?: ModelRoutingSessionState;
  readonly settings: SettingsView;
  /** Fixed surfaces avoid exposing the same route editor from two entry points. */
  readonly surface?: WorkbenchTab | "both";
}) {
  const [ownedSession, setOwnedSession] = useState<ModelRoutingSessionState>(() => modelRoutingSessionFromSettings(settings));
  const currentSession = session ?? ownedSession;
  const [ownedTab, setTab] = useState<WorkbenchTab>(initialTab);
  const tab: WorkbenchTab = surface === "both" ? ownedTab : surface;
  const [selectedProfileId, setSelectedProfileId] = useState(currentSession.profiles[0]?.id ?? "");
  const [sessionMode, setSessionMode] = useState<ModelSessionMode>(null);
  const [notice, setNotice] = useState("路由、模型档案与凭据变更将在点击页面“保存设置”后写入本地引擎；密钥不会进入浏览器存储。");
  const [editorError, setEditorError] = useState("");
  const [testingProfileId, setTestingProfileId] = useState("");
  const selected = useMemo(() => profileById(currentSession.profiles, selectedProfileId), [currentSession.profiles, selectedProfileId]);
  const routeSupportsMultiTurnById = useMemo(() => Object.fromEntries(settings.routingGroups.flatMap((group) => group.routes.map((route) => [route.id, route.supportsMultiTurn]))), [settings]);
  const currentSessionRef = useRef(currentSession);
  const controlledSessionRef = useRef(session);
  const onRouteChangeRef = useRef(onRouteChange);
  const onSessionChangeRef = useRef(onSessionChange);
  currentSessionRef.current = currentSession;
  controlledSessionRef.current = session;
  onRouteChangeRef.current = onRouteChange;
  onSessionChangeRef.current = onSessionChange;
  const commit = useCallback((next: ModelRoutingSessionState, nextNotice: string) => {
    if (controlledSessionRef.current === undefined) setOwnedSession(next);
    onSessionChangeRef.current?.(next);
    setNotice(nextNotice);
  }, []);
  const updateRoute = useCallback((routeId: string, draft: RouteDraft, route: TaskRouteView) => {
    const next = setSessionRouteDraft(currentSessionRef.current, routeId, draft, route.supportsMultiTurn);
    commit(next, "当前任务路由草案已更新；点击页面“保存设置”即可提交到本地引擎。");
    onRouteChangeRef.current?.({ routeId, draft: next.drafts[routeId]! });
  }, [commit]);
  const updateBulkDraft = useCallback((bulkKey: string, draft: RouteDraft, supportsMultiTurn: boolean) => {
    commit(setSessionBulkDraft(currentSessionRef.current, bulkKey, draft, supportsMultiTurn), "批量路由草案已更新；应用后会同时带入备用链，等待页面“保存设置”提交。");
  }, [commit]);
  const applyGroup = useCallback((bulkKey: string, group: RoutingGroupView, draft: RouteDraft, routeIds: readonly string[]) => {
    const next = applySessionGroupRouteDraft(currentSessionRef.current, routeIds, draft, routeSupportsMultiTurnById, bulkKey);
    commit(next, `已将「${group.label}」的 ${routeIds.length} 个步骤更新为统一主路由、备用链与能力草案；${draft.temperature === null ? "温度保持任务级设置" : "温度已同步到可设置的步骤"}，点击“保存设置”后生效。`);
    for (const routeId of routeIds) onRouteChangeRef.current?.({ routeId, draft: next.drafts[routeId]! });
  }, [commit, routeSupportsMultiTurnById]);
  const inheritDefaultForGroup = useCallback((routeIds: readonly string[]) => {
    const next = clearSessionGroupRouteOverrides(
      currentSessionRef.current,
      routeIds,
      routeSupportsMultiTurnById,
    );
    commit(next, `已清除 ${routeIds.length} 个步骤的显式主路由与备用链；它们将在保存后继承默认文本模型。`);
    for (const routeId of routeIds) onRouteChangeRef.current?.({ routeId, draft: next.drafts[routeId]! });
  }, [commit, routeSupportsMultiTurnById]);
  const updateDefaultProfile = useCallback((profileId: string) => {
    commit(setSessionDefaultProfile(currentSessionRef.current, profileId), "默认模型草案已更新；未指定的任务将在保存后继承它。");
  }, [commit]);
  const saveProfile = (draft: ModelProfileDraft, connection: { readonly apiKey: string; readonly apiKeyAction: "preserve" | "replace" | "clear"; readonly baseUrl: string }) => {
    try {
      const result = saveLocalModelProfile(currentSession.profiles, draft, sessionMode === "edit" ? selected?.id : undefined);
      const replace = result.previousId !== undefined && result.previousId !== result.profile.id;
      const drafts = replace ? replaceProfileInRouteDrafts(currentSession.drafts, result.previousId!, result.profile, result.profiles, routeSupportsMultiTurnById) : Object.fromEntries(Object.entries(currentSession.drafts).map(([routeId, routeDraft]) => [routeId, sanitizeRouteDraft(routeDraft, result.profiles, routeSupportsMultiTurnById[routeId] ?? true)]));
      const bulkDrafts = Object.fromEntries(Object.entries(currentSession.bulkDrafts).map(([bulkKey, bulkDraft]) => [bulkKey, replace ? rewriteDraftReferences(bulkDraft, result.previousId!, result.profile, result.profiles, true) : sanitizeRouteDraft(bulkDraft, result.profiles, true)]));
      const connectionDrafts = { ...currentSession.connectionDrafts };
      if (result.previousId !== undefined) delete connectionDrafts[result.previousId];
      connectionDrafts[result.profile.id] = { ...connection, previousId: result.previousId };
      const profiles = result.profiles.map((profile) => profile.id === result.profile.id ? {
        ...profile,
        baseUrl: connection.baseUrl,
        keyConfigured: connection.apiKeyAction === "replace"
          ? true
          : connection.apiKeyAction === "clear"
            ? profile.provider === "ollama"
            : selected?.keyConfigured ?? false,
        maskedKey: connection.apiKeyAction === "replace" ? "待安全保存" : connection.apiKeyAction === "clear" ? "(未配置)" : selected?.maskedKey,
        isEmbedding: /(?:^|[-_/])embed(?:ding)?(?:[-_/]|$)/i.test(profile.model),
      } : profile);
      const next = { ...currentSession, profiles, connectionDrafts, drafts, bulkDrafts, defaultProfileId: normalizeDefaultProfileId(currentSession.defaultProfileId === result.previousId ? result.profile.id : currentSession.defaultProfileId, profiles) };
      commit(next, `已${result.previousId === undefined ? "添加" : "更新"}「${result.profile.label}」草案；点击页面“保存设置”后写入模型配置。`);
      setSelectedProfileId(result.profile.id);
      setSessionMode(null);
      setEditorError("");
    } catch (error) {
      setEditorError(error instanceof Error ? error.message : "无法保存模型草案。");
    }
  };
  const deleteSelected = () => {
    if (selected === undefined) return;
    const result = removeLocalModelProfile(currentSession.profiles, currentSession.drafts, selected.id, routeSupportsMultiTurnById, currentSession.defaultProfileId);
    const bulkDrafts = Object.fromEntries(Object.entries(currentSession.bulkDrafts).map(([bulkKey, bulkDraft]) => [bulkKey, sanitizeRouteDraft({ ...bulkDraft, primaryProfileId: bulkDraft.primaryProfileId === selected.id ? result.defaultProfileId : bulkDraft.primaryProfileId, fallbackRoutes: bulkDraft.fallbackRoutes.filter((candidate) => candidate.profileId !== selected.id) }, result.profiles, true)]));
    const connectionDrafts = { ...currentSession.connectionDrafts };
    delete connectionDrafts[selected.id];
    commit({ ...currentSession, ...result, connectionDrafts, bulkDrafts }, `已标记移除「${selected.label}」，相关路由已回退；点击页面“保存设置”后会同步清理其受保护凭据。`);
    setSelectedProfileId(result.profiles[0]?.id ?? "");
    setSessionMode(null);
  };
  const testSelected = async () => {
    if (selected === undefined || commandClient === undefined) {
      setNotice("当前演示适配器不支持真实模型探活。");
      return;
    }
    const connection = currentSession.connectionDrafts[selected.id];
    setTestingProfileId(selected.id);
    setNotice(`正在连接「${selected.label}」…`);
    try {
      const result = await commandClient.testModelProfile({
        kind: "test_model_profile",
        id: selected.id,
        previousId: connection?.previousId,
        provider: selected.provider,
        model: selected.model,
        apiKeyAction: connection?.apiKeyAction ?? "preserve",
        apiKey: connection?.apiKey,
        baseUrl: connection?.baseUrl ?? selected.baseUrl ?? "",
      });
      const next = {
        ...currentSession,
        profiles: currentSession.profiles.map((profile) => profile.id === selected.id ? {
          ...profile,
          statusLabel: result.ok ? result.detail : `异常 · ${result.detail}`,
          supportsThinking: result.ok ? result.supportsThinking : profile.supportsThinking,
          supportsMultiTurn: result.ok ? result.supportsMultiTurn : profile.supportsMultiTurn,
        } : profile),
      };
      commit(next, result.ok ? `「${selected.label}」${result.detail}` : `「${selected.label}」连接失败：${result.detail}`);
    } catch (error) {
      setNotice(`「${selected.label}」连接失败：${error instanceof Error ? error.message : "本地引擎无响应"}`);
    } finally {
      setTestingProfileId("");
    }
  };
  const explicitRouteCount = Object.values(currentSession.drafts).filter(
    (draft) => draft.primaryProfileId.length > 0,
  ).length;

  return <div className={`model-routing-workbench${embedded ? " is-embedded" : ""}`}>
    {surface === "both" && <div aria-label="模型与流程设置" className="model-routing-tabs" role="tablist"><button aria-selected={tab === "models"} className={tab === "models" ? "is-active" : ""} onClick={() => setTab("models")} role="tab" type="button">模型管理</button><button aria-selected={tab === "routes"} className={tab === "routes" ? "is-active" : ""} onClick={() => setTab("routes")} role="tab" type="button">流程路由</button></div>}
    <DefaultModelControl defaultProfileId={currentSession.defaultProfileId} explicitRouteCount={explicitRouteCount} onChange={updateDefaultProfile} profiles={currentSession.profiles} />
    {tab === "models" ? <div className="model-registry"><aside><button className="model-registry-add" onClick={() => { setEditorError(""); setSessionMode("add"); }} type="button">＋ 添加模型</button>{currentSession.profiles.map((profile) => <button className={selectedProfileId === profile.id ? "is-active" : ""} key={profile.id} onClick={() => { setSelectedProfileId(profile.id); setSessionMode(null); }} type="button"><span className={profileStatusClass(profile.statusLabel)}>{profile.statusLabel}</span><strong>{profile.label}</strong><small>{profile.provider} · {profile.model}</small></button>)}</aside><article>{sessionMode === "add" || sessionMode === "edit" ? <><ModelProfileForm initialApiKey={sessionMode === "edit" && selected !== undefined ? currentSession.connectionDrafts[selected.id]?.apiKey : ""} initialBaseUrl={sessionMode === "edit" && selected !== undefined ? currentSession.connectionDrafts[selected.id]?.baseUrl ?? selected.baseUrl : ""} initialDraft={sessionMode === "edit" && selected !== undefined ? profileDraftFromProfile(selected) : emptyProfileDraft()} initialKeyConfigured={sessionMode === "edit" && selected !== undefined && (currentSession.connectionDrafts[selected.id]?.apiKeyAction === "replace" || Boolean(selected.keyConfigured))} mode={sessionMode} onCancel={() => { setSessionMode(null); setEditorError(""); }} onSave={saveProfile} providerOptions={settings.modelProviderOptions} />{editorError && <p className="model-editor-error" role="alert">{editorError}</p>}</> : sessionMode === "delete" && selected !== undefined ? <section className="model-remove-confirm"><span className="section-kicker">确认删除</span><h3>删除「{selected.label}」？</h3><p>使用该模型的流程路由会被清除，并回退到当前默认模型。点击页面“保存设置”后会同时删除该档案及其专属凭据。</p><footer><button className="button button-secondary" onClick={() => setSessionMode(null)} type="button">取消</button><button className="button button-danger" onClick={deleteSelected} type="button">确认删除</button></footer></section> : selected === undefined ? <p>尚未选择模型。</p> : <><header><div><span className="section-kicker">模型档案</span><h3>{selected.label}</h3><p>{selected.provider} · {selected.model}</p></div><span className={`model-status ${profileStatusClass(selected.statusLabel)}`.trim()}>{selected.statusLabel}</span></header><dl><div><dt>质量层级</dt><dd>{selected.tierLabel}</dd></div><div><dt>模型类型</dt><dd>{selected.isEmbedding ? "嵌入模型" : "生成模型"}</dd></div><div><dt>密钥</dt><dd>{selected.maskedKey ?? (selected.keyConfigured ? "已配置" : "(未配置)")}</dd></div><div><dt>接口地址</dt><dd>{selected.baseUrl || "供应商默认"}</dd></div><div><dt>思考模式</dt><dd>{selected.supportsThinking ? "支持" : "不支持"}</dd></div><div><dt>多轮上下文</dt><dd>{selected.supportsMultiTurn ? "支持" : "不支持"}</dd></div></dl><div className="model-registry-actions"><button className="button button-secondary" disabled={testingProfileId === selected.id} onClick={() => void testSelected()} type="button">{testingProfileId === selected.id ? "测试中…" : "测试连接"}</button><button className="button button-secondary" onClick={() => { setEditorError(""); setSessionMode("edit"); }} type="button">编辑</button><button className="button button-danger" onClick={() => setSessionMode("delete")} type="button">删除</button></div><p className="model-boundary">密钥仅在当前编辑会话中暂存，保存后由本地引擎写入受保护的 .env；界面只会重新读取遮掩值。</p></>}</article></div> : <RoutingMatrix onApply={applyGroup} onBulkDraftChange={updateBulkDraft} onInheritDefault={inheritDefaultForGroup} onRouteChange={updateRoute} profiles={currentSession.profiles} session={currentSession} settings={settings} />}
    <p aria-live="polite" className="model-routing-notice">{notice}</p>
  </div>;
}

export interface ModelRoutingChange { readonly routeId: string; readonly draft: RouteDraft; }
