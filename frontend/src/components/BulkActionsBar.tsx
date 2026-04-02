import { cn } from "@/lib/utils";
import { Tooltip } from "@/components/Tooltip";

interface Props {
  selectedCount: number;
  onAcceptAll: () => void;
  onResolveAll: () => void;
  onClear: () => void;
  className?: string;
}

export default function BulkActionsBar({
  selectedCount,
  onAcceptAll,
  onResolveAll,
  onClear,
  className,
}: Props) {
  if (selectedCount === 0) return null;

  return (
    <div
      className={cn(
        "fixed bottom-4 left-1/2 -translate-x-1/2 z-50",
        "flex items-center gap-3 px-4 py-2.5 rounded-xl",
        "bg-surface-hover/95 backdrop-blur-md border border-surface-hover shadow-xl",
        className
      )}
    >
      <span className="text-sm text-text-primary font-medium">
        {selectedCount} selected
      </span>

      <div className="h-4 w-px bg-surface-hover" />

      <Tooltip content="Apply the highest-confidence match to all selected items and mark them as resolved.">
        <button onClick={onAcceptAll} className="btn-primary btn-sm text-xs">
          Accept Top Match
        </button>
      </Tooltip>

      <Tooltip content="Re-run matching for all selected items to find better candidates.">
        <button onClick={onResolveAll} className="btn-secondary btn-sm text-xs">
          Re-resolve
        </button>
      </Tooltip>

      <Tooltip content="Clear selection without making changes.">
        <button onClick={onClear} className="btn-ghost btn-sm text-xs">
          Clear
        </button>
      </Tooltip>
    </div>
  );
}
