/** Explicit dev-only entry for the Chapter Studio coauthor placement regression. */
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import type {
  AuthoringSessionView,
  ChapterStudioView,
  EngineClient,
  EngineCommandClient,
  NarrativeToolsView,
  ProjectView,
} from "@nimo/engine-contracts";

import { ChapterStudioPage } from "../../src/components/ChapterStudioPage";
import { ToastProvider } from "../../src/components/Toast";
import { EngineRuntimeContext } from "../../src/lib/engine-runtime-context";
import { mockEngineClient, mockEngineCommandClient } from "../../src/lib/mock-engine";
import { ThemeProvider } from "../../src/theme/ThemeProvider";
import "../../src/styles/global.css";
import "../../src/styles/app-shell.css";
import "../../src/styles/theme-system.css";
import "../../src/styles/design-tokens.css";
import "../../src/styles/interactions.css";
import "../../src/styles/chapter-studio-parity.css";

const session: AuthoringSessionView = {
  projectId: "test-long",
  configured: true,
  disabled: false,
  policy: {
    version: 3,
    mode: "manual",
    startChapter: 5,
    endChapter: 5,
    budgetUsd: null,
    stopped: true,
    stopReason: "离线布局验收",
  },
  inputVersion: "fixture-input-v1",
  allowedActions: [],
  currentTaskId: "",
  waitingReason: "等待作者操作",
  stopState: "stopped",
  proposalIds: [],
  revisionHistory: [],
};

const engine = {
  ...mockEngineClient,
  getAuthoringSession: async () => session,
  getAuthoringProposals: async () => [],
  getAuthoringMessages: async () => [],
} as EngineClient;

const commands = {
  ...mockEngineCommandClient,
  createAuthoringProposal: async () => ({}) as never,
  sendAuthoringMessage: async () => ({}) as never,
  setAuthoringPolicy: async () => session,
  startAuthoringSession: async () => session,
  pauseAuthoringSession: async () => session,
  decideAuthoringProposal: async () => ({}) as never,
  applyAuthoringProposal: async () => ({}) as never,
} as EngineCommandClient;

function Fixture() {
  const [studio, setStudio] = useState<ChapterStudioView | null>(null);
  const [tools, setTools] = useState<NarrativeToolsView | null>(null);
  const [projects, setProjects] = useState<readonly ProjectView[]>([]);

  useEffect(() => {
    let active = true;
    void Promise.all([
      mockEngineClient.getChapterStudio("test-long"),
      mockEngineClient.getNarrativeTools("test-long"),
      mockEngineClient.getWorkspace(),
    ]).then(([nextStudio, nextTools, workspace]) => {
      if (!active) return;
      setStudio(nextStudio);
      setTools(nextTools);
      setProjects(workspace.projects);
    });
    return () => { active = false; };
  }, []);

  if (studio === null || tools === null) return <p>正在装载章台布局…</p>;
  return <div className="nimo-app" data-page="chapter_studio" style={{ display: "block", minHeight: "100dvh" }}>
    <main style={{ padding: 12 }}>
      <ChapterStudioPage
        commandClient={commands}
        engineClient={engine}
        initialDialog={null}
        onNavigate={() => undefined}
        onProjectChange={() => undefined}
        onReadProject={() => undefined}
        onRefresh={() => undefined}
        parityState={null}
        projects={projects.map((project) => ({ id: project.id, title: project.title }))}
        studio={studio}
        tools={tools}
      />
    </main>
  </div>;
}

if (!import.meta.env.DEV) throw new Error("Offline chapter authoring placement fixture is development-only");
createRoot(document.getElementById("root")!).render(
  <ThemeProvider>
    <ToastProvider>
      <EngineRuntimeContext.Provider value={{
        mode: "mock",
        negotiation: { status: "negotiating" },
        diagnostic: { connection: "connected", message: "离线布局验收", canSubmitTasks: true },
        canSubmitTasks: true,
        isCommandAvailable: () => true,
        isFeatureEnabled: (feature) => feature === "authoring_coauthor",
      }}>
        <Fixture />
      </EngineRuntimeContext.Provider>
    </ToastProvider>
  </ThemeProvider>,
);
