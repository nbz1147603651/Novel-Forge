import { useState } from "react";

import { AppDialog } from "../../AppDialog";
import { OverlaySurface } from "../../OverlaySurface";
import {
  normalizeShortWorkflowPayload,
  shortLanguageOptions,
  type ShortWorkflowPayload,
  type ShortWorkflowPayloadPatch,
} from "../../../lib/short-workflow-session";
import { shortFieldSummary, type FieldDefinition } from "./fields";

/** 字段卡片区（核心/高级分区，点击打开字段编辑弹窗）。 */
export function FieldCards({ fields, onOpen, payload, title, description }: { readonly fields: readonly FieldDefinition[]; readonly onOpen: (id: string) => void; readonly payload: ShortWorkflowPayload; readonly title: string; readonly description?: string }) {
  return <section className="workflow-short-fields">
    <header><h3>{title}</h3><p>{description ?? "点击字段可在大窗口中查看和编辑；主页面只保留摘要，方便快速配置。"}</p></header>
    <div>{fields.map((field) => <button key={field.id} onClick={() => onOpen(field.id)} type="button">
      <span>{field.label}{field.required ? " *" : ""}</span>
      <strong>{shortFieldSummary(field, payload).trim() || "（未填写）"}</strong>
      <small>{field.hint}</small>
    </button>)}</div>
  </section>;
}

/** 字段编辑弹窗（多字段导航 + 输入/多行/标题语言三种编辑形态）。 */
export function ShortFieldEditorDialog({ fields, initialId, onApply, onClose, payload, title }: { readonly fields: readonly FieldDefinition[]; readonly initialId: string; readonly onApply: (payload: ShortWorkflowPayload) => void; readonly onClose: () => void; readonly payload: ShortWorkflowPayload; readonly title: string }) {
  const [selectedId, setSelectedId] = useState(initialId);
  const [draft, setDraft] = useState(payload);
  const [validation, setValidation] = useState("");
  const selected = fields.find((field) => field.id === selectedId) ?? fields[0];

  if (selected === undefined) return null;
  const apply = () => {
    if (fields.some((field) => field.required && field.key !== undefined && !draft[field.key].trim())) {
      const missing = fields.find((field) => field.required && field.key !== undefined && !draft[field.key].trim());
      setSelectedId(missing?.id ?? selectedId);
      setValidation(`请填写必填字段「${missing?.label ?? "故事主题"}」。`);
      return;
    }
    onApply(draft);
  };
  const update = (patch: ShortWorkflowPayloadPatch) =>
    setDraft((current) => normalizeShortWorkflowPayload({ ...current, ...patch }));

  return <OverlaySurface ariaLabel={title} className="workflow-short-overlay is-field-editor" onClose={onClose}>
    <header className="workflow-source-dialog-intro"><h2>{title}</h2><p>在这里集中查看和编辑长字段，应用后同步回主表单。</p></header>
    <div className="workflow-field-editor-body">
      <nav aria-label="短篇创作字段" className="workflow-field-nav">{fields.map((field) => <button className={field.id === selected.id ? "is-active" : ""} key={field.id} onClick={() => { setSelectedId(field.id); setValidation(""); }} type="button">{field.label}{field.required ? " *" : ""}</button>)}</nav>
      <section className="workflow-field-editor-pane"><header><h3>{selected.label}{selected.required ? " *" : ""}</h3><p>{selected.hint}</p></header>
        {selected.kind === "title-language" ? <div className="workflow-short-title-language-editor">
          <label><span>作品名</span><input aria-label="编辑作品名" onChange={(event) => update({ title: event.target.value })} placeholder="作品暂定名，留空由 AI 生成" value={draft.title} /></label>
          <label><span>语言</span><select aria-label="编辑语言" onChange={(event) => update({ language: event.target.value as ShortWorkflowPayload["language"] })} value={draft.language}>{shortLanguageOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
        </div> : selected.kind === "singleline" && selected.key !== undefined ? <input aria-label={`编辑${selected.label}`} onChange={(event) => update({ [selected.key!]: event.target.value })} placeholder={selected.hint} value={draft[selected.key]} /> : selected.key !== undefined ? <textarea aria-label={`编辑${selected.label}`} onChange={(event) => update({ [selected.key!]: event.target.value })} placeholder={selected.hint} value={draft[selected.key]} /> : null}
        {validation && <p aria-live="polite" className="workflow-field-validation">{validation}</p>}
      </section>
    </div>
    <footer className="workflow-preset-dialog-footer"><button className="button button-secondary" onClick={onClose} type="button">取消</button><button className="button button-primary" onClick={apply} type="button">应用</button></footer>
  </OverlaySurface>;
}

/** 折叠区（懒加载 body，用于叙事要素偏好面板）。 */
export function BlueprintCollapsibleSection({ children, label }: { readonly children: React.ReactNode; readonly label: string }) {
  const [expanded, setExpanded] = useState(false);
  return <section className={expanded ? "blueprint-section is-expanded" : "blueprint-section"}>
    <button aria-expanded={expanded} className="blueprint-section-toggle" onClick={() => setExpanded((v) => !v)} type="button">
      <span className="blueprint-section-arrow">{expanded ? "⌄" : "›"}</span>
      {label}
    </button>
    {expanded && <div className="blueprint-section-body">{children}</div>}
  </section>;
}
