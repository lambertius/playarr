/**
 * SuggestionCard — Displays a single recommended music video.
 *
 * Design: Thumbnail-focused card with compact metadata and action buttons.
 * No embedded player — thumbnail only. Actions: Open Source, Add, Cart, Dismiss.
 *
 * The card is 280px wide to fit several per row in a horizontal scroll.
 */
import { useState } from "react";
import {
  ExternalLink, Plus, ShoppingCart, X, XCircle,
  Shield, ShieldCheck, ShieldAlert,
} from "lucide-react";
import type { SuggestedVideoItem } from "@/types";
import { Tooltip } from "@/components/Tooltip";
import {
  useNewVideosAddToCart,
  useNewVideosRemoveFromCart,
  useNewVideosDismiss,
  useNewVideosFeedback,
} from "@/hooks/queries";
import { useToast } from "@/components/Toast";

function formatDuration(seconds: number | null): string {
  if (!seconds) return "";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function TrustBadge({ score, sourceType }: { score: number; sourceType: string | null }) {
  if (score >= 0.85) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-medium text-green-400 bg-green-500/10 px-1.5 py-0.5 rounded">
        <ShieldCheck size={10} />
        {sourceType === "vevo" ? "VEVO" : "Official"}
      </span>
    );
  }
  if (score >= 0.6) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-medium text-yellow-400 bg-yellow-500/10 px-1.5 py-0.5 rounded">
        <Shield size={10} />
        Likely Official
      </span>
    );
  }
  if (score < 0.4) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-medium text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded">
        <ShieldAlert size={10} />
        Unverified
      </span>
    );
  }
  return null;
}

export function SuggestionCard({ video, onAdd }: { video: SuggestedVideoItem; onAdd?: (url: string) => void }) {
  const addToCartMutation = useNewVideosAddToCart();
  const removeFromCartMutation = useNewVideosRemoveFromCart();
  const dismissMutation = useNewVideosDismiss();
  const feedbackMutation = useNewVideosFeedback();
  const { toast } = useToast();
  const [dismissed, setDismissed] = useState(false);

  if (dismissed) return null;

  const handleOpenSource = () => {
    feedbackMutation.mutate({
      suggested_video_id: video.id,
      feedback_type: "opened_source",
      provider: video.provider,
      provider_video_id: video.provider_video_id,
      artist: video.artist ?? undefined,
      category: video.category,
    });
    window.open(video.url, "_blank", "noopener,noreferrer");
  };

  const handleAdd = () => {
    if (onAdd) {
      onAdd(video.url);
      // Auto-dismiss so the card disappears immediately
      dismissMutation.mutate({ id: video.id, type: "permanent" });
      setDismissed(true);
    }
  };

  const handleCartToggle = () => {
    if (video.in_cart) {
      removeFromCartMutation.mutate(video.id, {
        onSuccess: () => toast({ type: "success", title: "Removed from cart" }),
      });
    } else {
      addToCartMutation.mutate(video.id, {
        onSuccess: () => toast({ type: "success", title: "Added to cart" }),
      });
    }
  };

  const handleDismiss = (type: "temporary" | "permanent") => {
    dismissMutation.mutate({ id: video.id, type });
    setDismissed(true);
  };

  const primaryReason = video.reasons?.[0];

  return (
    <div className="rounded-lg bg-surface-light border border-surface-border overflow-hidden group hover:border-accent/40 transition-colors">
      {/* Thumbnail */}
      <div className="relative aspect-video bg-surface cursor-pointer" onClick={handleOpenSource}>
        {video.thumbnail_url ? (
          <img
            src={video.thumbnail_url}
            alt={video.title}
            className="w-full h-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-text-muted">
            No thumbnail
          </div>
        )}

        {/* Duration overlay */}
        {video.duration_seconds && (
          <span className="absolute bottom-1 right-1 bg-black/80 text-white text-[10px] px-1.5 py-0.5 rounded">
            {formatDuration(video.duration_seconds)}
          </span>
        )}

        {/* Provider badge */}
        <span className="absolute top-1 left-1 bg-black/70 text-white text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wider">
          {video.provider}
        </span>

        {/* Play overlay on hover */}
        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition-colors flex items-center justify-center">
          <ExternalLink size={24} className="text-white opacity-0 group-hover:opacity-100 transition-opacity" />
        </div>
      </div>

      {/* Metadata */}
      <div className="p-3 space-y-2">
        {/* Title & artist */}
        <div>
          <h3 className="text-sm font-medium text-text-primary leading-tight line-clamp-2" title={video.title}>
            {video.title}
          </h3>
          {video.artist && (
            <p className="text-xs text-text-secondary mt-0.5 truncate">{video.artist}</p>
          )}
        </div>

        {/* Channel & badges */}
        <div className="flex items-center gap-1.5 flex-wrap">
          {video.channel && (
            <span className="text-[10px] text-text-muted truncate max-w-[120px]">{video.channel}</span>
          )}
          <TrustBadge score={video.trust_score} sourceType={video.source_type} />
        </div>

        {/* Reason text */}
        {primaryReason && (
          <p className="text-[11px] text-accent/80 leading-tight line-clamp-2 italic">
            {primaryReason}
          </p>
        )}

        {/* Actions */}
        <div className="flex items-center gap-1 pt-1">
          <Tooltip content="Open in new tab">
          <button
            onClick={handleOpenSource}
            className="btn btn-sm text-[11px] px-2 py-1"
          >
            <ExternalLink size={12} />
          </button>
          </Tooltip>
          <Tooltip content="Import this video now">
          <button
            onClick={handleAdd}
            className="btn-primary btn-sm text-[11px] px-2 py-1"
          >
            <Plus size={12} />
            <span className="ml-0.5">Add</span>
          </button>
          </Tooltip>
          <button
            onClick={handleCartToggle}
            disabled={addToCartMutation.isPending || removeFromCartMutation.isPending}
            className={`btn-sm text-[11px] px-2 py-1 border rounded font-medium transition-all duration-200 ${
              video.in_cart
                ? "bg-accent text-white border-accent shadow-[0_0_8px_rgba(225,29,46,0.3)]"
                : "border-accent/40 text-accent hover:bg-accent hover:text-white hover:border-accent hover:scale-125 hover:shadow-[0_0_12px_rgba(225,29,46,0.4)]"
            }`}
            title={video.in_cart ? "Remove from cart" : "Add to import cart"}
          >
            <ShoppingCart size={12} />
          </button>
          <div className="ml-auto flex gap-0.5">
            <Tooltip content="Dismiss temporarily — may reappear later">
            <button
              onClick={() => handleDismiss("temporary")}
              className="btn btn-sm text-[11px] px-1.5 py-1 text-text-muted hover:text-yellow-400"
            >
              <X size={12} />
            </button>
            </Tooltip>
            <Tooltip content="Permanently hide — never suggest again">
            <button
              onClick={() => handleDismiss("permanent")}
              className="btn btn-sm text-[11px] px-1.5 py-1 text-text-muted hover:text-red-400"
            >
              <XCircle size={12} />
            </button>
            </Tooltip>
          </div>
        </div>
      </div>
    </div>
  );
}
