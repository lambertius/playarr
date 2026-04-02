import { create } from "zustand";

interface FireworksState {
  visible: boolean;
  duration: number; // ms
  show: (durationMs: number) => void;
  hide: () => void;
}

export const useFireworksStore = create<FireworksState>((set) => ({
  visible: false,
  duration: 8000,
  show: (durationMs: number) => set({ visible: true, duration: durationMs }),
  hide: () => set({ visible: false }),
}));
