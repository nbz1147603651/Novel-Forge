import { useEffect, useState } from "react";

import type {
  ChapterCommandResult,
  EngineClient,
  EngineCommandClient,
  InitManualRepairView,
} from "@nimo/engine-contracts";

import { AppDialog } from "./AppDialog";

interface InitManualRepairDialogProps {
  readonly commandClient: Pick<EngineCommandClient, "saveInitManualRepair">;
  readonly engineClient: Pick<EngineClient, "getInitManualRepair">;
  readonly onClose: () => void;
  readonly onRetry: () => Promise<ChapterCommandResult>;
  readonly projectId: string;
}

/**
 * Engine-backed counterpart of PySide's InitManualRepairDialog.  The browser
 * owns only the unsaved text buffer; artifact selection, validation, revision
 * conflicts, persistence, and follow-up retry remain on the Engine boundary.
 */
export function InitManualRepairDialog({
  commandClient,
  engineClient,
  onClose,
  onRetry,
  projectId,
}: InitManualRepairDialogProps) {
  const [repair, setRepair] = useState<InitManualRepairView | null>(null);
  const [draft, setDraft] = useState("");
  const [message, setMessage] = useState("正在读取初始化修复产物…");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let disposed = false;
    setRepair(null);
    setDraft("");
    setMessage("正在读取初始化修复产物…");
    void engineClient.getInitManualRepair(projectId).then((next) => {
      if (disposed) return;
      setRepair(next);
      setDraft(next.payload === null ? "" : JSON.stringify(next.payload, null, 2));
      setMessage(next.available ? "请依据问题定位修改 JSON；保存后将提交初始化复审。" : next.summary);
    }).catch((error: unknown) => {
      if (!disposed) setMessage(error instanceof Error ? error.message : "无法读取人工修复产物。");
    });
    return () => { disposed = true; };
  }, [engineClient, projectId]);

  const saveAndRetry = async () => {
    if (repair === null || !repair.available || repair.artifact === "") return;
    let payload: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(draft);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("待修复产物顶层必须是 JSON 对象。");
      }
      payload = parsed as Record<string, unknown>;
    } catch (error) {
      setMessage(error instanceof Error ? `JSON 格式错误：${error.message}` : "JSON 格式错误。");
      return;
    }
    setSaving(true);
    try {
      const saved = await commandClient.saveInitManualRepair({
        kind: "save_init_manual_repair",
        projectId,
        artifact: repair.artifact,
        payload,
        expectedRevision: repair.revision,
      });
      if (saved.repair !== undefined) {
        setRepair(saved.repair);
        setDraft(saved.repair.payload === null ? "" : JSON.stringify(saved.repair.payload, null, 2));
      }
      if (saved.status !== "saved") {
        setMessage(saved.message);
        return;
      }
      const retry = await onRetry();
      setMessage(`${saved.message} ${retry.message}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存并复审失败，未确认的编辑仍保留在本地缓冲区。 ");
    } finally {
      setSaving(false);
    }
  };

  const title = repair?.available ? `人工修复 · ${repair.artifactLabel}` : "人工修复";
  const description = repair?.available
    ? `修改 ${repair.artifactPath} 后，Engine 会先校验结构，再重新提交初始化复审。`
    : "当前初始化失败没有可安全编辑的产物。请查看任务日志或刷新工作区。";

  return <AppDialog closeOnConfirm={false} confirmDisabled={saving || repair?.available !== true} confirmLabel={saving ? "保存中…" : "保存并复审"} description={description} onClose={onClose} onConfirm={() => void saveAndRetry()} size="wide" title={title}>
    <div className="narrative-dialog-form">
      <label className="narrative-dialog-form-wide">准入诊断<textarea aria-label="初始化修复摘要" readOnly value={repair?.summary ?? message} /></label>
      {repair?.issues.map((issue) => <article className="narrative-dialog-note narrative-dialog-form-wide" key={issue.title}><strong>{issue.title}</strong><pre>{issue.summary}</pre>{issue.locations.map((location) => <small key={`${location.label}:${location.pointer}`}>{location.confidence === "exact" ? "精准定位" : "建议范围"}：{location.pointer || location.label}</small>)}</article>)}
      {repair?.available === true && <label className="narrative-dialog-form-wide">待修复产物：{repair.artifactPath}<textarea aria-label="初始化修复 JSON" onChange={(event) => setDraft(event.target.value)} spellCheck={false} value={draft} /></label>}
      <p aria-live="polite" className="narrative-dialog-note narrative-dialog-form-wide">{message}</p>
    </div>
  </AppDialog>;
}
