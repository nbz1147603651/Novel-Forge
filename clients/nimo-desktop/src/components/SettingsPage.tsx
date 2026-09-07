import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import type { ChapterRuntimePolicyCommand, EngineClient, EngineCommandClient, FontPreferencesView, SaveSettingsCommand, SaveSettingsResult, SettingsView } from "@nimo/engine-contracts";

import { ConnectivityController } from "../lib/connectivity-controller";
import {
  modelRoutingSessionFromSettings,
  normalizeDefaultProfileId,
  sanitizeRouteDraft,
  type ModelRoutingSessionState,
  type RouteDraft,
} from "../lib/model-routing-session";
import type { SettingsDialogFixture } from "../lib/parity-fixture";
import { withoutVoiceTextRoutes } from "../lib/voice-text-routing";
import { desktopThemes, type ThemeId } from "../theme/theme";
import type { ThemeContrast, ThemeMode } from "../theme/ThemeProvider";
import {
  defaultFontPreferences,
  fontScaleChoices,
  normalizeFontPreferences,
  readingFontChoices,
  uiFontChoices,
} from "../theme/font-preferences";
import { useT, type Locale } from "../lib/i18n";
import { buildSettingsTransferPayload, parseSettingsTransferPayload } from "../lib/settings-transfer";
import { AppDialog } from "./AppDialog";
import { ChapterRuntimePolicyCard } from "./ChapterRuntimePolicyCard";
import { CreationParameterSections } from "./CreationParameterSections";
import { CreativeTemperatureSection, defaultCreativeTemperatureDraft, isCreativeTemperatureDraftValid, isSameCreativeTemperatureDraft, type CreativeTemperatureDraft } from "./CreativeTemperatureSection";
import { ErrorArchivePanel } from "./ErrorArchivePanel";
import { ModelConnectionStatus } from "./ModelConnectionStatus";
import { ModelProfileDialog } from "./ModelProfileDialog";
import { OllamaModelPanel } from "./OllamaModelPanel";

const LazyModelRoutingWorkbench = lazy(async () => {
  const module = await import("./ModelRoutingWorkbench");
  return { default: module.ModelRoutingWorkbench };
});

type SettingsDialog = "models";

async function allowSaveStateToPaint(): Promise<void> {
  await new Promise<void>((resolve) => {
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => resolve());
      return;
    }
    setTimeout(resolve, 0);
  });
}

export interface SettingsPageProps {
  readonly archiveClient: Pick<EngineClient, "getErrorArchiveSummary">;
  readonly commandClient: EngineCommandClient;
  readonly engineClient: Pick<EngineClient, "getOllama">;
  readonly connectivityController: ConnectivityController;
  readonly initialProfileDialogFixture?: SettingsDialogFixture | null;
  readonly initialSection?: "creative-temperature" | "model-routing" | null;
  readonly locale: Locale;
  readonly onDirtyChange: (dirty: boolean) => void;
  /** Reload the Engine snapshot after a successful persisted settings write. */
  readonly onSettingsPersisted: () => Promise<SettingsView>;
  readonly onLocaleChange: (locale: Locale) => void;
  readonly onThemeChange: (theme: ThemeId) => void;
  readonly onThemeContrastChange: (contrast: ThemeContrast) => void;
  readonly onThemeModeChange: (mode: ThemeMode) => void;
  readonly onFontPreferencesChange: (preferences: FontPreferencesView) => void;
  readonly onPetVisibleChange?: (visible: boolean) => void;
  readonly petVisible?: boolean;
  readonly saveRequest: number;
  readonly settings: SettingsView;
  readonly themeId: ThemeId;
  readonly themeContrast: ThemeContrast;
  readonly themeMode: ThemeMode;
}

/**
 * The visible settings shell is intentionally small.  Dense model and route
 * controls load only after the user asks to edit them, so page navigation
 * never pays the cost of constructing the routing matrix.
 */
export default function SettingsPage({ archiveClient, commandClient, connectivityController, engineClient, initialProfileDialogFixture = null, initialSection = null, locale, onDirtyChange, onFontPreferencesChange, onLocaleChange, onPetVisibleChange, onSettingsPersisted, onThemeChange, onThemeContrastChange, onThemeModeChange, petVisible = true, saveRequest, settings, themeContrast, themeId, themeMode }: SettingsPageProps) {
  const t = useT();
  const [themeSectionOpen, setThemeSectionOpen] = useState(() => {
    if (initialSection === "creative-temperature") return false;
    try { const v = localStorage.getItem("nimo:settings:themeSectionOpen"); return v === null ? true : v === "1"; } catch { return true; }
  });
  const [routingSectionOpen, setRoutingSectionOpen] = useState(() => {
    if (initialSection === "model-routing") return true;
    try { const v = localStorage.getItem("nimo:settings:routingSectionOpen"); return v === null ? false : v === "1"; } catch { return false; }
  });
  useEffect(() => { try { localStorage.setItem("nimo:settings:themeSectionOpen", themeSectionOpen ? "1" : "0"); } catch { /* ignore */ } }, [themeSectionOpen]);
  useEffect(() => { try { localStorage.setItem("nimo:settings:routingSectionOpen", routingSectionOpen ? "1" : "0"); } catch { /* ignore */ } }, [routingSectionOpen]);
  const [dialog, setDialog] = useState<SettingsDialog | null>(null);
  const [profileDialogFixture, setProfileDialogFixture] = useState<SettingsDialogFixture | null>(initialProfileDialogFixture);
  const [savedNotice, setSavedNotice] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveResult, setSaveResult] = useState<SaveSettingsResult | null>(null);
  const [settingsTransferMessage, setSettingsTransferMessage] = useState("");
  const [creativeDraft, setCreativeDraft] = useState<CreativeTemperatureDraft>(() => creativeTemperatureDraftFromSettings(settings));
  const [savedCreativeDraft, setSavedCreativeDraft] = useState<CreativeTemperatureDraft>(() => creativeTemperatureDraftFromSettings(settings));
  const [fontDraft, setFontDraft] = useState<FontPreferencesView>(() => normalizeFontPreferences(settings.fontPreferences ?? defaultFontPreferences));
  const [savedFontDraft, setSavedFontDraft] = useState<FontPreferencesView>(() => normalizeFontPreferences(settings.fontPreferences ?? defaultFontPreferences));
  const [showCreativeValidation, setShowCreativeValidation] = useState(false);
  const [otherSettingsDirty, setOtherSettingsDirty] = useState(false);
  const creationParamsRef = useRef<Record<string, string>>({});
  const [creationParameterDraft, setCreationParameterDraft] = useState<Readonly<Record<string, string>>>(
    () => settings.creationParameters ?? {},
  );
  const [chapterPolicyDraft, setChapterPolicyDraft] = useState<ChapterRuntimePolicyCommand | null>(() => chapterPolicyFromSettings(settings));
  const [savedChapterPolicyDraft, setSavedChapterPolicyDraft] = useState<ChapterRuntimePolicyCommand | null>(() => chapterPolicyFromSettings(settings));
  // This state intentionally belongs to the Settings page instead of either
  // lazy workbench. Opening model management or collapsing the routing card
  // must never create a second draft of the same model/routing configuration.
  const [routingSession, setRoutingSession] = useState<ModelRoutingSessionState>(() => modelRoutingSessionFromSettings(settings));
  const lastSaveRequest = useRef(saveRequest);
  const settingsImportInput = useRef<HTMLInputElement | null>(null);

  // The controller owns only transient probe state. Keep its cards aligned to
  // the editable session so a deleted profile disappears before persistence.
  useEffect(() => {
    const nextSession = modelRoutingSessionFromSettings(settings);
    const nextCreativeDraft = creativeTemperatureDraftFromSettings(settings);
    const nextFontDraft = normalizeFontPreferences(settings.fontPreferences ?? defaultFontPreferences);
    const nextCreationParameters = settings.creationParameters ?? {};
    const nextChapterPolicy = chapterPolicyFromSettings(settings);
    setRoutingSession(nextSession);
    setCreativeDraft(nextCreativeDraft);
    setSavedCreativeDraft(nextCreativeDraft);
    setFontDraft(nextFontDraft);
    setSavedFontDraft(nextFontDraft);
    setCreationParameterDraft(nextCreationParameters);
    setChapterPolicyDraft(nextChapterPolicy);
    setSavedChapterPolicyDraft(nextChapterPolicy);
    creationParamsRef.current = { ...nextCreationParameters };
    connectivityController.syncProfiles(nextSession.profiles, nextSession.connectionDrafts);
  }, [connectivityController, settings]);

  const connectivity = useSyncExternalStore(
    connectivityController.subscribe,
    connectivityController.getSnapshot,
    connectivityController.getSnapshot,
  );
  const activeTheme = desktopThemes.find((theme) => theme.id === themeId) ?? desktopThemes[0]!;
  const routingWorkbenchSettings = useMemo(() => withoutVoiceTextRoutes(settings), [settings]);
  const configuredProfiles = routingSession.profiles.filter((profile) => profile.statusLabel === "已配置" || profile.statusLabel.includes("通过"));
  const configuredProviders = [...new Set(configuredProfiles.map((profile) => profile.provider))];
  const defaultModelLabel = routingSession.profiles.find((profile) => profile.id === routingSession.defaultProfileId)?.label ?? "尚未设置";

  const creativeDraftValid = isCreativeTemperatureDraftValid(creativeDraft);
  const creativeDraftDirty = !isSameCreativeTemperatureDraft(creativeDraft, savedCreativeDraft);
  const fontDraftDirty =
    fontDraft.uiFamily !== savedFontDraft.uiFamily
    || fontDraft.readingFamily !== savedFontDraft.readingFamily
    || fontDraft.scale !== savedFontDraft.scale;
  const chapterPolicyDirty = JSON.stringify(chapterPolicyDraft) !== JSON.stringify(savedChapterPolicyDraft);
  const settingsDirty = creativeDraftDirty || fontDraftDirty || chapterPolicyDirty || otherSettingsDirty;
  const saveSettings = useCallback(async () => {
    if (!creativeDraftValid) {
      setShowCreativeValidation(true);
      setSavedNotice(false);
      return;
    }
    setSaveState("saving");
    setSavedNotice(false);
    await allowSaveStateToPaint();
    try {
      // Build the command from current routing session and creative draft
      const routeCommands: Record<string, { primaryProfileId: string; fallbackRoutes: readonly { profileId: string; thinkingEnabled: boolean; multiTurnEnabled: boolean }[]; thinkingEnabled: boolean; multiTurnEnabled: boolean; temperature: number | null }> = {};
      for (const [routeId, draft] of Object.entries(routingSession.drafts)) {
        routeCommands[routeId] = {
          primaryProfileId: draft.primaryProfileId,
          fallbackRoutes: draft.fallbackRoutes,
          thinkingEnabled: draft.thinkingEnabled,
          multiTurnEnabled: draft.multiTurnEnabled,
          temperature: draft.temperature,
        };
      }
      const command: SaveSettingsCommand = {
        kind: "save_settings",
        defaultProfileId: routingSession.defaultProfileId,
        profiles: routingSession.profiles.map((p) => ({
          ...(routingSession.connectionDrafts[p.id]?.previousId
            ? { previousId: routingSession.connectionDrafts[p.id]!.previousId }
            : {}),
          id: p.id,
          label: p.label,
          provider: p.provider,
          model: p.model,
          tierLabel: p.tierLabel,
          supportsThinking: p.supportsThinking,
          supportsMultiTurn: p.supportsMultiTurn,
          apiKeyAction: routingSession.connectionDrafts[p.id]?.apiKeyAction ?? "preserve",
          apiKey: routingSession.connectionDrafts[p.id]?.apiKey,
          baseUrl: routingSession.connectionDrafts[p.id]?.baseUrl ?? p.baseUrl ?? "",
        })),
        routes: routeCommands,
        creativeTemperature: {
          enabled: creativeDraft.enabled,
          scope: creativeDraft.scope,
          downDelta: creativeDraft.lowerDelta,
          upDelta: creativeDraft.upperDelta,
          customTaskKeys: creativeDraft.customTaskKeys ?? [],
        },
        themeId,
        fontPreferences: fontDraft,
        creationParameters: creationParamsRef.current,
        ...(chapterPolicyDraft ? { chapterRuntimePolicy: chapterPolicyDraft } : {}),
      };
      const result = await commandClient.saveSettings(command);
      setSaveResult(result);
      if (result.status === "saved" || result.status === "partial") {
        if (result.persistence === "persisted") {
          await onSettingsPersisted();
        }
        setSavedCreativeDraft(creativeDraft);
        setSavedFontDraft(fontDraft);
        setSavedChapterPolicyDraft(chapterPolicyDraft);
        setOtherSettingsDirty(false);
        setShowCreativeValidation(false);
        setSettingsTransferMessage("");
        setSavedNotice(true);
        setSaveState("saved");
      } else {
        setSaveState("error");
      }
    } catch {
      setSaveState("error");
      setSaveResult(null);
    }
  }, [chapterPolicyDraft, commandClient, creativeDraft, creativeDraftValid, fontDraft, onSettingsPersisted, routingSession, themeId]);
  useEffect(() => {
    onDirtyChange(settingsDirty);
    return () => onDirtyChange(false);
  }, [onDirtyChange, settingsDirty]);
  useEffect(() => {
    if (saveRequest === lastSaveRequest.current) return;
    lastSaveRequest.current = saveRequest;
    saveSettings();
  }, [saveRequest, saveSettings]);
  const changeCreativeDraft = (nextDraft: CreativeTemperatureDraft) => {
    setCreativeDraft(nextDraft);
    setSavedNotice(false);
    setSaveState("idle");
    setSaveResult(null);
  };
  const changeRoutingSession = useCallback((nextSession: ModelRoutingSessionState) => {
    connectivityController.syncProfiles(nextSession.profiles, nextSession.connectionDrafts);
    setRoutingSession(nextSession);
    setOtherSettingsDirty(true);
    setSavedNotice(false);
    setSaveState("idle");
    setSaveResult(null);
  }, [connectivityController]);
  const changeFontDraft = (patch: Partial<FontPreferencesView>) => {
    const next = normalizeFontPreferences({ ...fontDraft, ...patch });
    setFontDraft(next);
    onFontPreferencesChange(next);
    setSavedNotice(false);
    setSaveState("idle");
    setSaveResult(null);
  };
  const applyImportedSettings = (raw: unknown) => {
    const imported = parseSettingsTransferPayload(raw);
    if (imported.themeId !== undefined && !desktopThemes.some((theme) => theme.id === imported.themeId)) {
      throw new Error(`主题 ${imported.themeId} 不受当前版本支持。`);
    }
    const profiles = imported.profiles ?? routingSession.profiles;
    const routeSupportsMultiTurn = Object.fromEntries(
      settings.routingGroups.flatMap((group) => group.routes.map((route) => [route.id, route.supportsMultiTurn])),
    );
    const drafts: Record<string, RouteDraft> = Object.fromEntries(
      Object.entries(routingSession.drafts).map(([routeId, draft]) => [
        routeId,
        sanitizeRouteDraft(
          imported.routes?.[routeId] ?? draft,
          profiles,
          routeSupportsMultiTurn[routeId] ?? true,
        ),
      ]),
    );
    const connectionDrafts = Object.fromEntries(profiles.map((profile) => {
      const currentConnection = routingSession.connectionDrafts[profile.id];
      return [profile.id, {
        ...currentConnection,
        apiKey: "",
        apiKeyAction: currentConnection?.apiKeyAction ?? "preserve" as const,
        baseUrl: profile.baseUrl ?? currentConnection?.baseUrl ?? "",
      }];
    }));
    const nextSession: ModelRoutingSessionState = {
      defaultProfileId: normalizeDefaultProfileId(
        imported.defaultProfileId ?? routingSession.defaultProfileId,
        profiles,
      ),
      profiles,
      connectionDrafts,
      drafts,
      bulkDrafts: {},
    };
    setRoutingSession(nextSession);
    connectivityController.syncProfiles(nextSession.profiles, nextSession.connectionDrafts);
    if (imported.creativeTemperature !== undefined) setCreativeDraft(imported.creativeTemperature);
    if (imported.fontPreferences !== undefined) {
      const nextFontDraft = normalizeFontPreferences(imported.fontPreferences);
      setFontDraft(nextFontDraft);
      onFontPreferencesChange(nextFontDraft);
    }
    if (imported.creationParameters !== undefined) {
      setCreationParameterDraft((current) => {
        const next = { ...current, ...imported.creationParameters };
        creationParamsRef.current = next;
        return next;
      });
    }
    if (imported.chapterRuntimePolicy !== undefined) {
      setChapterPolicyDraft(imported.chapterRuntimePolicy);
    }
    if (imported.themeId !== undefined) {
      onThemeChange(imported.themeId as ThemeId);
    }
    setOtherSettingsDirty(true);
    setSavedNotice(false);
    setSaveState("idle");
    setSaveResult(null);
    setSettingsTransferMessage("配置已导入当前草案；点击“保存设置”后才会写入引擎。");
  };
  const exportSettings = useCallback(() => {
    const exportPayload = buildSettingsTransferPayload({
      defaultProfileId: routingSession.defaultProfileId,
      profiles: routingSession.profiles,
      routes: routingSession.drafts,
      creativeTemperature: creativeDraft,
      themeId,
      fontPreferences: fontDraft,
      creationParameters: creationParamsRef.current,
      ...(chapterPolicyDraft ? { chapterRuntimePolicy: chapterPolicyDraft } : {}),
    });
    const blob = new Blob([JSON.stringify(exportPayload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "nimo_settings_export.json";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
    setSettingsTransferMessage("已导出模型、路由、创作参数、主题与字体设置。");
  }, [chapterPolicyDraft, creativeDraft, fontDraft, routingSession, themeId]);
  const importSettingsFile = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        applyImportedSettings(JSON.parse(reader.result as string));
      } catch (error) {
        setSaveState("error");
        setSettingsTransferMessage(error instanceof Error ? error.message : "配置文件格式无效。");
      }
    };
    reader.readAsText(file);
    event.target.value = "";
  };
  const sourceProfileDraft = profileDialogFixture === "model-profile-edit"
    ? { label: "OpenAI · GPT-4o", provider: "openai", model: "gpt-4o", tierLabel: "高质量", supportsThinking: false, supportsMultiTurn: true }
    : { label: "", provider: "tongyi", model: "qwen3.7-max", tierLabel: "高质量", supportsThinking: true, supportsMultiTurn: true };

  return (
    <div className="settings-page">
      <input
        accept=".json,application/json"
        aria-label="选择 NIMO 设置文件"
        id="nimo-settings-import-file"
        onChange={importSettingsFile}
        ref={settingsImportInput}
        style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0,0,0,0)" }}
        tabIndex={-1}
        type="file"
      />
      <button hidden id="nimo-settings-export-trigger" onClick={exportSettings} type="button" />
      {settingsTransferMessage.length > 0 && <span aria-live="polite" className="settings-transfer-notice settings-transfer-global" role="status">{settingsTransferMessage}</span>}
      {!routingSectionOpen && <section className="settings-hero">
        <div className="settings-hero-copy">
          <span className="section-kicker">火候与设置</span>
          <h2>炼鼎通路，调鹽火候，令山河流载不穷。</h2>
          <p>保存会提交模型档案、任务路由与运行参数草案；文本生成路由只接受生成模型，嵌入模型仅用于向量检索。保存回执会明确说明配置是否持久化、运行时是否已重载。<br />新提交的任务读取最新存档；正在运行的任务不会热切换配置。</p>
          <div className="button-row">
            <button
              className={`button button-primary${saveState === "saving" ? " is-loading" : ""}`}
              disabled={saveState === "saving"}
              onClick={saveSettings}
              title="保存当前所有设置到本地配置文件。"
              type="button"
            >
              {saveState === "saving" ? "正在保存…" : savedNotice && saveResult ? `${saveResult.persistence === "persisted" ? "已保存" : "已提交"} · ${saveResult.runtimeReloadStatus === "reloaded" || saveResult.runtimeReloadStatus === "current" ? "运行时已更新" : saveResult.runtimeReloadStatus === "unavailable" ? "下次任务生效" : "需重启引擎"}` : settingsDirty ? "保存设置 · 有变更" : "保存设置"}
            </button>
            {settingsDirty && <span className="settings-dirty-badge">● 未保存</span>}
            <button
              className="button button-secondary"
              disabled={connectivity.activeProfileIds.length > 0}
              onClick={() => connectivityController.start()}
              title="测试所有已配置模型的连通性。"
              type="button"
            >
              {connectivity.activeProfileIds.length === 0 ? "检测全部通路" : "检测中…"}
            </button>
            <button
              className="button button-secondary"
              onClick={exportSettings}
              title="将模型、路由、创作参数、主题与字体设置导出为 JSON 文件。"
              type="button"
            >
              导出配置文件
            </button>
            <button className="button button-secondary" onClick={() => settingsImportInput.current?.click()} title="从 JSON 文件导入完整 NIMO 设置草案。" type="button">
              导入配置文件
            </button>
          </div>
        </div>
        <div className="settings-overview">
          <h3>运行总览</h3>
          <p>当前工作区环境、已载入 Provider 与模型连接状态。</p>
          <div className="settings-metric-grid">
            <SettingMetric label="工作区路径" value={settings.storageRootLabel} detail="当前检测到 2 个项目" />
            <SettingMetric label="默认模型" value={defaultModelLabel} detail={`共 ${configuredProfiles.length} 个已配置模型 · 可在模型管理中修改`} />
            <SettingMetric label="已载入 Provider" value={String(configuredProviders.length)} detail={configuredProviders.join(" · ") || "尚未配置"} />
            <SettingMetric label="运行模式" value="真实模型模式" detail="可在下方“调试”分区开关。" />
          </div>
        </div>
      </section>}

      {!routingSectionOpen && <ModelConnectionStatus onManageModels={() => setDialog("models")} session={connectivity} />}

      <section className="settings-card settings-routing-card">
        <button aria-expanded={routingSectionOpen} className="settings-accordion-toggle settings-card-toggle" onClick={() => setRoutingSectionOpen((open) => !open)} type="button">
          <span>{routingSectionOpen ? "⌄" : "›"}</span>流程路由 — 为每个步骤选择模型与能力
        </button>
        {routingSectionOpen && <div className="settings-card-body settings-routing-body">
          <p>先按主流程或子流程批量指定文本生成模型，再按单个步骤覆盖；思考/多轮只在支持的模型上生效。嵌入模型不会进入主路由或备用链，只可用于“记忆模块”的向量检索。选择“改用默认模型”会清除本组显式路由，避免旧配置继续覆盖默认模型。</p>
          <Suspense fallback={<WorkbenchLoading />}><LazyModelRoutingWorkbench commandClient={commandClient} embedded initialTab="routes" onSessionChange={changeRoutingSession} session={routingSession} settings={routingWorkbenchSettings} surface="routes" /></Suspense>
          <p className="settings-routing-transfer-note">配音脚本、说话人裁决、音色复核、旁白画像与声音设计路由已移至“声腔 → 平台设置 → 文本智能”，避免同一任务出现两套入口。</p>
          {saveState === "saving" && <p className="settings-session-save-notice" role="status">正在保存模型路由与运行参数…</p>}
          {savedNotice && saveResult && <div className={`settings-session-save-notice${saveResult.runtimeReloadStatus === "failed" ? " is-error" : ""}`} role="status"><p>{saveResult.message} <small>（{saveResult.savedAtLabel}）</small></p>{saveResult.rejectedRoutes.length > 0 && <ul aria-label="未采纳的设置项">{saveResult.rejectedRoutes.map((item) => <li key={`${item.routeId}:${item.reason}`}><code>{item.routeId}</code>：{item.reason}</li>)}</ul>}</div>}
          {saveState === "error" && <p className="settings-session-save-notice is-error" role="alert">保存失败；请检查引擎连接后重试。</p>}
        </div>}
      </section>

      <section className="settings-section-heading"><h2>{t("settings.appearance.title")}</h2><p>{t("settings.appearance.body")}</p></section>
      <section className="settings-card">
        <button aria-expanded={themeSectionOpen} className="settings-accordion-toggle settings-card-toggle" onClick={() => setThemeSectionOpen((open) => !open)} type="button">
          <span>{themeSectionOpen ? "⌄" : "›"}</span>{t("settings.theme.title")}
        </button>
        {themeSectionOpen && <div className="settings-card-body">
          <label className="setting-row">
            <span><strong>{t("settings.language.title")}</strong><small>{t("settings.language.desc")}</small></span>
            <div className="locale-toggle" role="radiogroup" aria-label={t("settings.language.title")}>
              <button aria-checked={locale === "zh"} className={locale === "zh" ? "locale-toggle-btn is-active" : "locale-toggle-btn"} onClick={() => onLocaleChange("zh")} role="radio" type="button">中文</button>
              <button aria-checked={locale === "en"} className={locale === "en" ? "locale-toggle-btn is-active" : "locale-toggle-btn"} onClick={() => onLocaleChange("en")} role="radio" type="button">English</button>
            </div>
          </label>
          <p>{t("settings.theme.body")}</p>
          <div className="theme-system-controls">
            <div>
              <span><strong>主题策略</strong><small>跟随系统时自动在素纸与暮墨之间切换。</small></span>
              <div className="locale-toggle" role="radiogroup" aria-label="主题策略">
                <button aria-checked={themeMode === "manual"} className={themeMode === "manual" ? "locale-toggle-btn is-active" : "locale-toggle-btn"} onClick={() => onThemeModeChange("manual")} role="radio" type="button">手动</button>
                <button aria-checked={themeMode === "system"} className={themeMode === "system" ? "locale-toggle-btn is-active" : "locale-toggle-btn"} onClick={() => onThemeModeChange("system")} role="radio" type="button">跟随系统</button>
              </div>
            </div>
            <label>
              <span><strong>高对比辅助</strong><small>增强正文、边界与状态的可辨识度。</small></span>
              <input checked={themeContrast === "high"} onChange={(event) => onThemeContrastChange(event.target.checked ? "high" : "standard")} type="checkbox" />
            </label>
          </div>
          <div className="theme-grid" role="radiogroup" aria-label="桌面主题">
            {desktopThemes.map((theme) => (
              <button
                aria-checked={theme.id === themeId}
                className={theme.id === themeId ? "theme-grid-item is-active" : "theme-grid-item"}
                key={theme.id}
                onClick={() => { onThemeChange(theme.id as ThemeId); setOtherSettingsDirty(true); setSavedNotice(false); }}
                role="radio"
                type="button"
              >
                <span className="theme-grid-swatch">
                  <span style={{ background: theme.tokens["bg.sidebar.start"] }} />
                  <span style={{ background: theme.tokens["accent.primary"] }} />
                  <span style={{ background: theme.tokens["bg.surface"] }} />
                </span>
                <strong>{theme.label}</strong>
                <small>{theme.description}</small>
              </button>
            ))}
          </div>
          <div className="font-preference-grid" aria-label="字体管理">
            <label className="setting-row">
              <span><strong>界面字体</strong><small>用于表单、按钮、任务卡与导航；默认沿用 PySide6 立项界面的主题宋体。</small></span>
              <select aria-label="界面字体" onChange={(event) => changeFontDraft({ uiFamily: event.target.value as FontPreferencesView["uiFamily"] })} value={fontDraft.uiFamily}>
                {uiFontChoices.map((choice) => <option key={choice.id} title={choice.description} value={choice.id}>{choice.label}</option>)}
              </select>
            </label>
            <label className="setting-row">
              <span><strong>阅读字体</strong><small>用于卷帙正文、报告、标题与流式成稿内容。</small></span>
              <select aria-label="阅读字体" onChange={(event) => changeFontDraft({ readingFamily: event.target.value as FontPreferencesView["readingFamily"] })} value={fontDraft.readingFamily}>
                {readingFontChoices.map((choice) => <option key={choice.id} title={choice.description} value={choice.id}>{choice.label}</option>)}
              </select>
            </label>
            <label className="setting-row">
              <span><strong>字体缩放</strong><small>100% 为舒适桌面基线；缩放会同步作用于页面、表单和弹窗，不改变业务信息层级。</small></span>
              <select aria-label="字体缩放" onChange={(event) => changeFontDraft({ scale: Number(event.target.value) })} value={String(fontDraft.scale)}>
                {fontScaleChoices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
              </select>
            </label>
          </div>
          <label className="setting-row"><span><strong>Nimo 案头宠物</strong><small>在页面右下角显示任务状态、流式输出与 Token 用量。</small></span><select aria-label="Nimo 案头宠物" onChange={(event) => onPetVisibleChange?.(event.target.value === "true")} value={String(petVisible)}><option value="true">显示</option><option value="false">隐藏</option></select></label>
        </div>}
      </section>

      <section className="settings-section-heading"><h2>创作参数</h2><p>控制短篇、长篇初始化、章节写作、上下文投喂与状态归档。</p></section>
      {chapterPolicyDraft && settings.chapterRuntimePolicy && <ChapterRuntimePolicyCard onChange={(next) => { setChapterPolicyDraft(next); setSavedNotice(false); setSaveState("idle"); }} options={settings.chapterRuntimePolicy.presetOptions} value={chapterPolicyDraft} />}
      <CreativeTemperatureSection draft={creativeDraft} initiallyExpanded={initialSection === "creative-temperature"} onDraftChange={changeCreativeDraft} routingGroups={settings.routingGroups} showValidation={showCreativeValidation} />
      <CreationParameterSections initialValues={creationParameterDraft} onSettingsChange={() => { setOtherSettingsDirty(true); setSavedNotice(false); }} valuesRef={creationParamsRef} />

      <section className="settings-section-heading"><h2>Ollama 模型</h2><p>通过 Engine 管理 Engine 主机上的运行态、模型、角色与路由。</p></section>
      <section className="settings-card">
        <OllamaModelPanel commandClient={commandClient} engineClient={engineClient} />
      </section>

      <section className="settings-section-heading"><h2>任务错误档案</h2><p>死信队列保留失败任务的诊断信息，便于回溯。</p></section>
      <section className="settings-card">
        <ErrorArchivePanel commandClient={commandClient} summaryClient={archiveClient} />
      </section>

      {dialog === "models" && <AppDialog description="在此添加、编辑、测试和删除模型；API Key 由本地引擎安全写入 .env，界面只显示遮掩值。" onClose={() => setDialog(null)} size="wide" title="模型管理"><Suspense fallback={<WorkbenchLoading />}><LazyModelRoutingWorkbench commandClient={commandClient} initialTab="models" onSessionChange={changeRoutingSession} session={routingSession} settings={settings} surface="models" /></Suspense></AppDialog>}
      {profileDialogFixture !== null && <ModelProfileDialog initialApiKey={profileDialogFixture === "model-profile-edit" ? "fixture-key-never-used" : ""} initialDraft={sourceProfileDraft} mode={profileDialogFixture === "model-profile-edit" ? "edit" : "add"} onCancel={() => setProfileDialogFixture(null)} onSave={() => { setProfileDialogFixture(null); setSavedNotice(true); }} providerOptions={settings.modelProviderOptions} />}
    </div>
  );
}

function chapterPolicyFromSettings(settings: SettingsView): ChapterRuntimePolicyCommand | null {
  const policy = settings.chapterRuntimePolicy;
  if (!policy) return null;
  return {
    preset: policy.preset,
    intentGuardMode: policy.intentGuardMode,
    factRefreshEnabled: policy.factRefreshEnabled,
    inspirationEnabled: policy.inspirationEnabled,
    inspirationCooldown: policy.inspirationCooldown,
    shortAdaptiveRevisionEnabled: policy.shortAdaptiveRevisionEnabled,
    longSingleFinalVerifyEnabled: policy.longSingleFinalVerifyEnabled,
  };
}

function creativeTemperatureDraftFromSettings(settings: SettingsView): CreativeTemperatureDraft {
  const source = settings.creativeTemperature;
  return source === undefined
    ? defaultCreativeTemperatureDraft
    : {
      enabled: source.enabled,
      scope: source.scope,
      lowerDelta: source.downDelta,
      upperDelta: source.upDelta,
      customTaskKeys: source.customTaskKeys,
    };
}

function WorkbenchLoading() {
  return <div className="dialog-check-list" aria-label="正在载入模型工作台"><span>正在载入模型工作台…</span><span>页面主体保持可用。</span></div>;
}

function SettingMetric({ detail, label, value }: { readonly label: string; readonly value: string; readonly detail: string }) {
  return <article className="setting-metric"><h4>{label}</h4><strong>{value}</strong><p>{detail}</p></article>;
}
