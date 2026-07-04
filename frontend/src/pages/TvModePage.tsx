import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { usePartyMode } from "@/hooks/usePartyMode";
import { usePlaybackStore } from "@/stores/playbackStore";
import { useArtworkSettings } from "@/stores/artworkSettingsStore";
import { NowPlayingPage } from "@/pages/NowPlayingPage";
import { PartyStartGate, type PartyStartChoice } from "@/components/PartyStartGate";

/** Full-screen "the party is spinning up" overlay, shown from the moment Start
 *  is pressed until the first video frame actually plays. Covers both the queue
 *  fetch and the on-the-fly transcode/buffer window, which is otherwise a black
 *  screen with no feedback. */
function StartingPartyOverlay() {
  return (
    <div className="pointer-events-none fixed inset-0 z-40 flex flex-col items-center justify-center gap-6 bg-black text-white">
      <div className="flex items-center gap-3">
        <Sparkles className="h-8 w-8 text-accent animate-pulse" />
        <span className="text-3xl font-bold tracking-tight">Starting the party…</span>
      </div>
      <Loader2 className="h-10 w-10 animate-spin text-accent" />
      <span className="text-sm uppercase tracking-widest text-white/50">
        Cueing up your videos — this takes a moment
      </span>
      <span className="text-xs text-white/30">Press OK if it doesn't begin on its own</span>
    </div>
  );
}

/**
 * TV / kiosk mode (`/tv`) — opens the exact Party Mode visual full-screen with
 * no app chrome, for a TV device's browser (NVIDIA Shield / TV Bro, Android/
 * Google TV, a Chromium kiosk, …).
 *
 * TV browsers often render the page at a low logical viewport, which would
 * leave the artwork grid only a few tiles wide on a 4K screen.  So the content
 * lays out on a fixed 16:9 canvas at the configured TV resolution (1080/1440/
 * 2160) and is CSS-scaled to fit — giving browser-like tile density and the
 * same video-to-background ratio a desktop browser shows at that resolution.
 *
 * Playback (NowPlayingPage `tvMode`): a single combined audio+video stream that
 * is its own clock and advances the queue; the global audio element stays
 * silent.  Autoplay falls back to a one-tap "Press OK to start" if blocked.
 */
export function TvModePage() {
  const { launch } = usePartyMode();
  const setTvMode = usePlaybackStore((s) => s.setTvMode);
  const clearQueue = usePlaybackStore((s) => s.clearQueue);
  const setFullscreenMode = usePlaybackStore((s) => s.setFullscreenMode);
  const tvResolution = useArtworkSettings((s) => s.tvResolution);

  const canvasH = tvResolution;
  const canvasW = Math.round((tvResolution * 16) / 9);
  const [fit, setFit] = useState({ scale: 1, x: 0, y: 0 });

  // Every access is a fresh first access — start at the prompt on every mount.
  const [phase, setPhase] = useState<"prompt" | "loading" | "ready" | "error">("prompt");
  const startedRef = useRef(false);
  // The "Starting the party…" overlay stays up until the video actually plays.
  const [playbackStarted, setPlaybackStarted] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const update = () => {
      const scale = Math.min(window.innerWidth / canvasW, window.innerHeight / canvasH);
      setFit({
        scale,
        x: (window.innerWidth - canvasW * scale) / 2,
        y: (window.innerHeight - canvasH * scale) / 2,
      });
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [canvasW, canvasH]);

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

  // Detect the moment real playback begins so the overlay can step aside.
  // Media `playing` events don't bubble, so listen in the capture phase on the
  // wrapper. A safety timeout reveals the surface even if `playing` never fires
  // (e.g. autoplay was blocked and the user hasn't pressed OK yet).
  useEffect(() => {
    if (phase !== "loading" && phase !== "ready") return;
    const el = wrapRef.current;
    if (!el) return;
    const onPlaying = () => setPlaybackStarted(true);
    el.addEventListener("playing", onPlaying, true);
    const safety = window.setTimeout(() => setPlaybackStarted(true), 15000);
    return () => {
      el.removeEventListener("playing", onPlaying, true);
      window.clearTimeout(safety);
    };
  }, [phase]);

  // If autoplay is blocked, NowPlayingPage shows its own "Press OK" prompt —
  // step our overlay aside so that prompt is visible and tappable.
  const handleNeedsGesture = useCallback((needs: boolean) => {
    if (needs) setPlaybackStarted(true);
  }, []);

  const handleStart = useCallback((choice: PartyStartChoice) => {
    if (startedRef.current) return;
    startedRef.current = true;
    setPhase("loading");
    setTvMode(true);
    (async () => {
      try {
        await launch(
          { party_year: choice.partyYear },
          { navigate: false, fullscreenMode: choice.fullscreenMode, ignoreSavedEra: true },
        );
        setPhase("ready");
      } catch {
        setPhase("error");
      }
    })();
  }, [launch, setTvMode]);

  if (phase === "prompt") {
    return <PartyStartGate surface="tv" onStart={handleStart} />;
  }

  if (phase === "error") {
    return (
      <div className="flex h-screen w-screen flex-col items-center justify-center gap-3 bg-black text-center text-white/70">
        <span className="text-xl font-semibold">No videos to play</span>
        <span className="text-sm text-white/50">Check your Party Mode filters in Playarr settings.</span>
      </div>
    );
  }

  // From here on the surface is mounted. The video element only exists once
  // `phase === "ready"`, so during "loading" the overlay stands alone; once
  // ready it sits on top of the (buffering) player until the first frame plays.
  const showStartingOverlay = phase === "loading" || (phase === "ready" && !playbackStarted);

  return (
    <div ref={wrapRef} className="fixed inset-0 overflow-hidden bg-black">
      {phase === "ready" && (
        <div
          style={{
            position: "absolute",
            width: canvasW,
            height: canvasH,
            left: fit.x,
            top: fit.y,
            transform: `scale(${fit.scale})`,
            transformOrigin: "top left",
          }}
        >
          <NowPlayingPage profile="tv" tvCanvasHeight={canvasH} onNeedsGesture={handleNeedsGesture} />
        </div>
      )}
      {showStartingOverlay && <StartingPartyOverlay />}
    </div>
  );
}
