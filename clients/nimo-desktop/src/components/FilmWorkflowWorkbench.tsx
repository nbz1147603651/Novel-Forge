import {
  memo,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type {
  EngineClient,
  EngineCommandClient,
  FilmGraphDefinitionView,
  FilmGraphNodeView,
  FilmGraphRunScope,
  FilmGraphRunView,
  FilmGraphValidationIssue,
  FilmGraphView,
  FilmNodeDefinitionView,
  FilmProviderCatalogView,
  FilmPromptSource,
  FilmRunEstimateView,
  FilmStudioView,
} from "@nimo/engine-contracts";

import { useLocale, useT } from "../lib/i18n";
import {
  autoLayoutFilmGraph,
  filmExpertWorkbenchClassName,
  renderFilmNodePrompt,
  replaceFilmPromptSection,
} from "./film-workflow-utils";
import { FilmGraphCanvas } from "./FilmRunPlanCanvas";

interface FilmWorkflowWorkbenchProps {
  readonly canvasRequest?: number;
  readonly commandClient: EngineCommandClient;
  readonly engineClient: EngineClient;
  readonly onError: (message: string) => void;
  readonly onNotice: (message: string) => void;
  readonly onCanvasFocusChange?: (focused: boolean) => void;
  readonly studio: FilmStudioView;
}

type WorkbenchMode = "guided" | "expert";

const NODE_CATEGORY_KEYS: Readonly<Record<string, string>> = {
  "策划与风格": "film.nodeCategory.planning",
  "资产生成": "film.nodeCategory.assets",
  "镜头与分镜": "film.nodeCategory.storyboard",
  "检索与上下文": "film.nodeCategory.context",
  "视频生成": "film.nodeCategory.video",
  "质量与交付": "film.nodeCategory.delivery",
};

function localizedNodeLabel(typeId: string, fallback: string, t: ReturnType<typeof useT>): string {
  const key = `film.node.${typeId}`;
  const translated = t(key);
  return translated === key ? fallback : translated;
}

function localizedCategoryLabel(category: string, t: ReturnType<typeof useT>): string {
  const key = NODE_CATEGORY_KEYS[category];
  return key ? t(key) : category;
}

function localizedProviderValue(
  providerId: string,
  field: "bestFor" | "label" | "short",
  fallback: string,
  t: ReturnType<typeof useT>,
): string {
  const key = `film.provider.${providerId}.${field}`;
  const translated = t(key);
  return translated === key ? fallback : translated;
}

interface VideoModelOption {
  readonly id: string;
  readonly label: string;
  readonly aspectRatios: readonly string[];
  readonly defaultDuration: number | null;
  readonly defaultResolution: string;
  readonly durations: readonly number[];
  readonly features: readonly string[];
  readonly modes: readonly string[];
  readonly recommended: boolean;
  readonly resolutions: readonly string[];
}

function stringValues(value: unknown): readonly string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function durationValues(value: unknown): readonly number[] {
  if (Array.isArray(value)) {
    return value.filter((item): item is number => typeof item === "number" && Number.isInteger(item));
  }
  if (typeof value !== "object" || value === null) return [];
  const domain = value as Readonly<Record<string, unknown>>;
  const min = typeof domain.min === "number" ? Math.ceil(domain.min) : null;
  const max = typeof domain.max === "number" ? Math.floor(domain.max) : null;
  if (min === null || max === null || min > max) return [];
  return Array.from({ length: max - min + 1 }, (_, index) => min + index);
}

function videoModels(catalog: FilmProviderCatalogView, providerId: string): readonly VideoModelOption[] {
  return (catalog[providerId]?.videoModels ?? []).flatMap((model) => {
    const id = typeof model.id === "string" ? model.id : "";
    if (id.length === 0) return [];
    return [{
      id,
      label: typeof model.label === "string" ? model.label : id,
      aspectRatios: stringValues(model.aspectRatios),
      defaultDuration: typeof model.defaultDuration === "number" ? model.defaultDuration : null,
      defaultResolution: typeof model.defaultResolution === "string" ? model.defaultResolution : "",
      durations: durationValues(model.durations),
      features: stringValues(model.features),
      modes: stringValues(model.modes),
      recommended: model.recommended === true,
      resolutions: stringValues(model.resolutions),
    }];
  });
}

function selectedVideoModel(
  models: readonly VideoModelOption[],
  modelId: string,
  mode = "",
): VideoModelOption | undefined {
  return models.find((model) => model.id === modelId)
    ?? models.find((model) => model.recommended && model.modes.includes(mode))
    ?? models.find((model) => model.modes.includes(mode))
    ?? models.find((model) => model.recommended)
    ?? models[0];
}

function closestDuration(value: unknown, model: VideoModelOption | undefined): number {
  const current = typeof value === "number" ? Math.round(value) : model?.defaultDuration ?? 5;
  if ((model?.durations.length ?? 0) === 0) return Math.max(2, Math.min(30, current));
  return model!.durations.reduce((closest, candidate) => (
    Math.abs(candidate - current) < Math.abs(closest - current) ? candidate : closest
  ));
}

function resolvedModelValue(current: unknown, values: readonly string[], fallback: string): string {
  const value = typeof current === "string" ? current : "";
  if (values.includes(value)) return value;
  return values[0] ?? fallback;
}

function explicitProviderIds(definition: FilmNodeDefinitionView | undefined): readonly string[] {
  if (definition === undefined) return [];
  return [...new Set(definition.providerCapabilities.flatMap((capability) => {
    const separator = capability.indexOf(":");
    return separator > 0 ? [capability.slice(0, separator)] : [];
  }))];
}

function isRoutableVideoNode(definition: FilmNodeDefinitionView | undefined): boolean {
  return definition !== undefined
    && definition.outputPorts.some((port) => port.artifactType === "VideoClip")
    && !definition.inputPorts.some((port) => port.artifactType === "VideoClip")
    && explicitProviderIds(definition).length > 1;
}

function routeVideoNode(
  node: FilmGraphNodeView,
  catalog: FilmProviderCatalogView,
  providerId: string,
  requestedModelId = "",
): FilmGraphNodeView {
  const models = videoModels(catalog, providerId);
  const previousMode = String(node.config.mode ?? "reference_to_video");
  const model = selectedVideoModel(models, requestedModelId, previousMode);
  if (model === undefined) return node;
  const providerChanged = node.providerId !== providerId;
  const mode = model.modes.includes(previousMode) ? previousMode : model.modes[0] ?? previousMode;
  const resolution = resolvedModelValue(
    node.config.resolution,
    model.resolutions,
    model.defaultResolution || "1080P",
  );
  const ratio = mode === "image_to_video" || mode === "first_last_frame"
    ? "adaptive"
    : resolvedModelValue(node.config.ratio, model.aspectRatios, providerId === "minimax" ? "adaptive" : "16:9");
  return {
    ...node,
    providerId,
    modelId: model.id,
    config: {
      ...node.config,
      mode,
      duration: closestDuration(node.config.duration, model),
      resolution,
      ratio,
      seed: model.features.includes("seed") ? node.config.seed ?? null : null,
      watermark: providerId === "minimax" ? node.config.aigc_watermark === true : node.config.watermark === true,
      aigc_watermark: providerId === "minimax" ? node.config.aigc_watermark === true : false,
      prompt_optimizer: model.features.includes("prompt_optimizer")
        ? providerChanged || node.config.prompt_optimizer !== false
        : false,
      generate_audio: model.features.includes("generate_audio")
        ? providerChanged || node.config.generate_audio !== false
        : false,
      return_last_frame: model.features.includes("return_last_frame")
        ? providerChanged || node.config.return_last_frame !== false
        : false,
    },
  };
}

export function routeFilmGraphProvider(
  graph: FilmGraphDefinitionView,
  definitions: readonly FilmNodeDefinitionView[],
  providerCatalog: FilmProviderCatalogView,
  providerId: string,
): { readonly graph: FilmGraphDefinitionView; readonly changedNodeCount: number } {
  const byType = new Map(definitions.map((definition) => [definition.typeId, definition]));
  const generationNode = graph.nodes.find((node) => isRoutableVideoNode(byType.get(node.typeId)));
  const preferredMode = String(generationNode?.config.mode ?? "reference_to_video");
  const targetModel = selectedVideoModel(videoModels(providerCatalog, providerId), "", preferredMode);
  let changedNodeCount = 0;
  const nodes = graph.nodes.map((node) => {
    const definition = byType.get(node.typeId);
    if (!explicitProviderIds(definition).includes(providerId)) return node;
    const next = isRoutableVideoNode(definition)
      ? routeVideoNode(node, providerCatalog, providerId)
      : node.typeId === "h3_context_ir" && targetModel
        ? { ...node, providerId, modelId: targetModel.id, config: { ...node.config, target_model: targetModel.id } }
        : node;
    if (next !== node) changedNodeCount += 1;
    return next;
  });
  return { graph: { ...graph, nodes }, changedNodeCount };
}

function formatMoney(value: number, locale: "zh" | "en"): string {
  return new Intl.NumberFormat(locale === "zh" ? "zh-CN" : "en-US", {
    currency: "CNY",
    maximumFractionDigits: 2,
    style: "currency",
  }).format(value * 7.2);
}

function formatDuration(seconds: number, t: ReturnType<typeof useT>): string {
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return minutes > 0 ? t("film.workbench.minutesSeconds", { minutes, seconds: rest }) : t("film.unit.seconds", { n: rest });
}

function runStatusLabel(status: FilmGraphRunView["status"], t: ReturnType<typeof useT>): string {
  const key = `film.status.${status}`;
  const translated = t(key);
  return translated === key ? status : translated;
}

function issueSummary(issues: readonly FilmGraphValidationIssue[], t: ReturnType<typeof useT>): string {
  const errors = issues.filter((issue) => issue.severity === "error").length;
  const warnings = issues.length - errors;
  if (errors === 0 && warnings === 0) return t("film.workbench.validationPassed");
  return t("film.workbench.validationIssues", { errors, warnings });
}

function replaceNode(
  graph: FilmGraphDefinitionView,
  nodeId: string,
  mapper: (node: FilmGraphNodeView) => FilmGraphNodeView,
): FilmGraphDefinitionView {
  return { ...graph, nodes: graph.nodes.map((node) => node.nodeId === nodeId ? mapper(node) : node) };
}

function RunHistory({ runs, onCancel }: {
  readonly runs: readonly FilmGraphRunView[];
  readonly onCancel: (runId: string) => void;
}) {
  const t = useT();
  const locale = useLocale();
  if (runs.length === 0) return <p className="film-workbench-empty">{t("film.workbench.noRuns")}</p>;
  return (
    <div className="film-run-history-list">
      {runs.slice(0, 12).map((run) => (
        <article key={run.runId}>
          <span className={`is-${run.status}`} />
          <div>
            <strong>{runStatusLabel(run.status, t)}</strong>
            <small>{t("film.workbench.runMeta", { scope: run.scope, count: run.targetNodeIds.length || t("film.workbench.all"), revision: run.graphRevision })}</small>
          </div>
          <b>{formatMoney(run.estimate.estimatedCostUsd, locale)}</b>
          <time>{new Date(run.createdAt).toLocaleTimeString(locale === "zh" ? "zh-CN" : "en-US", { hour: "2-digit", minute: "2-digit" })}</time>
          {run.status === "queued" || run.status === "running" || run.status === "waiting_confirmation" ? (
            <button onClick={() => onCancel(run.runId)} type="button">{t("film.common.cancel")}</button>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function EstimatePanel({ estimate, onCancel, onConfirm }: {
  readonly estimate: FilmRunEstimateView;
  readonly onCancel: () => void;
  readonly onConfirm: (highPriority: boolean) => void;
}) {
  const t = useT();
  const locale = useLocale();
  const [highPriority, setHighPriority] = useState(false);
  const blocked = estimate.validationIssues.some((issue) => issue.severity === "error")
    || estimate.missingInputs.length > 0;
  return (
    <aside className="film-run-estimate" aria-label={t("film.workbench.estimateAria")}>
      <header><span>RUN ESTIMATE</span><strong>{t("film.workbench.preRunConfirmation")}</strong></header>
      <dl>
        <div><dt>{t("film.workbench.executionNodes")}</dt><dd>{estimate.executionNodeIds.length}</dd></div>
        <div><dt>{t("film.workbench.cacheHits")}</dt><dd>{estimate.cachedNodeIds.length}</dd></div>
        <div><dt>{t("film.workbench.estimatedTime")}</dt><dd>{formatDuration(estimate.estimatedDurationS, t)}</dd></div>
        <div><dt>{t("film.workbench.estimatedCost")}</dt><dd>{formatMoney(estimate.estimatedCostUsd, locale)}</dd></div>
      </dl>
      {estimate.missingInputs.length > 0 ? <p className="is-error">{t("film.workbench.missingInputs", { inputs: estimate.missingInputs.join(" / ") })}</p> : null}
      {estimate.validationIssues.length > 0 ? <p className="is-error">{issueSummary(estimate.validationIssues, t)}</p> : null}
      <label><input checked={highPriority} onChange={(event) => setHighPriority(event.target.checked)} type="checkbox" /> {t("film.workbench.highPriority")}</label>
      <footer>
        <button onClick={onCancel} type="button">{t("film.workbench.backToEdit")}</button>
        <button className="is-primary" disabled={blocked} onClick={() => onConfirm(highPriority)} type="button">
          {t(estimate.requiresConfirmation ? "film.workbench.confirmCostRun" : "film.workbench.confirmRun")}
        </button>
      </footer>
    </aside>
  );
}

function PlatformVideoInspector({ catalog, definition, node, onChange, onProviderChange }: {
  readonly catalog: FilmProviderCatalogView;
  readonly definition: FilmNodeDefinitionView;
  readonly node: FilmGraphNodeView;
  readonly onChange: (node: FilmGraphNodeView) => void;
  readonly onProviderChange: (providerId: string) => void;
}) {
  const t = useT();
  const providerIds = explicitProviderIds(definition).filter(
    (providerId) => (catalog[providerId]?.videoModels.length ?? 0) > 0,
  );
  const providerId = providerIds.includes(node.providerId) ? node.providerId : providerIds[0] ?? node.providerId;
  const models = videoModels(catalog, providerId);
  const model = selectedVideoModel(models, node.modelId, String(node.config.mode ?? ""));
  const mode = String(node.config.mode ?? "reference_to_video");
  const modes = model?.modes.length ? model.modes : ["text_to_video", "image_to_video", "first_last_frame", "reference_to_video"];
  const durations = model?.durations.length ? model.durations : [closestDuration(node.config.duration, model)];
  const resolutions = model?.resolutions.length ? model.resolutions : [String(node.config.resolution ?? "1080P")];
  const ratioDisabled = mode === "image_to_video" || mode === "first_last_frame";
  const ratios = model?.aspectRatios.length ? model.aspectRatios : ["16:9", "9:16", "1:1", "4:3", "3:4"];
  const provider = catalog[providerId];
  const supports = (feature: string) => model?.features.includes(feature) === true;
  const update = (key: string, value: unknown) => onChange({ ...node, config: { ...node.config, [key]: value } });
  const changeProvider = (nextProviderId: string) => onProviderChange(nextProviderId);
  const changeModel = (nextModelId: string) => onChange(routeVideoNode(node, catalog, providerId, nextModelId));
  return (
    <>
      <section className="film-node-provider-route">
        <header><h4>{t("film.workbench.platformRoute")}</h4><small>{t("film.workbench.platformRouteHint")}</small></header>
        <div aria-label={t("film.workbench.platformAria")} className="film-provider-switch" role="tablist">
          {providerIds.map((item) => (
            <button
              aria-selected={item === providerId}
              className={item === providerId ? "is-active" : ""}
              key={item}
              onClick={() => changeProvider(item)}
              role="tab"
              title={localizedProviderValue(item, "bestFor", catalog[item]?.bestFor ?? item, t)}
              type="button"
            >
              {localizedProviderValue(item, "short", catalog[item]?.shortLabel ?? catalog[item]?.label ?? item, t)}
            </button>
          ))}
        </div>
        <label>{t("film.workbench.currentModel")}
          <select onChange={(event) => changeModel(event.target.value)} value={model?.id ?? ""}>
            {models.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </label>
        <div className="film-provider-summary">
          <div><strong>{localizedProviderValue(providerId, "label", provider?.label ?? providerId, t)}</strong><small>{model?.label ?? node.modelId}</small></div>
          {provider?.docsUrl ? <a href={provider.docsUrl} rel="noreferrer" target="_blank">{t("film.common.officialDocs")}</a> : null}
          <p>{provider?.bestFor ? localizedProviderValue(providerId, "bestFor", provider.bestFor, t) : t("film.workbench.registryDriven")}</p>
        </div>
        <div aria-label={t("film.workbench.nativeCapabilities")} className="film-provider-pills">
          {(model?.features.length ? model.features : provider?.strengths ?? []).slice(0, 8).map((feature) => <span key={feature}>{feature.replaceAll("_", " ")}</span>)}
        </div>
      </section>
      <section className="film-node-provider-params">
        <header><h4>{t("film.workbench.nativeParameters")}</h4><small>{t("film.workbench.parametersFollowModel")}</small></header>
        <label>{t("film.workbench.generationMode")}
          <select onChange={(event) => update("mode", event.target.value)} value={mode}>
            {modes.map((item) => <option key={item} value={item}>{t({ text_to_video: "film.workbench.t2v", image_to_video: "film.workbench.i2v", first_last_frame: "film.workbench.firstLast", reference_to_video: "film.workbench.r2v", video_continuation: "film.workbench.continuation" }[item] ?? item)}</option>)}
          </select>
        </label>
        <div className="film-workbench-field-grid">
          <label>{t("film.workbench.duration")}
            <select onChange={(event) => update("duration", Number(event.target.value))} value={closestDuration(node.config.duration, model)}>
              {durations.map((duration) => <option key={duration} value={duration}>{t("film.unit.seconds", { n: duration })}</option>)}
            </select>
          </label>
          <label>{t("film.workbench.directResolution")}
            <select onChange={(event) => update("resolution", event.target.value)} value={resolvedModelValue(node.config.resolution, resolutions, model?.defaultResolution ?? "1080P")}>
              {resolutions.map((resolution) => <option key={resolution} value={resolution}>{resolution}</option>)}
            </select>
          </label>
          <label>{t("film.workbench.aspectRatio")}
            <select disabled={ratioDisabled} onChange={(event) => update("ratio", event.target.value)} value={ratioDisabled ? "adaptive" : resolvedModelValue(node.config.ratio, ratios, providerId === "minimax" ? "adaptive" : "16:9")}>
              {providerId === "minimax" ? <option value="adaptive">{t("film.workbench.adaptive")}</option> : null}
              {ratios.map((ratio) => <option key={ratio} value={ratio}>{ratio}</option>)}
            </select>
          </label>
          {supports("seed") ? <label>{t("film.workbench.seed")}<input min={0} onChange={(event) => update("seed", event.target.value.length ? Number(event.target.value) : null)} placeholder={t("film.workbench.randomSeed")} type="number" value={typeof node.config.seed === "number" ? node.config.seed : ""} /></label> : null}
        </div>
        <div className="film-node-provider-toggles">
          {supports("prompt_optimizer") ? <label className="film-workbench-checkbox"><input checked={node.config.prompt_optimizer !== false} onChange={(event) => update("prompt_optimizer", event.target.checked)} type="checkbox" /> {t("film.workbench.promptOptimizer")}</label> : null}
          {supports("generate_audio") ? <label className="film-workbench-checkbox"><input checked={node.config.generate_audio !== false} onChange={(event) => update("generate_audio", event.target.checked)} type="checkbox" /> {t("film.workbench.generateAudio")}</label> : null}
          {supports("return_last_frame") ? <label className="film-workbench-checkbox"><input checked={node.config.return_last_frame !== false} onChange={(event) => update("return_last_frame", event.target.checked)} type="checkbox" /> {t("film.workbench.returnLastFrame")}</label> : null}
          {supports("watermark") ? <label className="film-workbench-checkbox"><input checked={providerId === "minimax" ? node.config.aigc_watermark === true : node.config.watermark === true} onChange={(event) => update(providerId === "minimax" ? "aigc_watermark" : "watermark", event.target.checked)} type="checkbox" /> {t("film.workbench.aigcWatermark")}</label> : null}
        </div>
        <small>{t(ratioDisabled ? "film.workbench.ratioI2vHint" : mode === "text_to_video" ? "film.workbench.ratioT2vHint" : "film.workbench.ratioR2vHint")}</small>
      </section>
      {providerId === "bailian" ? <section className="film-node-provider-native"><h4>{t("film.workbench.bailianNative")}</h4><label>{t("film.workbench.negativePrompt")}<textarea onChange={(event) => update("negative_prompt", event.target.value)} rows={2} value={String(node.config.negative_prompt ?? "")} /></label><label>{t("film.workbench.drivingAudio")}<input onChange={(event) => update("driving_audio_url", event.target.value)} placeholder="https://…/audio.mp3" value={String(node.config.driving_audio_url ?? "")} /></label></section> : null}
      {providerId === "volcengine_ark" ? <section className="film-node-provider-native"><h4>{t("film.workbench.seedanceNative")}</h4><label>{t("film.workbench.trustedAssets")}<textarea onChange={(event) => update("trusted_asset_uris", event.target.value)} placeholder="asset://asset-…" rows={2} value={String(node.config.trusted_asset_uris ?? "")} /></label><p>{t("film.workbench.seedanceAssetHint")}</p></section> : null}
      {providerId === "minimax" ? <section className="film-h3-warning"><h4>{t("film.workbench.capabilityBoundary")}</h4><p>{t("film.workbench.h3Boundary")}</p><p>{t("film.workbench.h3Seed")}</p></section> : null}
    </>
  );
}

function NodePromptEditor({ commandClient, definition, node, onChange, onError, onNotice, projectId }: {
  readonly commandClient: EngineCommandClient;
  readonly definition: FilmNodeDefinitionView;
  readonly node: FilmGraphNodeView;
  readonly onChange: (node: FilmGraphNodeView) => void;
  readonly onError: (message: string) => void;
  readonly onNotice: (message: string) => void;
  readonly projectId: string;
}) {
  const t = useT();
  const [activeSectionId, setActiveSectionId] = useState(node.prompt.sections[0]?.sectionId ?? "");
  const [draft, setDraft] = useState("");
  const [notesDraft, setNotesDraft] = useState(node.prompt.userNotes);
  const [optimizing, setOptimizing] = useState(false);
  const [optimizationNotes, setOptimizationNotes] = useState<readonly string[]>([]);
  const activeSection = node.prompt.sections.find((section) => section.sectionId === activeSectionId)
    ?? node.prompt.sections[0];

  useEffect(() => {
    const firstSectionId = node.prompt.sections[0]?.sectionId ?? "";
    if (!node.prompt.sections.some((section) => section.sectionId === activeSectionId)) {
      setActiveSectionId(firstSectionId);
    }
  }, [activeSectionId, node.nodeId, node.prompt.sections]);

  useEffect(() => setOptimizationNotes([]), [node.nodeId]);

  useEffect(() => {
    setDraft(activeSection?.content ?? "");
  }, [activeSection?.content, activeSection?.sectionId, node.nodeId]);

  useEffect(() => {
    setNotesDraft(node.prompt.userNotes);
  }, [node.nodeId, node.prompt.userNotes]);

  const nodeWithDrafts = useCallback((): FilmGraphNodeView => {
    const promptWithSection = activeSection === undefined
      ? node.prompt
      : replaceFilmPromptSection(node.prompt, { ...activeSection, content: draft });
    return {
      ...node,
      prompt: {
        ...promptWithSection,
        userNotes: notesDraft,
        updatedAt: new Date().toISOString(),
      },
    };
  }, [activeSection, draft, node, notesDraft]);

  const commitDrafts = useCallback(() => {
    if (activeSection === undefined) return;
    if (draft === activeSection.content && notesDraft === node.prompt.userNotes) return;
    onChange(nodeWithDrafts());
  }, [activeSection, draft, node.prompt.userNotes, nodeWithDrafts, notesDraft, onChange]);

  const selectSection = (sectionId: string) => {
    commitDrafts();
    setActiveSectionId(sectionId);
  };

  const restoreSection = () => {
    if (activeSection === undefined) return;
    const templateSection = definition.promptTemplate.sections.find(
      (section) => section.sectionId === activeSection.sectionId,
    );
    if (templateSection === undefined) return;
    const next = {
      ...node,
      prompt: replaceFilmPromptSection(node.prompt, templateSection),
    };
    setDraft(templateSection.content);
    onChange(next);
    onNotice(`已恢复「${templateSection.label}」模板，可使用撤销返回。`);
  };

  const restoreTemplate = () => {
    const prompt = {
      ...definition.promptTemplate,
      userNotes: notesDraft,
      updatedAt: new Date().toISOString(),
    };
    setActiveSectionId(prompt.sections[0]?.sectionId ?? "");
    setDraft(prompt.sections[0]?.content ?? "");
    onChange({ ...node, prompt });
    onNotice("已恢复当前节点的完整提示词模板，可使用撤销返回。");
  };

  const optimize = async () => {
    setOptimizing(true);
    try {
      const result = await commandClient.optimizeFilmNodePrompt({
        kind: "optimize_film_node_prompt",
        projectId,
        node: nodeWithDrafts(),
      });
      const next = { ...node, prompt: result.prompt };
      const nextActive = result.prompt.sections.find(
        (section) => section.sectionId === activeSectionId,
      ) ?? result.prompt.sections[0];
      setDraft(nextActive?.content ?? "");
      setNotesDraft(result.prompt.userNotes);
      setOptimizationNotes([...result.changes, ...result.warnings]);
      onChange(next);
      onNotice(`提示词结构优化完成 · ${result.characterCount} 字符`);
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : "提示词优化失败");
    } finally {
      setOptimizing(false);
    }
  };

  const previewNode = nodeWithDrafts();
  const preview = renderFilmNodePrompt(previewNode.prompt);
  return (
    <section className="film-prompt-editor">
      <header>
        <div><h4>{t("film.workbench.promptWorkbench")}</h4><small>{node.prompt.templateId} · {node.prompt.templateVersion}</small></div>
        <b>{t("film.workbench.characters", { count: preview.length })}</b>
      </header>
      <p>{node.prompt.purpose}</p>
      <label>{t("film.workbench.editSection")}
        <select onChange={(event) => selectSection(event.target.value)} value={activeSection?.sectionId ?? ""}>
          {node.prompt.sections.map((section) => (
            <option key={section.sectionId} value={section.sectionId}>{section.required ? "● " : "○ "}{section.label}</option>
          ))}
        </select>
      </label>
      {activeSection ? (
        <div className="film-prompt-section-editor">
          <div className="film-prompt-sources">
            {activeSection.sources.map((source) => <span className={`is-${source}`} key={source}>{t(`film.promptSource.${source}`)}</span>)}
          </div>
          <small>{activeSection.guidance}</small>
          <textarea
            aria-label={`${activeSection.label}提示词`}
            onBlur={commitDrafts}
            onChange={(event) => setDraft(event.target.value)}
            spellCheck={false}
            value={draft}
          />
        </div>
      ) : <p className="is-error">{t("film.workbench.promptMissing")}</p>}
      <label>{t("film.workbench.userNotes")}
        <textarea
          className="film-prompt-user-notes"
          onBlur={commitDrafts}
          onChange={(event) => setNotesDraft(event.target.value)}
          placeholder={t("film.workbench.userNotesPlaceholder")}
          value={notesDraft}
        />
      </label>
      <div className="film-prompt-actions">
        <button disabled={activeSection === undefined} onClick={restoreSection} type="button">{t("film.workbench.restoreSection")}</button>
        <button onClick={restoreTemplate} type="button">{t("film.workbench.restoreTemplate")}</button>
        <button className="is-primary" disabled={optimizing} onClick={() => void optimize()} type="button">{t(optimizing ? "film.workbench.optimizing" : "film.workbench.optimizeStructure")}</button>
      </div>
      {node.typeId === "h3_context_ir" ? <p className="film-prompt-provider-note">当前 IR 将按 {node.providerId || "目标平台"} / {node.modelId || "目标模型"} 的能力边界编译；上方“结构优化”只整理本地契约，不提交生成任务。</p> : null}
      {optimizationNotes.length > 0 ? <ul className="film-prompt-notes">{optimizationNotes.map((note) => <li key={note}>{note}</li>)}</ul> : null}
      <details className="film-prompt-preview">
        <summary>{t("film.workbench.previewPrompt")}</summary>
        <pre>{preview}</pre>
      </details>
    </section>
  );
}

function NodeInspector({ commandClient, definition, hidden, node, onChange, onDelete, onDuplicate, onError, onNotice, onProviderChange, projectId, providerCatalog, run }: {
  readonly commandClient: EngineCommandClient;
  readonly definition: FilmNodeDefinitionView | undefined;
  readonly hidden?: boolean;
  readonly node: FilmGraphNodeView | undefined;
  readonly onChange: (node: FilmGraphNodeView) => void;
  readonly onDelete: () => void;
  readonly onDuplicate: () => void;
  readonly onError: (message: string) => void;
  readonly onNotice: (message: string) => void;
  readonly onProviderChange: (providerId: string) => void;
  readonly projectId: string;
  readonly providerCatalog: FilmProviderCatalogView;
  readonly run: FilmGraphRunView | null | undefined;
}) {
  const t = useT();
  if (node === undefined) return <aside className="film-node-inspector is-empty" hidden={hidden}><p>{t("film.workbench.selectNodeHint")}</p></aside>;
  const attempt = run?.attempts.find((item) => item.nodeId === node.nodeId);
  return (
    <aside className="film-node-inspector" hidden={hidden}>
      <header><span>NODE INSPECTOR</span><strong>{localizedNodeLabel(node.typeId, node.label, t)}</strong><small>{node.typeId} · v{node.typeVersion}</small></header>
      <section>
        <h4>{t("film.workbench.basicSettings")}</h4>
        <label>{t("film.workbench.nodeName")}<input onChange={(event) => onChange({ ...node, label: event.target.value })} value={node.label} /></label>
        {!isRoutableVideoNode(definition) && node.providerId ? <p className="film-node-locked-route"><span>{t("film.workbench.lockedRoute")}</span><b>{localizedProviderValue(node.providerId, "label", providerCatalog[node.providerId]?.label ?? node.providerId, t)}</b>{node.modelId ? <small>{node.modelId}</small> : null}</p> : null}
        <label className="film-workbench-checkbox"><input checked={node.bypassed} disabled={definition?.allowsBypass === false} onChange={(event) => onChange({ ...node, bypassed: event.target.checked })} type="checkbox" /> {t("film.workbench.bypassNode")}</label>
      </section>
      {definition && isRoutableVideoNode(definition) ? <PlatformVideoInspector catalog={providerCatalog} definition={definition} node={node} onChange={onChange} onProviderChange={onProviderChange} /> : (
        <section><h4>{t("film.workbench.nodeConfig")}</h4><p>{definition?.description ?? t("film.workbench.registeredNode")}</p><small>{Object.keys(node.config).length > 0 ? JSON.stringify(node.config) : t("film.workbench.defaultConfig")}</small></section>
      )}
      {definition ? <NodePromptEditor commandClient={commandClient} definition={definition} node={node} onChange={onChange} onError={onError} onNotice={onNotice} projectId={projectId} /> : null}
      <section>
        <h4>{t("film.workbench.runStatus")}</h4>
        <p>{attempt ? `${runStatusLabel(attempt.status, t)} · ${t("film.workbench.attempt", { n: attempt.attempt })}` : t("film.canvas.notRun")}</p>
        {attempt?.errorMessage ? <p className="is-error">{attempt.errorMessage}</p> : null}
      </section>
      <footer><button onClick={onDuplicate} type="button">{t("film.workbench.duplicate")}</button><button className="is-danger" onClick={onDelete} type="button">{t("film.workbench.delete")}</button></footer>
    </aside>
  );
}

function GuidedPlanning({ catalog, graph, onEnterExpert, runs, studio }: {
  readonly catalog: readonly FilmNodeDefinitionView[];
  readonly graph: FilmGraphView;
  readonly onEnterExpert: () => void;
  readonly runs: readonly FilmGraphRunView[];
  readonly studio: FilmStudioView;
}) {
  const t = useT();
  const locale = useLocale();
  const missingSources = studio.productionBible.sources.filter((source) => !source.exists);
  const lockedAssets = studio.visualAssets.filter((asset) => asset.locked).length;
  const paidNodes = graph.definition.nodes.filter((node) => node.estimatedCostUsd > 0);
  const checkpointCount = graph.definition.nodes.filter((node) => node.humanCheckpoint).length;
  const estimateCost = paidNodes.reduce((total, node) => total + node.estimatedCostUsd, 0);
  return (
    <div className="film-guided-planning">
      <section className="film-guide-hero">
        <header><span>PROJECT INTENT · GRAPH V{graph.definition.revision}</span><h2>{studio.productionBible.title}</h2><p>{studio.productionBible.logline}</p></header>
        <div className="film-guide-facts">
          <article><span>{t("film.workbench.targetFormat")}</span><strong>{studio.productionBible.format}</strong><small>{studio.productionBible.style.aspectRatio} · {studio.productionBible.style.frameRate} FPS</small></article>
          <article className={missingSources.length > 0 ? "is-warning" : "is-ready"}><span>{t("film.workbench.upstreamInputs")}</span><strong>{missingSources.length > 0 ? t("film.workbench.missingCount", { n: missingSources.length }) : t("film.workbench.ready")}</strong><small>{missingSources.map((source) => source.artifactType).join(" / ") || t("film.workbench.sourcesAvailable")}</small></article>
          <article><span>{t("film.workbench.lockedAssets")}</span><strong>{lockedAssets} / {studio.visualAssets.length}</strong><small>{t("film.workbench.assetVersions")}</small></article>
        </div>
      </section>
      <section className="film-guide-section">
        <header><span>01 · CREATIVE PREPARATION</span><h3>{t("film.workbench.creativeReadiness")}</h3></header>
        <div className="film-guide-grid">
          <article><span>{t("film.workbench.visualThesis")}</span><strong>{studio.productionBible.style.visualThesis}</strong><small>{studio.productionBible.style.genre} · {studio.productionBible.style.tone}</small></article>
          <article><span>{t("film.workbench.characterAssets")}</span><strong>{t("film.workbench.characterCount", { count: studio.productionBible.characters.length })}</strong><small>{studio.productionBible.characters.map((item) => item.name).join("、")}</small></article>
          <article><span>{t("film.workbench.sceneSound")}</span><strong>{t("film.workbench.locationCount", { count: studio.productionBible.locations.length })}</strong><small>{studio.productionBible.style.soundThesis}</small></article>
        </div>
      </section>
      <section className="film-guide-section">
        <header><span>02 · WORKFLOW SUMMARY</span><h3>{t("film.workbench.workflowCostTime")}</h3></header>
        <div className="film-guide-metrics">
          <div><b>{graph.definition.nodes.length}</b><span>{t("film.workbench.registeredNodes")}</span></div><div><b>{graph.definition.edges.length}</b><span>{t("film.workbench.typedConnections")}</span></div><div><b>{formatMoney(estimateCost, locale)}</b><span>{t("film.workbench.pathEstimate")}</span></div><div><b>{catalog.length}</b><span>{t("film.workbench.availableNodeTypes")}</span></div>
        </div>
        <p>{issueSummary(graph.validationIssues, t)}{t("film.workbench.estimateNote")}</p>
      </section>
      <section className="film-guide-section">
        <header><span>03 · RISKS & CHECKPOINTS</span><h3>{t("film.workbench.risksCheckpoints")}</h3></header>
        <div className="film-guide-risk-list">
          <p><b>{t("film.workbench.checkpointCount", { count: checkpointCount })}</b><span>{t("film.workbench.checkpointHint")}</span></p>
          {(studio.notices.length > 0 ? studio.notices : [t("film.workbench.paidEstimateNotice")]).map((notice) => <p key={notice}><b>{t("film.workbench.notice")}</b><span>{notice}</span></p>)}
        </div>
      </section>
      <section className="film-guide-section is-canvas-entry">
        <div><span>04 · EXPERT GRAPH</span><h3>{t("film.workbench.enterCanvas")}</h3><p>{t("film.workbench.sharedGraph")}</p></div>
        <button className="is-primary" onClick={onEnterExpert} type="button">{t("film.workbench.openExpertCanvas")}</button>
      </section>
      <section className="film-guide-section"><header><span>RECENT RUNS</span><h3>{t("film.workbench.recentRuns")}</h3></header><RunHistory onCancel={() => undefined} runs={runs.slice(0, 4)} /></section>
    </div>
  );
}

export const FilmWorkflowWorkbench = memo(function FilmWorkflowWorkbench({
  canvasRequest = 0,
  commandClient,
  engineClient,
  onError,
  onNotice,
  onCanvasFocusChange,
  studio,
}: FilmWorkflowWorkbenchProps) {
  const t = useT();
  const [mode, setMode] = useState<WorkbenchMode>("guided");
  const [graphView, setGraphView] = useState<FilmGraphView | null>(null);
  const [catalog, setCatalog] = useState<readonly FilmNodeDefinitionView[]>([]);
  const [providerCatalog, setProviderCatalog] = useState<FilmProviderCatalogView>({});
  const [runs, setRuns] = useState<readonly FilmGraphRunView[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("全部");
  const [queueOpen, setQueueOpen] = useState(true);
  const [catalogOpen, setCatalogOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [snapEnabled, setSnapEnabled] = useState(true);
  const [focusMode, setFocusMode] = useState(false);
  const [fitRequestId, setFitRequestId] = useState(0);
  const [layoutReady, setLayoutReady] = useState(false);
  const [estimate, setEstimate] = useState<FilmRunEstimateView | null>(null);
  const [busy, setBusy] = useState("");
  const [dirty, setDirty] = useState(false);
  const [past, setPast] = useState<readonly FilmGraphDefinitionView[]>([]);
  const [future, setFuture] = useState<readonly FilmGraphDefinitionView[]>([]);
  const clipboardNode = useRef<FilmGraphNodeView | null>(null);
  const liveDefinition = useRef<FilmGraphDefinitionView | null>(null);
  const rootRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const stored = window.localStorage.getItem("nimo.film.workbench-mode");
    if (stored === "guided" || stored === "expert") setMode(stored);
    try {
      const layout = JSON.parse(window.localStorage.getItem("nimo.film.expert-layout.v1") ?? "{}") as {
        readonly catalogOpen?: boolean;
        readonly inspectorOpen?: boolean;
        readonly snapEnabled?: boolean;
      };
      if (typeof layout.catalogOpen === "boolean") setCatalogOpen(layout.catalogOpen);
      if (typeof layout.inspectorOpen === "boolean") setInspectorOpen(layout.inspectorOpen);
      if (typeof layout.snapEnabled === "boolean") setSnapEnabled(layout.snapEnabled);
    } catch {
      // Ignore stale preferences and keep the accessible default layout.
    }
    setLayoutReady(true);
  }, []);

  useEffect(() => {
    if (!layoutReady) return;
    window.localStorage.setItem("nimo.film.expert-layout.v1", JSON.stringify({
      catalogOpen,
      inspectorOpen,
      snapEnabled,
    }));
  }, [catalogOpen, inspectorOpen, layoutReady, snapEnabled]);

  useEffect(() => {
    onCanvasFocusChange?.(focusMode);
  }, [focusMode, onCanvasFocusChange]);

  useEffect(() => () => onCanvasFocusChange?.(false), [onCanvasFocusChange]);

  useEffect(() => {
    if (!layoutReady || mode !== "expert") return;
    const frame = window.requestAnimationFrame(() => setFitRequestId((request) => request + 1));
    return () => window.cancelAnimationFrame(frame);
  }, [catalogOpen, focusMode, inspectorOpen, layoutReady, mode, queueOpen]);

  const changeMode = useCallback((next: WorkbenchMode) => {
    if (next !== "expert") setFocusMode(false);
    setMode(next);
    window.localStorage.setItem("nimo.film.workbench-mode", next);
    window.requestAnimationFrame(() => {
      rootRef.current?.closest<HTMLElement>(".film-workspace-grid")?.scrollTo({ top: 0 });
    });
  }, []);

  // Explicit navigation also works when planning is already mounted.
  useEffect(() => {
    if (canvasRequest > 0) changeMode("expert");
  }, [canvasRequest, changeMode]);

  const toggleCatalog = useCallback(() => {
    if (focusMode) {
      setFocusMode(false);
      setCatalogOpen(true);
      return;
    }
    setCatalogOpen((open) => !open);
  }, [focusMode]);

  const toggleInspector = useCallback(() => {
    if (focusMode) {
      setFocusMode(false);
      setInspectorOpen(true);
      return;
    }
    setInspectorOpen((open) => !open);
  }, [focusMode]);

  const reload = useCallback(async () => {
    setBusy("正在读取工作流");
    try {
      const [nextGraph, nextCatalog, nextProviderCatalog, nextRuns] = await Promise.all([
        engineClient.getFilmGraph(studio.projectId),
        engineClient.getFilmNodeCatalog(),
        engineClient.getFilmProviderCatalog(),
        engineClient.listFilmGraphRuns(studio.projectId, 30),
      ]);
      setGraphView(nextGraph);
      liveDefinition.current = nextGraph.definition;
      setCatalog(nextCatalog);
      setProviderCatalog(nextProviderCatalog);
      setRuns(nextRuns);
      setSelectedNodeId((current) => current || nextGraph.definition.nodes[0]?.nodeId || "");
      setDirty(false);
      setPast([]);
      setFuture([]);
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : "读取 v2 工作流失败");
    } finally {
      setBusy("");
    }
  }, [engineClient, onError, studio.projectId]);

  useEffect(() => { void reload(); }, [reload]);

  const applyDefinition = useCallback((next: FilmGraphDefinitionView, record = true) => {
    setGraphView((current) => {
      if (current === null) return current;
      if (record) setPast((items) => [...items.slice(-49), current.definition]);
      setFuture([]);
      liveDefinition.current = next;
      return { ...current, definition: next };
    });
    setDirty(true);
  }, []);

  const save = useCallback(async () => {
    const definition = liveDefinition.current;
    if (definition === null || busy.length > 0) return;
    setBusy("正在保存图版本");
    try {
      const saved = await commandClient.saveFilmGraph({ kind: "save_film_graph", projectId: studio.projectId, expectedRevision: definition.revision, graph: definition });
      setGraphView(saved);
      liveDefinition.current = saved.definition;
      setDirty(false);
      onNotice(`工作流已保存为版本 ${saved.definition.revision}`);
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : "保存工作流失败；可能存在版本冲突");
    } finally {
      setBusy("");
    }
  }, [busy.length, commandClient, onError, onNotice, studio.projectId]);

  useEffect(() => {
    if (!dirty || busy.length > 0) return;
    const timer = window.setTimeout(() => { void save(); }, 900);
    return () => window.clearTimeout(timer);
  }, [busy.length, dirty, save]);

  const undo = useCallback(() => {
    if (graphView === null || past.length === 0) return;
    const previous = past.at(-1);
    if (previous === undefined) return;
    setPast((items) => items.slice(0, -1));
    setFuture((items) => [graphView.definition, ...items].slice(0, 50));
    liveDefinition.current = previous;
    setGraphView({ ...graphView, definition: previous });
    setDirty(true);
  }, [graphView, past]);

  const redo = useCallback(() => {
    if (graphView === null || future.length === 0) return;
    const next = future[0];
    if (next === undefined) return;
    setFuture((items) => items.slice(1));
    setPast((items) => [...items.slice(-49), graphView.definition]);
    liveDefinition.current = next;
    setGraphView({ ...graphView, definition: next });
    setDirty(true);
  }, [future, graphView]);

  const addNode = useCallback((definition: FilmNodeDefinitionView, source?: FilmGraphNodeView) => {
    if (graphView === null) return;
    const suffix = Math.random().toString(36).slice(2, 7);
    const nodeId = `${definition.typeId}-${suffix}`;
    const maxX = Math.max(0, ...graphView.definition.nodes.map((node) => node.position.x));
    const nextNode: FilmGraphNodeView = {
      nodeId,
      typeId: definition.typeId,
      typeVersion: definition.version,
      label: source ? `${source.label} 副本` : definition.label,
      stage: definition.stage,
      position: source ? { x: source.position.x + 42, y: source.position.y + 42 } : { x: maxX + 280, y: 80 },
      groupId: "",
      config: source?.config ?? definition.defaultConfig,
      prompt: source?.prompt ?? definition.promptTemplate,
      inputPorts: definition.inputPorts,
      outputPorts: definition.outputPorts,
      providerId: source?.providerId ?? (definition.providerCapabilities.some((item) => item.startsWith("minimax:")) ? "minimax" : ""),
      modelId: source?.modelId ?? (["h3_context_ir", "minimax_h3_video"].includes(definition.typeId) ? "MiniMax-H3" : ""),
      bypassed: false,
      disabled: false,
      humanCheckpoint: definition.humanCheckpoint,
      estimatedCostUsd: definition.estimatedCostUsd,
    };
    applyDefinition({ ...graphView.definition, nodes: [...graphView.definition.nodes, nextNode] });
    setSelectedNodeId(nodeId);
  }, [applyDefinition, graphView]);

  const selectedNode = graphView?.definition.nodes.find((node) => node.nodeId === selectedNodeId);
  const selectedDefinition = catalog.find((item) => item.typeId === selectedNode?.typeId);
  const graphProviderId = isRoutableVideoNode(selectedDefinition)
    ? selectedNode?.providerId ?? ""
    : graphView?.definition.nodes.find((node) => isRoutableVideoNode(catalog.find((item) => item.typeId === node.typeId)))?.providerId ?? "";

  const switchGraphProvider = useCallback((providerId: string) => {
    if (graphView === null) return;
    const routed = routeFilmGraphProvider(graphView.definition, catalog, providerCatalog, providerId);
    if (routed.changedNodeCount === 0) return;
    applyDefinition(routed.graph);
    const provider = providerCatalog[providerId];
    onNotice(t("film.workbench.graphProviderChanged", { count: routed.changedNodeCount, provider: localizedProviderValue(providerId, "label", provider?.label ?? providerId, t) }));
  }, [applyDefinition, catalog, graphView, onNotice, providerCatalog, t]);

  const duplicateSelected = useCallback(() => {
    if (selectedNode === undefined || selectedDefinition === undefined) return;
    addNode(selectedDefinition, selectedNode);
  }, [addNode, selectedDefinition, selectedNode]);

  const deleteSelected = useCallback(() => {
    if (graphView === null || selectedNode === undefined) return;
    applyDefinition({
      ...graphView.definition,
      nodes: graphView.definition.nodes.filter((node) => node.nodeId !== selectedNode.nodeId),
      edges: graphView.definition.edges.filter((edge) => edge.sourceNodeId !== selectedNode.nodeId && edge.targetNodeId !== selectedNode.nodeId),
    });
    setSelectedNodeId("");
  }, [applyDefinition, graphView, selectedNode]);

  const validate = useCallback(async () => {
    if (graphView === null) return;
    setBusy("正在校验");
    try {
      const issues = await commandClient.validateFilmGraph({ kind: "validate_film_graph", projectId: studio.projectId, graph: graphView.definition });
      setGraphView((current) => current === null ? current : { ...current, validationIssues: issues });
      onNotice(issueSummary(issues, t));
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : "工作流校验失败");
    } finally { setBusy(""); }
  }, [commandClient, graphView, onError, onNotice, studio.projectId, t]);

  const requestRun = useCallback(async (scope: FilmGraphRunScope) => {
    if (graphView === null) return;
    if (scope !== "all" && selectedNodeId.length === 0) { onError("请先选择目标节点。"); return; }
    setBusy("正在估算运行");
    try {
      const next = await commandClient.estimateFilmGraphRun({ kind: "estimate_film_graph_run", projectId: studio.projectId, scope, targetNodeIds: scope === "all" ? [] : [selectedNodeId] });
      setEstimate(next);
    } catch (caught) { onError(caught instanceof Error ? caught.message : "运行估算失败"); }
    finally { setBusy(""); }
  }, [commandClient, graphView, onError, selectedNodeId, studio.projectId]);

  const confirmRun = useCallback(async (highPriority: boolean) => {
    if (estimate === null) return;
    setBusy("正在创建运行");
    try {
      const run = await commandClient.createFilmGraphRun({ kind: "create_film_graph_run", projectId: studio.projectId, scope: estimate.scope, targetNodeIds: estimate.targetNodeIds, confirmedCost: true, highPriority });
      setRuns((items) => [run, ...items.filter((item) => item.runId !== run.runId)]);
      setGraphView((current) => current === null ? current : { ...current, latestRun: run });
      setEstimate(null);
      setQueueOpen(true);
      onNotice(`运行 ${run.runId} 已创建：${runStatusLabel(run.status, t)}`);
    } catch (caught) { onError(caught instanceof Error ? caught.message : "创建运行失败"); }
    finally { setBusy(""); }
  }, [commandClient, estimate, onError, onNotice, studio.projectId, t]);

  const cancelRun = useCallback(async (runId: string) => {
    setBusy("正在取消运行");
    try {
      const run = await commandClient.cancelFilmGraphRun({ kind: "cancel_film_graph_run", projectId: studio.projectId, runId });
      setRuns((items) => items.map((item) => item.runId === runId ? run : item));
      setGraphView((current) => current === null ? current : { ...current, latestRun: current.latestRun?.runId === runId ? run : current.latestRun ?? null });
      onNotice(`运行 ${runId} 已取消`);
    } catch (caught) { onError(caught instanceof Error ? caught.message : "取消运行失败"); }
    finally { setBusy(""); }
  }, [commandClient, onError, onNotice, studio.projectId]);

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select, [contenteditable=true]")) return;
      const modifier = event.metaKey || event.ctrlKey;
      if (modifier && event.key.toLowerCase() === "s") { event.preventDefault(); void save(); }
      else if (modifier && event.key.toLowerCase() === "z" && !event.shiftKey) { event.preventDefault(); undo(); }
      else if (modifier && (event.key.toLowerCase() === "y" || (event.key.toLowerCase() === "z" && event.shiftKey))) { event.preventDefault(); redo(); }
      else if (modifier && event.key.toLowerCase() === "c" && selectedNode !== undefined) clipboardNode.current = selectedNode;
      else if (modifier && event.key.toLowerCase() === "v" && clipboardNode.current !== null) {
        const definition = catalog.find((item) => item.typeId === clipboardNode.current?.typeId);
        if (definition !== undefined) addNode(definition, clipboardNode.current);
      } else if (event.key.toLowerCase() === "b" && selectedNode !== undefined && selectedDefinition?.allowsBypass !== false) {
        applyDefinition(replaceNode(graphView!.definition, selectedNode.nodeId, (node) => ({ ...node, bypassed: !node.bypassed })));
      } else if (event.shiftKey && event.key === "Enter") { event.preventDefault(); void requestRun("selected"); }
      else if (event.key.toLowerCase() === "q") setQueueOpen((open) => !open);
      else if (mode === "expert" && event.key === "Escape" && focusMode) setFocusMode(false);
      else if (mode === "expert" && event.key.toLowerCase() === "f") setFocusMode((focused) => !focused);
      else if (mode === "expert" && event.key === "[") toggleCatalog();
      else if (mode === "expert" && event.key === "]") toggleInspector();
      else if (mode === "expert" && event.shiftKey && event.key.toLowerCase() === "a" && graphView !== null) {
        event.preventDefault();
        applyDefinition(autoLayoutFilmGraph(graphView.definition));
        setFitRequestId((request) => request + 1);
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [addNode, applyDefinition, catalog, focusMode, graphView, mode, redo, requestRun, save, selectedDefinition, selectedNode, toggleCatalog, toggleInspector, undo]);

  const categories = useMemo(() => ["全部", ...new Set(catalog.map((item) => item.category))], [catalog]);
  const visibleCatalog = useMemo(() => catalog.filter((item) => {
    const matchesCategory = category === "全部" || item.category === category;
    const needle = query.trim().toLowerCase();
    return matchesCategory && (needle.length === 0 || `${item.label} ${item.typeId} ${item.description}`.toLowerCase().includes(needle));
  }), [catalog, category, query]);

  let content: ReactNode;
  if (graphView === null) content = <div className="film-workbench-loading"><span />{busy || t("film.workbench.loading")}</div>;
  else if (mode === "guided") content = <GuidedPlanning catalog={catalog} graph={graphView} onEnterExpert={() => changeMode("expert")} runs={runs} studio={studio} />;
  else content = (
    <div className={filmExpertWorkbenchClassName({ catalogOpen, focusMode, inspectorOpen, queueOpen })}>
      <aside className="film-node-catalog" hidden={!catalogOpen || focusMode}>
        <header><div><span>REGISTERED NODES</span><strong>{t("film.workbench.nodeCatalogTitle")}</strong></div><small>{catalog.length}</small></header>
        <label className="film-node-search"><span>⌕</span><input aria-label={t("film.workbench.searchNode")} onChange={(event) => setQuery(event.target.value)} placeholder={t("film.workbench.searchNodePlaceholder")} value={query} /></label>
        <div className="film-node-categories">{categories.map((item) => <button className={category === item ? "is-active" : ""} key={item} onClick={() => setCategory(item)} type="button">{item === "全部" ? t("film.workbench.allCategories") : localizedCategoryLabel(item, t)}</button>)}</div>
        <div className="film-node-catalog-list">{visibleCatalog.map((definition) => <button key={definition.typeId} onClick={() => addNode(definition)} title={definition.description} type="button"><i className={definition.paid ? "is-paid" : ""} /><span><b>{localizedNodeLabel(definition.typeId, definition.label, t)}</b><small>{definition.typeId}</small></span>{definition.paid ? <em>{t("film.workbench.paidBadge")}</em> : null}</button>)}</div>
        <footer>{t("film.workbench.catalogFooter", { count: graphView.definition.nodes.length })}</footer>
      </aside>
      <main className="film-graph-main">
        <div className="film-graph-toolbar" role="toolbar" aria-label={t("film.workbench.canvasToolbar")}>
          <div aria-label={t("film.workbench.viewControls")} className="film-graph-view-controls" role="group">
            <button aria-pressed={catalogOpen && !focusMode} onClick={toggleCatalog} title={t(catalogOpen && !focusMode ? "film.workbench.hideCatalogTitle" : "film.workbench.showCatalogTitle")} type="button"><span aria-hidden="true">▥</span><b>{t("film.workbench.nodeCatalog")}</b></button>
            <button aria-pressed={inspectorOpen && !focusMode} onClick={toggleInspector} title={t(inspectorOpen && !focusMode ? "film.workbench.hideInspectorTitle" : "film.workbench.showInspectorTitle")} type="button"><span aria-hidden="true">▤</span><b>{t("film.workbench.inspector")}</b></button>
            <button aria-pressed={focusMode} className={focusMode ? "is-active" : ""} onClick={() => setFocusMode((focused) => !focused)} title={t(focusMode ? "film.workbench.exitFocusTitle" : "film.workbench.focusTitle")} type="button"><span aria-hidden="true">⛶</span><b>{t(focusMode ? "film.workbench.exitFocus" : "film.workbench.focusCanvas")}</b></button>
          </div>
          <span />
          <button disabled={past.length === 0} onClick={undo} title={t("film.workbench.undoTitle")} type="button">↶</button><button disabled={future.length === 0} onClick={redo} title={t("film.workbench.redoTitle")} type="button">↷</button>
          <span />
          <button onClick={() => { applyDefinition(autoLayoutFilmGraph(graphView.definition)); setFitRequestId((request) => request + 1); }} title={t("film.workbench.arrangeTitle")} type="button">{t("film.workbench.arrange")}</button>
          <button aria-pressed={snapEnabled} className={snapEnabled ? "is-active" : ""} onClick={() => setSnapEnabled((enabled) => !enabled)} title={t("film.workbench.snapTitle")} type="button">{t("film.workbench.snap")}</button>
          {Object.keys(providerCatalog).length > 0 ? <label className="film-graph-provider-select"><span>{t("film.workbench.canvasPlatform")}</span><select aria-label={t("film.workbench.canvasPlatform")} onChange={(event) => switchGraphProvider(event.target.value)} value={graphProviderId}>{Object.entries(providerCatalog).filter(([, provider]) => provider.videoModels.length > 0).map(([providerId, provider]) => <option key={providerId} value={providerId}>{localizedProviderValue(providerId, "short", provider.shortLabel ?? provider.label, t)}</option>)}</select></label> : null}
          <button onClick={() => void validate()} type="button">{t("film.workbench.validate")}</button>
          <button className={dirty ? "is-dirty" : ""} onClick={() => void save()} type="button">{t(dirty ? "film.workbench.saveChanges" : "film.workbench.saved")}</button>
          <i>{busy || issueSummary(graphView.validationIssues, t)}</i>
          <button onClick={() => void requestRun("selected")} title={t("film.workbench.runNodeTitle")} type="button">{t("film.workbench.runNode")}</button>
          <button onClick={() => void requestRun("to_node")} type="button">{t("film.workbench.runToHere")}</button>
          <button onClick={() => void requestRun("downstream")} type="button">{t("film.workbench.runDownstream")}</button>
          <button className="is-primary" onClick={() => void requestRun("all")} type="button">{t("film.workbench.runTerminal")}</button>
        </div>
        <FilmGraphCanvas
          catalog={catalog}
          graph={graphView.definition}
          fitRequestId={fitRequestId}
          latestRun={graphView.latestRun ?? null}
          onConnectionError={onError}
          onGraphChange={applyDefinition}
          onSelectedNodeChange={setSelectedNodeId}
          selectedNodeId={selectedNodeId}
          snapEnabled={snapEnabled}
        />
      </main>
      <NodeInspector
        commandClient={commandClient}
        definition={selectedDefinition}
        hidden={!inspectorOpen || focusMode}
        node={selectedNode}
        onChange={(next) => applyDefinition(replaceNode(graphView.definition, next.nodeId, () => next))}
        onDelete={deleteSelected}
        onDuplicate={duplicateSelected}
        onError={onError}
        onNotice={onNotice}
        onProviderChange={switchGraphProvider}
        projectId={studio.projectId}
        providerCatalog={providerCatalog}
        run={graphView.latestRun}
      />
      <section className={`film-queue-drawer ${queueOpen ? "is-open" : ""}`} hidden={focusMode}>
        <button aria-expanded={queueOpen} onClick={() => setQueueOpen((open) => !open)} type="button"><span>⌄</span>{t("film.workbench.runQueueHistory")} <b>{runs.length}</b><small>{t("film.workbench.queueShortcut")}</small></button>
        {queueOpen ? <RunHistory onCancel={(runId) => void cancelRun(runId)} runs={runs} /> : null}
      </section>
      {estimate ? <EstimatePanel estimate={estimate} onCancel={() => setEstimate(null)} onConfirm={(priority) => void confirmRun(priority)} /> : null}
    </div>
  );

  return (
    <section className={`film-workflow-workbench is-${mode} ${focusMode ? "is-focus-mode" : ""}`} ref={rootRef}>
      <header className="film-workbench-modebar">
        <div><span>{t("film.workbench.brand")}</span><strong>{t("film.workbench.subtitle")}</strong></div>
        <div role="tablist" aria-label={t("film.workbench.modeAria")}><button aria-selected={mode === "guided"} className={mode === "guided" ? "is-active" : ""} onClick={() => changeMode("guided")} role="tab" type="button">{t("film.workbench.guided")}</button><button aria-selected={mode === "expert"} className={mode === "expert" ? "is-active" : ""} onClick={() => changeMode("expert")} role="tab" type="button">{t("film.workbench.expert")}</button></div>
      </header>
      {content}
    </section>
  );
});
