/**
 * NewVideosPage — Discovery feed for music video recommendations.
 *
 * Display strategy: Thumbnail-based cards in horizontal scrollable rows
 * (one row per category). No embedded players — keeps page fast and avoids
 * API quota issues. Users click "Open Source" to preview externally.
 *
 * Layout: Category sections stacked vertically, each with a horizontally
 * scrollable card carousel. This lets users scan many suggestions quickly
 * while keeping the page clean.
 */
import { useState, useCallback } from "react";
import { RefreshCw, ShoppingCart, Sparkles, TrendingUp, Flame, Music, Star, Trophy } from "lucide-react";
import { Tooltip } from "@/components/Tooltip";
import {
  useNewVideosFeed,
  useNewVideosCart,
  useRefreshNewVideos,
  useNewVideosImportCart,
  useNewVideosClearCart,
  useNewVideosDismiss,
} from "@/hooks/queries";
import type { NewVideoCategory, SuggestedVideoItem } from "@/types";
import { SuggestionCard } from "@/components/new-videos/SuggestionCard";
import { CartPanel } from "@/components/new-videos/CartPanel";
import { AddVideoModal } from "@/components/AddVideoModal";
import { ImportOptionsModal } from "@/components/new-videos/ImportOptionsModal";
import type { ImportOptions } from "@/components/new-videos/ImportOptionsModal";

const CATEGORY_META: Record<NewVideoCategory, { label: string; icon: React.ElementType; description: string }> = {
  new:       { label: "New",                  icon: Sparkles,    description: "Recently released music videos" },
  popular:   { label: "Popular",              icon: TrendingUp,  description: "High-view official music videos" },
  rising:    { label: "Rising",               icon: Flame,       description: "Videos with notable recent growth" },
  by_artist: { label: "Recommended By Artist",icon: Music,       description: "Missing videos by artists in your library" },
  taste:     { label: "Songs You Might Like", icon: Star,        description: "Based on your ratings and library" },
  famous:    { label: "Famous",               icon: Trophy,      description: "Iconic music videos you might be missing" },
};

const CATEGORY_ORDER: NewVideoCategory[] = ["famous", "popular", "by_artist", "taste"];

export function NewVideosPage() {
  const { data: feed, isLoading, error } = useNewVideosFeed();
  const { data: cart } = useNewVideosCart();
  const refreshMutation = useRefreshNewVideos();
  const importAllMutation = useNewVideosImportCart();
  const clearCartMutation = useNewVideosClearCart();
  const dismissMutation = useNewVideosDismiss();
  const [showCart, setShowCart] = useState(false);
  const [importUrl, setImportUrl] = useState<string | null>(null);
  const [showImportOptions, setShowImportOptions] = useState(false);

  const cartCount = cart?.count ?? feed?.cart_count ?? 0;
  const cartItemCount = cart?.items?.length ?? 0;

  const handleAddVideo = useCallback((url: string) => {
    setImportUrl(url);
  }, []);

  const handleImportAll = useCallback((options: ImportOptions) => {
    importAllMutation.mutate(options, {
      onSuccess: () => setShowImportOptions(false),
    });
  }, [importAllMutation]);

  const handleImportSuccess = useCallback((url: string) => {
    if (!feed) return;
    for (const cat of CATEGORY_ORDER) {
      const video = feed.categories[cat]?.videos?.find(v => v.url === url);
      if (video) {
        dismissMutation.mutate({ id: video.id, type: "permanent" });
        break;
      }
    }
  }, [feed, dismissMutation]);

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">New Videos</h1>
          <p className="text-sm text-text-muted mt-1">
            Discover music videos you don&apos;t have yet
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Cart toggle */}
          <Tooltip content="View and manage items queued for import">
          <button
            onClick={() => setShowCart(!showCart)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm relative border border-accent/40 text-accent hover:bg-accent/10 rounded-lg font-medium transition-colors"
          >
            <ShoppingCart size={16} />
            <span className="ml-1">Cart</span>
            {cartCount > 0 && (
              <span className="absolute -top-1.5 -right-1.5 badge-red text-[10px] px-1.5 min-w-[18px] text-center">
                {cartCount}
              </span>
            )}
          </button>
          </Tooltip>

          {/* Refresh */}
          <Tooltip content="Re-fetch suggestions for all categories">
          <button
            onClick={() => refreshMutation.mutate({ force: true })}
            disabled={refreshMutation.isPending}
            className="btn btn-sm"
          >
            <RefreshCw size={16} className={refreshMutation.isPending ? "animate-spin" : ""} />
            <span className="ml-1">Refresh</span>
          </button>
          </Tooltip>
        </div>
      </div>

      {/* Cart panel */}
      {showCart && (
        <CartPanel
          onImportAll={() => setShowImportOptions(true)}
          onClear={() => clearCartMutation.mutate()}
          importPending={importAllMutation.isPending}
          clearPending={clearCartMutation.isPending}
        />
      )}

      {/* Loading state */}
      {isLoading && (
        <div className="flex items-center justify-center py-20 text-text-muted">
          <RefreshCw size={20} className="animate-spin mr-2" />
          Loading recommendations...
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-red-400">
          Failed to load recommendations: {(error as Error).message}
        </div>
      )}

      {/* Category sections */}
      {feed && CATEGORY_ORDER.map(cat => {
        const meta = CATEGORY_META[cat];
        const catData = feed.categories[cat];
        const videos = catData?.videos ?? [];

        if (videos.length === 0) return null;

        return (
          <CategorySection
            key={cat}
            category={cat}
            label={meta.label}
            description={meta.description}
            icon={meta.icon}
            videos={videos}
            onAdd={handleAddVideo}
          />
        );
      })}

      {/* Empty state */}
      {feed && CATEGORY_ORDER.every(cat => (feed.categories[cat]?.videos?.length ?? 0) === 0) && !isLoading && (
        <div className="text-center py-20 text-text-muted">
          <Trophy size={48} className="mx-auto mb-4 opacity-30" />
          <p className="text-lg">No recommendations yet</p>
          <p className="text-sm mt-1">Click Refresh to discover music videos</p>
          <button
            onClick={() => refreshMutation.mutate({ force: true })}
            className="btn-primary btn-sm mt-4"
          >
            <RefreshCw size={14} className="mr-1" />
            Generate Recommendations
          </button>
        </div>
      )}

      {/* Add Video modal */}
      <AddVideoModal
        open={importUrl !== null}
        onClose={() => setImportUrl(null)}
        initialUrl={importUrl ?? undefined}
        onImportSuccess={handleImportSuccess}
      />

      {/* Import Options modal for cart Import All */}
      <ImportOptionsModal
        open={showImportOptions}
        onClose={() => setShowImportOptions(false)}
        onImport={handleImportAll}
        itemCount={cartItemCount}
        isPending={importAllMutation.isPending}
      />
    </div>
  );
}

function CategorySection({
  category: _category,
  label,
  description,
  icon: Icon,
  videos,
  onAdd,
}: {
  category: NewVideoCategory;
  label: string;
  description: string;
  icon: React.ElementType;
  videos: SuggestedVideoItem[];
  onAdd?: (url: string) => void;
}) {
  return (
    <section className="pt-5 border-t-2 border-surface-border first:border-t-0 first:pt-0">
      <div className="flex items-center gap-2 mb-3">
        <Icon size={20} className="text-accent" />
        <h2 className="text-lg font-semibold text-text-primary">{label}</h2>
        <span className="text-xs text-text-muted ml-1">— {description}</span>
        <span className="text-xs text-text-muted ml-auto">{videos.length} suggestions</span>
      </div>

      {/* Responsive column grid */}
      <div className="grid grid-cols-[repeat(auto-fill,200px)] gap-4">
        {videos.slice(0, 12).map(video => (
          <SuggestionCard key={video.id} video={video} onAdd={onAdd} />
        ))}
      </div>
    </section>
  );
}
