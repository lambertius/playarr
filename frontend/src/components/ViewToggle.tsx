import { LayoutGrid, List } from "lucide-react";
import { cn } from "@/lib/utils";

export type CollectionView = "grid" | "list";

export function ViewToggle({ value, onChange, label = "Collection layout" }: {
  value: CollectionView;
  onChange: (view: CollectionView) => void;
  label?: string;
}) {
  return (
    <div className="inline-flex rounded-md border border-surface-border p-0.5" role="group" aria-label={label}>
      {(["grid", "list"] as const).map(view => {
        const Icon = view === "grid" ? LayoutGrid : List;
        return (
          <button key={view} type="button"
            aria-label={`${view === "grid" ? "Grid" : "List"} view`}
            aria-pressed={value === view} onClick={() => onChange(view)}
            className={cn("btn-ghost btn-sm px-2", value === view && "bg-surface-hover text-accent")}>
            <Icon size={14} />
          </button>
        );
      })}
    </div>
  );
}
