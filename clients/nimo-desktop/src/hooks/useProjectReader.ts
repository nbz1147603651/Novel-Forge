import { useEffect, useRef, useState } from "react";

import type { EngineClient, NarrativeToolsView, ProjectReaderView, WorkspaceView } from "@nimo/engine-contracts";

import { LatestRequestGate } from "../lib/latest-request";
import { ProjectionPoller } from "../lib/projection-poller";

/**
 * Manages project reader state: selection, loading, and data fetching.
 */
export function useProjectReader(
  engineClient: EngineClient,
  workspace: WorkspaceView | null,
  initialProjectId: string | null,
  active = false,
) {
  const [projectReader, setProjectReader] = useState<ProjectReaderView | null>(null);
  const [projectReaderId, setProjectReaderId] = useState<string | null>(initialProjectId);
  const requestGateRef = useRef(new LatestRequestGate());

  useEffect(() => {
    if (workspace === null) {
      requestGateRef.current.invalidate();
      return;
    }
    if (projectReaderId === null) {
      requestGateRef.current.invalidate();
      setProjectReader(null);
      return;
    }
    const matchingProject = workspace.projects.find(
      (project) => project.id === projectReaderId,
    );
    if (matchingProject === undefined) {
      requestGateRef.current.invalidate();
      setProjectReaderId(null);
      setProjectReader(null);
      return;
    }
    const request = requestGateRef.current.begin(`project-reader:${matchingProject.id}`);
    setProjectReader((current) => current?.projectId === matchingProject.id ? current : null);
    const poller = new ProjectionPoller({
      load: () => engineClient.getProjectReader(matchingProject.id),
      onValue: (nextReader) => {
        if (requestGateRef.current.isCurrent(request)) setProjectReader(nextReader);
      },
      ...(active ? { intervalMs: 5_000 } : {}),
    });
    poller.start();
    return () => {
      poller.stop();
      if (requestGateRef.current.isCurrent(request)) requestGateRef.current.invalidate();
    };
  }, [active, engineClient, projectReaderId, workspace]);

  const openProjectReader = (projectId: string) => {
    if (projectId === projectReaderId) return;
    // Invalidate immediately rather than waiting for the effect after render;
    // otherwise an old project can win in the event-handler-to-effect gap.
    requestGateRef.current.invalidate();
    setProjectReader(null);
    setProjectReaderId(projectId);
  };

  return { projectReader, projectReaderId, openProjectReader };
}

function extractErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.length > 0) {
    return error.message;
  }
  return fallback;
}

/**
 * Pure loader: resolves to either the narrative-tools payload or an error
 * string. Exposed (not just used inside the hook) so unit tests can exercise
 * the error-classification contract without needing React or jsdom.
 */
export async function loadNarrativeTools(
  engineClient: EngineClient,
  projectId: string,
): Promise<
  | { readonly status: "ok"; readonly tools: NarrativeToolsView }
  | { readonly status: "error"; readonly message: string }
> {
  try {
    const tools = await engineClient.getNarrativeTools(projectId);
    return { status: "ok", tools };
  } catch (error: unknown) {
    return {
      status: "error",
      message: extractErrorMessage(
        error,
        `无法加载项目 ${projectId} 的叙事工具。`,
      ),
    };
  }
}

/**
 * Manages narrative tools loading for one explicitly selected project.
 *
 * Each caller must supply the project that its own workbench is currently
 * displaying. The chapter studio and project reader may intentionally have
 * different selections, so their data must never be coupled through a shared
 * default project id.
 *
 * Errors are surfaced instead of being swallowed: a 4xx/5xx response from
 * ``engineClient.getNarrativeTools`` populates ``narrativeToolsError`` so the
 * shell can render a retry-able error surface.
 */
export function useNarrativeTools(
  engineClient: EngineClient,
  projectId: string | null,
  reloadToken: number = 0,
  active = false,
): {
  readonly narrativeTools: NarrativeToolsView | null;
  readonly narrativeToolsError: string | null;
} {
  const [snapshot, setSnapshot] = useState<{
    projectId: string; tools: NarrativeToolsView | null; error: string | null;
  } | null>(null);

  useEffect(() => {
    if (projectId === null) {
      setSnapshot(null);
      return;
    }
    const poller = new ProjectionPoller({
      load: () => loadNarrativeTools(engineClient, projectId),
      onValue: (result) => {
        if (result.status === "ok") {
          setSnapshot({ projectId, tools: result.tools, error: null });
        } else {
          setSnapshot((current) => ({
            projectId, tools: current?.projectId === projectId ? current.tools : null,
            error: result.message,
          }));
        }
      },
      ...(active ? { intervalMs: 5_000 } : {}),
    });
    poller.start();
    return () => poller.stop();
  }, [active, engineClient, projectId, reloadToken]);

  return {
    narrativeTools: snapshot?.projectId === projectId ? snapshot.tools : null,
    narrativeToolsError: snapshot?.projectId === projectId ? snapshot.error : null,
  };
}
