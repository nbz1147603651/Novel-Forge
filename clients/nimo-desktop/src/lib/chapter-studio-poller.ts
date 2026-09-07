/**
 * Chapter-studio projection poller.
 *
 * Engine autorun state (studio.autorun) and job activity change on the
 * backend without any push channel to the web surface, so the chapter-studio
 * page polls the projection while it is active.  The poller never clears a
 * loaded view (no LoadingSurface flicker) and keeps the last loaded view on
 * transient failures.
 */

import type { ChapterStudioView } from "@nimo/engine-contracts";

import { LatestRequestGate, type LatestRequestToken } from "./latest-request";

export const CHAPTER_STUDIO_POLL_INTERVAL_MS = 2_500;

export interface ChapterStudioPollerOptions {
  readonly fetchStudio: (projectId: string) => Promise<ChapterStudioView>;
  readonly onStudio: (studio: ChapterStudioView) => void;
  readonly intervalMs?: number;
}

export class ChapterStudioPoller {
  private readonly fetchStudio: ChapterStudioPollerOptions["fetchStudio"];
  private readonly onStudio: ChapterStudioPollerOptions["onStudio"];
  private readonly intervalMs: number;
  private readonly gate = new LatestRequestGate();
  private timer: ReturnType<typeof setInterval> | null = null;
  private projectId: string | null = null;
  private inFlightRequest: LatestRequestToken | null = null;
  private cancelled = false;

  constructor(options: ChapterStudioPollerOptions) {
    this.fetchStudio = options.fetchStudio;
    this.onStudio = options.onStudio;
    this.intervalMs = options.intervalMs ?? CHAPTER_STUDIO_POLL_INTERVAL_MS;
  }

  start(projectId: string): void {
    this.stop();
    this.projectId = projectId;
    this.cancelled = false;
    this.timer = setInterval(() => {
      void this.tick();
    }, this.intervalMs);
  }

  stop(): void {
    this.cancelled = true;
    this.gate.invalidate();
    this.inFlightRequest = null;
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.projectId = null;
  }

  private async tick(): Promise<void> {
    const projectId = this.projectId;
    // Do not stack slow network requests.  The interval is a refresh cadence,
    // not a concurrency target; the next tick after the current request
    // settles will fetch the newest projection.
    if (projectId === null || this.inFlightRequest !== null) return;
    const request = this.gate.begin(`studio-poll:${projectId}`);
    this.inFlightRequest = request;
    try {
      const nextStudio = await this.fetchStudio(projectId);
      if (this.cancelled || !this.gate.isCurrent(request)) return;
      this.onStudio(nextStudio);
    } catch {
      // Keep the last loaded view; the initial-load effect surfaces errors.
    } finally {
      if (this.inFlightRequest === request) this.inFlightRequest = null;
    }
  }
}
