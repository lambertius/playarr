import { create } from "zustand";
import { getOrPrerender, getCachedFireworksUrl, invalidateFireworksCache } from "@/lib/fireworksPrerenderer";

interface FireworksState {
  visible: boolean;
  duration: number; // ms
  blobUrl: string | null;
  prerendering: boolean;
  show: (durationMs: number) => void;
  hide: () => void;
  /** Kick off background pre-render. Call on app startup & when settings change. */
  prerender: (durationMs: number) => void;
}

export const useFireworksStore = create<FireworksState>((set, get) => ({
  visible: false,
  duration: 8000,
  blobUrl: null,
  prerendering: false,
  show: (durationMs: number) => set({ visible: true, duration: durationMs }),
  hide: () => set({ visible: false }),
  prerender: (durationMs: number) => {
    const cached = getCachedFireworksUrl();
    if (cached && get().duration === durationMs) {
      set({ blobUrl: cached, duration: durationMs });
      return;
    }
    invalidateFireworksCache();
    set({ blobUrl: null, prerendering: true, duration: durationMs });
    getOrPrerender(durationMs).then((url) => {
      set({ blobUrl: url, prerendering: false });
    }).catch(() => {
      set({ prerendering: false });
    });
  },
}));
