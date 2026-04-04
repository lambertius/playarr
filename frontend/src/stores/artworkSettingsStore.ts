import { create } from "zustand";

export type ArtChangeStyle = "fade" | "flip" | "spin" | "random";

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
}

const STORAGE_KEY = "playarr-artwork-settings";

type Persisted = Pick<ArtworkSettings, "artworkSize" | "scrollDuration" | "changeRate" | "fadeDuration" | "playbackRatio" | "queueOpacity" | "overlayDuration" | "artRepeatPenalty" | "overlaySize" | "queueClock" | "artChangeEnabled" | "artChangeCount" | "artChangeStyle">;

const DEFAULTS: Persisted = { artworkSize: 150, scrollDuration: 60, changeRate: 4, fadeDuration: 1, playbackRatio: 75, queueOpacity: 70, overlayDuration: 30, artRepeatPenalty: 50, overlaySize: 35, queueClock: false, artChangeEnabled: true, artChangeCount: 1, artChangeStyle: "fade" };

function loadDefaults(): Persisted {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch { /* ignore */ }
  return DEFAULTS;
}

function persist(state: Persisted) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function snap(): Persisted {
  const s = useArtworkSettings.getState();
  return { artworkSize: s.artworkSize, scrollDuration: s.scrollDuration, changeRate: s.changeRate, fadeDuration: s.fadeDuration, playbackRatio: s.playbackRatio, queueOpacity: s.queueOpacity, overlayDuration: s.overlayDuration, artRepeatPenalty: s.artRepeatPenalty, overlaySize: s.overlaySize, queueClock: s.queueClock, artChangeEnabled: s.artChangeEnabled, artChangeCount: s.artChangeCount, artChangeStyle: s.artChangeStyle };
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
}));
