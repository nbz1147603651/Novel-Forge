import { useCallback, useEffect, useRef, useState } from "react";

import type { EngineClient, JobView, SettingsView, WorkflowView, WorkspaceView } from "@nimo/engine-contracts";

import { negotiateEngine, getNegotiationState } from "../lib/engine-client-factory";
import type { NegotiationState } from "../lib/engine-negotiation";
import { LatestRequestGate } from "../lib/latest-request";

export type ConnectionState = "connecting" | "connected" | "disconnected" | "retrying" | "incompatible";

const MAX_RETRIES = 4;
const BASE_DELAY_MS = 1_000;

/**
 * Loads workspace, jobs, and workflow data from the engine client.
 * Includes automatic retry with exponential backoff when the backend
 * is unreachable, and exposes connection state for UI feedback.
 */
export function useEngineData(engineClient: EngineClient) {
  const [workspace, setWorkspace] = useState<WorkspaceView | null>(null);
  const [jobs, setJobs] = useState<readonly JobView[]>([]);
  const [workflow, setWorkflow] = useState<WorkflowView | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [negotiation, setNegotiation] = useState<NegotiationState>(getNegotiationState);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const taskDataRequestGateRef = useRef(new LatestRequestGate());

  /**
   * Refresh workspace and task projections together.
   *
   * Workflow cards and the floating companion intentionally consume separate
   * Engine views, while long-init actions also depend on the workspace's
   * resumable-project flag. Keeping all three at one refresh boundary prevents
   * a reset task from disappearing while stale "继续立项" actions remain visible.
   */
  const refreshTaskData = useCallback(async (): Promise<WorkflowView> => {
    const request = taskDataRequestGateRef.current.begin("task-data");
    const [nextWorkspace, nextJobs, nextWorkflow] = await Promise.all([
      engineClient.getWorkspace(),
      engineClient.listJobs(),
      engineClient.getWorkflow(),
    ]);
    if (!taskDataRequestGateRef.current.isCurrent(request)) return nextWorkflow;
    setWorkspace(nextWorkspace);
    setJobs(nextJobs);
    setWorkflow(nextWorkflow);
    return nextWorkflow;
  }, [engineClient]);

  const fetchData = useCallback(async () => {
    const request = taskDataRequestGateRef.current.begin("task-data");
    try {
      // Run capabilities negotiation before fetching data.
      // In mock mode this returns immediately with a synthetic connected state.
      const negResult = await negotiateEngine();
      if (!taskDataRequestGateRef.current.isCurrent(request)) return;
      setNegotiation(negResult);

      if (negResult.status === "incompatible") {
        setConnectionState("incompatible");
        return;
      }
      if (negResult.status === "unavailable") {
        // Fall through to retry logic below
        throw new Error(negResult.reason);
      }

      const [nextWorkspace, nextJobs, nextWorkflow] = await Promise.all([
        engineClient.getWorkspace(),
        engineClient.listJobs(),
        engineClient.getWorkflow(),
      ]);
      if (!taskDataRequestGateRef.current.isCurrent(request)) return;
      setWorkspace(nextWorkspace);
      setJobs(nextJobs);
      setWorkflow(nextWorkflow);
      setConnectionState("connected");
      retryCountRef.current = 0;
    } catch {
      if (!taskDataRequestGateRef.current.isCurrent(request)) return;
      if (retryCountRef.current >= MAX_RETRIES) {
        setConnectionState("disconnected");
        return;
      }
      setConnectionState("retrying");
      const delay = BASE_DELAY_MS * 2 ** retryCountRef.current;
      retryCountRef.current += 1;
      retryTimerRef.current = setTimeout(() => void fetchData(), delay);
    }
  }, [engineClient]);

  useEffect(() => {
    void fetchData();
    return () => {
      if (retryTimerRef.current !== null) clearTimeout(retryTimerRef.current);
    };
  }, [fetchData]);

  const retry = useCallback(() => {
    retryCountRef.current = 0;
    if (retryTimerRef.current !== null) clearTimeout(retryTimerRef.current);
    setConnectionState("connecting");
    void fetchData();
  }, [fetchData]);

  return {
    workspace,
    jobs,
    workflow,
    connectionState,
    negotiation,
    refreshTaskData,
    retry,
  };
}

/**
 * Manages settings loading with lazy initialization and dirty tracking.
 */
export function useSettings(engineClient: EngineClient) {
  const [settings, setSettings] = useState<SettingsView | null>(null);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [settingsSaveRequest, setSettingsSaveRequest] = useState(0);
  const settingsRequestRef = useRef<Promise<SettingsView> | null>(null);

  const loadSettings = useCallback((force: boolean): Promise<SettingsView> => {
    if (!force && settingsRequestRef.current !== null) return settingsRequestRef.current;
    const request = engineClient.getSettings();
    settingsRequestRef.current = request;
    return request
      .then((nextSettings) => {
        if (settingsRequestRef.current === request) setSettings(nextSettings);
        return nextSettings;
      })
      .catch((error: unknown) => {
        if (settingsRequestRef.current === request) settingsRequestRef.current = null;
        throw error;
      });
  }, [engineClient]);

  const ensureSettingsLoaded = useCallback(() => loadSettings(false), [loadSettings]);
  const reloadSettings = useCallback(() => loadSettings(true), [loadSettings]);

  const requestSave = () => setSettingsSaveRequest((request) => request + 1);

  return {
    settings,
    settingsDirty,
    settingsSaveRequest,
    setSettingsDirty,
    ensureSettingsLoaded,
    reloadSettings,
    requestSave,
  };
}
