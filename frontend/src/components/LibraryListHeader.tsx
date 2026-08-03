import { LIBRARY_LIST_GRID } from "@/components/VideoRow";

interface LibraryListHeaderProps {
  allSelected: boolean;
  sortBy?: string;
  sortDirection?: "asc" | "desc";
  onToggleAll: () => void;
  onSort: (column: string) => void;
}

const SORTABLE_COLUMNS = [
  { key: "artist", label: "Artist", align: "text-left" },
  { key: "title", label: "Title", align: "text-left" },
  { key: "album", label: "Album", align: "text-left" },
  { key: "year", label: "Year", align: "text-center" },
  { key: "quality", label: "Quality", align: "text-center" },
] as const;

function sortLabel(label: string, active: boolean, direction: "asc" | "desc") {
  return `${label}${active ? direction === "asc" ? " ↑" : " ↓" : ""}`;
}

export function LibraryListHeader({
  allSelected, sortBy, sortDirection = "asc", onToggleAll, onSort,
}: LibraryListHeaderProps) {
  return (
    <div className={`grid ${LIBRARY_LIST_GRID} min-w-[1050px] items-center gap-3 px-4 py-2 border-b border-surface-border text-xs text-text-muted font-medium`}>
      <div>
        <input
          type="checkbox"
          aria-label="Select all videos on this page"
          checked={allSelected}
          onChange={onToggleAll}
          className="h-4 w-4 rounded border-surface-border bg-surface-lighter text-accent focus:ring-accent cursor-pointer accent-[var(--color-accent)]"
        />
      </div>
      <span aria-hidden="true" />
      {SORTABLE_COLUMNS.map(({ key, label, align }) => {
        const active = sortBy === key;
        return (
          <button
            key={key}
            type="button"
            onClick={() => onSort(key)}
            className={`${align} hover:text-text-primary focus-visible:ring-2 focus-visible:ring-accent rounded`}
            aria-sort={active ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}
          >
            {sortLabel(label, active, sortDirection)}
          </button>
        );
      })}
      <span className="text-center">Version</span>
      <button
        type="button"
        onClick={() => onSort("enrichment")}
        className="text-center hover:text-text-primary focus-visible:ring-2 focus-visible:ring-accent rounded"
        aria-sort={sortBy === "enrichment" ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}
      >
        {sortLabel("AI status", sortBy === "enrichment", sortDirection)}
      </button>
      <button
        type="button"
        onClick={() => onSort("created_at")}
        className="text-right hover:text-text-primary focus-visible:ring-2 focus-visible:ring-accent rounded"
        aria-sort={sortBy === "created_at" ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}
      >
        {sortLabel("Added", sortBy === "created_at", sortDirection)}
      </button>
      <span aria-hidden="true" />
    </div>
  );
}
