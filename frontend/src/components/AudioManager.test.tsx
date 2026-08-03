import { fireEvent, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AudioManager } from "./AudioManager";
import { usePlaybackStore } from "@/stores/playbackStore";

vi.mock("@/lib/api", () => ({
  playbackApi: {
    streamUrl: (videoId: number) => `/api/playback/stream/${videoId}`,
    killStreams: vi.fn(() => Promise.resolve()),
    recordHistory: vi.fn(() => Promise.resolve()),
  },
}));

describe("AudioManager playback ownership", () => {
  beforeEach(() => {
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
    const store = usePlaybackStore.getState();
    store.clearQueue();
    store.setTvMode(false);
    store.replaceQueue([
      { videoId: 1, artist: "A", title: "One", hasPoster: false },
      { videoId: 2, artist: "B", title: "Two", hasPoster: false },
    ]);
  });

  it("ignores delayed desktop ended events after TV takes ownership", () => {
    const { container, rerender } = render(<AudioManager />);
    const audio = container.querySelector("audio");
    expect(audio).not.toBeNull();

    usePlaybackStore.getState().setTvMode(true);
    rerender(<AudioManager />);
    fireEvent.ended(audio!);

    expect(usePlaybackStore.getState().currentIndex).toBe(0);
    expect(audio).not.toHaveAttribute("src");
  });

  it("advances normally when desktop audio owns playback", () => {
    const { container } = render(<AudioManager />);
    fireEvent.ended(container.querySelector("audio")!);
    expect(usePlaybackStore.getState().currentIndex).toBe(1);
  });
});
