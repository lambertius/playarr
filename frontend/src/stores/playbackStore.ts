import { create } from "zustand";

export type RepeatMode = "off" | "all" | "one";
export type FullscreenMode = "off" | "theater" | "video";

export interface PlaybackTrack {
  videoId: number;
  artist: string;
  title: string;
  duration?: number; // seconds, from quality_signature
  hasPoster: boolean;
  playCount?: number;
}

interface PlaybackState {
  // ── Queue ──
  queue: PlaybackTrack[];
  currentIndex: number;

  // ── Transport ──
  isPlaying: boolean;
  currentTime: number;
  duration: number;

  // ── Modes ──
  shuffle: boolean;
  repeat: RepeatMode;
  fullscreenMode: FullscreenMode;

  // ── Individual playback pause-over ──
  /** When a video is played individually while a playlist runs, the playlist pauses. */
  pausedForIndividual: boolean;
  individualTrack: PlaybackTrack | null;

  // ── Actions ──
  play: (track: PlaybackTrack) => void;
  playNext: (track: PlaybackTrack) => void;
  addToQueue: (track: PlaybackTrack) => void;
  addMultipleToQueue: (tracks: PlaybackTrack[]) => void;
  replaceQueue: (tracks: PlaybackTrack[], startIndex?: number) => void;
  removeFromQueue: (index: number) => void;
  clearQueue: () => void;

  togglePlay: () => void;
  pause: () => void;
  resume: () => void;
  stop: () => void;
  next: () => void;
  prev: () => void;
  seekTo: (time: number) => void;
  setCurrentTime: (time: number) => void;
  setDuration: (dur: number) => void;

  toggleShuffle: () => void;
  cycleRepeat: () => void;

  playIndividual: (track: PlaybackTrack) => void;
  stopIndividual: () => void;

  setFullscreenMode: (mode: FullscreenMode) => void;
  cycleFullscreen: () => void;
  exitFullscreen: () => void;

  // ── Derived ──
  currentTrack: () => PlaybackTrack | null;
}

/** Weighted shuffle helper: pick next random index, favouring lower playCount */
function weightedRandomIndex(queue: PlaybackTrack[], exclude: number): number {
  if (queue.length <= 1) return 0;
  const weights = queue.map((t, i) =>
    i === exclude ? 0 : 1 / (1 + (t.playCount ?? 0)),
  );
  const total = weights.reduce((a, b) => a + b, 0);
  if (total === 0) {
    // fallback: uniform random excluding current
    let idx: number;
    do { idx = Math.floor(Math.random() * queue.length); } while (idx === exclude);
    return idx;
  }
  let r = Math.random() * total;
  for (let i = 0; i < weights.length; i++) {
    r -= weights[i];
    if (r <= 0) return i;
  }
  return queue.length - 1;
}

export const usePlaybackStore = create<PlaybackState>((set, get) => ({
  queue: [],
  currentIndex: -1,
  isPlaying: false,
  currentTime: 0,
  duration: 0,
  shuffle: false,
  repeat: "off",
  fullscreenMode: "off",
  pausedForIndividual: false,
  individualTrack: null,

  currentTrack: () => {
    const { individualTrack, queue, currentIndex } = get();
    if (individualTrack) return individualTrack;
    if (currentIndex >= 0 && currentIndex < queue.length) return queue[currentIndex];
    return null;
  },

  play: (track) =>
    set({
      queue: [track],
      currentIndex: 0,
      isPlaying: true,
      currentTime: 0,
      duration: track.duration ?? 0,
      pausedForIndividual: false,
      individualTrack: null,
    }),

  playNext: (track) =>
    set((s) => {
      const q = [...s.queue];
      const insertAt = s.currentIndex + 1;
      q.splice(insertAt, 0, track);
      return { queue: q };
    }),

  addToQueue: (track) =>
    set((s) => ({ queue: [...s.queue, track] })),

  addMultipleToQueue: (tracks) =>
    set((s) => ({ queue: [...s.queue, ...tracks] })),

  replaceQueue: (tracks, startIndex = 0) =>
    set({
      queue: tracks,
      currentIndex: startIndex,
      isPlaying: true,
      currentTime: 0,
      duration: tracks[startIndex]?.duration ?? 0,
      pausedForIndividual: false,
      individualTrack: null,
    }),

  removeFromQueue: (index) =>
    set((s) => {
      const q = [...s.queue];
      q.splice(index, 1);
      let ci = s.currentIndex;
      if (index < ci) ci--;
      else if (index === ci) ci = Math.min(ci, q.length - 1);
      return { queue: q, currentIndex: ci };
    }),

  clearQueue: () =>
    set({
      queue: [],
      currentIndex: -1,
      isPlaying: false,
      currentTime: 0,
      duration: 0,
      pausedForIndividual: false,
      individualTrack: null,
    }),

  togglePlay: () =>
    set((s) => ({ isPlaying: !s.isPlaying })),

  pause: () => set({ isPlaying: false }),
  resume: () => set({ isPlaying: true }),

  stop: () =>
    set({
      isPlaying: false,
      currentTime: 0,
      pausedForIndividual: false,
      individualTrack: null,
    }),

  next: () =>
    set((s) => {
      if (s.individualTrack) {
        // Stop individual, resume playlist
        return {
          individualTrack: null,
          pausedForIndividual: false,
          isPlaying: s.queue.length > 0,
          currentTime: 0,
        };
      }
      if (s.queue.length === 0) return {};
      let nextIdx: number;
      if (s.shuffle) {
        nextIdx = weightedRandomIndex(s.queue, s.currentIndex);
      } else {
        nextIdx = s.currentIndex + 1;
        if (nextIdx >= s.queue.length) {
          if (s.repeat === "all") nextIdx = 0;
          else return { isPlaying: false };
        }
      }
      return {
        currentIndex: nextIdx,
        currentTime: 0,
        duration: s.queue[nextIdx]?.duration ?? 0,
        isPlaying: true,
      };
    }),

  prev: () =>
    set((s) => {
      if (s.individualTrack) {
        return { currentTime: 0 };
      }
      if (s.queue.length === 0) return {};
      // If > 3s into the track, restart it
      if (s.currentTime > 3) return { currentTime: 0 };
      let prevIdx = s.currentIndex - 1;
      if (prevIdx < 0) {
        if (s.repeat === "all") prevIdx = s.queue.length - 1;
        else return { currentTime: 0 };
      }
      return {
        currentIndex: prevIdx,
        currentTime: 0,
        duration: s.queue[prevIdx]?.duration ?? 0,
        isPlaying: true,
      };
    }),

  seekTo: (time) => set({ currentTime: time }),
  setCurrentTime: (time) => set({ currentTime: time }),
  setDuration: (dur) => {
    // Only accept finite, positive values — streaming endpoints often
    // report NaN/Infinity/0 until the full file is buffered.
    if (!Number.isFinite(dur) || dur <= 0) return;
    // Don't shrink duration — the DB value set by replaceQueue/next/prev
    // is authoritative.  Piped FFmpeg streams report partial durations
    // that grow as data buffers; accepting smaller values would make the
    // progress bar jump.
    const current = get().duration;
    if (current > 0 && dur < current * 0.95) return;
    set({ duration: dur });
  },

  toggleShuffle: () => set((s) => ({ shuffle: !s.shuffle })),

  cycleRepeat: () =>
    set((s) => {
      const modes: RepeatMode[] = ["off", "all", "one"];
      const next = modes[(modes.indexOf(s.repeat) + 1) % modes.length];
      return { repeat: next };
    }),

  playIndividual: (track) =>
    set((s) => ({
      pausedForIndividual: s.queue.length > 0 && s.isPlaying,
      individualTrack: track,
      isPlaying: true,
      currentTime: 0,
      duration: track.duration ?? 0,
    })),

  stopIndividual: () =>
    set((s) => ({
      individualTrack: null,
      isPlaying: s.pausedForIndividual,
      pausedForIndividual: false,
      currentTime: 0,
    })),

  setFullscreenMode: (mode) => {
    set({ fullscreenMode: mode });
    if (mode !== "off" && !document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else if (mode === "off" && document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    }
  },
  cycleFullscreen: () => {
    const s = get();
    const modes: FullscreenMode[] = ["off", "theater", "video"];
    const next = modes[(modes.indexOf(s.fullscreenMode) + 1) % modes.length];
    set({ fullscreenMode: next });
    if (next !== "off" && !document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else if (next === "off" && document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    }
  },
  exitFullscreen: () => {
    set({ fullscreenMode: "off" });
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    }
  },
}));
