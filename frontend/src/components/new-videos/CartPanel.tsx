/**
 * CartPanel — Shows the user's import cart with batch actions.
 *
 * Displays as a collapsible panel below the header. Shows all queued items
 * with remove buttons and provides Import All / Clear Cart actions.
 */
import { ShoppingCart, Download, Trash2, X, ExternalLink } from "lucide-react";
import { Tooltip } from "@/components/Tooltip";
import { useNewVideosCart, useNewVideosRemoveFromCart } from "@/hooks/queries";
import type { CartItem } from "@/types";

export function CartPanel({
  onImportAll,
  onClear,
  importPending,
  clearPending,
}: {
  onImportAll: () => void;
  onClear: () => void;
  importPending: boolean;
  clearPending: boolean;
}) {
  const { data: cart, isLoading } = useNewVideosCart();
  const removeMutation = useNewVideosRemoveFromCart();

  const items = cart?.items ?? [];

  return (
    <div className="bg-surface-light border border-surface-border rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <ShoppingCart size={18} className="text-accent" />
          <h3 className="font-semibold text-text-primary">Import Cart</h3>
          <span className="text-xs text-text-muted">({items.length} items)</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onImportAll}
            disabled={importPending || items.length === 0}
            className="btn-primary btn-sm text-xs"
          >
            <Download size={14} className="mr-1" />
            {importPending ? "Importing..." : "Import All"}
          </button>
          <button
            onClick={onClear}
            disabled={clearPending || items.length === 0}
            className="btn btn-sm text-xs text-red-400 hover:text-red-300"
          >
            <Trash2 size={14} className="mr-1" />
            Clear
          </button>
        </div>
      </div>

      {isLoading && (
        <p className="text-sm text-text-muted">Loading cart...</p>
      )}

      {!isLoading && items.length === 0 && (
        <p className="text-sm text-text-muted text-center py-4">
          Cart is empty. Add videos from the suggestions below.
        </p>
      )}

      {items.length > 0 && (
        <div className="space-y-1.5 max-h-60 overflow-y-auto">
          {items.map((item: CartItem) => (
            <div
              key={item.id}
              className="flex items-center gap-3 bg-surface rounded-md px-3 py-2 group"
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm text-text-primary truncate">
                  {item.artist && <span className="text-text-secondary">{item.artist} — </span>}
                  {item.title || item.url}
                </p>
              </div>
              <Tooltip content="Open source URL in a new tab">
              <a
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-text-muted hover:text-accent transition-colors"
              >
                <ExternalLink size={14} />
              </a>
              </Tooltip>
              <Tooltip content="Remove this item from the import cart">
              <button
                onClick={() => removeMutation.mutate(item.suggested_video_id)}
                disabled={removeMutation.isPending}
                className="text-text-muted hover:text-red-400 transition-colors"
              >
                <X size={14} />
              </button>
              </Tooltip>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
