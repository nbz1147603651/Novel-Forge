import { useState } from "react";

import type { DeleteProjectsResult, EngineCommandClient, PageId, ProjectView } from "@nimo/engine-contracts";

import { AppDialog } from "../AppDialog";
import { RebuildVectorsDialog } from "../RebuildVectorsDialog";

interface ProjectDetailCardProps {
  readonly project: ProjectView;
  readonly commandClient?: EngineCommandClient | undefined;
  readonly canMoveEarlier: boolean;
  readonly canMoveLater: boolean;
  readonly onNavigate: (page: PageId) => void;
  readonly onReadProject: (projectId: string) => void;
  readonly onWorkspaceRefresh?: (() => Promise<unknown> | void) | undefined;
  readonly onDelete: (projectId: string) => Promise<DeleteProjectsResult>;
  readonly onMoveEarlier: () => void;
  readonly onMoveLater: () => void;
}

/**
 * 选中项目的卷页细览（mirrors dashboard_page.py detail_panel Surface("hero")）。
 * 目录和向量维护由专用命令处理；删除必须跨过 Engine 确认边界。
 */
export function ProjectDetailCard({
  canMoveEarlier,
  canMoveLater,
  commandClient,
  onDelete,
  onMoveEarlier,
  onMoveLater,
  onNavigate,
  onReadProject,
  onWorkspaceRefresh,
  project,
}: ProjectDetailCardProps) {
  const modeLabel = project.mode === "long" ? "长篇" : "短篇";
  const [dialog, setDialog] = useState<"directory" | "vectors" | "delete" | null>(
    null,
  );
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteMessage, setDeleteMessage] = useState("");
  const deleteProject = async () => {
    if (deleteBusy) return;
    setDeleteBusy(true);
    setDeleteMessage("");
    try {
      const result = await onDelete(project.id);
      setDeleteMessage(
        result.failures.map((failure) => failure.message).join("；") || result.message,
      );
      if (result.deletedProjectIds.includes(project.id)) setDialog(null);
    } catch (error) {
      setDeleteMessage(error instanceof Error ? error.message : "项目删除失败。");
    } finally {
      setDeleteBusy(false);
    }
  };
  return (
    <section className="project-detail">
      <div className="detail-title">
        <h2>{project.title}</h2>
        <span
          className={
            project.status === "completed"
              ? "status-badge is-success"
              : "status-badge is-warning"
          }
        >
          {project.statusLabel}
        </span>
      </div>
      <div className="button-row">
        <button
          className="button button-primary"
          onClick={() =>
            onNavigate(project.mode === "long" ? "chapter_studio" : "workflow")
          }
          type="button"
        >
          {project.mode === "long" ? "续此卷" : "去机杼"}
        </button>
        <button
          className="button button-secondary"
          onClick={() => onReadProject(project.id)}
          type="button"
        >
          阅卷
        </button>
        <button className="button button-secondary" disabled={!canMoveEarlier} onClick={onMoveEarlier} type="button">前移</button>
        <button className="button button-secondary" disabled={!canMoveLater} onClick={onMoveLater} type="button">后移</button>
        <button
          className="button button-secondary"
          onClick={() => setDialog("directory")}
          type="button"
        >
          打开目录
        </button>
        <button
          className="button button-secondary"
          onClick={() => setDialog("vectors")}
          type="button"
        >
          重建向量
        </button>
        <button
          className="button button-danger"
          onClick={() => {
            setDeleteMessage("");
            setDialog("delete");
          }}
          type="button"
        >
          删除项目
        </button>
      </div>
      <p className="detail-meta">
        {modeLabel} · {project.genre} · {project.tone} · 近次修订{" "}
        {project.updatedLabel}
      </p>
      <p className="detail-body">{project.headline}</p>
      <p className="detail-meta">
        {project.progressLabel} · 已完成 {project.completedChapters}{" "}
        {project.totalChapters === null
          ? "章"
          : `/ ${project.totalChapters} 章`}
      </p>
      {dialog === "directory" && (
        <AppDialog
          description="第一阶段不操作文件系统；该路径仅用于确认 UI 路由是否指向正确项目。"
          onClose={() => setDialog(null)}
          title="项目目录"
        >
          <pre className="dialog-json-preview">{`data/${project.id}/`}</pre>
        </AppDialog>
      )}
      {dialog === "vectors" && (
        <RebuildVectorsDialog
          commandClient={commandClient}
          onClose={() => setDialog(null)}
          onSubmitted={onWorkspaceRefresh}
          projectId={project.id}
        />
      )}
      {dialog === "delete" && (
        <AppDialog
          closeOnConfirm={false}
          confirmDisabled={deleteBusy}
          confirmLabel={deleteBusy ? "删除中…" : "永久删除项目"}
          description={`这会永久删除“${project.title}”的整个项目目录，包括正文、大纲、报告、配音与运行日志，且无法撤销。`}
          onClose={() => { if (!deleteBusy) setDialog(null); }}
          onConfirm={() => void deleteProject()}
          title="永久删除项目？"
          tone="danger"
        >
          <p className="narrative-dialog-note">正在运行或排队的项目不会被删除。</p>
          {deleteMessage.length > 0 && <p aria-live="polite" className="narrative-dialog-note">{deleteMessage}</p>}
        </AppDialog>
      )}
    </section>
  );
}
