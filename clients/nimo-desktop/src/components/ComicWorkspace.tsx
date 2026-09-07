/**
 * ComicWorkspace — 映界 · 漫画形态工作台。
 *
 * 漫画是映界的分镜形态：剧本来自映界成稿回流投影（FilmStudioState 的
 * screenplay），身份锁随角色进入每格 prompt；ComicPanel 镜像 FilmShot，
 * 页/条镜像 Track，与剧集主镜线共享同一套可行域分配算法。分格结果可作为
 * 短剧的动态分镜回流到主镜线。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  ComicAuditResult,
  ComicExportResult,
  ComicStudioView,
  EngineClient,
  EngineCommandClient,
} from "@nimo/engine-contracts";
import { useT } from "../lib/i18n";

export interface ComicWorkspaceProps {
  readonly commandClient: EngineCommandClient;
  readonly engineClient: EngineClient;
  readonly projectId: string;
  readonly onEnterFeatureLine: () => void;
  readonly onNotice: (message: string) => void;
}

type ComicStageId = "plan" | "audit" | "export";

const STAGE_ORDER: readonly ComicStageId[] = ["plan", "audit", "export"];

const stageTitleKey = (stage: ComicStageId): string => `film.comic.stage.${stage}`;

export function ComicWorkspace({
  commandClient,
  engineClient,
  projectId,
  onEnterFeatureLine,
  onNotice,
}: ComicWorkspaceProps) {
  const t = useT();
  const [studio, setStudio] = useState<ComicStudioView | null>(null);
  const [audit, setAudit] = useState<ComicAuditResult | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyStage, setBusyStage] = useState<ComicStageId | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [exportResult, setExportResult] = useState<ComicExportResult | null>(null);
  const [format, setFormat] = useState<ComicStudioView["format"]>("page");
  const [activeStage, setActiveStage] = useState<ComicStageId>("plan");

  const loadStudio = useCallback(
    async (nextProjectId: string) => {
      if (nextProjectId.length === 0) return;
      setLoadError(null);
      try {
        const view = await engineClient.getComicStudio(nextProjectId);
        setStudio(view);
        if (view.format === "page" || view.format === "webtoon") setFormat(view.format);
        const nextAudit = view.pages.length > 0 ? await engineClient.getComicAudit(nextProjectId) : null;
        setAudit(nextAudit);
        // 入场即闭环：自动定位到第一个未完成阶段。
        setActiveStage(view.pages.length === 0 ? "plan" : nextAudit === null ? "audit" : "export");
      } catch (error) {
        setLoadError(error instanceof Error ? error.message : String(error));
      }
    },
    [engineClient],
  );

  useEffect(() => {
    setStudio(null);
    setAudit(null);
    setExportResult(null);
    void loadStudio(projectId);
  }, [projectId, loadStudio]);

  const runStage = useCallback(async (stage: ComicStageId, action: () => Promise<void>) => {
    setBusyStage(stage);
    setActionError(null);
    try {
      await action();
      const orderIndex = STAGE_ORDER.indexOf(stage);
      setActiveStage(STAGE_ORDER[Math.min(orderIndex + 1, STAGE_ORDER.length - 1)] ?? stage);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyStage(null);
    }
  }, []);

  const stageStatus = useMemo(() => {
    const planned = (studio?.pages.length ?? 0) > 0;
    const audited = audit !== null;
    return {
      plan: planned ? "done" : "ready",
      audit: audited ? "done" : planned ? "ready" : "locked",
      export: exportResult !== null ? "done" : audited ? "ready" : "locked",
    } as Record<ComicStageId, "done" | "ready" | "locked">;
  }, [studio, audit, exportResult]);

  const totalPanels = useMemo(
    () => (studio?.pages ?? []).reduce((sum, page) => sum + page.panels.length, 0),
    [studio],
  );

  // 下一步提示：规划 → 审计 → 导出；全部完成后提示闭环与回流入口。
  const nextStep = useMemo(() => {
    if (studio === null) return null;
    if (studio.pages.length === 0) return { stage: "plan" as ComicStageId, hint: t("film.comic.next.plan") };
    if (audit === null) return { stage: "audit" as ComicStageId, hint: t("film.comic.next.audit") };
    if (!audit.gatePassed) return { stage: "audit" as ComicStageId, hint: t("film.comic.next.auditFailed") };
    if (exportResult === null) return { stage: "export" as ComicStageId, hint: t("film.comic.next.export") };
    return null;
  }, [studio, audit, exportResult, t]);

  if (studio === null) {
    return (
      <section className="film-overview-panel film-format-panel">
        <header className="film-panel-heading">
          <div><span>COMIC LINE</span><h2>{t("film.comic.loading")}</h2></div>
        </header>
        {loadError !== null ? <p className="film-empty-hint">{loadError}</p> : null}
      </section>
    );
  }

  return (
    <div className="film-format-workspace">
      <nav aria-label={t("film.comic.stageAria")} className="film-stage-rail is-inline">
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
        <p className="film-next-step is-done"><span>{t("film.comic.closed")}</span>{t("film.comic.closedHint")}</p>
      ) : (
        <button className="film-next-step" onClick={() => setActiveStage(nextStep.stage)} type="button">
          <span>{t("film.comic.nextStep")}</span>{nextStep.hint}<i aria-hidden="true">→</i>
        </button>
      )}

      {activeStage === "plan" && (
        <section className="film-overview-panel film-format-panel" key="plan">
          <header className="film-panel-heading">
            <div>
              <span>COMIC LINE · STAGE 01</span>
              <h2>{t("film.comic.stage.plan")}</h2>
              <p>{t("film.comic.planDesc")}</p>
            </div>
            <div className="film-board-stats">
              <span>{studio.sourceRevision || t("film.comic.waitingProjection")}</span>
              <span>{t(studio.format === "webtoon" ? "film.comic.webtoonCount" : "film.comic.pageCount", { n: studio.pages.length })}</span>
              <span>{t("film.comic.panelCount", { n: totalPanels })}</span>
            </div>
          </header>
          <div className="film-format-form">
            <label>{t("film.comic.format")}
              <select onChange={(event) => setFormat(event.target.value as ComicStudioView["format"])} value={format}>
                <option value="page">{t("film.comic.formatPage")}</option>
                <option value="webtoon">{t("film.comic.formatWebtoon")}</option>
              </select>
            </label>
            <button
              className="is-primary"
              disabled={busyStage !== null}
              onClick={() =>
                void runStage("plan", async () => {
                  const next = await commandClient.planComicPages({ kind: "plan_comic_pages", projectId, format });
                  setStudio(next);
                  setExportResult(null);
                  setAudit(await engineClient.getComicAudit(projectId));
                  onNotice(t("film.comic.planCompleted"));
                })
              }
              type="button"
            >
              {t(busyStage === "plan" ? "film.comic.planning" : "film.comic.planPages")}
            </button>
          </div>
          <div className="film-lineage-card is-inline">
            <span>STORYBOARD LINEAGE</span>
            <strong>ComicPanel ⇄ FilmShot · 页/条 ⇄ Track</strong>
            <small>{t("film.comic.lineageDesc")}</small>
          </div>
        </section>
      )}

      {activeStage === "audit" && (
        <section className="film-overview-panel film-format-panel" key="audit">
          <header className="film-panel-heading">
            <div>
              <span>COMIC LINE · STAGE 02</span>
              <h2>{t("film.comic.auditTitle")}</h2>
              <p>{t("film.comic.auditDesc")}</p>
            </div>
            <div className="film-board-stats">
              <span>{t(audit === null ? "film.comic.notAudited" : audit.gatePassed ? "film.comic.auditPassed" : "film.comic.auditFailed")}</span>
            </div>
          </header>
          {studio.pages.length === 0 ? (
            <p className="film-empty-hint">{t("film.comic.planFirst")}</p>
          ) : (
            <div className="film-comic-pages">
              {studio.pages.map((page) => (
                <article className="film-comic-page" key={page.pageNumber}>
                  <header>
                    <b>{t(studio.format === "page" ? "film.comic.pageNumber" : "film.comic.webtoonNumber", { n: page.pageNumber })}</b>
                    <small>{t("film.comic.panelCount", { n: page.panels.length })}</small>
                    {page.rhythmNote.length > 0 ? <em>{page.rhythmNote}</em> : null}
                  </header>
                  <div className={`film-comic-grid is-${studio.format}`}>
                    {page.panels.map((panel) => (
                      <div className="film-comic-panel" key={panel.panelId}>
                        <span>{t("film.comic.panelNumber", { n: panel.panelNumber })}</span>
                        <p>{panel.beat}</p>
                        {panel.bubbles.length > 0 && (
                          <footer>
                            {panel.bubbles.map((bubble, bubbleIndex) => (
                              <em key={bubbleIndex}>
                                {bubble.speaker.length > 0 ? `${bubble.speaker}：` : ""}{bubble.text}
                              </em>
                            ))}
                          </footer>
                        )}
                      </div>
                    ))}
                  </div>
                </article>
              ))}
            </div>
          )}
          {audit !== null && (
            <div className={`film-gate-report ${audit.gatePassed ? "is-pass" : "is-fail"}`}>
              <b>{t(audit.gatePassed ? "film.comic.layoutPassed" : "film.comic.layoutFailed")}</b>
              <span>{audit.gatePassed ? t("film.comic.layoutPassedDesc") : audit.issues.join(" / ")}</span>
            </div>
          )}
        </section>
      )}

      {activeStage === "export" && (
        <section className="film-overview-panel film-format-panel" key="export">
          <header className="film-panel-heading">
            <div>
              <span>COMIC LINE · STAGE 03</span>
              <h2>{t("film.comic.exportTitle")}</h2>
              <p>{t("film.comic.exportDesc")}</p>
            </div>
          </header>
          <div className="film-format-form">
            <button
              className="is-primary"
              disabled={busyStage !== null || audit === null || !audit.gatePassed}
              onClick={() =>
                void runStage("export", async () => {
                  const result = await commandClient.exportComicPackage({ kind: "export_comic_package", projectId });
                  setExportResult(result);
                  onNotice(t("film.comic.exportCompleted", { n: result.panelCount }));
                })
              }
              type="button"
            >
              {t(busyStage === "export" ? "film.comic.exporting" : "film.comic.exportPackage")}
            </button>
          </div>
          {exportResult !== null && (
            <div className="film-gate-report is-pass">
              <b>{t("film.comic.exportedPanels", { n: exportResult.panelCount })}</b>
              <span>{exportResult.pagesPath}</span>
              <span>{t("film.comic.manifest")}: {exportResult.manifestPath}</span>
            </div>
          )}
          {studio.pages.length > 0 && (
            <div className="film-handoff-card">
              <div>
                <b>{t("film.comic.handoffTitle")}</b>
                <small>{t("film.comic.handoffDesc")}</small>
              </div>
              <button onClick={onEnterFeatureLine} type="button">{t("film.comic.enterFeatureLine")}</button>
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
