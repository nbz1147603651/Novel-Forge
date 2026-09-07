/** Explicit dev-only entry, not imported by the application or build entry. */
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import type { EngineClient, EngineCommandClient, ProjectReaderView, ProjectView } from "@nimo/engine-contracts";
import { ProjectsReader } from "../../src/components/ProjectsReader";
import { ToastProvider } from "../../src/components/Toast";
import { ThemeProvider } from "../../src/theme/ThemeProvider";
import { EngineRuntimeContext } from "../../src/lib/engine-runtime-context";
import { LegacyLocalEngineClient } from "../../src/lib/legacy-engine-client";
import { mockEngineClient } from "../../src/lib/mock-engine";
import "../../src/styles/global.css";
import "../../src/styles/app-shell.css";
import "../../src/styles/theme-system.css";
import "../../src/styles/projects-primary.css";
import "../../src/styles/interactions.css";
import "../../src/styles/narrative-tools.css";
import "../../src/styles/document-renderer.css";
import "../../src/styles/document-rich.css";
import "../../src/styles/source-primitives-parity.css";
import "../../src/styles/source-message-dialog.css";
import "../../src/styles/design-tokens.css";
import "../../src/styles/pyside-page-parity.css";
import "../../src/styles/reader-folio.css";
import "../../src/styles/typography-system.css";
import "../../src/styles/toast.css";

const baseUrl = "http://127.0.0.1:18791";
const client = new LegacyLocalEngineClient({ baseUrl, maxRetries: 0 });
const commands: EngineCommandClient = client;
const engine: EngineClient = client;

function Fixture() {
  const [reader, setReader] = useState<ProjectReaderView | null>(null);
  const [projects, setProjects] = useState<readonly ProjectView[]>([]);
  const [released, setReleased] = useState(true);
  const [stats, setStats] = useState("");
  const refresh = async () => {
    const [data, template, workspace] = await Promise.all([
      fetch(`${baseUrl}/fixture`).then((response) => response.json()),
      mockEngineClient.getProjectReader("test-long"), mockEngineClient.getWorkspace(),
    ]);
    setReader({ ...template, projectId: "book", projectTitle: "药证 · 离线验收", tabs: template.tabs.map((tab) => tab.id === "chapters" ? { ...tab, artifacts: [{ id: "chapter-1", label: "第 1 章 晨钟与药盏", caption: "已归档测试正文", sourceLabel: "chapters/chapter_001.md", format: "markdown", content: data.text, paragraphs: [data.text], facts: [], chapterNumber: 1 }] } : tab) });
    setProjects([{ ...workspace.projects[0]!, id: "book", title: "药证 · 离线验收" }]);
    setStats(`任务 ${data.jobs} · 真实模型调用 ${data.modelCalls} · 后续章：${data.laterChapter}`);
  };
  useEffect(() => { void refresh(); }, []);
  return <ThemeProvider><ToastProvider><EngineRuntimeContext.Provider value={{ mode: "legacy", negotiation: { status: "negotiating" }, diagnostic: { connection: "checking", message: "离线测试", canSubmitTasks: true }, canSubmitTasks: true, isCommandAvailable: () => true, isFeatureEnabled: () => released }}>
    <div style={{ height: "100vh", padding: 12, display: "grid", gridTemplateRows: "auto minmax(0, 1fr)", gap: 8 }}>
      <header style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}><strong>离线验收，不连接真实作品</strong><button type="button" onClick={() => { void refresh(); }}>核验正式正文</button><button type="button" onClick={() => setReleased((value) => !value)}>切换能力协商</button><button type="button" onClick={() => { void fetch(`${baseUrl}/fixture/concurrent-edit`, { method: "POST" }).then(refresh); }}>模拟另窗人工改稿</button><small>{stats}</small></header>
      <ProjectsReader engineClient={engine} commandClient={commands} isLoading={reader === null} projects={projects} reader={reader} tools={null} onSelectProject={() => {}} />
    </div>
  </EngineRuntimeContext.Provider></ToastProvider></ThemeProvider>;
}

if (!import.meta.env.DEV) throw new Error("Offline authoring fixture is development-only");
createRoot(document.getElementById("root")!).render(<Fixture />);
