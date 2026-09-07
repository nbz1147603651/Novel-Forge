/**
 * DramaWorkspace — 映界 · 短剧形态工作台。
 *
 * 短剧不是独立模块，而是映界的剧集化产出形态：它共享映界的项目选择器、
 * 上游资产锁（小说契约 / 角色身份 / 声纹表演）与阶段轨交互语言。剧本完成
 * 后可通过「进入剧集主镜线」把分集剧本回流到映界分镜/镜头阶段。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  DramaExportResult,
  DramaStudioView,
  EngineClient,
  EngineCommandClient,
} from "@nimo/engine-contracts";
import { useLocale, useT } from "../lib/i18n";

export interface DramaWorkspaceProps {
  readonly commandClient: EngineCommandClient;
  readonly engineClient: EngineClient;
  readonly projectId: string;
  readonly onEnterFeatureLine: () => void;
  readonly onNotice: (message: string) => void;
}

type DramaStageId = "plan" | "outline" | "screenplay" | "export";

const STAGE_ORDER: readonly DramaStageId[] = ["plan", "outline", "screenplay", "export"];

const stageTitleKey = (stage: DramaStageId): string => `film.drama.stage.${stage}`;

export function DramaWorkspace({
  commandClient,
  engineClient,
  projectId,
  onEnterFeatureLine,
  onNotice,
}: DramaWorkspaceProps) {
  const t = useT();
  const locale = useLocale();
  const [studio, setStudio] = useState<DramaStudioView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyStage, setBusyStage] = useState<DramaStageId | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [exportResult, setExportResult] = useState<DramaExportResult | null>(null);
  const [activeStage, setActiveStage] = useState<DramaStageId>("plan");

  const [planTitle, setPlanTitle] = useState("");
  const [totalEpisodes, setTotalEpisodes] = useState(8);
  const [genre, setGenre] = useState("都市");
  const [outlineStart, setOutlineStart] = useState(1);
  const [outlineEnd, setOutlineEnd] = useState(3);
  const [screenplayEpisode, setScreenplayEpisode] = useState(1);

  const loadStudio = useCallback(
    async (nextProjectId: string) => {
      if (nextProjectId.length === 0) return;
      setLoadError(null);
      try {
        const view = await engineClient.getDramaStudio(nextProjectId);
        setStudio(view);
        // 入场即闭环：自动定位到第一个未完成阶段，用户一眼知道下一步该做什么。
        setActiveStage(
          view.seriesPlan === null
            ? "plan"
            : view.outlines.length === 0
              ? "outline"
              : view.screenplays.length === 0
                ? "screenplay"
                : "export",
        );
      } catch (error) {
        setLoadError(error instanceof Error ? error.message : String(error));
      }
    },
    [engineClient],
  );

  useEffect(() => {
    setStudio(null);
    setExportResult(null);
    void loadStudio(projectId);
  }, [projectId, loadStudio]);

  const runStage = useCallback(async (stage: DramaStageId, action: () => Promise<DramaStudioView>) => {
    setBusyStage(stage);
    setActionError(null);
    try {
      const next = await action();
      setStudio(next);
      const orderIndex = STAGE_ORDER.indexOf(stage);
      const nextStage = STAGE_ORDER[Math.min(orderIndex + 1, STAGE_ORDER.length - 1)] ?? stage;
      setActiveStage(nextStage);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyStage(null);
    }
  }, []);

  const stageStatus = useMemo(() => {
    const planned = studio?.seriesPlan != null;
    const outlined = (studio?.outlines.length ?? 0) > 0;
    const scripted = (studio?.screenplays.length ?? 0) > 0;
    return {
      plan: planned ? "done" : "idle",
      outline: outlined ? "done" : planned ? "ready" : "locked",
      screenplay: scripted ? "done" : outlined ? "ready" : "locked",
      export: exportResult != null ? "done" : scripted ? "ready" : "locked",
    } as Record<DramaStageId, "done" | "ready" | "locked" | "idle">;
  }, [studio, exportResult]);

  const paywallMarkers = useMemo(
    () => (studio?.timelineMarkers ?? []).filter((marker) => marker.name.startsWith("paywall:")),
    [studio],
  );

  // 下一步提示：按生产顺序指向第一个未完成阶段；全部完成后提示闭环与回流入口。
  const nextStep = useMemo(() => {
    if (studio === null) return null;
    if (studio.seriesPlan === null) return { stage: "plan" as DramaStageId, hint: t("film.drama.next.plan") };
    if (studio.outlines.length === 0) return { stage: "outline" as DramaStageId, hint: t("film.drama.next.outline") };
    if (studio.screenplays.length === 0) return { stage: "screenplay" as DramaStageId, hint: t("film.drama.next.screenplay") };
    if (exportResult === null) return { stage: "export" as DramaStageId, hint: t("film.drama.next.export") };
    return null;
  }, [studio, exportResult, t]);

  if (studio === null) {
    return (
      <section className="film-overview-panel film-format-panel">
        <header className="film-panel-heading">
          <div><span>DRAMA LINE</span><h2>{t("film.drama.loading")}</h2></div>
        </header>
        {loadError !== null ? <p className="film-empty-hint">{loadError}</p> : null}
      </section>
    );
  }

  const outlineMax = studio.seriesPlan?.totalEpisodes ?? 200;
  const screenplayOutlineReady = studio.outlines.some(
    (outline) => outline.episodeNumber === screenplayEpisode,
  );

  return (
    <div className="film-format-workspace">
      <nav aria-label={t("film.drama.stageAria")} className="film-stage-rail is-inline">
        {STAGE_ORDER.map((stageId, index) => {
          const status = stageStatus[stageId];
          return (
            <button
              aria-current={activeStage === stageId ? "step" : undefined}
              className={`film-stage ${activeStage === stageId ? "is-active" : ""} is-${status === "done" ? "completed" : status === "locked" ? "blocked" : "ready"}${busyStage === stageId ? " is-busy" : ""}`}
              key={stageId}
              onClick={() => setActiveStage(stageId)}
              type="button"
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{t(stageTitleKey(stageId))}</strong>
              <i aria-hidden="true" />
            </button>
          );
        })}
      </nav>

      {nextStep === null ? (
        <p className="film-next-step is-done"><span>{t("film.drama.closed")}</span>{t("film.drama.closedHint")}</p>
      ) : (
        <button className="film-next-step" onClick={() => setActiveStage(nextStep.stage)} type="button">
          <span>{t("film.drama.nextStep")}</span>{nextStep.hint}<i aria-hidden="true">→</i>
        </button>
      )}

      {activeStage === "plan" && (
        <section className="film-overview-panel film-format-panel" key="plan">
          <header className="film-panel-heading">
            <div>
              <span>DRAMA LINE · STAGE 01</span>
              <h2>{t("film.drama.stage.plan")}</h2>
              <p>{t("film.drama.planDesc")}</p>
            </div>
            <div className="film-board-stats">
              <span>{t(studio.seriesPlan !== null ? "film.drama.planned" : "film.drama.notPlanned")}</span>
              <span>{t("film.drama.outlineCount", { n: studio.outlines.length })}</span>
            </div>
          </header>
          <div className="film-format-form">
            <label>{t("film.drama.title")}
              <input onChange={(event) => setPlanTitle(event.target.value)} placeholder={t("film.drama.titlePlaceholder")} value={planTitle} />
            </label>
            <label>{t("film.drama.totalEpisodes")}
              <input max={200} min={1} onChange={(event) => setTotalEpisodes(Number(event.target.value) || 1)} type="number" value={totalEpisodes} />
            </label>
            <label>{t("film.drama.genre")}
              <input onChange={(event) => setGenre(event.target.value)} placeholder={t("film.drama.genrePlaceholder")} value={genre} />
            </label>
            <button
              className="is-primary"
              disabled={busyStage !== null || planTitle.trim().length === 0}
              onClick={() =>
                void runStage("plan", () =>
                  commandClient.planDrama({
                    kind: "plan_drama",
                    projectId,
                    title: planTitle.trim(),
                    totalEpisodes,
                    genre,
                    logline: "",
                    episodeDurationS: 120,
                    characterRoster: [],
                    language: locale,
                  }),
                ).then(() => onNotice(t("film.drama.planCreated")))
              }
              type="button"
            >
              {t(busyStage === "plan" ? "film.drama.planning" : "film.drama.generatePlan")}
            </button>
          </div>
          {studio.seriesPlan !== null && (
            <div className="film-format-summary">
              <article><span>{t("film.drama.threeAct")}</span><p>{studio.seriesPlan.threeActs.join(" / ")}</p></article>
              <article><span>{t("film.drama.waveform")}</span><p>{studio.seriesPlan.waveformStages.map((stage) => stage.stage).join(" → ")}</p></article>
              <article><span>{t("film.drama.paywallBeats")}</span>
                <p>{studio.seriesPlan.paywallBeats.map((beat) => `第${beat.episodeNumber}集 · ${beat.strategy} @ ${beat.position}`).join("；")}</p>
              </article>
            </div>
          )}
        </section>
      )}

      {activeStage === "outline" && (
        <section className="film-overview-panel film-format-panel" key="outline">
          <header className="film-panel-heading">
            <div>
              <span>DRAMA LINE · STAGE 02</span>
              <h2>{t("film.drama.stage.outline")}</h2>
              <p>{t("film.drama.outlineDesc")}</p>
            </div>
            <div className="film-board-stats"><span>{t("film.drama.expandedEpisodes", { n: studio.outlines.length })}</span></div>
          </header>
          <div className="film-format-form">
            <label>{t("film.drama.startEpisode")}
              <input max={outlineMax} min={1} onChange={(event) => setOutlineStart(Number(event.target.value) || 1)} type="number" value={outlineStart} />
            </label>
            <label>{t("film.drama.endEpisode")}
              <input max={outlineMax} min={outlineStart} onChange={(event) => setOutlineEnd(Number(event.target.value) || outlineStart)} type="number" value={outlineEnd} />
            </label>
            <button
              className="is-primary"
              disabled={busyStage !== null || studio.seriesPlan === null || outlineStart > outlineEnd}
              onClick={() =>
                void runStage("outline", () =>
                  commandClient.expandDramaOutlines({ kind: "expand_drama_outlines", projectId, start: outlineStart, end: outlineEnd }),
                ).then(() => onNotice(t("film.drama.outlineExpanded")))
              }
              type="button"
            >
              {t(busyStage === "outline" ? "film.drama.expandingOutline" : "film.drama.expandOutline")}
            </button>
          </div>
          {studio.seriesPlan === null ? <p className="film-empty-hint">{t("film.drama.planFirst")}</p> : (
            <div className="film-format-list">
              {studio.outlines.map((outline) => (
                <article key={outline.episodeNumber}>
                  <header>
                    <b>{t("film.drama.episode", { n: outline.episodeNumber })} · {outline.title}</b>
                    {outline.paywallMarker.length > 0 ? <em className="film-paywall-tag">{outline.paywallMarker}</em> : null}
                  </header>
                  <p>{outline.summary}</p>
                </article>
              ))}
            </div>
          )}
        </section>
      )}

      {activeStage === "screenplay" && (
        <section className="film-overview-panel film-format-panel" key="screenplay">
          <header className="film-panel-heading">
            <div>
              <span>DRAMA LINE · STAGE 03</span>
              <h2>{t("film.drama.stage.screenplay")}</h2>
              <p>{t("film.drama.screenplayDesc")}</p>
            </div>
            <div className="film-board-stats"><span>{t("film.drama.screenplayCount", { n: studio.screenplays.length })}</span></div>
          </header>
          <div className="film-format-form">
            <label>{t("film.drama.episodeNumber")}
              <input max={Math.max(outlineMax, studio.outlines.length)} min={1} onChange={(event) => setScreenplayEpisode(Number(event.target.value) || 1)} type="number" value={screenplayEpisode} />
            </label>
            <button
              className="is-primary"
              disabled={busyStage !== null || !screenplayOutlineReady}
              onClick={() =>
                void runStage("screenplay", () =>
                  commandClient.writeDramaScreenplay({ kind: "write_drama_screenplay", projectId, episodeNumber: screenplayEpisode }),
                ).then(() => onNotice(t("film.drama.screenplayCompleted", { n: screenplayEpisode })))
              }
              type="button"
            >
              {t(busyStage === "screenplay" ? "film.drama.writingScreenplay" : "film.drama.writeScreenplay")}
            </button>
          </div>
          <div className="film-format-list">
            {studio.screenplays.map((screenplay) => {
              const shots = screenplay.scenes.reduce((total, scene) => total + scene.shotCount, 0);
              const dialogue = screenplay.scenes.reduce((total, scene) => total + scene.lines.length, 0);
              return (
                <article key={screenplay.episodeNumber}>
                  <header><b>{t("film.drama.episode", { n: screenplay.episodeNumber })}</b><small>{t("film.drama.screenplayStats", { scenes: screenplay.scenes.length, shots, dialogue })}</small></header>
                </article>
              );
            })}
          </div>
          {studio.screenplays.length > 0 && (
            <div className="film-handoff-card">
              <div>
                <b>{t("film.drama.handoffTitle")}</b>
                <small>{t("film.drama.handoffDesc")}</small>
              </div>
              <button onClick={onEnterFeatureLine} type="button">{t("film.drama.enterFeatureLine")}</button>
            </div>
          )}
        </section>
      )}

      {activeStage === "export" && (
        <section className="film-overview-panel film-format-panel" key="export">
          <header className="film-panel-heading">
            <div>
              <span>DRAMA LINE · STAGE 04</span>
              <h2>{t("film.drama.exportTitle")}</h2>
              <p>{t("film.drama.exportDesc")}</p>
            </div>
            <div className="film-board-stats"><span>{t("film.drama.paywallMarkers", { n: paywallMarkers.length })}</span></div>
          </header>
          <div className="film-format-form">
            <button
              className="is-primary"
              disabled={busyStage !== null}
              onClick={async () => {
                setBusyStage("export");
                setActionError(null);
                try {
                  const result = await commandClient.exportDramaPackage({ kind: "export_drama_package", projectId });
                  setExportResult(result);
                  onNotice(t(result.gatePassed ? "film.drama.gatePassedNotice" : "film.drama.gateFailedNotice"));
                } catch (error) {
                  setActionError(error instanceof Error ? error.message : String(error));
                } finally {
                  setBusyStage(null);
                }
              }}
              type="button"
            >
              {t(busyStage === "export" ? "film.drama.exporting" : "film.drama.exportPackage")}
            </button>
          </div>
          {exportResult !== null && (
            <div className={`film-gate-report ${exportResult.gatePassed ? "is-pass" : "is-fail"}`}>
              <b>{t(exportResult.gatePassed ? "film.drama.gatePassed" : "film.drama.gateFailed")}</b>
              <span>{t("film.drama.exportStats", { outlines: exportResult.outlinedEpisodes.length, screenplays: exportResult.screenplayEpisodes.length, markers: exportResult.paywallMarkers.length })}</span>
              {exportResult.auditIssues.length > 0 && (
                <ul>{exportResult.auditIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
              )}
            </div>
          )}
          {paywallMarkers.length > 0 && (
            <div className="film-format-list">
              {paywallMarkers.map((marker) => (
                <article key={marker.name}>
                  <header><i className="film-marker-dot" style={{ background: marker.color }} /><b>{marker.name}</b><small>@ {marker.timeS.toFixed(0)}s</small></header>
                </article>
              ))}
            </div>
          )}
        </section>
      )}

      {(loadError !== null || actionError !== null) && (
        <div className="film-gate-report is-fail" role="alert">{loadError ?? actionError}</div>
      )}
    </div>
  );
}
