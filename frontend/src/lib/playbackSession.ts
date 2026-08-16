/** Guards media events across imperative source replacement. */
export class PlaybackSessionGuard {
  private current = 0;
  private transitioned = new Set<number>();

  begin(): number { this.current += 1; return this.current; }
  isCurrent(session: number): boolean { return session === this.current; }
  claimTransition(session: number): boolean {
    if (!this.isCurrent(session) || this.transitioned.has(session)) return false;
    this.transitioned.add(session);
    return true;
  }
  releaseTransition(session: number): void { this.transitioned.delete(session); }
}

/** Keeps a self-clocked TV/Cast media occurrence monotonic.
 *
 * Some TV browsers refill a forward-only fragmented MP4 by attaching a fresh
 * response to the same media element. If that response starts at timestamp 0,
 * accepting its timeupdate would silently restart the song. Small clock jitter
 * is allowed; a material rollback after real progress is restored instead.
 */
export class PlaybackTimelineGuard {
  private highWater = 0;

  reset(time = 0): void {
    this.highWater = Number.isFinite(time) && time > 0 ? time : 0;
  }

  observe(time: number): { acceptedTime: number; restoreTo: number | null } {
    if (!Number.isFinite(time) || time < 0) {
      return { acceptedTime: this.highWater, restoreTo: null };
    }
    if (this.highWater > 5 && time < this.highWater - 2) {
      return { acceptedTime: this.highWater, restoreTo: this.highWater };
    }
    if (time > this.highWater) this.highWater = time;
    return { acceptedTime: time, restoreTo: null };
  }

  hasReached(duration: number, tolerance = 0.5): boolean {
    return Number.isFinite(duration)
      && duration > 0
      && this.highWater >= Math.max(0, duration - tolerance);
  }

  get highWaterMark(): number { return this.highWater; }
}

export function classifyFullscreenChange(
  wasNativeFullscreen: boolean,
  isNativeFullscreen: boolean,
  profile: "browser" | "tv" | "cast",
): { exited: boolean; recoverVideoSurface: boolean } {
  const exited = wasNativeFullscreen && !isNativeFullscreen;
  return {
    exited,
    // Desktop uses a separate audio master, so its muted video compositor can
    // be remounted without interrupting sound. TV/Cast owns one combined
    // stream and must never be restarted just to repair a desktop-only layer.
    recoverVideoSurface: exited && profile === "browser",
  };
}
