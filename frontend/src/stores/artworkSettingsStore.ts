import { create } from "zustand";
import { getPref, setPref } from "@/lib/preferences";

export type ArtChangeStyle = "fade" | "flip" | "spin" | "random";
/** Queue panel auto-hide behaviour in theatre mode. */
export type QueueHideMode = "off" | "per-song" | "auto";

interface ArtworkSettings {
  /** Artwork cell size in pixels */
  artworkSize: number;
  /** Scroll animation duration in seconds (lower = faster) */
  scrollDuration: number;
  /** Artwork swap interval in seconds */
  changeRate: number;
  /** Crossfade transition duration in seconds */
  fadeDuration: number;
  /** Playback area ratio — percentage of screen width (50-95) */
  playbackRatio: number;
  /** Queue panel opacity 10-90% */
  queueOpacity: number;
  /** Metadata overlay display duration in seconds (0 = off, max 90) */
  overlayDuration: number;
  /** Artwork repetition penalty strength 0-100 (0 = off) */
  artRepeatPenalty: number;
  /** Metadata overlay height as percentage of video height (20-60) */
  overlaySize: number;
  /** Show estimated start times in queue panel */
  queueClock: boolean;
  /** Enable artwork tile swapping */
  artChangeEnabled: boolean;
  /** Number of tiles to swap per interval (1-50) */
  artChangeCount: number;
  /** Transition style for artwork swaps */
  artChangeStyle: ArtChangeStyle;
  /** Queue panel auto-hide behaviour in theatre mode */
  queueHideMode: QueueHideMode;
  /** Seconds before the queue fades out in per-song / auto hide modes */
  queueHideDelay: number;
  /** TV/kiosk render resolution (logical height: 1080 / 1440 / 2160) — the /tv
   *  view lays out on this 16:9 canvas then CSS-scales to fit, giving
   *  browser-like artwork density regardless of the low logical viewport a TV
   *  browser reports. */
  tvResolution: number;
  /** Force a server-side compatibility transcode (H.264/AAC) for TV mode. */
  tvTranscode: boolean;
  /** Force a server-side compatibility transcode (H.264/AAC) for browser playback. */
  browserTranscode: boolean;
  /** Force a server-side compatibility transcode (H.264/AAC) for the /cast page. */
  castTranscode: boolean;

  setArtworkSize: (size: number) => void;
  setScrollDuration: (duration: number) => void;
  setChangeRate: (rate: number) => void;
  setFadeDuration: (duration: number) => void;
  setPlaybackRatio: (ratio: number) => void;
  setQueueOpacity: (opacity: number) => void;
  setOverlayDuration: (duration: number) => void;
  setArtRepeatPenalty: (penalty: number) => void;
  setOverlaySize: (size: number) => void;
  setQueueClock: (enabled: boolean) => void;
  setArtChangeEnabled: (enabled: boolean) => void;
  setArtChangeCount: (count: number) => void;
  setArtChangeStyle: (style: ArtChangeStyle) => void;
  setQueueHideMode: (mode: QueueHideMode) => void;
  setQueueHideDelay: (sec: number) => void;
  setTvResolution: (res: number) => void;
  setTvTranscode: (on: boolean) => void;
  setBrowserTranscode: (on: boolean) => void;
  setCastTranscode: (on: boolean) => void;
}

// Server-stored preference group.  LEGACY_KEY is the old browser-only blob,
// read once as the migration fallback the first time this runs post-upgrade.
const PREF_GROUP = "artwork";
const LEGACY_KEY = "playarr-artwork-settings";

type Persisted = Pick<ArtworkSettings, "artworkSize" | "scrollDuration" | "changeRate" | "fadeDuration" | "playbackRatio" | "queueOpacity" | "overlayDuration" | "artRepeatPenalty" | "overlaySize" | "queueClock" | "artChangeEnabled" | "artChangeCount" | "artChangeStyle" | "queueHideMode" | "queueHideDelay" | "tvResolution" | "tvTranscode" | "browserTranscode" | "castTranscode">;

const DEFAULTS: Persisted = { artworkSize: 150, scrollDuration: 60, changeRate: 4, fadeDuration: 1, playbackRatio: 75, queueOpacity: 70, overlayDuration: 30, artRepeatPenalty: 50, overlaySize: 35, queueClock: false, artChangeEnabled: true, artChangeCount: 1, artChangeStyle: "fade", queueHideMode: "off", queueHideDelay: 5, tvResolution: 1080, tvTranscode: false, browserTranscode: false, castTranscode: false };

function loadLegacy(): Persisted {
  try {
    const raw = localStorage.getItem(LEGACY_KEY);
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch { /* ignore */ }
  return DEFAULTS;
}

function loadDefaults(): Persisted {
  return { ...DEFAULTS, ...getPref<Partial<Persisted>>(PREF_GROUP, loadLegacy()) };
}

function persist(state: Persisted) {
  setPref(PREF_GROUP, state);
}

function snap(): Persisted {
  const s = useArtworkSettings.getState();
  return { artworkSize: s.artworkSize, scrollDuration: s.scrollDuration, changeRate: s.changeRate, fadeDuration: s.fadeDuration, playbackRatio: s.playbackRatio, queueOpacity: s.queueOpacity, overlayDuration: s.overlayDuration, artRepeatPenalty: s.artRepeatPenalty, overlaySize: s.overlaySize, queueClock: s.queueClock, artChangeEnabled: s.artChangeEnabled, artChangeCount: s.artChangeCount, artChangeStyle: s.artChangeStyle, queueHideMode: s.queueHideMode, queueHideDelay: s.queueHideDelay, tvResolution: s.tvResolution, tvTranscode: s.tvTranscode, browserTranscode: s.browserTranscode, castTranscode: s.castTranscode };
}

export const useArtworkSettings = create<ArtworkSettings>((set) => ({
  ...loadDefaults(),

  setArtworkSize: (size) => {
    set({ artworkSize: size });
    persist({ ...snap(), artworkSize: size });
  },
  setScrollDuration: (duration) => {
    set({ scrollDuration: duration });
    persist({ ...snap(), scrollDuration: duration });
  },
  setChangeRate: (rate) => {
    set({ changeRate: rate });
    persist({ ...snap(), changeRate: rate });
  },
  setFadeDuration: (duration) => {
    set({ fadeDuration: duration });
    persist({ ...snap(), fadeDuration: duration });
  },
  setPlaybackRatio: (ratio) => {
    set({ playbackRatio: ratio });
    persist({ ...snap(), playbackRatio: ratio });
  },
  setQueueOpacity: (opacity) => {
    set({ queueOpacity: opacity });
    persist({ ...snap(), queueOpacity: opacity });
  },
  setOverlayDuration: (duration) => {
    set({ overlayDuration: duration });
    persist({ ...snap(), overlayDuration: duration });
  },
  setArtRepeatPenalty: (penalty) => {
    set({ artRepeatPenalty: penalty });
    persist({ ...snap(), artRepeatPenalty: penalty });
  },
  setOverlaySize: (size) => {
    set({ overlaySize: size });
    persist({ ...snap(), overlaySize: size });
  },
  setQueueClock: (enabled) => {
    set({ queueClock: enabled });
    persist({ ...snap(), queueClock: enabled });
  },
  setArtChangeEnabled: (enabled) => {
    set({ artChangeEnabled: enabled });
    persist({ ...snap(), artChangeEnabled: enabled });
  },
  setArtChangeCount: (count) => {
    set({ artChangeCount: count });
    persist({ ...snap(), artChangeCount: count });
  },
  setArtChangeStyle: (style) => {
    set({ artChangeStyle: style });
    persist({ ...snap(), artChangeStyle: style });
  },
  setQueueHideMode: (mode) => {
    set({ queueHideMode: mode });
    persist({ ...snap(), queueHideMode: mode });
  },
  setQueueHideDelay: (sec) => {
    set({ queueHideDelay: sec });
    persist({ ...snap(), queueHideDelay: sec });
  },
  setTvResolution: (res) => {
    set({ tvResolution: res });
    persist({ ...snap(), tvResolution: res });
  },
  setTvTranscode: (on) => {
    set({ tvTranscode: on });
    persist({ ...snap(), tvTranscode: on });
  },
  setBrowserTranscode: (on) => {
    set({ browserTranscode: on });
    persist({ ...snap(), browserTranscode: on });
  },
  setCastTranscode: (on) => {
    set({ castTranscode: on });
    persist({ ...snap(), castTranscode: on });
  },
}));
