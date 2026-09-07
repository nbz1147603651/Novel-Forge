import { useState } from "react";

import { AppDialog } from "../../AppDialog";
import { longLanguageOptions } from "../../../lib/long-init-session";
import {
  getFieldValue,
  type FieldDefinition,
  type FieldGroup,
  type LongInitPayload,
  type LongInitPayloadPatch,
} from "./fields";

/** 字段卡片区（核心/高级设置分区，点击打开字段编辑弹窗）。 */
export function FieldCardSection({ title, description, fields, payload, onOpen }: {
  readonly title: string;
  readonly description: string;
  readonly fields: readonly FieldDefinition[];
  readonly group: FieldGroup;
  readonly payload: LongInitPayload;
  readonly onOpen: (fieldId: string) => void;
}) {
  return (
    <div className="long-init-field-section">
      <div className="long-init-field-section-header"><h3>{title}</h3><p>{description}</p></div>
      <div className="long-init-field-cards">
        {fields.map((field) => {
          const value = getFieldSummary(field, payload);
          return (
            <button className={`long-init-field-card ${value ? "has-value" : ""}`} key={field.id} onClick={() => onOpen(field.id)} type="button">
              <span className="long-init-field-card-label">{field.label}{field.required && <span className="long-init-required-mark">*</span>}</span>
              <span className="long-init-field-card-value">{value || field.hint}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function getFieldSummary(field: FieldDefinition, payload: LongInitPayload): string {
  if (field.kind === "title-language") {
    return [payload.title, payload.language].filter(Boolean).join(" · ");
  }
  if (field.key) {
    const value = payload[field.key];
    if (typeof value === "string") return value.length > 60 ? value.slice(0, 59) + "…" : value;
  }
  return "";
}

/** 字段编辑弹窗（多字段导航 + 输入/多行/标题语言三种编辑形态）。 */
export function FieldEditDialog({ fields, selectedId, title, payload, onClose, onApply }: {
  readonly fields: readonly FieldDefinition[];
  readonly selectedId: string;
  readonly title: string;
  readonly payload: LongInitPayload;
  readonly onClose: () => void;
  readonly onApply: (patch: LongInitPayloadPatch) => void;
}) {
  const [activeId, setActiveId] = useState(selectedId);
  const [drafts, setDrafts] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const field of fields) { initial[field.id] = getFieldValue(field, payload); }
    return initial;
  });
  const [draftTitle, setDraftTitle] = useState(payload.title);
  const [draftLanguage, setDraftLanguage] = useState<LongInitPayload["language"]>(payload.language);
  const activeField = fields.find((f) => f.id === activeId) ?? fields[0]!;

  const handleApply = () => {
    const p: LongInitPayloadPatch = {};
    for (const field of fields) {
      if (field.key && field.kind !== "title-language") {
        Object.assign(p, { [field.key]: drafts[field.id] ?? "" });
      }
    }
    if (fields.some((f) => f.kind === "title-language")) {
      Object.assign(p, { title: draftTitle, language: draftLanguage });
    }
    onApply(p);
  };

  return (
    <AppDialog
      className="long-init-field-editor-dialog"
      confirmLabel="应用"
      description="编辑字段内容"
      onClose={onClose}
      onConfirm={handleApply}
      size="wide"
      title={title}
    >
      <div className="long-init-field-dialog">
        <nav className="long-init-field-dialog-nav">
          {fields.map((field) => (
            <button className={`long-init-field-nav-item ${field.id === activeId ? "is-active" : ""}`} key={field.id} onClick={() => setActiveId(field.id)} type="button">
              {field.label}{field.required && <span className="long-init-required-mark">*</span>}
            </button>
          ))}
        </nav>
        <div className="long-init-field-dialog-body">
          <p className="long-init-field-dialog-hint">{activeField.hint}</p>
          {activeField.kind === "title-language" ? (
            <div className="long-init-title-language-editor">
              <label>
                <span>作品名</span>
                <input
                  autoFocus
                  className="long-init-input is-dialog"
                  onChange={(event) => setDraftTitle(event.target.value)}
                  placeholder="作品暂定名，留空由 AI 生成"
                  type="text"
                  value={draftTitle}
                />
              </label>
              <label>
                <span>语言</span>
                <select
                  aria-label="编辑长篇语言"
                  className="long-init-input is-dialog"
                  onChange={(event) => setDraftLanguage(event.target.value as LongInitPayload["language"])}
                  value={draftLanguage}
                >
                  {longLanguageOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              </label>
            </div>
          ) : activeField.kind === "multiline" ? (
            <textarea className="long-init-textarea is-dialog" onChange={(e) => setDrafts((d) => ({ ...d, [activeField.id]: e.target.value }))} value={drafts[activeField.id] ?? ""} />
          ) : (
            <input className="long-init-input is-dialog" onChange={(e) => setDrafts((d) => ({ ...d, [activeField.id]: e.target.value }))} type="text" value={drafts[activeField.id] ?? ""} />
          )}
        </div>
      </div>
    </AppDialog>
  );
}
