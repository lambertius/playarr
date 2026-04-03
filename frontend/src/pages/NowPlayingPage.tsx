import { useCallback, useEffect, useRef, useState, memo } from "react";
import { Play, Monitor, Maximize, X } from "lucide-react";
import { Tooltip } from "@/components/Tooltip";
import { usePlaybackStore, type PlaybackTrack } from "@/stores/playbackStore";
import { useArtworkSettings } from "@/stores/artworkSettingsStore";
import { playbackApi, libraryApi } from "@/lib/api";
import { FullscreenControls } from "@/components/FullscreenControls";
import type { VideoItemDetail } from "@/types";

// ── Animated artwork grid background ──────────────────────
type TransitionKind = "fade" | "flip" | "spin";
type CellState = { url: string; nextUrl: string | null; transition: TransitionKind };

const ArtworkBackground = memo(function ArtworkBackground() {
  const artworkSize = useArtworkSettings((s) => s.artworkSize);
  const scrollDuration = useArtworkSettings((s) => s.scrollDuration);
  const changeRate = useArtworkSettings((s) => s.changeRate);
  const fadeDuration = useArtworkSettings((s) => s.fadeDuration);
  const artRepeatPenalty = useArtworkSettings((s) => s.artRepeatPenalty);
  const artChangeEnabled = useArtworkSettings((s) => s.artChangeEnabled);
  const artChangeCount = useArtworkSettings((s) => s.artChangeCount);
  const artChangeStyle = useArtworkSettings((s) => s.artChangeStyle);

  // Compute columns to fill viewport width
  const containerRef = useRef<HTMLDivElement>(null);
  const [cols, setCols] = useState(6);

  useEffect(() => {
    const update = () => {
      const w = containerRef.current?.clientWidth ?? window.innerWidth;
      setCols(Math.max(1, Math.ceil(w / artworkSize)));
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [artworkSize]);

  // Rows: fill viewport height, then double for seamless loop
  const viewH = typeof window !== "undefined" ? window.innerHeight : 800;
  const rowsNeeded = Math.max(4, Math.ceil(viewH / artworkSize) + 2);
  const CELL_COUNT = cols * rowsNeeded;

  const [artworkPool, setArtworkPool] = useState<string[]>([]);
  const [grid, setGrid] = useState<CellState[]>([]);
  const offsetRef = useRef(0);

  // Penalty tracking for anti-repetition: url → penalty weight (0-1)
  const penaltyMapRef = useRef<Map<string, number>>(new Map());

  // Keep mutable refs for values used inside the swap timer so the
  // effect doesn't need to restart when they change.
  const cellCountRef = useRef(CELL_COUNT);
  cellCountRef.current = CELL_COUNT;
  const changeRateRef = useRef(changeRate);
  changeRateRef.current = changeRate;
  const poolRef = useRef(artworkPool);
  poolRef.current = artworkPool;
  const fadeDurationRef = useRef(fadeDuration);
  fadeDurationRef.current = fadeDuration;
  const artRepeatPenaltyRef = useRef(artRepeatPenalty);
  artRepeatPenaltyRef.current = artRepeatPenalty;
  const artChangeEnabledRef = useRef(artChangeEnabled);
  artChangeEnabledRef.current = artChangeEnabled;
  const artChangeCountRef = useRef(artChangeCount);
  artChangeCountRef.current = artChangeCount;
  const artChangeStyleRef = useRef(artChangeStyle);
  artChangeStyleRef.current = artChangeStyle;

  // Fetch artwork IDs once
  useEffect(() => {
    playbackApi.artworkIds().then((items) => {
      const urls = items.map((item) =>
        item.type === "poster"
          ? playbackApi.posterUrl(item.videoId)
          : playbackApi.artworkUrl(item.videoId, item.type)
      );
      if (urls.length === 0) return;
      // Shuffle
      for (let i = urls.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [urls[i], urls[j]] = [urls[j], urls[i]];
      }
      setArtworkPool(urls);
    }).catch(() => {});
  }, []);

  // Rebuild grid when pool or cell count changes
  useEffect(() => {
    if (artworkPool.length === 0) return;
    const initial: CellState[] = [];
    for (let i = 0; i < CELL_COUNT; i++) {
      initial.push({ url: artworkPool[i % artworkPool.length], nextUrl: null, transition: "fade" });
    }
    setGrid(initial);
    offsetRef.current = CELL_COUNT;
  }, [artworkPool, CELL_COUNT]);

  // Pick next artwork url using anti-repetition penalty
  const pickNextUrl = useCallback(() => {
    const pool = poolRef.current;
    if (pool.length === 0) return pool[0];
    const penalty = artRepeatPenaltyRef.current / 100; // 0-1
    if (penalty <= 0) {
      // No penalty — cycle sequentially
      const url = pool[offsetRef.current % pool.length];
      offsetRef.current++;
      return url;
    }

    // Gather currently visible URLs for penalty
    const gridRef = penaltyMapRef.current;

    // Weighted random selection from pool
    const weights = pool.map((url) => {
      const p = gridRef.get(url) ?? 0;
      return Math.max(0.01, 1 - p * penalty);
    });
    const total = weights.reduce((a, b) => a + b, 0);
    let r = Math.random() * total;
    for (let i = 0; i < weights.length; i++) {
      r -= weights[i];
      if (r <= 0) return pool[i];
    }
    return pool[pool.length - 1];
  }, []);

  // Swap a random cell periodically with crossfade.
  useEffect(() => {
    if (artworkPool.length === 0) return;
    let alive = true;
    let outerTimeout: ReturnType<typeof setTimeout>;
    const fadeTimeouts = new Set<ReturnType<typeof setTimeout>>();

    // Decay penalties over time
    const decayInterval = setInterval(() => {
      const map = penaltyMapRef.current;
      for (const [url, val] of map) {
        const next = val * 0.92; // decay ~8% per tick
        if (next < 0.05) map.delete(url);
        else map.set(url, next);
      }
    }, 2000);

    const scheduleSwap = () => {
      const rate = changeRateRef.current * 1000;
      const swapCount = Math.min(artChangeCountRef.current, cellCountRef.current);
      // Distribute swaps evenly: interval = rate / swapCount, with ±25% jitter
      const perTileInterval = Math.max(200, rate / Math.max(1, swapCount));
      const min = Math.max(200, perTileInterval * 0.75);
      const max = perTileInterval * 1.25;
      const delay = min + Math.random() * (max - min);

      outerTimeout = setTimeout(() => {
        if (!alive) return;
        if (!artChangeEnabledRef.current) { scheduleSwap(); return; }
        const count = cellCountRef.current;
        const pool = poolRef.current;
        if (pool.length === 0 || count === 0) { scheduleSwap(); return; }

        // Swap exactly ONE tile per tick
        const idx = Math.floor(Math.random() * count);

        // Resolve transition style
        const styleChoices: TransitionKind[] = ["fade", "flip", "spin"];
        const pickTransition = (): TransitionKind => {
          const s = artChangeStyleRef.current;
          if (s === "random") return styleChoices[Math.floor(Math.random() * styleChoices.length)];
          return s;
        };

        const newUrl = pickNextUrl();
        const transition = pickTransition();

        // Apply penalty to newly shown image
        penaltyMapRef.current.set(newUrl, 1);

        // Set nextUrl to trigger transition
        setGrid((prev) => {
          if (idx >= prev.length) return prev;
          const outgoing = prev[idx].url;
          const map = penaltyMapRef.current;
          const existing = map.get(outgoing) ?? 0;
          map.set(outgoing, Math.max(0, existing - 0.3));

          const copy = [...prev];
          copy[idx] = { ...copy[idx], nextUrl: newUrl, transition };
          return copy;
        });

        // After transition completes, promote nextUrl to url
        const fadeDur = fadeDurationRef.current * 1000;
        const fadeId = setTimeout(() => {
          fadeTimeouts.delete(fadeId);
          if (!alive) return;
          setGrid((prev) => {
            if (idx >= prev.length) return prev;
            if (prev[idx]?.nextUrl) {
              const copy = [...prev];
              copy[idx] = { url: copy[idx].nextUrl!, nextUrl: null, transition: "fade" };
              return copy;
            }
            return prev;
          });
        }, fadeDur);
        fadeTimeouts.add(fadeId);

        scheduleSwap();
      }, delay);
    };
    scheduleSwap();
    return () => {
      alive = false;
      clearTimeout(outerTimeout);
      clearInterval(decayInterval);
      fadeTimeouts.forEach(clearTimeout);
    };
  }, [artworkPool, pickNextUrl]);

  if (grid.length === 0) return <div className="absolute inset-0 bg-black" />;

  const cells = [...grid, ...grid];

  return (
    <div ref={containerRef} className="absolute inset-0 overflow-hidden bg-black">
      <div
        className="animate-scroll-down"
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${cols}, ${artworkSize}px)`,
          gridAutoRows: `${artworkSize}px`,
          gap: "4px",
          justifyContent: "center",
          animationDuration: `${scrollDuration}s`,
        }}
      >
        {/* Render grid twice for seamless loop */}
        {cells.map((cell, i) =>
          cell ? (
            <div key={i} className="overflow-hidden relative" style={{ width: artworkSize, height: artworkSize, perspective: artworkSize * 2 }}>
              {/* Fade transition: new image fades in over old */}
              {cell.transition === "fade" && (
                <>
                  <img
                    src={cell.url}
                    alt=""
                    className="absolute inset-0 w-full h-full object-cover"
                  />
                  {cell.nextUrl && (
                    <img
                      src={cell.nextUrl}
                      alt=""
                      className="absolute inset-0 w-full h-full object-cover"
                      style={{ animation: `artwork-fade-in ${fadeDuration}s ease-in-out forwards` }}
                    />
                  )}
                </>
              )}
              {/* Flip transition: card flips on Y axis, showing new art on the back */}
              {cell.transition === "flip" && cell.nextUrl && (
                <div
                  className="absolute inset-0"
                  style={{
                    animation: `artwork-flip ${fadeDuration}s ease-in-out forwards`,
                    transformStyle: "preserve-3d",
                  }}
                >
                  <img
                    src={cell.url}
                    alt=""
                    className="absolute inset-0 w-full h-full object-cover"
                    style={{ opacity: 1, animation: `artwork-flip-hide ${fadeDuration}s step-end forwards` }}
                  />
                  <img
                    src={cell.nextUrl}
                    alt=""
                    className="absolute inset-0 w-full h-full object-cover"
                    style={{ opacity: 0, animation: `artwork-flip-show ${fadeDuration}s step-end forwards` }}
                  />
                </div>
              )}
              {cell.transition === "flip" && !cell.nextUrl && (
                <img src={cell.url} alt="" className="absolute inset-0 w-full h-full object-cover" />
              )}
              {/* Spin transition: tile spins 360°, art swaps at midpoint */}
              {cell.transition === "spin" && cell.nextUrl && (
                <div
                  className="absolute inset-0"
                  style={{ animation: `artwork-spin ${fadeDuration}s ease-in-out forwards` }}
                >
                  <img
                    src={cell.url}
                    alt=""
                    className="absolute inset-0 w-full h-full object-cover"
                    style={{ opacity: 1, animation: `artwork-spin-hide ${fadeDuration}s step-end forwards` }}
                  />
                  <img
                    src={cell.nextUrl}
                    alt=""
                    className="absolute inset-0 w-full h-full object-cover"
                    style={{ opacity: 0, animation: `artwork-spin-show ${fadeDuration}s step-end forwards` }}
                  />
                </div>
              )}
              {cell.transition === "spin" && !cell.nextUrl && (
                <img src={cell.url} alt="" className="absolute inset-0 w-full h-full object-cover" />
              )}
            </div>
          ) : null,
        )}
      </div>
      {/* Dark overlay to keep content readable */}
      <div className="absolute inset-0 bg-black/60" />
    </div>
  );
});

export function NowPlayingPage() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const outerRef = useRef<HTMLDivElement>(null);
  const queue = usePlaybackStore((s) => s.queue);
  const currentIndex = usePlaybackStore((s) => s.currentIndex);
  const isPlaying = usePlaybackStore((s) => s.isPlaying);
  const track = usePlaybackStore((s) => {
    if (s.individualTrack) return s.individualTrack;
    if (s.currentIndex >= 0 && s.currentIndex < s.queue.length) return s.queue[s.currentIndex];
    return null;
  });
  const replaceQueue = usePlaybackStore((s) => s.replaceQueue);
  const removeFromQueue = usePlaybackStore((s) => s.removeFromQueue);
  const clearQueue = usePlaybackStore((s) => s.clearQueue);
  const playbackRatio = useArtworkSettings((s) => s.playbackRatio);
  const queueOpacity = useArtworkSettings((s) => s.queueOpacity);
  const overlayDuration = useArtworkSettings((s) => s.overlayDuration);
  const overlaySize = useArtworkSettings((s) => s.overlaySize);
  const queueClock = useArtworkSettings((s) => s.queueClock);
  const currentTime = usePlaybackStore((s) => s.currentTime);
  const fullscreenMode = usePlaybackStore((s) => s.fullscreenMode);
  const setFullscreenMode = usePlaybackStore((s) => s.setFullscreenMode);
  const individualTrack = usePlaybackStore((s) => s.individualTrack);
  const stopIndividual = usePlaybackStore((s) => s.stopIndividual);

  const [videoHovered, setVideoHovered] = useState(false);
  const [_videoAspect, setVideoAspect] = useState("16 / 9");
  const [nativeWidth, setNativeWidth] = useState(1920);
  const [nativeHeight, setNativeHeight] = useState(1080);
  const [boxSize, setBoxSize] = useState<{ w: number; h: number }>({ w: 0, h: 0 });

  // ── Metadata overlay state ──
  const [overlayVisible, setOverlayVisible] = useState(false);
  const [overlayDetail, setOverlayDetail] = useState<VideoItemDetail | null>(null);
  const [overlayFading, setOverlayFading] = useState(false);
  const overlayTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const overlayFadeTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const lastOverlayTrackRef = useRef<number | null>(null);

  const isFullscreen = fullscreenMode !== "off";
  const isVideoOnly = fullscreenMode === "video";

  // ── Fetch full video detail for metadata overlay when track changes ──
  useEffect(() => {
    if (!track || overlayDuration <= 0) {
      setOverlayVisible(false);
      setOverlayDetail(null);
      return;
    }
    // Only trigger on track change
    if (lastOverlayTrackRef.current === track.videoId) return;
    lastOverlayTrackRef.current = track.videoId;

    // Clear any existing timers
    if (overlayTimerRef.current) clearTimeout(overlayTimerRef.current);
    if (overlayFadeTimerRef.current) clearTimeout(overlayFadeTimerRef.current);

    // Fetch the full video detail
    libraryApi.get(track.videoId).then((detail) => {
      setOverlayDetail(detail);
      setOverlayFading(false);
      setOverlayVisible(true);

      // Start fade-out 2s before end
      const fadeStart = Math.max(0, (overlayDuration - 2)) * 1000;
      overlayFadeTimerRef.current = setTimeout(() => {
        setOverlayFading(true);
      }, fadeStart);

      // Hide completely after duration
      overlayTimerRef.current = setTimeout(() => {
        setOverlayVisible(false);
        setOverlayFading(false);
      }, overlayDuration * 1000);
    }).catch(() => {});

    return () => {
      if (overlayTimerRef.current) clearTimeout(overlayTimerRef.current);
      if (overlayFadeTimerRef.current) clearTimeout(overlayFadeTimerRef.current);
    };
  }, [track?.videoId, overlayDuration]);

  // Sync play/pause with store
  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    if (isPlaying) {
      el.play().catch(() => {});
    } else {
      el.pause();
    }
  }, [isPlaying]);

  // Sync seek position
  useEffect(() => {
    const unsub = usePlaybackStore.subscribe((state) => {
      const el = videoRef.current;
      if (!el) return;
      if (Math.abs(el.currentTime - state.currentTime) > 0.3) {
        el.currentTime = state.currentTime;
      }
    });
    return unsub;
  }, []);

  // Immediate sync when video starts playing
  const handleVideoPlaying = useCallback(() => {
    const el = videoRef.current;
    if (!el) return;
    const audioTime = usePlaybackStore.getState().currentTime;
    if (Math.abs(el.currentTime - audioTime) > 0.05) {
      el.currentTime = audioTime;
    }
  }, []);

  // Detect native video aspect ratio from metadata
  const handleLoadedMetadata = useCallback(() => {
    const el = videoRef.current;
    if (!el || !el.videoWidth || !el.videoHeight) return;
    setVideoAspect(`${el.videoWidth} / ${el.videoHeight}`);
    setNativeWidth(el.videoWidth);
    setNativeHeight(el.videoHeight);
  }, []);

  // ── Playlist/individual track conflict: stop individual when it ends ──
  useEffect(() => {
    if (!individualTrack) return;
    const el = videoRef.current;
    if (!el) return;
    const onEnded = () => stopIndividual();
    el.addEventListener("ended", onEnded);
    return () => el.removeEventListener("ended", onEnded);
  }, [individualTrack, stopIndividual]);

  const QUEUE_WIDTH = 320;

  // Compute the bounding container aspect ratio (always 16:9)
  // and the video's position within it
  const containerAR = 16 / 9;
  const videoAR = nativeWidth / nativeHeight;

  // ── ResizeObserver: compute 16:9 box pixel dimensions to fit within available space ──
  useEffect(() => {
    const el = outerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      const availW = Math.max(0, width - QUEUE_WIDTH);
      const availH = height;
      let bw: number, bh: number;
      if (availH * 16 / 9 <= availW) {
        bh = availH;
        bw = availH * 16 / 9;
      } else {
        bw = availW;
        bh = availW * 9 / 16;
      }
      setBoxSize({ w: Math.round(bw), h: Math.round(bh) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Queue height matches the actual video render area inside the 16:9 box
  const videoRenderH = videoAR > containerAR
    ? boxSize.w / videoAR   // wide video: fills box width, shorter
    : boxSize.h;            // narrow or 16:9: fills box height

  // ── Mode 2: Video-only fullscreen ──
  if (isVideoOnly) {
    return (
      <div className="relative flex items-center justify-center h-screen w-screen bg-black">
        {track ? (
          <video
            ref={videoRef}
            key={track.videoId}
            src={playbackApi.streamUrl(track.videoId)}
            className="h-full w-full object-contain"
            autoPlay={isPlaying}
            controls={false}
            disablePictureInPicture
            muted
            onPlaying={handleVideoPlaying}
            onLoadedMetadata={handleLoadedMetadata}
          />
        ) : (
          <div className="flex items-center justify-center text-white/50 text-lg">
            Nothing playing
          </div>
        )}

        {/* ── Metadata overlay ── */}
        {overlayVisible && overlayDetail && track && (
          <MetadataOverlay
            detail={overlayDetail}
            track={track}
            fading={overlayFading}
            opacity={queueOpacity}
            overlaySize={overlaySize}
            overlayDuration={overlayDuration}
          />
        )}

        <FullscreenControls />
      </div>
    );
  }

  // ── Mode 1: Theater fullscreen (artwork bg + video + queue, no chrome) ──
  // Also used for normal (non-fullscreen) layout
  return (
    <div className="relative flex h-full overflow-hidden">
      {/* Animated artwork grid behind everything */}
      <ArtworkBackground />

      {/* Content layer — centres video + queue as a single block */}
      <div className="relative z-10 flex items-center justify-center h-full w-full p-6">
        {/* Outer wrapper: measured by ResizeObserver to compute 16:9 box size */}
        <div
          ref={outerRef}
          className="flex items-center justify-center w-full"
          style={{
            height: `calc((${isFullscreen ? "100vh - 48px" : "100vh - 160px"}) * ${playbackRatio / 100})`,
          }}
        >
          {/* ── 16:9 bounding box — pixel-sized to always fit ── */}
          <div
            className="relative flex items-center justify-center rounded-l-lg overflow-hidden flex-shrink-0"
            style={{ width: boxSize.w, height: boxSize.h }}
            onMouseEnter={() => setVideoHovered(true)}
            onMouseLeave={() => setVideoHovered(false)}
          >
            {track ? (
              <video
                ref={videoRef}
                key={track.videoId}
                src={playbackApi.streamUrl(track.videoId)}
                className="w-full h-full object-contain"
                autoPlay={isPlaying}
                controls={false}
                disablePictureInPicture
                muted
                onPlaying={handleVideoPlaying}
                onLoadedMetadata={handleLoadedMetadata}
              />
            ) : (
              <div className="flex items-center justify-center text-white/50 text-lg h-full w-full">
                Nothing playing
              </div>
            )}

            {/* ── Metadata overlay ── */}
            {overlayVisible && overlayDetail && track && (
              <MetadataOverlay
                detail={overlayDetail}
                track={track}
                fading={overlayFading}
                opacity={queueOpacity}
                overlaySize={overlaySize}
                overlayDuration={overlayDuration}
              />
            )}

            {/* Fullscreen buttons — visible on hover */}
            <div
              className={`absolute top-3 right-3 flex flex-col gap-1.5 z-20 transition-opacity duration-200 ${
                videoHovered ? "opacity-100" : "opacity-0 pointer-events-none"
              }`}
            >
              <Tooltip content="Theatre mode — expand the video to fill the page width">
              <button
                onClick={() => setFullscreenMode(fullscreenMode === "theater" ? "off" : "theater")}
                className="flex h-8 w-8 items-center justify-center rounded-full bg-black/60 text-white/80 hover:text-white hover:bg-black/80 transition-colors"
              >
                <Monitor size={15} />
              </button>
              </Tooltip>
              <Tooltip content="Video fullscreen — make the video fill the entire screen">
              <button
                onClick={() => { const m = usePlaybackStore.getState().fullscreenMode; setFullscreenMode(m === "video" ? "off" : "video"); }}
                className="flex h-8 w-8 items-center justify-center rounded-full bg-black/60 text-white/80 hover:text-white hover:bg-black/80 transition-colors"
              >
                <Maximize size={15} />
              </button>
              </Tooltip>
            </div>
          </div>

          {/* ── Queue panel — attached to video edge, matches video height ── */}
          <div
            className="flex flex-col border-l border-white/10 rounded-r-lg overflow-hidden flex-shrink-0"
            style={{
              width: QUEUE_WIDTH,
              height: videoRenderH,
              backgroundColor: `rgba(0, 0, 0, ${queueOpacity / 100})`,
              backdropFilter: "blur(8px)",
            }}
          >
            {/* Queue header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 flex-shrink-0">
              <span className="text-sm font-semibold text-white">
                Queue{queue.length > 0 && ` (${queue.length})`}
              </span>
              <div className="flex items-center gap-3">
                {queueClock && <SystemClock />}
                {queue.length > 0 && (
                  <button
                    onClick={clearQueue}
                    className="text-xs text-white/50 hover:text-danger transition-colors"
                  >
                    Clear
                  </button>
                )}
              </div>
            </div>

            {/* Queue list — auto-scroll to keep current track centred */}
            <QueueList
              queue={queue}
              currentIndex={currentIndex}
              onPlay={(idx) => replaceQueue(queue, idx)}
              onRemove={removeFromQueue}
              queueClock={queueClock}
              currentTime={currentTime}
            />
          </div>
        </div>
      </div>

      {/* Fullscreen hover controls (theater mode only) */}
      {isFullscreen && <FullscreenControls />}
    </div>
  );
}

// ── Artwork URL with priority: poster > album_thumb > artist_thumb > video_thumb ──
// Only trust poster if provenance is from wiki, MusicBrainz/CAA, or user upload
const TRUSTED_POSTER_PROVENANCE = new Set([
  "wikipedia_scrape", "artwork_pipeline", "coverartarchive",
  "scraper", "manual_thumbnail",
]);
function getOverlayArtworkUrl(detail: VideoItemDetail): string | null {
  const priority = ["poster", "album_thumb", "artist_thumb", "video_thumb"];
  for (const type of priority) {
    const asset = detail.media_assets?.find(
      (a) => a.asset_type === type && a.status !== "invalid" && a.status !== "missing"
    );
    if (!asset) continue;
    if (type === "poster" && !TRUSTED_POSTER_PROVENANCE.has(asset.provenance ?? "")) continue;
    if (type === "poster") return playbackApi.posterUrl(detail.id);
    if (type === "video_thumb") return `/api/playback/thumb/${detail.id}`;
    return playbackApi.artworkUrl(detail.id, type);
  }
  return null;
}

// ── Read-only star display (scales with container) ──
function Stars({ value, label }: { value: number; label: string }) {
  return (
    <span className="inline-flex items-center gap-[0.4em]">
      <span className="text-[0.6em] text-white/40 w-[2.5em]">{label}</span>
      <span className="inline-flex gap-[0.1em]">
        {[1, 2, 3, 4, 5].map((s) => (
          <svg key={s} viewBox="0 0 20 20" className={`w-[0.75em] h-[0.75em] ${s <= value ? "text-accent" : "text-white/20"}`}>
            <path fill="currentColor" d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.286 3.957a1 1 0 00.95.69h4.162c.969 0 1.371 1.24.588 1.81l-3.37 2.448a1 1 0 00-.364 1.118l1.287 3.957c.3.921-.755 1.688-1.54 1.118l-3.37-2.448a1 1 0 00-1.176 0l-3.37 2.448c-.784.57-1.838-.197-1.539-1.118l1.287-3.957a1 1 0 00-.364-1.118L2.063 9.384c-.783-.57-.38-1.81.588-1.81h4.162a1 1 0 00.95-.69l1.286-3.957z" />
          </svg>
        ))}
      </span>
    </span>
  );
}

// ── Auto-scrolling text for long descriptions ──
function ScrollingText({ text, className, overlayDuration = 30 }: { text: string; className?: string; overlayDuration?: number }) {
  const outerRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const outer = outerRef.current;
    const inner = innerRef.current;
    if (!outer || !inner) return;

    let anim: Animation | null = null;

    const start = () => {
      const ov = inner.scrollHeight - outer.clientHeight;
      if (ov <= 5) return;

      // Hold 5s at top, scroll for (duration - 10)s, hold 5s at bottom
      const totalMs = overlayDuration * 1000;
      const holdPct = 5000 / totalMs; // fraction of total for 5s hold

      anim = inner.animate(
        [
          { transform: "translateY(0)", offset: 0 },
          { transform: "translateY(0)", offset: Math.min(holdPct, 0.2) },
          { transform: `translateY(-${ov}px)`, offset: Math.max(1 - holdPct, 0.8) },
          { transform: `translateY(-${ov}px)`, offset: 1 },
        ],
        { duration: totalMs, fill: "forwards", easing: "linear" },
      );
    };

    // Wait a frame for layout to settle
    const raf = requestAnimationFrame(start);
    return () => {
      cancelAnimationFrame(raf);
      anim?.cancel();
    };
  }, [text, overlayDuration]);

  return (
    <div ref={outerRef} className="overflow-hidden flex-1 min-h-0 relative">
      <div ref={innerRef} className={className}>
        {text}
      </div>
    </div>
  );
}

// ── Metadata overlay card ──
function MetadataOverlay({
  detail,
  track: _track,
  fading,
  opacity,
  overlaySize,
  overlayDuration,
}: {
  detail: VideoItemDetail;
  track: PlaybackTrack;
  fading: boolean;
  opacity: number;
  overlaySize: number;
  overlayDuration: number;
}) {
  const artUrl = getOverlayArtworkUrl(detail);
  const genres = detail.genres?.map((g) => g.name) ?? [];

  // Measure actual pixel height to scale text proportionally
  const boxRef = useRef<HTMLDivElement>(null);
  const [baseFontPx, setBaseFontPx] = useState(16);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const measure = () => {
      // Use ~8% of container height as base font, clamped 12–32px
      const h = el.clientHeight;
      setBaseFontPx(Math.min(32, Math.max(12, h * 0.08)));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div
      ref={boxRef}
      className={`absolute z-30 transition-opacity duration-[2000ms] ${
        fading ? "opacity-0" : "opacity-100"
      }`}
      style={{
        bottom: 0,
        left: 0,
        right: 0,
        height: `${overlaySize}%`,
      }}
    >
      <div
        className="flex items-stretch h-full mx-4 mb-4 rounded-lg overflow-hidden border border-white/10"
        style={{
          backgroundColor: `rgba(0, 0, 0, ${opacity / 100})`,
          backdropFilter: "blur(12px)",
          fontSize: `${baseFontPx}px`,
        }}
      >
        {/* Artwork + Metadata (max 50% width) */}
        <div className="flex items-stretch flex-shrink-0" style={{ maxWidth: "50%" }}>
          {/* Artwork */}
          {artUrl && (
            <img
              src={artUrl}
              alt=""
              className="h-full aspect-square object-cover flex-shrink-0"
            />
          )}

          {/* Metadata column */}
          <div className="flex-shrink min-w-0 px-[1em] py-[0.75em] flex flex-col justify-center gap-[0.15em]" style={{ minWidth: "11em" }}>
            <p className="text-[1em] font-bold text-white truncate">{detail.title}</p>
            <p className="text-[0.8em] text-accent truncate">{detail.artist}</p>
            {detail.album && (
              <p className="text-[0.8em] text-white/60 truncate">{detail.album}</p>
            )}
            {detail.year && (
              <p className="text-[0.75em] text-white/50">{detail.year}</p>
            )}
            {genres.length > 0 && (
              <p className="text-[0.75em] text-white/40 truncate">{genres.join(", ")}</p>
            )}
            {detail.version_type && detail.version_type !== "normal" && (
              <p className="text-[0.6em] text-white/40 uppercase tracking-wide">{detail.version_type}</p>
            )}
            <div className="flex flex-col gap-[0.15em] mt-[0.3em]">
              <Stars value={detail.song_rating ?? 3} label="Song" />
              <Stars value={detail.video_rating ?? 3} label="Video" />
            </div>
          </div>
        </div>

        {/* Description column */}
        {detail.plot && (
          <div className="flex-1 min-w-0 px-[1em] py-[0.75em] border-l border-white/10 flex flex-col">
            <p className="text-[0.6em] font-semibold text-white/50 uppercase tracking-wide mb-[0.3em] flex-shrink-0">Description</p>
            <ScrollingText
              text={detail.plot}
              className="text-[0.8em] text-white/70 leading-relaxed"
              overlayDuration={overlayDuration}
            />
          </div>
        )}
      </div>
    </div>
  );
}

// ── System clock for queue header ──
function SystemClock() {
  const [time, setTime] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <span className="text-[11px] font-mono text-accent tabular-nums">
      {time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
    </span>
  );
}

// ── Format seconds-since-midnight into HH:MM ──
function formatTime(date: Date): string {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// ── Queue list with auto-scroll to keep current track centred ──
function QueueList({
  queue,
  currentIndex,
  onPlay,
  onRemove,
  queueClock,
  currentTime,
}: {
  queue: PlaybackTrack[];
  currentIndex: number;
  onPlay: (idx: number) => void;
  onRemove: (idx: number) => void;
  queueClock: boolean;
  currentTime: number;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const currentRowRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to keep the current track centred in the visible area
  useEffect(() => {
    const row = currentRowRef.current;
    const container = listRef.current;
    if (!row || !container) return;

    // Use rAF to ensure layout has settled before measuring
    const raf = requestAnimationFrame(() => {
      const containerH = container.clientHeight;
      const rowTop = row.offsetTop;
      const rowH = row.offsetHeight;

      // If current track is near the top, snap to 0 instantly to avoid partial clipping
      if (rowTop < containerH / 2) {
        container.scrollTop = 0;
      } else {
        const targetScroll = rowTop - containerH / 2 + rowH / 2;
        container.scrollTo({ top: targetScroll, behavior: "smooth" });
      }
    });
    return () => cancelAnimationFrame(raf);
  }, [currentIndex]);

  // Compute estimated start times for each track
  const startTimes = (() => {
    if (!queueClock) return [];
    const now = Date.now();
    const currentTrack = queue[currentIndex];
    const remaining = currentTrack?.duration
      ? Math.max(0, (currentTrack.duration ?? 0) - currentTime)
      : 0;
    const times: Date[] = [];
    let cumulative = remaining;
    for (let i = 0; i < queue.length; i++) {
      if (i <= currentIndex) {
        times.push(new Date(now)); // current or past
      } else {
        times.push(new Date(now + cumulative * 1000));
        cumulative += queue[i].duration ?? 0;
      }
    }
    return times;
  })();

  if (queue.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-white/40 text-sm">
        Queue is empty
      </div>
    );
  }

  return (
    <div ref={listRef} className="flex-1 overflow-y-auto min-h-0 relative">
      {queue.map((t, idx) => (
        <QueueRow
          key={`${t.videoId}-${idx}`}
          ref={idx === currentIndex ? currentRowRef : undefined}
          track={t}
          index={idx}
          isCurrent={idx === currentIndex}
          onPlay={() => onPlay(idx)}
          onRemove={() => onRemove(idx)}
          startTime={queueClock && idx > currentIndex ? startTimes[idx] : undefined}
        />
      ))}
    </div>
  );
}

import { forwardRef } from "react";

const QueueRow = forwardRef<
  HTMLDivElement,
  {
    track: PlaybackTrack;
    index: number;
    isCurrent: boolean;
    onPlay: () => void;
    onRemove: () => void;
    startTime?: Date;
  }
>(function QueueRow({ track, index, isCurrent, onPlay, onRemove, startTime }, ref) {
  return (
    <div
      ref={ref}
      className={`flex items-center gap-2 px-2 py-2 group cursor-pointer hover:bg-white/10 transition-colors ${
        isCurrent ? "bg-white/15 border-l-2 border-accent" : ""
      }`}
      onClick={onPlay}
    >
      {/* Index / play icon */}
      <span className="w-5 text-center text-xs text-white/50 flex-shrink-0">
        {isCurrent ? (
          <Play size={13} className="text-accent inline" fill="currentColor" />
        ) : (
          index + 1
        )}
      </span>

      {/* Remove */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onRemove();
        }}
        className="flex-shrink-0 p-0.5 text-white/25 hover:text-danger transition-colors"
      >
        <Tooltip content="Remove from queue">
        <X size={11} />
        </Tooltip>
      </button>

      {/* Poster */}
      {track.hasPoster ? (
        <img
          src={playbackApi.posterUrl(track.videoId)}
          alt=""
          className="h-9 w-9 rounded object-cover flex-shrink-0"
        />
      ) : (
        <div className="h-9 w-9 rounded bg-white/10 flex items-center justify-center flex-shrink-0">
          <Play size={12} className="text-white/40" />
        </div>
      )}

      {/* Info */}
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium text-white truncate">{track.artist}</p>
        <p className="text-[11px] text-white/60 truncate">{track.title}</p>
      </div>

      {/* Start time (when queue clock enabled) */}
      {startTime && (
        <span className="text-[11px] font-mono text-white/50 tabular-nums flex-shrink-0">
          {formatTime(startTime)}
        </span>
      )}
    </div>
  );
});
