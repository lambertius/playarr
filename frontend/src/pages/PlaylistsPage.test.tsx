import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PlaylistDetail } from "./PlaylistsPage";

const mocks = vi.hoisted(() => ({
  batchMutate: vi.fn(),
  updateMutate: vi.fn(),
  replaceQueue: vi.fn(),
  setPref: vi.fn(),
}));

const playlist = {
  id: 7,
  stable_id: "playlist-7",
  revision: 3,
  name: "Road trip",
  description: null,
  entry_count: 3,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  entries: [
    { id: 1, occurrence_id: "one", video_id: 11, position: 0, artist: "Zulu", title: "Second", album: "B", year: 2002, has_poster: false, duration_seconds: null },
    { id: 2, occurrence_id: "two", video_id: 12, position: 1, artist: "Alpha", title: "Third", album: "C", year: 2003, has_poster: false, duration_seconds: null },
    { id: 3, occurrence_id: "three", video_id: 13, position: 2, artist: "Mike", title: "First", album: "A", year: 2001, has_poster: false, duration_seconds: null },
  ],
};

vi.mock("@/hooks/queries", () => ({
  usePlaylist: () => ({ data: playlist }),
  useBatchEditPlaylist: () => ({ mutate: mocks.batchMutate, isPending: false }),
  useUpdatePlaylist: () => ({ mutate: mocks.updateMutate, isPending: false }),
  usePlaylists: () => ({ data: [] }),
  useCreatePlaylist: () => ({ mutate: vi.fn() }),
  useDeletePlaylist: () => ({ mutate: vi.fn() }),
}));
vi.mock("@/stores/playbackStore", () => ({
  usePlaybackStore: (selector: (state: { replaceQueue: typeof mocks.replaceQueue }) => unknown) => selector({ replaceQueue: mocks.replaceQueue }),
}));
vi.mock("@/components/Toast", () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock("@/lib/preferences", () => ({ getPref: () => ({}), setPref: mocks.setPref }));
vi.mock("@/lib/api", () => ({ playbackApi: { posterUrl: (id: number) => `/poster/${id}` } }));

function rowTitles() {
  return screen.getAllByLabelText(/Hold Alt and use arrows/).map((row) => within(row).getByText(/First|Second|Third/).textContent);
}

describe("PlaylistDetail", () => {
  beforeEach(() => {
    mocks.batchMutate.mockClear();
    mocks.updateMutate.mockClear();
  });

  it("sorts only bulk-selected tracks and can undo the draft", async () => {
    const user = userEvent.setup();
    render(<PlaylistDetail playlistId={7} onDelete={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Sort selected" })).toBeDisabled();
    await user.click(screen.getByLabelText("Select Zulu — Second"));
    await user.click(screen.getByLabelText("Select Mike — First"));
    await user.click(screen.getByRole("button", { name: "Title" }));

    // Alpha was not selected and therefore remains fixed in the middle slot.
    expect(rowTitles()).toEqual(["First", "Third", "Second"]);
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Undo changes" }));
    expect(rowTitles()).toEqual(["Second", "Third", "First"]);
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("stages bulk removals and commits them through one save", async () => {
    const user = userEvent.setup();
    render(<PlaylistDetail playlistId={7} onDelete={vi.fn()} />);

    await user.click(screen.getByLabelText("Select Zulu — Second"));
    await user.click(screen.getByRole("button", { name: "Remove selected (1)" }));
    expect(screen.queryByText("Second")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(mocks.batchMutate).toHaveBeenCalledWith(
      {
        playlistId: 7,
        expectedRevision: 3,
        orderedOccurrenceIds: ["two", "three"],
        removedOccurrenceIds: ["one"],
      },
      expect.any(Object),
    );
  });

  it("exposes every row as a draggable reorder target", () => {
    render(<PlaylistDetail playlistId={7} onDelete={vi.fn()} />);
    for (const row of screen.getAllByLabelText(/Hold Alt and use arrows/)) {
      expect(row).toHaveAttribute("draggable", "true");
    }
  });
});
