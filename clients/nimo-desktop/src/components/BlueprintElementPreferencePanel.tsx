import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  EXTENSION_CARDS,
  GENRE_PRESETS,
  getRelatedExtensionIdsForGenre,
  recommendPresetForGenre,
  type BlueprintElementCard,
} from "../lib/blueprint-element-data";
import type { BlueprintElementPreferences } from "../lib/short-workflow-session";

/** Maximum extension items for short mode (PySide6 uses 6 for short, 8 for long). */
const SHORT_MAX_EXTENSION_COUNT = 6;

interface ElementRowState {
  readonly card: BlueprintElementCard;
  enabled: boolean;
  locked: boolean;
  weight: number;
}

export interface BlueprintPreferencePanelProps {
  /** Current genre text for filtering and preset recommendation. */
  readonly genreText: string;
  /** Current preference payload. */
  readonly preferences: BlueprintElementPreferences;
  /** Called when any row, preset, or toggle changes. */
  readonly onChange: (preferences: BlueprintElementPreferences) => void;
}

/**
 * 1:1 React mirror of PySide6 `BlueprintElementPreferencePanel`.
 *
 * Provides manual controls for blueprint element selection:
 * - Genre preset combo with apply/suggested/reset
 * - Related-genre filter toggle
 * - Manual override toggle
 * - Per-element enabled/locked/weight rows
 */
export function BlueprintElementPreferencePanel({
  genreText,
  preferences,
  onChange,
}: BlueprintPreferencePanelProps) {
  const [rows, setRows] = useState<Map<string, ElementRowState>>(() => initRows(preferences));
  const [selectedPresetId, setSelectedPresetId] = useState(preferences.presetId);
  const [presetApplied, setPresetApplied] = useState(!!preferences.presetId);
  const [manualOverride, setManualOverride] = useState(preferences.manualOverride);
  const [relatedOnly, setRelatedOnly] = useState(false);
  const prevGenreRef = useRef(genreText);

  // Initialize rows from preferences
  function initRows(prefs: BlueprintElementPreferences): Map<string, ElementRowState> {
    const map = new Map<string, ElementRowState>();
    const prefMap = new Map(prefs.items.map((i) => [i.elementId, i]));
    for (const card of EXTENSION_CARDS) {
      const pref = prefMap.get(card.id);
      map.set(card.id, {
        card,
        enabled: pref?.enabled === true,
        locked: pref?.locked ?? false,
        weight: pref?.weight ?? 50,
      });
    }
    return map;
  }

  // Sync from external preferences changes
  useEffect(() => {
    setRows(initRows(preferences));
    setSelectedPresetId(preferences.presetId);
    setPresetApplied(!!preferences.presetId);
    setManualOverride(preferences.manualOverride);
  }, [preferences]);

  // Genre context → suggestion
  const suggestedPresetId = useMemo(() => recommendPresetForGenre(genreText), [genreText]);
  const suggestedPreset = GENRE_PRESETS.find((p) => p.id === suggestedPresetId);

  // Auto-select suggested preset in combo when genre changes (if not manually applied)
  useEffect(() => {
    if (prevGenreRef.current === genreText) return;
    prevGenreRef.current = genreText;
    if (!presetApplied && suggestedPresetId) {
      setSelectedPresetId(suggestedPresetId);
    } else if (!presetApplied && !suggestedPresetId) {
      setSelectedPresetId("");
    }
  }, [genreText, presetApplied, suggestedPresetId]);

  // Emit changes upward
  const emitChange = useCallback(
    (nextRows: Map<string, ElementRowState>, nextPresetId: string, nextManual: boolean) => {
      const items = Array.from(nextRows.values())
        .filter((row) => row.enabled || row.locked || Math.abs(row.weight - 50) >= 0.1)
        .map((row) => ({
          elementId: row.card.id,
          enabled: row.enabled ? true : row.locked ? false : null,
          locked: row.locked,
          weight: row.weight,
        }));
      onChange({ presetId: nextPresetId, manualOverride: nextManual, items });
    },
    [onChange],
  );

  const applyPreset = useCallback(
    (presetId: string) => {
      setRows((prev) => {
        const next = new Map(prev);
        // Reset all rows
        for (const [id, row] of next) {
          next.set(id, { ...row, enabled: false, locked: false, weight: 50 });
        }
        if (!presetId) {
          setPresetApplied(false);
          emitChange(next, "", manualOverride);
          return next;
        }
        const preset = GENRE_PRESETS.find((p) => p.id === presetId);
        if (!preset) {
          setPresetApplied(false);
          emitChange(next, "", manualOverride);
          return next;
        }
        // Apply default weights
        for (const [elementId, weight] of Object.entries(preset.defaultWeights)) {
          const row = next.get(elementId);
          if (row) next.set(elementId, { ...row, weight });
        }
        // Apply default enabled
        for (const elementId of preset.defaultEnabled) {
          const row = next.get(elementId);
          if (row) next.set(elementId, { ...row, enabled: true });
        }
        setPresetApplied(true);
        emitChange(next, presetId, manualOverride);
        return next;
      });
    },
    [manualOverride, emitChange],
  );

  const handleApplySuggested = useCallback(() => {
    if (!suggestedPresetId) return;
    setSelectedPresetId(suggestedPresetId);
    applyPreset(suggestedPresetId);
  }, [suggestedPresetId, applyPreset]);

  const handleReset = useCallback(() => {
    setRows((prev) => {
      const next = new Map(prev);
      for (const [id, row] of next) {
        next.set(id, { ...row, enabled: false, locked: false, weight: 50 });
      }
      setSelectedPresetId("");
      setPresetApplied(false);
      setManualOverride(false);
      setRelatedOnly(false);
      emitChange(next, "", false);
      return next;
    });
  }, [emitChange]);

  const updateRow = useCallback(
    (elementId: string, patch: Partial<Pick<ElementRowState, "enabled" | "locked" | "weight">>) => {
      setRows((prev) => {
        const row = prev.get(elementId);
        if (!row) return prev;
        const next = new Map(prev);
        next.set(elementId, { ...row, ...patch });
        emitChange(next, selectedPresetId, manualOverride);
        return next;
      });
    },
    [selectedPresetId, manualOverride, emitChange],
  );

  const toggleManualOverride = useCallback(
    (checked: boolean) => {
      setManualOverride(checked);
      emitChange(rows, selectedPresetId, checked);
    },
    [rows, selectedPresetId, emitChange],
  );

  // Related-genre filter
  const relatedIds = useMemo(() => {
    if (!relatedOnly) return null;
    const currentPreset = presetApplied ? selectedPresetId : suggestedPresetId;
    const ids = getRelatedExtensionIdsForGenre(genreText, currentPreset);
    return ids.length > 0 ? new Set(ids) : null;
  }, [relatedOnly, presetApplied, selectedPresetId, suggestedPresetId, genreText]);

  const visibleRows = useMemo(() => {
    const allRows = Array.from(rows.values());
    if (!relatedIds) return allRows;
    return allRows.filter((row) => relatedIds.has(row.card.id) || row.enabled || row.locked);
  }, [rows, relatedIds]);

  // Group rows by category
  const groupedRows = useMemo(() => {
    const groups = new Map<string, ElementRowState[]>();
    for (const row of visibleRows) {
      const cat = row.card.category;
      let group = groups.get(cat);
      if (!group) {
        group = [];
        groups.set(cat, group);
      }
      group.push(row);
    }
    return groups;
  }, [visibleRows]);

  return (
    <div className="blueprint-pref-panel">
      <p className="blueprint-pref-hint">
        勾选=建议保留；锁定=覆盖自动选择；权重越高越优先。当前模式扩展项上限：{SHORT_MAX_EXTENSION_COUNT}。
      </p>

      {/* Top controls: preset combo + action buttons */}
      <div className="blueprint-pref-top">
        <select
          className="blueprint-pref-combo"
          value={selectedPresetId}
          onChange={(e) => setSelectedPresetId(e.target.value)}
          aria-label="题材预置选择"
          title="选择后需点击“套用题材预置”才会生效。"
        >
          <option value="">不使用预置</option>
          {GENRE_PRESETS.map((p) => (
            <option key={p.id} value={p.id}>{p.label}</option>
          ))}
        </select>
        <button
          className="blueprint-pref-btn"
          type="button"
          onClick={() => applyPreset(selectedPresetId)}
        >
          套用题材预置
        </button>
        <button
          className="blueprint-pref-btn"
          type="button"
          disabled={!suggestedPresetId}
          onClick={handleApplySuggested}
        >
          应用建议预置
        </button>
        <button
          className="blueprint-pref-btn blueprint-pref-btn-reset"
          type="button"
          onClick={handleReset}
        >
          重置偏好
        </button>
      </div>

      {/* Genre suggestion */}
      <p className="blueprint-pref-suggestion">
        {suggestedPreset
          ? `当前题材建议预置：${suggestedPreset.label}（仅建议，不自动套用）${suggestedPreset.description ? ` · ${suggestedPreset.description}` : ""}`
          : "当前题材暂未匹配预置，可手动选择。"}
      </p>

      {/* Filter toggles */}
      <div className="blueprint-pref-toggles">
        <label className="blueprint-pref-toggle" title="按当前题材（和建议预置）过滤扩展要素列表。">
          <input
            type="checkbox"
            checked={relatedOnly}
            onChange={(e) => setRelatedOnly(e.target.checked)}
          />
          <span>只看当前题材相关要素</span>
        </label>
        <label className="blueprint-pref-toggle" title="开启后，仅保留你勾选（或锁定保留）的扩展要素。">
          <input
            type="checkbox"
            checked={manualOverride}
            onChange={(e) => toggleManualOverride(e.target.checked)}
          />
          <span>手动覆盖自动选择结果</span>
        </label>
      </div>

      {/* Element rows */}
      <div className="blueprint-pref-scroll">
        {Array.from(groupedRows.entries()).map(([category, categoryRows]) => (
          <div key={category} className="blueprint-pref-category">
            <h4 className="blueprint-pref-category-label">{category}</h4>
            {categoryRows.map((row) => (
              <div key={row.card.id} className="blueprint-pref-row">
                <div className="blueprint-pref-row-main">
                  <div className="blueprint-pref-row-info">
                    <span className="blueprint-pref-row-name">{row.card.name}</span>
                    <span className="blueprint-pref-row-badge">{row.card.category}</span>
                  </div>
                  <div className="blueprint-pref-row-controls">
                    <label className="blueprint-pref-check" title="建议保留该要素（与“锁定”同时勾选可强制保留）。">
                      <input
                        type="checkbox"
                        checked={row.enabled}
                        onChange={(e) => updateRow(row.card.id, { enabled: e.target.checked })}
                      />
                      <span>启用</span>
                    </label>
                    <label className="blueprint-pref-check" title="锁定后覆盖自动选择：勾选启用=强制保留，不勾选启用=强制排除。">
                      <input
                        type="checkbox"
                        checked={row.locked}
                        onChange={(e) => updateRow(row.card.id, { locked: e.target.checked })}
                      />
                      <span>锁定</span>
                    </label>
                    <span className="blueprint-pref-weight-label">权重</span>
                    <input
                      className="blueprint-pref-slider"
                      type="range"
                      min={0}
                      max={100}
                      value={row.weight}
                      onChange={(e) => updateRow(row.card.id, { weight: Number(e.target.value) })}
                      aria-label={`${row.card.name} 权重`}
                    />
                    <span className="blueprint-pref-weight-value">{Math.round(row.weight)}</span>
                  </div>
                </div>
                <p className="blueprint-pref-row-desc">{row.card.description}</p>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
