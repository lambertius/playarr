import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PartyStartGate } from "./PartyStartGate";

vi.mock("@/lib/api", () => ({
  playlistApi: { list: vi.fn(() => Promise.resolve([])) },
}));

describe("TV/Cast party start panel", () => {
  beforeEach(() => localStorage.clear());

  it("exposes remote-sized audience filters and combines them into the launch", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn();
    render(<PartyStartGate surface="tv" onStart={onStart} />);

    for (const label of ["Normal", "Cover", "Live", "Alternate", "Remix", "Acoustic", "Uncensored", "Exclude 18+"]) {
      expect(screen.getByRole("checkbox", { name: label })).toBeInTheDocument();
    }
    await user.click(screen.getByRole("checkbox", { name: "Normal" }));
    await user.selectOptions(screen.getByLabelText("Minimum song rating"), "3");
    await user.click(screen.getByRole("button", { name: "Start the Party" }));

    expect(onStart).toHaveBeenCalledOnce();
    const choice = onStart.mock.calls[0][0];
    expect(choice.filters.exclude_version_types.split(",")).toEqual(expect.arrayContaining(["normal", "18+"]));
    expect(choice.filters.min_song_rating).toBe(3);
  });

  it("moves focus with remote arrow keys", async () => {
    const user = userEvent.setup();
    render(<PartyStartGate surface="cast" onStart={vi.fn()} />);
    const start = screen.getByRole("button", { name: "Start the Party" });
    expect(start).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("button", { name: "Fullscreen" })).toHaveFocus();
  });
});
