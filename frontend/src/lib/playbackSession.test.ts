import { describe, expect, it } from "vitest";
import { classifyFullscreenChange, PlaybackSessionGuard, PlaybackTimelineGuard } from "./playbackSession";

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

describe("self-clocked playback timeline guard", () => {
  it("restores a material same-occurrence rollback instead of accepting time zero", () => {
    const timeline = new PlaybackTimelineGuard();
    expect(timeline.observe(60)).toEqual({ acceptedTime: 60, restoreTo: null });
    expect(timeline.observe(0)).toEqual({ acceptedTime: 60, restoreTo: 60 });
    expect(timeline.highWaterMark).toBe(60);
  });

  it("allows small clock jitter and an intentional reset", () => {
    const timeline = new PlaybackTimelineGuard();
    timeline.observe(60);
    expect(timeline.observe(59)).toEqual({ acceptedTime: 59, restoreTo: null });
    timeline.reset();
    expect(timeline.observe(0)).toEqual({ acceptedTime: 0, restoreTo: null });
  });

  it("uses the authoritative duration to finish once despite a late ended event", () => {
    const session = new PlaybackSessionGuard();
    const timeline = new PlaybackTimelineGuard();
    const token = session.begin();
    timeline.observe(239.8);

    expect(timeline.hasReached(240, 0.25)).toBe(true);
    expect(session.claimTransition(token)).toBe(true);
    expect(session.claimTransition(token)).toBe(false);
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
