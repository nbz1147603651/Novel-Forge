import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { Select } from "radix-ui";
import {
  memo,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type {
  EngineClient,
  EngineCommandClient,
  FilmComplianceReportView,
  FilmDeliveryManifestView,
  FilmJobView,
  FilmMediaArtifactView,
  FilmProviderCatalogView,
  FilmQcReportView,
  FilmShotView,
  FilmStageId,
  FilmStudioView,
  FilmVisualAssetView,
  FilmVisionQcReportView,
  ProjectView,
} from "@nimo/engine-contracts";

import type { FilmStudioFormatId } from "../lib/ui-session";
import { useT } from "../lib/i18n";
import { ComicWorkspace } from "./ComicWorkspace";
import { DramaWorkspace } from "./DramaWorkspace";
import { filmStageClassName } from "./film-workflow-utils";
import { FilmWorkflowWorkbench } from "./FilmWorkflowWorkbench";

interface FilmStudioPageProps {
  readonly catalog: FilmProviderCatalogView;
  readonly commandClient: EngineCommandClient;
  readonly engineClient: EngineClient;
  /** 当前激活的制片形态：长篇剧集 / 竖屏短剧 / 漫画分镜（三者同属映界一个模块）。 */
  readonly format: FilmStudioFormatId;
  readonly onFormatChange: (format: FilmStudioFormatId) => void;
  readonly onProjectChange: (projectId: string) => void;
  readonly onStudioChange: (studio: FilmStudioView) => void;
  readonly projects: readonly ProjectView[];
  readonly studio: FilmStudioView;
}

/**
 * 映界三种产出形态（同一模块、分别交付）：
 * 顺序即生产逻辑——先主制片线（长篇剧集），再快速交付线（竖屏短剧），
 * 最后分镜衍生线（漫画分镜，可回流前两者）。
 */
const FORMAT_META: readonly { readonly id: FilmStudioFormatId; readonly labelKey: string; readonly captionKey: string }[] = [
  { id: "feature", labelKey: "film.format.feature", captionKey: "film.format.featureCaption" },
  { id: "drama", labelKey: "film.format.drama", captionKey: "film.format.dramaCaption" },
  { id: "comic", labelKey: "film.format.comic", captionKey: "film.format.comicCaption" },
];

/** 映界形态切换器：短剧与漫画不再是独立页面，而是映界的制片形态。 */
const FilmFormatSwitcher = memo(function FilmFormatSwitcher({
  format,
  onChange,
}: {
  readonly format: FilmStudioFormatId;
  readonly onChange: (format: FilmStudioFormatId) => void;
}) {
  const t = useT();
  const activeIndex = Math.max(0, FORMAT_META.findIndex((item) => item.id === format));
  return (
    <div className="film-format-switch" role="tablist" aria-label={t("film.format.ariaLabel")}>
      <i aria-hidden="true" className="film-format-thumb" style={{ "--film-format-index": activeIndex } as CSSProperties} />
      {FORMAT_META.map((item) => (
        <button
          aria-selected={format === item.id}
          className={format === item.id ? "is-active" : ""}
          key={item.id}
          onClick={() => onChange(item.id)}
          role="tab"
          title={t(item.captionKey)}
          type="button"
        >
          <strong>{t(item.labelKey)}</strong>
          <small>{t(item.captionKey)}</small>
        </button>
      ))}
    </div>
  );
});

interface SelectOption {
  readonly value: string;
  readonly label: string;
  readonly description?: string;
}

interface ModelOption extends SelectOption {
  readonly aspectRatios: readonly string[];
  readonly defaultDuration: number | null;
  readonly defaultResolution: string;
  readonly durations: readonly number[];
  readonly features: readonly string[];
  readonly modes: readonly string[];
  readonly recommended: boolean;
  readonly resolutions: readonly string[];
}

interface RenderSpec {
  readonly width: number | null;
  readonly height: number | null;
  readonly frameRate: number | null;
  readonly burnSubtitles: boolean;
}

interface RouteDefaults {
  readonly imageProvider: string;
  readonly imageModel: string;
  readonly videoProvider: string;
  readonly videoModel: string;
}

const stageMeta: readonly { readonly id: FilmStageId; readonly index: string; readonly labelKey: string }[] = [
  { id: "planning", index: "01", labelKey: "film.stage.planning" },
  { id: "screenplay", index: "02", labelKey: "film.stage.screenplay" },
  { id: "visual_development", index: "03", labelKey: "film.stage.visualDevelopment" },
  { id: "storyboard", index: "04", labelKey: "film.stage.storyboard" },
  { id: "shot_production", index: "05", labelKey: "film.stage.shotProduction" },
  { id: "sound_picture", index: "06", labelKey: "film.stage.soundPicture" },
  { id: "edit", index: "07", labelKey: "film.stage.edit" },
  { id: "compliance", index: "08", labelKey: "film.stage.compliance" },
  { id: "delivery", index: "09", labelKey: "film.stage.delivery" },
];

const featureLabelKey = (feature: string): string => `film.feature.${feature}`;
const jobKindLabelKey = (kind: string): string => `film.job.${kind}`;

const resolutionPresets: readonly { readonly value: string; readonly label: string; readonly width: number; readonly height: number }[] = [
  { value: "720", label: "1280 × 720 · HD", width: 1280, height: 720 },
  { value: "1080", label: "1920 × 1080 · FHD", width: 1920, height: 1080 },
  { value: "1440", label: "2560 × 1440 · QHD", width: 2560, height: 1440 },
  { value: "2160", label: "3840 × 2160 · UHD", width: 3840, height: 2160 },
];

function stringList(value: unknown): readonly string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function durationValues(value: unknown): readonly number[] {
  if (Array.isArray(value)) {
    return value.filter((item): item is number => typeof item === "number" && Number.isInteger(item));
  }
  if (typeof value !== "object" || value === null) return [];
  const domain = value as Readonly<Record<string, unknown>>;
  const min = numberValue(domain.min);
  const max = numberValue(domain.max);
  if (min === null || max === null || min > max) return [];
  return Array.from({ length: Math.floor(max) - Math.ceil(min) + 1 }, (_, index) => Math.ceil(min) + index);
}

function modelOptions(
  catalog: FilmProviderCatalogView,
  providerId: string,
  kind: "imageModels" | "videoModels",
  t?: ReturnType<typeof useT>,
): readonly ModelOption[] {
  const models = catalog[providerId]?.[kind] ?? [];
  return models.flatMap((model) => {
    const id = typeof model.id === "string" ? model.id : "";
    if (id.length === 0) return [];
    return [{
      value: id,
      label: t ? localizedCatalogValue(t, `film.model.${id}`, typeof model.label === "string" ? model.label : id) : typeof model.label === "string" ? model.label : id,
      aspectRatios: stringList(model.aspectRatios),
      defaultDuration: numberValue(model.defaultDuration),
      defaultResolution: typeof model.defaultResolution === "string" ? model.defaultResolution : "",
      durations: durationValues(model.durations),
      features: stringList(model.features),
      modes: stringList(model.modes),
      recommended: model.recommended === true,
      resolutions: stringList(model.resolutions),
    }];
  });
}

function preferredModel(options: readonly ModelOption[]): ModelOption | undefined {
  return options.find((option) => option.recommended) ?? options[0];
}

function clampDuration(value: number, model: ModelOption | undefined): number {
  const durations = model?.durations ?? [];
  if (durations.length === 0) return Math.max(2, Math.min(30, Math.round(value)));
  return durations.reduce((closest, candidate) => (
    Math.abs(candidate - value) < Math.abs(closest - value) ? candidate : closest
  ));
}

function resolvedResolution(value: string, model: ModelOption | undefined): string {
  const resolutions = model?.resolutions ?? [];
  if (resolutions.includes(value)) return value;
  if (model?.defaultResolution && resolutions.includes(model.defaultResolution)) return model.defaultResolution;
  return resolutions[0] ?? (value || "1080P");
}

function resolvedAspectRatio(value: string, model: ModelOption | undefined, allowAdaptive: boolean): string {
  const declared = model?.aspectRatios ?? [];
  const options = [
    ...(allowAdaptive ? ["adaptive"] : []),
    ...(declared.length > 0 ? declared : ["16:9", "9:16", "1:1", "4:3", "3:4"]),
  ];
  return options.includes(value) ? value : options[0] ?? "16:9";
}

function localizedCatalogValue(t: ReturnType<typeof useT>, key: string, fallback: string): string {
  const translated = t(key);
  return translated === key ? fallback : translated;
}

function providerOptions(catalog: FilmProviderCatalogView, t: ReturnType<typeof useT>, short = false): readonly SelectOption[] {
  return Object.entries(catalog).map(([value, provider]) => ({
    value,
    label: localizedCatalogValue(
      t,
      `film.provider.${value}.${short ? "short" : "label"}`,
      short ? provider.shortLabel ?? provider.label : provider.label,
    ),
    ...(provider.bestFor ? { description: localizedCatalogValue(t, `film.provider.${value}.bestFor`, provider.bestFor) } : {}),
  }));
}

function statusLabel(status: string, t: ReturnType<typeof useT>): string {
  return localizedCatalogValue(t, `film.status.${status}`, status);
}

function formatBytes(size: number): string {
  if (size <= 0) return "—";
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function shortId(value: string, length = 8): string {
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

function StudioSelect({
  ariaLabel,
  disabled = false,
  onChange,
  options,
  value,
}: {
  readonly ariaLabel: string;
  readonly disabled?: boolean;
  readonly onChange: (value: string) => void;
  readonly options: readonly SelectOption[];
  readonly value: string;
}) {
  const t = useT();
  const selected = options.find((item) => item.value === value) ?? options[0];
  return (
    <Select.Root disabled={disabled} onValueChange={onChange} value={selected?.value ?? ""}>
      <Select.Trigger aria-label={ariaLabel} className="film-select-trigger">
        <Select.Value>{selected?.label ?? t("film.common.notConfigured")}</Select.Value>
        <Select.Icon aria-hidden="true">⌄</Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Content className="film-select-content" position="popper" sideOffset={5}>
          <Select.Viewport>
            {options.map((option) => (
              <Select.Item className="film-select-item" key={option.value} value={option.value}>
                <Select.ItemText>{option.label}</Select.ItemText>
                {option.description ? <small>{option.description}</small> : null}
                <Select.ItemIndicator aria-hidden="true">✓</Select.ItemIndicator>
              </Select.Item>
            ))}
          </Select.Viewport>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  );
}

const StageRail = memo(function StageRail({
  active,
  studio,
  onSelect,
}: {
  readonly active: FilmStageId;
  readonly studio: FilmStudioView;
  readonly onSelect: (stage: FilmStageId) => void;
}) {
  const t = useT();
  const statuses = useMemo(
    () => new Map(studio.stages.map((item) => [item.stage, item.status])),
    [studio.stages],
  );
  return (
    <nav aria-label={t("film.stage.ariaLabel")} className="film-stage-rail">
      {stageMeta.map((item) => {
        const status = statuses.get(item.id) ?? "pending";
        const label = t(item.labelKey);
        return (
          <button
            aria-current={active === item.id ? "step" : undefined}
            className={filmStageClassName(active === item.id, status)}
            key={item.id}
            onClick={() => onSelect(item.id)}
            title={status === "active" ? `${label} · ${t("film.stage.backgroundRunning")}` : label}
            type="button"
          >
            <span>{item.index}</span><strong>{label}</strong><i aria-hidden="true" />
          </button>
        );
      })}
    </nav>
  );
});

const SceneRail = memo(function SceneRail({
  activeSceneId,
  studio,
  onSelect,
}: {
  readonly activeSceneId: string;
  readonly studio: FilmStudioView;
  readonly onSelect: (sceneId: string) => void;
}) {
  const t = useT();
  let previousChapter: number | null | undefined;
  return (
    <aside className="film-scene-rail">
      <header><span>{t("film.sceneRail.title")}</span><b>{studio.screenplay.scenes.length}</b></header>
      <div className="film-scene-list">
        {studio.screenplay.scenes.map((scene) => {
          const shots = studio.shots.filter((shot) => shot.sceneId === scene.sceneId);
          const approved = shots.filter((shot) => shot.locked || shot.qcStatus === "approved" || shot.qcStatus === "passed").length;
          const showChapter = scene.sourceChapter !== previousChapter;
          previousChapter = scene.sourceChapter;
          return (
            <div className="film-scene-entry" key={scene.sceneId}>
              {showChapter ? <small className="film-source-chapter">{t("film.sceneRail.sourceChapter", { n: scene.sourceChapter ?? "—" })}</small> : null}
              <button className={activeSceneId === scene.sceneId ? "is-active" : ""} onClick={() => onSelect(scene.sceneId)} type="button">
                <span>SC {String(scene.sequenceNumber).padStart(2, "0")}</span>
                <strong>{scene.heading.replace(/^内景\s*·\s*/, "")}</strong>
                <small>{scene.timeOfDay} · {t("film.unit.seconds", { n: Math.round(scene.durationS) })}</small>
                <i style={{ "--film-scene-progress": `${shots.length === 0 ? 0 : (approved / shots.length) * 100}%` } as CSSProperties} />
              </button>
            </div>
          );
        })}
      </div>
      <section className="film-lineage-card">
        <span>{t("film.sceneRail.upstreamFusion")}</span>
        <strong>{t("film.sceneRail.sourcesConnected", { n: studio.productionBible.sources.filter((item) => item.exists).length })}</strong>
        <small>{t("film.sceneRail.traceableVersions")}</small>
      </section>
    </aside>
  );
});

function SourceLockStrip({ studio }: { readonly studio: FilmStudioView }) {
  const t = useT();
  const bible = studio.productionBible;
  const voiced = bible.characters.filter((item) => item.voicePerformance.voiceId.length > 0).length;
  const lockedAssets = studio.visualAssets.filter((item) => item.locked).length;
  const items = [
    { label: t("film.lock.novelContract"), value: t("film.unit.sources", { n: bible.sources.filter((item) => item.exists).length }), detail: t("film.lock.novelContractDetail") },
    { label: t("film.lock.characterIdentity"), value: t("film.unit.people", { n: bible.characters.length }), detail: t("film.lock.visualAssets", { n: lockedAssets }) },
    { label: t("film.lock.voicePerformance"), value: `${voiced}/${bible.characters.length}`, detail: t("film.lock.voicePerformanceDetail") },
    { label: t("film.lock.sceneSpace"), value: t("film.unit.locations", { n: bible.locations.length }), detail: t("film.lock.sceneSpaceDetail") },
    { label: t("film.lock.styleBible"), value: bible.style.aspectRatio, detail: `${bible.style.frameRate} FPS · ${bible.style.textureMedium || t("film.value.realistic")}` },
  ];
  return (
    <div className="film-source-locks" aria-label={t("film.lock.ariaLabel")}>
      <header><span>{t("film.lock.kicker")}</span><small>{t("film.lock.changeWarning")}</small></header>
      <div>{items.map((item) => <article key={item.label}><i aria-hidden="true">✓</i><span><small>{item.label}</small><strong>{item.value}</strong><em>{item.detail}</em></span></article>)}</div>
    </div>
  );
}

function ShotPreview({ shot }: { readonly shot: FilmShotView }) {
  const t = useT();
  const url = shot.selectedAssetUrl || shot.candidates[0] || shot.firstFrameUrl;
  if (url) {
    return <div className="film-shot-preview"><img alt={t("film.preview.alt", { title: shot.title })} src={url} /></div>;
  }
  const task = shot.providerTask;
  return (
    <div className="film-shot-preview is-empty">
      <span>{shot.shotId.toUpperCase()}</span>
      <small>{task && task.state !== "succeeded" ? statusLabel(task.state, t) : `${shot.durationS.toFixed(1)}s · ${shot.language.shotSize}`}</small>
    </div>
  );
}

function ShotProductionTable({
  activeSceneId,
  activeShotId,
  catalog,
  studio,
  onSelectShot,
}: {
  readonly activeSceneId: string;
  readonly activeShotId: string;
  readonly catalog: FilmProviderCatalogView;
  readonly studio: FilmStudioView;
  readonly onSelectShot: (shotId: string) => void;
}) {
  const t = useT();
  const scene = studio.screenplay.scenes.find((item) => item.sceneId === activeSceneId) ?? studio.screenplay.scenes[0];
  const shots = useMemo(() => studio.shots.filter((shot) => shot.sceneId === scene?.sceneId), [scene?.sceneId, studio.shots]);
  const materialized = useMemo(
    () => new Set(studio.mediaArtifacts.filter((item) => item.kind === "video" && item.localPath.length > 0).map((item) => item.subjectRef)),
    [studio.mediaArtifacts],
  );
  const columns = useMemo<ColumnDef<FilmShotView>[]>(() => [
    { id: "preview", header: t("film.shotTable.frame"), cell: ({ row }) => <ShotPreview shot={row.original} /> },
    { id: "shot", header: t("film.shotTable.shotAction"), cell: ({ row }) => <div className="film-table-copy"><strong>{row.original.title}</strong><span>{row.original.action}</span><small>{row.original.shotId.toUpperCase()} · {t("film.unit.seconds", { n: row.original.durationS.toFixed(1) })}</small></div> },
    { id: "language", header: t("film.shotTable.cinematography"), cell: ({ row }) => <div className="film-table-language"><strong>{row.original.language.shotSize} · {row.original.language.cameraMotion}</strong><small>{row.original.language.cameraAngle} · {row.original.language.lensMm}mm</small><span>{row.original.language.lighting}</span></div> },
    { id: "media", header: t("film.shotTable.localMedia"), cell: ({ row }) => <span className={`film-qc ${materialized.has(row.original.shotId) ? "is-approved" : "is-review"}`}>{t(materialized.has(row.original.shotId) ? "film.shotTable.materialized" : "film.shotTable.notMaterialized")}</span> },
    { id: "route", header: "生成路由", cell: ({ row }) => {
      const provider = catalog[row.original.providerId];
      const model = modelOptions(catalog, row.original.providerId, "videoModels", t).find((item) => item.value === row.original.modelId);
      const providerLabel = provider ? localizedCatalogValue(t, `film.provider.${row.original.providerId}.short`, provider.shortLabel ?? provider.label) : row.original.providerId;
      return <div className="film-table-route"><strong>{providerLabel}</strong><small>{model?.label ?? row.original.modelId}</small><span>{row.original.generationMode.replaceAll("_", " ")}</span></div>;
    } },
    { id: "qc", header: t("film.shotTable.status"), cell: ({ row }) => <span className={`film-qc is-${row.original.qcStatus}`}>{statusLabel(row.original.qcStatus, t)}</span> },
  ], [catalog, materialized, t]);
  const table = useReactTable({ columns, data: shots, getCoreRowModel: getCoreRowModel() });
  return (
    <section className="film-board-panel">
      <SourceLockStrip studio={studio} />
      <header className="film-panel-heading">
        <div><span>SHOT PRODUCTION · {scene?.sceneId.toUpperCase()}</span><h2>{scene?.heading ?? "等待场次"}</h2><p>{scene?.objective}</p></div>
        <div className="film-board-stats"><span>{shots.length} SHOTS</span><span>{Math.round(shots.reduce((total, shot) => total + shot.durationS, 0))} SEC</span><span>{shots.filter((shot) => materialized.has(shot.shotId)).length} 落盘</span></div>
      </header>
      <div className="film-shot-table-wrap">
        <table className="film-shot-table">
          <thead>{table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) => <th key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</th>)}</tr>)}</thead>
          <tbody>{table.getRowModel().rows.map((row) => <tr aria-selected={row.original.shotId === activeShotId} className={row.original.shotId === activeShotId ? "is-active" : ""} key={row.id} onClick={() => onSelectShot(row.original.shotId)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelectShot(row.original.shotId); } }} tabIndex={0}>{row.getVisibleCells().map((cell) => <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody>
        </table>
      </div>
      {scene ? <footer className="film-scene-contract"><span><b>目标</b>{scene.objective}</span><span><b>冲突</b>{scene.conflict}</span><span><b>转折</b>{scene.turn}</span><span><b>声钩</b>{scene.soundHook}</span></footer> : null}
    </section>
  );
}

// 分镜阶段：镜头设计稿。只呈现叙事与摄影语言设计（动作/对白/声设计/转场），
// 不含生成路由与媒体管线，生产动作统一收敛到「镜头」阶段，避免双入口重复。
function StoryboardBoard({
  activeSceneId,
  studio,
  onEnterShotLine,
}: {
  readonly activeSceneId: string;
  readonly studio: FilmStudioView;
  readonly onEnterShotLine: () => void;
}) {
  const t = useT();
  const scene = studio.screenplay.scenes.find((item) => item.sceneId === activeSceneId) ?? studio.screenplay.scenes[0];
  const shots = useMemo(() => studio.shots.filter((shot) => shot.sceneId === scene?.sceneId), [scene?.sceneId, studio.shots]);
  const totalSeconds = shots.reduce((total, shot) => total + shot.durationS, 0);
  return (
    <section className="film-board-panel film-storyboard-panel">
      <SourceLockStrip studio={studio} />
      <header className="film-panel-heading">
        <div><span>STORYBOARD · {scene?.sceneId.toUpperCase()}</span><h2>{scene?.heading ?? t("film.common.waitingScene")}</h2><p>{scene?.objective}</p></div>
        <div className="film-board-stats"><span>{t("film.unit.storyFrames", { n: shots.length })}</span><span>{Math.round(totalSeconds)} SEC</span><span>{t("film.storyboard.averagePerShot", { n: (shots.length === 0 ? 0 : totalSeconds / shots.length).toFixed(1) })}</span></div>
      </header>
      <div className="film-storyboard-grid">
        {shots.map((shot) => (
          <article className="film-story-card" key={shot.shotId}>
            <header><span>{shot.shotId.toUpperCase()}</span><strong>{shot.title}</strong>{shot.locked ? <i className="is-locked">{t("film.storyboard.locked")}</i> : null}<em>{shot.durationS.toFixed(1)}s</em></header>
            <ul className="film-shot-language">
              <li>{t("film.storyboard.shotSize")} · {shot.language.shotSize}</li>
              <li>{t("film.storyboard.cameraMotion")} · {shot.language.cameraMotion}</li>
              <li>{t("film.storyboard.cameraAngle")} · {shot.language.cameraAngle}</li>
              <li>{t("film.storyboard.lighting")} · {shot.language.lighting}</li>
              <li>{shot.language.lensMm}mm</li>
            </ul>
            <p className="film-story-action">{shot.action}</p>
            {shot.dialogue.length > 0 ? <p className="film-story-line"><b>{t("film.storyboard.dialogue")}</b>{shot.dialogue}</p> : null}
            {shot.soundDesign.length > 0 ? <p className="film-story-line"><b>{t("film.storyboard.soundDesign")}</b>{shot.soundDesign}</p> : null}
            {shot.transition.length > 0 ? <p className="film-story-line"><b>{t("film.storyboard.transition")}</b>{shot.transition}</p> : null}
          </article>
        ))}
      </div>
      {scene ? <footer className="film-scene-contract"><span><b>{t("film.storyboard.goal")}</b>{scene.objective}</span><span><b>{t("film.storyboard.conflict")}</b>{scene.conflict}</span><span><b>{t("film.storyboard.turn")}</b>{scene.turn}</span><span><b>{t("film.storyboard.soundHook")}</b>{scene.soundHook}</span></footer> : null}
      <footer className="film-storyboard-cta">
        <span>{t("film.storyboard.handoff")}</span>
        <button onClick={onEnterShotLine} type="button">{t("film.storyboard.enterShotProduction")}</button>
      </footer>
    </section>
  );
}

function ShotInspector({
  catalog,
  disabled,
  routeDefaults,
  shot,
  studio,
  onGenerate,
  onMaterialize,
  onPoll,
  onQc,
  onQueryTask,
  onSave,
}: {
  readonly catalog: FilmProviderCatalogView;
  readonly disabled: boolean;
  readonly routeDefaults: RouteDefaults;
  readonly shot: FilmShotView;
  readonly studio: FilmStudioView;
  readonly onGenerate: (patch: Readonly<Record<string, unknown>>, providerId: string, modelId: string) => Promise<void>;
  readonly onMaterialize: (targetId: string) => Promise<void>;
  readonly onPoll: (shotId: string) => Promise<void>;
  readonly onQc: (shotId: string) => Promise<void>;
  readonly onQueryTask: (targetId: string) => Promise<void>;
  readonly onSave: (patch: Readonly<Record<string, unknown>>) => Promise<void>;
}) {
  const t = useT();
  const [prompt, setPrompt] = useState(shot.prompt);
  const deferredPrompt = useDeferredValue(prompt);
  const [providerId, setProviderId] = useState(shot.providerId || routeDefaults.videoProvider);
  const options = modelOptions(catalog, providerId, "videoModels", t);
  const initialModelId = options.some((item) => item.value === shot.modelId) ? shot.modelId
    : (options.some((item) => item.value === routeDefaults.videoModel)
      ? routeDefaults.videoModel
      : preferredModel(options)?.value ?? "");
  const [modelId, setModelId] = useState(initialModelId);
  const selectedModel = options.find((item) => item.value === modelId) ?? preferredModel(options);
  const [shotSize, setShotSize] = useState(shot.language.shotSize);
  const [motion, setMotion] = useState(shot.language.cameraMotion);
  const [angle, setAngle] = useState(shot.language.cameraAngle);
  const [lighting, setLighting] = useState(shot.language.lighting);
  const params = shot.generationParams ?? {};
  const isH3 = modelId.startsWith("MiniMax-H3");
  const [durationS, setDurationS] = useState(clampDuration(shot.durationS, selectedModel));
  const [resolution, setResolution] = useState(
    resolvedResolution(
      typeof params.resolution === "string" ? params.resolution : "",
      selectedModel,
    ),
  );
  const [aspectRatio, setAspectRatio] = useState(
    typeof params.aspectRatio === "string" && params.aspectRatio.length > 0 ? params.aspectRatio : "adaptive",
  );
  const [seedText, setSeedText] = useState(
    typeof params.seed === "number" ? String(params.seed) : "",
  );
  const [watermark, setWatermark] = useState(params.watermark === true);
  const [promptOptimizer, setPromptOptimizer] = useState(params.promptOptimizer !== false);
  const [generateAudio, setGenerateAudio] = useState(params.generateAudio !== false);
  const [returnLastFrame, setReturnLastFrame] = useState(params.returnLastFrame !== false);
  const [negativePrompt, setNegativePrompt] = useState(shot.negativePrompt);
  const [drivingAudioUrl, setDrivingAudioUrl] = useState(
    typeof params.drivingAudioUrl === "string" ? params.drivingAudioUrl : "",
  );
  const [videoRefsText, setVideoRefsText] = useState(shot.referenceVideoUrls.join("\n"));
  const [audioRefsText, setAudioRefsText] = useState(shot.referenceAudioUrls.join("\n"));
  const [trustedAssetRefsText, setTrustedAssetRefsText] = useState(
    shot.identityReferenceUrls.filter((url) => url.startsWith("asset://")).join("\n"),
  );
  const provider = catalog[providerId];
  const characters = studio.productionBible.characters.filter((item) => shot.characterIds.includes(item.characterId));
  const location = studio.productionBible.locations.find((item) => item.locationId === shot.locationId);
  const task = shot.providerTask;
  const localVideo = studio.mediaArtifacts.find((item) => item.subjectRef === shot.shotId && item.kind === "video" && item.localPath.length > 0);
  const qcReport = [...studio.qcReports].reverse().find((report) => report.targetId === shot.shotId);
  const visionReport = [...studio.visionQcReports].reverse().find((report) => report.targetId === shot.shotId);
  const hasMedia = shot.selectedAssetUrl.length > 0 || shot.candidates.length > 0;
  const splitLines = (value: string): readonly string[] => value.split(/[\n,，]+/).map((item) => item.trim()).filter((item) => item.length > 0);
  const referenceVideos = splitLines(videoRefsText);
  const referenceAudios = splitLines(audioRefsText);
  const hasVisualReference = shot.firstFrameUrl.length > 0
    || shot.lastFrameUrl.length > 0
    || shot.identityReferenceUrls.some((url) => !url.startsWith("asset://"))
    || referenceVideos.length > 0;
  const allowAdaptiveRatio = isH3 && hasVisualReference;
  const effectiveResolution = resolvedResolution(resolution, selectedModel);
  const effectiveAspectRatio = resolvedAspectRatio(aspectRatio, selectedModel, allowAdaptiveRatio);
  const durationOptions = selectedModel?.durations.length
    ? selectedModel.durations.map((value) => ({ value: String(value), label: `${value} 秒` }))
    : [{ value: String(durationS), label: `${durationS} 秒` }];
  const aspectRatioOptions = [
    ...(allowAdaptiveRatio ? [{ value: "adaptive", label: "自适应参考素材" }] : []),
    ...((selectedModel?.aspectRatios.length ?? 0) > 0
      ? selectedModel!.aspectRatios
      : ["16:9", "9:16", "1:1", "4:3", "3:4"]
    ).map((value) => ({ value, label: value })),
  ];
  const supports = (feature: string) => selectedModel?.features.includes(feature) === true;
  const h3AudioOnly = isH3 && referenceAudios.length > 0 && !hasVisualReference;
  const routeModeLabel = shot.firstFrameUrl && shot.lastFrameUrl
    ? "首尾帧生成"
    : shot.firstFrameUrl
      ? "图生视频"
      : hasVisualReference || trustedAssetRefsText.trim().length > 0
        ? "多模态参考生成"
        : "文生视频";
  const patch = useCallback(() => {
    const seed = seedText.trim();
    const trustedAssets = splitLines(trustedAssetRefsText).filter((url) => url.startsWith("asset://"));
    const portableIdentityRefs = shot.identityReferenceUrls.filter((url) => !url.startsWith("asset://"));
    return {
      prompt,
      negativePrompt,
      providerId,
      modelId,
      durationS,
      language: { ...shot.language, shotSize, cameraMotion: motion, cameraAngle: angle, lighting },
      identityReferenceUrls: providerId === "volcengine_ark"
        ? [...portableIdentityRefs, ...trustedAssets]
        : shot.identityReferenceUrls,
      referenceVideoUrls: [...referenceVideos],
      referenceAudioUrls: [...referenceAudios],
      generationParams: {
        ...params,
        resolution: effectiveResolution,
        aspectRatio: effectiveAspectRatio,
        watermark,
        promptOptimizer,
        generateAudio,
        returnLastFrame,
        drivingAudioUrl: drivingAudioUrl.trim(),
        ...(supports("seed") && seed.length > 0 && Number.isFinite(Number(seed)) && Number(seed) >= 0
          ? { seed: Number(seed) }
          : { seed: null }),
      },
    };
  }, [angle, drivingAudioUrl, durationS, effectiveAspectRatio, effectiveResolution, generateAudio, lighting, modelId, motion, negativePrompt, params, prompt, promptOptimizer, providerId, referenceAudios, referenceVideos, returnLastFrame, seedText, shot.identityReferenceUrls, shot.language, shotSize, supports, trustedAssetRefsText, watermark]);
  const selectModel = (nextModelId: string, nextProviderId = providerId) => {
    const nextOptions = modelOptions(catalog, nextProviderId, "videoModels", t);
    const nextModel = nextOptions.find((item) => item.value === nextModelId) ?? preferredModel(nextOptions);
    setModelId(nextModel?.value ?? "");
    setDurationS((current) => clampDuration(current, nextModel));
    setResolution((current) => resolvedResolution(current, nextModel));
    setAspectRatio((current) => resolvedAspectRatio(current, nextModel, false));
    if (nextModel?.features.includes("seed") !== true) setSeedText("");
  };
  const changeProvider = (next: string) => {
    setProviderId(next);
    const nextOptions = modelOptions(catalog, next, "videoModels", t);
    selectModel(preferredModel(nextOptions)?.value ?? "", next);
  };
  return (
    <aside className="film-inspector">
      <header><div><span>镜头检查器</span><h3>{shot.shotId.toUpperCase()}</h3></div><span className={`film-qc is-${shot.qcStatus}`}>{statusLabel(shot.qcStatus, t)}</span></header>
      <section className="film-inspector-section">
        <h4>上游生产锁</h4>
        <div className="film-inspector-locks">
          {characters.map((character) => <span key={character.characterId}><i>✓</i><b>{character.name}</b><small>{character.screenIdentity.facialAnchors.join(" · ") || character.screenIdentity.silhouette}</small></span>)}
          {location ? <span><i>✓</i><b>{location.name}</b><small>{location.spatialLayout}</small></span> : null}
        </div>
      </section>
      <section className="film-inspector-section"><h4>六维摄影语言</h4><div className="film-field-grid">
        <label>景别<StudioSelect ariaLabel="景别" onChange={setShotSize} options={[{ value: "wide", label: "全景" }, { value: "medium", label: "中景" }, { value: "close_up", label: "近景" }, { value: "extreme_close_up", label: "大特写" }]} value={shotSize} /></label>
        <label>运镜<StudioSelect ariaLabel="运镜" onChange={setMotion} options={[{ value: "static", label: "固定" }, { value: "slow_push", label: "缓推" }, { value: "tracking", label: "跟拍" }, { value: "subtle_handheld", label: "轻手持" }]} value={motion} /></label>
        <label>机位<StudioSelect ariaLabel="机位" onChange={setAngle} options={[{ value: "eye_level", label: "平视" }, { value: "low_angle", label: "仰拍" }, { value: "high_angle", label: "俯拍" }, { value: "over_shoulder", label: "过肩" }]} value={angle} /></label>
        <label>光影<StudioSelect ariaLabel="光影" onChange={setLighting} options={[{ value: "motivated", label: "实用光" }, { value: "rembrandt", label: "伦勃朗光" }, { value: "rim_light", label: "轮廓光" }, { value: "volumetric", label: "体积光" }]} value={lighting} /></label>
      </div></section>
      <section className="film-inspector-section is-provider-route">
        <header><h4>三平台生成通路</h4><small aria-live="polite">{routeModeLabel}</small></header>
        <div aria-label="影视生成平台" className="film-provider-switch" role="tablist">
          {providerOptions(catalog, t, true).map((option) => (
            <button
              aria-selected={providerId === option.value}
              className={providerId === option.value ? "is-active" : ""}
              key={option.value}
              onClick={() => changeProvider(option.value)}
              role="tab"
              title={option.description}
              type="button"
            >
              {option.label}
            </button>
          ))}
        </div>
        <label>当前模型<StudioSelect ariaLabel="影视生成模型" onChange={selectModel} options={options} value={modelId} /></label>
        <div className="film-provider-summary">
          <div><strong>{provider ? localizedCatalogValue(t, `film.provider.${providerId}.label`, provider.label) : providerId}</strong><small>{selectedModel?.label ?? modelId}</small></div>
          {provider?.docsUrl ? <a href={provider.docsUrl} rel="noreferrer" target="_blank">{t("film.common.officialDocs")}</a> : null}
          <p>{provider?.bestFor ? localizedCatalogValue(t, `film.provider.${providerId}.bestFor`, provider.bestFor) : null}</p>
        </div>
        <div aria-label="当前模型原生能力" className="film-provider-pills">{(selectedModel?.features.length ? selectedModel.features : provider?.strengths ?? []).slice(0, 8).map((feature) => <span key={feature}>{localizedCatalogValue(t, featureLabelKey(feature), feature)}</span>)}</div>
      </section>
      <section className="film-inspector-section is-provider-params">
        <header><h4>{isH3 ? "H3 输出规格" : `${provider?.shortLabel ?? provider?.label ?? "平台"} 输出规格`}</h4><small>按模型能力自动约束</small></header>
        <div className="film-field-grid">
          <label>时长<StudioSelect ariaLabel={`${selectedModel?.label ?? "模型"} 时长`} onChange={(value) => setDurationS(Number(value))} options={durationOptions} value={String(clampDuration(durationS, selectedModel))} /></label>
          <label>分辨率<StudioSelect ariaLabel={`${selectedModel?.label ?? "模型"} 分辨率`} onChange={setResolution} options={(selectedModel?.resolutions.length ? selectedModel.resolutions : [effectiveResolution]).map((value) => ({ value, label: value === "2K" ? "2K · 高清直出" : value }))} value={effectiveResolution} /></label>
          <label>画面比例<StudioSelect ariaLabel={`${selectedModel?.label ?? "模型"} 画面比例`} onChange={setAspectRatio} options={aspectRatioOptions} value={effectiveAspectRatio} /></label>
          {supports("seed") ? <label>随机种子<input aria-label="随机种子" max={2_147_483_647} min={0} onChange={(event) => setSeedText(event.target.value)} placeholder="随机" type="number" value={seedText} /></label> : null}
        </div>
        <div className="film-provider-toggles">
          {supports("prompt_optimizer") ? <label className="film-checkbox"><input checked={promptOptimizer} onChange={(event) => setPromptOptimizer(event.target.checked)} type="checkbox" />提示词扩写</label> : null}
          {supports("generate_audio") ? <label className="film-checkbox"><input checked={generateAudio} onChange={(event) => setGenerateAudio(event.target.checked)} type="checkbox" />生成原生声音</label> : null}
          {supports("return_last_frame") ? <label className="film-checkbox"><input checked={returnLastFrame} onChange={(event) => setReturnLastFrame(event.target.checked)} type="checkbox" />返回尾帧</label> : null}
          {supports("watermark") ? <label className="film-checkbox"><input checked={watermark} onChange={(event) => setWatermark(event.target.checked)} type="checkbox" />AIGC 水印</label> : null}
        </div>
        {isH3 ? <p className="film-parameter-note">H3 v2 支持 4–15 秒整数与 768P / 2K 直出；当前接口不提供可复现种子，时间线提示词由服务端原样保留。</p> : null}
      </section>
      {providerId === "bailian" ? <section className="film-inspector-section is-native-inputs"><header><h4>百炼原生参数</h4><small>Wan 2.7</small></header>
        <label className="film-refs-field">负面提示词<textarea onChange={(event) => setNegativePrompt(event.target.value)} placeholder="模糊、形变、多余人物……" rows={2} value={negativePrompt} /></label>
        {supports("driving_audio") ? <label className="film-refs-field">配乐 / 驱动音频 URL<input onChange={(event) => setDrivingAudioUrl(event.target.value)} placeholder="https://…/soundtrack.mp3" value={drivingAudioUrl} /></label> : null}
        <p className="film-parameter-note">短提示建议开启扩写；精确分镜或已写时间码时建议关闭。音频支持 MP3 / WAV，并按镜头时长裁切。</p>
      </section> : null}
      {providerId === "volcengine_ark" ? <section className="film-inspector-section is-native-inputs"><header><h4>Seedance 原生参数</h4><small>可信素材与声画生成</small></header>
        <label className="film-refs-field">可信演员 Asset URI（每行一个）<textarea onChange={(event) => setTrustedAssetRefsText(event.target.value)} placeholder={"asset://asset-…"} rows={2} value={trustedAssetRefsText} /></label>
        <label className="film-refs-field">驱动音频 URL<input onChange={(event) => setDrivingAudioUrl(event.target.value)} placeholder="https://…/dialogue.mp3" value={drivingAudioUrl} /></label>
        <p className="film-parameter-note">仅接受已在方舟完成授权的 asset:// 素材；切换到其他平台时服务端会自动隔离这些私域引用。</p>
      </section> : null}
      {isH3 ? <section className="film-inspector-section is-native-inputs"><header><h4>H3 多模态参考</h4><small>≤3 视频 / ≤3 音频 · 各 2–15s · 总 ≤15s</small></header>
        <label className="film-refs-field">参考视频（每行一个 URL）<textarea onChange={(event) => setVideoRefsText(event.target.value)} placeholder={"https://…/motion.mp4"} rows={2} value={videoRefsText} /></label>
        <label className="film-refs-field">参考音频（每行一个 URL）<textarea onChange={(event) => setAudioRefsText(event.target.value)} placeholder={"https://…/voice.mp3"} rows={2} value={audioRefsText} /></label>
        {h3AudioOnly ? <p className="film-parameter-warning" role="alert">H3 音频参考必须与图片或视频参考共同使用。</p> : null}
      </section> : null}
      <section className="film-inspector-section is-prompt"><header><h4>导演提示词</h4><small>{deferredPrompt.length} 字</small></header><textarea onChange={(event) => setPrompt(event.target.value)} value={prompt} /><p>服务端会把角色描述卡、媒介锁、风格锁、场景空间与声纹规则逐镜注入。</p></section>
      <div className="film-inspector-actions"><button disabled={disabled} onClick={() => void onSave(patch())} type="button">保存镜头</button><button className="is-primary" disabled={disabled || modelId.length === 0 || h3AudioOnly} onClick={() => void onGenerate(patch(), providerId, modelId)} type="button">{disabled ? "处理中…" : "生成候选镜头"}</button></div>
      <section className="film-inspector-section film-media-pipeline"><h4>媒体管线</h4>
        <div className="film-media-status">
          <span><small>远端任务</small><b className={task ? `is-${task.state}` : ""}>{task ? `${statusLabel(task.state, t)} · ${shortId(task.taskId)}` : "未提交"}</b>{task && task.errorMessage ? <em>{task.errorMessage}</em> : null}</span>
          <span><small>本地媒体</small><b>{localVideo ? `${formatBytes(localVideo.sizeBytes)} · ${localVideo.codec || "video"}` : "未落盘"}</b>{localVideo ? <em>{localVideo.localPath}</em> : null}</span>
          <span><small>镜头质检</small><b className={qcReport ? (qcReport.passed ? "is-succeeded" : "is-failed") : ""}>{qcReport ? (qcReport.passed ? "已通过" : "未通过") : "未质检"}</b>{qcReport?.checks.map((check) => <em key={check.name}>{check.name}: {statusLabel(check.status, t)} {check.detail}</em>)}</span>
          <span><small>视觉质检</small><b className={visionReport ? (visionReport.passed ? "is-succeeded" : "is-failed") : ""}>{visionReport ? `${visionReport.overallScore.toFixed(1)}/10 · ${visionReport.passed ? "可交付" : "需重拍"}` : "未评估"}</b>{visionReport?.dimensions.map((dimension) => <em key={dimension.dimension}>{dimension.dimension}: {dimension.score.toFixed(1)} {dimension.note}</em>)}</span>
        </div>
        <div className="film-inspector-actions">
          {task && task.state !== "succeeded" && task.state !== "failed" ? <button disabled={disabled} onClick={() => void onQueryTask(shot.shotId)} type="button">查询任务</button> : null}
          {task && task.state !== "succeeded" && task.state !== "failed" ? <button className="is-primary" disabled={disabled} onClick={() => void onPoll(shot.shotId)} title="自动轮询直到生成完成（排队/处理分阶段上报进度）" type="button">等待完成</button> : null}
          <button disabled={disabled || !hasMedia} onClick={() => void onMaterialize(shot.shotId)} title="把远端候选下载并探测为本地媒体" type="button">落盘媒体</button>
          <button disabled={disabled || !localVideo} onClick={() => void onQc(shot.shotId)} title="对已落盘视频执行时长与黑帧质检" type="button">镜头质检</button>
        </div>
      </section>
    </aside>
  );
}

function AssetCard({ asset, disabled, routeDefaults, onGenerate, onOpen, catalog }: {
  readonly asset: FilmVisualAssetView;
  readonly disabled: boolean;
  readonly routeDefaults: RouteDefaults;
  readonly onGenerate: (asset: FilmVisualAssetView, providerId: string, modelId: string) => Promise<void>;
  readonly onOpen: (assetId: string) => void;
  readonly catalog: FilmProviderCatalogView;
}) {
  const t = useT();
  const [providerId, setProviderId] = useState(asset.providerId || routeDefaults.imageProvider);
  const options = modelOptions(catalog, providerId, "imageModels", t);
  const [modelId, setModelId] = useState(
    options.some((item) => item.value === asset.modelId) ? asset.modelId
      : (options.some((item) => item.value === routeDefaults.imageModel) ? routeDefaults.imageModel : options[0]?.value ?? ""),
  );
  return (
    <article className={asset.locked ? "is-locked" : ""}>
      <button className="film-asset-open" onClick={() => onOpen(asset.assetId)} type="button">
        <div className={`film-asset-preview is-${asset.assetType}`}>{asset.selectedUrl.startsWith("data:image") || asset.selectedUrl.startsWith("http") ? <img alt={`${asset.name} 已选资产`} src={asset.selectedUrl} /> : <><span /><i /></>}<em>{asset.assetType.toUpperCase()}</em><b>进入资产页 →</b></div>
        <div><span>{asset.view}</span><h3>{asset.name}</h3><p>{asset.prompt}</p></div>
      </button>
      <div className="film-asset-route"><StudioSelect ariaLabel={`${asset.name} 平台`} onChange={(next) => { setProviderId(next); setModelId(modelOptions(catalog, next, "imageModels", t)[0]?.value ?? ""); }} options={providerOptions(catalog, t, true)} value={providerId} /><StudioSelect ariaLabel={`${asset.name} 模型`} onChange={setModelId} options={options} value={modelId} /></div>
      <footer><small>{asset.locked ? "身份 / 空间已锁定" : statusLabel(asset.qcStatus, t)}</small><button disabled={disabled || modelId.length === 0} onClick={() => void onGenerate(asset, providerId, modelId)} type="button">{asset.candidates.length > 0 ? "再生成" : "生成资产"}</button></footer>
    </article>
  );
}

function AssetBoard({ catalog, disabled, routeDefaults, studio, onGenerate, onOpen }: {
  readonly catalog: FilmProviderCatalogView;
  readonly disabled: boolean;
  readonly routeDefaults: RouteDefaults;
  readonly studio: FilmStudioView;
  readonly onGenerate: (asset: FilmVisualAssetView, providerId: string, modelId: string) => Promise<void>;
  readonly onOpen: (assetId: string) => void;
}) {
  return <section className="film-assets-panel"><SourceLockStrip studio={studio} /><header className="film-panel-heading"><div><span>VISUAL DEVELOPMENT</span><h2>角色、场景与道具资产库</h2><p>主页面只展示调度信息；点击资产进入独立版本、质检和路由页面。</p></div><div className="film-board-stats"><span>{studio.visualAssets.filter((item) => item.locked).length} LOCKED</span><span>{studio.visualAssets.length} ASSETS</span></div></header><div className="film-asset-grid">{studio.visualAssets.map((asset) => <AssetCard asset={asset} catalog={catalog} disabled={disabled} key={asset.assetId} onGenerate={onGenerate} onOpen={onOpen} routeDefaults={routeDefaults} />)}</div></section>;
}

function AssetDetailPage({ asset, catalog, disabled, studio, onBack, onGenerate, onSelect }: {
  readonly asset: FilmVisualAssetView;
  readonly catalog: FilmProviderCatalogView;
  readonly disabled: boolean;
  readonly studio: FilmStudioView;
  readonly onBack: () => void;
  readonly onGenerate: (asset: FilmVisualAssetView, providerId: string, modelId: string) => Promise<void>;
  readonly onSelect: (asset: FilmVisualAssetView, url: string, lock: boolean) => Promise<void>;
}) {
  const t = useT();
  const [providerId, setProviderId] = useState(asset.providerId || "bailian");
  const options = modelOptions(catalog, providerId, "imageModels", t);
  const [modelId, setModelId] = useState(options.some((item) => item.value === asset.modelId) ? asset.modelId : options[0]?.value ?? "");
  const character = studio.productionBible.characters.find((item) => item.characterId === asset.subjectId);
  const location = studio.productionBible.locations.find((item) => item.locationId === asset.subjectId);
  const anchors = character ? [...character.screenIdentity.facialAnchors, character.screenIdentity.silhouette, ...character.screenIdentity.costumePalette] : location ? [location.spatialLayout, ...location.materials, ...location.practicalLights] : [];
  const candidates = asset.candidates.length > 0 ? asset.candidates : asset.selectedUrl ? [asset.selectedUrl] : [];
  return (
    <section className="film-asset-detail">
      <header className="film-asset-detail-header"><button onClick={onBack} type="button">← 视觉资产库</button><span>/</span><small>{asset.assetType}</small><h2>{asset.name}</h2><i className={`film-qc is-${asset.qcStatus}`}>{statusLabel(asset.qcStatus, t)}</i></header>
      <div className="film-asset-detail-grid">
        <main><div className="film-asset-hero">{asset.selectedUrl.startsWith("data:image") || asset.selectedUrl.startsWith("http") ? <img alt={`${asset.name} 主资产`} src={asset.selectedUrl} /> : <div><span>{asset.assetType === "character" ? "CHARACTER IDENTITY" : "ENVIRONMENT LOCK"}</span><strong>{asset.name}</strong><small>生成候选后在此进行逐版本审核</small></div>}</div><section><header><h3>候选版本</h3><small>{candidates.length} 个候选 · 只重跑失败节点</small></header><div className="film-candidate-grid">{candidates.length === 0 ? <p>尚无生成候选。右侧选择平台和模型后开始生成。</p> : candidates.map((url, index) => <button className={url === asset.selectedUrl ? "is-selected" : ""} key={`${url}-${index}`} onClick={() => void onSelect(asset, url, false)} type="button"><img alt={`${asset.name} 候选 ${index + 1}`} src={url} /><span>V{String(index + 1).padStart(2, "0")}</span></button>)}</div></section></main>
        <aside><section><span>ASSET CONTRACT</span><h3>身份 / 空间契约</h3><div className="film-anchor-list">{anchors.filter(Boolean).map((anchor) => <p key={anchor}><i>✓</i>{anchor}</p>)}</div></section><section><span>GENERATION ROUTE</span><h3>生成与重跑</h3><label>平台<StudioSelect ariaLabel="资产生成平台" onChange={(next) => { setProviderId(next); setModelId(modelOptions(catalog, next, "imageModels", t)[0]?.value ?? ""); }} options={providerOptions(catalog, t)} value={providerId} /></label><label>模型<StudioSelect ariaLabel="资产生成模型" onChange={setModelId} options={options} value={modelId} /></label><p>{catalog[providerId]?.bestFor}</p><button className="is-primary" disabled={disabled || modelId.length === 0} onClick={() => void onGenerate(asset, providerId, modelId)} type="button">生成新候选</button>{asset.selectedUrl ? <button disabled={disabled} onClick={() => void onSelect(asset, asset.selectedUrl, true)} type="button">审核通过并锁定</button> : null}</section><section><span>QUALITY GATE</span><h3>商业质检</h3>{["身份 / 空间一致性", "媒介与风格锁", "构图与可读性", "无多余人物 / 漂移", "长篇连续性"].map((item) => <p key={item}><i>{asset.locked ? "✓" : "○"}</i>{item}</p>)}</section></aside>
      </div>
    </section>
  );
}

function ProductionOverview({ canvasRequest, commandClient, engineClient, onCanvasFocusChange, onError, onNotice, studio }: {
  readonly canvasRequest: number;
  readonly commandClient: EngineCommandClient;
  readonly engineClient: EngineClient;
  readonly onCanvasFocusChange: (focused: boolean) => void;
  readonly onError: (message: string) => void;
  readonly onNotice: (message: string) => void;
  readonly studio: FilmStudioView;
}) {
  return <section className="film-overview-panel"><FilmWorkflowWorkbench canvasRequest={canvasRequest} commandClient={commandClient} engineClient={engineClient} onCanvasFocusChange={onCanvasFocusChange} onError={onError} onNotice={onNotice} studio={studio} /></section>;
}

function ScreenplayBoard({ studio }: { readonly studio: FilmStudioView }) {
  return <section className="film-script-panel"><header className="film-panel-heading"><div><span>SCREENPLAY · V{studio.screenplay.version}</span><h2>可拍摄长篇场次剧本</h2><p>{studio.screenplay.synopsis}</p></div><div className="film-board-stats"><span>{studio.screenplay.scenes.length} SCENES</span><span>{Math.round(studio.screenplay.estimatedDurationS / 60)} MIN</span></div></header><div className="film-script-pages">{studio.screenplay.scenes.map((scene) => <article key={scene.sceneId}><header><span>{String(scene.sequenceNumber).padStart(2, "0")}</span><h3>{scene.heading}</h3><small>{scene.durationS}s</small></header><p className="film-script-action">{scene.lines[0]?.text}</p><dl><div><dt>目标</dt><dd>{scene.objective}</dd></div><div><dt>冲突</dt><dd>{scene.conflict}</dd></div><div><dt>转折</dt><dd>{scene.turn}</dd></div></dl><footer>源：第 {scene.sourceChapter ?? "—"} 章 · {scene.sourceSceneRef}</footer></article>)}</div></section>;
}

function SoundPictureBoard({ disabled, studio, onMaterialize }: {
  readonly disabled: boolean;
  readonly studio: FilmStudioView;
  readonly onMaterialize: (targetId: string) => Promise<void>;
}) {
  const bible = studio.productionBible;
  const audioArtifacts = studio.mediaArtifacts.filter((item) => item.kind === "audio");
  const dialogueShots = studio.shots.filter((shot) => shot.dialogue.length > 0);
  const speakerVoice = (speaker: string) => bible.characters.find((item) => item.name === speaker || item.characterId === speaker);
  return (
    <section className="film-sound-panel">
      <header className="film-panel-heading">
        <div><span>SOUND & PICTURE</span><h2>声画对齐与配音台账</h2><p>声腔音色、对白节拍与环境声在此逐镜核对；上游声腔页面负责音色生成。</p></div>
        <div className="film-board-stats">
          <span>{bible.characters.filter((item) => item.voicePerformance.voiceId.length > 0).length}/{bible.characters.length} 配音</span>
          <span>{dialogueShots.length} 对白镜头</span>
          <span>{audioArtifacts.length} 音频素材</span>
        </div>
      </header>
      <div className="film-sound-grid">
        <article className="film-sound-cast">
          <h3>声纹演员表</h3>
          {bible.characters.map((character) => (
            <div className="film-sound-cast-row" key={character.characterId}>
              <b>{character.name}</b>
              <span className={`film-qc ${character.voicePerformance.voiceId ? "is-approved" : "is-review"}`}>{character.voicePerformance.voiceId ? "已定音色" : "待声腔"}</span>
              <small>{character.voicePerformance.timbre || "前往声腔页面生成音色与表演规则"}</small>
            </div>
          ))}
        </article>
        <article className="film-sound-lines">
          <h3>对白与声设计</h3>
          {dialogueShots.length === 0 ? <p className="film-empty-hint">暂无带对白的镜头。先在分镜阶段补充对白。</p> : dialogueShots.map((shot) => {
            const speaker = speakerVoice(shot.dialogue.split(/[:：]/)[0]?.trim() ?? "");
            return (
              <div className="film-sound-line" key={shot.shotId}>
                <header><b>{shot.title}</b><small>{shot.shotId.toUpperCase()} · {shot.durationS.toFixed(1)}s</small></header>
                <p><b>{speaker ? speaker.name : "旁白"}</b>{shot.dialogue}</p>
                <small>{speaker?.voicePerformance.timbre ? `音色：${speaker.voicePerformance.timbre}` : "音色待定"} · 声设计：{shot.soundDesign || "—"}</small>
              </div>
            );
          })}
        </article>
        <article className="film-sound-assets">
          <h3>音频素材台账</h3>
          {audioArtifacts.length === 0 ? <p className="film-empty-hint">尚未落盘音频素材。对白 TTS 与环境声可在声腔页面生成后回传。</p> : audioArtifacts.map((item) => (
            <div className="film-sound-asset" key={item.artifactId}>
              <b>{item.subjectRef.toUpperCase()}</b>
              <small>{item.localPath ? `${formatBytes(item.sizeBytes)} · ${item.codec || "audio"} · ${item.durationS.toFixed(1)}s` : "远端待落盘"}</small>
              {item.localPath ? <em>{item.localPath}</em> : <button disabled={disabled} onClick={() => void onMaterialize(item.subjectRef)} type="button">落盘</button>}
            </div>
          ))}
          <h3>场次声钩</h3>
          {studio.screenplay.scenes.map((scene) => <p key={scene.sceneId}><b>SC {String(scene.sequenceNumber).padStart(2, "0")}</b>{scene.soundHook}</p>)}
        </article>
      </div>
    </section>
  );
}

function EditBoard({ disabled, studio, onBatchGenerate, onMaterialize, onMaterializeAll }: {
  readonly disabled: boolean;
  readonly studio: FilmStudioView;
  readonly onBatchGenerate: (shotIds: readonly string[]) => Promise<void>;
  readonly onMaterialize: (targetId: string) => Promise<void>;
  readonly onMaterializeAll: (targetIds: readonly string[]) => Promise<void>;
}) {
  const t = useT();
  const [view, setView] = useState<"media" | "readiness">("media");
  const subjectName = (ref: string) => studio.shots.find((shot) => shot.shotId === ref)?.title
    ?? studio.visualAssets.find((asset) => asset.assetId === ref)?.name ?? ref.toUpperCase();
  const pendingShots = studio.shots.filter((shot) => !shot.selectedAssetUrl && shot.candidates.length === 0 && !(shot.providerTask && shot.providerTask.state !== "failed"));
  const readyToMaterialize = studio.shots.filter((shot) => (shot.selectedAssetUrl || shot.candidates.length > 0)
    && !studio.mediaArtifacts.some((item) => item.subjectRef === shot.shotId && item.kind === "video" && item.localPath.length > 0));
  const materialized = studio.mediaArtifacts.filter((item) => item.localPath.length > 0);
  return (
    <section className="film-edit-panel">
      <header className="film-panel-heading">
        <div><span>EDIT & VAULT</span><h2>{t("film.edit.title")}</h2><p>{t("film.edit.subtitle")}</p></div>
        <div className="film-board-stats">
          <span>{t("film.edit.shots", { count: studio.shots.length })}</span>
          <span>{t("film.edit.localMedia", { count: materialized.length })}</span>
          <span>{t("film.edit.pendingGeneration", { count: pendingShots.length })}</span>
          <span>{t("film.edit.pendingMaterialize", { count: readyToMaterialize.length })}</span>
        </div>
      </header>
      <div className="film-edit-actions">
        <button className="is-primary" disabled={disabled || pendingShots.length === 0} onClick={() => void onBatchGenerate(pendingShots.map((shot) => shot.shotId))} type="button">{t("film.edit.batchGenerate", { count: pendingShots.length })}</button>
        <button disabled={disabled || readyToMaterialize.length === 0} onClick={() => void onMaterializeAll(readyToMaterialize.map((shot) => shot.shotId))} type="button">{t("film.edit.materializeAll", { count: readyToMaterialize.length })}</button>
        <div aria-label={t("film.edit.viewAria")} className="film-edit-view-switch" role="tablist"><button aria-selected={view === "media"} className={view === "media" ? "is-active" : ""} onClick={() => setView("media")} role="tab" type="button">{t("film.edit.mediaLedger")}</button><button aria-selected={view === "readiness"} className={view === "readiness" ? "is-active" : ""} onClick={() => setView("readiness")} role="tab" type="button">{t("film.edit.readiness")}</button></div>
        <small>{t("film.edit.batchHint")}</small>
      </div>
      <div className="film-edit-grid is-single">
        {view === "media" ? <article>
          <h3>{t("film.edit.mediaLedger")}</h3>
          {studio.mediaArtifacts.length === 0 ? <p className="film-empty-hint">{t("film.edit.noMedia")}</p> : (
            <table className="film-media-table">
              <thead><tr><th>{t("film.edit.subject")}</th><th>{t("film.edit.kind")}</th><th>{t("film.edit.spec")}</th><th>{t("film.edit.localPath")}</th><th>{t("film.edit.action")}</th></tr></thead>
              <tbody>
                {studio.mediaArtifacts.map((item) => (
                  <tr key={item.artifactId}>
                    <td><b>{subjectName(item.subjectRef)}</b><small>{item.subjectRef}</small></td>
                    <td>{item.kind}</td>
                    <td>{item.width > 0 ? `${item.width}×${item.height}` : "—"} · {item.durationS > 0 ? `${item.durationS.toFixed(1)}s` : "—"} · {formatBytes(item.sizeBytes)}{item.codec ? ` · ${item.codec}` : ""}</td>
                    <td>{item.localPath ? <span className="film-qc is-approved">{t("film.edit.materialized")}</span> : <span className="film-qc is-review">{t("film.edit.remote")}</span>}<small>{item.localPath}</small></td>
                    <td>{item.localPath ? null : <button disabled={disabled} onClick={() => void onMaterialize(item.subjectRef)} type="button">{t("film.edit.materialize")}</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </article> : null}
        {view === "readiness" ? <article>
          <h3>{t("film.edit.readiness")}</h3>
          {studio.shots.map((shot) => {
            const local = studio.mediaArtifacts.some((item) => item.subjectRef === shot.shotId && item.kind === "video" && item.localPath.length > 0);
            return (
              <div className="film-readiness-row" key={shot.shotId}>
                <b>{shot.title}</b>
                <span className={`film-qc ${local ? "is-approved" : shot.selectedAssetUrl || shot.candidates.length > 0 ? "is-review" : "is-failed"}`}>{t(local ? "film.edit.readyToEdit" : shot.selectedAssetUrl || shot.candidates.length > 0 ? "film.edit.awaitingMaterialize" : "film.edit.awaitingGeneration")}</span>
                <small>{shot.durationS.toFixed(1)}s · {shot.language.shotSize}</small>
                {local || (!shot.selectedAssetUrl && shot.candidates.length === 0) ? null : <button disabled={disabled} onClick={() => void onMaterialize(shot.shotId)} type="button">{t("film.edit.materialize")}</button>}
              </div>
            );
          })}
        </article> : null}
      </div>
    </section>
  );
}

function QcReportList({ reports, title }: { readonly reports: readonly FilmQcReportView[]; readonly title: string }) {
  if (reports.length === 0) return null;
  return (
    <article>
      <h3>{title}</h3>
      {reports.map((report) => (
        <div className="film-qc-report" key={`${report.targetId}-${report.createdAt}`}>
          <header><b>{report.targetId.toUpperCase()}</b><span className={`film-qc ${report.passed ? "is-approved" : "is-failed"}`}>{report.passed ? "通过" : "未通过"}</span><small>{report.createdAt}</small></header>
          {report.checks.map((check) => <p key={check.name}><i className={`is-${check.status}`} /><b>{check.name}</b>{check.detail}{typeof check.metric === "number" ? <small> · {check.metric.toFixed(2)}</small> : null}</p>)}
        </div>
      ))}
    </article>
  );
}

function VisionQcReportList({ reports, title }: { readonly reports: readonly FilmVisionQcReportView[]; readonly title: string }) {
  if (reports.length === 0) return null;
  return (
    <article>
      <h3>{title}</h3>
      {reports.map((report) => (
        <div className="film-qc-report" key={`${report.targetId}-${report.createdAt}`}>
          <header><b>{report.targetId.toUpperCase()}</b><span className={`film-qc ${report.passed ? "is-approved" : "is-failed"}`}>{report.overallScore.toFixed(1)}/10 · {report.passed ? "可交付" : "需重拍"}</span><small>{report.retriesUsed > 0 ? `重试 ${report.retriesUsed} 次 · ` : ""}{report.createdAt}</small></header>
          {report.dimensions.map((dimension) => <p key={dimension.dimension}><i className={dimension.score >= 7 ? "is-pass" : dimension.score >= 5 ? "is-warn" : "is-fail"} /><b>{dimension.dimension}</b>{dimension.note}<small> · {dimension.score.toFixed(1)}</small></p>)}
        </div>
      ))}
    </article>
  );
}

function DeliveryBoard({ disabled, renderSpec, studio, onRender, onRenderSpecChange }: {
  readonly disabled: boolean;
  readonly renderSpec: RenderSpec;
  readonly studio: FilmStudioView;
  readonly onRender: () => Promise<void>;
  readonly onRenderSpecChange: (next: RenderSpec) => void;
}) {
  const delivery: FilmDeliveryManifestView | null = studio.delivery ?? null;
  const materialized = studio.mediaArtifacts.filter((item) => item.kind === "video" && item.localPath.length > 0).length;
  const presetValue = resolutionPresets.find((item) => item.width === renderSpec.width && item.height === renderSpec.height)?.value ?? "custom";
  const applyPreset = (value: string) => {
    if (value === "custom") return;
    const preset = resolutionPresets.find((item) => item.value === value);
    if (preset) onRenderSpecChange({ ...renderSpec, width: preset.width, height: preset.height });
  };
  return (
    <section className="film-delivery-panel">
      <header className="film-panel-heading">
        <div><span>DELIVERY</span><h2>母版渲染与交付清单</h2><p>FFmpeg 归一化拼接 + 字幕烧录 + 对白混音；渲染后自动执行母版质检并生成交付清单。</p></div>
        <div className="film-board-stats">
          <span>{materialized}/{studio.shots.length} 镜头落盘</span>
          <span>{delivery ? "已交付" : "未交付"}</span>
        </div>
      </header>
      <div className="film-delivery-grid">
        <article className="film-render-form">
          <h3>渲染规格</h3>
          <label>分辨率
            <StudioSelect ariaLabel="渲染分辨率" onChange={applyPreset} options={[...resolutionPresets.map((item) => ({ value: item.value, label: item.label })), { value: "custom", label: "自定义…" }]} value={presetValue} />
          </label>
          <div className="film-field-grid">
            <label>宽<input max={7680} min={320} onChange={(event) => onRenderSpecChange({ ...renderSpec, width: event.target.value ? Number(event.target.value) : null })} placeholder="默认" type="number" value={renderSpec.width ?? ""} /></label>
            <label>高<input max={7680} min={320} onChange={(event) => onRenderSpecChange({ ...renderSpec, height: event.target.value ? Number(event.target.value) : null })} placeholder="默认" type="number" value={renderSpec.height ?? ""} /></label>
            <label>帧率
              <StudioSelect ariaLabel="渲染帧率" onChange={(value) => onRenderSpecChange({ ...renderSpec, frameRate: value === "default" ? null : Number(value) })} options={[{ value: "default", label: `风格圣经 ${studio.productionBible.style.frameRate} FPS` }, { value: "24", label: "24 FPS · 电影感" }, { value: "25", label: "25 FPS · PAL" }, { value: "30", label: "30 FPS" }]} value={renderSpec.frameRate === null ? "default" : String(renderSpec.frameRate)} />
            </label>
            <label className="film-checkbox"><input checked={renderSpec.burnSubtitles} onChange={(event) => onRenderSpecChange({ ...renderSpec, burnSubtitles: event.target.checked })} type="checkbox" />烧录 SRT 字幕</label>
          </div>
          <button className="is-primary" disabled={disabled || materialized === 0} onClick={() => void onRender()} type="button">{disabled ? "渲染中…" : `渲染母版（${materialized} 个落盘镜头）`}</button>
          {materialized === 0 ? <p className="film-empty-hint">先到「剪辑」阶段把镜头媒体落盘，再渲染母版。</p> : null}
        </article>
        <article className="film-delivery-manifest">
          <h3>交付清单</h3>
          {delivery === null ? <p className="film-empty-hint">尚未生成母版。渲染完成后此处列出母版、字幕、OTIO 与各分镜文件路径。</p> : (
            <>
              <div className="film-manifest-meta">
                <span><small>母版视频</small><b>{delivery.masterVideoPath}</b></span>
                <span><small>字幕</small><b>{delivery.subtitlePath || "—"}</b></span>
                <span><small>OTIO</small><b>{delivery.otioPath || "—"}</b></span>
                <span><small>规格</small><b>{delivery.width}×{delivery.height} · {delivery.frameRate} FPS · {delivery.durationS.toFixed(1)}s</b></span>
                <span><small>镜头数</small><b>{delivery.shotCount} 段 · {delivery.clipPaths.length} 个分镜文件</b></span>
                <span><small>质检</small><b className={delivery.qcPassed ? "is-succeeded" : "is-failed"}>{delivery.qcPassed ? "母版质检通过" : "母版质检未通过"}</b></span>
                <span><small>合规</small><b className={delivery.compliancePassed ? "is-succeeded" : "is-failed"}>{delivery.compliancePassed ? "合规审核通过" : "合规未放行"}</b></span>
              </div>
              {delivery.notes.length > 0 ? <div className="film-manifest-notes">{delivery.notes.map((note) => <p key={note}>{note}</p>)}</div> : null}
            </>
          )}
        </article>
        <QcReportList reports={studio.qcReports} title="质检报告台账" />
        <VisionQcReportList reports={studio.visionQcReports} title="视觉质检台账（语义级软门禁）" />
      </div>
    </section>
  );
}

function ComplianceBoard({ studio }: { readonly studio: FilmStudioView }) {
  const report: FilmComplianceReportView | null = studio.complianceReport ?? null;
  const severityLabels: Readonly<Record<string, string>> = {
    red_line: "红线",
    high_risk: "高风险",
    positive_value: "正向价值观",
  };
  const actionLabels: Readonly<Record<string, string>> = {
    block: "拦截交付",
    revise: "需要整改",
    review: "建议优化",
    none: "无需处理",
  };
  return (
    <section className="film-delivery-panel">
      <header className="film-panel-heading">
        <div><span>COMPLIANCE</span><h2>合规审核（红线 / 高风险 / 正向价值观）</h2><p>确定性扫描负责红线与高风险硬规则；语义检查评估正向价值观。报告写入交付清单并拦截未放行交付。</p></div>
        <div className="film-board-stats">
          <span>{report ? "已审核" : "未审核"}</span>
          <span>{report ? report.summary : "推进到本阶段后自动生成"}</span>
        </div>
      </header>
      {report === null ? <p className="film-empty-hint">尚未执行合规审核。在剪辑完成后推进到本阶段，系统会自动扫描剧本文本并生成可执行报告。</p> : (
        <div className="film-delivery-grid">
          <article className="film-delivery-manifest">
            <h3>审核结论</h3>
            <div className="film-manifest-meta">
              <span><small>放行状态</small><b className={report.blocked ? "is-failed" : report.passed ? "is-succeeded" : "is-failed"}>{report.blocked ? "红线拦截，禁止交付" : report.passed ? "合规通过，可交付" : "存在高风险项，待整改"}</b></span>
              <span><small>检查项总数</small><b>{report.findings.length} 项发现</b></span>
              <span><small>审核时间</small><b>{report.createdAt}</b></span>
            </div>
          </article>
          <article className="film-delivery-manifest">
            <h3>可执行整改清单</h3>
            {report.findings.length === 0 ? <p className="film-empty-hint">未发现红线与高风险问题，正向价值观检查全部成立。</p> : (
              <div className="film-manifest-notes">
                {report.findings.map((finding, index) => (
                  <p key={`${finding.checkId}-${index}`}>
                    【{severityLabels[finding.severity] ?? finding.severity} · {actionLabels[finding.action] ?? finding.action}】{finding.title}：{finding.detail}{finding.location ? `（${finding.location}）` : ""}
                  </p>
                ))}
              </div>
            )}
          </article>
        </div>
      )}
    </section>
  );
}

function JobLedger({ disabled, jobs, onCancelJob }: {
  readonly disabled: boolean;
  readonly jobs: readonly FilmJobView[];
  readonly onCancelJob: (jobId: string) => Promise<void>;
}) {
  const t = useT();
  if (jobs.length === 0) return <p className="film-empty-hint">暂无任务记录。生成、落盘与渲染任务都会登记到账本，重复提交自动去重。</p>;
  return (
    <table className="film-job-table">
      <thead><tr><th>任务</th><th>目标 / 路由</th><th>状态</th><th>尝试</th><th>备注</th><th>操作</th></tr></thead>
      <tbody>
        {jobs.map((job) => (
          <tr key={job.jobId}>
            <td><b>{localizedCatalogValue(t, jobKindLabelKey(job.kind), job.kind)}</b><small>{shortId(job.jobId, 10)}</small></td>
            <td><b>{job.targetId}</b><small>{job.providerId ? `${job.providerId}:${job.modelId}` : t("film.common.localPipeline")}</small></td>
            <td><span className={`film-qc is-${job.state === "succeeded" ? "approved" : job.state === "failed" || job.state === "cancelled" ? "failed" : "review"}`}>{statusLabel(job.state, t)}</span></td>
            <td>{job.attempts}/{job.maxAttempts}</td>
            <td><small title={job.errorMessage}>{job.errorMessage ? shortId(job.errorMessage, 24) : `更新于 ${job.updatedAt.slice(11, 19)}`}</small></td>
            <td>{job.state === "queued" || job.state === "running" ? <button disabled={disabled} onClick={() => void onCancelJob(job.jobId)} type="button">{t("film.common.cancel")}</button> : null}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function FilmSettingsPanel({ catalog, disabled, renderSpec, routeDefaults, studio, onClose, onCancelJob, onRenderSpecChange, onRouteDefaultsChange }: {
  readonly catalog: FilmProviderCatalogView;
  readonly disabled: boolean;
  readonly renderSpec: RenderSpec;
  readonly routeDefaults: RouteDefaults;
  readonly studio: FilmStudioView;
  readonly onClose: () => void;
  readonly onCancelJob: (jobId: string) => Promise<void>;
  readonly onRenderSpecChange: (next: RenderSpec) => void;
  readonly onRouteDefaultsChange: (next: RouteDefaults) => void;
}) {
  const t = useT();
  const providers = providerOptions(catalog, t);
  const imageModels = modelOptions(catalog, routeDefaults.imageProvider, "imageModels", t);
  const videoModels = modelOptions(catalog, routeDefaults.videoProvider, "videoModels", t);
  const defaultVideoModel = videoModels.find((model) => model.value === routeDefaults.videoModel)
    ?? preferredModel(videoModels);
  const activeJobs = studio.jobs.filter((job) => job.state === "queued" || job.state === "running").length;
  return (
    <section className="film-settings-overlay" role="dialog" aria-label="制片设置">
      <header>
        <div><span>PRODUCTION SETTINGS</span><h2>制片设置</h2><small>默认生成路由、渲染规格与任务账本；参数即时生效并用于后续提交。</small></div>
        <button aria-label="关闭制片设置" onClick={onClose} type="button">×</button>
      </header>
      <div className="film-settings-grid">
        <article>
          <h3>默认生成路由</h3>
          <p className="film-settings-hint">镜头 / 资产未单独指定路由时采用以下默认值。平台能力差异见选项说明。</p>
          <label>图像平台<StudioSelect ariaLabel="默认图像平台" onChange={(next) => onRouteDefaultsChange({ ...routeDefaults, imageProvider: next, imageModel: modelOptions(catalog, next, "imageModels", t)[0]?.value ?? "" })} options={providers} value={routeDefaults.imageProvider} /></label>
          <label>图像模型<StudioSelect ariaLabel="默认图像模型" onChange={(next) => onRouteDefaultsChange({ ...routeDefaults, imageModel: next })} options={imageModels} value={routeDefaults.imageModel} /></label>
          <label>视频平台</label>
          <div aria-label="默认视频平台" className="film-provider-switch" role="tablist">
            {providerOptions(catalog, t, true).map((option) => (
              <button
                aria-selected={routeDefaults.videoProvider === option.value}
                className={routeDefaults.videoProvider === option.value ? "is-active" : ""}
                key={option.value}
                onClick={() => {
                  const nextModels = modelOptions(catalog, option.value, "videoModels", t);
                  onRouteDefaultsChange({
                    ...routeDefaults,
                    videoProvider: option.value,
                    videoModel: preferredModel(nextModels)?.value ?? "",
                  });
                }}
                role="tab"
                title={option.description}
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
          <label>视频模型<StudioSelect ariaLabel="默认视频模型" onChange={(next) => onRouteDefaultsChange({ ...routeDefaults, videoModel: next })} options={videoModels} value={routeDefaults.videoModel} /></label>
          <div className="film-provider-summary">
            <div><strong>{catalog[routeDefaults.videoProvider]?.label}</strong><small>{defaultVideoModel?.label}</small></div>
            {catalog[routeDefaults.videoProvider]?.docsUrl ? <a href={catalog[routeDefaults.videoProvider]?.docsUrl} rel="noreferrer" target="_blank">{t("film.common.officialDocs")}</a> : null}
            <p>{catalog[routeDefaults.videoProvider]?.bestFor ? localizedCatalogValue(t, `film.provider.${routeDefaults.videoProvider}.bestFor`, catalog[routeDefaults.videoProvider]?.bestFor ?? "") : null}</p>
          </div>
          <div className="film-provider-pills">{(defaultVideoModel?.features.length ? defaultVideoModel.features : catalog[routeDefaults.videoProvider]?.strengths ?? []).slice(0, 8).map((feature) => <span key={feature}>{localizedCatalogValue(t, featureLabelKey(feature), feature)}</span>)}</div>
        </article>
        <article>
          <h3>默认渲染规格</h3>
          <p className="film-settings-hint">交付阶段渲染母版时采用；可在交付面板临时覆盖。</p>
          <div className="film-field-grid">
            <label>宽<input max={7680} min={320} onChange={(event) => onRenderSpecChange({ ...renderSpec, width: event.target.value ? Number(event.target.value) : null })} placeholder="默认" type="number" value={renderSpec.width ?? ""} /></label>
            <label>高<input max={7680} min={320} onChange={(event) => onRenderSpecChange({ ...renderSpec, height: event.target.value ? Number(event.target.value) : null })} placeholder="默认" type="number" value={renderSpec.height ?? ""} /></label>
            <label>帧率<input max={120} min={1} onChange={(event) => onRenderSpecChange({ ...renderSpec, frameRate: event.target.value ? Number(event.target.value) : null })} placeholder={`圣经 ${studio.productionBible.style.frameRate}`} step="0.001" type="number" value={renderSpec.frameRate ?? ""} /></label>
            <label className="film-checkbox"><input checked={renderSpec.burnSubtitles} onChange={(event) => onRenderSpecChange({ ...renderSpec, burnSubtitles: event.target.checked })} type="checkbox" />烧录 SRT 字幕</label>
          </div>
        </article>
        <article className="film-settings-jobs">
          <h3>任务账本 <small>（活跃 {activeJobs} · 共 {studio.jobs.length}）</small></h3>
          <JobLedger disabled={disabled} jobs={studio.jobs} onCancelJob={onCancelJob} />
        </article>
      </div>
    </section>
  );
}

const FilmTimeline = memo(function FilmTimeline({
  height,
  isCollapsed,
  onHeightChange,
  onHide,
  onToggle,
  studio,
}: {
  readonly height: number;
  readonly isCollapsed: boolean;
  readonly onHeightChange: (height: number) => void;
  readonly onHide: () => void;
  readonly onToggle: () => void;
  readonly studio: FilmStudioView;
}) {
  const t = useT();
  const maxDuration = Math.max(24, ...studio.timeline.tracks.flatMap((track) => track.clips.map((clip) => clip.startS + clip.durationS)));
  return <section className={`film-timeline ${isCollapsed ? "is-collapsed" : ""}`}><header><div><span>MASTER TIMELINE · OTIO</span><strong>{studio.timeline.name}</strong></div>{isCollapsed ? null : <div className="film-time-ruler">{[0, .25, .5, .75, 1].map((ratio) => <span key={ratio}>{new Date(maxDuration * ratio * 1000).toISOString().slice(14, 19)}</span>)}</div>}{isCollapsed ? null : <label className="film-timeline-size" title={t("film.timeline.heightAria")}><span>{t("film.timeline.height")}</span><input aria-label={t("film.timeline.heightAria")} max={360} min={120} onChange={(event) => onHeightChange(Number(event.target.value))} type="range" value={height} /></label>}<div className="film-timeline-actions"><button className="film-timeline-hide" onClick={onHide} title={t("film.timeline.hideTitle")} type="button">{t("film.timeline.hide")}</button><button aria-controls="film-master-timeline-tracks" aria-expanded={!isCollapsed} className="film-timeline-toggle" onClick={onToggle} title={t(isCollapsed ? "film.timeline.expand" : "film.timeline.collapse")} type="button"><span aria-hidden="true">{isCollapsed ? "⌃" : "⌄"}</span>{t(isCollapsed ? "film.timeline.expand" : "film.timeline.collapse")}</button></div></header><div className="film-track-stack" hidden={isCollapsed} id="film-master-timeline-tracks">{studio.timeline.tracks.map((track) => <div className={`film-track is-${track.kind}`} key={track.trackId}><label><b>{track.name}</b><small>{t("film.timeline.clips", { count: track.clips.length })}</small></label><div className="film-track-lane">{track.clips.map((clip) => <span key={clip.clipId} style={{ left: `${(clip.startS / maxDuration) * 100}%`, width: `${Math.max(4, (clip.durationS / maxDuration) * 100)}%` }} title={clip.name}>{clip.name}</span>)}</div></div>)}</div></section>;
});

type FilmTimelineMode = "hidden" | "peek" | "expanded";

export function defaultFilmTimelineMode(stage: FilmStageId): FilmTimelineMode {
  return ["shot_production", "sound_picture", "edit", "compliance", "delivery"].includes(stage)
    ? "peek"
    : "hidden";
}

export function FilmStudioPage({ catalog, commandClient, engineClient, format, onFormatChange, onProjectChange, onStudioChange, projects, studio }: FilmStudioPageProps) {
  const t = useT();
  const [canvasRequest, setCanvasRequest] = useState(0);
  const [selectedStage, setSelectedStage] = useState<FilmStageId>(studio.currentStage);
  // 高亮同步：项目切换时无条件回到引擎当前阶段；同一项目内若用户未手动
  // 选过阶段，则跟随引擎推进（advance/bootstrap 后高亮立即落到新阶段）。
  const manualStageSelection = useRef(false);
  useEffect(() => {
    if (stageProjectRef.current !== studio.projectId) {
      stageProjectRef.current = studio.projectId;
      manualStageSelection.current = false;
      setSelectedStage(studio.currentStage);
      return;
    }
    if (!manualStageSelection.current) setSelectedStage(studio.currentStage);
  }, [studio.projectId, studio.currentStage]);
  const [selectedSceneId, setSelectedSceneId] = useState(studio.activeSceneId || studio.screenplay.scenes[0]?.sceneId || "");
  const initialShot = studio.shots.find((shot) => shot.shotId === studio.activeShotId) ?? studio.shots.find((shot) => shot.sceneId === selectedSceneId) ?? studio.shots[0];
  const [selectedShotId, setSelectedShotId] = useState(initialShot?.shotId ?? "");
  const [assetDetailId, setAssetDetailId] = useState<string | null>(null);
  const [timelineModes, setTimelineModes] = useState<Partial<Record<FilmStageId, FilmTimelineMode>>>(() => {
    try {
      return JSON.parse(window.localStorage.getItem("nimo.film.timeline-modes.v1") ?? "{}") as Partial<Record<FilmStageId, FilmTimelineMode>>;
    } catch {
      return {};
    }
  });
  const [timelineHeight, setTimelineHeight] = useState(174);
  const [showSettings, setShowSettings] = useState(false);
  const [isCanvasFocus, setIsCanvasFocus] = useState(false);
  const [busyLabel, setBusyLabel] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [renderSpec, setRenderSpec] = useState<RenderSpec>({ width: null, height: null, frameRate: null, burnSubtitles: true });
  const stageProjectRef = useRef(studio.projectId);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const stageScrollPositions = useRef(new Map<FilmStageId, number>());
  const [routeDefaults, setRouteDefaults] = useState<RouteDefaults>(() => ({
    imageProvider: "bailian",
    imageModel: preferredModel(modelOptions(catalog, "bailian", "imageModels"))?.value ?? "",
    videoProvider: "bailian",
    videoModel: preferredModel(modelOptions(catalog, "bailian", "videoModels"))?.value ?? "",
  }));
  const selectedShot = studio.shots.find((shot) => shot.shotId === selectedShotId) ?? studio.shots.find((shot) => shot.sceneId === selectedSceneId) ?? studio.shots[0];
  const detailAsset = assetDetailId === null ? undefined : studio.visualAssets.find((asset) => asset.assetId === assetDetailId);
  const timelineMode = timelineModes[selectedStage] ?? defaultFilmTimelineMode(selectedStage);
  const setTimelineMode = useCallback((mode: FilmTimelineMode) => {
    setTimelineModes((current) => {
      const next = { ...current, [selectedStage]: mode };
      window.localStorage.setItem("nimo.film.timeline-modes.v1", JSON.stringify(next));
      return next;
    });
  }, [selectedStage]);

  const perform = useCallback(async (label: string, operation: () => Promise<FilmStudioView>): Promise<FilmStudioView | null> => {
    setBusyLabel(label); setError("");
    try { const next = await operation(); onStudioChange(next); setNotice(t("film.notice.completed", { label })); return next; }
    catch (caught) { setError(caught instanceof Error ? caught.message : t("film.notice.failed", { label })); return null; }
    finally { setBusyLabel(""); }
  }, [onStudioChange, t]);
  const handleAdvance = useCallback(async () => {
    const target = studio.mode === "autonomous" ? "delivery" : undefined;
    const next = await perform(t(studio.mode === "autonomous" ? "film.command.aiProduction" : "film.command.reviewAdvance"), () => commandClient.advanceFilm({ kind: "advance_film", projectId: studio.projectId, mode: studio.mode, useAi: true, ...(target ? { runUntil: target } : {}) }));
    if (next) {
      manualStageSelection.current = false;
      setSelectedStage(next.currentStage);
    }
  }, [commandClient, perform, studio.mode, studio.projectId, t]);
  const handleGenerateAsset = useCallback(async (asset: FilmVisualAssetView, providerId: string, modelId: string) => { await perform(`生成 ${asset.name}`, () => commandClient.generateFilmAsset({ kind: "generate_film_asset", projectId: studio.projectId, assetId: asset.assetId, providerId, modelId, imageCount: 4 })); }, [commandClient, perform, studio.projectId]);
  const handleSelectAsset = useCallback(async (asset: FilmVisualAssetView, url: string, lock: boolean) => { await perform(lock ? "锁定资产" : "选择候选", () => commandClient.selectFilmAsset({ kind: "select_film_asset", projectId: studio.projectId, assetId: asset.assetId, url, lock })); }, [commandClient, perform, studio.projectId]);
  const handleSaveShot = useCallback(async (patch: Readonly<Record<string, unknown>>) => { if (selectedShot) await perform("保存镜头", () => commandClient.updateFilmShot({ kind: "update_film_shot", projectId: studio.projectId, shotId: selectedShot.shotId, patch })); }, [commandClient, perform, selectedShot, studio.projectId]);
  const handleGenerateShot = useCallback(async (patch: Readonly<Record<string, unknown>>, providerId: string, modelId: string) => { if (!selectedShot) return; const saved = await perform("保存导演方案", () => commandClient.updateFilmShot({ kind: "update_film_shot", projectId: studio.projectId, shotId: selectedShot.shotId, patch })); if (saved) await perform("提交镜头生成", () => commandClient.generateFilmShot({ kind: "generate_film_shot", projectId: studio.projectId, shotId: selectedShot.shotId, providerId, modelId })); }, [commandClient, perform, selectedShot, studio.projectId]);
  const handleQueryTask = useCallback(async (targetId: string) => { await perform("查询媒体任务", () => commandClient.queryFilmMedia({ kind: "query_film_media", projectId: studio.projectId, targetId })); }, [commandClient, perform, studio.projectId]);
  const handlePollShot = useCallback(async (shotId: string) => { await perform(`等待 ${shotId} 完成`, () => commandClient.pollFilmShot({ kind: "poll_film_shot", projectId: studio.projectId, shotId })); }, [commandClient, perform, studio.projectId]);
  const handleMaterialize = useCallback(async (targetId: string) => { await perform(`落盘 ${targetId}`, () => commandClient.materializeFilmMedia({ kind: "materialize_film_media", projectId: studio.projectId, targetId })); }, [commandClient, perform, studio.projectId]);
  const handleMaterializeAll = useCallback(async (targetIds: readonly string[]) => {
    for (const targetId of targetIds) {
      const next = await perform(`落盘 ${targetId}`, () => commandClient.materializeFilmMedia({ kind: "materialize_film_media", projectId: studio.projectId, targetId }));
      if (next === null) break;
    }
  }, [commandClient, perform, studio.projectId]);
  const handleQcShot = useCallback(async (shotId: string) => { await perform(`质检 ${shotId}`, () => commandClient.qcFilmShot({ kind: "qc_film_shot", projectId: studio.projectId, shotId })); }, [commandClient, perform, studio.projectId]);
  const handleBatchGenerate = useCallback(async (shotIds: readonly string[]) => { if (shotIds.length === 0) return; await perform(`批量生成 ${shotIds.length} 个镜头`, () => commandClient.batchGenerateFilmShots({ kind: "batch_generate_film_shots", projectId: studio.projectId, shotIds })); }, [commandClient, perform, studio.projectId]);
  const handleRenderMaster = useCallback(async () => {
    await perform("渲染母版", () => commandClient.renderFilmMaster({
      kind: "render_film_master",
      projectId: studio.projectId,
      burnSubtitles: renderSpec.burnSubtitles,
      ...(renderSpec.width !== null ? { width: renderSpec.width } : {}),
      ...(renderSpec.height !== null ? { height: renderSpec.height } : {}),
      ...(renderSpec.frameRate !== null ? { frameRate: renderSpec.frameRate } : {}),
    }));
  }, [commandClient, perform, renderSpec, studio.projectId]);
  const handleCancelJob = useCallback(async (jobId: string) => { await perform("取消任务", () => commandClient.cancelFilmJob({ kind: "cancel_film_job", projectId: studio.projectId, jobId })); }, [commandClient, perform, studio.projectId]);
  const isBoardStage = selectedStage === "storyboard" || selectedStage === "shot_production";
  const isStoryboardStage = selectedStage === "storyboard";
  const projectOptions = projects.map((project) => ({ value: project.id, label: project.title, description: t(project.mode === "long" ? "film.project.long" : "film.project.short") }));
  const handleStageSelect = useCallback((stage: FilmStageId) => {
    if (workspaceRef.current !== null) stageScrollPositions.current.set(selectedStage, workspaceRef.current.scrollTop);
    manualStageSelection.current = true;
    setIsCanvasFocus(false);
    setSelectedStage(stage);
    setAssetDetailId(null);
    window.requestAnimationFrame(() => {
      workspaceRef.current?.scrollTo({ top: stageScrollPositions.current.get(stage) ?? 0 });
    });
  }, [selectedStage]);
  const handleCanvasFocusChange = useCallback((focused: boolean) => {
    setIsCanvasFocus(focused);
    if (focused) setShowSettings(false);
  }, []);
  const handleWorkspaceKeyDown = useCallback((event: ReactKeyboardEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (target !== event.currentTarget && target.matches("input, textarea, select, [contenteditable=true]")) return;
    const workspace = event.currentTarget;
    if (event.key === "PageDown") {
      event.preventDefault();
      workspace.scrollBy({ top: workspace.clientHeight * .86, behavior: "auto" });
    } else if (event.key === "PageUp") {
      event.preventDefault();
      workspace.scrollBy({ top: -workspace.clientHeight * .86, behavior: "auto" });
    } else if (event.key === "Home") {
      event.preventDefault();
      workspace.scrollTo({ top: 0, behavior: "auto" });
    } else if (event.key === "End") {
      event.preventDefault();
      workspace.scrollTo({ top: workspace.scrollHeight, behavior: "auto" });
    } else if (event.key.toLowerCase() === "t") {
      event.preventDefault();
      setTimelineMode(timelineMode === "hidden" ? "peek" : "hidden");
    }
  }, [setTimelineMode, timelineMode]);

  // 短剧 / 漫画形态：与剧集主线同构的壳层——命令栏 + 左阶段轨 + 右工作区。
  if (format === "drama" || format === "comic") {
    const enterFeatureLine = () => {
      onFormatChange("feature");
      manualStageSelection.current = true;
      setSelectedStage("storyboard");
    };
    return <div className="film-studio-shell is-format-mode">
      <header className="film-command-bar">
        <div className="film-project-control">
          <span>{t("film.command.activeProject")}</span>
          <StudioSelect ariaLabel={t("film.command.projectAria")} onChange={onProjectChange} options={projectOptions} value={studio.projectId} />
          <small>{t(format === "drama" ? "film.format.dramaProjectHint" : "film.format.comicProjectHint")}</small>
        </div>
        <FilmFormatSwitcher format={format} onChange={onFormatChange} />
        <div className="film-command-actions">
          <button disabled={busyLabel.length > 0} onClick={() => void perform(t("film.command.syncSources"), () => commandClient.bootstrapFilm({ kind: "bootstrap_film", projectId: studio.projectId, mode: studio.mode, refreshSources: true }))} type="button">{t("film.command.syncSources")}</button>
        </div>
      </header>
      {notice || error ? <div className={`film-notice ${error ? "is-error" : ""}`}><span>{error || notice}</span><button onClick={() => { setNotice(""); setError(""); }} type="button">×</button></div> : null}
      <div className="film-workspace-grid is-format">
        <div className="film-format-content" key={format}>
          <SourceLockStrip studio={studio} />
          {format === "drama" ? (
            <DramaWorkspace commandClient={commandClient} engineClient={engineClient} onEnterFeatureLine={enterFeatureLine} onNotice={setNotice} projectId={studio.projectId} />
          ) : (
            <ComicWorkspace commandClient={commandClient} engineClient={engineClient} onEnterFeatureLine={enterFeatureLine} onNotice={setNotice} projectId={studio.projectId} />
          )}
        </div>
      </div>
    </div>;
  }

  let mainContent: ReactNode;
  if (detailAsset) mainContent = <AssetDetailPage asset={detailAsset} catalog={catalog} disabled={busyLabel.length > 0} onBack={() => setAssetDetailId(null)} onGenerate={handleGenerateAsset} onSelect={handleSelectAsset} studio={studio} />;
  else if (selectedStage === "planning") mainContent = <ProductionOverview canvasRequest={canvasRequest} commandClient={commandClient} engineClient={engineClient} onCanvasFocusChange={handleCanvasFocusChange} onError={setError} onNotice={setNotice} studio={studio} />;
  else if (selectedStage === "screenplay") mainContent = <ScreenplayBoard studio={studio} />;
  else if (selectedStage === "visual_development") mainContent = <AssetBoard catalog={catalog} disabled={busyLabel.length > 0} onGenerate={handleGenerateAsset} onOpen={setAssetDetailId} routeDefaults={routeDefaults} studio={studio} />;
  else if (isStoryboardStage) mainContent = <StoryboardBoard activeSceneId={selectedSceneId} onEnterShotLine={() => { manualStageSelection.current = true; setSelectedStage("shot_production"); }} studio={studio} />;
  else if (isBoardStage) mainContent = <ShotProductionTable activeSceneId={selectedSceneId} activeShotId={selectedShotId} catalog={catalog} onSelectShot={setSelectedShotId} studio={studio} />;
  else if (selectedStage === "sound_picture") mainContent = <SoundPictureBoard disabled={busyLabel.length > 0} onMaterialize={handleMaterialize} studio={studio} />;
  else if (selectedStage === "edit") mainContent = <EditBoard disabled={busyLabel.length > 0} onBatchGenerate={handleBatchGenerate} onMaterialize={handleMaterialize} onMaterializeAll={handleMaterializeAll} studio={studio} />;
  else if (selectedStage === "compliance") mainContent = <ComplianceBoard studio={studio} />;
  else if (selectedStage === "delivery") mainContent = <DeliveryBoard disabled={busyLabel.length > 0} onRender={handleRenderMaster} onRenderSpecChange={setRenderSpec} renderSpec={renderSpec} studio={studio} />;
  else mainContent = <ProductionOverview canvasRequest={canvasRequest} commandClient={commandClient} engineClient={engineClient} onCanvasFocusChange={handleCanvasFocusChange} onError={setError} onNotice={setNotice} studio={studio} />;

  return <div className={`film-studio-shell ${timelineMode === "hidden" ? "is-timeline-hidden" : timelineMode === "peek" ? "is-timeline-collapsed" : ""} ${isCanvasFocus ? "is-canvas-focus" : ""}`} style={{ "--film-timeline-height": `${timelineMode === "hidden" ? 0 : timelineMode === "peek" ? 44 : timelineHeight}px` } as CSSProperties}>
    <header className="film-command-bar">
      <div className="film-project-control"><span>{t("film.command.activeProject")}</span><StudioSelect ariaLabel={t("film.command.projectAria")} onChange={onProjectChange} options={projectOptions} value={studio.projectId} /><small>{studio.productionBible.style.aspectRatio} · {studio.productionBible.style.frameRate} FPS · {t("film.unit.scenes", { n: studio.screenplay.scenes.length })}</small></div>
      <FilmFormatSwitcher format={format} onChange={onFormatChange} />
      <div className="film-mode-switch" role="group" aria-label={t("film.command.modeAria")}><button className={studio.mode === "collaborative" ? "is-active" : ""} onClick={() => void perform(t("film.command.collaborative"), () => commandClient.bootstrapFilm({ kind: "bootstrap_film", projectId: studio.projectId, mode: "collaborative", refreshSources: false }))} type="button">{t("film.command.collaborative")}</button><button className={studio.mode === "autonomous" ? "is-active" : ""} onClick={() => void perform(t("film.command.autonomous"), () => commandClient.bootstrapFilm({ kind: "bootstrap_film", projectId: studio.projectId, mode: "autonomous", refreshSources: false }))} type="button">{t("film.command.autonomous")}</button></div>
      <div className="film-command-actions"><button disabled={busyLabel.length > 0} onClick={() => void perform(t("film.command.syncSources"), () => commandClient.bootstrapFilm({ kind: "bootstrap_film", projectId: studio.projectId, mode: studio.mode, refreshSources: true }))} type="button">{t("film.command.syncSources")}</button><button disabled={busyLabel.length > 0} onClick={() => void commandClient.exportFilmTimeline({ kind: "export_film_timeline", projectId: studio.projectId }).then((result) => setNotice(`${t("film.command.exportOtio")}: ${result.path}`))} type="button">{t("film.command.exportOtio")}</button><button aria-expanded={showSettings} className={showSettings ? "is-active" : ""} onClick={() => setShowSettings((open) => !open)} type="button">{t("film.command.productionSettings")}</button><button className="is-primary" disabled={busyLabel.length > 0} onClick={() => void handleAdvance()} type="button">{busyLabel || t(studio.mode === "autonomous" ? "film.command.aiProduction" : "film.command.reviewAdvance")}</button></div>
    </header>
    <div className="film-stage-navigation">
      <button className="film-canvas-entry" onClick={() => {
        handleStageSelect("planning");
        setCanvasRequest((request) => request + 1);
      }} type="button">{t("film.workbench.expert")}</button>
      <StageRail active={selectedStage} onSelect={handleStageSelect} studio={studio} />
    </div>
    {notice || error ? <div className={`film-notice ${error ? "is-error" : ""}`}><span>{error || notice}</span><button onClick={() => { setNotice(""); setError(""); }} type="button">×</button></div> : null}
    <div aria-label={`${selectedStage} 工作区`} className={`film-workspace-grid ${selectedStage === "planning" ? "is-planning" : ""} ${isBoardStage && !detailAsset ? `is-board${isStoryboardStage ? " is-storyboard" : ""}` : ""}`} onKeyDown={handleWorkspaceKeyDown} ref={workspaceRef} tabIndex={0}>{isBoardStage && !detailAsset ? <SceneRail activeSceneId={selectedSceneId} onSelect={(sceneId) => { setSelectedSceneId(sceneId); const first = studio.shots.find((shot) => shot.sceneId === sceneId); if (first) setSelectedShotId(first.shotId); }} studio={studio} /> : null}{mainContent}{!isStoryboardStage && isBoardStage && !detailAsset && selectedShot ? <ShotInspector catalog={catalog} disabled={busyLabel.length > 0} key={selectedShot.shotId} onGenerate={handleGenerateShot} onMaterialize={handleMaterialize} onPoll={handlePollShot} onQc={handleQcShot} onQueryTask={handleQueryTask} onSave={handleSaveShot} routeDefaults={routeDefaults} shot={selectedShot} studio={studio} /> : null}</div>
    {showSettings ? <FilmSettingsPanel catalog={catalog} disabled={busyLabel.length > 0} onCancelJob={handleCancelJob} onClose={() => setShowSettings(false)} onRenderSpecChange={setRenderSpec} onRouteDefaultsChange={setRouteDefaults} renderSpec={renderSpec} routeDefaults={routeDefaults} studio={studio} /> : null}
    {timelineMode === "hidden" ? <button aria-expanded="false" className="film-timeline-launcher" onClick={() => setTimelineMode("peek")} title={t("film.timeline.showTitle")} type="button"><span aria-hidden="true">▥</span>{t("film.timeline.show")}</button> : <FilmTimeline height={timelineHeight} isCollapsed={timelineMode === "peek"} onHeightChange={setTimelineHeight} onHide={() => setTimelineMode("hidden")} onToggle={() => setTimelineMode(timelineMode === "peek" ? "expanded" : "peek")} studio={studio} />}
  </div>;
}
