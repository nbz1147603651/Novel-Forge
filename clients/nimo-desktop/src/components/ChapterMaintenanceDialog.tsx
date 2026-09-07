import { useState } from "react";

import type {
  ChapterCommandResult,
  EngineCommandClient,
  RepairControlMode,
} from "@nimo/engine-contracts";

import { AppDialog } from "./AppDialog";

type MaintenanceOperation =
  | "repair_continuity"
  | "repair_causal"
  | "repair_issues"
  | "reevaluate_chapter"
  | "reextract_relationships"
  | "repair_motif_history";

const OPERATIONS: readonly {
  readonly id: MaintenanceOperation;
  readonly label: string;
  readonly description: string;
  readonly supportsRepairMode?: boolean;
}[] = [
  { id: "repair_continuity", label: "连续性修复", description: "由 Engine 在当前章的未解决连续性问题中裁决修复范围。", supportsRepairMode: true },
  { id: "repair_causal", label: "因果链修复", description: "由 Engine 复核当前章的因果断裂与回收遗漏。", supportsRepairMode: true },
  { id: "repair_issues", label: "综合问题修复", description: "统一处理当前章的连续性和因果问题，受全局修复预算约束。", supportsRepairMode: true },
  { id: "reevaluate_chapter", label: "重评估", description: "只重新评估当前章，不修改正文。" },
  { id: "reextract_relationships", label: "关系重提取", description: "从已归档正文重新提取人物关系变化。" },
  { id: "repair_motif_history", label: "母题历史修复", description: "重建当前章相关的母题统计与回收历史。" },
];

interface ChapterMaintenanceDialogProps {
  readonly chapterNumber: number;
  readonly commandClient: Pick<
    EngineCommandClient,
    | "repairCausal"
    | "repairContinuity"
    | "repairIssues"
    | "reevaluateChapter"
    | "reextractRelationships"
    | "repairMotifHistory"
  >;
  readonly onClose: () => void;
  readonly onSubmitted: (result: ChapterCommandResult) => void;
  readonly projectId: string;
}

/**
 * A thin control surface for the existing Engine maintenance jobs.  It keeps
 * only user choices locally; task state and retry/cancellation stay in the
 * durable Engine job stream.
 */
export function ChapterMaintenanceDialog({
  chapterNumber,
  commandClient,
  onClose,
  onSubmitted,
  projectId,
}: ChapterMaintenanceDialogProps) {
  const [operation, setOperation] = useState<MaintenanceOperation>("repair_issues");
  const [repairControlMode, setRepairControlMode] = useState<RepairControlMode>("ai_assisted");
  const [reextractAll, setReextractAll] = useState(false);
  const [forceReextract, setForceReextract] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const selected = OPERATIONS.find((candidate) => candidate.id === operation)!;

  const submit = async () => {
    setSubmitting(true);
    try {
      let result: ChapterCommandResult;
      switch (operation) {
        case "repair_continuity":
          result = await commandClient.repairContinuity({
            kind: operation,
            projectId,
            chapterNumber,
            repairControlMode,
          });
          break;
        case "repair_causal":
          result = await commandClient.repairCausal({
            kind: operation,
            projectId,
            chapterNumber,
            repairControlMode,
          });
          break;
        case "repair_issues":
          result = await commandClient.repairIssues({
            kind: operation,
            projectId,
            chapterNumber,
            repairControlMode,
          });
          break;
        case "reevaluate_chapter":
          result = await commandClient.reevaluateChapter({ kind: operation, projectId, chapterNumber });
          break;
        case "reextract_relationships":
          result = await commandClient.reextractRelationships({
            kind: operation,
            projectId,
            chapterNumber: reextractAll ? 0 : chapterNumber,
          });
          break;
        case "repair_motif_history":
          result = await commandClient.repairMotifHistory({
            kind: operation,
            projectId,
            chapterNumber,
            forceReExtract: forceReextract,
            startChapter: 1,
            endChapter: chapterNumber,
          });
          break;
      }
      setMessage(result.message);
      onSubmitted(result);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "章节维护任务提交失败。")
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AppDialog
      closeOnConfirm={false}
      confirmDisabled={submitting}
      confirmLabel={submitting ? "提交中…" : "提交 Engine 任务"}
      description={`第 ${chapterNumber} 章 · ${selected.description}`}
      onClose={onClose}
      onConfirm={() => void submit()}
      size="wide"
      title="章节维护"
    >
      <div className="narrative-dialog-form">
        <label className="narrative-dialog-form-wide">
          维护操作
          <select aria-label="章节维护操作" onChange={(event) => setOperation(event.target.value as MaintenanceOperation)} value={operation}>
            {OPERATIONS.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.label}</option>)}
          </select>
        </label>
        {selected.supportsRepairMode === true && (
          <label className="narrative-dialog-form-wide">
            修复控制
            <select aria-label="修复控制模式" onChange={(event) => setRepairControlMode(event.target.value as RepairControlMode)} value={repairControlMode}>
              <option value="manual">仅生成修复建议</option>
              <option value="ai_assisted">AI 辅助修复</option>
              <option value="ai_auto">自动修复</option>
            </select>
          </label>
        )}
        {operation === "reextract_relationships" && (
          <label className="chapter-operation-checkbox narrative-dialog-form-wide">
            <input checked={reextractAll} onChange={(event) => setReextractAll(event.target.checked)} type="checkbox" />
            对全部已完成章节重提取关系
          </label>
        )}
        {operation === "repair_motif_history" && (
          <label className="chapter-operation-checkbox narrative-dialog-form-wide">
            <input checked={forceReextract} onChange={(event) => setForceReextract(event.target.checked)} type="checkbox" />
            对母题缓存为空的章节强制重新提取
          </label>
        )}
        <p className="narrative-dialog-note narrative-dialog-form-wide">
          {selected.supportsRepairMode === true
            ? "未指定问题时，Engine 会从当前章的持久化报告中选择未解决问题，并遵守修复预算与回滚规则。"
            : "提交后请在机杼任务流中观察、取消或恢复该持久化任务。"}
        </p>
        {message && <p aria-live="polite" className="narrative-dialog-note narrative-dialog-form-wide">{message}</p>}
      </div>
    </AppDialog>
  );
}
