import { fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { usePlaybackMediaSession } from "./usePlaybackMediaSession";
import { usePlaybackStore } from "@/stores/playbackStore";

vi.mock("@/hooks/usePlaybackDiagnostics", () => ({
  usePlaybackDiagnostics: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  playbackApi: {
    streamUrl: (videoId: number, transcode: boolean) =>
      `/api/playback/stream/${videoId}?transcode=${transcode ? 1 : 0}`,
    videoOnlyStreamUrl: (videoId: number, transcode: boolean) =>
      `/api/playback/stream/${videoId}/video?transcode=${transcode ? 1 : 0}`,
  },
}));

function Harness() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const track = usePlaybackStore((state) => state.currentTrack());
  const session = usePlaybackMediaSession({
    videoRef,
    track,
    profile: "tv",
    transcode: true,
    videoSurfaceEpoch: 0,
  });
  return (
    <>
      <video
        ref={videoRef}
        onCanPlay={session.handleCanPlay}
        onWaiting={session.markBuffering}
        onStalled={session.markBuffering}
      />
      <output data-testid="state">{session.playbackState}</output>
      <output data-testid="error">{session.playbackError}</output>
      <button onClick={session.retryPlayback}>Retry</button>
    </>
  );
}

describe("TV playback media session", () => {
  let loadMock: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    loadMock = vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
    usePlaybackStore.setState({
      queue: [],
      currentIndex: -1,
      isPlaying: false,
      currentTime: 0,
      duration: 0,
      repeat: "off",
      individualTrack: null,
      pausedForIndividual: false,
    });
    usePlaybackStore.getState().replaceQueue([
      { videoId: 11, artist: "Artist", title: "First", duration: 240, hasPoster: false },
      { videoId: 12, artist: "Artist", title: "Second", duration: 180, hasPoster: false },
    ]);
  });

  it("restores the same occurrence after a buffering refill jumps to zero", () => {
    const { container } = render(<Harness />);
    const video = container.querySelector("video")!;
    const initialOccurrence = usePlaybackStore.getState().queue[0].queueEntryId;

    video.currentTime = 47;
    fireEvent.timeUpdate(video);
    fireEvent.waiting(video);
    expect(screen.getByTestId("state")).toHaveTextContent("buffering");

    video.currentTime = 0;
    fireEvent.timeUpdate(video);

    expect(video.currentTime).toBe(47);
    expect(usePlaybackStore.getState().currentTime).toBe(47);
    expect(usePlaybackStore.getState().currentIndex).toBe(0);
    expect(usePlaybackStore.getState().queue[0].queueEntryId).toBe(initialOccurrence);
    expect(loadMock).toHaveBeenCalledTimes(1);

    fireEvent.canPlay(video);
    expect(screen.getByTestId("state")).toHaveTextContent("playing");
    expect(loadMock).toHaveBeenCalledTimes(1);
  });

  it("does not treat a truncated stream as the end of the track", () => {
    const { container } = render(<Harness />);
    const video = container.querySelector("video")!;

    video.currentTime = 47;
    fireEvent.timeUpdate(video);
    fireEvent.ended(video);

    expect(usePlaybackStore.getState().currentIndex).toBe(0);
    expect(usePlaybackStore.getState().currentTime).toBe(47);
    expect(screen.getByTestId("state")).toHaveTextContent("error");
    expect(screen.getByTestId("error")).toHaveTextContent("stream ended early");

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(loadMock).toHaveBeenCalledTimes(2);
    video.currentTime = 0; // emulate a TV engine discarding the pre-metadata seek
    fireEvent.canPlay(video);
    expect(video.currentTime).toBe(47);
    expect(usePlaybackStore.getState().currentIndex).toBe(0);
  });
});
