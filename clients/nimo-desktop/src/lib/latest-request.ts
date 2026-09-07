/**
 * Last-request-wins guard for resource reads and command result projections.
 *
 * A resource may be requested again before its earlier request completes
 * (project switching, changing chapters, explicit refreshes).  Consumers
 * retain the returned token and must verify it immediately before mutating
 * React state.  Starting or invalidating a request makes every older token
 * stale without requiring transport-specific cancellation support.
 */
export interface LatestRequestToken {
  readonly key: string;
  readonly generation: number;
}

export class LatestRequestGate {
  private currentGeneration = 0;

  begin(key: string): LatestRequestToken {
    this.currentGeneration += 1;
    return { key, generation: this.currentGeneration };
  }

  invalidate(): void {
    this.currentGeneration += 1;
  }

  isCurrent(token: LatestRequestToken): boolean {
    return token.generation === this.currentGeneration;
  }
}
