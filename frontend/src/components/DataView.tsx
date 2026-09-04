import { useMemo } from "react";
import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { getPref, setPref } from "@/lib/preferences";
import type { ViewMode } from "@/types";
import { ViewToggle } from "@/components/ViewToggle";

export interface DataViewColumn<T> {
  id: string;
  label: string;
  width?: string;
  render: (row: T) => ReactNode;
  sortValue?: (row: T) => string | number | null | undefined;
  align?: "left" | "center" | "right";
}

interface PanelPreferences {
  thumbnailsExpanded?: boolean;
  trackHistoryExpanded?: boolean;
  navViews?: Record<string, ViewMode>;
  navSorts?: Record<string, string>;
  navDirections?: Record<string, "asc" | "desc">;
  navPageSizes?: Record<string, number>;
}

const PAGE_SIZE_OPTIONS = [0, 25, 50, 100, 200];

interface DataViewProps<T> {
  rows: T[];
  rowKey: (row: T) => string | number;
  columns: DataViewColumn<T>[];
  renderCard: (row: T) => ReactNode;
  /** Optional grouped/specialised grid renderer. List view remains shared. */
  renderGrid?: (rows: T[]) => ReactNode;
  preferenceKey: string;
  defaultSort: string;
  defaultDirection?: "asc" | "desc";
  empty: ReactNode;
  /** Places navigation controls inside the page's canonical, always-visible filter tile. */
  renderFilterTile?: (navigationControls: ReactNode) => ReactNode;
}

/** Shared grid/list query, sorting, paging and alignment primitive. */
export function DataView<T>({
  rows, rowKey, columns, renderCard, preferenceKey, defaultSort, defaultDirection = "asc", empty,
  renderFilterTile, renderGrid,
}: DataViewProps<T>) {
  const [params, setParams] = useSearchParams();
  const prefs = getPref<PanelPreferences>("panels", {});
  const view = (params.get("view") as ViewMode | null) ?? prefs.navViews?.[preferenceKey] ?? "grid";
  const sort = params.get("sort") ?? prefs.navSorts?.[preferenceKey] ?? defaultSort;
  const direction = (params.get("dir") as "asc" | "desc" | null) ?? prefs.navDirections?.[preferenceKey] ?? defaultDirection;
  const queryPageSize = params.get("page_size");
  const storedPageSize = prefs.navPageSizes?.[preferenceKey];
  const pageSize = queryPageSize !== null && PAGE_SIZE_OPTIONS.includes(Number(queryPageSize))
    ? Number(queryPageSize)
    : storedPageSize !== undefined && PAGE_SIZE_OPTIONS.includes(storedPageSize)
      ? storedPageSize
      : 0;
  const requestedPage = Number(params.get("page")) || 1;

  const update = (patch: Record<string, string>) => setParams((previous) => {
    const next = new URLSearchParams(previous);
    for (const [key, value] of Object.entries(patch)) next.set(key, value);
    if (!("page" in patch)) next.delete("page");
    return next;
  });
  const savePrefs = (patch: Partial<PanelPreferences>) => setPref("panels", { ...prefs, ...patch });

  const sorted = useMemo(() => {
    const column = columns.find((candidate) => candidate.id === sort && candidate.sortValue);
    if (!column?.sortValue) return rows;
    return [...rows].sort((left, right) => {
      const a = column.sortValue!(left);
      const b = column.sortValue!(right);
      if (a == null) return 1;
      if (b == null) return -1;
      const result = typeof a === "number" && typeof b === "number"
        ? a - b
        : String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
      return direction === "asc" ? result : -result;
    });
  }, [columns, direction, rows, sort]);
  const totalPages = pageSize === 0 ? 1 : Math.max(1, Math.ceil(sorted.length / pageSize));
  const page = Math.min(requestedPage, totalPages);
  const visible = pageSize === 0 ? sorted : sorted.slice((page - 1) * pageSize, page * pageSize);
  const template = columns.map((column) => column.width ?? "minmax(8rem,1fr)").join(" ");

  const changeView = (next: ViewMode) => {
    savePrefs({ navViews: { ...(prefs.navViews ?? {}), [preferenceKey]: next } });
    update({ view: next });
  };
  const changeSort = (id: string) => {
    const nextDirection = sort === id && direction === "asc" ? "desc" : "asc";
    savePrefs({
      navSorts: { ...(prefs.navSorts ?? {}), [preferenceKey]: id },
      navDirections: { ...(prefs.navDirections ?? {}), [preferenceKey]: nextDirection },
    });
    update({ sort: id, dir: nextDirection });
  };
  const changePageSize = (size: number) => {
    savePrefs({ navPageSizes: { ...(prefs.navPageSizes ?? {}), [preferenceKey]: size } });
    update({ page_size: String(size) });
  };

  const navigationControls = (
    <div className="ml-auto flex items-end gap-2">
      <label className="flex flex-col gap-1 text-xs text-text-muted">Page size
        <select value={pageSize} onChange={(event) => changePageSize(Number(event.target.value))} className="input-field py-1 text-xs w-auto">
          {PAGE_SIZE_OPTIONS.map((size) => <option key={size} value={size}>{size === 0 ? "All" : size}</option>)}
        </select>
      </label>
      <ViewToggle value={view} onChange={changeView} label={`${preferenceKey} layout`} />
    </div>
  );
  const controlSurface = renderFilterTile
    ? renderFilterTile(navigationControls)
    : <div className="flex justify-end items-center gap-2 mb-3">{navigationControls}</div>;

  if (!rows.length) return <>{controlSurface}{empty}</>;
  return <div>
    {controlSurface}
    {view === "grid" ? (
      renderGrid ? renderGrid(visible) : <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-4">{visible.map((row) => <div key={rowKey(row)}>{renderCard(row)}</div>)}</div>
    ) : (
      <div className="card p-0 overflow-x-auto">
        <div className="grid min-w-[560px] gap-3 px-4 py-2 border-b border-surface-border text-xs text-text-muted font-medium" style={{ gridTemplateColumns: template }}>
          {columns.map((column) => column.sortValue ? (
            <button key={column.id} onClick={() => changeSort(column.id)} aria-sort={sort === column.id ? (direction === "asc" ? "ascending" : "descending") : "none"} className="hover:text-text-primary rounded focus-visible:ring-2 focus-visible:ring-accent" style={{ textAlign: column.align ?? "left" }}>
              {column.label}{sort === column.id ? (direction === "asc" ? " ↑" : " ↓") : ""}
            </button>
          ) : <span key={column.id} style={{ textAlign: column.align ?? "left" }}>{column.label}</span>)}
        </div>
        {visible.map((row) => <div key={rowKey(row)} className="grid min-w-[560px] items-center gap-3 px-4 py-2 border-b border-surface-border hover:bg-surface-lighter/70" style={{ gridTemplateColumns: template }}>
          {columns.map((column) => <div key={column.id} className="min-w-0 text-sm" style={{ textAlign: column.align ?? "left" }}>{column.render(row)}</div>)}
        </div>)}
      </div>
    )}
    {pageSize > 0 && totalPages > 1 && <div className="flex justify-center items-center gap-3 mt-4 text-sm text-text-muted">
      <button className="btn-ghost btn-sm" disabled={page <= 1} onClick={() => update({ page: String(page - 1) })}><ChevronLeft size={15} /> Previous</button>
      <span>Page {page} of {totalPages}</span>
      <button className="btn-ghost btn-sm" disabled={page >= totalPages} onClick={() => update({ page: String(page + 1) })}>Next <ChevronRight size={15} /></button>
    </div>}
  </div>;
}
