import { useCallback, useEffect, useRef, useState } from "react";
import { usePartyMode } from "@/hooks/usePartyMode";
import { usePlaybackStore } from "@/stores/playbackStore";
import { NowPlayingPage } from "@/pages/NowPlayingPage";
import { PartyStartGate, type PartyStartChoice } from "@/components/PartyStartGate";

/**
 * Cast mode (`/cast`) — a low-overhead variant of Party Mode tuned for Chrome's
 * "Cast tab" feature.  Chrome casting re-encodes the entire rendered tab in
 * real time, so every pixel of on-screen motion becomes CPU + upload bandwidth
 * on the casting PC.  This page therefore minimises motion:
 *   • the artwork grid is STATIC (no scroll, no tile swaps — profile="cast"),
 *   • backdrop-filter blur is disabled (very expensive to re-encode),
 *   • only the video itself moves.
 *
 * Unlike `/tv` it renders at the browser's native size (no fixed scaled canvas):
 * Chrome captures the real tab, so the desktop window resolution is what gets
 * sent — no need to over-render onto a large logical canvas.
 *
 * Playback uses the same single combined audio+video stream as TV mode (its own
 * clock, advances the queue itself); an optional server-side compatibility
 * transcode is controlled separately by the Cast-mode toggle in settings.
 */
export function CastModePage() {
  const { launch } = usePartyMode();
  const setTvMode = usePlaybackStore((s) => s.setTvMode);
  const clearQueue = usePlaybackStore((s) => s.clearQueue);
  const setFullscreenMode = usePlaybackStore((s) => s.setFullscreenMode);

  // Every access is a fresh first access — start at the prompt on every mount.
  const [phase, setPhase] = useState<"prompt" | "loading" | "ready" | "error">("prompt");
  const startedRef = useRef(false);

  // Teardown on unmount. Clear the queue (→ videoId=null) BEFORE leaving tv mode
  // so AudioManager's load effect takes its clean teardown branch instead of
  // briefly starting an audio-master stream for the track being torn down.
  useEffect(() => {
    return () => {
      clearQueue();
      setFullscreenMode("off");
      setTvMode(false);
    };
  }, [clearQueue, setFullscreenMode, setTvMode]);

  const handleStart = useCallback((choice: PartyStartChoice) => {
    if (startedRef.current) return;
    startedRef.current = true;
    setPhase("loading");
    setTvMode(true);
    (async () => {
      try {
        await launch(
          { ...choice.filters, party_year: choice.partyYear },
          { navigate: false, fullscreenMode: choice.fullscreenMode, ignoreSavedEra: true, playlistId: choice.playlistId },
        );
        setPhase("ready");
      } catch {
        setPhase("error");
      }
    })();
  }, [launch, setTvMode]);

  if (phase === "prompt") {
    return <PartyStartGate surface="cast" onStart={handleStart} />;
  }

  if (phase === "error") {
    return (
      <div className="flex h-screen w-screen flex-col items-center justify-center gap-3 bg-black text-center text-white/70">
        <span className="text-xl font-semibold">No videos to play</span>
        <span className="text-sm text-white/50">Check your Party Mode filters in Playarr settings.</span>
      </div>
    );
  }

  if (phase === "loading") {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-black">
        <span className="text-sm uppercase tracking-widest text-white/50 animate-pulse">Loading Cast Mode…</span>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 overflow-hidden bg-black">
      <NowPlayingPage profile="cast" />
    </div>
  );
}
