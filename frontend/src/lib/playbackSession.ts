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
