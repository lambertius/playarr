import { describe, expect, it } from "vitest";
import { classifyFullscreenChange, PlaybackSessionGuard } from "./playbackSession";

describe("playback media session guard", () => {
  it("ignores stale and duplicate ended events", () => {
    const guard = new PlaybackSessionGuard();
    const first = guard.begin(); const second = guard.begin();
    expect(guard.claimTransition(first)).toBe(false);
    expect(guard.claimTransition(second)).toBe(true);
    expect(guard.claimTransition(second)).toBe(false);
  });

  it("allows repeat-one to re-arm the current occurrence", () => {
    const guard = new PlaybackSessionGuard(); const session = guard.begin();
    expect(guard.claimTransition(session)).toBe(true);
    guard.releaseTransition(session);
    expect(guard.claimTransition(session)).toBe(true);
  });
});

describe("fullscreen media-surface recovery", () => {
  it("recovers the desktop video layer only on native fullscreen exit", () => {
    expect(classifyFullscreenChange(true, false, "browser")).toEqual({ exited: true, recoverVideoSurface: true });
    expect(classifyFullscreenChange(false, true, "browser")).toEqual({ exited: false, recoverVideoSurface: false });
    expect(classifyFullscreenChange(true, false, "tv")).toEqual({ exited: true, recoverVideoSurface: false });
    expect(classifyFullscreenChange(true, false, "cast")).toEqual({ exited: true, recoverVideoSurface: false });
  });
});
