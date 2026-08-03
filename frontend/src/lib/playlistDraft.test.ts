import { describe, expect, it } from "vitest";
import {
  filterPlaylistEntries,
  movePlaylistEntry,
  removePlaylistDraftEntries,
  sortPlaylistDraft,
} from "./playlistDraft";
import type { PlaylistEntry } from "@/types";

const entry = (occurrence_id: string, title: string, year: number): PlaylistEntry => ({
  id: year, occurrence_id, video_id: year, position: year, artist: "Artist",
  title, album: "Album", year, duration_seconds: null, has_poster: false,
});
const rows = [entry("A", "Zulu", 2000), entry("B", "Beta", 2001), entry("C", "Alpha", 2002), entry("D", "Delta", 2003)];

describe("playlist draft reducer", () => {
  it("sorts selected values only within their occupied slots", () => {
    const result = sortPlaylistDraft(rows, new Set(["B", "D"]), "title", "desc");
    expect(result.map((row) => row.occurrence_id)).toEqual(["A", "D", "C", "B"]);
  });

  it("never sorts the whole playlist without a bulk selection", () => {
    expect(sortPlaylistDraft(rows, new Set(), "title", "asc")).toEqual(rows);
    expect(sortPlaylistDraft(rows, new Set(["B"]), "title", "asc")).toEqual(rows);
  });

  it("moves entries to adjacent and boundary positions without mutating server rows", () => {
    expect(movePlaylistEntry(rows, 2, 0).map((row) => row.occurrence_id)).toEqual(["C", "A", "B", "D"]);
    expect(rows.map((row) => row.occurrence_id)).toEqual(["A", "B", "C", "D"]);
  });

  it("filters across the visible library-style columns", () => {
    expect(filterPlaylistEntries(rows, "beta").map((row) => row.occurrence_id)).toEqual(["B"]);
    expect(filterPlaylistEntries(rows, "2002").map((row) => row.occurrence_id)).toEqual(["C"]);
  });

  it("stages bulk removals without mutating the source order", () => {
    const result = removePlaylistDraftEntries(rows, new Set(["A", "C"]));
    expect(result.entries.map((row) => row.occurrence_id)).toEqual(["B", "D"]);
    expect(result.removedOccurrenceIds).toEqual(["A", "C"]);
    expect(rows).toHaveLength(4);
  });
});
