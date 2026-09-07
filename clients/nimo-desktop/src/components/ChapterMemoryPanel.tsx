import { useState } from "react";

import type { ChapterMemoryTabView } from "@nimo/engine-contracts";

interface ChapterMemoryPanelProps {
  readonly onOpen: () => void;
  readonly tabs: readonly ChapterMemoryTabView[];
}

/**
 * The Desktop source presents chapter context as a compact inspector rather
 * than a growing stack of cards.  Keep the tab state local and consume only
 * presentation-ready engine data, so future Tauri/native transports share
 * the same UI boundary.
 */
export function ChapterMemoryPanel({ onOpen, tabs }: ChapterMemoryPanelProps) {
  const [activeTabId, setActiveTabId] = useState(() => tabs[0]?.id ?? "overview");
  const activeTab = tabs.find((tab) => tab.id === activeTabId) ?? tabs[0];

  return (
    <aside aria-label="记忆上下文" className="studio-memory" role="region">
      <div className="studio-panel-heading">
        <div>
          <h3>记忆上下文</h3>
          <p>当前章节可用的故事边界</p>
        </div>
        <span>记忆</span>
      </div>
      <div aria-label="记忆分类" className="chapter-memory-tabs" role="tablist">
        {tabs.map((tab) => (
          <button
            aria-controls={`chapter-memory-panel-${tab.id}`}
            aria-selected={activeTab?.id === tab.id}
            className={activeTab?.id === tab.id ? "is-active" : ""}
            id={`chapter-memory-tab-${tab.id}`}
            key={tab.id}
            onClick={() => setActiveTabId(tab.id)}
            role="tab"
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </div>
      {activeTab !== undefined && (
        <div
          aria-labelledby={`chapter-memory-tab-${activeTab.id}`}
          className="chapter-memory-content"
          id={`chapter-memory-panel-${activeTab.id}`}
          role="tabpanel"
        >
          {activeTab.cards.map((card) => (
            <article className={`chapter-memory-card${card.tone === "highlight" ? " is-highlight" : card.tone === "warning" ? " is-warning" : ""}`} key={card.id}>
              <strong>{card.label}</strong>
              <p>{card.content}</p>
            </article>
          ))}
        </div>
      )}
      <button className="button button-secondary" onClick={onOpen} type="button">查看完整记忆</button>
    </aside>
  );
}
