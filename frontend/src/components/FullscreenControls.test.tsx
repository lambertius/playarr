import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { FullscreenControls } from "./FullscreenControls";
import { usePlaybackStore } from "@/stores/playbackStore";

describe("TV/cast transport", () => {
  beforeEach(() => {
    usePlaybackStore.getState().clearQueue();
    usePlaybackStore.getState().replaceQueue([
      { videoId: 1, artist: "A", title: "One", hasPoster: false },
      { videoId: 2, artist: "B", title: "Two", hasPoster: false },
    ]);
  });

  it("offers three 72px targets with directional focus navigation", async () => {
    const user = userEvent.setup();
    render(<FullscreenControls profile="tv" />);
    const previous = screen.getByRole("button", { name: "Previous track" });
    const random = screen.getByRole("button", { name: "Random track" });
    const next = screen.getByRole("button", { name: "Next track" });
    expect(previous).toHaveClass("h-[72px]", "w-[72px]");
    previous.focus();
    await user.keyboard("{ArrowRight}"); expect(random).toHaveFocus();
    await user.keyboard("{ArrowDown}"); expect(next).toHaveFocus();
    await user.keyboard("{ArrowRight}"); expect(previous).toHaveFocus();
  });
});
