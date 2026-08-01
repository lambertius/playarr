import { useMemo } from "react";
import type { ReactNode } from "react";
import { LayoutGrid, List, ChevronLeft, ChevronRight } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { getPref, setPref } from "@/lib/preferences";
import type { ViewMode } from "@/types";

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

interface DataViewProps<T> {
  rows: T[];
  rowKey: (row: T) => string | number;
  columns: DataViewColumn<T>[];
  renderCard: (row: T) => ReactNode;
  preferenceKey: string;
  defaultSort: string;
  defaultDirection?: "asc" | "desc";
  empty: ReactNode;
}

/** Shared grid/list query, sorting, paging and alignment primitive. */
export function DataView<T>({
  rows, rowKey, columns, renderCard, preferenceKey, defaultSort, defaultDirection = "asc", empty,
}: DataViewProps<T>) {
  const [params, setParams] = useSearchParams();
  const prefs = getPref<PanelPreferences>("panels", {});
  const view = (params.get("view") as ViewMode | null) ?? prefs.navViews?.[preferenceKey] ?? "grid";
  const sort = params.get("sort") ?? prefs.navSorts?.[preferenceKey] ?? defaultSort;
  const direction = (params.get("dir") as "asc" | "desc" | null) ?? prefs.navDirections?.[preferenceKey] ?? defaultDirection;
  const pageSize = Number(params.get("page_size")) || prefs.navPageSizes?.[preferenceKey] || 50;
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
  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const page = Math.min(requestedPage, totalPages);
  const visible = sorted.slice((page - 1) * pageSize, page * pageSize);
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

  if (!rows.length) return <>{empty}</>;
  return <div>
    <div className="flex justify-end items-center gap-2 mb-3">
      <label className="text-xs text-text-muted flex items-center gap-2">Page size
        <select value={pageSize} onChange={(event) => changePageSize(Number(event.target.value))} className="input-field py-1 text-xs w-auto">
          {[25, 50, 100, 200].map((size) => <option key={size} value={size}>{size}</option>)}
        </select>
      </label>
      <div className="flex border border-surface-border rounded-lg overflow-hidden">
        <button onClick={() => changeView("grid")} className={`p-1.5 ${view === "grid" ? "bg-accent/10 text-accent" : "text-text-muted"}`} aria-label="Grid view"><LayoutGrid size={16} /></button>
        <button onClick={() => changeView("list")} className={`p-1.5 ${view === "list" ? "bg-accent/10 text-accent" : "text-text-muted"}`} aria-label="List view"><List size={16} /></button>
      </div>
    </div>
    {view === "grid" ? (
      <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-4">{visible.map((row) => <div key={rowKey(row)}>{renderCard(row)}</div>)}</div>
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
    {totalPages > 1 && <div className="flex justify-center items-center gap-3 mt-4 text-sm text-text-muted">
      <button className="btn-ghost btn-sm" disabled={page <= 1} onClick={() => update({ page: String(page - 1) })}><ChevronLeft size={15} /> Previous</button>
      <span>Page {page} of {totalPages}</span>
      <button className="btn-ghost btn-sm" disabled={page >= totalPages} onClick={() => update({ page: String(page + 1) })}>Next <ChevronRight size={15} /></button>
    </div>}
  </div>;
}
