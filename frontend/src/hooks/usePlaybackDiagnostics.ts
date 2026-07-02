import { useEffect } from "react";
import { playbackApi } from "@/lib/api";

interface DiagOpts {
  videoId?: number | null;
  mode: string;            // "tv" | "browser" | …
  enabled?: boolean;       // default true
}

/**
 * Attaches lightweight playback-health diagnostics to a <video> element:
 *  - counts buffer-underrun stalls (`waiting`/`stalled`) and time spent waiting,
 *  - samples dropped-vs-total decoded frames via getVideoPlaybackQuality(),
 *  - tracks how many seconds are buffered ahead of the playhead,
 * and reports a summary every 15s (and once on cleanup) to the server log via
 * /api/playback/client-metrics, plus console.debug.  This makes network
 * frame-drops/hangs visible in playarr.log without opening dev tools — handy
 * for diagnosing TV playback.
 */
export function usePlaybackDiagnostics(
  videoRef: React.RefObject<HTMLVideoElement | null>,
  { videoId, mode, enabled = true }: DiagOpts,
) {
  useEffect(() => {
    if (!enabled) return;

    let stalls = 0;
    let waitingMs = 0;
    let waitingStart = 0;

    const onWaiting = () => {
      stalls += 1;
      waitingStart = performance.now();
    };
    const onStalled = () => {
      stalls += 1;
    };
    const onResume = () => {
      if (waitingStart) {
        waitingMs += performance.now() - waitingStart;
        waitingStart = 0;
      }
    };

    // The <video> DOM node can be swapped without a videoId change (e.g. toggling
    // video-only fullscreen remounts the element), so we track the currently
    // bound node and re-attach listeners when it changes — otherwise the
    // listeners would leak on the detached node and diagnostics silently die.
    let bound: HTMLVideoElement | null = null;
    const attach = (el: HTMLVideoElement) => {
      el.addEventListener("waiting", onWaiting);
      el.addEventListener("stalled", onStalled);
      el.addEventListener("playing", onResume);
      el.addEventListener("canplay", onResume);
    };
    const detach = (el: HTMLVideoElement) => {
      el.removeEventListener("waiting", onWaiting);
      el.removeEventListener("stalled", onStalled);
      el.removeEventListener("playing", onResume);
      el.removeEventListener("canplay", onResume);
    };
    const sync = () => {
      const el = videoRef.current;
      if (el === bound) return;
      if (bound) detach(bound);
      bound = el;
      if (bound) attach(bound);
    };

    const report = () => {
      sync();
      const el = bound;
      if (!el) return;
      let dropped = 0;
      let total = 0;
      try {
        const q = el.getVideoPlaybackQuality?.();
        if (q) {
          dropped = q.droppedVideoFrames;
          total = q.totalVideoFrames;
        }
      } catch {
        /* not supported */
      }
      // Skip empty snapshots — nothing has played and nothing stalled, so the
      // report would just be noise (e.g. mount→unmount with no playback).
      if (!total && !stalls && !waitingMs) return;
      let bufferedAhead: number | null = null;
      try {
        const b = el.buffered;
        if (b.length) bufferedAhead = Math.max(0, b.end(b.length - 1) - el.currentTime);
      } catch {
        /* ignore */
      }
      const pct = total ? ((dropped / total) * 100).toFixed(1) : "0.0";
      // eslint-disable-next-line no-console
      console.debug(
        `[playback:${mode}] dropped ${dropped}/${total} (${pct}%), stalls=${stalls}, waiting=${Math.round(waitingMs)}ms, ahead=${bufferedAhead?.toFixed(1) ?? "?"}s`,
      );
      playbackApi
        .clientMetrics({
          video_id: videoId ?? null,
          mode,
          dropped,
          total,
          stalls,
          waiting_ms: Math.round(waitingMs),
          buffered_ahead: bufferedAhead,
        })
        .catch(() => {});
      // Reset per-interval counters; frame totals are cumulative on the element.
      stalls = 0;
      waitingMs = 0;
    };

    sync();
    const intervalId = setInterval(report, 15000);

    return () => {
      clearInterval(intervalId);
      report(); // final snapshot for this track (skipped if empty)
      if (bound) detach(bound);
    };
  }, [videoRef, videoId, mode, enabled]);
}
