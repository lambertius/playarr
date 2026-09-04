import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DataView, type DataViewColumn } from "./DataView";

const setPref = vi.fn();
vi.mock("@/lib/preferences", () => ({
  getPref: () => ({}),
  setPref: (...args: unknown[]) => setPref(...args),
}));

interface Row {
  id: string;
  artist: string;
  year: number;
}

const rows: Row[] = [
  { id: "later", artist: "Zulu", year: 2020 },
  { id: "earlier", artist: "Alpha", year: 1990 },
];
const columns: DataViewColumn<Row>[] = [
  { id: "artist", label: "Artist", width: "2fr", render: (row) => row.artist, sortValue: (row) => row.artist },
  { id: "year", label: "Year", width: "5rem", render: (row) => row.year, sortValue: (row) => row.year, align: "right" },
];

function LocationProbe() {
  return <output aria-label="location">{useLocation().search}</output>;
}

function subject(initialEntry = "/artists?view=list&sort=artist&dir=asc", subjectRows = rows) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/artists" element={<>
          <DataView
            rows={subjectRows}
            rowKey={(row) => row.id}
            columns={columns}
            renderCard={(row) => <article>{row.artist}</article>}
            preferenceKey="artists"
            defaultSort="artist"
            empty={<p>No artists</p>}
          />
          <LocationProbe />
        </>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("DataView", () => {
  beforeEach(() => setPref.mockClear());

  it("uses one grid template for aligned list headers and rows", () => {
    const { container } = subject();
    const grids = Array.from(container.querySelectorAll<HTMLElement>(".grid"));
    expect(grids).toHaveLength(3);
    expect(grids.every((grid) => grid.style.gridTemplateColumns === "2fr 5rem")).toBe(true);
    expect(screen.getAllByText(/Alpha|Zulu/).map((node) => node.textContent)).toEqual(["Alpha", "Zulu"]);
  });

  it("supports keyboard sorting and stores URL query state", async () => {
    const user = userEvent.setup();
    subject();
    const artistHeader = screen.getByRole("button", { name: /Artist/ });
    artistHeader.focus();
    await user.keyboard("{Enter}");

    expect(artistHeader).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByLabelText("location")).toHaveTextContent("sort=artist");
    expect(screen.getByLabelText("location")).toHaveTextContent("dir=desc");
    expect(setPref).toHaveBeenCalled();
    const list = artistHeader.closest<HTMLElement>(".card");
    expect(list).not.toBeNull();
    expect(within(list!).getAllByText(/Alpha|Zulu/).map((node) => node.textContent)).toEqual(["Zulu", "Alpha"]);
  });

  it("switches to grid without losing shareable URL state", async () => {
    const user = userEvent.setup();
    subject();
    await user.click(screen.getByRole("button", { name: "Grid view" }));
    expect(screen.getByLabelText("location")).toHaveTextContent("view=grid");
    expect(screen.getAllByRole("article")).toHaveLength(2);
  });

  it("shows all rows by default and lets the user enable or disable pagination", async () => {
    const user = userEvent.setup();
    const manyRows = Array.from({ length: 30 }, (_, index) => ({
      id: String(index), artist: `Artist ${index}`, year: 2000 + index,
    }));
    subject("/artists?view=grid&sort=artist&dir=asc", manyRows);

    const pageSize = screen.getByRole("combobox", { name: "Page size" });
    expect(pageSize).toHaveValue("0");
    expect(screen.getAllByRole("article")).toHaveLength(30);
    expect(screen.queryByText(/Page 1 of/)).not.toBeInTheDocument();

    await user.selectOptions(pageSize, "25");
    expect(screen.getAllByRole("article")).toHaveLength(25);
    expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();
    expect(screen.getByLabelText("location")).toHaveTextContent("page_size=25");

    await user.selectOptions(pageSize, "0");
    expect(screen.getAllByRole("article")).toHaveLength(30);
    expect(screen.queryByText(/Page 1 of/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("location")).toHaveTextContent("page_size=0");
  });

  it("can place view controls inside the page filter tile", () => {
    render(
      <MemoryRouter initialEntries={["/artists?view=list"]}>
        <Routes>
          <Route path="/artists" element={(
            <DataView
              rows={rows}
              rowKey={(row) => row.id}
              columns={columns}
              renderCard={(row) => <article>{row.artist}</article>}
              preferenceKey="artists"
              defaultSort="artist"
              empty={<p>No artists</p>}
              renderFilterTile={(controls) => <section aria-label="Filters">Shared filters {controls}</section>}
            />
          )} />
        </Routes>
      </MemoryRouter>,
    );

    const filterTile = screen.getByRole("region", { name: "Filters" });
    expect(filterTile).toContainElement(screen.getByLabelText("artists layout"));
    expect(filterTile).toContainElement(screen.getByText("Page size"));
  });
});
