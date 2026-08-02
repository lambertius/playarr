import type { PlaylistEntry, PlaylistSortField } from "@/types";

export function sortPlaylistDraft(
  entries: PlaylistEntry[], selected: Set<string>, field: PlaylistSortField,
  direction: "asc" | "desc",
): PlaylistEntry[] {
  const reverse = direction === "desc";
  const valueFor = (entry: PlaylistEntry) => field === "year"
    ? entry.year ?? (reverse ? Number.MIN_SAFE_INTEGER : Number.MAX_SAFE_INTEGER)
    : (entry[field] ?? "").toLocaleLowerCase();
  const compare = (a: PlaylistEntry, b: PlaylistEntry) => {
    const av = valueFor(a); const bv = valueFor(b);
    const result = typeof av === "number" && typeof bv === "number"
      ? av - bv : String(av).localeCompare(String(bv));
    return reverse ? -result : result;
  };
  const next = [...entries];
  if (!selected.size) return next.sort(compare);
  const positions = next.map((entry, index) => selected.has(entry.occurrence_id) ? index : -1)
    .filter((index) => index >= 0);
  const sorted = positions.map((index) => next[index]).sort(compare);
  positions.forEach((position, index) => { next[position] = sorted[index]; });
  return next;
}

export function movePlaylistEntry(
  entries: PlaylistEntry[], from: number, to: number,
): PlaylistEntry[] {
  if (from === to || from < 0 || from >= entries.length || to < 0 || to >= entries.length) return entries;
  const next = [...entries];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}
