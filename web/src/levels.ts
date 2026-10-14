// A tiny observable number for high-rate meters (mic level, assistant level).
// Views subscribe and write CSS variables directly, so a 60 fps meter never
// re-renders the React tree.

export class LevelBus {
  private value = 0;
  private readonly listeners = new Set<(value: number) => void>();

  get(): number {
    return this.value;
  }

  set(value: number): void {
    const next = value < 0 ? 0 : value > 1 ? 1 : value;
    if (Math.abs(next - this.value) < 0.002) return;
    this.value = next;
    for (const listener of this.listeners) listener(next);
  }

  subscribe(listener: (value: number) => void): () => void {
    this.listeners.add(listener);
    listener(this.value);
    return () => {
      this.listeners.delete(listener);
    };
  }
}

/** Attack/release smoothing for meters: fast rise, slower fall. */
export function smoothLevel(previous: number, target: number, attack = 0.6, release = 0.15): number {
  const k = target > previous ? attack : release;
  return previous + (target - previous) * k;
}
