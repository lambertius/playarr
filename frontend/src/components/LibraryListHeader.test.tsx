import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LibraryListHeader } from "./LibraryListHeader";

describe("LibraryListHeader", () => {
  it("renders artist and title as independent sortable columns", async () => {
    const user = userEvent.setup();
    const onSort = vi.fn();
    render(
      <LibraryListHeader
        allSelected={false}
        sortBy="artist"
        sortDirection="asc"
        onToggleAll={vi.fn()}
        onSort={onSort}
      />,
    );

    const artist = screen.getByRole("button", { name: "Artist ↑" });
    const title = screen.getByRole("button", { name: "Title" });
    expect(artist).toHaveAttribute("aria-sort", "ascending");
    expect(title).toHaveAttribute("aria-sort", "none");

    await user.click(title);
    await user.click(screen.getByRole("button", { name: /Year/ }));
    expect(onSort).toHaveBeenNthCalledWith(1, "title");
    expect(onSort).toHaveBeenNthCalledWith(2, "year");
  });

  it("aligns year and quality headers to their centred row columns", () => {
    render(
      <LibraryListHeader
        allSelected={false}
        sortBy="year"
        sortDirection="desc"
        onToggleAll={vi.fn()}
        onSort={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Year ↓" })).toHaveClass("text-center");
    expect(screen.getByRole("button", { name: "Quality" })).toHaveClass("text-center");
  });
});
