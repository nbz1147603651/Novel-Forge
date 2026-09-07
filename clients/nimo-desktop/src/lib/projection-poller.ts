/** Serial, last-session-wins reads. Unchanged snapshots retain React identity. */
export class ProjectionPoller<T> {
  private timer: ReturnType<typeof setTimeout> | undefined;
  private generation = 0;
  private fingerprint: string | undefined;

  constructor(private readonly options: {
    readonly load: () => Promise<T>;
    readonly onValue: (value: T) => void;
    readonly onError?: (error: unknown) => void;
    readonly intervalMs?: number;
  }) {}

  start(): void {
    this.stop();
    this.fingerprint = undefined;
    void this.tick(this.generation);
  }

  stop(): void {
    this.generation += 1;
    clearTimeout(this.timer);
  }

  private async tick(generation: number): Promise<void> {
    try {
      const value = await this.options.load();
      if (generation !== this.generation) return;
      const fingerprint = JSON.stringify(value);
      if (fingerprint !== this.fingerprint) {
        this.fingerprint = fingerprint;
        this.options.onValue(value);
      }
    } catch (error) {
      if (generation === this.generation) {
        this.fingerprint = undefined; // Recovery must clear the visible error, even for identical data.
        this.options.onError?.(error);
      }
    } finally {
      if (generation === this.generation && this.options.intervalMs !== undefined) {
        this.timer = setTimeout(() => void this.tick(generation), this.options.intervalMs);
      }
    }
  }
}
