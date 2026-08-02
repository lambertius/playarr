import { beforeEach, describe, expect, it } from "vitest";
import { usePlaybackStore, type PlaybackTrack } from "./playbackStore";

const track = (title: string): PlaybackTrack => ({ videoId: 7, artist: "Artist", title, hasPoster: false });

describe("playback queue occurrences", () => {
  beforeEach(() => usePlaybackStore.getState().clearQueue());
  it("plays repeated instances of one video as distinct occurrences", () => {
    const store = usePlaybackStore.getState();
    store.replaceQueue([track("First"), track("Second")]);
    const ids = usePlaybackStore.getState().queue.map((item) => item.queueEntryId);
    expect(ids[0]).toBeTruthy(); expect(ids[1]).toBeTruthy(); expect(ids[0]).not.toBe(ids[1]);
    store.next();
    expect(usePlaybackStore.getState().currentTrack()?.title).toBe("Second");
  });
  it("random transport selects a different eligible occurrence", () => {
    const store = usePlaybackStore.getState();
    store.replaceQueue([track("First"), track("Second")]); store.random();
    expect(usePlaybackStore.getState().currentIndex).toBe(1);
  });
});
