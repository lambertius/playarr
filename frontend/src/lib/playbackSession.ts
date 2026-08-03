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
